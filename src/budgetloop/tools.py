"""Tool layer: a registry, a workspace sandbox, and a fail-closed approval gate.

Design stance (this is the guardrail half of the harness):

* Tools are *declared*, not discovered. A tool exists only if it is registered.
* Every filesystem tool is confined to a workspace root; escapes raise.
* Mutating tools require an explicit human approval callback. If no policy is
  configured we **refuse**, we do not proceed -- fail closed, not open.
* Observations are byte-capped so a single huge read cannot eat the budget.
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 - used with an explicit argv (never a shell) and an allow-list
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ApproveFn = Callable[["Tool", dict[str, Any]], bool]

#: Path fragments that file tools refuse to touch even inside the workspace.
DENIED_PATH_PARTS = frozenset({".git", ".budgetloop", "__pycache__", ".venv", "node_modules"})


class ToolError(Exception):
    """Raised when a tool cannot complete its job."""


class SandboxViolation(ToolError):
    """Raised when a requested path escapes the workspace root."""


class ApprovalDenied(ToolError):
    """Raised when a mutating tool is refused by the operator or by policy."""


@dataclass(frozen=True)
class Tool:
    """A named capability the agent may request."""

    name: str
    description: str
    parameters: dict[str, str]
    handler: Callable[..., str]
    mutates: bool = False

    def schema(self) -> dict[str, Any]:
        """JSON-schema-ish declaration, suitable for a provider's tools argument."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    key: {"type": "string", "description": value}
                    for key, value in self.parameters.items()
                },
                "required": sorted(self.parameters),
            },
        }

    def spec_block(self) -> str:
        """Compact prompt-friendly description (also what the budget demo charges)."""
        args = ", ".join(f"{name}: {desc}" for name, desc in self.parameters.items())
        flag = " [mutates]" if self.mutates else ""
        return f"- {self.name}({args or 'no arguments'}){flag}: {self.description}"


class Workspace:
    """Confines file tools to a single root directory."""

    def __init__(self, root: str | Path, max_read_bytes: int = 12_000) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ToolError(f"workspace root is not a directory: {self.root}")
        self.max_read_bytes = max_read_bytes

    def resolve(self, relative: str, *, must_exist: bool = True) -> Path:
        """Resolve ``relative`` inside the workspace or raise ``SandboxViolation``."""
        if not isinstance(relative, str) or not relative.strip():
            raise SandboxViolation("path must be a non-empty string")
        candidate = Path(relative)
        if candidate.is_absolute():
            raise SandboxViolation(f"absolute paths are not allowed: {relative!r}")
        resolved = (self.root / candidate).resolve()  # resolve() follows symlinks
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxViolation(f"path escapes the workspace: {relative!r}")
        relative_parts = resolved.relative_to(self.root).parts
        denied = DENIED_PATH_PARTS.intersection(relative_parts)
        if denied:
            raise SandboxViolation(
                f"path touches a protected directory {sorted(denied)}: {relative!r}"
            )
        if must_exist and not resolved.exists():
            raise ToolError(f"no such path in workspace: {relative!r}")
        return resolved

    # -- handlers ---------------------------------------------------------
    def read_file(self, path: str) -> str:
        target = self.resolve(path, must_exist=True)
        if target.is_dir():
            raise ToolError(f"{path!r} is a directory; use list_files")
        raw = target.read_bytes()
        truncated = len(raw) > self.max_read_bytes
        raw = raw[: self.max_read_bytes]
        if b"\x00" in raw:
            raise ToolError(f"{path!r} looks binary; refusing to read it as text")
        text = raw.decode("utf-8", errors="replace")
        header = f"{target.relative_to(self.root)} ({len(raw)} bytes shown"
        header += ", TRUNCATED]" if truncated else "]"
        return f"{header}\n{text}"

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(content, encoding="utf-8")
        verb = "updated" if existed else "created"
        return f"{verb} {target.relative_to(self.root)} ({len(content)} chars)"

    def list_files(self, pattern: str = "**/*", limit: int = 200) -> str:
        matches: list[str] = []
        for path in sorted(self.root.glob(pattern)):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            if DENIED_PATH_PARTS.intersection(relative.parts):
                continue
            matches.append(str(relative))
            if len(matches) >= limit:
                matches.append(f"... (stopped at {limit} entries)")
                break
        if not matches:
            return f"no files matched {pattern!r}"
        return "\n".join(matches)


