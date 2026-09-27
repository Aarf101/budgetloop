"""Tests for the token-budget reporter skill script.

The skill answers "where did the budget go" from a run's trace file,
so these tests pin the breakdown down: headroom from peak, per-step
growth, per-tool cost, and the compaction net. Bad traces must fail
loudly instead of scoring zero.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests._loader import REPO_ROOT, load_script

REPORTER = load_script("skills/report-token-budget/scripts/report_budget.py")
SKILL_DIR = REPO_ROOT / "skills" / "report-token-budget"


def sample_trace() -> dict:
    """A small trace with two tool steps and one compaction."""
    return {
        "task": "audit the budget",
        "provider": "scripted",
        "budget": 1000,
        "max_steps": 8,
        "result": {
            "status": "completed",
            "steps": 3,
            "tool_calls": 2,
            "observations": 2,
            "failed_observations": 1,
            "final_tokens": 700,
            "peak_tokens": 800,
            "dropped_messages": 2,
            "compactions": 1,
            "transcript": [
                {
                    "step": 1,
                    "assistant_text": "list first",
                    "tool_calls": [
                        {"name": "list_files", "arguments": {"pattern": "**/*.py"}}
                    ],
                    "observations": [
                        {"tool": "list_files", "ok": True, "chars": 500, "error": None}
                    ],
                    "window_tokens": 300,
                    "note": "acted",
                },
                {
                    "step": 2,
                    "assistant_text": "read one file",
                    "tool_calls": [
                        {"name": "read_file", "arguments": {"path": "README.md"}}
                    ],
                    "observations": [
                        {
                            "tool": "read_file",
                            "ok": False,
                            "chars": 2000,
                            "error": "refused",
                        }
                    ],
                    "window_tokens": 800,
                    "note": "acted",
                },
                {
                    "step": 3,
                    "assistant_text": "done",
                    "tool_calls": [],
                    "observations": [],
                    "window_tokens": 700,
                    "note": "final",
                },
            ],
            "context": {
                "budget": 1000,
                "reserve_output_tokens": 200,
                "usable_budget": 800,
                "tokens": 700,
                "peak_tokens": 800,
                "messages": 7,
                "dropped_messages": 2,
                "ledger": [],
                "compactions": [],
            },
        },
    }


def with_compaction(payload: dict) -> dict:
    """Return the sample trace with one compaction event attached."""
    context = payload["result"]["context"]
    context["compactions"] = [
        {
            "step": 2,
            "dropped_messages": 2,
            "dropped_tool_names": ["list_files"],
            "reclaimed_tokens": 150,
            "summary_tokens": 50,
            "net_reclaimed": 100,
            "reason": "append:observation needs room",
        }
    ]
    return payload


class SummariseTests(unittest.TestCase):
    """Headroom, biggest step, biggest tool, and the compaction net."""

    def test_headroom_uses_peak_not_final(self):
        summary = REPORTER.summarise(with_compaction(sample_trace()))
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["usable_budget"], 800)
        self.assertEqual(summary["peak_tokens"], 800)
        self.assertEqual(summary["headroom_tokens"], 0)
        self.assertAlmostEqual(summary["utilisation"], 1.0)

    def test_biggest_step_and_tool_are_named(self):
        summary = REPORTER.summarise(with_compaction(sample_trace()))
        biggest_step = summary["biggest_step"]
        biggest_tool = summary["biggest_tool"]
        assert isinstance(biggest_step, dict)
        assert isinstance(biggest_tool, dict)
        self.assertEqual(biggest_step["step"], 2)
        self.assertEqual(biggest_step["growth_tokens"], 500)
        self.assertEqual(biggest_tool["tool"], "read_file")
        self.assertEqual(summary["tool_costs"][0]["failures"], 1)

    def test_compaction_net_is_reported(self):
        summary = REPORTER.summarise(with_compaction(sample_trace()))
        self.assertEqual(summary["compactions"], 1)
        self.assertEqual(summary["dropped_messages"], 2)
        self.assertEqual(summary["net_reclaimed"], 100)

    def test_trace_without_compactions_reports_zero_net(self):
        summary = REPORTER.summarise(sample_trace())
        self.assertEqual(summary["compaction_events"], [])
        self.assertEqual(summary["net_reclaimed"], 0)
        self.assertIn("never filled", REPORTER.render(summary))

    def test_render_states_budget_steps_tools_and_compactions(self):
        text = REPORTER.render(REPORTER.summarise(with_compaction(sample_trace())))
        self.assertIn("peak 800", text)
        self.assertIn("step  2 [acted] read_file", text)
        self.assertIn("read_file: 1 call(s), 2000 chars, 1 failed", text)
        self.assertIn("dropped 2 message(s) [list_files]", text)
        self.assertIn("biggest step: 2", text)
        self.assertIn("biggest tool: read_file", text)


class MalformedTraceTests(unittest.TestCase):
    """Unreadable traces are refused, never scored as zero."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        """Write helper text to the fixture directory."""
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_is_an_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(REPORTER.main([str(self.root / "absent.json")]), 2)

    def test_invalid_json_is_an_error(self):
        path = self.write("bad.json", "{not json")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(REPORTER.main([str(path)]), 2)

    def test_non_object_envelope_is_an_error(self):
        path = self.write("list.json", "[1, 2]")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(REPORTER.main([str(path)]), 2)

    def test_bad_usage_needs_exactly_one_trace(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(REPORTER.main([]), 2)
            self.assertEqual(REPORTER.main(["a.json", "b.json"]), 2)

    def test_a_committed_trace_fixture_reports_its_biggest_spender(self):
        # Bound to a committed fixture, never to the gitignored runtime trace
        # directory: a test that needs a runtime artifact fails on a fresh clone.
        fixture = REPO_ROOT / "tests" / "fixtures" / "trace-demo-run.json"
        self.assertTrue(fixture.is_file(), "the trace fixture must be committed")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(REPORTER.main([str(fixture)]), 0)
        self.assertIn("biggest tool: read_file", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(REPORTER.main([str(fixture), "--json"]), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["biggest_tool"]["tool"], "read_file")
        self.assertEqual(payload["biggest_step"]["step"], 2)


class SkillLayoutTests(unittest.TestCase):
    """The new skill ships the documented layout beside the reporter."""

    def test_skill_files_exist(self):
        for relative in (
            "SKILL.md",
            "scripts/report_budget.py",
            "references/budget-fields.md",
            "assets/report-template.md",
        ):
            with self.subTest(path=relative):
                self.assertTrue((SKILL_DIR / relative).is_file())


if __name__ == "__main__":
    unittest.main()
