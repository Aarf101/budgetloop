"""Tests for the provider seam: scripted determinism and the live payload builder.

The live provider is never called here. What *is* tested is the part that breaks
in production without a network connection: the wire format. A compacted
transcript that produces a malformed payload is a 400 from the API, so the
translation layer gets its own tests.
"""

from __future__ import annotations

import unittest

from budgetloop.messages import (
    ToolCall,
    assistant_message,
    system_message,
    tool_message,
    user_message,
)
from budgetloop.providers import (
    AnthropicProvider,
    EchoProvider,
    Provider,
    ProviderError,
    ScriptedProvider,
    final_reply,
    to_anthropic_messages,
    tool_call_reply,
)


class ScriptedProviderTests(unittest.TestCase):
    """Determinism is a feature: the same script must always replay the same way."""

    def test_replays_the_script_in_order(self):
        provider = ScriptedProvider(
            replies=[tool_call_reply("read_file", {"path": "a"}, "c1"), final_reply("done")]
        )
        first = provider.complete([user_message("task")], [])
        second = provider.complete([user_message("task")], [])
        self.assertEqual(first.tool_calls[0].name, "read_file")
        self.assertTrue(first.wants_tools)
        self.assertEqual(second.content, "done")
        self.assertFalse(second.wants_tools)

    def test_exhausted_script_raises_a_provider_error(self):
        provider = ScriptedProvider(replies=[final_reply("only one")])
        provider.complete([user_message("task")], [])
        with self.assertRaises(ProviderError):
            provider.complete([user_message("task")], [])

    def test_it_records_every_transcript_it_was_handed(self):
        provider = ScriptedProvider(replies=[final_reply("one"), final_reply("two")])
        provider.complete([user_message("first")], [])
        provider.complete([user_message("second")], [])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(provider.calls[0][0].content, "first")
        self.assertEqual(provider.calls[1][0].content, "second")

    def test_remaining_counts_down(self):
        provider = ScriptedProvider(replies=[final_reply("a"), final_reply("b")])
        self.assertEqual(provider.remaining, 2)
        provider.complete([], [])
        self.assertEqual(provider.remaining, 1)

    def test_both_offline_providers_satisfy_the_provider_protocol(self):
        self.assertIsInstance(ScriptedProvider(), Provider)
        self.assertIsInstance(EchoProvider(), Provider)

    def test_echo_lists_the_tools_it_was_offered(self):
        reply = EchoProvider().complete(
            [user_message("hi")], [{"name": "read_file"}, {"name": "run_check"}]
        )
        self.assertIn("read_file", reply.content)
        self.assertIn("run_check", reply.content)


class WireFormatTests(unittest.TestCase):
    """The translation to the Messages API shape, which is where 400s come from."""

    def test_system_messages_are_lifted_out_of_the_wire_messages(self):
        system, wire = to_anthropic_messages([system_message("contract"), user_message("task")])
        self.assertEqual(system, "contract")
        self.assertEqual([message["role"] for message in wire], ["user"])

    def test_text_round_trips(self):
        _, wire = to_anthropic_messages([user_message("question"), assistant_message("answer")])
        self.assertEqual(wire[0]["content"][0]["text"], "question")
        self.assertEqual(wire[1]["content"][0]["text"], "answer")

    def test_tool_call_becomes_a_tool_use_block(self):
        call = ToolCall(call_id="c1", name="read_file", arguments={"path": "a.txt"})
        _, wire = to_anthropic_messages([assistant_message("looking", [call])])
        blocks = wire[0]["content"]
        self.assertEqual(blocks[0]["type"], "text")
        self.assertEqual(blocks[1]["type"], "tool_use")
        self.assertEqual(blocks[1]["id"], "c1")
        self.assertEqual(blocks[1]["input"], {"path": "a.txt"})

    def test_observation_becomes_a_user_tool_result_block(self):
        call = ToolCall(call_id="c1", name="read_file", arguments={"path": "a.txt"})
        _, wire = to_anthropic_messages(
            [assistant_message("looking", [call]), tool_message(call, "file contents")]
        )
        self.assertEqual(wire[1]["role"], "user")
        self.assertEqual(wire[1]["content"][0]["type"], "tool_result")
        self.assertEqual(wire[1]["content"][0]["tool_use_id"], "c1")

    def test_consecutive_same_role_turns_are_merged(self):
        call = ToolCall(call_id="c1", name="read_file", arguments={})
        _, wire = to_anthropic_messages(
            [
                user_message("task"),
                assistant_message("looking", [call]),
                tool_message(call, "result one"),
                tool_message(ToolCall("c2", "list_files", {}), "result two"),
            ]
        )
        self.assertEqual([message["role"] for message in wire], ["user", "assistant", "user"])
        self.assertEqual(len(wire[2]["content"]), 2)

    def test_empty_transcript_yields_no_system_prompt(self):
        system, wire = to_anthropic_messages([])
        self.assertIsNone(system)
        self.assertEqual(wire, [])


class AnthropicProviderTests(unittest.TestCase):
    """Payload construction without any network: key handling and token ceilings."""

    def build(self, **overrides) -> AnthropicProvider:
        settings = {"api_key": "test-key", "model": "test-model"}
        settings.update(overrides)
        return AnthropicProvider(**settings)

    def test_it_refuses_to_construct_without_a_key(self):
        with self.assertRaises(ProviderError):
            AnthropicProvider(api_key="")

    def test_payload_carries_model_system_and_tools(self):
        payload = self.build().build_payload(
            [system_message("contract"), user_message("task")],
            [{"name": "read_file", "description": "reads", "input_schema": {}}],
            system="extra context",
            max_output_tokens=256,
        )
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["max_tokens"], 256)
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertIn("extra context", payload["system"])
        self.assertIn("contract", payload["system"])
        self.assertEqual(payload["tools"][0]["name"], "read_file")

    def test_output_room_is_capped_by_the_provider_ceiling(self):
        provider = self.build(max_output_tokens=128)
        payload = provider.build_payload(
            [user_message("task")], [], system=None, max_output_tokens=4_096
        )
        self.assertEqual(payload["max_tokens"], 128)

    def test_tools_are_omitted_when_none_are_offered(self):
        payload = self.build().build_payload(
            [user_message("task")], [], system=None, max_output_tokens=64
        )
        self.assertNotIn("tools", payload)


if __name__ == "__main__":
    unittest.main()
