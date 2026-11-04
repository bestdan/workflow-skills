#!/usr/bin/env python3
"""Hermetic tests for running the agent commands on another machine's
dashboard: agent_remote.py's `POST /agent` route and client, the `[agent]` and
`[remote]` config, and `dashboard.py agent-token add`.

The real server runs in-process on an ephemeral 127.0.0.1 port over a temp DB
and config, with a no-op sync. An accepted request runs dashboard.py as a real
child process, so the end-to-end cases show what a remote caller actually gets.
Needs Python 3.11: run via scripts/test-task-dashboard-agent-remote.sh.
"""

import contextlib
import http.client
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))

import agent_remote  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import ingest  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402

TOKEN = "lindev-agent-token-" + "a" * 32
INGEST_TOKEN = "lindev-ingest-token-" + "b" * 32

SERVER_TOML = """
[jira]
enabled = false
[github]
enabled = false
"""


def run_cli(*argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        if env is not None:
            stack.enter_context(mock.patch.dict(os.environ, env))
        rc = dashboard.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class ServerCase(unittest.TestCase):
    machines: dict = {"lindev": TOKEN}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.db_path = d / "dashboard.db"
        self.config_path = d / "server.toml"
        self.config_path.write_text(SERVER_TOML)
        cfg = config.load(self.config_path)
        cfg["agent"]["machines"] = {
            h: ingest.digest(t) for h, t in self.machines.items()
        }
        cfg["ingest"]["machines"] = {"lindev": ingest.digest(INGEST_TOKEN)}
        self.httpd, _ = server.build(
            cfg, self.db_path, 0, lambda mode: None, run_at_start=False,
            config_path=self.config_path, build_parser=dashboard.build_parser,
        )  # fmt: skip
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/agent"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def post(self, body, *, token=TOKEN, path="/agent"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        c.request("POST", path, body=raw, headers=headers)
        resp = c.getresponse()
        out = resp.status, resp.read().decode()
        c.close()
        return out

    def client_config(self, *, token=TOKEN, mode=0o600, url=None):
        p = Path(self.tmp.name) / "client.toml"
        p.write_text(
            "[collector]\nenabled = false\n[remote]\n"
            f'url = "{url or self.url}"\ntoken = "{token}"\n'
        )
        p.chmod(mode)
        return p


class UnconfiguredTest(ServerCase):
    machines: dict = {}

    def test_no_agent_machines_means_no_route(self):
        status, _ = self.post({"argv": ["items"]})
        self.assertEqual(status, 404)


class AuthTest(ServerCase):
    def test_missing_or_wrong_token_is_401(self):
        for token in (None, "nope", INGEST_TOKEN):
            status, body = self.post({"argv": ["items"]}, token=token)
            self.assertEqual(status, 401, token)
            self.assertNotIn("stdout", body)

    def test_oversized_body_is_413_before_it_is_read(self):
        # Declare an oversized body and send none of it. Sending the full
        # MAX_BODY + 1 bytes races the server, which answers 413 and closes
        # without reading: the client's sendall then hits EPIPE (#1378). A 413
        # with no body sent is also the direct proof that nothing was read.
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        c.putrequest("POST", "/agent")
        c.putheader("Content-Type", "application/json")
        c.putheader("Authorization", f"Bearer {TOKEN}")
        c.putheader("Content-Length", str(agent_remote.MAX_BODY + 1))
        c.endheaders()
        resp = c.getresponse()
        resp.read()
        c.close()
        self.assertEqual(resp.status, 413)


class RefusalTest(ServerCase):
    def refused(self, argv, **extra):
        status, body = self.post({"argv": argv, **extra})
        self.assertEqual(status, 400, argv)
        return json.loads(body)["error"]

    def test_commands_outside_the_allow_list(self):
        for argv in (["sync"], ["serve"], ["export", "/tmp/x"], ["import", "/tmp/x"],
                     ["install"], ["uninstall"], ["agent-token", "add", "x"]):  # fmt: skip
            self.assertIn("not run remotely", self.refused(argv))

    def test_db_and_config_are_the_servers(self):
        for argv in (["items", "--db", "/tmp/x.db"], ["items", "--config=/x"],
                     ["items", "--conf", "/x"], ["items", "--d", "/x"]):  # fmt: skip
            self.assertIn("server's to choose", self.refused(argv))

    def test_plan_set_reads_no_file(self):
        argv = ["plan", "set", "--scope", "all", "--by", "x", "--file", "/etc/passwd"]
        self.assertIn("--file must be -", self.refused(argv))

    def test_bad_argv_shapes(self):
        for argv in ([], "items", [1], ["items", "a\0b"], ["x"] * 65):
            self.refused(argv)
        self.assertIn("help", self.refused(["items", "--help"]))
        self.assertIn("unrecognized", self.refused(["items", "--bogus"]))
        self.assertIn("stdin", self.refused(["items"], stdin=3))


class RunTest(ServerCase):
    def test_items_runs_against_the_servers_db(self):
        status, body = self.post({"argv": ["items"]})
        self.assertEqual(status, 200, body)
        doc = json.loads(body)
        self.assertEqual(doc["exit"], 0, doc["stderr"])
        self.assertEqual(json.loads(doc["stdout"])["items"], [])

    def test_a_commands_own_failure_comes_back_as_its_exit(self):
        status, body = self.post({"argv": ["items", "--kind", "nonsense"]})
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertEqual(doc["exit"], 2)
        self.assertIn("nonsense", doc["stderr"])

    def test_child_never_forwards(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw["env"])
            return mock.Mock(returncode=0, stdout="", stderr="")

        self.httpd.app.agent.run = fake_run
        self.post({"argv": ["items"]})
        self.assertEqual(seen[agent_remote.NO_REMOTE_ENV], "1")
        self.assertEqual(seen["TASK_DASHBOARD_DB"], str(self.db_path))
        self.assertEqual(seen["TASK_DASHBOARD_CONFIG"], str(self.config_path))


class ClientTest(ServerCase):
    def test_items_forwards_and_prints_the_remote_output(self):
        p = self.client_config()
        rc, out, err = run_cli("items", "--config", str(p))
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["items"], [])

    def test_overlay_write_lands_in_the_servers_db(self):
        p = self.client_config()
        rc, out, err = run_cli(
            "overlay", "pin", "--source", "github", "--key", "k1", "--config", str(p)
        )
        self.assertEqual(rc, 0, err)
        self.assertIn("overlay pin: done", out)
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute("SELECT item_key FROM pins").fetchall()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("k1", rows[0][0])

    def test_plan_set_sends_the_file_as_stdin(self):
        plan = Path(self.tmp.name) / "plan.json"
        plan.write_text("[]")
        sent = {}

        def poster(url, token, body):
            sent.update(json.loads(body))
            return 200, json.dumps({"exit": 0, "stdout": "ok\n", "stderr": ""})

        p = self.client_config()
        with mock.patch.object(agent_remote, "post", poster):
            args = ["plan", "set", "--scope", "all", "--by", "me", "--file", str(plan)]
            rc, out, _ = run_cli(*args, "--config", str(p))
        self.assertEqual((rc, out), (0, "ok\n"))
        self.assertEqual(sent["argv"][-2:], ["--file", "-"])
        self.assertNotIn("--config", sent["argv"])
        self.assertEqual(sent["stdin"], "[]")
        agent_remote.check_argv(sent["argv"], dashboard.build_parser)

    def test_wrong_token_says_how_to_fix_it(self):
        p = self.client_config(token="wrong")
        rc, _, err = run_cli("items", "--config", str(p))
        self.assertEqual(rc, 1)
        self.assertIn("agent-token add", err)

    def test_unreachable_server_is_exit_1(self):
        p = self.client_config(url="http://127.0.0.1:1/agent")
        rc, _, err = run_cli("items", "--config", str(p))
        self.assertEqual(rc, 1)
        self.assertIn("no answer", err)

    def test_group_readable_config_is_refused(self):
        p = self.client_config(mode=0o644)
        rc, _, err = run_cli("items", "--config", str(p))
        self.assertEqual(rc, 2)
        self.assertIn("chmod 600", err)

    def test_no_remote_env_keeps_it_local(self):
        p = self.client_config()
        with mock.patch.object(agent_remote, "forward") as fwd:
            # The DB comes from the environment, not --db, so the env guard
            # is the only thing keeping this run local.
            run_cli("items", "--config", str(p), env={
                agent_remote.NO_REMOTE_ENV: "1", "TASK_DASHBOARD_DB": str(self.db_path),
            })  # fmt: skip
        fwd.assert_not_called()


