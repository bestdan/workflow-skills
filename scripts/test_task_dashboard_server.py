#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/server.py.

Each case starts the real server in-process on an ephemeral 127.0.0.1 port,
over a temp DB, with a fake sync function in place of acli and gh. Requests go
through http.client so the Host and Origin headers can be forged. Needs Python
3.11: run via scripts/test-task-dashboard-server.sh.
"""

import contextlib
import http.client
import io
import json
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))

# mypy checks this file without scripts/task-dashboard on its path; the
# modules themselves are type-checked in their own group (scripts/typecheck.sh).
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402

USER_CONFIG = {
    "jira": {
        "site": "example.invalid",
        "board_ids": [77],
        "fields": {"sprint": "customfield_10303", "flagged": "customfield_10007"},
    },
    "github": {"enabled": False},
}

TS = "%Y-%m-%dT%H:%M:%SZ"


def make_config(**server_cfg):
    cfg = config.deep_merge(config.DEFAULTS, USER_CONFIG)
    cfg["server"].update(server_cfg)
    return cfg


class FakeSync:
    """Records each run's mode; a run blocks until `release` is set."""

    def __init__(self):
        self.modes = []
        self.started = threading.Semaphore(0)
        self.release = threading.Event()
        self.release.set()

    def __call__(self, mode):
        self.modes.append(mode)
        self.started.release()
        assert self.release.wait(10), "fake run never released"


# A fixture sprint covering every section the jira tracker can reach, plus two
# rows that must not appear: an issue outside the active sprint and a non-Jira
# row in the shared table.
ISSUES = [
    # key, status_category, labels, priority, created
    ("ABC-1", "new", [], "Medium", "2026-10-01T00:00:00Z"),
    ("ABC-2", "new", ["human-approval-requested"], "High", "2026-10-02T00:00:00Z"),
    ("ABC-3", "new", ["auto-eligible"], "Low", "2026-10-03T00:00:00Z"),
    ("ABC-4", "indeterminate", [], "Highest", "2026-10-04T00:00:00Z"),
    ("ABC-5", "indeterminate", ["blocked"], None, "2026-10-05T00:00:00Z"),
    ("ABC-6", "indeterminate", ["needs-review"], "Medium", "2026-10-06T00:00:00Z"),
    ("ABC-7", "done", ["blocked"], "Medium", "2026-10-07T00:00:00Z"),
    ("ABC-8", "indeterminate", [], "Low", "2026-10-08T00:00:00Z"),
    ("ABC-9", "indeterminate", [], "Highest", "2026-10-09T00:00:00Z"),
]


def seed(conn, *, issues=ISSUES):
    conn.execute(
        "INSERT INTO sprints (id, board_id, name, state) VALUES (5, 77, 'Sprint 5', 'active')"
    )
    for key, cat, labels, prio, created in issues:
        conn.execute(
            "INSERT INTO issues (source, kind, key, summary, status_name,"
            " status_category, priority, labels, created, sprint_id, sprint_name,"
            " sprint_state, scope) VALUES ('jira', 'ticket', ?, ?, 'Status', ?, ?,"
            " ?, ?, 5, 'Sprint 5', 'active', 'search')",
            (key, f"summary {key}", cat, prio, json.dumps(labels), created),
        )
    conn.execute(
        "INSERT INTO issues (source, kind, key, summary, status_category, labels,"
        " sprint_state) VALUES ('jira', 'ticket', 'ABC-99', 'old sprint', 'new',"
        " '[]', 'closed')"
    )
    conn.execute(
        "INSERT INTO issues (source, kind, key, summary, status_category, labels,"
        " sprint_state) VALUES ('machine:mini', 'session', 'ABC-98', 'not jira',"
        " 'new', '[]', 'active')"
    )
    conn.commit()


def add_run(conn, outcome, finished, error=None, kind="incremental"):
    conn.execute(
        "INSERT INTO sync_runs (kind, started_at, finished_at, outcome, error)"
        " VALUES (?, ?, ?, ?, ?)",
        (kind, finished.strftime(TS), finished.strftime(TS), outcome, error),
    )
    conn.commit()


