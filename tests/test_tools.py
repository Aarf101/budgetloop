"""Tests for the tool layer: the sandbox, the approval gate, and argument hygiene.

These are the tests that would matter most if this loop ever ran unattended: a
sandbox that leaks and an approval gate that defaults to "yes" are the two ways
an agent harness turns into an incident report.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from budgetloop.tools import (
    ApprovalDenied,
    SandboxViolation,
    Tool,
    ToolError,
    ToolRegistry,
    Workspace,
    default_checks,
    default_registry,
    make_check_tool,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class WorkspaceSandboxTests(unittest.TestCase):
    """Confining the file tools to one root, and refusing everything outside it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "note.md").write_text("# note\nhello\n", encoding="utf-8")
        (self.root / "big.txt").write_text("y" * 5_000, encoding="utf-8")
        self.workspace = Workspace(self.root, max_read_bytes=1_000)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reads_a_file_inside_the_workspace(self):
        observation = self.workspace.read_file("docs/note.md")
        self.assertIn("hello", observation)
        self.assertIn("docs/note.md", observation)

    def test_read_is_truncated_with_a_visible_notice(self):
        self.assertIn("TRUNCATED", self.workspace.read_file("big.txt"))

    def test_parent_traversal_is_refused(self):
        with self.assertRaises(SandboxViolation):
            self.workspace.read_file("../../etc/passwd")

    def test_absolute_paths_are_refused(self):
        with self.assertRaises(SandboxViolation):
            self.workspace.read_file("/etc/passwd")

    def test_symlink_escape_is_refused(self):
        (self.root / "escape.txt").symlink_to("/etc/passwd")
        with self.assertRaises(SandboxViolation):
            self.workspace.read_file("escape.txt")

    def test_protected_directories_are_refused(self):
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("x", encoding="utf-8")
        with self.assertRaises(SandboxViolation):
            self.workspace.read_file(".git/config")

    def test_binary_files_are_refused(self):
        (self.root / "blob.bin").write_bytes(b"\x00\x01\x02binary")
        with self.assertRaises(ToolError):
            self.workspace.read_file("blob.bin")

    def test_missing_file_reports_a_tool_error(self):
        with self.assertRaises(ToolError):
            self.workspace.read_file("nope.txt")

    def test_empty_and_directory_paths_are_refused(self):
        with self.assertRaises(ToolError):
            self.workspace.read_file("")
        with self.assertRaises(ToolError):
            self.workspace.read_file("docs")

    def test_list_files_skips_protected_directories(self):
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "index.js").write_text("x", encoding="utf-8")
        listing = self.workspace.list_files("**/*")
        self.assertIn("docs/note.md", listing)
        self.assertNotIn("node_modules", listing)

    def test_write_creates_parent_directories_inside_the_workspace(self):
        observation = self.workspace.write_file("deep/nested/file.txt", "content")
        self.assertTrue((self.root / "deep" / "nested" / "file.txt").exists())
        self.assertIn("created", observation)
        self.assertIn("updated", self.workspace.write_file("deep/nested/file.txt", "again"))


