#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/snapshot.py.

The fixture is built per test in a temp directory: a fake repo with two linked
worktrees, a handoff note, a session-state hook log and a history file, all
synthetic. git runs for real (with the global config pinned to /dev/null);
tmux and branch-remove.sh go through a fake runner, so nothing reaches the
network. Needs Python 3.11: run via scripts/test-task-dashboard-snapshot.sh.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    os.environ.pop(var, None)

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"td_{name}", PKG / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


snap = _load("snapshot")

SECRET = "PLANTED-PROMPT-TEXT-7f3a"
NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)


def git(cwd, *args):
    subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    )


class FakeRunner:
    """Real git; canned tmux and branch-remove.sh answers. Records every argv."""

    def __init__(self, tmux=(1, "", "no server running"), landed=None):
        self.tmux = tmux
        self.landed = landed or {}
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if argv[0] == "git":
            return snap.run(argv, timeout)
        if argv[0] == "tmux":
            return self.tmux
        if argv[0] == "bash" and argv[1].endswith("branch-remove.sh"):
            branch = argv[argv.index("--check") + 1]
            return self.landed.get(branch, (3, "", " .. no merged PR for it\n"))
        raise AssertionError(f"unexpected call: {argv}")


def hook_line(session, event, ts, **extra):
    rec = {"v": 1, "ts": ts, "event": event, "session_id": session,
           "cwd": "/w", "branch": "main", **extra}  # fmt: skip
    return json.dumps(rec) + "\n"


def history_line(ts_ms, session, text):
    return json.dumps({
        "display": text, "pastedContents": {"1": {"content": SECRET}},
        "timestamp": ts_ms, "project": "/w", "sessionId": session,
    }) + "\n"  # fmt: skip


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        self.state = self.tmp / "state"
        self.history = self.tmp / "history.jsonl"
        origin = self.tmp / "origin.git"
        repo = self.src / "fakerepo"
        repo.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
        )
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "t")
        (repo / "f.txt").write_text("one\n")
        git(repo, "add", "f.txt")
        git(repo, "commit", "-q", "-m", "init")
        git(repo, "remote", "add", "origin", str(origin))
        git(repo, "push", "-q", "origin", "main")
        wt = self.src / "worktrees" / "fakerepo"
        self.wt_a, self.wt_b = wt / "alpha", wt / "beta"
        git(
            repo, "worktree", "add", "-q", "-b", "bestdan/alpha", str(self.wt_a), "main"
        )
        git(repo, "worktree", "add", "-q", "-b", "bestdan/beta", str(self.wt_b), "main")
        (self.wt_a / "f.txt").write_text("dirty\n")
        (self.wt_b / "g.txt").write_text("new\n")
        git(self.wt_b, "add", "g.txt")
        git(self.wt_b, "commit", "-q", "-m", "unpushed")
        self.repo = repo

        handoffs = self.src / "worktrees" / "fakerepo" / ".handoffs"
        handoffs.mkdir()
        (handoffs / "2026-10-23-alpha.md").write_text(
            "---\ncreated: 2026-10-23\naim: finish alpha\nrepo: fakerepo\n"
            'branch: bestdan/alpha\npr: "#12"\nexpires: 2026-10-30\n---\n\n'
            f"Body text that must not ship: {SECRET}\n"
        )

        sessions = self.state / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "sess-1.jsonl").write_text(
            hook_line("sess-1", "UserPromptSubmit", "2026-10-23T10:00:00.000Z")
            + hook_line("sess-1", "Stop", "2026-10-23T10:01:00.000Z")
            + hook_line("sess-1", "Notification", "2026-10-23T10:02:00.000Z",
                        notification="permission_prompt")
        )  # fmt: skip
        self.history.write_text(
            history_line(1792784500000, "sess-1", f"please refactor {SECRET}")
            + history_line(1792784510000, "sess-1", "yes")
        )

    def build(self, runner=None, **kw):
        return snap.build(
            src_root=self.src, state_dir=self.state, history=self.history,
            host="testhost", now=NOW, runner=runner or FakeRunner(), **kw,
        )  # fmt: skip

    def take(self, runner=None):
        """One collector run: build, then advance the cursor."""
        doc, cursor = self.build(runner)
        snap.save_cursor(self.state / snap.CURSOR_NAME, cursor)
        return doc


