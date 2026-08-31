#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-issue-ready.py.

Stubs the module's run_gh() seam so nothing shells out to `gh` or touches the
network. Covers the readiness rule (a `status:2_ready` issue is ready iff none
of its `blocked_by` dependencies are still open), that the open blockers get
named, that the script never issues a mutating call, and that the ready label
comes from labels.yml rather than a literal in the script.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-issue-ready.py"
LABELS_FILE = ROOT / "commands" / "handlers" / "assets" / "labels.yml"

_spec = importlib.util.spec_from_file_location("gh_issue_ready", ASSET)
gh_issue_ready = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gh_issue_ready)


class FakeRepo:
    """A repo's ready-labeled issues and their dependency graphs.

    `issues` maps issue number -> title. `blocked_by` maps issue number -> list
    of (number, state) tuples describing its blockers. `page_size` splits each
    blocker list into pages, so a test can put an open blocker beyond page one —
    the shape `gh api --paginate --slurp` returns.
    """

    def __init__(self, issues, blocked_by=None, page_size=100):
        self.issues = dict(issues)
        self.blocked_by = dict(blocked_by or {})
        self.page_size = page_size
        self.calls = []

    def run_gh(self, args):
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            payload = [
                {"number": n, "title": t, "labels": [{"name": "status:2_ready"}]}
                for n, t in self.issues.items()
            ]
            return 0, json.dumps(payload), ""
        if args[0] == "api":
            path = next(a for a in args if "/issues/" in a)
            issue = int(path.split("/issues/")[1].split("/")[0])
            blockers = self.blocked_by.get(issue, [])
            entries = [{"number": n, "state": s} for n, s in blockers]
            pages = [
                entries[i : i + self.page_size]
                for i in range(0, max(len(entries), 1), self.page_size)
            ]
            return 0, json.dumps(pages), ""
        raise AssertionError(f"unexpected gh call: {args}")

    def mutating_calls(self):
        return [
            c
            for c in self.calls
            if "--method" in c or (len(c) >= 2 and c[:2] == ["issue", "edit"])
        ]


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self._orig_run_gh = gh_issue_ready.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_issue_ready.run_gh = self._orig_run_gh

    def _compute(self, repo, limit=50):
        gh_issue_ready.run_gh = repo.run_gh
        return gh_issue_ready.compute(
            repo="owner/name", labels_file=LABELS_FILE, limit=limit
        )

    def test_issue_with_no_blockers_is_ready(self):
        repo = FakeRepo({1: "no deps"})
        result = self._compute(repo)

        self.assertEqual([i["number"] for i in result["ready"]], [1])
        self.assertEqual(result["blocked"], [])

    def test_issue_whose_every_blocker_is_closed_is_ready(self):
        repo = FakeRepo(
            {1: "all closed"}, blocked_by={1: [(10, "closed"), (11, "closed")]}
        )
        result = self._compute(repo)

        self.assertEqual([i["number"] for i in result["ready"]], [1])
        self.assertEqual(result["blocked"], [])

    def test_issue_with_one_open_blocker_is_not_ready_and_it_is_named(self):
        repo = FakeRepo({1: "still blocked"}, blocked_by={1: [(99, "open")]})
        result = self._compute(repo)

        self.assertEqual(result["ready"], [])
        self.assertEqual(len(result["blocked"]), 1)
        blocked = result["blocked"][0]
        self.assertEqual(blocked["number"], 1)
        self.assertEqual(blocked["open_blockers"], [99])

    def test_issue_with_a_mix_of_open_and_closed_blockers_names_only_the_open_one(self):
        repo = FakeRepo({1: "mixed"}, blocked_by={1: [(10, "closed"), (99, "open")]})
        result = self._compute(repo)

        self.assertEqual(result["ready"], [])
        self.assertEqual(result["blocked"][0]["open_blockers"], [99])

    def test_never_issues_a_mutating_call(self):
        repo = FakeRepo({1: "ready", 2: "blocked"}, blocked_by={2: [(99, "open")]})
        self._compute(repo)

        self.assertEqual(repo.mutating_calls(), [])
        for args in repo.calls:
            self.assertNotIn("--method", args)
            self.assertNotEqual(args[:2], ["issue", "edit"])

    def _bad_vocabulary(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "labels.yml"
        path.write_text(text)
        return path

    def test_a_renamed_ready_value_fails_loudly_rather_than_matching_nothing(self):
        bad = self._bad_vocabulary(
            "status: [0_untriaged, 1_needs_refinement, 2_reddy, 3_started, "
            "4_needs_review]\n"
            "auto: [eligible, human-review-needed]\n"
            "prio: [0, 1, 2, 3]\n"
            "est: [1, 2, 3, 5, 8, 13]\n"
            "colors:\n  status: 1d76db\n  auto: 0e8a16\n  prio: d93f0b\n  est: 5319e2\n"
        )
        repo = FakeRepo({1: "irrelevant"})
        gh_issue_ready.run_gh = repo.run_gh
        with self.assertRaises(gh_issue_ready.VocabularyError) as ctx:
            gh_issue_ready.compute(repo="owner/name", labels_file=bad, limit=50)
        self.assertIn("2_ready", str(ctx.exception))
        self.assertEqual(repo.calls, [], "must fail before any network call")


class MainTests(unittest.TestCase):
    def setUp(self):
        self._orig_run_gh = gh_issue_ready.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_issue_ready.run_gh = self._orig_run_gh

    def test_json_output_reports_ready_and_blocked(self):
        repo = FakeRepo({1: "ready", 2: "blocked"}, blocked_by={2: [(99, "open")]})
        gh_issue_ready.run_gh = repo.run_gh
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gh_issue_ready.main(
                ["--repo", "owner/name", "--labels-file", str(LABELS_FILE), "--json"]
            )
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertEqual([i["number"] for i in result["ready"]], [1])
        self.assertEqual(result["blocked"][0]["open_blockers"], [99])


class ScopeAndPagingTests(unittest.TestCase):
    def setUp(self):
        self._orig_run_gh = gh_issue_ready.run_gh
        self.addCleanup(setattr, gh_issue_ready, "run_gh", self._orig_run_gh)

    def test_an_open_blocker_past_the_first_page_is_still_seen(self):
        """A single-page read would call this issue ready. It is not.

        The endpoint is a REST list, so without --paginate only the first page
        comes back and an open blocker after it vanishes — a silent wrong
        "ready", which is the one answer this script must never give.
        """
        blockers = [(900 + i, "closed") for i in range(3)] + [(999, "open")]
        repo = FakeRepo({7: "seven"}, {7: blockers}, page_size=2)
        gh_issue_ready.run_gh = repo.run_gh

        result = gh_issue_ready.compute("o/n", LABELS_FILE, 50)

        self.assertEqual(result["ready"], [])
        self.assertEqual(result["blocked"][0]["open_blockers"], [999])
        # And the call really did ask for every page.
        api_call = next(c for c in repo.calls if c[0] == "api")
        self.assertIn("--paginate", api_call)
        self.assertIn("--slurp", api_call)

    def test_scope_labels_narrow_the_candidate_query(self):
        """The helper's window must be drawn over the caller's scope.

        Its --limit is applied by the API, so a repo-wide window can omit an
        in-scope issue entirely — and a missing verdict is indistinguishable
        from a ready one, so the caller cannot detect the omission.
        """
        repo = FakeRepo({1: "one"})
        gh_issue_ready.run_gh = repo.run_gh

        gh_issue_ready.compute("o/n", LABELS_FILE, 50, ["follow-up", "task-loop"])

        listing = next(c for c in repo.calls if c[:2] == ["issue", "list"])
        self.assertEqual(listing.count("--label"), 3)  # ready label + two scopes
        self.assertIn("follow-up", listing)
        self.assertIn("task-loop", listing)


if __name__ == "__main__":
    unittest.main()
