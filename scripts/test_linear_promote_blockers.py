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


TEAM = "team-pre"


def dep_node(identifier, deps, truncated=False):
    """The fast path's reply for one completed id: its dependents as forward
    `blocks` relations, plus a `related` edge and an inverse-direction-only
    neighbour that must both be ignored."""
    nodes: list[dict] = [
        {
            "type": "related",
            "relatedIssue": {
                "identifier": "REL-1",
                "team": {"id": TEAM},
                "state": {"type": "backlog"},
            },
        }
    ]
    for ident, team, state in deps:
        issue = (
            None
            if state is DELETED
            else {
                "identifier": ident,
                "team": {"id": team},
                "state": None if state is None else {"type": state},
            }
        )
        nodes.append({"type": "blocks", "relatedIssue": issue})
    return {
        "identifier": identifier,
        "relations": {"nodes": nodes, "pageInfo": {"hasNextPage": truncated}},
    }


def floor_ids(world):
    """The MCP floor's input: a deleted dependent is left out (its read
    reported not found); one whose read failed carries no statusType."""
    out = []
    for ident, deps in world.items():
        blocks = []
        for d, team, state in deps:
            if state is DELETED:
                continue
            entry = {"id": d, "teamId": team}
            if state is not None:
                entry["statusType"] = state
            blocks.append(entry)
        out.append({"id": ident, "blocks": blocks})
    return out


class DependentsTests(Harness):
    ARGS = ["--dependents", "--team-id", TEAM]

    def dep_fast(self, world):
        def fake_gql(key, query, variables):
            self.queries.append((query, variables))
            return {
                alias.replace("i", "c", 1): dep_node(ident, world[ident])
                for alias, ident in variables.items()
            }

        mod.gql = fake_gql
        return run(self.ARGS + ["--fetch"], [{"id": i} for i in world])

    def dep_both(self, world):
        fast, floor = self.dep_fast(world), run(self.ARGS, floor_ids(world))
        self.assertEqual(fast, floor, "fast path and MCP floor disagree")
        return fast

    def test_backlog_dependent_in_team_is_a_candidate(self):
        out = self.dep_both({"PRE-9": [("PRE-12", TEAM, "backlog")]})
        self.assertEqual(out["candidates"], [{"id": "PRE-12", "via": ["PRE-9"]}])
        self.assertEqual(out["dependents_of"], ["PRE-9"])
        self.assertEqual((out["foreign"], out["unread"]), ([], []))

    def test_dependent_past_backlog_is_dropped(self):
        out = self.dep_both(
            {
                "PRE-9": [
                    ("PRE-12", TEAM, "unstarted"),
                    ("PRE-13", TEAM, "started"),
                    ("PRE-14", TEAM, "completed"),
                    ("PRE-15", TEAM, "canceled"),
                ]
            }
        )
        self.assertEqual(
            (out["candidates"], out["foreign"], out["unread"]), ([], [], [])
        )

    def test_deleted_dependent_is_dropped(self):
        out = self.dep_both({"PRE-9": [("PRE-12", TEAM, DELETED)]})
        self.assertEqual(out["candidates"], [])

    def test_other_team_dependent_is_foreign(self):
        out = self.dep_both({"PRE-9": [("OPS-3", "team-ops", "backlog")]})
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["foreign"], [{"id": "OPS-3", "via": ["PRE-9"]}])

    def test_unread_dependent_is_never_a_candidate(self):
        out = self.dep_both({"PRE-9": [("PRE-12", TEAM, None)]})
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["unread"], [{"id": "PRE-12", "via": ["PRE-9"]}])

    def test_shared_dependent_appears_once_with_every_via(self):
        out = self.dep_both(
            {
                "PRE-9": [("PRE-12", TEAM, "backlog")],
                "PRE-10": [("PRE-12", TEAM, "backlog"), ("PRE-11", TEAM, "backlog")],
            }
        )
        self.assertEqual(
            out["candidates"],
            [
                {"id": "PRE-11", "via": ["PRE-10"]},
                {"id": "PRE-12", "via": ["PRE-9", "PRE-10"]},
            ],
        )

    def test_fast_truncated_relation_page_is_reported(self):
        mod.gql = lambda key, query, variables: {
            "c0": dep_node("PRE-9", [("PRE-12", TEAM, "backlog")], truncated=True)
        }
        out = run(self.ARGS + ["--fetch"], [{"id": "PRE-9"}])
        self.assertEqual(out["truncated"], ["PRE-9"])
        self.assertEqual(out["candidates"], [{"id": "PRE-12", "via": ["PRE-9"]}])

    def test_fast_query_reads_forward_relations(self):
        self.dep_fast({"PRE-9": []})
        query = self.queries[0][0]
        self.assertIn("relations(first: 50)", query)
        self.assertIn("relatedIssue { identifier team { id } state { type } }", query)
        self.assertNotIn("inverseRelations", query)

    def test_fast_missing_issue_exits_for_floor_fallback(self):
        mod.gql = lambda key, query, variables: {"c0": None}
        with self.assertRaises(SystemExit) as cm:
            run(self.ARGS + ["--fetch"], [{"id": "PRE-9"}])
        self.assertIn("PRE-9", str(cm.exception.code))

    def test_dependents_needs_team_id(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                run(["--dependents"], [{"id": "PRE-9"}])
            with self.assertRaises(SystemExit):
                run(["--team-id", TEAM], [{"id": "PRE-9"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
