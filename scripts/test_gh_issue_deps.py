#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-issue-deps.py.

Stubs the module's run_gh() seam so nothing shells out to `gh` or touches the
network. Covers the two facts that fail silently when wrong — the edge is a POST
to the dependencies endpoint rather than a body edit, and its payload carries the
blocker's DATABASE id rather than its issue number — plus create-missing-only
idempotency across pagination, the dry-run default, and the two ways a blocker
can fail to resolve.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-issue-deps.py"

_spec = importlib.util.spec_from_file_location("gh_issue_deps", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
gh_issue_deps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gh_issue_deps)

REPO = "bestdan/scratch"


class FakeRepo:
    """Issues with database ids, and the `blocked_by` list each one already has.

    `ids` maps issue number -> REST database id; a number absent from it does not
    exist in the repo, so its GET fails the way the real API's 404 does.
    `blocked_by` maps issue number -> list of blocker numbers already linked, and
    `page_size` splits that list into pages so a test can put an existing edge
    beyond page one — the shape `gh api --paginate --slurp` returns.
    """

    def __init__(self, ids, blocked_by=None, page_size=100):
        self.ids = dict(ids)
        self.blocked_by = dict(blocked_by or {})
        self.page_size = page_size
        self.calls = []
        self.posts = []
        self.deletes = []

    def run_gh(self, args, stdin=None):
        self.calls.append((args, stdin))
        if args[:3] == ["api", "--method", "POST"]:
            path = args[3]
            issue = int(path.split("/issues/")[1].split("/")[0])
            self.posts.append((issue, json.loads(stdin)))
            return 0, "{}", ""
        if args[:3] == ["api", "--method", "DELETE"]:
            path = args[3]
            issue = int(path.split("/issues/")[1].split("/")[0])
            # The last path segment is the identifier under test: the blocker's
            # DATABASE id, not its issue number.
            self.deletes.append((issue, int(path.rsplit("/", 1)[1])))
            return 0, "", ""
        if args[:3] == ["api", "--paginate", "--slurp"]:
            issue = int(args[3].split("/issues/")[1].split("/")[0])
            entries = [
                {"number": n, "state": "open"} for n in self.blocked_by.get(issue, [])
            ]
            pages = [
                entries[i : i + self.page_size]
                for i in range(0, max(len(entries), 1), self.page_size)
            ]
            return 0, json.dumps(pages), ""
        if args[0] == "api":
            issue = int(args[1].rsplit("/", 1)[1])
            if issue not in self.ids:
                return 1, "", "gh: Not Found (HTTP 404)"
            return 0, json.dumps({"number": issue, "id": self.ids[issue]}), ""
        raise AssertionError(f"unexpected gh call: {args}")

    def mutating_calls(self):
        return [
            args
            for args, _ in self.calls
            if "--method" in args or args[:2] == ["issue", "edit"]
        ]


