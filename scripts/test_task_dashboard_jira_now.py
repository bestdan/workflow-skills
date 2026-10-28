#!/usr/bin/env python3
"""Jira tickets as a source of the shared Now ranker, and the blocked tier
(fleet_now.py; dev_docs/designs/2026-10-27-shared-now-ranker.md, slice 3).

Hermetic: a temp DB seeded by hand, on a Jira instance with
`github.scope = "me"` unless a test says otherwise.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["TZ"] = "America/New_York"
time.tzset()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task-dashboard"))

import agent_view  # type: ignore[import-not-found]  # noqa: E402
import candidate  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_now  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import needs  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import views  # type: ignore[import-not-found]  # noqa: E402

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
ME = "Pat Example"
CFG = config.deep_merge(
    config.DEFAULTS,
    {
        "jira": {
            "site": "example.invalid",
            "board_ids": [77],
            "fields": {"sprint": "s", "flagged": "f"},
            "blocked_statuses": ["On Hold"],
            "me": ME,
        },
    },
)


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Case(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.conn = db.connect(Path(self._td.name) / "d.db")
        self.addCleanup(self.conn.close)

    def ticket(self, key, category="indeterminate", status="In Progress", **kw):
        row = {
            "summary": f"summary {key}",
            "status_name": status,
            "status_category": category,
            "status_category_changed": ts(NOW - timedelta(days=1)),
            "assignee": ME,
            "priority": "Medium",
            "labels": "[]",
            "flagged": 0,
            "sprint_state": "active",
            "sprint_name": "Sprint 5",
            "scope": "search",
            **kw,
        }
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO issues (source, kind, key, {cols})"
            f" VALUES ('jira', 'ticket', ?, {marks})",
            (key, *row.values()),
        )
        self.conn.commit()

    def pr(self, number, role, ci="success", decision=None):
        url = f"https://github.com/o/r/pull/{number}"
        self.conn.execute(
            "INSERT INTO prs (url, repo, number, title, is_draft, role,"
            " review_decision, ci_state, raw_json) VALUES (?, 'o/r', ?, ?, 0, ?,"
            " ?, ?, ?)",
            (url, number, f"pr {number}", role, decision, ci,
             json.dumps({"createdAt": ts(NOW - timedelta(days=2))})),
        )  # fmt: skip
        self.conn.commit()
        return url

    def shown(self, cfg=CFG):
        return fleet_now.build(self.conn, cfg, NOW, overlay.load(self.conn)).shown

    def by_key(self, cfg=CFG):
        return {r.need.item[1]: r for r in self.shown(cfg)}


class TierMappingTests(Case):
    def test_in_progress_is_started_and_to_do_in_the_sprint_is_the_rest(self):
        self.ticket("ABC-1")
        self.ticket("ABC-2", "new", "To Do")
        shown = self.by_key()
        self.assertEqual(shown["ABC-1"].category, "started")
        self.assertEqual(shown["ABC-1"].tier[1], "started")
        self.assertEqual(shown["ABC-2"].category, "ready")
        self.assertEqual(shown["ABC-2"].tier[1], fleet_now.REST)

    def test_a_to_do_ticket_outside_the_sprint_is_left_out_unless_pinned(self):
        self.ticket("ABC-3", "new", "To Do", sprint_state=None, sprint_name=None)
        self.ticket("ABC-4", "new", "To Do", sprint_state="closed")
        self.assertEqual(self.by_key(), {})
        overlay.pin(self.conn, ("jira", "ABC-3"), NOW)
        self.assertEqual(self.by_key()["ABC-3"].category, "ready")

    def test_someone_elses_pinned_ticket_is_named_a_pinned_ticket(self):
        self.ticket("ABC-6", assignee="Sam Sample")
        overlay.pin(self.conn, ("jira", "ABC-6"), NOW)
        r = self.by_key()["ABC-6"]
        self.assertEqual(r.why, "pinned ticket, In Progress")
        self.ticket("ABC-1")
        self.assertEqual(self.by_key()["ABC-1"].why, "your ticket, In Progress")

    def test_done_someone_elses_and_ignored_tickets_are_left_out(self):
        self.ticket("ABC-5", "done", "Done")
        self.ticket("ABC-6", assignee="Sam Sample")
        self.ticket("ABC-7")
        self.conn.execute(
            "INSERT INTO ignores (source, item_key) VALUES ('jira', 'ABC-7')"
        )
        self.conn.commit()
        self.assertEqual(self.by_key(), {})

    def test_a_ticket_brings_its_jira_terms_and_not_review_or_blocked(self):
        self.ticket("ABC-1", priority="Highest")
        r = self.by_key()["ABC-1"]
        names = [name for name, _, _ in r.terms]
        for name in fleet_now.JIRA_TERMS:
            self.assertIn(name, names)
        self.assertNotIn("review", names)
        self.assertNotIn("blocked", names)
        self.assertEqual(dict((n, v) for n, v, _ in r.terms)["priority"], 12)
        self.assertAlmostEqual(r.total, sum(v for _, v, _ in r.terms))
        self.assertIn("priority +12 (Highest (urgent))", fleet_now.why_line(r))

    def test_jira_off_ranks_no_tickets(self):
        self.ticket("ABC-1")
        cfg = config.deep_merge(CFG, {"jira": {"enabled": False}})
        self.assertEqual(self.by_key(cfg), {})


class JiraBlockedTests(Case):
    def assert_blocked(self, key, signal):
        r = self.by_key()[key]
        self.assertEqual(r.tier[1], fleet_now.BLOCKED)
        self.assertTrue(fleet_now.why_line(r).startswith("ship: blocked · score "))
        self.assertIn(f"blocked: {signal}", r.why)

    def test_the_blocked_label(self):
        self.ticket("ABC-1", labels=json.dumps(["blocked"]))
        self.assert_blocked("ABC-1", "label")

    def test_flagged(self):
        self.ticket("ABC-1", flagged=1)
        self.assert_blocked("ABC-1", "flagged")

    def test_a_blocked_status(self):
        self.ticket("ABC-1", status="On Hold")
        self.assert_blocked("ABC-1", "status")

    def test_an_unresolved_inward_blocks_link(self):
        self.ticket("ABC-1")
        self.conn.execute(
            "INSERT INTO links (issue_key, linked_key, link_type, direction,"
            " linked_status_category) VALUES ('ABC-1', 'ABC-8', 'Blocks', 'inward',"
            " 'indeterminate')"
        )
        self.conn.commit()
        self.assert_blocked("ABC-1", "link")

    def test_an_unblocked_ticket_keeps_its_tier(self):
        self.ticket("ABC-1")
        self.assertEqual(self.by_key()["ABC-1"].tier[1], "started")


class GithubIssueBlockedTests(unittest.TestCase):
    def test_the_label_or_an_open_blocked_by(self):
        self.assertTrue(fleet_now.issue_blocked({"labels": ["blocked"]}))
        self.assertTrue(fleet_now.issue_blocked({"labels": [], "blocked_by": 2}))
        self.assertFalse(fleet_now.issue_blocked({"labels": [], "blocked_by": 0}))
        self.assertFalse(fleet_now.issue_blocked({"labels": ["prio:1"]}))

    def test_an_issue_candidate_carries_it(self):
        row = {"labels": ["status:3_started"], "blocked_by": 1}
        need = needs.Need(
            item=("github", "u"), kind="issue", title="t", since=NOW, url="u",
            state="s", row=row,
        )  # fmt: skip
        self.assertTrue(fleet_now._candidate(need, "started", "x").blocked)
        pr = needs.Need(
            item=("github", "p"), kind="pr", title="t", since=NOW, url="p",
            state="s", row=row,
        )  # fmt: skip
        self.assertFalse(fleet_now._candidate(pr, "own_pr", "x").blocked)

    def test_the_issue_query_reads_blocked_by(self):
        self.assertIn(
            "blockedBy(first: 50) { nodes { state } }", github_fleet.ISSUE_QUERY
        )
        node = {
            "url": "https://github.com/o/r/issues/1", "number": 1, "title": "t",
            "repository": {"nameWithOwner": "o/r"},
            "blockedBy": {"nodes": [{"state": "OPEN"}, {"state": "CLOSED"}]},
        }  # fmt: skip
        self.assertEqual(github_fleet._issue_row(node)["blocked_by"], 1)
        del node["blockedBy"]
        self.assertIsNone(github_fleet._issue_row(node)["blocked_by"])


class BlockedTierOrderTests(Case):
    def ranked(self, key, category, total, blocked=False):
        need = needs.Need(
            item=("github", key), kind="pr", title=key, since=NOW, url=key, state="s"
        )
        c = candidate.Candidate(need, category, "why", blocked=blocked)
        return fleet_now.Ranked(c, total=total)

    def test_a_blocked_quick_win_sorts_below_every_unblocked_item(self):
        ranked = [
            self.ranked("win", "quick_win", 10, blocked=True),
            self.ranked("own", "own_pr", 1),
            self.ranked("ready", "ready", 1.5),
            self.ranked("started", "started", 5),
        ]
        keys = [
            r.need.item[1] for r in fleet_now.order(ranked, overlay.load(self.conn))
        ]
        self.assertEqual(keys, ["started", "ready", "own", "win"])
        self.assertEqual(fleet_now.Ranked(ranked[0].candidate).tier[1], "blocked")

    def test_blocked_has_no_effect_outside_ship(self):
        r = self.ranked("x", "est1_issue", 0, blocked=True)
        r.candidate.mode = fleet_now.LITTLE
        self.assertEqual(r.tier[1], "est:1 issue")


class ReviewRequestTests(Case):
    def test_a_scope_me_instance_ranks_review_requests_and_my_prs(self):
        theirs = self.pr(3, "reviewer")
        approved = self.pr(4, "author", decision="APPROVED")
        waiting = self.pr(5, "author", ci="pending")
        shown = self.by_key()
        self.assertEqual(shown[theirs].category, "others_pr")
        self.assertEqual(shown[theirs].why, "review requested")
        self.assertEqual(shown[approved].category, "own_approved")
        self.assertNotIn(waiting, shown)  # waits on CI, not on me

    def test_an_owner_instance_takes_review_requests_from_needs_you(self):
        theirs = self.pr(3, "reviewer")
        cfg = config.deep_merge(CFG, {"github": {"scope": "owner"}})
        self.assertNotIn(theirs, self.by_key(cfg))

    def test_github_off_ranks_no_prs(self):
        self.pr(3, "reviewer")
        self.pr(4, "author", decision="APPROVED")
        cfg = config.deep_merge(CFG, {"github": {"enabled": False}})
        self.assertEqual(self.by_key(cfg), {})
        page = fleet_now.render(self.conn, cfg, NOW)
        self.assertIn("github.enabled is false", page)


class OneNowTests(Case):
    """The separate Jira Now and its lane are gone: one ranked list."""

    def test_the_jira_now_is_removed(self):
        self.assertFalse(hasattr(views, "now_items"))
        self.assertFalse(hasattr(views, "jira_now_lane"))

    def test_a_jira_only_instance_now_lists_tickets_with_their_tier(self):
        self.ticket("ABC-1")
        self.ticket("ABC-2", "new", "To Do", flagged=1)
        self.pr(3, "reviewer")
        doc = agent_view.build(self.conn, CFG, "now", NOW)
        self.assertEqual([lane["id"] for lane in doc["lanes"]], ["next"])
        items = {i["key"]: i for i in doc["lanes"][0]["items"]}
        self.assertEqual(items["ABC-1"]["tier"], "started")
        self.assertEqual(items["ABC-1"]["kind"], "ticket")
        self.assertEqual(items["ABC-2"]["tier"], "blocked")
        self.assertTrue(items["ABC-2"]["why"].startswith("ship: blocked"))
        self.assertEqual(list(items)[-1], "ABC-2")
        self.assertIn("https://github.com/o/r/pull/3", items)

    def test_the_page_is_one_lane(self):
        self.ticket("ABC-1")
        page = views.render("/now", self.conn, CFG, NOW)
        self.assertEqual(page.count('<section class="lane"'), 1)
        self.assertIn('data-key="ABC-1"', page)
        self.assertNotIn('data-section="now"', page)


if __name__ == "__main__":
    unittest.main()
