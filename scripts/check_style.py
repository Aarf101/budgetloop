#!/usr/bin/env python3
"""Enforce this repository's house style rules.

The rules are executable instead of written in a wiki, because `make check` runs
this file: an agent that ignores AGENTS.md finds out immediately, and a reviewer
gets to argue about substance instead of whitespace.

Rules
  1. no tabs, no trailing whitespace
  2. no line longer than MAX_LINE_LENGTH (100) characters
  3. the file ends with exactly one newline
  4. library code does not print: ``print(`` is allowed only in the CLI and scripts/
  5. every module and every public top-level class/function has a docstring
  6. every module under src/ must have a docstring (src-docstring gate,
     the focused check for TASK-A; wired into `make check` via
     `make src-docstrings` and into the agent's `run_check` via the
     `src-docstrings` entry in `tools.default_checks`)
  7. every module under tests/ must have a docstring (test-docstring gate,
     the companion to rule 6; wired into `make check` via
     `make tests-docstrings` and into the agent's `run_check` via the
     `tests-docstrings` entry in `tools.default_checks`)
  8. no bare ``except:`` (it swallows KeyboardInterrupt too)
  9. no TODO/FIXME/XXX markers and no NotImplementedError placeholders

Usage: python scripts/check_style.py [PATH ...] [--src-docstrings-only]
       python scripts/check_style.py [PATH ...] [--test-docstrings-only]
Exit codes: 0 clean, 1 violations found, 2 bad usage.

With --src-docstrings-only or --test-docstrings-only, only that focused gate
runs: every module under src/ (respectively tests/) must have a module
docstring. The two flags are mutually exclusive.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

MAX_LINE_LENGTH = 100
SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "build",
        "dist",
        ".budgetloop",
        ".pytest_cache",
    }
)
#: Files allowed to write to stdout, beyond anything under scripts/.
PRINT_ALLOWED = frozenset({"src/budgetloop/cli.py"})
PLACEHOLDER_TOKENS = ("TODO", "FIXME", "XXX", "NotImplementedError")
#: This file names the tokens it forbids, so it is exempt from rule 8 for itself.
SELF_EXEMPT = frozenset({"scripts/check_style.py"})
_BARE_EXCEPT = re.compile(r"^\s*except\s*:")


@dataclass(frozen=True)
class Violation:
    """One rule failure, with just enough location to fix it."""

    path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{location}: [{self.rule}] {self.message}"


def _is_src_module(posix_path: str) -> bool:
    """Whether ``posix_path`` is a module under ``src/`` (the TASK-A scope)."""
    return posix_path == "src" or posix_path.startswith("src/") or "/src/" in posix_path


def _is_tests_module(posix_path: str) -> bool:
    """Whether ``posix_path`` is a module under ``tests/`` (the companion scope)."""
    return posix_path == "tests" or posix_path.startswith("tests/") or "/tests/" in posix_path


def check_src_docstrings(root: Path | str = "src") -> list[Violation]:
    """Fail when any module under ``src/`` is missing its module docstring.

    The focused gate for TASK-A: one function the tests and the agent's
    ``run_check`` can call without running the whole style suite. It reuses
    :func:`check_file` and keeps only the ``src-docstring`` violations, so the
    rule lives in ``scripts/check_style.py`` instead of a new one-off script.
    """
    base = Path(root)
    if not base.exists():
        return [Violation(str(base), 0, "src-docstring", f"no such path: {base}")]
    violations: list[Violation] = []
    for path in python_files([base]):
        violations.extend(
            violation for violation in check_file(path) if violation.rule == "src-docstring"
        )
    return violations


def check_test_docstrings(root: Path | str = "tests") -> list[Violation]:
    """Fail when any module under ``tests/`` is missing its module docstring.

    The companion to :func:`check_src_docstrings`: tests are the contract in
    this repository, so a test module that does not say what it pins down is a
    defect worth a dedicated gate. Reuses :func:`check_file` and keeps only the
    ``test-docstring`` violations.
    """
    base = Path(root)
    if not base.exists():
        return [Violation(str(base), 0, "test-docstring", f"no such path: {base}")]
    violations: list[Violation] = []
    for path in python_files([base]):
        violations.extend(
            violation for violation in check_file(path) if violation.rule == "test-docstring"
        )
    return violations


def python_files(roots: Iterable[Path]) -> list[Path]:
    """Every .py file under ``roots``, skipping caches and virtualenvs."""
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix == ".py":
                found.append(root)
            continue
        for path in sorted(root.rglob("*.py")):
            if SKIP_DIRS.intersection(path.parts):
                continue
            found.append(path)
    return found


def _normalise(path: Path) -> str:
    """Repo-relative POSIX path when possible, so messages are stable."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _print_allowed(posix_path: str) -> bool:
    if posix_path in PRINT_ALLOWED:
        return True
    return posix_path.startswith("scripts/") or "/scripts/" in posix_path


