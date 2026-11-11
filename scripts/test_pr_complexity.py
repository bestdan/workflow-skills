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

    def test_quoted_path_with_spaces(self) -> None:
        header = (
            'diff --git "a/my dir/x y.py" "b/my dir/x y.py"\n'
            'index 1..2 100644\n--- "a/my dir/x y.py"\n+++ "b/my dir/x y.py"\n'
        )
        [f] = pc.parse_diff(file_diff("", ["+a"], 0, 1, header=header))
        self.assertEqual((f.path, f.additions), ("my dir/x y.py", 1))

    def test_form_feed_inside_a_line_does_not_split_it(self) -> None:
        text = file_diff("x.c", [" a", "+b\x0c++ y", "+c", " a2"], 2, 4)
        [f] = pc.parse_diff(text)
        self.assertEqual((f.path, f.additions, f.deletions), ("x.c", 2, 0))


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

    def test_size_bands_at_the_default_cuts(self) -> None:
        self.assertEqual(
            [pc.size(v, pc.DEFAULT_CUTS)[0] for v in (0, 29, 30, 59, 60, 79, 80, 100)],
            [
                "small",
                "small",
                "medium",
                "medium",
                "large",
                "large",
                "very-large",
                "very-large",
            ],
        )

    def test_size_bands_follow_custom_cuts(self) -> None:
        cuts = {"medium": 10, "large": 20, "very_large": 90}
        self.assertEqual(
            [pc.size(v, cuts)[0] for v in (9, 10, 20, 89, 90)],
            ["small", "medium", "large", "large", "very-large"],
        )

    def test_line_trigger_makes_a_saturated_diff_very_large(self) -> None:
        cuts = pc.DEFAULT_CUTS
        self.assertEqual(pc.size(79, cuts, 2999, 3000), ("large", "score"))
        self.assertEqual(pc.size(79, cuts, 3000, 3000), ("very-large", "lines"))
        self.assertEqual(pc.size(85, cuts, 5000, 3000), ("very-large", "score"))
        self.assertEqual(pc.size(10, cuts, 10**5, 0), ("small", "score"))


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

    def test_local_review_generated_paths_are_excluded(self) -> None:
        excludes = [pc.re.compile(s) for s in pc.DEFAULT_EXCLUDES]
        for path in (
            "api/x.pb.go",
            "py/x_pb2.py",
            "py/x_pb2_grpc.py",
            "src/api.generated.ts",
            "pkg/generated/client.go",
            "vendor/lib/a.go",
            "src/__snapshots__/a.test.js.snap",
            "dist/app.js.map",
        ):
            self.assertTrue(any(p.search(path) for p in excludes), path)


class GeneratedRules(unittest.TestCase):
    EXCLUDES = [pc.re.compile(s) for s in pc.DEFAULT_EXCLUDES]

    def measure(self, diff: str, **kw: object) -> object:
        return pc.measure(pc.parse_diff(diff), self.EXCLUDES, **kw)  # type: ignore[arg-type]

    def test_header_on_a_new_file_excludes_it(self) -> None:
        diff = file_diff(
            "api/x.go",
            [
                "+// Copyright",
                "+",
                "+// Code generated by protoc. DO NOT EDIT.",
                "+package api",
            ],
            0,
            4,
        )
        self.assertEqual(self.measure(diff).files, 0)  # type: ignore[attr-defined]

    def test_unchanged_header_shown_as_context_excludes_it(self) -> None:
        diff = file_diff(
            "api/x.go",
            [
                " // Code generated by protoc. DO NOT EDIT.",
                " package api",
                "+var a = 1",
            ],
            2,
            3,
        )
        self.assertEqual(self.measure(diff).files, 0)  # type: ignore[attr-defined]

    def test_marker_past_line_five_does_not_exclude(self) -> None:
        diff = (
            "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
            "@@ -100,2 +100,3 @@\n x = 1\n+# the token is Generated by the auth server\n y = 2\n"
        )
        m = self.measure(diff)
        self.assertEqual((m.files, m.additions), (1, 1))  # type: ignore[attr-defined]

    def test_marker_is_case_sensitive(self) -> None:
        diff = file_diff(
            "docs/r.md", ["+# code generated reports, generated by hand"], 0, 1
        )
        self.assertEqual(self.measure(diff).files, 1)  # type: ignore[attr-defined]

    def test_headers_off_keeps_the_file(self) -> None:
        diff = file_diff("api/x.go", ["+// DO NOT EDIT"], 0, 1)
        self.assertEqual(self.measure(diff, headers=False).files, 1)  # type: ignore[attr-defined]

    def test_untracked_file_header_excludes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "gen.ts")
            p.write_text("// @generated\nexport const a = 1\n")
            m = pc.measure([pc.untracked_file(str(p))], self.EXCLUDES)
            self.assertEqual(m.files, 0)

    def test_linguist_generated_via_root(self) -> None:
        import os
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            env = dict(
                os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1"
            )
            subprocess.run(["git", "init", "-q", tmp], check=True, env=env)
            Path(tmp, ".gitattributes").write_text("gen/** linguist-generated\n")
            self.assertEqual(
                pc.generated_attr(["gen/c.ts", "src/x.ts"], tmp), {"gen/c.ts"}
            )

    def test_non_git_root_marks_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pc.generated_attr(["gen/c.ts"], tmp), set())