class DepsTests(unittest.TestCase):
    def setUp(self):
        self._orig_run_gh = gh_issue_deps.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_issue_deps.run_gh = self._orig_run_gh

    def _run(self, repo, argv):
        gh_issue_deps.run_gh = repo.run_gh
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gh_issue_deps.main(["--repo", REPO, "--json", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_creates_the_edge_with_a_post_to_the_dependencies_endpoint(self):
        """The edge is a POST to `dependencies/blocked_by`, not a body edit."""
        repo = FakeRepo(ids={11: 900011, 12: 900012})
        code, out, _ = self._run(repo, ["--edge", "12:11", "--apply"])

        self.assertEqual(code, 0)
        posted = [
            args for args, _ in repo.calls if args[:3] == ["api", "--method", "POST"]
        ]
        self.assertEqual(len(posted), 1)
        self.assertEqual(
            posted[0][3], f"repos/{REPO}/issues/12/dependencies/blocked_by"
        )
        self.assertEqual(json.loads(out)["created"][0]["blocked"], 12)
        # Nothing rewrote an issue body to carry a footer instead.
        self.assertEqual(
            [args for args, _ in repo.calls if args[:2] == ["issue", "edit"]], []
        )

    def test_payload_carries_the_blockers_database_id_not_its_number(self):
        """`issue_id` is the REST `id`. Passing #11 would link a different issue."""
        repo = FakeRepo(ids={11: 900011, 12: 900012})
        self._run(repo, ["--edge", "12:11", "--apply"])

        self.assertEqual(repo.posts, [(12, {"issue_id": 900011})])

    def test_an_existing_edge_is_not_created_twice(self):
        """Create-missing-only, so a re-push adds no duplicate."""
        repo = FakeRepo(ids={11: 900011, 12: 900012}, blocked_by={12: [11]})
        code, out, _ = self._run(repo, ["--edge", "12:11", "--apply"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.posts, [])
        result = json.loads(out)
        self.assertEqual(result["created"], [])
        self.assertEqual(result["existing"], [{"blocked": 12, "blocker": 11}])

    def test_an_existing_edge_past_the_first_page_is_still_seen(self):
        """A bare read stops at 30 entries; `--paginate --slurp` is why this holds."""
        blockers = list(range(20, 51))  # 31 entries -> two pages at page_size 30
        ids = {n: 900000 + n for n in [*blockers, 12]}
        repo = FakeRepo(ids=ids, blocked_by={12: blockers}, page_size=30)

        code, out, _ = self._run(repo, ["--edge", "12:50", "--apply"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.posts, [])
        self.assertEqual(json.loads(out)["existing"], [{"blocked": 12, "blocker": 50}])

    def test_a_repeated_edge_is_created_once(self):
        repo = FakeRepo(ids={11: 900011, 12: 900012})
        code, out, _ = self._run(
            repo, ["--edge", "12:11", "--edge", "12:11", "--apply"]
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(repo.posts), 1)
        self.assertEqual(len(json.loads(out)["created"]), 1)

    def test_without_apply_nothing_is_written(self):
        repo = FakeRepo(ids={11: 900011, 12: 900012})
        code, out, _ = self._run(repo, ["--edge", "12:11"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.mutating_calls(), [])
        result = json.loads(out)
        self.assertFalse(result["applied"])
        self.assertEqual(result["created"][0]["blocker_id"], 900011)

    def test_an_unresolved_slug_blocker_is_skipped_with_a_warning(self):
        """`--ready-only` holds a blocker back, so its slug never became an issue."""
        repo = FakeRepo(ids={12: 900012})
        code, out, err = self._run(repo, ["--edge", "12:some_task_slug", "--apply"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.posts, [])
        self.assertEqual(
            json.loads(out)["skipped"],
            [{"blocked": 12, "blocker": "some_task_slug"}],
        )
        self.assertIn("some_task_slug", err)

    def test_a_blocker_in_another_repo_is_skipped_not_invented(self):
        repo = FakeRepo(ids={12: 900012})
        code, out, _ = self._run(repo, ["--edge", "12:other/repo#5", "--apply"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.posts, [])
        self.assertEqual(json.loads(out)["skipped"][0]["blocker"], "other/repo#5")

    def test_this_repos_own_qualified_reference_is_accepted(self):
        """`/push-plan` records `tracker_id` as `owner/repo#<n>` when the repo is configured."""
        repo = FakeRepo(ids={11: 900011, 12: 900012})
        code, _, _ = self._run(repo, ["--edge", f"{REPO}#12:{REPO}#11", "--apply"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.posts, [(12, {"issue_id": 900011})])

    def test_a_blocker_that_does_not_exist_yet_fails_loudly(self):
        """Reaching here means the edge pass ran before its issues were created."""
        repo = FakeRepo(ids={12: 900012})
        gh_issue_deps.run_gh = repo.run_gh
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                gh_issue_deps.main(["--repo", REPO, "--edge", "12:11", "--apply"])

        self.assertIn("#11", str(caught.exception))
        self.assertEqual(repo.posts, [])

    def test_a_self_edge_is_refused_before_any_network_call(self):
        repo = FakeRepo(ids={12: 900012})
        code, _, err = self._run(repo, ["--edge", "12:12", "--apply"])

        self.assertEqual(code, 2)
        self.assertEqual(repo.calls, [])
        self.assertIn("cannot block itself", err)

    # --- removal: stale-link repair for /reoptimize-tasks ------------------

    def test_removal_deletes_by_the_blockers_database_id(self):
        """Same identifier the POST body carries, in the path's last segment.

        The issue number there addresses a different edge, or none — and either
        way GitHub answers 204, so getting it wrong is silent.
        """
        repo = FakeRepo(ids={11: 900011, 12: 900012}, blocked_by={12: [11]})
        code, _, _ = self._run(repo, ["--remove-edge", "12:11", "--apply"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.deletes, [(12, 900011)])

    def test_removal_is_a_delete_not_a_body_edit(self):
        """The stale footer line is not the dependency; the edge is."""
        repo = FakeRepo(ids={11: 900011, 12: 900012}, blocked_by={12: [11]})
        self._run(repo, ["--remove-edge", "12:11", "--apply"])

        self.assertEqual(
            [args for args, _ in repo.calls if args[:2] == ["issue", "edit"]], []
        )

    def test_removing_an_edge_that_is_not_there_is_a_no_op(self):
        """Remove-existing-only, so re-running an approved repair costs nothing."""
        repo = FakeRepo(ids={11: 900011, 12: 900012}, blocked_by={12: []})
        code, out, _ = self._run(repo, ["--remove-edge", "12:11", "--apply"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.deletes, [])
        removal = json.loads(out)["removal"]
        self.assertEqual(removal["removed"], [])
        self.assertEqual(removal["absent"], [{"blocked": 12, "blocker": 11}])

    def test_a_repeated_removal_is_sent_once(self):
        repo = FakeRepo(ids={11: 900011, 12: 900012}, blocked_by={12: [11]})
        code, out, _ = self._run(
            repo, ["--remove-edge", "12:11", "--remove-edge", "12:11", "--apply"]
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(repo.deletes), 1)
        self.assertEqual(len(json.loads(out)["removal"]["removed"]), 1)

    def test_removal_without_apply_writes_nothing(self):
        repo = FakeRepo(ids={11: 900011, 12: 900012}, blocked_by={12: [11]})
        code, out, _ = self._run(repo, ["--remove-edge", "12:11"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.deletes, [])
        self.assertEqual(repo.mutating_calls(), [])
        self.assertEqual(json.loads(out)["removal"]["removed"][0]["blocker"], 11)

    def test_a_malformed_removal_is_refused_before_any_creation_runs(self):
        """Both lists are parsed first, so a half-written graph is impossible."""
        repo = FakeRepo(ids={11: 900011, 12: 900012})
        code, _, err = self._run(
            repo, ["--edge", "12:11", "--remove-edge", "12", "--apply"]
        )

        self.assertEqual(code, 2)
        self.assertEqual(repo.calls, [])
        self.assertIn("<blocked>:<blocker>", err)

    def test_a_malformed_edge_is_refused_before_any_network_call(self):
        repo = FakeRepo(ids={12: 900012})
        code, _, err = self._run(repo, ["--edge", "12", "--apply"])

        self.assertEqual(code, 2)
        self.assertEqual(repo.calls, [])
        self.assertIn("<blocked>:<blocker>", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
