"""Tests for the budget window: accounting, eviction policy, and ledger honesty.

The eviction tests are the important ones. A context manager that "works" by
dropping messages at random is worse than no manager at all, so what is asserted
here is the *policy*: what is protected, what leaves first, and what the
transcript must still satisfy afterwards.
"""

from __future__ import annotations

import unittest

from budgetloop.context import ContextOverflow, ContextWindow
from budgetloop.messages import (
    MESSAGE_OVERHEAD_TOKENS,
    Role,
    ToolCall,
    assistant_message,
    system_message,
    tool_message,
    user_message,
)
from budgetloop.providers import to_anthropic_messages


def make_call(index: int, name: str = "read_file") -> ToolCall:
    """Build a tool call whose id and path argument are derived from the index."""
    return ToolCall(call_id=f"call-{index}", name=name, arguments={"path": f"file{index}.txt"})


def add_exchange(
    window: ContextWindow, index: int, chars: int = 200, name: str = "read_file"
) -> ToolCall:
    """Append one assistant tool-call turn plus the observation answering it."""
    call = make_call(index, name)
    window.append(assistant_message(f"turn {index}", [call]), reason="append:assistant")
    window.append(tool_message(call, "x" * chars), reason=f"append:observation:{name}")
    return call


def wire_tool_use_ids(messages) -> tuple[set[str], set[str]]:
    """Return (tool_use ids offered, tool_result ids answered) from a wire payload."""
    _, wire = to_anthropic_messages(messages)
    offered: set[str] = set()
    answered: set[str] = set()
    for message in wire:
        for block in message["content"]:
            if block["type"] == "tool_use":
                offered.add(block["id"])
            elif block["type"] == "tool_result":
                answered.add(block["tool_use_id"])
    return offered, answered


class AccountingTests(unittest.TestCase):
    """Token accounting: conservative estimates, charged appends, honest headroom."""

    def test_estimate_is_conservative_and_never_zero_for_text(self):
        from budgetloop.messages import estimate_tokens

        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abc"), 1)
        self.assertGreaterEqual(estimate_tokens("a" * 400), 100)

    def test_message_cost_includes_framing_overhead(self):
        message = user_message("hello")
        self.assertEqual(message.tokens, 2 + MESSAGE_OVERHEAD_TOKENS)

    def test_append_charges_the_window_and_records_ledger_entry(self):
        window = ContextWindow(1_000, reserve_output_tokens=100)
        self.assertEqual(window.tokens, 0)
        message = window.append(user_message("do the thing"))
        self.assertEqual(window.tokens, message.tokens)
        self.assertEqual(window.headroom, 900 - message.tokens)
        self.assertEqual(len(window.ledger), 1)
        self.assertEqual(window.ledger[0].action, "append:user")

    def test_invalid_construction_is_rejected(self):
        with self.assertRaises(ValueError):
            ContextWindow(0)
        with self.assertRaises(ValueError):
            ContextWindow(100, reserve_output_tokens=100)

    def test_usable_budget_excludes_the_output_reserve(self):
        window = ContextWindow(1_000, reserve_output_tokens=250)
        self.assertEqual(window.usable_budget, 750)


