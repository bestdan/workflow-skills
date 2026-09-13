#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-issue-pr-sync.py.

Stubs the run_gh() seam of the gh-issue-state module that pr-sync loads, so
nothing shells out to `gh` or touches the network. Covers the two rung
transitions (ready-for-review forward, closed-unmerged back), the merged branch
that strips both rungs from every issue the PR closed, every no-op gate, and the
fact that a write is one PATCH carrying the complete set — never
`--add-label`, which is not atomic.

Also asserts three things about the workflow that drives it, which live in YAML
rather than Python but are load-bearing behavior:

- it triggers on `opened`, `ready_for_review` AND `closed`. Dropping `opened`
  reopens the gap it closes: a PR opened straight to non-draft never emits
  `ready_for_review`, so nothing would ever catch it.
- the job's `if:` gates on `github.event.pull_request.draft`, with `closed`
  exempt. That gate, not the trigger list, is what keeps drafts out — `opened`
  fires for a draft too, and a draft PR is not `needs_review`.
- it declares `issues: write`, without which every write would 403.
"""

import importlib.util
import io
import contextlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-issue-pr-sync.py"
WORKFLOW = ROOT / ".github" / "workflows" / "gh-issue-pr-sync.yml"

_spec = importlib.util.spec_from_file_location("gh_issue_pr_sync", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
pr_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_sync)


class FakeRemote:
    """One issue, plus a log of every `gh` call made against it."""

    def __init__(self, labels=(), state="OPEN"):
        self.labels = list(labels)
        self.state = state
        self.calls = []

    def run_gh(self, args, stdin=None):
        self.calls.append((args, stdin))
        if args[:2] == ["issue", "view"]:
            payload = {
                "labels": [{"name": name} for name in self.labels],
                "state": self.state,
            }
            return 0, json.dumps(payload), ""
        if args[:3] == ["api", "--method", "PATCH"]:
            self.labels = json.loads(stdin)["labels"]
            return 0, "{}", ""
        raise AssertionError(f"unexpected gh call: {args}")

    def patches(self):
        return [
            json.loads(stdin)
            for args, stdin in self.calls
            if args[:3] == ["api", "--method", "PATCH"]
        ]


def run(remote, argv):
    """Invoke the CLI against `remote`, returning (exit code, parsed JSON)."""
    original = pr_sync.gh_issue_state.run_gh
    pr_sync.gh_issue_state.run_gh = remote.run_gh
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = pr_sync.main([*argv, "--json"])
    finally:
        pr_sync.gh_issue_state.run_gh = original
    return code, json.loads(out.getvalue())


READY = ["status:3_started", "auto:eligible", "prio:1", "est:3"]
IN_REVIEW = ["status:4_needs_review", "auto:eligible", "prio:1", "est:3"]


class FakeMergedRemote:
    """Several issues plus a PR's closing references — the merged branch's world.

    `issues` maps number -> (label list, state). `closing` is what
    `closingIssuesReferences` returns, as (number, owner, repo name) triples, so
    a test can put a reference in ANOTHER repository and check it is dropped.
    """

    def __init__(self, issues=None, closing=()):
        self.issues = {n: (list(ls), st) for n, (ls, st) in (issues or {}).items()}
        self.closing = list(closing)
        self.calls = []

    def run_gh(self, args, stdin=None):
        self.calls.append((args, stdin))
        if args[:2] == ["pr", "view"]:
            refs = [
                {
                    "number": number,
                    "repository": {"name": name, "owner": {"login": owner}},
                }
                for number, owner, name in self.closing
            ]
            return 0, json.dumps({"closingIssuesReferences": refs}), ""
        if args[:2] == ["issue", "view"]:
            labels, state = self.issues[int(args[2])]
            payload = {
                "labels": [{"name": name} for name in labels],
                "state": state,
            }
            return 0, json.dumps(payload), ""
        if args[:3] == ["api", "--method", "PATCH"]:
            number = int(next(a for a in args if "/issues/" in a).rsplit("/", 1)[1])
            labels, state = self.issues[number]
            self.issues[number] = (json.loads(stdin)["labels"], state)
            return 0, "{}", ""
        raise AssertionError(f"unexpected gh call: {args}")

    def labels(self, number):
        return self.issues[number][0]

    def patches(self):
        return [
            json.loads(stdin)
            for args, stdin in self.calls
            if args[:3] == ["api", "--method", "PATCH"]
        ]


class ForwardTransitionTests(unittest.TestCase):
    def test_ready_for_review_moves_started_to_needs_review(self):
        remote = FakeRemote(READY)
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "bestdan/task-142",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertIsNone(result["skipped"])
        self.assertEqual(result["issue"], 142)
        self.assertIn("status:4_needs_review", remote.labels)
        self.assertNotIn("status:3_started", remote.labels)

    def test_opened_makes_the_same_move_as_ready_for_review(self):
        # A PR opened straight to non-draft never emits `ready_for_review`, so
        # `opened` has to reach the identical transition or that PR is uncaught.
        # The draft case never gets here — the workflow's `if:` filters it.
        remote = FakeRemote(READY)
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "bestdan/task-142",
                "--event",
                "opened",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertIsNone(result["skipped"])
        self.assertEqual(result["target"], "status:4_needs_review")
        self.assertIn("status:4_needs_review", remote.labels)

    def test_opened_on_an_issue_already_in_review_is_a_no_op(self):
        # The rung gate is what keeps the two routes from fighting: whichever
        # event arrives second finds the issue already moved and does nothing.
        remote = FakeRemote(IN_REVIEW)
        code, result = run(
            remote,
            ["--repo", "o/n", "--branch", "task-9", "--event", "opened", "--apply"],
        )
        self.assertEqual(code, 0)
        self.assertIsNotNone(result["skipped"])
        self.assertEqual(remote.patches(), [])

    def test_both_branch_prefixes_reach_the_write(self):
        for branch in ("bestdan/task-142", "claude/task-142", "task-142"):
            remote = FakeRemote(READY)
            code, result = run(
                remote,
                [
                    "--repo",
                    "o/n",
                    "--branch",
                    branch,
                    "--event",
                    "ready_for_review",
                    "--apply",
                ],
            )
            self.assertEqual(code, 0, branch)
            self.assertEqual(result["issue"], 142, branch)
            self.assertIn("status:4_needs_review", remote.labels, branch)

    def test_the_other_rungs_and_unmanaged_labels_survive(self):
        # The write replaces the issue's ENTIRE label set, so anything not
        # echoed back is deleted. `follow-up` is the handler's own marker and
        # /archive-tasks refuses to sweep without it.
        remote = FakeRemote([*READY, "follow-up", "bug"])
        run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "task-9",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        for label in ("auto:eligible", "prio:1", "est:3", "follow-up", "bug"):
            self.assertIn(label, remote.labels)

    def test_labels_the_write_purges_are_named_not_silently_deleted(self):
        # The full-set write is right to purge a `prio:urgent` a human invented,
        # but unattended an Actions log is the only place anyone could see it go.
        remote = FakeRemote([*READY, "prio:urgent", "est:99"])
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "task-9",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertEqual(sorted(result["dropped"]), ["est:99", "prio:urgent"])
        for label in ("prio:urgent", "est:99"):
            self.assertNotIn(label, remote.labels)

    def test_write_is_one_patch_carrying_the_complete_set(self):
        remote = FakeRemote(READY)
        run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "task-9",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        patches = remote.patches()
        self.assertEqual(len(patches), 1)
        self.assertEqual(
            sorted(patches[0]["labels"]),
            sorted(["status:4_needs_review", "auto:eligible", "prio:1", "est:3"]),
        )
        for args, _stdin in remote.calls:
            self.assertNotIn("--add-label", args)
            self.assertNotIn("--remove-label", args)

    def test_without_apply_it_reads_but_never_writes(self):
        remote = FakeRemote(READY)
        code, result = run(
            remote,
            ["--repo", "o/n", "--branch", "task-9", "--event", "ready_for_review"],
        )
        self.assertEqual(code, 0)
        self.assertFalse(result["applied"])
        self.assertEqual(remote.patches(), [])
        self.assertEqual(remote.labels, READY)


class ReverseTransitionTests(unittest.TestCase):
    def test_closed_unmerged_pr_returns_the_issue_to_started(self):
        remote = FakeRemote(IN_REVIEW)
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "bestdan/task-142",
                "--event",
                "closed",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertIsNone(result["skipped"])
        self.assertIn("status:3_started", remote.labels)
        self.assertNotIn("status:4_needs_review", remote.labels)


class MergedPRTests(unittest.TestCase):
    """A merged PR STRIPS the rungs from the issues it closed.

    This replaces an earlier test asserting the merged case made no request at
    all. That premise was the defect (#608): GitHub's auto-close flips the state
    and leaves every label, so "no request" left the issue violating labels.yml
    with a stale `auto:eligible` on finished work. The rule that survives is
    narrower — a merged PR must never WRITE a rung — and stripping is its
    opposite, not an exception to it.
    """

    def _run(self, remote, pr="612", apply=True):
        argv = [
            "--repo",
            "o/n",
            "--branch",
            "bestdan/task-142",
            "--event",
            "closed",
            "--merged",
            "--pr",
            pr,
        ]
        if apply:
            argv.append("--apply")
        return run(remote, argv)

    def test_strips_both_rungs_and_keeps_prio_and_est(self):
        remote = FakeMergedRemote(
            issues={142: (IN_REVIEW, "CLOSED")}, closing=[(142, "o", "n")]
        )
        code, result = self._run(remote)

        self.assertEqual(code, 0)
        self.assertEqual(sorted(remote.labels(142)), ["est:3", "prio:1"])
        outcome = result["merged"][0]
        self.assertEqual(outcome["rungs"], ["status:4_needs_review", "auto:eligible"])
        self.assertTrue(outcome["applied"])

    def test_the_write_never_reopens_the_issue(self):
        remote = FakeMergedRemote(
            issues={142: (IN_REVIEW, "CLOSED")}, closing=[(142, "o", "n")]
        )
        self._run(remote)

        self.assertTrue(remote.patches())
        for payload in remote.patches():
            self.assertNotIn("state", payload)

    def test_unmanaged_labels_ride_through(self):
        remote = FakeMergedRemote(
            issues={142: (IN_REVIEW + ["follow-up"], "CLOSED")},
            closing=[(142, "o", "n")],
        )
        self._run(remote)

        self.assertIn("follow-up", remote.labels(142))

    def test_every_issue_the_pr_closed_is_stripped(self):
        remote = FakeMergedRemote(
            issues={142: (IN_REVIEW, "CLOSED"), 143: (READY, "CLOSED")},
            closing=[(142, "o", "n"), (143, "o", "n")],
        )
        code, result = self._run(remote)

        self.assertEqual(len(result["merged"]), 2)
        self.assertEqual(remote.labels(142), ["prio:1", "est:3"])
        self.assertEqual(remote.labels(143), ["prio:1", "est:3"])

    def test_an_issue_in_another_repo_is_dropped_not_written(self):
        """GITHUB_TOKEN is repo-scoped; writing there fails, and writing to a
        same-numbered local issue instead would be worse."""
        remote = FakeMergedRemote(
            issues={142: (IN_REVIEW, "CLOSED")}, closing=[(142, "other", "repo")]
        )
        code, result = self._run(remote)

        self.assertEqual(code, 0)
        self.assertEqual(result["merged"], [])
        self.assertEqual(remote.patches(), [])

    def test_an_issue_the_merge_left_open_is_a_no_op(self):
        """A human reopened it, or the reference resolved without a close.
        Stripping a live issue's rungs would retire work still moving."""
        remote = FakeMergedRemote(
            issues={142: (IN_REVIEW, "OPEN")}, closing=[(142, "o", "n")]
        )
        code, result = self._run(remote)

        self.assertIn("open", result["merged"][0]["skipped"])
        self.assertEqual(remote.patches(), [])
        self.assertEqual(remote.labels(142), IN_REVIEW)

    def test_an_already_clean_issue_is_a_no_op(self):
        """Row 4's sweep, or a rerun, got here first."""
        remote = FakeMergedRemote(
            issues={142: (["prio:1"], "CLOSED")}, closing=[(142, "o", "n")]
        )
        code, result = self._run(remote)

        self.assertIn("no rung", result["merged"][0]["skipped"])
        self.assertEqual(remote.patches(), [])

    def test_refuses_when_the_rung_free_set_would_still_be_illegal(self):
        remote = FakeMergedRemote(
            issues={142: (["auto:eligible", "prio:1", "prio:2"], "CLOSED")},
            closing=[(142, "o", "n")],
        )
        code, result = self._run(remote)

        # Reported, never fatal — the PR's other issues are still worth
        # stripping and row 4 will re-report this one.
        self.assertEqual(code, 0)
        self.assertIn("prio", result["merged"][0]["refused"])
        self.assertEqual(remote.patches(), [])

    def test_a_pr_that_closed_nothing_makes_no_issue_request(self):
        remote = FakeMergedRemote(closing=[])
        code, result = self._run(remote)

        self.assertEqual(code, 0)
        self.assertEqual(result["merged"], [])
        self.assertEqual(remote.patches(), [])

    def test_without_apply_it_reads_but_never_writes(self):
        remote = FakeMergedRemote(
            issues={142: (IN_REVIEW, "CLOSED")}, closing=[(142, "o", "n")]
        )
        code, result = self._run(remote, apply=False)

        self.assertFalse(result["merged"][0]["applied"])
        self.assertEqual(remote.patches(), [])
        self.assertEqual(remote.labels(142), IN_REVIEW)

    def test_merged_without_pr_is_an_error_not_a_silent_no_op(self):
        remote = FakeMergedRemote()
        original = pr_sync.gh_issue_state.run_gh
        pr_sync.gh_issue_state.run_gh = remote.run_gh
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    pr_sync.main(
                        [
                            "--repo",
                            "o/n",
                            "--branch",
                            "bestdan/task-142",
                            "--event",
                            "closed",
                            "--merged",
                            "--apply",
                        ]
                    )
        finally:
            pr_sync.gh_issue_state.run_gh = original

        # Pin WHICH SystemExit. `closing_issues()` raises a bare one too, so
        # asserting the type alone would let a failed `gh pr view` satisfy a
        # test named for the missing argument.
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--merged requires --pr", stderr.getvalue())

    def test_the_branch_name_does_not_decide_which_issue_is_stripped(self):
        """The branch says 142; the PR actually closed 500. GitHub's reference
        is the one that names what closed."""
        remote = FakeMergedRemote(
            issues={500: (IN_REVIEW, "CLOSED"), 142: (READY, "OPEN")},
            closing=[(500, "o", "n")],
        )
        self._run(remote)

        self.assertEqual(sorted(remote.labels(500)), ["est:3", "prio:1"])
        self.assertEqual(remote.labels(142), READY)


class NoOpGateTests(unittest.TestCase):
    def test_non_task_branch_makes_no_request_at_all(self):
        for branch in ("main", "feature/other", "task-142-fixup", "mytask-142"):
            remote = FakeRemote(READY)
            code, result = run(
                remote,
                [
                    "--repo",
                    "o/n",
                    "--branch",
                    branch,
                    "--event",
                    "ready_for_review",
                    "--apply",
                ],
            )
            self.assertEqual(code, 0, branch)
            self.assertIn("not a task branch", result["skipped"], branch)
            self.assertEqual(remote.calls, [], branch)

    def test_unexpected_current_rung_is_a_no_op_not_a_correction(self):
        # This runs unattended on every PR in the repo, so it must never be the
        # thing that invents a state.
        for rung in ("status:0_untriaged", "status:2_ready", "status:4_needs_review"):
            remote = FakeRemote([rung, "auto:eligible"])
            code, result = run(
                remote,
                [
                    "--repo",
                    "o/n",
                    "--branch",
                    "task-9",
                    "--event",
                    "ready_for_review",
                    "--apply",
                ],
            )
            self.assertEqual(code, 0, rung)
            self.assertIsNotNone(result["skipped"], rung)
            self.assertEqual(remote.patches(), [], rung)

    def test_issue_with_no_status_rung_is_a_no_op(self):
        remote = FakeRemote(["auto:eligible", "prio:1"])
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "task-9",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertIn("no single status: rung", result["skipped"])
        self.assertEqual(remote.patches(), [])

    def test_closed_issue_is_a_no_op(self):
        # gh-issue-state.py refuses an ordinary write against a closed issue,
        # so acting here would paint the Action red on an issue someone closed
        # by hand.
        remote = FakeRemote(IN_REVIEW, state="CLOSED")
        code, result = run(
            remote,
            ["--repo", "o/n", "--branch", "task-9", "--event", "closed", "--apply"],
        )
        self.assertEqual(code, 0)
        self.assertIn("is closed", result["skipped"])
        self.assertEqual(remote.patches(), [])

    def test_missing_auto_rung_refuses_rather_than_writing_a_broken_set(self):
        remote = FakeRemote(["status:3_started", "prio:1"])
        original = pr_sync.gh_issue_state.run_gh
        pr_sync.gh_issue_state.run_gh = remote.run_gh
        err = io.StringIO()
        try:
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = pr_sync.main(
                    [
                        "--repo",
                        "o/n",
                        "--branch",
                        "task-9",
                        "--event",
                        "ready_for_review",
                        "--apply",
                    ]
                )
        finally:
            pr_sync.gh_issue_state.run_gh = original
        self.assertEqual(code, 2)
        self.assertIn("auto", err.getvalue())
        self.assertEqual(remote.patches(), [])


class WorkflowTriggerTests(unittest.TestCase):
    """The workflow's trigger list and permissions are behavior, not config."""

    def setUp(self):
        self.text = WORKFLOW.read_text()

    def trigger_types(self):
        match = re.search(r"^\s*types:\s*\[([^\]]*)\]", self.text, re.MULTILINE)
        if match is None:
            self.fail("no `types: [...]` list in the workflow")
        return [t.strip() for t in match.group(1).split(",") if t.strip()]

    def test_triggers_on_every_event_that_makes_a_pr_reviewable_or_ends_it(self):
        types = self.trigger_types()
        # `opened` and `ready_for_review` are two routes to the same transition:
        # a PR opened straight to non-draft NEVER emits `ready_for_review`, so
        # dropping `opened` reopens the gap this trigger list exists to close.
        self.assertIn("opened", types)
        self.assertIn("ready_for_review", types)
        self.assertIn("closed", types)

    def test_opened_is_gated_on_draft_so_a_draft_pr_is_never_moved(self):
        # This is the term `opened` bought. A draft PR is not needs_review, and
        # the house convention makes some repos' PRs always draft — without the
        # guard, `opened` would move an issue on every draft they open.
        condition = re.search(r"(?ms)^\s+if:.*?\n\n", self.text)
        if condition is None:
            self.fail("no `if:` guard on the job")
        guard = condition.group(0)
        self.assertIn("github.event.pull_request.draft", guard)
        # `closed` must stay exempt: a draft can be closed, and the reverse
        # transition has to run for it.
        self.assertIn("github.event.action == 'closed'", guard)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            guard,
        )

    def test_runs_for_one_pr_are_serialized_against_each_other(self):
        # One PR emits two events in quick succession (ready, then closed), and
        # without serialization the close run reads the rung before the ready
        # run's PATCH lands, no-ops, and leaves the issue in needs_review with a
        # closed PR. Both runs go green, so nothing else would catch this.
        block = re.search(r"(?ms)^concurrency:\n(?:[ \t]+.*\n)+", self.text)
        if block is None:
            self.fail("no workflow-level `concurrency:` block")
        group = block.group(0)
        self.assertIn("github.event.pull_request.number", group)
        # Cancelling would kill a run between its read and its write.
        self.assertRegex(group, r"cancel-in-progress:\s*false")

    def test_declares_the_issues_write_permission_the_patch_needs(self):
        self.assertRegex(self.text, r"(?m)^\s+issues:\s*write\s*$")

    def test_declares_the_pull_requests_read_the_merged_branch_needs(self):
        """`gh pr view --json closingIssuesReferences` has no scope without it.

        Naming any permission sets every unlisted scope to `none`, so this is a
        denial rather than a default — and invisible on a public repo, where a
        restricted token still reads public data.
        """
        self.assertRegex(self.text, r"(?m)^\s+pull-requests:\s*read\s*$")

    def test_runs_the_sync_asset(self):
        self.assertIn("commands/handlers/assets/gh-issue-pr-sync.py", self.text)

    def test_passes_the_pr_number_the_merged_branch_needs(self):
        """Without `--pr` the merged branch exits with an argparse error, and an
        unattended run would paint red on every merge rather than strip."""
        self.assertIn("PR: ${{ github.event.pull_request.number }}", self.text)
        self.assertIn('--pr "$PR"', self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
