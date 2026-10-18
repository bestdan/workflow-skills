#!/usr/bin/env python3
"""Hermetic tests for scripts/build-copilot-instructions.py.

Every case but the last runs against a fixture tree under a temp directory,
with the module's ROOT/SOURCES/OUTPUTS globals repointed at it, so nothing here
depends on what AGENTS.md happens to say today. The last case is the exception
on purpose: it runs --check against the real repo, which is the drift gate
itself, and fails if the committed .github/ files have fallen behind the
guidance they were generated from.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-copilot-instructions.py"

_spec = importlib.util.spec_from_file_location("build_copilot_instructions", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


def block(block_id: str, body: str) -> str:
    return f"<!-- copilot:begin id={block_id} -->\n\n{body}\n\n<!-- copilot:end -->\n"


class Fixture:
    """A temp repo with one source file and one output spec, wired into the module."""

    def __init__(self, source_text: str, blocks=("only",), apply_to=None):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "SOURCE.md").write_text(source_text)
        self.patches = [
            mock.patch.object(build, "ROOT", self.root),
            mock.patch.object(build, "SOURCES", ("SOURCE.md",)),
            mock.patch.object(
                build,
                "OUTPUTS",
                (
                    build.Output(
                        path="out.md", apply_to=apply_to, title="T", blocks=blocks
                    ),
                ),
            ),
        ]

    def __enter__(self):
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()
        self.dir.cleanup()

    def out(self) -> str:
        return (self.root / "out.md").read_text()


class BuildCopilotInstructions(unittest.TestCase):
    def run_main(self, argv):
        with mock.patch("sys.argv", ["build-copilot-instructions.py", *argv]):
            return build.main()

    def test_writes_the_marked_span_and_nothing_else(self):
        source = "before\n\n" + block("only", "- a rule\n- another rule") + "\nafter\n"
        with Fixture(source) as fx:
            self.assertEqual(self.run_main([]), 0)
            out = fx.out()
        self.assertIn("- a rule", out)
        self.assertNotIn("before", out)
        self.assertNotIn("after", out)

    def test_path_scoped_file_leads_with_applyto_frontmatter(self):
        # A *.instructions.md file without applyTo is ignored by Copilot outright,
        # and frontmatter is only frontmatter on line 1.
        with Fixture(block("only", "- a rule"), apply_to="skills/**") as fx:
            self.run_main([])
            out = fx.out()
        self.assertTrue(out.startswith('---\napplyTo: "skills/**"\n---\n'), out[:60])

    def test_markdown_links_are_flattened_to_their_text(self):
        with Fixture(block("only", "- see [the docs](dev_docs/x.md) for more")) as fx:
            self.run_main([])
            out = fx.out()
        self.assertIn("- see the docs for more", out)

    def test_a_bare_url_in_a_marked_block_fails(self):
        # Copilot will not follow it, so a URL that survives into an output file
        # is an instruction that silently does nothing.
        with Fixture(block("only", "- follow https://example.com/style")):
            with self.assertRaises(SystemExit):
                self.run_main([])

    def test_autolink_is_dropped_rather_than_left_bare(self):
        with Fixture(block("only", "- install mise <https://mise.jdx.dev/>")) as fx:
            self.assertEqual(self.run_main([]), 0)
            self.assertNotIn("mise.jdx.dev", fx.out())

    def test_unclosed_block_fails(self):
        with Fixture("<!-- copilot:begin id=only -->\n\n- a rule\n"):
            with self.assertRaises(SystemExit):
                self.run_main([])

    def test_duplicate_block_id_fails(self):
        with Fixture(block("only", "- first") + block("only", "- second")):
            with self.assertRaises(SystemExit):
                self.run_main([])

    def test_referencing_a_block_that_does_not_exist_fails(self):
        with Fixture(block("only", "- a rule"), blocks=("only", "missing")):
            with self.assertRaises(SystemExit):
                self.run_main([])

    def test_a_marked_but_unused_block_fails(self):
        # Otherwise a rule looks single-sourced while reaching no reviewer.
        with Fixture(block("only", "- a rule") + block("orphan", "- unrouted")):
            with self.assertRaises(SystemExit):
                self.run_main([])

    def test_check_reports_drift_and_leaves_the_file_alone(self):
        with Fixture(block("only", "- a rule")) as fx:
            self.run_main([])
            fresh = fx.out()
            (fx.root / "SOURCE.md").write_text(block("only", "- a revised rule"))
            self.assertEqual(self.run_main(["--check"]), 1)
            self.assertEqual(fx.out(), fresh)
            self.assertEqual(self.run_main([]), 0)
            self.assertEqual(self.run_main(["--check"]), 0)

    def test_check_fails_when_the_output_file_is_missing(self):
        with Fixture(block("only", "- a rule")):
            self.assertEqual(self.run_main(["--check"]), 1)

    def test_line_ceiling_is_enforced(self):
        long_body = "\n".join(f"- rule {i}" for i in range(build.MAX_LINES + 10))
        with Fixture(block("only", long_body)):
            with self.assertRaises(SystemExit):
                self.run_main([])

    def test_committed_files_match_this_repos_guidance(self):
        self.assertEqual(self.run_main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
