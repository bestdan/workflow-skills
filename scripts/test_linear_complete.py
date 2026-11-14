#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-complete.py.

`gql` is stubbed, so nothing reaches the network. The load-bearing properties:

- a terminal issue (completed or canceled) is never written;
- the target state is chosen by type and board position, never by name;
- a non-zero exit happens only before any write, so the caller's MCP fallback
  can never double-write;
- a comment failure after the state moved is reported, not raised.
"""

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

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

    def __init__(self, state_type, team_id=TEAM_ID, move_ok=True, comment_fails=False):
        self.state_type = state_type
        self.team_id = team_id
        self.move_ok = move_ok
        self.comment_fails = comment_fails
        self.mutations = []

    def __call__(self, key, query, variables=None):
        if "teams(" in query:
            return {
                "teams": {
                    "nodes": [
                        {"id": TEAM_ID, "name": "Core", "states": {"nodes": STATES}}
                    ]
                }
            }
        if "issueUpdate" in query:
            self.mutations.append(("move", variables))
            return {"issueUpdate": {"success": self.move_ok}}
        if "commentCreate" in query:
            self.mutations.append(("comment", variables))
            if self.comment_fails:
                sys.exit("GraphQL request failed: timed out")
            return {"commentCreate": {"success": True}}
        if "issue(" in query:
            return {
                "issue": {
                    "id": "uuid-12",
                    "identifier": "ABC-12",
                    "team": {"id": self.team_id},
                    "state": {"id": "s-x", "name": "Current", "type": self.state_type},
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

    def test_comment_failure_after_the_move_is_reported_not_raised(self):
        fake = FakeLinear("started", comment_fails=True)
        out = self.run_with(fake)
        self.assertEqual(out["result"], "completed")
        self.assertEqual(out["comment"], "failed")
        self.assertIn("timed out", out["comment_error"])


if __name__ == "__main__":
    unittest.main()