class CompactionPolicyTests(unittest.TestCase):
    """What leaves the window, in what order, and what must never leave it."""

    def make_window(self, **overrides) -> ContextWindow:
        settings = {"budget": 700, "reserve_output_tokens": 60, "keep_recent_observations": 1}
        settings.update(overrides)
        return ContextWindow(**settings)

    def test_window_stays_inside_its_budget(self):
        window = self.make_window()
        window.append(system_message("system contract"))
        window.append(user_message("the task"))
        for index in range(1, 9):
            add_exchange(window, index, chars=400)
        self.assertLessEqual(window.tokens, window.usable_budget)
        self.assertTrue(window.compactions)

    def test_oldest_exchange_is_evicted_first(self):
        window = self.make_window()
        window.append(system_message("system contract"))
        window.append(user_message("the task"))
        for index in range(1, 11):
            add_exchange(window, index, chars=300, name=f"tool{index}")
        dropped = [name for event in window.compactions for name in event.dropped_tool_names]
        self.assertTrue(window.compactions)
        self.assertIn("tool1", dropped)
        self.assertNotIn("tool10", dropped)

    def test_newest_exchanges_are_protected(self):
        window = self.make_window(keep_recent_observations=2)
        window.append(system_message("system contract"))
        window.append(user_message("the task"))
        for index in range(1, 11):
            add_exchange(window, index, chars=300, name=f"tool{index}")
        dropped = [name for event in window.compactions for name in event.dropped_tool_names]
        self.assertTrue(window.compactions)
        self.assertIn("tool1", dropped)
        self.assertNotIn("tool9", dropped)
        self.assertNotIn("tool10", dropped)

    def test_system_prompt_and_current_task_survive(self):
        window = self.make_window()
        window.append(system_message("SYSTEM CONTRACT"))
        window.append(user_message("DO THE TASK"))
        for index in range(1, 8):
            add_exchange(window, index, chars=300)
        contents = [message.content for message in window.messages]
        self.assertIn("SYSTEM CONTRACT", contents)
        self.assertIn("DO THE TASK", contents)

    def test_eviction_never_orphans_a_tool_result(self):
        window = self.make_window()
        window.append(system_message("system contract"))
        window.append(user_message("the task"))
        for index in range(1, 9):
            add_exchange(window, index, chars=300)
        offered, answered = wire_tool_use_ids(window.messages)
        self.assertTrue(offered)
        self.assertEqual(answered - offered, set())

    def test_overflow_raises_when_nothing_is_evictable(self):
        window = ContextWindow(300, reserve_output_tokens=30, keep_recent_observations=2)
        window.append(system_message("short contract"))
        window.append(user_message("the task"))
        with self.assertRaises(ContextOverflow):
            window.append(user_message("x" * 4_000))


    def test_compacted_transcript_still_alternates_roles_on_the_wire(self):
        window = self.make_window()
        window.append(system_message("system contract"))
        window.append(user_message("the task"))
        for index in range(1, 9):
            add_exchange(window, index, chars=300)
        wire = to_anthropic_messages(window.messages)[1]
        roles = [message["role"] for message in wire]
        self.assertTrue(roles)
        for first, second in zip(roles, roles[1:]):
            self.assertNotEqual(first, second)

    def test_compaction_summary_is_bounded(self):
        window = self.make_window(summary_token_cap=40)
        window.append(system_message("system contract"))
        window.append(user_message("the task"))
        for index in range(1, 13):
            add_exchange(window, index, chars=600)
        summaries = [
            message
            for message in window.messages
            if message.content.startswith("[compacted context]")
        ]
        self.assertTrue(summaries, "expected at least one compaction summary")
        for summary in summaries:
            self.assertLessEqual(summary.tokens, 40 + MESSAGE_OVERHEAD_TOKENS + 5)

    def test_summaries_merge_instead_of_accumulating(self):
        # A regression guard: one summary per compaction would slowly fill the
        # window with protected summaries of summaries.
        window = self.make_window(summary_token_cap=60)
        window.append(system_message("system contract"))
        window.append(user_message("the task"))
        for index in range(1, 25):
            add_exchange(window, index, chars=700)
        summaries = [
            message
            for message in window.messages
            if message.content.startswith("[compacted context]")
        ]
        self.assertGreaterEqual(len(window.compactions), 2)
        self.assertEqual(len(summaries), 1)


class LedgerTests(unittest.TestCase):
    """The ledger is the evidence: it must exist, be ordered, and describe losses."""

    def build_window(self) -> ContextWindow:
        window = ContextWindow(700, reserve_output_tokens=60, keep_recent_observations=1)
        window.append(system_message("system contract"))
        window.append(user_message("the task"))
        for index in range(1, 8):
            add_exchange(window, index, chars=300)
        return window

    def test_ledger_records_appends_and_compactions(self):
        window = self.build_window()
        actions = [entry.action for entry in window.ledger]
        self.assertEqual(actions[0], "append:system")
        self.assertEqual(actions[1], "append:user")
        self.assertIn("compact", actions)
        self.assertTrue(any(action.startswith("append:observation:") for action in actions))
        steps = [entry.step for entry in window.ledger]
        self.assertEqual(steps, sorted(steps))

    def test_report_states_budget_and_loss(self):
        report = self.build_window().report()
        self.assertIn("budget=700", report)
        self.assertIn("usable=640", report)
        self.assertIn("compact", report)
        self.assertIn("budget, not a bucket", report)

    def test_to_dict_is_serialisable_and_self_describing(self):
        window = ContextWindow(500, reserve_output_tokens=50)
        window.append(system_message("system contract"))
        window.append(user_message("the task"))
        payload = window.to_dict()
        for key in ("budget", "usable_budget", "tokens", "ledger", "compactions", "final_window"):
            self.assertIn(key, payload)
        self.assertEqual(payload["usable_budget"], 450)
        self.assertEqual(len(payload["final_window"]), 2)


if __name__ == "__main__":
    unittest.main()
