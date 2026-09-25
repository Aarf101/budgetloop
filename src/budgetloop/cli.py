"""Command line interface.

Three verbs, chosen to match the three things a learner needs to *see*:

* ``run``     -- drive the loop once, streaming each step and printing the budget ledger.
* ``demo``    -- a scripted, offline run against this very repo that forces a
                 compaction, so "context is a budget, not a bucket" is visible,
                 not just asserted.
* ``inspect`` -- reopen a trace file and report what actually happened.

Exit codes: 0 = completed, 1 = ended on a guardrail or provider fault, 2 = usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .loop import DEFAULT_SYSTEM_PROMPT, AgentLoop, StepRecord
from .messages import ModelReply
from .providers import (
    DEFAULT_MODEL,
    AnthropicProvider,
    EchoProvider,
    Provider,
    ProviderError,
    ScriptedProvider,
    final_reply,
    tool_call_reply,
)
from .tools import ApproveFn, Tool, Workspace, default_checks, default_registry

EXIT_OK = 0
EXIT_GUARDRAIL = 1
EXIT_USAGE = 2

#: Appended to the system prompt when a project brief is supplied, so the run
#: always keeps its stop conditions even when the brief is long.
BRIEF_HEADER = (
    "The repository you are working in publishes the following conventions."
    " Treat them as authoritative, and prefer them over your defaults.\n\n"
)


def _read_text(path: str, *, limit: int) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc
    if len(text) > limit:
        text = text[:limit] + f"\n... [truncated at {limit} characters by --brief-chars]"
    return text


def build_system_prompt(args: argparse.Namespace) -> str:
    """Compose the system prompt: contract first, project knowledge second."""
    parts = [DEFAULT_SYSTEM_PROMPT]
    if getattr(args, "system_file", None):
        parts.append(_read_text(args.system_file, limit=args.brief_chars))
    if getattr(args, "brief", None):
        parts.append(BRIEF_HEADER + _read_text(args.brief, limit=args.brief_chars))
    return "\n\n".join(parts)


def load_script(path: str) -> list[ModelReply]:
    """Load a deterministic reply script (the no-model path, usable in CI).

    Format: a JSON array whose entries are either
    ``{"tool": "read_file", "arguments": {...}}`` or ``{"final": "text"}``.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load script {path}: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"script {path} must be a non-empty JSON array")
    replies: list[ModelReply] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise SystemExit(f"script entry {index} must be an object")
        if "final" in entry:
            replies.append(final_reply(str(entry["final"])))
        elif "tool" in entry:
            replies.append(
                tool_call_reply(
                    str(entry["tool"]),
                    dict(entry.get("arguments") or {}),
                    f"call-{index}",
                    note=str(entry.get("note", "")),
                )
            )
        else:
            raise SystemExit(f"script entry {index} needs either 'tool' or 'final'")
    return replies


def build_provider(args: argparse.Namespace) -> Provider:
    """Construct the provider, refusing the network unless explicitly allowed."""
    kind = args.provider
    if kind == "echo":
        return EchoProvider()
    if kind == "scripted":
        if not getattr(args, "script", None):
            return ScriptedProvider(replies=[], name="scripted(empty)")
        return ScriptedProvider(replies=load_script(args.script), name="scripted")
    if kind == "anthropic":
        if not args.allow_network:
            raise SystemExit(
                "refusing to use a live provider without --allow-network "
                "(the offline providers exist so this is a choice, not an accident)"
            )
        try:
            return AnthropicProvider(
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
                model=args.model,
            )
        except ProviderError as exc:
            raise SystemExit(str(exc)) from exc
    raise SystemExit(f"unknown provider {kind!r}")


def make_approval_policy(args: argparse.Namespace) -> ApproveFn:
    """Fail closed: approve only with ``--yes`` or an interactive ``y`` from a human.

    All narration goes to stderr, so ``--json`` output on stdout stays parseable.
    """

    def policy(tool: Tool, arguments: dict[str, Any]) -> bool:
        target = arguments.get("path", "(no path argument)")
        size = len(str(arguments.get("content", "")))
        print(
            f"[approval] {tool.name} wants to write {target} ({size} chars)",
            file=sys.stderr,
        )
        if args.yes:
            print("[approval] allowed by --yes (operator pre-authorised this run)", file=sys.stderr)
            return True
        if not sys.stdin.isatty():
            print(
                "[approval] DENIED: no interactive terminal and --yes was not passed",
                file=sys.stderr,
            )
            return False
        # The prompt goes to stderr: in --json mode stdout is machine-readable, and
        # input() always writes its prompt to stdout.
        print("[approval] apply this change? [y/N] ", file=sys.stderr, end="")
        try:
            answer = input().strip().lower()
        except EOFError:
            print("[approval] denied: stdin closed before answering", file=sys.stderr)
            return False
        allowed = answer in {"y", "yes"}
        print(f"[approval] {'allowed' if allowed else 'denied'} by operator", file=sys.stderr)
        return allowed

    return policy


