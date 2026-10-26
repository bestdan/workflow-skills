#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/github_fleet.py.

Every gh call goes through a fake runner fed from fixtures/gh/fleet_*.json;
nothing reaches the network. Needs Python 3.11: run via
scripts/test-task-dashboard-fleet-gh.sh.
"""

import base64
import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
FIXTURES = PKG / "fixtures" / "gh"

# github_fleet imports its sibling github_gh by name, as dashboard.py does.
sys.path.insert(0, str(PKG))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"td_{name}", PKG / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fleet = _load("github_fleet")
db = _load("db")

REPO = "bestdan/workflow-skills"
NOW = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def ok(obj):
    return (0, json.dumps(obj), "")


NOT_FOUND = (1, "", "gh: Not Found (HTTP 404)")


def arg(argv, name):
    for a in argv:
        if a.startswith(f"{name}="):
            return a.split("=", 1)[1]
    return None


class Router:
    """Answers each gh call by a rule on its argv; records every argv."""

    def __init__(self, rules):
        self.rules = rules
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        for match, response in self.rules:
            if match(argv):
                return response(argv) if callable(response) else response
        raise AssertionError(f"unexpected gh call: {argv}")


def search_q(argv):
    return arg(argv, "q") or ""


def review_page(nodes=None):
    page = fixture("fleet_review_search.json")
    if nodes is not None:
        page["data"]["search"]["nodes"] = nodes
    return page


def pr_node(**kw):
    node = copy.deepcopy(
        fixture("fleet_review_search.json")["data"]["search"]["nodes"][0]
    )
    node.update(kw)
    return node


class DecisionBriefTests(unittest.TestCase):
    def test_section_returned(self):
        body = (
            "Closes #1\n\n## Decision brief\n\n"
            "Adds the fleet adapter.\nDecide: is the queue author-or-reviewer?\n"
            "A no invalidates the gauge.\n\n## What & why\n\nlong text\n"
        )
        self.assertEqual(
            fleet.decision_brief(body),
            "Adds the fleet adapter.\nDecide: is the queue author-or-reviewer?\n"
            "A no invalidates the gauge.",
        )

    def test_absent_is_none(self):
        for node in fixture("fleet_review_search.json")["data"]["search"]["nodes"]:
            self.assertIsNone(fleet.decision_brief(node["body"]))
        self.assertIsNone(fleet.decision_brief(""))
        self.assertIsNone(fleet.decision_brief(None))

    def test_longer_section_capped_at_three_lines(self):
        body = "## Decision brief\none\ntwo\n\nthree\nfour\nfive\n"
        self.assertEqual(fleet.decision_brief(body), "one\ntwo\nthree")

    def test_stops_at_next_heading(self):
        body = "## Decision brief\r\nonly line\r\n### Detail\r\nnot brief\r\n"
        self.assertEqual(fleet.decision_brief(body), "only line")

    def test_empty_section_is_none(self):
        self.assertIsNone(fleet.decision_brief("## Decision brief\n\n## Next\nx\n"))

    def test_heading_case_and_other_levels(self):
        self.assertEqual(fleet.decision_brief("## decision BRIEF\nx\n"), "x")
        self.assertIsNone(fleet.decision_brief("### Decision brief\nx\n"))
        self.assertIsNone(fleet.decision_brief("Decision brief: x\n"))


GREEN = ok(
    {
        "statusCheckRollup": [
            {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}
        ]
    }
)


def milestone(number, approved=False):
    data = fixture(f"fleet_milestone_{number}.json")
    if approved:
        data["description"] += "\n\n  Approved: YES  \n"
    return data


# The viewer login in fleet_comments.json, the account the capture ran as.
VIEWER = "bestdan"


def stamp(sha, at="2026-10-23T20:00:00Z", author=VIEWER):
    return {
        "body": f"co-review done\n<!-- co-review-stamp v1 head={sha} -->\n",
        "createdAt": at,
        "author": {"login": author},
    }


def comments_page(nodes):
    page = fixture("fleet_comments.json")
    page["data"]["repository"]["pullRequest"]["comments"]["nodes"] = nodes
    return page


def queue_rules(
    ci=GREEN, approved=(), comments=None, milestone_error=None, comments_error=None
):
    """Router rules for everything review_queue calls after its searches.

    `approved` names the milestone numbers whose description says
    `approved: yes`; `comments` maps a PR number to its comment nodes. A
    `*_error` response replaces every milestone or comments reply.
    """
    comments = comments or {}

    def milestone_response(argv):
        if milestone_error:
            return milestone_error
        number = int(argv[2].rsplit("/", 1)[1])
        return ok(milestone(number, number in approved))

    def comments_response(argv):
        if comments_error:
            return comments_error
        return ok(comments_page(comments.get(int(arg(argv, "number")), [])))

    return [
        (lambda a: a[:3] == ["gh", "pr", "view"], ci),
        (lambda a: "/milestones/" in a[2], milestone_response),
        (lambda a: arg(a, "owner") is not None, comments_response),
    ]


class ReviewQueueTests(unittest.TestCase):
    def run_queue(self, author_nodes, reviewer_nodes=(), **rules):
        runner = Router(
            [
                (lambda a: "author:@me" in search_q(a), ok(review_page(author_nodes))),
                (
                    lambda a: "review-requested:@me" in search_q(a),
                    ok(review_page(list(reviewer_nodes))),
                ),
            ]
            + queue_rules(**rules)
        )
        rows, failed = fleet.review_queue(["bestdan"], runner)
        return rows, failed, runner

    def queued(self, rows):
        return sorted(r["number"] for r in rows if r["in_queue"])

    def test_searches_ask_github_for_the_awaiting_set(self):
        _, _, runner = self.run_queue([])
        self.assertEqual(
            [search_q(c) for c in runner.calls],
            [
                "is:pr is:open draft:false -review:approved author:@me org:bestdan",
                "is:pr is:open draft:false -review:approved review-requested:@me org:bestdan",
            ],
        )
        query = arg(runner.calls[0], "query")
        for field in ("body", "headRefOid", "closingIssuesReferences"):
            self.assertIn(field, query)

    def test_fixture_rows_all_awaiting_none_queued(self):
        # The real milestones carry no `approved: yes`, and the real PRs no
        # stamp, so the live count is 0 until co-review writes stamps.
        rows, failed, runner = self.run_queue(None)
        self.assertEqual(failed, [])
        self.assertEqual(fleet.queue_counts(rows), {"queue": 0, "awaiting": 4})
        row = next(r for r in rows if r["number"] == 1114)
        self.assertEqual(row["kind"], "pr")
        self.assertEqual(row["key"], f"https://github.com/{REPO}/pull/1114")
        self.assertEqual(row["role"], "author")
        self.assertEqual(row["queue_misses"], ["milestone"])
        self.assertIsNone(row["stamped"])
        self.assertIsNone(row["brief"])
        # Rule 3 is not asked for a PR that already missed another rule.
        self.assertFalse(any(arg(c, "owner") for c in runner.calls))

    def test_all_four_rules_queue_a_pr(self):
        nodes = review_page()["data"]["search"]["nodes"]
        heads = {n["number"]: n["headRefOid"] for n in nodes}
        rows, _, _ = self.run_queue(
            None,
            approved={14},
            comments={1115: [stamp(heads[1115])], 1116: [stamp("0" * 40)]},
        )
        self.assertEqual(self.queued(rows), [1115])
        by = {r["number"]: r for r in rows}
        self.assertEqual(by[1116]["queue_misses"], ["co_review"])
        self.assertFalse(by[1116]["stamped"])
        self.assertEqual(by[1117]["queue_misses"], ["milestone"])
        self.assertEqual(fleet.queue_counts(rows), {"queue": 1, "awaiting": 4})

    def test_ci_must_be_success(self):
        node = review_page()["data"]["search"]["nodes"][1]
        ok_stamp = {node["number"]: [stamp(node["headRefOid"])]}
        for ci in (
            ok({"statusCheckRollup": []}),
            ok(
                {
                    "statusCheckRollup": [
                        {"__typename": "CheckRun", "status": "IN_PROGRESS"}
                    ]
                }
            ),
            ok(
                {
                    "statusCheckRollup": [
                        {
                            "__typename": "CheckRun",
                            "status": "COMPLETED",
                            "conclusion": "FAILURE",
                        }
                    ]
                }
            ),
            (1, "", "HTTP 502"),
        ):
            rows, _, _ = self.run_queue([node], ci=ci, approved={14}, comments=ok_stamp)
            self.assertEqual(rows[0]["queue_misses"], ["ci"], ci)
        rows, _, _ = self.run_queue([node], approved={14}, comments=ok_stamp)
        self.assertTrue(rows[0]["in_queue"])

    def test_newest_stamp_wins(self):
        node = review_page()["data"]["search"]["nodes"][1]
        head = node["headRefOid"]
        old = stamp(head, "2026-10-01T00:00:00Z")
        new = stamp("a" * 40, "2026-10-02T00:00:00Z")
        rows, _, _ = self.run_queue(
            [node], approved={14}, comments={node["number"]: [new, old]}
        )
        self.assertEqual(rows[0]["queue_misses"], ["co_review"])
        rows, _, _ = self.run_queue(
            [node],
            approved={14},
            comments={
                node["number"]: [
                    {"body": "lgtm", "createdAt": "2026-10-03T00:00:00Z"},
                    old,
                ]
            },
        )
        self.assertTrue(rows[0]["in_queue"])

    def test_stamp_parse(self):
        sha = "ABCDEF0123" * 4
        me = {"login": VIEWER}
        self.assertEqual(fleet.stamp_head([stamp(sha)], VIEWER), sha.lower())
        self.assertIsNone(
            fleet.stamp_head(
                [{"body": "<!-- co-review-stamp v1 head=abc -->", "author": me}],
                VIEWER,
            )
        )
        self.assertIsNone(
            fleet.stamp_head(
                [
                    {
                        "body": "<!-- co-review-stamp v2 head=" + "a" * 40 + " -->",
                        "author": me,
                    }
                ],
                VIEWER,
            )
        )
        self.assertIsNone(fleet.stamp_head([], VIEWER))

    def test_stamp_from_another_author_is_ignored(self):
        sha = "b" * 40
        self.assertIsNone(fleet.stamp_head([stamp(sha, author="mallory")], VIEWER))
        self.assertIsNone(fleet.stamp_head([{"body": stamp(sha)["body"]}], VIEWER))
        # A newer forged stamp does not supersede the viewer's own.
        mine = stamp(sha, "2026-10-01T00:00:00Z")
        forged = stamp("c" * 40, "2026-10-02T00:00:00Z", author="mallory")
        self.assertEqual(fleet.stamp_head([forged, mine], VIEWER), sha)

    def test_forged_stamp_does_not_queue(self):
        node = review_page()["data"]["search"]["nodes"][1]
        forged = stamp(node["headRefOid"], author="mallory")
        rows, _, runner = self.run_queue(
            [node], approved={14}, comments={node["number"]: [forged]}
        )
        self.assertEqual(rows[0]["queue_misses"], ["co_review"])
        query = next(arg(c, "query") for c in runner.calls if arg(c, "owner"))
        self.assertIn("viewer { login }", query)
        self.assertIn("author { login }", query)

    def test_milestone_lookup_failure_is_a_miss_not_fatal(self):
        rows, failed, runner = self.run_queue(None, milestone_error=(1, "", "HTTP 502"))
        self.assertEqual(len(rows), 4)
        self.assertEqual(sorted(failed), sorted(r["url"] for r in rows))
        self.assertTrue(all(r["queue_misses"] == ["milestone"] for r in rows))
        # Four PRs sit in two milestones; a failure is remembered, so each
        # milestone is asked once rather than once per PR.
        fetched = [c[2] for c in runner.calls if "/milestones/" in c[2]]
        self.assertEqual(
            sorted(fetched),
            [f"repos/{REPO}/milestones/13", f"repos/{REPO}/milestones/14"],
        )

    def test_comments_lookup_failure_is_a_miss_not_fatal(self):
        node = review_page()["data"]["search"]["nodes"][1]
        rows, failed, _ = self.run_queue(
            [node], approved={14}, comments_error=(1, "", "HTTP 502")
        )
        self.assertEqual(failed, [node["url"]])
        self.assertEqual(rows[0]["queue_misses"], ["co_review"])
        self.assertFalse(rows[0]["stamped"])

    def test_lookup_auth_failure_still_raises(self):
        auth = (1, "", "HTTP 401: Bad credentials")
        with self.assertRaises(fleet.github_gh.AuthError):
            self.run_queue(None, milestone_error=auth)
        node = review_page()["data"]["search"]["nodes"][1]
        with self.assertRaises(fleet.github_gh.AuthError):
            self.run_queue([node], approved={14}, comments_error=auth)

    def test_milestone_approved_line(self):
        self.assertTrue(fleet.milestone_approved("Goal: x\n  APPROVED: yes \r\n"))
        self.assertTrue(fleet.milestone_approved("approved:yes"))
        self.assertFalse(fleet.milestone_approved("approved: yes, once tests land"))
        self.assertFalse(fleet.milestone_approved("not approved: yes"))
        self.assertFalse(fleet.milestone_approved("approved: no"))
        self.assertFalse(fleet.milestone_approved(None))

    def test_milestone_fetched_once_per_repo_run(self):
        rows, _, runner = self.run_queue(None)
        fetched = [c[2] for c in runner.calls if "/milestones/" in c[2]]
        self.assertEqual(
            sorted(fetched),
            [f"repos/{REPO}/milestones/13", f"repos/{REPO}/milestones/14"],
        )

    def outside_milestones(self):
        """Two PRs the milestone rule skips: no closing issue, and one whose
        closing issue is in no milestone."""
        bare = pr_node(url="u/pull/5", number=5, closingIssuesReferences={"nodes": []})
        unfiled = pr_node(
            url="u/pull/6",
            number=6,
            closingIssuesReferences={
                "nodes": [
                    {
                        "number": 7,
                        "repository": {"nameWithOwner": REPO},
                        "milestone": None,
                    }
                ]
            },
        )
        return [bare, unfiled]

    def test_pr_outside_a_milestone_skips_the_rule(self):
        nodes = self.outside_milestones()
        stamps = {n["number"]: [stamp(n["headRefOid"])] for n in nodes}
        rows, failed, runner = self.run_queue(nodes, comments=stamps)
        self.assertEqual(failed, [])
        self.assertEqual(self.queued(rows), [5, 6])
        for row in rows:
            self.assertIsNone(row["milestone_approved"])
            self.assertTrue(row["stamped"])
        self.assertFalse(any("/milestones/" in c[2] for c in runner.calls))

    def test_skipped_rule_still_checks_the_stamp(self):
        rows, _, runner = self.run_queue(self.outside_milestones())
        self.assertEqual([r["queue_misses"] for r in rows], [["co_review"]] * 2)
        self.assertEqual(sum(1 for c in runner.calls if arg(c, "owner")), 2)

    def test_skipped_rule_still_needs_ci(self):
        failing = (1, "", "HTTP 502")
        rows, _, _ = self.run_queue(self.outside_milestones(), ci=failing)
        self.assertEqual([r["queue_misses"] for r in rows], [["ci"]] * 2)
        self.assertTrue(all(r["milestone_approved"] is None for r in rows))

    def test_one_milestoned_issue_brings_the_rule_back(self):
        nodes = review_page()["data"]["search"]["nodes"]
        milestoned = next(
            i
            for n in nodes
            for i in n["closingIssuesReferences"]["nodes"]
            if i.get("milestone")
        )
        refs = self.outside_milestones()[1]["closingIssuesReferences"]["nodes"]
        node = pr_node(
            url="u/pull/7",
            number=7,
            closingIssuesReferences={"nodes": refs + [milestoned]},
        )
        good = {7: [stamp(node["headRefOid"])]}
        rows, _, _ = self.run_queue([node], comments=good)
        self.assertEqual(rows[0]["queue_misses"], ["milestone"])
        self.assertIs(rows[0]["milestone_approved"], False)
        number = milestoned["milestone"]["number"]
        rows, _, _ = self.run_queue([node], approved={number}, comments=good)
        self.assertTrue(rows[0]["in_queue"])
        self.assertIs(rows[0]["milestone_approved"], True)

    def test_draft_excluded(self):
        rows, _, _ = self.run_queue(
            [
                pr_node(url="u/pull/1", number=1, isDraft=True),
                pr_node(url="u/pull/2", number=2),
            ]
        )
        self.assertEqual([r["number"] for r in rows], [2])

    def test_approved_excluded(self):
        rows, _, _ = self.run_queue(
            [
                pr_node(url="u/pull/1", number=1, reviewDecision="APPROVED"),
                pr_node(url="u/pull/2", number=2, reviewDecision="CHANGES_REQUESTED"),
                pr_node(url="u/pull/3", number=3, reviewDecision="REVIEW_REQUIRED"),
            ]
        )
        self.assertEqual([r["number"] for r in rows], [2, 3])

    def test_author_and_reviewer_deduplicate(self):
        node = pr_node(url="u/pull/9", number=9)
        rows, _, _ = self.run_queue([node], [node, pr_node(url="u/pull/8", number=8)])
        self.assertEqual(
            [(r["number"], r["role"]) for r in rows], [(9, "author"), (8, "reviewer")]
        )

    def test_brief_carried(self):
        rows, _, _ = self.run_queue(
            [pr_node(url="u/pull/1", number=1, body="## Decision brief\na\nb\nc\nd\n")]
        )
        self.assertEqual(rows[0]["brief"], "a\nb\nc")


class IssuesTests(unittest.TestCase):
    def test_fixture_rows_and_query(self):
        runner = Router([(lambda a: True, ok(fixture("fleet_issues_search.json")))])
        rows = fleet.issues_needing_human(["bestdan"], runner)
        self.assertEqual(
            search_q(runner.calls[0]),
            'is:issue is:open label:"auto:human-review-needed" org:bestdan',
        )
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["kind"] == "issue" for r in rows))
        self.assertTrue(all("auto:human-review-needed" in r["labels"] for r in rows))
        self.assertEqual(rows[0]["number"], 1111)

    def test_owner_name_validated(self):
        with self.assertRaises(ValueError):
            fleet.issues_needing_human(["bestdan is:closed"], Router([]))


def issue_node(n, *labels):
    return {
        "url": f"https://github.com/{REPO}/issues/{n}",
        "number": n,
        "title": f"issue {n}",
        "createdAt": "2026-10-20T00:00:00Z",
        "updatedAt": "2026-10-20T00:00:00Z",
        "repository": {"nameWithOwner": REPO},
        "labels": {"nodes": [{"name": name} for name in labels]},
    }


def paged(nodes, size):
    """A search answered `size` nodes a page, following `after`."""

    def answer(argv):
        start = int(arg(argv, "after") or 0)
        page = nodes[start : start + size]
        more = start + size < len(nodes)
        return ok(
            {
                "data": {
                    "search": {
                        "pageInfo": {
                            "hasNextPage": more,
                            "endCursor": str(start + size),
                        },
                        "nodes": page,
                    }
                }
            }
        )

    return answer


class OpenIssuesTests(unittest.TestCase):
    HUMAN = "auto:human-review-needed"

    def runner(self, human, rest, size=2):
        return Router(
            [
                (lambda a: self.HUMAN in search_q(a), paged(human, size)),
                (lambda a: "is:issue" in search_q(a), paged(rest, size)),
            ]
        )

    def test_the_cap_is_enforced_and_reported(self):
        rest = [issue_node(n, "status:2_ready") for n in range(1, 8)]
        runner = self.runner([], rest)
        rows, truncated = fleet.open_issues(["bestdan"], runner, cap=3)
        self.assertEqual([r["number"] for r in rows], [1, 2, 3])
        self.assertEqual(truncated, ["bestdan"])
        # It stops paging once the cap is reached: two pages of two, not four.
        wide = [a for a in runner.calls if self.HUMAN not in search_q(a)]
        self.assertEqual(len(wide), 2)
        self.assertEqual(search_q(wide[0]), "is:issue is:open org:bestdan")

    def test_a_cap_met_exactly_on_the_last_page_is_not_truncation(self):
        rest = [issue_node(n) for n in range(1, 5)]
        rows, truncated = fleet.open_issues(["bestdan"], self.runner([], rest), cap=4)
        self.assertEqual(len(rows), 4)
        self.assertEqual(truncated, [])

    def test_the_human_review_set_is_whole_past_the_cap(self):
        human = [issue_node(n, self.HUMAN) for n in range(100, 106)]
        rest = human[:2] + [issue_node(n) for n in range(1, 5)]
        rows, truncated = fleet.open_issues(
            ["bestdan"], self.runner(human, rest), cap=3
        )
        got = {r["number"] for r in rows if fleet.needs_human(r)}
        self.assertEqual(got, set(range(100, 106)))
        self.assertEqual(truncated, ["bestdan"])
        urls = [r["url"] for r in rows]
        self.assertEqual(len(urls), len(set(urls)))

    def test_fetch_reports_the_truncated_owners(self):
        runner = WriteTests().full_runner()
        plan = fleet.fetch(
            {"github": {"orgs": ["bestdan"], "design_repos": []}}, runner, now=NOW
        )
        self.assertEqual(plan["issues_truncated"], [])


def designs_router(listing=None, extra=()):
    listing = fixture("fleet_designs_list.json") if listing is None else listing
    by_path = {
        "dev_docs/designs/2026-10-14-reenter-worktrees-by-name.md": ok(
            fixture("fleet_design_proposed.json")
        ),
        "dev_docs/designs/2026-10-18-co-review-ledger-sync.md": ok(
            fixture("fleet_design_accepted.json")
        ),
    }

    def file_response(argv):
        path = argv[2].split("/contents/", 1)[1]
        if path in by_path:
            return by_path[path]
        # research-spike-skill.md has no front matter; the real file carries
        # `status:` lines in its body, which must not be read as its status.
        body = "# Research spike\n\nstatus: proposed\n"
        return ok(
            {"encoding": "base64", "content": base64.b64encode(body.encode()).decode()}
        )

    return Router(
        list(extra)
        + [
            (lambda a: a[2].endswith("/contents/dev_docs/designs"), ok(listing)),
            (lambda a: "/contents/dev_docs/designs/" in a[2], file_response),
        ]
    )


class DesignTests(unittest.TestCase):
    def test_only_proposed(self):
        rows, truncated, missing = fleet.proposed_designs([REPO], designs_router())
        self.assertEqual(truncated, [])
        self.assertEqual(missing, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["kind"], "design")
        self.assertEqual(row["status"], "proposed")
        self.assertEqual(row["created"], "2026-10-14")
        self.assertEqual(
            row["path"], "dev_docs/designs/2026-10-14-reenter-worktrees-by-name.md"
        )
        self.assertTrue(row["title"].startswith("Re-enter hook-layout worktrees"))
        self.assertTrue(row["key"].startswith(f"https://github.com/{REPO}/blob/"))

    def test_reads_default_branch(self):
        runner = designs_router()
        fleet.proposed_designs([REPO], runner)
        self.assertTrue(all("ref=" not in " ".join(c) for c in runner.calls))

    def test_missing_directory_is_reported(self):
        runner = Router([(lambda a: True, NOT_FOUND)])
        self.assertEqual(
            fleet.proposed_designs(["bestdan/x"], runner), ([], [], ["bestdan/x"])
        )

    def test_missing_repo_does_not_hide_the_others(self):
        runner = designs_router(extra=[(lambda a: "bestdan/typo/" in a[2], NOT_FOUND)])
        rows, truncated, missing = fleet.proposed_designs(
            ["bestdan/typo", REPO], runner
        )
        self.assertEqual(missing, ["bestdan/typo"])
        self.assertEqual(truncated, [])
        self.assertEqual(len(rows), 1)

    def test_cap_reads_newest_and_reports(self):
        runner = designs_router()
        rows, truncated, _ = fleet.proposed_designs([REPO], runner, max_files=2)
        self.assertEqual(truncated, [REPO])
        read = [c[2] for c in runner.calls if c[2].endswith(".md")]
        self.assertEqual(len(read), 2)
        self.assertNotIn("2026-08-01", " ".join(read))
        self.assertEqual(len(rows), 1)

    def test_other_error_raises(self):
        runner = Router([(lambda a: True, (1, "", "HTTP 502: Bad Gateway"))])
        with self.assertRaises(fleet.GhError):
            fleet.proposed_designs([REPO], runner)

    def test_front_matter_only(self):
        self.assertEqual(
            fleet.front_matter(
                "---\nstatus: proposed\ncreated: 2026-01-02\n---\nstatus: x\n"
            ),
            {"status": "proposed", "created": "2026-01-02"},
        )
        self.assertIsNone(fleet.front_matter("# t\nstatus: proposed\n"))
        self.assertIsNone(fleet.front_matter("---\nstatus: proposed\n"))

    def test_repo_validated(self):
        with self.assertRaises(ValueError):
            fleet.proposed_designs(["../etc"], Router([]))


class ClosedTests(unittest.TestCase):
    def closed_page(self, unmerge=()):
        page = fixture("fleet_closed_search.json")
        for node in page["data"]["search"]["nodes"]:
            if node["number"] in unmerge:
                node["mergedAt"] = None
                node["state"] = "CLOSED"
        return page

    def test_search_is_my_closed_prs_in_the_window(self):
        runner = Router([(lambda a: True, ok(self.closed_page()))])
        fleet.recently_closed(["bestdan", "acme"], NOW, runner)
        self.assertEqual(
            [search_q(a) for a in runner.calls],
            [
                f"is:pr is:closed author:@me closed:>=2026-10-09 org:{o}"
                for o in ("bestdan", "acme")
            ],
        )

    def test_rows_carry_merged_and_close_time(self):
        runner = Router([(lambda a: True, ok(self.closed_page(unmerge={1134})))])
        rows = {r["number"]: r for r in fleet.recently_closed(["bestdan"], NOW, runner)}
        self.assertEqual(set(rows), {1131, 1134, 1135})
        self.assertEqual(rows[1135]["kind"], "closed_pr")
        self.assertTrue(rows[1135]["merged"])
        self.assertEqual(rows[1135]["head_ref"], "bestdan/task-1133")
        self.assertEqual(rows[1135]["closed_at"], "2026-10-24T02:31:10Z")
        self.assertEqual(rows[1135]["status"], "merged")
        self.assertFalse(rows[1134]["merged"])
        self.assertEqual(rows[1134]["status"], "closed")

    def test_an_open_pr_wins_over_a_closed_row_for_the_same_url(self):
        page = self.closed_page()
        page["data"]["search"]["nodes"][0]["url"] = review_page()["data"]["search"][
            "nodes"
        ][0]["url"]
        runner = WriteTests().full_runner()
        runner.rules.insert(0, (lambda a: "is:closed" in search_q(a), ok(page)))
        plan = fleet.fetch(
            {"github": {"orgs": ["bestdan"], "design_repos": []}}, runner, now=NOW
        )
        urls = [r["url"] for r in plan["rows"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(sum(1 for r in plan["rows"] if r["kind"] == "closed_pr"), 2)

    def test_closed_rows_do_not_count_as_open(self):
        rows = [
            {"kind": "closed_pr", "key": "u", "title": "t", "in_queue": True},
            {"kind": "pr", "key": "v", "title": "t", "in_queue": False},
        ]
        self.assertEqual(fleet.queue_counts(rows), {"queue": 0, "awaiting": 1})


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "d.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def config(self):
        return {"github": {"orgs": ["bestdan"], "design_repos": [REPO]}}

    def full_runner(self, ci=None):
        ci = ci or ok({"statusCheckRollup": []})
        return designs_router(
            extra=queue_rules(ci=ci)
            + [
                (
                    lambda a: "is:closed" in search_q(a),
                    ok(fixture("fleet_closed_search.json")),
                ),
                (lambda a: "author:@me" in search_q(a), ok(review_page())),
                (lambda a: "review-requested:@me" in search_q(a), ok(review_page([]))),
                (
                    lambda a: "is:issue" in search_q(a),
                    ok(fixture("fleet_issues_search.json")),
                ),
            ]
        )

    def test_rows_land_as_github_by_kind(self):
        plan = fleet.fetch(self.config(), self.full_runner())
        self.assertEqual(plan["failed"], [])
        self.assertEqual(plan["missing"], [])
        fleet.write(self.conn, plan["rows"], NOW)
        kinds = dict(
            self.conn.execute(
                "SELECT kind, COUNT(*) FROM issues WHERE source = 'github' GROUP BY kind"
            ).fetchall()
        )
        self.assertEqual(kinds, {"pr": 4, "issue": 3, "design": 1, "closed_pr": 3})
        raw = json.loads(
            self.conn.execute(
                "SELECT raw_json FROM issues WHERE kind = 'pr' LIMIT 1"
            ).fetchone()[0]
        )
        self.assertEqual(raw["ci_state"], "none")
        self.assertEqual(plan["counts"], {"queue": 0, "awaiting": 4})
        self.assertEqual(
            self.conn.execute(
                "SELECT DISTINCT status_name FROM issues WHERE kind = 'pr'"
            ).fetchall(),
            [("awaiting",)],
        )

    def test_jira_rows_untouched_and_github_replaced(self):
        self.conn.execute(
            "INSERT INTO issues (source, kind, key, summary) VALUES ('jira', 'ticket', 'AB-1', 's')"
        )
        self.conn.execute(
            "INSERT INTO issues (source, kind, key, summary) VALUES ('github', 'pr', 'stale', 's')"
        )
        self.conn.commit()
        fleet.write(
            self.conn, fleet.fetch(self.config(), self.full_runner())["rows"], NOW
        )
        keys = {(s, k) for s, k in self.conn.execute("SELECT source, key FROM issues")}
        self.assertIn(("jira", "AB-1"), keys)
        self.assertNotIn(("github", "stale"), keys)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM jira_issues").fetchone()[0], 1
        )

    def test_fetch_reports_missing_design_repos(self):
        config = {
            "github": {"orgs": ["bestdan"], "design_repos": ["bestdan/typo", REPO]}
        }
        runner = self.full_runner()
        runner.rules.insert(0, (lambda a: "bestdan/typo/" in a[2], NOT_FOUND))
        plan = fleet.fetch(config, runner)
        self.assertEqual(plan["missing"], ["bestdan/typo"])
        self.assertEqual(sum(1 for r in plan["rows"] if r["kind"] == "design"), 1)

    def test_ci_failure_is_reported_not_fatal(self):
        plan = fleet.fetch(self.config(), self.full_runner(ci=(1, "", "HTTP 502")))
        self.assertEqual(len(plan["failed"]), 4)
        self.assertTrue(
            all(r.get("ci_state") is None for r in plan["rows"] if r["kind"] == "pr")
        )

    def test_failed_write_rolls_back(self):
        self.conn.execute(
            "INSERT INTO issues (source, kind, key) VALUES ('github', 'pr', 'kept')"
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.Error):
            fleet.write(self.conn, [{"kind": None, "key": "x", "title": "t"}], NOW)
        self.assertEqual(
            self.conn.execute(
                "SELECT key FROM issues WHERE source='github'"
            ).fetchall(),
            [("kept",)],
        )


if __name__ == "__main__":
    unittest.main()
