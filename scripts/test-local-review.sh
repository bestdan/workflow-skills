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

# --- server-behavior cases: real subprocesses, one python3 process launches ---
# them all. Each server is started with --diff-file (no `gh` needed) and no
# --port, so the OS picks a free port and the server reports it.
import re as _re
import selectors as _selectors
import shutil as _shutil
import subprocess as _subprocess
import tempfile as _tempfile
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


def start_server(extra_args, env=None):
    patch_fd, patch_path = _tempfile.mkstemp(suffix=".patch")
    with os.fdopen(patch_fd, "w") as f:
        f.write(PATCH)
    proc = _subprocess.Popen(
        [sys.executable, server_path, "--diff-file", patch_path] + extra_args,
        stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True, env=env,
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

# -- full round trip: GET /, POST /submit, atomic $OUT, --once exits --------
out_fd, out_path = _tempfile.mkstemp(suffix=".json")
os.close(out_fd)
os.unlink(out_path)  # server must create it; --once poller checks for existence
proc, patch_path = start_server(["--once", "--out", out_path])
try:
    url = read_url(proc)
    check("server: --once run prints LOCAL_REVIEW_URL", bool(url), url)
    if url:
        with _urlrequest.urlopen(f"{url}/", timeout=5) as resp:
            body = resp.read().decode()
            check("server: GET / returns 200", resp.status == 200, resp.status)
            check("server: GET / body contains 'Submit review'", "Submit review" in body, "")
        payload = {"meta": {}, "summary": "looks good", "approved": True, "comments": []}
        req = _urlrequest.Request(
            f"{url}/submit", data=json.dumps(payload).encode(),
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
            check("server: $OUT parses as JSON with the submitted fields",
                  written.get("summary") == "looks good" and written.get("approved") is True,
                  written)
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
bad_out = os.path.join(_tempfile.mkdtemp(prefix="lr-gone-"), "missing", "out.json")
proc, patch_path = start_server(["--out", bad_out])
try:
    url = read_url(proc)
    check("server: unwritable-out run still starts", bool(url), url)
    if url:
        req = _urlrequest.Request(
            f"{url}/submit", data=b'{"meta":{},"summary":"","approved":false,"comments":[]}',
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