class TestDocument(Fixture):
    def test_validates_against_schema(self):
        # A registry directory that does not exist is an empty registry.
        doc, _ = self.build(registry_dir=self.state / "no-sessions")
        self.assertEqual(snap.validate(doc), [])
        self.assertEqual(doc["schema"], 4)
        self.assertEqual(doc["registry"], [])
        self.assertEqual(doc["scan_errors"], [])
        # No registry directory at all reads none: schema 2, which ingest
        # never reads as every session ended.
        doc, _ = self.build()
        self.assertEqual(snap.validate(doc), [])
        self.assertEqual(doc["schema"], 2)
        self.assertNotIn("registry", doc)
        self.assertEqual(doc["host"], "testhost")
        self.assertEqual(doc["taken_at"], "2026-10-23T12:00:00.000Z")

    def test_validate_rejects_an_unexpected_key(self):
        doc, _ = self.build()
        doc["history"].append(
            {"ts": "x", "session_id": None, "project": None,
             "approval_shaped": False, "text": "leak"}
        )  # fmt: skip
        self.assertIn("history[2].text: unexpected key", snap.validate(doc))
        doc.pop("tmux")
        self.assertIn("tmux: missing", snap.validate(doc))

    def test_no_prompt_or_body_text_anywhere(self):
        doc, _ = self.build()
        for value in snap.iter_values(doc):
            self.assertNotIn(SECRET, str(value))


class TestWorktrees(Fixture):
    def test_matches_git_worktree_list(self):
        doc, _ = self.build()
        out = subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "list", "--porcelain"],
            check=True, capture_output=True, text=True,
        ).stdout  # fmt: skip
        expected = [
            ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith("worktree ")
        ]
        self.assertEqual([w["path"] for w in doc["worktrees"]], expected)
        self.assertEqual(len(expected), 3)

    def test_dirty_unpushed_and_branch(self):
        doc, _ = self.build()
        by = {Path(w["path"]).name: w for w in doc["worktrees"]}
        self.assertEqual(by["fakerepo"]["branch"], "main")
        self.assertEqual(
            (by["fakerepo"]["dirty"], by["fakerepo"]["unpushed"]), (False, 0)
        )
        self.assertEqual(by["alpha"]["branch"], "bestdan/alpha")
        self.assertEqual((by["alpha"]["dirty"], by["alpha"]["unpushed"]), (True, 0))
        self.assertEqual((by["beta"]["dirty"], by["beta"]["unpushed"]), (False, 1))
        self.assertTrue(all(w["repo"] == "fakerepo" for w in doc["worktrees"]))

    def test_landed_comes_from_the_teardown_gate(self):
        runner = FakeRunner(
            landed={
                "bestdan/alpha": (0, " .. branch bestdan/alpha landed — PR #3\n", "")
            }
        )
        doc, _ = self.build(runner)
        by = {w["branch"]: w["landed"] for w in doc["worktrees"]}
        self.assertEqual(by["bestdan/alpha"]["state"], "landed")
        self.assertIn("PR #3", by["bestdan/alpha"]["reason"])
        self.assertEqual(by["bestdan/beta"]["state"], "not_landed")
        gate = [c for c in runner.calls if c[0] == "bash"]
        self.assertTrue(gate)
        for call in gate:
            self.assertEqual(call[1], str(snap.BRANCH_REMOVE))
            self.assertEqual(call[2], "--check")
            self.assertEqual(call[4], str(self.repo))

    def test_gate_failure_is_unknown_not_landed(self):
        doc, _ = self.build(FakeRunner(landed={"main": (64, "", "")}))
        main = next(w for w in doc["worktrees"] if w["branch"] == "main")
        self.assertEqual(main["landed"]["state"], "unknown")

    def test_detached_head_skips_the_gate(self):
        self.assertEqual(snap.landed(FakeRunner(), "/r", None)["state"], "unknown")

    def test_failed_worktree_list_is_a_scan_error_not_an_absence(self):
        second = self.src / "second"
        second.mkdir()
        git(second, "init", "-q", "-b", "main")
        failing = str(self.repo)

        class Failing(FakeRunner):
            def __call__(self, argv, timeout):
                if argv[:3] == ["git", "-C", failing] and "worktree" in argv:
                    self.calls.append(argv)
                    return 124, "fatal: planted output\n", "timed out"
                return super().__call__(argv, timeout)

        doc, _ = self.build(Failing())
        self.assertEqual(snap.validate(doc), [])
        self.assertEqual(
            doc["scan_errors"], [{"repo": "fakerepo", "step": "worktree_list"}]
        )
        self.assertEqual({w["repo"] for w in doc["worktrees"]}, {"second"})
        for value in snap.iter_values(doc):
            self.assertNotIn("planted", str(value))

    def test_missing_path_has_null_state(self):
        shutil.rmtree(self.wt_a)
        doc, _ = self.build()
        alpha = next(w for w in doc["worktrees"] if w["branch"] == "bestdan/alpha")
        self.assertEqual((alpha["dirty"], alpha["unpushed"]), (None, None))
        self.assertEqual(snap.validate(doc), [])


