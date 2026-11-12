#!/usr/bin/env python3
"""Hermetic tests for scripts/check-cache.py.

Each case builds a git repo in a temp directory, with GIT_CONFIG_GLOBAL pinned
to /dev/null, and calls the module from inside it. The environment probes are
replaced with fixed values, so no case depends on what is installed here.
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-cache.py"

_spec = importlib.util.spec_from_file_location("check_cache", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


class CheckCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self._cwd = os.getcwd()
        self._env = dict(os.environ)
        os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
        os.environ.pop("GIT_INDEX_FILE", None)
        os.chdir(self.repo)
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        (self.repo / ".gitignore").write_text("ignored/\n")
        (self.repo / "a.txt").write_text("a\n")
        git("add", "-A")
        git("commit", "-qm", "init")
        self._probes = cc.ENVIRONMENT
        self.env_value = "v1"
        cc.ENVIRONMENT = [("tool", lambda: self.env_value)]
        self.record = Path(self._tmp.name) / "record.json"

    def tearDown(self) -> None:
        cc.ENVIRONMENT = self._probes
        os.chdir(self._cwd)
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def key(self) -> str:
        k = cc.key()
        assert k is not None
        return k

    def quiet(self, fn, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with contextlib.redirect_stderr(io.StringIO()):
                result = fn(*args)
        return result, out.getvalue()

    def test_key_is_stable_on_an_unchanged_tree(self) -> None:
        self.assertEqual(self.key(), self.key())

    def test_tracked_edit_changes_the_key(self) -> None:
        before = self.key()
        (self.repo / "a.txt").write_text("b\n")
        self.assertNotEqual(before, self.key())

    def test_untracked_file_changes_the_key(self) -> None:
        before = self.key()
        (self.repo / "new.txt").write_text("x\n")
        self.assertNotEqual(before, self.key())

    def test_deleted_file_changes_the_key(self) -> None:
        before = self.key()
        (self.repo / "a.txt").unlink()
        self.assertNotEqual(before, self.key())

    def test_ignored_file_leaves_the_key_alone(self) -> None:
        before = self.key()
        (self.repo / "ignored").mkdir()
        (self.repo / "ignored" / "x").write_text("x\n")
        self.assertEqual(before, self.key())

    def test_staging_alone_leaves_the_key_alone(self) -> None:
        (self.repo / "a.txt").write_text("b\n")
        before = self.key()
        git("add", "a.txt")
        self.assertEqual(before, self.key())

    def test_same_size_rewrite_right_after_a_commit_changes_the_key(self) -> None:
        # Fingerprinting from a copy of the real index let git trust stale stat
        # data: a same-size rewrite just after a commit kept the old key in
        # about 1 run in 8. Repeat the pattern so a regression shows up.
        for i in range(30):
            (self.repo / "a.txt").write_text(f"{i % 10}\n")
            git("commit", "-qam", f"c{i}")
            before = self.key()
            (self.repo / "a.txt").write_text(f"{(i + 1) % 10}\n")
            self.assertNotEqual(before, self.key(), f"iteration {i}")

    def test_mode_change_changes_the_key(self) -> None:
        before = self.key()
        (self.repo / "a.txt").chmod(0o755)
        self.assertNotEqual(before, self.key())

    def test_real_index_is_untouched(self) -> None:
        (self.repo / "new.txt").write_text("x\n")
        self.key()
        self.assertEqual(git("diff", "--cached", "--name-only"), "")
        self.assertIn("?? new.txt", git("status", "--porcelain"))

    def test_environment_change_changes_the_key(self) -> None:
        before = self.key()
        self.env_value = "v2"
        self.assertNotEqual(before, self.key())

    def test_lookup_hits_after_store(self) -> None:
        k = self.key()
        stored, _ = self.quiet(cc.store, self.record, k)
        self.assertTrue(stored)
        hit, out = self.quiet(cc.lookup, self.record, k)
        self.assertTrue(hit)
        self.assertIn("tree unchanged", out)

    def test_lookup_misses_on_another_key(self) -> None:
        self.quiet(cc.store, self.record, self.key())
        (self.repo / "a.txt").write_text("b\n")
        hit, out = self.quiet(cc.lookup, self.record, self.key())
        self.assertFalse(hit)
        self.assertEqual(out, "")

    def test_lookup_misses_without_a_record(self) -> None:
        hit, _ = self.quiet(cc.lookup, self.record, self.key())
        self.assertFalse(hit)

    def test_lookup_misses_on_a_corrupt_record(self) -> None:
        self.record.write_text("not json")
        hit, _ = self.quiet(cc.lookup, self.record, self.key())
        self.assertFalse(hit)

    def test_store_refuses_when_the_tree_moved_during_the_run(self) -> None:
        k = self.key()
        (self.repo / "a.txt").write_text("edited mid-run\n")
        stored, _ = self.quiet(cc.store, self.record, k)
        self.assertFalse(stored)
        self.assertFalse(self.record.exists())

    def test_main_rejects_bad_usage(self) -> None:
        rc, _ = self.quiet(cc.main, ["bogus"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
