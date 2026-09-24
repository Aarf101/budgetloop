"""Message and token-accounting primitives.

The whole point of this module is that context is *measurable*. Every object
that can enter the model's window knows what it costs, so the loop can make an
informed decision instead of discovering the limit by crashing into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Rough, provider-agnostic conversion. 1 token ~= 4 characters of English.
CHARS_PER_TOKEN = 4

#: Per-message framing cost (role markers, separators) charged on top of content.
MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str) -> int:
    """Estimate the token cost of ``text``.

    Deliberately conservative: it rounds up and never returns 0 for non-empty
    input. Over-estimating keeps the harness inside its budget; under-estimating
    would silently blow the window, which is the failure mode we are guarding
    against. Swap this for a real tokenizer (e.g. ``tiktoken``) when the exact
    count matters more than the dependency budget.
    """
    if not text:
        return 0
    return max(1, -(-len(text) // CHARS_PER_TOKEN))


class Role(str, Enum):
    """Who authored a message."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A request from the model to run a named tool with JSON arguments."""

    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.call_id, "name": self.name, "arguments": self.arguments}


@dataclass
class ModelReply:
    """One turn of model output: either prose, or tool calls, or both."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class Message:
    """A single entry in the conversation transcript."""

    role: Role
    content: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        """Estimated cost of carrying this message in the window."""
        cost = estimate_tokens(self.content) + MESSAGE_OVERHEAD_TOKENS
        for call in self.tool_calls:
            cost += estimate_tokens(call.name) + estimate_tokens(_render_args(call.arguments))
        return cost

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_name:
            payload["tool_name"] = self.tool_name
        if self.tool_calls:
            payload["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        return payload


def _render_args(arguments: dict[str, Any]) -> str:
    """Deterministic rendering used for cost estimation only."""
    return " ".join(f"{key}={value}" for key, value in sorted(arguments.items()))


def system_message(content: str) -> Message:
    """Build a system message: instructions that hold for the whole run."""
    return Message(role=Role.SYSTEM, content=content)


def user_message(content: str) -> Message:
    """Build a user message: a task or a clarification from the human."""
    return Message(role=Role.USER, content=content)


def assistant_message(content: str, tool_calls: list[ToolCall] | None = None) -> Message:
    """Build an assistant message, optionally carrying tool requests."""
    return Message(
        role=Role.ASSISTANT,
        content=content,
        tool_calls=list(tool_calls or []),
    )


def tool_message(call: ToolCall, observation: str) -> Message:
    """Build the observation that answers one tool call."""
    return Message(
        role=Role.TOOL,
        content=observation,
        tool_call_id=call.call_id,
        tool_name=call.name,
    )
