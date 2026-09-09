#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-false-closures.py's
--prs-file path — the alternative to --repo for hosts where `gh` cannot reach
the GitHub API (a Claude Code cloud routine). These tests stub gql, get_key,
and the shared `_linear_pr.run_gh` seam so nothing touches the network or a
real `gh` binary.

Covers only the new behaviour: --repo/--prs-file mutual exclusivity, the
--prs-file classification parity with an equivalent --repo run, the coverage
guard that refuses to classify an issue created before the window opened, the
null-mergedAt filter, and the malformed-input error messages.
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ASSET = (
    Path(__file__).resolve().parents[1]
    / "commands"
    / "handlers"
    / "assets"
    / "linear-false-closures.py"
)

_spec = importlib.util.spec_from_file_location("linear_false_closures", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
linear_false_closures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(linear_false_closures)

# The asset puts its own directory on sys.path at import time, so the shared
# module it pulls `merged_prs`/`pr_identity` from is importable by name here.
# That module owns the one `gh` subprocess seam both linear assets share.
import _linear_pr  # type: ignore[import-not-found]  # noqa: E402

TEAM_UUID = "12345678-1234-1234-1234-1234567890ab"


def _issue(
    identifier,
    started_at,
    completed_at,
    attachments=None,
    children=None,
    created_at=None,
):
    return {
        "id": f"id-{identifier}",
        "identifier": identifier,
        "title": f"title for {identifier}",
        # The coverage guard anchors on createdAt. Defaulting it to the issue's
        # own start keeps every existing fixture's window semantics unchanged;
        # a case that needs an issue created before it started passes it.
        "createdAt": created_at or started_at or completed_at,
        "startedAt": started_at,
        "completedAt": completed_at,
        "team": {"id": TEAM_UUID},
        "attachments": attachments or {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "children": children or {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }


def _pr(number, head_ref, title="", body="", merged_at="2026-08-10T00:00:00Z"):
    return {
        "number": number,
        "headRefName": head_ref,
        "url": f"https://github.com/bestdan/repo/pull/{number}",
        "title": title,
        "body": body,
        "mergedAt": merged_at,
    }


class RunCase(unittest.TestCase):
    """Shared harness: stub gql (issue listing) and get_key; capture main()'s
    stdout and return code without touching the network."""

    def setUp(self):
        self._orig_gql = linear_false_closures.gql
        self._orig_get_key = linear_false_closures.get_key
        self._orig_run = _linear_pr.run_gh
        self._orig_argv = sys.argv
        linear_false_closures.get_key = lambda: "k"

    def tearDown(self):
        linear_false_closures.gql = self._orig_gql
        linear_false_closures.get_key = self._orig_get_key
        _linear_pr.run_gh = self._orig_run
        sys.argv = self._orig_argv

    def _stub_issues(self, issues):
        def fake_gql(key, query, variables=None):
            return {
                "project": {
                    "name": "Test Project",
                    "issues": {
                        "nodes": issues,
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }
            }

        linear_false_closures.gql = fake_gql

    def _forbid_gh(self):
        def fail(*a, **k):
            raise AssertionError("gh should not be invoked under --prs-file")

        _linear_pr.run_gh = fail

    def _run_main(self, argv):
        sys.argv = ["linear-false-closures.py"] + argv
        buf = io.StringIO()
        code = None
        try:
            with contextlib.redirect_stdout(buf):
                code = linear_false_closures.main()
        except SystemExit as e:
            code = e.code
        return buf.getvalue(), code


class MutualExclusivityTests(RunCase):
    def test_repo_and_prs_file_together_is_a_usage_error(self):
        # main() calls die() -> sys.exit(str), which SystemExit propagates.
        with self.assertRaises(SystemExit) as ctx:
            sys.argv = [
                "linear-false-closures.py",
                "--project",
                "p",
                "--repo",
                "o/r",
                "--prs-file",
                "x.json",
            ]
            linear_false_closures.main()
        self.assertIn("exactly one of --repo or --prs-file", str(ctx.exception))

    def test_neither_repo_nor_prs_file_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            sys.argv = ["linear-false-closures.py", "--project", "p"]
            linear_false_closures.main()
        self.assertIn("exactly one of --repo or --prs-file", str(ctx.exception))


class ParityTests(RunCase):
    """A well-formed --prs-file run classifies exactly like the equivalent
    --repo run, and never shells out to `gh`."""

    def _fixture(self):
        issues = [
            _issue("PRE-1", "2026-08-10T00:00:00Z", "2026-08-11T00:00:00Z"),
            _issue("PRE-2", "2026-08-10T00:00:00Z", "2026-08-11T00:00:00Z"),
        ]
        prs = [
            _pr(101, "dpegan/pre-1-do-the-thing", merged_at="2026-08-10T12:00:00Z"),
        ]
        return issues, prs

    def test_prs_file_matches_repo_run_and_skips_gh(self):
        issues, prs = self._fixture()

        self._stub_issues(issues)

        def fake_run_gh(args):
            return 0, "\n".join(json.dumps(pr) for pr in prs), ""

        _linear_pr.run_gh = fake_run_gh
        repo_out, repo_code = self._run_main(
            ["--project", "p", "--repo", "bestdan/repo"]
        )

        self._stub_issues(issues)
        self._forbid_gh()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(
                {"complete_since": "2026-01-01T00:00:00Z", "pull_requests": prs}, f
            )
            prs_path = f.name
        try:
            prs_out, prs_code = self._run_main(
                ["--project", "p", "--prs-file", prs_path]
            )
        finally:
            Path(prs_path).unlink()

        self.assertEqual(repo_code, prs_code)
        self.assertEqual(repo_out, prs_out)
        # Load-bearing: without these, two *empty* outputs would also compare
        # equal, so they prove the fixture actually exercised both branches.
        self.assertIn("ok    PRE-1", repo_out)
        self.assertIn("BAD   PRE-2", repo_out)


class CoverageGuardTests(RunCase):
    def test_issue_predating_window_is_skipped_not_flagged_and_exit_is_clean(self):
        # No owning PR at all -- would be a false closure if classified.
        issue = _issue("PRE-9", "2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z")
        self._stub_issues([issue])
        self._forbid_gh()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(
                {"complete_since": "2026-08-08T00:00:00Z", "pull_requests": []}, f
            )
            prs_path = f.name
        try:
            out, code = self._run_main(["--project", "p", "--prs-file", prs_path])
        finally:
            Path(prs_path).unlink()

        self.assertIn("skip  PRE-9", out)
        self.assertIn("merged-PR window starts 2026-08-08T00:00:00Z", out)
        self.assertNotIn("FALSE CLOSURES", out)
        self.assertEqual(code, 0)

    def test_issue_within_window_with_no_owner_is_still_flagged(self):
        issue = _issue("PRE-10", "2026-08-09T00:00:00Z", "2026-08-10T00:00:00Z")
        self._stub_issues([issue])
        self._forbid_gh()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(
                {"complete_since": "2026-08-08T00:00:00Z", "pull_requests": []}, f
            )
            prs_path = f.name
        try:
            out, code = self._run_main(["--project", "p", "--prs-file", prs_path])
        finally:
            Path(prs_path).unlink()

        self.assertIn("FALSE CLOSURES", out)
        self.assertIn("PRE-10", out)
        self.assertEqual(code, 1)

    def test_never_started_issue_created_before_window_is_skipped(self):
        # This is exactly the population the tool targets: completed with no
        # work behind it, hence often never started. Before the createdAt
        # anchor, this was classified -- and could be reopened -- because
        # completedAt (inside the window by construction) was used instead.
        issue = _issue(
            "PRE-11",
            started_at=None,
            completed_at="2026-08-09T00:00:00Z",
            created_at="2026-06-01T00:00:00Z",
        )
        self._stub_issues([issue])
        self._forbid_gh()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(
                {"complete_since": "2026-08-08T00:00:00Z", "pull_requests": []}, f
            )
            prs_path = f.name
        try:
            out, code = self._run_main(["--project", "p", "--prs-file", prs_path])
        finally:
            Path(prs_path).unlink()

        self.assertIn("skip  PRE-11", out)
        self.assertNotIn("FALSE CLOSURES", out)
        self.assertEqual(code, 0)


class NullMergedAtTests(unittest.TestCase):
    def test_pr_with_null_merged_at_is_dropped(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "complete_since": "2026-08-08T00:00:00Z",
                    "pull_requests": [
                        _pr(1, "some-branch", merged_at=None),
                        _pr(2, "other-branch"),
                    ],
                },
                f,
            )
            path = f.name
        try:
            since, prs = linear_false_closures.load_prs_file(path)
        finally:
            Path(path).unlink()
        self.assertEqual(since, "2026-08-08T00:00:00Z")
        self.assertEqual([pr["number"] for pr in prs], [2])


class MalformedInputTests(unittest.TestCase):
    def _die_message(self, payload_text):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(payload_text)
            path = f.name
        try:
            with self.assertRaises(SystemExit) as ctx:
                linear_false_closures.load_prs_file(path)
            return str(ctx.exception)
        finally:
            Path(path).unlink()

    def test_missing_file(self):
        with self.assertRaises(SystemExit) as ctx:
            linear_false_closures.load_prs_file("/nonexistent/does-not-exist.json")
        self.assertIn("--prs-file", str(ctx.exception))

    def test_unparseable_json(self):
        msg = self._die_message(payload_text="{not json")
        self.assertIn("invalid JSON", msg)

    def test_top_level_not_an_object(self):
        msg = self._die_message(payload_text="[]")
        self.assertIn("--prs-file", msg)
        self.assertIn("expected an object", msg)

    def test_missing_complete_since(self):
        msg = self._die_message(payload_text=json.dumps({"pull_requests": []}))
        self.assertIn("complete_since", msg)

    def test_blank_complete_since(self):
        msg = self._die_message(
            payload_text=json.dumps({"complete_since": "  ", "pull_requests": []})
        )
        self.assertIn("complete_since", msg)

    def test_unparseable_complete_since(self):
        msg = self._die_message(
            payload_text=json.dumps(
                {"complete_since": "not-a-timestamp", "pull_requests": []}
            )
        )
        self.assertIn("complete_since", msg)

    def test_pull_requests_not_a_list(self):
        msg = self._die_message(
            payload_text=json.dumps(
                {"complete_since": "2026-08-08T00:00:00Z", "pull_requests": {}}
            )
        )
        self.assertIn("pull_requests", msg)

    def test_pull_requests_entry_that_is_a_bare_string_is_fatal(self):
        # An agent that flattens the fetched list to URLs produces a file
        # where every entry is dropped, complete_since still looks valid, and
        # every in-window issue becomes a false closure -- so this must be loud.
        msg = self._die_message(
            payload_text=json.dumps(
                {
                    "complete_since": "2026-08-08T00:00:00Z",
                    "pull_requests": [
                        "https://github.com/bestdan/workflow-skills/pull/1",
                    ],
                }
            )
        )
        self.assertIn("pull_requests", msg)
        self.assertTrue("[0]" in msg or "str" in msg)

    def test_merged_entry_without_a_url_is_fatal(self):
        # Dropping it instead would shrink the ownership evidence and turn
        # delivered work into a "false closure" that --apply reopens.
        msg = self._die_message(
            payload_text=json.dumps(
                {
                    "complete_since": "2026-08-08T00:00:00Z",
                    "pull_requests": [
                        {"number": 1, "mergedAt": "2026-09-01T00:00:00Z"},
                    ],
                }
            )
        )
        self.assertIn("pull_requests", msg)
        self.assertIn("url", msg)


if __name__ == "__main__":
    unittest.main()
