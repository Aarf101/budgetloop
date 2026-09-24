"""Model providers: the thing that turns a context window into a next action.

The loop does not care who is at the other end. It needs a ``str`` name and a
``complete`` method. That seam is what lets the test suite exercise the loop
(guards, budget, eviction, termination) with zero network calls and zero cost,
while the same code path can be pointed at a live model with one flag.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

from .messages import Message, ModelReply, Role, ToolCall

#: Default live model. Model ids rotate; override with --model or BUDGETLOOP_MODEL.
DEFAULT_MODEL = os.environ.get("BUDGETLOOP_MODEL", "claude-sonnet-4-5")


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a reply."""


@runtime_checkable
class Provider(Protocol):
    """The single seam the loop depends on."""

    name: str

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        *,
        system: str | None = None,
        max_output_tokens: int = 256,
    ) -> ModelReply:
        """Return the next model turn for ``messages``."""
        ...


def tool_call_reply(
    name: str, arguments: dict[str, Any], call_id: str, note: str = ""
) -> ModelReply:
    """Convenience builder: a reply that asks for exactly one tool."""
    return ModelReply(
        content=note,
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        stop_reason="tool_use",
    )


def final_reply(text: str) -> ModelReply:
    """Convenience builder: a reply that ends the run."""
    return ModelReply(content=text, stop_reason="end_turn")


@dataclass
class ScriptedProvider:
    """Deterministic replay of a fixed script. Powers demos and the test suite.

    It also doubles as an honest demo of a *bad* vendor: if the script runs out
    it raises instead of improvising, which is exactly what a flaky provider does
    and exactly what the loop's error handling has to survive.
    """

    replies: list[ModelReply] = field(default_factory=list)
    name: str = "scripted"
    calls: list[list[Message]] = field(default_factory=list, repr=False)
    _index: int = field(default=0, init=False, repr=False)

    @property
    def remaining(self) -> int:
        return len(self.replies) - self._index

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        *,
        system: str | None = None,
        max_output_tokens: int = 256,
    ) -> ModelReply:
        self.calls.append(list(messages))
        if self._index >= len(self.replies):
            raise ProviderError(
                f"script exhausted after {self._index} reply(ies); the agent asked for more"
            )
        reply = self.replies[self._index]
        self._index += 1
        return reply


@dataclass
class EchoProvider:
    """A degenerate provider that never calls tools; useful for smoke tests."""

    name: str = "echo"

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        *,
        system: str | None = None,
        max_output_tokens: int = 256,
    ) -> ModelReply:
        tool_names = ", ".join(tool["name"] for tool in tools) or "none"
        return ModelReply(
            content=(
                f"[echo] saw {len(messages)} message(s), tools available: {tool_names}. "
                "No action taken: this provider has no model behind it."
            ),
            stop_reason="end_turn",
        )


@dataclass
class AnthropicProvider:
    """Live provider over the Messages API, using only ``urllib`` (no SDK dependency).

    Network use is explicit and opt-in: the CLI refuses to pick this provider
    unless ``--allow-network`` is passed, and it refuses to construct without a
    key. Every failure raises ``ProviderError`` with the response body attached,
    so the loop can report a real diagnostic instead of a bare stack trace.
    """

    api_key: str = ""
    model: str = DEFAULT_MODEL
    max_output_tokens: int = 1024
    base_url: str = "https://api.anthropic.com/v1/messages"
    timeout_seconds: int = 60
    name: str = "anthropic"

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ProviderError(
                "live provider needs an API key: export ANTHROPIC_API_KEY=... "
                "or use --provider scripted"
            )

    def build_payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        *,
        system: str | None,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        system_prompt, wire = to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": min(max_output_tokens, self.max_output_tokens),
            "messages": wire,
        }
        if system or system_prompt:
            payload["system"] = "\n\n".join(filter(None, [system, system_prompt]))
        if tools:
            payload["tools"] = [dict(tool) for tool in tools]
        return payload

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        *,
        system: str | None = None,
        max_output_tokens: int = 256,
    ) -> ModelReply:
        payload = self.build_payload(
            messages, tools, system=system, max_output_tokens=max_output_tokens
        )
        body = self._post(payload)
        blocks = body.get("content") or []
        text = " ".join(
            str(block.get("text", "")) for block in blocks if block.get("type") == "text"
        ).strip()
        calls = [
            ToolCall(
                call_id=str(block.get("id", "")),
                name=str(block.get("name", "")),
                arguments=dict(block.get("input") or {}),
            )
            for block in blocks
            if block.get("type") == "tool_use"
        ]
        return ModelReply(
            content=text,
            tool_calls=calls,
            stop_reason=str(body.get("stop_reason") or "end_turn"),
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - https URL, configured base only
                request, timeout=self.timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:600]
            raise ProviderError(f"HTTP {exc.code} from {self.base_url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"cannot reach {self.base_url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"malformed JSON from {self.base_url}: {exc}") from exc


def _push(wire: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    """Append blocks, merging into the previous turn when roles repeat.

    The Messages API requires alternating roles, and a compacted transcript can
    easily produce two user turns in a row -- merging keeps the payload legal.
    """
    if not blocks:
        return
    if wire and wire[-1]["role"] == role:
        wire[-1]["content"].extend(blocks)
    else:
        wire.append({"role": role, "content": list(blocks)})


def to_anthropic_messages(
    messages: Sequence[Message],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Translate an internal transcript into a ``(system, wire messages)`` pair."""
    systems: list[str] = []
    wire: list[dict[str, Any]] = []
    for message in messages:
        if message.role is Role.SYSTEM:
            systems.append(message.content)
        elif message.role is Role.USER:
            _push(wire, "user", [{"type": "text", "text": message.content}])
        elif message.role is Role.ASSISTANT:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {
                    "type": "tool_use",
                    "id": call.call_id,
                    "name": call.name,
                    "input": dict(call.arguments),
                }
                for call in message.tool_calls
            )
            _push(wire, "assistant", blocks or [{"type": "text", "text": "(empty turn)"}])
        elif message.role is Role.TOOL:
            _push(
                wire,
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id or "",
                        "content": message.content,
                    }
                ],
            )
    return ("\n\n".join(systems) or None), wire
