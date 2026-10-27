#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/fleet_now.py, the fleet Now.

A fixture DB is built through the real writers (snapshot_adapter.ingest and
sessions.write for the hook sessions, github_fleet.write for the PRs and
issues, the `prs` table as the GitHub step writes it), then Now is ranked and
rendered from it, directly and through the in-process server. No gh, git,
claude or network runs: the server runs with a fake sync function. Needs
Python 3.11: run via scripts/test-task-dashboard-fleet-now.sh.
"""

import copy
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import close_out  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_now  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import include  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import score  # type: ignore[import-not-found]  # noqa: E402
import sessions  # type: ignore[import-not-found]  # noqa: E402
import snapshot_adapter  # type: ignore[import-not-found]  # noqa: E402
import views  # type: ignore[import-not-found]  # noqa: E402
import whynot  # type: ignore[import-not-found]  # noqa: E402
import workstreams  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_fleet_tabs as tabs_fx  # noqa: E402
import test_task_dashboard_needs as needs_fx  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

NOW = tabs_fx.NOW
HUMAN = github_fleet.HUMAN_LABEL
pr_url, issue_url = tabs_fx.pr_url, tabs_fx.issue_url
GH = github_fleet.SOURCE

ROWS = [
    tabs_fx.awaiting(1),  # in the review queue
    tabs_fx.awaiting(2, misses=["co_review"]),  # mine, green, no stamp
    tabs_fx.awaiting(3, misses=["ci"], ci="pending"),  # waits on CI: left out
    tabs_fx.awaiting(4, misses=["ci"], ci="failure"),  # mine, CI failing
    tabs_fx.awaiting(7, misses=["ci"], ci="none", role="reviewer"),  # not listed
    tabs_fx.others(20, "azack", ci="failure"),  # a human's PR
    tabs_fx.others(21, "dependabot[bot]", ci="pending", bot=True),  # bot, not green
    tabs_fx.others(22, "github-actions", bot=True),  # bot, green: the quick win
    tabs_fx.issue(10, HUMAN, "status:1_needs_refinement", "prio:1"),
    tabs_fx.issue(11, "auto:eligible", "status:4_needs_review", "prio:0"),
    tabs_fx.issue(12, "auto:eligible", "status:3_started", "prio:2"),
    tabs_fx.issue(13, "auto:eligible", "status:2_ready"),
    tabs_fx.issue(14, "status:0_untriaged"),
    tabs_fx.issue(16, HUMAN, "status:2_ready", title="ready but held"),
]

PRS = [
    # number, draft, decision, ci, role
    (1, False, None, "success", "author"),
    (2, False, None, "success", "author"),
    (3, False, None, "pending", "author"),
    (4, False, None, "failure", "author"),
    (5, True, None, "failure", "author"),  # a draft: left out
    (6, False, "APPROVED", "success", "author"),  # approved: merge it
    (7, False, None, "none", "reviewer"),
]

# Ship's order: the tiers, then the default weights within each. The sessions
# tie at `asked` and go by the longest wait (s-turn asked 30h ago, s-pr 26h,
# s-perm 25h). Nothing here has close-out evidence.
EXPECTED = [
    "s-turn",  # asked
    "s-pr",
    "s-perm",
    pr_url(22),  # ready to merge: quick_win
    pr_url(6),  # ready to merge: own_approved (approved, CI green)
    pr_url(1),  # review_queue
    issue_url(12),  # started
    pr_url(20),  # the rest: others_pr (opened earlier, so older)
    pr_url(21),
    issue_url(10),  # human_issue, prio:1
    issue_url(16),  # human_issue, no prio
    pr_url(4),  # own_ci_failing
    issue_url(13),  # ready
    pr_url(2),  # own_pr, opened 10-03
]


def seed(conn, rows=ROWS, prs=PRS, with_sessions=True):
    if with_sessions:
        snapshot_adapter.ingest(
            conn, needs_fx.with_branch(copy.deepcopy(needs_fx.SNAPSHOT)), NOW
        )
        sessions.write(conn, sessions.derive(conn, NOW), NOW)
    tabs_fx.seed(conn, rows=rows, prs=prs)


def fleet_cfg(jira=False):
    cfg = needs_fx.fleet_cfg()
    cfg["jira"]["enabled"] = jira
    return cfg


def item_keys(text):
    return re.findall(
        r'<div class="card[^"]*" data-source="[^"]*" data-key="([^"]+)"', text
    )


class Case(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.conn = db.connect(Path(self._td.name) / "d.db")
        self.addCleanup(self.conn.close)
        self.cfg = fleet_cfg()

    def ranked(self, inc=None):
        ov = overlay.load(self.conn)
        return [
            r.need.item[1]
            for r in fleet_now.build(self.conn, self.cfg, NOW, ov, inc).shown
        ]

    def everything(self):
        """Every candidate in rank order, past the list's limit."""
        ov = overlay.load(self.conn)
        return [
            r.need.item[1]
            for r in fleet_now.order(fleet_now.candidates(self.conn, self.cfg, NOW), ov)
        ]


