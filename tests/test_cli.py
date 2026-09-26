"""Tests for the command line interface: exit codes, guardrails, and evidence.

The CLI is where a human meets the guardrails, so these tests assert the things a
human would notice if they broke: exit codes, refusals that actually refuse, and
a `--json` mode that stays parseable.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import subprocess
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
from budgetloop.tools import ApprovalDenied, Workspace, demo_registry

REPO_ROOT = Path(__file__).resolve().parent.parent


def invoke(argv: list[str], stdin: io.StringIO | None = None) -> tuple[int, str]:
    """Run the CLI with stdout captured and stdin controlled, the way CI would.

    Stdin defaults to an empty non-tty stream, so approval-gate tests behave the
    same in a terminal (where stdin is a tty) and in CI (where it is not).
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), mock.patch("sys.stdin", stdin or io.StringIO("")):
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
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code, _ = invoke(self.base)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("DENIED", stderr.getvalue())
        self.assertFalse((self.root / "made-by-the-agent.txt").exists())

    def test_write_is_allowed_with_the_yes_flag(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = invoke(self.base + ["--yes"])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(
            (self.root / "made-by-the-agent.txt").read_text(encoding="utf-8"), "hello"
        )

    def test_json_output_stays_parseable_when_a_write_is_refused(self):
        # Narration belongs on stderr: stdout is the machine-readable channel.
        with contextlib.redirect_stderr(io.StringIO()):
            code, output = invoke(self.base + ["--json"])
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(output)
        self.assertEqual(payload["failed_observations"], 1)
        self.assertIn("declined", payload["transcript"][0]["observations"][0]["error"])

    def test_closed_stdin_counts_as_a_refusal(self):
        class ClosedStdin(io.StringIO):
            """A terminal whose user hung up: still a tty, but out of input."""

            def isatty(self) -> bool:
                return True

        with contextlib.redirect_stderr(io.StringIO()) as errors:
            code, _ = invoke(self.base, stdin=ClosedStdin(""))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("stdin closed", errors.getvalue())
        self.assertFalse((self.root / "made-by-the-agent.txt").exists())


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


class DemoNoWriteProofTests(unittest.TestCase):
    """The proof that ``make demo`` can never write a file.

    The claim is structural, not anecdotal: the demo registry has no
    subprocess tool (so no child process can write outside the approval
    gate), its only mutating tool refuses before touching the disk, and
    both loop passes run with ``trace_dir=None`` (so no trace is written).
    These tests fail if any of those properties regress, which is exactly
    what would silently reintroduce a demo write path.
    """

    def test_demo_registry_has_no_write_capable_tool(self):
        workspace = Workspace(REPO_ROOT)
        registry = demo_registry(workspace)
        self.assertEqual(registry.names, ("list_files", "read_file", "write_file"))
        self.assertNotIn("run_check", registry.names)
        for name in registry.names:
            tool = registry.get(name)
            if name == "write_file":
                self.assertTrue(tool.mutates)
            else:
                self.assertFalse(tool.mutates, f"{name} must stay read-only")

    def test_demo_write_handler_never_touches_the_disk(self):
        workspace = Workspace(REPO_ROOT)
        with mock.patch.object(
            Workspace, "write_file", side_effect=AssertionError("must not run")
        ):
            tool = demo_registry(workspace).get("write_file")
            self.assertNotIn("Workspace", type(tool.handler).__name__)
            with self.assertRaises(ApprovalDenied) as caught:
                tool.handler(path="demo-scratch.txt", content="x")
        self.assertIn("no approval policy configured", str(caught.exception))
        self.assertFalse((REPO_ROOT / "demo-scratch.txt").exists())

    def test_demo_run_refuses_write_even_with_an_approving_policy(self):
        workspace = Workspace(REPO_ROOT)
        with mock.patch.object(
            Workspace, "write_file", side_effect=AssertionError("must not run")
        ):
            registry = demo_registry(workspace)
            with self.assertRaises(ApprovalDenied):
                registry.run(
                    "write_file",
                    {"path": "demo-scratch.txt", "content": "x"},
                    approve=lambda tool, args: True,
                )
        self.assertFalse((REPO_ROOT / "demo-scratch.txt").exists())

    def test_demo_has_no_subprocess_or_trace_write_path(self):
        source = (REPO_ROOT / "src" / "budgetloop" / "cli.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        demo = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_demo"
        )
        names = {
            node.id for node in ast.walk(demo) if isinstance(node, ast.Name)
        }
        attrs = {
            node.attr for node in ast.walk(demo) if isinstance(node, ast.Attribute)
        }
        strings = {
            node.value
            for node in ast.walk(demo)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertIn("demo_registry", names)
        self.assertNotIn("default_registry", names)
        self.assertNotIn("default_checks", names)
        self.assertNotIn("subprocess", names | attrs)
        self.assertNotIn("Popen", names | attrs)
        self.assertNotIn("run_check", strings)
        keywords = {
            node.arg
            for node in ast.walk(demo)
            if isinstance(node, ast.keyword) and node.arg is not None
        }
        self.assertIn("trace_dir", keywords)
        values = [
            ast.literal_eval(node.value)
            for node in ast.walk(demo)
            if isinstance(node, ast.keyword)
            and node.arg == "trace_dir"
            and isinstance(node.value, ast.Constant)
        ]
        self.assertTrue(values, "cmd_demo must pass trace_dir explicitly")
        self.assertTrue(all(value is None for value in values))

    def test_demo_command_writes_nothing_with_writes_trapped(self):
        sentinel = REPO_ROOT / "demo-scratch.txt"
        if sentinel.exists():
            sentinel.unlink()
        real_open = open

        def _trapped_open(file, mode="r", *args, **kwargs):
            if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
                raise AssertionError(f"demo must not open for writing: {file!r}")
            return real_open(file, mode, *args, **kwargs)

        with mock.patch.object(
            Workspace, "write_file", side_effect=AssertionError("disk write")
        ), mock.patch(
            "subprocess.run", side_effect=AssertionError("subprocess write")
        ), mock.patch(
            "subprocess.Popen", side_effect=AssertionError("subprocess write")
        ), mock.patch(
            "pathlib.Path.write_text", side_effect=AssertionError("Path write")
        ), mock.patch(
            "pathlib.Path.write_bytes", side_effect=AssertionError("Path write")
        ), mock.patch(
            "pathlib.Path.mkdir", side_effect=AssertionError("mkdir")
        ), mock.patch(
            "builtins.open", side_effect=_trapped_open
        ):
            code, output = invoke(["demo", "--workspace", str(REPO_ROOT)])
        self.assertIn(code, (EXIT_OK, EXIT_GUARDRAIL))
        self.assertIn("no approval policy configured", output)
        self.assertFalse(sentinel.exists())


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
