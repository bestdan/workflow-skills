"""Hermetic tests for jira-resolve-transition.py.

Loaded via importlib since the asset's filename is hyphenated. No network,
no subprocess — the asset only reads stdin and decides.
"""

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "jira-resolve-transition.py"

_spec = importlib.util.spec_from_file_location("jira_resolve_transition", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
resolve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolve)


def _transition(tid, to_name, category, own_name=None):
    entry = {
        "id": tid,
        "to": {"name": to_name, "statusCategory": {"key": category}},
    }
    if own_name is not None:
        entry["name"] = own_name
    return entry


class ResolveTestCase(unittest.TestCase):
    def call(self, payload, argv):
        import sys

        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                code = resolve.main(argv)
            return code, out.getvalue()
        finally:
            sys.stdin = old_stdin


class TestCategoryRealWorkflowShape(ResolveTestCase):
    """jira-claim.md's real board: In Execution / Validation / On Hold, no
    transition literally named 'In Progress'."""

    def test_resolves_the_single_non_hold_non_validation_transition(self):
        payload = {
            "transitions": [
                _transition("11", "In Execution", "indeterminate"),
                _transition("12", "Validation", "indeterminate"),
                _transition("13", "On Hold/Blocked", "indeterminate"),
                _transition("14", "Done", "done"),
            ]
        }
        code, out = self.call(
            payload,
            [
                "--category",
                "indeterminate",
                "--exclude",
                "hold|block|review|validation|wait",
            ],
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "11\tIn Execution\n")


class TestCategoryCanceledOnlyTerminalBoard(ResolveTestCase):
    """jira-complete.md's stated defect: a board whose only Done-category
    transition is Canceled must not be completed with it. This fails if the
    exclude filter runs after counting instead of before."""

    def test_none_when_the_only_done_transition_is_cancellation_style(self):
        payload = {
            "transitions": [
                _transition("21", "Canceled", "done"),
                _transition("22", "In Progress", "indeterminate"),
            ]
        }
        code, out = self.call(
            payload,
            ["--category", "done", "--exclude", "won.?t do|cancel|reject|obsolete"],
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "NONE\nCanceled\n")


class TestExactMatchFallback(ResolveTestCase):
    """promote/create/archive mode: to.name first, then the transition's own
    name when no to.name matches."""

    def test_falls_back_to_transitions_own_name(self):
        payload = {
            "transitions": [
                _transition("31", "Resolved", "done", own_name="Resolve"),
                _transition("32", "In Progress", "indeterminate", own_name="Start"),
            ]
        }
        code, out = self.call(payload, ["--exact", "Resolve"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "31\tResolved\n")

    def test_matches_to_name_directly_without_needing_the_fallback(self):
        payload = {
            "transitions": [
                _transition("41", "Selected for Development", "new", own_name="Ready"),
            ]
        }
        code, out = self.call(payload, ["--exact", "selected for development"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "41\tSelected for Development\n")

    def test_none_when_neither_to_name_nor_own_name_matches(self):
        payload = {
            "transitions": [
                _transition("51", "Backlog", "new", own_name="Back to backlog"),
            ]
        }
        code, out = self.call(payload, ["--exact", "Selected for Development"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "NONE\n")

    def test_ambiguous_when_two_transitions_share_the_target_name(self):
        payload = {
            "transitions": [
                _transition("61", "Done", "done"),
                _transition("62", "Done", "done"),
            ]
        }
        code, out = self.call(payload, ["--exact", "Done"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "AMBIGUOUS\nDone\nDone\n")


class TestAmbiguous(ResolveTestCase):
    """Several candidates remain and --prefer either wasn't given or doesn't
    narrow to exactly one."""

    def test_ambiguous_with_no_prefer_given(self):
        payload = {
            "transitions": [
                _transition("61", "Complete", "done"),
                _transition("62", "Resolved", "done"),
            ]
        }
        code, out = self.call(payload, ["--category", "done"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "AMBIGUOUS\nComplete\nResolved\n")

    def test_prefer_narrows_to_exactly_one(self):
        payload = {
            "transitions": [
                _transition("71", "In Execution", "indeterminate"),
                _transition("72", "In Progress", "indeterminate"),
            ]
        }
        code, out = self.call(
            payload, ["--category", "indeterminate", "--prefer", "in progress"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "72\tIn Progress\n")

    def test_prefer_still_ambiguous_reports_the_narrowed_set(self):
        payload = {
            "transitions": [
                _transition("81", "In Progress (dev)", "indeterminate"),
                _transition("82", "In Progress (qa)", "indeterminate"),
                _transition("83", "Blocked", "indeterminate"),
            ]
        }
        code, out = self.call(
            payload, ["--category", "indeterminate", "--prefer", "in progress"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "AMBIGUOUS\nIn Progress (dev)\nIn Progress (qa)\n")

    def test_prefer_narrows_to_zero_reports_the_pre_prefer_set(self):
        payload = {
            "transitions": [
                _transition("91", "On Hold", "indeterminate"),
                _transition("92", "Blocked", "indeterminate"),
            ]
        }
        code, out = self.call(
            payload, ["--category", "indeterminate", "--prefer", "in progress"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "NONE\nOn Hold\nBlocked\n")


class TestArgValidation(unittest.TestCase):
    def test_requires_exactly_one_of_category_or_exact(self):
        with self.assertRaises(SystemExit):
            resolve.main([])
        with self.assertRaises(SystemExit):
            resolve.main(["--category", "done", "--exact", "Done"])

    def test_exclude_and_prefer_require_category(self):
        with self.assertRaises(SystemExit):
            resolve.main(["--exact", "Done", "--exclude", "cancel"])


class TestMalformedInput(unittest.TestCase):
    def test_invalid_json_exits_nonzero(self):
        import sys

        old_stdin = sys.stdin
        sys.stdin = io.StringIO("not json")
        try:
            code = resolve.main(["--category", "done"])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, 1)

    def test_missing_transitions_key_exits_nonzero(self):
        import sys

        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps({}))
        try:
            code = resolve.main(["--category", "done"])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
