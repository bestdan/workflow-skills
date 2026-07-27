#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-archive.py.

The module talks to Linear over HTTP via ``gql``; these tests stub that seam so
nothing touches the network. They cover both sweep paths — whole-team (no
``--project``) and single-project (``--project <uuid>``) — and assert the
load-bearing invariant that broke in PRE-567: the GraphQL operation must never
declare a ``$variable`` it does not *reference in the operation body* (Linear
rejects a declared-but-unused variable with HTTP 400).
"""

import importlib.util
import re
import unittest
from pathlib import Path

ASSET = (
    Path(__file__).resolve().parents[1]
    / "commands"
    / "handlers"
    / "assets"
    / "linear-archive.py"
)

_spec = importlib.util.spec_from_file_location("linear_archive", ASSET)
linear_archive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(linear_archive)

TEAM_UUID = "12345678-1234-1234-1234-1234567890ab"
PROJECT_UUID = "abcdef01-2345-6789-abcd-ef0123456789"
DECLARED_VAR_RE = re.compile(r"\$(\w+)\s*:")


class FindQueryTests(unittest.TestCase):
    def _capture(self, team, project):
        """Run find() with gql stubbed; return (query, variables) it was called with."""
        seen = {}

        def fake_gql(key, query, variables=None):
            seen["query"] = query
            seen["variables"] = variables or {}
            return {
                "issues": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }

        original = linear_archive.gql
        linear_archive.gql = fake_gql
        try:
            linear_archive.find(
                key="k",
                team=team,
                project=project,
                state_type="completed",
                ts_field="completedAt",
                cutoff="P30D",
            )
        finally:
            linear_archive.gql = original
        return seen["query"], seen["variables"]

    def assert_vars_declared_used_and_passed(self, query, variables):
        """The HTTP-400 invariant: every declared $var must be *referenced in the
        operation body* (the condition Linear 400s on), and the passed variables
        must match the declared set. A declared var appears once in the header;
        one that is also used appears again in the body, so its ``$name`` token
        occurs more than once."""
        declared = set(DECLARED_VAR_RE.findall(query))
        passed = set(variables)
        self.assertEqual(
            declared,
            passed,
            f"declared {sorted(declared)} != passed {sorted(passed)}",
        )
        for var in declared:
            self.assertGreater(
                query.count(f"${var}"),
                1,
                f"${var} is declared but never referenced in the operation "
                f"body (Linear rejects a declared-but-unused variable with 400)",
            )

    def test_whole_team_sweep_declares_no_project(self):
        query, variables = self._capture(team="PreThink", project=None)
        self.assertNotIn("$project", query)
        self.assertNotIn("project: {", query)
        self.assertNotIn("project", variables)
        self.assert_vars_declared_used_and_passed(query, variables)

    def test_single_project_sweep_declares_and_passes_project(self):
        query, variables = self._capture(team="PreThink", project=PROJECT_UUID)
        self.assertIn("$project: ID", query)
        self.assertIn("project: { id: { eq: $project } }", query)
        self.assertEqual(variables.get("project"), PROJECT_UUID)
        self.assert_vars_declared_used_and_passed(query, variables)

    def test_team_name_vs_uuid_selects_filter_field(self):
        name_query, _ = self._capture(team="PreThink", project=None)
        uuid_query, _ = self._capture(team=TEAM_UUID, project=None)
        self.assertIn("team: { name: { eq: $team } }", name_query)
        self.assertIn("team: { id: { eq: $team } }", uuid_query)


class TerminalPassesTests(unittest.TestCase):
    def test_sweeps_every_terminal_state(self):
        """All three of Linear's terminal state types are swept unconditionally.

        A state left unswept can never be archived and consumes the workspace
        issue cap forever — which is what happened to `duplicate` while the
        canceled pass was opt-in. `duplicate` is its own type, not a flavour of
        `canceled`, so it needs its own pass; both are timestamped by canceledAt.
        """
        self.assertEqual(
            linear_archive.terminal_passes(),
            [
                ("completed", "completedAt"),
                ("canceled", "canceledAt"),
                ("duplicate", "canceledAt"),
            ],
        )

    def test_every_pass_uses_a_timestamp_the_state_actually_sets(self):
        """A pass filtering on a timestamp its state never sets matches nothing —
        a silent no-op rather than an error. completed sets completedAt; canceled
        and duplicate both set canceledAt."""
        expected = {
            "completed": "completedAt",
            "canceled": "canceledAt",
            "duplicate": "canceledAt",
        }
        for state_type, ts_field in linear_archive.terminal_passes():
            self.assertEqual(expected[state_type], ts_field, f"wrong ts for {state_type}")


if __name__ == "__main__":
    unittest.main()
