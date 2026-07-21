#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-archive.py.

The module talks to Linear over HTTP via ``gql``; these tests stub that seam so
nothing touches the network. They cover both sweep paths — whole-team (no
``--project``) and single-project (``--project <uuid>``) — and assert the
load-bearing invariant that broke in PRE-567: the GraphQL operation must never
declare a ``$variable`` it does not pass (Linear rejects that with HTTP 400).
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

    def assert_no_undeclared_or_unused_vars(self, query, variables):
        """The HTTP-400 invariant: declared $vars and passed variables match exactly."""
        declared = set(DECLARED_VAR_RE.findall(query))
        passed = set(variables)
        self.assertEqual(
            declared,
            passed,
            f"declared {sorted(declared)} != passed {sorted(passed)}",
        )

    def test_whole_team_sweep_declares_no_project(self):
        query, variables = self._capture(team="PreThink", project=None)
        self.assertNotIn("$project", query)
        self.assertNotIn("project: {", query)
        self.assertNotIn("project", variables)
        self.assert_no_undeclared_or_unused_vars(query, variables)

    def test_single_project_sweep_declares_and_passes_project(self):
        query, variables = self._capture(team="PreThink", project=PROJECT_UUID)
        self.assertIn("$project: ID", query)
        self.assertIn("project: { id: { eq: $project } }", query)
        self.assertEqual(variables.get("project"), PROJECT_UUID)
        self.assert_no_undeclared_or_unused_vars(query, variables)

    def test_team_name_vs_uuid_selects_filter_field(self):
        name_query, _ = self._capture(team="PreThink", project=None)
        uuid_query, _ = self._capture(team=TEAM_UUID, project=None)
        self.assertIn("team: { name: { eq: $team } }", name_query)
        self.assertIn("team: { id: { eq: $team } }", uuid_query)


if __name__ == "__main__":
    unittest.main()