@dataclass
class ToolRegistry:
    """Declared tools + argument validation + approval gating + output capping."""

    workspace: Workspace
    approve: ApproveFn | None = None
    max_observation_bytes: int = 8_000
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ToolError(f"duplicate tool name: {tool.name!r}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            known = ", ".join(self.names) or "none"
            raise ToolError(f"unknown tool {name!r}; known tools: {known}") from None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def describe(self) -> str:
        return "\n".join(tool.spec_block() for _, tool in sorted(self._tools.items()))

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for _, tool in sorted(self._tools.items())]

    def validate(self, tool: Tool, arguments: Mapping[str, Any]) -> dict[str, str]:
        """Reject hallucinated arguments before they reach a handler."""
        if not isinstance(arguments, Mapping):
            raise ToolError(
                f"{tool.name} expected an object of arguments, got {type(arguments).__name__}"
            )
        unknown = sorted(set(arguments) - set(tool.parameters))
        if unknown:
            raise ToolError(
                f"{tool.name} received unexpected argument(s) {unknown}; "
                f"expected {sorted(tool.parameters)}"
            )
        missing = sorted(set(tool.parameters) - set(arguments))
        if missing:
            raise ToolError(f"{tool.name} is missing required argument(s) {missing}")
        coerced: dict[str, str] = {}
        for key, value in arguments.items():
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ToolError(
                    f"{tool.name}.{key} must be a string or number, got {type(value).__name__}"
                )
            coerced[key] = str(value)
        return coerced

    def run(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        approve: ApproveFn | None = None,
    ) -> str:
        """Execute a tool, or raise ``ToolError`` on refusal or failure.

        Keeping the raise here (instead of returning an error string) makes the
        policy testable in isolation; the loop converts these into observations.
        """
        tool = self.get(name)
        args = self.validate(tool, arguments or {})
        policy = approve if approve is not None else self.approve
        if tool.mutates:
            if policy is None:
                raise ApprovalDenied(
                    f"refusing to run mutating tool {name!r}: no approval policy configured"
                    " (fail closed)"
                )
            if not policy(tool, args):
                raise ApprovalDenied(f"operator declined mutating tool {name!r} with {args}")
        return self._cap(tool.handler(**args))

    def _cap(self, text: str) -> str:
        limit = self.max_observation_bytes
        if len(text) <= limit:
            return text
        omitted = len(text) - limit
        notice = f"\n... [observation truncated: {omitted} chars omitted by the harness]"
        return text[:limit] + notice


def make_check_tool(
    workspace: Workspace,
    checks: Mapping[str, Sequence[str]],
    *,
    timeout_seconds: int = 120,
    tail_chars: int = 4_000,
) -> Tool:
    """Build a ``run_check`` tool backed by an allow-list of argv commands.

    There is no shell and no free-form command argument: the model may only name
    a check a human already registered. That is the difference between "the agent
    can run the tests" and "the agent can run anything".
    """
    if not checks:
        raise ToolError("at least one check must be registered")

    def handler(name: str) -> str:
        argv = list(checks.get(name, ()))
        if not argv:
            raise ToolError(f"unknown check {name!r}; allowed checks: {sorted(checks)}")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, ["src", env.get("PYTHONPATH", "")]))
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, shell=False, allow-listed
                argv,
                cwd=workspace.root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return f"$ {' '.join(argv)}\nexit_code=124\nTIMEOUT after {timeout_seconds}s"
        output = (completed.stdout + completed.stderr).strip()
        if len(output) > tail_chars:
            output = "... [earlier output elided]\n" + output[-tail_chars:]
        return f"$ {' '.join(argv)}\nexit_code={completed.returncode}\n{output}"

    return Tool(
        name="run_check",
        description=(
            "Run one registered project check (for example the test suite)"
            " and return its output."
        ),
        parameters={"name": f"check name, one of: {', '.join(sorted(checks))}"},
        handler=handler,
    )


def default_checks() -> dict[str, list[str]]:
    """The project's canonical checks, as allow-listed argv commands."""
    python = sys.executable
    return {
        "tests": [python, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        # The focused TASK-A gate: fails when any module under src/ lacks a
        # docstring. Lives in scripts/check_style.py, not a one-off script.
        "style": [python, "scripts/check_style.py"],
        "src-docstrings": [python, "scripts/check_style.py", "src", "--src-docstrings-only"],
    }


def default_registry(
    workspace: Workspace,
    *,
    approve: ApproveFn | None = None,
    checks: Mapping[str, Sequence[str]] | None = None,
) -> ToolRegistry:
    """The standard toolbelt: read, list, write (gated), run a registered check."""
    registry = ToolRegistry(workspace=workspace, approve=approve)
    registry.register(
        Tool(
            name="read_file",
            description="Read a UTF-8 text file inside the workspace.",
            parameters={"path": "workspace-relative path, e.g. README.md"},
            handler=workspace.read_file,
        )
    )
    registry.register(
        Tool(
            name="list_files",
            description="List workspace files matching a glob pattern.",
            parameters={"pattern": "glob such as **/*.py; defaults to **/*"},
            handler=lambda pattern="**/*": workspace.list_files(pattern),
        )
    )
    registry.register(
        Tool(
            name="write_file",
            description=(
                "Create or overwrite a text file inside the workspace."
                " Requires human approval."
            ),
            parameters={"path": "workspace-relative path", "content": "full new file content"},
            handler=workspace.write_file,
            mutates=True,
        )
    )
    registry.register(make_check_tool(workspace, checks or default_checks()))
    return registry
