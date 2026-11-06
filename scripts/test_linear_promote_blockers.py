#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-promote-blockers.py —
the native-blocker hold `/promote-tasks` (linear) applies to Backlog
candidates (#1060).

No network: the fast path's gql()/get_key() seams are stubbed with canned
GraphQL replies. Each scenario is written once as a world of issues and run
through both the fast path and the MCP floor, so the two paths are checked
against each other as well as against the expected answer.
"""

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path

ASSET = (
    Path(__file__).resolve().parents[1]
    / "commands"
    / "handlers"
    / "assets"
    / "linear-promote-blockers.py"
)

_spec = importlib.util.spec_from_file_location("linear_promote_blockers", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

# A blocker's state, or DELETED for a blocker that no longer exists.
DELETED = object()


def gql_node(identifier, blockers, truncated=False):
    """The fast path's reply for one candidate: blockers as inverse `blocks`
    relations, plus one unrelated `related` edge that must be ignored."""
    nodes: list[dict] = [
        {
            "type": "related",
            "issue": {"identifier": "REL-1", "state": {"type": "started"}},
        }
    ]
    for ident, state in blockers:
        issue = (
            None
            if state is DELETED
            else {"identifier": ident, "state": {"type": state}}
        )
        nodes.append({"type": "blocks", "issue": issue})
    return {
        "identifier": identifier,
        "inverseRelations": {"nodes": nodes, "pageInfo": {"hasNextPage": truncated}},
    }


def floor_candidate(identifier, blockers):
    """The MCP floor's input: a deleted blocker is left out (its get_issue read
    reported not found), every other one carries its statusType."""
    return {
        "id": identifier,
        "blockedBy": [
            {"id": i, "statusType": s} for i, s in blockers if s is not DELETED
        ],
    }


def run(argv, stdin):
    old_argv, old_stdin = sys.argv, sys.stdin
    sys.argv = ["linear-promote-blockers.py"] + argv
    sys.stdin = io.StringIO(json.dumps(stdin))
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main()
        return json.loads(buf.getvalue())
    finally:
        sys.argv, sys.stdin = old_argv, old_stdin


class Harness(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, mod, "gql", mod.gql)
        self.addCleanup(setattr, mod, "get_key", mod.get_key)
        mod.get_key = lambda: "fake-key"
        self.queries = []

    def fast(self, world):
        def fake_gql(key, query, variables):
            self.queries.append(variables)
            return {
                alias.replace("i", "c", 1): gql_node(ident, world[ident])
                for alias, ident in variables.items()
            }

        mod.gql = fake_gql
        return run(["--fetch"], [{"id": i} for i in world])

    def floor(self, world):
        return run([], [floor_candidate(i, b) for i, b in world.items()])

    def both(self, world):
        fast, floor = self.fast(world), self.floor(world)
        self.assertEqual(fast, floor, "fast path and MCP floor disagree")
        return fast


class BlockerRuleTests(Harness):
    def test_open_blocker_holds_and_names_it(self):
        out = self.both({"PRE-1": [("PRE-9", "started")]})
        self.assertEqual(out, {"held": {"PRE-1": "waiting on PRE-9"}, "clear": []})

    def test_blocker_in_another_project_or_team_still_holds(self):
        # Nothing scopes the relation read: a blocker outside the scored
        # project (here, another team's key) is read and gated like any other.
        out = self.both({"PRE-1": [("OPS-4", "unstarted")]})
        self.assertEqual(out["held"], {"PRE-1": "waiting on OPS-4"})

    def test_canceled_blocker_holds(self):
        out = self.both({"PRE-1": [("PRE-9", "canceled")]})
        self.assertEqual(out["held"], {"PRE-1": "waiting on PRE-9"})

    def test_completed_blocker_clears(self):
        out = self.both({"PRE-1": [("PRE-9", "completed")]})
        self.assertEqual(out, {"held": {}, "clear": ["PRE-1"]})

    def test_deleted_blocker_clears(self):
        out = self.both({"PRE-1": [("PRE-9", DELETED)]})
        self.assertEqual(out, {"held": {}, "clear": ["PRE-1"]})

    def test_no_blockers_clears(self):
        self.assertEqual(self.both({"PRE-1": []}), {"held": {}, "clear": ["PRE-1"]})

    def test_only_open_blockers_are_named(self):
        out = self.both(
            {
                "PRE-1": [
                    ("PRE-7", "completed"),
                    ("PRE-8", "backlog"),
                    ("PRE-9", "canceled"),
                ]
            }
        )
        self.assertEqual(out["held"], {"PRE-1": "waiting on PRE-8, PRE-9"})

    def test_mixed_set(self):
        out = self.both(
            {
                "PRE-1": [("PRE-9", "started")],
                "PRE-2": [("PRE-9", "completed")],
                "PRE-3": [],
            }
        )
        self.assertEqual(
            out, {"held": {"PRE-1": "waiting on PRE-9"}, "clear": ["PRE-2", "PRE-3"]}
        )


class FailClosedTests(Harness):
    def test_floor_blocker_with_unread_state_holds(self):
        out = run([], [{"id": "PRE-1", "blockedBy": [{"id": "PRE-9"}]}])
        self.assertEqual(out["held"], {"PRE-1": "waiting on PRE-9"})

    def test_fast_truncated_relation_page_holds(self):
        mod.gql = lambda key, query, variables: {
            "c0": gql_node("PRE-1", [], truncated=True)
        }
        out = run(["--fetch"], [{"id": "PRE-1"}])
        self.assertEqual(out["held"], {"PRE-1": "waiting on more than 50 blockers"})

    def test_fast_missing_issue_exits_for_floor_fallback(self):
        mod.gql = lambda key, query, variables: {"c0": None}
        with self.assertRaises(SystemExit) as cm:
            run(["--fetch"], [{"id": "PRE-1"}])
        self.assertIn("PRE-1", str(cm.exception.code))


class QueryShapeTests(Harness):
    def test_chunks_at_fifty(self):
        world: dict[str, list] = {f"PRE-{n}": [] for n in range(1, 121)}
        out = self.fast(world)
        self.assertEqual([len(q) for q in self.queries], [50, 50, 20])
        self.assertEqual(len(out["clear"]), 120)

    def test_query_reads_blocker_state_by_identifier(self):
        q = mod.chunk_query(2)
        self.assertIn("$i0: String!, $i1: String!", q)
        self.assertIn("c1: issue(id: $i1)", q)
        self.assertIn("inverseRelations", q)
        self.assertIn("state { type }", q)


if __name__ == "__main__":
    unittest.main(verbosity=2)
