#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/fleet_tabs.py.

A fixture DB is built through the real writers (github_fleet.write for the
awaiting PRs and the issues, the `prs` table as the Jira sync's GitHub step
writes it), then the Pull requests and Issues tabs are rendered from it,
directly and through the in-process server. No gh or network is touched: the
server runs with a fake sync function. Needs Python 3.11: run via
scripts/test-task-dashboard-fleet-tabs.sh.
"""

import json
import re
import sys
import unittest
from datetime import datetime, timezone

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_tabs  # type: ignore[import-not-found]  # noqa: E402
import fleet_views  # type: ignore[import-not-found]  # noqa: E402
import gauges  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import include  # type: ignore[import-not-found]  # noqa: E402
import needs  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
REPO = "bestdan/workflow-skills"
HUMAN = github_fleet.HUMAN_LABEL


def pr_url(n):
    return f"https://github.com/{REPO}/pull/{n}"


def issue_url(n):
    return f"https://github.com/{REPO}/issues/{n}"


def awaiting(n, *, misses=(), ci="success", role="author", author=None, bot=False):
    row = {
        "kind": "pr",
        "key": pr_url(n),
        "url": pr_url(n),
        "repo": REPO,
        "number": n,
        "title": f"awaiting {n}",
        "role": role,
        "ci_state": ci,
        "in_queue": not misses and role != "others",
        "queue_misses": list(misses),
        "review_decision": None,
        "created": f"2026-10-{n % 20 + 1:02d}T00:00:00Z",
    }
    if author:
        row.update(author=author, author_is_bot=bot)
    return row


def others(n, author, *, ci="success", bot=False):
    return awaiting(n, ci=ci, role="others", author=author, bot=bot)


def issue(n, *labels, title=None):
    return {
        "kind": "issue",
        "key": issue_url(n),
        "url": issue_url(n),
        "repo": REPO,
        "number": n,
        "title": title or f"issue {n}",
        "labels": list(labels),
        "created": "2026-10-20T00:00:00Z",
    }


AWAITING = [
    awaiting(1),  # in the queue
    awaiting(2, misses=["co_review"]),
    awaiting(3, misses=["ci", "milestone"], ci="pending"),
    awaiting(4, misses=["ci"], ci="failure"),
    awaiting(7, misses=["ci"], ci="none", role="reviewer"),
    awaiting(8, misses=["co_review"]),  # ignored
    awaiting(9, misses=["co_review"]),  # snoozed
    # PRs others opened on my repos. Created oldest first: 20, 21, 22.
    others(20, "azack", ci="failure"),  # a human's: From others, not CI failing
    others(21, "dependabot[bot]", ci="pending", bot=True),
    others(22, "github-actions", bot=True),  # a green bot PR leads the lane
]

# The `prs` mirror: every open PR of mine, drafts and approved ones included.
PRS = [
    # url, number, draft, decision, ci, role
    (1, False, None, "success", "author"),
    (2, False, None, "success", "author"),
    (3, False, None, "pending", "author"),
    (4, False, None, "failure", "author"),
    (5, True, None, "failure", "author"),  # a draft: Drafts, not CI failing
    (6, False, "APPROVED", "success", "author"),  # approved: never awaiting
    (7, False, None, "none", "reviewer"),
    (8, False, None, "success", "author"),
    (9, False, None, "success", "author"),
]

ISSUES = [
    issue(10, HUMAN, "status:1_needs_refinement", "prio:1", "est:3"),
    issue(11, "auto:eligible", "status:4_needs_review", "prio:0"),
    issue(12, "auto:eligible", "status:3_started", "est:5"),
    issue(13, "auto:eligible", "status:2_ready"),
    issue(14, "status:0_untriaged"),
    issue(15),  # a repo with no status ladder
    issue(16, HUMAN, "status:2_ready", title="ready but held"),
]


def seed(conn, *, fleet_ran=True, prs=PRS, rows=None, counts=None):
    github_fleet.write(conn, rows if rows is not None else AWAITING + ISSUES, NOW)
    conn.executemany(
        "INSERT INTO prs (url, repo, number, title, is_draft, review_decision,"
        " ci_state, role, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                pr_url(n),
                REPO,
                n,
                f"pr {n}",
                int(draft),
                decision,
                ci,
                role,
                json.dumps({"createdAt": "2026-10-01T00:00:00Z"}),
            )
            for n, draft, decision, ci, role in prs
        ],
    )
    conn.commit()
    if fleet_ran:
        conn.execute(
            "INSERT INTO sync_runs (kind, started_at, finished_at, outcome, counts)"
            " VALUES (?, ?, ?, 'ok', ?)",
            (
                github_fleet.RUN_KIND,
                "2026-10-25T11:00:00Z",
                "2026-10-25T11:01:00Z",
                json.dumps(counts or {}),
            ),
        )
        conn.commit()


def fleet_cfg():
    cfg = config.deep_merge(config.DEFAULTS, server_harness.USER_CONFIG)
    cfg["github"].update(enabled=True, scope="owner")
    return cfg


def section(page, name, required=True):
    m = re.search(
        rf'<section class="lane" data-section="{name}"[^>]*>(.*?)</section>', page, re.S
    )
    assert m or not required, f"no section {name}"
    return m.group(1) if m else ""


def keys(text):
    return re.findall(r'data-key="([^"]+)"', text)


class Case(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "d.db")
        self.cfg = fleet_cfg()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def overlay_hide(self):
        overlay.ignore(self.conn, (github_fleet.SOURCE, pr_url(8)), NOW)
        overlay.snooze(self.conn, (github_fleet.SOURCE, pr_url(9)), "2026-11-01")


class PullRequestTests(Case):
    def page(self, inc=None):
        return fleet_tabs.render_prs(self.conn, self.cfg, NOW, inc)

    def test_each_pr_lies_in_exactly_one_lane(self):
        seed(self.conn)
        self.overlay_hide()
        page = self.page()
        lanes = {name: keys(section(page, name)) for name, _, _ in fleet_tabs.PR_LANES}
        self.assertEqual(lanes["ready"], [pr_url(1)])
        self.assertEqual(
            [m for m in re.findall(r'data-section="([a-z-]+)"', page)],
            ["ready", "waiting", "others", "review", "ci", "drafts"],
        )
        self.assertEqual(sorted(lanes["waiting"]), [pr_url(n) for n in (2, 3, 6)])
        self.assertEqual(lanes["ci"], [pr_url(4)])
        self.assertEqual(lanes["drafts"], [pr_url(5)])
        self.assertEqual(lanes["review"], [pr_url(7)])
        self.assertEqual(lanes["others"], [pr_url(22), pr_url(20), pr_url(21)])
        placed = [k for ks in lanes.values() for k in ks]
        self.assertEqual(len(placed), len(set(placed)))
        # Every mirrored PR not ignored or snoozed is placed.
        mirrored = {pr_url(n) for n, *_ in PRS} | {r["url"] for r in AWAITING}
        self.assertEqual(set(placed), mirrored - {pr_url(8), pr_url(9)})
        # Ignored and snoozed PRs are out; the snooze is counted.
        self.assertNotIn(pr_url(8), page)
        self.assertNotIn(pr_url(9), placed)
        self.assertIn('data-snoozed="1"', page)

    def test_a_pr_waiting_on_you_names_its_misses(self):
        seed(self.conn)
        waiting = section(self.page(), "waiting")
        cards = dict(
            re.findall(r'data-key="([^"]+)".*?class="meta">(.*?)</div>', waiting, re.S)
        )
        self.assertIn("misses: no co-review stamp at its head", cards[pr_url(2)])
        self.assertIn("misses: CI pending, milestone not approved", cards[pr_url(3)])
        self.assertIn("approved", cards[pr_url(6)])

    def test_cards_are_title_first_then_the_age_and_ci(self):
        # The shared card (cards.py): the repo is in the ref, so the meta
        # line leads with the bare age and shows CI as the word alone.
        seed(self.conn)
        ready = section(self.page(), "ready")
        self.assertRegex(ready, r'<a class="title" href="[^"]+/pull/1"[^>]*>pr 1</a>')
        self.assertIn(f'<span class="ref">{REPO}#1</span>', ready)
        self.assertRegex(
            ready,
            r'<div class="meta"><span class="age" title="opened [0-9-]+">\d+d</span>'
            r' · <span class="ci" title="CI success">CI</span></div>',
        )
        self.assertNotIn(f"· {REPO}", ready)

    def test_the_review_and_others_lanes_appear_only_with_rows(self):
        seed(
            self.conn,
            prs=[p for p in PRS if p[4] == "author"],
            rows=[r for r in AWAITING if r["role"] == "author"],
        )
        page = self.page()
        self.assertNotIn('data-section="review"', page)
        self.assertNotIn('data-section="others"', page)

    def test_a_pr_from_others_names_its_author_and_tags_a_bot(self):
        seed(self.conn)
        lane = section(self.page(), "others")
        cards = dict(re.findall(r'data-key="([^"]+)"(.*?)class="ov"', lane, re.S))
        self.assertIn("by azack", cards[pr_url(20)])
        self.assertNotIn('class="badge">bot', cards[pr_url(20)])
        self.assertIn("by github-actions", cards[pr_url(22)])
        self.assertIn('<span class="badge">bot</span>', cards[pr_url(22)])

    def test_an_awaiting_row_without_a_prs_row_still_counts(self):
        seed(self.conn, prs=[])
        page = self.page()
        self.assertEqual(keys(section(page, "ready")), [pr_url(1)])
        self.assertIn(pr_url(4), keys(section(page, "ci")))

    def test_include_filter_switches_the_lanes_off(self):
        seed(self.conn)
        page = self.page(include.Include("prs", frozenset({"tasks"}), "url"))
        self.assertEqual(keys(page), [])
        self.assertIn("PRs are switched off", page)


class IssueTests(Case):
    def page(self, inc=None):
        return fleet_tabs.render_issues(self.conn, self.cfg, NOW, inc)

    def test_issues_are_laned_by_status_label(self):
        seed(self.conn)
        page = self.page()
        lanes = {
            name: keys(section(page, name)) for name, _, _ in fleet_tabs.ISSUE_LANES
        }
        self.assertEqual(
            lanes,
            {
                "needs-you": [issue_url(10), issue_url(16)],
                "needs-review": [issue_url(11)],
                "started": [issue_url(12)],
                "ready": [issue_url(13)],
                "untriaged": [issue_url(14), issue_url(15)],
            },
        )

    def test_cards_carry_prio_and_est(self):
        seed(self.conn)
        needs_you = section(self.page(), "needs-you")
        self.assertIn("prio:1 · est:3", needs_you)
        self.assertIn(f'<span class="ref">{REPO}#10</span>', needs_you)

    def test_the_cap_is_surfaced(self):
        seed(self.conn, counts={"issues_truncated": 1})
        self.assertIn('data-truncated="1"', self.page())
        self.assertIn(str(github_fleet.MAX_OPEN_ISSUES), self.page())

    def test_no_fleet_run_shows_nothing(self):
        seed(self.conn, fleet_ran=False)
        page = self.page()
        self.assertEqual(keys(page), [])
        self.assertIn("No fleet sync has succeeded yet", page)

    def test_ignored_and_snoozed_issues_are_left_out(self):
        seed(self.conn)
        overlay.ignore(self.conn, (github_fleet.SOURCE, issue_url(14)), NOW)
        overlay.snooze(self.conn, (github_fleet.SOURCE, issue_url(15)), "2026-11-01")
        page = self.page()
        self.assertEqual(keys(section(page, "untriaged")), [])
        self.assertIn('data-snoozed="1"', page)


class HumanSetTests(Case):
    """Widening the fetch leaves Needs you and Stale on the human-review set."""

    def test_needs_you_lists_only_the_human_review_issues(self):
        seed(self.conn)
        got = sorted(
            n.item[1]
            for n in needs.collect(self.conn, self.cfg, NOW)
            if n.kind == "issue"
        )
        self.assertEqual(got, [issue_url(10), issue_url(16)])

    def test_needs_you_lists_every_pr_from_others_beside_the_queue(self):
        seed(self.conn)
        prs = {
            n.item[1]: n.state
            for n in needs.collect(self.conn, self.cfg, NOW)
            if n.kind == "pr"
        }
        self.assertEqual(set(prs), {pr_url(1), pr_url(20), pr_url(21), pr_url(22)})
        self.assertTrue(prs[pr_url(20)].startswith("PR from azack · CI failure"))
        self.assertTrue(prs[pr_url(22)].startswith("PR from github-actions · bot"))

    def test_the_gauge_counts_the_review_queue_only(self):
        seed(self.conn)
        self.assertEqual(
            gauges.review_queue(self.conn, True),
            {"queue": 1, "awaiting": 7, "others": 3},
        )

    def test_the_fleet_read_holds_only_the_human_review_issues(self):
        seed(self.conn)
        f = fleet_views.load(self.conn, self.cfg)
        self.assertEqual(
            sorted(r["url"] for r in f.github["issue"]), [issue_url(10), issue_url(16)]
        )


class RouteTests(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: NOW
        conn = db.connect(self.db_path)
        seed(conn)
        conn.close()

    def test_both_tabs_render_with_the_filter_box(self):
        for path, name in (("/prs", "Pull requests"), ("/issues", "Issues")):
            status, page = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(f"aria-current=page>{name}", page, path)
            self.assertEqual(page.count('id="filter"'), 1, path)
            self.assertNotIn('href="/waiting-on"', page, path)
            self.assertNotIn("jira.me is not set", page, path)

    def test_the_include_switch_row_is_drawn(self):
        page = self.request("GET", "/issues?include=tasks")[1]
        self.assertIn('data-view="issues"', page)
        self.assertIn(issue_url(13), page)


if __name__ == "__main__":
    unittest.main()