class RegistryTests(unittest.TestCase):
    """Argument validation, the approval gate, and observation caps."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("alpha\n", encoding="utf-8")
        (self.root / "big.txt").write_text("z" * 200, encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unknown_tool_is_reported_with_the_known_names(self):
        registry = default_registry(self.workspace)
        with self.assertRaises(ToolError) as caught:
            registry.run("delete_everything", {})
        self.assertIn("read_file", str(caught.exception))

    def test_unexpected_arguments_are_rejected(self):
        registry = default_registry(self.workspace)
        with self.assertRaises(ToolError) as caught:
            registry.run("read_file", {"path": "a.txt", "mode": "rb"})
        self.assertIn("unexpected argument", str(caught.exception))

    def test_missing_arguments_are_rejected(self):
        registry = default_registry(self.workspace)
        with self.assertRaises(ToolError) as caught:
            registry.run("read_file", {})
        self.assertIn("missing required argument", str(caught.exception))

    def test_non_scalar_arguments_are_rejected(self):
        registry = default_registry(self.workspace)
        with self.assertRaises(ToolError):
            registry.run("read_file", {"path": ["a.txt"]})

    def test_numeric_arguments_are_coerced_to_strings(self):
        registry = ToolRegistry(workspace=self.workspace)
        seen: dict[str, object] = {}

        def handler(count):
            seen["count"] = count
            return f"count={count}"

        registry.register(
            Tool(
                name="count",
                description="counts things",
                parameters={"count": "how many"},
                handler=handler,
            )
        )
        registry.run("count", {"count": 3})
        self.assertEqual(seen["count"], "3")

    def test_duplicate_tool_names_are_rejected(self):
        registry = default_registry(self.workspace)
        with self.assertRaises(ToolError) as caught:
            registry.register(
                Tool(name="read_file", description="clash", parameters={}, handler=lambda: "")
            )
        self.assertIn("duplicate", str(caught.exception))

    def test_describe_lists_every_declared_tool(self):
        registry = default_registry(self.workspace)
        description = registry.describe()
        for name in ("read_file", "list_files", "write_file", "run_check"):
            self.assertIn(name, description)
        self.assertIn("[mutates]", description)

    def test_mutating_tool_without_a_policy_is_refused(self):
        registry = default_registry(self.workspace)
        with self.assertRaises(ApprovalDenied) as caught:
            registry.run("write_file", {"path": "new.txt", "content": "x"})
        self.assertIn("fail closed", str(caught.exception))
        self.assertFalse((self.root / "new.txt").exists())

    def test_mutating_tool_runs_when_the_operator_approves(self):
        seen: list[tuple[str, str]] = []

        def policy(tool, arguments) -> bool:
            seen.append((tool.name, arguments["path"]))
            return True

        registry = default_registry(self.workspace, approve=policy)
        observation = registry.run("write_file", {"path": "new.txt", "content": "hello"})
        self.assertEqual(seen, [("write_file", "new.txt")])
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "hello")
        self.assertIn("created", observation)

    def test_mutating_tool_is_blocked_when_the_operator_declines(self):
        registry = default_registry(self.workspace, approve=lambda tool, arguments: False)
        with self.assertRaises(ApprovalDenied):
            registry.run("write_file", {"path": "new.txt", "content": "hello"})
        self.assertFalse((self.root / "new.txt").exists())

    def test_read_only_tools_need_no_approval(self):
        registry = default_registry(self.workspace)
        self.assertIn("alpha", registry.run("read_file", {"path": "a.txt"}))

    def test_observations_are_capped(self):
        registry = default_registry(self.workspace)
        registry.max_observation_bytes = 40
        self.assertIn("truncated", registry.run("read_file", {"path": "big.txt"}))


class CheckToolTests(unittest.TestCase):
    """The check runner may only run commands a human registered by name."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_only_registered_checks_can_run(self):
        registry = default_registry(
            self.workspace,
            checks={"smoke": [sys.executable, "-c", "print('smoke ok')"]},
        )
        observation = registry.run("run_check", {"name": "smoke"})
        self.assertIn("exit_code=0", observation)
        self.assertIn("smoke ok", observation)
        with self.assertRaises(ToolError) as caught:
            registry.run("run_check", {"name": "rm -rf /"})
        self.assertIn("unknown check", str(caught.exception))

    def test_failing_check_reports_its_exit_code(self):
        registry = default_registry(
            self.workspace,
            checks={"fail": [sys.executable, "-c", "raise SystemExit(3)"]},
        )
        self.assertIn("exit_code=3", registry.run("run_check", {"name": "fail"}))

    def test_default_registry_registers_the_test_suite_check(self):
        registry = default_registry(self.workspace)
        self.assertIn("run_check", registry.names)
        self.assertIn("tests", registry.describe())
        self.assertIn("style", registry.describe())
        self.assertIn("src-docstrings", registry.describe())

    def test_default_checks_include_the_src_docstring_gate(self):
        checks = default_checks()
        self.assertEqual(
            checks["src-docstrings"],
            [sys.executable, "scripts/check_style.py", "src", "--src-docstrings-only"],
        )
        self.assertEqual(checks["style"], [sys.executable, "scripts/check_style.py"])

    def test_src_docstring_gate_passes_against_the_repo(self):
        workspace = Workspace(REPO_ROOT)
        registry = default_registry(workspace)
        observation = registry.run("run_check", {"name": "src-docstrings"})
        self.assertIn("exit_code=0", observation)
        self.assertIn("src-docstrings: clean", observation)

    def test_empty_check_allow_list_is_rejected(self):
        with self.assertRaises(ToolError):
            make_check_tool(self.workspace, {})


if __name__ == "__main__":
    unittest.main()
