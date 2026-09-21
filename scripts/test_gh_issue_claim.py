#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-issue-claim.py.

Stubs the module's run_gh() seam so nothing shells out to `gh` or touches the
network. Covers the branch-name model (deterministic, any-prefix parseable),
the acquire election's exit-code contract (0 won / 3 lost / 4 indeterminate)
including the concurrency case where a second acquire against the same fake
remote must lose without ever touching the issue, the wip count's single
query, both in-flight labels and its clamped batch `slack`, and release.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-issue-claim.py"
LABELS_FILE = ROOT / "commands" / "handlers" / "assets" / "labels.yml"

_spec = importlib.util.spec_from_file_location("gh_issue_claim", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
gh_issue_claim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gh_issue_claim)


class FakeRemote:
    """A fake GitHub remote: create-only refs, plus WIP search results.

    `refs` starts as the set of refs that already exist. `run_gh` records
    every call so a test can assert the loser of a race made no mutating
    call against the issue itself.
    """

    def __init__(self, refs=(), wip_issues=()):
        self.refs = set(refs)
        self.wip_issues = list(wip_issues)
        self.calls = []

    def run_gh(self, args, stdin=None):
        self.calls.append((args, stdin))
        if args[:3] == ["api", "--method", "POST"] and args[3] == "repos/o/n/git/refs":
            payload = json.loads(stdin)
            ref = payload["ref"]
            if ref in self.refs:
                return (
                    1,
                    "",
                    'gh: Reference already exists (HTTP 422: "https://api.github.com/...")',
                )
            self.refs.add(ref)
            return 0, json.dumps({"ref": ref}), ""
        if args[:3] == ["api", "--method", "DELETE"]:
            path = args[3]
            ref = "refs/heads/" + path.split("/git/refs/heads/", 1)[1]
            if ref not in self.refs:
                return 1, "", "gh: Reference does not exist (HTTP 422)"
            self.refs.discard(ref)
            return 0, "", ""
        if args[:2] == ["issue", "list"]:
            # Honour --limit: the real API truncates before anything local
            # runs, which is the whole reason the caller must ask for enough.
            limit = int(args[args.index("--limit") + 1])
            payload = [
                {"number": n, "title": t, "labels": []}
                for n, t in self.wip_issues[:limit]
            ]
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected gh call: {args}")

    def mutating_issue_calls(self):
        """Calls that touch the ISSUE itself (assignee/labels) — never legal here."""
        return [
            (args, stdin)
            for args, stdin in self.calls
            if args[:2] == ["issue", "edit"]
            or (
                "--method" in args
                and any(a.startswith("repos/") and "/issues/" in a for a in args)
            )
        ]


class BranchNameTests(unittest.TestCase):
    def test_default_prefix_is_empty(self):
        self.assertEqual(gh_issue_claim.branch_name(142), "task-142")

    def test_explicit_prefix_is_used_verbatim(self):
        self.assertEqual(
            gh_issue_claim.branch_name(142, "bestdan/"), "bestdan/task-142"
        )


class IssueNumberTests(unittest.TestCase):
    def test_parses_configured_and_cloud_and_bare_prefixes(self):
        self.assertEqual(gh_issue_claim.parse_issue_number("bestdan/task-142"), 142)
        self.assertEqual(gh_issue_claim.parse_issue_number("claude/task-142"), 142)
        self.assertEqual(gh_issue_claim.parse_issue_number("task-142"), 142)

    def test_rejects_non_task_branches(self):
        self.assertIsNone(gh_issue_claim.parse_issue_number("feature/other"))
        self.assertIsNone(gh_issue_claim.parse_issue_number("task-142-fixup"))
        self.assertIsNone(gh_issue_claim.parse_issue_number("mytask-142"))
        self.assertIsNone(gh_issue_claim.parse_issue_number("task-"))

    def test_cli_exits_1_with_no_stdout_on_a_non_task_branch(self):
        for branch in ("feature/other", "task-142-fixup", "mytask-142"):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = gh_issue_claim.main(["issue-number", "--branch", branch])
            self.assertEqual(code, 1, branch)
            self.assertEqual(out.getvalue(), "", branch)
            self.assertNotEqual(err.getvalue(), "", branch)


class AcquireConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self._orig = gh_issue_claim.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_issue_claim.run_gh = self._orig

    def _acquire(self, remote, prefix=""):
        gh_issue_claim.run_gh = remote.run_gh
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gh_issue_claim.main(
                [
                    "acquire",
                    "--repo",
                    "o/n",
                    "--issue",
                    "142",
                    "--base-sha",
                    "deadbeef",
                    "--prefix",
                    prefix,
                ]
            )
        return code, out.getvalue(), err.getvalue()

    def test_first_acquire_wins_second_loses_and_issue_is_never_touched(self):
        remote = FakeRemote()

        code1, out1, _ = self._acquire(remote)
        self.assertEqual(code1, 0)
        self.assertEqual(out1.strip(), "task-142")
        refs_after_first = set(remote.refs)

        code2, out2, err2 = self._acquire(remote)
        self.assertEqual(code2, 3)
        self.assertEqual(out2, "", "a lost race must never print an acquired branch")
        self.assertIn("task-142", err2)

        # The loser must not have changed the ref set, and neither call may
        # have touched the issue itself.
        self.assertEqual(remote.refs, refs_after_first)
        self.assertEqual(remote.mutating_issue_calls(), [])

    def test_indeterminate_failure_is_exit_4_not_3_and_claims_nothing(self):
        class Forbidden:
            def __init__(self):
                self.calls = []

            def run_gh(self, args, stdin=None):
                self.calls.append((args, stdin))
                return 1, "", "HTTP 403: Resource not accessible by integration"

        remote = Forbidden()
        gh_issue_claim.run_gh = remote.run_gh
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gh_issue_claim.main(
                [
                    "acquire",
                    "--repo",
                    "o/n",
                    "--issue",
                    "142",
                    "--base-sha",
                    "deadbeef",
                ]
            )
        self.assertEqual(code, 4)
        self.assertEqual(out.getvalue().strip(), "")
        self.assertNotIn("acquired", err.getvalue().lower())
        self.assertIn("403", err.getvalue())


class AcquireRefTests(unittest.TestCase):
    """`acquire-ref` shares acquire_ref() with `acquire` but takes the branch
    name directly, for callers (claim-lock.md, the jira handler) that derive
    it under their own naming rule instead of gh-issue's issue-number rule."""

    def setUp(self):
        self._orig = gh_issue_claim.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_issue_claim.run_gh = self._orig

    def _acquire_ref(self, remote, branch="task/PLAT-142"):
        gh_issue_claim.run_gh = remote.run_gh
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gh_issue_claim.main(
                [
                    "acquire-ref",
                    "--repo",
                    "o/n",
                    "--branch",
                    branch,
                    "--base-sha",
                    "deadbeef",
                ]
            )
        return code, out.getvalue(), err.getvalue()

    def test_wins_with_a_tracker_neutral_branch_name(self):
        remote = FakeRemote()

        code, out, _ = self._acquire_ref(remote)

        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "task/PLAT-142")

    def test_422_is_a_lost_race_not_an_error(self):
        remote = FakeRemote(refs={"refs/heads/task/PLAT-142"})

        code, out, err = self._acquire_ref(remote)

        self.assertEqual(code, 3)
        self.assertEqual(out, "", "a lost race must never print an acquired branch")
        self.assertIn("task/PLAT-142", err)
        self.assertEqual(remote.mutating_issue_calls(), [])

    def test_indeterminate_failure_is_exit_4_not_3(self):
        class Forbidden:
            def __init__(self):
                self.calls = []

            def run_gh(self, args, stdin=None):
                self.calls.append((args, stdin))
                return 1, "", "HTTP 403: Resource not accessible by integration"

        code, out, err = self._acquire_ref(Forbidden())

        self.assertEqual(code, 4)
        self.assertEqual(out.strip(), "")
        self.assertNotIn("acquired", err.lower())
        self.assertIn("403", err)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self._orig = gh_issue_claim.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_issue_claim.run_gh = self._orig

    def test_release_deletes_the_right_ref_path(self):
        remote = FakeRemote(refs={"refs/heads/task-142"})
        gh_issue_claim.run_gh = remote.run_gh

        code = gh_issue_claim.main(["release", "--repo", "o/n", "--issue", "142"])

        self.assertEqual(code, 0)
        delete_call = next(
            (args, stdin) for args, stdin in remote.calls if "--method" in args
        )
        self.assertEqual(
            delete_call[0],
            ["api", "--method", "DELETE", "repos/o/n/git/refs/heads/task-142"],
        )
        self.assertNotIn("refs/heads/task-142", remote.refs)


