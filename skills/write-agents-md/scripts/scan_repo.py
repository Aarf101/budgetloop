#!/usr/bin/env python3
"""Scan a repository and report the facts an AGENTS.md has to get right.

Self-contained on purpose: a skill can be activated in a repository with no
tooling of its own, so this script depends on nothing but the standard library.

It reports what it can *observe* -- marker files, existing agent files, CI
commands, test directories, file mix, recent commits -- and then proposes a
command set. The proposals are hypotheses for the caller to verify by running
them; they are never presented as facts.

Usage: python scripts/scan_repo.py [PATH] [--json]
Exit codes: 0 report produced, 2 bad usage.
"""

from __future__ import annotations

import json
import subprocess  # noqa: S404 - argv only, never a shell, fixed commands
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "build",
        "dist",
        "target",
        ".budgetloop",
        ".pytest_cache",
        ".mypy_cache",
    }
)
#: marker file -> the stack it implies, and a suggested canonical command.
STACK_MARKERS: dict[str, tuple[str, str]] = {
    "pyproject.toml": ("Python (pyproject)", "python -m pip install -e ."),
    "setup.py": ("Python (setuptools)", "python -m pip install -e ."),
    "requirements.txt": ("Python (requirements)", "python -m pip install -r requirements.txt"),
    "package.json": ("Node", "npm install"),
    "pnpm-lock.yaml": ("Node (pnpm)", "pnpm install"),
    "yarn.lock": ("Node (yarn)", "yarn install"),
    "go.mod": ("Go", "go build ./..."),
    "Cargo.toml": ("Rust", "cargo build"),
    "Gemfile": ("Ruby", "bundle install"),
    "composer.json": ("PHP", "composer install"),
    "Makefile": ("Make", "make help"),
    "docker-compose.yml": ("Docker Compose", "docker compose up -d"),
    "compose.yaml": ("Docker Compose", "docker compose up -d"),
    "Dockerfile": ("Docker", "docker build ."),
}
TEST_COMMAND_BY_MARKER: dict[str, str] = {
    "pyproject.toml": "python -m unittest discover -s tests -t .   # or: pytest -q",
    "requirements.txt": "python -m unittest discover -s tests -t .   # or: pytest -q",
    "package.json": "npm test",
    "go.mod": "go test ./...",
    "Cargo.toml": "cargo test",
    "Gemfile": "bundle exec rspec",
}
AGENT_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".cursorrules",
    ".github/copilot-instructions.md",
    ".aider.conf.yml",
)
TEXT_SUFFIXES = frozenset(
    {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java", ".kt",
     ".c", ".h", ".cc", ".cpp", ".cs", ".sh", ".sql", ".md", ".yml", ".yaml",
     ".toml", ".json", ".html", ".css", ".php"}
)
MAX_FILES_SCANNED = 4_000
MAX_FILE_BYTES = 512_000


@dataclass
class Report:
    """Everything the scan observed about one repository."""

    root: Path
    stacks: list[str] = field(default_factory=list)
    agent_files: list[tuple[str, int]] = field(default_factory=list)
    ci_commands: list[str] = field(default_factory=list)
    test_dirs: list[tuple[str, int]] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    extensions: list[tuple[str, int, int]] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    scanned_files: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "stacks": self.stacks,
            "agent_files": [{"path": p, "lines": n} for p, n in self.agent_files],
            "ci_commands": self.ci_commands,
            "test_dirs": [{"path": p, "test_files": n} for p, n in self.test_dirs],
            "entry_points": self.entry_points,
            "extensions": [
                {"suffix": s, "files": f, "lines": n} for s, f, n in self.extensions
            ],
            "recent_commits": self.recent_commits,
            "notes": self.notes,
            "scanned_files": self.scanned_files,
            "truncated": self.truncated,
        }


