# shellcheck shell=bash
# shellcheck disable=SC2034 # Everything defined here is read by a part or the epilogue, which ShellCheck cannot see from this file alone.
# Shared prelude for the scripts/test-spawn-orchestrator-*.sh parts.
#
# The orchestrator harness used to be one 5816-line file. It was split into
# per-topic parts because that file was simultaneously the gate's slowest suite
# and the bulk of its ShellCheck bill (see dev_docs/gate-performance.md). Every
# part sources this file first and the matching epilogue last, so each one is a
# standalone, independently runnable suite with its own fixture tree — no part
# may depend on state another part built.
#
# Everything here is either an isolation guarantee that must hold for EVERY part
# (the git-env neutralization, the caller-repo snapshot, the seatbelt probe, the
# notifier guard) or a fixture that more than one part reads. Anything used by a
# single part belongs in that part, not here.
#
# Sourced, never executed: `set -uo pipefail` is the caller's job, because the
# exit status of the whole part depends on it.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/scripts/spawn-orchestrator.sh"

# Inherited git env is neutralized FIRST, before anything reads a repo.
# GIT_CONFIG_COUNT/PARAMETERS are command-scope config that outranks the
# GIT_CONFIG_GLOBAL pin below, and GIT_DIR/GIT_INDEX_FILE/etc are exported by
# git into every hook subprocess — so a check.sh invoked from a pre-commit hook
# would otherwise hand fixture `git add` calls the caller's repo and index. It
# has to precede the snapshot below: a leaked GIT_DIR redirects the snapshot's
# own probe, which then reports "not a git repo" and silently skips every
# caller-safety assertion in exactly the case they exist to cover.
# GIT_AUTHOR_*/GIT_COMMITTER_* outrank the gitconfig [user] pin below and are
# exported into hook and `git rebase -x` subprocesses. This is the explicit
# list, not `unset $(git rev-parse --local-env-vars)`: that dynamic form fails
# open — a missing or broken git yields empty output, the error is swallowed,
# and this line (whose whole purpose is to run before git is trusted) would
# silently grant zero isolation.
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT \
  GIT_OBJECT_DIRECTORY GIT_DIR GIT_WORK_TREE GIT_IMPLICIT_WORK_TREE GIT_GRAFT_FILE \
  GIT_INDEX_FILE GIT_NO_REPLACE_OBJECTS GIT_REPLACE_REF_BASE GIT_PREFIX GIT_SHALLOW_FILE \
  GIT_COMMON_DIR GIT_TEMPLATE_DIR \
  GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_AUTHOR_DATE \
  GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE

# --- Caller-repo safety snapshot (PRE-618) ------------------------------------
# This suite drives REAL git-mutating fixtures. Several deliberately use a run dir
# that is NOT its own git repo (to exercise the orchestrator's non-git / empty-HEAD
# paths). git discovers a repo by walking UPWARD from cwd, so a git op against such
# a dir resolves to the nearest ENCLOSING repo. When BASE lands inside a real repo
# (the `mktemp` fallback below, or the suite being launched from a live checkout),
# that enclosing repo is the CALLER's — and a fixture's supervisor-halt / run-state
# commit then lands on the caller's branch. That is how running `check.sh` from a
# live worktree once committed fixture files and stray branches onto an in-flight
# task branch and reverted uncommitted work (PRE-618). Capture the caller's repo
# now, into a DEDICATED name — the suite reuses the `ROOT` variable for its own
# fixtures (e.g. the verify-branch `ROOT="$VB/root"` block), so by the end-of-suite
# assertion $ROOT no longer points here.
CALLER_REPO="$ROOT"
CALLER_IS_GIT=0
if git -C "$CALLER_REPO" rev-parse --git-dir >/dev/null 2>&1; then
  CALLER_IS_GIT=1
  CALLER_HEAD_BEFORE="$(git -C "$CALLER_REPO" rev-parse HEAD 2>/dev/null || echo NONE)"
  CALLER_REF_BEFORE="$(git -C "$CALLER_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo NONE)"
  # Tracked working tree + index only: a repo-local BASE fallback is untracked and
  # trap-cleaned on exit, so it is not corruption and must not false-fail here.
  CALLER_TRACKED_BEFORE="$(git -C "$CALLER_REPO" status --porcelain --untracked-files=no 2>/dev/null)"
  # The branch SET is deliberately not asserted. Everything above is per-worktree
  # state that only this checkout can move; refs/heads is shared across every
  # worktree of the repo, so a concurrent agent creating or deleting a branch
  # during this suite's multi-minute run fails it for a reason that has nothing
  # to do with fixture leakage. An escaped commit moves HEAD, an escaped
  # checkout -b changes the current branch, and escaped adds dirty the tracked
  # tree — so the checks that remain already cover every escape a fixture here
  # can produce.