class ServerCase(unittest.TestCase):
    extra_hosts: list = []
    fleet = False  # github.enabled and github.fleet both on when True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "dashboard.db"
        self.cfg = make_config(extra_allowed_hosts=list(self.extra_hosts))
        if self.fleet:
            self.cfg["github"].update(enabled=True, fleet=True)
        self.fake = FakeSync()
        self.httpd, self.syncer = server.build(
            self.cfg, self.db_path, 0, self.fake, run_at_start=False
        )
        self.port = self.httpd.server_address[1]
        self.token = self.httpd.app.token
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.syncer.start()

    def tearDown(self):
        self.fake.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.syncer.stop()
        self.tmp.cleanup()

    def conn(self):
        return sqlite3.connect(self.db_path)

    def request(self, method, path, *, host=None, origin=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}" if host is None else host}
        if origin is not None:
            headers["Origin"] = origin
        data = None
        if body is not None:
            data = urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        # skip_host: http.client would otherwise send its own Host header.
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in headers.items():
            c.putheader(k, v)
        c.putheader("Content-Length", str(len(data or b"")))
        c.endheaders(data)
        resp = c.getresponse()
        out = resp.status, resp.read().decode()
        c.close()
        return out

    def post_sync(self, mode="incremental", **kw):
        origin = kw.pop("origin", f"http://127.0.0.1:{self.port}")
        body = {"mode": mode, "token": kw.pop("token", self.token)}
        return self.request("POST", "/sync", origin=origin, body=body, **kw)


class HostGuardTest(ServerCase):
    def test_foreign_host_is_refused(self):
        status, _ = self.request("GET", "/", host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/status", host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)

    def test_loopback_hosts_are_served(self):
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            status, _ = self.request("GET", "/", host=host)
            self.assertEqual(status, 200, host)

    def test_wrong_port_and_bare_host_are_refused(self):
        for host in (f"127.0.0.1:{self.port + 1}", "127.0.0.1", "localhost", ""):
            status, _ = self.request("GET", "/", host=host)
            self.assertEqual(status, 403, host)

    def test_extra_host_is_refused_unless_configured(self):
        status, _ = self.request("GET", "/", host="mini.tail0.ts.net:8765")
        self.assertEqual(status, 403)

    def test_post_with_foreign_host_is_refused(self):
        status, _ = self.post_sync(host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)
        self.assertEqual(self.fake.modes, [])


class ExtraHostTest(ServerCase):
    extra_hosts = ["Mini.Tail0.ts.net:8765"]

    def test_configured_extra_host_is_served_and_may_post(self):
        status, _ = self.request("GET", "/", host="mini.tail0.ts.net:8765")
        self.assertEqual(status, 200)
        status, _ = self.post_sync(
            host="mini.tail0.ts.net:8765", origin="https://mini.tail0.ts.net:8765"
        )
        self.assertEqual(status, 202)

    def test_origin_must_match_the_request_host(self):
        status, _ = self.post_sync(
            host="mini.tail0.ts.net:8765", origin=f"http://127.0.0.1:{self.port}"
        )
        self.assertEqual(status, 403)


class PostGuardTest(ServerCase):
    def test_post_without_token_is_refused(self):
        status, _ = self.post_sync(token="")
        self.assertEqual(status, 403)
        status, _ = self.post_sync(token="nope")
        self.assertEqual(status, 403)
        self.assertEqual(self.fake.modes, [])

    def test_post_with_foreign_or_missing_origin_is_refused(self):
        for origin in ("http://evil.example", f"http://evil.example:{self.port}", ""):
            status, _ = self.post_sync(origin=origin)
            self.assertEqual(status, 403, origin)
        status, _ = self.request(
            "POST", "/sync", body={"mode": "incremental", "token": self.token}
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.fake.modes, [])

    def test_valid_post_returns_202_with_run_state(self):
        self.fake.release.clear()
        status, body = self.post_sync()
        self.assertEqual(status, 202)
        state = json.loads(body)
        self.assertTrue(state["busy"])
        self.assertIn("age_seconds", state)
        self.fake.release.set()
        self.assertTrue(self.syncer.wait_idle(5))
        self.assertEqual(self.fake.modes, ["incremental"])

    def test_bad_mode_is_400(self):
        status, _ = self.post_sync(mode="full")
        self.assertEqual(status, 400)

    def test_token_is_minted_once_and_kept_in_meta(self):
        conn = db.connect(self.db_path)
        try:
            self.assertEqual(server.ensure_token(conn), self.token)
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (server.TOKEN_KEY,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], self.token)
        self.assertIn(f'content="{self.token}"', self.request("GET", "/")[1])


