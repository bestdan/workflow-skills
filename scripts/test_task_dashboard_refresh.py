#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/refresh.py: one item's refresh,
the server's POST /refresh, the card's button and script, and the CLI's
`refresh` command.

The fixture DB is fleet_tabs' seed, written through the real writers. Every
gh call goes through a fake runner that answers the GraphQL read with a
canned item (and the review search and `gh pr view` when asked), so nothing
reaches the network; the server runs in process with a fake sync function.
Needs Python 3.11: run via scripts/test-task-dashboard-refresh.sh.

PageScriptTests runs the page's script under jsdom, through
test_task_dashboard_refresh_dom.js. The repo vendors no npm packages, so they
run only with TASK_DASHBOARD_JSDOM naming a node_modules that holds jsdom
(and node on PATH, or TASK_DASHBOARD_NODE), and skip otherwise.
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import agent_view  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_tabs  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import github_gh  # type: ignore[import-not-found]  # noqa: E402
import needs  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import refresh  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_fleet_tabs as tabs_fx  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

NOW = tabs_fx.NOW
REPO = tabs_fx.REPO
VIEWER = "bestdan"
HUMAN = github_fleet.HUMAN_LABEL
pr_url, issue_url = tabs_fx.pr_url, tabs_fx.issue_url


def sha(n):
    return f"{n:040x}"


def check(conclusion):
    return {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": conclusion}


def pr_node(
    n,
    *,
    state="OPEN",
    draft=False,
    decision=None,
    author=VIEWER,
    ci="SUCCESS",
    stamped=True,
    requested=(),
    teams=(),
    title=None,
    checks=None,
    more_checks=False,
    url=None,
):
    comments = (
        [
            {
                "body": f"<!-- co-review-stamp v1 head={sha(n)} -->",
                "createdAt": "2026-10-25T10:00:00Z",
                "author": {"login": VIEWER},
            }
        ]
        if stamped
        else []
    )
    return {
        "__typename": "PullRequest",
        "url": url or pr_url(n),
        "number": n,
        "title": title or f"pr {n}",
        "body": "",
        "state": state,
        "isDraft": draft,
        "reviewDecision": decision,
        "headRefName": f"bestdan/task-{n}",
        "headRefOid": sha(n),
        "createdAt": "2026-10-01T00:00:00Z",
        "updatedAt": "2026-10-25T10:00:00Z",
        "closedAt": "2026-10-25T11:00:00Z" if state != "OPEN" else None,
        "mergedAt": "2026-10-25T11:00:00Z" if state == "MERGED" else None,
        "author": {"login": author, "__typename": "User"},
        "reviewRequests": {
            "nodes": [
                {"requestedReviewer": {"__typename": "User", "login": who}}
                for who in requested
            ]
            + [{"requestedReviewer": {"__typename": "Team"}} for _ in teams]
        },
        "closingIssuesReferences": {"nodes": []},
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "statusCheckRollup": {
                            "contexts": {
                                "pageInfo": {"hasNextPage": more_checks},
                                "nodes": checks if checks is not None else [check(ci)],
                            }
                        }
                    }
                }
            ]
        },
        "comments": {"nodes": comments},
    }


def issue_node(n, *labels, state="OPEN"):
    return {
        "__typename": "Issue",
        "url": issue_url(n),
        "number": n,
        "title": f"issue {n}",
        "state": state,
        "createdAt": "2026-10-20T00:00:00Z",
        "updatedAt": "2026-10-25T10:00:00Z",
        "labels": {"nodes": [{"name": name} for name in labels]},
    }


def answer(node, *, archived=False, name=REPO):
    return json.dumps(
        {
            "data": {
                "viewer": {"login": VIEWER},
                "repository": {
                    "nameWithOwner": name,
                    "isArchived": archived,
                    "issueOrPullRequest": node,
                },
            }
        }
    )


def arg(argv, name):
    prefix = f"{name}="
    return next((a[len(prefix) :] for a in argv if a.startswith(prefix)), None)