class ScoreTests(unittest.TestCase):
    W = config.DEFAULTS["score"]["fleet"]

    def score(self, category, prio=None, since=None):
        item = {"category": category, "why": "because", "prio": prio, "since": since}
        return score.fleet_score(item, NOW, self.W)

    def test_one_term_per_name_and_they_add_up(self):
        total, terms = self.score("human_issue", "prio:0", NOW - timedelta(days=14))
        self.assertEqual([t[0] for t in terms], list(score.FLEET_TERMS))
        values = dict((n, v) for n, v, _ in terms)
        self.assertEqual(values["human_issue"], 3)
        self.assertEqual(values["prio"], 0.8)
        self.assertEqual(values["age"], 0.2)
        self.assertEqual(values["quick_win"], 0)
        self.assertEqual(total, 4.0)
        self.assertEqual(dict((n, w) for n, _, w in terms)["human_issue"], "because")

    def test_age_is_capped_and_an_unknown_wait_adds_nothing(self):
        _, terms = self.score("ready", since=NOW - timedelta(days=400))
        self.assertEqual(dict((n, v) for n, v, _ in terms)["age"], 0.5)
        total, terms = self.score("ready")
        self.assertEqual(total, 1.5)
        self.assertEqual(
            dict((n, w) for n, _, w in terms)["age"], "waiting for an unknown time"
        )

    def test_the_default_categories_are_in_their_declared_order(self):
        tops = [self.W[c] for c in score.FLEET_CATEGORIES]
        self.assertEqual(tops, sorted(tops, reverse=True))
        # The most prio and age can add never lifts an item past a session
        # that asked, or a session past a green bot PR.
        lift = self.W["prio"] * 4 + self.W["age"] * score.AGE_CAP_WEEKS
        self.assertLess(self.W["started"] + lift, self.W["asked"])
        self.assertLess(self.W["asked"] + lift, self.W["quick_win"])

    def test_an_unknown_category_is_refused(self):
        with self.assertRaises(ValueError):
            self.score("focus")

    def test_prio_of_takes_the_most_urgent(self):
        self.assertEqual(score.prio_of(["prio:2", "prio:0", "est:3"]), "prio:0")
        self.assertIsNone(score.prio_of(["est:3"]))
        self.assertIsNone(score.prio_of(None))


class ConfigTests(unittest.TestCase):
    def load(self, extra):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.toml"
            p.write_text(extra + "[jira]\nenabled = false\n")
            return config.load(p)

    def test_defaults_and_a_partial_override(self):
        self.assertEqual(
            self.load("")["score"]["fleet"], config.DEFAULTS["score"]["fleet"]
        )
        cfg = self.load("[score.fleet]\nready = 7\n")
        self.assertEqual(cfg["score"]["fleet"]["ready"], 7)
        self.assertEqual(cfg["score"]["fleet"]["quick_win"], 10)
        # The Jira weights are untouched by it.
        self.assertEqual(cfg["score"]["weights"], config.DEFAULTS["score"]["weights"])

    def test_every_default_names_a_term(self):
        self.assertEqual(set(config.DEFAULTS["score"]["fleet"]), set(score.FLEET_TERMS))

    def test_bad_weights_are_config_errors(self):
        for extra, needle in (
            (
                "[score.fleet]\nquickwin = 3\n",
                "score.fleet.quickwin names no fleet term",
            ),
            ('[score.fleet]\nasked = "high"\n', "score.fleet.asked must be a number"),
            ("[score.fleet]\nasked = true\n", "score.fleet.asked must be a number"),
            ("[score]\nfleet = 3\n", "score.fleet must be a table"),
        ):
            with self.assertRaises(config.ConfigError) as cm:
                self.load(extra)
            self.assertIn(needle, str(cm.exception), extra)


class RankTests(Case):
    def test_the_default_order(self):
        seed(self.conn)
        self.assertEqual(self.everything(), EXPECTED)
        self.assertEqual(self.ranked(), EXPECTED[: fleet_now.LIMIT])

    def test_a_green_bot_pr_outranks_a_human_review_issue_and_a_pin_both(self):
        seed(self.conn)
        everything = self.everything()
        self.assertLess(everything.index(pr_url(22)), everything.index(issue_url(10)))
        overlay.pin(self.conn, (GH, pr_url(4)), NOW)
        overlay.pin(self.conn, (GH, issue_url(13)), NOW + timedelta(minutes=1))
        self.assertEqual(self.ranked()[:3], [pr_url(4), issue_url(13), "s-turn"])
        # Pins keep the overlay's order, not the score's.
        overlay.set_order(self.conn, [(GH, issue_url(13)), (GH, pr_url(4))])
        self.assertEqual(self.ranked()[:2], [issue_url(13), pr_url(4)])

    def test_own_pr_categories(self):
        seed(self.conn)
        by_item = {
            r.need.item[1]: r for r in fleet_now.candidates(self.conn, self.cfg, NOW)
        }
        self.assertEqual(by_item[pr_url(4)].category, "own_ci_failing")
        self.assertEqual(by_item[pr_url(6)].category, "own_approved")
        self.assertIn("approved, CI green", by_item[pr_url(6)].why)
        self.assertIn("no co-review stamp", by_item[pr_url(2)].why)
        self.assertIn("/co-review", by_item[pr_url(2)].need.hint)
        # Waiting on CI, a draft, a review request out of the queue: not mine to act on.
        for n in (3, 5, 7):
            self.assertNotIn(pr_url(n), by_item, n)
        # Issues in review, untriaged ones and closed lanes are not own work.
        for n in (11, 14):
            self.assertNotIn(issue_url(n), by_item, n)
        # Held for a human: Needs you's human_issue, listed once.
        self.assertEqual(by_item[issue_url(16)].category, "human_issue")
        self.assertTrue(by_item[issue_url(16)].from_needs)
        self.assertFalse(by_item[issue_url(13)].from_needs)

    def test_ignores_snoozes_and_the_include_filter_apply(self):
        seed(self.conn)
        overlay.ignore(self.conn, (GH, pr_url(22)), NOW)
        overlay.snooze(self.conn, (sessions.SOURCE, "s-turn"), "2026-11-01")
        ov = overlay.load(self.conn)
        page = fleet_now.build(self.conn, self.cfg, NOW, ov)
        shown = [r.need.item[1] for r in page.shown]
        self.assertNotIn(pr_url(22), shown)
        self.assertNotIn("s-turn", shown)
        self.assertEqual(page.snoozed, 1)
        self.assertEqual(shown[0], "s-pr")
        prs = include.Include("now", frozenset({"prs"}), "url")
        self.assertEqual(
            self.ranked(prs),
            [pr_url(6), pr_url(1), pr_url(20), pr_url(21), pr_url(4), pr_url(2)],
        )
        tasks = include.Include("now", frozenset({"tasks"}), "url")
        self.assertEqual(
            self.ranked(tasks),
            [issue_url(12), issue_url(10), issue_url(16), issue_url(13)],
        )

    def test_no_successful_fleet_run_means_no_issues(self):
        seed(self.conn)
        self.conn.execute("DELETE FROM sync_runs")
        self.conn.commit()
        everything = self.everything()
        self.assertFalse([k for k in everything if "/issues/" in k], everything)
        self.assertIn("s-turn", everything)