class TestHandoffsAndTmux(Fixture):
    def test_handoff_front_matter_only(self):
        doc, _ = self.build()
        [h] = doc["handoffs"]
        self.assertEqual(h["aim"], "finish alpha")
        self.assertEqual(h["branch"], "bestdan/alpha")
        self.assertEqual(h["pr"], "#12")
        self.assertEqual(h["expires"], "2026-10-30")
        self.assertEqual(h["created"], "2026-10-23")
        self.assertNotIn("repo", h)
        self.assertTrue(h["mtime"].endswith("Z"))

    def test_handoff_without_front_matter(self):
        note = self.src / "worktrees" / "fakerepo" / ".handoffs" / "bare.md"
        note.write_text(f"no front matter {SECRET}\n")
        doc, _ = self.build()
        bare = next(h for h in doc["handoffs"] if h["path"].endswith("bare.md"))
        self.assertIsNone(bare["aim"])

    def test_tmux_not_running_is_empty(self):
        doc, _ = self.build(FakeRunner(tmux=(127, "", "tmux: not found")))
        self.assertEqual(doc["tmux"], [])

    def test_tmux_sessions(self):
        out = "main\t1792784500\t1\nscratch\t1792784000\t0\n"
        doc, _ = self.build(FakeRunner(tmux=(0, out, "")))
        self.assertEqual(
            doc["tmux"],
            [{"name": "main", "created": "2026-10-23T19:41:40.000Z", "attached": True},
             {"name": "scratch", "created": "2026-10-23T19:33:20.000Z", "attached": False}],
        )  # fmt: skip


