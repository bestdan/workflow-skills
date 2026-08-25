#!/usr/bin/env bash
# test-local-review.sh — fixture-based tests for parse_diff() in
# scripts/local-review/server.py.
#
# All cases run inside a SINGLE python3 invocation: interpreter startup
# dominates suite time at this repo's scale (see the test-research-spike.sh
# comment in scripts/check.sh — 25s over 305 invocations), so one process
# importing parse_diff once and looping over inline fixtures is the cheap
# shape. The module lives in a dashed directory (local-review), so it is
# loaded via importlib rather than a plain import.
#
# The python body is written to a temp file with `cat > file <<'PYEOF'`
# rather than embedded as `python3 - <<'PYEOF'` inside a `$(...)` command
# substitution: Bash 3.2's $(...) parser does a naive quote-balance scan that
# gets confused by any apostrophe inside a heredoc body nested in a command
# substitution (verified: a heredoc containing "it's" breaks under
# /bin/bash 3.2, but the identical heredoc redirected to a plain file does
# not). Writing to a file first sidesteps the nesting entirely.
#
# Run directly: bash scripts/test-local-review.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/scripts/local-review/server.py"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/test-local-review.XXXXXX")" || exit 2
trap 'rm -rf "$tmp"' EXIT

cat >"$tmp/test_parse_diff.py" <<'PYEOF'
import importlib.util
import json
import os
import sys

server_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("local_review_server", server_path)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
parse_diff = server.parse_diff

pass_count = 0
fail_count = 0
skip_count = 0


def ok(name):
    global pass_count
    pass_count += 1
    print(f"ok - {name}")


def bad(name, detail):
    global fail_count
    fail_count += 1
    print(f"not ok - {name}: {detail}")


def check(name, cond, detail=""):
    if cond:
        ok(name)
    else:
        bad(name, detail)


def skip(name, why):
    global skip_count
    skip_count += 1
    print(f"# skip - {name}: {why}")


# --- plain modification hunk: context/add/del rows, independent line numbers -
diff_mod = """diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@ def foo():
 line one
-line two
+line two changed
 line three
"""
files = parse_diff(diff_mod)
check("plain mod: one file", len(files) == 1, f"got {len(files)}")
f = files[0]
check("plain mod: status modified", f["status"] == "modified", f["status"])
rows = f["hunks"][0]["rows"]
check("plain mod: three rows", len(rows) == 3, f"got {len(rows)}")
r0, r1, r2 = rows
check("plain mod: row0 is context", r0["l"]["t"] == "ctx" and r0["r"]["t"] == "ctx", rows)
check("plain mod: row0 line numbers l=1 r=1", r0["l"]["n"] == 1 and r0["r"]["n"] == 1,
      (r0["l"]["n"], r0["r"]["n"]))
check("plain mod: row1 is del/add pair", r1["l"]["t"] == "del" and r1["r"]["t"] == "add", rows)
check("plain mod: row1 line numbers l=2 r=2", r1["l"]["n"] == 2 and r1["r"]["n"] == 2,
      (r1["l"]["n"], r1["r"]["n"]))
check("plain mod: row2 line numbers l=3 r=3 (advanced independently)",
      r2["l"]["n"] == 3 and r2["r"]["n"] == 3, (r2["l"]["n"], r2["r"]["n"]))

# --- unbalanced -/+ run: shorter side padded with {"t": "empty"} -------------
# Old starts at 10, new starts at 1: the two counters diverge, so a bug that
# numbers the extra addition from the old/del counter (which lands on 12)
# instead of the new counter (which lands on 3) cannot hide behind
# coincidentally-equal counters.
diff_unbalanced = """diff --git a/bar.py b/bar.py
index 1111111..2222222 100644
--- a/bar.py
+++ b/bar.py
@@ -10,2 +1,3 @@
-old one
-old two
+new one
+new two
+new three
"""
rows = parse_diff(diff_unbalanced)[0]["hunks"][0]["rows"]
check("unbalanced: three rows (max of 2 dels, 3 adds)", len(rows) == 3, f"got {len(rows)}")
check("unbalanced: row0 left is del 'old one' at l=10",
      rows[0]["l"] == {"t": "del", "n": 10, "s": "old one"}, rows[0]["l"])
check("unbalanced: row0 right is add 'new one' at r=1",
      rows[0]["r"] == {"t": "add", "n": 1, "s": "new one"}, rows[0]["r"])
check("unbalanced: row1 left is del 'old two' at l=11",
      rows[1]["l"] == {"t": "del", "n": 11, "s": "old two"}, rows[1]["l"])
check("unbalanced: row1 right is add 'new two' at r=2",
      rows[1]["r"] == {"t": "add", "n": 2, "s": "new two"}, rows[1]["r"])
check("unbalanced: row2 left is empty padding", rows[2]["l"]["t"] == "empty", rows[2]["l"])
check("unbalanced: row2 right is the extra add, numbered from the new counter (n=3) not the diverged old counter (12)",
      rows[2]["r"] == {"t": "add", "n": 3, "s": "new three"}, rows[2]["r"])

# --- new file mode -> status added; deleted file mode -> status deleted -----
diff_added = """diff --git a/new.py b/new.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/new.py
@@ -0,0 +1,1 @@
+content
"""
check("new file mode: status added", parse_diff(diff_added)[0]["status"] == "added",
      parse_diff(diff_added)[0]["status"])

diff_deleted = """diff --git a/old.py b/old.py
deleted file mode 100644
index 1111111..0000000
--- a/old.py
+++ /dev/null
@@ -1,1 +0,0 @@
-content
"""
check("deleted file mode: status deleted", parse_diff(diff_deleted)[0]["status"] == "deleted",
      parse_diff(diff_deleted)[0]["status"])

# --- file["single"]: which side, if either, carries all the content ---------
# This is what decides whether the page offers the single-column view mode.
check("single: added file is right-only", parse_diff(diff_added)[0]["single"] == "r",
      parse_diff(diff_added)[0]["single"])
check("single: deleted file is left-only", parse_diff(diff_deleted)[0]["single"] == "l",
      parse_diff(diff_deleted)[0]["single"])
check("single: a mixed modification is neither", parse_diff(diff_mod)[0]["single"] is None,
      parse_diff(diff_mod)[0]["single"])

diff_append = """diff --git a/plan.md b/plan.md
--- a/plan.md
+++ b/plan.md
@@ -1,2 +1,4 @@
 # Plan

+## New section
+more
"""
f = parse_diff(diff_append)[0]
check("single: a pure append is right-only despite its context rows",
      f["single"] == "r", f["single"])
check("single: a pure append is still status modified", f["status"] == "modified", f["status"])

# --- file["truncated"]: a hunk that ends owing lines per its own @@ header ---
# Only preview reads this, and it is the difference between rendering a partial
# file as a whole document and declining to.
diff_short = """diff --git a/new.md b/new.md
new file mode 100644
--- /dev/null
+++ b/new.md
@@ -0,0 +1,5 @@
+one
+two
"""
check("truncated: a hunk short of its @@ count marks the file truncated",
      parse_diff(diff_short)[0]["truncated"] is True,
      parse_diff(diff_short)[0]["truncated"])
check("truncated: a complete added file is not marked truncated",
      parse_diff(diff_added)[0]["truncated"] is False,
      parse_diff(diff_added)[0]["truncated"])
check("truncated: a complete modification is not marked truncated",
      parse_diff(diff_mod)[0]["truncated"] is False,
      parse_diff(diff_mod)[0]["truncated"])

diff_nochange = """diff --git a/a.py b/b.py
similarity index 100%
rename from a.py
rename to b.py
"""
check("single: a rename with no content change is neither",
      parse_diff(diff_nochange)[0]["single"] is None, parse_diff(diff_nochange)[0]["single"])

# --- rename from/rename to -> status renamed, old != new --------------------
diff_renamed = """diff --git a/old_name.py b/new_name.py
similarity index 100%
rename from old_name.py
rename to new_name.py
"""
f = parse_diff(diff_renamed)[0]
check("rename: status renamed", f["status"] == "renamed", f["status"])
check("rename: exact old path", f["old"] == "old_name.py", f["old"])
check("rename: exact new path", f["new"] == "new_name.py", f["new"])

# --- Binary files line -> binary True, no rows -------------------------------
diff_binary = """diff --git a/img.png b/img.png
index 1111111..2222222 100644
Binary files a/img.png and b/img.png differ
"""
f = parse_diff(diff_binary)[0]
check("binary: binary True", f["binary"] is True, f["binary"])
check("binary: no hunks/rows", f["hunks"] == [], f["hunks"])

# --- multi-hunk file: each hunk's section from the @@ trailer, and its own --
# del/add rows kept separate by the hunk-start flush -------------------------
diff_multihunk = """diff --git a/multi.py b/multi.py
index 1111111..2222222 100644
--- a/multi.py
+++ b/multi.py
@@ -1,1 +1,1 @@ def one():
-a
+b
@@ -10,1 +10,1 @@ def two():
-c
+d
"""
f = parse_diff(diff_multihunk)[0]
check("multi-hunk: two hunks", len(f["hunks"]) == 2, len(f["hunks"]))
check("multi-hunk: hunk0 section", f["hunks"][0]["section"] == "def one():", f["hunks"][0]["section"])
check("multi-hunk: hunk1 section", f["hunks"][1]["section"] == "def two():", f["hunks"][1]["section"])
h0rows = f["hunks"][0]["rows"]
h1rows = f["hunks"][1]["rows"]
check("multi-hunk: hunk0 has exactly its own one row (not left empty by a missing hunk-start flush)",
      len(h0rows) == 1, f"got {len(h0rows)}")
check("multi-hunk: hunk0 row is del 'a' / add 'b'",
      bool(h0rows) and h0rows[0]["l"]["s"] == "a" and h0rows[0]["r"]["s"] == "b", h0rows)
check("multi-hunk: hunk1 has exactly its own one row (not hunk0's pair shifted into it)",
      len(h1rows) == 1, f"got {len(h1rows)}")
check("multi-hunk: hunk1 row is del 'c' / add 'd'",
      bool(h1rows) and h1rows[0]["l"]["s"] == "c" and h1rows[0]["r"]["s"] == "d", h1rows)

# --- multi-file diff: rows and adds/dels counts are per-file, not cumulative -
# file0's hunk is a pure addition (zero-length old range), so its row content
# can be pinned precisely and any leak into file1 (a missing file-boundary
# flush) shows up as corrupted rows on one side or the other.
diff_multifile = """diff --git a/one.py b/one.py
index 1111111..2222222 100644
--- a/one.py
+++ b/one.py
@@ -0,0 +1,2 @@
+added one
+added two
diff --git a/two.py b/two.py
index 1111111..2222222 100644
--- a/two.py
+++ b/two.py
@@ -1,2 +1,1 @@
-removed one
-removed two
+kept
"""
files = parse_diff(diff_multifile)
check("multi-file: two files", len(files) == 2, len(files))
check("multi-file: file0 adds=2 dels=0", files[0]["adds"] == 2 and files[0]["dels"] == 0,
      (files[0]["adds"], files[0]["dels"]))
check("multi-file: file1 adds=1 dels=2 (not cumulative)", files[1]["adds"] == 1 and files[1]["dels"] == 2,
      (files[1]["adds"], files[1]["dels"]))
f0rows = files[0]["hunks"][0]["rows"]
f1rows = files[1]["hunks"][0]["rows"]
check("multi-file: file0 has exactly its own two add rows (not lost to the next file's boundary flush)",
      len(f0rows) == 2, f"got {len(f0rows)}")
check("multi-file: file0 rows are its own adds, in order",
      f0rows[0]["r"]["s"] == "added one" and f0rows[1]["r"]["s"] == "added two", f0rows)
check("multi-file: file1 has exactly its own two rows (not file0's pending adds leaked in)",
      len(f1rows) == 2, f"got {len(f1rows)}")
check("multi-file: file1 row0 is del 'removed one' / add 'kept', row1 is del 'removed two' / empty",
      f1rows[0]["l"]["s"] == "removed one" and f1rows[0]["r"]["s"] == "kept"
      and f1rows[1]["l"]["s"] == "removed two" and f1rows[1]["r"]["t"] == "empty", f1rows)

# --- "No newline at end of file" marker is skipped, not a row ---------------
diff_no_newline = """diff --git a/nn.py b/nn.py
index 1111111..2222222 100644
--- a/nn.py
+++ b/nn.py
@@ -1,1 +1,1 @@
-old
\\ No newline at end of file
+new
\\ No newline at end of file
"""
rows = parse_diff(diff_no_newline)[0]["hunks"][0]["rows"]
check("no-newline marker: skipped, one row only", len(rows) == 1, f"got {len(rows)}")

# --- filename matching GENERATED regex -> generated True ---------------------
diff_generated = """diff --git a/lib/foo.g.dart b/lib/foo.g.dart
index 1111111..2222222 100644
--- a/lib/foo.g.dart
+++ b/lib/foo.g.dart
@@ -1,1 +1,1 @@
-old
+new
"""
check("generated: foo.g.dart flagged generated", parse_diff(diff_generated)[0]["generated"] is True,
      parse_diff(diff_generated)[0]["generated"])

diff_lockfile = """diff --git a/Gemfile.lock b/Gemfile.lock
index 1111111..2222222 100644
--- a/Gemfile.lock
+++ b/Gemfile.lock
@@ -1,1 +1,1 @@
-old
+new
"""
check("generated: Gemfile.lock flagged generated", parse_diff(diff_lockfile)[0]["generated"] is True,
      parse_diff(diff_lockfile)[0]["generated"])

diff_not_generated = """diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,1 +1,1 @@
-old
+new
"""
check("generated: foo.py not flagged generated", parse_diff(diff_not_generated)[0]["generated"] is False,
      parse_diff(diff_not_generated)[0]["generated"])

