#!/usr/bin/env python3
"""Hermetic tests for the fleet view's ingest: ingest.py, snapshot_adapter.py,
the `[ingest]` config and `dashboard.py ingest-token add`.

The real server runs in-process on an ephemeral 127.0.0.1 port over a temp DB,
with a no-op sync. Every snapshot is synthetic and is checked against
snapshot.validate() before it is sent, so a fixture cannot drift from schema 1.
Needs Python 3.11: run via scripts/test-task-dashboard-ingest.sh.
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
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))

# mypy checks this file without scripts/task-dashboard on its path; the
# modules themselves are type-checked in their own group (scripts/typecheck.sh).
import config  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import ingest  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402
import snapshot  # type: ignore[import-not-found]  # noqa: E402
import snapshot_adapter  # type: ignore[import-not-found]  # noqa: E402

TOKENS = {"lindev": "lindev-token-" + "a" * 32, "macbook": "macbook-token-" + "b" * 32}
NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)


def make_config(machines):
    cfg = config.deep_merge(
        config.DEFAULTS,
        {
            "jira": {
                "site": "example.invalid",
                "board_ids": [],
                "fields": {"sprint": "s", "flagged": "f"},
            },
            "github": {"enabled": False},
        },
    )
    cfg["ingest"]["machines"] = {h: ingest.digest(t) for h, t in machines.items()}
    return cfg


def make_doc(host="lindev", taken_at="2026-10-23T11:00:00.000Z", branches=("a", "b")):
    doc = {
        "schema": 1,
        "host": host,
        "taken_at": taken_at,
        "worktrees": [
            {
                "repo": "demo",
                "path": f"/home/u/src/worktrees/demo/{b}",
                "branch": f"bestdan/{b}",
                "head": "0" * 40,
                "dirty": False,
                "unpushed": 0,
                "landed": {"state": "not_landed", "reason": "no merged PR"},
            }
            for b in branches
        ],
        "handoffs": [
            {
                "path": "/home/u/src/worktrees/demo/.handoffs/note.md",
                "mtime": "2026-10-23T10:00:00.000Z",
                "aim": "finish the demo",
                "branch": "bestdan/a",
                "pr": None,
                "expires": "2026-10-30",
                "created": "2026-10-22",
            }
        ],
        "tmux": [
            {"name": "work", "created": "2026-10-23T09:00:00.000Z", "attached": True}
        ],
        "sessions": [
            {
                "v": 1,
                "ts": "2026-10-23T10:30:00.000Z",
                "event": "Stop",
                "session_id": "s-1",
                "cwd": "/home/u/src/demo",
            }
        ],
        "history": [
            {
                "ts": "2026-10-23T10:29:00.000Z",
                "session_id": "s-1",
                "project": "/home/u/src/demo",
                "approval_shaped": True,
            }
        ],
    }
    assert snapshot.validate(doc) == [], snapshot.validate(doc)
    return doc


class IngestCase(unittest.TestCase):
    machines: dict = TOKENS
    extra_hosts: list = []

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "dashboard.db"
        cfg = make_config(self.machines)
        cfg["server"]["extra_allowed_hosts"] = list(self.extra_hosts)
        self.httpd, self.syncer = server.build(
            cfg, self.db_path, 0, lambda mode: None, run_at_start=False
        )
        self.httpd.app.now = lambda: NOW
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def post(self, doc=None, *, token="lindev", host=None, raw=None, headers=None,
             length=None):  # fmt: skip
        body = raw if raw is not None else json.dumps(doc or make_doc()).encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": f"127.0.0.1:{self.port}" if host is None else host}
        if token is not None:
            hdrs["Authorization"] = f"Bearer {TOKENS.get(token, token)}"
        hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        c.putrequest("POST", "/ingest", skip_host=True, skip_accept_encoding=True)
        for k, v in hdrs.items():
            c.putheader(k, v)
        if length != "omit":
            c.putheader("Content-Length", str(len(body) if length is None else length))
        c.endheaders()
        if length is None:
            c.send(body)
        resp = c.getresponse()
        out = resp.status, resp.read().decode()
        c.close()
        return out

    def q(self, sql, *args):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def assert_nothing_written(self):
        self.assertEqual(self.q("SELECT * FROM machines"), [])
        self.assertEqual(
            self.q("SELECT * FROM issues WHERE source LIKE 'machine:%'"), []
        )
        self.assertEqual(self.q("SELECT * FROM session_events"), [])
        self.assertEqual(self.q("SELECT * FROM history_entries"), [])


class UnconfiguredTest(IngestCase):
    machines: dict = {}

    def test_ingest_is_404_without_ingest_config(self):
        status, _ = self.post()
        self.assertEqual(status, 404)
        self.assert_nothing_written()


class RefusalTest(IngestCase):
    def test_wrong_token_is_401(self):
        status, _ = self.post(token="not-a-token")
        self.assertEqual(status, 401)
        self.assert_nothing_written()

    def test_missing_or_non_bearer_auth_is_401(self):
        self.assertEqual(self.post(token=None)[0], 401)
        status, _ = self.post(
            token=None, headers={"Authorization": f"Basic {TOKENS['lindev']}"}
        )
        self.assertEqual(status, 401)
        self.assert_nothing_written()

    def test_token_for_another_host_is_403(self):
        status, body = self.post(make_doc(host="lindev"), token="macbook")
        self.assertEqual(status, 403, body)
        self.assert_nothing_written()

    def test_oversized_body_is_413_before_it_is_read(self):
        status, _ = self.post(raw=b"", length=ingest.MAX_BODY + 1)
        self.assertEqual(status, 413)
        self.assert_nothing_written()

    def test_missing_content_length_is_411(self):
        status, _ = self.post(length="omit")
        self.assertEqual(status, 411)
        self.assert_nothing_written()

    def test_unknown_schema_is_400(self):
        for schema in (2, "1", None):
            doc = make_doc()
            doc["schema"] = schema
            status, body = self.post(doc)
            self.assertEqual(status, 400, schema)
            self.assertIn("unsupported schema", body)
        self.assert_nothing_written()

    def test_malformed_schema_1_is_400(self):
        doc = make_doc()
        doc["history"][0]["prompt"] = "planted prompt text"  # a closed key set
        status, body = self.post(doc)
        self.assertEqual(status, 400)
        self.assertIn("unexpected key", body)
        for raw in (b"not json", b"[1, 2]"):
            self.assertEqual(self.post(raw=raw)[0], 400, raw)
        self.assert_nothing_written()

    def test_unparseable_taken_at_is_400(self):
        status, _ = self.post(make_doc(taken_at="yesterday"))
        self.assertEqual(status, 400)
        self.assert_nothing_written()

    def test_taken_at_far_ahead_of_server_time_is_400(self):
        # NOW is 12:00Z; the tolerance is five minutes.
        status, body = self.post(make_doc(taken_at="2026-10-23T12:06:00.000Z"))
        self.assertEqual(status, 400)
        self.assertIn("ahead of server time", body)
        self.assert_nothing_written()
        status, _ = self.post(make_doc(taken_at="2026-10-23T12:04:00.000Z"))
        self.assertEqual(status, 200)

    def test_database_errors_are_503_and_never_leak(self):
        # db.connect cannot open a directory as a database.
        self.httpd.app.ingest.db_path = Path(self.tmp.name)
        status, body = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "database unavailable"})
        self.httpd.app.ingest.db_path = self.db_path
        # A lock held past the busy timeout, raised inside the write.
        locked = sqlite3.OperationalError("database is locked")
        with mock.patch.object(snapshot_adapter, "ingest", side_effect=locked):
            status, body = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "database unavailable"})
        self.assert_nothing_written()

    def test_foreign_host_header_is_403_even_with_a_valid_token(self):
        status, _ = self.post(host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)
        status, _ = self.post(host="mini.tail0.ts.net:8765")
        self.assertEqual(status, 403)
        self.assert_nothing_written()

    def test_get_is_not_a_route(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/ingest", headers={"Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(c.getresponse().status, 404)
        c.close()


class TailnetHostTest(IngestCase):
    extra_hosts = ["mini.tail0.ts.net:8765"]

    def test_configured_extra_host_ingests_without_an_origin(self):
        status, body = self.post(host="mini.tail0.ts.net:8765")
        self.assertEqual(status, 200, body)


class WriteTest(IngestCase):
    def test_snapshot_lands_as_machine_rows(self):
        status, body = self.post()
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["worktrees"], 2)
        rows = self.q(
            "SELECT kind, key, summary FROM issues WHERE source = 'machine:lindev'"
            " ORDER BY key"
        )
        self.assertEqual(
            rows,
            [
                ("handoff", "handoff:/home/u/src/worktrees/demo/.handoffs/note.md",
                 "finish the demo"),
                ("tmux", "tmux:work", "work"),
                ("worktree", "worktree:/home/u/src/worktrees/demo/a", "demo: bestdan/a"),
                ("worktree", "worktree:/home/u/src/worktrees/demo/b", "demo: bestdan/b"),
            ],
        )  # fmt: skip
        raw = self.q("SELECT raw_json FROM issues WHERE key = 'tmux:work'")[0][0]
        self.assertTrue(json.loads(raw)["attached"])
        self.assertEqual(
            self.q("SELECT host, last_snapshot_at, received_at FROM machines"),
            [("lindev", "2026-10-23T11:00:00.000Z", "2026-10-23T12:00:00Z")],
        )
        self.assertEqual(
            self.q("SELECT host, event, session_id FROM session_events"),
            [("lindev", "Stop", "s-1")],
        )
        self.assertEqual(
            self.q("SELECT host, session_id, approval_shaped FROM history_entries"),
            [("lindev", "s-1", 1)],
        )

    def test_second_snapshot_replaces_its_host_and_leaves_others(self):
        self.assertEqual(self.post(make_doc(host="macbook"), token="macbook")[0], 200)
        conn = db.connect(self.db_path)
        conn.execute(
            "INSERT INTO issues (source, kind, key, summary) VALUES"
            " ('jira', 'ticket', 'ABC-1', 'a jira row')"
        )
        conn.commit()
        conn.close()
        self.assertEqual(self.post()[0], 200)
        later = make_doc(taken_at="2026-10-23T11:30:00.000Z", branches=("c",))
        self.assertEqual(self.post(later)[0], 200)
        self.assertEqual(
            self.q(
                "SELECT key FROM issues WHERE source = 'machine:lindev'"
                " AND kind = 'worktree'"
            ),
            [("worktree:/home/u/src/worktrees/demo/c",)],
        )
        self.assertEqual(
            self.q(
                "SELECT count(*) FROM issues WHERE source = 'machine:macbook'"
                " AND kind = 'worktree'"
            ),
            [(2,)],
        )
        self.assertEqual(self.q("SELECT key FROM jira_issues"), [("ABC-1",)])
        # Deltas append: two lindev snapshots, one macbook.
        self.assertEqual(
            self.q(
                "SELECT host, count(*) FROM session_events GROUP BY host ORDER BY host"
            ),
            [("lindev", 2), ("macbook", 1)],
        )
        self.assertEqual(
            self.q("SELECT last_snapshot_at FROM machines WHERE host = 'lindev'"),
            [("2026-10-23T11:30:00.000Z",)],
        )

    def test_resent_or_older_snapshot_is_409_and_writes_nothing(self):
        self.assertEqual(self.post()[0], 200)
        before = self.q("SELECT * FROM session_events")
        older = make_doc(taken_at="2026-10-23T10:00:00.000Z", branches=("z",))
        for doc in (make_doc(), older):
            status, _ = self.post(doc)
            self.assertEqual(status, 409)
        self.assertEqual(self.q("SELECT * FROM session_events"), before)
        self.assertEqual(
            self.q("SELECT count(*) FROM issues WHERE key LIKE 'worktree:%/z'"), [(0,)]
        )


class AdapterTest(unittest.TestCase):
    def test_a_failed_write_rolls_back_whole(self):
        with tempfile.TemporaryDirectory() as d:
            conn = db.connect(Path(d) / "d.db")
            snapshot_adapter.ingest(conn, make_doc(), NOW)
            bad = make_doc(taken_at="2026-10-23T11:30:00.000Z", branches=("c",))
            bad["history"][0]["approval_shaped"] = object()  # int() of it raises
            with self.assertRaises(TypeError):
                snapshot_adapter.ingest(conn, bad, NOW)
            keys = conn.execute(
                "SELECT key FROM issues WHERE kind = 'worktree' ORDER BY key"
            ).fetchall()
            self.assertEqual(len(keys), 2)
            self.assertEqual(
                conn.execute("SELECT last_snapshot_at FROM machines").fetchall(),
                [("2026-10-23T11:00:00.000Z",)],
            )
            conn.close()


class ConfigTest(unittest.TestCase):
    BASE = '[jira]\nsite = "x"\nboard_ids = []\n[jira.fields]\nsprint = "s"\nflagged = "f"\n'

    def load(self, extra):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(self.BASE + extra)
            return config.load(p)

    def test_ingest_defaults_to_off(self):
        self.assertEqual(self.load("")["ingest"]["machines"], {})

    def test_valid_machine_digest_loads(self):
        line = f'lindev = "{ingest.digest("t")}"'
        cfg = self.load(f"[ingest.machines]\n{line}\n")
        self.assertEqual(list(cfg["ingest"]["machines"]), ["lindev"])

    def test_bad_machine_entries_are_config_errors(self):
        for bad in (
            'lindev = "plain-token"',
            'lindev = "sha256:abc"',
            '"bad host" = "sha256:' + "0" * 64 + '"',
            "lindev = 3",
        ):
            with self.assertRaises(config.ConfigError, msg=bad):
                self.load(f"[ingest.machines]\n{bad}\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text("ingest = 3\n" + self.BASE)  # root key, before any table
            with self.assertRaises(config.ConfigError) as ctx:
                config.load(p)
            self.assertIn("ingest must be a table", str(ctx.exception))

    def test_two_machines_sharing_a_digest_is_a_config_error(self):
        d = ingest.digest("one-token-for-both")
        with self.assertRaises(config.ConfigError) as ctx:
            self.load(f'[ingest.machines]\nlindev = "{d}"\nmacbook = "{d}"\n')
        self.assertIn("same token digest as ingest.machines.lindev", str(ctx.exception))
        other = ingest.digest("another-token")
        cfg = self.load(f'[ingest.machines]\nlindev = "{d}"\nmacbook = "{other}"\n')
        self.assertEqual(sorted(cfg["ingest"]["machines"]), ["lindev", "macbook"])


class TokenCliTest(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dashboard.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_add_prints_a_token_and_its_matching_digest(self):
        rc, out, _ = self.run_cli("ingest-token", "add", "lindev")
        self.assertEqual(rc, 0)
        token = re.search(r"collector's config:\n\n  (\S+)", out)
        line = re.search(r'lindev = "(sha256:[0-9a-f]{64})"', out)
        assert token is not None and line is not None, out
        self.assertEqual(ingest.digest(token.group(1)), line.group(1))
        rc2, out2, _ = self.run_cli("ingest-token", "add", "lindev")
        self.assertNotEqual(out, out2)

    def test_add_refuses_a_non_host_name(self):
        rc, _, err = self.run_cli("ingest-token", "add", "../etc")
        self.assertEqual(rc, 2)
        self.assertIn("host name", err)


class DigestTest(unittest.TestCase):
    def test_every_machine_is_compared(self):
        ing = ingest.Ingest(
            {h: ingest.digest(t) for h, t in TOKENS.items()}, Path("/nonexistent"),
            lambda: NOW,
        )  # fmt: skip
        self.assertEqual(ing.machine_for(TOKENS["macbook"]), "macbook")
        self.assertIsNone(ing.machine_for(""))
        self.assertIsNone(ing.machine_for(TOKENS["lindev"] + "x"))


if __name__ == "__main__":
    unittest.main()