fi

# Sandboxed runs may deny the system temp dir — fall back to a repo-local base.
# Resolve to the physical path (pwd -P): the renderer canonicalizes every path,
# so fixtures must be canonical too or /var vs /private/var would mismatch.
#
# Bare `mktemp -d` (no template) ignores $TMPDIR on macOS — it resolves
# _CS_DARWIN_USER_TEMP_DIR via confstr and always targets /var/folders/...,
# which a sandbox denies regardless of what $TMPDIR is set to. So the first
# arm alone is not a real $TMPDIR attempt; it has to fail before $TMPDIR is
# even consulted. The second arm targets $TMPDIR explicitly (falling back to
# /tmp if unset) so a sandboxed run actually lands fixtures in the writable
# tree it was granted, instead of skipping straight to the repo-local
# fallback. The repo-local fallback stays last, and is now only ever reached
# when both temp dirs are unwritable too: under the primary checkout a
# sandbox denies git's metadata-preserving template copy into it ("cannot
# copy .../hooks/commit-msg.sample ... Operation not permitted"), which
# cascaded ~139 failures through every fixture that git-inits a subdir.
#
# Latent coupling, for whoever touches either side of this next: a sandboxed
# $TMPDIR is typically /tmp/claude-<uid>, which MATCHES the renderer's
# ambient harness-grant regex (see "harness /tmp runtime granted by
# uid-independent pattern" below, and grep the renderer for
# "/private/tmp/claude"). So every profile this suite renders while running
# under such a sandbox ambient-grants write access to the fixture tree BASE
# sits in. That is harmless ONLY because the seatbelt capability probe below
# (SEATBELT_OK) causes this environment to skip every runtime-denial
# assertion that would otherwise depend on that tree NOT being writable —
# change the probe's skip conditions without noticing this, and a
# behavioral-denial test could start passing for the same wrong reason the
# six nested-sandbox false greens did (see the SEATBELT_OK probe below).
BASE="$(mktemp -d 2>/dev/null \
  || mktemp -d "${TMPDIR:-/tmp}/so-test.XXXXXX" 2>/dev/null \
  || mktemp -d "$ROOT/.so-test.XXXXXX")"
trap 'rm -rf "$BASE"' EXIT
# If the cd fails, exit rather than falling through with an empty BASE (which
# would make every later "$BASE/x" resolve to a root-relative /x).
BASE="$(cd "$BASE" && pwd -P)" || exit 2

# --- Seatbelt capability probe -------------------------------------------------
# `command -v sandbox-exec` only proves the BINARY is present, not that it can
# actually apply a profile. Inside a nested sandbox (e.g. this suite itself run
# under sandbox-exec/Solo), sandbox_apply is refused with "Operation not
# permitted" (rc=71) before the profile's own rules are ever consulted. The six
# blocks below that used to gate on presence alone therefore misbehaved in two
# ways when run nested: assertions that need a profile to SUCCEED failed
# outright, and assertions written as "if sandbox-exec denies X, ok" passed for
# entirely the wrong reason — sandbox_apply failing IS a denial, so the assert
# reports ok on evidence it never gathered (the profile's own deny rule was
# never reached). Probing once here with a trivial always-allow profile turns
# both failure modes into an honest, loud skip instead of a false pass/fail.
SEATBELT_OK=0
if command -v sandbox-exec >/dev/null 2>&1; then
  printf '(version 1)\n(allow default)\n' >"$BASE/seatbelt-probe.sb"
  if sandbox-exec -f "$BASE/seatbelt-probe.sb" /usr/bin/true >/dev/null 2>&1; then
    SEATBELT_OK=1
  fi
fi