def check_file(path: Path) -> list[Violation]:
    """Apply every rule to one file and return the violations found."""
    posix = _normalise(path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Violation(posix, 0, "readable", str(exc))]

    violations: list[Violation] = []
    for number, line in enumerate(source.splitlines(), start=1):
        if len(line) > MAX_LINE_LENGTH:
            violations.append(
                Violation(
                    posix,
                    number,
                    "line-length",
                    f"{len(line)} > {MAX_LINE_LENGTH} characters",
                )
            )
        if line != line.rstrip():
            violations.append(Violation(posix, number, "trailing-whitespace", "remove it"))
        if "\t" in line:
            violations.append(Violation(posix, number, "tabs", "indent with spaces"))
        if _BARE_EXCEPT.match(line):
            violations.append(
                Violation(posix, number, "bare-except", "name the exception you expect")
            )
        if posix not in SELF_EXEMPT:
            for token in PLACEHOLDER_TOKENS:
                if token in line:
                    violations.append(
                        Violation(posix, number, "placeholder", f"{token} left in the code")
                    )

    last_line = len(source.splitlines())
    if not source.endswith("\n"):
        violations.append(
            Violation(posix, last_line, "final-newline", "file must end with a newline")
        )
    elif source.endswith("\n\n"):
        violations.append(
            Violation(posix, last_line, "final-newline", "exactly one trailing newline")
        )

    try:
        tree = ast.parse(source, filename=posix)
    except SyntaxError as exc:
        violations.append(Violation(posix, exc.lineno or 0, "parses", str(exc.msg)))
        return violations

    if ast.get_docstring(tree) is None:
        violations.append(Violation(posix, 1, "module-docstring", "add a module docstring"))
        if _is_src_module(posix):
            violations.append(
                Violation(posix, 1, "src-docstring", "add a module docstring under src/")
            )
        if _is_tests_module(posix):
            violations.append(
                Violation(posix, 1, "test-docstring", "add a module docstring under tests/")
            )
    if not _print_allowed(posix):
        # Scanned through the AST, not the raw text: a string that merely
        # contains "print(" is not a call to print.
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                violations.append(
                    Violation(posix, node.lineno, "no-print", "library code must not print")
                )
    for node in tree.body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        if ast.get_docstring(node) is None:
            violations.append(
                Violation(posix, node.lineno, "public-docstring", f"{node.name} has no docstring")
            )
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """Check every path given (default: the repo root) and print the violations."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    src_only = "--src-docstrings-only" in arguments
    tests_only = "--test-docstrings-only" in arguments
    focused_flags = ("--src-docstrings-only", "--test-docstrings-only")
    arguments = [argument for argument in arguments if argument not in focused_flags]
    if src_only and tests_only:
        print(
            "choose one of --src-docstrings-only or --test-docstrings-only",
            file=sys.stderr,
        )
        return 2
    roots = [Path(argument) for argument in arguments] or [Path(".")]
    missing = [root for root in roots if not root.exists()]
    if missing:
        for root in missing:
            print(f"no such path: {root}", file=sys.stderr)
        return 2

    paths = python_files(roots)
    violations: list[Violation] = []
    for path in paths:
        violations.extend(check_file(path))
    if src_only or tests_only:
        rule = "src-docstring" if src_only else "test-docstring"
        scope = "src/" if src_only else "tests/"
        label = "src-docstrings" if src_only else "tests-docstrings"
        matcher = _is_src_module if src_only else _is_tests_module
        violations = [violation for violation in violations if violation.rule == rule]
        # A focused run on paths outside the scope checks nothing, so say so
        # loudly instead of reporting a misleading clean bill for it.
        if not any(matcher(_normalise(path)) for path in paths):
            print(f"no modules under {scope} in the checked paths", file=sys.stderr)
            return 2

    for violation in violations:
        print(violation)
    if violations:
        print(f"\n{len(violations)} style violation(s) across {len(paths)} file(s)")
        return 1
    if src_only or tests_only:
        print(f"{label}: clean ({len(paths)} file(s) checked)")
    else:
        print(f"style: clean ({len(paths)} file(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