class SingleFlightTest(ServerCase):
    def start_blocked(self, mode):
        self.fake.release.clear()
        self.assertEqual(self.post_sync(mode)[0], 202)
        self.assertTrue(self.fake.started.acquire(timeout=5), "run never started")

    def test_two_posts_during_one_run_start_one_run(self):
        self.start_blocked("incremental")
        self.assertEqual(self.post_sync()[0], 202)
        self.assertEqual(self.post_sync()[0], 202)
        self.fake.release.set()
        self.assertTrue(self.syncer.wait_idle(5))
        self.assertEqual(self.fake.modes, ["incremental"])

    def test_reconcile_during_incremental_runs_once_after_it(self):
        self.start_blocked("incremental")
        _, body = self.post_sync("reconcile")
        self.assertTrue(json.loads(body)["pending_reconcile"])
        self.assertEqual(self.post_sync("reconcile")[0], 202)
        self.assertEqual(self.post_sync("incremental")[0], 202)
        self.fake.release.set()
        self.assertTrue(self.syncer.wait_idle(5))
        self.assertEqual(self.fake.modes, ["incremental", "reconcile"])

    def test_reconcile_during_reconcile_joins_it(self):
        self.start_blocked("reconcile")
        self.assertEqual(self.post_sync("reconcile")[0], 202)
        self.assertEqual(self.post_sync("incremental")[0], 202)
        self.fake.release.set()
        self.assertTrue(self.syncer.wait_idle(5))
        self.assertEqual(self.fake.modes, ["reconcile"])

    def test_status_reports_running(self):
        self.start_blocked("incremental")
        status, body = self.request("GET", "/status")
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertTrue(state["running"])
        self.assertEqual(state["mode"], "incremental")
        self.assertIn("Syncing", self.request("GET", "/")[1])


class SyncerTest(unittest.TestCase):
    """The loop on its own, with a fake clock where timing matters."""

    def test_manual_run_resets_the_interval_timer(self):
        clock = [1000.0]
        fake = FakeSync()

        def fn(mode):
            fake(mode)
            clock[0] += 7.0  # the run takes 7s of fake time

        s = server.Syncer(fn, 300.0, run_at_start=False, clock=lambda: clock[0])
        self.assertEqual(s.next_due, 1300.0)
        s.start()
        try:
            clock[0] = 1200.0
            s.request("incremental")
            self.assertTrue(s.wait_idle(5))
        finally:
            s.stop()
        self.assertEqual(fake.modes, ["incremental"])
        self.assertEqual(s.next_due, 1207.0 + 300.0)

    def test_interval_fires_and_reconcile_when_due(self):
        clock = [0.0]
        fake = FakeSync()
        s = server.Syncer(fake, 0.05, 100.0, run_at_start=True, clock=lambda: clock[0])
        s.start()
        try:
            self.assertTrue(fake.started.acquire(timeout=5))
            self.assertTrue(s.wait_idle(5))
            clock[0] = 100.0
            s._wake.set()
            self.assertTrue(fake.started.acquire(timeout=5))
            self.assertTrue(s.wait_idle(5))
        finally:
            s.stop()
        self.assertEqual(fake.modes[:2], ["incremental", "reconcile"])

    def test_a_failing_run_does_not_kill_the_thread(self):
        modes = []

        def fn(mode):
            modes.append(mode)
            if len(modes) == 1:
                raise RuntimeError("boom")

        s = server.Syncer(fn, 3600.0, run_at_start=False)
        s.start()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                s.request("incremental")
                self.assertTrue(s.wait_idle(5))
                s.request("reconcile")
                self.assertTrue(s.wait_idle(5))
            finally:
                s.stop()
        self.assertEqual(modes, ["incremental", "reconcile"])
        self.assertIn("RuntimeError: boom", err.getvalue())