# --- table-driven: GENERATED pattern coverage across languages/ecosystems ---
# One multi-file diff fixture built from a path list, rather than 24 hand-
# written fixtures, keeps this table cheap to extend.
GENERATED_TRUE_PATHS = [
    "uv.lock", "package-lock.json", "go.sum",
    "api.pb.go", "models_pb2.py", "models_pb2_grpc.py",
    "types.generated.ts", "src/__generated__/schema.ts", "app/generated/client.py",
    "bundle.min.js", "styles.min.css", "bundle.js.map",
    "tests/__snapshots__/foo.test.js.snap", "widget.snap",
    "vendor/lib.js", "third/vendor/lib.js",
    "foo.g.dart", "foo.freezed.dart",
]
GENERATED_FALSE_PATHS = [
    "src/vendors.py", "lockfile_test.go", "generated_report.md", "minify.js",
    "snapshot.py", "golden.snapshot.md",
]


def diff_for_paths(paths):
    parts = []
    for p in paths:
        parts.append(
            f"diff --git a/{p} b/{p}\n"
            f"index 1111111..2222222 100644\n"
            f"--- a/{p}\n"
            f"+++ b/{p}\n"
            f"@@ -1,1 +1,1 @@\n"
            f"-old\n"
            f"+new\n"
        )
    return "".join(parts)


table_paths = GENERATED_TRUE_PATHS + GENERATED_FALSE_PATHS
table_files = parse_diff(diff_for_paths(table_paths))
check("generated table: one file per path", len(table_files) == len(table_paths),
      f"got {len(table_files)}")
table_by_path = {f["new"]: f["generated"] for f in table_files}
for p in GENERATED_TRUE_PATHS:
    check(f"generated table: {p} flagged generated", table_by_path.get(p) is True,
          table_by_path.get(p))
for p in GENERATED_FALSE_PATHS:
    check(f"generated table: {p} not flagged generated (near-miss stays expanded)",
          table_by_path.get(p) is False, table_by_path.get(p))

# --- plain unified patch (no `diff --git` header) ---------------------------
diff_plain = """--- a.txt\t2024-01-01 00:00:00.000000000 +0000
+++ b.txt\t2024-01-01 00:00:00.000000000 +0000
@@ -1,1 +1,1 @@
-old line
+new line
"""
files = parse_diff(diff_plain)
check("plain unified: one file", len(files) == 1, f"got {len(files)}")
f = files[0]
check("plain unified: display name b.txt (no a/ b/ prefix to strip)", f["display"] == "b.txt", f["display"])
check("plain unified: status modified", f["status"] == "modified", f["status"])
rows = f["hunks"][0]["rows"]
check("plain unified: one del/add row", len(rows) == 1
      and rows[0]["l"]["s"] == "old line" and rows[0]["r"]["s"] == "new line", rows)

# --- headerless multi-file patch: two ---/+++ pairs, no `diff --git` --------
diff_headerless_multi = """--- a1.txt
+++ b1.txt
@@ -1,1 +1,1 @@
-old1
+new1
--- a2.txt
+++ b2.txt
@@ -1,1 +1,1 @@
-old2
+new2
"""
files = parse_diff(diff_headerless_multi)
check("headerless multi-file: two files", len(files) == 2, f"got {len(files)}")
check("headerless multi-file: file0 name b1.txt", files[0]["display"] == "b1.txt", files[0]["display"])
check("headerless multi-file: file1 name b2.txt", files[1]["display"] == "b2.txt", files[1]["display"])
rows0 = files[0]["hunks"][0]["rows"]
rows1 = files[1]["hunks"][0]["rows"]
check("headerless multi-file: file0 has exactly its own row",
      len(rows0) == 1 and rows0[0]["l"]["s"] == "old1" and rows0[0]["r"]["s"] == "new1", rows0)
check("headerless multi-file: file1 has exactly its own row (not file0's leaked in)",
      len(rows1) == 1 and rows1[0]["l"]["s"] == "old2" and rows1[0]["r"]["s"] == "new2", rows1)

# --- plain patch with a/ b/ prefixes but no `diff --git` header -------------
diff_plain_prefixed = """--- a/foo.txt
+++ b/foo.txt
@@ -1,1 +1,1 @@
-x
+y
"""
f = parse_diff(diff_plain_prefixed)[0]
check("plain unified with a/b prefix: prefix stripped", f["display"] == "foo.txt", f["display"])

# --- /dev/null on old side -> added; on new side -> deleted (headerless) ----
diff_plain_added = """--- /dev/null
+++ b/new.txt
@@ -0,0 +1,1 @@
+content
"""
f = parse_diff(diff_plain_added)[0]
check("plain unified /dev/null old: status added", f["status"] == "added", f["status"])
check("plain unified /dev/null old: name new.txt", f["new"] == "new.txt", f["new"])

diff_plain_deleted = """--- a/old.txt
+++ /dev/null
@@ -1,1 +0,0 @@
-content
"""
f = parse_diff(diff_plain_deleted)[0]
check("plain unified /dev/null new: status deleted", f["status"] == "deleted", f["status"])
check("plain unified /dev/null new: name old.txt", f["old"] == "old.txt", f["old"])

# --- quoted `diff --git` header (core.quotepath, non-ASCII path) ------------
diff_quoted = r"""diff --git "a/p\303\244th.py" "b/p\303\244th.py"
index 1111111..2222222 100644
--- "a/p\303\244th.py"
+++ "b/p\303\244th.py"
@@ -1,1 +1,1 @@
-old
+new
"""
f = parse_diff(diff_quoted)[0]
check("quoted header: new decodes non-ASCII octal escapes", f["new"] == "päth.py", f["new"])
check("quoted header: old decodes non-ASCII octal escapes", f["old"] == "päth.py", f["old"])

# _unquote_git_path: the full git escape set, and malformed octal doesn't raise
uq = server._unquote_git_path
check("unquote: full C escape set", uq(r"a\ab\bc\fd\ne\rf\tg\vh") == "a\ab\bc\fd\ne\rf\tg\vh",
      repr(uq(r"a\ab\bc\fd\ne\rf\tg\vh")))
check("unquote: malformed octal \\800 passes through without raising",
      uq(r"x\800y") == "x\\800y", repr(uq(r"x\800y")))
check("unquote: octal requires all three digits octal", uq(r"\079") == "\\079", repr(uq(r"\079")))
rows = f["hunks"][0]["rows"]
check("quoted header: hunk row still parses", len(rows) == 1
      and rows[0]["l"]["s"] == "old" and rows[0]["r"]["s"] == "new", rows)

# --- mixed quoting: plain a-side, quoted b-side ------------------------------
diff_mixed_quote = r"""diff --git a/plain.py "b/quot\303\251.py"
index 1111111..2222222 100644
--- a/plain.py
+++ "b/quot\303\251.py"
@@ -1,1 +1,1 @@
-old
+new
"""
f = parse_diff(diff_mixed_quote)[0]
check("mixed quoting: old is the plain a-side", f["old"] == "plain.py", f["old"])
check("mixed quoting: new decodes the quoted b-side", f["new"] == "quoté.py", f["new"])

# --- mixed quoting with an unquoted spaced a-side + quoted b-side -----------
# Git never C-quotes an ordinary space, so a spaced path can appear unquoted
# right alongside a quoted counterpart.
diff_mixed_quote_space_a = r"""diff --git a/old name.py "b/n\303\251w.py"
index 1111111..2222222 100644
--- a/old name.py
+++ "b/n\303\251w.py"
@@ -1,1 +1,1 @@
-old
+new
"""
f = parse_diff(diff_mixed_quote_space_a)[0]
check("mixed quoting, spaced unquoted a-side: old keeps its space", f["old"] == "old name.py", f["old"])
check("mixed quoting, spaced unquoted a-side: new decodes the quoted b-side", f["new"] == "néw.py", f["new"])

# --- mixed quoting with a quoted a-side + unquoted spaced b-side ------------
diff_mixed_quote_space_b = r"""diff --git "a/\303\244ld.py" b/new name.py
index 1111111..2222222 100644
--- "a/\303\244ld.py"
+++ b/new name.py
@@ -1,1 +1,1 @@
-old
+new
"""
f = parse_diff(diff_mixed_quote_space_b)[0]
check("mixed quoting, spaced unquoted b-side: old decodes the quoted a-side", f["old"] == "äld.py", f["old"])
check("mixed quoting, spaced unquoted b-side: new keeps its space", f["new"] == "new name.py", f["new"])

# --- headerless ambiguity: a deleted/added line whose content itself starts
# with "-- "/"++ " (raw "--- "/"+++ ") must stay part of the active hunk, not
# be mistaken for the next file's ---/+++ pair -------------------------------
diff_headerless_ambiguous = """--- a.txt
+++ b.txt
@@ -1,2 +1,2 @@
 context line
--- deleted content
+++ added content
"""
files = parse_diff(diff_headerless_ambiguous)
check("headerless ambiguity: exactly one file (no bogus second file)", len(files) == 1, f"got {len(files)}")
rows = files[0]["hunks"][0]["rows"]
check("headerless ambiguity: two rows (context, then the del/add pair)", len(rows) == 2, f"got {len(rows)}")
check("headerless ambiguity: row0 is the context line",
      rows[0]["l"]["t"] == "ctx" and rows[0]["l"]["s"] == "context line", rows[0])
check("headerless ambiguity: row1 is del '-- deleted content' / add '++ added content'",
      rows[1]["l"]["s"] == "-- deleted content" and rows[1]["r"]["s"] == "++ added content", rows[1])

# --- headerless ambiguity: multi-line hunk correctly exhausts its @@ counts
# before the next file's `--- ` is recognized as a new file boundary --------
diff_headerless_multiline = """--- a1.txt
+++ b1.txt
@@ -1,2 +1,2 @@
 ctx
-old1
+new1
--- a2.txt
+++ b2.txt
@@ -1,1 +1,1 @@
-old2
+new2
"""
files = parse_diff(diff_headerless_multiline)
check("headerless multiline: two files (second --- recognized only after counts exhausted)",
      len(files) == 2, f"got {len(files)}")
check("headerless multiline: file0 name b1.txt", files[0]["display"] == "b1.txt", files[0]["display"])
check("headerless multiline: file1 name b2.txt", files[1]["display"] == "b2.txt", files[1]["display"])
f0rows = files[0]["hunks"][0]["rows"]
f1rows = files[1]["hunks"][0]["rows"]
check("headerless multiline: file0 has its context row then its del/add row",
      len(f0rows) == 2 and f0rows[0]["l"]["s"] == "ctx"
      and f0rows[1]["l"]["s"] == "old1" and f0rows[1]["r"]["s"] == "new1", f0rows)
check("headerless multiline: file1 has exactly its own row",
      len(f1rows) == 1 and f1rows[0]["l"]["s"] == "old2" and f1rows[0]["r"]["s"] == "new2", f1rows)

# --- empty input -> [] --------------------------------------------------------
check("empty input: returns []", parse_diff("") == [], parse_diff(""))

# --- sh() on a failing command raises RuntimeError, not CalledProcessError --
try:
    server.sh(["false"])
    bad("sh(): failing command raises", "no exception raised")
except RuntimeError as e:
    check("sh(): failing command raises RuntimeError with the command in the message",
          "false" in str(e), str(e))
except Exception as e:
    bad("sh(): failing command raises RuntimeError", f"raised {type(e).__name__} instead: {e}")

# --- sh() on a MISSING command (gh absent from PATH) also raises RuntimeError
try:
    server.sh(["definitely-not-a-real-command-xyzzy"])
    bad("sh(): missing command raises", "no exception raised")
except RuntimeError as e:
    check("sh(): missing command raises RuntimeError, not FileNotFoundError",
          "definitely-not-a-real-command-xyzzy" in str(e), str(e))
except Exception as e:
    bad("sh(): missing command raises RuntimeError", f"raised {type(e).__name__} instead: {e}")

# --- bind_server: only EADDRINUSE triggers the autoselect fallback ----------
import errno as _errno


class _FakeSrv:
    def __init__(self, addr, handler, err=None):
        if err is not None:
            raise OSError(err, "boom")
        self.server_address = addr


_real_srv = server.ThreadingHTTPServer
try:
    calls = []
    def fake_addrinuse(addr, handler):
        calls.append(addr[1])
        return _FakeSrv(addr, handler, _errno.EADDRINUSE if addr[1] == 8765 else None)
    server.ThreadingHTTPServer = fake_addrinuse
    srv, fell_back = server.bind_server(None)
    check("bind_server: EADDRINUSE on 8765 falls back to autoselect",
          fell_back and calls == [8765, 0], (fell_back, calls))

    def fake_eacces(addr, handler):
        return _FakeSrv(addr, handler, _errno.EACCES if addr[1] == 8765 else None)
    server.ThreadingHTTPServer = fake_eacces
    try:
        server.bind_server(None)
        bad("bind_server: non-EADDRINUSE OSError propagates", "no exception raised")
    except OSError as e:
        check("bind_server: non-EADDRINUSE OSError propagates", e.errno == _errno.EACCES, e.errno)
finally:
    server.ThreadingHTTPServer = _real_srv

# --- esc_py escapes quotes as well as angle brackets/ampersand --------------
esc_out = server.esc_py('"><script>')
check("esc_py: no raw double quote in output", '"' not in esc_out, esc_out)
check("esc_py: no raw angle brackets in output", "<" not in esc_out and ">" not in esc_out, esc_out)
check("esc_py: single quote is also escaped", "'" not in server.esc_py("it's"), server.esc_py("it's"))

# --- build_page: a diff line containing </script> cannot break out of the ---
# <script> element the DIFF/META JSON is interpolated into (PR #369
# copilot+codex finding).
evil_files = [{
    "old": "x", "new": "x", "display": "x", "status": "modified", "generated": False,
    "binary": False, "adds": 1, "dels": 0,
    "hunks": [{"header": "@@ -1,1 +1,1 @@", "section": "", "rows": [
        {"l": {"t": "empty"}, "r": {"t": "add", "n": 1, "s": "</script><script>alert(1)</script>"}},
    ]}],
}]
page_html = server.build_page(evil_files, {"title": "t"}).decode("utf-8")
check("build_page: the raw injected payload does not survive",
      "</script><script>alert(1)</script>" not in page_html, page_html)
