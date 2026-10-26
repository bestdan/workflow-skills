#!/usr/bin/env python3
"""Hermetic tests for the fleet view's session rows.

Covers scripts/task-dashboard/sessions.py (rows from the session-state hook's
ingested events), claude_remote.py (the optional cloud session list) and their
sync steps. The cloud read never reaches Claude Code: it goes through an
in-process fake runner or dev/fake-claude, both fed from fixtures/claude/.
Needs Python 3.11: run via scripts/test-task-dashboard-sessions.sh.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
FIXTURES = PKG / "fixtures" / "claude"
FAKE_CLAUDE = PKG / "dev" / "fake-claude"
FAKE_ACLI = PKG / "dev" / "fake-acli"

sys.path.insert(0, str(PKG))

import claude_remote  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402
import sessions  # type: ignore[import-not-found]  # noqa: E402
import sync  # type: ignore[import-not-found]  # noqa: E402

NOW = datetime(2026, 10, 23, 21, 0, 0, tzinfo=timezone.utc)
DIGEST = "sha256:" + "ab" * 32
USER_CONFIG = {
    "jira": {
        "site": "example.invalid",
        "board_ids": [77],
        "fields": {"sprint": "customfield_10303", "flagged": "customfield_10007"},
    },
    "github": {"enabled": False},
}


def make_config(ingest=False, remote=False):
    user = json.loads(json.dumps(USER_CONFIG))
    if ingest:
        user["ingest"] = {"machines": {"mini": DIGEST}}
    if remote:
        user["claude_remote"] = {"enabled": True}
    return config.deep_merge(config.DEFAULTS, user)


def fixture_runner(name, rc=0, err=""):
    """A runner answering every call with fixtures/claude/<name>."""
    calls = []

    def runner(argv, stdin, timeout):
        calls.append((argv, stdin, timeout))
        return rc, (FIXTURES / name).read_text(), err

    runner.calls = calls
    return runner


def ts(minutes_ago, now=NOW):
    t = now - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


class DbCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.conn = db.connect(self.tmp / "d.db")
        self.addCleanup(self.conn.close)

    def event(self, session, minutes_ago, event, notification=None, host="mini",
              cwd="/w/repo-a", branch="feature/x", asked=None):  # fmt: skip
        self.conn.execute(
            "INSERT INTO session_events (host, ts, event, session_id, cwd, branch,"
            " notification, asked, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (host, ts(minutes_ago), event, session, cwd, branch, notification,
             asked, "2026-10-23T21:00:00Z"),
        )  # fmt: skip
        self.conn.commit()

    def rows(self, source):
        out = {}
        for key, status, category, raw in self.conn.execute(
            "SELECT key, status_name, status_category, raw_json FROM issues"
            " WHERE source = ?",
            (source,),
        ):
            out[key] = (status, category, json.loads(raw) if raw else None)
        return out


class HookSessionTests(DbCase):
    def derive(self):
        sessions.write(self.conn, sessions.derive(self.conn, NOW), NOW)
        return self.rows(sessions.SOURCE)

    def test_permission_prompt_after_a_prompt_is_a_waiting_need_input_row(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s1", 20, "Notification", "permission_prompt")
        status, category, raw = self.derive()["s1"]
        self.assertEqual((status, category), ("waiting", "need_input"))
        self.assertTrue(raw["needs_action"])
        self.assertEqual(raw["waiting_since"], ts(20))
        self.assertEqual(raw["permission_prompts"], 1)
        self.assertFalse(raw["empty_spawn"])
        self.assertEqual(raw["host"], "mini")
        self.assertEqual(raw["title"], "repo-a @ feature/x")

    def test_turn_end_is_waiting_without_needs_action(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s1", 25, "Stop")
        self.event("s1", 24, "Notification", "idle_prompt")
        status, category, raw = self.derive()["s1"]
        self.assertEqual((status, category), ("waiting", "turn_ended"))
        self.assertFalse(raw["needs_action"])
        self.assertEqual(raw["waiting_since"], ts(25))

    def test_turn_end_after_a_permission_prompt_clears_need_input(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s1", 25, "Notification", "permission_prompt")
        self.event("s1", 20, "Stop")
        status, category, raw = self.derive()["s1"]
        self.assertEqual((status, category), ("waiting", "turn_ended"))
        self.assertFalse(raw["needs_action"])
        self.assertEqual(raw["permission_prompts"], 1)
        self.assertEqual(raw["waiting_since"], ts(25))

    def test_a_turn_that_asked_is_need_input(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s1", 25, "Stop", asked=1)
        self.event("s1", 24, "Notification", "idle_prompt")
        status, category, raw = self.derive()["s1"]
        self.assertEqual((status, category), ("waiting", "need_input"))
        self.assertTrue(raw["needs_action"])
        self.assertTrue(raw["asked"])
        self.assertEqual(raw["permission_prompts"], 0)
        self.assertEqual(raw["waiting_since"], ts(25))

    def test_a_permission_prompt_after_an_asked_stop_wins(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s1", 25, "Stop", asked=1)
        self.event("s1", 20, "Notification", "permission_prompt")
        status, category, raw = self.derive()["s1"]
        self.assertEqual((status, category), ("waiting", "need_input"))
        self.assertFalse(raw["asked"])
        self.assertEqual(raw["permission_prompts"], 1)

    def test_asked_false_or_unknown_is_a_plain_turn_end(self):
        for asked in (0, None):
            with self.subTest(asked=asked):
                sid = f"s-{asked}"
                self.event(sid, 30, "UserPromptSubmit")
                self.event(sid, 25, "Stop", asked=asked)
                status, category, raw = self.derive()[sid]
                self.assertEqual((status, category), ("waiting", "turn_ended"))
                self.assertFalse(raw["asked"])

    def test_a_prompt_or_a_later_stop_clears_asked(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s1", 25, "Stop", asked=1)
        self.event("s1", 20, "UserPromptSubmit")
        self.event("s2", 30, "UserPromptSubmit")
        self.event("s2", 25, "Stop", asked=1)
        self.event("s2", 20, "UserPromptSubmit")
        self.event("s2", 15, "Stop", asked=0)
        rows = self.derive()
        self.assertEqual(rows["s1"][:2], ("working", None))
        self.assertFalse(rows["s1"][2]["asked"])
        self.assertEqual(rows["s2"][:2], ("waiting", "turn_ended"))
        self.assertFalse(rows["s2"][2]["asked"])

    def test_a_new_prompt_clears_the_wait(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s1", 25, "Notification", "permission_prompt")
        self.event("s1", 20, "Stop")
        self.event("s1", 10, "UserPromptSubmit")
        status, category, raw = self.derive()["s1"]
        self.assertEqual((status, category), ("working", None))
        self.assertFalse(raw["needs_action"])
        self.assertIsNone(raw["waiting_since"])
        self.assertEqual((raw["prompts"], raw["permission_prompts"]), (2, 1))
        self.assertEqual((raw["created"], raw["updated"]), (ts(30), ts(10)))

    def test_a_session_with_no_prompt_is_an_empty_spawn(self):
        self.event("spawn", 15, "Notification", "idle_prompt")
        self.event("used", 15, "UserPromptSubmit")
        rows = self.derive()
        self.assertTrue(rows["spawn"][2]["empty_spawn"])
        self.assertFalse(rows["spawn"][2]["has_prompt"])
        self.assertFalse(rows["used"][2]["empty_spawn"])

    def test_window_keeps_whole_history_of_a_recent_session(self):
        old = 40 * 24 * 60
        self.event("gone", old, "UserPromptSubmit")
        self.event("long", old, "UserPromptSubmit")
        self.event("long", 5, "Stop")
        rows = self.derive()
        self.assertNotIn("gone", rows)
        self.assertEqual(rows["long"][2]["created"], ts(old))

    def test_write_replaces_only_hook_rows(self):
        self.conn.execute(
            "INSERT INTO issues (source, kind, key) VALUES ('jira', 'ticket', 'DEMO-1')"
        )
        self.conn.execute(
            "INSERT INTO issues (source, kind, key) VALUES (?, 'session', 'stale')",
            (sessions.SOURCE,),
        )
        self.conn.commit()
        self.event("s1", 5, "UserPromptSubmit")
        self.assertEqual(list(self.derive()), ["s1"])
        self.assertEqual(
            self.conn.execute(
                "SELECT key FROM issues WHERE source = 'jira'"
            ).fetchall(),
            [("DEMO-1",)],
        )

    def test_null_session_ids_are_skipped(self):
        self.event(None, 5, "Stop")
        self.assertEqual(self.derive(), {})

    def registry(self, host, session_id, **fields):
        entry = {"session_id": session_id, "bridge_session_id": None, "name": None,
                 "status": None, "pid": 1, "updated_at": None, "tmux": None,
                 **fields}  # fmt: skip
        self.conn.execute(
            "INSERT INTO issues (source, kind, key, raw_json) VALUES (?, 'registry', ?, ?)",
            (f"machine:{host}", f"registry:{session_id}", json.dumps(entry)),
        )
        self.conn.commit()

    def test_a_live_registry_entry_joins_its_session_by_host(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s1", 25, "Stop", asked=1)
        self.event("s2", 30, "UserPromptSubmit")
        self.registry("mini", "s1", bridge_session_id="session_01RC", name="src-32",
                      status="idle", pid=4242, tmux="rc:@5.%5",
                      updated_at="2026-10-23T20:59:00.000Z")  # fmt: skip
        # Another host's entry for the same id is not this session's.
        self.registry("lindev", "s2", bridge_session_id="session_01OTHER", name="x")
        rows = self.derive()
        raw = rows["s1"][2]
        self.assertEqual(raw["title"], "src-32")
        self.assertEqual(raw["bridge_session_id"], "session_01RC")
        self.assertEqual(raw["live_status"], "idle")
        self.assertEqual(raw["live_name"], "src-32")
        self.assertEqual(raw["pid"], 4242)
        self.assertEqual(raw["tmux"], "rc:@5.%5")
        self.assertEqual(raw["live_updated_at"], "2026-10-23T20:59:00.000Z")
        summary = self.conn.execute(
            "SELECT summary FROM issues WHERE source = ? AND key = 's1'",
            (sessions.SOURCE,),
        ).fetchone()
        self.assertEqual(summary, ("src-32",))
        other = rows["s2"][2]
        self.assertEqual(other["title"], "repo-a @ feature/x")
        self.assertIsNone(other["bridge_session_id"])
        self.assertIsNone(other["live_status"])

    def machine(self, host, counts):
        self.conn.execute(
            "INSERT OR REPLACE INTO machines (host, last_snapshot_at, received_at,"
            " counts) VALUES (?, ?, ?, ?)",
            (host, "2026-10-23T20:59:00Z", "2026-10-23T20:59:00Z",
             None if counts is None else json.dumps(counts)),
        )  # fmt: skip
        self.conn.commit()

    def test_a_session_missing_from_a_registry_host_has_ended(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s2", 30, "UserPromptSubmit")
        self.registry("mini", "s1")
        # No machines row for mini, or one from before the schema was
        # recorded, or one from schema 2: nothing has ended.
        for counts in (False, None, {"registry": 0}, {"schema": 2}, {"schema": "3"}):
            if counts is not False:
                self.machine("mini", counts)
            rows = self.derive()
            self.assertFalse(rows["s1"][2]["ended"], counts)
            self.assertFalse(rows["s2"][2]["ended"], counts)
        self.machine("mini", {"schema": 3, "registry": 1})
        rows = self.derive()
        self.assertFalse(rows["s1"][2]["ended"])
        self.assertTrue(rows["s2"][2]["ended"])
        # Another host's registry says nothing about mini's sessions.
        self.machine("mini", {"schema": 2})
        self.machine("lindev", {"schema": 3})
        self.assertFalse(self.derive()["s2"][2]["ended"])


class ListSessionsTests(unittest.TestCase):
    def test_ok_page(self):
        runner = fixture_runner("list-ok.jsonl")
        got, truncated = claude_remote.list_sessions("claude", runner, 9)
        self.assertTrue(truncated)
        self.assertEqual(len(got), 6)
        argv, stdin, timeout = runner.calls[0]
        self.assertEqual(argv, ["claude", *claude_remote.ARGV])
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(timeout, 9)
        subtypes = [
            json.loads(line)["request"]["subtype"] for line in stdin.splitlines()
        ]
        self.assertEqual(subtypes, ["initialize", "list_cloud_sessions"])

    def test_started_by_session_is_unavailable_not_empty(self):
        with self.assertRaises(claude_remote.Unavailable) as cm:
            claude_remote.list_sessions(
                runner=fixture_runner("list-started-by-session.jsonl")
            )
        self.assertEqual(cm.exception.reason, "started_by_session")
        self.assertNotIsInstance(cm.exception, claude_remote.AuthError)

    def test_not_signed_in_is_an_auth_error(self):
        with self.assertRaises(claude_remote.AuthError) as cm:
            claude_remote.list_sessions(
                runner=fixture_runner("list-not-signed-in.jsonl")
            )
        self.assertEqual(cm.exception.reason, "not_signed_in")

    def reply(self, response):
        line = json.dumps({"type": "control_response", "response": response})
        return lambda argv, stdin, timeout: (0, line + "\n", "")

    def test_no_reply_names_the_exit_and_stderr(self):
        runner = lambda argv, stdin, timeout: (1, "not json\n", "boom\nlast words\n")  # noqa: E731
        with self.assertRaises(claude_remote.Unavailable) as cm:
            claude_remote.list_sessions(runner=runner)
        self.assertEqual(cm.exception.reason, "no_reply")
        self.assertIn("exit 1", str(cm.exception))
        self.assertIn("last words", str(cm.exception))

    def test_error_reply_is_unavailable(self):
        runner = self.reply({"subtype": "error", "request_id": "list", "error": "nope"})
        with self.assertRaises(claude_remote.Unavailable) as cm:
            claude_remote.list_sessions(runner=runner)
        self.assertEqual(cm.exception.reason, "error")

    def test_ok_without_sessions_is_malformed_not_empty(self):
        runner = self.reply(
            {"subtype": "success", "request_id": "list", "response": {"status": "ok"}}
        )
        with self.assertRaises(claude_remote.Unavailable) as cm:
            claude_remote.list_sessions(runner=runner)
        self.assertEqual(cm.exception.reason, "malformed")

    def test_unknown_reason_is_unavailable(self):
        runner = self.reply(
            {"subtype": "success", "request_id": "list",
             "response": {"status": "unavailable", "reason": "brand_new"}}
        )  # fmt: skip
        with self.assertRaises(claude_remote.Unavailable) as cm:
            claude_remote.list_sessions(runner=runner)
        self.assertEqual(cm.exception.reason, "brand_new")

    def test_timeout_is_unavailable(self):
        exc = subprocess.TimeoutExpired(["claude"], 1)
        with mock.patch.object(claude_remote.subprocess, "run", side_effect=exc):
            with self.assertRaises(claude_remote.Unavailable) as cm:
                claude_remote.run(["claude"], "", 1)
        self.assertEqual(cm.exception.reason, "timeout")


class ToRowTests(unittest.TestCase):
    def rows(self):
        got, _ = claude_remote.list_sessions(runner=fixture_runner("list-ok.jsonl"))
        return {r["key"][-1]: r for r in map(claude_remote.to_row, got)}

    def test_requires_action_is_a_waiting_need_input_row(self):
        r = self.rows()["1"]
        self.assertEqual(
            (r["status_bucket"], r["status_category"], r["needs_action"]),
            ("waiting", "need_input", True),
        )
        self.assertEqual(r["machine"], "cloud")
        self.assertEqual(r["repository"], "example/repo-a")
        self.assertEqual(r["branch"], "feature/one")

    def test_buckets(self):
        rows = self.rows()
        self.assertEqual(rows["2"]["status_bucket"], "working")
        self.assertEqual(rows["2"]["machine"], "cloud")
        self.assertEqual(rows["3"]["status_bucket"], "idle")
        self.assertTrue(rows["4"]["archived"])
        # No status at all: the CLI documents reading that as idle.
        self.assertEqual(
            (rows["5"]["status"], rows["5"]["status_bucket"]), (None, "idle")
        )
        self.assertIsNone(rows["5"]["title"])

    def test_route_cannot_tell_an_empty_spawn(self):
        for r in self.rows().values():
            self.assertIsNone(r["empty_spawn"])
            self.assertIsNone(r["has_prompt"])
            self.assertIsNone(r["brief"])

    def test_missing_id_is_skipped(self):
        self.assertIsNone(claude_remote.to_row({"title": "x"}))


class ReconcileTests(DbCase):
    def put(self, key, synced_at):
        self.conn.execute(
            "INSERT INTO issues (source, kind, key, status_name, synced_at)"
            " VALUES (?, 'session', ?, 'idle', ?)",
            (claude_remote.SOURCE, key, synced_at),
        )
        self.conn.commit()

    def write_page(self, now=NOW):
        plan = claude_remote.fetch(
            make_config(remote=True), fixture_runner("list-ok.jsonl")
        )
        return claude_remote.write(self.conn, plan["rows"], now)

    def test_archived_session_is_dropped_on_reconcile(self):
        self.put("session_01FIXTURE0000000000000004", "2026-10-23T20:00:00Z")
        dropped = self.write_page()
        rows = self.rows(claude_remote.SOURCE)
        self.assertNotIn("session_01FIXTURE0000000000000004", rows)
        self.assertEqual(dropped["dropped_archived"], 1)
        self.assertEqual(len(rows), 3)

    def test_archived_session_never_seen_is_not_inserted(self):
        self.write_page()
        self.assertNotIn(
            "session_01FIXTURE0000000000000004", self.rows(claude_remote.SOURCE)
        )

    def test_remote_control_sessions_are_not_written(self):
        self.write_page()
        rows = self.rows(claude_remote.SOURCE)
        self.assertEqual(sorted(k[-1] for k in rows), ["1", "2", "6"])
        for _, _, raw in rows.values():
            self.assertEqual(raw["environment_kind"], "anthropic_cloud")
        for bridge in ("3", "5"):
            self.assertNotIn(f"session_01FIXTURE000000000000000{bridge}", rows)

    def test_absence_from_the_page_is_not_archival(self):
        self.put("session_offpage", "2026-10-20T00:00:00Z")
        dropped = self.write_page()
        rows = self.rows(claude_remote.SOURCE)
        self.assertIn("session_offpage", rows)
        self.assertEqual(dropped, {"dropped_archived": 0, "dropped_unseen": 0})
        synced = dict(
            self.conn.execute(
                "SELECT key, synced_at FROM issues WHERE source = ?",
                (claude_remote.SOURCE,),
            )
        )
        self.assertEqual(synced["session_offpage"], "2026-10-20T00:00:00Z")
        self.assertEqual(
            synced["session_01FIXTURE0000000000000001"], "2026-10-23T21:00:00Z"
        )

    def test_unseen_for_retain_days_ages_out(self):
        self.put("session_long_gone", "2026-09-01T00:00:00Z")
        dropped = self.write_page()
        self.assertNotIn("session_long_gone", self.rows(claude_remote.SOURCE))
        self.assertEqual(dropped["dropped_unseen"], 1)

    def test_upsert_updates_a_seen_row(self):
        self.write_page()
        self.write_page(NOW + timedelta(minutes=5))
        status, category, raw = self.rows(claude_remote.SOURCE)[
            "session_01FIXTURE0000000000000001"
        ]
        self.assertEqual((status, category), ("waiting", "need_input"))
        self.assertEqual(raw["title"], "Session 1")


class FakeClaudeTests(unittest.TestCase):
    def test_real_runner_against_fake_claude(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "ok"}):
            got, truncated = claude_remote.list_sessions(str(FAKE_CLAUDE))
        self.assertEqual((len(got), truncated), (6, True))

    def test_refusal_through_fake_claude(self):
        env = {"FAKE_CLAUDE_MODE": "started_by_session"}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(claude_remote.Unavailable) as cm:
                claude_remote.list_sessions(str(FAKE_CLAUDE))
        self.assertEqual(cm.exception.reason, "started_by_session")

    def test_environment_reaches_the_cli_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "env"
            env = {"FAKE_CLAUDE_ENV_OUT": str(out), "CLAUDE_CODE_CHILD_SESSION": "1"}
            with mock.patch.dict(os.environ, env):
                claude_remote.list_sessions(str(FAKE_CLAUDE))
            self.assertEqual(out.read_text().strip(), "set")

    def test_list_command_prints_counts_and_no_titles(self):
        r = subprocess.run(
            [sys.executable, str(PKG / "claude_remote.py"), "list", str(FAKE_CLAUDE)],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "FAKE_CLAUDE_MODE": "ok"},
        )  # fmt: skip
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sessions: 6; truncated: true", r.stdout)
        self.assertIn("requires_action 1", r.stdout)
        self.assertNotIn("Session 1", r.stdout)


class SyncStepTests(DbCase):
    def test_gates_default_off(self):
        cfg = config.deep_merge(config.DEFAULTS, USER_CONFIG)
        self.assertFalse(cfg["claude_remote"]["enabled"])
        self.assertFalse(sessions.enabled(cfg))
        self.assertTrue(sessions.enabled(make_config(ingest=True)))

    def test_claude_remote_ok_run(self):
        row = sync.run_claude_remote(
            make_config(remote=True), self.conn, NOW,
            runner=fixture_runner("list-ok.jsonl"), clock=lambda: NOW,
        )  # fmt: skip
        self.assertEqual((row["kind"], row["outcome"]), ("claude_remote", "ok"))
        counts = json.loads(row["counts"])
        self.assertEqual(
            (counts["page"], counts["kept"], counts["truncated"]), (6, 4, 1)
        )
        self.assertEqual(len(self.rows(claude_remote.SOURCE)), 3)

    def test_refused_read_fails_the_run_and_keeps_the_last_rows(self):
        cfg = make_config(remote=True)
        sync.run_claude_remote(
            cfg,
            self.conn,
            NOW,
            runner=fixture_runner("list-ok.jsonl"),
            clock=lambda: NOW,
        )
        row = sync.run_claude_remote(
            cfg, self.conn, NOW, runner=fixture_runner("list-started-by-session.jsonl"),
            clock=lambda: NOW,
        )  # fmt: skip
        self.assertEqual(row["outcome"], "failed")
        self.assertIn("started_by_session", row["error"])
        self.assertEqual(len(self.rows(claude_remote.SOURCE)), 3)

    def test_auth_failure_carries_the_login_hint(self):
        row = sync.run_claude_remote(
            make_config(remote=True), self.conn, NOW,
            runner=fixture_runner("list-not-signed-in.jsonl"), clock=lambda: NOW,
        )  # fmt: skip
        self.assertEqual(row["outcome"], "auth_failed")
        self.assertIn("claude auth login", row["error"])

    def test_sessions_run_counts(self):
        self.event("s1", 30, "UserPromptSubmit")
        self.event("s1", 20, "Notification", "permission_prompt")
        self.event("spawn", 10, "Stop")
        row = sync.run_sessions(self.conn, NOW, clock=lambda: NOW)
        self.assertEqual((row["kind"], row["outcome"]), ("sessions", "ok"))
        self.assertEqual(
            json.loads(row["counts"]),
            {"sessions": 2, "waiting": 2, "need_input": 1, "empty_spawns": 1},
        )

    def test_session_runs_do_not_make_jira_look_fresh(self):
        self.conn.executemany(
            "INSERT INTO sync_runs (kind, started_at, finished_at, outcome)"
            " VALUES (?, ?, ?, ?)",
            [
                ("full", "2026-10-23T10:00:00Z", "2026-10-23T10:00:05Z", "ok"),
                ("full", "2026-10-23T20:00:00Z", "2026-10-23T20:00:05Z", "failed"),
                ("sessions", "2026-10-23T20:59:00Z", "2026-10-23T20:59:01Z", "ok"),
                ("claude_remote", "2026-10-23T20:59:01Z", "2026-10-23T20:59:03Z", "ok"),
            ],
        )
        self.conn.commit()
        import server_install  # type: ignore[import-not-found]

        jira = server_install._finished_after(self.tmp / "d.db", 0, fleet=False)
        self.assertEqual((jira[1], jira[2]), ("full", "failed"))
        health = server.sync_health(self.conn, 300, NOW)
        self.assertEqual(health["last_ok_at"], "2026-10-23T10:00:05Z")
        self.assertEqual(health["last_run"]["kind"], "full")
        self.assertTrue(health["stale"])


class EndToEndTests(unittest.TestCase):
    """`dashboard.py sync` against dev/fake-acli and dev/fake-claude."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.cfg = self.tmp / "config.toml"
        self.db = self.tmp / "d.db"

    def write_config(self, remote):
        text = (
            "[jira]\n"
            'site = "example.invalid"\n'
            "board_ids = [77]\n"
            f'acli_path = "{FAKE_ACLI}"\n'
            "[jira.fields]\n"
            'sprint = "customfield_10303"\n'
            'flagged = "customfield_10007"\n'
            "[github]\n"
            "enabled = false\n"
            "[ingest.machines]\n"
            f'mini = "{DIGEST}"\n'
        )
        if remote:
            text += f'[claude_remote]\nenabled = true\nclaude_path = "{FAKE_CLAUDE}"\n'
        self.cfg.write_text(text)

    def sync(self, mode="ok"):
        env = {**os.environ, "FAKE_ACLI_STATE": str(self.tmp / "state"),
               "FAKE_CLAUDE_MODE": mode}  # fmt: skip
        env.pop("FAKE_ACLI_MODE", None)
        return subprocess.run(
            [sys.executable, str(PKG / "dashboard.py"), "sync", "--mode", "full",
             "--db", str(self.db), "--config", str(self.cfg)],
            capture_output=True, text=True, env=env, timeout=120,
        )  # fmt: skip

    def seed_events(self):
        conn = db.connect(self.db)
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO session_events (host, ts, event, session_id, received_at)"
            " VALUES ('mini', ?, 'Notification', 'hook-1', ?)",
            (ts(5, now), ts(5, now)),
        )
        conn.execute("UPDATE session_events SET notification = 'permission_prompt'")
        conn.commit()
        conn.close()

    def query(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_sync_writes_both_session_sources(self):
        self.write_config(remote=True)
        self.seed_events()
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.query("SELECT kind, outcome FROM sync_runs ORDER BY id"),
            [("full", "ok"), ("sessions", "ok"), ("claude_remote", "ok")],
        )
        self.assertEqual(
            self.query(
                "SELECT source, COUNT(*) FROM issues WHERE kind = 'session'"
                " GROUP BY source ORDER BY source"
            ),
            [("claude-hook", 1), ("claude-remote", 3)],
        )
        self.assertEqual(
            self.query("SELECT status_category FROM issues WHERE key = 'hook-1'"),
            [("need_input",)],
        )

    def test_refused_cloud_read_exits_nonzero_and_says_why(self):
        self.write_config(remote=True)
        r = self.sync("started_by_session")
        self.assertEqual(r.returncode, 1)
        self.assertIn("sync claude_remote: failed", r.stdout)
        self.assertIn("started_by_session", r.stderr)

    def test_gate_off_never_runs_claude(self):
        self.write_config(remote=False)
        r = self.sync("garbage")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            [k for (k,) in self.query("SELECT kind FROM sync_runs ORDER BY id")],
            ["full", "sessions"],
        )


class ConfigTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.toml"
            p.write_text(
                '[jira]\nsite = "x"\nboard_ids = []\n'
                '[jira.fields]\nsprint = "a"\nflagged = "b"\n' + text
            )
            return config.load(p)

    def test_defaults(self):
        cr = self.load("")["claude_remote"]
        self.assertEqual(
            cr, {"enabled": False, "claude_path": "claude", "timeout_seconds": 45}
        )

    def test_enabled_must_be_a_bool(self):
        with self.assertRaisesRegex(config.ConfigError, "claude_remote.enabled"):
            self.load('[claude_remote]\nenabled = "yes"\n')

    def test_timeout_must_be_positive(self):
        with self.assertRaisesRegex(config.ConfigError, "timeout_seconds"):
            self.load("[claude_remote]\ntimeout_seconds = 0\n")

    def test_claude_path_must_be_set(self):
        with self.assertRaisesRegex(config.ConfigError, "claude_path"):
            self.load('[claude_remote]\nclaude_path = ""\n')


if __name__ == "__main__":
    unittest.main(verbosity=1)
