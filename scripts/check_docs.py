#!/usr/bin/env python3
"""Check that every relative link in the repository's Markdown resolves.

Docs drift silently: a file gets renamed, a section moves, and the README sends the
next reader — or the next agent — to something that is not there. That is the
failure this repository is built to argue against, so the check is cheap enough to
run on every push.

Checked
  * ``[text](target)`` and ``![alt](target)`` links whose target is relative to the
    file that contains them
  * targets that do not exist are reported with file and line

Ignored (deliberately)
  * external targets: anything with a scheme (``https:``, ``mailto:``, ``tel:``)
  * pure anchors: ``#section``
  * links inside fenced code blocks, which are examples rather than links

Usage: python scripts/check_docs.py [PATH ...]        (default: the repo root)
Exit codes: 0 clean, 1 broken links, 2 bad usage.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

SKIP_DIRS = frozenset(
    {".git", "__pycache__", ".venv", "venv", "node_modules", "build", "dist", ".budgetloop"}
)
#: ``[text](target)`` and ``![alt](target)``; target may carry a #anchor.
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
#: Inline code spans, which is how this repository writes file paths in prose.
CODE_SPAN_PATTERN = re.compile(r"`([^`]+)`")
#: A repo path: no spaces, no flags, and a known source/data extension.
PATH_LIKE_PATTERN = re.compile(
    r"^(?:\.{0,2}/)?[\w.-]+(?:/[\w.-]+)*\.(?:py|md|json|toml|ya?ml|csv|txt|cfg|ini)$"
)
SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
FENCE_PATTERN = re.compile(r"^\s*(```|~~~)")
#: Runtime trees that legitimately do not exist in a fresh clone.
RUNTIME_PREFIXES = (".budgetloop/",)
#: The first line of a fenced block that should be read as a layout of the repo.
LAYOUT_ROOT = (".", "./")
#: Lines carrying this marker are skipped, for prose about *other* repositories.
#: The marker is an HTML comment, so it does not render in a Markdown viewer.
SKIP_MARKER = "docs-check: skip"


@dataclass(frozen=True)
class Finding:
    """One broken link, located well enough to fix."""

    path: str
    line: int
    target: str
    resolved: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.target}] does not exist"


def markdown_files(roots: Iterable[Path]) -> list[Path]:
    """Every Markdown file under ``roots``, skipping generated and vendored trees."""
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix == ".md":
                found.append(root)
            continue
        for path in sorted(root.rglob("*.md")):
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


def _is_checkable(target: str) -> bool:
    if not target or target.startswith("#"):
        return False
    if target.startswith(RUNTIME_PREFIXES):
        return False
    return not SCHEME_PATTERN.match(target)


def _is_path_like(text: str) -> bool:
    """Whether an inline code span is a repo path rather than a name or a command.

    Deliberately conservative: a path must contain a slash and must not be a glob,
    a placeholder or a command. Bare names such as ``AGENTS.md`` are prose, and
    flagging them would train the reader to ignore this check.
    """
    if " " in text or text.startswith("-"):
        return False
    if text.startswith(RUNTIME_PREFIXES) or not PATH_LIKE_PATTERN.match(text):
        return False
    if any(character in text for character in "*{}<>~$"):
        return False
    return "/" in text


def _repo_root(start: Path) -> Path:
    """Walk up from ``start`` to the repository root (the directory holding .git)."""
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def _missing(path: Path, target: str) -> bool:
    """Whether a path-like target resolves to nothing, relatively or from the root."""
    relative = target.split("#", 1)[0]
    if (path.parent / relative).exists():
        return False
    return not (_repo_root(path) / relative.lstrip("./")).exists()


def check_file(path: Path) -> list[Finding]:
    """Return every relative link or path-like code span that does not exist."""
    findings: list[Finding] = []
    in_fence = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if SKIP_MARKER in line:
            continue  # an explicit exception, usually prose about another repository
        targets = [match.group(1) for match in LINK_PATTERN.finditer(line)]
        targets += [span for span in CODE_SPAN_PATTERN.findall(line) if _is_path_like(span)]
        for target in targets:
            if not _is_checkable(target):
                continue
            if _missing(path, target):
                findings.append(
                    Finding(_normalise(path), number, target, target.split("#", 1)[0])
                )
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    """Check every Markdown file under the given paths and print the broken links."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in arguments
    targets = [Path(argument) for argument in arguments if not argument.startswith("--")]
    roots = targets or [Path(".")]
    missing = [root for root in roots if not root.exists()]
    if missing:
        for root in missing:
            print(f"no such path: {root}", file=sys.stderr)
        return 2

    paths = markdown_files(roots)
    findings: list[Finding] = []
    for path in paths:
        findings.extend(check_file(path))

    if as_json:
        print(
            json.dumps(
                [
                    {
                        "path": finding.path,
                        "line": finding.line,
                        "target": finding.target,
                        "resolved": finding.resolved,
                    }
                    for finding in findings
                ],
                indent=2,
            )
        )
    else:
        for finding in findings:
            print(finding)
        if findings:
            print(f"\n{len(findings)} broken link(s) across {len(paths)} Markdown file(s)")
        else:
            print(f"docs: clean ({len(paths)} Markdown file(s) checked)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
