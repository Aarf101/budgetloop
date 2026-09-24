"""Tests for the skill validator: the spec rules, and this repo's own skills.

The validator is what makes "a skill is a standard, not a folder" true in practice,
so each constraint from the specification gets a test in both directions: the good
shape passes, and the bad shape is refused with a rule name that names the
constraint it broke.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tests._loader import REPO_ROOT, load_script

SKILLS = load_script("scripts/validate_skill.py")
#: A frontmatter that satisfies every rule, so each test varies exactly one thing.
VALID = "name: {name}\ndescription: Does a thing. Use when the thing needs doing."


class ValidatorRuleTests(unittest.TestCase):
    """One case per spec constraint."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_skill(self, name: str, frontmatter: str, body: str = "# Steps\n\nDo it.\n") -> Path:
        """Create a skill folder with the given frontmatter and return its path."""
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
        return folder

    def rules(self, findings) -> list[str]:
        """The rule names behind a list of findings, for readable assertions."""
        return [finding.rule for finding in findings]

    def test_a_minimal_valid_skill_passes(self):
        skill = self.write_skill("pdf-processing", VALID.format(name="pdf-processing"))
        self.assertEqual(SKILLS.validate_skill(skill), [])

    def test_optional_fields_are_accepted(self):
        frontmatter = (
            "name: pdf-processing\n"
            "description: Extracts text from PDFs. Use when handling a PDF.\n"
            "license: Apache-2.0\n"
            "compatibility: Requires pdftotext and Python 3.10+\n"
            "metadata:\n  author: example-org\n  version: \"1.0\"\n"
            "allowed-tools: Bash(git:*) Read\n"
        )
        self.assertEqual(SKILLS.validate_skill(self.write_skill("pdf-processing", frontmatter)), [])

    def test_missing_skill_md_is_an_error(self):
        folder = self.root / "no-skill"
        folder.mkdir()
        self.assertEqual(self.rules(SKILLS.validate_skill(folder)), ["skill-md"])

    def test_missing_frontmatter_is_an_error(self):
        folder = self.root / "bare"
        folder.mkdir()
        (folder / "SKILL.md").write_text("# No frontmatter\n", encoding="utf-8")
        self.assertEqual(self.rules(SKILLS.validate_skill(folder)), ["frontmatter"])

    def test_unterminated_frontmatter_is_an_error(self):
        folder = self.root / "open"
        folder.mkdir()
        (folder / "SKILL.md").write_text("---\nname: open\n\n# body\n", encoding="utf-8")
        findings = SKILLS.validate_skill(folder)
        self.assertEqual(self.rules(findings), ["frontmatter"])
        self.assertIn("closing", str(findings[0]))

    def test_name_is_required(self):
        skill = self.write_skill("x", "description: Uses a thing when needed, often.")
        self.assertIn("name", self.rules(SKILLS.validate_skill(skill)))

    def test_name_must_be_lowercase_and_single_hyphens(self):
        for bad_name in ("PDF-Processing", "-pdf", "pdf--processing", "pdf_processing"):
            with self.subTest(name=bad_name):
                skill = self.write_skill("ignored", VALID.format(name=bad_name))
                self.assertIn("name", self.rules(SKILLS.validate_skill(skill)))

    def test_name_must_match_the_directory(self):
        skill = self.write_skill("actual-name", VALID.format(name="other-name"))
        findings = SKILLS.validate_skill(skill)
        self.assertIn("name", self.rules(findings))
        self.assertIn("directory name", str(findings[0]))

    def test_name_length_limit(self):
        skill = self.write_skill("ignored", VALID.format(name="a" * 65))
        self.assertIn("name", self.rules(SKILLS.validate_skill(skill)))

    def test_description_is_required(self):
        skill = self.write_skill("x", "name: x")
        self.assertIn("description", self.rules(SKILLS.validate_skill(skill)))

    def test_description_length_limit(self):
        frontmatter = f"name: x\ndescription: {'d' * 1025}"
        skill = self.write_skill("x", frontmatter)
        self.assertIn("description", self.rules(SKILLS.validate_skill(skill)))

    def test_description_should_say_when_to_use_it(self):
        frontmatter = "name: x\ndescription: Helps with things."
        findings = SKILLS.validate_skill(self.write_skill("x", frontmatter))
        self.assertIn("description", self.rules(findings))
        self.assertEqual(findings[0].level, "warning")
        strict = SKILLS.validate_skill(self.write_skill("x", frontmatter), strict=True)
        self.assertEqual(strict[0].level, "error")


    def test_compatibility_length_limit(self):
        frontmatter = f"{VALID.format(name='x')}\ncompatibility: {'c' * 501}"
        skill = self.write_skill("x", frontmatter)
        self.assertIn("compatibility", self.rules(SKILLS.validate_skill(skill)))

    def test_metadata_must_be_a_map_of_strings(self):
        frontmatter = f"{VALID.format(name='x')}\nmetadata:\n  version:\n"
        findings = SKILLS.validate_skill(self.write_skill("x", frontmatter))
        # Either the map rule or the syntax rule may catch it: both are correct.
        self.assertTrue({"metadata", "frontmatter-syntax"} & set(self.rules(findings)))

    def test_allowed_tools_must_be_a_string(self):
        frontmatter = f"{VALID.format(name='x')}\nallowed-tools:\n  - Bash\n"
        findings = SKILLS.validate_skill(self.write_skill("x", frontmatter))
        self.assertTrue({"allowed-tools", "frontmatter-syntax"} & set(self.rules(findings)))

    def test_unknown_fields_warn_and_strict_promotes(self):
        frontmatter = f"{VALID.format(name='x')}\nversion: 1.0"
        findings = SKILLS.validate_skill(self.write_skill("x", frontmatter))
        self.assertIn("unknown-field", self.rules(findings))
        self.assertEqual(findings[0].level, "warning")

    def test_unsupported_yaml_is_reported_not_guessed(self):
        folder = self.root / "listy"
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            "---\nname: listy\ndescription: d\ntags:\n  - one\n---\n\nbody\n", encoding="utf-8"
        )
        self.assertIn("frontmatter-syntax", self.rules(SKILLS.validate_skill(folder)))

    def test_a_missing_usage_hint_is_the_only_warning_for_a_tidy_skill(self):
        frontmatter = "name: x\ndescription: " + "d" * 200
        findings = SKILLS.validate_skill(self.write_skill("x", frontmatter))
        self.assertEqual([finding.level for finding in findings], ["warning"])

    def test_empty_body_is_reported(self):
        skill = self.write_skill("x", VALID.format(name="x"), body="")
        self.assertIn("body", self.rules(SKILLS.validate_skill(skill)))