# The belt to the snapshot above (the braces): git must NEVER discover a repo at
# or above BASE. A ceiling at BASE stops the upward repo-discovery walk before it
# can reach a repo that CONTAINS base — so a fixture whose run dir is not its own
# git repo gets a clean "not a git repository" (its intended behavior) instead of
# silently resolving to the caller's repo, even when BASE is repo-local. Fixtures
# that git-init their own subdir are unaffected: their `.git` sits BELOW the
# ceiling and is found first. Exported so the spawn-orchestrator.sh subprocesses
# the fixtures invoke inherit it too. This makes the escape impossible by
# construction, the same floor-not-ceiling stance as the notifier guard above.
export GIT_CEILING_DIRECTORIES="$BASE"

# A developer's global/system git config leaks into these fixture repos too:
# core.hooksPath (whose pre-commit hook blocks commits to main, and git init
# names the initial branch main) can silently veto fixture commits, and
# init.templateDir/commit.gpgsign/aliases are other injection routes. Pin the
# config env instead of nulling it, so `git init` still deterministically
# produces branch "main" on stock upstream git. Exported so the
# spawn-orchestrator.sh subprocesses the fixtures invoke inherit it too. (The
# env half of this guard is unset at the top of the file, before the
# caller-repo snapshot.)
printf '[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n' >"$BASE/gitconfig"
export GIT_CONFIG_GLOBAL="$BASE/gitconfig"
export GIT_CONFIG_SYSTEM=/dev/null

# --- The notifier guard: the suite may NEVER reach the real /usr/bin/osascript --
#
# The alarm (task 16) ends in a real macOS desktop notification, and several
# tests trigger one INCIDENTALLY — supervisor-check's fatal-auth and no-progress
# halts, the exit-handling tests, a doctor halt delivered by supervisor-scan —
# because `_supervisor_halt` raises an alarm on its way out. Those tests assert on
# the ALARM sentinel and REPORT.md, never on the notifier, so nothing stubbed it:
# `_alarm_notify` did `command -v osascript`, found the REAL one, and every
# developer running `scripts/check.sh` on a Mac got four desktop notifications
# with `com.autopilot.test.*` fixture labels. The suite still passed — the side
# effect escaped into the real world and nothing asserted a thing about it, which
# is the same harness-diverges-from-production class as the findings this file
# exists to pin down.
#
# So the leak is made IMPOSSIBLE BY CONSTRUCTION, not by remembering to stub:
# ONE guard dir, prepended to PATH for the WHOLE suite, shadowing every binary
# that can reach the user's machine — `osascript`, `terminal-notifier` (the
# notifiers), `open`, and `launchctl` (which could otherwise bootstrap a REAL
# launchd job from a fixture plist). Every stub RECORDS its invocation, so a test
# that wants to assert on notifier calls can point $NOTIFY_GUARD_LOG at its own
# recorder and count them (see the doctor halt's zero-notification assertion).
# Tests with their own stub dir (the alarm suite's $ALSTUB) prepend it to PATH
# themselves and still win — this guard is the floor, not a ceiling.
GUARD="$BASE/guard-bin"
mkdir -p "$GUARD"
NOTIFY_GUARD_LOG="$BASE/guard-notify.calls"
: >"$NOTIFY_GUARD_LOG"
export NOTIFY_GUARD_LOG
# The notifiers: record and succeed (a REAL notification is what we are preventing).
for _n in osascript terminal-notifier open; do
  printf '#!/bin/sh\nprintf "%%s: %%s\\n" "%s" "$*" >>"${NOTIFY_GUARD_LOG:-/dev/null}"\nexit 0\n' "$_n" >"$GUARD/$_n"
  chmod +x "$GUARD/$_n"
done
# launchctl: same shape as the alarm suite's own stub, so behavior is identical
# whichever is on PATH — `print` exits 1 (job gone, which is the truth for every
# fixture label), everything else no-ops 0. This also keeps a stray `bootstrap`
# from loading a fixture plist into the developer's real launchd.
printf '#!/bin/sh\nprintf "launchctl: %%s\\n" "$*" >>"${NOTIFY_GUARD_LOG:-/dev/null}"\n[ "$1" = print ] && exit 1\nexit 0\n' >"$GUARD/launchctl"
chmod +x "$GUARD/launchctl"
export PATH="$GUARD:$PATH"

