#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/fleet_views.py.

A fixture DB is built through the real writers (github_fleet.write and
snapshot_adapter.ingest), then the Since you were away and Stale pages are
rendered from it, directly and through the in-process server. No gh, git or
network is touched: the server runs with a fake sync function. Needs Python
3.11: run via scripts/test-task-dashboard-fleet-views.sh.
"""

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

import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_views  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import include  # type: ignore[import-not-found]  # noqa: E402
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
    cfg["github"].update(enabled=True, fleet=True, **github)
    return cfg


def section(page, name):
    m = re.search(
        rf'<section class="lane" data-section="{name}"[^>]*>(.*?)</section>', page, re.S
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


class AwayTests(FleetCase):
    def away(self, cfg=None):
        return fleet_views.render_away(self.conn, cfg or fleet_cfg(), NOW)

    def test_lists_a_pr_merged_after_the_visit_and_omits_one_before(self):
        seed(self.conn)
        landed = section(self.away(), "landed")
        self.assertIn(pr_url(1135), keys(landed))
        self.assertNotIn(pr_url(1100), keys(landed))
        self.assertIn(pr_url(1101), keys(landed))
        self.assertIn("closed without merging", landed)
        self.assertLess(landed.index(pr_url(1101)), landed.index(pr_url(1135)))

    def test_sessions_that_finished_and_what_newly_waits(self):
        seed(self.conn)
        page = self.away()
        self.assertEqual(keys(section(page, "finished")), ["s-done"])
        waiting = keys(section(page, "waiting"))
        self.assertIn("s-wait", waiting)
        self.assertIn("s-asked", waiting)
        self.assertIn("asked (heuristic)", section(page, "waiting"))
        # A permission prompt after the question is what blocks it now.
        self.assertIn("s-askperm", waiting)
        card = re.search(
            r'data-key="s-askperm".*?</div>', section(page, "waiting"), re.S
        )
        assert card is not None
        self.assertNotIn("asked (heuristic)", card.group(0))
        self.assertIn("permission_prompt", card.group(0))
        self.assertIn(pr_url(1140), waiting)
        self.assertIn(f"https://github.com/{REPO}/issues/950", waiting)
        self.assertIn(DESIGN_URL, waiting)
        self.assertNotIn(pr_url(900), waiting)
        self.assertNotIn(OTHER_DESIGN, waiting)

    def test_rendering_does_not_move_the_visit(self):
        seed(self.conn)
        before = overlay.load(self.conn).last_visit
        page = self.away()
        self.assertEqual(overlay.load(self.conn).last_visit, before)
        self.assertIn('data-ov="visit" data-at="2026-10-23T21:00:00Z"', page)
        self.assertIn(f'data-last-visit="{before}"', page)

    def test_no_visit_covers_the_closed_window(self):
        seed(self.conn, last_visit=None)
        page = self.away()
        self.assertIn("No visit recorded yet", page)
        self.assertIn(pr_url(1100), keys(section(page, "landed")))

    def test_a_design_focus_puts_its_repo_first(self):
        seed(self.conn)
        overlay.set_focus(self.conn, ("github", DESIGN_URL), NOW)
        page = self.away()
        self.assertLess(
            page.index('data-section="focus"'), page.index('data-section="landed"')
        )
        focus = keys(section(page, "focus"))
        self.assertIn(pr_url(1135), focus)
        self.assertIn(pr_url(1140), focus)
        self.assertNotIn(pr_url(1135), keys(section(page, "landed")))
        # s-done ran in dotfiles, outside the focus design's repo.
        self.assertEqual(keys(section(page, "finished")), ["s-done"])

    def test_a_non_design_focus_adds_no_section(self):
        seed(self.conn)
        overlay.set_focus(self.conn, ("github", pr_url(1140)), NOW)
        self.assertNotIn('data-section="focus"', self.away())

    def test_fleet_off_hides_leftover_github_rows(self):
        seed(self.conn)
        cfg = fleet_cfg()
        cfg["github"]["fleet"] = False
        cfg["ingest"]["machines"] = {"mini": "sha256:" + "0" * 64}
        page = self.away(cfg)
        self.assertIn("github.fleet is off", page)
        self.assertNotIn("https://github.com/", page)
        self.assertEqual(keys(section(page, "finished")), ["s-done"])
        stale = fleet_views.render_stale(self.conn, cfg, NOW)
        self.assertNotIn(pr_url(900), stale)
        self.assertNotIn("PR #1135 merged", stale)
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
        self.assertIn("PR #1135 merged 18h ago", card)
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

    def test_handoffs_close_on_a_passed_date_or_a_merged_pr(self):
        _, close, check = self.stale()
        self.assertIn("expires 2026-10-20: passed", self.card(close, "aim dated"))
        self.assertIn(
            "expires `when #1135 merges`: PR #1135 merged",
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
        self.assertIn("PR #1135 merged 18h ago", card)

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


class MachinesLineTests(FleetCase):
    def test_each_host_with_its_age_and_a_stale_one_marked(self):
        seed(self.conn)  # mini at 20:00, 60 minutes before NOW
        fresh = dict(failed_scan("lindev", "2026-10-23T20:56:00.000Z"))
        fresh["scan_errors"] = []
        snapshot_adapter.ingest(self.conn, fresh, NOW)
        line = fleet_views.machines_line(self.conn, NOW)
        self.assertEqual(re.sub(r"<[^>]+>", "", line), "lindev 4m · mini 60m")
        self.assertIn('<span title="2026-10-23T20:56:00.000Z">lindev 4m</span>', line)
        self.assertIn(
            '<span class="stale" title="2026-10-23T20:00:00.000Z">mini 60m', line
        )

    def test_a_failed_scan_is_named_on_the_host(self):
        seed(self.conn)
        snapshot_adapter.ingest(self.conn, failed_scan(), NOW)
        line = fleet_views.machines_line(self.conn, NOW)
        self.assertIn("mini 30m (1 repo scan failed)", line)
        self.assertNotIn('class="stale"', line)

    def test_no_machines_renders_nothing(self):
        self.assertEqual(fleet_views.machines_line(self.conn, NOW), "")


class NavTests(unittest.TestCase):
    def test_away_tab_only_on_a_fleet_instance(self):
        jira = config.deep_merge(config.DEFAULTS, server_harness.USER_CONFIG)
        self.assertNotIn('href="/away"', views.nav("/now", jira))
        self.assertIn('href="/away"', views.nav("/now", fleet_cfg()))
        machines = config.deep_merge(jira, {"ingest": {"machines": {"mini": "x"}}})
        self.assertTrue(fleet_views.enabled(machines))
        self.assertFalse(fleet_views.enabled(jira))


class AwayRouteTests(server_harness.ServerCase):
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

    def test_loading_the_page_does_not_move_last_visit(self):
        before = self.last_visit()
        for _ in range(2):
            status, page = self.request("GET", "/away")
            self.assertEqual(status, 200)
            self.assertIn(pr_url(1135), page)
            self.assertIn("aria-current=page>Since you were away", page)
        self.assertEqual(self.last_visit(), before)

    def test_mark_as_read_records_the_render_time(self):
        page = self.request("GET", "/away")[1]
        m = re.search(r'data-ov="visit" data-at="([^"]+)"', page)
        assert m is not None
        at = m.group(1)
        status, _ = self.request(
            "POST",
            "/overlay/visit",
            origin=f"http://127.0.0.1:{self.port}",
            body=[("token", self.token), ("at", at)],
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.last_visit(), "2026-10-23T21:00:00Z")
        page = self.request("GET", "/away")[1]
        self.assertNotIn(pr_url(1135), section(page, "landed"))

    def test_header_shows_each_machines_age(self):
        page = self.request("GET", "/away")[1]
        header = page.split("<header", 1)[1].split("</header>", 1)[0]
        self.assertIn('<span class="machines">', header)
        self.assertIn("mini 60m", header)

    def test_stale_shows_the_fleet_lanes_under_jira(self):
        page = self.request("GET", "/stale")[1]
        self.assertIn("PR #1135 merged", section(page, "close"))
        self.assertIn(pr_url(900), section(page, "check"))


if __name__ == "__main__":
    unittest.main()