def find_root(path: Path) -> Path:
    """Prefer the git top level, so the scan describes the whole repository."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return path.resolve()
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return path.resolve()


def _count_lines(path: Path) -> int:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return 0
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _git_log(root: Path, count: int = 5) -> list[str]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            ["git", "-C", str(root), "log", f"-{count}", "--pretty=%h %s"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _is_test_file(name: str) -> bool:
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def scan(root: Path) -> Report:
    """Observe a repository and return the report the caller will render."""
    report = Report(root=root)
    files, truncated = _iter_files(root)
    report.truncated = truncated
    report.scanned_files = len(files)

    for name, (stack, _install) in STACK_MARKERS.items():
        if (root / name).exists():
            report.stacks.append(stack)

    for candidate in sorted(root.rglob("AGENTS.md")):
        if SKIP_DIRS.intersection(candidate.relative_to(root).parts):
            continue
        report.agent_files.append(
            (candidate.relative_to(root).as_posix(), _count_lines(candidate))
        )
    for name in AGENT_FILES:
        path = root / name
        if name != "AGENTS.md" and path.is_file():
            report.agent_files.append((name, _count_lines(path)))

    for workflow in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        for line in workflow.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith("run:"):
                command = stripped[len("run:") :].strip().strip("|>").strip()
                if command and len(report.ci_commands) < 20:
                    report.ci_commands.append(f"{workflow.name}: {command}")

    test_counts: Counter[str] = Counter()
    entry_points: list[str] = []
    suffix_files: Counter[str] = Counter()
    suffix_lines: Counter[str] = Counter()
    for path in files:
        relative = path.relative_to(root)
        if _is_test_file(path.name):
            test_counts[str(relative.parent)] += 1
        if path.name in {"main.py", "cli.py", "app.py", "index.js", "index.ts", "server.py"}:
            entry_points.append(relative.as_posix())
        if path.suffix in TEXT_SUFFIXES:
            suffix_files[path.suffix] += 1
            suffix_lines[path.suffix] += _count_lines(path)

    report.test_dirs = sorted(test_counts.items(), key=lambda item: (-item[1], item[0]))
    report.entry_points = entry_points[:12]
    report.extensions = [
        (suffix, count, suffix_lines[suffix])
        for suffix, count in suffix_files.most_common(10)
    ]
    report.recent_commits = _git_log(root)

    if not report.agent_files:
        report.notes.append("no AGENTS.md yet: an agent is working from your README alone")
    elif len(report.agent_files) > 1:
        report.notes.append(
            f"{len(report.agent_files)} agent files exist: the one nearest the edited file wins"
        )
    if not report.test_dirs:
        report.notes.append(
            "no test files found: say so explicitly, or an agent will invent a test command"
        )
    if report.ci_commands:
        report.notes.append("copy the CI commands rather than inventing new ones")
    if truncated:
        report.notes.append(f"scan stopped at {MAX_FILES_SCANNED} files; counts are a lower bound")
    return report


def _iter_files(root: Path) -> tuple[list[Path], bool]:
    """Every non-vendored file under ``root``, capped, plus a truncation flag."""
    found: list[Path] = []
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        if SKIP_DIRS.intersection(candidate.relative_to(root).parts):
            continue
        found.append(candidate)
        if len(found) >= MAX_FILES_SCANNED:
            return found, True
    return found, False


def suggest_commands(report: Report) -> list[str]:
    """Propose a command set derived from markers and CI -- hypotheses to verify."""
    suggestions: list[str] = []
    for name, (_stack, install) in STACK_MARKERS.items():
        if (report.root / name).exists() and install not in suggestions:
            suggestions.append(install)
    for name, command in TEST_COMMAND_BY_MARKER.items():
        if (report.root / name).exists():
            suggestions.append(command)
            break
    if (report.root / "Makefile").exists():
        suggestions.append("make check   # only if that target exists")
    for config, command in (
        ("ruff.toml", "ruff check ."),
        (".ruff.toml", "ruff check ."),
        ("mypy.ini", "mypy ."),
        ("setup.cfg", "flake8"),
        (".eslintrc.json", "npx eslint ."),
        ("eslint.config.js", "npx eslint ."),
        (".prettierrc", "npx prettier --check ."),
    ):
        if (report.root / config).exists():
            suggestions.append(command)
    return suggestions


def render(report: Report) -> str:
    """Render the report as Markdown for a human or an agent to read."""
    lines = [
        f"# Repository scan — {report.root}",
        "",
        f"Files scanned: {report.scanned_files}"
        + (" (capped: counts are a lower bound)" if report.truncated else ""),
        "",
        "## Observed stacks",
    ]
    lines += [f"- {stack}" for stack in report.stacks] or ["- (no marker files found)"]
    lines += ["", "## Agent files already present"]
    lines += (
        [f"- {path} ({count} lines)" for path, count in report.agent_files]
        or ["- none: this repository has no AGENTS.md"]
    )
    lines += ["", "## CI commands (copy these rather than inventing new ones)"]
    lines += [f"- {command}" for command in report.ci_commands] or ["- (no workflows found)"]
    lines += ["", "## Test files"]
    lines += (
        [f"- {path}/ — {count} test file(s)" for path, count in report.test_dirs]
        or ["- none found: state that explicitly, or an agent will invent a test command"]
    )
    lines += ["", "## Entry points"]
    lines += [f"- {path}" for path in report.entry_points] or ["- (none detected)"]
    lines += ["", "## File mix (top 10 by file count)", ""]
    lines += ["| suffix | files | lines |", "| --- | --- | --- |"]
    lines += [
        f"| `{suffix}` | {count} | {line_count} |"
        for suffix, count, line_count in report.extensions
    ]
    lines += ["", "## Recent commits"]
    lines += [f"- {commit}" for commit in report.recent_commits] or ["- (no git history)"]
    lines += ["", "## Notes and gaps"]
    lines += [f"- {note}" for note in report.notes] or ["- (nothing flagged)"]
    lines += ["", "## Suggested commands — VERIFY EACH ONE", ""]
    lines += [f"- `{command}`" for command in suggest_commands(report)] or ["- (none derived)"]
    lines += [
        "",
        "> These are hypotheses derived from file names, not facts. Run each command from a",
        "> clean checkout before writing it into AGENTS.md, and record the failures too.",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Scan the given path (default: the current directory) and print the report."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in arguments
    targets = [argument for argument in arguments if not argument.startswith("--")]
    if len(targets) > 1:
        print("usage: scan_repo.py [PATH] [--json]", file=sys.stderr)
        return 2
    path = Path(targets[0]) if targets else Path(".")
    if not path.exists():
        print(f"no such path: {path}", file=sys.stderr)
        return 2
    root = find_root(path)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    report = scan(root)
    if as_json:
        payload = report.to_dict()
        payload["suggested_commands"] = suggest_commands(report)
        print(json.dumps(payload, indent=2))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
