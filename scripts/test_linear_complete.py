#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-complete.py.

`gql` is stubbed, so nothing reaches the network. The load-bearing properties:

- a terminal issue (completed or canceled) is never written;
- the target state is chosen by type and board position, never by name;
- a non-zero exit means no move was confirmed; a failed move call that Linear
  applied anyway is caught by a re-read and counts as completed;
- a comment failure after the state moved is reported, not raised.
"""

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ASSET = (
    Path(__file__).resolve().parents[1]
    / "commands"
    / "handlers"
    / "assets"
    / "linear-complete.py"
)
_spec = importlib.util.spec_from_file_location("linear_complete", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
lc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lc)

TEAM_ID = "team-1"
STATES = [
    {"id": "s-todo", "name": "Todo", "type": "unstarted", "position": 1},
    {"id": "s-shipped", "name": "Shipped", "type": "completed", "position": 5},
    {"id": "s-done", "name": "Renamed Done", "type": "completed", "position": 3},
    {"id": "s-cancel", "name": "Canceled", "type": "canceled", "position": 6},
]


class FakeLinear:
    """Answers each operation by what its text asks for; records mutations."""

    def __init__(
        self,
        state_type,
        team_id=TEAM_ID,
        move_ok=True,
        move_error=None,
        move_applied=True,
        comment_error=None,
        states=STATES,
        teams_found=True,
        issue_found=True,
        reread_error=None,
        closed_elsewhere=None,
        reread_missing=False,
    ):
        # reread_missing: an issue read after a move call returns issue: null.
        # Separate from issue_found, which also blanks the first read.
        self.reread_missing = reread_missing
        self.state_type = state_type
        self.state_id = "s-x"
        # closed_elsewhere: a state id another actor moves the issue to while
        # this call's move fails, so the re-read finds it completed but not
        # in the target state.
        self.closed_elsewhere = closed_elsewhere
        self.team_id = team_id
        self.move_ok = move_ok
        # move_error: the call exits with this message; move_applied says
        # whether Linear applied it anyway, which the re-read then shows.
        self.move_error = move_error
        self.move_applied = move_applied
        self.comment_error = comment_error
        self.states = states
        self.teams_found = teams_found
        self.issue_found = issue_found
        # reread_error: an issue read after a move call exits with this.
        self.reread_error = reread_error
        self.mutations = []

    def __call__(self, key, query, variables=None):
        if "teams(" in query:
            team = {"id": TEAM_ID, "name": "Core", "states": {"nodes": self.states}}
            return {"teams": {"nodes": [team] if self.teams_found else []}}
        if "issueUpdate" in query:
            self.mutations.append(("move", variables))
            if self.move_error:
                if self.closed_elsewhere:
                    self.state_type, self.state_id = "completed", self.closed_elsewhere
                elif self.move_applied:
                    self.state_type, self.state_id = "completed", variables["state"]
                sys.exit(self.move_error)
            if self.move_ok:
                self.state_type, self.state_id = "completed", variables["state"]
            return {"issueUpdate": {"success": self.move_ok}}
        if "commentCreate" in query:
            self.mutations.append(("comment", variables))
            if self.comment_error:
                sys.exit(self.comment_error)
            return {"commentCreate": {"success": True}}
        if "issue(" in query:
            if self.reread_error and self.mutations:
                sys.exit(self.reread_error)
            if not self.issue_found or (self.reread_missing and self.mutations):
                return {"issue": None}
            return {
                "issue": {
                    "id": "uuid-12",
                    "identifier": "ABC-12",
                    "team": {"id": self.team_id},
                    "state": {
                        "id": self.state_id,
                        "name": "Current",
                        "type": self.state_type,
                    },
                }
            }
        raise AssertionError(f"unexpected query: {query}")


class CompleteTests(unittest.TestCase):
    def run_with(self, fake, comment=lc.DEFAULT_COMMENT, dry_run=False):
        original = lc.gql
        lc.gql = fake
        try:
            return lc.run("k", "Core", "ABC-12", comment, dry_run)
        finally:
            lc.gql = original

    def test_started_issue_moves_to_first_completed_state_by_position(self):
        fake = FakeLinear("started")
        out = self.run_with(fake)
        self.assertEqual(out["result"], "completed")
        self.assertEqual(out["to"], {"name": "Renamed Done", "type": "completed"})
        self.assertEqual(
            fake.mutations[0], ("move", {"id": "uuid-12", "state": "s-done"})
        )

    def test_omitted_comment_posts_the_default(self):
        fake = FakeLinear("started")
        out = self.run_with(fake)
        self.assertEqual(out["comment"], "posted")
        self.assertEqual(fake.mutations[1][1]["body"], lc.DEFAULT_COMMENT)

    def test_empty_comment_posts_none(self):
        fake = FakeLinear("started")
        out = self.run_with(fake, comment="")
        self.assertEqual(out["comment"], "skipped")
        self.assertEqual([m[0] for m in fake.mutations], ["move"])

    def test_terminal_issues_are_never_written(self):
        for state_type, result in (
            ("completed", "already-complete"),
            ("canceled", "canceled"),
        ):
            with self.subTest(state_type=state_type):
                fake = FakeLinear(state_type)
                out = self.run_with(fake)
                self.assertEqual(out["result"], result)
                self.assertIsNone(out["to"])
                self.assertEqual(fake.mutations, [])

    def test_dry_run_plans_without_writing(self):
        fake = FakeLinear("started")
        out = self.run_with(fake, dry_run=True)
        self.assertEqual(out["result"], "planned")
        self.assertEqual(out["to"]["name"], "Renamed Done")
        self.assertEqual(fake.mutations, [])

    def test_issue_on_another_team_exits_before_any_write(self):
        fake = FakeLinear("started", team_id="team-2")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_with(fake)
        self.assertEqual(fake.mutations, [])

    def test_unsuccessful_move_exits_nonzero(self):
        fake = FakeLinear("started", move_ok=False)
        with self.assertRaises(SystemExit) as cm:
            self.run_with(fake)
        self.assertIn("success=false", str(cm.exception.code))
        self.assertEqual([m[0] for m in fake.mutations], ["move"])

    def test_exits_before_any_write_when_nothing_resolves(self):
        no_done = [s for s in STATES if s["type"] != "completed"]
        cases = {
            "no completed state": FakeLinear("started", states=no_done),
            "team not found": FakeLinear("started", teams_found=False),
            "issue not found": FakeLinear("started", issue_found=False),
        }
        for name, fake in cases.items():
            with self.subTest(name):
                with self.assertRaises(SystemExit):
                    self.run_with(fake)
                self.assertEqual(fake.mutations, [])

    def test_failed_move_call_that_applied_counts_as_completed(self):
        fake = FakeLinear("started", move_error="GraphQL request failed: timed out")
        out = self.run_with(fake)
        self.assertEqual(out["result"], "completed")
        self.assertEqual(out["comment"], "posted")

    def test_failed_move_then_closed_elsewhere_is_not_reported_as_ours(self):
        fake = FakeLinear(
            "started",
            move_error="GraphQL request failed: timed out",
            move_applied=False,
            closed_elsewhere="s-shipped",
        )
        with self.assertRaises(SystemExit) as cm:
            self.run_with(fake)
        self.assertIn("is not in Renamed Done", str(cm.exception.code))
        self.assertEqual([m[0] for m in fake.mutations], ["move"])

    def test_failed_re_read_exits_naming_both_failures(self):
        fake = FakeLinear(
            "started",
            move_error="GraphQL request failed: timed out",
            reread_error="GraphQL request failed: connection reset",
        )
        with self.assertRaises(SystemExit) as cm:
            self.run_with(fake)
        msg = str(cm.exception.code)
        self.assertIn("timed out", msg)
        self.assertIn("connection reset", msg)
        self.assertEqual([m[0] for m in fake.mutations], ["move"])

    def test_failed_move_then_missing_issue_on_re_read_exits_nonzero(self):
        fake = FakeLinear(
            "started",
            move_error="GraphQL request failed: timed out",
            reread_missing=True,
        )
        with self.assertRaises(SystemExit) as cm:
            self.run_with(fake)
        msg = str(cm.exception.code)
        self.assertIn("is not in Renamed Done", msg)
        self.assertIn("now unknown", msg)
        self.assertEqual([m[0] for m in fake.mutations], ["move"])

    def test_failed_move_call_that_did_not_apply_exits_nonzero(self):
        fake = FakeLinear(
            "started",
            move_error="GraphQL request failed: timed out",
            move_applied=False,
        )
        with self.assertRaises(SystemExit) as cm:
            self.run_with(fake)
        self.assertIn("is not in Renamed Done", str(cm.exception.code))
        self.assertEqual([m[0] for m in fake.mutations], ["move"])

    def test_comment_failure_is_reported_not_raised(self):
        cases = {
            "GraphQL request failed: timed out": "unknown",
            "GraphQL HTTP 503: unavailable": "unknown",
            "GraphQL HTTP 400: bad input": "failed",
            'GraphQL error: [{"message": "no"}]': "failed",
        }
        for error, outcome in cases.items():
            with self.subTest(error):
                fake = FakeLinear("started", comment_error=error)
                out = self.run_with(fake)
                self.assertEqual(out["result"], "completed")
                self.assertEqual(out["comment"], outcome)
                self.assertEqual(out["comment_error"], error)


class GqlTransportTests(unittest.TestCase):
    """A body-read failure must become a SystemExit, never a traceback."""

    def test_read_errors_exit_with_a_message(self):
        for exc in (TimeoutError("timed out"), ValueError("not json")):
            with self.subTest(type(exc).__name__):
                response = mock.MagicMock()
                response.__enter__.return_value.read.side_effect = exc
                with mock.patch.object(
                    lc.urllib.request, "urlopen", return_value=response
                ):
                    with self.assertRaises(SystemExit) as cm:
                        lc.gql("k", "{ x }")
                self.assertIn("GraphQL request failed", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
