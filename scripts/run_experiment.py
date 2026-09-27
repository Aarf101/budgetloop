#!/usr/bin/env python3
"""Prepare the Lab 2 before/after so both arms start from one commit.

The reviewer's objection to the first attempt was fair: the arms started from
different trees, so the numbers were a story rather than a comparison. This script
removes that mistake by construction.

* ``context/`` — a clone of this repository as it is: AGENTS.md, the skills, the docs
* ``cold/``    — a clone with the agent governance removed, in a commit, so that no
  command can restore it from history

It cannot drive your agent, and it does not pretend to. What it does is make the two
arms identical except for the variable under test, print the prompts and the
recording commands, and refuse to hand you a comparison you would have to explain
away later.

Usage:
    python3 scripts/run_experiment.py prepare [--base REV] [--out DIR]
    python3 scripts/run_experiment.py record --scorecard PATH --arm cold --task TASK-A
"""

from __future__ import annotations

import argparse
import shutil
import subprocess  # noqa: S404 - argv lists only, never a shell
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path("/tmp/lab2-experiment")
#: A revision where the protocol tasks are still open, since HEAD may already have them.
BASE_WITHOUT_TASKS = "01bc767"
#: Governance the treatment consists of, and the tests that assert it exists.
GOVERNANCE_PATHS = ("AGENTS.md", "CLAUDE.md", "REFLECTION.md", "docs", "skills", ".claude")
GOVERNANCE_TESTS = (
    "tests/test_skill_validator.py",
    "tests/test_experiment_scorer.py",
    "tests/test_budget_reporter.py",
    "tests/test_docs_checker.py",
)
TASKS = (
    (
        "TASK-A",
        "Add a check that fails if any module in src/ is missing a docstring, and wire"
        " it into the existing checks.",
    ),
    (
        "TASK-B",
        "Prove that `make demo` can never write a file, and make that proof part of"
        " the checks.",
    ),
    ("TASK-C", "Add a second skill that reports how a run's token budget was spent."),
)


