"""Hermetic tests for linear-pr-resolve.py.

Loaded via importlib since the asset's filename is hyphenated. The one seam is
`run_gh`, stubbed per test with a scripted queue of (returncode, stdout,
stderr) replies, so every probe, every failure exit and every post-filter runs
with no network and no `gh` binary.

The regression that matters most is `TestPre73Regression`: the 2026-09-02
nightly tidy run reported PRE-73 as "genuinely no PR found yet" while open PR
#1003, titled `Scaffold packages/rest-server FastAPI package (PRE-73)`,
existed -- the post-filter had narrowed to the `[<IDENTIFIER>]` bracket form.
"""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "linear-pr-resolve.py"

_spec = importlib.util.spec_from_file_location("linear_pr_resolve", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
resolve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolve)

PRE73_TITLE = "Scaffold packages/rest-server FastAPI package (PRE-73)"


class GhStub:
    """A scripted `run_gh`. Each call pops the next reply and records its args."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if not self.replies:
            raise AssertionError(f"unscripted gh call: {args}")
        return self.replies.pop(0)


def ok(payload):
    return (0, json.dumps(payload), "")


def fail(msg="boom"):
    return (1, "", msg)


class SeamTestCase(unittest.TestCase):
    def setUp(self):
        self._real_run_gh = resolve.run_gh
        self.addCleanup(setattr, resolve, "run_gh", self._real_run_gh)

    def stub(self, *replies):
        gh = GhStub(replies)
        resolve.run_gh = gh
        return gh

    def row(self, issue, repo="bestdan/finplan", repos=None):
        return resolve.resolve_issue(issue, repos or {}, repo)


# --------------------------------------------------------------- the matcher


class TestIdentifierInTitle(unittest.TestCase):
    def test_the_pre73_title_matches(self):
        """The positive fixture: parentheses, at the end of the title."""
        self.assertTrue(resolve.identifier_in_title("PRE-73", PRE73_TITLE))

    def test_a_trailing_digit_does_not_match(self):
        self.assertFalse(resolve.identifier_in_title("PRE-73", "Do the thing PRE-730"))

    def test_a_trailing_letter_does_not_match(self):
        self.assertFalse(resolve.identifier_in_title("PRE-73", "Do the thing PRE-73a"))

    def test_every_punctuation_shape_matches(self):
        for title in (
            "[PRE-73] scaffold it",
            "(PRE-73) scaffold it",
            "PRE-73: scaffold it",
            "scaffold it PRE-73",
            "PRE-73",
            "scaffold it, PRE-73.",
        ):
            with self.subTest(title=title):
                self.assertTrue(resolve.identifier_in_title("PRE-73", title))

    def test_a_leading_alphanumeric_does_not_match(self):
        self.assertFalse(resolve.identifier_in_title("PRE-73", "XPRE-73"))
        self.assertFalse(resolve.identifier_in_title("PRE-73", "1PRE-73"))

    def test_separate_tokens_do_not_match(self):
        """The false positive the post-filter exists to stop: GitHub search
        tokenizes on punctuation, so `PRE-73 in:title` also returns this."""
        self.assertFalse(resolve.identifier_in_title("PRE-73", "PRE work, item 73"))

    def test_empty_inputs_do_not_match(self):
        self.assertFalse(resolve.identifier_in_title("PRE-73", None))
        self.assertFalse(resolve.identifier_in_title("PRE-73", ""))


# ------------------------------------------------------------ classification


class TestClassifyPrecedence(SeamTestCase):
    """The precedence table, checked in order, each rung shadowing the next."""

    def test_no_prs_is_no_pr(self):
        self.assertEqual(resolve.classify([]), "no_pr")

    def test_merged_wins_over_everything(self):
        prs = [
            {"state": "CLOSED"},
            {"state": None},
            {"state": "OPEN"},
            {"state": "MERGED"},
        ]
        self.assertEqual(resolve.classify(prs), "merged")

    def test_open_wins_over_unread_and_closed(self):
        prs = [{"state": "CLOSED"}, {"state": None}, {"state": "OPEN"}]
        self.assertEqual(resolve.classify(prs), "open")

    def test_an_unread_pr_beats_closed_unmerged(self):
        """Fail-closed: /reconcile-tasks row 3 demotes only closed_unmerged, so
        an unreadable PR must never reach that bucket."""
        prs = [{"state": "CLOSED"}, {"state": None}]
        self.assertEqual(resolve.classify(prs), "unresolved")

    def test_all_closed_unmerged(self):
        prs = [{"state": "CLOSED"}, {"state": "CLOSED"}]
        self.assertEqual(resolve.classify(prs), "closed_unmerged")


# ----------------------------------------------------------- source in turn


class TestAttachmentSource(SeamTestCase):
    def test_a_links_attachment_resolves_with_no_probe(self):
        """Source 1 is authoritative and needs no repo -- the url carries one."""
        gh = self.stub(
            ok(
                {
                    "number": 12,
                    "url": "https://github.com/bestdan/finplan/pull/12",
                    "state": "MERGED",
                    "mergedAt": "2026-09-01T00:00:00Z",
                }
            )
        )
        row = self.row(
            {
                "id": "u1",
                "identifier": "PRE-73",
                "attachments": [
                    {"url": "https://docs.example/spec"},
                    {"url": "https://github.com/bestdan/finplan/pull/12?src=linear"},
                ],
            },
            repo=None,
        )
        self.assertEqual(row["resolved_via"], "attachment")
        self.assertEqual(row["state"], "merged")
        self.assertEqual(len(gh.calls), 1)
        self.assertEqual(gh.calls[0][:2], ["pr", "view"])

    def test_a_non_pr_attachment_does_not_resolve(self):
        gh = self.stub(ok([]), ok([]))
        row = self.row(
            {
                "id": "u1",
                "identifier": "PRE-73",
                "attachments": [{"url": "https://linear.app/x/issue/PRE-73"}],
                "branchName": "dpegan/pre-73",
            }
        )
        self.assertIsNone(row["resolved_via"])
        self.assertEqual(row["state"], "no_pr")
        self.assertEqual(len(gh.calls), 2)


class TestTitleSource(SeamTestCase):
    def test_the_title_probe_resolves_after_the_post_filter(self):
        gh = self.stub(
            ok(
                [
                    {
                        "number": 1003,
                        "url": "https://github.com/bestdan/finplan/pull/1003",
                        "title": PRE73_TITLE,
                        "state": "OPEN",
                    },
                    {
                        "number": 44,
                        "url": "https://github.com/bestdan/finplan/pull/44",
                        "title": "PRE work, item 73",
                        "state": "MERGED",
                    },
                ]
            ),
            ok(
                {
                    "number": 1003,
                    "url": "https://github.com/bestdan/finplan/pull/1003",
                    "state": "OPEN",
                    "mergedAt": None,
                }
            ),
        )
        row = self.row(
            {"id": "u1", "identifier": "PRE-73", "attachments": []},
        )
        self.assertEqual(row["resolved_via"], "title")
        self.assertEqual([pr["number"] for pr in row["prs"]], [1003])
        self.assertEqual(row["state"], "open")
        self.assertIn("--search", gh.calls[0])
        self.assertIn("PRE-73 in:title", gh.calls[0])

    def test_the_probe_is_scoped_to_the_resolved_repo(self):
        """Without -R the search runs against whatever repo the sweep happens
        to be in, and every cross-repo issue is filed as 'no PR'."""
        gh = self.stub(ok([]), ok([]))
        self.row(
            {
                "id": "u1",
                "identifier": "PRE-73",
                "attachments": [],
                "project": {"id": "p1"},
                "branchName": "b",
            },
            repo="fallback/one",
            repos={"p1": "bestdan/finplan"},
        )
        self.assertIn("bestdan/finplan", gh.calls[0])
        self.assertNotIn("fallback/one", gh.calls[0])


class TestBranchSource(SeamTestCase):
    def test_the_branch_probe_resolves_when_the_title_finds_nothing(self):
        gh = self.stub(
            ok([]),
            ok(
                [
                    {
                        "number": 9,
                        "url": "https://github.com/bestdan/finplan/pull/9",
                        "state": "MERGED",
                    }
                ]
            ),
            ok(
                {
                    "number": 9,
                    "url": "https://github.com/bestdan/finplan/pull/9",
                    "state": "MERGED",
                    "mergedAt": "2026-09-01T00:00:00Z",
                }
            ),
        )
        row = self.row(
            {
                "id": "u1",
                "identifier": "PRE-73",
                "attachments": [],
                "branchName": "dpegan/pre-73-scaffold",
            }
        )
        self.assertEqual(row["resolved_via"], "branch")
        self.assertEqual(row["state"], "merged")
        self.assertIn("dpegan/pre-73-scaffold", gh.calls[1])

    def test_no_branch_name_skips_the_branch_probe(self):
        gh = self.stub(ok([]))
        row = self.row({"id": "u1", "identifier": "PRE-73", "attachments": []})
        self.assertEqual(len(gh.calls), 1)
        self.assertEqual(row["state"], "no_pr")


# -------------------------------------------------------------- failure -> unresolved


class TestProbeFailureIsUnresolved(SeamTestCase):
    def test_a_failed_title_probe_is_unresolved_not_no_pr(self):
        """`gh pr list` prints nothing on a network or auth failure, exactly as
        it does on a genuine no-match. Filing the failure as 'no PR' lets
        /reconcile-tasks row 4 GC an issue whose PR is alive."""
        gh = self.stub(fail("HTTP 502"), ok([]))
        row = self.row(
            {
                "id": "u1",
                "identifier": "PRE-73",
                "attachments": [],
                "branchName": "b",
            }
        )
        self.assertEqual(row["state"], "unresolved")
        self.assertTrue(row["unresolved"])
        self.assertEqual(row["prs"], [])
        self.assertIn("HTTP 502", " ".join(row["probe_errors"]))
        self.assertEqual(len(gh.calls), 2)

    def test_a_failed_branch_probe_is_unresolved(self):
        self.stub(ok([]), fail("rate limited"))
        row = self.row(
            {
                "id": "u1",
                "identifier": "PRE-73",
                "attachments": [],
                "branchName": "b",
            }
        )
        self.assertEqual(row["state"], "unresolved")

    def test_a_failed_merge_state_read_is_unresolved(self):
        self.stub(fail("gone"))
        row = self.row(
            {
                "id": "u1",
                "identifier": "PRE-73",
                "attachments": [{"url": "https://github.com/bestdan/finplan/pull/12"}],
            },
            repo=None,
        )
        self.assertEqual(row["state"], "unresolved")
        self.assertIsNone(row["prs"][0]["state"])

    def test_a_probe_failure_does_not_shadow_a_merged_pr(self):
        """Positive evidence still wins: a later source found a merged PR, and
        a merged read is not made less true by an earlier probe erroring."""
        self.stub(
            fail("HTTP 502"),
            ok(
                [
                    {
                        "number": 9,
                        "url": "https://github.com/bestdan/finplan/pull/9",
                        "state": "MERGED",
                    }
                ]
            ),
            ok(
                {
                    "number": 9,
                    "url": "https://github.com/bestdan/finplan/pull/9",
                    "state": "MERGED",
                    "mergedAt": "2026-09-01T00:00:00Z",
                }
            ),
        )
        row = self.row(
            {
                "id": "u1",
                "identifier": "PRE-73",
                "attachments": [],
                "branchName": "b",
            }
        )
        self.assertEqual(row["state"], "merged")

    def test_a_probe_failure_blocks_the_closed_unmerged_demote(self):
        """The title probe erred and the branch probe found only a closed PR.
        Demoting on that would act on evidence the failed probe may have hidden,
        so the row is unresolved -- and /reconcile-tasks row 3 never sees it."""
        closed = {
            "number": 4,
            "url": "https://github.com/bestdan/finplan/pull/4",
            "state": "CLOSED",
        }
        self.stub(fail("HTTP 502"), ok([closed]), ok({**closed, "mergedAt": None}))
        row = self.row(
            {
                "id": "u1",
                "identifier": "PRE-73",
                "attachments": [],
                "branchName": "b",
            }
        )
        self.assertEqual(resolve.classify(row["prs"]), "closed_unmerged")
        self.assertEqual(row["state"], "unresolved")

    def test_a_clean_run_of_closed_prs_stays_closed_unmerged(self):
        """The complement: with no probe error the demote bucket is reachable."""
        closed = {
            "number": 4,
            "url": "https://github.com/bestdan/finplan/pull/4",
            "title": "[PRE-73] old attempt",
            "state": "CLOSED",
        }
        self.stub(ok([closed]), ok({**closed, "mergedAt": None}))
        row = self.row({"id": "u1", "identifier": "PRE-73", "attachments": []})
        self.assertEqual(row["state"], "closed_unmerged")
        self.assertEqual(row["probe_errors"], [])

    def test_no_repo_at_all_is_unresolved(self):
        """Steps 2-3 search one repo. With none resolvable there is nothing to
        search, and that is a missing capability, not a confirmed absence."""
        gh = self.stub()
        row = self.row(
            {"id": "u1", "identifier": "PRE-73", "attachments": []}, repo=None
        )
        self.assertEqual(row["state"], "unresolved")
        self.assertEqual(gh.calls, [])


# ------------------------------------------------------------- the regression


class TestPre73Regression(SeamTestCase):
    """The 2026-09-02 nightly run, replayed."""

    def test_the_open_pr_is_found_rather_than_filed_as_no_pr(self):
        self.stub(
            ok(
                [
                    {
                        "number": 1003,
                        "url": "https://github.com/bestdan/finplan/pull/1003",
                        "title": PRE73_TITLE,
                        "state": "OPEN",
                    }
                ]
            ),
            ok(
                {
                    "number": 1003,
                    "url": "https://github.com/bestdan/finplan/pull/1003",
                    "state": "OPEN",
                    "mergedAt": None,
                }
            ),
        )
        row = self.row({"id": "u1", "identifier": "PRE-73", "attachments": []})
        self.assertEqual(row["resolved_via"], "title")
        self.assertEqual(row["state"], "open")
        self.assertNotEqual(row["state"], "no_pr")

    def test_a_bracket_only_match_would_have_dropped_it(self):
        """Pins the defect itself: the narrowed rule the run actually applied
        rejects the very title the search returned, which is how an open PR
        became 'genuinely no PR found yet'."""
        import re

        bracket_only = re.compile(r"\[PRE-73\]")
        self.assertIsNone(bracket_only.search(PRE73_TITLE))
        self.assertTrue(resolve.identifier_in_title("PRE-73", PRE73_TITLE))


# -------------------------------------------------------------------- config


CONFIG = """\
handler: linear
wip_limit: 3
linear:
  team: PreThink
  projects:
    - id: ebbc284b-a1c1-4cb3-96e0-914e210a79a2
      name: Handler parity follow-ups
      repo: bestdan/workflow-skills  # the repo whose merged PRs own this project
    - id: 9f3a0b1c-0000-0000-0000-000000000000
  global_wip_limit: 6