class FakeGh:
    """Answers the refresh's GraphQL read by item number, a search by its query
    (`searches`: query -> URLs) and `gh pr view` by URL (`rollups`: URL ->
    statusCheckRollup list); records each call."""

    def __init__(self, by_number=None, fail=None, searches=None, rollups=None):
        self.by_number = dict(by_number or {})
        self.fail = fail
        self.searches = dict(searches or {})
        self.rollups = dict(rollups or {})
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if self.fail:
            return self.fail
        if argv[1:3] == ["pr", "view"]:
            return 0, json.dumps({"statusCheckRollup": self.rollups[argv[3]]}), ""
        query = arg(argv, "q")
        if query is not None:
            nodes = [{"url": u} for u in self.searches.get(query, [])]
            page = {"pageInfo": {"hasNextPage": False, "endCursor": None}}
            return 0, json.dumps({"data": {"search": {**page, "nodes": nodes}}}), ""
        n = int(arg(argv, "number"))
        if n not in self.by_number:
            return (
                1,
                "",
                "gh: Could not resolve to an issue or pull request with the"
                f" number of {n}. (repository.issueOrPullRequest)",
            )
        return 0, self.by_number[n], ""


def fleet_cfg():
    cfg = tabs_fx.fleet_cfg()
    cfg["github"]["orgs"] = ["bestdan"]
    return cfg


def lane_counts(doc):
    return {lane["id"]: lane["count"] for lane in doc["lanes"]}


def lane_keys(doc):
    return {lane["id"]: [i["key"] for i in lane["items"]] for lane in doc["lanes"]}