def print_step(record: StepRecord) -> None:
    """Stream each turn: this is the 'observe' half of the loop, made visible."""
    calls = ", ".join(call["name"] for call in record.tool_calls) or "-"
    print(f"  step {record.step:>2} [{record.note}] {calls} | window {record.window_tokens} tokens")
    for observation in record.observations:
        mark = "ok " if observation["ok"] else "ERR"
        line = f"          {mark} {observation['tool']} ({observation['chars']} chars)"
        if observation["error"]:
            line += f"  {observation['error'][:90]}"
        print(line)


def cmd_run(args: argparse.Namespace) -> int:
    """Drive the loop once on a task and print the outcome and the ledger."""
    workspace = Workspace(args.workspace)
    provider = build_provider(args)
    policy = make_approval_policy(args)
    registry = default_registry(workspace, approve=policy, checks=default_checks())
    loop = AgentLoop(
        provider,
        registry,
        budget=args.budget,
        max_steps=args.max_steps,
        max_output_tokens=args.max_output_tokens,
        keep_recent_observations=args.keep_recent_observations,
        system_prompt=build_system_prompt(args),
        include_tool_catalog=not args.no_tool_catalog,
        approve=policy,
        trace_dir=None if args.no_trace else args.trace_dir,
        on_step=None if args.json else print_step,
    )
    if not args.json:
        name = getattr(provider, "name", type(provider).__name__)
        print(f"budgetloop run | provider={name} | workspace={workspace.root}")
        print(f"task: {args.task}\n")
    result = loop.run(args.task)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"\nanswer: {result.answer}\n")
        print(result.summary_line())
        print(result.ledger_report)
        if result.trace_path:
            print(f"trace: {result.trace_path}")
    return EXIT_OK if result.ok else EXIT_GUARDRAIL


