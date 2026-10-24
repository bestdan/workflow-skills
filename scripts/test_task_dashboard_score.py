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
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Date-only values compare in the machine's local timezone, so pin one: every
# expectation below is written for New York (UTC-4 on these dates), and the
# suite must not depend on the host's zone.
os.environ["TZ"] = "America/New_York"
time.tzset()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task-dashboard"))

# mypy checks this file without scripts/task-dashboard on its path; the
# modules themselves are type-checked in their own group (scripts/typecheck.sh).
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_views  # type: ignore[import-not-found]  # noqa: E402
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

    def values(self, i):
        c = ctx(stuck_days=CFG["score"]["stuck_days"])
        return {n: v for n, v, _ in score.score(i, c, WEIGHTS)[1]}

    def test_deadline_pressure_is_capped_when_long_overdue(self):
        late = issue(due_date=(NOW - timedelta(days=30)).date().isoformat())
        cap = WEIGHTS["deadline"] * score.DEADLINE_CAP
        self.assertEqual(self.values(late)["deadline"], cap)

    def test_stuck_is_capped_far_past_the_threshold(self):
        stale = issue(status_category_changed=ts(NOW - timedelta(days=60)))
        cap = WEIGHTS["stuck"] * score.STUCK_CAP
        self.assertEqual(self.values(stale)["stuck"], cap)

    def test_a_new_issue_is_stuck_only_past_fourteen_days(self):
        def new(days):
            changed = ts(NOW - timedelta(days=days))
            return issue(status_category="new", status_category_changed=changed)

        self.assertEqual(self.values(new(13))["stuck"], 0)
        self.assertGreater(self.values(new(20))["stuck"], 0)

    def test_green_pr_of_mine_counts_and_a_reviewers_does_not(self):
        mine = [{"role": "author", "ci_state": "success", "number": 1}]
        theirs = [{"role": "reviewer", "review_decision": "APPROVED", "number": 2}]
        self.assertGreater(self.total(issue(), prs=mine), self.total(issue()))
        self.assertEqual(self.total(issue(), prs=theirs), self.total(issue()))

    def test_a_green_draft_adds_nothing(self):
        draft = [{"role": "author", "ci_state": "success", "is_draft": 1, "number": 5}]
        self.assertEqual(self.total(issue(), prs=draft), self.total(issue()))


# 22:00 on 2026-10-23 in New York, already 2026-10-24 in UTC.
LATE = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)