class RenderTests(Case):
    def page(self, inc=None):
        return views.render("/now", self.conn, self.cfg, NOW, inc)

    def test_jira_off_now_lists_the_queue_with_a_why_line_on_every_card(self):
        seed(self.conn)
        page = self.page()
        self.assertIn('data-section="next"', page)
        self.assertNotIn('data-section="now"', page)  # no Jira lane
        self.assertNotIn("jira.me is not set", page)
        cards = page.split('<div class="card"')[1:]
        self.assertEqual(item_keys(page), EXPECTED[: fleet_now.LIMIT])
        for c in cards:
            m = re.search(
                r'<div class="why" data-terms="([^"]+)">ship: [a-z ]+ · score ([0-9.]+): ',
                c,
            )
            assert m is not None, c[:300]
            t = re.search(r'data-total="([^"]+)"', c)
            assert t is not None
            self.assertEqual(float(m.group(2)), float(t.group(1)))
            # Title first, so the page's filter box finds it.
            self.assertIn('class="title"', c.split('<div class="meta">', 1)[0])
            # The ordinary snooze control, and Pin.
            self.assertIn('data-ov="snooze"', c)
            self.assertIn('data-ov="pin"', c)
        quick = cards[3]
        self.assertIn('data-category="quick_win"', quick)
        self.assertIn('data-tier="ready to merge"', quick)
        self.assertIn("quick_win +10 (bot PR from github-actions, CI green", quick)

    def test_the_link_to_needs_you_counts_what_the_list_did_not_reach(self):
        seed(self.conn)
        # Past a limit of 5: own PR 6 and started issue 12 are own work,
        # while PRs 1, 20 and 21 and issues 10 and 16 are Needs you's.
        with mock.patch.object(fleet_now, "LIMIT", 5):
            page = self.page()
        self.assertEqual(item_keys(page), EXPECTED[:5])
        m = re.search(
            r'<p class="more" data-more="(\d+)"><a href="/needs">(\d+) more on Needs you</a>',
            page,
        )
        assert m is not None
        self.assertEqual(m.groups(), ("5", "5"))
        # At the default limit, own PR 6 moved up into the ready-to-merge
        # tier, so human_issue 16 is the one Needs you item past the list.
        self.assertIn('data-more="1"', self.page())

    def test_empty_and_filtered_out(self):
        seed(self.conn, rows=[], prs=[], with_sessions=False)
        self.assertIn("Nothing is waiting on you.", self.page())
        page = self.page(include.Include("now", frozenset({"other"}), "url"))
        self.assertIn("Nothing included by the filter is waiting.", page)

    def test_a_fleet_instance_with_jira_on_keeps_the_jira_now_below(self):
        self.cfg = fleet_cfg(jira=True)
        seed(self.conn)
        page = self.page()
        self.assertLess(
            page.index('data-section="next"'), page.index('data-section="now"')
        )


