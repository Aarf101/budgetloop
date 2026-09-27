"""Tests for the Markdown checker: docs that point at nothing must fail the gate.

The reviewer's second finding was documentation drift — a README pointing at a file
that does not exist, a layout that had stopped matching the tree. These tests pin
the checker that catches the mechanical half of that, in both directions: resolving
links pass, dead ones fail, and prose about other repositories can opt out.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests._loader import REPO_ROOT, load_script

DOCS = load_script("scripts/check_docs.py")


class CheckerRuleTests(unittest.TestCase):
    """One case per rule, including the escapes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "target.md").write_text("# target\n", encoding="utf-8")
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "tool.py").write_text("# tool\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, content: str, name: str = "doc.md") -> list[str]:
        """Write a document, check it, and return the broken targets found."""
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return [finding.target for finding in DOCS.check_file(path)]

    def test_a_resolving_link_passes(self):
        self.assertEqual(self.check("see [target](target.md) for details\n"), [])

    def test_a_broken_relative_link_is_flagged(self):
        self.assertEqual(self.check("see [gone](missing.md)\n"), ["missing.md"])

    def test_an_anchor_or_external_link_is_ignored(self):
        content = "[top](#section) and [site](https://example.com/x.md) and [mail](mailto:a@b.c)\n"
        self.assertEqual(self.check(content), [])

    def test_links_inside_fenced_code_blocks_are_ignored(self):
        self.assertEqual(self.check("```\n[fake](nope.md)\n```\n"), [])

    def test_a_broken_path_in_a_code_span_is_flagged(self):
        # This is the class of bug the reviewer found: a path in prose, not a link.
        self.assertEqual(self.check("run `tests/test_ghosts.py` first\n"), ["tests/test_ghosts.py"])

    def test_a_resolving_path_in_a_code_span_passes(self):
        self.assertEqual(self.check("look at `scripts/tool.py`\n"), [])

    def test_a_bare_filename_in_prose_is_not_treated_as_a_path(self):
        self.assertEqual(self.check("see `AGENTS.md` and `SKILL.md`\n"), [])

    def test_globs_and_placeholders_are_not_paths(self):
        self.assertEqual(self.check("`docs/*.md` and `{{path}}` and `$HOME/x.md`\n"), [])

    def test_runtime_directories_are_not_required_to_exist(self):
        self.assertEqual(self.check("traces live in `.budgetloop/traces/run-1.json`\n"), [])

    def test_the_skip_marker_exempts_one_line(self):
        content = "another repo uses `.foreign/config.yml` <!-- docs-check: skip -->\n"
        self.assertEqual(self.check(content), [])

    def test_main_reports_broken_links_and_exit_codes(self):
        self.check("see [gone](missing.md)\n")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(DOCS.main([str(self.root)]), 1)
        self.assertIn("missing.md", output.getvalue())
        (self.root / "doc.md").write_text("see [target](target.md)\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(DOCS.main([str(self.root)]), 0)

    def test_json_output_is_parseable(self):
        self.check("see [gone](missing.md)\n")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(DOCS.main([str(self.root), "--json"]), 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload[0]["target"], "missing.md")

    def test_missing_path_is_a_clean_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(DOCS.main([str(self.root / "absent")]), 2)


class RepositoryDocsTests(unittest.TestCase):
    """The repository must pass its own documentation check."""

    def test_the_repository_has_no_broken_links_or_paths(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(DOCS.main([str(REPO_ROOT)]), 0, output.getvalue())
        self.assertIn("docs: clean", output.getvalue())

    def test_every_shipped_skill_is_mentioned_in_the_readme(self):
        # Drift guard: the layout said "one skill" while two shipped.
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for skill in sorted((REPO_ROOT / "skills").iterdir()):
            if not skill.is_dir():
                continue
            with self.subTest(skill=skill.name):
                self.assertIn(skill.name, readme, "the README layout must mention every skill")

    def test_every_script_is_mentioned_somewhere_a_reader_looks(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for script in sorted((REPO_ROOT / "scripts").glob("*.py")):
            with self.subTest(script=script.name):
                self.assertIn(script.name, readme + agents)


if __name__ == "__main__":
    unittest.main()