class HeaderCase(ServerCase):
    def now(self):
        return datetime.now(timezone.utc)

    def header(self):
        page = self.request("GET", "/")[1]
        match = re.search(r"<header.*?</header>", page, re.S)
        assert match is not None, "page has no header"
        return match.group(0)


class HeaderTest(HeaderCase):
    def test_auth_failed_names_acli_login(self):
        conn = self.conn()
        add_run(conn, "ok", self.now() - timedelta(minutes=1))
        add_run(
            conn,
            "auth_failed",
            self.now(),
            "acli: unauthorized (fix: run `acli jira auth login`)",
        )
        conn.close()
        self.assertIn("acli jira auth login", self.header())

    def test_gh_auth_failed_names_gh_login(self):
        conn = self.conn()
        add_run(conn, "auth_failed", self.now(), "gh: 401 (fix: run `gh auth login`)")
        conn.close()
        header = self.header()
        self.assertIn("gh auth login", header)
        self.assertNotIn("acli jira auth login", header)

    def test_fresh_sync_is_not_stale(self):
        conn = self.conn()
        add_run(conn, "ok", self.now() - timedelta(minutes=2))
        conn.close()
        header = self.header()
        self.assertNotIn('class="stale"', header)
        self.assertIn("ago", header)
        self.assertNotIn("alert", header)
        self.assertIn("Sync now", header)

    def test_stale_past_three_intervals(self):
        conn = self.conn()
        add_run(conn, "ok", self.now() - timedelta(minutes=16))  # interval is 5m
        conn.close()
        self.assertIn('class="stale"', self.header())

    def test_never_synced_is_stale(self):
        header = self.header()
        self.assertIn('class="stale"', header)
        self.assertIn("never synced", header)

    def test_failed_run_shows_its_error_escaped(self):
        conn = self.conn()
        add_run(conn, "ok", self.now() - timedelta(minutes=1))
        add_run(conn, "failed", self.now(), "AcliError: <boom>\nmore")
        conn.close()
        header = self.header()
        self.assertIn("Last sync failed: AcliError: &lt;boom&gt;", header)
        self.assertNotIn("more", header)

    def test_status_carries_age(self):
        conn = self.conn()
        add_run(conn, "ok", self.now() - timedelta(minutes=3))
        conn.close()
        state = json.loads(self.request("GET", "/status")[1])
        self.assertGreaterEqual(state["age_seconds"], 170)
        self.assertFalse(state["stale"])
        self.assertEqual(state["last_run"]["outcome"], "ok")
        self.assertFalse(state["busy"])


def fleet_rows(queued, awaiting_only):
    """github_fleet rows: `queued` PRs in the queue, `awaiting_only` not, one issue."""
    prs = [
        {
            "kind": "pr",
            "key": f"https://g/pull/{n}",
            "title": "t",
            "in_queue": n < queued,
        }
        for n in range(queued + awaiting_only)
    ]
    return prs + [
        {"kind": "issue", "key": "https://g/issues/9", "title": "i", "labels": []}
    ]