def view_keys(conn, cfg, view):
    doc = agent_view.build(conn, cfg, view, NOW)
    return {i["key"] for lane in doc["lanes"] for i in lane["items"]}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "d.db"
        self.conn = db.connect(self.db_path)
        self.cfg = fleet_cfg()
        tabs_fx.seed(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def refresh(self, ref, runner):
        return refresh.refresh(self.conn, self.cfg, ref, NOW, runner)

    def runs(self):
        return self.conn.execute("SELECT * FROM sync_runs ORDER BY id").fetchall()

    def fleet_row(self, url):
        row = self.conn.execute(
            "SELECT kind, raw_json FROM issues WHERE source = 'github' AND key = ?",
            (url,),
        ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def prs_row(self, url):
        return self.conn.execute(
            "SELECT role, is_draft, ci_state FROM prs WHERE url = ?", (url,)
        ).fetchone()


class ParseRefTests(unittest.TestCase):
    def test_ref_and_urls(self):
        for text in (
            f"{REPO}#4",
            pr_url(4),
            issue_url(4),
            f" {pr_url(4)}/ ",
        ):
            self.assertEqual(refresh.parse_ref(text), (REPO, 4), text)

    def test_junk_is_refused(self):
        for text in (
            "",
            "#4",
            f"{REPO}#0",
            f"{REPO}#4 label:x",
            "https://x/a/b/pull/4",
        ):
            with self.assertRaises(refresh.RefreshError, msg=text):
                refresh.parse_ref(text)


class PullRequestTests(Case):
    def test_ci_going_green_moves_the_card_to_ready_and_changes_the_counts(self):
        before = agent_view.build(self.conn, self.cfg, "prs", NOW)
        self.assertIn(pr_url(4), lane_keys(before)["ci"])
        runs = self.runs()
        fake = FakeGh({4: answer(pr_node(4, ci="SUCCESS"))})
        result = self.refresh(f"{REPO}#4", fake)
        self.assertEqual((result.outcome, result.kind), ("updated", "pr"))
        self.assertEqual(len(fake.calls), 1)  # one gh call, nothing else
        after = agent_view.build(self.conn, self.cfg, "prs", NOW)
        self.assertNotIn(pr_url(4), lane_keys(after)["ci"])
        self.assertIn(pr_url(4), lane_keys(after)["ready"])
        old, new = lane_counts(before), lane_counts(after)
        self.assertEqual(new["ci"], old["ci"] - 1)
        self.assertEqual(new["ready"], old["ready"] + 1)
        # Both rows rewritten as a sync would: the queue verdict and the CI.
        kind, row = self.fleet_row(pr_url(4))
        self.assertEqual(
            (kind, row["in_queue"], row["ci_state"]), ("pr", True, "success")
        )
        self.assertEqual(row["queue_misses"], [])
        self.assertEqual(self.prs_row(pr_url(4)), ("author", 0, "success"))
        # The page's tab counts follow too.
        body = fleet_tabs.render_prs(self.conn, self.cfg, NOW)
        tabbed = server.lane_tabs.tabify(body, "prs", "/prs", {})
        self.assertIn(f'data-lane="ready" data-count="{new["ready"]}"', tabbed)
        # A refresh is not a sync.
        self.assertEqual(self.runs(), runs)

    def test_ci_going_red_leaves_ready(self):
        fake = FakeGh({1: answer(pr_node(1, ci="FAILURE"))})
        self.refresh(pr_url(1), fake)
        keys = lane_keys(agent_view.build(self.conn, self.cfg, "prs", NOW))
        self.assertIn(pr_url(1), keys["ci"])
        self.assertNotIn(pr_url(1), keys["ready"])
        self.assertEqual(self.fleet_row(pr_url(1))[1]["queue_misses"], ["ci"])

    def test_a_merged_pr_leaves_every_view_and_stale_sees_it_merged(self):
        everywhere = [v for v in agent_view.available(self.cfg)]
        self.assertTrue(
            any(pr_url(1) in view_keys(self.conn, self.cfg, v) for v in everywhere)
        )
        runs = self.runs()
        result = self.refresh(
            f"{REPO}#1", FakeGh({1: answer(pr_node(1, state="MERGED"))})
        )
        self.assertEqual((result.outcome, result.removed), ("merged", True))
        for view in everywhere:
            self.assertNotIn(pr_url(1), view_keys(self.conn, self.cfg, view), view)
        self.assertNotIn(pr_url(1), fleet_tabs.render_prs(self.conn, self.cfg, NOW))
        self.assertIsNone(self.prs_row(pr_url(1)))
        # The closed-PR row Stale reads for a landed worktree's merged PR.
        kind, row = self.fleet_row(pr_url(1))
        self.assertEqual(kind, "closed_pr")
        self.assertEqual((row["merged"], row["head_ref"]), (True, "bestdan/task-1"))
        self.assertEqual(self.runs(), runs)

    def test_a_closed_pr_leaves_every_view(self):
        result = self.refresh(
            pr_url(2), FakeGh({2: answer(pr_node(2, state="CLOSED"))})
        )
        self.assertEqual(result.outcome, "closed")
        for view in agent_view.available(self.cfg):
            self.assertNotIn(pr_url(2), view_keys(self.conn, self.cfg, view), view)
        self.assertEqual(self.fleet_row(pr_url(2))[1]["merged"], False)

    def test_someone_elses_closed_pr_leaves_no_closed_row(self):
        node = pr_node(20, state="MERGED", author="azack")
        self.refresh(pr_url(20), FakeGh({20: answer(node)}))
        self.assertIsNone(self.fleet_row(pr_url(20)))

    def test_an_open_pr_in_an_archived_repo_is_dropped(self):
        result = self.refresh(pr_url(3), FakeGh({3: answer(pr_node(3), archived=True)}))
        self.assertEqual(result.outcome, "archived")
        self.assertIsNone(self.fleet_row(pr_url(3)))
        self.assertIsNone(self.prs_row(pr_url(3)))

    def test_a_merged_pr_in_an_archived_repo_still_leaves_merged_evidence(self):
        node = pr_node(1, state="MERGED")
        result = self.refresh(pr_url(1), FakeGh({1: answer(node, archived=True)}))
        self.assertEqual(result.outcome, "merged")  # not "archived"
        self.assertIsNone(self.prs_row(pr_url(1)))
        kind, row = self.fleet_row(pr_url(1))
        self.assertEqual((kind, row["merged"]), ("closed_pr", True))

    def test_a_pr_turned_draft_leaves_the_queue_for_drafts(self):
        self.refresh(pr_url(1), FakeGh({1: answer(pr_node(1, draft=True))}))
        self.assertIsNone(self.fleet_row(pr_url(1)))  # no search returns it
        self.assertEqual(self.prs_row(pr_url(1)), ("author", 1, "success"))
        keys = lane_keys(agent_view.build(self.conn, self.cfg, "prs", NOW))
        self.assertIn(pr_url(1), keys["drafts"])

    def test_an_approved_pr_of_mine_is_not_awaiting(self):
        self.refresh(pr_url(2), FakeGh({2: answer(pr_node(2, decision="APPROVED"))}))
        self.assertIsNone(self.fleet_row(pr_url(2)))
        self.assertEqual(self.prs_row(pr_url(2))[0], "author")

    def test_a_review_request_gone_makes_it_from_others(self):
        node = pr_node(7, author="azack", requested=())
        self.refresh(pr_url(7), FakeGh({7: answer(node)}))
        self.assertIsNone(self.prs_row(pr_url(7)))
        kind, row = self.fleet_row(pr_url(7))
        self.assertEqual(
            (row["role"], row["in_queue"], row["author"]), ("others", False, "azack")
        )
        keys = lane_keys(agent_view.build(self.conn, self.cfg, "prs", NOW))
        self.assertIn(pr_url(7), keys["others"])

    def test_a_review_request_kept_stays_reviewer(self):
        node = pr_node(7, author="azack", requested=(VIEWER,), ci="FAILURE")
        self.refresh(pr_url(7), FakeGh({7: answer(node)}))
        self.assertEqual(self.prs_row(pr_url(7))[0], "reviewer")
        self.assertEqual(self.fleet_row(pr_url(7))[1]["role"], "reviewer")

    def test_a_fresh_team_request_for_me_makes_it_reviewer(self):
        # 20 is in the mirror as someone else's PR, not as asking for me.
        self.assertEqual(self.fleet_row(pr_url(20))[1]["role"], "others")
        query = f"is:pr is:open review-requested:@me repo:{REPO}"
        node = pr_node(20, author="azack", teams=("core",), ci="FAILURE")
        fake = FakeGh({20: answer(node)}, searches={query: [pr_url(3), pr_url(20)]})
        self.refresh(pr_url(20), fake)
        self.assertEqual([arg(c, "q") for c in fake.calls], [None, query])
        self.assertEqual(self.prs_row(pr_url(20))[0], "reviewer")
        self.assertEqual(self.fleet_row(pr_url(20))[1]["role"], "reviewer")
        keys = lane_keys(agent_view.build(self.conn, self.cfg, "prs", NOW))
        self.assertIn(pr_url(20), keys["review"])
        self.assertNotIn(pr_url(20), keys.get("others", []))

    def test_a_team_request_for_a_team_i_am_not_in_is_from_others(self):
        # 7 is in the mirror as asking for my review; the search now says no.
        query = f"is:pr is:open review-requested:@me repo:{REPO}"
        node = pr_node(7, author="azack", teams=("core",))
        fake = FakeGh({7: answer(node)}, searches={query: [pr_url(3)]})
        self.refresh(pr_url(7), fake)
        self.assertEqual(len(fake.calls), 2)
        self.assertIsNone(self.prs_row(pr_url(7)))
        self.assertEqual(self.fleet_row(pr_url(7))[1]["role"], "others")
        keys = lane_keys(agent_view.build(self.conn, self.cfg, "prs", NOW))
        self.assertIn(pr_url(7), keys["others"])

    def test_a_team_request_on_my_own_pr_runs_no_search(self):
        fake = FakeGh({4: answer(pr_node(4, teams=("core",)))})
        self.refresh(pr_url(4), fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.prs_row(pr_url(4))[0], "author")

    def test_no_team_request_runs_no_search(self):
        fake = FakeGh({7: answer(pr_node(7, author="azack", requested=(VIEWER,)))})
        self.refresh(pr_url(7), fake)
        self.assertEqual(len(fake.calls), 1)

    def test_more_than_100_checks_take_the_ci_state_the_sync_reads(self):
        # The first 100 contexts pass; one past them fails.
        rollup = [check("SUCCESS")] * 100 + [check("FAILURE")]
        node = pr_node(4, checks=rollup[:100], more_checks=True)
        fake = FakeGh({4: answer(node)}, rollups={pr_url(4): rollup})
        self.refresh(pr_url(4), fake)
        self.assertEqual(fake.calls[1][:4], ["gh", "pr", "view", pr_url(4)])
        synced = github_gh.ci_state(pr_url(4), fake)
        self.assertEqual(synced, "failure")
        self.assertEqual(self.prs_row(pr_url(4))[2], synced)
        self.assertEqual(self.fleet_row(pr_url(4))[1]["ci_state"], synced)
        keys = lane_keys(agent_view.build(self.conn, self.cfg, "prs", NOW))
        self.assertIn(pr_url(4), keys["ci"])

    def test_100_checks_or_fewer_take_one_call(self):
        node = pr_node(4, checks=[check("SUCCESS")] * 100)
        fake = FakeGh({4: answer(node)})
        self.refresh(pr_url(4), fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.prs_row(pr_url(4))[2], "success")

    def test_a_renamed_repo_leaves_no_row_under_the_old_url(self):
        new = "bestdan/renamed"
        new_url = f"https://github.com/{new}/pull/4"
        self.assertIsNotNone(self.fleet_row(pr_url(4)))
        self.assertIsNotNone(self.prs_row(pr_url(4)))
        node = pr_node(4, url=new_url)
        result = self.refresh(pr_url(4), FakeGh({4: answer(node, name=new)}))
        self.assertEqual(
            (result.outcome, result.repo, result.url), ("updated", new, new_url)
        )
        self.assertIsNone(self.fleet_row(pr_url(4)))
        self.assertIsNone(self.prs_row(pr_url(4)))
        self.assertEqual(self.prs_row(new_url), ("author", 0, "success"))
        self.assertEqual(self.fleet_row(new_url)[1]["repo"], new)
        for view in agent_view.available(self.cfg):
            self.assertNotIn(pr_url(4), view_keys(self.conn, self.cfg, view), view)

    def test_a_repo_transferred_outside_github_orgs_is_dropped(self):
        new = "someone/elsewhere"
        new_url = f"https://github.com/{new}/pull/4"
        node = pr_node(4, url=new_url)
        result = self.refresh(pr_url(4), FakeGh({4: answer(node, name=new)}))
        self.assertEqual((result.outcome, result.removed), ("out_of_scope", True))
        self.assertEqual(
            result.line(), f"moved outside github.orgs (removed): {new}#4 (pr)"
        )
        for url in (pr_url(4), new_url):
            self.assertIsNone(self.fleet_row(url))
            self.assertIsNone(self.prs_row(url))

    def test_not_found_drops_the_rows(self):
        result = self.refresh(pr_url(4), FakeGh({}))
        self.assertEqual((result.outcome, result.kind), ("not_found", None))
        self.assertIsNone(self.fleet_row(pr_url(4)))
        self.assertIsNone(self.prs_row(pr_url(4)))

    def test_github_failing_leaves_the_mirror_unchanged(self):
        before = self.conn.execute("SELECT * FROM issues ORDER BY key").fetchall()
        prs = self.conn.execute("SELECT * FROM prs ORDER BY url").fetchall()
        for fail, err in (
            ((1, "", "HTTP 502: Bad Gateway"), github_gh.GhError),
            ((1, "", "gh auth login"), github_gh.AuthError),
            ((0, "<html>", ""), github_gh.GhError),
        ):
            with self.assertRaises(err):
                self.refresh(pr_url(4), FakeGh(fail=fail))
        self.assertEqual(
            self.conn.execute("SELECT * FROM issues ORDER BY key").fetchall(), before
        )
        self.assertEqual(
            self.conn.execute("SELECT * FROM prs ORDER BY url").fetchall(), prs
        )

    def test_out_of_scope_refs_are_refused_before_any_call(self):
        fake = FakeGh({})
        with self.assertRaises(refresh.RefreshError):
            self.refresh("someone/else#4", fake)
        self.cfg["github"]["enabled"] = False
        with self.assertRaises(refresh.RefreshError):
            self.refresh(f"{REPO}#4", fake)
        self.assertEqual(fake.calls, [])


class IssueTests(Case):
    def test_an_issue_that_no_longer_needs_a_human_leaves_needs_but_stays_on_issues(
        self,
    ):
        self.assertIn(issue_url(10), view_keys(self.conn, self.cfg, "needs"))
        before = lane_keys(agent_view.build(self.conn, self.cfg, "issues", NOW))
        self.assertIn(issue_url(10), before["needs-you"])
        node = issue_node(10, "auto:eligible", "status:2_ready", "prio:1")
        result = self.refresh(issue_url(10), FakeGh({10: answer(node)}))
        self.assertEqual((result.outcome, result.kind), ("updated", "issue"))
        # Gone from Needs you, page and JSON alike...
        self.assertNotIn(issue_url(10), view_keys(self.conn, self.cfg, "needs"))
        page = needs.render(self.conn, self.cfg, NOW)
        self.assertNotIn(issue_url(10), page)
        # ...but still on Issues, in the lane its new labels give it.
        after = lane_keys(agent_view.build(self.conn, self.cfg, "issues", NOW))
        self.assertNotIn(issue_url(10), after["needs-you"])
        self.assertIn(issue_url(10), after["ready"])

    def test_an_issue_newly_held_for_a_human_joins_needs(self):
        node = issue_node(13, HUMAN, "status:2_ready")
        self.refresh(f"{REPO}#13", FakeGh({13: answer(node)}))
        self.assertIn(issue_url(13), view_keys(self.conn, self.cfg, "needs"))
        after = lane_keys(agent_view.build(self.conn, self.cfg, "issues", NOW))
        self.assertIn(issue_url(13), after["needs-you"])

    def test_a_closed_issue_leaves_every_view(self):
        node = issue_node(11, state="CLOSED")
        result = self.refresh(issue_url(11), FakeGh({11: answer(node)}))
        self.assertEqual(result.outcome, "closed")
        for view in agent_view.available(self.cfg):
            self.assertNotIn(issue_url(11), view_keys(self.conn, self.cfg, view), view)
        self.assertIsNone(self.fleet_row(issue_url(11)))

    def test_an_issue_in_an_archived_repo_is_dropped(self):
        node = issue_node(12, "status:3_started")
        result = self.refresh(issue_url(12), FakeGh({12: answer(node, archived=True)}))
        self.assertEqual(result.outcome, "archived")
        self.assertIsNone(self.fleet_row(issue_url(12)))


class CardTests(Case):
    def test_pr_and_issue_cards_carry_the_button(self):
        prs = fleet_tabs.render_prs(self.conn, self.cfg, NOW)
        self.assertIn("data-refresh", prs)
        issues = fleet_tabs.render_issues(self.conn, self.cfg, NOW)
        self.assertIn("data-refresh", issues)
        self.assertTrue(overlay.refreshable(("github", pr_url(4))))
        self.assertTrue(overlay.refreshable(("github", issue_url(4))))
        self.assertFalse(overlay.refreshable(("jira", pr_url(4))))
        design = "https://github.com/bestdan/x/blob/main/dev_docs/designs/a.md"
        self.assertFalse(overlay.refreshable(("github", design)))

    def test_the_script_is_pinned(self):
        js = refresh.JS
        # The button posts the card's key with the page token.
        self.assertIn("ev.target.closest('[data-refresh]')", js)
        self.assertIn("fetch('/refresh', { method: 'POST'", js)
        self.assertIn("'token=' + encodeURIComponent(token)", js)
        self.assertIn("'&key=' + encodeURIComponent(card.dataset.key)", js)
        # A spinner while it waits, cleared on failure with the error shown.
        self.assertIn("card.classList.add('refreshing');", js)
        self.assertIn("card.classList.remove('refreshing');", js)
        self.assertIn("e.textContent = 'Refresh failed: ' + why;", js)
        # Then the page as the server now builds it, with no reload...
        self.assertIn("fetch(location.href, { headers: { Accept: 'text/html' } })", js)
        self.assertIn("new DOMParser().parseFromString(html, 'text/html')", js)
        self.assertIn("if (next) { cur.innerHTML = next.innerHTML; return; }", js)
        # ...a note where the card left its lane...
        self.assertIn("n.className = 'refresh-note';", js)
        self.assertIn("merged: 'merged', closed: 'closed'", js)
        self.assertIn("'moved to ' + lanes.map(label).join(', ')", js)
        # ...and the tab counts from the new page.
        self.assertIn("mine.dataset.count = t.dataset.count;", js)
        # A tab whose lane the new page left out counts what is left here.
        self.assertIn("var n = lane.querySelectorAll('.card').length;", js)
        self.assertIn("window.laneTabs.refresh('')", js)
        self.assertIn("window.cardFilter.apply()", js)
        self.assertIn("window.cardFilter = { apply: apply };", server.JS)
        # The only reload: a lane this page did not draw.
        self.assertEqual(js.count("location.reload()"), 1)
        page = server.render_page("t", "", "", "x", False)
        self.assertIn(refresh.JS, page)
        self.assertIn(refresh.CSS, page)


NODE = os.environ.get("TASK_DASHBOARD_NODE") or shutil.which("node") or ""
# A node_modules directory holding jsdom; the repo vendors no npm packages.
JSDOM = os.environ.get("TASK_DASHBOARD_JSDOM") or ""
DOM_JS = ROOT / "scripts" / "test_task_dashboard_refresh_dom.js"


@unittest.skipUnless(
    NODE and JSDOM and Path(JSDOM, "jsdom").is_dir(),
    "needs node and jsdom: set TASK_DASHBOARD_JSDOM to a node_modules holding it",
)
class PageScriptTests(Case):
    """The page's Refresh script, run under jsdom over the real PRs page."""

    def page(self):
        """GET /prs as the server builds it, with no server bound."""
        syncer = server.Syncer(server_harness.FakeSync(), 60, 60, run_at_start=False)
        app = server.App(self.cfg, self.db_path, "tok", syncer)
        app.now = lambda: NOW
        return app.view_page("/prs")

    def drive(self, **case):
        path = Path(self.tmp.name) / "case.json"
        path.write_text(json.dumps(case))
        out = subprocess.run(
            [NODE, str(DOM_JS), JSDOM, str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        state = json.loads(out.stdout)
        self.assertEqual((state["errors"], state["failed"]), ([], None))
        self.assertFalse(state["reloaded"])
        self.assertEqual([c["method"] for c in state["calls"]], ["POST", "GET"])
        return state

    def go_green(self):
        """PR 2 gets its co-review stamp: it moves from Waiting to Ready for you."""
        before = self.page()
        result = self.refresh(pr_url(2), FakeGh({2: answer(pr_node(2))}))
        return before, self.page(), {"ok": True, **result.as_dict()}

    def heading(self, page, lane):
        m = re.search(rf'data-section="{lane}"[^>]*><h2>([^<]*)</h2>', page)
        assert m, f"no lane {lane}"
        return m.group(1)

    def test_an_open_or_unsaved_note_in_a_sibling_card_survives(self):
        before, after, result = self.go_green()
        self.assertIn(pr_url(3), tabs_fx.keys(tabs_fx.section(before, "waiting")))
        self.assertIn(pr_url(1), tabs_fx.keys(tabs_fx.section(before, "ready")))
        state = self.drive(
            before=before,
            after=after,
            result=result,
            click=pr_url(2),
            open=[pr_url(3)],  # its lane loses the clicked card
            dirty=[pr_url(1)],  # its lane gains it
        )
        for key, opened in ((pr_url(3), True), (pr_url(1), False)):
            edit = state["edits"][key]
            self.assertTrue(edit["same_card"] and edit["kept"], (key, edit))
            self.assertEqual(edit["open"], opened)
        lanes = state["lanes"]
        # Only the refreshed item moved, and both lanes say they may be stale.
        self.assertNotIn(pr_url(2), lanes["waiting"]["keys"])
        self.assertIn(pr_url(2), lanes["ready"]["keys"])
        self.assertEqual(
            lanes["ready"]["keys"], tabs_fx.keys(tabs_fx.section(after, "ready"))
        )
        self.assertTrue(lanes["waiting"]["stale"] and lanes["ready"]["stale"])
        for lane in ("waiting", "ready"):
            self.assertEqual(lanes[lane]["heading"], self.heading(after, lane))
        self.assertIn("moved to", lanes["waiting"]["note"])

    def test_with_no_editor_open_the_lanes_take_the_new_page(self):
        before, after, result = self.go_green()
        state = self.drive(before=before, after=after, result=result, click=pr_url(2))
        for lane in ("waiting", "ready"):
            got = state["lanes"][lane]
            self.assertEqual(got["keys"], tabs_fx.keys(tabs_fx.section(after, lane)))
            self.assertFalse(got["stale"])
        self.assertIn("moved to", state["lanes"]["waiting"]["note"])

    def test_an_editor_in_an_untouched_lane_does_not_stop_the_swap(self):
        before, after, result = self.go_green()
        state = self.drive(
            before=before,
            after=after,
            result=result,
            click=pr_url(2),
            open=[pr_url(20)],
        )
        self.assertTrue(state["edits"][pr_url(20)]["kept"])
        self.assertFalse(state["lanes"]["waiting"]["stale"])
        self.assertFalse(state["lanes"]["ready"]["stale"])

    def test_a_renamed_repo_s_card_is_followed_to_its_new_url(self):
        new_url = "https://github.com/bestdan/renamed/pull/2"
        before = self.page()
        fake = FakeGh({2: answer(pr_node(2, url=new_url), name="bestdan/renamed")})
        result = self.refresh(pr_url(2), fake)
        after = self.page()
        state = self.drive(
            before=before,
            after=after,
            result={"ok": True, **result.as_dict()},
            click=pr_url(2),
            open=[pr_url(3)],
        )
        lanes = state["lanes"]
        self.assertNotIn(pr_url(2), lanes["waiting"]["keys"])
        self.assertIn(new_url, lanes["ready"]["keys"])
        self.assertIn("moved to", lanes["waiting"]["note"])
        self.assertTrue(state["edits"][pr_url(3)]["kept"])


class ServerTests(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        app = self.httpd.app
        app.now = lambda: NOW
        app.cfg["github"]["orgs"] = ["bestdan"]
        app.cfg["jira"]["enabled"] = False
        self.gh = FakeGh({4: answer(pr_node(4))})
        app.gh_runner = self.gh
        conn = db.connect(self.db_path)
        tabs_fx.seed(conn)
        conn.close()

    def post(self, path, body, origin=None):
        origin = origin if origin is not None else f"http://127.0.0.1:{self.port}"
        return self.request("POST", path, origin=origin, body=body)

    def test_refresh_without_the_token_is_refused_as_overlay_is(self):
        for token in (None, "wrong"):
            body = {"key": pr_url(4)}
            if token:
                body["token"] = token
            refused = self.post("/refresh", body)
            self.assertEqual(
                refused, self.post("/overlay/pin", {**body, "source": "github"})
            )
            self.assertEqual(refused[0], 403)
        foreign = self.post(
            "/refresh",
            {"key": pr_url(4), "token": self.token},
            origin="http://evil.example",
        )
        self.assertEqual(foreign[0], 403)
        self.assertEqual(self.gh.calls, [])

    def test_refresh_with_the_token_rewrites_the_item(self):
        status, body = self.post("/refresh", {"key": pr_url(4), "token": self.token})
        self.assertEqual(status, 200, body)
        out = json.loads(body)
        self.assertEqual(
            (out["ok"], out["outcome"], out["ref"], out["removed"]),
            (True, "updated", f"{REPO}#4", False),
        )
        status, page = self.request("GET", "/prs?lane=ready")
        self.assertEqual(status, 200)
        ready = tabs_fx.section(page, "ready")
        self.assertIn(pr_url(4), tabs_fx.keys(ready))
        self.assertIn("data-refresh", ready)
        conn = self.conn()
        try:
            kinds = [k for (k,) in conn.execute("SELECT kind FROM sync_runs")]
        finally:
            conn.close()
        self.assertEqual(kinds, [github_fleet.RUN_KIND])  # the seed's, no more

    def test_errors_answer_json_and_leave_the_card(self):
        status, body = self.post("/refresh", {"key": "nonsense", "token": self.token})
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(body)["ok"])
        self.httpd.app.gh_runner = FakeGh(fail=(1, "", "HTTP 502"))
        status, body = self.post("/refresh", {"key": pr_url(4), "token": self.token})
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"], "GitHub did not answer; try again")


CONFIG_TOML = """
[jira]
site = "example.invalid"
board_ids = [77]
enabled = false
[jira.fields]
sprint = "customfield_10303"
flagged = "customfield_10007"
[github]
enabled = true
fleet = true
orgs = ["bestdan"]
"""


class CliTests(Case):
    def setUp(self):
        super().setUp()
        self.cfg_path = Path(self.tmp.name) / "config.toml"
        self.cfg_path.write_text(CONFIG_TOML)
        patch = mock.patch.object(dashboard, "_now", lambda: NOW)
        patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, *argv, runner):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(github_gh, "run", runner):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = dashboard.main(
                    [*argv, "--db", str(self.db_path), "--config", str(self.cfg_path)]
                )
        return code, out.getvalue(), err.getvalue()

    def test_updated_says_so(self):
        fake = FakeGh({4: answer(pr_node(4))})
        code, out, err = self.run_cli("refresh", f"{REPO}#4", runner=fake)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"updated: {REPO}#4 (pr)\n")
        self.assertEqual(len(fake.calls), 1)

    def test_merged_and_not_found_say_removed(self):
        fake = FakeGh({1: answer(pr_node(1, state="MERGED"))})
        self.assertEqual(
            self.run_cli("refresh", pr_url(1), runner=fake)[1],
            f"merged (removed): {REPO}#1 (pr)\n",
        )
        self.assertEqual(
            self.run_cli("refresh", f"{REPO}#99", runner=fake)[1],
            f"not found (removed): {REPO}#99\n",
        )

    def test_json_shapes_the_item_as_view_does(self):
        fake = FakeGh({4: answer(pr_node(4))})
        code, out, _ = self.run_cli("refresh", f"{REPO}#4", "--json", runner=fake)
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual((doc["outcome"], doc["kind"]), ("updated", "pr"))
        prs = doc["views"]["prs"]
        self.assertEqual([i["lane"] for i in prs], ["ready"])
        view = agent_view.build(self.conn, fleet_cfg(), "prs", NOW)
        ready = next(lane for lane in view["lanes"] if lane["id"] == "ready")
        self.assertIn(prs[0], ready["items"])

    def test_json_for_a_removed_item_lists_no_view(self):
        fake = FakeGh({1: answer(pr_node(1, state="CLOSED"))})
        code, out, _ = self.run_cli("refresh", pr_url(1), "--json", runner=fake)
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(
            (doc["outcome"], doc["removed"], doc["views"]), ("closed", True, {})
        )

    def test_failures_exit_nonzero_and_change_nothing(self):
        self.assertEqual(self.run_cli("refresh", "junk", runner=FakeGh({}))[0], 2)
        code, _, err = self.run_cli(
            "refresh", f"{REPO}#4", runner=FakeGh(fail=(1, "", "HTTP 502"))
        )
        self.assertEqual(code, 1)
        self.assertIn("mirror unchanged", err)
        self.assertIsNotNone(self.fleet_row(pr_url(4)))


if __name__ == "__main__":
    unittest.main()