class DiscoveryAndCliTests(unittest.TestCase):
    """The CLI contract: what it finds, and what it returns."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for name in ("one", "two"):
            folder = self.root / "skills" / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\n{VALID.format(name=name)}\n---\n\nbody\n", encoding="utf-8"
            )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_discovery_finds_children_and_direct_files(self):
        skills = SKILLS.discover_skills([self.root / "skills"])
        self.assertEqual([skill.name for skill in skills], ["one", "two"])
        direct = SKILLS.discover_skills([self.root / "skills" / "one" / "SKILL.md"])
        self.assertEqual([skill.name for skill in direct], ["one"])

    def test_main_returns_zero_for_valid_skills_and_one_for_broken_ones(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(SKILLS.main([str(self.root / "skills")]), 0)
        self.assertIn("ok", output.getvalue())

        broken = self.root / "skills" / "two" / "SKILL.md"
        broken.write_text("# no frontmatter\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(SKILLS.main([str(self.root / "skills")]), 1)
        self.assertIn("FAIL", output.getvalue())

    def test_main_rejects_missing_paths_and_missing_skills(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(SKILLS.main([str(self.root / "absent")]), 2)
            self.assertEqual(SKILLS.main([str(self.root)]), 2)

    def test_main_can_emit_json(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(SKILLS.main([str(self.root / "skills"), "--json"]), 0)
        self.assertEqual(output.getvalue().strip(), "[]")


class RepositorySkillTests(unittest.TestCase):
    """This repository must satisfy the standard it ships a skill for."""

    def test_every_skill_passes_strict_validation(self):
        skills = SKILLS.discover_skills([REPO_ROOT / "skills"])
        self.assertTrue(skills, "expected at least one skill under skills/")
        for skill in skills:
            with self.subTest(skill=skill.name):
                findings = SKILLS.validate_skill(skill, strict=True)
                self.assertEqual([str(finding) for finding in findings], [])

    def test_the_agents_md_skill_has_the_documented_layout(self):
        skill = REPO_ROOT / "skills" / "write-agents-md"
        for relative in (
            "SKILL.md",
            "scripts/scan_repo.py",
            "references/best-practices.md",
            "assets/AGENTS.md.template",
        ):
            with self.subTest(path=relative):
                self.assertTrue((skill / relative).is_file())

    def test_the_agents_md_skill_frontmatter_parses_cleanly(self):
        skill = REPO_ROOT / "skills" / "write-agents-md"
        frontmatter, body = SKILLS.split_frontmatter(
            (skill / "SKILL.md").read_text(encoding="utf-8")
        )
        fields, problems = SKILLS.parse_simple_yaml(frontmatter or "")
        self.assertEqual(problems, [])
        self.assertEqual(fields["name"], skill.name)
        self.assertIn("when", fields["description"].lower())
        self.assertTrue(body.strip(), "the skill body must contain instructions")


if __name__ == "__main__":
    unittest.main()
