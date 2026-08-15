#!/usr/bin/env bash
# shellcheck shell=bash
# shellcheck disable=SC1010,SC2034 # Fixtures intentionally contain shell source and retain outputs for diagnostics.
# Shared prelude for the scripts/test-spawn-orchestrator/*.sh suites. SOURCED by
# each of them, never run on its own.
#
# The suites together are the test harness for scripts/spawn-orchestrator.sh:
# self-contained and offline, building real fixture dirs/binaries in a temp base
# and asserting the script's output + exit codes. No network, no stubs. The
# Seatbelt compile checks run only where `sandbox-exec` can actually apply a
# profile (macOS), and skip-with-note elsewhere so the harness stays green on
# Linux CI.
#
# They were one 5816-line file until it became the quality gate's critical path
# twice over — the slowest suite AND, because ShellCheck's cost is superlinear in
# file size, most of its lint cost. See dev_docs/gate-performance.md.
#
# What this file owns, and why each piece is here rather than in a suite:
#   - the caller-repo safety snapshot and the notifier guard (PRE-618 and the
#     desktop-spam bug). Each suite is its own process, so each needs its own
#     bracket; putting both ends here makes that automatic rather than
#     remembered. finish() asserts them.
#   - BASE, the git isolation, and the fixtures every suite starts from.
#   - the assertion helpers and their counters.
#   - fc() and _gate_iso()/NOW_EPOCH, hoisted out of the suites that first
#     defined them because a later suite reads them.
#
# Run one suite: bash scripts/test-spawn-orchestrator/doctor.sh
# Run them all:  bash scripts/test-spawn-orchestrator.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/scripts/spawn-orchestrator.sh"
# The summary line and the driver's per-suite accounting both key off this.
SUITE="$(basename "${BASH_SOURCE[1]:-$0}" .sh)"

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
# "The guard caught N incidental alarms" is a whole-RUN assertion — the alarms
# come from three different suites, so no one suite can make it. The driver
# therefore exports a POOLED log and asserts over all of them at once; a suite
# run on its own gets a private one and simply does not make that assertion.
# Only the private case truncates: appending is what makes the pooled count add
# up, and it is safe under concurrency because each stub writes one short line.
if [ -z "${NOTIFY_GUARD_LOG:-}" ]; then
  NOTIFY_GUARD_LOG="$BASE/guard-notify.calls"
  : >"$NOTIFY_GUARD_LOG"
fi
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

# --- hoisted helpers: defined by one suite's area, read by another ------------
# fail-closed assertion for the renderer: <name> <expected-substr> <args...>.
fc() { # <name> <expected-substr> <args...>
  local name="$1" want="$2"
  shift 2
  local target="$BASE/fc.sb"
  rm -f "$target"
  local o c
  o="$("$SCRIPT" render-profile "$@" --out "$target" 2>&1)"
  c=$?
  if [ "$c" = 2 ] && [ ! -e "$target" ] && printf '%s' "$o" | grep -qF "$want"; then
    ok "fail-closed: $name"
  else
    bad "fail-closed: $name" "exit=$c wrote=$([ -e "$target" ] && echo YES || echo no) msg=$o"
  fi
}

