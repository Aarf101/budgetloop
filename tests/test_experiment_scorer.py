"""Tests for the experiment scorer: the arithmetic behind "first-pass success".

The definition is the experiment. A generous definition measures nothing, so these
tests pin it down: the number reported in docs/LAB2-BEFORE-AFTER.md must mean the
same thing every time it is recomputed.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests._loader import REPO_ROOT, load_script

SCORER = load_script("scripts/score_experiment.py")
HEADER = "arm,task_id,attempt,check_passed,human_edits,clarification_prompts,agent_turns,notes\n"


class ScorecardTestCase(unittest.TestCase):
    """Fixture: helpers that build scorecards and score them."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "scorecard.csv"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def row(
        self,
        arm: str = "cold",
        task: str = "T1",
        attempt: int = 1,
        check: int = 1,
        edits: int = 0,
        prompts: int = 0,
        turns: int = 3,
        notes: str = "",
    ) -> str:
        """One scorecard line, with everything about it explicit."""
        return f"{arm},{task},{attempt},{check},{edits},{prompts},{turns},{notes}\n"

    def write(self, rows: list[str]) -> Path:
        """Write raw rows to the fixture scorecard and return its path."""
        self.path.write_text(HEADER + "".join(rows), encoding="utf-8")
        return self.path

    def score(self, rows: list[str]) -> dict:
        """Write the rows to a scorecard and return the summary."""
        return SCORER.summarise(SCORER.read_scorecard(self.write(rows)))

    def test_first_pass_requires_a_green_check(self):
        summary = self.score([self.row(check=1), self.row(check=0)])
        self.assertEqual(summary["arms"]["cold"]["first_pass"], 1)
        self.assertEqual(summary["arms"]["cold"]["check_pass_rate"], 0.5)

    def test_first_pass_requires_no_human_edits(self):
        summary = self.score([self.row(edits=0), self.row(edits=1)])
        self.assertEqual(summary["arms"]["cold"]["first_pass"], 1)

    def test_one_clarification_is_tolerated_and_two_are_not(self):
        summary = self.score([self.row(prompts=1), self.row(prompts=2)])
        self.assertEqual(summary["arms"]["cold"]["first_pass"], 1)
        self.assertEqual(summary["arms"]["cold"]["mean_clarifications"], 1.5)

    def test_rates_and_delta_are_reported_per_arm(self):
        summary = self.score(
            [
                self.row(arm="cold", check=1, edits=1),
                self.row(arm="cold", check=0),
                self.row(arm="context", check=1),
                self.row(arm="context", check=1),
            ]
        )
        self.assertEqual(summary["arms"]["cold"]["first_pass_rate"], 0.0)
        self.assertEqual(summary["arms"]["context"]["first_pass_rate"], 1.0)
        self.assertEqual(summary["first_pass_delta"], 1.0)
        self.assertEqual(summary["attempts"], 4)

    def test_inflated_success_counts_green_runs_that_needed_help(self):
        summary = self.score([self.row(check=1, edits=2), self.row(check=1, edits=0)])
        self.assertEqual(summary["arms"]["cold"]["inflated_success"], 1)
        self.assertEqual(summary["arms"]["cold"]["mean_human_edits"], 1.0)

    def test_task_breakdown_separates_the_arms(self):
        summary = self.score(
            [
                self.row(arm="cold", task="T1", turns=9),
                self.row(arm="context", task="T1", turns=3),
                self.row(arm="cold", task="T2", check=0, turns=7),
            ]
        )
        self.assertEqual(set(summary["tasks"]), {"T1", "T2"})
        self.assertEqual(summary["tasks"]["T1"]["context"]["mean_agent_turns"], 3)
        self.assertEqual(summary["tasks"]["T1"]["cold"]["mean_agent_turns"], 9)
        self.assertNotIn("context", summary["tasks"]["T2"])

    def test_a_single_arm_still_reports_a_null_delta(self):
        summary = self.score([self.row(arm="context")])
        self.assertIsNone(summary["first_pass_delta"])
        self.assertNotIn("cold", summary["arms"])


class ScorecardReadingTests(ScorecardTestCase):
    """Malformed or unmeasured scorecards must fail loudly, not score zero."""

    def test_template_rows_are_ignored(self):
        rows = [
            self.row(notes="replace with your measurement"),
            self.row(arm="context", turns=2),
        ]
        self.assertEqual(len(SCORER.read_scorecard(self.write(rows))), 1)

    def test_an_unmeasured_scorecard_is_refused(self):
        self.write(["cold,T1,1,,,,,\n"])
        with self.assertRaises(ValueError) as caught:
            SCORER.read_scorecard(self.path)
        self.assertIn("no measured rows", str(caught.exception))

    def test_comments_and_blank_lines_are_skipped(self):
        self.path.write_text(
            "# a comment\n\n" + HEADER + self.row(arm="context"), encoding="utf-8"
        )
        self.assertEqual(len(SCORER.read_scorecard(self.path)), 1)

    def test_unknown_arm_is_refused(self):
        self.write([self.row(arm="vibes")])
        with self.assertRaises(ValueError) as caught:
            SCORER.read_scorecard(self.path)
        self.assertIn("arm must be one of", str(caught.exception))

    def test_non_integer_values_are_refused(self):
        self.write(["cold,T1,1,1,0,0,many,\n"])
        with self.assertRaises(ValueError) as caught:
            SCORER.read_scorecard(self.path)
        self.assertIn("must be an integer", str(caught.exception))

    def test_missing_file_is_refused(self):
        with self.assertRaises(ValueError):
            SCORER.read_scorecard(self.path.parent / "absent.csv")


class ScorecardCliTests(ScorecardTestCase):
    """Exit codes, output shapes, and the shipped template."""

    def test_main_reports_a_scored_run_with_its_caveat(self):
        self.write([self.row(arm="cold"), self.row(arm="context")])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(SCORER.main([str(self.path)]), 0)
        text = output.getvalue()
        self.assertIn("delta (context - cold)", text)
        self.assertIn("direction of travel", text)

    def test_main_can_emit_json(self):
        self.write([self.row(arm="context", turns=2)])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(SCORER.main([str(self.path), "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["attempts"], 1)

    def test_main_refuses_the_unfilled_shipped_scorecard(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(SCORER.main([str(REPO_ROOT / "docs" / "experiment-scorecard.csv")]), 2)

    def test_template_flag_prints_a_usable_header(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(SCORER.main(["--template"]), 0)
        text = output.getvalue()
        self.assertIn("arm,task_id,attempt,check_passed", text)
        self.assertIn("cold,TASK-A,1", text)

    def test_bad_usage_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(SCORER.main([]), 2)
            self.assertEqual(SCORER.main(["a.csv", "b.csv"]), 2)

    def test_the_shipped_scorecard_has_the_expected_header(self):
        scorecard = (REPO_ROOT / "docs" / "experiment-scorecard.csv").read_text(encoding="utf-8")
        self.assertIn(HEADER.strip(), scorecard)
        for arm in ("cold", "context"):
            self.assertIn(f"{arm},TASK-", scorecard)


if __name__ == "__main__":
    unittest.main()