class WipTests(unittest.TestCase):
    def setUp(self):
        self._orig = gh_issue_claim.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_issue_claim.run_gh = self._orig

    def test_counts_both_in_flight_labels_in_one_query(self):
        remote = FakeRemote(wip_issues=[(1, "started"), (2, "in review")])
        gh_issue_claim.run_gh = remote.run_gh

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gh_issue_claim.main(
                ["wip", "--repo", "o/n", "--wip-limit", "3", "--json"]
            )
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["count"], 2)
        self.assertFalse(result["at_limit"])

        list_calls = [args for args, _ in remote.calls if args[:2] == ["issue", "list"]]
        self.assertEqual(
            len(list_calls), 1, "must issue exactly one gh issue list call"
        )
        search = list_calls[0][list_calls[0].index("--search") + 1]
        self.assertIn("assignee:@me", search)
        self.assertIn('"status:3_started"', search)
        self.assertIn('"status:4_needs_review"', search)

    def test_at_limit_true_at_cap_false_below_it(self):
        remote_below = FakeRemote(wip_issues=[(1, "a"), (2, "b")])
        gh_issue_claim.run_gh = remote_below.run_gh
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gh_issue_claim.main(["wip", "--repo", "o/n", "--wip-limit", "3", "--json"])
        self.assertFalse(json.loads(out.getvalue())["at_limit"])

        remote_at = FakeRemote(wip_issues=[(1, "a"), (2, "b"), (3, "c")])
        gh_issue_claim.run_gh = remote_at.run_gh
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gh_issue_claim.main(["wip", "--repo", "o/n", "--wip-limit", "3", "--json"])
        self.assertTrue(json.loads(out.getvalue())["at_limit"])

    def test_slack_is_the_remaining_batch_ceiling(self):
        """A batch reads `slack`, never `limit - count` done in prose."""
        remote = FakeRemote(wip_issues=[(1, "a"), (2, "b")])
        gh_issue_claim.run_gh = remote.run_gh
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gh_issue_claim.main(["wip", "--repo", "o/n", "--wip-limit", "3", "--json"])
        self.assertEqual(json.loads(out.getvalue())["slack"], 1)

        remote_at = FakeRemote(wip_issues=[(1, "a"), (2, "b"), (3, "c")])
        gh_issue_claim.run_gh = remote_at.run_gh
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gh_issue_claim.main(["wip", "--repo", "o/n", "--wip-limit", "3", "--json"])
        self.assertEqual(json.loads(out.getvalue())["slack"], 0)

    def test_slack_is_clamped_at_zero_over_the_limit(self):
        """An over-limit board must not hand a batch a negative ceiling."""
        remote = FakeRemote(wip_issues=[(n, "x") for n in range(1, 6)])
        gh_issue_claim.run_gh = remote.run_gh
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gh_issue_claim.main(["wip", "--repo", "o/n", "--wip-limit", "3", "--json"])
        result = json.loads(out.getvalue())
        self.assertEqual(result["count"], 5)
        self.assertEqual(result["slack"], 0)

    def test_the_query_fetches_enough_to_decide_the_limit(self):
        """A wip_limit above the query cap must not manufacture slack.

        count_wip's --limit defaults to 100. With a larger wip_limit, a
        truncated page reads as a count below the limit, so `slack` comes
        back positive on a board that is already over it and the batch
        dispatches into the overflow.
        """
        remote = FakeRemote(wip_issues=[(n, "x") for n in range(1, 201)])
        gh_issue_claim.run_gh = remote.run_gh
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gh_issue_claim.main(
                ["wip", "--repo", "o/n", "--wip-limit", "150", "--json"]
            )
        result = json.loads(out.getvalue())
        self.assertEqual(result["slack"], 0)
        self.assertTrue(result["at_limit"])

        list_calls = [args for args, _ in remote.calls if args[:2] == ["issue", "list"]]
        fetched = int(list_calls[0][list_calls[0].index("--limit") + 1])
        self.assertGreaterEqual(
            fetched, 150, "must fetch at least wip_limit before deriving the ceiling"
        )

    def test_exits_0_regardless_of_at_limit(self):
        remote = FakeRemote(wip_issues=[(1, "a"), (2, "b"), (3, "c")])
        gh_issue_claim.run_gh = remote.run_gh
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gh_issue_claim.main(["wip", "--repo", "o/n", "--wip-limit", "3"])
        self.assertEqual(code, 0)

    def _bad_vocabulary(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "labels.yml"
        path.write_text(text)
        return path

    def test_missing_started_value_fails_loudly(self):
        bad = self._bad_vocabulary(
            "status: [0_untriaged, 1_needs_refinement, 2_ready, 4_needs_review]\n"
            "auto: [eligible, human-review-needed]\n"
            "prio: [0, 1, 2, 3]\n"
            "est: [1, 2, 3, 5, 8, 13]\n"
            "colors:\n  status: 1d76db\n  auto: 0e8a16\n  prio: d93f0b\n  est: 5319e2\n"
        )
        remote = FakeRemote()
        gh_issue_claim.run_gh = remote.run_gh

        with self.assertRaises(gh_issue_claim.VocabularyError) as ctx:
            gh_issue_claim.count_wip("o/n", bad, 100)
        self.assertIn("3_started", str(ctx.exception))
        self.assertEqual(remote.calls, [], "must fail before any network call")


class FakeGit:
    """A fake `git ls-remote --heads`: a ref list, or a failure that prints nothing.

    `fail` reproduces the case the exit-code contract exists for — `ls-remote`
    exits non-zero and writes nothing to stdout, so output alone cannot tell an
    outage from a free issue.
    """

    def __init__(self, refs=(), fail=False):
        self.refs = list(refs)
        self.fail = fail
        self.calls = []

    def run_git(self, args):
        self.calls.append(args)
        if self.fail:
            return 128, "", "fatal: could not read from remote repository"
        body = "".join(f"{'0' * 40}\t{ref}\n" for ref in self.refs)
        return 0, body, ""


class FindTaskRefsTests(unittest.TestCase):
    """The cross-prefix probe. Each case here is a defect the prose form shipped."""

    def setUp(self):
        self._real = gh_issue_claim.run_git
        self.addCleanup(self._restore)

    def _restore(self):
        gh_issue_claim.run_git = self._real

    def _find(self, refs, issue=42, fail=False):
        git = FakeGit(refs=refs, fail=fail)
        gh_issue_claim.run_git = git.run_git
        return gh_issue_claim.find_task_refs(issue)

    def test_finds_every_prefix_shape_and_keeps_the_prefix(self):
        found = self._find(
            [
                "refs/heads/task-42",
                "refs/heads/bestdan/task-42",
                "refs/heads/claude/sess-1/task-42",
            ]
        )
        self.assertEqual(found, ["task-42", "bestdan/task-42", "claude/sess-1/task-42"])

    def test_rejects_near_misses_the_parser_also_rejects(self):
        # A pattern loose enough to catch `team-task-42` also catches these,
        # and each would fire a hard stop against an unrelated branch.
        found = self._find(
            [
                "refs/heads/pre-task-42",
                "refs/heads/refactor-task-42",
                "refs/heads/team-task-42",
                "refs/heads/task-420",
                "refs/heads/task-42-fixup",
                "refs/heads/a/pre-task-42",
            ]
        )
        self.assertEqual(found, [], "probe must agree with parse_issue_number")

    def test_a_name_containing_refs_heads_is_not_collapsed(self):
        # A greedy strip reduces this to `task-42`, which then compares EQUAL to
        # a bare `<branch>` and reads a foreign ref as this session's own.
        found = self._find(["refs/heads/foo/refs/heads/task-42"])
        self.assertEqual(found, ["foo/refs/heads/task-42"])

    def test_non_branch_refs_are_ignored(self):
        found = self._find(["refs/tags/task-42", "refs/pull/42/head"])
        self.assertEqual(found, [])

    def test_a_failed_probe_raises_instead_of_looking_free(self):
        with self.assertRaises(gh_issue_claim.ProbeFailed):
            self._find(["refs/heads/bestdan/task-42"], fail=True)

    def test_cli_exit_codes_separate_none_found_from_no_answer(self):
        git = FakeGit(refs=["refs/heads/bestdan/task-42"])
        gh_issue_claim.run_git = git.run_git
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gh_issue_claim.main(["find-task-refs", "--issue", "42"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), "bestdan/task-42")

        git = FakeGit(refs=["refs/heads/bestdan/task-99"])
        gh_issue_claim.run_git = git.run_git
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gh_issue_claim.main(["find-task-refs", "--issue", "42"])
        self.assertEqual(code, 1, "no ref for this issue is exit 1")
        self.assertEqual(out.getvalue(), "")

        git = FakeGit(fail=True)
        gh_issue_claim.run_git = git.run_git
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gh_issue_claim.main(["find-task-refs", "--issue", "42"])
        self.assertEqual(code, 4, "an unanswered probe must not look like exit 1")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("probe failed", err.getvalue())

    def test_remote_is_configurable_and_reaches_ls_remote(self):
        git = FakeGit(refs=[])
        gh_issue_claim.run_git = git.run_git
        gh_issue_claim.main(["find-task-refs", "--issue", "42", "--remote", "upstream"])
        self.assertEqual(git.calls, [["ls-remote", "--heads", "upstream"]])


if __name__ == "__main__":
    unittest.main()