check("build_page: the diff's </script> is escaped to \\u003c/script>",
      "\\u003c/script>" in page_html, page_html)
# PAGE has exactly four structural </script> closes: the three vendored
# <script src=...></script> includes (highlight, dart, marked) and the one
# inline block. A diff/meta payload that could inject a fifth would mean the
# escaping regressed.
check("build_page: exactly the 4 structural </script> closes remain, none injected from the diff",
      page_html.count("</script>") == 4, page_html.count("</script>"))

# --- build_page: meta['url'] is escaped before landing in the href ----------
evil_meta = {"title": "t", "url": '"><script>alert(2)</script>', "number": 1}
page_html2 = server.build_page([], evil_meta).decode("utf-8")
check("build_page: meta['url'] quotes are escaped before the href interpolation",
      'href="&quot;' in page_html2 and "&gt;" in page_html2, page_html2)

# --- build_page: the payload markers are substituted in ONE pass ------------
# Reviewing a diff of server.py itself puts the literal marker text into the
# diff payload. Chained .replace() calls substituted into the JSON they had
# just inserted, corrupting it — so dogfooding this tool on itself broke the
# page. Both markers must survive as data.
self_files = parse_diff("""diff --git a/server.py b/server.py
--- a/server.py
+++ b/server.py
@@ -1,2 +1,2 @@
-const META = /*__META_JSON__*/{};
+const DIFF = /*__DIFF_JSON__*/[];
""")
page_self = server.build_page(self_files, {"title": "self"}).decode("utf-8")
i = page_self.index("const DIFF = ") + len("const DIFF = ")
try:
    decoded, _ = json.JSONDecoder().raw_decode(page_self[i:])
    ok_self, why = True, ""
except ValueError as e:
    decoded, ok_self, why = None, False, str(e)
check("build_page: the DIFF payload is still valid JSON when the diff quotes the markers",
      ok_self, why)
# A PR title can carry a marker too. Assert on the rendered <title> element
# specifically: the literal marker also appears in the META JSON (re.sub does
# not rescan its own replacement), so a whole-page substring check passes even
# when the title itself has been corrupted to "Review · fix the [] ...".
page_title = server.build_page(
    [], {"title": "fix the /*__DIFF_JSON__*/[] substitution"}).decode("utf-8")
title_el = page_title.split("<title>")[1].split("</title>")[0]
check("build_page: a marker inside the PR TITLE survives as data, unsubstituted",
      "/*__DIFF_JSON__*/[]" in title_el, title_el)

check("build_page: a marker inside the diff text survives as data, unsubstituted",
      ok_self and any("/*__META_JSON__*/{};" in r["l"]["s"]
                      for r in decoded[0]["hunks"][0]["rows"] if r["l"]["t"] == "del"),
      decoded)

# --- WORDLIST sanity: exactly 1024 distinct lowercase 3-7 letter words ------
import re as _wordlist_re
check("WORDLIST: exactly 1024 entries", len(server.WORDLIST) == 1024, len(server.WORDLIST))
check("WORDLIST: all entries distinct", len(set(server.WORDLIST)) == 1024,
      len(set(server.WORDLIST)))
check("WORDLIST: every entry is 3-7 lowercase letters",
      all(_wordlist_re.fullmatch(r"[a-z]{3,7}", w) for w in server.WORDLIST),
      [w for w in server.WORDLIST if not _wordlist_re.fullmatch(r"[a-z]{3,7}", w)])

# --- _git_diff_args: the three --git spec forms map to the right git argv ---
check("_git_diff_args: uncommitted -> diff HEAD",
      server._git_diff_args("/repo", "uncommitted") == ["git", "-C", "/repo", "diff", "--end-of-options", "HEAD"],
      server._git_diff_args("/repo", "uncommitted"))
check("_git_diff_args: a single ref -> diff <ref>...HEAD",
      server._git_diff_args("/repo", "main") == ["git", "-C", "/repo", "diff", "--end-of-options", "main...HEAD"],
      server._git_diff_args("/repo", "main"))
check("_git_diff_args: an explicit A...B range passes through verbatim",
      server._git_diff_args("/repo", "abc123...def456") == ["git", "-C", "/repo", "diff", "--end-of-options", "abc123...def456"],
      server._git_diff_args("/repo", "abc123...def456"))
try:
    server._git_diff_args("/repo", "--output=/tmp/pwned")
    bad("_git_diff_args: option-like spec is rejected", "no exception raised")
except RuntimeError as e:
    check("_git_diff_args: option-like spec is rejected", "invalid --git spec" in str(e), str(e))

# --- server-behavior cases: real subprocesses, one python3 process launches ---
# them all. Each server is started with --diff-file (no `gh` needed) and no
# --port, so the OS picks a free port and the server reports it.
import http.cookiejar as _cookiejar
import re as _re
import selectors as _selectors
import shutil as _shutil
import socket as _socket
import subprocess as _subprocess
import tempfile as _tempfile
import threading as _threading
import time as _time
import urllib.error as _urlerror
import urllib.request as _urlrequest

PATCH = """diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@ def foo():
 line one
-line two
+line two changed
 line three
"""


def start_server(extra_args, env=None, cwd=None):
    patch_fd, patch_path = _tempfile.mkstemp(suffix=".patch")
    with os.fdopen(patch_fd, "w") as f:
        f.write(PATCH)
    proc = _subprocess.Popen(
        [sys.executable, server_path, "--diff-file", patch_path] + extra_args,
        stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True, env=env, cwd=cwd,
    )
    return proc, patch_path


def make_fake_gh_dir(diff_ok=False):
    # A `gh` stand-in that fails, so a PR-mode run exercises the same
    # "gh command failed" path a real unauthenticated/misconfigured gh would.
    # With diff_ok, `gh pr diff` succeeds (emits a small patch) while `gh pr
    # view` still fails — that exercises the get_meta/resolve_gh propagation
    # specifically, past a successful get_diff.
    d = _tempfile.mkdtemp(prefix="fakegh-")
    gh_path = os.path.join(d, "gh")
    if diff_ok:
        body = ("#!/bin/sh\n"
                "if [ \"$1\" = pr ] && [ \"$2\" = diff ]; then\n"
                "  printf 'diff --git a/x b/x\\n--- a/x\\n+++ b/x\\n@@ -1,1 +1,1 @@\\n-a\\n+b\\n'\n"
                "  exit 0\n"
                "fi\n"
                "echo 'gh: authentication required' >&2\nexit 1\n")
    else:
        body = "#!/bin/sh\necho 'gh: authentication required' >&2\nexit 1\n"
    with open(gh_path, "w") as f:
        f.write(body)
    os.chmod(gh_path, 0o755)
    return d


def read_url(proc, deadline=5.0):
    # readline() blocks until a line arrives or the pipe closes, so a server
    # that goes quiet without exiting would hang this forever. Read the raw fd
    # in non-blocking chunks, gated by select() against a real deadline, and
    # split lines ourselves -- selecting on proc.stdout itself is not enough:
    # a single raw read can pull multiple lines into its buffer, leaving the
    # fd not-yet-readable again while an unread line is already sitting in
    # that buffer.
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)
    sel = _selectors.DefaultSelector()
    sel.register(fd, _selectors.EVENT_READ)
    start = _time.monotonic()
    buf = ""
    try:
        while True:
            remaining = deadline - (_time.monotonic() - start)
            if remaining <= 0:
                return None
            if not sel.select(timeout=remaining):
                continue
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                if proc.poll() is not None:
                    return None
                continue
            buf += chunk.decode(errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                m = _re.search(r"LOCAL_REVIEW_URL=(\S+)", line)
                if m:
                    return m.group(1)
    finally:
        sel.close()


def url_parts(url):
    # url is "http://host:port/token/" -- split into (host, port, "/token/")
    # for callers that need to build a raw HTTP request by hand.
    scheme_rest = url.split("//", 1)[1]
    host_port, token_path = scheme_rest.split("/", 1)
    host, port_s = host_port.split(":")
    return host, int(port_s), host_port, "/" + token_path


def open_token_url(url, timeout=5):
    # The tokenized URL now 302s to a cookie-scoped /, so opening it needs a
    # cookie jar to follow the redirect the way a browser would.
    opener = _urlrequest.build_opener(_urlrequest.HTTPCookieProcessor(_cookiejar.CookieJar()))
    return opener.open(url, timeout=timeout)


# -- launching without --port autoselects a free port and prints the URL ----
proc1, patch1 = start_server([])
proc2, patch2 = start_server([])
try:
    url1 = read_url(proc1)
    url2 = read_url(proc2)
    check("server: autoselect prints LOCAL_REVIEW_URL for server1", bool(url1), url1)
    check("server: autoselect prints LOCAL_REVIEW_URL for server2", bool(url2), url2)
    check("server: two autoselected servers get distinct ports",
          bool(url1) and bool(url2) and url1 != url2, (url1, url2))
    TOKEN_RE = _re.compile(r"^[a-z]{3,7}(-[a-z]{3,7}){3}$")
    tok1 = url_parts(url1)[3].strip("/") if url1 else None
    tok2 = url_parts(url2)[3].strip("/") if url2 else None
    check("server: token is four hyphenated 3-7 letter words",
          bool(tok1) and bool(TOKEN_RE.match(tok1)), tok1)
    check("server: two launches produce different tokens",
          bool(tok1) and bool(tok2) and tok1 != tok2, (tok1, tok2))
finally:
    for p in (proc1, proc2):
        p.terminate()
        try:
            p.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)
    for p in (patch1, patch2):
        os.unlink(p)

# -- default-port fallback: 8765 held -> server must land elsewhere ---------
# Try to bind our own socket on 8765 first. If that succeeds, our hold
# guarantees the server can't land there. If it raises (already busy on this
# machine, e.g. another process or a leftover from a previous run), the port
# is already held by someone else, which serves the same purpose -- either
# way the server must not land on 8765 while it's occupied. Only close the
# socket in the finally when we actually acquired it.
hold_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
hold_sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
acquired = False
try:
    hold_sock.bind(("127.0.0.1", 8765))
    hold_sock.listen(1)
    acquired = True
except OSError:
    pass
try:
    proc3, patch3 = start_server([])
    try:
        url3 = read_url(proc3)
        check("server: fallback run still prints LOCAL_REVIEW_URL", bool(url3), url3)
        if url3:
            _, port3, _, _ = url_parts(url3)
            check("server: fallback lands on a port other than the held 8765",
                  port3 != 8765, port3)
    finally:
        proc3.terminate()
        try:
            proc3.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc3.kill()
            proc3.wait(timeout=5)
        os.unlink(patch3)
finally:
    if acquired:
        hold_sock.close()

# -- full round trip: GET /, POST /submit, atomic $OUT, --once exits --------
# (--out with --once: one-shot mode)
out_fd, out_path = _tempfile.mkstemp(suffix=".json")
os.close(out_fd)
os.unlink(out_path)  # server must create it; --once poller checks for existence
proc, patch_path = start_server(["--once", "--out", out_path])
try:
    url = read_url(proc)
    check("server: --once run prints LOCAL_REVIEW_URL", bool(url), url)
    if url:
        with open_token_url(url) as resp:
            body = resp.read().decode()
            check("server: GET / returns 200", resp.status == 200, resp.status)
            # The token is a path segment, so this header is what stops it
            # riding the Referer of an outbound link — and preview renders
            # links authored by whoever wrote the diff. Asserted on a page
            # response, an asset response, and an error response below,
            # because the guarantee claimed is "every response".
            check("server: page response sets Referrer-Policy: no-referrer",
                  resp.headers.get("Referrer-Policy") == "no-referrer",
                  resp.headers.get("Referrer-Policy"))
        with _urlrequest.urlopen(url + "vendor/marked.umd.js", timeout=5) as resp2:
            check("server: asset response sets Referrer-Policy: no-referrer",
                  resp2.headers.get("Referrer-Policy") == "no-referrer",
                  resp2.headers.get("Referrer-Policy"))
        try:
            _urlrequest.urlopen(url + "no-such-route", timeout=5)
            bad("server: a bogus route 404s", "it did not")
        except _urlerror.HTTPError as e:
            check("server: error response sets Referrer-Policy: no-referrer",
                  e.headers.get("Referrer-Policy") == "no-referrer",
                  e.headers.get("Referrer-Policy"))
            check("server: GET / body contains 'Submit review'", "Submit review" in body, "")
        payload = {"meta": {}, "summary": "looks good",
                   "comments": [{"file": "a.py", "side": "R", "line": 1, "code": "x", "text": "one"}]}
        req = _urlrequest.Request(
            f"{url}submit", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: POST /submit returns 200", resp.status == 200, resp.status)
        try:
            rc = proc.wait(timeout=5)
            check("server: --once exits after a successful /submit", rc == 0, rc)
        except _subprocess.TimeoutExpired:
            bad("server: --once exits after a successful /submit", "did not exit within 5s")
        check("server: $OUT exists after submit", os.path.exists(out_path), out_path)
        if os.path.exists(out_path):
            with open(out_path) as f:
                written = json.load(f)
            check("server: $OUT parses as JSON with the submitted fields and no approved key",
                  written.get("summary") == "looks good" and "approved" not in written,
                  written)
            check("server: one-shot $OUT carries no round, threads, or finished key",
                  "round" not in written and "threads" not in written and "finished" not in written,
                  written)
            check("server: one-shot $OUT keeps the submitted comment, unminted",
                  len(written.get("comments", [])) == 1
                  and all("id" not in c for c in written["comments"]), written)
        _fd = proc.stdout.fileno()
        os.set_blocking(_fd, False)
        _stdout_tail = ""
        while True:
            try:
                _chunk = os.read(_fd, 4096)
            except (BlockingIOError, OSError):
                break
            if not _chunk:
                break
            _stdout_tail += _chunk.decode(errors="replace")
        check("server: stdout prints REVIEW SUBMITTED after a submit",
              "REVIEW SUBMITTED" in _stdout_tail, _stdout_tail)
        check("server: stdout carries no APPROVED: line after a submit",
              "APPROVED:" not in _stdout_tail, _stdout_tail)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    if os.path.exists(out_path):
        os.unlink(out_path)

# -- unwritable --out directory: /submit 500s before any side effect --------
# (--out with no --once: threads mode, stays alive)
bad_out_root = _tempfile.mkdtemp(prefix="lr-gone-")
bad_out = os.path.join(bad_out_root, "missing", "out.json")
proc, patch_path = start_server(["--out", bad_out])
try:
    url = read_url(proc)
    check("server: unwritable-out run still starts", bool(url), url)
    if url:
        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"","comments":[]}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: unwritable --out returns 500", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            body = json.loads(e.read().decode())
            check("server: unwritable --out returns 500", e.code == 500, e.code)
            check("server: unwritable --out reports the error", body.get("ok") is False and "cannot write --out" in body.get("error", ""), body)
        check("server: unwritable --out does not crash the server", proc.poll() is None, proc.poll())
        check("server: no OUT file appears", not os.path.exists(bad_out), bad_out)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    _shutil.rmtree(bad_out_root, ignore_errors=True)

