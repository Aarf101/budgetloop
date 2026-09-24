"""Tests for the style checker itself: it must catch what it claims to catch.

A checker nobody tests is a checker that quietly stops working, so every rule in
``scripts/check_style.py`` gets a deliberately broken file here. The marker tokens
are assembled from fragments because this file lives inside the tree the checker
scans, and it should not flag itself.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_WORD = "TO" + "DO"
PLACEHOLDER_NAME = "NotImple" + "mentedError"


def load_checker():
    """Import scripts/check_style.py by path, without depending on sys.path."""
    path = REPO_ROOT / "scripts" / "check_style.py"
    spec = importlib.util.spec_from_file_location("check_style_under_test", path)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve annotations through sys.modules, so register first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STYLE = load_checker()


class CheckerRuleTests(unittest.TestCase):
    """One case per rule: the checker must flag what it claims to flag."""

    def check(self, content: str, name: str = "sample.py") -> list[str]:
        """Write ``content`` to a temp file and return the rule names it violates."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return [violation.rule for violation in STYLE.check_file(path)]

    def test_a_clean_file_passes(self):
        source = (
            '"""A module docstring."""\n\n\n'
            'def go():\n    """Public, documented."""\n    return 1\n'
        )
        self.assertEqual(self.check(source), [])

    def test_long_line_is_flagged(self):
        self.assertIn("line-length", self.check('"""Doc."""\n\nvalue = "' + "a" * 120 + '"\n'))

    def test_trailing_whitespace_is_flagged(self):
        self.assertIn("trailing-whitespace", self.check('"""Doc."""\nvalue = 1 \n'))

    def test_tabs_are_flagged(self):
        self.assertIn("tabs", self.check('"""Doc."""\n\nif True:\n\tpass\n'))

    def test_print_in_library_code_is_flagged(self):
        self.assertIn("no-print", self.check('"""Doc."""\n\nprint("hi")\n'))

    def test_print_in_a_script_is_allowed(self):
        self.assertNotIn("no-print", self.check('"""Doc."""\n\nprint("hi")\n', name="scripts/x.py"))

    def test_bare_except_is_flagged(self):
        source = '"""Doc."""\n\ntry:\n    pass\nexcept:\n    pass\n'
        self.assertIn("bare-except", self.check(source))

    def test_placeholder_markers_are_flagged(self):
        comment = f'"""Doc."""\n\n# {PLACEHOLDER_WORD}: later\nvalue = 1\n'
        self.assertIn("placeholder", self.check(comment))
        raise_source = (
            f'"""Doc."""\n\n\ndef go():\n    """Doc."""\n    raise {PLACEHOLDER_NAME}\n'
        )
        self.assertIn("placeholder", self.check(raise_source))

    def test_missing_final_newline_is_flagged(self):
        self.assertIn("final-newline", self.check('"""Doc."""\nvalue = 1'))

    def test_extra_final_newline_is_flagged(self):
        self.assertIn("final-newline", self.check('"""Doc."""\nvalue = 1\n\n'))

    def test_missing_module_docstring_is_flagged(self):
        self.assertIn("module-docstring", self.check("value = 1\n"))

    def test_public_function_without_a_docstring_is_flagged(self):
        self.assertIn("public-docstring", self.check('"""Doc."""\n\n\ndef go():\n    return 1\n'))

    def test_private_functions_need_no_docstring(self):
        private_source = '"""Doc."""\n\n\ndef _go():\n    return 1\n'
        self.assertNotIn("public-docstring", self.check(private_source))

    def test_syntax_errors_are_reported(self):
        self.assertIn("parses", self.check('"""Doc."""\n\ndef broken(:\n'))

    def test_missing_path_is_a_clean_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(STYLE.main(["/definitely/not/here"]), 2)


class RepositoryIsCleanTests(unittest.TestCase):
    """The repo must pass its own gate: a rule nobody follows is decoration."""

    def test_the_repository_passes_its_own_style_rules(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = STYLE.main([str(REPO_ROOT)])
        self.assertEqual(code, 0, output.getvalue())

    def test_skip_directories_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "bad.py").write_text("value=1", encoding="utf-8")
            self.assertEqual(STYLE.python_files([root]), [])


if __name__ == "__main__":
    unittest.main()
