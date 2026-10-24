#!/usr/bin/env python3
"""Hermetic tests for the dashboard's LaunchAgent install (server_install.py).

Covers the stable launcher's installPath resolution against fixture
installed_plugins.json files (run for real, under the real uv), the plist and
launcher install renders, the install step order and its failure handling,
uninstall keeping the DB, config and a shared launcher, the status and /doctor
line, and `sync-now` against a real server on an ephemeral port.

launchctl is never run: every command goes through a fake runner, and every
path sits under a temp HOME. Needs Python 3.11 and uv: run via
scripts/test-task-dashboard-install.sh.
"""

import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

# mypy checks this file without scripts/task-dashboard on its path; the
# modules themselves are type-checked in their own group (scripts/typecheck.sh).
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import server_install as si  # type: ignore[import-not-found]  # noqa: E402
import sync  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
UV = shutil.which("uv")

# A dashboard.py stand-in: prints which copy ran and with what arguments.
FAKE_DASHBOARD = (
    'import json, sys\nprint(json.dumps({"file": __file__, "argv": sys.argv[1:]}))\n'
)


def make_install(root: Path, name: str) -> Path:
    """A fake plugin install directory holding scripts/task-dashboard/dashboard.py."""
    d = root / name
    (d / "scripts" / "task-dashboard").mkdir(parents=True)
    (d / "scripts" / "task-dashboard" / "dashboard.py").write_text(FAKE_DASHBOARD)
    return d


def record(path, scope="user", **extra):
    return {"scope": scope, "installPath": str(path), "version": "1.0.0", **extra}