class RouteTests(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: NOW
        self.httpd.app.cfg["ingest"]["machines"] = {"mini": "sha256:" + "0" * 64}
        self.httpd.app.cfg["jira"]["enabled"] = False
        conn = db.connect(self.db_path)
        seed(conn)
        conn.close()

    def test_the_home_page_is_now(self):
        for path in ("/", "/now"):
            status, page = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn("<title>Now · Task dashboard</title>", page, path)
            self.assertIn("aria-current=page>Now", page, path)
            self.assertEqual(
                item_keys(page.split('data-section="next"', 1)[1])[0], "s-turn"
            )
        nav = page.split("<nav>", 1)[1].split("</nav>", 1)[0]
        self.assertEqual(re.findall(r'href="([^"]+)"', nav)[:2], ["/now", "/needs"])


class JiraNowUnchangedTest(server_harness.ServerCase):
    def test_a_jira_instance_now_has_no_fleet_queue(self):
        page = self.request("GET", "/now")[1]
        self.assertNotIn('data-section="next"', page)
        self.assertIn('data-section="now"', page)


class SessionCardTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.conn = db.connect(Path(self._td.name) / "d.db")
        self.addCleanup(self.conn.close)
        needs_fx.seed(self.conn)
        needs_fx.seed_registry(self.conn)

    def page(self):
        return fleet_now.render(self.conn, needs_fx.fleet_cfg(), needs_fx.NOW)

    def test_a_session_card_shows_what_it_asked_and_its_pr(self):
        before = self.page()
        needs_fx.post_transcripts(self.conn, [
            needs_fx.summary("s-turn", "edit the notes", needs_fx.ASK, "bestdan/n"),
        ])  # fmt: skip
        page = self.page()
        turn = needs_fx.card(page, "s-turn")
        self.assertIn('>edit the notes</a> <span class="stag">src-32</span>', turn)
        self.assertIn(f'<div class="ask">› {fleet_now._e(needs_fx.ASK)}</div>', turn)
        self.assertIn("notes: bestdan/n", turn)
        pr_card = needs_fx.card(page, "s-pr")
        self.assertIn(
            f'<a href="{needs_fx.pr_url(1201)}" target="_blank" rel="noopener">'
            "PR #1201</a>",
            pr_card,
        )
        # Without a summary, a session's card is as it was.
        self.assertEqual(needs_fx.card(page, "s-perm"), needs_fx.card(before, "s-perm"))


# --- close-outs (close_out.py) -----------------------------------------------

CO_WT = "/h/src/worktrees/workflow-skills"
CO_TAKEN = "2026-10-25T11:00:00.000Z"


def co_ev(sid, ts, event, cwd, branch, asked=None):
    line = {"v": 2, "ts": ts, "event": event, "session_id": sid, "cwd": cwd,
            "branch": branch}  # fmt: skip
    if event == "Stop":
        line["asked"] = asked
    return line


def co_session(sid, day, name, *, asked=False):
    """A prompt then a Stop on 2026-10-<day>, in worktree `name`."""
    cwd, branch = f"{CO_WT}/{name}", f"bestdan/{name}"
    return [
        co_ev(sid, f"2026-10-{day}T09:00:00.000Z", "UserPromptSubmit", cwd, branch),
        co_ev(sid, f"2026-10-{day}T10:00:00.000Z", "Stop", cwd, branch, asked),
    ]


def co_live(sid, name, bridge=None):
    return {"session_id": sid, "bridge_session_id": bridge, "name": None,
            "status": "idle", "pid": 5000, "updated_at": CO_TAKEN,
            "tmux": None, "cwd": f"{CO_WT}/{name}"}  # fmt: skip


def co_worktree(name, branch, state, path=None):
    return {"repo": "workflow-skills", "path": path or f"{CO_WT}/{name}",
            "branch": branch, "head": "a" * 40, "dirty": False, "unpushed": 0,
            "landed": {"state": state, "reason": state}}  # fmt: skip


CO_SNAPSHOT = {
    "schema": 4,
    "host": "mini",
    "taken_at": CO_TAKEN,
    "scan_errors": [],
    "handoffs": [],
    "tmux": [],
    "history": [],
    "worktrees": [
        co_worktree("main", "main", "landed", path="/h/src/workflow-skills"),
        co_worktree("task-2", "bestdan/task-2", "landed"),  # landed, still there
        co_worktree("task-5", "bestdan/task-5", "not_landed"),  # idle only
    ],
    # Live: c-merged (with a Remote Control id), c-landed, c-idle, c-asked. Every other
    # session is missing from the registry, so it has ended.
    "registry": [
        co_live("c-merged", "task-1", "session_01MERGED"),
        co_live("c-landed", "task-2"),
        co_live("c-idle", "task-5"),
        co_live("c-asked", "task-6"),
    ],
    "sessions": (
        co_session("c-merged", "23", "task-1")  # PR 301 merged, 401 closed
        + co_session("c-landed", "22", "task-2")  # branch landed, no PR
        + co_session("c-ended", "21", "task-3")  # ended; PR 303 merged, 403 open
        + co_session("c-open", "24", "task-4")  # ended; PR 304 still open
        + co_session("c-idle", "24", "task-5")  # a turn ended: no evidence
        + co_session("c-asked", "24", "task-6", asked=True)  # asked: not a close-out
        + co_session("c-old", "08", "task-7")  # ended; PR 307 merged, too old
        + co_session("c-all", "20", "task-8")  # ended; every step done
    ),
}


def co_closed(n, branch, closes=(), merged=True):
    node = {
        "url": pr_url(n), "number": n, "title": f"closed {n}",
        "state": "MERGED" if merged else "CLOSED", "headRefName": branch,
        "createdAt": "2026-10-01T00:00:00Z", "closedAt": "2026-10-20T00:00:00Z",
        "mergedAt": "2026-10-20T00:00:00Z" if merged else None,
        "repository": {"nameWithOwner": tabs_fx.REPO},
        "closingIssuesReferences": {"nodes": [
            {"number": i, "state": st,
             "repository": {"nameWithOwner": tabs_fx.REPO}}
            for i, st in closes
        ]},
    }  # fmt: skip
    return github_fleet.closed_row(node)


CO_CLOSED = [
    co_closed(301, "bestdan/task-1", [(401, "CLOSED")]),
    co_closed(303, "bestdan/task-3", [(403, "OPEN"), (404, "CLOSED")]),
    co_closed(307, "bestdan/task-7"),
    co_closed(308, "bestdan/task-8", [(408, "CLOSED")]),
]

MS = "ms:bestdan/workflow-skills#7"


def co_seed(conn, with_workstream=True):
    snapshot_adapter.ingest(conn, copy.deepcopy(CO_SNAPSHOT), NOW)
    sessions.write(conn, sessions.derive(conn, NOW), NOW)
    tabs_fx.seed(
        conn, rows=[tabs_fx.others(22, "github-actions", bot=True)] + CO_CLOSED, prs=[]
    )
    conn.execute(
        "INSERT INTO prs (url, repo, number, title, head_ref, is_draft, role)"
        " VALUES (?, ?, ?, ?, ?, 0, 'author')",
        (pr_url(304), tabs_fx.REPO, 304, "open 304", "bestdan/task-4"),
    )
    conn.commit()
    if with_workstream:
        ws = {"id": MS, "source": "milestone", "title": "Now modes",
              "status": "open", "url": "https://github.com/bestdan/workflow-skills/milestone/7",
              "repo": tabs_fx.REPO, "done": 2, "total": 2,
              "updated": "2026-10-19T00:00:00Z"}  # fmt: skip
        members = [
            {"ws_id": MS, "item_source": GH, "item_key": issue_url(n),
             "kind": "issue", "via": "milestone", "is_open": False,
             "started": False, "waiting": False,
             "updated": "2026-10-19T00:00:00Z"}
            for n in (501, 502)
        ]  # fmt: skip
        workstreams.write(conn, [ws], members, NOW)
        server_harness.add_run(conn, "ok", NOW, kind=workstreams.RUN_KIND)


class CloseOutTests(Case):
    def found(self):
        return {c.need.item[1]: c for c in close_out.collect(self.conn, self.cfg, NOW)}

    def steps(self, c):
        return [(s.name, s.done) for s in c.steps]

    def test_a_session_enters_only_on_evidence(self):
        co_seed(self.conn)
        found = self.found()
        self.assertEqual(set(found), {"c-merged", "c-landed", "c-ended", "c-open", MS})
        # c-idle merely ended a turn, c-asked waits on me, c-old is past the
        # window, and c-all has every step ticked.
        self.assertIn("PR #301 merged", found["c-merged"].why)
        self.assertIn("branch bestdan/task-2 landed", found["c-landed"].why)
        self.assertEqual(found["c-open"].why, "finished: the session ended")

    def test_the_checklist_ticks_from_evidence_and_skips_what_has_none(self):
        co_seed(self.conn)
        found = self.found()
        P, T, W, S = (close_out.PR_MERGED, close_out.TASK_CLOSED,
                      close_out.WORKTREE_REMOVED, close_out.SESSION_ENDED)  # fmt: skip
        self.assertEqual(
            self.steps(found["c-merged"]), [(P, True), (T, True), (W, True), (S, False)]
        )
        # No PR on its branch: no PR or task step. Its worktree is still listed.
        self.assertEqual(self.steps(found["c-landed"]), [(W, False), (S, False)])
        # One closing issue still open leaves the task step open.
        self.assertEqual(
            self.steps(found["c-ended"]), [(P, True), (T, False), (W, True), (S, True)]
        )
        # An open PR is an open step; it says nothing about the task yet.
        self.assertEqual(
            self.steps(found["c-open"]), [(P, False), (W, True), (S, True)]
        )
        self.assertEqual(self.steps(found[MS]), [(close_out.MILESTONE_CLOSED, False)])

    def test_a_host_without_a_registry_gives_no_session_ended_evidence(self):
        snap = copy.deepcopy(CO_SNAPSHOT)
        snap["schema"] = 2
        del snap["registry"]
        snapshot_adapter.ingest(self.conn, snap, NOW)
        sessions.write(self.conn, sessions.derive(self.conn, NOW), NOW)
        tabs_fx.seed(self.conn, rows=CO_CLOSED, prs=[])
        found = self.found()
        # Nothing can show a session ended, so that step is skipped rather than
        # left open: c-merged now has every step ticked and leaves, and c-ended
        # stays only for its open task.
        self.assertNotIn("c-merged", found)
        self.assertEqual(
            self.steps(found["c-ended"]),
            [(close_out.PR_MERGED, True), (close_out.TASK_CLOSED, False),
             (close_out.WORKTREE_REMOVED, True)],
        )  # fmt: skip

    def test_no_newer_snapshot_or_an_unscanned_repo_is_no_worktree_evidence(self):
        co_seed(self.conn)
        self.conn.execute(
            "UPDATE machines SET last_snapshot_at = '2026-10-20T00:00:00.000Z'"
        )
        self.conn.commit()
        names = [s.name for s in self.found()["c-merged"].steps]
        self.assertNotIn(close_out.WORKTREE_REMOVED, names)
        rows = [co_worktree("x", "bestdan/x", "landed")]
        rows[0]["repo"] = "other"
        last = NOW - timedelta(days=2)
        self.assertIsNone(close_out._worktree_step(rows, NOW, last, f"{CO_WT}/task-1"))

    def test_the_window_ends_a_close_out(self):
        co_seed(self.conn)
        # c-ended's last event was 2026-10-21T10:00Z: it stays for the window
        # after that, whatever its checklist, and then leaves.
        last = NOW.replace(day=21, hour=10)
        later = last + timedelta(days=close_out.WINDOW_DAYS, hours=-1)
        self.assertIn("c-ended", {c.need.item[1] for c in close_out.collect(
            self.conn, self.cfg, later)})  # fmt: skip
        later = last + timedelta(days=close_out.WINDOW_DAYS, hours=1)
        self.assertNotIn("c-ended", {c.need.item[1] for c in close_out.collect(
            self.conn, self.cfg, later)})  # fmt: skip

    def test_the_resume_action_carries_the_steps_left(self):
        co_seed(self.conn)
        found = self.found()
        live = found["c-merged"].need
        self.assertEqual(live.url, "https://claude.ai/code/session_01MERGED")
        self.assertIn("send: Finish closing out this session: end this session.",
                      live.hint)  # fmt: skip
        ended = found["c-ended"].need
        self.assertIsNone(ended.url)
        self.assertEqual(
            ended.command,
            ("claude", "--resume", "c-ended",
             "Finish closing out this session: close bestdan/workflow-skills#403."),
        )  # fmt: skip
        self.assertIn("`claude --resume c-ended 'Finish closing out", ended.hint)
        self.assertIn(f"in {CO_WT}/task-3 on mini", ended.hint)

    def test_ship_tiers_asked_then_close_outs_capped_oldest_first(self):
        co_seed(self.conn)
        page = fleet_now.build(self.conn, self.cfg, NOW, overlay.load(self.conn))
        shown = [(r.need.item[1], r.category) for r in page.shown]
        # Five close-outs, oldest first: MS (10-19), c-ended (10-21),
        # c-landed (10-22), then c-merged (10-23) and c-open (10-24), cut.
        self.assertEqual(
            shown,
            [
                ("c-asked", "asked"),
                (MS, "close_out"),
                ("c-ended", "close_out"),
                ("c-landed", "close_out"),
                (pr_url(22), "quick_win"),
            ],
        )
        self.assertEqual(page.more_close_outs, 2)
        # A pinned close-out is shown whatever the cap, and leaves room.
        overlay.pin(self.conn, (sessions.SOURCE, "c-open"), NOW)
        page = fleet_now.build(self.conn, self.cfg, NOW, overlay.load(self.conn))
        keys = [r.need.item[1] for r in page.shown]
        self.assertEqual(keys[:2], ["c-open", "c-asked"])
        self.assertEqual(keys.count("c-merged"), 0)

    def test_a_workstream_close_out_snoozes_and_ignores_by_its_own_key(self):
        co_seed(self.conn)
        item = (close_out.WS_SOURCE, MS)
        overlay.ignore(self.conn, item, NOW)
        page = fleet_now.build(self.conn, self.cfg, NOW, overlay.load(self.conn))
        keys = [r.need.item[1] for r in page.shown]
        self.assertNotIn(MS, keys)
        # The next-oldest close-out takes its place under the cap.
        self.assertIn("c-merged", keys)

    def test_the_card_shows_its_checklist(self):
        co_seed(self.conn)
        page = fleet_now.render(self.conn, self.cfg, NOW)
        card = needs_fx.card(page, "c-ended")
        self.assertIn('data-tier="close out"', card)
        self.assertIn(
            '<div class="checklist">✓ PR merged · ○ task closed · ✓ worktree removed'
            " · ✓ session ended</div>",
            card,
        )
        self.assertIn("ship: close out · score", card)
        self.assertIn('data-more-close-outs="2"', page)

    def test_own_approved_needs_ci_green(self):
        seed(self.conn, prs=[(6, False, "APPROVED", "success", "author"),
                             (8, False, "APPROVED", "pending", "author")])  # fmt: skip
        by_item = {
            r.need.item[1]: r for r in fleet_now.candidates(self.conn, self.cfg, NOW)
        }
        self.assertEqual(by_item[pr_url(6)].category, "own_approved")
        self.assertEqual(by_item[pr_url(8)].category, "own_pr")
        self.assertEqual(fleet_now.tier_of("own_approved"), (2, "ready to merge"))
        self.assertEqual(fleet_now.tier_of("own_pr")[1], fleet_now.REST)


# --- Little ------------------------------------------------------------------


def sized(row, files, adds, dels, paths=None):
    """`row` with a PR size, as github_gh.pr_size() reads one."""
    paths = paths if paths is not None else [f"f{i}.py" for i in range(files)]
    row["size"] = {"files": files, "additions": adds, "deletions": dels,
                   "paths": paths}  # fmt: skip
    return row


LITTLE_ROWS = [
    tabs_fx.others(22, "github-actions", bot=True),  # bot, green: bot PR
    tabs_fx.others(21, "dependabot[bot]", ci="pending", bot=True),  # pending: out
    sized(tabs_fx.awaiting(30), 1, 3, 1, ["README.md"]),  # docs-only
    sized(tabs_fx.awaiting(31, misses=["co_review"]), 2, 14, 3),  # small
    sized(tabs_fx.awaiting(32), 4, 10, 0),  # 4 files: not small
    sized(tabs_fx.awaiting(33), 2, 40, 20),  # 60 lines: not small
    sized(tabs_fx.awaiting(34, misses=["ci"], ci="failure"), 1, 1, 1),  # CI: out
    tabs_fx.awaiting(35),  # no size: unknown is not little
    tabs_fx.issue(40, "est:1", "status:2_ready", "prio:1"),
    tabs_fx.issue(41, "est:1", "status:0_untriaged"),
    tabs_fx.issue(42, "est:2", HUMAN, "status:1_needs_refinement"),
    tabs_fx.issue(43, "est:1", "status:3_started"),  # started: out
    tabs_fx.issue(44, "status:2_ready"),  # unsized: out
    tabs_fx.issue(45, "est:3", "status:2_ready"),  # not small
]

# Smallest tier first, then prio, then the longer wait. PR 37 is only in the
# `prs` table, opened 10-02; PR 31 was opened 10-12.
EXPECTED_LITTLE = [
    pr_url(22),  # bot PR
    pr_url(30),  # docs-only PR
    issue_url(40),  # est:1, prio:1
    issue_url(41),  # est:1
    pr_url(37),  # small PR, the longer wait
    pr_url(31),
    issue_url(42),  # est:2
]


def seed_little(conn):
    tabs_fx.seed(conn, rows=LITTLE_ROWS, prs=[])
    small = {"createdAt": "2026-10-02T00:00:00Z", "changedFiles": 1,
             "additions": 2, "deletions": 2,
             "files": {"nodes": [{"path": "a.py"}]}}  # fmt: skip
    conn.executemany(
        "INSERT INTO prs (url, repo, number, title, is_draft, review_decision,"
        " ci_state, role, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (pr_url(36), tabs_fx.REPO, 36, "a draft", 1, None, "success", "author",
             json.dumps(small)),  # a draft: out
            (pr_url(37), tabs_fx.REPO, 37, "review me", 0, None, "success",
             "reviewer", json.dumps(small)),
        ],
    )  # fmt: skip
    conn.commit()