class FleetStatusTest(HeaderCase):
    """/status, the header alert and the review-queue gauge with github.fleet on."""

    fleet = True

    def state(self):
        return json.loads(self.request("GET", "/status")[1])

    def gauge(self):
        m = re.search(
            r'data-gauge="review-queue"[^>]*>(.*?)</span>', self.header(), re.S
        )
        assert m is not None, "header has no review-queue gauge"
        return re.sub(r"<[^>]+>", "", m.group(1))

    def test_fleet_run_does_not_freshen_a_failing_jira_sync(self):
        conn = self.conn()
        add_run(conn, "failed", self.now() - timedelta(minutes=20), "AcliError: x")
        add_run(conn, "ok", self.now(), kind="github_fleet")
        conn.close()
        state = json.loads(self.request("GET", "/status")[1])
        self.assertTrue(state["stale"])
        self.assertIsNone(state["last_ok_at"])
        self.assertEqual(state["last_run"]["kind"], "incremental")
        self.assertEqual(state["fleet_run"]["outcome"], "ok")
        self.assertIn("Last sync failed: AcliError: x", self.header())

    def test_fleet_auth_failure_names_gh_login_beside_the_jira_state(self):
        conn = self.conn()
        add_run(conn, "ok", self.now())
        add_run(
            conn, "auth_failed", self.now(), "gh: 401 (fix: run `gh auth login`)",
            kind="github_fleet",
        )  # fmt: skip
        conn.close()
        header = self.header()
        self.assertIn("gh auth login", header)
        self.assertNotIn('class="stale"', header)

    def test_fleet_failure_is_labelled(self):
        conn = self.conn()
        add_run(conn, "ok", self.now())
        add_run(conn, "failed", self.now(), "GhError: 502", kind="github_fleet")
        conn.close()
        self.assertIn("GitHub fleet sync failed: GhError: 502", self.header())

    def test_status_and_gauge_agree_from_never_synced_to_counted(self):
        # Before any successful fleet run: unknown in both, even with rows.
        conn = self.conn()
        github_fleet.write(conn, fleet_rows(1, 1), self.now())
        add_run(conn, "failed", self.now(), "GhError: 502", kind="github_fleet")
        conn.close()
        self.assertIsNone(self.state()["review_queue"])
        self.assertEqual(self.gauge(), "Review queue —/5")
        # A successful run that found nothing reads 0 in both.
        conn = self.conn()
        github_fleet.write(conn, [], self.now())
        add_run(conn, "ok", self.now(), kind="github_fleet")
        conn.close()
        self.assertEqual(self.state()["review_queue"], {"queue": 0, "awaiting": 0})
        self.assertEqual(self.gauge(), "Review queue 0/5")
        # Then the rows' counts, again in both.
        conn = self.conn()
        github_fleet.write(conn, fleet_rows(6, 2), self.now())
        conn.close()
        self.assertEqual(self.state()["review_queue"], {"queue": 6, "awaiting": 8})
        self.assertEqual(self.gauge(), "Review queue 6/5")


class FleetOffStatusTest(HeaderCase):
    """github.fleet off: leftover fleet rows and runs are not reported."""

    def test_leftover_rows_and_failed_run_are_not_reported(self):
        conn = self.conn()
        add_run(conn, "ok", self.now() - timedelta(minutes=1), kind="github_fleet")
        github_fleet.write(conn, fleet_rows(3, 2), self.now())
        add_run(conn, "ok", self.now())
        add_run(conn, "failed", self.now(), "GhError: 502", kind="github_fleet")
        conn.close()
        state = json.loads(self.request("GET", "/status")[1])
        self.assertIsNone(state["review_queue"])
        self.assertIsNone(state["fleet_run"])
        self.assertEqual(state["last_run"]["outcome"], "ok")
        header = self.header()
        self.assertNotIn("GitHub fleet sync failed", header)
        self.assertIn("Review queue <b>—/5</b>", header)