class Home(unittest.TestCase):
    """A temp HOME and a config with GitHub off and an executable fake acli."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = config.deep_merge(config.DEFAULTS, server_harness.USER_CONFIG)
        self.cfg["jira"]["acli_path"] = str(PKG / "dev" / "fake-acli")
        self.tools = si.Tools(
            uv="/opt/uv/bin/uv", acli=self.cfg["jira"]["acli_path"], gh=""
        )


@unittest.skipUnless(UV, "uv is not on PATH")
class LauncherResolutionTest(unittest.TestCase):
    """The rendered launcher, run for real, against fixture manifests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.launcher = self.root / "task-dashboard-launch"
        self.launcher.write_text(si.render_launcher(str(UV)))
        self.launcher.chmod(0o755)
        self.manifest = self.root / "installed_plugins.json"

    def write_manifest(self, plugins):
        self.manifest.write_text(json.dumps({"version": 2, "plugins": plugins}))

    def launch(self, *args):
        env = {
            "HOME": str(self.root),
            "PATH": os.environ.get("PATH", ""),
            "TASK_DASHBOARD_PLUGINS_JSON": str(self.manifest),
        }
        return subprocess.run(
            [str(self.launcher), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    def ran(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_picks_the_workflow_skills_user_entry_over_others(self):
        user = make_install(self.root, "ws-2.0.0")
        project = make_install(self.root, "ws-1.0.0")
        other = make_install(self.root, "other")
        self.write_manifest(
            {
                "agent-guidance@agent-guidance": [record(other)],
                # Project scopes come first in the real file; the user scope must win.
                "workflow-skills@workflow-skills": [
                    record(project, scope="project", projectPath="/x"),
                    record(user),
                ],
            }
        )
        out = self.ran(self.launch("serve"))
        self.assertEqual(
            Path(out["file"]).resolve(),
            (user / "scripts/task-dashboard/dashboard.py").resolve(),
        )
        self.assertEqual(out["argv"], ["serve"])

    def test_skips_an_entry_whose_directory_is_gone(self):
        live = make_install(self.root, "ws-live")
        self.write_manifest(
            {
                "workflow-skills@workflow-skills": [
                    record(self.root / "ws-removed"),
                    record(live, scope="project", projectPath="/x"),
                ]
            }
        )
        out = self.ran(self.launch("status", "--json"))
        self.assertTrue(
            out["file"].startswith(str(live.resolve()))
            or out["file"].startswith(str(live))
        )
        self.assertEqual(out["argv"], ["status", "--json"])

    def test_a_plugin_update_is_picked_up_on_the_next_start(self):
        old = make_install(self.root, "ws-1")
        new = make_install(self.root, "ws-2")
        self.write_manifest({"workflow-skills@workflow-skills": [record(old)]})
        self.assertIn("ws-1", self.ran(self.launch("serve"))["file"])
        self.write_manifest({"workflow-skills@workflow-skills": [record(new)]})
        self.assertIn("ws-2", self.ran(self.launch("serve"))["file"])

    def test_errors_clearly_when_the_entry_is_absent(self):
        self.write_manifest({"agent-guidance@agent-guidance": [record(self.root)]})
        proc = self.launch("serve")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("workflow-skills is not installed", proc.stderr)
        self.assertIn(str(self.manifest), proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_errors_clearly_when_no_entry_has_the_dashboard(self):
        self.write_manifest(
            {"workflow-skills@workflow-skills": [record(self.root / "gone")]}
        )
        proc = self.launch("serve")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("has scripts/task-dashboard/dashboard.py", proc.stderr)

    def test_errors_clearly_when_the_manifest_is_missing(self):
        proc = self.launch("serve")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not found; is Claude Code installed?", proc.stderr)


class ResolveToolsTest(unittest.TestCase):
    def cfg(self, acli="acli", gh=True):
        cfg = config.deep_merge(config.DEFAULTS, server_harness.USER_CONFIG)
        cfg["jira"]["acli_path"] = acli
        cfg["github"]["enabled"] = gh
        return cfg

    def test_resolves_bare_names_to_absolute_paths(self):
        fake = str(PKG / "dev" / "fake-acli")
        found = {"uv": "/bin/sh", "acli": fake, "gh": "/bin/ls"}
        tools = si.resolve_tools(self.cfg(), found.get)
        self.assertEqual((tools.uv, tools.acli, tools.gh), ("/bin/sh", fake, "/bin/ls"))

    def test_missing_uv_acli_or_gh_is_refused_by_name(self):
        fake = str(PKG / "dev" / "fake-acli")
        with mock.patch.dict(os.environ):
            os.environ.pop("UV", None)
            with self.assertRaisesRegex(si.InstallError, "uv is not on PATH"):
                si.resolve_tools(self.cfg(), {"acli": fake}.get)
        with self.assertRaisesRegex(si.InstallError, "jira.acli_path 'acli'"):
            si.resolve_tools(self.cfg(), {"uv": "/bin/sh"}.get)
        with self.assertRaisesRegex(si.InstallError, "gh is not on PATH"):
            si.resolve_tools(self.cfg(acli=fake), {"uv": "/bin/sh"}.get)

    def test_gh_is_not_needed_when_github_is_off(self):
        fake = str(PKG / "dev" / "fake-acli")
        tools = si.resolve_tools(self.cfg(acli=fake, gh=False), {"uv": "/bin/sh"}.get)
        self.assertEqual(tools.gh, "")

    def test_a_versioned_mise_install_becomes_its_shim(self):
        with tempfile.TemporaryDirectory() as d:
            mise = Path(d) / "mise"
            real = mise / "installs" / "uv" / "0.9.1" / "bin" / "uv"
            real.parent.mkdir(parents=True)
            real.write_text("")
            shim = mise / "shims" / "uv"
            shim.parent.mkdir(parents=True)
            shim.write_text("")
            self.assertEqual(si.stable_tool(str(real)), str(shim))
            self.assertEqual(si.stable_tool("/usr/bin/true"), "/usr/bin/true")


class PlanTest(Home):
    def test_launcher_carries_the_absolute_uv_and_is_executable(self):
        p = si.plan(self.cfg, home=self.home, tools=self.tools, uid=501)
        launcher = {path: (text, mode) for path, text, mode in p.files}[
            si.launcher_path(self.home)
        ]
        self.assertIn("UV=/opt/uv/bin/uv\n", launcher[0])
        self.assertNotIn("@UV@", launcher[0])
        self.assertEqual(launcher[1], 0o755)

    def test_plist_runs_the_launcher_and_keeps_alive(self):
        p = si.plan(
            self.cfg,
            home=self.home,
            tools=self.tools,
            uid=501,
            overrides={"TASK_DASHBOARD_PLUGINS_JSON": "/tmp/copy.json"},
        )
        text = {path: t for path, t, _ in p.files}[si.plist_path(self.home)]
        doc = plistlib.loads(text.encode())
        self.assertEqual(doc["Label"], "com.bestdan.task-dashboard")
        self.assertEqual(
            doc["ProgramArguments"], [str(si.launcher_path(self.home)), "serve"]
        )
        self.assertIs(doc["RunAtLoad"], True)
        self.assertIs(doc["KeepAlive"], True)
        logs = self.home / ".local/state/task-dashboard"
        self.assertEqual(Path(doc["StandardOutPath"]).parent, logs)
        self.assertEqual(Path(doc["StandardErrorPath"]).parent, logs)
        env = doc["EnvironmentVariables"]
        self.assertEqual(env["HOME"], str(self.home))
        self.assertTrue(env["PATH"].startswith("/opt/uv/bin:" + str(PKG / "dev")))
        self.assertEqual(env["TASK_DASHBOARD_PLUGINS_JSON"], "/tmp/copy.json")
        self.assertNotIn(str(ROOT), doc["ProgramArguments"][0])

    def test_steps_warm_and_sync_then_load(self):
        p = si.plan(self.cfg, home=self.home, tools=self.tools, uid=501)
        self.assertEqual(p.uid, 501)
        argvs = [s.argv for s in p.steps]
        self.assertEqual(argvs[0][:2], ["/usr/bin/env", "-i"])
        self.assertEqual(argvs[0][-2:], [str(si.launcher_path(self.home)), "init-db"])
        self.assertEqual(argvs[1][-3:], ["sync", "--mode", "full"])
        self.assertEqual(
            argvs[2],
            ["launchctl", "bootstrap", "gui/501", str(si.plist_path(self.home))],
        )
        self.assertIn(f"HOME={self.home}", argvs[0])

    def test_install_boots_out_before_writing_any_file(self):
        seen = []

        def runner(argv, timeout):
            seen.append((argv[:2], si.plist_path(self.home).exists()))
            if argv[:2] == ["launchctl", "print"]:
                return 113, "", ""
            return 0, "", ""

        p = si.plan(self.cfg, home=self.home, tools=self.tools, uid=501)
        self.assertEqual(si.execute(p, runner=runner, log=lambda _: None), 0)
        self.assertEqual(seen[0], (["launchctl", "bootout"], False))
        self.assertEqual(seen[1], (["launchctl", "print"], False))
        self.assertIn(
            "launchctl bootout gui/501/com.bestdan.task-dashboard", si.describe(p)
        )

    def test_a_relative_uv_is_refused(self):
        self.tools.uv = "uv"
        with self.assertRaisesRegex(si.InstallError, "absolute"):
            si.plan(self.cfg, home=self.home, tools=self.tools, uid=501)

    def test_notes_say_the_url_is_local_only(self):
        p = si.plan(self.cfg, home=self.home, tools=self.tools, uid=501)
        self.assertIn(
            "http://127.0.0.1:8765/ (localhost: opens only on this machine)", p.notes[0]
        )


class FakeRunner:
    """Records argv; answers by the first matching (needle, rc, out, err)."""

    def __init__(self, answers=()):
        self.calls = []
        self.answers = list(answers)

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        for needle, rc, out, err in self.answers:
            if needle in argv:
                return rc, out, err
        if argv[:2] == ["launchctl", "print"]:
            return 113, "", 'Could not find service "x"'  # not loaded
        return 0, "", ""


class ExecuteTest(Home):
    def run_plan(self, runner):
        logs: list[str] = []
        self.sleeps: list[float] = []
        p = si.plan(self.cfg, home=self.home, tools=self.tools, uid=501)
        rc = si.execute(p, runner=runner, log=logs.append, sleep=self.sleeps.append)
        return rc, "\n".join(logs)

    def assert_undone(self, log):
        self.assertFalse(si.plist_path(self.home).exists(), log)
        self.assertFalse(si.launcher_path(self.home).exists(), log)
        self.assertIn("install undone", log)
        self.assertNotIn("previous install", log)

    def test_success_writes_files_and_loads(self):
        runner = FakeRunner([("bootout", 3, "", "Boot-out failed: 3: No such process")])
        rc, log = self.run_plan(runner)
        self.assertEqual(rc, 0, log)
        self.assertTrue(si.plist_path(self.home).exists())
        self.assertTrue(os.access(si.launcher_path(self.home), os.X_OK))
        self.assertTrue((self.home / ".local/state/task-dashboard").is_dir())
        self.assertEqual(runner.calls[-1][:2], ["launchctl", "bootstrap"])
        self.assertNotIn("WARNING", log)

    def test_a_failed_launcher_start_undoes_the_install(self):
        runner = FakeRunner([("init-db", 1, "", "workflow-skills is not installed")])
        rc, log = self.run_plan(runner)
        self.assertEqual(rc, 1)
        self.assertIn("workflow-skills is not installed", log)
        self.assertFalse(any("bootstrap" in c for c in runner.calls))
        self.assert_undone(log)

    def test_a_failed_first_sync_undoes_the_install(self):
        runner = FakeRunner(
            [("full", 3, "sync full: auth_failed", "run acli jira auth login")]
        )
        rc, log = self.run_plan(runner)
        self.assertEqual(rc, 1)
        self.assertIn("FAILED (3): first full sync", log)
        self.assertIn("acli jira auth login", log)
        self.assertFalse(any("bootstrap" in c for c in runner.calls))
        self.assert_undone(log)

    def test_a_failed_bootstrap_undoes_the_install(self):
        runner = FakeRunner(
            [("bootstrap", 5, "", "Bootstrap failed: 5: Input/output error")]
        )
        rc, log = self.run_plan(runner)
        self.assertEqual(rc, 1)
        self.assertIn("Input/output error", log)
        self.assert_undone(log)

    def test_a_failed_install_keeps_a_launcher_another_agent_runs(self):
        collector = self.home / "Library/LaunchAgents/c.plist"
        collector.parent.mkdir(parents=True)
        collector.write_bytes(
            plistlib.dumps(
                {
                    "Label": "c",
                    "ProgramArguments": [str(si.launcher_path(self.home)), "collect"],
                }
            )
        )
        rc, log = self.run_plan(FakeRunner([("init-db", 1, "", "boom")]))
        self.assertEqual(rc, 1)
        self.assertFalse(si.plist_path(self.home).exists())
        self.assertTrue(si.launcher_path(self.home).exists())
        self.assertIn("still run by", log)

    def test_a_failed_reinstall_says_the_previous_install_is_gone(self):
        si.plist_path(self.home).parent.mkdir(parents=True)
        si.plist_path(self.home).write_text("<plist/>")  # an earlier install
        rc, log = self.run_plan(FakeRunner([("full", 3, "", "auth")]))
        self.assertEqual(rc, 1)
        self.assertIn("install undone, and the previous install with it", log)
        self.assertFalse(si.plist_path(self.home).exists())

    def raising_runner(self, needle, exc):
        def runner(argv, timeout):
            if needle in argv:
                raise exc
            return FakeRunner()(argv, timeout)

        return runner

    def test_an_interrupted_first_sync_undoes_and_reraises(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_plan(self.raising_runner("full", KeyboardInterrupt()))
        self.assertFalse(si.plist_path(self.home).exists())
        self.assertFalse(si.launcher_path(self.home).exists())

    def test_a_failed_file_write_undoes_and_reraises(self):
        real = si._write
        calls = []

        def write(path, text, mode):
            calls.append(path)
            if path == si.plist_path(self.home):
                raise OSError("disk full")
            real(path, text, mode)

        with mock.patch.object(si, "_write", write), self.assertRaises(OSError):
            self.run_plan(FakeRunner())
        self.assertEqual(calls[0], si.launcher_path(self.home))
        self.assertFalse(si.launcher_path(self.home).exists())

    def test_an_interrupt_once_bootstrap_started_keeps_the_files(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_plan(self.raising_runner("bootstrap", KeyboardInterrupt()))
        self.assertTrue(si.plist_path(self.home).exists())
        self.assertTrue(si.launcher_path(self.home).exists())

    def test_bootout_waits_for_launchd_to_drop_the_service(self):
        prints = iter([(0, LOADED, ""), (113, "", "Could not find service")])

        def runner(argv, timeout):
            if argv[:2] == ["launchctl", "print"]:
                return next(prints)
            return 0, "", ""

        rc, log = self.run_plan(runner)
        self.assertEqual(rc, 0, log)
        self.assertEqual(self.sleeps, [si.BOOTOUT_POLL_S])
        self.assertTrue(si.plist_path(self.home).exists())

    def test_bootout_leaving_the_agent_loaded_stops_before_writing(self):
        runner = FakeRunner(
            [
                ("bootout", 5, "", "Boot-out failed: 5: Input/output error"),
                ("print", 0, LOADED, ""),
            ]
        )
        rc, log = self.run_plan(runner)
        self.assertEqual(rc, 1)
        self.assertIn("still loaded after bootout (exit 5: Boot-out failed", log)
        self.assertFalse(si.plist_path(self.home).exists())
        self.assertFalse(si.launcher_path(self.home).exists())
        # bootout, then one print plus a re-check every poll for the whole wait.
        polls = int(si.BOOTOUT_WAIT_S / si.BOOTOUT_POLL_S)
        self.assertEqual(len(runner.calls), 2 + polls)
        self.assertEqual(self.sleeps, [si.BOOTOUT_POLL_S] * polls)

    def test_an_unreadable_launchd_state_stops_before_writing(self):
        runner = FakeRunner(
            [("bootout", 1, "", "Operation not permitted"), ("print", 1, "", "denied")]
        )
        rc, log = self.run_plan(runner)
        self.assertEqual(rc, 1)
        self.assertIn("launchctl print exited 1: denied", log)
        self.assertFalse(si.plist_path(self.home).exists())

    def install_files(self):
        p = si.plan(self.cfg, home=self.home, tools=self.tools, uid=501)
        si.execute(p, runner=FakeRunner(), log=lambda _: None)
        self.db_file = self.home / ".local/state/task-dashboard/dashboard.db"
        self.db_file.write_text("db")
        self.config_file = self.home / ".config/task-dashboard/config.toml"
        self.config_file.parent.mkdir(parents=True)
        self.config_file.write_text("cfg")

    def uninstall(self, runner):
        logs: list[str] = []
        rc = si.uninstall(
            home=self.home,
            uid=501,
            db_file=self.db_file,
            config_file=self.config_file,
            runner=runner,
            log=logs.append,
            sleep=lambda _: None,
        )
        return rc, "\n".join(logs)

    def test_waits_for_launchd_to_drop_the_service(self):
        self.install_files()
        prints = iter([(0, LOADED, ""), (113, "", "")])

        def runner(argv, timeout):
            return next(prints) if argv[:2] == ["launchctl", "print"] else (0, "", "")

        rc, log = self.uninstall(runner)
        self.assertEqual(rc, 0, log)
        self.assertFalse(si.plist_path(self.home).exists())

    def test_removes_plist_and_launcher_and_keeps_data(self):
        self.install_files()
        runner = FakeRunner([("print", 113, "", "Could not find service")])
        rc, log = self.uninstall(runner)
        self.assertEqual(rc, 0, log)
        self.assertEqual(
            runner.calls[0],
            ["launchctl", "bootout", "gui/501/com.bestdan.task-dashboard"],
        )
        self.assertFalse(si.plist_path(self.home).exists())
        self.assertFalse(si.launcher_path(self.home).exists())
        self.assertTrue(self.db_file.exists())
        self.assertTrue(self.config_file.exists())
        self.assertIn(
            f"kept the DB ({self.db_file}) and the config ({self.config_file})", log
        )

    def test_keeps_a_launcher_another_agent_still_runs(self):
        self.install_files()
        collector = (
            self.home
            / "Library/LaunchAgents/com.bestdan.task-dashboard.collector.plist"
        )
        collector.write_bytes(
            plistlib.dumps(
                {
                    "Label": "c",
                    "ProgramArguments": [str(si.launcher_path(self.home)), "collect"],
                }
            )
        )
        rc, log = self.uninstall(FakeRunner([("print", 113, "", "")]))
        self.assertEqual(rc, 0)
        self.assertFalse(si.plist_path(self.home).exists())
        self.assertTrue(si.launcher_path(self.home).exists())
        self.assertIn("still run by", log)
        self.assertIn(collector.name, log)

    def test_still_loaded_removes_nothing(self):
        self.install_files()
        rc, log = self.uninstall(FakeRunner([("print", 0, "state = running", "")]))
        self.assertEqual(rc, 1)
        self.assertTrue(si.plist_path(self.home).exists())
        self.assertTrue(si.launcher_path(self.home).exists())

    def test_an_unreadable_launchd_state_removes_nothing(self):
        self.install_files()
        rc, log = self.uninstall(FakeRunner([("print", 1, "", "Bad request.")]))
        self.assertEqual(rc, 1)
        self.assertIn("launchctl print exited 1", log)
        self.assertTrue(si.plist_path(self.home).exists())
        self.assertTrue(si.launcher_path(self.home).exists())

    def test_nothing_installed_is_fine(self):
        self.db_file = self.home / "none.db"
        self.config_file = self.home / "none.toml"
        rc, _ = self.uninstall(FakeRunner([("print", 113, "", "")]))
        self.assertEqual(rc, 0)


LOADED = "gui/501/com.bestdan.task-dashboard = {\n\tstate = running\n\tpid = 4242\n}\n"


class StatusTest(Home):
    def setUp(self):
        super().setUp()
        self.db_file = self.home / "dashboard.db"
        si.plist_path(self.home).parent.mkdir(parents=True)
        si.plist_path(self.home).write_text("<plist/>")

    def add_run(self, outcome, finished, error=None):
        conn = db.connect(self.db_file)
        try:
            server_harness.add_run(conn, outcome, finished, error)
        finally:
            conn.close()

    def status(self, runner, cfg="default"):
        return si.status(
            home=self.home,
            uid=501,
            cfg=self.cfg if cfg == "default" else cfg,
            config_error="config file not found: x",
            db_file=self.db_file,
            now=NOW,
            runner=runner,
        )

    def test_not_installed_without_the_plist(self):
        si.plist_path(self.home).unlink()
        runner = FakeRunner()
        st = self.status(runner)
        self.assertFalse(st.installed)
        self.assertEqual(runner.calls, [])

    def test_loaded_and_fresh_is_healthy(self):
        self.add_run("ok", NOW - timedelta(minutes=4))
        st = self.status(FakeRunner([("print", 0, LOADED, "")]))
        self.assertTrue(st.healthy(), st.problems)
        self.assertEqual((st.state, st.pid), ("running", "4242"))
        self.assertEqual(st.url, "http://127.0.0.1:8765/")
        self.assertEqual(
            si.doctor_line(st),
            "PASS  task-dashboard — loaded, pid 4242, last sync 4m ago",
        )
        self.assertIn("opens only on this machine", si.render_status(st))

    def test_stale_past_three_intervals_warns(self):
        self.add_run("ok", NOW - timedelta(minutes=16))
        self.add_run("failed", NOW - timedelta(minutes=1), error="acli exploded")
        st = self.status(FakeRunner([("print", 0, LOADED, "")]))
        self.assertFalse(st.healthy())
        line = si.doctor_line(st)
        self.assertTrue(
            line.startswith(
                "WARN  task-dashboard — last successful sync 16m ago (stale past 15m)"
            ),
            line,
        )
        self.assertIn("last run failed: acli exploded", line)

    def test_just_inside_three_intervals_passes(self):
        self.add_run("ok", NOW - timedelta(minutes=14))
        self.assertTrue(self.status(FakeRunner([("print", 0, LOADED, "")])).healthy())

    def test_auth_failure_shows_the_recorded_fix(self):
        for error, fix in (
            ("401 (fix: run `acli jira auth login`)", "acli jira auth login"),
            ("bad credentials (fix: run `gh auth login`)", "gh auth login"),
        ):
            with self.subTest(fix=fix):
                self.add_run("auth_failed", NOW - timedelta(minutes=1), error=error)
                line = si.doctor_line(
                    self.status(FakeRunner([("print", 0, LOADED, "")]))
                )
                self.assertIn("never", line)
                self.assertIn(f"last run auth_failed: {error}", line)
                if fix == "gh auth login":
                    self.assertNotIn("acli", line)

    def test_not_loaded_warns_with_the_load_command(self):
        self.add_run("ok", NOW - timedelta(minutes=1))
        st = self.status(FakeRunner([("print", 113, "", "Could not find service")]))
        self.assertIn("launchctl bootstrap gui/501", si.doctor_line(st))

    def test_an_unreadable_launchd_state_is_not_reported_as_unloaded(self):
        self.add_run("ok", NOW - timedelta(minutes=1))
        line = si.doctor_line(self.status(FakeRunner([("print", 1, "", "denied")])))
        self.assertIn(
            "cannot read the agent's state: launchctl print exited 1: denied", line
        )
        self.assertNotIn("not loaded", line)

    def test_no_db_and_bad_config_warn(self):
        st = self.status(FakeRunner([("print", 0, LOADED, "")]))
        self.assertIn("no DB at", si.doctor_line(st))
        st = self.status(FakeRunner([("print", 0, LOADED, "")]), cfg=None)
        self.assertIn("config unreadable: config file not found", si.doctor_line(st))

    def test_status_never_creates_the_db(self):
        self.status(FakeRunner([("print", 0, LOADED, "")]))
        self.assertFalse(self.db_file.exists())


class SyncNowTest(unittest.TestCase):
    """sync-now against a real server whose sync records a run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_file = Path(self.tmp.name) / "dashboard.db"
        self.outcome = "ok"
        self.modes: list[str] = []

        self.release = threading.Event()
        self.release.set()
        self.started = threading.Event()

        def fake_sync(mode):
            # Like sync.run: the row is inserted unfinished when the run
            # starts and finished when it ends.
            self.modes.append(mode)
            conn = sqlite3.connect(self.db_file)
            try:
                cur = conn.execute(
                    "INSERT INTO sync_runs (kind, started_at) VALUES (?, ?)",
                    (mode, datetime.now(timezone.utc).strftime(server_harness.TS)),
                )
                conn.commit()
                self.started.set()
                assert self.release.wait(10), "fake run never released"
                conn.execute(
                    "UPDATE sync_runs SET finished_at = ?, outcome = ? WHERE id = ?",
                    (
                        datetime.now(timezone.utc).strftime(server_harness.TS),
                        self.outcome,
                        cur.lastrowid,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        cfg = server_harness.make_config()
        self.httpd, self.syncer = server.build(
            cfg, self.db_file, 0, fake_sync, run_at_start=False
        )
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.syncer.start()
        self.addCleanup(self.syncer.stop)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.release.set)  # runs first: unblock before stop

    def sync_now(self, mode="incremental", port=None, wait_s=10.0):
        logs: list[str] = []
        rc = si.sync_now(
            port=port or self.port,
            db_file=self.db_file,
            mode=mode,
            wait_s=wait_s,
            sleep=lambda _: None,
            log=logs.append,
        )
        return rc, "\n".join(logs)

    def test_runs_a_sync_through_the_guarded_post(self):
        rc, log = self.sync_now("reconcile")
        self.assertEqual(rc, 0, log)
        self.assertEqual(self.modes, ["reconcile"])
        self.assertIn("sync reconcile: ok", log)

    def test_a_failed_run_exits_1(self):
        self.outcome = "failed"
        rc, log = self.sync_now()
        self.assertEqual(rc, 1)
        self.assertIn("incremental: failed", log)

    def test_joining_a_run_already_in_flight_reports_that_run(self):
        self.release.clear()
        self.syncer.request("incremental")
        self.assertTrue(self.started.wait(10))
        logs: list[str] = []

        def log(msg):
            logs.append(msg)
            if msg.endswith("sync requested"):  # the POST joined the run
                self.release.set()

        rc = si.sync_now(
            port=self.port,
            db_file=self.db_file,
            mode="incremental",
            wait_s=10,
            sleep=lambda _: None,
            log=log,
        )
        self.assertEqual(rc, 0, logs)
        self.assertEqual(self.modes, ["incremental"])
        self.assertIn("sync incremental: ok", "\n".join(logs))

    def test_a_fleet_run_after_the_jira_run_does_not_hide_it(self):
        def jira_then_fleet(mode):
            self.modes.append(mode)
            conn = sqlite3.connect(self.db_file)
            try:
                now = datetime.now(timezone.utc)
                server_harness.add_run(conn, "ok", now, kind=mode)
                server_harness.add_run(
                    conn, "auth_failed", now, error="fix: run `gh auth login`",
                    kind=sync.FLEET_KIND,
                )  # fmt: skip
            finally:
                conn.close()

        self.syncer._sync_fn = jira_then_fleet
        rc, log = self.sync_now()
        self.assertEqual(rc, 0, log)
        self.assertIn("sync incremental: ok", log)
        self.assertIn(f"sync {sync.FLEET_KIND}: auth_failed", log)
        self.assertIn("gh auth login", log)

    def test_a_run_outlasting_the_wait_exits_3(self):
        self.release.clear()
        rc, log = self.sync_now(wait_s=0)
        self.assertEqual(rc, 3)
        self.assertIn("carries on in the server", log)

    def test_unreachable_server_exits_1(self):
        with socket_port() as free:
            rc, log = self.sync_now(port=free)
        self.assertEqual(rc, 1)
        self.assertIn("cannot reach the dashboard", log)

    def test_no_token_exits_1(self):
        logs: list[str] = []
        rc = si.sync_now(
            port=self.port,
            db_file=Path(self.tmp.name) / "missing.db",
            mode="incremental",
            wait_s=1,
            log=logs.append,
        )
        self.assertEqual(rc, 1)
        self.assertIn("no server token", logs[0])


class socket_port:
    """A port nothing listens on: bound, then released, on entry."""

    def __enter__(self):
        import socket

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def __exit__(self, *exc):
        return False


class CliTest(unittest.TestCase):
    """dashboard.py wiring that needs no launchd."""

    def run_cli(self, *args, env=None):
        full = {**os.environ, **(env or {})}
        return subprocess.run(
            [sys.executable, str(PKG / "dashboard.py"), *args],
            capture_output=True,
            text=True,
            env=full,
            timeout=60,
        )

    def test_status_doctor_is_silent_without_a_plist(self):
        with tempfile.TemporaryDirectory() as d:
            proc = self.run_cli(
                "status",
                "--doctor",
                "--config",
                str(Path(d) / "none.toml"),
                env={"HOME": d},
            )
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_status_json_and_doctor_are_exclusive(self):
        proc = self.run_cli("status", "--json", "--doctor")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not allowed with argument", proc.stderr)

    def test_open_is_an_alias_of_url(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.toml"
            cfg.write_text(
                '[jira]\nsite = "x.atlassian.net"\nboard_ids = []\n'
                '[jira.fields]\nsprint = "a"\nflagged = "b"\n[server]\nport = 9003\n'
            )
            proc = self.run_cli("open", "--config", str(cfg))
        self.assertEqual(proc.stdout.strip(), "http://127.0.0.1:9003/")

    def test_url_prints_the_loopback_url(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.toml"
            cfg.write_text(
                '[jira]\nsite = "x.atlassian.net"\nboard_ids = []\n'
                '[jira.fields]\nsprint = "a"\nflagged = "b"\n[server]\nport = 9001\n'
            )
            proc = self.run_cli("url", "--config", str(cfg))
        self.assertEqual(proc.stdout.strip(), "http://127.0.0.1:9001/")


class InstalledPathsTest(unittest.TestCase):
    """Commands after install read the paths install wrote into the plist."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.cfg_file = self.home / "elsewhere" / "config.toml"
        self.cfg_file.parent.mkdir()
        self.cfg_file.write_text(
            '[jira]\nsite = "x.atlassian.net"\nboard_ids = []\n'
            '[jira.fields]\nsprint = "a"\nflagged = "b"\n[server]\nport = 9004\n'
        )
        self.db_file = self.home / "elsewhere" / "dash.db"
        self.manifest = self.home / "elsewhere" / "plugins.json"
        si.plist_path(self.home).parent.mkdir(parents=True)
        si.plist_path(self.home).write_text(
            si.render_plist(
                self.home,
                si.agent_env(
                    self.home,
                    "/usr/bin",
                    {
                        "TASK_DASHBOARD_CONFIG": str(self.cfg_file),
                        "TASK_DASHBOARD_DB": str(self.db_file),
                        "TASK_DASHBOARD_PLUGINS_JSON": str(self.manifest),
                    },
                ),
            )
        )
        env = {
            k: v for k, v in os.environ.items() if not k.startswith("TASK_DASHBOARD_")
        }
        env["HOME"] = str(self.home)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def args(self, *argv):
        return dashboard.build_parser().parse_args(list(argv))

    def test_reads_the_plist_paths_when_nothing_else_names_them(self):
        self.assertEqual(
            si.installed_paths(self.home),
            {
                "TASK_DASHBOARD_CONFIG": str(self.cfg_file),
                "TASK_DASHBOARD_DB": str(self.db_file),
                "TASK_DASHBOARD_PLUGINS_JSON": str(self.manifest),
            },
        )
        self.assertEqual(
            dashboard._agent_paths(self.args("status")),
            (str(self.cfg_file), str(self.db_file)),
        )

    def test_a_reinstall_without_flags_keeps_the_plist_paths(self):
        out = dashboard._overrides(self.args("install"))
        self.assertEqual(out["TASK_DASHBOARD_PLUGINS_JSON"], str(self.manifest))
        self.assertEqual(out["TASK_DASHBOARD_CONFIG"], str(self.cfg_file))

    def test_a_flagless_reinstall_plans_against_the_plists_config(self):
        tools = si.Tools(uv="/opt/uv/bin/uv", acli="/bin/sh", gh="")
        out: list[str] = []
        with (
            mock.patch.object(sys, "platform", "darwin"),
            mock.patch.object(si, "resolve_tools", return_value=tools),
            mock.patch(
                "builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))
            ),
        ):
            rc = dashboard.main(["install", "--dry-run"])
        self.assertEqual(rc, 0, out)
        self.assertIn("http://127.0.0.1:9004/", out[0])

    def test_a_flag_or_env_var_still_wins(self):
        other = str((self.home / "other.toml").resolve())
        self.assertEqual(
            dashboard._agent_paths(self.args("status", "--config", other))[0], other
        )
        os.environ["TASK_DASHBOARD_DB"] = str(self.home / "env.db")
        self.assertEqual(
            dashboard._agent_paths(self.args("status"))[1],
            str((self.home / "env.db").resolve()),
        )

    def test_status_and_url_use_the_agents_config(self):
        buf = []
        with mock.patch(
            "builtins.print", lambda *a, **k: buf.append(" ".join(map(str, a)))
        ):
            self.assertEqual(dashboard.main(["url"]), 0)
        self.assertEqual(buf, ["http://127.0.0.1:9004/"])
        st = si.status(
            home=self.home,
            uid=501,
            cfg=config.load(dashboard._agent_paths(self.args("status"))[0]),
            config_error="",
            db_file=Path(dashboard._agent_paths(self.args("status"))[1]),
            now=NOW,
            runner=FakeRunner([("print", 0, LOADED, "")]),
        )
        self.assertEqual(st.url, "http://127.0.0.1:9004/")
        self.assertIn(f"no DB at {self.db_file}", si.doctor_line(st))

    def install_cli(self, path_dirs):
        """`dashboard.main(["install"])` with PATH limited to `path_dirs`."""
        err: list[str] = []
        env = {"PATH": os.pathsep.join(str(d) for d in path_dirs)}
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(sys, "platform", "darwin"),
            mock.patch(
                "builtins.print", lambda *a, **k: err.append(" ".join(map(str, a)))
            ),
        ):
            os.environ.pop("UV", None)
            rc = dashboard.main(["install"])
        return rc, "\n".join(err)

    def test_collector_only_refuses_server_only_flags(self):
        for flags in (["--db", "x.db"], ["--plugins-json", "p.json"]):
            with self.subTest(flags=flags):
                err: list[str] = []
                with mock.patch(
                    "builtins.print", lambda *a, **k: err.append(" ".join(map(str, a)))
                ):
                    rc = dashboard.main(["install", "--collector-only", *flags])
                self.assertEqual(rc, 2)
                self.assertIn(f"{flags[0]} apply only to the server install", err[0])

    def test_install_refuses_off_macos_with_exit_2(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(dashboard.main(["install"]), 2)
        self.assertFalse(si.launcher_path(self.home).exists())

    def test_install_refuses_without_uv_with_exit_2(self):
        empty = self.home / "empty-bin"
        empty.mkdir()
        rc, err = self.install_cli([empty])
        self.assertEqual(rc, 2)
        self.assertIn("uv is not on PATH", err)
        self.assertFalse(si.launcher_path(self.home).exists())

    def test_install_refuses_without_acli_with_exit_2(self):
        bindir = self.home / "uv-only"
        bindir.mkdir()
        (bindir / "uv").write_text("#!/bin/sh\n")
        (bindir / "uv").chmod(0o755)
        rc, err = self.install_cli([bindir])
        self.assertEqual(rc, 2)
        self.assertIn("jira.acli_path 'acli' does not resolve", err)
        self.assertFalse(si.launcher_path(self.home).exists())

    def test_uninstall_reports_the_agents_paths_and_refuses_off_macos(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(dashboard.cmd_uninstall(self.args("uninstall")), 2)
        self.assertTrue(si.plist_path(self.home).exists())
        with (
            mock.patch.object(sys, "platform", "darwin"),
            mock.patch.object(si, "uninstall", return_value=0) as fake,
        ):
            self.assertEqual(dashboard.cmd_uninstall(self.args("uninstall")), 0)
        self.assertEqual(fake.call_args.kwargs["db_file"], self.db_file)
        self.assertEqual(fake.call_args.kwargs["config_file"], self.cfg_file)


if __name__ == "__main__":
    unittest.main()
