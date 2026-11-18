#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/promote-reason.py.

Runs against the real Confidence check table in skills/task/SKILL.md, so a
reshaped table that the parser no longer reads fails here rather than at a
promoter's run time.
"""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "promote-reason.py"
SKILL = ROOT / "skills" / "task" / "SKILL.md"

_spec = importlib.util.spec_from_file_location("promote_reason", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
reason = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reason)

AC = "description missing acceptance criteria"
SPLIT = "scope exceeds size 5 — split into sub-tasks"


class PickTest(unittest.TestCase):
    def test_estimate_over_ceiling_takes_the_slot(self):
        # The #1571 dry run: estimated 13, recorded the AC reason.
        self.assertEqual(reason.pick(["has_acceptance_criteria"], 13), SPLIT)

    def test_estimate_over_ceiling_with_nothing_failed(self):
        self.assertEqual(reason.pick([], 13), SPLIT)

    def test_estimate_at_ceiling_keeps_first_failure(self):
        self.assertEqual(reason.pick(["has_acceptance_criteria"], 5), AC)

    def test_table_order_decides_not_argument_order(self):
        got = reason.pick(
            ["scope_fits", "no_open_questions_or_tbd", "has_acceptance_criteria"], None
        )
        self.assertEqual(got, AC)

    def test_size_placeholder_is_filled(self):
        self.assertEqual(
            reason.pick(["size_valid"], 4), "size '4' must be one of 1/2/3/5"
        )

    def test_nothing_failed_is_an_error(self):
        with self.assertRaises(reason.ReasonError):
            reason.pick([], 3)


class MainTest(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = reason.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_unknown_check_fails_loudly(self):
        code, out, err = self.run_main(["has_acceptance_criteria", "has_ac"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("has_ac", err)

    def test_unknown_check_fails_despite_estimate_over_ceiling(self):
        code, out, err = self.run_main(["--estimate", "8", "has_ac"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("has_ac", err)

    def test_table_without_rows_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text("# Task\n\nNo table here.\n", encoding="utf-8")
            with self.assertRaises(reason.ReasonError):
                reason.load_table(path)

    def test_table_with_an_unreadable_row_is_an_error(self):
        text = SKILL.read_text(encoding="utf-8").replace(
            "| `required_fields_present` ", "| required_fields_present ", 1
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(reason.ReasonError):
                reason.load_table(path)


if __name__ == "__main__":
    unittest.main()