class ConfigTest(unittest.TestCase):
    def write(self, text, mode=0o600):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d))
        p = Path(d) / "config.toml"
        p.write_text(text)
        p.chmod(mode)
        return p

    def test_load_remote_is_none_without_a_table_or_a_file(self):
        self.assertIsNone(config.load_remote(self.write("[collector]\n")))
        self.assertIsNone(config.load_remote(Path(tempfile.gettempdir()) / "nope.toml"))

    def test_load_remote_validates(self):
        for bad in ('url = "x"\ntoken = "t"', 'url = "http://h:1/agent"',
                    'url = "http://h:1/agent"\ntoken = "t"\nextra = 1',
                    'url = "http://h:1/agent"\ntoken = " t"'):  # fmt: skip
            with self.assertRaises(config.ConfigError, msg=bad):
                config.load_remote(self.write(f"[remote]\n{bad}\n"))
        got = config.load_remote(
            self.write('[remote]\nurl = "http://h:1/agent"\ntoken = "t"\n')
        )
        assert got is not None
        self.assertEqual((got["url"], got["token"]), ("http://h:1/agent", "t"))

    def test_agent_machines_validated_like_ingest(self):
        d = ingest.digest("one")
        with self.assertRaises(config.ConfigError) as ctx:
            config.load(
                self.write(SERVER_TOML + f'[agent.machines]\na = "{d}"\nb = "{d}"\n')
            )
        self.assertIn("agent.machines.b has the same token digest", str(ctx.exception))
        with self.assertRaises(config.ConfigError) as ctx:
            config.load(self.write(SERVER_TOML + '[agent.machines]\na = "plain"\n'))
        self.assertIn("agent-token add", str(ctx.exception))


class TokenCliTest(unittest.TestCase):
    def test_add_prints_a_remote_table_and_its_matching_digest(self):
        rc, out, _ = run_cli(
            "agent-token", "add", "lindev", "--url", "https://m:1/agent"
        )
        self.assertEqual(rc, 0)
        token = re.search(r'token = "(\S+)"', out)
        line = re.search(r'lindev = "(sha256:[0-9a-f]{64})"', out)
        assert token is not None and line is not None, out
        self.assertEqual(ingest.digest(token.group(1)), line.group(1))
        self.assertIn('url = "https://m:1/agent"', out)

    def test_add_refuses_a_non_host_name(self):
        rc, _, err = run_cli("agent-token", "add", "../etc")
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