class LocalDateTest(unittest.TestCase):
    def test_local_day_start_is_local_midnight(self):
        start = score.local_day_start(date(2026, 10, 24))
        self.assertEqual(start, datetime(2026, 10, 24, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(score.parse_ts("2026-10-24"), start)

    def test_due_today_is_today_in_the_evening(self):
        # The UTC date is already the 24th; locally the 23rd is still today.
        _, terms = score.score(issue(due_date="2026-10-23"), ctx(now=LATE), WEIGHTS)
        deadline = {n: (v, w) for n, v, w in terms}["deadline"]
        self.assertEqual(deadline, (2, "due 2026-10-23, in 0d"))

    def test_due_tomorrow_is_one_day_out_at_any_hour(self):
        for now in (LATE, datetime(2026, 10, 23, 4, 30, tzinfo=timezone.utc)):
            _, terms = score.score(issue(due_date="2026-10-24"), ctx(now=now), WEIGHTS)
            self.assertIn("in 1d", {n: w for n, _, w in terms}["deadline"], now)

    def test_due_dates_count_calendar_days_across_dst(self):
        # 00:30 on 2026-11-01, the 25-hour day DST ends; and 23:30 on
        # 2027-03-13, the evening before the 23-hour day it starts.
        cases = (
            (datetime(2026, 11, 1, 4, 30, tzinfo=timezone.utc), "2026-11-02", "in 1d"),
            (datetime(2027, 3, 14, 4, 30, tzinfo=timezone.utc), "2027-03-15", "in 2d"),
        )
        for now, due, want in cases:
            _, terms = score.score(issue(due_date=due), ctx(now=now), WEIGHTS)
            self.assertIn(want, {n: w for n, _, w in terms}["deadline"], due)

    def test_sprint_end_names_the_local_day(self):
        i = issue(sprint_end="2026-10-24T02:00:00Z")  # 22:00 on the 23rd locally
        _, terms = score.score(i, ctx(now=NOW), WEIGHTS)
        self.assertIn("sprint ends 2026-10-23", {n: w for n, _, w in terms}["deadline"])

    def test_a_snooze_until_tomorrow_holds_through_the_local_evening(self):
        self.assertTrue(views._in_future("2026-10-24", LATE))
        after = datetime(2026, 10, 24, 4, 1, tzinfo=timezone.utc)
        self.assertFalse(views._in_future("2026-10-24", after))


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

    def test_a_blocker_of_unknown_status_blocks(self):
        self.assertEqual(
            score.is_blocked(issue(), [link("inward", None)], CFG), (True, ["link"])
        )

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
        ("https://github.com/o/r/pull/1", 1, "ABC-1 fix", 0, "author", "APPROVED", "success", "ABC-1"),
        ("https://github.com/o/r/pull/2", 2, "ABC-2 wip", 0, "author", "REVIEW_REQUIRED", "pending", "ABC-2"),
        ("https://github.com/o/r/pull/3", 3, "their change", 0, "reviewer", None, "success", None),
        ("https://github.com/o/r/pull/4", 4, "no review rule", 0, "author", None, "success", None),
        # A draft is waiting on nobody, even where a review is required.
        ("https://github.com/o/r/pull/5", 5, "draft", 1, "author", "REVIEW_REQUIRED", "success", None),
    ]  # fmt: skip
    conn.executemany(
        "INSERT INTO prs (url, repo, number, title, is_draft, role, review_decision,"
        " ci_state, issue_key) VALUES (?, 'o/r', ?, ?, ?, ?, ?, ?, ?)",
        prs,
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

    def test_blocking_my_own_issue_is_not_waiting_on_me(self):
        self.conn.executemany(
            "INSERT INTO links (issue_key, linked_key, link_type, direction,"
            " linked_status_category) VALUES (?, ?, 'Blocks', ?, 'new')",
            [("ABC-2", "ABC-9", "outward"), ("ABC-9", "ABC-2", "inward")],
        )
        self.conn.commit()
        issues, _ = views.waiting_on_me(self.mirror())
        self.assertEqual([i["key"] for i, _ in issues], ["ABC-4"])

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

    def test_a_recent_update_keeps_an_old_category_off_stale(self):
        self.conn.execute(
            "INSERT INTO issues (source, kind, key, status_name, status_category,"
            " status_category_changed, updated, assignee, labels, scope) VALUES"
            " ('jira', 'ticket', 'ABC-20', 'In Progress', 'indeterminate', ?, ?, ?,"
            " '[]', 'search')",
            (ts(NOW - timedelta(days=20)), ts(NOW - timedelta(days=1)), ME),
        )
        self.conn.commit()
        keys = [i["key"] for i, _ in views.stale(self.mirror())]
        self.assertEqual(keys, ["ABC-7"])

    def test_epics_rollup(self):
        # ABC-5 is blocked by its inward link from ABC-4, which is open.
        (g,) = views.epics(self.mirror())
        self.assertEqual(
            (g["key"], g["total"], g["done"], g["blocked"]), ("EPIC-1", 3, 1, 1)
        )

    def set_parent_summary(self, key, summary):
        raw = {"key": key, "fields": {"parent": {"key": "EPIC-1",
               "fields": {"summary": summary}}}}  # fmt: skip
        self.conn.execute(
            "UPDATE issues SET raw_json = ? WHERE key = ?", (json.dumps(raw), key)
        )
        self.conn.commit()

    def card(self, page, key):
        (text,) = [
            c for c in page.split('<div class="card"') if f'data-key="{key}"' in c
        ]
        return text

    def test_card_leads_with_the_title_and_shows_the_epic(self):
        self.set_parent_summary("ABC-1", "Checkout <rewrite>")
        page = views.render("/now", self.conn, CFG, NOW)
        one = self.card(page, "ABC-1")
        self.assertRegex(
            one, r'<a class="title" href="[^"]*/browse/ABC-1" target="_blank" '
            r'rel="noopener">summary ABC-1</a> '
            r'<span class="ref">\(ABC-1\)</span>',
        )  # fmt: skip
        self.assertIn('<div class="epic">◆ Checkout &lt;rewrite&gt;</div>', one)
        self.assertNotIn('class="epic"', self.card(page, "ABC-2"))

    def test_status_icon_by_category_and_blocked(self):
        page = views.render("/now", self.conn, CFG, NOW)
        # ABC-1 is blocked by ABC-8, which is open.
        self.assertIn('aria-label="Blocked: ', self.card(page, "ABC-1"))
        self.assertIn('class="icon s-', self.card(page, "ABC-2"))
        self.assertEqual(
            views.status_icon("done", "Shipped"),
            '<span class="icon s-done" role="img" aria-label="Done: Shipped"'
            ' data-tip="Done: Shipped">●</span>',
        )
        self.assertIn('aria-label="Unknown status"', views.status_icon(None, None))

    def test_ignoring_an_issue_or_its_epic_hides_it(self):
        before = [i["key"] for i in views.now_items(self.mirror())]
        self.assertIn("ABC-1", before)
        self.assertIn("ABC-4", before)  # a child of EPIC-1
        self.conn.executemany(
            "INSERT INTO ignores (source, item_key) VALUES ('jira', ?)",
            [("ABC-1",), ("EPIC-1",)],
        )
        self.conn.commit()
        m = self.mirror()
        after = [i["key"] for i in views.now_items(m)]
        self.assertNotIn("ABC-1", after)
        self.assertNotIn("ABC-4", after)
        self.assertEqual(views.epics(m), [])
        self.assertEqual([i["key"] for i, _ in views.waiting_on(m)[0]], [])
        footer = views.render_ignored(m.conn)
        self.assertIn("Ignored (2)", footer)
        self.assertIn("hides 3 child issues", footer)
        self.assertEqual(footer.count('data-ov="unignore"'), 2)

    def test_ignoring_a_pr_hides_it_from_every_lane(self):
        url = "https://github.com/o/r/pull/2"
        page = views.render("/waiting-on", self.conn, CFG, NOW)
        self.assertIn(f'data-source="github" data-key="{url}"', page)
        self.conn.execute(
            "INSERT INTO ignores (source, item_key) VALUES ('github', ?)", (url,)
        )
        self.conn.commit()
        m = self.mirror()
        self.assertNotIn(url, [p["url"] for p, _ in views.waiting_on(m)[1]])
        self.assertNotIn(url, [p["url"] for p in m.prs_by_issue.get("ABC-2", [])])
        footer = views.render_ignored(m.conn)
        self.assertIn("Ignored (1)", footer)
        self.assertIn("ABC-2 wip (o/r#2)", footer)
        self.assertIn('data-ov="unignore"', footer)

    def add_issue(self, key, assignee, parent, reporter=None, scope="search"):
        self.conn.execute(
            "INSERT INTO issues (source, kind, key, summary, status_category,"
            " assignee, reporter, parent_key, labels, scope)"
            " VALUES ('jira', 'ticket', ?, ?, 'new', ?, ?, ?, '[]', ?)",
            (key, f"summary {key}", assignee, reporter, parent, scope),
        )
        self.conn.commit()

    def test_epics_split_by_how_the_user_relates_to_them(self):
        # The seeded epic has a child assigned to the user: involved.
        self.add_issue("EPIC-2", ME, None)  # the epic itself is mine
        self.add_issue("ABC-20", THEM, "EPIC-2")
        self.add_issue("ABC-21", THEM, "EPIC-3", reporter=ME)  # reported a child
        self.add_issue("ABC-22", THEM, "EPIC-4", scope="linked")  # linked only
        self.add_issue("ABC-23", THEM, "EPIC-5", scope="linked")
        self.conn.execute(
            "INSERT INTO pins (source, item_key) VALUES ('jira', 'EPIC-5')"
        )
        self.conn.commit()
        roles = {g["key"]: g["role"] for g in views.epics(self.mirror())}
        self.assertEqual(
            roles,
            {"EPIC-1": "involved", "EPIC-2": "mine", "EPIC-3": "involved",
             "EPIC-4": "other", "EPIC-5": "pinned"},
        )  # fmt: skip
        page = views.render("/epics", self.conn, CFG, NOW)
        others = page.split('data-section="other"')[1]
        self.assertIn("Other epics (1)", others)
        self.assertIn('data-key="EPIC-4"', others)
        self.assertNotIn('data-key="EPIC-4"', page.split('data-section="other"')[0])

    def test_view_cards_carry_the_board_controls(self):
        page = views.render("/waiting-on", self.conn, CFG, NOW)
        issue = self.card(page, "ABC-1")
        for act in ("pin", "focus", "snooze", "ignore", "note"):
            self.assertIn(f'data-ov="{act}"', issue, act)
        pr = page.split('data-key="https://github.com/o/r/pull/2"')[1]
        pr = pr.split('<div class="card"')[0]
        self.assertIn('data-ov="snooze"', pr)
        self.assertNotIn('data-ov="pin"', pr)

    def test_single_lane_views_have_no_heading(self):
        for path in ("/now", "/stale"):
            page = views.render(path, self.conn, CFG, NOW)
            self.assertNotIn("<h2>", page, path)
        page = views.render("/waiting-on", self.conn, CFG, NOW)
        self.assertIn("<h2>Issues (", page)
        self.assertIn("<h2>Pull requests (", page)

    def test_no_ignored_footer_when_nothing_is_ignored(self):
        self.assertEqual(views.render_ignored(self.conn), "")

    def test_ignoring_an_epic_hides_its_grandchildren(self):
        # A sub-task of a story that sits under the seeded epic.
        self.add_issue("ABC-30", ME, "ABC-4")
        self.assertIn("ABC-30", [i["key"] for i in views.now_items(self.mirror())])
        self.conn.execute(
            "INSERT INTO ignores (source, item_key) VALUES ('jira', 'EPIC-1')"
        )
        self.conn.commit()
        self.assertNotIn("ABC-30", [i["key"] for i in views.now_items(self.mirror())])
        self.assertIn("hides 4 child issues", views.render_ignored(self.conn))

    def test_the_ancestry_walk_survives_a_parent_cycle(self):
        parents = {"A-1": "A-2", "A-2": "A-1"}
        self.assertFalse(views.under_ignored("A-1", "A-2", parents.get, {"Z-1"}))
        self.assertTrue(views.under_ignored("A-1", "A-2", parents.get, {"A-2"}))
        self.add_issue("CYC-1", ME, "CYC-2")
        self.add_issue("CYC-2", ME, "CYC-1")
        self.conn.execute(
            "INSERT INTO ignores (source, item_key) VALUES ('jira', 'CYC-1')"
        )
        self.conn.commit()
        self.assertIn("CYC-1", views.render_ignored(self.conn))

    def test_an_untitled_ignored_pr_is_named_by_its_url(self):
        url = "https://github.com/o/r/pull/2"
        self.conn.execute("UPDATE prs SET title = NULL WHERE url = ?", (url,))
        self.conn.execute(
            "INSERT INTO ignores (source, item_key) VALUES ('github', ?)", (url,)
        )
        self.conn.commit()
        self.assertIn(f"{url} (o/r#2)", views.render_ignored(self.conn))

    def test_tooltips_say_what_snooze_and_an_epic_pin_do(self):
        page = views.render("/waiting-on", self.conn, CFG, NOW)
        self.assertIn("hidden from Now and Needs, dimmed elsewhere", page)
        page = views.render("/epics", self.conn, CFG, NOW)
        self.assertIn("Keep it on Epics; while it is open it also shows on Now", page)

    def test_epic_name_falls_back_to_a_childs_copy(self):
        self.set_parent_summary("ABC-4", "Named by the child")
        (g,) = views.epics(self.mirror())
        self.assertEqual(g["summary"], "Named by the child")

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
        # A Jira instance: the fleet-only tabs are hidden, and their pages are
        # covered by test_task_dashboard_fleet_views.py.
        for path in views.ROUTES:
            if path in fleet_views.ONLY:
                continue
            status, page = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(f'href="{path}" aria-current=page', page, path)
            for href, _ in views.TABS:
                if href in fleet_views.ONLY:
                    self.assertNotIn(f'href="{href}"', page, path)
                else:
                    self.assertIn(f'href="{href}"', page, path)
            cards = re.findall(
                r'<div class="card" data-source="jira" data-key="([^"]+)"', page
            )
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

    def test_every_page_has_the_filter_box_beside_sync_now(self):
        for path in (
            "/now",
            "/waiting-on-me",
            "/waiting-on",
            "/stale",
            "/epics",
            "/board",
        ):
            with self.subTest(path):
                page = self.get(path)[1]
                self.assertEqual(page.count('id="filter"'), 1)
                header = page[page.index("<header") : page.index("</header>")]
                self.assertLess(header.index('id="filter"'), header.index('id="sync"'))

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