# epoch -> RUN.md's ISO-8601 UTC form, portable across BSD/GNU `date`.
_gate_iso() { date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
NOW_EPOCH="$(date -u +%s)"

# --- shared launch inputs -----------------------------------------------------
# The render suite BUILDS and ASSERTS these; four other suites consume them as
# known-good INPUTS to write-launch and supervisor fixtures (a valid profile, a
# valid settings file, a prompt, a generated wrapper, and the classify-exit
# logs). In the single-file harness they were made once and reused down the
# file; a suite that needs them now says so by calling this after sourcing the
# prelude. The flags here MUST stay identical to the render suite's own calls —
# runtime.sh's §"write-launch: the generated script classifies its own exit"
# and exit-contract.sh's §"the generated launch script wires the contract up"
# grep $BASE/launch.sh for generator behavior, and they must see the same
# wrapper the render suite asserted the generator produces.
LAUNCH_PATH='/opt/homebrew/bin:/usr/bin:/bin'
shared_launch_inputs() {
  mkdir -p "$BASE/root/wt/tmp"
  "$SCRIPT" render-profile --confine-under "$BASE/root" --rw "$BASE/root/wt" \
    --tmpdir "$BASE/root/wt/tmp" --exec "$BIN" --out "$BASE/cf.sb" >/dev/null 2>&1
  printf 'run the graph\n' >"$BASE/prompt.txt"
  "$SCRIPT" render-settings --source plan --coder codex --out "$BASE/wl.json" >/dev/null 2>&1
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
    --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --until 'T' --label com.autopilot.test --claude-bin "$BIN" \
    --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" --out-script "$BASE/launch.sh" --out-plist "$BASE/job.plist" >/dev/null 2>&1
  # classify-exit input logs: runtime.sh asserts classification on these; the
  # exit-contract suite reuses auth.log/weird.log to drive supervisor-check.
  CX="$BASE/cx"
  mkdir -p "$CX"
  printf 'ok\n' >"$CX/clean.log"
  printf 'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}\n' >"$CX/auth.log"
  printf 'API Error: 429 rate_limit_error: overloaded\n' >"$CX/rate.log"
  printf 'some other unrelated crash\n' >"$CX/weird.log"
}

# --- finish: the closing bracket every suite runs -----------------------------
# Call as the LAST statement of a suite; its status is the suite's exit status.
#
# Three kinds of check live here, and they are separated on purpose.
#
# 1. The composite-PATH notifier assertions. Several fixtures run the script
#    under an OVERRIDDEN PATH (`PATH="$STUB_PATH" "$SCRIPT" …`), which escapes
#    the guard-dir check below entirely: if such a PATH omits $GUARD and the
#    stub dir has no osascript of its own, the alarm resolves
#    /usr/bin/osascript and pops a REAL notification on the developer's
#    desktop, while the guard log stays plausible and the suite stays green.
#    That is exactly how the leak survived — a test that asserts only what it
#    catches can never report what got away. So every composite PATH this
#    harness builds is asserted at the source. Each suite builds its own, so
#    each asserts its own, and the set across the suites is the set the single
#    file asserted.
#
# 2. The per-PROCESS invariants: the caller-repo bracket (PRE-618) and the fact
#    that `osascript`/`terminal-notifier` still resolve inside the guard dir.
#    These were one assertion each when this harness was one process. They are
#    now one assertion each PER SUITE, because each suite is its own process
#    with its own fixtures and its own chance to escape — a bracket that only
#    one of them closed would leave the other six unguarded.
#
# 3. The strict-mode gate and the summary line.
#
# What is deliberately NOT here: "the guard caught N incidental alarms". That
# one is a whole-RUN property — the alarms come from three different suites, so
# a per-suite version would fail every suite that legitimately raises none.
# scripts/test-spawn-orchestrator.sh pools $NOTIFY_GUARD_LOG across the suites
# and makes that assertion once, over all of them (see the export above).
finish() {
  local pv pval resolved osa tn
  # (1) One assertion per composite PATH this suite built; `continue` skips the
  # ones it did not. Indirect expansion, so the three named vars stay findable
  # by grep from the suites that set them.
  for pv in GT_PATH ALPATH STUB_PATH; do
    eval "pval=\"\${$pv:-}\""
    [ -n "$pval" ] || continue
    resolved="$(PATH="$pval" command -v osascript 2>/dev/null || true)"
    case "$resolved" in
      "$BASE"/*) ok "notifier guard: \$$pv routes osascript inside the test tree, never a real binary" ;;
      *) bad "notifier guard: \$$pv routes osascript to a REAL binary (a suite run would pop a desktop notification)" "resolved to: ${resolved:-(not found)}" ;;
    esac
  done
  # The PATHs built inline rather than stored in a var (the failing-bootout and
  # task-26 unwritable-sentinel fixtures) get the same assertion.
  for pv in "${STUBF:-}" "${ALFAIL:-}"; do
    [ -n "$pv" ] || continue
    resolved="$(PATH="$pv:$GUARD:/usr/bin:/bin" command -v osascript 2>/dev/null || true)"
    case "$resolved" in
      "$BASE"/*) ok "notifier guard: the inline stub PATH ${pv##*/} routes osascript inside the test tree" ;;
      *) bad "notifier guard: the inline stub PATH ${pv##*/} routes osascript to a REAL binary" "resolved to: ${resolved:-(not found)}" ;;
    esac
  done

  # (2a) The guard is still in place for the INHERITED PATH: a binary outside
  # the guard dir was unreachable by name for this whole suite. (Nothing in
  # spawn-orchestrator.sh calls a notifier by absolute path; `_alarm_notify`
  # goes through `command -v`, which is exactly what this shadows.)
  osa="$(command -v osascript || true)"
  case "$osa" in
    "$GUARD"/*) ok "notifier guard [$SUITE]: osascript resolves INSIDE the guard dir, never the real binary" ;;
    *) bad "notifier guard [$SUITE]: osascript resolves INSIDE the guard dir" "resolved to: ${osa:-(not found)}" ;;
  esac
  tn="$(command -v terminal-notifier || true)"
  case "$tn" in
    "$GUARD"/*) ok "notifier guard [$SUITE]: terminal-notifier resolves INSIDE the guard dir" ;;
    *) bad "notifier guard [$SUITE]: terminal-notifier resolves INSIDE the guard dir" "resolved to: ${tn:-(not found)}" ;;
  esac

  # (2b) The PRE-618 regression test, against the snapshot taken at the top of
  # this file: after the suite, the caller's repo must be what it was before —
  # same HEAD, same current branch, same tracked working tree + index. If any
  # moved, a fixture reached outside its own temp repo and corrupted the
  # caller's checkout. (Untracked files are excluded on purpose: a repo-local
  # BASE fallback is trap-cleaned on exit and is not corruption.)
  if [ "$CALLER_IS_GIT" = 1 ]; then
    # The after-probes must observe the caller's repo the way the caller's own
    # git does — otherwise a pinned-config view of a tree checked out under the
    # developer's own config (core.autocrlf, core.fileMode, core.attributesFile
    # all affect `status --porcelain`) can report phantom modifications that
    # were never there. `env -u` CLEARS the three rather than restoring them,
    # so this reproduces the before-snapshot exactly when the caller had none
    # of them set — the normal case — and diverges for a caller who exports
    # them in their own shell. That divergence fails loud and safe (a false
    # positive here, never a missed escape), which is why it is not worth
    # carrying the save/restore machinery to close.
    local head_after ref_after tracked_after
    head_after="$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CEILING_DIRECTORIES git -C "$CALLER_REPO" rev-parse HEAD 2>/dev/null || echo NONE)"
    ref_after="$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CEILING_DIRECTORIES git -C "$CALLER_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo NONE)"
    tracked_after="$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CEILING_DIRECTORIES git -C "$CALLER_REPO" status --porcelain --untracked-files=no 2>/dev/null)"
    if [ "$head_after" = "$CALLER_HEAD_BEFORE" ]; then
      ok "caller-repo safety [$SUITE]: HEAD unmoved (no fixture committed into the caller's repo)"
    else
      bad "caller-repo safety [$SUITE]: HEAD MOVED — a fixture committed into the caller's repo" \
        "before=$CALLER_HEAD_BEFORE after=$head_after"
    fi
    if [ "$ref_after" = "$CALLER_REF_BEFORE" ]; then
      ok "caller-repo safety [$SUITE]: current branch unchanged"
    else
      bad "caller-repo safety [$SUITE]: current branch changed" "before=$CALLER_REF_BEFORE after=$ref_after"
    fi
    if [ "$tracked_after" = "$CALLER_TRACKED_BEFORE" ]; then
      ok "caller-repo safety [$SUITE]: tracked working tree + index unchanged"
    else
      bad "caller-repo safety [$SUITE]: tracked files changed under the caller's repo" \
        "before=[$CALLER_TRACKED_BEFORE] after=[$tracked_after]"
    fi
  fi

  # (3) Opt-in strict mode: exists so a macos-latest CI job can DEMAND the
  # seatbelt-behavioral assertions actually ran, instead of quietly passing
  # while skipping everything it was set up to run. Gates on the SKIP COUNT,
  # not on SEATBELT_OK: a usable seatbelt with a missing host fixture skips
  # real assertions while SEATBELT_OK stays 1, so the job would pass having
  # silently run fewer than it claims — precisely the "green while skipping
  # what it exists to run" outcome this flag exists to make impossible. A zero
  # skip count is the only thing that means full coverage, and SEATBELT_OK=0
  # already forces a non-zero skip count, so it needs no arm here.
  if [ "${SO_TEST_REQUIRE_SEATBELT:-0}" = 1 ] && [ "$skip" -gt 0 ]; then
    bad "seatbelt coverage required (SO_TEST_REQUIRE_SEATBELT=1) but $skip seatbelt-behavioral assertions did not run (SEATBELT_OK=$SEATBELT_OK)"
  fi
  # A quiet skip count buries the fact that a whole behavioral layer went
  # unexercised — loud enough to notice, not loud enough to fail a run that
  # never claimed seatbelt coverage (that's what SO_TEST_REQUIRE_SEATBELT is
  # for).
  if [ "$skip" -gt 0 ] && [ "$SEATBELT_OK" != 1 ] && command -v sandbox-exec >/dev/null 2>&1; then
    echo
    echo "NOTE: $skip seatbelt-behavioral tests were SKIPPED — sandbox-exec cannot apply a"
    echo "      profile here (nested sandbox). Run this suite unsandboxed for full coverage."
    echo
  fi

  echo "test-spawn-orchestrator/$SUITE: $pass passed, $fail failed, $skip skipped"
  [ "$fail" = 0 ]
}