class LittleTests(Case):
    def setUp(self):
        super().setUp()
        seed_little(self.conn)

    def little(self, inc=None):
        ov = overlay.load(self.conn)
        return fleet_now.build(self.conn, self.cfg, NOW, ov, inc, fleet_now.LITTLE)

    def test_only_small_items_smallest_first(self):
        page = self.little()
        self.assertEqual([r.need.item[1] for r in page.shown], EXPECTED_LITTLE)
        self.assertEqual(page.mode, fleet_now.LITTLE)
        self.assertEqual(
            [r.tier[1] for r in page.shown],
            ["bot PR", "docs-only PR", "est:1 issue", "est:1 issue", "small PR",
             "small PR", "est:2 issue"],
        )  # fmt: skip

    def test_why_lines_name_littles_terms(self):
        why = {r.need.item[1]: fleet_now.why_line(r) for r in self.little().shown}
        self.assertEqual(why[pr_url(22)], "little · bot PR from github-actions")
        self.assertEqual(why[pr_url(30)], "little · docs-only PR, 1 file, +3/−1")
        self.assertEqual(why[pr_url(31)], "little · PR, 2 files, +14/−3")
        self.assertEqual(why[issue_url(40)], "little · est:1 issue · prio:1")
        self.assertEqual(why[issue_url(42)], "little · est:2 issue")

    def test_docs_only(self):
        self.assertTrue(fleet_now.docs_only(["a.md", "docs/x.txt", "dev_docs/y.png"]))
        self.assertFalse(fleet_now.docs_only(["a.md", "a.py"]))
        self.assertFalse(fleet_now.docs_only([]))
        self.assertFalse(fleet_now.docs_only(None))  # the list was cut short

    def test_the_limit_and_n_more(self):
        with mock.patch.object(fleet_now, "LIMIT", 3):
            page = self.little()
        self.assertEqual(len(page.shown), 3)
        self.assertEqual(page.more_in_mode, len(EXPECTED_LITTLE) - 3)

    def test_pins_snoozes_and_ignores_apply(self):
        overlay.pin(self.conn, ("github", issue_url(42)), NOW)
        overlay.snooze(self.conn, ("github", pr_url(22)), "2026-11-01")
        overlay.ignore(self.conn, ("github", pr_url(30)), NOW)
        page = self.little()
        keys = [r.need.item[1] for r in page.shown]
        self.assertEqual(keys[0], issue_url(42))
        self.assertNotIn(pr_url(22), keys)
        self.assertNotIn(pr_url(30), keys)
        self.assertEqual(page.snoozed, 1)

    def test_a_pinned_item_little_does_not_rank_shows_under_ship(self):
        # In the review queue, so Ship ranks it; too big for Little.
        overlay.pin(self.conn, ("github", pr_url(32)), NOW)
        first = self.little().shown[0]
        self.assertEqual(first.need.item[1], pr_url(32))
        self.assertEqual(first.mode, fleet_now.SHIP)
        self.assertTrue(fleet_now.why_line(first).startswith("ship: review queue"))

    def test_the_include_filter_applies(self):
        inc = include.resolve("now", {include.PARAM: ["tasks"]}, None, self.cfg)
        kinds = {r.need.kind for r in self.little(inc).shown}
        self.assertEqual(kinds, {"issue"})

    def closed_row(self, since):
        conn = self.conn
        conn.execute(
            "INSERT INTO issues (source, kind, key, summary, raw_json) VALUES"
            " ('github', ?, ?, 'closed today', ?)",
            (
                github_fleet.CLOSED_TODAY,
                github_fleet.CLOSED_TODAY,
                json.dumps({"kind": github_fleet.CLOSED_TODAY, "since": since,
                            "count": 5, "merged": 3, "closed": 2}),
            ),
        )  # fmt: skip
        conn.commit()

    def test_closed_today_reads_todays_count_only(self):
        midnight = NOW.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        self.closed_row(midnight.isoformat(timespec="seconds"))
        self.assertEqual(self.little().closed_today, 5)
        page = fleet_now.render(self.conn, self.cfg, NOW, mode=fleet_now.LITTLE)
        self.assertIn('data-closed-today="5">closed today: 5</p>', page)
        # Ship has no such header.
        self.assertNotIn("closed today", fleet_now.render(self.conn, self.cfg, NOW))
        later = NOW + timedelta(days=1)
        self.assertIsNone(fleet_now.closed_today(self.conn, later))

    def test_no_count_no_header(self):
        self.assertIsNone(self.little().closed_today)
        page = fleet_now.render(self.conn, self.cfg, NOW, mode=fleet_now.LITTLE)
        self.assertNotIn("closed today", page)

    def test_the_snapshot_records_the_mode(self):
        item = ("github", pr_url(31))
        snap = fleet_now.snapshot_of(self.conn, self.cfg, item, NOW, fleet_now.LITTLE)
        self.assertEqual(snap["features"]["mode"], "little")
        self.assertEqual(snap["features"]["category"], "small_pr")
        # Not a mode: looked up under Ship, where PR 30 is in the review queue.
        ship_item = ("github", pr_url(30))
        snap = fleet_now.snapshot_of(self.conn, self.cfg, ship_item, NOW, "nap")
        self.assertIsNotNone(snap)
        self.assertEqual(snap["features"]["mode"], "ship")

    def test_shown_items_record_the_mode(self):
        fleet_now.render(self.conn, self.cfg, NOW, mode=fleet_now.LITTLE)
        modes = {
            json.loads(f)["mode"]
            for (f,) in self.conn.execute("SELECT features FROM now_shown")
        }
        self.assertEqual(modes, {"little"})


