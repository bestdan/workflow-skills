#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-ready.py.

Stubs the module's fetch_issues()/resolve_team()/get_key() seams so nothing
touches the network. Covers the Unassigned-bucket exclusion pass added for
PRE-501: with 1+ `--project` scopes, a configured-project candidate keeps its
real project tag, a null-project or unconfigured-project candidate is tagged
`__unassigned__`, there is no duplication between the per-project and
whole-team queries, and the 0-projects-configured (whole-team-only) path is
unaffected (no extra query, no `__unassigned__` candidates).
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
    / "linear-ready.py"
)

_spec = importlib.util.spec_from_file_location("linear_ready", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
linear_ready = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(linear_ready)

CONFIGURED_A = "aaaaaaaa-0000-0000-0000-000000000000"
CONFIGURED_B = "bbbbbbbb-0000-0000-0000-000000000000"
UNCONFIGURED = "cccccccc-0000-0000-0000-000000000000"


def issue(identifier, project_id, estimate=2):
    return {
        "id": identifier,
        "identifier": identifier,
        "title": "title " + identifier,
        "priority": 3,
        "estimate": estimate,
        "updatedAt": "2026-01-01T00:00:00Z",
        "branchName": "b/" + identifier,
        "url": "https://example/" + identifier,
        "assignee": None,
        "labels": {"nodes": []},
        "state": {"id": "state1", "type": "unstarted"},
        "project": {"id": project_id, "name": "project-" + project_id}
        if project_id
        else None,
    }


class ReadyHarness(unittest.TestCase):
    def setUp(self):
        self._orig_fetch_issues = linear_ready.fetch_issues
        self._orig_resolve_team = linear_ready.resolve_team
        self._orig_get_key = linear_ready.get_key
        self.addCleanup(self._restore)
        linear_ready.resolve_team = lambda key, team: (
            {"id": "viewer-1"},
            {"id": "team-1", "name": "TeamX", "states": {"nodes": []}},
        )
        linear_ready.get_key = lambda: "fake-key"

    def _restore(self):
        linear_ready.fetch_issues = self._orig_fetch_issues
        linear_ready.resolve_team = self._orig_resolve_team
        linear_ready.get_key = self._orig_get_key

    def _run(self, argv, db):
        """Stub fetch_issues to serve from `db` (keyed by project_id, None = whole-team)
        and record every (team_id, project_id) it was called with."""
        calls = []

        def fake_fetch_issues(key, team_id, project_id, page_size):
            calls.append(project_id)
            return db.get(project_id, [])

        linear_ready.fetch_issues = fake_fetch_issues

        old_argv = sys.argv
        sys.argv = ["linear-ready.py"] + argv
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                linear_ready.main()
            result = json.loads(buf.getvalue())
        finally:
            sys.argv = old_argv
        return result, calls


class UnassignedBucketTests(ReadyHarness):
    def test_configured_scopes_tag_own_project_and_unassigned_pass_buckets_the_rest(
        self,
    ):
        db = {
            CONFIGURED_A: [issue("A-1", CONFIGURED_A)],
            CONFIGURED_B: [issue("B-1", CONFIGURED_B)],
            None: [
                issue("A-1", CONFIGURED_A),
                issue("B-1", CONFIGURED_B),
                issue("OUT-1", UNCONFIGURED),
                issue("NOPROJ-1", None),
            ],
        }
        result, calls = self._run(
            [
                "--team",
                "TeamX",
                "--project",
                f"{CONFIGURED_A}:3",
                "--project",
                f"{CONFIGURED_B}:3",
                "--max-estimate",
                "3",
            ],
            db,
        )

        # The whole-team query (project_id=None) ran once, alongside the two
        # per-project queries — this is the exclusion pass itself.
        self.assertEqual(calls.count(None), 1)
        self.assertEqual(calls.count(CONFIGURED_A), 1)
        self.assertEqual(calls.count(CONFIGURED_B), 1)

        by_id = {c["identifier"]: c for c in result["candidates"]}
        self.assertEqual(set(by_id), {"A-1", "B-1", "OUT-1", "NOPROJ-1"})

        # Configured-project issues keep their real scope...
        self.assertEqual(by_id["A-1"]["project"]["id"], CONFIGURED_A)
        self.assertEqual(by_id["B-1"]["project"]["id"], CONFIGURED_B)
        # ...null-project and unconfigured-project issues are bucketed.
        self.assertEqual(by_id["OUT-1"]["project"]["id"], "__unassigned__")
        self.assertEqual(by_id["OUT-1"]["project"]["name"], "Unassigned")
        self.assertEqual(by_id["NOPROJ-1"]["project"]["id"], "__unassigned__")

        # No duplication: each identifier appears exactly once.
        identifiers = [c["identifier"] for c in result["candidates"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_zero_projects_configured_skips_the_exclusion_pass(self):
        db = {None: [issue("W-1", CONFIGURED_A), issue("W-2", None)]}
        result, calls = self._run(["--team", "TeamX", "--max-estimate", "3"], db)

        # Exactly one whole-team query — no separate exclusion pass on top of it.
        self.assertEqual(calls, [None])

        by_id = {c["identifier"]: c for c in result["candidates"]}
        self.assertEqual(set(by_id), {"W-1", "W-2"})
        # The synthetic whole-team scope (id: None -> "TeamX"), never __unassigned__.
        for c in result["candidates"]:
            self.assertNotEqual(c["project"]["id"], "__unassigned__")


def relation(identifier, state_type, rel_type="blocks"):
    return {
        "type": rel_type,
        "issue": {"identifier": identifier, "state": {"type": state_type}},
    }


class BlockerGateTests(ReadyHarness):
    """The fast path reads each issue's inverse `blocks` relations and drops an
    issue whose blocker has not completed, wherever that blocker lives."""

    def with_relations(self, identifier, nodes, has_next=False):
        i = issue(identifier, CONFIGURED_A)
        i["inverseRelations"] = {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next},
        }
        return i

    def run_one(self, i):
        result, _ = self._run(
            ["--team", "TeamX", "--project", f"{CONFIGURED_A}:3"],
            {CONFIGURED_A: [i], None: [i]},
        )
        dropped = {d["identifier"]: d["reason"] for d in result["dropped"]}
        kept = [c["identifier"] for c in result["candidates"]]
        return kept, dropped

    def test_open_blocker_in_another_project_drops(self):
        # The blocker is outside every configured project, so a graph built
        # from the configured scopes cannot see it; the relation read does.
        kept, dropped = self.run_one(
            self.with_relations("A-1", [relation("OTHER-7", "started")])
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped, {"A-1": "waiting on OTHER-7"})

    def test_completed_blocker_passes(self):
        kept, dropped = self.run_one(
            self.with_relations("A-1", [relation("B-1", "completed")])
        )
        self.assertEqual(kept, ["A-1"])
        self.assertEqual(dropped, {})

    def test_canceled_blocker_still_blocks(self):
        _, dropped = self.run_one(
            self.with_relations("A-1", [relation("B-1", "canceled")])
        )
        self.assertEqual(dropped, {"A-1": "waiting on B-1"})

    def test_deleted_blocker_passes(self):
        kept, _ = self.run_one(
            self.with_relations("A-1", [{"type": "blocks", "issue": None}])
        )
        self.assertEqual(kept, ["A-1"])

    def test_non_blocking_relation_is_ignored(self):
        kept, _ = self.run_one(
            self.with_relations("A-1", [relation("B-1", "started", "related")])
        )
        self.assertEqual(kept, ["A-1"])

    def test_truncated_relation_page_holds_the_issue(self):
        _, dropped = self.run_one(
            self.with_relations("A-1", [relation("B-1", "completed")], has_next=True)
        )
        self.assertEqual(dropped, {"A-1": "waiting on more than 50 blockers"})


class StackInSetTests(ReadyHarness):
    """`--stack-in-set` (auto-pilot's list_ready) counts a blocker that is
    itself a surviving candidate as met; nothing else changes."""

    def chain(self, identifier, *blockers, estimate=2):
        i = issue(identifier, None, estimate=estimate)
        i["inverseRelations"] = {
            "nodes": [relation(b, s) for b, s in blockers],
            "pageInfo": {"hasNextPage": False},
        }
        return i

    def run_set(self, issues, stack):
        argv = ["--team", "TeamX", "--max-estimate", "3"]
        if stack:
            argv.append("--stack-in-set")
        result, _ = self._run(argv, {None: issues})
        dropped = {d["identifier"]: d["reason"] for d in result["dropped"]}
        return sorted(c["identifier"] for c in result["candidates"]), dropped

    def test_in_set_parent_is_a_stack_edge(self):
        issues = [self.chain("P-1"), self.chain("C-1", ("P-1", "unstarted"))]
        self.assertEqual(self.run_set(issues, stack=True), (["C-1", "P-1"], {}))

    def test_without_the_flag_the_child_waits(self):
        issues = [self.chain("P-1"), self.chain("C-1", ("P-1", "unstarted"))]
        kept, dropped = self.run_set(issues, stack=False)
        self.assertEqual(kept, ["P-1"])
        self.assertEqual(dropped, {"C-1": "waiting on P-1"})

    def test_chain_rooted_outside_the_set_drops_whole(self):
        issues = [
            self.chain("P-1", ("OUT-1", "started")),
            self.chain("C-1", ("P-1", "unstarted")),
        ]
        kept, dropped = self.run_set(issues, stack=True)
        self.assertEqual(kept, [])
        self.assertEqual(dropped, {"P-1": "waiting on OUT-1", "C-1": "waiting on P-1"})

    def test_parent_gated_out_for_another_reason_holds_the_child(self):
        issues = [
            self.chain("P-1", estimate=5),
            self.chain("C-1", ("P-1", "unstarted")),
        ]
        kept, dropped = self.run_set(issues, stack=True)
        self.assertEqual(kept, [])
        self.assertEqual(dropped["P-1"], "estimate 5 >= 3")
        self.assertEqual(dropped["C-1"], "waiting on P-1")

    def test_blocker_cycle_drops_both(self):
        # Neither can precede the other, so neither is a stack edge.
        issues = [
            self.chain("A-1", ("B-1", "unstarted")),
            self.chain("B-1", ("A-1", "unstarted")),
            self.chain("R-1"),
        ]
        kept, dropped = self.run_set(issues, stack=True)
        self.assertEqual(kept, ["R-1"])
        self.assertEqual(dropped, {"A-1": "waiting on B-1", "B-1": "waiting on A-1"})

    def test_chain_of_three_from_a_ready_root(self):
        issues = [
            self.chain("C-1", ("B-1", "unstarted")),
            self.chain("B-1", ("A-1", "unstarted")),
            self.chain("A-1"),
        ]
        self.assertEqual(self.run_set(issues, stack=True), (["A-1", "B-1", "C-1"], {}))


if __name__ == "__main__":
    unittest.main()
