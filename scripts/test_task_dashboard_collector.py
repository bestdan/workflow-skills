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
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
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
        self.ingest = FakeIngest()  # for the cleanup's close()

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
        self.assertEqual(cfg["config_path"], self.path)

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
            'host = "a.b"\n': "collector.host",
            "[server]\nport = 1\n": "no \\[collector\\]",
        }
        for text, pattern in cases.items():
            with self.subTest(pattern=pattern):
                self.write(text)
                with self.assertRaisesRegex(config.ConfigError, pattern):
                    config.load_collector(self.path)


FAKE_HOME = {"darwin": Path("/Users/tester"), "linux": Path("/home/tester")}
FAKE_PATH = "/opt/fake/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


class TestUnits(unittest.TestCase):
    def check_golden(self, name, text):
        golden = GOLDEN / name
        if os.environ.get("UPDATE_GOLDEN"):
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_text(text)
        self.assertEqual(text, golden.read_text(), f"{name} drifted from its golden")

    def test_plist_matches_golden(self):
        text = collector_install.render_plist(FAKE_HOME["darwin"], FAKE_PATH)
        self.check_golden(f"{collector_install.LABEL}.plist", text)
        doc = plistlib.loads(text.encode())
        self.assertTrue(doc["RunAtLoad"])
        self.assertEqual(doc["StartInterval"], 300)
        self.assertEqual(
            doc["ProgramArguments"],
            ["/Users/tester/.local/bin/task-dashboard-launch", "collect"],
        )

    def test_systemd_units_match_golden(self):
        unit = collector_install.UNIT
        self.check_golden(
            f"{unit}.service",
            collector_install.render_service(FAKE_HOME["linux"], FAKE_PATH),
        )
        self.check_golden(f"{unit}.timer", collector_install.render_timer())

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
    def __init__(self, linger="yes"):
        self.calls = []
        self.linger = linger

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if argv[0] == "loginctl":
            return 0, self.linger + "\n", ""
        if argv[:2] == ["launchctl", "bootout"]:
            return 3, "", "Boot-out failed: 3: No such process"
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
        path = self.home / "off.toml"
        path.write_text(f'[collector]\ningest_url = "http://m/i"\ntoken = "{TOKEN}"\n')
        os.chmod(path, 0o600)
        cfg = config.load_collector(path)
        p = collector_install.plan(cfg, home=self.home, platform="linux",
                                   uv="/u/uv", path_env=FAKE_PATH, uid=1, user="t")  # fmt: skip
        self.assertTrue(any("hook is off" in n for n in p.notes))
        self.assertFalse(any("hook is off" in n for n in self.plan("linux").notes))

    def test_install_without_collector_only_is_refused(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(dashboard.main(["install"]), 2)
        self.assertIn("#1093", err.getvalue())


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