class ModeTests(Case):
    def setUp(self):
        super().setUp()
        seed_little(self.conn)

    def test_no_saved_mode_is_ship_and_the_saved_one_is_read(self):
        ov = overlay.load(self.conn)
        self.assertEqual(fleet_now.resolve_mode(None, ov), fleet_now.SHIP)
        overlay.set_now_mode(self.conn, "little", NOW)
        ov = overlay.load(self.conn)
        self.assertEqual(fleet_now.resolve_mode(None, ov), "little")
        self.assertEqual(fleet_now.resolve_mode("ship", ov), "ship")
        with self.assertRaises(fleet_now.ModeError):
            fleet_now.resolve_mode("nap", ov)

    def test_the_button_cycles_the_modes(self):
        for mode in fleet_now.MODES:
            page = fleet_now.render(self.conn, self.cfg, NOW, mode=mode)
            nxt = fleet_now.next_mode(mode)
            self.assertIn(f'<div class="modes" data-mode="{mode}">', page)
            self.assertIn(f'data-ov="mode" data-mode="{nxt}"', page)
            self.assertIn(f"Mode: {mode.title()}</button>", page)
        self.assertEqual(fleet_now.next_mode(fleet_now.MODES[-1]), fleet_now.MODES[0])

    def test_cards_carry_their_mode_for_the_why_not_answer(self):
        page = fleet_now.render(self.conn, self.cfg, NOW, mode="little")
        self.assertIn(f'data-key="{pr_url(31)}"', page)
        self.assertNotIn('data-mode="ship">', page.split('data-section="next"')[1])
        self.assertIn('data-mode="little">', page)

    def test_the_agent_view_takes_a_mode(self):
        import agent_view  # type: ignore[import-not-found]

        doc = agent_view.build(self.conn, self.cfg, "now", NOW, mode="little")
        self.assertEqual(doc["notes"]["mode"], "little")
        self.assertIsNone(doc["notes"]["closed_today"])
        keys = [i["key"] for i in doc["lanes"][0]["items"]]
        self.assertEqual(keys, EXPECTED_LITTLE)
        # No --mode reads the saved one, so an agent sees what the owner sees.
        overlay.set_now_mode(self.conn, "little", NOW)
        doc = agent_view.build(self.conn, self.cfg, "now", NOW)
        self.assertEqual(doc["notes"]["mode"], "little")
        with self.assertRaises(fleet_now.ModeError):
            agent_view.build(self.conn, self.cfg, "now", NOW, mode="nap")


