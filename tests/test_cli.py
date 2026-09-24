"""Tests for the command line interface: exit codes, guardrails, and evidence.

The CLI is where a human meets the guardrails, so these tests assert the things a
human would notice if they broke: exit codes, refusals that actually refuse, and
a `--json` mode that stays parseable.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from budgetloop.cli import (
    EXIT_GUARDRAIL,
    EXIT_OK,
    build_parser,
    build_system_prompt,
    load_script,
    main,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def invoke(argv: list[str]) -> tuple[int, str]:
    """Run the CLI with stdout captured, the way a shell would."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


@contextlib.contextmanager
def quiet_stderr():
    """Swallow argparse's usage output while a test asserts on SystemExit."""
    with contextlib.redirect_stderr(io.StringIO()):
        yield


class ParserTests(unittest.TestCase):
    """The defaults matter: offline, bounded and non-approving unless told otherwise."""

    def test_a_subcommand_is_required(self):
        with quiet_stderr(), self.assertRaises(SystemExit):
            build_parser().parse_args([])

    def test_run_requires_a_task(self):
        with quiet_stderr(), self.assertRaises(SystemExit):
            build_parser().parse_args(["run"])

    def test_defaults_are_offline_and_bounded(self):
        args = build_parser().parse_args(["run", "--task", "do a thing"])
        self.assertEqual(args.provider, "scripted")
        self.assertFalse(args.allow_network)
        self.assertEqual(args.budget, 4_000)
        self.assertEqual(args.max_steps, 12)
        self.assertFalse(args.yes)


class ScriptAndBriefTests(unittest.TestCase):
    """The two ways context enters a run: a reply script and a project brief."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_script_loads_tool_and_final_entries(self):
        path = self.write(
            "script.json",
            json.dumps(
                [
                    {"tool": "read_file", "arguments": {"path": "note.txt"}},
                    {"final": "read it"},
                ]
            ),
        )
        replies = load_script(str(path))
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0].tool_calls[0].name, "read_file")
        self.assertEqual(replies[0].tool_calls[0].arguments, {"path": "note.txt"})
        self.assertEqual(replies[1].content, "read it")

    def test_script_rejects_malformed_input(self):
        cases = {
            "empty.json": "[]",
            "object.json": "{}",
            "bad-json.json": "{not json",
            "no-key.json": '[{"note": "nothing to do"}]',
            "list-entry.json": '["just a string"]',
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                path = self.write(name, text)
                with self.assertRaises(SystemExit):
                    load_script(str(path))

    def test_brief_follows_the_contract_in_the_system_prompt(self):
        brief = self.write("AGENTS.md", "# Conventions\nRun `make check` before claiming done.\n")
        args = build_parser().parse_args(
            ["run", "--task", "t", "--brief", str(brief), "--brief-chars", "500"]
        )
        prompt = build_system_prompt(args)
        self.assertLess(prompt.index("careful coding agent"), prompt.index("make check"))
        self.assertIn("authoritative", prompt)

    def test_brief_is_truncated_at_the_requested_limit(self):
        brief = self.write("big.md", "x" * 4_000)
        args = build_parser().parse_args(
            ["run", "--task", "t", "--brief", str(brief), "--brief-chars", "100"]
        )
        self.assertIn("truncated at 100 characters", build_system_prompt(args))

    def test_missing_brief_file_is_a_clean_error(self):
        args = build_parser().parse_args(
            ["run", "--task", "t", "--brief", str(self.root / "absent.md")]
        )
        with self.assertRaises(SystemExit):
            build_system_prompt(args)


class RunCommandTests(unittest.TestCase):
    """Exit codes and output shapes a shell script or CI job would depend on."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "note.txt").write_text("the note is short\n", encoding="utf-8")
        self.script = self.root / "read.json"
        self.script.write_text(
            json.dumps(
                [
                    {"tool": "read_file", "arguments": {"path": "note.txt"}},
                    {"final": "The note is short."},
                ]
            ),
            encoding="utf-8",
        )
        self.base = [
            "run",
            "--workspace",
            str(self.root),
            "--task",
            "summarise note.txt",
            "--script",
            str(self.script),
            "--trace-dir",
            str(self.root / "traces"),
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_offline_run_completes_and_reports_a_trace(self):
        code, output = invoke(self.base)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("[completed]", output)
        self.assertIn("trace:", output)
        self.assertTrue(list((self.root / "traces").glob("run-*.json")))

    def test_json_output_is_parseable(self):
        code, output = invoke(self.base + ["--json"])
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(output)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["steps"], 2)
        self.assertIn("ledger", payload["context"])

    def test_live_provider_requires_the_network_flag(self):
        with self.assertRaises(SystemExit):
            invoke(["run", "--task", "t", "--provider", "anthropic"])

    def test_live_provider_requires_a_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(SystemExit):
                invoke(["run", "--task", "t", "--provider", "anthropic", "--allow-network"])

    def test_step_limit_is_reported_with_a_guardrail_exit_code(self):
        loop_script = self.root / "loop.json"
        loop_script.write_text(
            json.dumps([{"tool": "list_files", "arguments": {"pattern": "*"}}] * 4),
            encoding="utf-8",
        )
        code, output = invoke(
            [
                "run",
                "--workspace",
                str(self.root),
                "--task",
                "spin",
                "--script",
                str(loop_script),
                "--max-steps",
                "2",
                "--no-trace",
            ]
        )
        self.assertEqual(code, EXIT_GUARDRAIL)
        self.assertIn("step_limit", output)


