#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/task-config-resolve.py.

Stubs the module's run_git() seam so nothing shells out to git. The cases that
matter are the three sources the committed layer can come from, and above all
that they are three and not two: a config tracked here but deleted in the
fetched base must yield an EMPTY layer, not the still-checked-out file, whose
stale `branch_prefix` is the #748 split this whole change exists to close.
"""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "task-config-resolve.py"

_spec = importlib.util.spec_from_file_location("task_config_resolve", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
task_config_resolve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(task_config_resolve)

TRACKED_IN_BASE = "handler: gh-issue\ngh-issue:\n  branch_prefix: fetched/\n"


class FakeGit:
    """git answers for the three cases, plus the case where git itself fails.

    `tracked` drives `ls-files --error-unmatch` (0 tracked / 1 untracked),
    `in_base` drives `cat-file -e <base>:<path>`, and `broken` makes every call
    fail with a code that is neither 0 nor 1 — the "cannot answer" case, which
    must never be read as untracked.
    """

    def __init__(self, tracked=True, in_base=True, broken=False):
        self.tracked = tracked
        self.in_base = in_base
        self.broken = broken
        self.calls = []

    def run_git(self, args, root=None):
        self.calls.append(args)
        if self.broken:
            return 128, "", "fatal: not a git repository"
        if args[:2] == ["ls-files", "--error-unmatch"]:
            return (0, "", "") if self.tracked else (1, "", "error: pathspec")
        if args[:2] == ["cat-file", "-e"]:
            return (
                (0, "", "")
                if self.in_base
                else (128, "", "fatal: path ... does not exist")
            )
        if args[0] == "show":
            return 0, TRACKED_IN_BASE, ""
        raise AssertionError(f"unexpected git call: {args}")


class CommittedLayerTests(unittest.TestCase):
    def setUp(self):
        self._real = task_config_resolve.run_git
        self.addCleanup(self._restore)

    def _restore(self):
        task_config_resolve.run_git = self._real

    def _layer(self, git, root=None):
        task_config_resolve.run_git = git.run_git
        return task_config_resolve.committed_layer("main", root=root)

    def test_tracked_and_present_reads_the_fetched_base(self):
        text, source = self._layer(FakeGit(tracked=True, in_base=True))
        self.assertEqual(source, "base")
        self.assertIn("branch_prefix: fetched/", text)

    def test_tracked_but_deleted_in_base_is_empty_not_the_worktree(self):
        # The whole point: the old file is still checked out, so a fallback to
        # the working tree here would hand back a stale prefix.
        text, source = self._layer(FakeGit(tracked=True, in_base=False))
        self.assertEqual(source, "absent")
        self.assertEqual(text, "")

    def test_untracked_reads_the_working_tree(self):
        with contextlib.ExitStack() as stack:
            tmp = stack.enter_context(_temp_repo("handler: gh-issue\n"))
            text, source = self._layer(FakeGit(tracked=False), root=tmp)
        self.assertEqual(source, "worktree")
        self.assertEqual(text, "handler: gh-issue\n")

    def test_untracked_and_missing_is_absent_not_an_error(self):
        with contextlib.ExitStack() as stack:
            tmp = stack.enter_context(_temp_repo(None))
            text, source = self._layer(FakeGit(tracked=False), root=tmp)
        self.assertEqual(source, "absent")
        self.assertEqual(text, "")

    def test_git_failing_is_not_read_as_untracked(self):
        # Reading a git failure as "untracked" would send a TRACKED config's
        # read to the working tree, which is the stale-prefix case.
        with self.assertRaises(task_config_resolve.GitUnavailable):
            self._layer(FakeGit(broken=True))

    def test_tracked_ness_is_decided_by_the_index_not_by_base_presence(self):
        git = FakeGit(tracked=True, in_base=True)
        self._layer(git)
        self.assertEqual(git.calls[0][:2], ["ls-files", "--error-unmatch"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self._real = task_config_resolve.run_git
        self.addCleanup(self._restore)

    def _restore(self):
        task_config_resolve.run_git = self._real

    def _run(self, git, root=None):
        task_config_resolve.run_git = git.run_git
        argv = ["committed", "--base", "main"]
        if root is not None:
            argv += ["--root", str(root)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = task_config_resolve.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_prints_the_layer_and_names_its_source(self):
        code, out, err = self._run(FakeGit(tracked=True, in_base=True))
        self.assertEqual(code, 0)
        self.assertIn("branch_prefix: fetched/", out)
        self.assertIn("source: base", err)

    def test_an_empty_layer_still_exits_0(self):
        code, out, err = self._run(FakeGit(tracked=True, in_base=False))
        self.assertEqual(
            code, 0, "an empty committed layer is a decision, not a failure"
        )
        self.assertEqual(out, "")
        self.assertIn("source: absent", err)

    def test_git_unavailable_is_exit_4_with_nothing_on_stdout(self):
        code, out, err = self._run(FakeGit(broken=True))
        self.assertEqual(code, 4)
        self.assertEqual(out, "", "a caller must not overlay a guessed layer")
        self.assertIn("cannot resolve", err)


@contextlib.contextmanager
def _temp_repo(config_text):
    """A directory that may or may not contain dev_docs/tasks/.task-config.yml."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tasks = Path(tmp) / "dev_docs" / "tasks"
        tasks.mkdir(parents=True)
        if config_text is not None:
            (tasks / ".task-config.yml").write_text(config_text)
        yield tmp


if __name__ == "__main__":
    unittest.main()