# -- client disconnect mid-response: --once still exits, OUT still lands ----
dc_fd, dc_out = _tempfile.mkstemp(suffix=".json")
os.close(dc_fd)
os.unlink(dc_out)
proc, patch_path = start_server(["--once", "--out", dc_out])
try:
    url = read_url(proc)
    check("server: disconnect run starts", bool(url), url)
    if url:
        host, port, host_port, token_path = url_parts(url)
        body = b'{"meta":{},"summary":"gone","comments":[]}'
        raw = (b"POST " + (token_path + "submit").encode() + b" HTTP/1.1\r\nHost: " + host_port.encode()
               + b"\r\nContent-Type: application/json\r\nContent-Length: "
               + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        s = _socket.create_connection((host, port), timeout=5)
        s.sendall(raw)
        s.close()  # walk away without reading the response
        try:
            rc = proc.wait(timeout=5)
            check("server: --once exits despite client disconnect", rc == 0, rc)
        except _subprocess.TimeoutExpired:
            bad("server: --once exits despite client disconnect", "did not exit within 5s")
        check("server: OUT is durable despite client disconnect",
              os.path.exists(dc_out) and json.load(open(dc_out)).get("summary") == "gone", dc_out)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    if os.path.exists(dc_out):
        os.unlink(dc_out)

# -- submission slot: concurrent posts don't race, sequential rounds still work
slot_fd, slot_out = _tempfile.mkstemp(suffix=".json")
os.close(slot_fd)
os.unlink(slot_out)
proc, patch_path = start_server(["--out", slot_out])  # stay-alive: threads mode (--out, no --once)
try:
    url = read_url(proc)
    check("server: slot run starts", bool(url), url)
    if url:
        def submit_once(results, idx):
            req = _urlrequest.Request(
                f"{url}submit", data=b'{"meta":{},"summary":"race","comments":[]}',
                headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                with _urlrequest.urlopen(req, timeout=5) as resp:
                    results[idx] = resp.status
            except _urlerror.HTTPError as e:
                results[idx] = e.code
            except Exception as e:
                results[idx] = str(e)
        results = [None] * 4
        threads = [_threading.Thread(target=submit_once, args=(results, i)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        check("server: concurrent submits each get 200 or 409, nothing else",
              all(r in (200, 409) for r in results), results)
        check("server: at least one concurrent submit wins", 200 in results, results)
        check("server: OUT is whole JSON after the race",
              os.path.exists(slot_out) and json.load(open(slot_out)).get("summary") == "race", slot_out)
        check("server: threads mode stays alive after a submit", proc.poll() is None, proc.poll())
        # sequential second round on a stay-alive server must still be allowed
        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"round 2","comments":[]}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: a sequential second round still returns 200", resp.status == 200, resp.status)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    if os.path.exists(slot_out):
        os.unlink(slot_out)

# -- threads mode: id minting, round numbering, lossless threads, Finish -----
th_fd, th_out = _tempfile.mkstemp(suffix=".json")
os.close(th_fd)
os.unlink(th_out)
proc, patch_path = start_server(["--out", th_out])  # stay-alive: threads mode (--out, no --once)
try:
    url = read_url(proc)
    check("server: threads-mode run starts", bool(url), url)
    if url:
        # round 1: two comments -> ids t1, t2
        payload1 = {"meta": {}, "summary": "", "comments": [
            {"file": "a.py", "side": "R", "line": 1, "code": "x", "text": "first"},
            {"file": "a.py", "side": "R", "line": 2, "code": "y", "text": "second"},
        ]}
        req = _urlrequest.Request(
            f"{url}submit", data=json.dumps(payload1).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: threads-mode round 1 returns 200", resp.status == 200, resp.status)
            info1 = json.loads(resp.read().decode())
        check("server: threads-mode round 1 response mints ids t1, t2",
              info1.get("ids") == ["t1", "t2"], info1)
        check("server: threads-mode round 1 response carries round 1",
              info1.get("round") == 1, info1)
        check("server: threads-mode server stays alive after round 1", proc.poll() is None, proc.poll())
        with open(th_out) as f:
            written1 = json.load(f)
        check("server: threads-mode round 1 --out carries round 1", written1.get("round") == 1, written1)
        check("server: threads-mode round 1 --out finished is false", written1.get("finished") is False, written1)
        check("server: threads-mode round 1 --out threads has 2 entries",
              len(written1.get("threads", [])) == 2, written1)
        check("server: threads-mode round 1 threads start unresolved with no replies",
              all(t.get("resolved") is False and t.get("replies") == [] for t in written1.get("threads", [])),
              written1)
        check("server: threads-mode round 1 comments carry the minted ids",
              [c.get("id") for c in written1.get("comments", [])] == ["t1", "t2"], written1)

        # round 2: one more comment -> id t3; threads array grows losslessly
        payload2 = {"meta": {}, "summary": "", "comments": [
            {"file": "a.py", "side": "R", "line": 3, "code": "z", "text": "third"},
        ]}
        req = _urlrequest.Request(
            f"{url}submit", data=json.dumps(payload2).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: threads-mode round 2 returns 200", resp.status == 200, resp.status)
            info2 = json.loads(resp.read().decode())
        check("server: threads-mode round 2 response mints id t3", info2.get("ids") == ["t3"], info2)
        check("server: threads-mode round 2 response carries round 2", info2.get("round") == 2, info2)
        with open(th_out) as f:
            written2 = json.load(f)
        check("server: threads-mode round 2 --out carries round 2", written2.get("round") == 2, written2)
        check("server: threads-mode round 2 --out comments has only the new-in-round entry",
              len(written2.get("comments", [])) == 1, written2)
        check("server: threads-mode round 2 --out threads carries all 3 (lossless overwrite)",
              len(written2.get("threads", [])) == 3, written2)
        check("server: threads-mode round 2 --out finished is false", written2.get("finished") is False, written2)
        check("server: threads-mode server stays alive after round 2", proc.poll() is None, proc.poll())

        # a non-boolean finished must 400, not shut the server down:
        # bool("false") is True, and a Finish keeps the submit slot forever.
        bad_fin = {"meta": {}, "summary": "", "comments": [], "finished": "false"}
        req = _urlrequest.Request(
            f"{url}submit", data=json.dumps(bad_fin).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: a non-boolean finished returns 400", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: a non-boolean finished returns 400", e.code == 400, e.code)
        check("server: a rejected non-boolean finished leaves the server alive",
              proc.poll() is None, proc.poll())

        # round 3: Finish -> finished true, server shuts down
        payload3 = {"meta": {}, "summary": "", "comments": [], "finished": True}
        req = _urlrequest.Request(
            f"{url}submit", data=json.dumps(payload3).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: threads-mode Finish round returns 200", resp.status == 200, resp.status)
        try:
            rc = proc.wait(timeout=5)
            check("server: threads-mode Finish shuts the server down", rc == 0, rc)
        except _subprocess.TimeoutExpired:
            bad("server: threads-mode Finish shuts the server down", "did not exit within 5s")
        with open(th_out) as f:
            written3 = json.load(f)
        check("server: threads-mode Finish --out sets finished true", written3.get("finished") is True, written3)
        check("server: threads-mode Finish --out carries round 3", written3.get("round") == 3, written3)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    if os.path.exists(th_out):
        os.unlink(th_out)

# -- threads mode: GET /threads, POST /reply, POST /resolve, threads_rev -----
ep_fd, ep_out = _tempfile.mkstemp(suffix=".json")
os.close(ep_fd)
os.unlink(ep_out)
proc, patch_path = start_server(["--out", ep_out])  # stay-alive: threads mode (--out, no --once)
try:
    url = read_url(proc)
    check("server: threads-endpoints run starts", bool(url), url)
    if url:
        def post_json(route, obj):
            req = _urlrequest.Request(
                f"{url}{route}", data=json.dumps(obj).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with _urlrequest.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())

        def get_json(route):
            with _urlrequest.urlopen(f"{url}{route}", timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())

        payload = {"meta": {}, "summary": "", "comments": [
            {"file": "a.py", "side": "R", "line": 1, "code": "x", "text": "first"},
            {"file": "a.py", "side": "R", "line": 2, "code": "y", "text": "second"},
        ]}
        status, info = post_json("submit", payload)
        check("server: threads-endpoints submit returns 200", status == 200, status)
        check("server: threads-endpoints submit mints ids t1, t2", info.get("ids") == ["t1", "t2"], info)

        status, state0 = get_json("state")
        check("server: GET /state in threads mode carries threads_rev", "threads_rev" in state0, state0)
        rev0 = state0.get("threads_rev")
        check("server: threads_rev after one submit is 1", rev0 == 1, state0)

        status, before = get_json("threads")
        check("server: GET /threads returns 200", status == 200, status)
        check("server: GET /threads carries threads_rev, round, threads",
              set(["threads_rev", "round", "threads"]) <= set(before.keys()), before)
        check("server: GET /threads round matches the one submitted round", before.get("round") == 1, before)
        check("server: GET /threads has 2 threads, both unresolved with no replies",
              len(before.get("threads", [])) == 2
              and all(t.get("resolved") is False and t.get("replies") == [] for t in before["threads"]),
              before)

        # a. reply round-trip
        status, r1 = post_json("reply", {"thread_id": "t1", "author": "agent", "text": "Fixed in place."})
        check("server: POST /reply returns 200", status == 200, status)
        check("server: POST /reply response carries ok and threads_rev", r1.get("ok") is True, r1)
        check("server: POST /reply bumps threads_rev", r1.get("threads_rev") == rev0 + 1, (rev0, r1))

        status, after = get_json("threads")
        t1 = next(t for t in after["threads"] if t["id"] == "t1")
        check("server: GET /threads shows the new reply", len(t1["replies"]) == 1, t1)
        reply = t1["replies"][0]
        check("server: reply carries author 'agent'", reply.get("author") == "agent", reply)
        check("server: reply carries the text", reply.get("text") == "Fixed in place.", reply)
        check("server: reply carries an integer ts", isinstance(reply.get("ts"), int), reply)
        check("server: GET /threads threads_rev matches /reply's response",
              after.get("threads_rev") == r1["threads_rev"], (after, r1))

        # b. resolve round-trip
        status, res1 = post_json("resolve", {"thread_id": "t1", "resolved": True})
        check("server: POST /resolve (true) returns 200", status == 200, status)
        check("server: POST /resolve (true) bumps threads_rev",
              res1.get("threads_rev") == r1["threads_rev"] + 1, (r1, res1))
        _, after_resolve = get_json("threads")
        t1r = next(t for t in after_resolve["threads"] if t["id"] == "t1")
        check("server: GET /threads shows resolved true", t1r.get("resolved") is True, t1r)

        status, res2 = post_json("resolve", {"thread_id": "t1", "resolved": False})
        check("server: POST /resolve (false) returns 200", status == 200, status)
        check("server: POST /resolve (false) bumps threads_rev again",
              res2.get("threads_rev") == res1["threads_rev"] + 1, (res1, res2))
        _, after_reopen = get_json("threads")
        t1o = next(t for t in after_reopen["threads"] if t["id"] == "t1")
        check("server: GET /threads shows resolved false after reopen", t1o.get("resolved") is False, t1o)

        # e. unknown thread_id -> 404; empty text -> 400
        try:
            post_json("reply", {"thread_id": "t999", "author": "agent", "text": "nope"})
            bad("server: POST /reply with an unknown thread_id returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /reply with an unknown thread_id returns 404", e.code == 404, e.code)

        try:
            post_json("resolve", {"thread_id": "t999", "resolved": True})
            bad("server: POST /resolve with an unknown thread_id returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /resolve with an unknown thread_id returns 404", e.code == 404, e.code)

        try:
            post_json("reply", {"thread_id": "t1", "author": "agent", "text": "   "})
            bad("server: POST /reply with empty text returns 400", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /reply with empty text returns 400", e.code == 400, e.code)

        try:
            post_json("reply", {"thread_id": "t1", "author": "bystander", "text": "hi"})
            bad("server: POST /reply with an invalid author returns 400", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /reply with an invalid author returns 400", e.code == 400, e.code)

        # resolved must be a JSON boolean: bool("false") is True, so coercion
        # would silently resolve a thread on a mistyped value.
        try:
            post_json("resolve", {"thread_id": "t1", "resolved": "false"})
            bad("server: POST /resolve with a non-boolean resolved returns 400", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /resolve with a non-boolean resolved returns 400", e.code == 400, e.code)
        _, still = get_json("threads")
        t1s = next(t for t in still["threads"] if t["id"] == "t1")
        check("server: a rejected non-boolean resolve leaves the bit unchanged", t1s.get("resolved") is False, t1s)

        # f. threads_rev keeps climbing across a second submit
        status, info2 = post_json("submit", {"meta": {}, "summary": "", "comments": []})
        check("server: threads-endpoints second submit returns 200", status == 200, status)
        _, state1 = get_json("state")
        check("server: threads_rev increments again on a second submit",
              state1.get("threads_rev") == res2["threads_rev"] + 1, (res2, state1))

        # c. /reply 404s without a valid token or cookie
        host, port, host_port, token_path = url_parts(url)
        base = f"http://{host_port}"
        req = _urlrequest.Request(
            f"{base}/reply", data=b'{"thread_id":"t1","author":"agent","text":"x"}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: POST /reply with no token and no cookie returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /reply with no token and no cookie returns 404", e.code == 404, e.code)
        req = _urlrequest.Request(
            f"{base}/not-the-token/reply", data=b'{"thread_id":"t1","author":"agent","text":"x"}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: POST /<wrong-token>/reply returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /<wrong-token>/reply returns 404", e.code == 404, e.code)

        # d. /reply rejects a cross-origin Origin, matching /submit's gate
        req = _urlrequest.Request(
            f"{url}reply", data=b'{"thread_id":"t1","author":"agent","text":"x"}',
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: POST /reply with a cross-origin Origin returns 403", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /reply with a cross-origin Origin returns 403", e.code == 403, e.code)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    if os.path.exists(ep_out):
        os.unlink(ep_out)

# -- thread endpoints exist only in threads mode; 404 elsewhere --------------
once_ep_fd, once_ep_out = _tempfile.mkstemp(suffix=".json")
os.close(once_ep_fd)
os.unlink(once_ep_out)
proc, patch_path = start_server(["--once", "--out", once_ep_out])
try:
    url = read_url(proc)
    check("server: one-shot run for endpoint-gating starts", bool(url), url)
    if url:
        req = _urlrequest.Request(
            f"{url}reply", data=b'{"thread_id":"t1","author":"agent","text":"x"}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: POST /reply in one-shot mode returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /reply in one-shot mode returns 404", e.code == 404, e.code)
        try:
            _urlrequest.urlopen(f"{url}threads", timeout=5)
            bad("server: GET /threads in one-shot mode returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: GET /threads in one-shot mode returns 404", e.code == 404, e.code)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    if os.path.exists(once_ep_out):
        os.unlink(once_ep_out)

human_ep_dir = _tempfile.mkdtemp(prefix="lr-humanonly-ep-")
proc, patch_path = start_server([], cwd=human_ep_dir)
try:
    url = read_url(proc)
    check("server: human-only run for endpoint-gating starts", bool(url), url)
    if url:
        req = _urlrequest.Request(
            f"{url}reply", data=b'{"thread_id":"t1","author":"agent","text":"x"}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: POST /reply in human-only mode returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /reply in human-only mode returns 404", e.code == 404, e.code)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    _shutil.rmtree(human_ep_dir, ignore_errors=True)

# -- security: path token gates every route; Origin/Sec-Fetch-Site gate POSTs
sec_fd, sec_out = _tempfile.mkstemp(suffix=".json")
os.close(sec_fd)
os.unlink(sec_out)
proc, patch_path = start_server(["--out", sec_out])  # stay-alive: threads mode (--out, no --once); several requests in sequence
try:
    url = read_url(proc)
    check("server: security-test run starts", bool(url), url)
    if url:
        host, port, host_port, token_path = url_parts(url)
        base = f"http://{host_port}"

        try:
            _urlrequest.urlopen(f"{base}/", timeout=5)
            bad("server: GET / (no token) returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: GET / (no token) returns 404", e.code == 404, e.code)

        try:
            _urlrequest.urlopen(f"{base}/not-the-token/", timeout=5)
            bad("server: GET /<wrong-token>/ returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: GET /<wrong-token>/ returns 404", e.code == 404, e.code)

        with open_token_url(url) as resp:
            body = resp.read().decode()
            check("server: GET tokenized URL returns 200", resp.status == 200, resp.status)
            check("server: GET tokenized URL body contains 'Submit review'", "Submit review" in body, "")

        token = token_path.strip("/")
        cookie_name = f"local_review_{port}"

        class _NoRedirectCookie(_urlrequest.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener_nr = _urlrequest.build_opener(_NoRedirectCookie)
        try:
            opener_nr.open(url, timeout=5)
            bad("server: GET /<token>/ (redirects not followed) returns 302", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            set_cookie = e.headers.get("Set-Cookie", "")
            check("server: GET /<token>/ (redirects not followed) returns 302", e.code == 302, e.code)
            check("server: GET /<token>/ redirect Location is exactly /",
                  e.headers.get("Location", "") == "/", e.headers.get("Location"))
            check("server: GET /<token>/ Set-Cookie carries the port-named cookie and the token",
                  f"{cookie_name}={token}" in set_cookie, set_cookie)
            check("server: GET /<token>/ Set-Cookie is HttpOnly", "HttpOnly" in set_cookie, set_cookie)
            check("server: GET /<token>/ Set-Cookie is SameSite=Strict", "SameSite=Strict" in set_cookie, set_cookie)

        req = _urlrequest.Request(f"{base}/", headers={"Cookie": f"{cookie_name}={token}"})
        with _urlrequest.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
            check("server: GET / with the session cookie returns 200", resp.status == 200, resp.status)
            check("server: GET / with the session cookie body contains 'Submit review'",
                  "Submit review" in body, "")

        req = _urlrequest.Request(f"{base}/", headers={"Cookie": f"{cookie_name}=not-the-token"})
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: GET / with a wrong-value cookie returns 404", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: GET / with a wrong-value cookie returns 404", e.code == 404, e.code)

        # Once the page lands on the bare /, every request it makes travels the
        # cookie branch, not the token prefix — so the routes it actually calls
        # have to be asserted there too, or that branch can regress on
        # everything except the page root while this suite stays green.
        req = _urlrequest.Request(f"{base}/state", headers={"Cookie": f"{cookie_name}={token}"})
        with _urlrequest.urlopen(req, timeout=5) as resp:
            state_body = json.loads(resp.read().decode())
            check("server: GET /state with the session cookie returns 200", resp.status == 200, resp.status)
            check("server: GET /state with the session cookie returns a sig", "sig" in state_body, state_body)

        req = _urlrequest.Request(f"{base}/vendor/marked.umd.js",
                                  headers={"Cookie": f"{cookie_name}={token}"})
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: GET /vendor/*.js with the session cookie returns 200", resp.status == 200, resp.status)
            check("server: GET /vendor/*.js with the session cookie returns the asset",
                  len(resp.read()) > 0, "")

        # slashless token alias must redirect, not serve a page whose relative
        # asset/fetch URLs resolve outside the token prefix
        class _NoRedirect(_urlrequest.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = _urlrequest.build_opener(_NoRedirect)
        try:
            opener.open(url.rstrip("/"), timeout=5)
            bad("server: GET /<token> (no slash) redirects to /<token>/", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: GET /<token> (no slash) redirects to /<token>/",
                  e.code == 301 and e.headers.get("Location", "") == token_path,
                  (e.code, e.headers.get("Location")))

        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"sfs","comments":[]}',
            headers={"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: POST /submit with Sec-Fetch-Site cross-site returns 403", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /submit with Sec-Fetch-Site cross-site returns 403", e.code == 403, e.code)
        check("server: Sec-Fetch-Site cross-site POST does not write OUT", not os.path.exists(sec_out), sec_out)

        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"evil","comments":[]}',
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: POST /submit with a cross-origin Origin returns 403", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /submit with a cross-origin Origin returns 403", e.code == 403, e.code)
        check("server: cross-origin POST does not write OUT", not os.path.exists(sec_out), sec_out)

        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"noorigin","comments":[]}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: POST /submit with no Origin header returns 200", resp.status == 200, resp.status)
        check("server: OUT written after no-Origin submit",
              os.path.exists(sec_out) and json.load(open(sec_out)).get("summary") == "noorigin", sec_out)
        os.unlink(sec_out)

        same_origin = f"http://127.0.0.1:{port}"
        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"sameorigin","comments":[]}',
            headers={"Content-Type": "application/json", "Origin": same_origin}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: POST /submit with a matching Origin returns 200", resp.status == 200, resp.status)
        check("server: OUT written after same-origin submit",
              os.path.exists(sec_out) and json.load(open(sec_out)).get("summary") == "sameorigin", sec_out)
        os.unlink(sec_out)

        vanity_origin = f"http://review.localhost:{port}"
        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"vanity","comments":[]}',
            headers={"Content-Type": "application/json", "Origin": vanity_origin}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: POST /submit with Origin review.localhost returns 200", resp.status == 200, resp.status)
        check("server: OUT written after review.localhost-origin submit",
              os.path.exists(sec_out) and json.load(open(sec_out)).get("summary") == "vanity", sec_out)
        os.unlink(sec_out)

        evil_vanity_origin = f"http://evil.localhost:{port}"
        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"evilvanity","comments":[]}',
            headers={"Content-Type": "application/json", "Origin": evil_vanity_origin}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: POST /submit with Origin evil.localhost returns 403", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            check("server: POST /submit with Origin evil.localhost returns 403", e.code == 403, e.code)
        check("server: evil.localhost-origin POST does not write OUT", not os.path.exists(sec_out), sec_out)

        # The page submits from the bare /, so /submit has to authorize off the
        # cookie too. Sits after the Origin cases above because it writes OUT,
        # and those assert OUT is still absent.
        req = _urlrequest.Request(
            f"{base}/submit", data=b'{"meta":{},"summary":"cookiepath","comments":[]}',
            headers={"Content-Type": "application/json", "Cookie": f"{cookie_name}={token}"},
            method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: POST /submit with the session cookie returns 200", resp.status == 200, resp.status)
        check("server: OUT written after a cookie-authorized submit",
              os.path.exists(sec_out) and json.load(open(sec_out)).get("summary") == "cookiepath", sec_out)
        os.unlink(sec_out)

        # Vendor path traversal, sent as a raw request line so dot-segments
        # reach the server unnormalized (urllib normalizes them client-side
        # before the request ever goes out, so a urlopen() call can't exercise
        # this path).
        raw = (b"GET " + (token_path + "vendor/../../server.py").encode() + b" HTTP/1.1\r\nHost: "
               + host_port.encode() + b"\r\nConnection: close\r\n\r\n")
        s = _socket.create_connection((host, port), timeout=5)
        s.sendall(raw)
        s.settimeout(5)
        resp_bytes = b""
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp_bytes += chunk
        except _socket.timeout:
            pass
        s.close()
        status_line = resp_bytes.split(b"\r\n", 1)[0].decode(errors="replace")
        check("server: GET vendor path traversal (../..) returns 404", " 404 " in status_line, status_line)

        # Port isolation: server1's cookie is named for server1's port, so it
        # must not authorize a second review running on a different port.
        proc_b, patch_b = start_server([])
        try:
            url_b = read_url(proc_b)
            check("server: second server for port-isolation test starts", bool(url_b), url_b)
            if url_b:
                host_b, port_b, host_port_b, _ = url_parts(url_b)
                req = _urlrequest.Request(
                    f"http://{host_port_b}/", headers={"Cookie": f"{cookie_name}={token}"},
                )
                try:
                    _urlrequest.urlopen(req, timeout=5)
                    bad("server: server1's cookie does not authorize server2's /", "request unexpectedly succeeded")
                except _urlerror.HTTPError as e:
                    check("server: server1's cookie does not authorize server2's /", e.code == 404, e.code)
        finally:
            if proc_b.poll() is None:
                proc_b.terminate()
                try:
                    proc_b.wait(timeout=5)
                except _subprocess.TimeoutExpired:
                    proc_b.kill()
                    proc_b.wait(timeout=5)
            os.unlink(patch_b)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    if os.path.exists(sec_out):
        os.unlink(sec_out)

# -- --out pointing at an existing DIRECTORY: preflight must 500, not post ---
# (--out with no --once: threads mode, stays alive)
dir_out = _tempfile.mkdtemp(prefix="lr-isdir-")
proc, patch_path = start_server(["--out", dir_out])
try:
    url = read_url(proc)
    check("server: dir-out run still starts", bool(url), url)
    if url:
        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"","comments":[]}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5)
            bad("server: --out as existing directory returns 500", "request unexpectedly succeeded")
        except _urlerror.HTTPError as e:
            body = json.loads(e.read().decode())
            check("server: --out as existing directory returns 500", e.code == 500, e.code)
            check("server: --out as existing directory names the problem",
                  body.get("ok") is False and "cannot write --out" in body.get("error", ""), body)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    _shutil.rmtree(dir_out, ignore_errors=True)

# -- mode derivation: no --out, no --once => human-only, stays alive --------
# writes the default pr_comments.json (relative to the server's cwd) on
# submit, exactly like today's hand-run flow.
human_only_dir = _tempfile.mkdtemp(prefix="lr-humanonly-")
human_only_out = os.path.join(human_only_dir, "pr_comments.json")
proc, patch_path = start_server([], cwd=human_only_dir)
try:
    url = read_url(proc)
    check("server: human-only run starts", bool(url), url)
    if url:
        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"human-only","comments":[]}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: human-only POST /submit returns 200", resp.status == 200, resp.status)
        check("server: human-only submit writes pr_comments.json in the server's cwd",
              os.path.exists(human_only_out), human_only_out)
        if os.path.exists(human_only_out):
            with open(human_only_out) as f:
                written = json.load(f)
            check("server: human-only pr_comments.json carries the submitted summary",
                  written.get("summary") == "human-only", written)
            check("server: human-only pr_comments.json carries no round, threads, or finished key",
                  "round" not in written and "threads" not in written and "finished" not in written,
                  written)
        check("server: human-only mode stays alive after a submit", proc.poll() is None, proc.poll())
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    _shutil.rmtree(human_only_dir, ignore_errors=True)

# -- mode derivation: --once with no --out => still exits, still writes ------
# pr_comments.json (the default out path, relative to the server's cwd).
once_no_out_dir = _tempfile.mkdtemp(prefix="lr-oncenoout-")
once_no_out = os.path.join(once_no_out_dir, "pr_comments.json")
proc, patch_path = start_server(["--once"], cwd=once_no_out_dir)
try:
    url = read_url(proc)
    check("server: --once-no-out run starts", bool(url), url)
    if url:
        req = _urlrequest.Request(
            f"{url}submit", data=b'{"meta":{},"summary":"once-no-out","comments":[]}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _urlrequest.urlopen(req, timeout=5) as resp:
            check("server: --once-no-out POST /submit returns 200", resp.status == 200, resp.status)
        try:
            rc = proc.wait(timeout=5)
            check("server: --once with no --out still exits after a successful /submit", rc == 0, rc)
        except _subprocess.TimeoutExpired:
            bad("server: --once with no --out still exits after a successful /submit",
                "did not exit within 5s")
        check("server: --once with no --out still writes pr_comments.json",
              os.path.exists(once_no_out), once_no_out)
        if os.path.exists(once_no_out):
            with open(once_no_out) as f:
                written = json.load(f)
            check("server: --once-no-out pr_comments.json carries the submitted summary",
                  written.get("summary") == "once-no-out", written)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    os.unlink(patch_path)
    _shutil.rmtree(once_no_out_dir, ignore_errors=True)

# -- PR-mode startup with gh unavailable: the RuntimeError from sh() must ----
# propagate to main()'s handler (a one-line stderr error, exit 1) instead of
# resolve_gh/get_meta swallowing it and starting degraded (gh=None).
fake_gh_dir = make_fake_gh_dir()
gh_fail_env = dict(os.environ)
gh_fail_env["PATH"] = fake_gh_dir + os.pathsep + os.environ.get("PATH", "")
pr_out_fd, pr_out_path = _tempfile.mkstemp(suffix=".json")
os.close(pr_out_fd)
os.unlink(pr_out_path)
proc = _subprocess.Popen(
    [sys.executable, server_path, "999999", "--out", pr_out_path],
    stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True, env=gh_fail_env,
)
try:
    try:
        rc = proc.wait(timeout=5)
    except _subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        rc = None
    output = proc.stdout.read()
    check("server: PR-mode startup with gh unavailable exits 1 (not starting degraded)",
          rc == 1, rc)
    lines = [ln for ln in output.splitlines() if ln.strip()]
    check("server: PR-mode startup with gh unavailable prints a one-line stderr error, no traceback",
          len(lines) == 1 and lines[0].startswith("error:") and "Traceback" not in output, output)
    check("server: PR-mode startup with gh unavailable never serves (no LOCAL_REVIEW_URL)",
          "LOCAL_REVIEW_URL=" not in output, output)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    _shutil.rmtree(fake_gh_dir, ignore_errors=True)
    if os.path.exists(pr_out_path):
        os.unlink(pr_out_path)

# -- PR-mode startup where `gh pr diff` succeeds but `gh pr view` fails: the -
# propagation being pinned is specifically get_meta/resolve_gh's — get_diff
# succeeding must not let a later gh failure start the server degraded.
fake_gh_dir_v = make_fake_gh_dir(diff_ok=True)
gh_view_env = dict(os.environ)
gh_view_env["PATH"] = fake_gh_dir_v + os.pathsep + os.environ.get("PATH", "")
proc = _subprocess.Popen(
    [sys.executable, server_path, "999999"],
    stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True, env=gh_view_env,
)
try:
    try:
        rc = proc.wait(timeout=5)
    except _subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        rc = None
    output = proc.stdout.read()
    check("server: gh-view failure after a good diff exits 1 (get_meta/resolve_gh propagate)",
          rc == 1, rc)
    check("server: gh-view failure never serves (no LOCAL_REVIEW_URL)",
          "LOCAL_REVIEW_URL=" not in output and "Traceback" not in output, output)
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    _shutil.rmtree(fake_gh_dir_v, ignore_errors=True)

# -- the server never issues a gh WRITE, even for a github-flagged comment ----
# The page can reach /submit, so anything this handler can do a web page the
# user has open can cause — and a `gh` post is not undoable. The button records
# intent; the agent posts. This asserts the capability is gone rather than
# merely unused: a gh stand-in logs every invocation, PR mode runs for real, a
# comment flagged github:true is submitted, and the log must contain no write.
gh_log_dir = _tempfile.mkdtemp(prefix="ghlog-")
gh_log = os.path.join(gh_log_dir, "calls.log")
with open(os.path.join(gh_log_dir, "gh"), "w") as f:
    f.write(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "' + gh_log + '"\n'
        'if [ "$1" = pr ] && [ "$2" = diff ]; then\n'
        "  printf 'diff --git a/n.md b/n.md\\nnew file mode 100644\\n--- /dev/null\\n+++ b/n.md\\n@@ -0,0 +1,1 @@\\n+hi\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = pr ] && [ "$2" = view ]; then\n'
        '  printf \'{"url":"https://github.com/o/r/pull/7","number":7,"headRefOid":"deadbeef","title":"T"}\'\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n")
os.chmod(os.path.join(gh_log_dir, "gh"), 0o755)
gh_log_env = dict(os.environ)
gh_log_env["PATH"] = gh_log_dir + os.pathsep + os.environ.get("PATH", "")

nw_fd, nw_out = _tempfile.mkstemp(suffix=".json")
os.close(nw_fd)
os.unlink(nw_out)
nw_proc = _subprocess.Popen(
    [sys.executable, server_path, "7", "--once", "--out", nw_out],
    stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True, env=gh_log_env,
)
try:
    nw_url = read_url(nw_proc)
    check("no-write: PR-mode server starts against the logging gh stub", bool(nw_url), nw_url)
    if nw_url:
        nw_payload = {"summary": "s", "comments": [
            {"file": "n.md", "side": "R", "line": 1, "code": "hi",
             "text": "post me", "github": True}]}
        req = _urlrequest.Request(
            f"{nw_url}submit", data=json.dumps(nw_payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with _urlrequest.urlopen(req, timeout=5) as resp:
            nw_body = json.loads(resp.read().decode())
        check("no-write: /submit reports the comment as flagged, not posted",
              nw_body.get("github_flagged") == 1
              and "github_posted" not in nw_body
              and "github_failed" not in nw_body, nw_body)
        deadline = _time.time() + 5
        while _time.time() < deadline and not (os.path.exists(nw_out) and os.path.getsize(nw_out)):
            _time.sleep(0.05)
        written = json.load(open(nw_out)) if os.path.exists(nw_out) else {}
        check("no-write: the payload keeps github:true so the agent knows what to post",
              written.get("comments", [{}])[0].get("github") is True, written)
        check("no-write: the payload carries no github_posted/github_failed",
              "github_posted" not in written and "github_failed" not in written, list(written))
        calls = open(gh_log).read() if os.path.exists(gh_log) else ""
        check("no-write: gh was invoked (read path is intact)", bool(calls.strip()), calls)
        # Per LINE, not over the whole blob: `calls` is one invocation per line,
        # so a substring test like `" api " in (" " + calls)` only ever gets the
        # leading boundary right for the FIRST line — every later `api …` sits
        # after a newline and never matches. That clause silently never fired.
        gh_writes = [ln for ln in calls.splitlines()
                     if "--method POST" in ln or "api" in ln.split()]
        check("no-write: NO gh invocation was a write", gh_writes == [], gh_writes)
        _nw_fd = nw_proc.stdout.fileno()
        os.set_blocking(_nw_fd, False)
        _nw_stdout = ""
        _nw_deadline = _time.time() + 2
        while _time.time() < _nw_deadline:
            try:
                _chunk = os.read(_nw_fd, 4096)
            except BlockingIOError:
                _time.sleep(0.05)
                continue
            if not _chunk:
                break
            _nw_stdout += _chunk.decode(errors="replace")
        check("no-write: stdout prints REVIEW SUBMITTED after a submit",
              "REVIEW SUBMITTED" in _nw_stdout, _nw_stdout)
        check("no-write: stdout carries no APPROVED: line after a submit",
              "APPROVED:" not in _nw_stdout, _nw_stdout)
finally:
    if nw_proc.poll() is None:
        nw_proc.terminate()
        try:
            nw_proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            nw_proc.kill()
            nw_proc.wait(timeout=5)
    _shutil.rmtree(gh_log_dir, ignore_errors=True)
    if os.path.exists(nw_out):
        os.unlink(nw_out)

# -- --diff-file run still works with gh absent/failing: resolve_gh/get_meta -
# short-circuit on diff_file before ever calling sh(), so this path must be
# unaffected by gh being broken.
fake_gh_dir2 = make_fake_gh_dir()
gh_fail_env2 = dict(os.environ)
gh_fail_env2["PATH"] = fake_gh_dir2 + os.pathsep + os.environ.get("PATH", "")
proc2, patch2 = start_server(["--once"], env=gh_fail_env2)
try:
    url2 = read_url(proc2)
    check("server: --diff-file run still works with gh absent/failing (fallback preserved)",
          bool(url2), url2)
finally:
    if proc2.poll() is None:
        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc2.kill()
            proc2.wait(timeout=5)
    os.unlink(patch2)
    _shutil.rmtree(fake_gh_dir2, ignore_errors=True)

# -- --git mode: throwaway git fixture repos. Pin GIT_CONFIG_GLOBAL/SYSTEM to
# /dev/null and an explicit `git init -b main` so the fixture cannot inherit
# the developer machine's global git config (a global core.hooksPath
# pre-commit that refuses commits to main is exactly what a fresh fixture
# looks like, and would otherwise fail this suite on such a machine while
# passing in CI).
def make_git_fixture():
    d = _tempfile.mkdtemp(prefix="lr-gitfixture-")
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"

    def git(*args):
        r = _subprocess.run(["git"] + list(args), cwd=d, env=env,
                             capture_output=True, text=True)
        assert r.returncode == 0, (args, r.stdout, r.stderr)
        return r.stdout

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    with open(os.path.join(d, "file.txt"), "w") as f:
        f.write("line one\nline two\nline three\n")
    git("add", "file.txt")
    git("commit", "-m", "initial")
    return d, env, git


def wait_exit(proc, timeout=5.0):
    try:
        return proc.wait(timeout=timeout)
    except _subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        return None


def stop_proc(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# -- --git uncommitted: server starts on a modified tracked file, page shows -
# it, and /state reports fresh right after startup.
git_dir1, git_env1, git1 = make_git_fixture()
try:
    file_path1 = os.path.join(git_dir1, "file.txt")
    with open(file_path1, "w") as f:
        f.write("line one\nline two CHANGED\nline three\n")
    proc = _subprocess.Popen(
        [sys.executable, server_path, "--git", "uncommitted"],
        stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True,
        cwd=git_dir1, env=git_env1,
    )
    try:
        url = read_url(proc)
        check("git uncommitted: server starts and prints LOCAL_REVIEW_URL", bool(url), url)
        if url:
            with open_token_url(url) as resp:
                body = resp.read().decode()
                check("git uncommitted: page contains the modified file", "file.txt" in body, "")
            with _urlrequest.urlopen(f"{url}state", timeout=5) as resp:
                state = json.loads(resp.read().decode())
                check("git uncommitted: /state reports fresh right after startup",
                      state.get("stale") is False, state)

            # LIVE REFRESH: edit the file again, then use POST /refresh (which
            # always recomputes, unlike the throttled /state poll) for the
            # fastest deterministic check that the new content and a changed
            # sig are picked up live.
            old_sig = state.get("sig")
            with open(file_path1, "w") as f:
                f.write("line one\nline two CHANGED AGAIN\nline three\n")
            req = _urlrequest.Request(f"{url}refresh", data=b"", method="POST")
            with _urlrequest.urlopen(req, timeout=5) as resp:
                refresh_body = json.loads(resp.read().decode())
                check("git uncommitted: POST /refresh returns ok", refresh_body.get("ok") is True, refresh_body)
            with open_token_url(url) as resp:
                body2 = resp.read().decode()
                check("git uncommitted: page after refresh shows the new edit", "CHANGED AGAIN" in body2, "")
            with _urlrequest.urlopen(f"{url}state", timeout=5) as resp:
                state2 = json.loads(resp.read().decode())
                check("git uncommitted: sig changed after refresh picks up the live edit",
                      state2.get("sig") != old_sig, (old_sig, state2.get("sig")))
    finally:
        stop_proc(proc)
finally:
    _shutil.rmtree(git_dir1, ignore_errors=True)

# -- --git <ref> (branch mode): shows the committed diff of the branch vs ----
# the given ref (here main).
git_dir2, git_env2, git2 = make_git_fixture()
try:
    git2("checkout", "-b", "feature")
    with open(os.path.join(git_dir2, "file.txt"), "w") as f:
        f.write("line one\nline two on the feature branch\nline three\n")
    git2("commit", "-am", "feature change")
    proc = _subprocess.Popen(
        [sys.executable, server_path, "--git", "main"],
        stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True,
        cwd=git_dir2, env=git_env2,
    )
    try:
        url = read_url(proc)
        check("git branch mode: server starts and prints LOCAL_REVIEW_URL", bool(url), url)
        if url:
            with open_token_url(url) as resp:
                body = resp.read().decode()
                check("git branch mode: page shows the committed diff vs main",
                      "feature branch" in body, "")
    finally:
        stop_proc(proc)
finally:
    _shutil.rmtree(git_dir2, ignore_errors=True)

# -- --git in a non-repo cwd: sh()'s RuntimeError from the startup pin -------
# propagates as a one-line stderr error, exit 1, no traceback.
nonrepo_dir = _tempfile.mkdtemp(prefix="lr-nonrepo-")
try:
    nonrepo_env = dict(os.environ)
    nonrepo_env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    nonrepo_env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    proc = _subprocess.Popen(
        [sys.executable, server_path, "--git", "uncommitted"],
        stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True,
        cwd=nonrepo_dir, env=nonrepo_env,
    )
    rc = wait_exit(proc)
    output = proc.stdout.read()
    lines = [ln for ln in output.splitlines() if ln.strip()]
    check("git non-repo cwd: exits 1", rc == 1, rc)
    check("git non-repo cwd: one-line stderr error, no traceback",
          len(lines) == 1 and lines[0].startswith("error:") and "Traceback" not in output, output)
finally:
    _shutil.rmtree(nonrepo_dir, ignore_errors=True)

# -- --git with an invalid spec (no such ref): same one-line error contract -
git_dir3, git_env3, git3 = make_git_fixture()
try:
    proc = _subprocess.Popen(
        [sys.executable, server_path, "--git", "no-such-branch-xyz"],
        stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True,
        cwd=git_dir3, env=git_env3,
    )
    rc = wait_exit(proc)
    output = proc.stdout.read()
    lines = [ln for ln in output.splitlines() if ln.strip()]
    check("git invalid spec: exits 1", rc == 1, rc)
    # git's own "unknown revision" fatal carries a multi-line usage hint, but
    # main() flattens it to one line before printing, so the one-line error:
    # contract holds even though the underlying RuntimeError is multiline.
    check("git invalid spec: one-line stderr error, no traceback",
          len(lines) == 1 and lines[0].startswith("error:") and "Traceback" not in output, output)
finally:
    _shutil.rmtree(git_dir3, ignore_errors=True)

# -- exactly-one-input validation: --git and --diff-file together exits 2 ---
patch_fd5, patch_path5 = _tempfile.mkstemp(suffix=".patch")
os.close(patch_fd5)
proc = _subprocess.Popen(
    [sys.executable, server_path, "--diff-file", patch_path5, "--git", "uncommitted"],
    stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True,
)
try:
    rc = wait_exit(proc)
    output = proc.stdout.read()
    check("both --git and --diff-file: exits 2", rc == 2, rc)
    check("both --git and --diff-file: clear one-of-three error message",
          "exactly one" in output, output)
finally:
    os.unlink(patch_path5)

# -- --title: --diff-file with an explicit --title shows that label ---------
proc, patch6 = start_server(["--title", "Custom Label"])
try:
    url = read_url(proc)
    check("title: --diff-file with --title starts", bool(url), url)
    if url:
        with open_token_url(url) as resp:
            body = resp.read().decode()
            check("title: --diff-file page contains the custom --title label",
                  "Custom Label" in body, "")
finally:
    stop_proc(proc)
    os.unlink(patch6)

# -- --title: --git uncommitted with no --title gets the generated default --
git_dir4, git_env4, git4 = make_git_fixture()
try:
    repo_name = os.path.basename(git_dir4)
    proc = _subprocess.Popen(
        [sys.executable, server_path, "--git", "uncommitted"],
        stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True,
        cwd=git_dir4, env=git_env4,
    )
    try:
        url = read_url(proc)
        check("title: --git uncommitted with no --title starts", bool(url), url)
        if url:
            with open_token_url(url) as resp:
                body = resp.read().decode()
                expected = f"uncommitted changes ({repo_name})"
                check("title: --git uncommitted default title names the change and repo dir",
                      expected in body, (expected, body))
    finally:
        stop_proc(proc)
finally:
    _shutil.rmtree(git_dir4, ignore_errors=True)

# --- the page's inline <script> parses -------------------------------------
# PAGE is a Python string, so a broken edit to the client JS is invisible to
# every other assertion here and to mypy — it surfaces only when a human opens
# the browser. `node --check` parses without executing, which is all that is
# needed to catch it. This is a parse check, not a behavior check: nothing here
# exercises the DOM, so render/mode-switch/comment-restoration remain
# eyeball-only.
_node = _shutil.which("node")
if _node is None:
    # mise installs node without putting it on PATH unless a config pins it.
    try:
        _mise = _subprocess.run(["mise", "which", "node"], capture_output=True, text=True)
        if _mise.returncode == 0 and _mise.stdout.strip():
            _node = _mise.stdout.strip()
    except OSError:
        pass

if _node is None:
    skip("page JS parses under node --check", "no node on PATH and none resolvable via mise")
else:
    _page = server.PAGE
    _blocks = _re.findall(r"<script>(.*?)</script>", _page, _re.S)
    check("page JS: exactly one inline <script> block to check",
          len(_blocks) == 1, f"got {len(_blocks)}")
    # No placeholder substitution is needed: the two payload markers are
    # comment-wrapped, and __TITLE__/__TITLE_HTML__ live in the HTML, not in
    # the script. So the block is already valid JS as it stands.
    _js = "\n".join(_blocks)
    # encoding is pinned on both the write and the read. The page JS carries
    # non-ASCII (· × – — … ↻ ⊘ ▾ ✎ ✓), so under a non-UTF-8 locale the write
    # raises UnicodeEncodeError and kills the whole harness, and node's own
    # error output — which echoes the offending source line — comes back as a
    # UnicodeDecodeError exactly when the message matters most.
    _fd, _js_path = _tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(_fd, "w", encoding="utf-8") as _f:
            _f.write(_js)
        _r = _subprocess.run([_node, "--check", _js_path],
                             capture_output=True, encoding="utf-8")
        check("page JS parses under node --check", _r.returncode == 0,
              (_r.stderr or _r.stdout).strip())
    finally:
        os.unlink(_js_path)

# --- no innerHTML assignment takes untrusted content ------------------------
# The page's XSS argument used to be "we escape it". Now it is "we never put it
# in an HTML string at all", which is checkable from the source: none of the
# names that carry diff content or reviewer text may appear inside an
# innerHTML assignment. Issue #385.
_UNTRUSTED = ["a.path", "c.file", "file.display", "h.header", "h.section",
              "c.text", "cell.s", "res.value", "_emitter"]
_dirty = []
for _chunk in server.PAGE.split("innerHTML")[1:]:
    # Only real assignments. The prose above and in the page itself names
    # innerHTML too, and a comment has no expression to bound — the scan would
    # run on into unrelated code and flag it.
    if not _re.match(r"\s*=[^=]", _chunk):
        continue
    # An assignment ends at the first statement terminator that closes the
    # expression: a backtick, quote, or paren immediately before the `;`.
    _end = min([i for i in (_chunk.find("`;"), _chunk.find("';"), _chunk.find('";'))
                if i != -1] or [len(_chunk)])
    _expr = _chunk[:_end]
    _dirty += [f"{n}: {_expr.strip()[:70]}" for n in _UNTRUSTED if n in _expr]
check("no innerHTML assignment interpolates diff content or reviewer text",
      _dirty == [], _dirty)
# The escaper is gone with the last of its callers. Its presence would mean an
# interpolation somewhere still depends on it being correct.
check("the page carries no HTML-escaping helper any more",
      "const esc =" not in server.PAGE and "function esc(" not in server.PAGE,
      "an esc() helper is back — something is interpolating again")
# --- no verdict: the finish dialog has one button, not an approve verdict ---
with open(server_path) as _f:
    _server_src = _f.read()
_verdict_hits = [s for s in ("finishApprove", ">Approve<", "Approved ✓") if s in _server_src]
check("server.py carries no Approve button or verdict string",
      _verdict_hits == [], _verdict_hits)

# --- preview: describe() against a security corpus --------------------------
# describe() is the whole XSS argument for preview mode: it turns marked's
# token stream into a plain description tree, and materialize() adds no logic.
# So asserting on describe()'s OUTPUT is asserting the security property, and
# it needs no DOM — which matters because jsdom is unreachable here.
_PURE_BEGIN = "// >>> PURE-PREVIEW-BEGIN"
_PURE_END = "// <<< PURE-PREVIEW-END"
_marked_js = os.path.join(os.path.dirname(server_path), "vendor", "marked.umd.js")
_hljs_js = os.path.join(os.path.dirname(server_path), "vendor", "highlight.min.js")

if _node is None:
    skip("preview: describe() security corpus", "no node resolvable")
elif not os.path.exists(_marked_js):
    skip("preview: describe() security corpus", "vendor/marked.umd.js not present")
elif not os.path.exists(_hljs_js):
    skip("preview: describe() security corpus", "vendor/highlight.min.js not present")
elif _PURE_BEGIN not in server.PAGE or _PURE_END not in server.PAGE:
    bad("preview: the PURE-PREVIEW markers are present in PAGE",
        "markers missing — the describe() corpus cannot be sliced out")
else:
    # Drop the rest of the marker's own line: the split eats its "//", which
    # would otherwise leave the marker's trailing prose as bare code.
    _pure = server.PAGE.split(_PURE_BEGIN, 1)[1].split("\n", 1)[1].split(_PURE_END, 1)[0]
    # Strip // comments before looking: the region's own comment warns against
    # `document.` references, and a naive substring test matches that warning.
    _code_only = "\n".join(_re.sub(r"//.*$", "", ln) for ln in _pure.split("\n"))
    check("preview: the sliced region touches no DOM",
          "document." not in _code_only and "window." not in _code_only,
          [ln.strip() for ln in _code_only.split("\n") if "document." in ln or "window." in ln])

    _harness = r"""
const g = {};
Object.defineProperty(globalThis, 'self', {value: g, configurable: true});
// The UMD takes its CommonJS branch here, so it exports rather than attaching
// to self. Take whichever it used, and fail loudly if neither worked —
// otherwise describe() returns null for everything and the corpus "passes".
const req = require(process.argv[2]);
const marked = g.marked || req;
if(!marked || typeof marked.lexer !== 'function'){
  console.error('marked did not load: no lexer'); process.exit(2);
}
// The hljs bundle is an IIFE assigning `var hljs`, so it neither exports nor
// attaches to self. Run it and take its own binding, as a real global — the
// sliced region reaches it by bare name and guards on `typeof hljs`, so a
// silent miss would make every highlight assertion below vacuously pass.
globalThis.hljs = new Function(
  require('fs').readFileSync(process.argv[3], 'utf8') + '\n;return hljs;')();
if(!globalThis.hljs || typeof globalThis.hljs.highlight !== 'function'){
  console.error('hljs did not load: no highlight'); process.exit(2);
}
__PURE__
const out = {};
const walk = (n, f) => { f(n); (n.kids || []).forEach(k => walk(k, f)); };

// Every tag describe() emits, and every attribute name, over the whole corpus.
const tags = new Set(), attrs = new Set(), texts = [];
const CORPUS = {
  rawhtml:  '<img src=x onerror=alert(1)>\n\n<script>alert(2)</script>\n',
  jsHref:   '[a](javascript:alert(1)) [b](JaVaScRiPt:alert(2)) [c](  javascript:alert(3))\n',
  dataHref: '[d](data:text/html;base64,PHNjcmlwdD4=)\n',
  relHref:  '[e](../submit) [f](/x/y) [g](sneaky.html)\n',
  protoRel: '[h](//evil.test/x)\n',
  okHref:   '[i](https://ok.test/p?q=1) [j](mailto:a@b.test)\n',
  image:    '![alt](https://evil.test/track.png)\n',
  proto:    '---\n__proto__: polluted\nconstructor: x\n---\n\n# H\n',
  table:    '| a | b |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |\n',
  deep:     '> '.repeat(60) + 'deep\n',
  unknown:  '$$math$$\n\n:::note\nx\n:::\n',
  code:     '```js\nalert(1)\n```\n',
};
for(const [name, src] of Object.entries(CORPUS)){
  const d = describe(src);
  out[name] = d === null ? null : true;
  if(d) walk(d, n => {
    tags.add(n.tag);
    Object.keys(n.attrs || {}).forEach(a => attrs.add(a));
    if(n.attrs && n.attrs.href) texts.push('HREF:' + n.attrs.href);
    if(n.text) texts.push(n.text);
  });
}
out.tags = [...tags].sort();
out.attrs = [...attrs].sort();
out.tagsOutsideAllowlist = out.tags.filter(t => SAFE_TAGS.indexOf(t) === -1);
out.hrefs = texts.filter(t => t.startsWith('HREF:')).map(t => t.slice(5)).sort();
out.protoPolluted = ({}).polluted !== undefined || Object.prototype.polluted !== undefined;

// Anchoring: CRLF must not drift, and table rows get their own line.
const crlf = describe('# One\r\n\r\nTwo\r\n\r\nThree\r\n');
out.crlfLines = (crlf.kids || []).map(k => k.line);
const tbl = describe('intro\n\n| a | b |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |\n');
const rows = [];
walk(tbl, n => { if(n.tag === 'tr' && n.line != null) rows.push(n.line); });
out.tableRowLines = rows;
const fm = describe('---\nk: v\n---\n\n# After\n');
const heads = [];
walk(fm, n => { if(/^h[1-6]$/.test(n.tag)) heads.push(n.line); });
out.headingAfterFrontmatter = heads;

// Size cap, and that it is measured in UTF-8 BYTES: this string is well under
// the cap by .length but over it once encoded.
out.overCap = describe('x'.repeat(PREVIEW_MAX_BYTES + 1));
out.overCapMultibyte = describe('中'.repeat(Math.floor(PREVIEW_MAX_BYTES / 2)));
out.underCapMultibyte = describe('中'.repeat(1000)) === null;

// Nothing in the file may be invisible in the rendering. A rejected link's
// title used to be dropped on exactly the hostile branch.
const seen = src => { const t = []; walk(describe(src), n => { if(n.text) t.push(n.text); }); return t.join('|'); };
out.rejectedLinkTitle = seen('[x](javascript:alert(1) "HIDDEN_T")').includes('HIDDEN_T');
out.okLinkTitle = seen('[x](https://ok.test "SHOWN_T")').includes('SHOWN_T');
out.imageTitle = seen('![alt](https://e.test/i.png "IMG_T")').includes('IMG_T');
out.refDefTitle = seen('[r]: https://r.test "REF_T"\n').includes('REF_T');
// Deep nesting must degrade to visible raw text, not vanish.
out.deepInlineText = seen('*'.repeat(40) + 'DEEP_TXT' + '*'.repeat(40)).includes('DEEP_TXT');

// A TIGHT list item's inline markup: its child is a block `text` token, which
// used to be emitted as raw source, so every bullet printed its own asterisks.
const tagsOf = src => { const t = []; walk(describe(src), n => t.push(n.tag)); return t; };
out.tightListStrong = tagsOf('- **Hook:** once\n- **Party:** 3-6\n').filter(g => g === 'strong').length;
out.tightListRawStars = seen('- **Hook:** once\n- **Party:** 3-6\n').includes('**');
// The loose form already worked; it must keep working.
out.looseListStrong = tagsOf('- **Hook:** once\n\n- **Party:** 3-6\n').filter(g => g === 'strong').length;
// ...and an UNKNOWN token stays inert even when it carries a `tokens` field.
// No markdown source produces one, so the token is synthesized: this asserts
// the design doc's own bullet — the fallback never derives an element from the
// token type and never walks arbitrary token fields.
const mystery = describeBlock({type: 'mystery', raw: 'MYSTERY_RAW',
  tokens: [{type: 'strong', raw: '**x**', tokens: [{type: 'text', text: 'x'}]}]},
  {line: 1, endLine: 1}, 0);
const mysteryTags = []; walk(mystery, n => mysteryTags.push(n.tag));
out.unknownTokenInert = mysteryTags.join(',') === 'div' && mystery.text === 'MYSTERY_RAW';

// One anchor key per target: an li and its inner text fallback used to collide.
const keyed = [];
walk(describe('- one\n- two\npara\n\n| a | b |\n| - | - |\n| 1 | 2 |\n'), n => {
  if(n.line != null && n.attrs && String(n.attrs.class || '').includes('md-block'))
    keyed.push(n.line + ':' + n.endLine);
});
out.targetKeys = keyed;
out.duplicateTargetKeys = keyed.filter((k, i) => keyed.indexOf(k) !== i);

// --- structural highlighting (issue #385) ---------------------------------
// A diff line is attacker-influenceable — a fork PR chooses it. Whatever hljs
// tokenizes out of one, hlNodes must describe as spans carrying TEXT, and the
// concatenated text must equal the source byte for byte. Round-tripping is the
// property that proves no HTML parse happened: a parser would have eaten the
// tags, decoded the entities, or both.
const HOSTILE = [
  '<img src=x onerror=alert(1)>',
  '</script><script>alert(2)</script>',
  'const s = "&lt;img onerror=1&gt;";  // &#60;b&#62;',
  '<!-- <svg onload=alert(3)> -->',
  "x = '</textarea><img onerror=alert(4)>'",
  '<div class="hljs-comment">not really a comment</div>',
];
const hlTags = new Set(), hlAttrs = new Set(), hlClasses = new Set();
out.hlNotRoundTripped = [];
for(const line of HOSTILE){
  for(const lang of ['javascript', 'xml', 'python', null]){
    let text = '';
    (function visit(ns){
      for(const n of ns){
        hlTags.add(n.tag);
        Object.keys(n.attrs || {}).forEach(a => hlAttrs.add(a));
        if(n.attrs && n.attrs.class) hlClasses.add(n.attrs.class);
        if(n.text != null) text += n.text;
        visit(n.kids || []);
      }
    })(hlNodes(hlTree(line, lang)));
    if(text !== line) out.hlNotRoundTripped.push({lang, line, text});
  }
}
out.hlTags = [...hlTags].sort();
out.hlAttrs = [...hlAttrs].sort();
// Every class must be one hljs owns: an "hljs-"/"language-" head, or a
// trailing-underscore continuation from a dotted scope ("title.class").
out.hlBadClasses = [...hlClasses].filter(c => !c.split(' ').every(p =>
  p.indexOf('hljs-') === 0 || p.indexOf('language-') === 0 || /_+$/.test(p)));

// Comment detection moved off innerHTML onto the same tree. It must still be
// whole-line, language-agnostic, and unfooled by comment-looking content.
out.hlComment = {
  js:       hlIsComment(hlTree('  // <img onerror=1>', 'javascript')),
  python:   hlIsComment(hlTree('# note', 'python')),
  xml:      hlIsComment(hlTree('<!-- x -->', 'xml')),
  trailing: hlIsComment(hlTree('const a = 1; // tail', 'javascript')),
  blank:    hlIsComment(hlTree('', 'javascript')),
  fake:     hlIsComment(hlTree('<div class="hljs-comment">x</div>', 'xml')),
};

// The preview fence renders highlighted again, through that same path: token
// spans present, and the hostile body still intact as text.
const fenceBody = '<img src=x onerror=alert(1)>';
const fence = describe('```js\n' + fenceBody + '\n```\n');
const fenceTags = new Set(); const fenceClasses = new Set(); let fenceText = '';
walk(fence, n => {
  fenceTags.add(n.tag);
  if(n.attrs && n.attrs.class) fenceClasses.add(n.attrs.class);
  if(n.text) fenceText += n.text;
});
out.fenceTags = [...fenceTags].sort();
out.fenceTokenized = [...fenceClasses].some(c => c.indexOf('hljs-') === 0);
out.fenceBodyIntact = fenceText.includes(fenceBody);

// ...but ONLY a labelled fence. An unlabelled or unknown-labelled one must
// render plain rather than reach highlightAuto, which runs every vendored
// grammar over a body a fork PR chooses, in the default view for an added
// markdown file. Either way the body stays visible.
const probeFence = src => {
  const classes = new Set(); let text = '';
  walk(describe(src), n => {
    if(n.attrs && n.attrs.class) classes.add(n.attrs.class);
    if(n.text) text += n.text;
  });
  return {tokenized: [...classes].some(c => c.indexOf('hljs-') === 0), text};
};
const unlabelled = probeFence('```\n' + fenceBody + '\n```\n');
const unknownLabel = probeFence('```notalanguage\nconst x = 1;\n```\n');
out.unlabelledTokenized = unlabelled.tokenized;
out.unlabelledBodyIntact = unlabelled.text.includes(fenceBody);
out.unknownLabelTokenized = unknownLabel.tokenized;
out.unknownLabelBodyIntact = unknownLabel.text.includes('const x = 1;');

// hljs does not only fail by throwing: its `illegal` and safeMode
// `errorRaised` branches RETURN a partial emitter next to a complete escaped
// `value`. Reaching those through hlTree's own call shapes is hard —
// ignoreIllegals:true suppresses the illegal branch — which is exactly why the
// guard needs a direct test. Fabricate both result shapes and require null.
const realHighlight = hljs.highlight;
const partialTree = {children: ['const ']};          // half of the source below
out.guard = {};
for(const [name, extra] of [['illegal', {illegal: true}],
                            ['errorRaised', {errorRaised: new Error('synthetic')}]]){
  hljs.highlight = () => Object.assign({
    language: 'javascript', value: 'const LOST = 1;', relevance: 0,
    _emitter: {rootNode: partialTree},
  }, extra);
  out.guard[name] = hlTree('const LOST = 1;', 'javascript');
}
hljs.highlight = realHighlight;
out.guard.healthyStillWorks = !!hlTree('const OK = 1;', 'javascript');

console.log(JSON.stringify(out));
"""
    _src = _harness.replace("__PURE__", _pure)
    _fd, _hp = _tempfile.mkstemp(suffix=".cjs")
    try:
        with os.fdopen(_fd, "w", encoding="utf-8") as _f:
            _f.write(_src)
        _r = _subprocess.run([_node, _hp, _marked_js, _hljs_js],
                             capture_output=True, encoding="utf-8")
        if _r.returncode != 0:
            bad("preview: describe() corpus harness runs", (_r.stderr or "").strip()[:400])
        else:
            _o = json.loads(_r.stdout)
            check("preview: no tag outside the allowlist, over the whole corpus",
                  _o["tagsOutsideAllowlist"] == [], _o["tagsOutsideAllowlist"])
            check("preview: no attribute beyond class/href",
                  _o["attrs"] == ["class", "href"] or _o["attrs"] == ["class"] or _o["attrs"] == ["href"],
                  _o["attrs"])
            check("preview: javascript:, data:, relative and protocol-relative hrefs are all rejected",
                  all(h.startswith("https://") or h.startswith("mailto:") for h in _o["hrefs"]),
                  _o["hrefs"])
            check("preview: the two legitimate hrefs do survive",
                  len(_o["hrefs"]) == 2, _o["hrefs"])
            check("preview: a __proto__ frontmatter key does not pollute Object.prototype",
                  _o["protoPolluted"] is False, _o["protoPolluted"])
            check("preview: a document over the byte cap is declined",
                  _o["overCap"] is None, _o["overCap"])
            check("preview: CRLF does not drift the line anchors",
                  _o["crlfLines"] == [1, 3, 5], _o["crlfLines"])
            # 3 is the header row, 5 and 6 the body rows. The header used to be
            # the one uncommentable element in a rendered document.
            check("preview: every table row incl. the header anchors to its own line",
                  _o["tableRowLines"] == [3, 5, 6], _o["tableRowLines"])
            check("preview: the byte cap rejects a multibyte document under the char count",
                  _o["overCapMultibyte"] is None, "a UTF-16-length cap let it through")
            check("preview: a multibyte document well under the cap is still rendered",
                  _o["underCapMultibyte"] is False, _o["underCapMultibyte"])
            check("preview: a REJECTED link's title stays visible",
                  _o["rejectedLinkTitle"] is True, "hostile-branch title was dropped")
            check("preview: an accepted link's title stays visible",
                  _o["okLinkTitle"] is True, _o["okLinkTitle"])
            check("preview: an image's title stays visible",
                  _o["imageTitle"] is True, _o["imageTitle"])
            check("preview: a reference definition's title stays visible",
                  _o["refDefTitle"] is True, _o["refDefTitle"])
            check("preview: inline content past the depth cap degrades to visible text",
                  _o["deepInlineText"] is True, "deeply nested text vanished")
            check("preview: a tight list item renders its inline markup",
                  _o["tightListStrong"] == 2 and _o["tightListRawStars"] is False,
                  f'strong={_o["tightListStrong"]} rawStars={_o["tightListRawStars"]}')
            check("preview: a loose list item still renders its inline markup",
                  _o["looseListStrong"] == 2, _o["looseListStrong"])
            check("preview: an unknown token stays inert raw text, fields unwalked",
                  _o["unknownTokenInert"] is True, "the unknown-token fallback walked a token field")
            check("preview: no two comment targets share an anchor key",
                  _o["duplicateTargetKeys"] == [], _o["duplicateTargetKeys"])
            check("preview: frontmatter offset is added back to later anchors",
                  _o["headingAfterFrontmatter"] == [5], _o["headingAfterFrontmatter"])
            # --- structural highlighting (issue #385) ----------------------
            check("highlight: a hostile diff line round-trips as text, never as markup",
                  _o["hlNotRoundTripped"] == [], _o["hlNotRoundTripped"])
            check("highlight: token descriptions are spans and nothing else",
                  _o["hlTags"] == ["span"], _o["hlTags"])
            check("highlight: token descriptions carry no attribute but class",
                  _o["hlAttrs"] in ([], ["class"]), _o["hlAttrs"])
            check("highlight: every emitted class is one highlight.js owns",
                  _o["hlBadClasses"] == [], _o["hlBadClasses"])
            check("highlight: whole-line comments are still detected per language",
                  _o["hlComment"]["js"] and _o["hlComment"]["python"]
                  and _o["hlComment"]["xml"], _o["hlComment"])
            check("highlight: a trailing comment does not make the line a comment",
                  _o["hlComment"]["trailing"] is False, _o["hlComment"])
            check("highlight: an empty line is not a comment",
                  _o["hlComment"]["blank"] is False, _o["hlComment"])
            check("highlight: a line that merely says hljs-comment is not one",
                  _o["hlComment"]["fake"] is False, _o["hlComment"])
            check("highlight: preview renders a fence highlighted again",
                  _o["fenceTokenized"] is True, _o["fenceTags"])
            check("highlight: a hostile fence body survives intact as text",
                  _o["fenceBodyIntact"] is True, _o["fenceTags"])
            check("highlight: an UNLABELLED fence is not auto-detected",
                  _o["unlabelledTokenized"] is False,
                  "an unlabelled fence reached highlightAuto")
            check("highlight: an unlabelled fence still shows its body",
                  _o["unlabelledBodyIntact"] is True, _o["unlabelledBodyIntact"])
            check("highlight: an UNKNOWN fence label is not auto-detected",
                  _o["unknownLabelTokenized"] is False,
                  "an unknown fence label fell through to highlightAuto")
            check("highlight: an unknown-labelled fence still shows its body",
                  _o["unknownLabelBodyIntact"] is True, _o["unknownLabelBodyIntact"])
            check("highlight: an hljs `illegal` result is rejected, not rendered partial",
                  _o["guard"]["illegal"] is None, _o["guard"]["illegal"])
            check("highlight: an hljs `errorRaised` result is rejected too",
                  _o["guard"]["errorRaised"] is None, _o["guard"]["errorRaised"])
            check("highlight: the failure guard does not reject healthy results",
                  _o["guard"]["healthyStillWorks"] is True, _o["guard"])
    finally:
        os.unlink(_hp)

print(f"# {pass_count} passed, {fail_count} failed, {skip_count} skipped")
sys.exit(1 if fail_count else 0)
PYEOF

out="$(python3 "$tmp/test_parse_diff.py" "$SERVER")"
rc=$?

echo "$out"

pass_count="$(printf '%s\n' "$out" | grep -c '^ok - ')"
fail_count="$(printf '%s\n' "$out" | grep -c '^not ok - ')"
skip_count="$(printf '%s\n' "$out" | grep -c '^# skip - ')"

echo
echo "test-local-review: $pass_count passed, $fail_count failed, $skip_count skipped"
if [ "$rc" -ne 0 ]; then
  exit 1
fi
echo "test-local-review: OK"
