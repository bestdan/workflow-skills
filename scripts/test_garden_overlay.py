"""Hermetic tests for garden-overlay.py, the `garden.importance_overlay` resolver.

Loaded via importlib since the asset's filename is hyphenated. Every path is
built under a temporary directory; HOME is passed in, never read from the
developer's machine.
"""

import importlib.util
import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "garden-overlay.py"

_spec = importlib.util.spec_from_file_location("garden_overlay", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
overlay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(overlay)


class ResolveTest(unittest.TestCase):
    def test_each_form(self) -> None:
        home, root = "/h/me", "/r/repo"
        for value, want in (
            ("/abs/overlay.md", "/abs/overlay.md"),
            ("$HOME/o.md", "/h/me/o.md"),
            ("${HOME}/o.md", "/h/me/o.md"),
            ("~/o.md", "/h/me/o.md"),
            ("dev_docs/o.md", "/r/repo/dev_docs/o.md"),
            ("../shared/o.md", "/r/shared/o.md"),
            ("./o.md", "/r/repo/o.md"),
        ):
            with self.subTest(value=value):
                self.assertEqual(overlay.resolve(value, root, home), want)

    def test_only_home_is_expanded(self) -> None:
        # `$HOMEDIR` is not `$HOME`, and no other variable is read.
        self.assertEqual(
            overlay.resolve("$HOMEDIR/o.md", "/r", "/h"), "/r/$HOMEDIR/o.md"
        )
        self.assertEqual(overlay.resolve("$USER/o.md", "/r", "/h"), "/r/$USER/o.md")

    def test_empty_and_unset_home_refused(self) -> None:
        with self.assertRaises(ValueError):
            overlay.resolve("  ", "/r", "/h")
        with self.assertRaises(ValueError):
            overlay.resolve("$HOME/o.md", "/r", None)


class MainTest(unittest.TestCase):
    def run_main(self, *argv: str, home: str = "/nonexistent"):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": home}):
            with redirect_stdout(out), redirect_stderr(err):
                rc = overlay.main(list(argv))
        return rc, out.getvalue().strip(), err.getvalue()

    def test_found_missing_and_bad_root(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            os.makedirs(repo)
            Path(d, "o.md").write_text("# overlay\n")
            for value in ("../o.md", os.path.join(d, "o.md"), "$HOME/o.md"):
                with self.subTest(value=value):
                    rc, out, _ = self.run_main("--value", value, "--root", repo, home=d)
                    self.assertEqual((rc, out), (0, os.path.join(d, "o.md")))
            rc, out, err = self.run_main("--value", "nope.md", "--root", repo)
            self.assertEqual((rc, out), (3, os.path.join(repo, "nope.md")))
            self.assertIn("is not a file", err)
            rc, _, _ = self.run_main("--value", "o.md", "--root", "relative")
            self.assertEqual(rc, 2)


class DocsTest(unittest.TestCase):
    """The doc halves of #1563, which no other check reads."""

    def test_garden_stop_messages_carry_no_bare_issue_ref(self) -> None:
        # A consumer repo reads a bare `#1521` as its own issue.
        text = (ROOT / "commands" / "garden-backlog.md").read_text()
        quoted = re.findall(r'\*\*stop\*\* with: "(.*?)"', text, re.S)
        self.assertTrue(quoted)
        for q in quoted:
            self.assertNotRegex(q, r"(?<![\w/])#\d+")

    def test_task_config_shows_the_nested_garden_form(self) -> None:
        text = (ROOT / "commands" / "task-config.md").read_text()
        self.assertRegex(text, r"\n# garden:[^\n]*\n#   importance_overlay: ")


if __name__ == "__main__":
    unittest.main()
