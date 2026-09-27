"""Repository invariants that a fresh clone would otherwise discover the hard way.

The reviewer's first finding was a test that read a gitignored runtime directory:
green on the author's machine, red for everyone else. These tests exist so that
class of bug cannot come back quietly, and so the gate runs somewhere other than
one laptop.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
#: Directories the project writes at runtime and .gitignore excludes.
GITIGNORED_RUNTIME_DIRS = (".budgetloop",)
#: Files that name those directories on purpose: this guard, and the docs-checker
#: test that asserts runtime paths are exempt from link checking.
ALLOWED_MENTIONS = frozenset({"test_repo_hygiene.py", "test_docs_checker.py"})


class FreshCloneTests(unittest.TestCase):
    """Nothing under tests/ may depend on runtime artifacts."""

    def test_no_test_reads_a_gitignored_runtime_directory(self):
        offenders: list[str] = []
        for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
            if path.name in ALLOWED_MENTIONS:
                continue
            text = path.read_text(encoding="utf-8")
            for marker in GITIGNORED_RUNTIME_DIRS:
                if marker in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} mentions {marker}")
        self.assertEqual(
            offenders,
            [],
            "tests must pass on a fresh clone: use tests/fixtures/ instead of runtime artifacts",
        )

    def test_every_test_fixture_is_tracked_by_git(self):
        fixtures = sorted((REPO_ROOT / "tests" / "fixtures").glob("*"))
        self.assertTrue(fixtures, "expected at least one committed fixture")
        ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn("tests/fixtures", ignore, "fixtures must not be gitignored")


class ContinuousIntegrationTests(unittest.TestCase):
    """The gate has to run somewhere other than one laptop."""

    def workflow(self) -> str:
        """The CI workflow source, or an empty string when it is missing."""
        path = REPO_ROOT / ".github" / "workflows" / "check.yml"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def test_a_workflow_runs_the_gate_on_push_and_pull_request(self):
        workflow = self.workflow()
        self.assertTrue(workflow, "add .github/workflows/check.yml so the gate is not local-only")
        self.assertIn("make check", workflow)
        self.assertIn("push", workflow)
        self.assertIn("pull_request", workflow)

    def test_the_workflow_tests_the_oldest_supported_python(self):
        # AGENTS.md promises Python 3.10+; CI is where that promise is kept honest.
        workflow = self.workflow()
        self.assertIn('"3.10"', workflow)


if __name__ == "__main__":
    unittest.main()