def _run(argv: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing output, and never raising on a non-zero exit."""
    return subprocess.run(  # noqa: S603 - argv list, shell=False
        list(argv), cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False
    )


def _clone(destination: Path, base: str) -> None:
    """Clone this repository into ``destination`` and check out ``base``."""
    if destination.exists():
        shutil.rmtree(destination)
    clone = _run(["git", "clone", "--quiet", "--local", str(REPO_ROOT), str(destination)])
    if clone.returncode != 0:
        raise SystemExit(f"git clone failed: {clone.stderr.strip()}")
    checkout = _run(["git", "checkout", "--quiet", base], cwd=destination)
    if checkout.returncode != 0:
        raise SystemExit(f"cannot check out {base!r}: {checkout.stderr.strip()}")


def _strip_governance(cold: Path) -> None:
    """Remove the treatment from the cold arm and commit the removal."""
    for relative in GOVERNANCE_PATHS:
        target = cold / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    for relative in GOVERNANCE_TESTS:
        (cold / relative).unlink(missing_ok=True)
    # Drop the checks whose subject matter was just removed, whatever the base's
    # Makefile looked like. Older bases have a shorter `check:` line than HEAD does.
    makefile = cold / "Makefile"
    patched_lines: list[str] = []
    for line in makefile.read_text(encoding="utf-8").splitlines():
        if line.startswith("check:"):
            tokens = [token for token in line.split(" ") if token not in {"skill-lint", "docs"}]
            line = " ".join(tokens)
        patched_lines.append(line)
    makefile.write_text("\n".join(patched_lines) + "\n", encoding="utf-8")
    _run(["git", "add", "-A"], cwd=cold)
    _run(
        ["git", "commit", "--quiet", "-m", "cold arm: agent governance removed (experiment)"],
        cwd=cold,
    )


def _head(directory: Path) -> str:
    return _run(["git", "rev-parse", "--short", "HEAD"], cwd=directory).stdout.strip()


def _already_done(directory: Path) -> bool:
    makefile = directory / "Makefile"
    return makefile.is_file() and "tests-docstrings" in makefile.read_text(encoding="utf-8")


def cmd_prepare(args: argparse.Namespace) -> int:
    """Build both arms from one base revision and print how to run them."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    context_arm, cold_arm = out / "context", out / "cold"
    _clone(context_arm, args.base)
    _clone(cold_arm, args.base)
    _strip_governance(cold_arm)

    print(f"base revision: {_head(context_arm)}   ({args.base})")
    print(f"  context arm: {context_arm}  (governance present)")
    print(f"  cold arm:    {cold_arm}  (governance removed, unrecoverable from history)")
    if _already_done(context_arm):
        print(
            "\nWARNING: this base already implements the protocol tasks, so they are no-ops\n"
            f"here. Use --base {BASE_WITHOUT_TASKS} for a base where TASK-A/B/C are open."
        )
    for arm in (context_arm, cold_arm):
        gate = _run(["make", "check"], cwd=arm)
        status = "green" if gate.returncode == 0 else "RED: fix before running the experiment"
        print(f"  {arm.name}: make check -> {status}")
    print("\nprompts: one per attempt, in a fresh agent session, in that arm's directory:")
    for task_id, prompt in TASKS:
        print(f"  {task_id}: {prompt}")
    scorecard = REPO_ROOT / "docs" / "experiment-scorecard.csv"
    print("\nafter each attempt, record it immediately (turns or n/a):")
    print(
        "  python3 scripts/run_experiment.py record \\\n"
        f"      --scorecard {scorecard} --arm cold --task TASK-A \\\n"
        f"      --check 1 --edits 0 --prompts 0 --turns 6 --notes \"base {_head(context_arm)}\""
    )
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Append one attempt to the scorecard, with the fields the scorer expects."""
    if args.turns.lower() in {"n/a", "na", "unknown", "none"}:
        turns = ""
    else:
        try:
            turns = str(int(args.turns))
        except ValueError as exc:
            raise SystemExit(f"--turns must be an integer or n/a, got {args.turns!r}") from exc
    if args.check not in {0, 1}:
        raise SystemExit("--check must be 0 (red) or 1 (green)")
    row = (
        f"{args.arm},{args.task},{args.attempt},{args.check},{args.edits},"
        f"{args.prompts},{turns},{args.notes}"
    )
    scorecard = Path(args.scorecard)
    if not scorecard.is_file():
        raise SystemExit(f"no scorecard at {scorecard}")
    with scorecard.open("a", encoding="utf-8") as handle:
        handle.write(row + "\n")
    print(f"recorded: {row}")
    print(f"score it with: python3 scripts/score_experiment.py {scorecard}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Command line surface: build the arms, or record an attempt."""
    parser = argparse.ArgumentParser(
        prog="run_experiment",
        description="Prepare the Lab 2 arms from one commit, and record attempts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="build the cold and context arms")
    prepare.add_argument("--base", default="HEAD", help="revision both arms start from")
    prepare.add_argument("--out", default=str(DEFAULT_OUT), help="directory for the two clones")
    prepare.set_defaults(handler=cmd_prepare)

    record = subparsers.add_parser("record", help="append one attempt to the scorecard")
    record.add_argument("--scorecard", required=True)
    record.add_argument("--arm", choices=["cold", "context"], required=True)
    record.add_argument("--task", required=True)
    record.add_argument("--attempt", type=int, default=1)
    record.add_argument("--check", type=int, required=True, help="1 when the check went green")
    record.add_argument("--edits", type=int, default=0, help="times a human edited the code")
    record.add_argument("--prompts", type=int, default=0, help="times a human re-explained")
    record.add_argument("--turns", default="n/a", help="agent messages, or n/a when unmeasured")
    record.add_argument("--notes", default="")
    record.set_defaults(handler=cmd_record)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: prepare the arms, or record one attempt."""
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
