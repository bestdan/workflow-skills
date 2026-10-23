#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/score.py and views.py.

score() and is_blocked() are pure, so they are tested on plain dicts. The views
run over a temp DB seeded by hand, and one case serves every route through the
real server on an ephemeral port. Needs Python 3.11: run via
scripts/test-task-dashboard-score.sh.
"""

import http.client
import json
import math
import re
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task-dashboard"))

# mypy checks this file without scripts/task-dashboard on its path; the
# modules themselves are type-checked in their own group (scripts/typecheck.sh).
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import score  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402
import views  # type: ignore[import-not-found]  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
TS = "%Y-%m-%dT%H:%M:%SZ"

CFG = config.deep_merge(
    config.DEFAULTS,
    {
        "jira": {
            "site": "example.invalid",
            "board_ids": [77],
            "fields": {"sprint": "s", "flagged": "f"},
            "blocked_statuses": ["On Hold"],
            "me": "Pat Example",
        },
        "github": {"enabled": False},
    },
)
WEIGHTS = CFG["score"]["weights"]


def ts(dt):
    return dt.strftime(TS)


def issue(**kw):
    base = {
        "key": "ABC-1",
        "summary": "x",
        "status_name": "In Progress",
        "status_category": "indeterminate",
        "status_category_changed": ts(NOW),
        "priority": "Medium",
        "labels": "[]",
        "flagged": 0,
    }
    base.update(kw)
    return base


def ctx(**kw):
    base = {"now": NOW, "unblocks": 0, "prs": [], "blocked": (False, [])}
    base.update(kw)
    return base


def link(direction, category, key="ABC-9", link_type="Blocks"):
    return {
        "linked_key": key,
        "link_type": link_type,
        "direction": direction,
        "linked_status_category": category,
    }


class ScoreTest(unittest.TestCase):
    def total(self, i, **c):
        return score.score(i, ctx(**c), WEIGHTS)[0]

    def test_blocked_urgent_ranks_below_unblocked_medium(self):
        urgent = issue(priority="Highest")
        blocked = score.is_blocked(urgent, [link("inward", "new")], CFG)
        self.assertTrue(blocked[0])
        self.assertLess(
            self.total(urgent, blocked=blocked), self.total(issue(priority="Medium"))
        )

    def test_due_tomorrow_outranks_due_next_month(self):
        soon = issue(due_date=(NOW + timedelta(days=1)).date().isoformat())
        later = issue(due_date=(NOW + timedelta(days=31)).date().isoformat())
        self.assertGreater(self.total(soon), self.total(later))

    def test_sprint_end_stands_in_for_a_missing_due_date(self):
        ending = issue(sprint_end=ts(NOW + timedelta(days=2)))
        self.assertGreater(self.total(ending), self.total(issue()))
        terms = dict((n, w) for n, _, w in score.score(ending, ctx(), WEIGHTS)[1])
        self.assertIn("sprint ends", terms["deadline"])

    def test_terms_sum_to_the_total_and_cover_every_term(self):
        i = issue(
            priority="High",
            due_date=(NOW - timedelta(days=3)).date().isoformat(),
            status_category_changed=ts(NOW - timedelta(days=12)),
        )
        prs = [{"role": "author", "review_decision": "APPROVED", "number": 4}]
        total, terms = score.score(
            i,
            ctx(
                unblocks=2,
                prs=prs,
                blocked=(True, ["flagged"]),
                stuck_days=CFG["score"]["stuck_days"],
            ),
            WEIGHTS,
        )
        self.assertEqual([t[0] for t in terms], list(score.TERMS))
        self.assertTrue(math.isclose(total, sum(v for _, v, _ in terms)))
        values = {n: v for n, v, _ in terms}
        self.assertEqual(values["priority"], 9)
        self.assertEqual(values["unblocks"], 4)
        self.assertEqual(values["review"], 2)
        self.assertEqual(values["blocked"], -10)
        self.assertGreater(values["deadline"], 2)  # overdue: past the day-of 1.0
        self.assertGreater(values["stuck"], 0)

    def test_stuck_counts_only_past_the_category_threshold(self):
        stuck_days = CFG["score"]["stuck_days"]
        fresh = issue(status_category_changed=ts(NOW - timedelta(days=4)))
        old = issue(status_category_changed=ts(NOW - timedelta(days=8)))
        values = lambda i: dict(  # noqa: E731
            (n, v) for n, v, _ in score.score(i, ctx(stuck_days=stuck_days), WEIGHTS)[1]
        )
        self.assertEqual(values(fresh)["stuck"], 0)
        self.assertGreater(values(old)["stuck"], 0)

    def test_green_pr_of_mine_counts_and_a_reviewers_does_not(self):
        mine = [{"role": "author", "ci_state": "success", "number": 1}]
        theirs = [{"role": "reviewer", "review_decision": "APPROVED", "number": 2}]
        self.assertGreater(self.total(issue(), prs=mine), self.total(issue()))
        self.assertEqual(self.total(issue(), prs=theirs), self.total(issue()))


class IsBlockedTest(unittest.TestCase):
    def test_each_signal_alone_blocks_and_names_itself(self):
        cases = {
            "label": (issue(labels='["blocked"]'), []),
            "flagged": (issue(flagged=1), []),
            "status": (issue(status_name="on hold"), []),
            "link": (issue(), [link("inward", "indeterminate")]),
        }
        for signal, (i, links) in cases.items():
            self.assertEqual(score.is_blocked(i, links, CFG), (True, [signal]), signal)

    def test_unblocked_issue(self):
        self.assertEqual(score.is_blocked(issue(), [], CFG), (False, []))

    def test_inward_blocks_link_to_a_done_issue_does_not_block(self):
        self.assertEqual(
            score.is_blocked(issue(), [link("inward", "done")], CFG), (False, [])
        )

    def test_outward_and_non_blocks_links_do_not_block(self):
        links = [link("outward", "new"), link("inward", "new", link_type="Relates")]
        self.assertEqual(score.is_blocked(issue(), links, CFG), (False, []))


# --- views over a fixture DB --------------------------------------------------

ME, THEM = "Pat Example", "Sam Sample"

ROWS = [
    # key, assignee, category, status, priority, extra columns
    ("ABC-1", ME, "indeterminate", "In Progress", "Highest", {}),
    ("ABC-2", ME, "new", "To Do", "Low", {}),  # pinned, low score
    ("ABC-3", ME, "new", "To Do", "High", {}),  # snoozed until tomorrow
    ("ABC-4", ME, "new", "To Do", "Medium", {"parent_key": "EPIC-1"}),
    ("ABC-5", THEM, "new", "To Do", "Medium", {"parent_key": "EPIC-1"}),
    ("ABC-6", ME, "done", "Done", "High", {"parent_key": "EPIC-1"}),
    (
        "ABC-7",
        ME,
        "indeterminate",
        "In Review",
        "Medium",
        {
            "status_category_changed": ts(NOW - timedelta(days=20)),
            "updated": ts(NOW - timedelta(days=9)),
        },
    ),
    ("ABC-8", THEM, "indeterminate", "Blocked", "High", {"scope": "linked"}),
    ("ABC-9", ME, "new", "To Do", "Medium", {"snooze_past": True}),
]


def seed_views(conn):
    for key, who, cat, status, prio, extra in ROWS:
        conn.execute(
            "INSERT INTO issues (source, kind, key, summary, status_name,"
            " status_category, status_category_changed, updated, assignee,"
            " priority, labels, parent_key, scope) VALUES ('jira', 'ticket', ?, ?,"
            " ?, ?, ?, ?, ?, ?, '[]', ?, ?)",
            (
                key,
                f"summary {key}",
                status,
                cat,
                extra.get("status_category_changed", ts(NOW)),
                extra.get("updated", ts(NOW)),
                who,
                prio,
                extra.get("parent_key"),
                extra.get("scope", "search"),
            ),
        )
    # A non-Jira row carrying a Jira-looking key never reaches a view.
    conn.execute(
        "INSERT INTO issues (source, kind, key, summary, status_category, assignee,"
        " scope) VALUES ('machine:mini', 'session', 'ZZZ-1', 'not jira', 'new', ?,"
        " 'search')",
        (ME,),
    )
    links = [
        # ABC-4 (mine) blocks ABC-5 (theirs, open): Waiting on me.
        ("ABC-4", "ABC-5", "outward", "new"),
        ("ABC-5", "ABC-4", "inward", "new"),
        # ABC-1 (mine) is blocked by ABC-8 (theirs, open): I'm waiting on. The
        # link's inlined category says done; the mirrored row says otherwise.
        ("ABC-1", "ABC-8", "inward", "done"),
        # ABC-2 is blocked only by a done issue: not waiting.
        ("ABC-2", "ABC-6", "inward", "done"),
    ]
    conn.executemany(
        "INSERT INTO links (issue_key, linked_key, link_type, direction,"
        " linked_status_category) VALUES (?, ?, 'Blocks', ?, ?)",
        links,
    )
    prs = [
        ("https://github.com/o/r/pull/1", 1, "ABC-1 fix", "author", "APPROVED", "success", "ABC-1"),
        ("https://github.com/o/r/pull/2", 2, "ABC-2 wip", "author", "REVIEW_REQUIRED", "pending", "ABC-2"),
        ("https://github.com/o/r/pull/3", 3, "their change", "reviewer", None, "success", None),
        ("https://github.com/o/r/pull/4", 4, "no review rule", "author", None, "success", None),
    ]  # fmt: skip
    conn.executemany(
        "INSERT INTO prs (url, repo, number, title, is_draft, role, review_decision,"
        " ci_state, issue_key) VALUES (?, 'o/r', ?, ?, 0, ?, ?, ?, ?)",
        [(u, n, t, role, rd, ci, k) for u, n, t, role, rd, ci, k in prs],
    )
    conn.execute(
        "INSERT INTO pins (source, item_key, pinned_at) VALUES ('jira', 'ABC-2', ?)",
        (ts(NOW),),
    )
    tomorrow = (NOW + timedelta(days=1)).date().isoformat()
    conn.execute(
        "INSERT INTO snoozes (source, item_key, until) VALUES ('jira', 'ABC-3', ?)",
        (tomorrow,),
    )
    conn.execute(
        "INSERT INTO snoozes (source, item_key, until) VALUES ('jira', 'ABC-9', ?)",
        (ts(NOW - timedelta(hours=1)),),
    )
    conn.commit()


class ViewsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "d.db")
        seed_views(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def mirror(self, cfg=CFG):
        return views.Mirror(self.conn, cfg, NOW)

    def test_now_puts_the_pin_first_and_hides_the_snoozed(self):
        m = self.mirror()
        keys = [i["key"] for i in views.now_items(m)]
        self.assertEqual(keys[0], "ABC-2")
        self.assertLess(m.scores["ABC-2"][0], m.scores["ABC-1"][0])
        self.assertNotIn("ABC-3", keys)  # snoozed until tomorrow
        self.assertIn("ABC-9", keys)  # snooze already over
        self.assertNotIn("ABC-6", keys)  # done
        self.assertNotIn("ABC-5", keys)  # not mine
        self.assertNotIn("ZZZ-1", keys)  # not a Jira row
        rest = keys[1:]
        self.assertEqual(rest, sorted(rest, key=lambda k: (-m.scores[k][0], k)))

    def test_pins_follow_overlay_order(self):
        self.conn.execute(
            "INSERT INTO pins (source, item_key, pinned_at) VALUES ('jira', 'ABC-4', ?)",
            (ts(NOW + timedelta(minutes=1)),),
        )
        self.conn.execute(
            "INSERT INTO \"order\" (source, item_key, position) VALUES ('jira', 'ABC-4', 1)"
        )
        self.conn.execute(
            "INSERT INTO \"order\" (source, item_key, position) VALUES ('jira', 'ABC-2', 2)"
        )
        self.conn.commit()
        keys = [i["key"] for i in views.now_items(self.mirror())]
        self.assertEqual(keys[:2], ["ABC-4", "ABC-2"])

    def test_waiting_on_me(self):
        issues, prs = views.waiting_on_me(self.mirror())
        self.assertEqual([i["key"] for i, _ in issues], ["ABC-4"])
        self.assertIn("ABC-5", issues[0][1])
        self.assertEqual([p["number"] for p in prs], [3])

    def test_waiting_on_uses_the_mirrored_blocker_status(self):
        issues, prs = views.waiting_on(self.mirror())
        self.assertEqual([i["key"] for i, _ in issues], ["ABC-1"])
        self.assertEqual(
            [(p["number"], why) for p, why in prs], [(2, "awaiting review, CI pending")]
        )

    def test_stale(self):
        found = views.stale(self.mirror())
        self.assertEqual([i["key"] for i, _ in found], ["ABC-7"])
        self.assertIn("In Review", found[0][1])

    def test_epics_rollup(self):
        # ABC-5 is blocked by its inward link from ABC-4, which is open.
        (g,) = views.epics(self.mirror())
        self.assertEqual(
            (g["key"], g["total"], g["done"], g["blocked"]), ("EPIC-1", 3, 1, 1)
        )

    def test_unset_me_falls_back_to_scope(self):
        cfg = config.deep_merge(CFG, {"jira": {"me": ""}})
        keys = [i["key"] for i in views.now_items(self.mirror(cfg))]
        self.assertIn("ABC-5", keys)  # in scope, someone else's
        self.assertNotIn("ABC-8", keys)  # linked-only
        self.assertIn("jira.me is not set", views.render("/now", self.conn, cfg, NOW))


class RoutesTest(unittest.TestCase):
    """Every view through the real server: renders, and shows its score terms."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "d.db"
        conn = db.connect(path)
        seed_views(conn)
        conn.close()
        self.httpd, self.syncer = server.build(
            CFG, path, 0, lambda mode: None, run_at_start=False
        )
        self.httpd.app.now = lambda: NOW
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
        resp = c.getresponse()
        out = resp.status, resp.read().decode()
        c.close()
        return out

    def test_every_view_renders_with_tabs_and_terms(self):
        for path in views.ROUTES:
            status, page = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(f'href="{path}" aria-current=page', page, path)
            for href, _ in views.TABS:
                self.assertIn(f'href="{href}"', page, path)
            cards = re.findall(r'<div class="card" data-key="([^"]+)"', page)
            if path != "/epics":
                self.assertTrue(cards, path)
                self.assertEqual(page.count('class="terms"'), len(cards), path)

    def test_now_card_shows_terms_and_joined_pr(self):
        page = self.get("/now")[1]
        (text,) = [
            c for c in page.split('<div class="card"') if 'data-key="ABC-1"' in c
        ]
        self.assertIn("priority <b>+12</b>", text)
        self.assertIn("blocked <b>−10</b>", text)
        self.assertIn("https://github.com/o/r/pull/1", text)
        found = re.search(r'data-total="([^"]+)"', text)
        assert found is not None
        total = float(found.group(1))
        values = [
            float(v.replace("−", "-"))
            for v in re.findall(r"</?li[^>]*>[a-z]+ <b>([^<]+)</b>", text)
        ]
        self.assertTrue(math.isclose(total, sum(values)))

    def test_board_carries_the_tab_bar(self):
        page = self.get("/board")[1]
        self.assertIn('href="/board" aria-current=page', page)
        self.assertIn('href="/now"', page)

    def test_status_still_json(self):
        status, body = self.get("/status")
        self.assertEqual(status, 200)
        self.assertIn("busy", json.loads(body))


if __name__ == "__main__":
    unittest.main()
