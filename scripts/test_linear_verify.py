#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-verify.py.

Stubs the module's run_gh() seam against a fixture board, so nothing reaches
GitHub and every test can state exactly what the board says versus what the
export and plan say.

The properties under test are the ones whose failure would make the verifier
WORSE than no verifier — a check that passes on a broken board is a licence to
stop looking:

- edge comparison is by SET, so one missing edge and one extra edge in the same
  fixture are both named rather than netting to zero;
- a second transcript on an issue fails, not just a missing one;
- the cardinality rule is asked of labels.yml, so a label set that matches the
  plan exactly still fails when it breaks the rule;
- the three deliberate differences the import left behind (a rewritten body, a
  reopened issue's inherited label, an issue closed since the import) are NOTES
  and do not fail.
"""

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "linear-verify.py"

_spec = importlib.util.spec_from_file_location("linear_verify", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
linear_verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(linear_verify)

REPO = "owner/name"
SLUG = "acme"


def footer(key, related=None):
    lines = [
        "---",
        f"Migrated from Linear {key} (https://linear.app/{SLUG}/issue/{key}) "
        "on 2026-09-13.",
    ]
    if related:
        lines.append(
            f"Related: {', '.join(related)} (relations of type "
            "related/similar/duplicate are not native on GitHub)."
        )
    return "\n".join(lines)


def plan_body(key, mentions=()):
    """A plan body, in the pre-`--link` spelling: Linear markdown links."""
    text = "Context.\n"
    for other in mentions:
        text += f"See [{other}](https://linear.app/{SLUG}/issue/{other}/x).\n"
    return f"{text.rstrip()}\n\n{footer(key)}\n"


def live_body(key, mentions=()):
    """The same body after `--link` rewrote it to `#number`."""
    numbers = {"PRE-1": 101, "PRE-2": 102, "PRE-3": 103}
    text = "Context.\n"
    for other in mentions:
        text += f"See #{numbers[other]}.\n"
    return f"{text.rstrip()}\n\n{footer(key)}\n"


def export_issue(key, **overrides):
    node = {
        "identifier": key,
        "relations": [],
        "inverseRelations": [],
        "parent": None,
        "children": [],
        "comments": [],
    }
    node.update(overrides)
    return node


def base_export():
    """PRE-2 is blocked by PRE-1; PRE-3 is a child of PRE-1 and has a comment."""
    return {
        "issues": [
            export_issue(
                "PRE-1",
                relations=[{"type": "blocks", "identifier": "PRE-2"}],
                children=["PRE-3"],
            ),
            export_issue(
                "PRE-2", inverseRelations=[{"type": "blocks", "identifier": "PRE-1"}]
            ),
            export_issue(
                "PRE-3",
                parent="PRE-1",
                comments=[{"author": "Dan", "at": "2026-01-01", "body": "hi"}],
            ),
        ]
    }


def plan_entry(key, **overrides):
    entry = {
        "key": key,
        "action": "create",
        "title": f"Title {key}",
        "body": plan_body(key),
        "managed_labels": ["status:2_ready", "auto:eligible", "prio:2", "est:3"],
        "carried_labels": [],
        "milestone": None,
        "assignee": None,
        "blocked_by": [],
        "parent": None,
        "comments": [],
    }
    entry.update(overrides)
    return entry


def base_plan():
    return {
        "entries": [
            plan_entry("PRE-1", body=plan_body("PRE-1", ["PRE-2"])),
            plan_entry("PRE-2", blocked_by=["PRE-1"]),
            plan_entry("PRE-3", parent="PRE-1", comments=[{"body": "hi"}]),
        ]
    }


def base_mapping():
    return {
        "repo": REPO,
        "entries": {
            "PRE-1": {"key": "PRE-1", "number": 101, "phase": "done"},
            "PRE-2": {"key": "PRE-2", "number": 102, "phase": "done"},
            "PRE-3": {"key": "PRE-3", "number": 103, "phase": "done"},
        },
    }


def live_issue(number, key, **overrides):
    issue = {
        "number": number,
        "title": f"Title {key}",
        "body": live_body(key),
        "labels": [
            {"name": name}
            for name in ("status:2_ready", "auto:eligible", "prio:2", "est:3")
        ],
        "milestone": None,
        "assignees": [],
        "state": "OPEN",
        "stateReason": None,
        "comments": [],
    }
    issue.update(overrides)
    return issue


def marker(key):
    return linear_verify.load_import_module().COMMENT_MARKER.format(key=key)


def base_board():
    """The board as the import left it: everything agrees with the fixtures."""
    return {
        "issues": {
            101: live_issue(101, "PRE-1", body=live_body("PRE-1", ["PRE-2"])),
            102: live_issue(102, "PRE-2"),
            103: live_issue(
                103,
                "PRE-3",
                comments=[{"body": marker("PRE-3") + "\nComments migrated."}],
            ),
        },
        # (blocked -> {blockers}) and (parent -> {children})
        "blocked_by": {101: set(), 102: {101}, 103: set()},
        "sub_issues": {101: {103}, 102: set(), 103: set()},
        "login": "bestdan",
    }


def stub(board):
    """A run_gh that answers the four GETs linear-verify makes, and nothing else.

    An unrecognised call raises rather than returning an empty page: a stub that
    shrugs would let a test pass because the verifier never asked.
    """

    def run_gh(args):
        if args[:2] == ["api", "user"]:
            return 0, json.dumps({"login": board["login"]}), ""
        if args[0] == "issue" and args[1] == "view":
            number = int(args[2])
            return 0, json.dumps(board["issues"][number]), ""
        if args[0] == "api" and args[-1].endswith("/dependencies/blocked_by"):
            number = int(args[-1].split("/issues/")[1].split("/")[0])
            numbers = sorted(board["blocked_by"][number])
            return 0, json.dumps([[{"number": n} for n in numbers]]), ""
        if args[0] == "api" and args[-1].endswith("/sub_issues"):
            number = int(args[-1].split("/issues/")[1].split("/")[0])
            numbers = sorted(board["sub_issues"][number])
            return 0, json.dumps([[{"number": n} for n in numbers]]), ""
        raise AssertionError(f"unexpected gh call: {args}")

    return run_gh


class VerifyCase(unittest.TestCase):
    def setUp(self):
        self.export = base_export()
        self.plan = base_plan()
        self.mapping = base_mapping()
        self.board = base_board()
        self._real_run_gh = linear_verify.run_gh

    def tearDown(self):
        linear_verify.run_gh = self._real_run_gh

    def run_checks(self):
        linear_verify.run_gh = stub(self.board)
        groups, colors = linear_verify.load_vocabulary()
        vocabulary = linear_verify.expected_labels(groups, colors)
        return {
            result["name"]: result
            for result in linear_verify.verify(
                self.export,
                self.plan,
                self.mapping,
                REPO,
                linear_verify.load_import_module(),
                groups,
                vocabulary,
            )
        }

    def assert_all_pass(self, checks):
        failed = {
            name: result["failures"]
            for name, result in checks.items()
            if not result["ok"]
        }
        self.assertEqual(failed, {})


class TestCleanBoard(VerifyCase):
    def test_a_faithful_board_passes_every_check(self):
        self.assert_all_pass(self.run_checks())

    def test_the_plan_body_is_compared_after_the_link_rewrite(self):
        # PRE-1's plan body names PRE-2 as a Linear markdown link; the live body
        # says `#102`. Comparing the two verbatim would report a mismatch on
        # every one of the 33 bodies --link rewrote.
        self.assertIn("linear.app", self.plan["entries"][0]["body"])
        self.assertIn("#102", self.board["issues"][101]["body"])
        self.assert_all_pass(self.run_checks())


class TestEdges(VerifyCase):
    def test_one_missing_and_one_extra_edge_are_both_named(self):
        # The pair that nets to zero on a count: drop the real edge 102<-101 and
        # invent 103<-101 in its place. Two edges expected, two edges present.
        self.board["blocked_by"][102] = set()
        self.board["blocked_by"][103] = {101}
        checks = self.run_checks()
        failures = checks["edges"]["failures"]
        self.assertEqual(
            sorted((f["blocked"], f["blocker"], f["problem"]) for f in failures),
            [
                (102, 101, "missing on the board"),
                (103, 101, "not in the export"),
            ],
        )

    def test_an_edge_the_export_does_not_contain_fails(self):
        self.board["blocked_by"][101] = {103}
        checks = self.run_checks()
        self.assertFalse(checks["edges"]["ok"])
        self.assertEqual(
            checks["edges"]["failures"],
            [{"blocked": 101, "blocker": 103, "problem": "not in the export"}],
        )

    def test_the_export_is_read_from_both_relation_directions(self):
        # PRE-2's inverseRelations is the half --plan reads. Removing it leaves
        # PRE-1's forward `blocks`, which must still produce the same edge — a
        # verifier reading only --plan's half could not disagree with --plan.
        self.export["issues"][1]["inverseRelations"] = []
        self.assert_all_pass(self.run_checks())


class TestSubIssues(VerifyCase):
    def test_a_missing_sub_issue_link_is_named(self):
        self.board["sub_issues"][101] = set()
        checks = self.run_checks()
        self.assertEqual(
            checks["sub_issues"]["failures"],
            [{"parent": 101, "child": 103, "problem": "missing on the board"}],
        )

    def test_a_sub_issue_link_the_export_does_not_contain_is_named(self):
        self.board["sub_issues"][101] = {102, 103}
        checks = self.run_checks()
        self.assertEqual(
            checks["sub_issues"]["failures"],
            [{"parent": 101, "child": 102, "problem": "not in the export"}],
        )

    def test_the_parent_field_alone_is_enough(self):
        # `children` is populated for only some parents in the real export, so
        # the pair has to survive reading `parent` alone.
        self.export["issues"][0]["children"] = []
        self.assert_all_pass(self.run_checks())


class TestComments(VerifyCase):
    def test_a_duplicated_comment_marker_fails(self):
        self.board["issues"][103]["comments"].append(
            {"body": marker("PRE-3") + "\nposted twice"}
        )
        checks = self.run_checks()
        self.assertEqual(
            checks["comments"]["failures"],
            [{"key": "PRE-3", "number": 103, "want": 1, "got": 2}],
        )

    def test_a_missing_transcript_fails(self):
        self.board["issues"][103]["comments"] = []
        checks = self.run_checks()
        self.assertEqual(
            checks["comments"]["failures"],
            [{"key": "PRE-3", "number": 103, "want": 1, "got": 0}],
        )

    def test_a_transcript_on_an_issue_with_no_export_comments_fails(self):
        self.board["issues"][102]["comments"] = [{"body": marker("PRE-2")}]
        checks = self.run_checks()
        self.assertEqual(
            checks["comments"]["failures"],
            [{"key": "PRE-2", "number": 102, "want": 0, "got": 1}],
        )


class TestLabels(VerifyCase):
    def test_a_label_set_matching_the_plan_can_still_break_the_rule(self):
        # Two status rungs on the issue AND on the plan entry. The plan
        # comparison agrees — it is the same set — and only the independent
        # cardinality check catches it.
        both = ["status:2_ready", "status:3_started", "auto:eligible"]
        self.plan["entries"][1]["managed_labels"] = both
        self.board["issues"][102]["labels"] = [{"name": name} for name in both]
        checks = self.run_checks()
        self.assert_all_pass({"labels": checks["labels"]})
        self.assertEqual(
            checks["vocabulary"]["failures"],
            [
                {
                    "key": "PRE-2",
                    "number": 102,
                    "state": "OPEN",
                    "wrong": {"status": ["status:2_ready", "status:3_started"]},
                }
            ],
        )

    def test_a_hand_typed_rung_is_not_a_rung(self):
        # `status:blocked` is not in labels.yml. A prefix test would count it and
        # report the issue healthy; membership in the vocabulary is the question.
        self.board["issues"][102]["labels"] = [
            {"name": "status:blocked"},
            {"name": "auto:eligible"},
            {"name": "prio:2"},
            {"name": "est:3"},
        ]
        checks = self.run_checks()
        self.assertEqual(checks["vocabulary"]["failures"][0]["wrong"], {"status": []})

    def test_a_managed_label_the_plan_does_not_carry_fails(self):
        self.board["issues"][102]["labels"].append({"name": "prio:0"})
        checks = self.run_checks()
        wrong = checks["labels"]["failures"][0]["wrong"]
        self.assertEqual(wrong["managed"], {"missing": [], "unexpected": ["prio:0"]})

    def test_a_missing_carried_label_fails(self):
        self.plan["entries"][1]["carried_labels"] = ["papercut"]
        checks = self.run_checks()
        self.assertEqual(
            checks["labels"]["failures"][0]["wrong"]["carried_missing"], ["papercut"]
        )

    def test_a_reopened_issue_keeps_its_originals_unmanaged_label(self):
        # `papercut` on a reopened issue is absent from the plan's
        # carried_labels by design: gh-issue-state.py carries forward everything
        # outside the four managed namespaces.
        self.plan["entries"][1]["action"] = "reopen"
        self.board["issues"][102]["labels"].append({"name": "papercut"})
        checks = self.run_checks()
        self.assert_all_pass({"labels": checks["labels"]})
        self.assertEqual(
            checks["labels"]["notes"],
            [{"key": "PRE-2", "number": 102, "labels": ["papercut"]}],
        )

    def test_the_same_label_on_a_created_issue_fails(self):
        self.board["issues"][102]["labels"].append({"name": "papercut"})
        checks = self.run_checks()
        self.assertEqual(
            checks["labels"]["failures"][0]["wrong"]["carried_unexpected"],
            ["papercut"],
        )


class TestClosedIssues(VerifyCase):
    def retire(self, number):
        issue = self.board["issues"][number]
        issue["state"] = "CLOSED"
        issue["stateReason"] = "COMPLETED"
        issue["labels"] = [{"name": "prio:2"}, {"name": "est:3"}]

    def test_an_issue_completed_since_the_import_is_a_note(self):
        self.retire(102)
        checks = self.run_checks()
        self.assert_all_pass(checks)
        self.assertEqual(
            checks["mapping"]["notes"],
            [{"key": "PRE-2", "number": 102, "state_reason": "COMPLETED"}],
        )

    def test_a_closed_issue_still_carrying_a_rung_fails(self):
        self.board["issues"][102]["state"] = "CLOSED"
        checks = self.run_checks()
        self.assertFalse(checks["mapping"]["ok"])
        self.assertEqual(
            checks["mapping"]["failures"][0]["problem"],
            "closed while still carrying a status:/auto: rung",
        )
        # And the cardinality check says the same thing from its own end.
        self.assertFalse(checks["vocabulary"]["ok"])


class TestMapping(VerifyCase):
    def test_a_key_with_no_mapping_record_fails(self):
        del self.mapping["entries"]["PRE-3"]
        del self.board["issues"][103]
        del self.board["blocked_by"][103]
        del self.board["sub_issues"][103]
        self.board["sub_issues"][101] = set()
        checks = self.run_checks()
        self.assertIn(
            {"key": "PRE-3", "problem": "no mapping record"},
            checks["mapping"]["failures"],
        )

    def test_a_record_short_of_done_fails_without_skipping_the_issue(self):
        self.mapping["entries"]["PRE-2"]["phase"] = "labelled"
        checks = self.run_checks()
        self.assertEqual(
            checks["mapping"]["failures"],
            [{"key": "PRE-2", "number": 102, "problem": "phase is 'labelled'"}],
        )
        # The issue is still read: what the board says about a half-applied key
        # is exactly what the reader wants.
        self.assert_all_pass({"fields": checks["fields"]})


class TestFields(VerifyCase):
    def test_a_retitled_issue_fails(self):
        self.board["issues"][102]["title"] = "Renamed"
        checks = self.run_checks()
        self.assertEqual(
            checks["fields"]["failures"][0]["wrong"]["title"],
            {"want": "Title PRE-2", "got": "Renamed"},
        )

    def test_a_wrong_milestone_fails(self):
        self.board["issues"][102]["milestone"] = {"title": "reconcile-tasks"}
        checks = self.run_checks()
        self.assertEqual(
            checks["fields"]["failures"][0]["wrong"]["milestone"],
            {"want": None, "got": "reconcile-tasks"},
        )

    def test_at_me_is_resolved_to_the_authenticated_login(self):
        self.plan["entries"][1]["assignee"] = "@me"
        self.board["issues"][102]["assignees"] = [{"login": "bestdan"}]
        self.assert_all_pass(self.run_checks())

    def test_an_unexpected_assignee_fails(self):
        self.board["issues"][102]["assignees"] = [{"login": "someone"}]
        checks = self.run_checks()
        self.assertEqual(
            checks["fields"]["failures"][0]["wrong"]["assignee"],
            {"want": None, "got": ["someone"]},
        )

    def test_an_edited_body_fails(self):
        self.board["issues"][102]["body"] = "Context.\n"
        checks = self.run_checks()
        self.assertIn("body", checks["fields"]["failures"][0]["wrong"])


class TestExitCodes(VerifyCase):
    def test_a_clean_board_exits_zero_and_a_broken_one_exits_one(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for name, payload in (
                ("export", self.export),
                ("plan", self.plan),
                ("mapping", self.mapping),
            ):
                path = f"{tmp}/{name}.json"
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                paths[name] = path
            argv = [
                "--export",
                paths["export"],
                "--plan-file",
                paths["plan"],
                "--mapping",
                paths["mapping"],
                "--repo",
                REPO,
            ]
            linear_verify.run_gh = stub(self.board)
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(linear_verify.main(argv), 0)
            self.assertIn("all 7 checks passed", out.getvalue())

            self.board["blocked_by"][102] = set()
            linear_verify.run_gh = stub(self.board)
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(linear_verify.main(argv), 1)
            self.assertIn("FAIL  edges", out.getvalue())

    def test_an_unreadable_input_exits_two(self):
        argv = [
            "--export",
            "/nonexistent/export.json",
            "--plan-file",
            "/nonexistent/plan.json",
            "--mapping",
            "/nonexistent/mapping.json",
            "--repo",
            REPO,
        ]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(linear_verify.main(argv), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
