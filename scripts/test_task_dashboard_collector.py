#!/usr/bin/env python3
"""Hermetic tests for the fleet view's collector: collector.py, collector_install.py,
the stable launcher, `[collector]` config and `dashboard.py collect` / `install`.

Every push goes to a fake ingest server on an ephemeral 127.0.0.1 port that
answers from a script of status codes. Every scheduler command (launchctl,
systemctl, loginctl) goes through a fake runner, and every file lands under a
temp home: nothing here loads a unit or reaches the network. The generated
plist and systemd units are compared against golden files in
scripts/task-dashboard/fixtures/collector/; rerun with UPDATE_GOLDEN=1 to
rewrite them after a deliberate change.

Needs Python 3.11: run via scripts/test-task-dashboard-collector.sh.
"""

import contextlib
import http.server
import io
import json
import os
import plistlib
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
GOLDEN = PKG / "fixtures" / "collector"
sys.path.insert(0, str(PKG))

# mypy checks this file without scripts/task-dashboard on its path; the
# modules themselves are type-checked in their own group (scripts/typecheck.sh).
import collector  # type: ignore[import-not-found]  # noqa: E402
import collector_install  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import snapshot  # type: ignore[import-not-found]  # noqa: E402

TOKEN = "collector-token-" + "z" * 32
NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)


class FakeIngest:
    """A POST /ingest that answers from ``script`` (then 200) and records calls."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls: list[tuple[str, dict, dict]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                outer.calls.append((self.path, dict(self.headers), json.loads(body)))
                code = outer.script.pop(0) if outer.script else 200
                self.send_response(code)
                if 300 <= code < 400:
                    self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                out = json.dumps({"code": code}).encode()
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/ingest"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def docs(self):
        return [doc for _, _, doc in self.calls]


def tmux_runner(argv, timeout):
    if argv[0] == "tmux":
        return 1, "", "no server running"
    raise AssertionError(f"unexpected call: {argv}")


def hook_line(session, event, ts):
    rec = {"v": 1, "ts": ts, "event": event, "session_id": session,
           "cwd": "/w", "branch": "main"}  # fmt: skip
    return json.dumps(rec) + "\n"


class Home(unittest.TestCase):
    """A temp home with a session-state log, no repos, and a fake ingest."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.state = self.home / ".local/state/fleet"
        (self.state / "sessions").mkdir(parents=True)
        (self.home / "src").mkdir()
        self.log_lines = []
        self.ingest = FakeIngest()
        self.addCleanup(self.ingest.close)

    def cfg(self, **over):
        cfg = dict(config.COLLECTOR_DEFAULTS, ingest_url=self.ingest.url,
                   token=TOKEN, host="testhost", enabled=True)  # fmt: skip
        cfg.update(over)
        return cfg

    def hook(self, *events):
        with (self.state / "sessions" / "s1.jsonl").open("a") as fh:
            for event, ts in events:
                fh.write(hook_line("s1", event, ts))

    def run_once(self, cfg=None, at=NOW):
        return collector.collect(
            cfg or self.cfg(), home=self.home, now=at, runner=tmux_runner,
            log=self.log_lines.append,
        )  # fmt: skip

    def cursor(self):
        return snapshot.load_cursor(self.state / snapshot.CURSOR_NAME)

    def spool(self):
        return collector.spooled(self.state / "spool")