class BoardTest(ServerCase):
    def setUp(self):
        super().setUp()
        conn = self.conn()
        seed(conn)
        conn.close()

    def sections(self, page):
        return re.findall(r'data-section="([a-z_]+)"', page)

    def test_section_order_matches_kanban_classify(self):
        page = self.request("GET", "/board")[1]
        order = self.sections(page)
        k = server.kanban()
        self.assertEqual(order, list(k.SECTION_ORDER))
        self.assertEqual(len(order), 7)

    def test_rows_land_where_the_classifier_puts_them(self):
        page = self.request("GET", "/")[1]
        lanes = dict(
            re.findall(r'data-section="([a-z_]+)">(.*?)</section>', page, re.S)
        )
        found = {
            s: re.findall(r"class=\"ref\">\((ABC-\d+)\)<", body)
            for s, body in lanes.items()
        }
        self.assertEqual(found["new"], ["ABC-1"])
        self.assertEqual(found["needs_refinement"], ["ABC-2"])
        self.assertEqual(found["ready"], ["ABC-3"])
        # Priority first, then created: the two Highest rows sort by age.
        self.assertEqual(found["in_progress"], ["ABC-4", "ABC-9", "ABC-8"])
        self.assertEqual(found["blocked"], ["ABC-5"])
        self.assertEqual(found["needs_review"], ["ABC-6"])
        self.assertEqual(found["done"], ["ABC-7"])
        self.assertNotIn("ABC-99", page)  # not in the active sprint
        self.assertNotIn("ABC-98", page)  # not a Jira row
        self.assertIn("Sprint 5", page)
        self.assertIn('href="https://example.invalid/browse/ABC-1"', page)

    def test_empty_sections_are_omitted_in_order(self):
        conn = self.conn()
        conn.execute("DELETE FROM issues WHERE key IN ('ABC-2', 'ABC-5')")
        conn.commit()
        conn.close()
        order = self.sections(self.request("GET", "/")[1])
        expected = list(server.kanban().SECTION_ORDER)
        expected.remove("needs_refinement")
        expected.remove("blocked")
        self.assertEqual(order, expected)

    def add_issue(
        self, key, category, sprint_id, assignee=None, reporter=None, parent=None
    ):
        conn = self.conn()
        conn.execute(
            "INSERT INTO issues (source, kind, key, summary, status_category,"
            " labels, sprint_id, sprint_state, assignee, reporter, parent_key)"
            " VALUES ('jira', 'ticket', ?, 'x', ?, '[]', ?, 'active', ?, ?, ?)",
            (key, category, sprint_id, assignee, reporter, parent),
        )
        conn.commit()
        conn.close()

    def test_another_boards_active_sprint_is_left_out(self):
        self.add_issue("XYZ-1", "indeterminate", 6)  # sprint 6 is not configured
        self.assertNotIn("XYZ-1", self.request("GET", "/")[1])

    def test_my_issue_in_another_boards_sprint_is_kept(self):
        self.add_issue("XYZ-2", "indeterminate", 6, assignee="Pat Example")
        self.add_issue("XYZ-3", "indeterminate", 6, reporter="pat example")
        self.add_issue("XYZ-4", "indeterminate", 6, assignee="Someone Else")
        conn = self.conn()
        try:
            keys = {r["id"] for r in server.board_rows(conn, "Pat Example")}
            unset = {r["id"] for r in server.board_rows(conn, "")}
        finally:
            conn.close()
        self.assertLessEqual({"XYZ-2", "XYZ-3"}, keys)
        self.assertNotIn("XYZ-4", keys)
        self.assertFalse({"XYZ-2", "XYZ-3"} & unset)

    def test_ignored_issues_and_epic_children_leave_the_board(self):
        self.add_issue("ABC-60", "indeterminate", 5, parent="EPIC-9")
        self.add_issue("ABC-61", "indeterminate", 5)
        self.add_issue("ABC-62", "indeterminate", 5, parent="EPIC-8")
        self.add_issue("ABC-63", "indeterminate", 5, parent="ABC-60")  # a sub-task
        conn = self.conn()
        conn.executemany(
            "INSERT INTO ignores (source, item_key) VALUES ('jira', ?)",
            [("EPIC-9",), ("ABC-61",)],
        )
        conn.commit()
        conn.close()
        page = self.request("GET", "/")[1]
        lanes = "".join(
            re.findall(r'data-section="[a-z_]+">(.*?)</section>', page, re.S)
        )
        self.assertNotIn("(ABC-60)", lanes)  # a child of the ignored epic
        self.assertNotIn("(ABC-61)", lanes)  # ignored itself
        self.assertNotIn("(ABC-63)", lanes)  # under the ignored epic, two down
        self.assertIn("(ABC-62)", lanes)
        self.assertIn("Ignored (2)", page)
        self.assertEqual(page.count('data-ov="unignore"'), 2)

    def test_unclassified_rows_get_their_own_last_lane(self):
        self.add_issue("ABC-50", "undefined", 5)
        page = self.request("GET", "/")[1]
        self.assertEqual(self.sections(page)[-1], "unmatched")
        lane = re.search(r'data-section="unmatched">(.*?)</section>', page, re.S)
        assert lane is not None
        self.assertIn("Unclassified (1)", lane.group(1))
        self.assertIn("(ABC-50)</span>", lane.group(1))

    def test_summary_is_escaped(self):
        conn = self.conn()
        conn.execute(
            "UPDATE issues SET summary = '<script>alert(1)</script>' WHERE key = 'ABC-1'"
        )
        conn.commit()
        conn.close()
        page = self.request("GET", "/")[1]
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertEqual(page.count("<script>"), 1)  # only the page's own

    def test_responses_carry_nosniff(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        for path in ("/", "/status", "/nope"):
            c.request("GET", path)
            resp = c.getresponse()
            resp.read()
            self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff", path)
        c.close()

    def test_unknown_path_is_404(self):
        self.assertEqual(self.request("GET", "/nope")[0], 404)
        self.assertEqual(self.request("POST", "/x")[0], 404)


class JiraDisabledTest(ServerCase):
    """`jira.enabled = false`: no Jira-only tab and no `jira.me` note."""

    JIRA_TABS = ('href="/epics"', 'href="/board"', 'href="/waiting-on-me"',
                 'href="/waiting-on"')  # fmt: skip

    def setUp(self):
        super().setUp()
        p = Path(self.tmp.name) / "config.toml"
        p.write_text("[jira]\nenabled = false\n[github]\nenabled = false\n")
        self.httpd.app.cfg = config.load(p)

    def pages(self):
        paths = [*server.views.ROUTES, "/board", "/health"]
        out = {}
        for path in paths:
            status, page = self.request("GET", path)
            self.assertEqual(status, 200, path)
            out[path] = page
        return out

    def test_no_jira_tab_and_no_me_note_on_any_page(self):
        for path, page in self.pages().items():
            nav = page[page.index("<nav>") : page.index("</nav>")]
            for tab in self.JIRA_TABS:
                self.assertNotIn(tab, nav, path)
            self.assertIn('href="/now"', nav, path)
            self.assertIn('href="/stale"', nav, path)
            self.assertNotIn("jira.me is not set", page, path)

    def test_a_fleet_instance_drops_epics_and_board(self):
        self.httpd.app.cfg["github"].update(enabled=True, fleet=True)
        nav = server.views.nav("/now", self.httpd.app.cfg)
        for tab in self.JIRA_TABS[:2]:
            self.assertNotIn(tab, nav)
        for tab in self.JIRA_TABS[2:]:
            self.assertIn(tab, nav)

    def test_jira_enabled_keeps_every_tab_and_the_note(self):
        nav = server.views.nav("/now", self.cfg)
        for tab in self.JIRA_TABS:
            self.assertIn(tab, nav)
        self.httpd.app.cfg = self.cfg
        self.assertIn("jira.me is not set", self.request("GET", "/now")[1])


class ConfigTest(unittest.TestCase):
    def test_unreadable_config_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError) as ctx:
                config.load(d)  # a directory, not a file
            self.assertIn("cannot read", str(ctx.exception))

    def test_server_port_must_be_an_integer_port(self):
        base = (
            '[jira]\nsite = "x"\nboard_ids = []\n'
            '[jira.fields]\nsprint = "s"\nflagged = "f"\n'
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            for bad in ('"8765"', "true", "0", "70000", "87.5"):
                p.write_text(base + f"[server]\nport = {bad}\n")
                with self.assertRaises(config.ConfigError, msg=bad) as ctx:
                    config.load(p)
                self.assertIn("server.port", str(ctx.exception))
            p.write_text(base + "[server]\nport = 9000\n")
            self.assertEqual(config.load(p)["server"]["port"], 9000)

    def test_missing_config_keeps_its_message(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError) as ctx:
                config.load(Path(d) / "absent.toml")
            self.assertIn("not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
