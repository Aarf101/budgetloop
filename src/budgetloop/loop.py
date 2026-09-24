"""The agent loop: perceive -> plan -> act -> observe -> iterate, on a budget.

The loop is deliberately boring and deliberately finite. Four properties do all
the work:

1. It cannot run forever           -- ``max_steps`` is a hard stop.
2. It cannot silently spend more   -- the window is a budget with a ledger.
3. It cannot touch anything it was not given -- tools are declared and gated.
4. It leaves evidence               -- every run can be written to a JSON trace.

Anything the loop does not understand (a refused tool, a flaky provider, an
overflowing window) becomes a *recorded* fact, never a traceback: the run ends
with a status a human can act on.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from .context import ContextOverflow, ContextWindow
from .messages import (
    ToolCall,
    assistant_message,
    system_message,
    tool_message,
    user_message,
)
from .providers import Provider, ProviderError
from .tools import ApproveFn, ToolError, ToolRegistry

#: The default system prompt is *thin on purpose*: it states the contract and
#: the stop conditions, and says nothing about the project. Project knowledge
#: belongs in AGENTS.md / a Skill, where it can be reviewed and versioned.
DEFAULT_SYSTEM_PROMPT = (
    "You are a careful coding agent operating inside one repository.\n"
    "Work in small steps: inspect before you change, and verify with the project's\n"
    "registered checks after you change.\n"
    "Rules that are not negotiable:\n"
    "- Use only the registered tools. Never assume a file's contents; read it.\n"
    "- If a tool is refused or returns an error, report it instead of working around it.\n"
    "- Stay inside the workspace.\n"
    "- When the task is done, stop and say so in one short paragraph."
)


class RunStatus(str, Enum):
    """How a run ended. Every value is a fact a human can act on."""

    COMPLETED = "completed"
    STEP_LIMIT = "step_limit"
    BUDGET_EXCEEDED = "budget_exceeded"
    PROVIDER_ERROR = "provider_error"


@dataclass
class StepRecord:
    """One turn of observe -> plan -> act, kept for the trace and the report."""

    step: int
    assistant_text: str
    tool_calls: tuple[dict[str, Any], ...] = ()
    observations: tuple[dict[str, Any], ...] = ()
    window_tokens: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "assistant_text": self.assistant_text,
            "tool_calls": list(self.tool_calls),
            "observations": list(self.observations),
            "window_tokens": self.window_tokens,
            "note": self.note,
        }


@dataclass
class RunResult:
    """The full outcome of a run, including the evidence that the guards held."""

    status: RunStatus
    answer: str
    steps: int
    tool_calls: int
    observations: int
    failed_observations: int
    final_tokens: int
    peak_tokens: int
    dropped_messages: int
    compactions: int
    duration_seconds: float
    transcript: tuple[StepRecord, ...]
    context: dict[str, Any]
    ledger_report: str = ""
    trace_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "answer": self.answer,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "observations": self.observations,
            "failed_observations": self.failed_observations,
            "final_tokens": self.final_tokens,
            "peak_tokens": self.peak_tokens,
            "dropped_messages": self.dropped_messages,
            "compactions": self.compactions,
            "duration_seconds": round(self.duration_seconds, 4),
            "trace_path": self.trace_path,
            "transcript": [record.to_dict() for record in self.transcript],
            "context": self.context,
        }

    def summary_line(self) -> str:
        return (
            f"[{self.status.value}] {self.steps} step(s), {self.tool_calls} tool call(s), "
            f"{self.observations} observation(s) ({self.failed_observations} failed), "
            f"{self.final_tokens} tokens held (peak {self.peak_tokens}), "
            f"{self.dropped_messages} message(s) compacted away, "
            f"{self.duration_seconds:.3f}s"
        )


class AgentLoop:
    """Drives one provider through one task, with hard stops on both axes."""

    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        *,
        budget: int = 4_000,
        max_steps: int = 12,
        max_output_tokens: int = 512,
        keep_recent_observations: int = 2,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        include_tool_catalog: bool = True,
        approve: ApproveFn | None = None,
        trace_dir: str | Path | None = ".budgetloop/traces",
        on_step: Callable[[StepRecord], None] | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        self.provider = provider
        self.registry = registry
        self.budget = budget
        self.max_steps = max_steps
        self.max_output_tokens = max_output_tokens
        # Reserve output room first; the transcript gets what is left.
        self.reserve_tokens = min(max_output_tokens, max(1, budget // 4))
        self.keep_recent_observations = keep_recent_observations
        self.system_prompt = system_prompt
        self.include_tool_catalog = include_tool_catalog
        self.approve = approve
        self.trace_dir = Path(trace_dir) if trace_dir is not None else None
        self.on_step = on_step

    def _emit(
        self,
        step: int,
        assistant_text: str,
        calls: tuple[Any, ...],
        records: tuple[Any, ...],
        window: ContextWindow,
        note: str,
    ) -> StepRecord:
        """Observe: build the step record and stream it to any listener."""
        record = StepRecord(
            step=step,
            assistant_text=assistant_text,
            tool_calls=tuple(
                {"name": call.name, "arguments": dict(call.arguments)} for call in calls
            ),
            observations=tuple(records),
            window_tokens=window.tokens,
            note=note,
        )
        if self.on_step is not None:
            self.on_step(record)
        return record


    # -- public API -------------------------------------------------------
    def run(self, task: str) -> RunResult:
        """Run to a final answer or to a guardrail. Provider faults are recorded, not raised."""
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")
        started = time.monotonic()
        window = ContextWindow(
            self.budget,
            reserve_output_tokens=self.reserve_tokens,
            keep_recent_observations=self.keep_recent_observations,
        )
        try:
            window.append(system_message(self.system_prompt), reason="append:system")
            if self.include_tool_catalog:
                window.append(
                    system_message("Registered tools:\n" + self.registry.describe()),
                    reason="append:tool_catalog",
                )
            window.append(user_message(task.strip()), reason="append:task")
        except ContextOverflow as exc:
            # Mis-sized budget, not a bug: report it like any other outcome.
            return self._abort(
                window,
                task,
                started,
                RunStatus.BUDGET_EXCEEDED,
                f"the budget cannot hold the fixed context: {exc}",
            )

        transcript: list[StepRecord] = []
        status = RunStatus.STEP_LIMIT
        answer = (
            f"no final answer after {self.max_steps} step(s): the step limit is a guardrail,"
            " not a crash -- narrow the task or raise --max-steps"
        )
        tool_calls = observations = failed = 0

        for step in range(1, self.max_steps + 1):
            window.advise_step()
            try:
                reply = self.provider.complete(
                    window.messages,
                    self.registry.schemas(),
                    system=None,
                    max_output_tokens=self.max_output_tokens,
                )
            except ProviderError as exc:
                status = RunStatus.PROVIDER_ERROR
                answer = f"provider failed on step {step}: {exc}"
                transcript.append(self._emit(step, "", (), (), window, "provider_error"))
                break

            if not reply.wants_tools:
                answer = reply.content.strip() or "(the model returned an empty final answer)"
                window.append(assistant_message(answer), reason="append:final")
                status = RunStatus.COMPLETED
                transcript.append(self._emit(step, answer, (), (), window, "final"))
                break

            try:
                window.append(
                    assistant_message(reply.content, reply.tool_calls), reason="append:assistant"
                )
            except ContextOverflow as exc:
                status = RunStatus.BUDGET_EXCEEDED
                answer = f"context overflow before acting on step {step}: {exc}"
                transcript.append(self._emit(step, reply.content, (), (), window, "overflow"))
                break

            records, failed_here, overflow = self._run_tools(window, reply.tool_calls)
            tool_calls += len(reply.tool_calls)
            observations += len(records)
            failed += failed_here
            transcript.append(
                self._emit(
                    step,
                    reply.content,
                    tuple(reply.tool_calls),
                    tuple(records),
                    window,
                    "acted",
                )
            )
            if overflow:
                status = RunStatus.BUDGET_EXCEEDED
                answer = f"context overflow after step {step}: {overflow}"
                break

        # Exhausting the range leaves status at STEP_LIMIT: that is the guardrail.
        result = RunResult(
            status=status,
            answer=answer,
            steps=len(transcript),
            tool_calls=tool_calls,
            observations=observations,
            failed_observations=failed,
            final_tokens=window.tokens,
            peak_tokens=window.peak_tokens,
            dropped_messages=window.dropped_messages,
            compactions=len(window.compactions),
            duration_seconds=time.monotonic() - started,
            transcript=tuple(transcript),
            context=window.to_dict(),
            ledger_report=window.report(),
        )
        return self._record_trace(result, task)


    def _run_tools(
        self, window: ContextWindow, calls: Sequence[ToolCall]
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """Act and observe for one turn: run each tool, feed the result back."""
        records: list[dict[str, Any]] = []
        failed = 0
        for call in calls:
            try:
                text = self.registry.run(call.name, call.arguments, approve=self.approve)
                ok, error = True, None
            except ToolError as exc:
                # A refused or broken tool is an observation, not a crash.
                text, ok, error = f"ERROR: {exc}", False, str(exc)
                failed += 1
            records.append({"tool": call.name, "ok": ok, "chars": len(text), "error": error})
            try:
                window.append(tool_message(call, text), reason=f"append:observation:{call.name}")
            except ContextOverflow as exc:
                return records, failed, str(exc)
        return records, failed, None

    def _record_trace(self, result: RunResult, task: str) -> RunResult:
        """Write the run's evidence to disk. A trace failure never fails the run."""
        if self.trace_dir is None:
            return result
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            path = self.trace_dir / f"run-{stamp}.json"
            result.trace_path = str(path)
            payload = {
                "task": task,
                "provider": getattr(self.provider, "name", type(self.provider).__name__),
                "budget": self.budget,
                "max_steps": self.max_steps,
                "result": result.to_dict(),
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            result.trace_path = f"(trace not written: {exc})"
        return result

    def _abort(
        self,
        window: ContextWindow,
        task: str,
        started: float,
        status: RunStatus,
        answer: str,
    ) -> RunResult:
        """End a run before its first turn, keeping the same evidence trail."""
        result = RunResult(
            status=status,
            answer=answer,
            steps=0,
            tool_calls=0,
            observations=0,
            failed_observations=0,
            final_tokens=window.tokens,
            peak_tokens=window.peak_tokens,
            dropped_messages=window.dropped_messages,
            compactions=len(window.compactions),
            duration_seconds=time.monotonic() - started,
            transcript=(),
            context=window.to_dict(),
            ledger_report=window.report(),
        )
        return self._record_trace(result, task)