class TestCursor(Fixture):
    def test_first_run_reads_everything(self):
        doc = self.take()
        self.assertEqual([e["event"] for e in doc["sessions"]],
                         ["UserPromptSubmit", "Stop", "Notification"])  # fmt: skip
        self.assertEqual(doc["sessions"][2]["notification"], "permission_prompt")
        self.assertEqual(len(doc["history"]), 2)
        self.assertEqual(doc["history"][1], {
            "ts": "2026-10-23T19:41:50.000Z", "session_id": "sess-1",
            "project": "/w", "approval_shaped": True,
        })  # fmt: skip
        self.assertFalse(doc["history"][0]["approval_shaped"])

    def test_second_run_with_no_new_events_is_empty(self):
        self.take()
        doc = self.take()
        self.assertEqual(doc["sessions"], [])
        self.assertEqual(doc["history"], [])
        self.assertEqual(snap.validate(doc), [])

    def test_only_new_lines_after_the_cursor(self):
        self.take()
        with (self.state / "sessions" / "sess-1.jsonl").open("a") as fh:
            fh.write(
                hook_line("sess-1", "UserPromptSubmit", "2026-10-23T11:00:00.000Z")
            )
        (self.state / "sessions" / "sess-2.jsonl").write_text(
            hook_line("sess-2", "Stop", "2026-10-23T10:30:00.000Z")
        )
        with self.history.open("a") as fh:
            fh.write(history_line(1792784600000, "sess-2", "go ahead"))
        doc = self.take()
        self.assertEqual([(e["session_id"], e["ts"]) for e in doc["sessions"]],
                         [("sess-2", "2026-10-23T10:30:00.000Z"),
                          ("sess-1", "2026-10-23T11:00:00.000Z")])  # fmt: skip
        self.assertEqual([h["session_id"] for h in doc["history"]], ["sess-2"])

    def test_partial_line_waits_for_the_next_run(self):
        self.take()
        log = self.state / "sessions" / "sess-1.jsonl"
        line = hook_line("sess-1", "Stop", "2026-10-23T12:00:00.000Z")
        with log.open("a") as fh:
            fh.write(line[:20])
        self.assertEqual(self.take()["sessions"], [])
        with log.open("a") as fh:
            fh.write(line[20:])
        self.assertEqual([e["ts"] for e in self.take()["sessions"]],
                         ["2026-10-23T12:00:00.000Z"])  # fmt: skip

    def test_replaced_file_is_read_from_the_start(self):
        self.take()
        self.history.unlink()
        self.history.write_text(history_line(1792784700000, "sess-3", "ok"))
        self.assertEqual([h["session_id"] for h in self.take()["history"]], ["sess-3"])

    def test_build_alone_does_not_advance(self):
        self.build()
        doc, _ = self.build()
        self.assertEqual(len(doc["history"]), 2)

    def test_hook_lines_are_allowlisted(self):
        log = self.state / "sessions" / "sess-1.jsonl"
        with log.open("a") as fh:
            fh.write(
                hook_line("sess-1", "Stop", "2026-10-23T10:03:00.000Z", prompt=SECRET)
            )
            fh.write(json.dumps({"v": 3, "ts": "x"}) + "\nnot json\n")
            fh.write(json.dumps({"v": True, "ts": "y"}) + "\n")
        doc, _ = self.build()
        self.assertEqual(len(doc["sessions"]), 4)
        self.assertNotIn("prompt", doc["sessions"][3])

    def test_v1_and_v2_hook_lines_are_both_read(self):
        log = self.state / "sessions" / "sess-2.jsonl"
        with log.open("a") as fh:
            fh.write(hook_line("sess-2", "Stop", "2026-10-23T10:04:00.000Z"))
            fh.write(
                hook_line("sess-2", "Stop", "2026-10-23T10:05:00.000Z", v=2, asked=True)
            )
            fh.write(
                hook_line("sess-2", "Stop", "2026-10-23T10:06:00.000Z", v=2, asked=None)
            )
        doc, _ = self.build()
        mine = [e for e in doc["sessions"] if e["session_id"] == "sess-2"]
        self.assertEqual([e["v"] for e in mine], [1, 2, 2])
        self.assertNotIn("asked", mine[0])
        self.assertIs(mine[1]["asked"], True)
        self.assertIsNone(mine[2]["asked"])
        self.assertEqual(snap.validate(doc), [])

    def test_validate_checks_v_and_asked(self):
        doc, _ = self.build()
        base = {"v": 2, "ts": "2026-10-23T10:05:00.000Z", "event": "Stop"}
        for bad in (
            {"asked": "yes"},
            {"asked": 1},
            {"v": True},
            {"v": 3},
            {"v": 1, "asked": True},
            {"event": "Notification", "asked": False},
            {"event": "UserPromptSubmit", "asked": None},
        ):
            with self.subTest(bad=bad):
                doc["sessions"] = [{**base, **bad}]
                self.assertEqual(snap.validate(doc), ["sessions[0]: malformed"])
        doc["sessions"] = [
            {**base, "asked": False},
            {**base, "asked": None},
            {**base, "v": 1},
            {**base, "event": "Notification"},
        ]
        self.assertEqual(snap.validate(doc), [])


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def write(self, name, obj):
        (self.dir / name).write_text(obj if isinstance(obj, str) else json.dumps(obj))

    def test_pid_alive(self):
        self.assertTrue(snap.pid_alive(os.getpid()))
        for pid in (0, -1):  # a process group, never one process
            self.assertFalse(snap.pid_alive(pid))

    def test_unusable_files_are_skipped(self):
        self.write("1.json", "{torn")
        self.write("2.json", ["a list"])
        self.write("3.json", {"pid": 3})  # no sessionId
        self.write("4.json", {"pid": "4", "sessionId": "s"})  # pid not an int
        self.write("5.json", {"pid": True, "sessionId": "s"})
        self.assertEqual(snap.registry(self.dir, alive=lambda pid: True), [])

    def test_wrong_types_become_null(self):
        self.write("7.json", {"pid": 7, "sessionId": "s", "name": 5, "status": "",
                              "bridgeSessionId": ["x"], "updatedAt": "soon",
                              "tmux": None})  # fmt: skip
        [entry] = snap.registry(self.dir, alive=lambda pid: True)
        self.assertEqual(
            entry,
            {"session_id": "s", "bridge_session_id": None, "name": None,
             "status": None, "pid": 7, "updated_at": None, "tmux": None,
             "cwd": None},
        )  # fmt: skip

    def test_a_malformed_bridge_id_is_dropped_and_the_entry_kept(self):
        bad = ("session_01/../x", "session_01?next=1", "session_01#x", "other_01",
               "session_", " session_01")  # fmt: skip
        for i, bridge in enumerate(bad, start=10):
            self.write(f"{i}.json", {"pid": i, "sessionId": f"s{i}", "name": "n",
                                     "status": "idle", "bridgeSessionId": bridge})  # fmt: skip
        self.write("9.json", {"pid": 9, "sessionId": "s09",
                              "bridgeSessionId": "session_01Ab-c_9"})  # fmt: skip
        entries = {e["session_id"]: e for e in snap.registry(self.dir, lambda p: True)}
        self.assertEqual(entries["s09"]["bridge_session_id"], "session_01Ab-c_9")
        for i, bridge in enumerate(bad, start=10):
            entry = entries[f"s{i}"]
            self.assertIsNone(entry["bridge_session_id"], bridge)
            self.assertEqual((entry["name"], entry["status"]), ("n", "idle"), bridge)

    def test_a_dead_process_is_left_out(self):
        self.write("8.json", {"pid": 8, "sessionId": "s"})
        self.assertEqual(snap.registry(self.dir, alive=lambda pid: False), [])

    def test_an_unlistable_directory_is_a_failed_scan_sent_as_schema_2(self):
        self.write("8.json", {"pid": 8, "sessionId": "s"})
        # No directory, or a file in its place, is an empty registry.
        self.assertEqual(snap.registry(self.dir / "absent", lambda p: True), [])
        self.assertEqual(snap.registry(self.dir / "8.json", lambda p: True), [])
        real = os.listdir

        def denied(path):
            if Path(path) == self.dir:
                raise PermissionError(13, "Permission denied", str(path))
            return real(path)

        with mock.patch.object(snap.os, "listdir", side_effect=denied):
            self.assertIsNone(snap.registry(self.dir, alive=lambda pid: True))
            doc, _ = snap.build(
                src_root=self.dir / "src", state_dir=self.dir / "state",
                history=self.dir / "history.jsonl", host="h", now=NOW,
                runner=FakeRunner(), registry_dir=self.dir, alive=lambda pid: True,
            )  # fmt: skip
        self.assertEqual(doc["schema"], 2)
        self.assertNotIn("registry", doc)
        self.assertEqual(snap.validate(doc), [])

    def test_a_schema_3_document_needs_its_registry(self):
        doc = {"schema": 3, "host": "h", "taken_at": "t", "worktrees": [],
               "scan_errors": [], "handoffs": [], "tmux": [], "sessions": [],
               "history": []}  # fmt: skip
        self.assertIn("registry: missing", snap.validate(doc))
        doc["registry"] = [{"session_id": "s", "pid": 1}]
        problems = snap.validate(doc)
        self.assertIn("registry[0].bridge_session_id: missing", problems)
        doc["registry"] = []
        self.assertEqual(snap.validate(doc), [])
        doc["schema"] = 2
        self.assertIn("registry: unexpected", snap.validate(doc))

    def test_schema_4_adds_a_registry_cwd_and_downgrades_to_3(self):
        entry = {"session_id": "s", "bridge_session_id": None, "name": None,
                 "status": None, "pid": 1, "updated_at": None, "tmux": None}  # fmt: skip
        sent = dict(entry)
        doc = {"schema": 4, "host": "h", "taken_at": "t", "worktrees": [],
               "scan_errors": [], "handoffs": [], "tmux": [], "sessions": [],
               "history": [], "registry": [sent]}  # fmt: skip
        self.assertIn("registry[0].cwd: missing", snap.validate(doc))
        sent["cwd"] = "/h/src/x"
        self.assertEqual(snap.validate(doc), [])
        three = snap.downgrade(doc)
        assert three is not None
        self.assertEqual((three["schema"], three["registry"]), (3, [entry]))
        self.assertEqual(snap.validate(three), [])
        self.assertEqual(sent["cwd"], "/h/src/x")  # not mutated
        self.assertIn("registry[0].cwd: unexpected key",
                      snap.validate({**doc, "schema": 3}))  # fmt: skip
        self.assertIsNone(snap.downgrade(three))

    def test_the_registry_copies_the_process_cwd(self):
        self.write("9.json", {"pid": 9, "sessionId": "s", "cwd": "/h/src/x"})
        [entry] = snap.registry(self.dir, alive=lambda pid: True)
        self.assertEqual(entry["cwd"], "/h/src/x")


class TestApprovalShaped(unittest.TestCase):
    def test_assents(self):
        for text in (
            "y",
            "Yes",
            "yes.",
            "go ahead!",
            "LGTM",
            "ok, do it",
            "yes please",
        ):
            self.assertTrue(snap.approval_shaped(text), text)

    def test_not_assents(self):
        for text in (
            "",
            "no",
            "yes but rename the function first",
            "why?",
            "fix the tests",
        ):
            self.assertFalse(snap.approval_shaped(text), text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
