#!/usr/bin/env python3
"""Score a Lab 2 before/after experiment from a CSV scorecard.

One row per attempt:

    arm,task_id,attempt,check_passed,human_edits,clarification_prompts,agent_turns,notes

``arm`` is ``cold`` (no AGENTS.md, no skill) or ``context`` (both present, skill
activated). A row counts as a **first-pass success** only when the task's check
passed, the human edited nothing, and at most one clarification prompt was needed.
Every other combination is a failure -- a generous definition measures nothing, and
the interesting number is how often the agent claims success while a check is red.

Usage:
    python scripts/score_experiment.py docs/experiment-scorecard.csv [--json]
    python scripts/score_experiment.py --template
Exit codes: 0 report produced, 2 bad usage or unreadable scorecard.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ARMS = ("cold", "context")
FIELDS = (
    "arm",
    "task_id",
    "attempt",
    "check_passed",
    "human_edits",
    "clarification_prompts",
    "agent_turns",
    "notes",
)
#: At most this many clarification prompts still counts as a first pass.
CLARIFICATION_ALLOWANCE = 1

TEMPLATE = """\
# Lab 2 scorecard -- one row per attempt. Copy to docs/experiment-scorecard.csv.
# arm      : cold (no AGENTS.md, no skill) | context (AGENTS.md + skill)
# check_passed        : 1 if the task's stated check was green at the end
# human_edits         : how many times a human edited the agent's code
# clarification_prompts: how many times the human had to explain something again
# agent_turns         : messages the agent took, including failed attempts
arm,task_id,attempt,check_passed,human_edits,clarification_prompts,agent_turns,notes
cold,TASK-A,1,,,,,replace with your measurement
context,TASK-A,1,,,,,replace with your measurement
cold,TASK-B,1,,,,,
context,TASK-B,1,,,,,
cold,TASK-C,1,,,,,
context,TASK-C,1,,,,,
"""


@dataclass(frozen=True)
class Attempt:
    """One recorded attempt, with the derived first-pass verdict."""

    arm: str
    task_id: str
    attempt: int
    check_passed: bool
    human_edits: int
    clarification_prompts: int
    agent_turns: int

    @property
    def first_pass(self) -> bool:
        return (
            self.check_passed
            and self.human_edits == 0
            and self.clarification_prompts <= CLARIFICATION_ALLOWANCE
        )


def _as_int(row: dict[str, str], field: str, line: int) -> int:
    raw = (row.get(field) or "").strip()
    if not raw:
        raise ValueError(f"line {line}: {field} is empty")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"line {line}: {field} must be an integer, got {raw!r}") from exc


def _attempt_from_row(row: dict[str, str], offset: int) -> Attempt:
    """Turn one CSV row into an Attempt, refusing anything ambiguous."""
    arm = (row.get("arm") or "").strip().lower()
    if arm not in ARMS:
        raise ValueError(f"line {offset}: arm must be one of {ARMS}, got {arm!r}")
    return Attempt(
        arm=arm,
        task_id=(row.get("task_id") or "").strip() or f"line-{offset}",
        attempt=_as_int(row, "attempt", offset),
        check_passed=_as_int(row, "check_passed", offset) == 1,
        human_edits=_as_int(row, "human_edits", offset),
        clarification_prompts=_as_int(row, "clarification_prompts", offset),
        agent_turns=_as_int(row, "agent_turns", offset),
    )


def read_scorecard(path: Path) -> list[Attempt]:
    """Read a scorecard, skipping blank lines, comments and untouched template rows."""
    if not path.is_file():
        raise ValueError(f"no such scorecard: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [line for line in handle if line.strip() and not line.startswith("#")]
    attempts: list[Attempt] = []
    failures: list[str] = []
    for offset, row in enumerate(csv.DictReader(rows), start=2):
        if (row.get("notes") or "").strip().startswith(("replace", "example")):
            continue  # an untouched template row is not evidence
        try:
            attempts.append(_attempt_from_row(row, offset))
        except ValueError as exc:
            failures.append(str(exc))
    if not attempts:
        detail = failures[0] if failures else "the header row is all there is"
        raise ValueError(f"{path} has no measured rows: {detail} -- fill it in before scoring")
    return attempts


def _mean(values: list[int]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def summarise(attempts: Sequence[Attempt]) -> dict[str, Any]:
    """Aggregate attempts per arm and per task, then compute the delta."""
    arms: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        rows = [attempt for attempt in attempts if attempt.arm == arm]
        if not rows:
            continue
        first_pass = sum(1 for row in rows if row.first_pass)
        arms[arm] = {
            "attempts": len(rows),
            "first_pass": first_pass,
            "first_pass_rate": round(first_pass / len(rows), 3),
            "check_pass_rate": round(sum(1 for row in rows if row.check_passed) / len(rows), 3),
            "mean_agent_turns": _mean([row.agent_turns for row in rows]),
            "mean_human_edits": _mean([row.human_edits for row in rows]),
            "mean_clarifications": _mean([row.clarification_prompts for row in rows]),
            "inflated_success": sum(
                1 for row in rows if row.check_passed and row.human_edits > 0
            ),
        }

    tasks: dict[str, dict[str, Any]] = {}
    for task_id in sorted({attempt.task_id for attempt in attempts}):
        per_task: dict[str, Any] = {}
        for arm in ARMS:
            rows = [
                attempt
                for attempt in attempts
                if attempt.arm == arm and attempt.task_id == task_id
            ]
            if rows:
                per_task[arm] = {
                    "attempts": len(rows),
                    "first_pass": sum(1 for row in rows if row.first_pass),
                    "mean_agent_turns": _mean([row.agent_turns for row in rows]),
                }
        tasks[task_id] = per_task

    delta = None
    if "cold" in arms and "context" in arms:
        delta = round(arms["context"]["first_pass_rate"] - arms["cold"]["first_pass_rate"], 3)

    return {
        "attempts": len(attempts),
        "arms": arms,
        "tasks": tasks,
        "first_pass_delta": delta,
        "caveat": (
            "single-experiment rates are noisy; report the attempt count and treat the "
            "delta as a direction of travel, not a property of the model"
        ),
    }


def render(summary: dict[str, Any]) -> str:
    """Render the summary as plain text for a terminal or a report."""
    lines = [f"attempts recorded: {summary['attempts']}", ""]
    lines.append(
        f"{'arm':<9}{'attempts':>10}{'first-pass':>16}{'check green':>13}{'turns':>8}{'edits':>7}"
    )
    for arm, stats in summary["arms"].items():
        rate = f"{stats['first_pass']}/{stats['attempts']} ({stats['first_pass_rate']:.0%})"
        lines.append(
            f"{arm:<9}{stats['attempts']:>10}{rate:>16}"
            f"{stats['check_pass_rate']:>13.0%}{stats['mean_agent_turns']:>8}"
            f"{stats['mean_human_edits']:>7}"
        )
    lines += ["", "per task:"]
    for task_id, per_task in summary["tasks"].items():
        parts = [
            f"{arm}: {stats['first_pass']}/{stats['attempts']} first-pass, "
            f"{stats['mean_agent_turns']} turns"
            for arm, stats in per_task.items()
        ]
        lines.append(f"  {task_id:<12} " + " | ".join(parts))
    delta = summary["first_pass_delta"]
    lines += [
        "",
        f"delta (context - cold): {'n/a' if delta is None else f'{delta:+.0%}'}",
        f"note: {summary['caveat']}",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Read a scorecard (or print the template) and report the measured rates."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--template" in arguments:
        print(TEMPLATE, end="")
        return 0
    as_json = "--json" in arguments
    targets = [argument for argument in arguments if not argument.startswith("--")]
    if len(targets) != 1:
        print("usage: score_experiment.py SCORECARD.csv [--json]", file=sys.stderr)
        print("       score_experiment.py --template", file=sys.stderr)
        return 2
    try:
        attempts = read_scorecard(Path(targets[0]))
    except ValueError as exc:
        print(f"cannot score {targets[0]}: {exc}", file=sys.stderr)
        return 2
    summary = summarise(attempts)
    print(json.dumps(summary, indent=2) if as_json else render(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
