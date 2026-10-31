#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/fleet_views.py.

A fixture DB is built through the real writers (github_fleet.write and
snapshot_adapter.ingest), then the Stale page and Needs you's Finished group
are rendered from it, directly and through the in-process server. No gh, git or
network is touched: the server runs with a fake sync function. Needs Python
3.11: run via scripts/test-task-dashboard-fleet-views.sh.
"""

import http.client
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import agent_view  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_views  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import include  # type: ignore[import-not-found]  # noqa: E402
import needs  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import snapshot_adapter  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402
import views  # type: ignore[import-not-found]  # noqa: E402

NOW = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
LAST_VISIT = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
REPO = "bestdan/workflow-skills"
WT = "/h/src/worktrees/workflow-skills"


def pr_url(n, repo=REPO):
    return f"https://github.com/{repo}/pull/{n}"


def closed(n, at, merged=True, head=None):
    return {
        "kind": "closed_pr",
        "key": pr_url(n),
        "url": pr_url(n),
        "repo": REPO,
        "number": n,
        "title": f"closed {n}",
        "head_ref": head or f"bestdan/task-{n}",
        "merged": merged,
        "status": "merged" if merged else "closed",
        "closed_at": at,
        "merged_at": at if merged else None,
        "created": "2026-10-01T00:00:00Z",
        "updated": at,
    }


def open_pr(n, created):
    return {
        "kind": "pr",
        "key": pr_url(n),
        "url": pr_url(n),
        "repo": REPO,
        "number": n,
        "title": f"open {n}",
        "in_queue": False,
        "created": created,
    }


DESIGN_URL = f"https://github.com/{REPO}/blob/main/dev_docs/designs/2026-10-23-x.md"
OTHER_DESIGN = "https://github.com/bestdan/dotfiles/blob/main/dev_docs/designs/y.md"

GITHUB_ROWS = [
    closed(1135, "2026-10-23T02:31:10Z"),  # merged after the last visit
    closed(1100, "2026-10-20T10:00:00Z"),  # merged before it
    closed(1101, "2026-10-23T05:00:00Z", merged=False),
    open_pr(900, "2026-09-01T00:00:00Z"),  # old, no evidence: Check
    open_pr(1140, "2026-10-23T10:00:00Z"),  # new since the visit
    {
        "kind": "issue",
        "key": f"https://github.com/{REPO}/issues/950",
        "url": f"https://github.com/{REPO}/issues/950",
        "repo": REPO,
        "number": 950,
        "title": "needs a human",
        "labels": ["auto:human-review-needed"],
        "created": "2026-10-23T09:00:00Z",
    },
    {
        "kind": "design",
        "key": DESIGN_URL,
        "url": DESIGN_URL,
        "repo": REPO,
        "path": "dev_docs/designs/2026-10-23-x.md",
        "title": "x design",
        "status": "proposed",
        "created": "2026-10-23",
    },
    {
        "kind": "design",
        "key": OTHER_DESIGN,
        "url": OTHER_DESIGN,
        "repo": "bestdan/dotfiles",
        "path": "dev_docs/designs/y.md",
        "title": "y design",
        "status": "proposed",
        "created": "2026-08-01",
    },
]


def worktree(name, branch, state, reason="", dirty=False, path=None):
    return {
        "repo": "workflow-skills",
        "path": path or f"{WT}/{name}",
        "branch": branch,
        "head": "a" * 40,
        "dirty": dirty,
        "unpushed": 0,
        "landed": {"state": state, "reason": reason},
    }


def handoff(name, expires, created="2026-10-01"):
    return {
        "path": f"{WT}/.handoffs/{name}.md",
        "mtime": "2026-10-01T00:00:00.000Z",
        "aim": f"aim {name}",
        "branch": None,
        "pr": None,
        "expires": expires,
        "created": created,
    }


_V1 = object()


def ev(sid, ts, event, cwd="/h/src/dotfiles", notification=None, asked=_V1):
    """A hook line: format 1 unless `asked` is given, which makes it format 2."""
    line = {"v": 1, "ts": ts, "event": event, "session_id": sid, "cwd": cwd}
    if notification:
        line["notification"] = notification
    if asked is not _V1:
        line.update(v=2, asked=asked)
    return line


SNAPSHOT = {
    "schema": 1,
    "host": "mini",
    "taken_at": "2026-10-23T20:00:00.000Z",
    "worktrees": [
        worktree(
            "task-1135",
            "bestdan/task-1135",
            "landed",
            "branch bestdan/task-1135 landed — its tip is the head of a PR merged into main",
        ),
        worktree(
            "task-1100", "bestdan/task-1100", "landed", "landed — merged", dirty=True
        ),
        worktree("task-1200", "bestdan/task-1200", "not_landed", "no merged PR"),
        worktree(
            "fresh",
            "bestdan/fresh",
            "landed",
            "branch bestdan/fresh landed — its tip is already on origin/main",
        ),
        worktree(
            "main", "main", "landed", "on origin/main", path="/h/src/workflow-skills"
        ),
    ],
    "handoffs": [
        handoff("dated", "2026-10-20"),
        handoff("when-merged", "when #1135 merges"),
        handoff("vague", "when the spike lands", created="2026-09-01"),
        handoff("future", "2026-12-01"),
        handoff("fresh", "when #4242 merges", created="2026-10-22"),
        handoff("compound", "when #1135 and #4242 merge"),
    ],
    "tmux": [],
    "sessions": [
        ev("s-done", "2026-10-23T08:00:00.000Z", "UserPromptSubmit"),
        ev("s-done", "2026-10-23T09:00:00.000Z", "Stop", asked=False),
        ev("s-asked", "2026-10-23T08:00:00.000Z", "UserPromptSubmit"),
        ev("s-asked", "2026-10-23T09:10:00.000Z", "Stop", asked=True),
        ev("s-asked", "2026-10-23T09:11:00.000Z", "Notification", None, "idle_prompt"),
        ev("s-askperm", "2026-10-23T08:00:00.000Z", "UserPromptSubmit"),
        ev("s-askperm", "2026-10-23T09:12:00.000Z", "Stop", asked=True),
        ev(
            "s-askperm",
            "2026-10-23T09:13:00.000Z",
            "Notification",
            None,
            "permission_prompt",
        ),
        ev("s-wait", "2026-10-23T07:00:00.000Z", "UserPromptSubmit"),
        ev("s-wait", "2026-10-23T08:00:00.000Z", "Notification", None, "idle_prompt"),
        ev("s-before", "2026-10-21T08:00:00.000Z", "UserPromptSubmit"),
        ev("s-before", "2026-10-21T09:00:00.000Z", "Stop"),
        ev("s-empty", "2026-10-10T08:00:00.000Z", "Notification", None, "idle_prompt"),
        ev("s-idle", "2026-10-12T08:00:00.000Z", "UserPromptSubmit"),
        ev("s-idle", "2026-10-12T09:00:00.000Z", "Stop"),
        ev(
            "s-landed",
            "2026-10-15T08:00:00.000Z",
            "UserPromptSubmit",
            f"{WT}/task-1135",
        ),
        ev("s-landed", "2026-10-15T09:00:00.000Z", "Stop", f"{WT}/task-1135/sub"),
        ev("s-ancient", "2026-08-01T09:00:00.000Z", "Stop"),
        ev("s-fresh", "2026-10-14T08:00:00.000Z", "UserPromptSubmit", f"{WT}/fresh"),
        ev("s-fresh", "2026-10-14T09:00:00.000Z", "Stop", f"{WT}/fresh"),
    ],
    "history": [],
}


def seed(conn, last_visit=LAST_VISIT):
    github_fleet.write(conn, GITHUB_ROWS, NOW)
    snapshot_adapter.ingest(conn, SNAPSHOT, NOW)
    if last_visit is not None:
        overlay.set_last_visit(conn, last_visit)


def fleet_cfg(**github):
    cfg = config.deep_merge(config.DEFAULTS, server_harness.USER_CONFIG)
    cfg["github"].update(enabled=True, scope="owner", **github)
    return cfg


def section(page, name):
    m = re.search(
        rf'<section class="lane[^"]*" data-section="{name}"[^>]*>(.*?)</section>',
        page,
        re.S,
    )
    assert m, f"no section {name}"
    return m.group(1)


def keys(html_text):
    return re.findall(r'data-key="([^"]*)"', html_text)


class FleetCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.conn = db.connect(Path(self._td.name) / "d.db")
        self.addCleanup(self.conn.close)

    def card(self, lane, text):
        cards = re.findall(r'<div class="card">(.*?)</div></div>', lane, re.S)
        found = [c for c in cards if text in c]
        self.assertEqual(len(found), 1, text)
        return found[0]


class FinishedTests(FleetCase):
    """The sessions Since you were away listed as finished, now Needs you's
    Finished group: the same query, after the same last visit."""

    def finished(self, since=LAST_VISIT):
        f = fleet_views.load(self.conn, fleet_cfg())
        return [s.session_id for s in fleet_views.finished_sessions(f, since)]

    def page(self, cfg=None, inc=None):
        return needs.render(self.conn, cfg or fleet_cfg(), NOW, inc)

    def test_only_a_turn_that_ended_without_asking_since_the_visit(self):
        seed(self.conn)
        # s-asked and s-askperm asked; s-wait's last event is a notification;
        # s-before finished before the visit.
        self.assertEqual(self.finished(), ["s-done"])
        group = section(self.page(), "finished")
        self.assertEqual(keys(group), ["s-done"])
        self.assertIn("<h2>Finished since you looked (1)</h2>", group)
        self.assertIn("on mini · turn ended 12h ago", group)
        self.assertIn('<span class="title">dotfiles</span>', group)

    def test_the_lane_marks_as_read_at_render_time(self):
        seed(self.conn)
        group = section(self.page(), "finished")
        # A lane, so a tab of its own, with Mark as read inside it.
        self.assertNotIn('<details class="group"', group)
        self.assertIn('data-ov="visit" data-at="2026-10-23T21:00:00Z"', group)
        self.assertIn(f'data-last-visit="{overlay.load(self.conn).last_visit}"', group)

    def test_rendering_does_not_move_the_visit(self):
        seed(self.conn)
        before = overlay.load(self.conn).last_visit
        self.page()
        self.assertEqual(overlay.load(self.conn).last_visit, before)

    def test_no_visit_covers_the_closed_window(self):
        seed(self.conn, last_visit=None)
        group = section(self.page(), "finished")
        self.assertIn("No visit recorded yet", group)
        self.assertIn(f"covers the last {github_fleet.CLOSED_WINDOW_DAYS} days", group)
        # s-before finished a turn two days ago, inside the window.
        self.assertIn("s-before", keys(group))

    def test_the_group_is_hidden_when_nothing_finished(self):
        seed(self.conn, last_visit=NOW)
        self.assertNotIn('data-section="finished"', self.page())

    def test_ignores_snoozes_and_the_include_filter_reach_it(self):
        seed(self.conn)
        off = include.Include("needs", frozenset({"tasks", "prs", "other"}), "url")
        self.assertNotIn('data-section="finished"', self.page(inc=off))
        overlay.snooze(self.conn, ("claude-hook", "s-done"), "2999-01-01")
        page = self.page()
        self.assertNotIn('data-section="finished"', page)
        self.assertIn('data-snoozed="1"', page)
        overlay.snooze(self.conn, ("claude-hook", "s-done"), "")
        overlay.ignore(self.conn, ("claude-hook", "s-done"), NOW)
        self.assertNotIn('data-key="s-done"', self.page())

    def test_fleet_off_still_lists_finished_sessions(self):
        seed(self.conn)
        cfg = fleet_cfg()
        cfg["github"]["scope"] = "me"
        cfg["ingest"]["machines"] = {"mini": "sha256:" + "0" * 64}
        page = self.page(cfg)
        self.assertEqual(keys(section(page, "finished")), ["s-done"])
        stale = fleet_views.render_stale(self.conn, cfg, NOW)
        self.assertNotIn(pr_url(900), stale)
        self.assertNotIn("(#1135) merged", stale)
        self.assertIn(f"worktree {WT}/task-1135", stale)  # the gate's own reason


class StaleTests(FleetCase):
    def stale(self):
        seed(self.conn)
        items = fleet_views.stale_items(fleet_views.load(self.conn), NOW)
        page = fleet_views.render_stale(self.conn, fleet_cfg(), NOW)
        return items, section(page, "close"), section(page, "check")

    def test_an_ignored_worktree_leaves_stale(self):
        _, close, _ = self.stale()
        self.assertIn(f"worktree {WT}/task-1135", close)
        overlay.ignore(self.conn, ("machine:mini", f"worktree:{WT}/task-1135"), NOW)
        page = fleet_views.render_stale(self.conn, fleet_cfg(), NOW)
        self.assertNotIn(f"worktree {WT}/task-1135", page)
        self.assertIn(f"worktree {WT}/task-1100", page)

    def test_an_ignored_pr_or_session_leaves_stale(self):
        _, _, check = self.stale()
        self.assertIn(pr_url(900), check)
        self.assertIn("session s-idle", check)
        overlay.ignore(self.conn, ("github", pr_url(900)), NOW)
        overlay.ignore(self.conn, ("claude-hook", "s-idle"), NOW)
        page = fleet_views.render_stale(self.conn, fleet_cfg(), NOW)
        self.assertNotIn(pr_url(900), page)
        self.assertNotIn("session s-idle", page)

    def test_a_landed_pr_worktree_shows_its_evidence_and_action(self):
        _, close, _ = self.stale()
        card = self.card(close, f"worktree {WT}/task-1135")
        self.assertIn("closed 1135 (#1135) merged 18h ago", card)
        self.assertIn(f"<code>worktree-remove.sh {WT}/task-1135</code>", card)

    def test_an_old_pr_with_no_evidence_is_under_check(self):
        _, close, check = self.stale()
        self.assertNotIn(pr_url(900), close)
        card = self.card(check, pr_url(900))
        self.assertIn("no evidence", card)
        self.assertNotIn(pr_url(1140), check)

    def test_a_dirty_landed_worktree_is_a_check_and_main_is_absent(self):
        _, close, check = self.stale()
        card = self.card(check, f"worktree {WT}/task-1100")
        self.assertIn("uncommitted changes", card)
        self.assertNotIn("/h/src/workflow-skills<", close + check)
        self.assertNotIn("task-1200", close + check)

    def test_a_landed_worktree_with_no_merged_pr_is_a_check(self):
        _, close, check = self.stale()
        self.assertNotIn(f"worktree {WT}/fresh", close)
        card = self.card(check, f"worktree {WT}/fresh")
        self.assertIn("no merged PR found for its branch", card)
        self.assertIn("its tip is already on origin/main", card)
        # A session in it is not closed on the worktree's say-so either.
        self.assertNotIn("session s-fresh", close)
        self.assertIn("no evidence", self.card(check, "session s-fresh"))

    def test_a_compound_merge_condition_is_a_check(self):
        _, close, check = self.stale()
        self.assertNotIn("aim compound", close)
        self.assertIn("cannot be checked", self.card(check, "aim compound"))

    def test_an_old_pr_puts_its_title_first_and_its_ref_after(self):
        _, _, check = self.stale()
        self.assertIn(
            f'<a class="title" href="{pr_url(900)}" target="_blank" rel="noopener">open 900</a>'
            f' <span class="ref">{REPO}#900</span>',
            check,
        )

    def test_handoffs_close_on_a_passed_date_or_a_merged_pr(self):
        _, close, check = self.stale()
        self.assertIn("expires 2026-10-20: passed", self.card(close, "aim dated"))
        self.assertIn(
            "expires `when #1135 merges`: closed 1135 (#1135) merged",
            self.card(close, "aim when-merged").replace("&#x27;", "'"),
        )
        self.assertIn("cannot be checked", self.card(check, "aim vague"))
        for absent in ("aim future", "aim fresh"):
            self.assertNotIn(absent, close + check)

    def test_sessions_by_evidence(self):
        _, close, check = self.stale()
        self.assertIn("no prompt was ever sent", self.card(close, "session s-empty"))
        self.assertIn("its worktree landed", self.card(close, "session s-landed"))
        self.assertIn("no evidence", self.card(check, "session s-idle"))
        for absent in ("s-done", "s-wait", "s-ancient"):
            self.assertNotIn(f"session {absent}", close + check)

    def test_age_orders_each_lane_oldest_first(self):
        items, _, _ = self.stale()
        for lane in ("close", "check"):
            ats = [i.at for i in items if i.lane == lane and i.at is not None]
            self.assertEqual(ats, sorted(ats), lane)


class TitleTests(FleetCase):
    """The page's filter box searches a card's `.title`, `.ref` and `.meta`."""

    def test_every_card_on_each_fleet_page_carries_a_title(self):
        seed(self.conn)
        pages = {
            "finished": section(needs.render(self.conn, fleet_cfg(), NOW), "finished"),
            "stale": fleet_views.render_stale(self.conn, fleet_cfg(), NOW),
        }
        for name, page in pages.items():
            lanes = re.findall(
                r'<section class="lane[^"]*"[^>]*>(.*?)</section>', page, re.S
            ) or [page]
            cards = [
                c
                for lane in lanes
                for c in re.split(r'<(?:div|details) class="card', lane)[1:]
            ]
            self.assertGreater(len(cards), 0, name)
            for card in cards:
                m = re.search(r'class="title"[^>]*>([^<]+)<', card)
                assert m is not None, (name, card[:200])
                self.assertTrue(m.group(1).strip(), (name, card[:200]))

    def test_an_untitled_merged_pr_is_named_by_its_number(self):
        at = datetime(2026, 10, 23, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(
            fleet_views._pr_merged({"number": 7}, at, NOW), "PR #7 merged 18h ago"
        )
        self.assertEqual(
            fleet_views._pr_merged({"number": 7, "title": "fix: x"}, at, NOW),
            "fix: x (#7) merged 18h ago",
        )


def failed_scan(host="mini", taken_at="2026-10-23T20:30:00.000Z"):
    """A schema 2 snapshot from `host` whose workflow-skills scan failed."""
    return {
        "schema": 2,
        "host": host,
        "taken_at": taken_at,
        "worktrees": [],
        "scan_errors": [{"repo": "workflow-skills", "step": "worktree_list"}],
        "handoffs": [],
        "tmux": [],
        "sessions": [],
        "history": [],
    }


class KeptRowTests(FleetCase):
    """Rows kept from a failed scan are never evidence."""

    def stale(self):
        seed(self.conn)
        snapshot_adapter.ingest(self.conn, failed_scan(), NOW)
        page = fleet_views.render_stale(self.conn, fleet_cfg(), NOW)
        return None, section(page, "close"), section(page, "check")

    def test_a_kept_landed_worktree_is_a_check_naming_the_failure(self):
        _, close, check = self.stale()
        self.assertNotIn(f"worktree {WT}/task-1135", close)
        card = self.card(check, f"worktree {WT}/task-1135")
        self.assertIn("kept from a failed scan (worktree_list failed 30m ago)", card)
        self.assertIn("last listed 60m ago", card)
        self.assertIn("closed 1135 (#1135) merged 18h ago", card)

    def test_a_kept_worktree_respects_ignore_and_the_include_filter(self):
        _, _, check = self.stale()
        self.assertIn(f"worktree {WT}/task-1135", check)
        page = fleet_views.render_stale(
            self.conn, fleet_cfg(), NOW,
            include.Include("stale", frozenset({"prs", "sessions"}), "url"),
        )  # fmt: skip
        self.assertNotIn(f"worktree {WT}/task-1135", page)
        overlay.ignore(self.conn, ("machine:mini", f"worktree:{WT}/task-1135"), NOW)
        page = fleet_views.render_stale(self.conn, fleet_cfg(), NOW)
        self.assertNotIn(f"worktree {WT}/task-1135", page)
        self.assertIn(f"worktree {WT}/task-1100", page)

    def test_a_session_in_a_kept_worktree_is_not_closed_by_it(self):
        _, close, check = self.stale()
        self.assertNotIn("session s-landed", close)
        self.assertIn("session s-landed", check)


class NavTests(unittest.TestCase):
    def test_the_away_tab_is_gone(self):
        jira = config.deep_merge(config.DEFAULTS, server_harness.USER_CONFIG)
        for cfg in (jira, fleet_cfg()):
            self.assertNotIn('href="/away"', views.nav("/now", cfg))
        self.assertIn('href="/needs"', views.nav("/now", fleet_cfg()))
        machines = config.deep_merge(jira, {"ingest": {"machines": {"mini": "x"}}})
        self.assertTrue(fleet_views.enabled(machines))
        self.assertFalse(fleet_views.enabled(jira))


class FleetRouteTests(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: NOW
        conn = db.connect(self.db_path)
        seed(conn)
        conn.close()

    def last_visit(self):
        conn = db.connect(self.db_path)
        try:
            return overlay.load(conn).last_visit
        finally:
            conn.close()

    def test_away_redirects_to_needs_you(self):
        for path in ("/away", "/away?include=prs"):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
            resp = c.getresponse()
            resp.read()
            c.close()
            self.assertEqual(resp.status, 302, path)
            # Its Finished group lives on Needs you, not on the home page.
            self.assertEqual(resp.getheader("Location"), "/needs", path)

    def test_loading_needs_you_does_not_move_last_visit(self):
        before = self.last_visit()
        for _ in range(2):
            status, page = self.request("GET", "/needs")
            self.assertEqual(status, 200)
            self.assertIn('data-key="s-done"', page)
        self.assertEqual(self.last_visit(), before)

    def test_mark_as_read_records_the_render_time(self):
        page = self.request("GET", "/needs")[1]
        m = re.search(r'data-ov="visit" data-at="([^"]+)"', section(page, "finished"))
        assert m is not None
        status, _ = self.request(
            "POST",
            "/overlay/visit",
            origin=f"http://127.0.0.1:{self.port}",
            body=[("token", self.token), ("at", m.group(1))],
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.last_visit(), "2026-10-23T21:00:00Z")
        page = self.request("GET", "/needs")[1]
        self.assertNotIn('data-section="finished"', page)

    def test_the_freshness_panel_has_a_row_per_machine(self):
        page = self.request("GET", "/")[1]
        header = page.split("<header", 1)[1].split("</header>", 1)[0]
        self.assertNotIn('class="machines"', header)
        self.assertIn(
            '<tr data-source="machine:mini" data-outcome="ok" class="warn"><td>mini</td>'
            '<td title="2026-10-23T20:00:00.000Z">60m</td>',
            header,
        )
        # No sync step has succeeded here, so the page is not "60m" fresh.
        self.assertIn("never synced", header)

    def test_stale_shows_the_fleet_lanes_under_jira(self):
        page = self.request("GET", "/stale")[1]
        self.assertIn("closed 1135 (#1135) merged", section(page, "close"))
        self.assertIn(pr_url(900), section(page, "check"))


class FleetSwitchTests(server_harness.ServerCase):
    """Each caller of the fleet switch under scope x ingest (#1232).

    Being a fleet means ingesting from other hosts. The tabs, the home page
    and the Stale lanes stay on scope "owner" or any ingest machine; Now's
    mode button and Health's why-not section show on every instance.
    """

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: NOW
        conn = db.connect(self.db_path)
        seed(conn)
        conn.close()

    def combos(self):
        # GitHub off with scope "owner" is a Jira-only instance: no owner read.
        for github, scope in ((True, "owner"), (True, "me"), (False, "owner")):
            for machines in ({"mini": "x"}, {}):
                cfg = self.httpd.app.cfg
                cfg["github"].update(enabled=github, scope=scope)
                cfg["ingest"]["machines"] = machines
                yield (github, scope, bool(machines)), cfg

    def test_each_caller_under_scope_and_ingest(self):
        for (github, scope, ingest), cfg in self.combos():
            with self.subTest(github=github, scope=scope, ingest=ingest):
                pages = (github and scope == "owner") or ingest
                self.assertEqual(fleet_views.enabled(cfg), ingest)
                self.assertEqual(fleet_views.fleet_pages(cfg), pages)
                # Tabs: the fleet's own, or the Jira ones they replace.
                nav = views.nav("/now", cfg)
                self.assertEqual('href="/prs"' in nav, pages)
                self.assertEqual('href="/waiting-on-me"' in nav, not pages)
                # Home page: Now on a fleet, else the Board.
                home = self.request("GET", "/")[1]
                self.assertEqual("<title>Now ·" in home, pages)
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                c.request("GET", "/away", headers={"Host": f"127.0.0.1:{self.port}"})
                resp = c.getresponse()
                resp.read()
                c.close()
                self.assertEqual(resp.getheader("Location"), "/needs" if pages else "/")
                # Stale: the fleet's lanes, on the page and in the agent view.
                stale = self.request("GET", "/stale")[1]
                self.assertEqual('data-section="close"' in stale, pages)
                conn = db.connect(self.db_path)
                try:
                    doc = agent_view.build(conn, cfg, "stale", NOW)
                finally:
                    conn.close()
                ids = [lane["id"] for lane in doc["lanes"]]
                self.assertEqual("close" in ids, pages)
                # Now's modes and why-not answers work on every instance.
                self.assertIn('id="mode"', self.request("GET", "/now")[1])
                health = self.request("GET", "/health")[1]
                self.assertIn('data-section="why-not"', health)


if __name__ == "__main__":
    unittest.main()