class TestPush(Home):
    def test_success_advances_the_cursor(self):
        self.hook(("Stop", "2026-10-23T10:00:00.000Z"))
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(self.ingest.calls), 1)
        path, headers, doc = self.ingest.calls[0]
        self.assertEqual(path, "/ingest")
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(snapshot.validate(doc), [])
        self.assertEqual([e["event"] for e in doc["sessions"]], ["Stop"])
        self.assertIn("s1.jsonl", self.cursor()["sessions"])
        self.assertEqual(self.spool(), [])
        # The next run carries only what was appended since.
        self.hook(("UserPromptSubmit", "2026-10-23T10:05:00.000Z"))
        self.assertEqual(self.run_once(at=NOW + timedelta(minutes=5)), 0)
        self.assertEqual(
            [e["event"] for e in self.ingest.docs[1]["sessions"]], ["UserPromptSubmit"]
        )

    def test_5xx_keeps_the_snapshot_spooled_and_the_cursor_unchanged(self):
        self.hook(("Stop", "2026-10-23T10:00:00.000Z"))
        self.ingest.script = [503]
        self.assertEqual(self.run_once(), 1)
        self.assertEqual(self.cursor(), {})
        self.assertEqual(len(self.spool()), 1)
        self.assertTrue(any("kept for the next run" in m for m in self.log_lines))

        # Next run: the spooled one goes first, then the new one, and the new
        # one starts where the spooled one ended — no line is sent twice.
        self.hook(("UserPromptSubmit", "2026-10-23T10:05:00.000Z"))
        self.assertEqual(self.run_once(at=NOW + timedelta(minutes=5)), 0)
        sent = self.ingest.docs
        self.assertEqual(len(sent), 3)  # the refused try, then both
        self.assertEqual(sent[1], sent[0])
        self.assertEqual([e["event"] for e in sent[1]["sessions"]], ["Stop"])
        self.assertEqual(
            [e["event"] for e in sent[2]["sessions"]], ["UserPromptSubmit"]
        )
        self.assertLess(sent[1]["taken_at"], sent[2]["taken_at"])
        self.assertEqual(self.spool(), [])
        self.assertIn("s1.jsonl", self.cursor()["sessions"])

    def test_a_full_spool_drops_the_oldest_snapshot(self):
        self.ingest.script = [500, 500, 500]
        for i in range(3):
            self.assertEqual(self.run_once(self.cfg(spool_max=2),
                                           at=NOW + timedelta(minutes=5 * i)), 1)  # fmt: skip
        names = [p.name for p in self.spool()]
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].startswith("20261023T120500"), names)
        self.assertTrue(names[1].startswith("20261023T121000"), names)
        self.assertTrue(any("dropped the oldest" in m for m in self.log_lines))
        self.assertEqual(self.cursor(), {})

    def test_409_counts_as_delivered(self):
        self.ingest.script = [409]
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(self.spool(), [])
        self.assertIn("history", self.cursor())

    def test_400_is_dropped_with_the_reason(self):
        self.ingest.script = [400]
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(self.spool(), [])
        self.assertTrue(any("refused" in m and "for good" in m for m in self.log_lines))

    def test_401_and_a_redirect_are_kept(self):
        for code in (401, 403, 302):
            with self.subTest(code=code):
                self.ingest.script = [code]
                self.assertEqual(self.run_once(), 1)
                self.assertEqual(self.cursor(), {})
                self.assertEqual(len(self.ingest.calls), 1)  # redirect not followed
                for p in self.spool():
                    p.unlink()
                self.ingest.calls.clear()

    def test_no_answer_is_kept(self):
        self.ingest.close()
        self.assertEqual(self.run_once(), 1)
        self.assertEqual(len(self.spool()), 1)
        self.assertTrue(any("no answer" in m for m in self.log_lines))

    def test_a_malformed_response_is_kept(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        self.addCleanup(sock.close)

        def answer():
            conn, _ = sock.accept()
            with conn:
                conn.recv(65536)
                conn.sendall(b"NOT-HTTP garbage\r\n\r\n")

        threading.Thread(target=answer, daemon=True).start()
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/ingest"
        self.assertEqual(self.run_once(self.cfg(ingest_url=url)), 1)
        self.assertEqual(len(self.spool()), 1)
        self.assertTrue(any("no answer" in m for m in self.log_lines))

    def test_disabled_builds_and_pushes_nothing(self):
        self.ingest.script = [503]
        self.assertEqual(self.run_once(), 1)  # leaves one snapshot spooled
        self.ingest.calls.clear()

        def runner(argv, timeout):
            raise AssertionError(f"a disabled collector ran {argv}")

        rc = collector.collect(self.cfg(enabled=False), home=self.home, now=NOW,
                               runner=runner, log=self.log_lines.append)  # fmt: skip
        self.assertEqual(rc, 0)
        self.assertEqual(self.ingest.calls, [])
        self.assertEqual(len(self.spool()), 1)
        self.assertTrue(any("enabled is false" in m for m in self.log_lines))

    def test_an_invalid_snapshot_does_not_block_the_spool(self):
        self.hook(("Stop", "2026-10-23T10:00:00.000Z"))
        self.ingest.script = [503]
        self.assertEqual(self.run_once(), 1)
        waiting = self.ingest.docs[0]
        with mock.patch.object(snapshot, "validate", return_value=["host: bad"]):
            rc = self.run_once(at=NOW + timedelta(minutes=5))
        self.assertEqual(rc, 1)
        self.assertEqual(self.ingest.docs[1:], [waiting])  # sent; the bad one not
        self.assertEqual(self.spool(), [])
        self.assertIn("s1.jsonl", self.cursor()["sessions"])
        self.assertTrue(any("not spooled" in m for m in self.log_lines))

    def test_the_token_never_reaches_argv_logs_or_the_spool(self):
        calls = []

        def runner(argv, timeout):
            calls.append(argv)
            return tmux_runner(argv, timeout)

        self.ingest.script = [503]
        collector.collect(self.cfg(), home=self.home, now=NOW, runner=runner,
                          log=self.log_lines.append)  # fmt: skip
        self.assertNotIn(TOKEN, json.dumps(calls))
        self.assertNotIn(TOKEN, "\n".join(self.log_lines))
        for p in self.spool():
            self.assertNotIn(TOKEN, p.read_text())
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_a_second_run_skips_while_one_holds_the_lock(self):
        with collector._locked(self.state / "collect.lock") as held:
            self.assertTrue(held)
            self.assertEqual(self.run_once(), 0)
        self.assertEqual(self.ingest.calls, [])
        self.assertTrue(any("holds the lock" in m for m in self.log_lines))

    def test_verdicts(self):
        table = {200: "accepted", 204: "accepted", 409: "accepted",
                 400: "rejected", 413: "rejected", 404: "rejected",
                 401: "keep", 403: "keep", 408: "keep", 429: "keep",
                 500: "keep", 503: "keep", 302: "keep", None: "keep"}  # fmt: skip
        for status, want in table.items():
            self.assertEqual(collector.verdict(status), want, status)


OLD = "2026-09-01T10:00:00.000Z"  # 52 days before NOW
RECENT = "2026-10-20T10:00:00.000Z"  # 3 days before NOW


class TestPrune(Home):
    def session(self, name, *lines, age_days=60):
        """A session file of ``lines`` (raw strings), its mtime ``age_days`` old."""
        path = self.state / "sessions" / f"{name}.jsonl"
        path.write_text("".join(lines))
        mtime = (NOW - timedelta(days=age_days)).timestamp()
        os.utime(path, (mtime, mtime))
        return path

    def test_an_old_fully_ingested_file_is_deleted(self):
        old = self.session("old", hook_line("old", "Stop", OLD))
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(self.ingest.docs[0]["sessions"]), 1)  # ingested first
        self.assertFalse(old.exists())
        self.assertTrue(any("pruned 1 session-state file" in m for m in self.log_lines))
        # The next run's cursor no longer names it, and nothing is resent.
        self.assertEqual(self.run_once(at=NOW + timedelta(minutes=5)), 0)
        self.assertEqual(self.ingest.docs[1]["sessions"], [])
        self.assertNotIn("old.jsonl", self.cursor()["sessions"])

    def test_an_old_file_the_server_has_not_accepted_is_kept(self):
        # The spooled snapshot's cursor reaches EOF, but the saved one does not.
        old = self.session("old", hook_line("old", "Stop", OLD))
        self.ingest.script = [503]
        self.assertEqual(self.run_once(), 1)
        self.assertTrue(old.exists())
        self.assertEqual(self.cursor(), {})
        self.assertEqual(self.run_once(at=NOW + timedelta(minutes=5)), 0)
        self.assertFalse(old.exists())  # accepted now, so it goes

    def test_an_old_file_with_unread_lines_is_kept(self):
        self.assertEqual(self.run_once(), 0)  # a saved cursor with no mark for it
        unread = self.session("unread", hook_line("unread", "Stop", OLD))
        collector.prune(self.state, 30, NOW, self.log_lines.append)
        self.assertTrue(unread.exists())
        # A line the hook is still writing (no newline) keeps the cursor short
        # of the end, so the file is kept even once the rest is accepted.
        torn = self.session("torn", hook_line("torn", "Stop", OLD), '{"v":1,"ts"',
                            age_days=60)  # fmt: skip
        self.assertEqual(self.run_once(at=NOW + timedelta(minutes=5)), 0)
        self.assertTrue(torn.exists())
        self.assertFalse(unread.exists())

    def test_a_recent_file_is_kept(self):
        recent_line = self.session("a", hook_line("a", "Stop", RECENT), age_days=60)
        recent_mtime = self.session("b", hook_line("b", "Stop", OLD), age_days=3)
        self.assertEqual(self.run_once(), 0)
        self.assertTrue(recent_line.exists())
        self.assertTrue(recent_mtime.exists())

    def test_the_window_comes_from_retention_days(self):
        path = self.session("a", hook_line("a", "Stop", RECENT), age_days=3)
        self.assertEqual(self.run_once(self.cfg(retention_days=2)), 0)
        self.assertFalse(path.exists())

    def test_a_malformed_line_is_skipped_and_counted(self):
        path = self.session(
            "bad",
            hook_line("bad", "UserPromptSubmit", OLD),
            "not json at all\n",
            '{"v":1,"ts":"yesterday"}\n',
            hook_line("bad", "Stop", OLD),
        )
        self.assertEqual(collector.newest_ts(path)[1], 2)
        self.assertEqual(self.run_once(), 0)
        self.assertFalse(path.exists())
        self.assertTrue(
            any("skipped 2 malformed" in m for m in self.log_lines), self.log_lines
        )

    def accepted(self, path):
        """Save a cursor that has accepted all of ``path``."""
        st = path.stat()
        cursor = {"v": 1, "sessions": {path.name: {"offset": st.st_size,
                                                   "inode": st.st_ino}}}  # fmt: skip
        snapshot.save_cursor(self.state / snapshot.CURSOR_NAME, cursor)

    def test_a_disabled_collector_prunes_nothing(self):
        old = self.session("old", hook_line("old", "Stop", OLD))
        self.accepted(old)
        self.assertEqual(self.run_once(self.cfg(enabled=False)), 0)
        self.assertTrue(old.exists())
        # Enabled, but the push leaves a snapshot spooled: still kept.
        self.ingest.script = [503]
        self.assertEqual(self.run_once(), 1)
        self.assertTrue(old.exists())
        # The spool empties on the next run, and it goes.
        self.assertEqual(self.run_once(at=NOW + timedelta(minutes=5)), 0)
        self.assertFalse(old.exists())

    def test_an_invalid_snapshot_prunes_only_once_the_spool_is_empty(self):
        old = self.session("old", hook_line("old", "Stop", OLD))
        self.accepted(old)
        bad = mock.patch.object(snapshot, "validate", return_value=["host: bad"])
        with bad:
            # A waiting snapshot that cannot be sent: the spool is not empty.
            collector.write_entry(self.state / "spool",
                                  {"taken_at": "2026-10-23T11:00:00.000Z"},
                                  snapshot.load_cursor(self.state / snapshot.CURSOR_NAME))  # fmt: skip
            self.ingest.script = [503]
            self.assertEqual(self.run_once(), 1)
            self.assertTrue(old.exists())
            # The waiting one is accepted, the spool empties, and it goes.
            self.assertEqual(self.run_once(at=NOW + timedelta(minutes=5)), 1)
        self.assertEqual(self.spool(), [])
        self.assertFalse(old.exists())

    def test_a_failed_cursor_save_after_pruning_keeps_the_push_result(self):
        old = self.session("old", hook_line("old", "Stop", OLD))
        real = snapshot.save_cursor
        calls = []

        def save(path, cursor):
            calls.append(cursor)
            if len(calls) > 1:  # the first is flush's, the second prune's
                raise OSError(28, "No space left on device")
            real(path, cursor)

        with mock.patch.object(snapshot, "save_cursor", side_effect=save):
            self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(calls), 2)
        self.assertFalse(old.exists())
        self.assertTrue(
            any("could not drop 1 pruned mark" in m for m in self.log_lines),
            self.log_lines,
        )

    def test_a_pruned_name_is_dropped_from_the_cursor(self):
        old = self.session("s", hook_line("s", "UserPromptSubmit", OLD),
                           hook_line("s", "Stop", OLD))  # fmt: skip
        # Keep the inode alive through a hard link, so the "new" file the
        # resumed session writes reuses it, as ext4 can with a freed inode.
        keep = self.home / "keep"
        os.link(old, keep)
        inode = old.stat().st_ino
        self.assertEqual(self.run_once(), 0)
        self.assertFalse(old.exists())
        self.assertNotIn("s.jsonl", self.cursor()["sessions"])
        # The resumed session's file: same name, same inode, more bytes than
        # the old mark's offset.
        lines = [hook_line("s", "UserPromptSubmit", RECENT),
                 hook_line("s", "Notification", RECENT),
                 hook_line("s", "Stop", RECENT)]  # fmt: skip
        with keep.open("r+") as fh:
            fh.truncate(0)
            fh.write("".join(lines))
        os.replace(keep, old)
        self.assertEqual(old.stat().st_ino, inode)
        self.assertEqual(self.run_once(at=NOW + timedelta(minutes=5)), 0)
        self.assertEqual(
            [e["event"] for e in self.ingest.docs[-1]["sessions"]],
            ["UserPromptSubmit", "Notification", "Stop"],
        )


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = self.dir / "config.toml"

    def write(self, text, mode=0o600):
        self.path.write_text(text)
        os.chmod(self.path, mode)

    def test_a_collector_only_config_needs_no_jira(self):
        self.write(f'[collector]\nenabled = true\ningest_url = "http://m:8765/ingest"\n'
                   f'token = "{TOKEN}"\n')  # fmt: skip
        cfg = config.load_collector(self.path)
        self.assertEqual(cfg["token"], TOKEN)
        self.assertEqual(cfg["spool_max"], 50)
        self.assertEqual(cfg["retention_days"], 30)
        self.assertEqual(cfg["config_path"], self.path)

    def test_retention_days_is_validated_when_disabled(self):
        self.write("[collector]\nenabled = false\nretention_days = 7\n")
        self.assertEqual(config.load_collector(self.path)["retention_days"], 7)
        for bad in ("0", "-1", "true", "1.5"):
            with self.subTest(value=bad):
                self.write(f"[collector]\nenabled = false\nretention_days = {bad}\n")
                with self.assertRaisesRegex(config.ConfigError, "retention_days"):
                    config.load_collector(self.path)

    def test_a_readable_config_is_refused(self):
        self.write(f'[collector]\ningest_url = "http://m/ingest"\ntoken = "{TOKEN}"\n',
                   mode=0o644)  # fmt: skip
        with self.assertRaisesRegex(config.ConfigError, "chmod 600"):
            config.load_collector(self.path)

    def test_bad_values_are_refused(self):
        cases = {
            '[collector]\ningest_url = "http://m/ingest"\n': "collector.token",
            f'[collector]\ningest_url = "m:8765"\ntoken = "{TOKEN}"\n': "ingest_url",
            f'[collector]\ningest_url = "http://m/i"\ntoken = "{TOKEN}"\n'
            "spool_max = 0\n": "spool_max",
            f'[collector]\ningest_url = "http://m/i"\ntoken = "{TOKEN}"\n'
            "retention_days = 0\n": "retention_days",
            f'[collector]\ningest_url = "http://m/i"\ntoken = "{TOKEN}"\n'
            'retention_days = "30"\n': "retention_days",
            f'[collector]\ningest_url = "http://m/i"\ntoken = "{TOKEN}"\n'
            'host = "a.b"\n': "collector.host",
            "[server]\nport = 1\n": "no \\[collector\\]",
            f'[collector]\ningest_url = "http://[::1/ingest"\ntoken = "{TOKEN}"\n': "ingest_url",
            f'[collector]\ningest_url = "http://m:abc/ingest"\ntoken = "{TOKEN}"\n': "ingest_url",
        }
        for text, pattern in cases.items():
            with self.subTest(pattern=pattern):
                # Enabled, since a disabled table needs no URL or token.
                self.write(
                    text.replace("[collector]\n", "[collector]\nenabled = true\n")
                )
                with self.assertRaisesRegex(config.ConfigError, pattern):
                    config.load_collector(self.path)

    def test_enabled_must_be_a_bool(self):
        self.write('[collector]\nenabled = "yes"\n')
        with self.assertRaisesRegex(config.ConfigError, "true or false"):
            config.load_collector(self.path)

    def test_enabled_false_alone_is_a_valid_off_switch(self):
        self.write("[collector]\nenabled = false\n")
        cfg = config.load_collector(self.path)
        self.assertFalse(cfg["enabled"])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dashboard.main(["collect", "--config", str(self.path)])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("enabled is false", err.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = dashboard.main(
                ["install", "--collector-only", "--config", str(self.path)]
            )
        self.assertEqual(rc, 2)
        self.assertIn("enabled is false", err.getvalue())

    def test_the_mode_check_still_applies_when_disabled(self):
        self.write("[collector]\nenabled = false\n", mode=0o644)
        with self.assertRaisesRegex(config.ConfigError, "chmod 600"):
            config.load_collector(self.path)


FAKE_HOME = {"darwin": Path("/Users/tester"), "linux": Path("/home/tester")}
FAKE_PATH = "/opt/fake/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def fake_config(home):
    return home / ".config/task-dashboard/config.toml"


class TestUnits(unittest.TestCase):
    def check_golden(self, name, text):
        golden = GOLDEN / name
        if os.environ.get("UPDATE_GOLDEN"):
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_text(text)
        self.assertEqual(text, golden.read_text(), f"{name} drifted from its golden")

    def test_plist_matches_golden(self):
        home = FAKE_HOME["darwin"]
        text = collector_install.render_plist(home, FAKE_PATH, fake_config(home))
        self.check_golden(f"{collector_install.LABEL}.plist", text)
        doc = plistlib.loads(text.encode())
        self.assertTrue(doc["RunAtLoad"])
        self.assertEqual(doc["StartInterval"], 300)
        self.assertEqual(
            doc["ProgramArguments"],
            ["/Users/tester/.local/bin/task-dashboard-launch", "collect",
             "--config", "/Users/tester/.config/task-dashboard/config.toml"],
        )  # fmt: skip

    def test_systemd_units_match_golden(self):
        unit = collector_install.UNIT
        home = FAKE_HOME["linux"]
        self.check_golden(
            f"{unit}.service",
            collector_install.render_service(home, FAKE_PATH, fake_config(home)),
        )
        self.check_golden(f"{unit}.timer", collector_install.render_timer())

    def test_an_awkward_config_path_is_one_execstart_word(self):
        text = collector_install.render_service(
            FAKE_HOME["linux"], FAKE_PATH, Path('/home/t/my "cfg" 100%.toml')
        )
        self.assertIn(' --config "/home/t/my \\"cfg\\" 100%%.toml"\n', text)

    def test_mise_installs_resolve_to_their_shim(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        real = tmp / "mise/installs/uv/0.13.5/uv-aarch64/uv"
        real.parent.mkdir(parents=True)
        real.touch()
        self.assertEqual(collector_install.stable_tool(str(real)), str(real))
        shim = tmp / "mise/shims/uv"
        shim.parent.mkdir(parents=True)
        shim.touch()
        self.assertEqual(collector_install.stable_tool(str(real)), str(shim))
        self.assertEqual(collector_install.stable_tool("/usr/bin/git"), "/usr/bin/git")


class FakeScheduler:
    def __init__(self, linger="yes", bootstrap_failures=0):
        self.calls = []
        self.linger = linger
        self.bootstrap_failures = bootstrap_failures

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if argv[0] == "loginctl":
            return 0, self.linger + "\n", ""
        if argv[:2] == ["launchctl", "bootout"]:
            return 3, "", "Boot-out failed: 3: No such process"
        if argv[:2] == ["launchctl", "bootstrap"] and self.bootstrap_failures:
            self.bootstrap_failures -= 1
            return 5, "", "Bootstrap failed: 5: Input/output error"
        return 0, "", ""


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        cfg_path = self.home / "config.toml"
        cfg_path.write_text(f'[collector]\nenabled = true\ningest_url = "http://m/i"\n'
                            f'token = "{TOKEN}"\n')  # fmt: skip
        os.chmod(cfg_path, 0o600)
        self.cfg = config.load_collector(cfg_path)
        self.out = []

    def plan(self, platform):
        return collector_install.plan(
            self.cfg, home=self.home, platform=platform, uv="/opt/fake/bin/uv",
            path_env=FAKE_PATH, uid=501, user="tester",
        )  # fmt: skip

    def written(self):
        files = [p for p in self.home.rglob("*") if p.is_file()]
        return {str(p.relative_to(self.home)): p for p in files}

    def test_macos_writes_the_agent_and_bootstraps_it(self):
        runner = FakeScheduler()
        rc = collector_install.execute(self.plan("darwin"), runner=runner,
                                       log=self.out.append)  # fmt: skip
        self.assertEqual(rc, 0, self.out)
        files = self.written()
        plist = files[f"Library/LaunchAgents/{collector_install.LABEL}.plist"]
        launcher = files[".local/bin/task-dashboard-launch"]
        self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o755)
        self.assertIn("UV=/opt/fake/bin/uv\n", launcher.read_text())
        self.assertNotIn("@UV@", launcher.read_text())
        # The config install loaded, not the default path, reaches the agent.
        args = plistlib.loads(plist.read_bytes())["ProgramArguments"]
        self.assertEqual(
            args[1:], ["collect", "--config", str(self.home / "config.toml")]
        )
        self.assertEqual(
            [c[:2] for c in runner.calls[1:]],
            [["launchctl", "bootout"], ["launchctl", "bootstrap"]],
        )
        self.assertEqual(
            runner.calls[2], ["launchctl", "bootstrap", "gui/501", str(plist)]
        )
        self.assertEqual(runner.calls[0][-3:], ["python", "-c", "pass"])
        for p in files.values():
            if p.name != "config.toml":
                self.assertNotIn(TOKEN, p.read_text())
        self.assertNotIn(TOKEN, json.dumps(runner.calls))

    def test_linux_enables_the_timer_and_checks_lingering(self):
        runner = FakeScheduler(linger="no")
        rc = collector_install.execute(self.plan("linux"), runner=runner,
                                       log=self.out.append)  # fmt: skip
        self.assertEqual(rc, 0, self.out)
        files = self.written()
        self.assertIn(".config/systemd/user/task-dashboard-collector.service", files)
        self.assertIn(".config/systemd/user/task-dashboard-collector.timer", files)
        self.assertIn(
            [
                "systemctl",
                "--user",
                "enable",
                "--now",
                "task-dashboard-collector.timer",
            ],
            runner.calls,
        )
        self.assertIn(["loginctl", "show-user", "tester", "--property=Linger",
                       "--value"], runner.calls)  # fmt: skip
        warning = [m for m in self.out if m.startswith("WARNING")]
        self.assertEqual(len(warning), 1)
        self.assertIn("loginctl enable-linger tester", warning[0])

    def test_lingering_on_says_nothing(self):
        collector_install.execute(self.plan("linux"), runner=FakeScheduler(),
                                  log=self.out.append)  # fmt: skip
        self.assertFalse([m for m in self.out if m.startswith("WARNING")])

    def test_a_failed_command_stops_the_install(self):
        def runner(argv, timeout):
            return (1, "", "boom") if argv[0] == "systemctl" else (0, "yes", "")

        rc = collector_install.execute(self.plan("linux"), runner=runner,
                                       log=self.out.append)  # fmt: skip
        self.assertEqual(rc, 1)
        self.assertTrue(any(m.startswith("FAILED") for m in self.out))

    def test_dry_run_text_names_every_file_and_command(self):
        text = collector_install.describe(self.plan("darwin"))
        self.assertIn("launchctl bootstrap gui/501", text)
        self.assertIn("<key>StartInterval</key>", text)
        self.assertEqual(set(self.written()), {"config.toml"})

    def test_unknown_platform_and_missing_uv_are_refused(self):
        with self.assertRaisesRegex(collector_install.InstallError, "platform"):
            self.plan("win32")
        with self.assertRaisesRegex(collector_install.InstallError, "uv is not"):
            collector_install.plan(self.cfg, home=self.home, platform="linux", uv="",
                                   path_env=FAKE_PATH, uid=1, user="t")  # fmt: skip

    def test_an_off_hook_is_noted(self):
        # A quoted key: tomllib reads it as enabled, the hook's line scan not.
        path = self.home / "off.toml"
        path.write_text(f'[collector]\n"enabled" = true\ningest_url = "http://m/i"\n'
                        f'token = "{TOKEN}"\n')  # fmt: skip
        os.chmod(path, 0o600)
        cfg = config.load_collector(path)
        p = collector_install.plan(cfg, home=self.home, platform="linux",
                                   uv="/u/uv", path_env=FAKE_PATH, uid=1, user="t")  # fmt: skip
        self.assertTrue(any("hook is off" in n for n in p.notes))
        self.assertFalse(any("hook is off" in n for n in self.plan("linux").notes))

    def bootstraps(self, runner):
        return [c for c in runner.calls if c[:2] == ["launchctl", "bootstrap"]]

    def test_a_failed_bootstrap_is_retried(self):
        runner = FakeScheduler(bootstrap_failures=2)
        sleeps: list[float] = []
        rc = collector_install.execute(self.plan("darwin"), runner=runner,
                                       log=self.out.append, sleep=sleeps.append)  # fmt: skip
        self.assertEqual(rc, 0, self.out)
        self.assertEqual(len(self.bootstraps(runner)), 3)
        self.assertEqual(sleeps, [1.0, 1.0])

    def test_bootstrap_gives_up_after_three_retries(self):
        runner = FakeScheduler(bootstrap_failures=99)
        sleeps: list[float] = []
        rc = collector_install.execute(self.plan("darwin"), runner=runner,
                                       log=self.out.append, sleep=sleeps.append)  # fmt: skip
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.bootstraps(runner)), 4)
        self.assertEqual(sleeps, [1.0, 1.0, 1.0])
        self.assertTrue(any(m.startswith("FAILED (5)") for m in self.out))

    def test_only_bootstrap_is_retried(self):
        def runner(argv, timeout):
            return (1, "", "boom") if argv[0] == "systemctl" else (0, "yes", "")

        sleeps: list[float] = []
        collector_install.execute(self.plan("linux"), runner=runner,
                                  log=self.out.append, sleep=sleeps.append)  # fmt: skip
        self.assertEqual(sleeps, [])

    def test_a_disabled_collector_is_not_installed(self):
        path = self.home / "disabled.toml"
        path.write_text(f'[collector]\nenabled = false\ningest_url = "http://m/i"\n'
                        f'token = "{TOKEN}"\n')  # fmt: skip
        os.chmod(path, 0o600)
        cfg = config.load_collector(path)
        with self.assertRaisesRegex(collector_install.InstallError, "enabled is false"):
            collector_install.plan(cfg, home=self.home, platform="darwin", uv="/u/uv",
                                   path_env=FAKE_PATH, uid=1, user="t")  # fmt: skip
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = dashboard.main(["install", "--collector-only", "--config", str(path)])
        self.assertEqual(rc, 2)
        self.assertIn("enabled is false", err.getvalue())
        self.assertEqual(set(self.written()), {"config.toml", "disabled.toml"})

    def test_install_without_collector_only_is_the_server_install(self):
        # Plain `install` is the server's LaunchAgent (server_install.py), not a
        # refusal: with no server config it stops on that config before writing.
        err = io.StringIO()
        missing = str(self.home / "no-such-config.toml")
        with (
            mock.patch.object(sys, "platform", "darwin"),
            # Path.home() must not reach the real ~/Library/LaunchAgents plist.
            mock.patch.dict(os.environ, {"HOME": str(self.home)}),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(dashboard.main(["install", "--config", missing]), 2)
        self.assertIn("config file not found", err.getvalue())
        self.assertNotIn("#1093", err.getvalue())


class TestLauncher(unittest.TestCase):
    """The rendered launcher, run for real under this test's own uv."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        uv = shutil.which("uv") or os.environ.get("UV")
        if not uv:
            self.skipTest("uv not on PATH")
        self.launcher = self.tmp / "task-dashboard-launch"
        self.launcher.write_text(collector_install.render_launcher(uv))
        self.launcher.chmod(0o755)
        self.manifest = self.tmp / "installed_plugins.json"

    def plugin(self, name):
        root = self.tmp / name
        stub = root / "scripts/task-dashboard/dashboard.py"
        stub.parent.mkdir(parents=True)
        stub.write_text(
            "import json, sys\n"
            f"print(json.dumps({{'root': {name!r}, 'argv': sys.argv[1:]}}))\n"
        )
        return str(root)

    def launch(self, *args):
        env = dict(os.environ, TASK_DASHBOARD_PLUGINS_JSON=str(self.manifest))
        return subprocess.run([str(self.launcher), *args], capture_output=True,
                              text=True, env=env, timeout=120)  # fmt: skip

    def test_picks_the_user_scope_workflow_skills_entry(self):
        self.manifest.write_text(json.dumps({"version": 2, "plugins": {
            "agent-guidance@agent-guidance": [
                {"scope": "user", "installPath": self.plugin("other")}],
            "workflow-skills@workflow-skills": [
                {"scope": "project", "installPath": self.plugin("project")},
                {"scope": "user", "installPath": self.plugin("user")},
            ],
        }}))  # fmt: skip
        proc = self.launch("collect", "--config", "x")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout),
                         {"root": "user", "argv": ["collect", "--config", "x"]})  # fmt: skip

    def test_skips_a_path_without_the_dashboard(self):
        self.manifest.write_text(json.dumps({"plugins": {
            "workflow-skills@workflow-skills": [
                {"scope": "user", "installPath": str(self.tmp / "gone")},
                {"scope": "project", "installPath": self.plugin("project")},
            ],
        }}))  # fmt: skip
        proc = self.launch("collect")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["root"], "project")

    def test_errors_clearly_when_the_entry_is_absent(self):
        self.manifest.write_text(json.dumps({"plugins": {
            "agent-guidance@agent-guidance": [{"installPath": self.plugin("other")}],
        }}))  # fmt: skip
        proc = self.launch("collect")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("workflow-skills is not installed", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_errors_clearly_when_the_manifest_is_missing(self):
        proc = self.launch("collect")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not found", proc.stderr)


if __name__ == "__main__":
    unittest.main()
