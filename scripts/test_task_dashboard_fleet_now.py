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

# Default-weight order, by category; the sessions tie at `asked` and go by the
# longest wait (s-turn asked 30h ago, s-pr 26h, s-perm 25h).
EXPECTED = [
    pr_url(22),  # quick_win
    "s-turn",  # asked
    "s-pr",
    "s-perm",
    pr_url(1),  # review_queue
    issue_url(12),  # started
    pr_url(20),  # others_pr (opened earlier, so older)
    pr_url(21),
    issue_url(10),  # human_issue, prio:1
    issue_url(16),  # human_issue, no prio
    pr_url(4),  # own_ci_failing
    issue_url(13),  # ready
    pr_url(6),  # own_pr, opened 10-01 (no awaiting row: its age is the prs row's)
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
        self.assertEqual(self.ranked()[:3], [pr_url(4), issue_url(13), pr_url(22)])
        # Pins keep the overlay's order, not the score's.
        overlay.set_order(self.conn, [(GH, issue_url(13)), (GH, pr_url(4))])
        self.assertEqual(self.ranked()[:2], [issue_url(13), pr_url(4)])

    def test_own_pr_categories(self):
        seed(self.conn)
        by_item = {
            r.need.item[1]: r for r in fleet_now.candidates(self.conn, self.cfg, NOW)
        }
        self.assertEqual(by_item[pr_url(4)].category, "own_ci_failing")
        self.assertEqual(by_item[pr_url(6)].category, "own_pr")
        self.assertIn("approved", by_item[pr_url(6)].why)
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
            [pr_url(1), pr_url(20), pr_url(21), pr_url(4), pr_url(6), pr_url(2)],
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
            m = re.search(r'<div class="why" data-terms="([^"]+)">score ([0-9.]+): ', c)
            assert m is not None, c[:300]
            t = re.search(r'data-total="([^"]+)"', c)
            assert t is not None
            self.assertEqual(float(m.group(2)), float(t.group(1)))
            # Title first, so the page's filter box finds it.
            self.assertIn('class="title"', c.split('<div class="meta">', 1)[0])
            # The ordinary snooze control, and Pin.
            self.assertIn('data-ov="snooze"', c)
            self.assertIn('data-ov="pin"', c)
        quick = cards[0]
        self.assertIn('data-category="quick_win"', quick)
        self.assertIn("quick_win +10 (bot PR from github-actions, CI green", quick)

    def test_the_link_to_needs_you_counts_what_the_list_did_not_reach(self):
        seed(self.conn)
        # Past a limit of 5: started issue 12 is own work, while PRs 20 and 21
        # and issues 10 and 16 are Needs you's.
        with mock.patch.object(fleet_now, "LIMIT", 5):
            page = self.page()
        self.assertEqual(item_keys(page), EXPECTED[:5])
        m = re.search(
            r'<p class="more" data-more="(\d+)"><a href="/needs">(\d+) more on Needs you</a>',
            page,
        )
        assert m is not None
        self.assertEqual(m.groups(), ("4", "4"))
        # At the default limit every Needs you item is reached: no link.
        self.assertNotIn('class="more"', self.page())

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
                item_keys(page.split('data-section="next"', 1)[1])[0], pr_url(22)
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


if __name__ == "__main__":
    unittest.main()