class ApprovalGateTests(unittest.TestCase):
    """The gate is only worth having if the default answer is no."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.script = self.root / "write.json"
        self.script.write_text(
            json.dumps(
                [
                    {
                        "tool": "write_file",
                        "arguments": {"path": "made-by-the-agent.txt", "content": "hello"},
                    },
                    {"final": "I asked to write a file."},
                ]
            ),
            encoding="utf-8",
        )
        self.base = [
            "run",
            "--workspace",
            str(self.root),
            "--task",
            "write a file",
            "--script",
            str(self.script),
            "--no-trace",
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_is_denied_without_a_terminal_or_yes(self):
        code, output = invoke(self.base)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("DENIED", output)
        self.assertFalse((self.root / "made-by-the-agent.txt").exists())

    def test_write_is_allowed_with_the_yes_flag(self):
        code, output = invoke(self.base + ["--yes"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("allowed by --yes", output)
        self.assertEqual(
            (self.root / "made-by-the-agent.txt").read_text(encoding="utf-8"), "hello"
        )


class DemoTests(unittest.TestCase):
    """The demo is the teaching artifact, so it is tested against the real repo."""

    def test_demo_runs_offline_compacts_and_writes_nothing(self):
        code, output = invoke(["demo", "--workspace", str(REPO_ROOT)])
        # The demo self-sizes its budget, so it must finish or say why it stopped.
        self.assertIn(code, (EXIT_OK, EXIT_GUARDRAIL))
        self.assertIn("no approval policy configured", output)
        self.assertIn("budget, not a bucket", output)
        self.assertIn("measured cost with no budget pressure", output)
        match = re.search(r"compaction events (\d+)", output)
        self.assertIsNotNone(match, "the demo should print a budget report")
        self.assertGreaterEqual(int(match.group(1)), 1)
        self.assertFalse((REPO_ROOT / "demo-scratch.txt").exists())


class InspectTests(unittest.TestCase):
    """Reopening the evidence: the trace must be enough to explain a past run."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "note.txt").write_text("notes\n", encoding="utf-8")
        script = self.root / "read.json"
        script.write_text(
            json.dumps(
                [
                    {"tool": "read_file", "arguments": {"path": "note.txt"}},
                    {"final": "done"},
                ]
            ),
            encoding="utf-8",
        )
        code, _ = invoke(
            [
                "run",
                "--workspace",
                str(self.root),
                "--task",
                "read note.txt",
                "--script",
                str(script),
                "--trace-dir",
                str(self.root / "traces"),
            ]
        )
        self.assertEqual(code, EXIT_OK)
        self.trace = next((self.root / "traces").glob("run-*.json"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_inspect_summarises_a_trace_file(self):
        code, output = invoke(["inspect", str(self.trace)])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("status:   completed", output)
        self.assertIn("read note.txt", output)
        self.assertIn("step  1 [acted] read_file", output)

    def test_inspect_can_emit_the_raw_trace(self):
        code, output = invoke(["inspect", str(self.trace), "--json"])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(json.loads(output)["result"]["status"], "completed")

    def test_missing_trace_is_a_clean_error(self):
        with self.assertRaises(SystemExit):
            invoke(["inspect", str(self.root / "absent.json")])


if __name__ == "__main__":
    unittest.main()