class ModeRouteTests(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: NOW
        self.httpd.app.cfg["jira"]["enabled"] = False
        conn = db.connect(self.db_path)
        seed_little(conn)
        conn.close()

    def saved(self):
        conn = db.connect(self.db_path)
        try:
            return overlay.saved_now_mode(conn)
        finally:
            conn.close()

    def test_a_get_mode_is_a_one_off_view(self):
        status, page = self.request("GET", "/now?mode=little")
        self.assertEqual(status, 200)
        self.assertIn('<div class="modes" data-mode="little">', page)
        self.assertIsNone(self.saved())
        status, page = self.request("GET", "/now")
        self.assertIn('<div class="modes" data-mode="ship">', page)

    def test_an_unknown_mode_is_a_400(self):
        self.assertEqual(self.request("GET", "/now?mode=nap")[0], 400)

    def test_the_button_post_saves_the_mode(self):
        origin = f"http://127.0.0.1:{self.port}"
        body = [("token", self.token), ("mode", "little")]
        status, _ = self.request("POST", "/overlay/mode", origin=origin, body=body)
        self.assertEqual(status, 200)
        self.assertEqual(self.saved(), "little")
        page = self.request("GET", "/")[1]
        self.assertIn('<div class="modes" data-mode="little">', page)
        bad = [("token", self.token), ("mode", "nap")]
        status, _ = self.request("POST", "/overlay/mode", origin=origin, body=bad)
        self.assertEqual(status, 400)
        self.assertEqual(self.saved(), "little")


# --- the source seam ---------------------------------------------------------

# Golden output of Now over the fixtures below. Regenerate with
# NOW_GOLDEN=write only for a change that means to alter what Now shows.
GOLDEN = PKG / "fixtures" / "now" / "golden.json"


def _ship_seed(conn):
    seed(conn)
    overlay.pin(conn, (GH, issue_url(13)), NOW)
    overlay.snooze(conn, (GH, pr_url(20)), "2026-11-30")


def _little_seed(conn):
    seed_little(conn)
    overlay.pin(conn, (GH, pr_url(21)), NOW)  # only Ship ranks it
    overlay.snooze(conn, (GH, issue_url(41)), "2026-11-30")


SCENARIOS = {"ship": _ship_seed, "close_out": co_seed, "little": _little_seed}


class SeamEquivalenceTests(unittest.TestCase):
    """Now renders the same items, order and why lines as it did before the
    seam, in every mode, through the page, the agent view and the "why not
    now" snapshot of every candidate."""

    def observe(self, seed_fn):
        import agent_view  # type: ignore[import-not-found]

        out = {}
        with tempfile.TemporaryDirectory() as td:
            conn = db.connect(Path(td) / "d.db")
            try:
                seed_fn(conn)
                cfg = fleet_cfg()
                for mode in fleet_now.MODES:
                    every = fleet_now.mode_candidates(conn, cfg, NOW, mode)
                    out[mode] = {
                        "page": fleet_now.render(conn, cfg, NOW, mode=mode),
                        "agent": agent_view.build(conn, cfg, "now", NOW, mode=mode),
                        "snapshots": sorted(
                            ([*r.need.item, whynot.snapshot(r, NOW)] for r in every),
                            key=lambda x: (x[0], x[1]),
                        ),
                    }
            finally:
                conn.close()
        return json.loads(json.dumps(out, sort_keys=True, default=str))

    def test_now_is_unchanged_by_the_seam(self):
        seen = {name: self.observe(fn) for name, fn in SCENARIOS.items()}
        if os.environ.get("NOW_GOLDEN") == "write":
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(seen, indent=1, sort_keys=True) + "\n")
        want = json.loads(GOLDEN.read_text())
        self.maxDiff = None
        for name in SCENARIOS:
            for mode in fleet_now.MODES:
                for part in ("page", "agent", "snapshots"):
                    with self.subTest(scenario=name, mode=mode, part=part):
                        self.assertEqual(seen[name][mode][part], want[name][mode][part])


if __name__ == "__main__":
    unittest.main()