class Cli(unittest.TestCase):
    DIFF = file_diff("src/a.py", ["+x"] * 10, 0, 10) + file_diff(
        "uv.lock", ["+x"] * 500, 0, 500
    )

    def test_text_report_excludes_lockfile(self) -> None:
        rc, out = run([], self.DIFF)
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "### Complexity: 2 / 100 (small)")
        self.assertEqual(
            lines[1], "Reviewable: +10 / -0 across 1 file · 1 package · 1 hunk · code"
        )
        self.assertIn("size=small by=score cuts=30/60/80 lines_at=3000", lines[2])
        self.assertIn("excluded=1", lines[2])

    def test_no_default_excludes_counts_it(self) -> None:
        rc, out = run(["--no-default-excludes", "--json"], self.DIFF)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["r"], 510)

    def test_cut_flags_set_the_size(self) -> None:
        rc, out = run(
            ["--medium-at", "1", "--large-at", "2", "--very-large-at", "3", "--json"],
            self.DIFF,
        )
        data = json.loads(out)
        self.assertEqual(
            (rc, data["size"], data["cuts"]),
            (0, "large", {"medium": 1, "large": 2, "very_large": 3}),
        )

    def test_cuts_that_do_not_rise_are_a_usage_error(self) -> None:
        self.assertEqual(run(["--large-at", "90"], self.DIFF)[0], 2)
        self.assertEqual(run(["--medium-at", "0"], self.DIFF)[0], 2)

    def test_config_block(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".co-review.yml"
            cfg.write_text(
                "local_reviewers:\n  - codex\n"
                "complexity:\n  medium_at: 1  # low\n  large_at: 2\n  very_large_at: 3\n"
                "  exclude:\n    - '^src/'\n    - \"x\"\n"
                "other:\n  exclude:\n    - '.*'\n"
            )
            self.assertEqual(
                pc.load_config(str(cfg)),
                ({"medium": 1, "large": 2, "very_large": 3}, ["^src/", "x"]),
            )
            rc, out = run(
                ["--config", str(cfg), "--no-default-excludes", "--json"], self.DIFF
            )
            data = json.loads(out)
            self.assertEqual(
                (rc, data["f"], data["adds"], data["size"]), (0, 1, 500, "very-large")
            )
            # a flag outranks the config
            rc, out = run(
                ["--config", str(cfg), "--very-large-at", "50", "--json"], self.DIFF
            )
            self.assertEqual(json.loads(out)["cuts"]["very_large"], 50)

    def test_config_list_at_the_key_indent(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".co-review.yml"
            cfg.write_text(
                "complexity:\n  exclude:\n  - '^src/'\n  - x\n  large_at: 5\n"
            )
            self.assertEqual(pc.load_config(str(cfg)), ({"large": 5}, ["^src/", "x"]))

    def test_hash_inside_a_quoted_exclude_is_not_a_comment(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".co-review.yml"
            cfg.write_text(
                "complexity:\n  exclude:\n    - \"foo #bar\"  # why\n    - 'a#b'\n"
            )
            self.assertEqual(pc.load_config(str(cfg)), ({}, ["foo #bar", "a#b"]))

    def test_empty_exclude_entry_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".co-review.yml"
            for item in ("''", '""'):
                cfg.write_text(f"complexity:\n  exclude:\n    - {item}\n")
                self.assertEqual(run(["--config", str(cfg)], self.DIFF)[0], 2, item)

    def test_missing_config_is_not_an_error(self) -> None:
        rc, out = run(["--config", "/nonexistent/.co-review.yml", "--json"], self.DIFF)
        self.assertEqual((rc, json.loads(out)["cuts"]), (0, pc.DEFAULT_CUTS))

    def test_line_trigger_from_flag_and_config(self) -> None:
        rc, out = run(["--very-large-lines", "10", "--json"], self.DIFF)
        data = json.loads(out)
        self.assertEqual(
            (rc, data["size"], data["by"], data["lines_at"]),
            (0, "very-large", "lines", 10),
        )
        rc, out = run(["--very-large-lines", "10"], self.DIFF)
        self.assertEqual(
            out.splitlines()[0],
            "### Complexity: 2 / 100 (very-large: 10 reviewable lines)",
        )
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".co-review.yml"
            cfg.write_text("complexity:\n  very_large_lines: 0\n")
            self.assertEqual(pc.load_config(str(cfg)), ({"lines": 0}, []))
            rc, out = run(["--config", str(cfg), "--json"], self.DIFF)
            self.assertEqual(json.loads(out)["lines_at"], 0)
        self.assertEqual(run(["--very-large-lines", "-1"], self.DIFF)[0], 2)

    def test_bad_config_cut_is_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.yml"
            cfg.write_text("complexity:\n  large_at: high\n")
            self.assertEqual(run(["--config", str(cfg)], self.DIFF)[0], 2)

    def test_non_utf8_stdin_is_replaced_not_a_crash(self) -> None:
        diff = file_diff("x.c", ["-a\xe9", "+b"], 1, 1).encode("latin-1")
        out = io.StringIO()
        old_stdin = pc.sys.stdin
        pc.sys.stdin = io.TextIOWrapper(io.BytesIO(diff), encoding="utf-8")
        try:
            with contextlib.redirect_stdout(out):
                rc = pc.main(["--json"])
        finally:
            pc.sys.stdin = old_stdin
        self.assertEqual((rc, json.loads(out.getvalue())["r"]), (0, 2))

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
        self.assertEqual(
            (rc, data["score"], data["size"], data["p"]), (0, 0, "small", 0)
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
