"""Tests for the loop: termination, fault handling, and the evidence trail.

Every unhappy path here has exactly one expected outcome, and it is a recorded
status rather than an exception. That property is the difference between an agent
you can leave running and one you have to watch.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from budgetloop.loop import AgentLoop, RunStatus
from budgetloop.providers import EchoProvider, ScriptedProvider, final_reply, tool_call_reply
from budgetloop.tools import Workspace, default_registry


class LoopTestCase(unittest.TestCase):
    """Shared fixture: a tiny workspace plus a helper that builds a loop."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "facts.txt").write_text("the answer is 42\n", encoding="utf-8")
        (self.root / "large.txt").write_text("z" * 4_000, encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build_loop(self, replies, **overrides) -> AgentLoop:
        settings = {"budget": 2_000, "max_steps": 5, "trace_dir": None}
        settings.update(overrides)
        approve = settings.pop("approve", None)
        provider = settings.pop("provider", None) or ScriptedProvider(replies=list(replies))
        registry = default_registry(self.workspace, approve=approve)
        return AgentLoop(provider, registry, **settings)


class HappyPathTests(LoopTestCase):
    """The boring case that has to work before any of the others matter."""

    def test_run_completes_after_reading_a_file(self):
        loop = self.build_loop(
            [
                tool_call_reply("read_file", {"path": "facts.txt"}, "c1"),
                final_reply("The file says the answer is 42."),
            ]
        )
        result = loop.run("What does facts.txt say?")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertTrue(result.ok)
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.observations, 1)
        self.assertEqual(result.failed_observations, 0)
        self.assertIn("42", result.answer)

    def test_result_reports_the_budget_it_used(self):
        loop = self.build_loop([final_reply("done")], budget=1_000)
        result = loop.run("trivial task")
        self.assertGreater(result.final_tokens, 0)
        self.assertLessEqual(result.final_tokens, result.context["usable_budget"])
        self.assertIn("CONTEXT LEDGER", result.ledger_report)

    def test_an_empty_final_answer_is_reported_not_hidden(self):
        result = self.build_loop([final_reply("   ")]).run("task")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertIn("empty final answer", result.answer)

    def test_on_step_streams_each_turn(self):
        seen = []
        loop = self.build_loop(
            [
                tool_call_reply("list_files", {"pattern": "**/*.txt"}, "c1"),
                final_reply("done"),
            ],
            on_step=seen.append,
        )
        loop.run("list the text files")
        self.assertEqual([record.step for record in seen], [1, 2])
        self.assertEqual(seen[0].note, "acted")
        self.assertTrue(seen[0].observations[0]["ok"])
        self.assertEqual(seen[1].note, "final")

    def test_empty_task_is_rejected_before_the_provider_is_called(self):
        loop = self.build_loop([final_reply("done")])
        with self.assertRaises(ValueError):
            loop.run("   ")


class GuardrailTests(LoopTestCase):
    """Every stopping condition, asserted as a status rather than an exception."""

    def test_step_limit_ends_the_run_without_raising(self):
        replies = [tool_call_reply("list_files", {"pattern": "**/*"}, f"c{i}") for i in range(1, 9)]
        provider = ScriptedProvider(replies=replies)
        loop = self.build_loop(replies, max_steps=3, provider=provider)
        result = loop.run("loop forever")
        self.assertEqual(result.status, RunStatus.STEP_LIMIT)
        self.assertFalse(result.ok)
        self.assertEqual(result.steps, 3)
        self.assertEqual(provider.remaining, len(replies) - 3)
        self.assertIn("step limit", result.answer)

    def test_tool_refusal_is_an_observation_not_a_crash(self):
        loop = self.build_loop(
            [
                tool_call_reply("write_file", {"path": "x.txt", "content": "x"}, "c1"),
                final_reply("I could not write that file."),
            ]
        )
        result = loop.run("write a file")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.failed_observations, 1)
        observation = result.transcript[0].observations[0]
        self.assertFalse(observation["ok"])
        self.assertIn("fail closed", observation["error"])
        self.assertFalse((self.root / "x.txt").exists())

    def test_unknown_tool_becomes_an_error_observation(self):
        loop = self.build_loop(
            [tool_call_reply("drop_database", {}, "c1"), final_reply("no such tool")]
        )
        result = loop.run("do something silly")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.failed_observations, 1)
        self.assertIn("unknown tool", result.transcript[0].observations[0]["error"])

    def test_provider_failure_is_reported_as_a_status(self):
        result = self.build_loop([]).run("anything at all")
        self.assertEqual(result.status, RunStatus.PROVIDER_ERROR)
        self.assertIn("provider failed", result.answer)

    def test_unaffordable_fixed_context_ends_as_budget_exceeded(self):
        loop = self.build_loop([final_reply("done")], budget=300, max_output_tokens=60)
        result = loop.run("read the facts")
        self.assertEqual(result.status, RunStatus.BUDGET_EXCEEDED)
        self.assertEqual(result.steps, 0)
        self.assertIn("fixed context", result.answer)

    def test_observation_that_cannot_fit_ends_as_budget_exceeded(self):
        loop = self.build_loop(
            [
                tool_call_reply("read_file", {"path": "large.txt"}, "c1"),
                final_reply("done"),
            ],
            budget=700,
            max_output_tokens=100,
        )
        result = loop.run("read the large file")
        self.assertEqual(result.status, RunStatus.BUDGET_EXCEEDED)
        self.assertIn("overflow", result.answer)


class EvidenceTests(LoopTestCase):
    """The run must leave a trace a human can reopen, even when it fails."""

    def test_trace_file_records_task_status_and_context(self):
        trace_dir = self.root / "traces"
        loop = self.build_loop(
            [
                tool_call_reply("read_file", {"path": "facts.txt"}, "c1"),
                final_reply("42."),
            ],
            trace_dir=trace_dir,
        )
        result = loop.run("What does facts.txt say?")
        self.assertIsNotNone(result.trace_path)
        payload = json.loads(Path(str(result.trace_path)).read_text(encoding="utf-8"))
        self.assertEqual(payload["task"], "What does facts.txt say?")
        self.assertEqual(payload["provider"], "scripted")
        self.assertEqual(payload["result"]["status"], "completed")
        self.assertIn("ledger", payload["result"]["context"])
        self.assertTrue(payload["result"]["transcript"])

    def test_missing_trace_directory_is_created(self):
        trace_dir = self.root / "nested" / "traces"
        result = self.build_loop([final_reply("done")], trace_dir=trace_dir).run("task")
        self.assertTrue(Path(str(result.trace_path)).exists())

    def test_trace_failure_does_not_fail_the_run(self):
        blocker = self.root / "blocker"
        blocker.write_text("I am a file, not a directory", encoding="utf-8")
        result = self.build_loop([final_reply("done")], trace_dir=blocker / "traces").run("task")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertIn("trace not written", str(result.trace_path))

    def test_echo_provider_produces_a_completed_run_without_tools(self):
        provider = EchoProvider()
        result = self.build_loop([], provider=provider).run("what can you see?")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.tool_calls, 0)
        self.assertIn("read_file", result.answer)


if __name__ == "__main__":
    unittest.main()
