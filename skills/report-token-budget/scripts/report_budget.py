#!/usr/bin/env python3
"""Report how a run's token budget was spent from a trace file.

Self-contained on purpose: a skill can be activated in a repository
with no tooling of its own, so this script depends on nothing but the
standard library.

It reads one budgetloop trace (the JSON written per run) and breaks
the spend into four sections: budget headroom, per-step growth,
per-tool observation cost, and what compaction reclaimed. Numbers are
the harness's 4-characters-per-token estimate, comparable across runs
but never a vendor bill.

Usage: python scripts/report_budget.py TRACE [--json]
Exit codes: 0 report produced, 2 bad usage or unreadable trace.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Sequence


def load_trace(path: Path) -> dict[str, Any]:
    """Read a trace file and return its envelope as a dict."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read trace {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"trace {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"trace {path} must hold a JSON object")
    return payload


def split_envelope(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a trace envelope into its result and context dicts."""
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise ValueError("trace result must be an object")
    context = result.get("context", {})
    if not isinstance(context, dict):
        raise ValueError("trace context must be an object")
    return result, context


def step_costs(transcript: Any) -> list[dict[str, Any]]:
    """Per-step window growth derived from the transcript order."""
    if transcript is None:
        return []
    if not isinstance(transcript, list):
        raise ValueError("trace transcript must be a list")
    costs: list[dict[str, Any]] = []
    previous = 0
    for entry in transcript:
        if not isinstance(entry, dict):
            raise ValueError("each transcript entry must be an object")
        window = int(entry.get("window_tokens", 0) or 0)
        calls = entry.get("tool_calls", []) or []
        tools = [call.get("name", "?") for call in calls if isinstance(call, dict)]
        costs.append(
            {
                "step": int(entry.get("step", len(costs) + 1) or 0),
                "note": str(entry.get("note", "") or ""),
                "tools": [str(name) for name in tools],
                "window_tokens": window,
                "growth_tokens": window - previous,
            }
        )
        previous = window
    return costs


def tool_costs(transcript: Any) -> list[dict[str, Any]]:
    """Aggregate observation cost per tool across every step."""
    if transcript is None:
        return []
    if not isinstance(transcript, list):
        raise ValueError("trace transcript must be a list")
    totals: dict[str, dict[str, int]] = {}
    for entry in transcript:
        if not isinstance(entry, dict):
            raise ValueError("each transcript entry must be an object")
        observations = entry.get("observations", []) or []
        if not isinstance(observations, list):
            raise ValueError("step observations must be a list")
        for observation in observations:
            if not isinstance(observation, dict):
                raise ValueError("each observation must be an object")
            name = str(observation.get("tool", "unknown") or "unknown")
            bucket = totals.setdefault(name, {"calls": 0, "chars": 0, "fails": 0})
            bucket["calls"] += 1
            try:
                bucket["chars"] += int(observation.get("chars", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError("observation chars must be an integer") from exc
            if not observation.get("ok", True):
                bucket["fails"] += 1
    ranked = [
        {"tool": name, "calls": v["calls"], "chars": v["chars"], "failures": v["fails"]}
        for name, v in totals.items()
    ]
    ranked.sort(key=lambda item: (-item["chars"], str(item["tool"])))
    return ranked


def compaction_costs(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise the context's compaction events into plain dicts."""
    raw = context.get("compactions", []) or []
    if not isinstance(raw, list):
        raise ValueError("context compactions must be a list")
    events: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each compaction must be an object")
        reclaimed = int(entry.get("reclaimed_tokens", 0) or 0)
        summary = int(entry.get("summary_tokens", 0) or 0)
        net = entry.get("net_reclaimed", reclaimed - summary)
        events.append(
            {
                "step": int(entry.get("step", 0) or 0),
                "dropped_messages": int(entry.get("dropped_messages", 0) or 0),
                "dropped_tool_names": list(entry.get("dropped_tool_names", []) or []),
                "reclaimed_tokens": reclaimed,
                "summary_tokens": summary,
                "net_reclaimed": int(net or 0),
                "reason": str(entry.get("reason", "") or ""),
            }
        )
    return events


def summarise(payload: dict[str, Any]) -> dict[str, Any]:
    """Turn a trace envelope into the breakdown the skill reports."""
    result, context = split_envelope(payload)
    transcript = result.get("transcript", [])
    steps = step_costs(transcript)
    tools = tool_costs(transcript)
    events = compaction_costs(context)
    budget = payload.get("budget", context.get("budget"))
    usable = context.get("usable_budget")
    reserve = context.get("reserve_output_tokens")
    peak = int(result.get("peak_tokens", context.get("peak_tokens", 0)) or 0)
    final = int(result.get("final_tokens", context.get("tokens", 0)) or 0)
    usable_int = usable if isinstance(usable, int) else None
    headroom = (usable_int - peak) if usable_int is not None else None
    if usable_int:
        utilisation: float | None = peak / usable_int
    else:
        utilisation = None
    biggest_step = max(steps, key=lambda item: item["growth_tokens"]) if steps else None
    return {
        "task": payload.get("task"),
        "provider": payload.get("provider"),
        "status": str(result.get("status", "unknown") or "unknown"),
        "budget": budget,
        "usable_budget": usable,
        "reserve_output_tokens": reserve,
        "peak_tokens": peak,
        "final_tokens": final,
        "headroom_tokens": headroom,
        "utilisation": utilisation,
        "steps": int(result.get("steps", len(steps)) or 0),
        "tool_calls": int(result.get("tool_calls", 0) or 0),
        "observations": int(result.get("observations", 0) or 0),
        "failed_observations": int(result.get("failed_observations", 0) or 0),
        "dropped_messages": int(
            result.get("dropped_messages", context.get("dropped_messages", 0)) or 0
        ),
        "compactions": int(result.get("compactions", len(events)) or 0),
        "step_costs": steps,
        "biggest_step": biggest_step,
        "tool_costs": tools,
        "biggest_tool": tools[0] if tools else None,
        "compaction_events": events,
        "net_reclaimed": sum(event["net_reclaimed"] for event in events),
    }


def render(summary: dict[str, Any]) -> str:
    """Render the budget breakdown as plain text for a terminal."""
    lines = [
        f"task:     {summary.get('task')}",
        f"status:   {summary.get('status')}   provider: {summary.get('provider')}",
        f"budget:   {summary.get('budget')} total / "
        f"{summary.get('usable_budget')} usable "
        f"(reserve {summary.get('reserve_output_tokens')})",
        f"tokens:   peak {summary['peak_tokens']} / "
        f"final {summary['final_tokens']}   "
        f"headroom {summary.get('headroom_tokens')}",
    ]
    utilisation = summary.get("utilisation")
    if isinstance(utilisation, float):
        lines.append(f"pressure: peak used {utilisation:.0%} of the usable budget")
    lines += ["", "steps (growth since the previous step):"]
    if summary["step_costs"]:
        for item in summary["step_costs"]:
            tools = ", ".join(item["tools"]) or "-"
            lines.append(
                f"  step {item['step']:>2} [{item['note']}] {tools} | "
                f"{item['window_tokens']} tokens ({item['growth_tokens']:+d})"
            )
    else:
        lines.append("  (no transcript steps recorded)")
    lines += ["", "tools (observation cost by tool):"]
    if summary["tool_costs"]:
        for item in summary["tool_costs"]:
            lines.append(
                f"  {item['tool']}: {item['calls']} call(s), "
                f"{item['chars']} chars, {item['failures']} failed"
            )
    else:
        lines.append("  (no tool observations recorded)")
    lines += ["", "compactions:"]
    if summary["compaction_events"]:
        for event in summary["compaction_events"]:
            names = ", ".join(event["dropped_tool_names"]) or "none"
            lines.append(
                f"  step {event['step']}: dropped {event['dropped_messages']} "
                f"message(s) [{names}] -{event['reclaimed_tokens']} "
                f"+{event['summary_tokens']} (net {event['net_reclaimed']:+d})"
            )
        lines.append(
            f"  net reclaimed: {summary['net_reclaimed']:+d} tokens across "
            f"{summary['dropped_messages']} dropped message(s)"
        )
    else:
        lines.append("  (no compactions: the window never filled)")
    biggest_step = summary.get("biggest_step")
    biggest_tool = summary.get("biggest_tool")
    if isinstance(biggest_step, dict):
        lines.append(
            f"biggest step: {biggest_step['step']} "
            f"({biggest_step['growth_tokens']:+d} tokens)"
        )
    if isinstance(biggest_tool, dict):
        lines.append(
            f"biggest tool: {biggest_tool['tool']} "
            f"({biggest_tool['chars']} observation chars)"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Read one trace file and print where its token budget went."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in arguments
    targets = [argument for argument in arguments if not argument.startswith("--")]
    if len(targets) != 1:
        print("usage: report_budget.py TRACE [--json]", file=sys.stderr)
        return 2
    path = Path(targets[0])
    try:
        summary = summarise(load_trace(path))
    except ValueError as exc:
        print(f"cannot report {targets[0]}: {exc}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(render(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