def _demo_replies() -> list[ModelReply]:
    """The fixed script the demo replays: read three files, ask for a write, stop."""
    return [
        tool_call_reply("list_files", {"pattern": "**/*.py"}, "demo-1"),
        tool_call_reply("read_file", {"path": "pyproject.toml"}, "demo-2"),
        tool_call_reply("read_file", {"path": ".gitignore"}, "demo-3"),
        tool_call_reply("read_file", {"path": "README.md"}, "demo-4"),
        tool_call_reply(
            "write_file",
            {"path": "demo-scratch.txt", "content": "the model asked for this write\n"},
            "demo-5",
        ),
        final_reply(
            "Audit complete. I listed the Python modules, read three project files, and "
            "attempted one write. The write was refused because this demo runs with no "
            "approval policy, so nothing on disk changed. As the window filled, the harness "
            "evicted the oldest tool exchange into a one-line summary instead of failing: "
            "the transcript is lossy on purpose, and the ledger above records exactly what "
            "was lost."
        ),
    ]


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the script offline twice: once to measure the cost, once on a budget.

    Sizing a budget is part of the skill, so the demo does it rather than hardcoding
    a number: pass one runs with effectively no limit to learn what the script
    really costs, pass two runs on a budget just tight enough to force a
    compaction and still finish.
    """
    task = "Audit this repository's context budget and report what you find."
    workspace = Workspace(args.workspace, max_read_bytes=1_200)
    # No approval policy on purpose: this demonstrates the fail-closed path.
    registry = default_registry(workspace)
    limits = {"budget": 1_000_000, "max_steps": 8, "trace_dir": None}
    unchallenged = AgentLoop(
        ScriptedProvider(replies=_demo_replies(), name="scripted(demo)"),
        registry,
        on_step=None,
        **limits,
    ).run(task)
    budget = args.budget or max(600, int(unchallenged.peak_tokens * 1.15))
    print("DEMO: offline, scripted provider. No model, no network, no writes.")
    print(f"workspace={workspace.root}   reads capped at 1200 bytes")
    print(f"measured cost with no budget pressure: {unchallenged.peak_tokens} tokens")
    print(f"budget chosen for the real pass:       {budget} tokens")
    print(f"  (tight enough to force compaction, loose enough to finish)\n")
    result = AgentLoop(
        ScriptedProvider(replies=_demo_replies(), name="scripted(demo)"),
        registry,
        budget=budget,
        max_steps=8,
        trace_dir=None,
        on_step=print_step,
    ).run(task)
    print(f"\nanswer: {result.answer}\n")
    print(result.summary_line())
    print(result.ledger_report)
    print(
        "\nWhat to notice:\n"
        "  1. The window is a budget: every turn is charged, and the ledger shows the balance.\n"
        "  2. When the budget ran out the oldest observation was summarised away -- lossy,\n"
        "     bounded, and recorded -- instead of the run crashing.\n"
        "  3. The write was refused by policy, not by luck: no approval policy means no write.\n"
        "  4. The refusal came back to the model as ordinary 'ERROR: ...' observation text."
    )
    return EXIT_OK if result.ok else EXIT_GUARDRAIL


def cmd_inspect(args: argparse.Namespace) -> int:
    """Reopen a trace and report what actually happened."""
    try:
        payload = json.loads(Path(args.trace).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read trace {args.trace}: {exc}") from exc
    if args.json:
        print(json.dumps(payload, indent=2))
        return EXIT_OK
    result = payload.get("result", {})
    context = result.get("context", {})
    print(f"trace:    {args.trace}")
    print(f"task:     {payload.get('task')}")
    print(
        f"provider: {payload.get('provider')}   budget: {payload.get('budget')}"
        f"   max_steps: {payload.get('max_steps')}"
    )
    print(
        f"status:   {result.get('status')}   steps: {result.get('steps')}"
        f"   tool calls: {result.get('tool_calls')}"
    )
    print(
        f"tokens:   final {result.get('final_tokens')} / peak {result.get('peak_tokens')}"
        f"   dropped messages: {result.get('dropped_messages')}"
    )
    for compaction in context.get("compactions", []):
        tools = ", ".join(compaction.get("dropped_tool_names") or []) or "none"
        print(
            f"  compaction@step{compaction['step']}: dropped {compaction['dropped_messages']}"
            f" message(s) [{tools}] -> {compaction['net_reclaimed']} net tokens"
        )
    for step in result.get("transcript", []):
        tools = ", ".join(call["name"] for call in step.get("tool_calls", [])) or "-"
        print(f"  step {step['step']:>2} [{step['note']}] {tools} | {step['window_tokens']} tokens")
    return EXIT_OK


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default=".", help="root the file tools are confined to")
    parser.add_argument("--budget", type=int, default=4_000, help="total context tokens")
    parser.add_argument("--max-steps", type=int, default=12, help="hard cap on loop iterations")
    parser.add_argument("--max-output-tokens", type=int, default=512, help="output room reserved")
    parser.add_argument(
        "--keep-recent-observations",
        type=int,
        default=2,
        help="newest tool exchanges that compaction must not evict",
    )
    parser.add_argument("--trace-dir", default=".budgetloop/traces")
    parser.add_argument("--no-trace", action="store_true", help="do not write a trace file")


def build_parser() -> argparse.ArgumentParser:
    """Build the parser for the ``run``, ``demo`` and ``inspect`` verbs."""
    parser = argparse.ArgumentParser(
        prog="budgetloop",
        description="A minimal agent loop that treats context as a budget, not a bucket.",
    )
    parser.add_argument("--version", action="version", version=f"budgetloop {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="drive the loop once on a task")
    run_parser.add_argument("--task", required=True, help="the task, in natural language")
    _add_common(run_parser)
    run_parser.add_argument(
        "--provider", choices=["scripted", "echo", "anthropic"], default="scripted"
    )
    run_parser.add_argument("--script", help="JSON reply script used by --provider scripted")
    run_parser.add_argument("--model", default=DEFAULT_MODEL)
    run_parser.add_argument(
        "--allow-network", action="store_true", help="required before a live provider is used"
    )
    run_parser.add_argument("--system-file", help="extra system text, e.g. a conventions doc")
    run_parser.add_argument("--brief", help="project brief appended to the system prompt")
    run_parser.add_argument("--brief-chars", type=int, default=3_000)
    run_parser.add_argument("--no-tool-catalog", action="store_true")
    run_parser.add_argument("--yes", action="store_true", help="pre-approve mutating tools")
    run_parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    run_parser.set_defaults(handler=cmd_run)

    demo_parser = subparsers.add_parser("demo", help="offline scripted run showing compaction")
    demo_parser.add_argument("--workspace", default=".")
    demo_parser.add_argument(
        "--budget",
        type=int,
        default=None,
        help="override the self-sized budget (default: measured cost x 1.15)",
    )
    demo_parser.set_defaults(handler=cmd_demo)

    inspect_parser = subparsers.add_parser("inspect", help="report on a trace file")
    inspect_parser.add_argument("trace")
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(handler=cmd_inspect)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: parse the arguments and dispatch to the chosen verb."""
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":  # pragma: no cover - reached via the console script
    sys.exit(main())
