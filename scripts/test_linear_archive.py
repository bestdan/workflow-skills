#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-archive.py.

The module talks to Linear over HTTP via ``gql``; these tests stub that seam so
nothing touches the network. They cover both sweep paths — whole-team (no
``--project``) and single-project (``--project <uuid>``) — and assert the
load-bearing invariant that broke in PRE-567: the GraphQL operation must never
declare a ``$variable`` it does not *reference in the operation body* (Linear
rejects a declared-but-unused variable with HTTP 400).
"""

import argparse
import contextlib
import importlib.util
import io
import re
import sys
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
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
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

    def test_sweep_still_excludes_archived_issues(self):
        """The other half of the --issues fix: the named lookups ask for archived
        rows, the sweep must not. Excluding them by default is what makes a
        re-run of --older-than a clean no-op instead of re-listing everything
        already archived."""
        query, _ = self._capture(team="PreThink", project=None)
        self.assertNotIn("includeArchived", query)

    def test_team_name_vs_uuid_selects_filter_field(self):
        name_query, _ = self._capture(team="PreThink", project=None)
        uuid_query, _ = self._capture(team=TEAM_UUID, project=None)
        self.assertIn("team: { name: { eq: $team } }", name_query)
        self.assertIn("team: { id: { eq: $team } }", uuid_query)


class CollectAgedMultiProjectTests(unittest.TestCase):
    """--project is repeatable: collect_aged loops the sweep once per configured
    project and unions the results, deduped by issue id (PRE-416)."""

    def _run(self, projects, nodes_by_project):
        """Stub find() to return nodes_by_project[project] for each call, and
        run collect_aged with those --project values."""
        calls = []

        def fake_find(key, team, project, state_type, ts_field, cutoff):
            calls.append(project)
            # Only the `completed` pass returns anything, to keep the stub simple.
            if state_type != "completed":
                return []
            return list(nodes_by_project.get(project, []))

        args = argparse.Namespace(
            team="PreThink", project=projects, older_than=10, issues=[]
        )
        original = linear_archive.find
        linear_archive.find = fake_find
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                candidates = linear_archive.collect_aged("k", args)
        finally:
            linear_archive.find = original
        return candidates, calls, buf.getvalue()

    def test_no_project_sweeps_whole_team_once(self):
        candidates, calls, _ = self._run([], {None: [{"id": "i-1", "completedAt": "2026-01-01"}]})
        self.assertEqual(calls.count(None), 3)  # once per terminal pass
        self.assertEqual([c["id"] for c in candidates], ["i-1"])

    def test_multiple_projects_are_unioned(self):
        candidates, calls, scope = self._run(
            ["p-1", "p-2"],
            {
                "p-1": [{"id": "i-1", "completedAt": "2026-01-01"}],
                "p-2": [{"id": "i-2", "completedAt": "2026-01-02"}],
            },
        )
        self.assertEqual(set(calls), {"p-1", "p-2"})
        self.assertEqual({c["id"] for c in candidates}, {"i-1", "i-2"})
        self.assertIn("projects=p-1,p-2", scope)

    def test_overlapping_projects_are_deduped_by_id(self):
        """An issue returned under more than one project scope is counted once."""
        candidates, _, _ = self._run(
            ["p-1", "p-2"],
            {
                "p-1": [{"id": "i-1", "completedAt": "2026-01-01"}],
                "p-2": [{"id": "i-1", "completedAt": "2026-01-01"}],
            },
        )
        self.assertEqual([c["id"] for c in candidates], ["i-1"])


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
            self.assertEqual(
                expected[state_type], ts_field, f"wrong ts for {state_type}"
            )


class RefLookupCase(unittest.TestCase):
    """Shared stubs for the --issues lookup path."""

    def _stub(self, nodes):
        calls = []

        def fake_gql(key, query, variables=None):
            calls.append((query, variables or {}))
            return {"issues": {"nodes": nodes}}

        return calls, fake_gql

    def _find(self, nodes, identifiers, uuids, team="PreThink"):
        calls, fake_gql = self._stub(nodes)
        original = linear_archive.gql
        linear_archive.gql = fake_gql
        try:
            found, archived, missing = linear_archive.find_by_ref(
                "k", team, identifiers, uuids
            )
        finally:
            linear_archive.gql = original
        return found, archived, missing, calls

    def _node(
        self,
        ident,
        state_type="completed",
        uuid="u-1",
        team="PreThink",
        archived_at=None,
    ):
        return {
            "id": uuid,
            "identifier": ident,
            "title": ident,
            "completedAt": "2026-07-31T12:00:00.000Z",
            "canceledAt": None,
            "archivedAt": archived_at,
            "state": {"type": state_type},
            "team": {"id": TEAM_UUID, "name": team},
        }


class NamedIssueTests(RefLookupCase):
    """--issues archives named issues regardless of age (the age threshold is the
    bulk-sweep mode's gate, not a property of archiving), but never archives an
    issue that is not in a terminal state."""

    def test_lookup_filters_on_number_not_age(self):
        *_, calls = self._find([self._node("PRE-12")], ["PRE-12"], [])
        query, variables = calls[0]
        self.assertIn("number: { in: $numbers }", query)
        self.assertEqual(variables["numbers"], [12])
        self.assertNotIn("cutoff", query)
        self.assertNotIn("completedAt: {", query)

    def test_lookup_declares_only_variables_it_uses(self):
        """Same HTTP-400 invariant as the sweep query (PRE-567)."""
        for identifiers, uuids in ((["PRE-12"], []), ([], [PROJECT_UUID])):
            *_, calls = self._find([], identifiers, uuids)
            query, variables = calls[0]
            declared = set(DECLARED_VAR_RE.findall(query))
            self.assertEqual(declared, set(variables))
            for var in declared:
                self.assertGreater(query.count(f"${var}"), 1, f"${var} unused")

    def test_other_teams_same_number_is_not_archived(self):
        """The number filter is team-scoped, so a foreign prefix must come back
        missing rather than matching this team's issue with the same number."""
        found, _, missing, _ = self._find([self._node("PRE-12")], ["OTH-12"], [])
        self.assertEqual(found, [])
        self.assertEqual(missing, ["OTH-12"])

    def test_unmatched_refs_reported_missing(self):
        found, archived, missing, _ = self._find(
            [self._node("PRE-12")], ["PRE-12", "PRE-99"], []
        )
        self.assertEqual([n["identifier"] for n in found], ["PRE-12"])
        self.assertEqual(archived, [])
        self.assertEqual(missing, ["PRE-99"])

    def test_non_terminal_named_issue_is_skipped(self):
        args = argparse.Namespace(
            team="PreThink", project=None, older_than=None, issues=["PRE-12,PRE-13"]
        )
        nodes = [
            self._node("PRE-12", "completed", "u-12"),
            self._node("PRE-13", "started", "u-13"),
        ]
        original = linear_archive.gql
        linear_archive.gql = lambda key, query, variables=None: {
            "issues": {"nodes": nodes}
        }
        try:
            candidates = linear_archive.collect_named("k", args)
        finally:
            linear_archive.gql = original
        self.assertEqual([c["identifier"] for c in candidates], ["PRE-12"])

    def test_uuid_on_another_team_is_not_archived(self):
        """An issue `id` is a global key, so its query cannot bind the team —
        the confinement to --team has to happen client-side or a pasted UUID
        archives an issue on a team the caller never named."""
        foreign = self._node("OTH-4", uuid=PROJECT_UUID, team="OtherTeam")
        found, _, missing, _ = self._find([foreign], [], [PROJECT_UUID])
        self.assertEqual(found, [])
        self.assertEqual(missing, [PROJECT_UUID])

    def test_uuid_case_does_not_desync_found_and_missing(self):
        """Linear returns lowercase ids. An un-normalized uppercase ref matched
        nothing in `missing`, so the same issue was archived AND reported
        'not found'."""
        upper = PROJECT_UUID.upper()
        identifiers, uuids = linear_archive.parse_issue_refs([upper])
        self.assertEqual(uuids, [PROJECT_UUID])
        found, _, missing, _ = self._find(
            [self._node("PRE-12", uuid=PROJECT_UUID)], identifiers, uuids
        )
        self.assertEqual([n["id"] for n in found], [PROJECT_UUID])
        self.assertEqual(missing, [])

    def test_oversized_ref_list_is_rejected_not_truncated(self):
        """Both lookups fetch a single page, so an overflowing list would come
        back as 'not found' and go unarchived — indistinguishable from a clean
        run. Reject it instead."""
        refs = ",".join(f"PRE-{n}" for n in range(linear_archive.PAGE + 1))
        args = argparse.Namespace(
            team="PreThink", project=None, older_than=None, issues=[refs]
        )
        with self.assertRaises(SystemExit):
            linear_archive.collect_named("k", args)

    def test_ignored_sweep_flags_are_announced(self):
        args = argparse.Namespace(
            team="PreThink", project=[PROJECT_UUID], older_than=7, issues=["PRE-12"]
        )
        original = linear_archive.gql
        linear_archive.gql = lambda key, query, variables=None: {
            "issues": {"nodes": [self._node("PRE-12")]}
        }
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                linear_archive.collect_named("k", args)
        finally:
            linear_archive.gql = original
        self.assertIn("--project is ignored", buf.getvalue())
        self.assertIn("--older-than is ignored", buf.getvalue())

    def test_bad_ref_is_rejected_not_dropped(self):
        with self.assertRaises(SystemExit):
            linear_archive.parse_issue_refs(["PRE-12, not an id"])

    def test_refs_accept_identifiers_and_uuids(self):
        identifiers, uuids = linear_archive.parse_issue_refs(
            ["pre-12, PRE-13", f" {PROJECT_UUID} "]
        )
        self.assertEqual(identifiers, ["PRE-12", "PRE-13"])
        self.assertEqual(uuids, [PROJECT_UUID])


class AlreadyArchivedTests(RefLookupCase):
    """A re-run of --issues names issues that are already archived. Linear's
    `issues` query drops archived rows unless asked, so without includeArchived
    those refs came back as "Not found on this team" — failure-shaped output for
    work that had already succeeded."""

    ARCHIVED_AT = "2026-07-30T09:00:00.000Z"

    def test_both_lookups_ask_for_archived_issues(self):
        for identifiers, uuids in ((["PRE-12"], []), ([], [PROJECT_UUID])):
            *_, calls = self._find([], identifiers, uuids)
            self.assertIn("includeArchived: true", calls[0][0])
            self.assertIn("archivedAt", calls[0][0])

    def test_archived_issue_is_reported_archived_not_missing(self):
        node = self._node("PRE-12", archived_at=self.ARCHIVED_AT)
        found, archived, missing, _ = self._find([node], ["PRE-12"], [])
        self.assertEqual(found, [])
        self.assertEqual([n["identifier"] for n in archived], ["PRE-12"])
        self.assertEqual(missing, [])

    def test_archived_is_not_an_archive_candidate_and_run_succeeds(self):
        """The whole point: a second `--issues PRE-12 --apply` exits 0 and says
        the issue is already archived, instead of reporting it missing."""
        argv = ["linear-archive.py", "--team", "PreThink", "--issues", "PRE-12"]
        nodes = [self._node("PRE-12", archived_at=self.ARCHIVED_AT)]
        original_gql, original_key, original_argv = (
            linear_archive.gql,
            linear_archive.get_key,
            sys.argv,
        )
        linear_archive.gql = lambda key, query, variables=None: {
            "issues": {"nodes": nodes}
        }
        linear_archive.get_key = lambda: "k"
        sys.argv = argv + ["--apply"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                linear_archive.main()  # returns (exit 0); does not SystemExit
        finally:
            linear_archive.gql = original_gql
            linear_archive.get_key = original_key
            sys.argv = original_argv
        out = buf.getvalue()
        self.assertIn("Already archived", out)
        self.assertIn("PRE-12", out)
        self.assertNotIn("Not found on this team", out)
        self.assertNotIn("Archiving", out)
        self.assertIn("Nothing to archive.", out)

    def test_unknown_ref_is_still_missing(self):
        found, archived, missing, _ = self._find([], ["PRE-99"], [])
        self.assertEqual((found, archived), ([], []))
        self.assertEqual(missing, ["PRE-99"])

    def test_archived_issue_on_another_team_is_missing_not_archived(self):
        """The team check runs first: an out-of-team ref must stay missing, or
        'already archived' would confirm work on a team the caller never named."""
        foreign = self._node(
            "OTH-4",
            uuid=PROJECT_UUID,
            team="OtherTeam",
            archived_at=self.ARCHIVED_AT,
        )
        found, archived, missing, _ = self._find([foreign], [], [PROJECT_UUID])
        self.assertEqual((found, archived), ([], []))
        self.assertEqual(missing, [PROJECT_UUID])


if __name__ == "__main__":
    unittest.main()
