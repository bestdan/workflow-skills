#!/usr/bin/env python3
"""Hermetic tests for scripts/session-state-hook.py.

Runs the hook as the harness does — a subprocess fed a JSON payload on stdin —
with HOME pointed at a temp dir, so the opt-in file and the state directory
are both fixtures and nothing touches the real ones.
"""

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "session-state-hook.py"

_spec = importlib.util.spec_from_file_location("session_state_hook", HOOK)
assert _spec is not None and _spec.loader is not None, f"cannot load {HOOK}"
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

SESSION = "0b6c2f9e-1d2a-4c55-9f3e-abcdef012345"
PROMPT = "SECRET PROMPT TEXT do not log this"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class HookCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.state = self.home / ".local" / "state" / "fleet" / "sessions"
        self.repo = self.home / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n")

    def opt_in(self, body="[collector]\nenabled = true\n"):
        cfg = self.home / ".config" / "task-dashboard" / "config.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(body)

    def run_hook(self, payload):
        env = {k: v for k, v in os.environ.items() if k != hook.CONFIG_ENV}
        env["HOME"] = str(self.home)
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        return proc

    def payload(self, event, **extra):
        p = {
            "session_id": SESSION,
            "transcript_path": "/x/t.jsonl",
            "cwd": str(self.repo),
            "hook_event_name": event,
        }
        p.update(extra)
        return p

    def lines(self):
        return (self.state / f"{SESSION}.jsonl").read_text().splitlines()


class NoOptInTests(HookCase):
    def test_no_config_writes_nothing(self):
        proc = self.run_hook(self.payload("UserPromptSubmit", prompt=PROMPT))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout + proc.stderr, "")
        self.assertFalse((self.home / ".local").exists())

    def test_config_without_opt_in_writes_nothing(self):
        for body in (
            "[server]\nport = 1\n",
            "[collector]\nenabled = false\n",
            '[collector]\nenabled = "true"\n',
            "[other]\nenabled = true\n[collector]\n",
            "# [collector]\n# enabled = true\n",
        ):
            with self.subTest(body=body):
                self.opt_in(body)
                proc = self.run_hook(self.payload("Stop"))
                self.assertEqual(proc.returncode, 0)
                self.assertFalse((self.home / ".local").exists())


class OptInTests(HookCase):
    def setUp(self):
        super().setUp()
        self.opt_in(
            "[jira]\nsite = 'x'\n\n[collector]  # fleet\nenabled = true  # yes\n"
        )

    def test_one_well_formed_line_per_event(self):
        events = [
            self.payload("UserPromptSubmit", prompt=PROMPT),
            self.payload("Stop", stop_hook_active=False),
            self.payload(
                "Notification",
                message="Claude needs your permission to use Bash",
                notification_type="permission_prompt",
            ),
        ]
        for i, payload in enumerate(events, start=1):
            proc = self.run_hook(payload)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(len(self.lines()), i)

        records = [json.loads(line) for line in self.lines()]
        self.assertEqual(
            [r["event"] for r in records], ["UserPromptSubmit", "Stop", "Notification"]
        )
        for r in records:
            self.assertEqual(r["v"], 1)
            self.assertRegex(r["ts"], TS_RE)
            self.assertEqual(r["session_id"], SESSION)
            self.assertEqual(r["cwd"], str(self.repo))
            self.assertEqual(r["branch"], "feature/x")
        self.assertNotIn("notification", records[0])
        self.assertEqual(records[2]["notification"], "permission_prompt")

        raw = (self.state / f"{SESSION}.jsonl").read_text()
        self.assertNotIn("SECRET", raw)
        self.assertNotIn("permission to use", raw)

    def test_unwritable_state_dir_exits_zero(self):
        self.state.parent.mkdir(parents=True)
        self.state.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(self.state.parent.chmod, stat.S_IRWXU)
        proc = self.run_hook(self.payload("Stop"))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout + proc.stderr, "")
        self.assertFalse(self.state.exists())

    def test_bad_input_exits_zero_and_writes_nothing(self):
        for payload in (
            "not json",
            self.payload("PreToolUse"),
            self.payload("Stop", session_id="../escape"),
            self.payload("Stop", session_id=".hidden"),
        ):
            with self.subTest(payload=payload):
                env = dict(os.environ, HOME=str(self.home))
                env.pop(hook.CONFIG_ENV, None)
                proc = subprocess.run(
                    [sys.executable, str(HOOK)],
                    input=payload if isinstance(payload, str) else json.dumps(payload),
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=10,
                )
                self.assertEqual(proc.returncode, 0)
                self.assertFalse(self.state.exists())

    def test_fast(self):
        start = time.monotonic()
        proc = self.run_hook(self.payload("Stop"))
        elapsed = time.monotonic() - start
        self.assertEqual(proc.returncode, 0)
        # Generous ceiling for a loaded CI box; locally it is ~20ms.
        self.assertLess(elapsed, 1.0)


class UnitTests(unittest.TestCase):
    def test_notification_class_fallback(self):
        self.assertEqual(
            hook.notification_class(
                {"message": "Claude needs your permission to use Bash"}
            ),
            "permission_prompt",
        )
        self.assertEqual(
            hook.notification_class({"message": "Claude is waiting for your input"}),
            "idle_prompt",
        )
        self.assertEqual(hook.notification_class({"message": "hi"}), "other")
        self.assertEqual(
            hook.notification_class(
                {"notification_type": "idle_prompt", "message": "x"}
            ),
            "idle_prompt",
        )
        self.assertEqual(hook.notification_class({"notification_type": "a b"}), "other")

    def test_branch_linked_worktree_and_detached(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        gitdir = tmp / "main" / ".git" / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        (gitdir / "HEAD").write_text("ref: refs/heads/bestdan/wt\n")
        wt = tmp / "wt"
        (wt / "sub").mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {gitdir}\n")
        self.assertEqual(hook.git_branch(str(wt / "sub")), "bestdan/wt")
        (gitdir / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
        self.assertIsNone(hook.git_branch(str(wt)))

    def test_build_line_ts_format(self):
        now = datetime(2026, 10, 23, 14, 5, 9, 123456, tzinfo=timezone.utc)
        line = hook.build_line(
            {"hook_event_name": "Stop", "session_id": "abc", "cwd": None}, now
        )
        self.assertIsNotNone(line)
        rec = json.loads(line)
        self.assertEqual(rec["ts"], "2026-10-23T14:05:09.123Z")
        self.assertIsNone(rec["branch"])


if __name__ == "__main__":
    unittest.main()