pass=0
fail=0
skip=0
ok() {
  pass=$((pass + 1))
  echo "ok   - $1"
}
bad() {
  fail=$((fail + 1))
  echo "FAIL - $1"
  [ -n "${2:-}" ] && echo "       $2"
  return 0
}
# Only the seatbelt-behavioral blocks below count here — the file's other
# pre-existing "skip - ..." notes (e.g. Homebrew Cellar, python3, plutil, the
# git-absent ones) stay bare echoes and MUST stay bare: folding a non-seatbelt
# skip in would fail SO_TEST_REQUIRE_SEATBELT on hosts where seatbelt coverage
# is in fact complete, since the git-absent skips fire on every Linux runner.
# So this counter means exactly one thing: seatbelt-behavioral assertions that
# did not run, not "any skip anywhere". Note that is a broader reason than "no
# usable seatbelt" — a skip for a missing host fixture inside a block counts
# too, because SO_TEST_REQUIRE_SEATBELT gates on this number and an assertion
# that didn't run is uncovered whatever the cause.
skipped() {
  skip=$((skip + 1))
  echo "skip - $1"
}
# assert helpers use if/else — `cond && bad || ok` double-fires when bad returns non-zero.
have() { if grep -qF -- "$2" <<<"$3"; then ok "$1"; else bad "$1"; fi; }
lack() { if grep -qF -- "$2" <<<"$3"; then bad "$1"; else ok "$1"; fi; }
count_is() {
  local n
  n="$(grep -cF -- "$3" <<<"$4")"
  if [ "$n" = "$2" ]; then ok "$1"; else bad "$1" "want $2 got $n"; fi
}

# Fixtures: real dirs + a real executable + a real plain file.
RUN_WT="$BASE/run-wt"
WORKER_WT="$BASE/worker-wt"
REPO_RO="$BASE/repo-ro"
mkdir -p "$RUN_WT" "$WORKER_WT" "$REPO_RO"
PLAIN="$BASE/plain"
: >"$PLAIN"
BIN="$BASE/bin/tool"
mkdir -p "$BASE/bin"
printf '#!/bin/sh\n:\n' >"$BIN"
chmod +x "$BIN"

# epoch -> RUN.md's ISO-8601 UTC form, portable across BSD/GNU `date`. Lives
# here because the supervisor-gate part defines it and the exit-contract part
# reads it; under one file that was a plain forward reference, and across files
# it is the only function dependency the split could not localize.
_gate_iso() { date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
# Its companion, for the same reason. Note this one is invisible to a scan for
# `$NAME`: both parts read it only as `$((NOW_EPOCH + 3600))`, and a bare name
# inside an arithmetic expansion carries no sigil. Grep for the bare word when
# hunting cross-part reads, not just for `$`.
NOW_EPOCH="$(date -u +%s)"

# --- the shared fixture bundle ------------------------------------------------
# Five artifacts that more than one part reads: a confined profile (cf.sb), a
# settings file (wl.json), a prompt file, and the launch script + plist the
# renderer generates from them. In the monolith these were side effects of the
# render/write-launch tests — the profile part built them and the supervisor and
# exit-contract parts read them hundreds of lines later. That is exactly the
# coupling a split has to remove, so they are built here, unasserted, for every
# part. The parts that TEST these renderers still render their own copies over
# the top; this bundle only guarantees the files exist for the parts that merely
# consume them.
#
# Rendering is silent and unchecked on purpose: a failure here shows up as the
# consuming assertion failing, which is where the diagnosis belongs. Asserting
# it in the prelude would report the same breakage once per part.
mkdir -p "$BASE/root/wt" "$BASE/root/wt/tmp"
"$SCRIPT" render-profile --confine-under "$BASE/root" --rw "$BASE/root/wt" \
  --tmpdir "$BASE/root/wt/tmp" --exec "$BIN" --out "$BASE/cf.sb" >/dev/null 2>&1
printf 'run the graph\n' >"$BASE/prompt.txt"
"$SCRIPT" render-settings --source plan --coder codex --out "$BASE/wl.json" >/dev/null 2>&1
LAUNCH_PATH='/opt/homebrew/bin:/usr/bin:/bin'
"$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --until 'T' --label com.autopilot.test \
  --claude-bin "$BIN" --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" \
  --out-script "$BASE/launch.sh" --out-plist "$BASE/job.plist" >/dev/null 2>&1
