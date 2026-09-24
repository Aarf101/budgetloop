#!/usr/bin/env python3
"""Validate skills against the Agent Skills specification (agentskills.io).

A skill is a folder containing ``SKILL.md`` whose YAML frontmatter carries at
least ``name`` and ``description``. This checker enforces the parts of the spec a
machine can decide, so a malformed skill fails ``make check`` instead of failing
silently when an agent tries to activate it.

Errors (the spec is explicit):

* ``SKILL.md`` exists, and its frontmatter is delimited by ``---``
* ``name``: required; <= 64 chars; lowercase letters, digits and single hyphens;
  no leading or trailing hyphen; **must equal the parent directory name**
* ``description``: required, non-empty, <= 1024 characters
* ``compatibility``: <= 500 characters when present
* ``metadata``: a flat map of string values when present
* no frontmatter construct this tool cannot read (it supports a deliberate subset
  of YAML: ``key: value`` and one level of nesting)

Advice (warnings; ``--strict`` promotes them to errors):

* the body stays under 500 lines and its instructions fit a rough token budget,
  because the whole file is loaded into context on activation
* the description says *when* to use the skill, not only what it does

Usage: python scripts/validate_skill.py PATH [PATH ...] [--strict] [--json]
Exit codes: 0 clean, 1 findings, 2 bad usage.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
#: Anything but a single lowercase-alphanumeric run joined by single hyphens.
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: Progressive-disclosure guidance from the spec, expressed as advisories.
BODY_LINE_ADVISORY = 500
BODY_TOKEN_ADVISORY = 5_000
DESCRIPTION_ADVISORY = 40
CHARS_PER_TOKEN = 4
FRONTMATTER_DELIMITER = "---"
KNOWN_FIELDS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)


@dataclass(frozen=True)
class Finding:
    """One validation result, located and rule-tagged."""

    path: Path
    level: str
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: [{self.level}:{self.rule}] {self.message}"


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return ``(frontmatter, body)``; frontmatter is None when absent/unterminated."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIMITER:
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONTMATTER_DELIMITER:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None, text


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_simple_yaml(raw: str) -> tuple[dict[str, Any], list[str]]:
    """Parse the flat subset of YAML the spec's frontmatter needs.

    Supported: ``key: value``, optional quoting, and one level of nesting (as used
    by ``metadata``). Anything else is reported instead of guessed -- validating a
    skill with a misread field would be worse than refusing to validate it.
    """
    fields: dict[str, Any] = {}
    problems: list[str] = []
    nested_target: dict[str, str] | None = None
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            if nested_target is None:
                problems.append(f"line {number}: indented value without a parent key")
                continue
            key, separator, value = line.strip().partition(":")
            if not separator or not value.strip():
                problems.append(f"line {number}: nested entry needs a `key: value` form")
                continue
            nested_target[key.strip()] = _unquote(value.strip())
            continue
        if line.lstrip().startswith("-") or ":" not in line:
            problems.append(
                f"line {number}: only `key: value` pairs and one nested map are supported"
            )
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value:
            fields[key] = _unquote(value)
            nested_target = None
        else:
            child: dict[str, str] = {}
            fields[key] = child
            nested_target = child
    return fields, problems


def _check_name(name: Any, skill_dir: Path, skill_file: Path) -> list[Finding]:
    if not isinstance(name, str) or not name.strip():
        return [Finding(skill_file, "error", "name", "`name` is required and must be a string")]
    findings: list[Finding] = []
    if len(name) > NAME_MAX:
        findings.append(
            Finding(
                skill_file,
                "error",
                "name",
                f"`name` is {len(name)} characters, max {NAME_MAX}",
            )
        )
    if not NAME_PATTERN.match(name):
        findings.append(
            Finding(
                skill_file,
                "error",
                "name",
                f"`name` {name!r} must be lowercase letters, digits and single hyphens",
            )
        )
    if name != skill_dir.name:
        findings.append(
            Finding(
                skill_file,
                "error",
                "name",
                f"`name` {name!r} must match its directory name {skill_dir.name!r}",
            )
        )
    return findings


def _check_description(description: Any, skill_file: Path, strict: bool) -> list[Finding]:
    if not isinstance(description, str) or not description.strip():
        return [
            Finding(
                skill_file,
                "error",
                "description",
                "`description` is required and must be a non-empty string",
            )
        ]
    findings: list[Finding] = []
    if len(description) > DESCRIPTION_MAX:
        findings.append(
            Finding(
                skill_file,
                "error",
                "description",
                f"`description` is {len(description)} characters, max {DESCRIPTION_MAX}",
            )
        )
    if len(description) < DESCRIPTION_ADVISORY or "when" not in description.lower():
        findings.append(
            Finding(
                skill_file,
                "warning" if not strict else "error",
                "description",
                "describe *when* to use the skill, not only what it does "
                "(this is the text an agent matches tasks against)",
            )
        )
    return findings


def validate_skill(skill_dir: Path, *, strict: bool = False) -> list[Finding]:
    """Check one skill folder against the spec, returning every finding."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return [Finding(skill_dir, "error", "skill-md", "SKILL.md is missing")]

    text = skill_file.read_text(encoding="utf-8")
    raw, body = split_frontmatter(text)
    if raw is None:
        reason = (
            "frontmatter must open on the first line with ---"
            if not text.startswith(FRONTMATTER_DELIMITER)
            else "frontmatter is missing its closing ---"
        )
        return [Finding(skill_file, "error", "frontmatter", reason)]

    fields, problems = parse_simple_yaml(raw)
    findings = [Finding(skill_file, "error", "frontmatter-syntax", line) for line in problems]
    findings += _check_name(fields.get("name"), skill_dir, skill_file)
    findings += _check_description(fields.get("description"), skill_file, strict)

    compatibility = fields.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, str) or len(compatibility) > COMPATIBILITY_MAX
    ):
        findings.append(
            Finding(
                skill_file,
                "error",
                "compatibility",
                f"`compatibility` must be a string of at most {COMPATIBILITY_MAX} characters",
            )
        )
    metadata = fields.get("metadata")
    if metadata is not None and (
        not isinstance(metadata, dict) or not all(isinstance(v, str) for v in metadata.values())
    ):
        findings.append(
            Finding(
                skill_file,
                "error",
                "metadata",
                "`metadata` must be a map of string keys to string values",
            )
        )
    allowed_tools = fields.get("allowed-tools")
    if allowed_tools is not None and not isinstance(allowed_tools, str):
        findings.append(
            Finding(
                skill_file,
                "error",
                "allowed-tools",
                "`allowed-tools` must be a space-separated string",
            )
        )
    for key in fields:
        if key not in KNOWN_FIELDS:
            findings.append(
                Finding(
                    skill_file,
                    "warning" if not strict else "error",
                    "unknown-field",
                    f"`{key}` is not a spec field; clients may ignore it",
                )
            )

    level = "warning" if not strict else "error"
    body_lines = len(body.strip().splitlines())
    body_tokens = max(1, len(body) // CHARS_PER_TOKEN)
    if not body.strip():
        findings.append(
            Finding(
                skill_file,
                level,
                "body",
                "the body is empty: a skill with no steps does nothing",
            )
        )
    if body_lines > BODY_LINE_ADVISORY:
        findings.append(
            Finding(
                skill_file,
                level,
                "progressive-disclosure",
                f"body is {body_lines} lines; keep it under {BODY_LINE_ADVISORY} and move "
                "detail into references/ so it loads only when needed",
            )
        )
    if body_tokens > BODY_TOKEN_ADVISORY:
        findings.append(
            Finding(
                skill_file,
                level,
                "progressive-disclosure",
                f"body is roughly {body_tokens} tokens, over the advised {BODY_TOKEN_ADVISORY}",
            )
        )
    return findings


def discover_skills(paths: Iterable[Path]) -> list[Path]:
    """Find skill directories -- folders containing SKILL.md -- under the given paths."""
    found: set[Path] = set()
    for path in paths:
        if path.is_file() and path.name == "SKILL.md":
            found.add(path.parent)
        elif (path / "SKILL.md").is_file():
            found.add(path)
        elif path.is_dir():
            found.update(
                child for child in path.iterdir() if (child / "SKILL.md").is_file()
            )
    return sorted(found)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate every skill found under the given paths and print the findings."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in arguments
    as_json = "--json" in arguments
    targets = [Path(argument) for argument in arguments if not argument.startswith("--")]
    if not targets:
        print("usage: validate_skill.py PATH [PATH ...] [--strict] [--json]", file=sys.stderr)
        return 2
    missing = [target for target in targets if not target.exists()]
    if missing:
        for path in missing:
            print(f"no such path: {path}", file=sys.stderr)
        return 2

    skills = discover_skills(targets)
    if not skills:
        joined = ", ".join(str(target) for target in targets)
        print(f"no skills found under: {joined}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    for skill in skills:
        findings.extend(validate_skill(skill, strict=strict))

    errors = [finding for finding in findings if finding.level == "error"]
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "path": str(finding.path),
                        "level": finding.level,
                        "rule": finding.rule,
                        "message": finding.message,
                    }
                    for finding in findings
                ],
                indent=2,
            )
        )
    else:
        for finding in findings:
            print(finding)
        summary = (
            f"{len(skills)} skill(s) checked: {len(errors)} error(s),"
            f" {len(findings) - len(errors)} warning(s)"
        )
        print(("FAIL " if errors else "ok   ") + summary)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
