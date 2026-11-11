#!/usr/bin/env python3
"""Hermetic tests for scripts/pr-complexity.py (stdlib unittest, no network)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "pr_complexity", HERE / "pr-complexity.py"
)
assert spec is not None and spec.loader is not None
pc = importlib.util.module_from_spec(spec)
sys.modules["pr_complexity"] = pc
spec.loader.exec_module(pc)


def file_diff(
    path: str, body: list[str], old: int, new: int, header: str | None = None
) -> str:
    head = (
        header
        or f"diff --git a/{path} b/{path}\nindex 1..2 100644\n--- a/{path}\n+++ b/{path}\n"
    )
    return head + f"@@ -1,{old} +1,{new} @@\n" + "".join(line + "\n" for line in body)


def run(argv: list[str], stdin: str = "") -> tuple[int, str]:
    out = io.StringIO()
    old_stdin = pc.sys.stdin
    pc.sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                rc = pc.main(argv)
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
    finally:
        pc.sys.stdin = old_stdin
    return rc, out.getvalue()


class ParseDiff(unittest.TestCase):
    def test_counts_lines_and_hunks(self) -> None:
        text = file_diff("src/a.py", [" keep", "-old", "+new", "+more"], 2, 3)
        [f] = pc.parse_diff(text)
        self.assertEqual(
            (f.path, f.additions, f.deletions, f.hunks), ("src/a.py", 2, 1, 1)
        )

    def test_removed_line_starting_with_dashes_is_not_a_header(self) -> None:
        text = file_diff("doc.md", ["--- a/fake", "+++ b/fake", " ctx"], 2, 2)
        [f] = pc.parse_diff(text)
        self.assertEqual((f.path, f.additions, f.deletions), ("doc.md", 1, 1))

    def test_deleted_file_takes_the_old_path(self) -> None:
        text = (
            "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n"
            "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x\n-y\n"
        )
        [f] = pc.parse_diff(text)
        self.assertEqual((f.path, f.deletions, f.hunks), ("gone.py", 2, 1))

    def test_pure_rename_and_binary_count_as_files_with_no_lines(self) -> None:
        text = (
            "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
            "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n"
        )
        files = pc.parse_diff(text)
        self.assertEqual(
            [(f.path, f.additions + f.deletions) for f in files],
            [("new.py", 0), ("img.png", 0)],
        )

    def test_hunk_header_without_counts_means_one_line(self) -> None:
        text = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
        [f] = pc.parse_diff(text)
        self.assertEqual((f.additions, f.deletions), (1, 1))

    def test_no_newline_marker_is_ignored(self) -> None:
        text = file_diff("x", ["-a", "\\ No newline at end of file", "+b"], 1, 1)
        [f] = pc.parse_diff(text)
        self.assertEqual((f.additions, f.deletions), (1, 1))


class Categories(unittest.TestCase):
    def test_paths(self) -> None:
        cases = {
            "scripts/test-foo.sh": "test",
            "scripts/test_foo.py": "test",
            "src/__tests__/a.ts": "test",
            "lib/a_test.go": "test",
            "web/a.spec.ts": "test",
            "README.md": "docs",
            "dev_docs/x.py": "docs",
            ".github/workflows/ci.yml": "config",
            "pyproject.toml": "config",
            "justfile": "config",
            "ios/Runner/AppDelegate.swift": "native",
            "android/app/build.gradle": "native",
            "src/main.py": "code",
        }
        for path, want in cases.items():
            self.assertEqual(pc.category(path), want, path)


class Score(unittest.TestCase):
    def test_matches_the_formula_and_rounds_half_up(self) -> None:
        m = pc.Measurement(
            additions=453,
            deletions=39,
            files=6,
            packages=3,
            hunks=22,
            categories=["code", "test", "docs"],
        )
        # 9.84 + 9 + 8 + 10 + 6 = 42.84
        self.assertEqual(pc.score(m), 43)
        m = pc.Measurement(
            additions=25, files=1, packages=1, hunks=0, categories=["code"]
        )
        # 0.5 + 1.5 = 2.0
        self.assertEqual(pc.score(m), 2)
        # 0.5 rounds up to 1, not to even
        m = pc.Measurement(additions=25, files=0, packages=0, hunks=0, categories=[])
        self.assertEqual(pc.score(m), 1)

    def test_every_term_is_capped_at_100(self) -> None:
        m = pc.Measurement(
            additions=10**6,
            files=500,
            packages=50,
            hunks=500,
            categories=list(pc.CATEGORY_ORDER),
        )
        self.assertEqual(pc.score(m), 40 + 20 + 20 + 10 + 10)

    def test_bands(self) -> None:
        self.assertEqual(
            [pc.band(v) for v in (0, 29, 30, 59, 60, 100)],
            ["Low", "Low", "Moderate", "Moderate", "High", "High"],
        )


class Excludes(unittest.TestCase):
    def test_defaults_drop_generated_and_lockfiles(self) -> None:
        excludes = [pc.re.compile(s) for s in pc.DEFAULT_EXCLUDES]
        for path in (
            "app/lib/a.g.dart",
            "x/__generated__/q.ts",
            "uv.lock",
            "web/package-lock.json",
            "pnpm-lock.yaml",
            "go.sum",
            "ios/Podfile.lock",
            "Cargo.lock",
            "a.min.js",
        ):
            self.assertTrue(any(p.search(path) for p in excludes), path)
        for path in ("src/lock.py", "lib/a.dart", "docs/uv.lock.md"):
            self.assertFalse(any(p.search(path) for p in excludes), path)


class Cli(unittest.TestCase):
    DIFF = file_diff("src/a.py", ["+x"] * 10, 0, 10) + file_diff(
        "uv.lock", ["+x"] * 500, 0, 500
    )

    def test_text_report_excludes_lockfile(self) -> None:
        rc, out = run([], self.DIFF)
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "### Complexity: 2 / 100 (Low)")
        self.assertEqual(
            lines[1], "Reviewable: +10 / -0 across 1 file · 1 package · 1 hunk · code"
        )
        self.assertIn("split=not-required", lines[2])
        self.assertIn("excluded=1", lines[2])

    def test_no_default_excludes_counts_it(self) -> None:
        rc, out = run(["--no-default-excludes", "--json"], self.DIFF)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["r"], 510)

    def test_threshold_flag_sets_split(self) -> None:
        rc, out = run(["--threshold", "2", "--json"], self.DIFF)
        data = json.loads(out)
        self.assertEqual((rc, data["split"], data["threshold"]), (0, "required", 2))

    def test_config_block(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".co-review.yml"
            cfg.write_text(
                "local_reviewers:\n  - codex\n"
                "complexity:\n  split_threshold: 1  # low\n  exclude:\n    - '^src/'\n    - \"x\"\n"
                "other:\n  exclude:\n    - '.*'\n"
            )
            self.assertEqual(pc.load_config(str(cfg)), (1, ["^src/", "x"]))
            rc, out = run(
                ["--config", str(cfg), "--no-default-excludes", "--json"], self.DIFF
            )
            data = json.loads(out)
            self.assertEqual(
                (rc, data["f"], data["adds"], data["split"]), (0, 1, 500, "required")
            )
            # a flag outranks the config
            rc, out = run(
                ["--config", str(cfg), "--threshold", "50", "--json"], self.DIFF
            )
            self.assertEqual(json.loads(out)["threshold"], 50)

    def test_config_list_at_the_key_indent(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".co-review.yml"
            cfg.write_text(
                "complexity:\n  exclude:\n  - '^src/'\n  - x\n  split_threshold: 5\n"
            )
            self.assertEqual(pc.load_config(str(cfg)), (5, ["^src/", "x"]))

    def test_missing_config_is_not_an_error(self) -> None:
        rc, out = run(["--config", "/nonexistent/.co-review.yml", "--json"], self.DIFF)
        self.assertEqual((rc, json.loads(out)["threshold"]), (0, pc.DEFAULT_THRESHOLD))

    def test_bad_config_threshold_is_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.yml"
            cfg.write_text("complexity:\n  split_threshold: high\n")
            self.assertEqual(run(["--config", str(cfg)], self.DIFF)[0], 2)

    def test_bad_regex_is_usage_error(self) -> None:
        self.assertEqual(run(["--exclude", "("], self.DIFF)[0], 2)

    def test_untracked_files_count_as_added(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            new = Path(d) / "new.py"
            new.write_text("a\nb\nc\n")
            rc, out = run(["--untracked", str(new), "--json"], "")
            data = json.loads(out)
            self.assertEqual((rc, data["adds"], data["f"], data["h"]), (0, 3, 1, 1))

    def test_empty_diff(self) -> None:
        rc, out = run(["--json"], "")
        data = json.loads(out)
        self.assertEqual((rc, data["score"], data["band"], data["p"]), (0, 0, "Low", 0))


if __name__ == "__main__":
    unittest.main(verbosity=1)