archive_after: 30
"""


class TestConfig(unittest.TestCase):
    def parse(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
            fh.write(text)
            path = fh.name
        self.addCleanup(Path(path).unlink)
        return resolve.load_project_repos(path)

    def test_it_reads_only_projects_carrying_a_repo(self):
        repos = self.parse(CONFIG)
        self.assertEqual(
            repos,
            {"ebbc284b-a1c1-4cb3-96e0-914e210a79a2": "bestdan/workflow-skills"},
        )

    def test_a_gh_issue_block_named_projects_is_not_read(self):
        """Only `linear.projects` is parsed; a same-named key elsewhere is not."""
        repos = self.parse(
            "gh-issue:\n  projects:\n    - id: x\n      repo: other/one\n" + CONFIG
        )
        self.assertNotIn("x", repos)

    def test_a_project_with_no_id_is_loud(self):
        with self.assertRaises(ValueError):
            self.parse("linear:\n  projects:\n    - repo: bestdan/finplan\n")

    def test_a_stray_line_under_projects_is_loud(self):
        with self.assertRaises(ValueError):
            self.parse("linear:\n  projects:\n    not-a-list-item\n")

    def test_an_absent_projects_block_is_empty_not_an_error(self):
        self.assertEqual(self.parse("handler: linear\nlinear:\n  team: X\n"), {})


# ------------------------------------------------------------------ end to end


class TestMain(SeamTestCase):
    def run_main(self, issues, argv):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(issues))
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                code = resolve.main(argv)
            return code, json.loads(out.getvalue())
        finally:
            sys.stdin = old_stdin

    def test_one_row_per_issue_in_input_order(self):
        self.stub(
            ok(
                {
                    "number": 12,
                    "url": "https://github.com/bestdan/finplan/pull/12",
                    "state": "MERGED",
                    "mergedAt": "2026-09-01T00:00:00Z",
                }
            ),
            ok([]),
        )
        code, rows = self.run_main(
            [
                {
                    "id": "u1",
                    "identifier": "PRE-73",
                    "attachments": [
                        {"url": "https://github.com/bestdan/finplan/pull/12"}
                    ],
                },
                {"id": "u2", "identifier": "PRE-74", "attachments": []},
            ],
            ["--repo", "bestdan/finplan"],
        )
        self.assertEqual(code, 0)
        self.assertEqual([r["identifier"] for r in rows], ["PRE-73", "PRE-74"])
        self.assertEqual(rows[0]["state"], "merged")
        self.assertEqual(rows[1]["state"], "no_pr")

    def test_a_missing_identifier_stops_the_run(self):
        with self.assertRaises(SystemExit):
            self.run_main([{"id": "u1"}], ["--repo", "x/y"])

    def test_a_non_array_stdin_stops_the_run(self):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO('{"not": "an array"}')
        try:
            with self.assertRaises(SystemExit):
                resolve.main(["--repo", "x/y"])
        finally:
            sys.stdin = old_stdin


if __name__ == "__main__":
    unittest.main(verbosity=2)
