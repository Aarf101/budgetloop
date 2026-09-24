"""Context window management: a hard token budget, an audit ledger, compaction.

This module is the answer to "context is a budget, not a bucket". The window
knows its size, refuses to grow past it, and -- when it must shrink -- records
exactly what it threw away so the loss is auditable instead of invisible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .messages import CHARS_PER_TOKEN, Message, Role

#: Characters of an evicted message kept as a one-line excerpt in the summary.
EXCERPT_CHARS = 120
#: Hard ceiling on the summary text, so compaction can never grow the window.
SUMMARY_TOKEN_CAP = 220
#: Marker that identifies the single evolving compaction summary in a transcript.
SUMMARY_PREFIX = "[compacted context] "


class ContextOverflow(RuntimeError):
    """Raised when a message cannot be admitted even after compaction."""


@dataclass(frozen=True)
class CompactionEvent:
    """A record of one lossy compaction: what left the window, and what it cost."""

    step: int
    dropped_messages: int
    dropped_tool_names: tuple[str, ...]
    reclaimed_tokens: int
    summary_tokens: int
    reason: str

    @property
    def net_reclaimed(self) -> int:
        return self.reclaimed_tokens - self.summary_tokens

    def to_dict(self) -> dict[str, object]:
        return {
            "step": self.step,
            "dropped_messages": self.dropped_messages,
            "dropped_tool_names": list(self.dropped_tool_names),
            "reclaimed_tokens": self.reclaimed_tokens,
            "summary_tokens": self.summary_tokens,
            "net_reclaimed": self.net_reclaimed,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class LedgerEntry:
    """One auditable line: an action taken on the window and the resulting size."""

    step: int
    action: str
    tokens: int
    budget: int
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "step": self.step,
            "action": self.action,
            "tokens": self.tokens,
            "budget": self.budget,
            "detail": self.detail,
        }


class ContextWindow:
    """A transcript with a hard token budget and an audit trail.

    Compaction policy (deterministic, and deliberately conservative):

    * Messages are grouped into protocol units: an assistant turn that requests
      tools is welded to the observations those tools returned, so eviction can
      never leave an orphaned tool result that a real provider would reject.
    * Protected from eviction: the system prompt, the most recent user turn, and
      the ``keep_recent_observations`` newest tool exchanges.
    * Everything else that is assistant/tool output is evictable, oldest first,
      replaced by a single bounded excerpt summary.
    * If the protected set alone exceeds the budget, ``append`` raises
      ``ContextOverflow`` rather than silently dropping the task.
    """

    def __init__(
        self,
        budget: int,
        *,
        reserve_output_tokens: int = 256,
        keep_recent_observations: int = 2,
        summary_token_cap: int = SUMMARY_TOKEN_CAP,
    ) -> None:
        if not isinstance(budget, int) or budget <= 0:
            raise ValueError("budget must be a positive integer")
        if not 0 <= reserve_output_tokens < budget:
            raise ValueError("reserve_output_tokens must be >= 0 and smaller than the budget")
        if keep_recent_observations < 1:
            # The in-flight tool exchange must stay protected, otherwise an
            # eviction could orphan the observation the loop is about to add.
            raise ValueError("keep_recent_observations must be >= 1")
        self.budget = budget
        self.reserve_output_tokens = reserve_output_tokens
        self.keep_recent_observations = keep_recent_observations
        self.summary_token_cap = summary_token_cap
        self._messages: list[Message] = []
        self.ledger: list[LedgerEntry] = []
        self.compactions: list[CompactionEvent] = []
        self.step = 0
        self.peak_tokens = 0

    # -- introspection ----------------------------------------------------
    @property
    def usable_budget(self) -> int:
        return self.budget - self.reserve_output_tokens

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    @property
    def tokens(self) -> int:
        return sum(message.tokens for message in self._messages)

    @property
    def headroom(self) -> int:
        return self.usable_budget - self.tokens

    @property
    def dropped_messages(self) -> int:
        return sum(event.dropped_messages for event in self.compactions)

    def advise_step(self) -> int:
        """Enter the next agent step; returns the new step number."""
        self.step += 1
        return self.step

    # -- mutation ---------------------------------------------------------
    def append(self, message: Message, *, reason: str | None = None) -> Message:
        """Admit a message, compacting first if needed. Raises ``ContextOverflow``."""
        needed = message.tokens
        label = reason or f"append:{message.role.value}"
        # Each pass may leave the window slightly larger than predicted (the
        # summary itself costs tokens), so retry until it fits or nothing is
        # evictable. ``compact`` returning None is the terminator.
        while self.tokens + needed > self.usable_budget:
            room = self.usable_budget - needed
            if self.compact(reason=f"{label} needs {needed} tokens", target_tokens=room) is None:
                break
        if self.tokens + needed > self.usable_budget:
            raise ContextOverflow(
                f"cannot admit a {needed}-token {message.role.value} message: "
                f"window holds {self.tokens} of {self.usable_budget} usable tokens "
                "and no further eviction is possible"
            )
        self._messages.append(message)
        self.peak_tokens = max(self.peak_tokens, self.tokens)
        self._record(label, detail=f"+{needed} tokens")
        return message

    def compact(
        self, *, reason: str = "unspecified", target_tokens: int | None = None
    ) -> CompactionEvent | None:
        """Evict whole protocol groups (oldest first) into a bounded summary."""
        groups = self._groups()
        protected = self._protected_groups(groups)
        candidates = [
            group
            for index, group in enumerate(groups)
            if index not in protected
            and all(
                self._messages[position].role in (Role.ASSISTANT, Role.TOOL) for position in group
            )
        ]
        if not candidates:
            return None

        goal = int(self.usable_budget * 0.8) if target_tokens is None else target_tokens
        target = max(1, min(goal, self.usable_budget))
        evict: list[int] = []
        projected = self.tokens
        for group in candidates:  # oldest first
            if projected <= target:
                break
            for position in group:
                projected -= self._messages[position].tokens
            evict.extend(group)
        if not evict:
            return None

        evicted = set(evict)
        previous_summary = self._summary_index()
        summary_message = Message(
            role=Role.SYSTEM,
            content=self._merge_summary(previous_summary, evict),
        )
        dropped_tokens = sum(self._messages[position].tokens for position in evict)
        dropped_tools = tuple(
            self._messages[position].tool_name
            for position in evict
            if self._messages[position].role is Role.TOOL and self._messages[position].tool_name
        )

        insert_at = evict[0] if previous_summary is None else min(evict[0], previous_summary)
        rebuilt: list[Message] = []
        for position, message in enumerate(self._messages):
            if position == insert_at:
                rebuilt.append(summary_message)
            if position in evicted or position == previous_summary:
                continue
            rebuilt.append(message)
        self._messages = rebuilt

        event = CompactionEvent(
            step=self.step,
            dropped_messages=len(evict),
            dropped_tool_names=dropped_tools,
            reclaimed_tokens=dropped_tokens,
            summary_tokens=summary_message.tokens,
            reason=reason,
        )
        self.compactions.append(event)
        self._record(
            "compact",
            detail=(
                f"dropped {event.dropped_messages} message(s) "
                f"(-{event.reclaimed_tokens} +{event.summary_tokens} tokens)"
            ),
        )
        return event

    # -- internals --------------------------------------------------------
    def _record(self, action: str, *, detail: str = "") -> None:
        self.ledger.append(
            LedgerEntry(
                step=self.step,
                action=action,
                tokens=self.tokens,
                budget=self.usable_budget,
                detail=detail,
            )
        )

    def _groups(self) -> list[list[int]]:
        """Weld assistant tool-call turns to the observations that answer them.

        Evicting half of a tool exchange would leave a transcript a real provider
        rejects, so the unit of eviction is the whole exchange, never a message.
        """
        groups: list[list[int]] = []
        index = 0
        total = len(self._messages)
        while index < total:
            message = self._messages[index]
            if message.role is Role.ASSISTANT and message.tool_calls:
                call_ids = {call.call_id for call in message.tool_calls}
                group = [index]
                cursor = index + 1
                while (
                    cursor < total
                    and self._messages[cursor].role is Role.TOOL
                    and self._messages[cursor].tool_call_id in call_ids
                ):
                    group.append(cursor)
                    cursor += 1
                groups.append(group)
                index = cursor
            else:
                groups.append([index])
                index += 1
        return groups

    def _protected_groups(self, groups: list[list[int]]) -> set[int]:
        """System prompt, the current task, and the newest tool exchanges survive."""
        last_user = max(
            (index for index, m in enumerate(self._messages) if m.role is Role.USER),
            default=-1,
        )
        tool_groups = [
            index
            for index, group in enumerate(groups)
            if self._messages[group[0]].role is Role.ASSISTANT
            and self._messages[group[0]].tool_calls
        ]
        keep = self.keep_recent_observations
        recent = set(tool_groups[-keep:]) if keep else set()
        protected: set[int] = set()
        for index, group in enumerate(groups):
            if (
                index in recent
                or last_user in group
                or any(self._messages[position].role is Role.SYSTEM for position in group)
            ):
                protected.add(index)
        return protected

    def _summary_index(self) -> int | None:
        """Index of the existing compaction summary, if the transcript has one."""
        for index, message in enumerate(self._messages):
            if message.content.startswith(SUMMARY_PREFIX):
                return index
        return None

    def _merge_summary(self, previous_index: int | None, positions: Sequence[int]) -> str:
        """Fold new evictions into the single summary, keeping the newest excerpts.

        There is exactly one summary message on purpose. A fresh summary per
        compaction would itself become protected context, and the window would
        slowly fill with summaries of summaries.
        """
        previous = ""
        if previous_index is not None:
            previous = self._messages[previous_index].content[len(SUMMARY_PREFIX) :].strip()
        fresh = self._summary_for(positions)
        merged = " | ".join(part for part in (previous, fresh) if part)
        # Budget the prefix too, so the finished message respects the cap exactly.
        room = max(1, self.summary_token_cap * CHARS_PER_TOKEN - len(SUMMARY_PREFIX) - 3)
        if len(merged) > room:
            merged = "..." + merged[-room:]
        return SUMMARY_PREFIX + merged

    def _summary_for(self, positions: Sequence[int]) -> str:
        """A bounded, honest excerpt line per evicted message (never grows the window)."""
        lines: list[str] = []
        for position in positions:
            message = self._messages[position]
            text = " ".join(message.content.split())
            excerpt = text[:EXCERPT_CHARS] + ("..." if len(text) > EXCERPT_CHARS else "")
            lines.append(f"{message.tool_name or message.role.value}: {excerpt}")
        summary = " | ".join(lines)
        char_cap = self.summary_token_cap * CHARS_PER_TOKEN
        if len(summary) > char_cap:
            summary = summary[:char_cap] + "..."
        return summary

    # -- reporting --------------------------------------------------------
    def snapshot(self) -> list[dict[str, Any]]:
        return [message.to_dict() for message in self._messages]

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "reserve_output_tokens": self.reserve_output_tokens,
            "usable_budget": self.usable_budget,
            "tokens": self.tokens,
            "peak_tokens": self.peak_tokens,
            "messages": len(self._messages),
            "dropped_messages": self.dropped_messages,
            "compactions": [event.to_dict() for event in self.compactions],
            "ledger": [entry.to_dict() for entry in self.ledger],
            "final_window": self.snapshot(),
        }

    def report(self) -> str:
        """Human-readable budget report: the evidence that the guardrail worked."""
        width = 66
        lines = [
            "-" * width,
            f"CONTEXT LEDGER  budget={self.budget}  reserve={self.reserve_output_tokens}"
            f"  usable={self.usable_budget}",
            "-" * width,
        ]
        for entry in self.ledger:
            detail = f"  {entry.detail}" if entry.detail else ""
            lines.append(
                f"step {entry.step:>2}  {entry.action:<20} {entry.tokens:>6} tokens{detail}"
            )
        lines.append("-" * width)
        lines.append(
            f"peak {self.peak_tokens}/{self.usable_budget} tokens | final {self.tokens} | "
            f"messages {len(self._messages)} | compaction events {len(self.compactions)}"
        )
        if self.compactions:
            net = sum(event.net_reclaimed for event in self.compactions)
            lines.append(
                f"lossy: {self.dropped_messages} message(s) summarised away, "
                f"{net} net tokens reclaimed -- context is a budget, not a bucket"
            )
        return "\n".join(lines)
