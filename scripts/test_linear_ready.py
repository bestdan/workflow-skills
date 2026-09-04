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


class UnassignedBucketTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
