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
import sys

server_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("local_review_server", server_path)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
parse_diff = server.parse_diff

pass_count = 0
fail_count = 0


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
diff_unbalanced = """diff --git a/bar.py b/bar.py
index 1111111..2222222 100644
--- a/bar.py
+++ b/bar.py
@@ -1,2 +1,3 @@
-old one
-old two
+new one
+new two
+new three
"""
rows = parse_diff(diff_unbalanced)[0]["hunks"][0]["rows"]
check("unbalanced: three rows (max of 2 dels, 3 adds)", len(rows) == 3, f"got {len(rows)}")
check("unbalanced: row2 left is empty padding", rows[2]["l"]["t"] == "empty", rows[2]["l"])
check("unbalanced: row2 right is the extra add", rows[2]["r"]["t"] == "add" and rows[2]["r"]["s"] == "new three",
      rows[2]["r"])

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

# --- rename from/rename to -> status renamed, old != new --------------------
diff_renamed = """diff --git a/old_name.py b/new_name.py
similarity index 100%
rename from old_name.py
rename to new_name.py
"""
f = parse_diff(diff_renamed)[0]
check("rename: status renamed", f["status"] == "renamed", f["status"])
check("rename: old != new", f["old"] != f["new"], (f["old"], f["new"]))

# --- Binary files line -> binary True, no rows -------------------------------
diff_binary = """diff --git a/img.png b/img.png
index 1111111..2222222 100644
Binary files a/img.png and b/img.png differ
"""
f = parse_diff(diff_binary)[0]
check("binary: binary True", f["binary"] is True, f["binary"])
check("binary: no hunks/rows", f["hunks"] == [], f["hunks"])

# --- multi-hunk file: each hunk section captured from @@ trailer ------------
diff_multihunk = """diff --git a/multi.py b/multi.py
index 1111111..2222222 100644
--- a/multi.py
+++ b/multi.py
@@ -1,2 +1,2 @@ def one():
-a
+b
@@ -10,2 +10,2 @@ def two():
-c
+d
"""
f = parse_diff(diff_multihunk)[0]
check("multi-hunk: two hunks", len(f["hunks"]) == 2, len(f["hunks"]))
check("multi-hunk: hunk0 section", f["hunks"][0]["section"] == "def one():", f["hunks"][0]["section"])
check("multi-hunk: hunk1 section", f["hunks"][1]["section"] == "def two():", f["hunks"][1]["section"])

# --- multi-file diff: adds/dels counts are per-file, not cumulative --------
diff_multifile = """diff --git a/one.py b/one.py
index 1111111..2222222 100644
--- a/one.py
+++ b/one.py
@@ -1,1 +1,2 @@
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

# --- empty input -> [] --------------------------------------------------------
check("empty input: returns []", parse_diff("") == [], parse_diff(""))

print(f"# {pass_count} passed, {fail_count} failed")
sys.exit(1 if fail_count else 0)
PYEOF

out="$(python3 "$tmp/test_parse_diff.py" "$SERVER")"
rc=$?

echo "$out"

pass_count="$(printf '%s\n' "$out" | grep -c '^ok - ')"
fail_count="$(printf '%s\n' "$out" | grep -c '^not ok - ')"

echo
echo "test-local-review: $pass_count passed, $fail_count failed"
if [ "$rc" -ne 0 ]; then
  exit 1
fi
echo "test-local-review: OK"
