#!/usr/bin/env bash
# shellcheck disable=SC1010,SC2034 # Fixtures intentionally contain shell source and retain outputs for diagnostics.
# Test harness for scripts/spawn-orchestrator.sh.
#
# Self-contained and offline: builds real fixture dirs/binaries in a temp base
# and asserts the renderer's output + exit codes. No network, no stubs. The
# Seatbelt compile check runs only when `sandbox-exec` is present (macOS), and
# skips-with-note otherwise so the harness stays green on Linux CI.
#
# This slice covers the layer-1 profile renderer (PRE-484 task 1). Sibling tasks
# extend it with the network-allowlist and launch-script/plist generators.
#
# Run directly: bash scripts/test-spawn-orchestrator.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
BASE="$(mktemp -d 2>/dev/null || mktemp -d "$ROOT/.so-test.XXXXXX")"
trap 'rm -rf "$BASE"' EXIT
# If the cd fails, exit rather than falling through with an empty BASE (which
# would make every later "$BASE/x" resolve to a root-relative /x).
BASE="$(cd "$BASE" && pwd -P)" || exit 2

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

# --- render happy path: assert the token blocks are filled correctly ----------
prof="$BASE/happy.sb"
if out="$("$SCRIPT" render-profile --rw "$RUN_WT" --rw "$WORKER_WT" --ro "$REPO_RO" --exec "$BIN" --out "$prof" 2>&1)" \
  && [ -f "$prof" ]; then
  ok "render-profile: exit 0 and writes --out"
else
  bad "render-profile: exit 0 and writes --out" "$out"
fi

body="$(cat "$prof" 2>/dev/null)"
have "profile: deny-default present" '(deny default)' "$body"
have "profile: RW run worktree present" "(subpath \"$RUN_WT\")" "$body"
have "profile: exec binary as literal" "(literal \"$BIN\")" "$body"
# An RW path appears twice (file-read* + file-write*); an RO path once (read only).
count_is "profile: RW path is read+write" 2 "$RUN_WT" "$body"
count_is "profile: RO path is read-only" 1 "$REPO_RO" "$body"
# no unrendered template tokens remain
lack "profile: no @@tokens@@ remain" '@@' "$body"

# --- task 12 / finding #20: the harness's OWN runtime surface (srt-mux socket +
# the /tmp/claude-<uid> scratch tree + ~/.claude/session-env) must be permitted,
# narrowly scoped — never a blanket $TMPDIR or ~/.claude write. These are
# renderer-owned (host-resolved, never caller-supplied), so every render emits
# them regardless of --rw/--ro and they bypass the --confine-under caller check.
tmpdir_c="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
tmp_c="$(cd /tmp && pwd -P)"
# The srt-mux pattern must appear TWICE: once under (allow file-write* …) (to
# create the socket special file) and once under (allow network-bind …) (to
# actually bind/listen on it) — neither alone suffices (verified empirically
# against sandbox-exec).
count_is "profile: srt-mux socket pattern present in file-write* AND network-bind" \
  2 "(regex #\"^$tmpdir_c/srt-mux-[0-9]+-[0-9]+\\.sock\$\")" "$body"
have "profile: network-bind block present for the srt-mux socket" \
  '(allow network-bind' "$body"
# The harness mkdir's a TREE under /tmp/claude-<N> — a file-only grant would not
# permit the enclosing mkdir, so this must cover the tree, not just a file.
#
# Matched by PATTERN, never by a uid-resolved path: <N> is NOT the uid (a detached
# launchd run used /tmp/claude-522 on a uid-501 host). A uid-derived grant misses,
# the harness's mkdir is denied, and every exit code is re-poisoned to 1 — finding
# #20 restored silently. This assertion is what pins that down.
# Covers BOTH shapes the harness uses — the mkdir'd scratch TREE (/tmp/claude-<id>/…)
# and the cwd-tracking FILE rewritten after every Bash call (/tmp/claude-<hex>-cwd).
# Granting only one still poisons every exit code to 1; the detached orchestrator
# uses the -cwd files, an interactive session was seen using the numeric tree.
have "profile: harness /tmp runtime granted by uid-independent pattern (tree AND -cwd file)" \
  "(regex #\"^$tmp_c/claude-[A-Za-z0-9]+(-cwd)?(/|\$)\")" "$body"
lack "profile: harness /tmp grant is NOT a uid-resolved path (it would miss)" \
  "(subpath \"$tmp_c/claude-$(id -u)\")" "$body"
have "profile: ~/.claude/session-env granted as a subpath" \
  "(subpath \"$HOME/.claude/session-env\")" "$body"
# …but NOT one inch wider: a blanket $TMPDIR or ~/.claude write would defeat the
# jail (and, for ~/.claude, put the credential file in a writable scope).
lack "profile: harness grant does not widen to a blanket TMPDIR subpath" \
  "(subpath \"$tmpdir_c\")" "$body"
lack "profile: harness grant does not widen to a blanket ~/.claude subpath" \
  "(subpath \"$HOME/.claude\")" "$body"
lack "profile: harness grant does not widen to a blanket /tmp subpath" \
  "(subpath \"$tmp_c\")" "$body"

# --- A1 regression: an exec symlink must resolve to its real target -----------
# (Seatbelt matches process-exec against the resolved vnode path, not the link;
# `command -v` on Homebrew binaries returns the symlink, so the renderer must
# canonicalize it or the exec is silently denied.)
ln -s tool "$BASE/bin/tool-link" # relative symlink → $BASE/bin/tool
symprof="$BASE/sym.sb"
"$SCRIPT" render-profile --rw "$RUN_WT" --exec "$BASE/bin/tool-link" --out "$symprof" >/dev/null 2>&1
symbody="$(cat "$symprof" 2>/dev/null)"
have "exec symlink resolves to real target" "(literal \"$BIN\")" "$symbody"
lack "exec symlink literal not emitted" "tool-link" "$symbody"

# --- fail-closed: bad inputs exit 2 and write nothing -------------------------
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
fc "relative rw" "must be absolute" --rw "relative/path"
fc "missing rw" "does not exist" --rw "$BASE/nope"
fc "exec is a dir" "not an executable file" --exec "$REPO_RO"
fc "exec non-exec file" "not an executable file" --exec "$PLAIN"

# --out required
o="$("$SCRIPT" render-profile --rw "$RUN_WT" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --out' \
  && ok "fail-closed: --out required" || bad "fail-closed: --out required" "$o"

# unknown subcommand
o="$("$SCRIPT" bogus 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -q 'unknown subcommand' \
  && ok "usage: unknown subcommand exits 2" || bad "usage: unknown subcommand exits 2" "$o"

# --- Seatbelt compile check (macOS only; skip-with-note elsewhere) ------------
if command -v sandbox-exec >/dev/null 2>&1; then
  if o="$("$SCRIPT" check-profile "$prof" 2>&1)"; then
    ok "check-profile: rendered profile compiles"
  else
    bad "check-profile: rendered profile compiles" "$o"
  fi
  # a malformed profile must be rejected
  echo '(version 1) (this-is-not-valid' >"$BASE/bad.sb"
  if "$SCRIPT" check-profile "$BASE/bad.sb" >/dev/null 2>&1; then
    bad "check-profile: rejects a malformed profile"
  else
    ok "check-profile: rejects a malformed profile"
  fi
else
  echo "skip - check-profile: sandbox-exec not available on this host (non-macOS)"
fi

# --- exec-dir: toolchain-exec mode (subpath, coarser than --exec) -------------
edprof="$BASE/execdir.sb"
"$SCRIPT" render-profile --rw "$RUN_WT" --exec-dir /usr/bin --out "$edprof" >/dev/null 2>&1
edbody="$(cat "$edprof" 2>/dev/null)"
have "exec-dir: emits subpath for /usr/bin" '(subpath "/usr/bin")' "$edbody"

if command -v sandbox-exec >/dev/null 2>&1; then
  if o="$("$SCRIPT" check-profile "$edprof" 2>&1)"; then
    ok "check-profile: --exec-dir profile compiles"
  else
    bad "check-profile: --exec-dir profile compiles" "$o"
  fi
else
  echo "skip - check-profile: --exec-dir profile compiles (sandbox-exec not available)"
fi

fc "exec-dir /" "refusing --exec-dir /" --exec-dir /
fc "exec-dir plain file" "not a directory" --exec-dir "$PLAIN"

# --- toolchain: the active developer-tools dir (the CLT-shim grant) ------------
# Seatbelt matches the RESOLVED exec target, and /usr/bin/git is a Command Line
# Tools SHIM that re-execs <dev>/usr/bin/git — so (subpath "/usr/bin") permits the
# shim and not the binary, and a jailed git dies with "can't exec … (Operation not
# permitted)". --toolchain therefore has to grant xcode-select -p's usr/ dir.
# smoke-confinement.sh proves this against a REAL jail, but check.sh never runs the
# smoke (it is manual, macOS-only, and spends real credentials) — so without the
# assertions below, deleting the grant would leave CI fully green. xcode-select is
# invoked unqualified by the renderer, so a PATH-shadowing stub makes the whole
# thing deterministic and host-independent: no Xcode, no CLT, no macOS required.
tcstub="$BASE/tcstub"
tcdev="$BASE/devdir"
mkdir -p "$tcstub" "$tcdev/usr/bin"
printf '#!/bin/sh\nprintf "%%s\\n" "%s"\n' "$tcdev" >"$tcstub/xcode-select"
chmod +x "$tcstub/xcode-select"

tcprof="$BASE/toolchain.sb"
PATH="$tcstub:$PATH" "$SCRIPT" render-profile --rw "$RUN_WT" --toolchain --out "$tcprof" >/dev/null 2>&1
tcbody="$(cat "$tcprof" 2>/dev/null)"
have "toolchain: grants the active developer-tools usr/ dir (CLT shim's re-exec target)" \
  "(subpath \"$tcdev/usr\")" "$tcbody"
have "toolchain: still grants the standard bin dirs" '(subpath "/usr/bin")' "$tcbody"

# A developer dir with no usr/ subdir is omitted, not fatal — same posture as any
# other missing standard dir.
tcdev2="$BASE/devdir-nousr"
mkdir -p "$tcdev2"
printf '#!/bin/sh\nprintf "%%s\\n" "%s"\n' "$tcdev2" >"$tcstub/xcode-select"
tcprof2="$BASE/toolchain-nousr.sb"
if PATH="$tcstub:$PATH" "$SCRIPT" render-profile --rw "$RUN_WT" --toolchain --out "$tcprof2" >/dev/null 2>&1; then
  ok "toolchain: a developer dir with no usr/ is omitted, not fail-closed"
else
  bad "toolchain: a developer dir with no usr/ is omitted, not fail-closed"
fi
lack "toolchain: omits the usr-less developer dir from the exec block" \
  "(subpath \"$tcdev2\")" "$(cat "$tcprof2" 2>/dev/null)"

# xcode-select absent or failing (no developer tools installed) must not fail the
# render — the grant is simply skipped.
printf '#!/bin/sh\nexit 1\n' >"$tcstub/xcode-select"
tcprof3="$BASE/toolchain-noxcode.sb"
if PATH="$tcstub:$PATH" "$SCRIPT" render-profile --rw "$RUN_WT" --toolchain --out "$tcprof3" >/dev/null 2>&1; then
  ok "toolchain: a failing xcode-select is skipped, not fail-closed"
else
  bad "toolchain: a failing xcode-select is skipped, not fail-closed"
fi
have "toolchain: still renders the standard bin dirs without a developer dir" \
  '(subpath "/usr/bin")' "$(cat "$tcprof3" 2>/dev/null)"

# --- toolchain: Homebrew's Cellar (the symlink-farm grant) --------------------
# /opt/homebrew/bin is a symlink farm — nearly every entry points into
# Cellar/<pkg>/<ver>/bin — and Seatbelt matches the resolved target, so the bin-dir
# grant alone permits almost no Homebrew binary (gh, the tool the agent opens PRs
# with, included). Host-dependent by nature: assert it only where Cellar exists.
if [ -d /opt/homebrew/Cellar ]; then
  have "toolchain: grants Homebrew's Cellar (bin/ is a symlink farm into it)" \
    '(subpath "/opt/homebrew/Cellar")' "$tcbody"
else
  echo "skip - toolchain: grants Homebrew's Cellar (no /opt/homebrew/Cellar on this host)"
fi

if command -v sandbox-exec >/dev/null 2>&1; then
  EDBIN=""
  # Prefer no-arg zero-exit binaries so the success check is portable — BSD/macOS
  # `sed --version` exits non-zero (unknown flag), which would false-fail even when
  # exec-dir confinement is working.
  for cand in /usr/bin/true /bin/echo /usr/bin/env; do
    if command -v "$cand" >/dev/null 2>&1; then
      EDBIN="$cand"
      break
    fi
  done
  if [ -n "$EDBIN" ]; then
    EDDIR="$(dirname "$EDBIN")"
    edcprof="$BASE/execdir-confine.sb"
    "$SCRIPT" render-profile --exec-dir "$EDDIR" --rw "$RUN_WT" --out "$edcprof" >/dev/null 2>&1
    if sandbox-exec -f "$edcprof" "$EDBIN" >/dev/null 2>&1; then
      ok "exec-dir: exec inside allowed dir succeeds"
    else
      bad "exec-dir: exec inside allowed dir succeeds"
    fi
    # Write outside the RW scope is denied — target the test's own temp tree
    # ($BASE is outside --rw "$RUN_WT"), never the real $HOME.
    if sandbox-exec -f "$edcprof" bash -c "echo x > $BASE/exec-dir-denied" >/dev/null 2>&1; then
      bad "exec-dir: write outside rw scope still denied"
    else
      ok "exec-dir: write outside rw scope still denied"
    fi
    rm -f "$BASE/exec-dir-denied" 2>/dev/null
    if [ -x "$BIN" ] && [ "$(dirname "$BIN")" != "$EDDIR" ]; then
      if sandbox-exec -f "$edcprof" "$BIN" >/dev/null 2>&1; then
        bad "exec-dir: exec outside allowed dirs still denied"
      else
        ok "exec-dir: exec outside allowed dirs still denied"
      fi
    else
      echo "skip - exec-dir: exec outside allowed dirs still denied (no distinct fixture binary)"
    fi
  else
    echo "skip - exec-dir: confinement checks (no sed/env fixture found)"
  fi
else
  echo "skip - exec-dir: confinement checks (sandbox-exec not available)"
fi

# --- out-of-jail launch escape is closed (toolchain exec + mach brokers) -------
# Broadening exec to whole bin dirs puts /bin/launchctl and /usr/bin/open in
# reach; a job they broker to launchd/LaunchServices runs OUTSIDE the jail. The
# template must deny both the submission mach services and exec of the binaries.
escprof="$BASE/escape.sb"
"$SCRIPT" render-profile --exec-dir /bin --rw "$RUN_WT" --out "$escprof" >/dev/null 2>&1
escbody="$(cat "$escprof" 2>/dev/null)"
have "escape: denies mach-lookup to launchd" 'com.apple.xpc.launchd' "$escbody"
have "escape: denies mach-lookup to launchservices" 'com.apple.coreservices.launchservicesd' "$escbody"
have "escape: denies exec of launchctl" '(literal "/bin/launchctl")' "$escbody"
have "escape: denies exec of open" '(literal "/usr/bin/open")' "$escbody"
have "escape: denies exec of osascript" '(literal "/usr/bin/osascript")' "$escbody"
if command -v sandbox-exec >/dev/null 2>&1 && [ -x /bin/launchctl ]; then
  # /bin is exec-allowed here, so only the explicit process-exec deny can block
  # launchctl — a clean signal the escape binary is walled off, not merely absent.
  if sandbox-exec -f "$escprof" /bin/launchctl help >/dev/null 2>&1; then
    bad "escape: launchctl exec denied even with /bin allowed"
  else
    ok "escape: launchctl exec denied even with /bin allowed"
  fi
else
  echo "skip - escape: launchctl runtime deny (sandbox-exec or launchctl absent)"
fi

# --- render-settings: layer-2 egress allowlist narrowing (task 2) -------------
sj="$BASE/settings.json"
"$SCRIPT" render-settings --source linear --coder codex --out "$sj" >/dev/null 2>&1
sbody="$(cat "$sj" 2>/dev/null)"
have "settings: sandbox enabled" '"enabled":true' "$sbody"
have "settings: deny-default allowlist" '"allowedDomains"' "$sbody"
have "settings: loopback bind off by default" '"allowLocalBinding":false' "$sbody"
have "settings: {codex,linear} has openai" 'api.openai.com' "$sbody"
have "settings: {codex,linear} has linear" 'api.linear.app' "$sbody"
have "settings: always has anthropic" 'api.anthropic.com' "$sbody"
lack "settings: codex run omits devin" 'api.devin.ai' "$sbody"
lack "settings: no broad googleapis" 'googleapis' "$sbody"
if command -v python3 >/dev/null 2>&1; then
  if python3 -m json.tool "$sj" >/dev/null 2>&1; then ok "settings: valid JSON"; else bad "settings: valid JSON"; fi
else
  echo "skip - settings: JSON validity (python3 absent)"
fi

# plan source omits the linear host
"$SCRIPT" render-settings --source plan --coder codex --out "$BASE/plan.json" >/dev/null 2>&1
lack "settings: plan source omits linear" 'api.linear.app' "$(cat "$BASE/plan.json" 2>/dev/null)"

# fail-closed: unresolved / wildcard agy host writes nothing
sfc() { # <name> <want-substr> <args...>
  local name="$1" want="$2"
  shift 2
  local t="$BASE/sfc.json"
  rm -f "$t"
  local o c
  o="$("$SCRIPT" render-settings "$@" --out "$t" 2>&1)"
  c=$?
  if [ "$c" = 2 ] && [ ! -e "$t" ] && printf '%s' "$o" | grep -qF "$want"; then
    ok "settings fail-closed: $name"
  else
    bad "settings fail-closed: $name" "exit=$c wrote=$([ -e "$t" ] && echo YES || echo no) msg=$o"
  fi
}
sfc "agy needs --agy-host" "requires --agy-host" --source plan --coder agy
sfc "agy rejects wildcard" "never a wildcard" --source plan --coder agy --agy-host '*.googleapis.com'
sfc "unknown source" "unknown --source" --source bogus --coder codex
# host-value injection: a per-run --mcp-host must not smuggle a bare wildcard or JSON
sfc "mcp bare wildcard" "invalid egress host" --source plan --coder codex --mcp-host '*'
sfc "mcp JSON injection" "invalid egress host" --source plan --coder codex --mcp-host 'x","*'
sfc "mcp bad chars" "invalid egress host" --source plan --coder codex --mcp-host 'evil;rm'
# a well-formed mcp host and a legit subdomain wildcard are accepted
"$SCRIPT" render-settings --source plan --coder codex --mcp-host mcp.example.com --out "$BASE/mcp.json" >/dev/null 2>&1
have "settings: valid mcp host accepted" 'mcp.example.com' "$(cat "$BASE/mcp.json" 2>/dev/null)"
have "settings: github wildcard kept" '*.githubusercontent.com' "$(cat "$BASE/mcp.json" 2>/dev/null)"

# --add-task-host: a plan-source run's add-task destination is allowed regardless
# of --source (a plan run whose add-task handler routes to Linear still needs egress)
"$SCRIPT" render-settings --source plan --add-task-host api.linear.app --out "$BASE/addtask.json" >/dev/null 2>&1
have "settings: plan source + add-task-host allows linear" 'api.linear.app' "$(cat "$BASE/addtask.json" 2>/dev/null)"
sfc "add-task-host bare wildcard" "invalid egress host" --source plan --add-task-host '*'
sfc "add-task-host bad chars" "invalid egress host" --source plan --add-task-host 'evil*'
sfc "add-task-host empty" "invalid egress host" --source plan --add-task-host ''
sfc "add-task-host JSON injection" "invalid egress host" --source plan --add-task-host 'x","*'
sfc "add-task-host embedded newline" "invalid egress host" --source plan --add-task-host $'good.com\nevil*.com'

# --- hardening: --confine-under bounds write scopes (task 3, Fable #3) ---------
mkdir -p "$BASE/root/wt"
# Render with a run-owned --tmpdir inside the RW worktree: cf.sb is reused by the
# write-launch tests below, which read TMPDIR back from its @spawn-tmpdir stamp.
"$SCRIPT" render-profile --confine-under "$BASE/root" --rw "$BASE/root/wt" --tmpdir "$BASE/root/wt/tmp" --exec "$BIN" --out "$BASE/cf.sb" >/dev/null 2>&1 \
  && ok "confine-under: rw inside root accepted" || bad "confine-under: rw inside root accepted"
have "render: profile carries the @spawn-tmpdir stamp" ";; @spawn-tmpdir: $BASE/root/wt/tmp" "$(cat "$BASE/cf.sb" 2>/dev/null)"
# --tmpdir must sit inside a confinement root when confined (bounds the job's mkdir).
tdesc="$("$SCRIPT" render-profile --confine-under "$BASE/root" --rw "$BASE/root/wt" --tmpdir "$BASE/elsewhere/tmp" --out "$BASE/td.sb" 2>&1)"
tdc=$?
[ "$tdc" = 2 ] && printf '%s' "$tdesc" | grep -qF 'escapes --confine-under' \
  && ok "render: --tmpdir outside confine root fails closed" || bad "render: --tmpdir outside confine root fails closed" "$tdesc"
# SBPL line-injection: a scope path containing a newline (which could smuggle a
# fake `;; @spawn-tmpdir:` line or its own allow rule) is rejected fail-closed.
nlpath="$(printf '/x\n;; @spawn-tmpdir: /evil')"
nlo="$("$SCRIPT" render-profile --rw "$nlpath" --tmpdir "$BASE/root/wt/tmp" --out "$BASE/nl.sb" 2>&1)"
nlc=$?
[ "$nlc" = 2 ] && [ ! -e "$BASE/nl.sb" ] && printf '%s' "$nlo" | grep -qF 'newline' \
  && ok "render: a scope path with a newline fails closed" || bad "render: a scope path with a newline fails closed" "exit=$nlc"
cfo="$("$SCRIPT" render-profile --confine-under "$BASE/root" --rw / --out "$BASE/cfx.sb" 2>&1)"
cfc=$?
if [ "$cfc" = 2 ] && [ ! -e "$BASE/cfx.sb" ] && printf '%s' "$cfo" | grep -qF 'refusing --rw /'; then
  ok "confine-under: rw / fails closed"
else
  bad "confine-under: rw / fails closed" "exit=$cfc"
fi
# floor holds even WITHOUT --confine-under (the guard is opt-in; the floor isn't)
rfo="$("$SCRIPT" render-profile --rw / --out "$BASE/rf.sb" 2>&1)"
rfc=$?
[ "$rfc" = 2 ] && [ ! -e "$BASE/rf.sb" ] && printf '%s' "$rfo" | grep -qF 'refusing --rw /' \
  && ok "floor: rw / refused with no --confine-under" || bad "floor: rw / refused with no --confine-under" "exit=$rfc"
# a sibling that shares a prefix but is NOT under the root is rejected (literal prefix)
mkdir -p "$BASE/rootX/wt"
sib="$("$SCRIPT" render-profile --confine-under "$BASE/root" --rw "$BASE/rootX/wt" --out "$BASE/sib.sb" 2>&1)"
sibc=$?
[ "$sibc" = 2 ] && printf '%s' "$sib" | grep -qF 'escapes --confine-under' \
  && ok "confine-under: prefix-sibling rejected" || bad "confine-under: prefix-sibling rejected" "exit=$sibc"

# --- cred-ro: a credential file stays RO inside an RW state dir (task 3, P1 #5) --
# A tool state dir is --rw (its sessions/caches must be writable), but its own
# token must not be — the (subpath) write allow would otherwise cover it.
STATE="$BASE/state"
mkdir -p "$STATE"
CREDF="$STATE/auth.json"
printf '{"token":"secret"}\n' >"$CREDF"
crprof="$BASE/cred.sb"
"$SCRIPT" render-profile --rw "$STATE" --cred-ro "$CREDF" --exec "$BIN" --out "$crprof" >/dev/null 2>&1
crbody="$(cat "$crprof" 2>/dev/null)"
have "cred-ro: emits a deny file-write* block" '(deny file-write*' "$crbody"
have "cred-ro: denies the credential literal" "(literal \"$CREDF\")" "$crbody"
lack "cred-ro: no @@tokens@@ remain" '@@' "$crbody"
# Ordering is load-bearing AND must be proved precisely: Seatbelt takes the LAST
# matching rule, so the cred deny must follow the state dir's *file-write* allow*
# specifically — not merely some earlier write rule (the static /dev/null allow),
# and not the state path's *read* allow. Walk the profile: confirm the STATE
# subpath appears inside a `(allow file-write* …)` block, then a `(deny
# file-write* …)` block carrying the cred literal comes AFTER it. This is the only
# in-jail proof when the behavioral sandbox checks below are skipped.
order_ok="$(awk -v st="(subpath \"$STATE\")" -v cr="(literal \"$CREDF\")" '
  /\(allow file-write\*/ { mode="w" }
  /\(deny file-write\*/  { mode="d" }
  mode=="w" && index($0, st) { wl=NR }
  mode=="d" && wl && index($0, cr) { print "yes"; exit }
' "$crprof")"
if [ "$order_ok" = yes ]; then
  ok "cred-ro: cred deny follows the state-dir write allow (precise)"
else
  bad "cred-ro: cred deny follows the state-dir write allow (precise)"
fi
# no --cred-ro → placeholder comment, no stray deny form
"$SCRIPT" render-profile --rw "$STATE" --exec "$BIN" --out "$BASE/nocred.sb" >/dev/null 2>&1
lack "cred-ro: absent → no deny form" '(deny file-write*' "$(cat "$BASE/nocred.sb" 2>/dev/null)"
# fail-closed: a missing file / a directory writes nothing and exits 2
fc "cred-ro missing file" "does not exist" --cred-ro "$BASE/nope-cred"
fc "cred-ro is a dir" "not a file" --cred-ro "$STATE"

# behavioral proof (macOS only): the state dir is writable, the token is not,
# and the token is still READABLE (the deny is write-only).
if command -v sandbox-exec >/dev/null 2>&1; then
  if o="$("$SCRIPT" check-profile "$crprof" 2>&1)"; then
    ok "check-profile: cred-ro profile compiles"
  else
    bad "check-profile: cred-ro profile compiles" "$o"
  fi
  BASHBIN="$(command -v bash)"
  crxprof="$BASE/credx.sb"
  "$SCRIPT" render-profile --rw "$STATE" --cred-ro "$CREDF" --exec "$BASHBIN" --out "$crxprof" >/dev/null 2>&1
  # Use only bash builtins (echo / redirection / read) inside the jailed shell:
  # the profile permits exec of bash ONLY, so shelling out to `cat` etc. would be
  # denied regardless of file permissions and false-fail the assertion. Paths are
  # passed as quoted positionals so a space/metachar path can't reparse.
  if sandbox-exec -f "$crxprof" "$BASHBIN" -c 'echo x > "$1/session"' _ "$STATE" >/dev/null 2>&1; then
    ok "cred-ro: write to the state dir is allowed"
  else
    bad "cred-ro: write to the state dir is allowed"
  fi
  if sandbox-exec -f "$crxprof" "$BASHBIN" -c 'echo y > "$1"' _ "$CREDF" >/dev/null 2>&1; then
    bad "cred-ro: write to the credential file is denied"
  else
    ok "cred-ro: write to the credential file is denied"
  fi
  # readability via a pure builtin (`read` + input redirection) — no external exec.
  if sandbox-exec -f "$crxprof" "$BASHBIN" -c 'read -r _ < "$1"' _ "$CREDF" >/dev/null 2>&1; then
    ok "cred-ro: the credential file is still readable"
  else
    bad "cred-ro: the credential file is still readable"
  fi
  rm -f "$STATE/session" 2>/dev/null
else
  echo "skip - cred-ro: behavioral checks (sandbox-exec not available)"
fi

# --- --workdir: the pause-exempt LEDGER's own file stays RO inside an RW workdir -
# Same shape as --cred-ro above, different file and different reason: the
# no-progress guard's pause exemption is bounded by a cumulative-time ledger in
# supervisor-state, and that ledger is only an authority the agent cannot forge
# if the agent cannot write it — even though the run worktree (where it lives)
# is otherwise --rw.
WDROOT="$BASE/wd"
mkdir -p "$WDROOT/.auto-pilot"
wdprof="$BASE/wd.sb"
"$SCRIPT" render-profile --rw "$WDROOT" --workdir "$WDROOT" --exec "$BIN" --out "$wdprof" >/dev/null 2>&1
wdbody="$(cat "$wdprof" 2>/dev/null)"
have "--workdir: emits a deny file-write* block" '(deny file-write*' "$wdbody"
have "--workdir: denies the supervisor-state literal" "(literal \"$WDROOT/.auto-pilot/supervisor-state\")" "$wdbody"
lack "--workdir: no @@tokens@@ remain" '@@' "$wdbody"
# Precise ordering, same technique as the cred-ro proof above: the RW allow for
# $WDROOT must appear, and THEN (last-match-wins) the deny for the state file.
wd_order_ok="$(awk -v st="(subpath \"$WDROOT\")" -v sf="(literal \"$WDROOT/.auto-pilot/supervisor-state\")" '
  /\(allow file-write\*/ { mode="w" }
  /\(deny file-write\*/  { mode="d" }
  mode=="w" && index($0, st) { wl=NR }
  mode=="d" && wl && index($0, sf) { print "yes"; exit }
' "$wdprof")"
if [ "$wd_order_ok" = yes ]; then
  ok "--workdir: supervisor-state deny follows the workdir's write allow (precise)"
else
  bad "--workdir: supervisor-state deny follows the workdir's write allow (precise)"
fi
# The Seatbelt deny stops the agent WRITING the ledger — but `git add` only READS
# it and writes the index, so an agent told to "commit run state" could stage the
# very file the deny protects. Once TRACKED, a later checkout/reset tries to
# restore it, hits the deny, and fails the git operation. Assert the EFFECT (git
# genuinely refuses to stage it), not merely that a .gitignore line exists — the
# line is the mechanism, being untracked is the property.
if command -v git >/dev/null 2>&1; then
  GIROOT="$BASE/gitignore-ledger"
  mkdir -p "$GIROOT"
  git init -q "$GIROOT" 2>/dev/null
  git -C "$GIROOT" config user.email t@t
  git -C "$GIROOT" config user.name t
  git -C "$GIROOT" commit -q --allow-empty -m base 2>/dev/null
  : >"$GIROOT/gi.log"
  (cd "$GIROOT" && "$SCRIPT" supervisor-check --exit-code 0 --log "$GIROOT/gi.log" --dir "$GIROOT" \
    --label com.autopilot.gi --state "$GIROOT/.auto-pilot/supervisor-state" >/dev/null 2>&1) || true
  if [ -f "$GIROOT/.auto-pilot/supervisor-state" ]; then
    # `git add` must SUCCEED before its result means anything: a swallowed failure
    # (locked index, broken fixture) leaves an empty cached diff, which reads as
    # "the ledger wasn't staged" and passes the test without ever exercising it.
    if ! git -C "$GIROOT" add -A >/dev/null 2>&1; then
      bad "supervisor-state: precondition — \`git add -A\` succeeds" "git add failed in $GIROOT"
    elif git -C "$GIROOT" diff --cached --name-only | grep -qxF '.auto-pilot/supervisor-state'; then
      bad "supervisor-state: a plain \`git add -A\` STAGED the supervisor ledger" \
        "the Seatbelt deny protects writes, not \`git add\` — once tracked, checkout/reset fails"
    else
      ok "supervisor-state: \`git add -A\` cannot stage the supervisor ledger"
    fi
    git -C "$GIROOT" reset -q >/dev/null 2>&1 || true
  else
    bad "supervisor-state: precondition — the ledger was written" "not found in $GIROOT/.auto-pilot"
  fi
else
  echo "skip - supervisor-state: \`git add\` cannot stage the ledger (git not available)"
fi

# The leaf file need not exist yet (tolerated like --tmpdir tolerates the
# harness's lazily created dirs) — a workdir whose .auto-pilot/supervisor-state
# hasn't been written yet must still render, not fail closed.
WDROOT2="$BASE/wd2"
mkdir -p "$WDROOT2" # note: no .auto-pilot/ at all yet
if o="$("$SCRIPT" render-profile --rw "$WDROOT2" --workdir "$WDROOT2" --exec "$BIN" --out "$BASE/wd2.sb" 2>&1)"; then
  ok "--workdir: a not-yet-existing .auto-pilot/supervisor-state does not fail closed"
else
  bad "--workdir: a not-yet-existing .auto-pilot/supervisor-state does not fail closed" "$o"
fi
have "--workdir: still denies the (not-yet-existing) literal" \
  "(literal \"$WDROOT2/.auto-pilot/supervisor-state\")" "$(cat "$BASE/wd2.sb" 2>/dev/null)"
# no --workdir → placeholder comment, no stray deny form (an older caller's
# render keeps its prior, weaker shape rather than failing).
"$SCRIPT" render-profile --rw "$WDROOT" --exec "$BIN" --out "$BASE/nowd.sb" >/dev/null 2>&1
lack "--workdir: absent → no deny form" '(deny file-write*' "$(cat "$BASE/nowd.sb" 2>/dev/null)"
# fail-closed: a relative --workdir is refused.
fc "--workdir must be absolute" "must be absolute" --rw "$WDROOT" --workdir "wd" --exec "$BIN"
if command -v sandbox-exec >/dev/null 2>&1; then
  if o="$("$SCRIPT" check-profile "$wdprof" 2>&1)"; then
    ok "check-profile: --workdir profile compiles"
  else
    bad "check-profile: --workdir profile compiles" "$o"
  fi
  BASHBIN2="$(command -v bash)"
  wdxprof="$BASE/wdx.sb"
  "$SCRIPT" render-profile --rw "$WDROOT" --workdir "$WDROOT" --exec "$BASHBIN2" --out "$wdxprof" >/dev/null 2>&1
  if sandbox-exec -f "$wdxprof" "$BASHBIN2" -c 'echo x > "$1/.auto-pilot/other"' _ "$WDROOT" >/dev/null 2>&1; then
    ok "--workdir: write elsewhere in the run worktree is allowed"
  else
    bad "--workdir: write elsewhere in the run worktree is allowed"
  fi
  if sandbox-exec -f "$wdxprof" "$BASHBIN2" -c 'echo y > "$1/.auto-pilot/supervisor-state"' _ "$WDROOT" >/dev/null 2>&1; then
    bad "--workdir: write to the supervisor-state ledger is denied"
  else
    ok "--workdir: write to the supervisor-state ledger is denied"
  fi
  rm -f "$WDROOT/.auto-pilot/other" 2>/dev/null
else
  echo "skip - --workdir: behavioral checks (sandbox-exec not available)"
fi

# allowLocalBinding flag flips to true (task 3, Fable #6)
"$SCRIPT" render-settings --source plan --coder codex --allow-local-binding --out "$BASE/lb.json" >/dev/null 2>&1
have "settings: --allow-local-binding sets true" '"allowLocalBinding":true' "$(cat "$BASE/lb.json" 2>/dev/null)"

# --- write-launch: launch script + plist generation (task 3) ------------------
# Pass --claude-bin "$BIN" (a fixture) so the harness needs no real claude on PATH.
printf 'run the graph\n' >"$BASE/prompt.txt"
"$SCRIPT" render-settings --source plan --coder codex --out "$BASE/wl.json" >/dev/null 2>&1
LAUNCH_PATH='/opt/homebrew/bin:/usr/bin:/bin'
wlout="$("$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --until 'T' --label com.autopilot.test --claude-bin "$BIN" \
  --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" --out-script "$BASE/launch.sh" --out-plist "$BASE/job.plist" 2>&1)"
lbody="$(cat "$BASE/launch.sh" 2>/dev/null)"
have "launch: composes sandbox-exec -f" 'sandbox-exec -f' "$lbody"
have "launch: invokes resolved claude bin" "$BIN" "$lbody"
have "launch: -p reads prompt from file" '-p "$(cat' "$lbody"
have "launch: bypassPermissions flag" '--permission-mode bypassPermissions' "$lbody"
have "launch: passes --settings" '--settings' "$lbody"
have "launch: redirects to log" ">>$BASE/o.log" "$lbody"
have "launch: emits --verbose" '--verbose' "$lbody"
have "launch: exports resolved PATH" "export PATH=$LAUNCH_PATH" "$lbody"
verbose_ln="$(printf '%s\n' "$lbody" | grep -n -- '--verbose' | head -1 | cut -d: -f1)"
sjson_ln="$(printf '%s\n' "$lbody" | grep -n -- '--output-format stream-json' | head -1 | cut -d: -f1)"
if [ -n "$verbose_ln" ] && [ -n "$sjson_ln" ] && [ "$verbose_ln" -lt "$sjson_ln" ]; then
  ok "launch: --verbose precedes --output-format stream-json"
else
  bad "launch: --verbose precedes --output-format stream-json" "verbose@$verbose_ln sjson@$sjson_ln"
fi
# --park-limit defaults to 3 in the generated wrapper when omitted (the flag was
# parsed by supervisor-check/-scan but never emitted by write-launch, so every
# production wake silently used the default — asserted against the real wrapper).
# Asserted PER LINE, never against the whole wrapper body: `--park-limit 7` also
# appears on the supervisor-CHECK line, so a body-wide substring match cannot tell
# the two emissions apart — drop the flag from the supervisor-SCAN printf alone and
# a body-wide assertion still passes, blessing exactly half of the bug this task
# exists to fix (and the half that matters most: supervisor-scan runs ABOVE the
# gate, so it is the only park-storm alarm path on a gated wake).
lscan="$(printf '%s\n' "$lbody" | grep 'supervisor-scan' || true)"
lchk="$(printf '%s\n' "$lbody" | grep 'supervisor-check' || true)"
have "launch: supervisor-scan defaults --park-limit to 3" '--park-limit 3' "$lscan"
have "launch: supervisor-check defaults --park-limit to 3" '--park-limit 3' "$lchk"
# an explicit --park-limit is threaded into BOTH the supervisor-scan and
# supervisor-check invocations in the generated wrapper.
"$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.pl --claude-bin "$BIN" \
  --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" --park-limit 7 \
  --out-script "$BASE/pl.sh" --out-plist "$BASE/pl.plist" >/dev/null 2>&1
plbody="$(cat "$BASE/pl.sh" 2>/dev/null)"
pscan="$(printf '%s\n' "$plbody" | grep 'supervisor-scan' || true)"
pchk="$(printf '%s\n' "$plbody" | grep 'supervisor-check' || true)"
have "launch: --park-limit threaded into supervisor-scan" '--park-limit 7' "$pscan"
have "launch: --park-limit threaded into supervisor-check" '--park-limit 7' "$pchk"
# --pause-exempt-max defaults to 21600 (6h) in the generated wrapper when
# omitted. It belongs ONLY on the supervisor-SCAN line — the cap is enforced
# above the gate (a far-future paused_until means supervisor-check never runs
# at all), so it must never be threaded onto supervisor-check the way
# --park-limit/--no-progress-limit are. Asserted per line, same reasoning as
# the --park-limit assertion above: a body-wide substring match cannot tell
# "present on scan" from "present on check" apart.
have "launch: supervisor-scan defaults --pause-exempt-max to 21600" '--pause-exempt-max 21600' "$lscan"
lack "launch: supervisor-check never receives --pause-exempt-max" '--pause-exempt-max' "$lchk"
# an explicit --pause-exempt-max is threaded into supervisor-scan, and ONLY
# supervisor-scan.
"$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.pem --claude-bin "$BIN" \
  --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" --pause-exempt-max 90 \
  --out-script "$BASE/pem.sh" --out-plist "$BASE/pem.plist" >/dev/null 2>&1
pembody="$(cat "$BASE/pem.sh" 2>/dev/null)"
pemscan="$(printf '%s\n' "$pembody" | grep 'supervisor-scan' || true)"
pemchk="$(printf '%s\n' "$pembody" | grep 'supervisor-check' || true)"
have "launch: --pause-exempt-max threaded into supervisor-scan" '--pause-exempt-max 90' "$pemscan"
lack "launch: --pause-exempt-max never threaded into supervisor-check" '--pause-exempt-max' "$pemchk"
# fail-closed: garbage --pause-exempt-max is refused, and nothing is written.
pembad="$("$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.pembad --claude-bin "$BIN" \
  --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" --pause-exempt-max 0 \
  --out-script "$BASE/pembad.sh" --out-plist "$BASE/pembad.plist" 2>&1)"
pembadc=$?
[ "$pembadc" = 2 ] && [ ! -e "$BASE/pembad.sh" ] && printf '%s' "$pembad" | grep -qF 'positive integer' \
  && ok "write-launch: --pause-exempt-max 0 fails closed" \
  || bad "write-launch: --pause-exempt-max 0 fails closed" "exit=$pembadc $pembad"
# fail-closed at the supervisor-scan CLI itself, not just at write-launch's
# generation-time check — the two validate independently.
scpe="$("$SCRIPT" supervisor-scan --dir "$BASE" --label com.autopilot.pescan --pause-exempt-max abc 2>&1)"
scpec=$?
[ "$scpec" = 2 ] && printf '%s' "$scpe" | grep -qF 'positive integer' \
  && ok "supervisor-scan: --pause-exempt-max abc fails closed" \
  || bad "supervisor-scan: --pause-exempt-max abc fails closed" "exit=$scpec $scpe"
# both supervisor thresholds must survive the `launch` passthrough, which is the
# path production uses. A flag write-launch parses but launch rejects is the same
# lie this task exists to remove.
for lim in --park-limit --no-progress-limit --pause-exempt-max; do
  lpass="$("$SCRIPT" launch "$lim" 9 --dry-run 2>&1 || true)"
  case "$lpass" in
    *"unknown launch argument: $lim"*) bad "launch: $lim survives the launch passthrough" "$lpass" ;;
    *) ok "launch: $lim survives the launch passthrough" ;;
  esac
done
# fail-closed: --path is required
wlnopath="$("$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.nopath --claude-bin "$BIN" \
  --out-script "$BASE/nopath.sh" --out-plist "$BASE/nopath.plist" 2>&1)"
[ $? = 2 ] && [ ! -e "$BASE/nopath.sh" ] && printf '%s' "$wlnopath" | grep -qF 'requires --path' \
  && ok "launch: missing --path fails closed" || bad "launch: missing --path fails closed" "$wlnopath"
if command -v plutil >/dev/null 2>&1; then
  if plutil -lint "$BASE/job.plist" >/dev/null 2>&1; then ok "launch: plist lints"; else bad "launch: plist lints"; fi
  # plist injection: an XML-metachar path must still yield a VALID plist (escaped).
  mkdir -p "$BASE/a&b<x"
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/a&b<x" \
    --log "$BASE/a&b<x/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.esc --claude-bin "$BIN" \
    --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" --out-script "$BASE/e.sh" --out-plist "$BASE/e.plist" >/dev/null 2>&1
  if plutil -lint "$BASE/e.plist" >/dev/null 2>&1; then ok "launch: XML-metachar path still lints (escaped)"; else bad "launch: XML-metachar path still lints (escaped)"; fi
else
  echo "skip - launch: plist lint (plutil absent)"
fi
# label injection rejected at the source (defense-in-depth on top of xml_escape)
"$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label 'a</string><key>x' --claude-bin "$BIN" \
  --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" --out-script "$BASE/i.sh" --out-plist "$BASE/i.plist" >/dev/null 2>&1 \
  && bad "launch: injecting label rejected" || ok "launch: injecting label rejected"
# write-launch fail-closed on a missing input file
wlfc="$("$SCRIPT" write-launch --profile "$BASE/nope.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label x --claude-bin "$BIN" --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" --out-script "$BASE/x.sh" --out-plist "$BASE/x.plist" 2>&1)"
[ $? = 2 ] && printf '%s' "$wlfc" | grep -qF 'not found' && ok "launch: missing profile fails closed" || bad "launch: missing profile fails closed"

# TMPDIR is now derived from the profile's @spawn-tmpdir stamp, not a required
# flag — so a missing --tmpdir SUCCEEDS and the launch script exports the stamped
# dir (proving render and launch can't drift).
"$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.tm --claude-bin "$BIN" --path "$LAUNCH_PATH" \
  --out-script "$BASE/tm.sh" --out-plist "$BASE/tm.plist" >/dev/null 2>&1
have "launch: TMPDIR derived from the profile stamp (no --tmpdir needed)" \
  "export TMPDIR=$BASE/root/wt/tmp" "$(cat "$BASE/tm.sh" 2>/dev/null)"
have "launch: the job mkdir -p's its TMPDIR" "mkdir -p $BASE/root/wt/tmp" "$(cat "$BASE/tm.sh" 2>/dev/null)"
# a relative --tmpdir is still rejected (absolute check runs before the cross-check)
wltr="$("$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label x --claude-bin "$BIN" --path "$LAUNCH_PATH" \
  --tmpdir "relative/tmp" --out-script "$BASE/tr.sh" --out-plist "$BASE/tr.plist" 2>&1)"
[ $? = 2 ] && printf '%s' "$wltr" | grep -qF 'must be absolute' \
  && ok "launch: relative --tmpdir fails closed" || bad "launch: relative --tmpdir fails closed" "$wltr"
# a --tmpdir that does NOT match the profile's stamp is fail-closed (the exact
# render/launch drift that silently degraded the inner sandbox).
wltx="$("$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label x --claude-bin "$BIN" --path "$LAUNCH_PATH" \
  --tmpdir "$BASE/root/wt/OTHER" --out-script "$BASE/tx.sh" --out-plist "$BASE/tx.plist" 2>&1)"
[ $? = 2 ] && printf '%s' "$wltx" | grep -qF 'does not match the profile' \
  && ok "launch: --tmpdir mismatching the stamp fails closed" || bad "launch: --tmpdir mismatching the stamp fails closed" "$wltx"
# a profile with no @spawn-tmpdir stamp (stale render) is fail-closed.
grep -v '@spawn-tmpdir' "$BASE/cf.sb" >"$BASE/nostamp.sb"
wltn="$("$SCRIPT" write-launch --profile "$BASE/nostamp.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label x --claude-bin "$BIN" --path "$LAUNCH_PATH" \
  --out-script "$BASE/tn.sh" --out-plist "$BASE/tn.plist" 2>&1)"
[ $? = 2 ] && printf '%s' "$wltn" | grep -qF 'no @spawn-tmpdir stamp' \
  && ok "launch: profile with no stamp fails closed" || bad "launch: profile with no stamp fails closed" "$wltn"
# Belt to the braces (canonicalize's newline guard blocks the injection at the
# source): even if an earlier `;; @spawn-tmpdir:` line were present, the REAL
# stamp is appended LAST, so write-launch (tail -1) must pick it, not the forgery.
{
  printf ';; @spawn-tmpdir: /evil/tmp\n'
  cat "$BASE/cf.sb"
} >"$BASE/forged.sb"
"$SCRIPT" write-launch --profile "$BASE/forged.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.fg --claude-bin "$BIN" --path "$LAUNCH_PATH" \
  --out-script "$BASE/fg.sh" --out-plist "$BASE/fg.plist" >/dev/null 2>&1
have "launch: a forged earlier stamp loses to the real (last) one" "export TMPDIR=$BASE/root/wt/tmp" "$(cat "$BASE/fg.sh" 2>/dev/null)"
lack "launch: the forged stamp is NOT used" "export TMPDIR=/evil/tmp" "$(cat "$BASE/fg.sh" 2>/dev/null)"

# --- record-handle: dead pid / non-numeric pid fail closed (task 3) -----------
rho="$("$SCRIPT" record-handle --pid 999999 --out "$BASE/h.txt" 2>&1)"
[ $? = 2 ] && [ ! -e "$BASE/h.txt" ] && printf '%s' "$rho" | grep -qF 'no live process' && ok "record-handle: dead pid fails closed" || bad "record-handle: dead pid fails closed"
"$SCRIPT" record-handle --pid abc --out "$BASE/h.txt" >/dev/null 2>&1 && bad "record-handle: non-numeric pid fails" || ok "record-handle: non-numeric pid fails"

# The report's --gh / --usage-bin are explicit-only downstream so the suite can
# never reach a real gh. That makes `launch` — the production entry point — the
# only thing that can supply them: if it rejects or drops these flags, the
# report's PR reconciliation (the section that caught finding #23) is dead in
# production while every test stays green. Same defect as a parsed-but-unemitted
# --park-limit; pin the whole passthrough.
for lim in --report-every --report-gh --report-usage-bin --park-limit --no-progress-limit; do
  lpass="$("$SCRIPT" launch "$lim" 9 --dry-run 2>&1 || true)"
  case "$lpass" in
    *"unknown launch argument: $lim"*) bad "launch: $lim survives the launch passthrough" "$lpass" ;;
    *) ok "launch: $lim survives the launch passthrough" ;;
  esac
done

# --- a REAL (non-dry-run) launch reaches write_launch with the forwarded flags -
# `launch --dry-run` RETURNS BEFORE write_launch is ever called, so the
# passthrough loop above can only prove launch PARSES a flag — not that
# write_launch ACCEPTS what launch forwards. That gap shipped a real bug once
# (launch forwarded --park-limit in `wl`, write_launch had no parser: every
# real launch died with "unknown write-launch argument" while the dry-run
# tests stayed green). Drive the real path: stub claude + sandbox-exec so the
# smoke test passes, let the GUARD's launchctl stub stop the detach (print
# exits 1 → no pid → launch fails AFTER the artifacts are written). launchctl
# is already shadowed suite-wide, so nothing reaches the real launchd.
RL="$BASE/real-launch"
mkdir -p "$RL/bin"
printf '#!/bin/sh\nprintf %s\n' "'{\"type\":\"result\"}\\n'" >"$RL/bin/claude"
chmod +x "$RL/bin/claude"
printf '#!/bin/sh\nshift 2\nexec "$@"\n' >"$RL/bin/sandbox-exec"
chmod +x "$RL/bin/sandbox-exec"
rlout="$(PATH="$RL/bin:$PATH" "$SCRIPT" launch \
  --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/rl.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.rl \
  --claude-bin "$RL/bin/claude" --path "$RL/bin:$LAUNCH_PATH" \
  --park-limit 9 --pause-exempt-max 77 --no-progress-limit 4 --report-every 1m \
  --report-gh "$RL/bin/claude" --report-usage-bin "$RL/bin/claude" \
  --out-script "$RL/launch.sh" --out-plist "$RL/job.plist" --handle "$RL/handle" 2>&1)"
rlc=$?
lack "real launch: write_launch accepts every flag launch forwards (no unknown-argument die)" \
  'unknown write-launch argument' "$rlout"
have "real launch: got past write-launch AND the smoke test (the real order ran)" \
  'smoke-test OK' "$rlout"
rlbody="$(cat "$RL/launch.sh" 2>/dev/null)"
rlscan="$(printf '%s\n' "$rlbody" | grep 'supervisor-scan' || true)"
rlchk="$(printf '%s\n' "$rlbody" | grep 'supervisor-check' || true)"
have "real launch: --park-limit lands on the generated supervisor-scan line" '--park-limit 9' "$rlscan"
have "real launch: --park-limit lands on the generated supervisor-check line" '--park-limit 9' "$rlchk"
have "real launch: --report-every lands on the generated supervisor-scan line" '--report-every 1m' "$rlscan"
# the failure it DOES hit is the stubbed launchctl's missing pid — i.e. the
# launch got all the way to detach, not an argument death anywhere before it.
[ "$rlc" != 0 ] && printf '%s' "$rlout" | grep -qF 'could not read the orchestrator PID' \
  && ok "real launch: fails only at the (stubbed) detach, nowhere earlier" \
  || bad "real launch: fails only at the (stubbed) detach, nowhere earlier" "exit=$rlc $rlout"

# --- launch --dry-run: the safety-critical ordering (smoke BEFORE detach) ------
dro="$("$SCRIPT" launch --dry-run --out-script "$BASE/l.sh" --out-plist "$BASE/l.plist" --label com.x --handle "$BASE/h2.txt" 2>&1)"
smoke_ln="$(printf '%s\n' "$dro" | grep -n smoke-test | cut -d: -f1)"
detach_ln="$(printf '%s\n' "$dro" | grep -n detach | cut -d: -f1)"
if [ -n "$smoke_ln" ] && [ -n "$detach_ln" ] && [ "$smoke_ln" -lt "$detach_ln" ]; then
  ok "launch: smoke-test ordered before detach"
else
  bad "launch: smoke-test ordered before detach" "smoke@$smoke_ln detach@$detach_ln"
fi

# --- smoke_test invariant: must use the REAL flag set (--verbose + stream-json) -
# so it can't green-light an invocation the real launch rejects. Grepped from
# source since smoke-test execs claude for real and can't run offline here.
smoke_src="$(sed -n '/^smoke_test()/,/^}/p' "$SCRIPT")"
have "smoke-test: uses --verbose" '--verbose' "$smoke_src"
have "smoke-test: uses --output-format stream-json" '--output-format stream-json' "$smoke_src"

# --- task 4: verify broker (run verify OUTSIDE the jail) ----------------------
# The broker just runs `bash -c "$cmd"`, so these round-trip in-jail with no
# sandbox nesting — the pin/containment/fail-closed logic is fully exercised here.
if command -v shasum >/dev/null 2>&1; then
  VB="$BASE/vb"
  mkdir -p "$VB/root/wt" "$VB/outside"
  SENT="$VB/sentinel"
  ROOT="$(cd "$VB/root" && pwd -P)"
  WT="$(cd "$VB/root/wt" && pwd -P)"
  CMD='echo VERIFY_RAN; exit 0'
  PIN="$(printf '%s' "$CMD" | shasum -a 256 | awk '{print $1}')"

  # round trip: request -> broker (one scan) -> await
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash "$PIN" --id rt >/dev/null 2>&1
  have "verify-request: writes the request sentinel" "" "$([ -e "$SENT/rt.request" ] && echo present)"
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --confine-under "$ROOT" >/dev/null 2>&1
  have "verify-broker: consumes the request" "" "$([ ! -e "$SENT/rt.request" ] && echo consumed)"
  rtres="$(cat "$SENT/rt.result" 2>/dev/null)"
  have "verify-broker: result carries code 0" 'code: 0' "$rtres"
  have "verify-broker: ran the pinned command" 'VERIFY_RAN' "$rtres"
  awo="$("$SCRIPT" verify-await --sentinel-dir "$SENT" --id rt --timeout 4 2>&1)"
  have "verify-await: reports code + output" 'code=0' "$awo"
  have "verify-await: prints the verify output" 'VERIFY_RAN' "$awo"

  # a request whose cmd_hash != the broker's pinned hash is REFUSED, never run
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash deadbeef --id mism >/dev/null 2>&1
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --confine-under "$ROOT" >/dev/null 2>&1
  mres="$(cat "$SENT/mism.result" 2>/dev/null)"
  have "verify-broker: hash mismatch refused" 'cmd_hash mismatch' "$mres"
  lack "verify-broker: refused req did not run" 'VERIFY_RAN' "$mres"

  # a worktree OUTSIDE the run root is REFUSED
  OUT="$(cd "$VB/outside" && pwd -P)"
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$OUT" --cmd-hash "$PIN" --id esc >/dev/null 2>&1
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --confine-under "$ROOT" >/dev/null 2>&1
  have "verify-broker: worktree escape refused" 'escapes --confine-under' "$(cat "$SENT/esc.result" 2>/dev/null)"

  # the broker's own --cmd-hash must match --verify-cmd (install/args mismatch)
  bhm="$("$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --cmd-hash deadbeef --confine-under "$ROOT" 2>&1)"
  bhc=$?
  if [ "$bhc" = 2 ] && printf '%s' "$bhm" | grep -qF 'does not match'; then
    ok "verify-broker: pinned hash/cmd mismatch fails closed"
  else
    bad "verify-broker: pinned hash/cmd mismatch fails closed" "exit=$bhc"
  fi

  # the 126-vs-0 contrast the whole task exists for: a #!/usr/bin/env bash script
  # the broker runs via the pinned `bash <script>` (works) — the same script's
  # direct shebang exec is what execve-denies in-jail (finding #4).
  printf '#!/usr/bin/env bash\necho SHEBANG_OK\n' >"$WT/probe.sh"
  chmod +x "$WT/probe.sh"
  CMD2='bash probe.sh'
  PIN2="$(printf '%s' "$CMD2" | shasum -a 256 | awk '{print $1}')"
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash "$PIN2" --id sb >/dev/null 2>&1
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD2" --confine-under "$ROOT" >/dev/null 2>&1
  have "verify-broker: runs a shebang script via pinned bash" 'SHEBANG_OK' "$(cat "$SENT/sb.result" 2>/dev/null)"

  # verify-request fail-closed: non-hex cmd-hash, missing worktree
  fcr() {
    local name="$1" want="$2"
    shift 2
    local o c
    o="$("$SCRIPT" verify-request "$@" 2>&1)"
    c=$?
    if [ "$c" = 2 ] && printf '%s' "$o" | grep -qF "$want"; then ok "verify-request fail-closed: $name"; else bad "verify-request fail-closed: $name" "exit=$c msg=$o"; fi
  }
  fcr "non-hex cmd-hash" "must be lowercase hex" --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash "NOTHEX"
  fcr "missing worktree" "does not exist" --sentinel-dir "$SENT" --worktree "$VB/nope" --cmd-hash "$PIN"
  printf 'x\n' >"$VB/notadir"
  fcr "worktree is a file" "is not a directory" --sentinel-dir "$SENT" --worktree "$VB/notadir" --cmd-hash "$PIN"

  # write-verify-broker: renders an UN-JAILED launch script (no sandbox-exec) + a
  # valid plist, with the verify command pinned in.
  "$SCRIPT" write-verify-broker --sentinel-dir "$SENT" --verify-cmd 'bash scripts/check.sh' \
    --confine-under "$VB" --label com.autopilot.test.verify --workdir "$WT" --log "$VB/b.log" \
    --path '/usr/bin:/bin' --out-script "$VB/broker.sh" --out-plist "$VB/broker.plist" >/dev/null 2>&1
  vbody="$(cat "$VB/broker.sh" 2>/dev/null)"
  have "write-verify-broker: invokes verify-broker" 'verify-broker' "$vbody"
  have "write-verify-broker: pins the verify command" 'bash scripts/check.sh' "$vbody"
  have "write-verify-broker: pins a cmd-hash" '--cmd-hash' "$vbody"
  lack "write-verify-broker: broker is UN-JAILED (no sandbox-exec)" 'sandbox-exec' "$vbody"
  if command -v plutil >/dev/null 2>&1; then
    if plutil -lint "$VB/broker.plist" >/dev/null 2>&1; then ok "write-verify-broker: plist lints"; else bad "write-verify-broker: plist lints"; fi
  else
    echo "skip - write-verify-broker: plist lint (plutil absent)"
  fi
  # write-verify-broker fail-closed: bad label
  wvbc="$("$SCRIPT" write-verify-broker --sentinel-dir "$SENT" --verify-cmd 'x' --confine-under "$VB" \
    --label 'bad label' --workdir "$WT" --log "$VB/b.log" --path '/usr/bin:/bin' \
    --out-script "$VB/x.sh" --out-plist "$VB/x.plist" 2>&1)"
  wvc=$?
  if [ "$wvc" = 2 ] && printf '%s' "$wvbc" | grep -qF 'must be [A-Za-z0-9._-]'; then
    ok "write-verify-broker: bad label fails closed"
  else
    bad "write-verify-broker: bad label fails closed" "exit=$wvc"
  fi
else
  echo "skip - verify broker: shasum not available (needed for the command pin)"
fi

# --- status: read-only run inspection (task 8) ---------------------------------
RUNDIR="$BASE/run"
mkdir -p "$RUNDIR/.auto-pilot"
RUNMD="$RUNDIR/.auto-pilot/RUN.md"
{
  printf -- '---\n'
  printf 'run_id: test-run\n'
  printf 'status: active\n'
  printf 'orchestrator_pid: 999999\n'
  printf 'orchestrator_started_at: "Wed Jul  9 20:00:00 2026"\n'
  printf 'until: 2026-07-10T06:00:00\n'
  printf -- '---\n'
  printf '\n'
  printf '| task | phase        | branch | base | base_sha | pr  | notes |\n'
  printf '| ---- | ------------ | ------ | ---- | -------- | --- | ----- |\n'
  printf '| T-1  | handed-off   | b1     | main | -        | #1  | ok    |\n'
  printf '| T-2  | implementing | b2     | main | -        | -   | wip   |\n'
} >"$RUNMD"
printf '{"type":"assistant","message":{"content":[{"type":"text","text":"working on T-2"}]}}\n' >"$RUNDIR/.auto-pilot/orchestrator.log"

sout="$("$SCRIPT" status --label com.autopilot.test --dir "$RUNDIR" 2>&1)"
sc=$?
[ "$sc" = 0 ] && ok "status: exits 0 on a well-formed run dir" || bad "status: exits 0 on a well-formed run dir" "$sout"
have "status: prints the phase table" 'implementing' "$sout"
have "status: prints a STATUS: line" 'STATUS: active' "$sout"
have "status: STATUS line has tasks=2" 'tasks=2' "$sout"
have "status: STATUS line has until" 'until=2026-07-10T06:00:00' "$sout"

# a bogus/dead recorded PID (999999 — never a real live process) reports not-live
have "status: dead pid reports pid=dead" 'pid=dead' "$sout"

# a live pid (this test process's own $$) with a WRONG recorded start-time must
# still report not-live: kill -0 succeeds, but the start-time can't match a
# fabricated value (and ps is unavailable in some jails, which also falls to
# the not-live branch) — either way this must never read "pid=live".
RUNMD2="$BASE/run2/.auto-pilot"
mkdir -p "$RUNMD2"
{
  printf -- '---\n'
  printf 'status: active\n'
  printf 'orchestrator_pid: %s\n' "$$"
  printf 'orchestrator_started_at: "not-a-real-timestamp"\n'
  printf 'until: 2026-07-10T06:00:00\n'
  printf -- '---\n'
  printf '| task | phase | branch | base | base_sha | pr | notes |\n'
  printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
  printf '| T-1  | claimed | b1   | main | -        | -  | -     |\n'
} >"$RUNMD2/RUN.md"
sout2="$("$SCRIPT" status --label com.autopilot.test2 --dir "$BASE/run2" 2>&1)"
lack "status: mismatched start-time never reports live" 'pid=live' "$sout2"
have "status: mismatched start-time reports mismatch" 'pid=mismatch' "$sout2"

# front-matter parser: a double-quoted `until` with a trailing comment, and a
# `paused_until` line that precedes `until`, must yield the BARE until value
# (no quotes, no comment, not the paused_until value — anchored on `^until:`).
RUNMD3="$BASE/run3/.auto-pilot"
mkdir -p "$RUNMD3"
{
  printf -- '---\n'
  printf 'status: paused\n'
  printf 'paused_until: 2020-01-01T00:00:00\n'
  printf 'until: "2026-12-31T00:00:00"   # hard deadline\n'
  printf -- '---\n'
  printf '| task | phase | branch | base | base_sha | pr | notes |\n'
  printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
  printf '| T-1  | claimed | b1   | main | -        | -  | -     |\n'
} >"$RUNMD3/RUN.md"
sout4="$("$SCRIPT" status --label com.autopilot.test3 --dir "$BASE/run3" 2>&1)"
have "status: quoted+commented until parsed bare" 'until=2026-12-31T00:00:00' "$sout4"
lack "status: until does not match paused_until" 'until=2020-01-01T00:00:00' "$sout4"
lack "status: until value keeps no quotes" 'until="2026' "$sout4"

# fail-closed: missing --label / missing RUN.md
o="$("$SCRIPT" status --dir "$RUNDIR" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --label' \
  && ok "status fail-closed: missing --label" || bad "status fail-closed: missing --label" "$o"
o="$("$SCRIPT" status --label x --dir "$BASE/no-such-dir" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -q 'no run state found' \
  && ok "status fail-closed: missing RUN.md" || bad "status fail-closed: missing RUN.md" "$o"

# --- teardown --done-sentinel: the single completion mechanism (task 8) --------
sentinel="$RUNDIR/.auto-pilot/orchestrator.done"
rm -f "$sentinel"
# launchctl bootout will fail/no-op off-macOS or in-jail — that must not stop the
# sentinel write, so don't assert on teardown's own exit code here.
"$SCRIPT" teardown --label com.autopilot.test --done-sentinel "$sentinel" >/dev/null 2>&1
[ -f "$sentinel" ] && ok "teardown: writes the done-sentinel file" || bad "teardown: writes the done-sentinel file"

sout3="$("$SCRIPT" status --label com.autopilot.test --dir "$RUNDIR" 2>&1)"
have "status: reports done once the sentinel exists" 'STATUS: done' "$sout3"

# --- assert-run-head: the run-loop / --resume HEAD guard (task 13) ------------
# Build a real git repo with a run-state branch and a task branch, so the
# fixture matches finding #23 exactly: the run worktree's HEAD found parked on
# a task branch instead of `auto-pilot/<run_id>`.
HEAD_REPO="$BASE/head-repo"
mkdir -p "$HEAD_REPO"
git -C "$HEAD_REPO" init -q -b main
git -C "$HEAD_REPO" config user.email test@example.com
git -C "$HEAD_REPO" config user.name test
: >"$HEAD_REPO/seed"
git -C "$HEAD_REPO" add seed
git -C "$HEAD_REPO" commit -q -m seed

RUN_ID="2026-07-11-test-run"
git -C "$HEAD_REPO" checkout -q -b "auto-pilot/$RUN_ID"
mkdir -p "$HEAD_REPO/.auto-pilot"
printf -- '---\nrun_id: %s\n---\ncontent-on-run-state-branch\n' "$RUN_ID" >"$HEAD_REPO/.auto-pilot/RUN.md"
git -C "$HEAD_REPO" add .auto-pilot/RUN.md
git -C "$HEAD_REPO" commit -q -m "seed RUN.md"

git -C "$HEAD_REPO" checkout -q main
git -C "$HEAD_REPO" checkout -q -b "auto-pilot/hardening-task_3"
printf 'task work\n' >"$HEAD_REPO/task-file"
git -C "$HEAD_REPO" add task-file
git -C "$HEAD_REPO" commit -q -m "task work committed on the wrong branch"
# The run worktree is now parked on the TASK branch — exactly finding #23:
# .auto-pilot/RUN.md is absent here (it only exists on the run-state branch).
[ -f "$HEAD_REPO/.auto-pilot/RUN.md" ] && bad "fixture: RUN.md absent on task branch" \
  || ok "fixture: RUN.md absent from working tree while parked on the task branch"

# (b) --resume's belt: `git show <branch>:<path>` recovers RUN.md correctly
# EVEN THOUGH HEAD is parked on the task branch right now.
shown="$(git -C "$HEAD_REPO" show "auto-pilot/$RUN_ID:.auto-pilot/RUN.md" 2>&1)"
have "git show recovers RUN.md while HEAD is parked on a task branch" \
  'content-on-run-state-branch' "$shown"

# (a) the guard fires: detects the deviation, restores HEAD, and records it.
QFILE="$BASE/QUESTIONS.md"
: >"$QFILE"
gout="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions "$QFILE" 2>&1)"
gc=$?
[ "$gc" = 0 ] && ok "assert-run-head: exits 0 after restoring" || bad "assert-run-head: exits 0 after restoring" "$gout"
have "assert-run-head: reports the deviation" 'HEAD DEVIATION restored' "$gout"
restored="$(git -C "$HEAD_REPO" rev-parse --abbrev-ref HEAD)"
[ "$restored" = "auto-pilot/$RUN_ID" ] && ok "assert-run-head: HEAD restored to the run-state branch" \
  || bad "assert-run-head: HEAD restored to the run-state branch" "got $restored"
qbody="$(cat "$QFILE")"
have "assert-run-head: records the deviation in QUESTIONS.md" 'HEAD was parked on `auto-pilot/hardening-task_3`' "$qbody"
have "assert-run-head: QUESTIONS.md entry is reversible" '**Reversible:** yes' "$qbody"

# idempotent: running it again with HEAD already correct is a silent no-op.
gout2="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions "$QFILE" 2>&1)"
gc2=$?
[ "$gc2" = 0 ] && ok "assert-run-head: exits 0 when HEAD already correct" || bad "assert-run-head: exits 0 when HEAD already correct" "$gout2"
have "assert-run-head: reports HEAD OK on a clean run" 'HEAD OK' "$gout2"
qcount="$(grep -cE '^## Q[0-9]+' "$QFILE")"
[ "$qcount" = 1 ] && ok "assert-run-head: no duplicate entry once HEAD is already correct" \
  || bad "assert-run-head: no duplicate entry once HEAD is already correct" "got $qcount entries"

# fail-closed: not a git worktree at all
mkdir -p "$BASE/plain-dir-not-a-repo"
o="$("$SCRIPT" assert-run-head --dir "$BASE/plain-dir-not-a-repo" --run-id "$RUN_ID" 2>&1)"
[ $? = 2 ] \
  && printf '%s' "$o" | grep -q 'not a git worktree' \
  && ok "assert-run-head fail-closed: not a git worktree" || bad "assert-run-head fail-closed: not a git worktree" "$o"

# fail-closed: missing required args
o="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --dir and --run-id' \
  && ok "assert-run-head fail-closed: missing --run-id" || bad "assert-run-head fail-closed: missing --run-id" "$o"

# fail-closed: a DIRTY deviation must NOT restore (a non-conflicting checkout
# would silently carry the uncommitted task-branch edits onto the run-state branch).
git -C "$HEAD_REPO" checkout -q "auto-pilot/hardening-task_3"
printf 'uncommitted edit\n' >>"$HEAD_REPO/task-file"
o="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" 2>&1)"
drc=$?
dhead="$(git -C "$HEAD_REPO" rev-parse --abbrev-ref HEAD)"
[ "$drc" = 2 ] && printf '%s' "$o" | grep -q 'uncommitted changes' && [ "$dhead" = "auto-pilot/hardening-task_3" ] \
  && ok "assert-run-head fail-closed: dirty deviation is not restored" \
  || bad "assert-run-head fail-closed: dirty deviation is not restored" "rc=$drc head=$dhead msg=$o"
git -C "$HEAD_REPO" checkout -q -- task-file # drop the dirty edit for later checks

# numbering uses the MAX existing index, not a count — a non-contiguous
# QUESTIONS.md (Q9, Q10) yields Q11, never a colliding low number.
git -C "$HEAD_REPO" checkout -q "auto-pilot/hardening-task_3"
QFILE2="$BASE/Q-noncontig.md"
printf '## Q9 — X — a\n\n## Q10 — X — b\n' >"$QFILE2"
"$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions "$QFILE2" >/dev/null 2>&1
grep -q '^## Q11 — RUN —' "$QFILE2" \
  && ok "assert-run-head: numbers from the max index (Q11 after Q9/Q10)" \
  || bad "assert-run-head: numbers from the max index" "$(cat "$QFILE2")"

# a relative --questions resolves against --dir (the run worktree), matching the
# documented `.auto-pilot/QUESTIONS.md` invocation — not the caller's cwd.
git -C "$HEAD_REPO" checkout -q "auto-pilot/hardening-task_3"
rm -f "$HEAD_REPO/QREL.md"
"$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions QREL.md >/dev/null 2>&1
[ -f "$HEAD_REPO/QREL.md" ] && grep -q 'HEAD was parked' "$HEAD_REPO/QREL.md" \
  && ok "assert-run-head: relative --questions resolves against --dir" \
  || bad "assert-run-head: relative --questions resolves against --dir" "$(ls "$HEAD_REPO" 2>&1)"
# --- classify-exit: supervisor-side exit classification (task 10, #22) -------
CX="$BASE/cx"
mkdir -p "$CX"
printf 'ok\n' >"$CX/clean.log"
printf 'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}\n' >"$CX/auth.log"
printf 'API Error: 429 rate_limit_error: overloaded\n' >"$CX/rate.log"
printf 'some other unrelated crash\n' >"$CX/weird.log"

ceo="$("$SCRIPT" classify-exit --exit-code 0 --output "$CX/clean.log" 2>&1)"
cec=$?
[ "$cec" = 0 ] && [ "$ceo" = "done" ] && ok "classify-exit: clean exit -> done" || bad "classify-exit: clean exit -> done" "$ceo"

ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/auth.log" 2>&1)"
have "classify-exit: expired-OAuth 401 -> fatal" 'fatal:' "$ceo"
have "classify-exit: fatal reason names the auth failure" 'non-retryable auth failure' "$ceo"

ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/rate.log" 2>&1)"
have "classify-exit: rate-limit signal -> retry" 'retry:' "$ceo"
lack "classify-exit: rate-limit is not fatal" 'fatal:' "$ceo"

ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/weird.log" 2>&1)"
have "classify-exit: unclassified non-zero -> retry" 'retry:' "$ceo"

# A bare `401` in a transcript is NOT an auth failure. The classified bytes are
# a full stream-json transcript — line numbers, byte counts, SHAs, diffs — where
# those three digits appear constantly. Matching them would halt a healthy run
# with a WRONG diagnosis ("re-authenticate a credential that is fine").
printf '{"type":"assistant","text":"see foo.py:401 and the 4013-byte hunk @@ -401,7 +401,9 @@"}\n' >"$CX/incidental401.log"
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/incidental401.log" 2>&1)"
have "classify-exit: incidental 401 (line number/diff hunk) -> retry" 'retry:' "$ceo"
lack "classify-exit: incidental 401 is NOT fatal" 'fatal:' "$ceo"
# ...but a 401 in a genuine auth CONTEXT still is, including the exact shape the
# motivating run-#2 failure took.
printf 'API Error: 401 Invalid authentication credentials\n' >"$CX/ctx1.log"
have "classify-exit: run-#2 401 status line -> fatal" 'fatal:' "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/ctx1.log" 2>&1)"
printf '{"error":{"message":"nope"},"status":401}\n' >"$CX/ctx2.log"
have "classify-exit: HTTP status field 401 -> fatal" 'fatal:' "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/ctx2.log" 2>&1)"

# --since-offset: the log is APPENDED to across wakes, so a stale 401 from an
# earlier wake must not classify a later, unrelated failure as fatal — otherwise
# a human who re-authenticates and resumes gets halted again and told, falsely,
# that their credential is dead.
cat "$CX/auth.log" >"$CX/appended.log"
OFF="$(wc -c <"$CX/appended.log" | tr -d ' ')"
printf 'some later unrelated crash\n' >>"$CX/appended.log"
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/appended.log" --since-offset "$OFF" 2>&1)"
have "classify-exit: --since-offset ignores a previous wake's 401" 'retry:' "$ceo"
lack "classify-exit: stale 401 is not sticky across wakes" 'fatal:' "$ceo"
# without the offset the same file DOES read fatal — proving the offset is what
# does the work here, not an accident of the fixture.
have "classify-exit: whole-file read of the same log is fatal (the bug)" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/appended.log" 2>&1)"
# a live 401 within THIS wake's slice still halts
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/auth.log" --since-offset 0 2>&1)"
have "classify-exit: --since-offset 0 still sees this wake's 401" 'fatal:' "$ceo"
# fail-SAFE (not fail-closed): a garbage offset degrades to the whole file, so a
# broken offset can only over-halt, never silently relaunch forever.
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/auth.log" --since-offset bogus 2>&1)"
have "classify-exit: invalid --since-offset falls back to the whole file" 'fatal:' "$ceo"

# fail-closed: required args
o="$("$SCRIPT" classify-exit --output "$CX/clean.log" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -qF 'requires --exit-code' \
  && ok "classify-exit fail-closed: missing --exit-code" || bad "classify-exit fail-closed: missing --exit-code" "$o"
o="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/nope.log" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -qF 'not found' \
  && ok "classify-exit fail-closed: missing --output file" || bad "classify-exit fail-closed: missing --output file" "$o"

# co-review: an auth signal that is CONTENT (transcript prose, a diff, or the
# run's own REPORT.md re-read after --resume), not the orchestrator's own error
# line, must NOT classify fatal — else a task ABOUT auth, or the halt's own
# REPORT.md reason on the next wake, revives finding #22's loop via durable files.
printf '{"type":"assistant","text":"wrote tests asserting authentication_failed and 401 Unauthorized are surfaced"}\n' >"$CX/content-auth.log"
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/content-auth.log" 2>&1)"
lack "classify-exit: auth string in transcript CONTENT is not fatal" 'fatal:' "$ceo"
# the exact REPORT.md re-poison shape: a tool_result event carrying the halt's
# own reason text back into a later wake's slice.
printf '{"type":"user","message":{"content":[{"type":"tool_result","content":"## ALARM\\n- Reason: non-retryable auth failure (OAuth token has expired)"}]}}\n' >"$CX/report-echo.log"
lack "classify-exit: REPORT.md alarm echoed as a tool_result is not fatal" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/report-echo.log" 2>&1)"
# boundary: 401 as a PREFIX of a larger number in a status field is not a 401.
printf '{"error":{"message":"x"},"status":4013}\n' >"$CX/status4013.log"
lack "classify-exit: '\"status\":4013' does not match 401" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/status4013.log" 2>&1)"
# R1: `API Error:` INSIDE a stream-json event (a tool_result echoing a coder
# subagent's OWN failure) is content on a `{`-prefixed line — not the
# orchestrator's error surface — so it must NOT halt the run.
printf '%s\n' '{"type":"user","message":{"content":[{"type":"tool_result","content":"codex run failed: API Error: 401 Invalid authentication credentials"}]}}' >"$CX/coder-apierr.log"
lack "classify-exit: 'API Error:' inside event content is not fatal" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/coder-apierr.log" 2>&1)"
# R2: a plain-prose OAuth-expiry line on the CLI's OWN stderr (a NON-`{` line, so
# real error surface) must still classify fatal even without an API Error: / 401.
printf 'OAuth token has expired \xc2\xb7 Please obtain a new token or refresh your existing one.\n' >"$CX/prose-oauth.log"
have "classify-exit: plain-prose OAuth-expiry on stderr -> fatal" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/prose-oauth.log" 2>&1)"

# --- supervisor-check: fatal halt writes systemic status + REPORT alarm + teardown
# (task 10) — fixture is a real git checkout so the run-state commit is observable.
if command -v git >/dev/null 2>&1; then
  SC="$BASE/sc-fatal"
  mkdir -p "$SC/.auto-pilot"
  (cd "$SC" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'pause_reason: \n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  scout="$("$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/auth.log" --dir "$SC" \
    --label com.autopilot.test.fatal --state "$SC/.auto-pilot/supervisor-state" 2>&1)"
  have "supervisor-check: fatal halt reports itself" 'supervisor halt' "$scout"
  have "supervisor-check: fatal writes status: systemic" 'status: systemic' "$(cat "$SC/.auto-pilot/RUN.md")"
  have "supervisor-check: fatal writes a pause_reason" 'pause_reason: non-retryable auth failure' "$(cat "$SC/.auto-pilot/RUN.md")"
  have "supervisor-check: fatal appends a REPORT.md alarm" 'ALARM' "$(cat "$SC/.auto-pilot/REPORT.md")"
  scommits="$(git -C "$SC" log --oneline | wc -l | tr -d ' ')"
  [ "$scommits" = 2 ] && ok "supervisor-check: fatal halt commits the run-state change" \
    || bad "supervisor-check: fatal halt commits the run-state change" "commits=$scommits"

  # --- no-progress guard: N (default 3) consecutive non-zero, no-commit wakes halts
  SC2="$BASE/sc-noprogress"
  mkdir -p "$SC2/.auto-pilot"
  (cd "$SC2" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'pause_reason: \n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  STATE2="$SC2/.auto-pilot/supervisor-state"
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC2" --label com.autopilot.test.np --state "$STATE2" >/dev/null 2>&1
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC2" --label com.autopilot.test.np --state "$STATE2" >/dev/null 2>&1
  npout="$("$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC2" --label com.autopilot.test.np --state "$STATE2" 2>&1)"
  have "supervisor-check: no-progress guard halts after N consecutive failures" 'no forward progress' "$npout"
  have "supervisor-check: no-progress halt also writes status: systemic" 'status: systemic' "$(cat "$SC2/.auto-pilot/RUN.md")"

  # co-review (finding #1): the guard must still fire when _run_head returns
  # EMPTY (a non-git run dir, or git missing from the launchd PATH) — an empty
  # head is sentineled so consecutive wakes still count as no progress instead of
  # resetting the counter to 1 forever and never halting.
  SC_EH="$BASE/sc-emptyhead"
  mkdir -p "$SC_EH/.auto-pilot" # deliberately NOT a git repo
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf -- '---\n'
  } >"$SC_EH/.auto-pilot/RUN.md"
  printf '# report\n' >"$SC_EH/.auto-pilot/REPORT.md"
  STATE_EH="$SC_EH/.auto-pilot/supervisor-state"
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC_EH" --label com.autopilot.test.eh --state "$STATE_EH" >/dev/null 2>&1
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC_EH" --label com.autopilot.test.eh --state "$STATE_EH" >/dev/null 2>&1
  ehout="$("$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC_EH" --label com.autopilot.test.eh --state "$STATE_EH" 2>&1)"
  have "supervisor-check: no-progress guard halts even with an empty run HEAD" 'no forward progress' "$ehout"
  have "supervisor-check: empty-HEAD halt still writes status: systemic" 'status: systemic' "$(cat "$SC_EH/.auto-pilot/RUN.md")"

  # --- a legitimate paused_until wait never trips the guard, even repeated ------
  SC3="$BASE/sc-paused"
  mkdir -p "$SC3/.auto-pilot"
  (cd "$SC3" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: paused\n'
      printf 'paused_until: 2099-01-01T00:00:00\n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  STATE3="$SC3/.auto-pilot/supervisor-state"
  i=0
  while [ "$i" -lt 5 ]; do
    "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC3" --label com.autopilot.test.paused --state "$STATE3" >/dev/null 2>&1
    i=$((i + 1))
  done
  lack "supervisor-check: a paused wake never halts, however many repeats" 'systemic' "$(cat "$SC3/.auto-pilot/RUN.md")"

  # --- forward progress (a fresh run-state commit) resets the guard's counter ---
  SC4="$BASE/sc-progress"
  mkdir -p "$SC4/.auto-pilot"
  (cd "$SC4" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'pause_reason: \n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  STATE4="$SC4/.auto-pilot/supervisor-state"
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC4" --label com.autopilot.test.progress --state "$STATE4" >/dev/null 2>&1
  # a task did real work between wakes: a new run-state commit lands
  (cd "$SC4" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "task progressed")
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC4" --label com.autopilot.test.progress --state "$STATE4" >/dev/null 2>&1
  pgout="$("$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC4" --label com.autopilot.test.progress --state "$STATE4" 2>&1)"
  have "supervisor-check: a run-state commit resets the no-progress counter" '2/3 consecutive' "$pgout"
  lack "supervisor-check: progress in between never halts" 'systemic' "$(cat "$SC4/.auto-pilot/RUN.md")"
else
  echo "skip - supervisor-check: fatal/no-progress halt (git not available)"
fi

# --- write-launch: the generated script classifies its own exit (task 10) -----
lbody10="$(cat "$BASE/launch.sh" 2>/dev/null)"
have "launch: calls supervisor-check after claude exits" 'supervisor-check' "$lbody10"
have "launch: no longer execs claude directly" 'set +e' "$lbody10"
lack "launch: exec sandbox-exec no longer used" 'exec sandbox-exec' "$lbody10"
# the log offset must be captured BEFORE claude runs, and handed to
# supervisor-check — else classification reads every past wake's bytes too.
have "launch: captures the log offset before the run" 'off=$(wc -c' "$lbody10"
have "launch: passes --since-offset to supervisor-check" '--since-offset "$off"' "$lbody10"
off_ln="$(printf '%s\n' "$lbody10" | grep -n 'off=$(wc -c' | head -1 | cut -d: -f1)"
sbx_ln="$(printf '%s\n' "$lbody10" | grep -n '^sandbox-exec -f' | head -1 | cut -d: -f1)"
if [ -n "$off_ln" ] && [ -n "$sbx_ln" ] && [ "$off_ln" -lt "$sbx_ln" ]; then
  ok "launch: log offset is captured before sandbox-exec runs"
else
  bad "launch: log offset is captured before sandbox-exec runs" "off@$off_ln sandbox@$sbx_ln"
fi

# --- supervisor-gate: pre-invoke pause gate in shell (task 11, finding #19) ---
# Behavioral, on the GENERATED wrapper itself, not a string grep: run the real
# launch.sh with a stub claude (--claude-bin) and a stub sandbox-exec on PATH,
# each recording their own invocation via a marker file. "claude was never
# invoked" is then observable as "marker file absent" — no real jail involved.
GT="$BASE/gate"
mkdir -p "$GT/bin"
GT_CLAUDE="$GT/bin/claude-stub"
{
  printf '#!/bin/sh\n'
  printf ': >"%s/claude-called"\n' "$GT"
  printf 'exit 0\n'
} >"$GT_CLAUDE"
chmod +x "$GT_CLAUDE"
# The generated wrapper invokes `sandbox-exec -f <profile> <claude_bin> ...`
# bare (resolved via the script's own exported PATH) — a stub here both lets
# the test run fully offline (no real Seatbelt jail) and, by exec-ing through
# to the rest of argv, still reaches the claude stub above.
GT_SANDBOX="$GT/bin/sandbox-exec"
{
  printf '#!/bin/sh\n'
  printf ': >"%s/sandbox-exec-called"\n' "$GT"
  printf 'shift 2\n' # drop "-f <profile>"
  printf 'exec "$@"\n'
} >"$GT_SANDBOX"
chmod +x "$GT_SANDBOX"
GT_PATH="$GT/bin:$GUARD:/usr/bin:/bin:/usr/sbin:/sbin"

# epoch -> RUN.md's ISO-8601 UTC form, portable across BSD/GNU `date`.
_gate_iso() { date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
NOW_EPOCH="$(date -u +%s)"
FUTURE_TS="$(_gate_iso $((NOW_EPOCH + 3600)))"
PAST_TS="$(_gate_iso $((NOW_EPOCH - 3600)))"

# Build one run dir + generate its wrapper via write-launch, given a RUN.md
# front-matter body; returns (via echo) whether the claude stub ran.
_gate_case() {
  local name="$1" front="$2"
  local d="$GT/$name"
  mkdir -p "$d/.auto-pilot"
  { printf -- '---\n%s\n---\n' "$front"; } >"$d/.auto-pilot/RUN.md"
  rm -f "$GT/claude-called" "$GT/sandbox-exec-called"
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$d" \
    --log "$d/o.log" --prompt-file "$BASE/prompt.txt" --label "com.autopilot.gate.$name" \
    --claude-bin "$GT_CLAUDE" --path "$GT_PATH" \
    --out-script "$d/launch.sh" --out-plist "$d/job.plist" >/dev/null 2>&1
  "$d/launch.sh" >/dev/null 2>&1
  echo "rc=$? log=$d/o.log"
}

# Test A: paused_until an hour in the future -> exit 0, claude never invoked.
gaA="$(_gate_case gate-future "status: active
paused_until: $FUTURE_TS")"
rcA="${gaA#rc=}"
rcA="${rcA%% *}"
[ "$rcA" = 0 ] && ok "gate: future paused_until exits 0" || bad "gate: future paused_until exits 0" "$gaA"
[ ! -f "$GT/claude-called" ] && ok "gate: future paused_until never invokes claude" \
  || bad "gate: future paused_until never invokes claude"
have "gate: future paused_until logs the skip" "skipping this wake" "$(cat "$GT/gate-future/o.log" 2>/dev/null)"

# Test B: paused_until in the past -> claude IS invoked.
_gate_case gate-past "status: active
paused_until: $PAST_TS" >/dev/null
[ -f "$GT/claude-called" ] && ok "gate: past paused_until invokes claude" \
  || bad "gate: past paused_until invokes claude"

# Test C: paused_until empty -> claude IS invoked.
_gate_case gate-empty "status: active
paused_until:" >/dev/null
[ -f "$GT/claude-called" ] && ok "gate: empty paused_until invokes claude" \
  || bad "gate: empty paused_until invokes claude"

# Test D: status: done -> claude never invoked, the gate tears the job down.
gaD="$(_gate_case gate-done "status: done
paused_until:")"
[ ! -f "$GT/claude-called" ] && ok "gate: status done never invokes claude" \
  || bad "gate: status done never invokes claude"
have "gate: status done reports a teardown" "torn down" "$(cat "$GT/gate-done/o.log" 2>/dev/null)"

# Test E: status: systemic -> claude never invoked, teardown reported too.
gaE="$(_gate_case gate-systemic "status: systemic
paused_until:")"
[ ! -f "$GT/claude-called" ] && ok "gate: status systemic never invokes claude" \
  || bad "gate: status systemic never invokes claude"
have "gate: status systemic reports a teardown" "torn down" "$(cat "$GT/gate-systemic/o.log" 2>/dev/null)"

# Fail-safe: an unparseable paused_until proceeds rather than skipping forever.
_gate_case gate-garbage "status: active
paused_until: not-a-timestamp" >/dev/null
[ -f "$GT/claude-called" ] && ok "gate: garbage paused_until fails safe (proceeds)" \
  || bad "gate: garbage paused_until fails safe (proceeds)"
have "gate: garbage paused_until logs unparseable+proceeding" "unparseable" \
  "$(cat "$GT/gate-garbage/o.log" 2>/dev/null)"

# supervisor-gate itself (unit-level, not through the wrapper): --dir with no
# RUN.md at all is the other fail-safe path — proceed, don't die.
sgo="$("$SCRIPT" supervisor-gate --dir "$GT/no-such-run" --label com.autopilot.gate.norun 2>&1)"
sgc=$?
[ "$sgc" = 0 ] && ok "supervisor-gate: missing RUN.md fails safe (exit 0)" \
  || bad "supervisor-gate: missing RUN.md fails safe (exit 0)" "rc=$sgc out=$sgo"

# fail-closed: required args
o="$("$SCRIPT" supervisor-gate --label x 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -qF 'requires --dir and --label' \
  && ok "supervisor-gate fail-closed: missing --dir" || bad "supervisor-gate fail-closed: missing --dir" "$o"

# --- restack: post-merge restack of stacked PRs (task 18, finding #25) -------
# Builds a REAL git repo (a bare "origin" + a working clone) with a
# squash-merged parent + a stacked child, and a FAKE `gh` (offline — no
# GitHub calls) so the git mechanics run for real while every GitHub
# read/write is mocked and inspectable.
if command -v git >/dev/null 2>&1; then
  RS="$BASE/restack"
  mkdir -p "$RS"
  ORIGIN="$RS/origin.git"
  WORK="$RS/work"
  git init --bare -q "$ORIGIN"
  git init -q "$WORK"
  git -C "$WORK" remote add origin "$ORIGIN"
  git -C "$WORK" config user.email test@example.com
  git -C "$WORK" config user.name "Test"
  git -C "$WORK" checkout -q -b main
  echo root >"$WORK/root.txt"
  git -C "$WORK" add root.txt
  git -C "$WORK" commit -q -m root
  git -C "$WORK" push -q origin main

  # parent branch (task_parent, PR #100): adds parent.txt
  git -C "$WORK" checkout -q -b parent-branch
  printf 'line1\n' >"$WORK/parent.txt"
  git -C "$WORK" add parent.txt
  git -C "$WORK" commit -q -m "parent change"
  git -C "$WORK" push -q origin parent-branch
  PARENT_SHA="$(git -C "$WORK" rev-parse parent-branch)" # the child's frozen base_sha

  # child branch (task_child, PR #101): stacked on parent-branch, touches ONLY
  # child.txt — must restack cleanly regardless of what else happens to main.
  git -C "$WORK" checkout -q -b child-branch
  echo child >"$WORK/child.txt"
  git -C "$WORK" add child.txt
  git -C "$WORK" commit -q -m "child change"
  git -C "$WORK" push -q origin child-branch

  # Simulate the human squash-merging the parent PR into main: a NEW squash
  # commit on main, not an ancestor of parent-branch's own commits — the exact
  # shape that orphans a child under squash-merge.
  git -C "$WORK" checkout -q main
  git -C "$WORK" merge -q --squash parent-branch >/dev/null
  git -C "$WORK" commit -q -m "parent change (squashed)"
  # Then simulate a POST-HAND-OFF human review fix on the SAME line (run-state.md
  # "restacked child is stale" note): the child was co-reviewed against the
  # pre-review parent, so a clean rebase later must not be mistaken for proof
  # nothing was missed.
  printf 'line1-SECURITY-FIXED\n' >"$WORK/parent.txt"
  git -C "$WORK" add parent.txt
  git -C "$WORK" commit -q -m "parent: post-hand-off review fix"
  git -C "$WORK" push -q origin main

  # Fake gh: PR state lives in flat files under $FAKE_GH_DB; `pr edit --base`
  # rewrites the base file (so a second restack observes the retarget) and
  # appends to edits.log (so the test can assert exactly what was retargeted).
  FAKE_GH_DB="$RS/ghdb"
  mkdir -p "$FAKE_GH_DB"
  printf 'MERGED\n' >"$FAKE_GH_DB/100.state"
  printf 'main\n' >"$FAKE_GH_DB/100.base"
  printf 'OPEN\n' >"$FAKE_GH_DB/101.state"
  printf 'parent-branch\n' >"$FAKE_GH_DB/101.base"
  FAKE_GH="$RS/gh"
  cat >"$FAKE_GH" <<'GHEOF'
#!/usr/bin/env bash
set -uo pipefail
db="${FAKE_GH_DB:?FAKE_GH_DB not set}"
[ "$1" = pr ] || exit 1
sub="$2"; num="$3"; shift 3
case "$sub" in
  view)
    jqexpr=""
    while [ $# -gt 0 ]; do case "$1" in --jq) jqexpr="$2"; shift 2 ;; *) shift ;; esac; done
    case "$jqexpr" in
      .baseRefName) f="$db/$num.base" ;;
      .state)       f="$db/$num.state" ;;
      *) exit 1 ;;
    esac
    # Real `gh` contract (measured): a PR that does not exist exits 1 with a
    # GraphQL "Could not resolve" line on STDERR — never exit 0 with empty
    # output (the invariant-3 stub bug from the review-feedback doc).
    if [ -f "$f" ]; then cat "$f"; else
      echo "GraphQL: Could not resolve to a PullRequest with the number of $num. (repository.pullRequest)" >&2
      exit 1
    fi
    ;;
  edit)
    [ -f "$db/$num.editfail" ] && exit 1   # simulate a rejected `gh pr edit`
    while [ $# -gt 0 ]; do
      case "$1" in --base) printf '%s\n' "$2" >"$db/$num.base"; echo "$num $2" >>"$db/edits.log"; shift 2 ;; *) shift ;; esac
    done
    ;;
  *) exit 1 ;;
esac
GHEOF
  chmod +x "$FAKE_GH"
  export FAKE_GH_DB

  RUNDIR_RS="$RS/run"
  mkdir -p "$RUNDIR_RS/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n'
    printf '\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_parent | handed-off | parent-branch | main | - | #100 | |\n'
    printf '| task_child  | handed-off | child-branch | parent-branch | %s | #101 | |\n' "$PARENT_SHA"
  } >"$RUNDIR_RS/.auto-pilot/RUN.md"

  # HEAD invariant (finding #23 / task 13's assert-run-head): restack must never
  # move the caller's HEAD. `git rebase --onto X Y <branch>` CHECKS OUT <branch>,
  # so a naive restack would park the RUN worktree's HEAD on a task branch — the
  # exact bug task 13 guards against. Record HEAD before every restack below.
  head_ref_0="$(git -C "$WORK" rev-parse --abbrev-ref HEAD)"
  head_sha_0="$(git -C "$WORK" rev-parse HEAD)"

  rsout="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  rsc=$?
  [ "$rsc" = 0 ] && ok "restack: exits 0 on a clean stacked restack" || bad "restack: exits 0 on a clean stacked restack" "$rsout"
  have "restack: reports task_child done" 'restack task_child done' "$rsout"
  have "restack: prints the copy-pasteable rebase command" 'git rebase --onto origin/main' "$rsout"
  have "restack: retargets the PR base" '101 main' "$(cat "$FAKE_GH_DB/edits.log" 2>/dev/null)"

  if [ "$(git -C "$WORK" rev-parse --abbrev-ref HEAD)" = "$head_ref_0" ] \
    && [ "$(git -C "$WORK" rev-parse HEAD)" = "$head_sha_0" ]; then
    ok "restack: a successful restack does NOT move the caller's HEAD"
  else
    bad "restack: a successful restack does NOT move the caller's HEAD" \
      "was $head_ref_0@$head_sha_0 now $(git -C "$WORK" rev-parse --abbrev-ref HEAD)@$(git -C "$WORK" rev-parse HEAD)"
  fi
  # …and leaves no scratch worktree registered behind it
  if git -C "$WORK" worktree list 2>/dev/null | grep -q 'restack-wt'; then
    bad "restack: removes its scratch worktree"
  else
    ok "restack: removes its scratch worktree"
  fi

  # REPORT.md is where a human actually looks — the re-verify + stale-co-review
  # requirement is worthless on stdout alone (it only reaches orchestrator.log).
  rsreport="$(cat "$RUNDIR_RS/.auto-pilot/REPORT.md" 2>/dev/null)"
  have "restack: appends a Restack section to REPORT.md" '## Restack' "$rsreport"
  have "restack: REPORT.md demands re-verify for the child" 'Re-verify required' "$rsreport"
  have "restack: REPORT.md flags the co-review as STALE" 'STALE' "$rsreport"

  git -C "$WORK" fetch -q origin
  diffnames="$(git -C "$WORK" diff --name-only origin/main origin/child-branch)"
  if [ "$diffnames" = "child.txt" ]; then
    ok "restack: child's post-restack diff contains ONLY its own file"
  else
    bad "restack: child's post-restack diff contains ONLY its own file" "got: $diffnames"
  fi
  lack "restack: child diff does not re-propose parent.txt" 'parent.txt' "$diffnames"

  # idempotency: a second restack makes no additional rebase/push/gh-edit, and
  # does not churn REPORT.md either
  editcount_before="$(wc -l <"$FAKE_GH_DB/edits.log" 2>/dev/null | tr -d ' ')"
  reportsize_before="$(wc -c <"$RUNDIR_RS/.auto-pilot/REPORT.md" 2>/dev/null | tr -d ' ')"
  rsout2="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  rsc2=$?
  [ "$rsc2" = 0 ] && ok "restack: idempotent second run exits 0" || bad "restack: idempotent second run exits 0" "$rsout2"
  have "restack: idempotent second run reports no-op" 'already based on main (no-op)' "$rsout2"
  editcount_after="$(wc -l <"$FAKE_GH_DB/edits.log" 2>/dev/null | tr -d ' ')"
  [ "$editcount_before" = "$editcount_after" ] && ok "restack: idempotent run makes no additional gh edit" \
    || bad "restack: idempotent run makes no additional gh edit" "before=$editcount_before after=$editcount_after"
  reportsize_after="$(wc -c <"$RUNDIR_RS/.auto-pilot/REPORT.md" 2>/dev/null | tr -d ' ')"
  [ "$reportsize_before" = "$reportsize_after" ] && ok "restack: idempotent run does not churn REPORT.md" \
    || bad "restack: idempotent run does not churn REPORT.md" "before=$reportsize_before after=$reportsize_after"

  # --- fail-closed on conflict + the HEAD invariant under a MIXED run ---------
  # Build two more stacked children so ONE restack run both succeeds (clean2)
  # and conflicts (conflict-child):
  #   conflict-child edits the SAME line the parent's post-hand-off review fix
  #     touched — the "clean rebase proves nothing" case, except here it isn't
  #     clean: it must conflict, abort, and never force-push or retarget.
  #   clean2 touches only its own file and must restack normally.
  # Asserting HEAD across THIS run is the real test: a naive `git rebase --onto
  # … <branch>` would have checked out (and left HEAD on) a task branch.
  git -C "$WORK" checkout -q parent-branch
  PARENT_SHA2="$(git -C "$WORK" rev-parse parent-branch)"
  git -C "$WORK" checkout -q -b conflict-child
  printf 'line1-CONFLICTING-EDIT\n' >"$WORK/parent.txt"
  git -C "$WORK" add parent.txt
  git -C "$WORK" commit -q -m "child edits the same line"
  git -C "$WORK" push -q origin conflict-child
  printf 'OPEN\n' >"$FAKE_GH_DB/102.state"
  printf 'parent-branch\n' >"$FAKE_GH_DB/102.base"

  git -C "$WORK" checkout -q parent-branch
  git -C "$WORK" checkout -q -b clean2
  echo clean2 >"$WORK/clean2.txt"
  git -C "$WORK" add clean2.txt
  git -C "$WORK" commit -q -m "clean2 change"
  git -C "$WORK" push -q origin clean2
  printf 'OPEN\n' >"$FAKE_GH_DB/103.state"
  printf 'parent-branch\n' >"$FAKE_GH_DB/103.base"

  {
    printf '| task_conflict | handed-off | conflict-child | parent-branch | %s | #102 | |\n' "$PARENT_SHA2"
    printf '| task_clean2   | handed-off | clean2         | parent-branch | %s | #103 | |\n' "$PARENT_SHA2"
  } >>"$RUNDIR_RS/.auto-pilot/RUN.md"

  # Park HEAD somewhere deliberate (main) so a stray checkout is unmistakable.
  git -C "$WORK" checkout -q main
  head_ref_1="$(git -C "$WORK" rev-parse --abbrev-ref HEAD)"
  head_sha_1="$(git -C "$WORK" rev-parse HEAD)"

  precommit_tip="$(git -C "$ORIGIN" rev-parse conflict-child)"
  rsout3="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  rsc3=$?
  [ "$rsc3" = 2 ] && ok "restack: fail-closed conflict exits non-zero" || bad "restack: fail-closed conflict exits non-zero" "exit=$rsc3"
  have "restack: fail-closed conflict reports a conflict" 'FAILED — rebase conflict' "$rsout3"
  have "restack: a conflict does not stop the other children" 'restack task_clean2 done' "$rsout3"
  postcommit_tip="$(git -C "$ORIGIN" rev-parse conflict-child)"
  [ "$precommit_tip" = "$postcommit_tip" ] && ok "restack: conflict never force-pushes the broken branch" \
    || bad "restack: conflict never force-pushes the broken branch"
  lack "restack: conflict never retargets the PR" '102 main' "$(cat "$FAKE_GH_DB/edits.log" 2>/dev/null)"

  # THE invariant: a run that both succeeded and conflicted left HEAD untouched.
  if [ "$(git -C "$WORK" rev-parse --abbrev-ref HEAD)" = "$head_ref_1" ] \
    && [ "$(git -C "$WORK" rev-parse HEAD)" = "$head_sha_1" ]; then
    ok "restack: HEAD unchanged across a mixed success+conflict run (task 13 invariant)"
  else
    bad "restack: HEAD unchanged across a mixed success+conflict run (task 13 invariant)" \
      "was $head_ref_1@$head_sha_1 now $(git -C "$WORK" rev-parse --abbrev-ref HEAD)@$(git -C "$WORK" rev-parse HEAD)"
  fi
  [ -z "$(git -C "$WORK" status --porcelain 2>/dev/null)" ] \
    && ok "restack: caller's worktree left clean (no half-applied rebase)" \
    || bad "restack: caller's worktree left clean (no half-applied rebase)"
  if git -C "$WORK" worktree list 2>/dev/null | grep -q 'restack-wt'; then
    bad "restack: removes its scratch worktree even on the conflict path"
  else
    ok "restack: removes its scratch worktree even on the conflict path"
  fi

  # fail-closed: a dirty caller worktree is never touched (an automated rebase
  # over a human's uncommitted work is how "helpful" recovery destroys state)
  echo dirty >"$WORK/uncommitted.txt"
  dirty_out="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  dirty_c=$?
  if [ "$dirty_c" = 2 ] && printf '%s' "$dirty_out" | grep -qF 'worktree is dirty'; then
    ok "restack: dirty caller worktree fails closed"
  else
    bad "restack: dirty caller worktree fails closed" "exit=$dirty_c msg=$dirty_out"
  fi
  rm -f "$WORK/uncommitted.txt"

  # --- orphaned-child detector: a merged/deleted base is a flagged defect ----
  RSD="$RS/detect-run"
  mkdir -p "$RSD/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_parent  | handed-off | parent-branch | main | - | #100 | |\n'
    # deleted/unreadable base ref (LOUD case: gh has nothing for its baseRefName)
    printf '| task_deleted | handed-off | deleted-child | gone-branch | deadbeef | #200 | |\n'
    # base branch's own tracked PR is MERGED, but this child was never
    # retargeted (QUIET case — restack itself can't fix it: no base_sha) —
    # the exact "looks healthy, does nothing" shape finding #25 warns about
    printf '| task_quiet   | handed-off | quiet-child   | parent-branch | - | #201 | |\n'
  } >"$RSD/.auto-pilot/RUN.md"
  printf 'OPEN\n' >"$FAKE_GH_DB/200.state" # no 200.base file at all -> baseRefName lookup fails
  printf 'OPEN\n' >"$FAKE_GH_DB/201.state"
  printf 'parent-branch\n' >"$FAKE_GH_DB/201.base"

  dsout="$("$SCRIPT" restack --run-dir "$RSD" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  dsc=$?
  [ "$dsc" = 2 ] && ok "restack: orphan-detector run reports the missing-base_sha failure" \
    || bad "restack: orphan-detector run reports the missing-base_sha failure" "exit=$dsc"
  have "restack: flags a deleted/unreadable base as a defect" 'DEFECT task_deleted' "$dsout"
  have "restack: flags a PR still targeting a merged branch as a defect" 'DEFECT task_quiet' "$dsout"
  have "restack: defect summary count is non-zero" 'defects=2' "$dsout"

  # --- transient gh failure is UNDETERMINED, never a DEFECT ------------------
  # Real gh contract: a missing PR exits 1 with "Could not resolve to a
  # PullRequest" (a positive "it's gone", flagged above); an expired auth /
  # rate limit / network error also exits 1 but says something ELSE — that
  # proves nothing about the PR, so it must neither fail the restack (exit 2)
  # nor mint a false DEFECT during a GitHub blip.
  RSU="$RS/undetermined-run"
  mkdir -p "$RSU/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_t | handed-off | t-branch | untracked-parent | - | #301 | |\n'
  } >"$RSU/.auto-pilot/RUN.md"
  TRANSIENT_GH="$RS/gh-transient"
  printf '#!/bin/sh\necho "HTTP 401: Bad credentials (https://api.github.com/graphql)" >&2\nexit 1\n' >"$TRANSIENT_GH"
  chmod +x "$TRANSIENT_GH"
  udout="$("$SCRIPT" restack --run-dir "$RSU" --repo "$WORK" --remote origin --gh "$TRANSIENT_GH" 2>&1)"
  udc=$?
  [ "$udc" = 0 ] && ok "restack: a transient gh failure does NOT fail the restack (exit 0, fail-safe)" \
    || bad "restack: a transient gh failure does NOT fail the restack (exit 0, fail-safe)" "exit=$udc $udout"
  lack "restack: a transient gh failure is never flagged as a DEFECT" 'DEFECT' "$udout"
  have "restack: a transient gh failure is announced as UNDETERMINED (not silent)" 'UNDETERMINED task_t' "$udout"
  have "restack: the undetermined summary still reads defects=0" 'defects=0' "$udout"

  # === co-review scenarios: cascade (3-deep), retarget-failure, closed child ===
  # A fresh bare origin + clone so prior mutations don't bleed in.
  C_ORIGIN="$RS/c-origin.git"
  C_WORK="$RS/c-work"
  git init --bare -q "$C_ORIGIN"
  git init -q "$C_WORK"
  git -C "$C_WORK" remote add origin "$C_ORIGIN"
  git -C "$C_WORK" config user.email t@e
  git -C "$C_WORK" config user.name T
  git -C "$C_WORK" checkout -q -b main
  echo r >"$C_WORK/r.txt"
  git -C "$C_WORK" add r.txt
  git -C "$C_WORK" commit -q -m r
  git -C "$C_WORK" push -q origin main
  # chain A <- B <- C (each touches only its own file).
  git -C "$C_WORK" checkout -q -b br-a
  echo a >"$C_WORK/a.txt"
  git -C "$C_WORK" add a.txt
  git -C "$C_WORK" commit -q -m a
  git -C "$C_WORK" push -q origin br-a
  A_SHA="$(git -C "$C_WORK" rev-parse br-a)"
  git -C "$C_WORK" checkout -q -b br-b
  echo b >"$C_WORK/b.txt"
  git -C "$C_WORK" add b.txt
  git -C "$C_WORK" commit -q -m b
  git -C "$C_WORK" push -q origin br-b
  B_SHA="$(git -C "$C_WORK" rev-parse br-b)"
  git -C "$C_WORK" checkout -q -b br-c
  echo c >"$C_WORK/c.txt"
  git -C "$C_WORK" add c.txt
  git -C "$C_WORK" commit -q -m c
  git -C "$C_WORK" push -q origin br-c
  # A squash-merges to main.
  git -C "$C_WORK" checkout -q main
  git -C "$C_WORK" merge -q --squash br-a >/dev/null
  git -C "$C_WORK" commit -q -m "a squashed"
  git -C "$C_WORK" push -q origin main
  git -C "$C_WORK" checkout -q main
  C_DB="$RS/c-ghdb"
  mkdir -p "$C_DB"
  printf 'MERGED\n' >"$C_DB/1.state"
  printf 'main\n' >"$C_DB/1.base" # A merged
  printf 'OPEN\n' >"$C_DB/2.state"
  printf 'br-a\n' >"$C_DB/2.base" # B on parent branch
  printf 'OPEN\n' >"$C_DB/3.state"
  printf 'br-b\n' >"$C_DB/3.base" # C on B's branch
  C_RUN="$RS/c-run"
  mkdir -p "$C_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_a | handed-off | br-a | main | - | #1 | |\n'
    printf '| t_b | handed-off | br-b | br-a | %s | #2 | |\n' "$A_SHA"
    printf '| t_c | handed-off | br-c | br-b | %s | #3 | |\n' "$B_SHA"
  } >"$C_RUN/.auto-pilot/RUN.md"
  export FAKE_GH_DB="$C_DB"
  cout="$("$SCRIPT" restack --run-dir "$C_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  ccode=$?
  # B restacks onto-base (main); C CASCADEs onto B's new tip in a later pass —
  # its PR base stays br-b, never re-proposing B's changeset.
  have "restack cascade: B retargeted to main" '2 main' "$(cat "$C_DB/edits.log" 2>/dev/null)"
  have "restack cascade: C restacked in cascade mode" 'restack t_c done (cascade)' "$cout"
  lack "restack cascade: C is NOT retargeted to main" '3 main' "$(cat "$C_DB/edits.log" 2>/dev/null)"
  [ "$(cat "$C_DB/3.base")" = "br-b" ] && ok "restack cascade: C's PR base stays br-b" || bad "restack cascade: C's PR base stays br-b" "$(cat "$C_DB/3.base")"
  git -C "$C_WORK" fetch -q origin
  # C's PR targets br-b (cascade keeps the parent base), so its PR diff is br-b..br-c
  # — must be ONLY c.txt, i.e. C sits cleanly on B's NEW tip with no orphaned
  # duplicate of B's pre-rewrite commits.
  cdiff="$(git -C "$C_WORK" diff --name-only origin/br-b origin/br-c)"
  [ "$cdiff" = "c.txt" ] && ok "restack cascade: C's PR diff (br-b..br-c) is ONLY c.txt (cascaded onto B's new tip)" \
    || bad "restack cascade: C's PR diff is ONLY c.txt" "got: $cdiff"
  [ "$ccode" = 0 ] && ok "restack cascade: clean 3-deep run exits 0" || bad "restack cascade: clean 3-deep run exits 0" "exit=$ccode"

  # RESUMABLE cascade (fix 1): a PARTIAL earlier run rewrote the parent (B) but
  # never cascaded the grandchild (C) — a fresh process has an empty in-memory
  # _RS_NEWTIP, so C must be detected from the REMOTE (parent OPEN + already on
  # base_branch + its remote tip moved off C's base_sha) and cascaded, not
  # silently stranded. Fresh chain X<-Y<-Z; X merged.
  git -C "$C_WORK" checkout -q main
  git -C "$C_WORK" checkout -q -b br-x
  echo x >"$C_WORK/x.txt"
  git -C "$C_WORK" add x.txt
  git -C "$C_WORK" commit -q -m x
  git -C "$C_WORK" push -q origin br-x
  X_SHA="$(git -C "$C_WORK" rev-parse br-x)"
  git -C "$C_WORK" checkout -q -b br-y
  echo y >"$C_WORK/y.txt"
  git -C "$C_WORK" add y.txt
  git -C "$C_WORK" commit -q -m y
  git -C "$C_WORK" push -q origin br-y
  Y_SHA="$(git -C "$C_WORK" rev-parse br-y)"
  git -C "$C_WORK" checkout -q -b br-z
  echo z >"$C_WORK/z.txt"
  git -C "$C_WORK" add z.txt
  git -C "$C_WORK" commit -q -m z
  git -C "$C_WORK" push -q origin br-z
  git -C "$C_WORK" checkout -q main
  git -C "$C_WORK" merge -q --squash br-x >/dev/null
  git -C "$C_WORK" commit -q -m "x squashed"
  git -C "$C_WORK" push -q origin main
  git -C "$C_WORK" checkout -q main
  printf 'MERGED\n' >"$C_DB/30.state"
  printf 'main\n' >"$C_DB/30.base"
  printf 'OPEN\n' >"$C_DB/31.state"
  printf 'br-x\n' >"$C_DB/31.base"
  printf 'OPEN\n' >"$C_DB/32.state"
  printf 'br-y\n' >"$C_DB/32.base"
  # Phase 1: RUN.md WITHOUT Z, so only Y restacks (Z is never seen this run).
  P1_RUN="$RS/p1-run"
  mkdir -p "$P1_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_x | handed-off | br-x | main | - | #30 | |\n'
    printf '| t_y | handed-off | br-y | br-x | %s | #31 | |\n' "$X_SHA"
  } >"$P1_RUN/.auto-pilot/RUN.md"
  "$SCRIPT" restack --run-dir "$P1_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" >/dev/null 2>&1
  [ "$(cat "$C_DB/31.base")" = "main" ] && ok "restack resumable: phase-1 restacks Y to main" || bad "restack resumable: phase-1 restacks Y to main" "$(cat "$C_DB/31.base")"
  # Phase 2: a FRESH process (empty _RS_NEWTIP) with Z now in the table. Z's
  # recorded base_sha is Y's OLD tip; Y's remote tip has moved — Z must cascade.
  P2_RUN="$RS/p2-run"
  mkdir -p "$P2_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_x | handed-off | br-x | main | - | #30 | |\n'
    printf '| t_y | handed-off | br-y | br-x | %s | #31 | |\n' "$X_SHA"
    printf '| t_z | handed-off | br-z | br-y | %s | #32 | |\n' "$Y_SHA"
  } >"$P2_RUN/.auto-pilot/RUN.md"
  p2out="$("$SCRIPT" restack --run-dir "$P2_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  have "restack resumable: Z cascaded from the REMOTE parent tip (not stranded)" 'restack t_z done (cascade)' "$p2out"
  lack "restack resumable: Z not retargeted to main (base stays br-y)" '32 main' "$(cat "$C_DB/edits.log" 2>/dev/null)"
  git -C "$C_WORK" fetch -q origin
  zdiff="$(git -C "$C_WORK" diff --name-only origin/br-y origin/br-z)"
  [ "$zdiff" = "z.txt" ] && ok "restack resumable: Z's PR diff (br-y..br-z) is ONLY z.txt" || bad "restack resumable: Z's PR diff is ONLY z.txt" "got: $zdiff"
  # idempotent: a cascaded child re-run is a no-op — no re-cascade, no REPORT churn.
  p2report_before="$(wc -c <"$P2_RUN/.auto-pilot/REPORT.md" 2>/dev/null | tr -d ' ')"
  p3out="$("$SCRIPT" restack --run-dir "$P2_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  lack "restack resumable: re-run does NOT re-cascade Z" 'restack t_z done (cascade)' "$p3out"
  have "restack resumable: re-run reports the cascaded child a no-op" 'already cascaded' "$p3out"
  p2report_after="$(wc -c <"$P2_RUN/.auto-pilot/REPORT.md" 2>/dev/null | tr -d ' ')"
  [ "$p2report_before" = "$p2report_after" ] && ok "restack resumable: re-run does not churn REPORT.md" \
    || bad "restack resumable: re-run does not churn REPORT.md" "before=$p2report_before after=$p2report_after"

  # retarget-failure (fix 2): push succeeds, `gh pr edit` rejected -> DEFECT, the
  # child is NOT marked done, and the run exits non-zero. Fresh single stack.
  printf 'MERGED\n' >"$C_DB/10.state"
  printf 'main\n' >"$C_DB/10.base"
  printf 'OPEN\n' >"$C_DB/11.state"
  printf 'br-a\n' >"$C_DB/11.base"
  : >"$C_DB/11.editfail"
  git -C "$C_WORK" checkout -q br-a
  git -C "$C_WORK" checkout -q -b br-rt
  echo rt >"$C_WORK/rt.txt"
  git -C "$C_WORK" add rt.txt
  git -C "$C_WORK" commit -q -m rt
  git -C "$C_WORK" push -q origin br-rt
  git -C "$C_WORK" checkout -q main
  RT_RUN="$RS/rt-run"
  mkdir -p "$RT_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_p | handed-off | br-a | main | - | #10 | |\n'
    printf '| t_rt | handed-off | br-rt | br-a | %s | #11 | |\n' "$A_SHA"
  } >"$RT_RUN/.auto-pilot/RUN.md"
  rtout="$("$SCRIPT" restack --run-dir "$RT_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  rtcode=$?
  have "restack retarget-fail: reports a DEFECT" 'DEFECT — rebased and force-pushed' "$rtout"
  lack "restack retarget-fail: does NOT report the child done" 'restack t_rt done' "$rtout"
  [ "$rtcode" = 2 ] && ok "restack retarget-fail: exits non-zero" || bad "restack retarget-fail: exits non-zero" "exit=$rtcode"

  # closed child (fix 3): a CLOSED child PR is a LOUD orphan — flag it, and NEVER
  # force-push its branch.
  printf 'MERGED\n' >"$C_DB/20.state"
  printf 'main\n' >"$C_DB/20.base"
  printf 'CLOSED\n' >"$C_DB/21.state"
  printf 'br-a\n' >"$C_DB/21.base"
  git -C "$C_WORK" checkout -q br-a
  git -C "$C_WORK" checkout -q -b br-closed
  echo cl >"$C_WORK/cl.txt"
  git -C "$C_WORK" add cl.txt
  git -C "$C_WORK" commit -q -m cl
  git -C "$C_WORK" push -q origin br-closed
  git -C "$C_WORK" checkout -q main
  CLOSED_TIP_BEFORE="$(git -C "$C_WORK" rev-parse origin/br-closed)"
  CL_RUN="$RS/cl-run"
  mkdir -p "$CL_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_p2 | handed-off | br-a | main | - | #20 | |\n'
    printf '| t_cl | handed-off | br-closed | br-a | %s | #21 | |\n' "$A_SHA"
  } >"$CL_RUN/.auto-pilot/RUN.md"
  clout="$("$SCRIPT" restack --run-dir "$CL_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  clcode=$?
  have "restack closed-child: flagged as a DEFECT" 'DEFECT — PR #21 is CLOSED' "$clout"
  git -C "$C_WORK" fetch -q origin
  [ "$(git -C "$C_WORK" rev-parse origin/br-closed)" = "$CLOSED_TIP_BEFORE" ] \
    && ok "restack closed-child: branch was NOT force-pushed" \
    || bad "restack closed-child: branch was NOT force-pushed"
  [ "$clcode" = 2 ] && ok "restack closed-child: exits non-zero" || bad "restack closed-child: exits non-zero" "exit=$clcode"

  unset FAKE_GH_DB
else
  echo "skip - restack: git not available"
fi

# --- task 16: the ALARM — a halted or stalled run must actively TELL a human ---
# The bug in finding #22 was not the 401, it was the SILENCE: every guard wrote
# to REPORT.md, a file on a branch nobody reads at 3am, while the supervisor
# relaunched into the same non-retryable 401 for 4h14m.
#
# These tests OBSERVE BEHAVIOR, never source shape: `osascript`, `launchctl`,
# `claude` and `sandbox-exec` are real STUB EXECUTABLES on the launch PATH that
# record every invocation to a marker file, and the fatal-auth test drives the
# REAL generated launch wrapper. A test that would still pass with the alarm
# deleted is worthless here — every production failure in this system exited 0.
if command -v git >/dev/null 2>&1; then
  AL="$BASE/alarm"
  ALSTUB="$AL/stub"
  mkdir -p "$ALSTUB"
  OSA_CALLS="$AL/osascript.calls"
  LC_CALLS="$AL/launchctl.calls"
  CLAUDE_CALLS="$AL/claude.calls"

  # osascript stub: records the AppleScript it was asked to run. The REAL
  # /usr/bin/osascript is exec-DENIED inside the jail (orchestrator.sb.tmpl), so
  # observing this call is what proves the alarm fires from the UN-JAILED
  # supervisor and not from inside the agent (where it would be silently denied).
  printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>%s\nexit 0\n' "$OSA_CALLS" >"$ALSTUB/osascript"
  # launchctl stub: records the bootout; `print` exits 1 (job gone) so the halt's
  # bootout verification sees a torn-down job instead of warning.
  printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>%s\n[ "$1" = print ] && exit 1\nexit 0\n' "$LC_CALLS" >"$ALSTUB/launchctl"
  # claude stub: dies exactly the way run #2 died — a 401 on the CLI's own error
  # surface — and counts its own invocations, so a test can prove the alarm was
  # raised with NO further model call.
  printf '#!/bin/sh\nprintf "call\\n" >>%s\nprintf "API Error: 401 Invalid authentication credentials\\n"\nexit 1\n' \
    "$CLAUDE_CALLS" >"$ALSTUB/claude"
  # sandbox-exec stub: drops `-f <profile>` and execs the rest, so the REAL
  # generated wrapper runs end-to-end offline without nesting a Seatbelt jail
  # (the wrapper's sandbox-exec composition itself is asserted above).
  printf '#!/bin/sh\n[ "$1" = -f ] && shift 2\nexec "$@"\n' >"$ALSTUB/sandbox-exec"
  chmod +x "$ALSTUB/osascript" "$ALSTUB/launchctl" "$ALSTUB/claude" "$ALSTUB/sandbox-exec"
  ALPATH="$ALSTUB:$GUARD:/usr/bin:/bin"

  # <dir> <status> <until> <n-parked>: a real git run worktree, so the halt's
  # run-state commit and the no-progress guard's HEAD read both work for real.
  mkrun() {
    local d="$1" st="$2" un="$3" np="$4" i=0
    mkdir -p "$d/.auto-pilot"
    (cd "$d" && git init -q && git config user.email t@e && git config user.name t)
    {
      printf -- '---\n'
      printf 'status: %s\n' "$st"
      printf 'pause_reason: \n'
      printf 'until: %s\n' "$un"
      printf -- '---\n'
      printf '| task | phase | branch | base | base_sha | pr | notes |\n'
      printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
      while [ "$i" -lt "$np" ]; do
        printf '| T-%s | parked | b%s | main | - | - | x |\n' "$i" "$i"
        i=$((i + 1))
      done
    } >"$d/.auto-pilot/RUN.md"
    printf '# report\n' >"$d/.auto-pilot/REPORT.md"
    (cd "$d" && git add -A && git -c user.name=t -c user.email=t@e commit -q -m init)
  }
  # One supervisor wake, with the stubs on the launch PATH (the launchd wrapper's
  # own PATH is exactly this shape: a resolved list, not the caller's env).
  alwake() { # <dir> <exit-code> <log>
    PATH="$ALPATH" "$SCRIPT" supervisor-check --exit-code "$2" --log "$3" --dir "$1" \
      --label com.autopilot.test.alarm --state "$1/.auto-pilot/supervisor-state" 2>&1
  }
  # <name> <dir> <condition> <action-substring>: every alarm condition owes the
  # human the SAME three things — the shell-visible sentinel, REPORT.md's very
  # first line, and an OS notification that names what they must DO.
  alarm_asserts() {
    local name="$1" d="$2" cond="$3" action="$4"
    local sent="$d/.auto-pilot/ALARM"
    have "alarm/$name: writes the ALARM sentinel" "condition: $cond" "$(cat "$sent" 2>/dev/null)"
    have "alarm/$name: sentinel names the action" "$action" "$(cat "$sent" 2>/dev/null)"
    have "alarm/$name: REPORT.md's FIRST line is the alarm" \
      "**ALARM" "$(head -1 "$d/.auto-pilot/REPORT.md" 2>/dev/null)"
    have "alarm/$name: REPORT.md's first line names the condition" \
      "$cond" "$(head -1 "$d/.auto-pilot/REPORT.md" 2>/dev/null)"
    have "alarm/$name: the OS notification was actually INVOKED" \
      'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
    have "alarm/$name: the notification tells the human what to DO" \
      "$action" "$(cat "$OSA_CALLS" 2>/dev/null)"
  }

  # (1) fatal auth halt — the #22 condition itself.
  : >"$OSA_CALLS"
  A1="$AL/fatal"
  mkrun "$A1" active 2099-01-01T00:00:00 0
  printf 'API Error: 401 Invalid authentication credentials\n' >"$A1/.auto-pilot/orchestrator.log"
  a1out="$(alwake "$A1" 1 "$A1/.auto-pilot/orchestrator.log")"
  have "alarm/fatal-auth: the wake reports the ALARM" 'ALARM fatal-auth' "$a1out"
  alarm_asserts "fatal-auth" "$A1" "fatal-auth" 'claude /login'

  # (2) circuit-breaker / systemic — written by the AGENT, delivered by the
  # supervisor (the agent cannot notify: the jail denies osascript).
  : >"$OSA_CALLS"
  A2="$AL/systemic"
  mkrun "$A2" systemic 2099-01-01T00:00:00 0
  printf 'ok\n' >"$A2/.auto-pilot/orchestrator.log"
  a2out="$(alwake "$A2" 0 "$A2/.auto-pilot/orchestrator.log")"
  have "alarm/systemic: an exit-0 wake still alarms on a systemic RUN.md" 'ALARM systemic' "$a2out"
  alarm_asserts "systemic" "$A2" "systemic" 'REPORT.md'

  # (3) a failed invariant — raised by an IN-JAIL detector via alarm-request
  # (which cannot notify), drained + delivered by the un-jailed supervisor.
  : >"$OSA_CALLS"
  A3="$AL/invariant"
  mkrun "$A3" active 2099-01-01T00:00:00 0
  printf 'ok\n' >"$A3/.auto-pilot/orchestrator.log"
  "$SCRIPT" alarm-request --dir "$A3" --condition invariant \
    --reason 'invariant 7 FAILED: the run made no forward progress for 2 wakes' >/dev/null 2>&1
  [ -f "$A3/.auto-pilot/alarm-requests/invariant.alarm" ] \
    && ok "alarm/invariant: the jailed side writes a request it cannot deliver" \
    || bad "alarm/invariant: the jailed side writes a request it cannot deliver"
  lack "alarm/invariant: the jailed request raises NO notification by itself" \
    'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
  a3out="$(alwake "$A3" 0 "$A3/.auto-pilot/orchestrator.log")"
  have "alarm/invariant: the supervisor delivers the drained request" 'ALARM invariant' "$a3out"
  alarm_asserts "invariant" "$A3" "invariant" 'REPORT.md'
  [ -e "$A3/.auto-pilot/alarm-requests/invariant.alarm" ] \
    && bad "alarm/invariant: a delivered request is consumed" \
    || ok "alarm/invariant: a delivered request is consumed"

  # (4) N consecutive no-progress wakes — the STALL. #22 never reached a halt
  # state at all: RUN.md looked healthy and the run did nothing.
  : >"$OSA_CALLS"
  A4="$AL/noprogress"
  mkrun "$A4" active 2099-01-01T00:00:00 0
  printf 'some other unrelated crash\n' >"$A4/.auto-pilot/orchestrator.log"
  alwake "$A4" 1 "$A4/.auto-pilot/orchestrator.log" >/dev/null
  alwake "$A4" 1 "$A4/.auto-pilot/orchestrator.log" >/dev/null
  lack "alarm/no-progress: wakes below the limit do NOT alarm (no premature noise)" \
    'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
  a4out="$(alwake "$A4" 1 "$A4/.auto-pilot/orchestrator.log")"
  have "alarm/no-progress: the 3rd stalled wake alarms" 'ALARM no-progress' "$a4out"
  alarm_asserts "no-progress" "$A4" "no-progress" 'STALLED'

  # (5) a park storm — a graveyard of parked tasks with no single signal is
  # exactly what run-budget.md's circuit breaker exists to turn into one alarm.
  # It REPORTS but must not tear the job down: the other tasks may still deliver.
  : >"$OSA_CALLS"
  : >"$LC_CALLS"
  A5="$AL/parkstorm"
  mkrun "$A5" active 2099-01-01T00:00:00 3
  printf 'ok\n' >"$A5/.auto-pilot/orchestrator.log"
  a5out="$(alwake "$A5" 0 "$A5/.auto-pilot/orchestrator.log")"
  have "alarm/park-storm: 3 parked tasks alarm" 'ALARM park-storm' "$a5out"
  alarm_asserts "park-storm" "$A5" "park-storm" 'unblock them'
  lack "alarm/park-storm: reports but does NOT tear the job down" 'bootout' "$(cat "$LC_CALLS" 2>/dev/null)"
  # …and a run under the limit never alarms (the threshold is real, not decorative)
  : >"$OSA_CALLS"
  A5B="$AL/parkfew"
  mkrun "$A5B" active 2099-01-01T00:00:00 2
  printf 'ok\n' >"$A5B/.auto-pilot/orchestrator.log"
  alwake "$A5B" 0 "$A5B/.auto-pilot/orchestrator.log" >/dev/null
  lack "alarm/park-storm: 2 parked tasks (< limit) do not alarm" \
    'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
  [ -e "$A5B/.auto-pilot/ALARM" ] && bad "alarm/park-storm: no sentinel under the limit" \
    || ok "alarm/park-storm: no sentinel under the limit"

  # (6) a run that blew its --until without finishing.
  : >"$OSA_CALLS"
  A6="$AL/deadline"
  mkrun "$A6" active 2020-01-01T00:00:00 0
  printf 'ok\n' >"$A6/.auto-pilot/orchestrator.log"
  a6out="$(alwake "$A6" 0 "$A6/.auto-pilot/orchestrator.log")"
  have "alarm/deadline: a blown --until alarms" 'ALARM deadline' "$a6out"
  alarm_asserts "deadline" "$A6" "deadline" '--until'
  # …but a FINISHED run's past deadline is not an alarm (it's just a finished run)
  : >"$OSA_CALLS"
  A6B="$AL/deadline-done"
  mkrun "$A6B" done 2020-01-01T00:00:00 0
  printf 'ok\n' >"$A6B/.auto-pilot/orchestrator.log"
  alwake "$A6B" 0 "$A6B/.auto-pilot/orchestrator.log" >/dev/null
  lack "alarm/deadline: a DONE run's past deadline never alarms" \
    'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
  # (6c) REGRESSION (PRE-625): `until` is a UTC timestamp (run-state.md), so the
  # deadline check must compare it against a UTC `now` — comparing a LOCAL `now`
  # misfired by the machine's offset, halting a live run hours early east of UTC
  # ("blew the --until deadline" while it is still in the future). The cases above
  # use 2020/2099 dates no ±14h offset can flip, so they never caught it. This one
  # sets `until` 30 min in the FUTURE (UTC) and wakes under a +9h zone: pre-fix the
  # local-time compare reads it as blown; a UTC compare correctly does not. Force
  # the zone so the guard is deterministic on any runner, not just a non-UTC one.
  : >"$OSA_CALLS"
  A6C="$AL/deadline-tz"
  un_future="$(date -u -v+30M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+30 minutes' +%Y-%m-%dT%H:%M:%SZ)"
  mkrun "$A6C" active "$un_future" 0
  printf 'ok\n' >"$A6C/.auto-pilot/orchestrator.log"
  a6cout="$(TZ=Asia/Tokyo alwake "$A6C" 0 "$A6C/.auto-pilot/orchestrator.log")"
  lack "alarm/deadline: a still-future --until does NOT alarm under a non-UTC TZ (UTC compare)" \
    'ALARM deadline' "$a6cout"
  [ -e "$A6C/.auto-pilot/ALARM" ] \
    && bad "alarm/deadline: spurious deadline sentinel for a future --until under a +9h zone" \
    || ok "alarm/deadline: no spurious deadline sentinel for a future --until under a non-UTC TZ"
  # (6d) REGRESSION (PRE-625, offset form): `until` may carry a numeric zone offset
  # (--until accepts any absolute ISO-8601 time). The old check only stripped a
  # trailing `Z`, so an offset was never normalized — it compared the raw offset
  # string and misread the deadline. Parsing it through _parse_iso8601_utc makes it
  # a real epoch compare. This value is 30 min in the FUTURE (UTC) written in a
  # -05:00 zone; a naive read of its wall-clock hour looks already past, a correct
  # parse does not. The offset lives in the value itself, so no TZ forcing is
  # needed — deterministic on every runner.
  : >"$OSA_CALLS"
  A6D="$AL/deadline-offset"
  un_offset="$(date -u -v+30M -v-5H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '+30 minutes -5 hours' +%Y-%m-%dT%H:%M:%S)-05:00"
  mkrun "$A6D" active "$un_offset" 0
  printf 'ok\n' >"$A6D/.auto-pilot/orchestrator.log"
  a6dout="$(alwake "$A6D" 0 "$A6D/.auto-pilot/orchestrator.log")"
  lack "alarm/deadline: a future --until with a numeric zone offset does NOT alarm (normalized, not string-compared)" \
    'ALARM deadline' "$a6dout"

  # (7) THE load-bearing one: the alarm fires from the SUPERVISOR with NO model
  # call. Drive the REAL generated launch wrapper with a `claude` that dies on a
  # 401 (the agent is dead by construction — it cannot alarm for itself, and the
  # jail would deny it osascript anyway). Assert claude is invoked exactly ONCE:
  # the alarm must not have needed a second model call to produce itself.
  : >"$OSA_CALLS"
  : >"$CLAUDE_CALLS"
  : >"$LC_CALLS"
  A7="$AL/wrapper"
  mkrun "$A7" active 2099-01-01T00:00:00 0
  A7LOG="$A7/.auto-pilot/orchestrator.log"
  : >"$A7LOG"
  printf 'run the graph\n' >"$AL/prompt.txt"
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$A7" \
    --log "$A7LOG" --prompt-file "$AL/prompt.txt" --until '2099-01-01T00:00:00' \
    --label com.autopilot.test.alarm --claude-bin "$ALSTUB/claude" --path "$ALPATH" \
    --out-script "$AL/launch.sh" --out-plist "$AL/job.plist" >/dev/null 2>&1
  if [ -x "$AL/launch.sh" ]; then
    bash "$AL/launch.sh" >/dev/null 2>&1
    ccalls="$(wc -l <"$CLAUDE_CALLS" 2>/dev/null | tr -d ' ')"
    [ "$ccalls" = 1 ] \
      && ok "alarm/no-model-call: the alarm needed NO further claude invocation (claude ran once, and died)" \
      || bad "alarm/no-model-call: the alarm needed NO further claude invocation" "claude invocations=$ccalls"
    have "alarm/no-model-call: the real wrapper's supervisor notified the human" \
      'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
    have "alarm/no-model-call: the notification says to re-authenticate" \
      'claude /login' "$(cat "$OSA_CALLS" 2>/dev/null)"
    have "alarm/no-model-call: the notification names the run" \
      'com.autopilot.test.alarm' "$(cat "$OSA_CALLS" 2>/dev/null)"
    [ -f "$A7/.auto-pilot/ALARM" ] \
      && ok "alarm/no-model-call: the real wrapper wrote the ALARM sentinel" \
      || bad "alarm/no-model-call: the real wrapper wrote the ALARM sentinel"
    have "alarm/no-model-call: REPORT.md leads with the alarm" \
      '**ALARM' "$(head -1 "$A7/.auto-pilot/REPORT.md" 2>/dev/null)"
    have "alarm/no-model-call: the job is torn down (never relaunches into the same 401)" \
      'bootout' "$(cat "$LC_CALLS" 2>/dev/null)"
    have "alarm/no-model-call: the halt is recorded as systemic" \
      'status: systemic' "$(cat "$A7/.auto-pilot/RUN.md" 2>/dev/null)"
  else
    bad "alarm/no-model-call: write-launch produced no launch script"
  fi

  # (8) IDEMPOTENCY: a supervisor wakes every 300s. N wakes in the SAME condition
  # must produce ONE notification, or the alarm becomes the new noise and the
  # next real one is ignored. (Relaunch happens for real when a bootout doesn't
  # take — the very case the halt already warns about.)
  : >"$OSA_CALLS"
  A8="$AL/idem"
  mkrun "$A8" active 2099-01-01T00:00:00 0
  printf 'API Error: 401 Invalid authentication credentials\n' >"$A8/.auto-pilot/orchestrator.log"
  i=0
  while [ "$i" -lt 5 ]; do
    alwake "$A8" 1 "$A8/.auto-pilot/orchestrator.log" >/dev/null
    i=$((i + 1))
  done
  ncalls="$(grep -c 'display notification' "$OSA_CALLS" 2>/dev/null | tr -d ' ')"
  [ "$ncalls" = 1 ] && ok "alarm/idempotent: 5 wakes in the same condition notify ONCE" \
    || bad "alarm/idempotent: 5 wakes in the same condition notify ONCE" "notifications=$ncalls"
  nlines="$(grep -c '^> \*\*ALARM' "$A8/.auto-pilot/REPORT.md" 2>/dev/null | tr -d ' ')"
  [ "$nlines" = 1 ] && ok "alarm/idempotent: REPORT.md gets ONE top-line, not one per wake" \
    || bad "alarm/idempotent: REPORT.md gets ONE top-line, not one per wake" "lines=$nlines"
  nconds="$(grep -c '^condition: fatal-auth' "$A8/.auto-pilot/ALARM" 2>/dev/null | tr -d ' ')"
  [ "$nconds" = 1 ] && ok "alarm/idempotent: the sentinel records the condition once" \
    || bad "alarm/idempotent: the sentinel records the condition once" "entries=$nconds"

  # (9) a FAILED notifier must never cost the durable alarm — the marker + the
  # REPORT.md line are what a human (or `status`) can still see from a shell.
  # Both notifiers are stubbed to FAIL (exactly what the jail's process-exec deny
  # looks like from the caller's side), ahead of the real ones on PATH.
  ALFAIL="$AL/stub-fail"
  mkdir -p "$ALFAIL"
  printf '#!/bin/sh\nexit 1\n' >"$ALFAIL/osascript"
  printf '#!/bin/sh\nexit 1\n' >"$ALFAIL/terminal-notifier"
  chmod +x "$ALFAIL/osascript" "$ALFAIL/terminal-notifier"
  A9="$AL/nonotify"
  mkrun "$A9" active 2099-01-01T00:00:00 0
  printf 'API Error: 401 Invalid authentication credentials\n' >"$A9/.auto-pilot/orchestrator.log"
  n9="$(PATH="$ALFAIL:$GUARD:/usr/bin:/bin" "$SCRIPT" supervisor-check --exit-code 1 \
    --log "$A9/.auto-pilot/orchestrator.log" --dir "$A9" --label com.autopilot.test.nonotify \
    --state "$A9/.auto-pilot/supervisor-state" 2>&1)"
  have "alarm/no-notifier: says the notification failed" 'NOTIFY FAILED' "$n9"
  [ -f "$A9/.auto-pilot/ALARM" ] \
    && ok "alarm/no-notifier: the ALARM sentinel is still written" \
    || bad "alarm/no-notifier: the ALARM sentinel is still written"
  have "alarm/no-notifier: REPORT.md still leads with the alarm" \
    '**ALARM' "$(head -1 "$A9/.auto-pilot/REPORT.md" 2>/dev/null)"
  have "alarm/no-notifier: the halt still completed" 'status: systemic' "$(cat "$A9/.auto-pilot/RUN.md" 2>/dev/null)"

  # (10) `status` surfaces the alarm — the sentinel is only useful if the one-shot
  # state reporter shows it (the human who missed the desktop notification).
  st10="$("$SCRIPT" status --label com.autopilot.test.alarm --dir "$A1" 2>&1)"
  have "alarm/status: reports the raised condition" 'alarm: fatal-auth' "$st10"
  have "alarm/status: STATUS line carries the alarm count" 'alarms=1' "$st10"
  st10b="$("$SCRIPT" status --label com.autopilot.test.alarm --dir "$A5B" 2>&1)"
  have "alarm/status: a healthy run reports no alarm" 'alarm: none' "$st10b"

  # (11) THE GATE MUST NOT SWALLOW THE ALARM. Tests (1)-(10) call supervisor-check
  # directly, which is exactly how the bug hid: the REAL wrapper runs the
  # pre-invoke gate (task 11) FIRST, and the gate's `exit 0` used to short-circuit
  # the whole supervisor — alarm scan included — on precisely the wakes that prove
  # a run is stuck. These drive the GENERATED WRAPPER, gate and all.
  printf 'run the graph\n' >"$AL/prompt.txt"
  mkwrapper() { # <run-dir> <label> <out-script>
    "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$1" \
      --log "$1/.auto-pilot/orchestrator.log" --prompt-file "$AL/prompt.txt" \
      --until '2099-01-01T00:00:00' --label "$2" --claude-bin "$ALSTUB/claude" \
      --path "$ALPATH" --out-script "$3" --out-plist "$3.plist" >/dev/null 2>&1
  }
  # <dir> <status> <paused_until> <until>: a run the GATE will close on — either
  # paused (a future paused_until) or terminal (done/systemic).
  mkgaterun() {
    local d="$1"
    mkdir -p "$d/.auto-pilot"
    (cd "$d" && git init -q && git config user.email t@e && git config user.name t)
    {
      printf -- '---\n'
      printf 'status: %s\n' "$2"
      printf 'pause_reason: \n'
      printf 'paused_until: %s\n' "$3"
      printf 'until: %s\n' "$4"
      printf -- '---\n'
      printf '| task | phase | branch | base | base_sha | pr | notes |\n'
      printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    } >"$d/.auto-pilot/RUN.md"
    printf '# report\n' >"$d/.auto-pilot/REPORT.md"
    : >"$d/.auto-pilot/orchestrator.log"
    (cd "$d" && git add -A && git -c user.name=t -c user.email=t@e commit -q -m init)
  }

  # (11a) The agent's circuit breaker wrote `status: systemic`, and the wake that
  # would have announced it never finished (sleep / reboot / power loss — the
  # overnight runs this is FOR). The gate now sees `systemic`, boots the job out,
  # and exits 0: the LAST wake this run will ever get. If the scan sits under that
  # exit, the run is torn down forever with nobody told — finding #22's silence,
  # restored by the very mechanism meant to end it.
  : >"$OSA_CALLS"
  : >"$CLAUDE_CALLS"
  : >"$LC_CALLS"
  G1="$AL/gate-systemic"
  mkgaterun "$G1" systemic '' 2099-01-01T00:00:00
  mkwrapper "$G1" com.autopilot.test.gsys "$AL/gsys.sh"
  bash "$AL/gsys.sh" >/dev/null 2>&1
  have "alarm/gate-systemic: a gate-CLOSED wake still alarms (the gate skips the AGENT, not the supervisor)" \
    'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
  alarm_asserts "gate-systemic" "$G1" "systemic" 'REPORT.md'
  [ -s "$CLAUDE_CALLS" ] && bad "alarm/gate-systemic: the agent is NOT invoked (the gate still gates)" \
    || ok "alarm/gate-systemic: the agent is NOT invoked (the gate still gates)"
  have "alarm/gate-systemic: the job is torn down" 'bootout' "$(cat "$LC_CALLS" 2>/dev/null)"

  # (11b) A run parked behind a multi-hour rate-window pause that blew its --until.
  # The gate closes on paused_until and exits 0 — for the whole pause. A deadline
  # (or a park storm, or a pending in-jail alarm-request) under that exit is a
  # stalled run staying silent for hours: the exact #22 shape.
  : >"$OSA_CALLS"
  : >"$CLAUDE_CALLS"
  : >"$LC_CALLS"
  G2="$AL/gate-paused"
  mkgaterun "$G2" paused 2099-01-01T00:00:00Z 2020-01-01T00:00:00
  mkwrapper "$G2" com.autopilot.test.gpause "$AL/gpause.sh"
  bash "$AL/gpause.sh" >/dev/null 2>&1
  have "alarm/gate-paused: a blown --until alarms even though the gate closed the wake" \
    'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
  alarm_asserts "gate-paused" "$G2" "deadline" '--until'
  [ -s "$CLAUDE_CALLS" ] && bad "alarm/gate-paused: the agent is NOT invoked (no model call in a pause)" \
    || ok "alarm/gate-paused: the agent is NOT invoked (no model call in a pause)"

  # (11c) …and a HEALTHY paused run alarms NOTHING. A closed gate is not itself a
  # condition — the scan decides. Get this wrong and every paused wake screams.
  : >"$OSA_CALLS"
  : >"$CLAUDE_CALLS"
  G3="$AL/gate-healthy"
  mkgaterun "$G3" paused 2099-01-01T00:00:00Z 2099-01-01T00:00:00
  mkwrapper "$G3" com.autopilot.test.ghealthy "$AL/ghealthy.sh"
  bash "$AL/ghealthy.sh" >/dev/null 2>&1
  lack "alarm/gate-healthy: a healthy paused wake raises NO notification" \
    'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
  [ -e "$G3/.auto-pilot/ALARM" ] && bad "alarm/gate-healthy: a healthy paused wake writes no sentinel" \
    || ok "alarm/gate-healthy: a healthy paused wake writes no sentinel"

  # (12) A BROKEN ALARM MUST NOT BREAK THE HALT. `die` is an `exit`, so an alarm
  # that dies takes the supervisor down mid-halt — before the teardown — leaving a
  # `systemic` RUN.md next to a still-loaded job that relaunches into the same
  # condition every 300s: #22's loop, wearing the halt message as camouflage. The
  # sentinel path is made unwritable (a directory) to force the failure.
  : >"$LC_CALLS"
  : >"$OSA_CALLS"
  A11="$AL/badsentinel"
  mkrun "$A11" active 2099-01-01T00:00:00 0
  mkdir -p "$A11/.auto-pilot/ALARM"
  printf 'API Error: 401 Invalid authentication credentials\n' >"$A11/.auto-pilot/orchestrator.log"
  alwake "$A11" 1 "$A11/.auto-pilot/orchestrator.log" >/dev/null 2>&1
  have "alarm/broken-sentinel: the job is STILL torn down (a dead alarm cannot strand a live job)" \
    'bootout' "$(cat "$LC_CALLS" 2>/dev/null)"
  have "alarm/broken-sentinel: the halt still records systemic" \
    'status: systemic' "$(cat "$A11/.auto-pilot/RUN.md" 2>/dev/null)"
  have "alarm/broken-sentinel: the human is still notified" \
    'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"
  have "alarm/broken-sentinel: REPORT.md still leads with the alarm" \
    '**ALARM' "$(head -1 "$A11/.auto-pilot/REPORT.md" 2>/dev/null)"

  # (13) `--resume` clears the alarms. Every alarm's required action ends in a
  # `--resume`, so a sentinel that outlives one would suppress the NEXT alarm for
  # the same condition (a token that expires again) and the repaired run would halt
  # in silence. REPORT.md's history is not cleared — that is what the human reads.
  "$SCRIPT" alarm-request --dir "$A1" --condition invariant --reason 'pending' >/dev/null 2>&1
  "$SCRIPT" alarm-clear --dir "$A1" >/dev/null 2>&1
  [ -e "$A1/.auto-pilot/ALARM" ] && bad "alarm/clear: --resume drops the sentinel" \
    || ok "alarm/clear: --resume drops the sentinel"
  [ -e "$A1/.auto-pilot/alarm-requests" ] && bad "alarm/clear: --resume drops undelivered requests" \
    || ok "alarm/clear: --resume drops undelivered requests"
  have "alarm/clear: REPORT.md's alarm history SURVIVES the clear" \
    '**ALARM' "$(head -1 "$A1/.auto-pilot/REPORT.md" 2>/dev/null)"
  have "alarm/clear: status reports no alarm on the resumed run" \
    'alarm: none' "$("$SCRIPT" status --label com.autopilot.test.alarm --dir "$A1" 2>&1)"
  # …and the SAME condition can alarm again — the key is per run, and a resumed run
  # is a run that can fail again.
  : >"$OSA_CALLS"
  PATH="$ALPATH" "$SCRIPT" alarm --dir "$A1" --label com.autopilot.test.alarm \
    --condition fatal-auth --reason 're-expired after the resume' >/dev/null 2>&1
  have "alarm/clear: the same condition alarms AGAIN after a resume" \
    'display notification' "$(cat "$OSA_CALLS" 2>/dev/null)"

  # fail-closed: the condition id is an idempotency key and a grep anchor; the
  # request's fields are read back line-wise, so a newline could forge them.
  afc() {
    local name="$1" want="$2"
    shift 2
    local o c
    o="$("$SCRIPT" "$@" 2>&1)"
    c=$?
    if [ "$c" = 2 ] && printf '%s' "$o" | grep -qF "$want"; then ok "alarm fail-closed: $name"; else bad "alarm fail-closed: $name" "exit=$c msg=$o"; fi
  }
  afc "bad condition charset" "must be [A-Za-z0-9._-]" alarm --dir "$A1" --condition 'bad id' --reason x
  afc "relative --dir" "must be absolute" alarm --dir "rel/ative" --condition x --reason y
  afc "missing --reason" "requires --dir" alarm --dir "$A1" --condition x
  afc "request newline reason" "must not contain a newline" \
    alarm-request --dir "$A1" --condition x --reason "$(printf 'a\ncondition: forged')"
else
  echo "skip - alarm: git not available"
fi

# --- task 15: the exit contract (declared reason) + the heartbeat -------------
# The whole point of this task is that a green run and a wedged one used to be
# the SAME observable event (exit 0). So these tests refuse to assert on strings
# the script printed about itself: they drive the REAL generated launch wrapper
# with a stub `claude` (which records that it ran, and declares its exit reason
# exactly as the orchestrator prompt is specified to) and a stub `launchctl`
# (which records every invocation), then assert on the MARKER FILES — did the
# supervisor actually boot the job out, or not?
if command -v git >/dev/null 2>&1; then
  EC="$BASE/exitcontract"
  STUB="$EC/stub"
  mkdir -p "$STUB"

  # sandbox-exec stub: drop `-f <profile>` and exec the rest. The real wrapper
  # composes the jail around claude; here we want the wrapper's LOGIC exercised
  # end-to-end without needing a loadable Seatbelt profile (or macOS at all).
  cat >"$STUB/sandbox-exec" <<'SBEOF'
#!/usr/bin/env bash
[ "${1:-}" = -f ] && shift 2
exec "$@"
SBEOF
  # launchctl stub: the OBSERVATION point. A teardown means a real `bootout` call
  # reaches launchctl; a relaunch means it never does. `print` exits non-zero so
  # the halt's post-bootout verification reads the job as gone (a successful bootout).
  cat >"$STUB/launchctl" <<'LCEOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${STUB_LAUNCHCTL_LOG:?}"
case "${1:-}" in print) exit 1 ;; esac
exit 0
LCEOF
  # claude stub: proves the real code path ran (marker), writes a stream-json line
  # to the log, and DECLARES its exit reason through the real `exit-reason`
  # subcommand — the same call the orchestrator prompt makes before exiting.
  cat >"$STUB/claude" <<'CLEOF'
#!/usr/bin/env bash
printf 'ran\n' >>"${STUB_CLAUDE_MARKER:?}"
printf '{"type":"assistant","message":{"content":[{"type":"text","text":"stub wake"}]}}\n'
[ -n "${STUB_DECLARE:-}" ] \
  && "${STUB_SELF:?}" exit-reason --dir "${STUB_RUN_DIR:?}" --reason "$STUB_DECLARE" >/dev/null 2>&1
exit "${STUB_EXIT_CODE:-0}"
CLEOF
  chmod +x "$STUB/sandbox-exec" "$STUB/launchctl" "$STUB/claude"
  STUB_PATH="$STUB:$GUARD:/usr/bin:/bin"

  # Drive one full wake through the REAL generated launch script.
  # ec_wake <name> <declared-reason|""> <claude-exit-code>
  # Leaves: $EC_DIR (run dir), $EC_LC (launchctl log), $EC_MARK (claude marker).
  ec_wake() {
    local name="$1" declare="$2" ecode="$3"
    EC_DIR="$EC/$name"
    EC_LC="$EC_DIR/launchctl.log"
    EC_MARK="$EC_DIR/claude-ran"
    mkdir -p "$EC_DIR/.auto-pilot"
    {
      printf -- '---\n'
      printf 'run_id: %s\n' "$name"
      printf 'status: active\n'
      printf 'pause_reason: \n'
      printf -- '---\n'
      printf '\n'
      printf '| task | phase | branch | base | base_sha | pr | notes |\n'
      printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
      printf '| T-1  | claimed | b1   | main | -        | -  | -     |\n'
    } >"$EC_DIR/.auto-pilot/RUN.md"
    printf '# report\n' >"$EC_DIR/.auto-pilot/REPORT.md"
    (cd "$EC_DIR" && git init -q && git add -A \
      && git -c user.name=t -c user.email=t@t commit -q -m init)
    : >"$EC_DIR/.auto-pilot/orchestrator.log"
    : >"$EC_LC"
    rm -f "$EC_MARK"
    "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" \
      --workdir "$EC_DIR" --log "$EC_DIR/.auto-pilot/orchestrator.log" \
      --prompt-file "$BASE/prompt.txt" --label "com.autopilot.ec.$name" \
      --claude-bin "$STUB/claude" --path "$STUB_PATH" \
      --out-script "$EC_DIR/launch.sh" --out-plist "$EC_DIR/job.plist" >/dev/null 2>&1
    STUB_LAUNCHCTL_LOG="$EC_LC" STUB_CLAUDE_MARKER="$EC_MARK" STUB_SELF="$SCRIPT" \
      STUB_RUN_DIR="$EC_DIR" STUB_DECLARE="$declare" STUB_EXIT_CODE="$ecode" \
      bash "$EC_DIR/launch.sh" >/dev/null 2>&1
  }
  # <name> <label-for-the-assertion> <yes|no: is a teardown expected?>
  ec_assert() {
    local name="$1" want_teardown="$2"
    local lc
    lc="$(cat "$EC/$name/launchctl.log" 2>/dev/null)"
    if [ -f "$EC/$name/claude-ran" ]; then
      ok "exit contract [$name]: the generated wrapper really invoked claude"
    else
      bad "exit contract [$name]: the generated wrapper really invoked claude"
    fi
    if [ "$want_teardown" = yes ]; then
      if printf '%s' "$lc" | grep -q 'bootout'; then
        ok "exit contract [$name]: supervisor TORE THE JOB DOWN (launchctl bootout observed)"
      else
        bad "exit contract [$name]: supervisor TORE THE JOB DOWN (launchctl bootout observed)" "launchctl log: ${lc:-<empty>}"
      fi
      if [ -f "$EC/$name/.auto-pilot/orchestrator.done" ]; then
        ok "exit contract [$name]: done-sentinel written (the single relaunch/completion file)"
      else
        bad "exit contract [$name]: done-sentinel written (the single relaunch/completion file)"
      fi
    else
      if printf '%s' "$lc" | grep -q 'bootout'; then
        bad "exit contract [$name]: supervisor did NOT tear down (relaunch expected)" "launchctl log: $lc"
      else
        ok "exit contract [$name]: supervisor did NOT tear down (launchd relaunches on its timer)"
      fi
      if [ -f "$EC/$name/.auto-pilot/orchestrator.done" ]; then
        bad "exit contract [$name]: no done-sentinel on a relaunchable exit"
      else
        ok "exit contract [$name]: no done-sentinel on a relaunchable exit"
      fi
    fi
  }

  # continuing — work remains, context exhausted. Exit 0, which pre-task-15 was
  # INDISTINGUISHABLE from a finished run: the run must NOT be torn down.
  ec_wake continuing continuing 0
  ec_assert continuing no
  have "exit contract [continuing]: reason committed to the run-state branch" \
    'exit_reason: continuing' "$(git -C "$EC/continuing" show HEAD:.auto-pilot/RUN.md 2>&1)"
  have "exit contract [continuing]: status says a relaunch is expected" 'relaunch=yes' \
    "$("$SCRIPT" status --label com.autopilot.ec.continuing --dir "$EC/continuing" 2>&1)"

  # paused — rate window. Relaunch past the reset.
  ec_wake paused paused 0
  ec_assert paused no
  have "exit contract [paused]: reason committed to the run-state branch" \
    'exit_reason: paused' "$(git -C "$EC/paused" show HEAD:.auto-pilot/RUN.md 2>&1)"

  # done — no ready tasks remain. Tear down; do not relaunch. THE distinction
  # this task exists for: same exit code 0 as `continuing`, opposite decision.
  ec_wake done done 0
  ec_assert done yes
  have "exit contract [done]: reason committed to the run-state branch" \
    'exit_reason: done' "$(git -C "$EC/done" show HEAD:.auto-pilot/RUN.md 2>&1)"
  dstat="$("$SCRIPT" status --label com.autopilot.ec.done --dir "$EC/done" 2>&1)"
  have "exit contract [done]: status reports done" 'STATUS: done' "$dstat"
  have "exit contract [done]: status says NO relaunch" 'relaunch=no' "$dstat"

  # deadline — the pre-dispatch guard stopped with tasks still ready. Tear down;
  # only an explicit --resume brings it back (never the timer).
  ec_wake deadline deadline 0
  ec_assert deadline yes
  have "exit contract [deadline]: sentinel records the deadline reason (not a plain done)" \
    'reason: deadline' "$(cat "$EC/deadline/.auto-pilot/orchestrator.done" 2>/dev/null)"
  dlstat="$("$SCRIPT" status --label com.autopilot.ec.deadline --dir "$EC/deadline" 2>&1)"
  have "exit contract [deadline]: status says NO relaunch" 'relaunch=no' "$dlstat"
  lack "exit contract [deadline]: a deadline stop is NOT reported as a finished run" 'STATUS: done' "$dlstat"

  # systemic — circuit breaker / failed invariant. Tear down AND alarm.
  ec_wake systemic systemic 0
  ec_assert systemic yes
  have "exit contract [systemic]: RUN.md carries status: systemic" 'status: systemic' \
    "$(cat "$EC/systemic/.auto-pilot/RUN.md")"
  have "exit contract [systemic]: REPORT.md carries the alarm" 'ALARM' \
    "$(cat "$EC/systemic/.auto-pilot/REPORT.md")"
  systat="$("$SCRIPT" status --label com.autopilot.ec.systemic --dir "$EC/systemic" 2>&1)"
  have "exit contract [systemic]: status says NO relaunch" 'relaunch=no' "$systat"
  lack "exit contract [systemic]: a halted run never reads back as done" 'STATUS: done' "$systat"

  # The wrapper beats the heartbeat at the top of every wake, so even a claude
  # that wedges before its first loop iteration leaves an ageable timestamp.
  have "exit contract: the launch wrapper beats the heartbeat at wake start" 'note: wake-start' \
    "$(cat "$EC/continuing/.auto-pilot/heartbeat" 2>/dev/null)"

  # …and it beats it on a GATE-CLOSED wake too, which is the one that matters. The
  # pre-invoke gate (task 11) exits 0 without invoking the agent while `paused_until`
  # is in the future, and a rate-window pause is HOURS of such wakes. With the beat
  # below the gate's `exit 0`, the last beat ages past the 45m per-task ceiling and
  # `status` calls a healthy, paused run a STALL — the one signal that separates slow
  # from wedged, firing falsely exactly when the run is doing the right thing. So the
  # beat belongs ABOVE the gate, with the supervisor's own bookkeeping (task 16's
  # seam). Driven through the REAL generated wrapper: the gate must close (claude is
  # never invoked) AND the heartbeat must still be fresh.
  ec_wake gateclosed "" 0
  {
    printf -- '---\n'
    printf 'run_id: gateclosed\n'
    printf 'status: paused\n'
    printf 'paused_until: 2099-01-01T00:00:00\n'
    printf 'pause_reason: rate window\n'
    printf -- '---\n'
  } >"$EC/gateclosed/.auto-pilot/RUN.md"
  rm -f "$EC/gateclosed/.auto-pilot/heartbeat" "$EC/gateclosed/claude-ran"
  : >"$EC/gateclosed/launchctl.log"
  STUB_LAUNCHCTL_LOG="$EC/gateclosed/launchctl.log" STUB_CLAUDE_MARKER="$EC/gateclosed/claude-ran" \
    STUB_SELF="$SCRIPT" STUB_RUN_DIR="$EC/gateclosed" STUB_DECLARE="" STUB_EXIT_CODE=0 \
    bash "$EC/gateclosed/launch.sh" >/dev/null 2>&1
  if [ -f "$EC/gateclosed/claude-ran" ]; then
    bad "exit contract [gate-closed]: precondition — the gate really closed (claude NOT invoked)"
  else
    ok "exit contract [gate-closed]: precondition — the gate really closed (claude NOT invoked)"
  fi
  have "exit contract [gate-closed]: the heartbeat is STILL beaten (else a paused run reads as a STALL)" \
    'note: wake-start' "$(cat "$EC/gateclosed/.auto-pilot/heartbeat" 2>/dev/null)"
  have "exit contract [gate-closed]: status reports the paused run healthy, not stalled" \
    'heartbeat=healthy' "$("$SCRIPT" status --label com.autopilot.ec.gateclosed --dir "$EC/gateclosed" 2>&1)"

  # --- fail-SAFE: a stale / garbage / absent declaration never tears down ------
  # The reason lives on the run-state branch, so it OUTLIVES its wake. A previous
  # wake's `done` must not tear down a live run.
  SD="$EC/stale-done"
  mkdir -p "$SD/.auto-pilot"
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf 'exit_reason: done\n'
    printf 'exit_reason_at: 1000\n'
    printf -- '---\n'
  } >"$SD/.auto-pilot/RUN.md"
  printf '# report\n' >"$SD/.auto-pilot/REPORT.md"
  printf 'ok\n' >"$SD/log"
  : >"$SD/launchctl.log"
  STUB_LAUNCHCTL_LOG="$SD/launchctl.log" PATH="$STUB_PATH" \
    "$SCRIPT" supervisor-check --exit-code 0 --log "$SD/log" --wake-start 2000 \
    --dir "$SD" --label com.autopilot.ec.stale --state "$SD/.auto-pilot/supervisor-state" >/dev/null 2>&1
  if grep -q 'bootout' "$SD/launchctl.log" 2>/dev/null || [ -f "$SD/.auto-pilot/orchestrator.done" ]; then
    bad "exit contract: a PREVIOUS wake's 'done' does not tear down a live run"
  else
    ok "exit contract: a PREVIOUS wake's 'done' does not tear down a live run (freshness check)"
  fi

  # A garbage reason falls back to inference (task 10's path), never to a teardown.
  GB="$EC/garbage"
  mkdir -p "$GB/.auto-pilot"
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf 'exit_reason: whatever-nonsense\n'
    printf 'exit_reason_at: 9999999999\n'
    printf -- '---\n'
  } >"$GB/.auto-pilot/RUN.md"
  printf '# report\n' >"$GB/.auto-pilot/REPORT.md"
  printf 'ok\n' >"$GB/log"
  : >"$GB/launchctl.log"
  gbout="$(STUB_LAUNCHCTL_LOG="$GB/launchctl.log" PATH="$STUB_PATH" \
    "$SCRIPT" supervisor-check --exit-code 0 --log "$GB/log" --wake-start 1 \
    --dir "$GB" --label com.autopilot.ec.garbage --state "$GB/.auto-pilot/supervisor-state" 2>&1)"
  have "exit contract: a garbage reason falls back to inference" 'inferred' "$gbout"
  if grep -q 'bootout' "$GB/launchctl.log" 2>/dev/null; then
    bad "exit contract: a garbage reason never tears down (fail-safe)"
  else
    ok "exit contract: a garbage reason never tears down (fail-safe)"
  fi

  # A FATAL auth exit still halts even when this wake declared `continuing` —
  # inference outranks declaration in exactly one direction (over-halting is safe;
  # relaunching into a dead credential 52 times is finding #22).
  FA="$EC/fatal-vs-declared"
  mkdir -p "$FA/.auto-pilot"
  (cd "$FA" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf 'exit_reason: continuing\n'
    printf 'exit_reason_at: 9999999999\n'
    printf -- '---\n'
  } >"$FA/.auto-pilot/RUN.md"
  printf '# report\n' >"$FA/.auto-pilot/REPORT.md"
  : >"$FA/launchctl.log"
  STUB_LAUNCHCTL_LOG="$FA/launchctl.log" PATH="$STUB_PATH" \
    "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/auth.log" --wake-start 1 \
    --dir "$FA" --label com.autopilot.ec.fatal --state "$FA/.auto-pilot/supervisor-state" >/dev/null 2>&1
  have "exit contract: a fatal auth exit halts despite a fresh 'continuing' declaration" \
    'status: systemic' "$(cat "$FA/.auto-pilot/RUN.md")"
  if grep -q 'bootout' "$FA/launchctl.log" 2>/dev/null; then
    ok "exit contract: the fatal halt tore the job down (bootout observed)"
  else
    bad "exit contract: the fatal halt tore the job down (bootout observed)" "$(cat "$FA/launchctl.log")"
  fi

  # --- an undatable --wake-start DEGRADES the supervisor; it must never DISABLE it -
  # The wrapper computes `wake=$(date +%s)` under the plist's NARROWED PATH, so a
  # `date` it cannot reach leaves `wake` EMPTY — and `launch.sh` is generated ONCE
  # and persisted, while spawn-orchestrator.sh is updated under a live run, so an
  # in-flight run's wrapper may pass no --wake-start at all. Two things must hold at
  # once, and the second is the one that kills runs:
  #   1. NO declaration is honored (freshness unknowable → fail-closed): a RUN.md
  #      carrying a 1970-vintage `done` must not tear a LIVE run down.
  #   2. EVERY OTHER supervisor duty still runs — classification, the no-progress
  #      counter, the halt, the teardown. A `die` here would make the supervisor
  #      exit before classifying anything, on every wake, forever: claude burns
  #      quota, launchd relaunches, nothing alarms. Finding #22, unkillable.
  WS="$EC/wake-start"
  mkdir -p "$WS/.auto-pilot"
  (cd "$WS" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf 'exit_reason: done\n'
    printf 'exit_reason_at: 1000\n'
    printf -- '---\n'
  } >"$WS/.auto-pilot/RUN.md"
  printf '# report\n' >"$WS/.auto-pilot/REPORT.md"
  printf 'ok\n' >"$WS/log"
  # ws_case <name> [extra supervisor-check args…] — a LIVE run (exit 0, clean log)
  # whose RUN.md carries a STALE terminal declaration. Observed: did `bootout` reach
  # launchctl, and did the supervisor still do its job, rather than die on a usage error?
  ws_case() {
    local name="$1"
    shift
    : >"$WS/launchctl.log"
    rm -f "$WS/.auto-pilot/orchestrator.done"
    local wsc wsout
    wsout="$(STUB_LAUNCHCTL_LOG="$WS/launchctl.log" PATH="$STUB_PATH" \
      "$SCRIPT" supervisor-check --exit-code 0 --log "$WS/log" "$@" \
      --dir "$WS" --label com.autopilot.ec.ws --state "$WS/.auto-pilot/supervisor-state" 2>&1)"
    wsc=$?
    if grep -q 'bootout' "$WS/launchctl.log" 2>/dev/null || [ -f "$WS/.auto-pilot/orchestrator.done" ]; then
      bad "exit contract [$name]: an undatable wake must NOT tear a live run down" \
        "launchctl log: $(cat "$WS/launchctl.log" 2>/dev/null)"
    else
      ok "exit contract [$name]: an undatable wake never honors a stale declaration (no bootout, no sentinel)"
    fi
    # It KEPT SUPERVISING: it reached the inference fallback (the `*)` branch) and
    # decided this wake, rather than dying on a usage error before classifying.
    if [ "$wsc" = 2 ]; then
      bad "exit contract [$name]: the supervisor still SUPERVISES (a bad wake stamp must not disable it)" \
        "exit=2 (died on a usage error before classifying); output: $wsout"
    else
      ok "exit contract [$name]: the supervisor still SUPERVISES (classified this wake; exit=$wsc)"
    fi
    have "exit contract [$name]: the degraded wake stamp is warned about LOUDLY" \
      'WARNING' "$wsout"
  }
  ws_case "wake-start missing"
  ws_case "wake-start empty" --wake-start ''
  ws_case "wake-start non-numeric" --wake-start abc

  # …and the duty that MATTERS: a fatal auth log with NO --wake-start must STILL halt.
  # This is the exact input the reviewer reproduced the regression with — the run
  # that, with a `die` here, relaunched into the same 401 forever with zero alarm.
  WSF="$EC/wake-start-fatal"
  mkdir -p "$WSF/.auto-pilot"
  # The fixture never commits — the HALT PATH does, and the assertions below read
  # through `git show HEAD:`. So a leaked global hook would run against a commit
  # this file never issues: one blocking `main` (git init's default branch) vetoes
  # it, spawn-orchestrator swallows the failure, HEAD is never created, and the
  # assertions fail pointing at the halt logic instead of at the hook. The
  # suite-wide GIT_CONFIG_GLOBAL/SYSTEM pin above is what keeps that hook out.
  (cd "$WSF" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf 'exit_reason: continuing\n'
    printf 'exit_reason_at: 9999999999\n'
    printf -- '---\n'
  } >"$WSF/.auto-pilot/RUN.md"
  printf '# report\n' >"$WSF/.auto-pilot/REPORT.md"
  : >"$WSF/launchctl.log"
  STUB_LAUNCHCTL_LOG="$WSF/launchctl.log" PATH="$STUB_PATH" \
    "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/auth.log" \
    --dir "$WSF" --label com.autopilot.ec.wsf --state "$WSF/.auto-pilot/supervisor-state" >/dev/null 2>&1
  have "exit contract [no wake stamp]: a fatal auth exit STILL halts (status: systemic written)" \
    'status: systemic' "$(git -C "$WSF" show HEAD:.auto-pilot/RUN.md 2>&1)"
  have "exit contract [no wake stamp]: the halt STILL alarms in REPORT.md" \
    'ALARM' "$(git -C "$WSF" show HEAD:.auto-pilot/REPORT.md 2>&1)"
  if grep -q 'bootout' "$WSF/launchctl.log" 2>/dev/null; then
    ok "exit contract [no wake stamp]: the halt STILL tore the job down (bootout observed)"
  else
    bad "exit contract [no wake stamp]: the halt STILL tore the job down (bootout observed)" \
      "launchctl log: $(cat "$WSF/launchctl.log" 2>/dev/null)"
  fi

  # …and the no-progress counter STILL counts across wakes with no wake stamp.
  WSN="$EC/wake-start-noprogress"
  mkdir -p "$WSN/.auto-pilot"
  (cd "$WSN" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'pause_reason: \n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  : >"$WSN/launchctl.log"
  i=0
  while [ "$i" -lt 3 ]; do
    STUB_LAUNCHCTL_LOG="$WSN/launchctl.log" PATH="$STUB_PATH" \
      "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" \
      --dir "$WSN" --label com.autopilot.ec.wsn --state "$WSN/.auto-pilot/supervisor-state" >/dev/null 2>&1
    i=$((i + 1))
  done
  have "exit contract [no wake stamp]: the no-progress guard STILL counts and halts" \
    'status: systemic' "$(git -C "$WSN" show HEAD:.auto-pilot/RUN.md 2>&1)"

  # --- clear-exit-state: --resume's first act ------------------------------------
  # The exit contract is DURABLE (committed reason + the done-sentinel file), and
  # `deadline` is BY DEFINITION the reason a --resume recovers from. Nothing else in
  # the repo clears either, so a resumed — RUNNING — run used to read back as
  # finished forever, which a PathState watcher (and, with a stale declaration, the
  # supervisor itself) would act on.
  CES="$EC/clear-exit-state"
  mkdir -p "$CES/.auto-pilot"
  {
    printf -- '---\n'
    printf 'run_id: ces\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf 'exit_reason: deadline\n'
    printf 'exit_reason_at: 1000\n'
    printf 'exit_reason_detail: the pre-dispatch guard stopped with tasks still ready\n'
    printf -- '---\n'
    printf '\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| T-1  | claimed | b1   | main | -        | -  | -     |\n'
  } >"$CES/.auto-pilot/RUN.md"
  printf 'com.autopilot.ec.ces deadline 1970-01-01T00:00:00Z\nreason: deadline\n' \
    >"$CES/.auto-pilot/orchestrator.done"
  (cd "$CES" && git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  have "clear-exit-state: precondition — the stale terminal state reads back as NO relaunch" \
    'relaunch=no' "$("$SCRIPT" status --label com.autopilot.ec.ces --dir "$CES" 2>&1)"
  "$SCRIPT" clear-exit-state --dir "$CES" >/dev/null 2>&1
  if [ -f "$CES/.auto-pilot/orchestrator.done" ]; then
    bad "clear-exit-state: the done-sentinel is REMOVED (the launchd relaunch gate)"
  else
    ok "clear-exit-state: the done-sentinel is REMOVED (the launchd relaunch gate)"
  fi
  cesafter="$("$SCRIPT" status --label com.autopilot.ec.ces --dir "$CES" 2>&1)"
  have "clear-exit-state: a resumed run reads back as RUNNING (relaunch=yes)" 'relaunch=yes' "$cesafter"
  lack "clear-exit-state: a resumed run is never reported finished" 'STATUS: done' "$cesafter"
  cesbranch="$(git -C "$CES" show HEAD:.auto-pilot/RUN.md 2>&1)"
  lack "clear-exit-state: the stale reason is gone from the run-state BRANCH" 'exit_reason: deadline' "$cesbranch"
  lack "clear-exit-state: the stale detail is gone from the branch too" \
    'the pre-dispatch guard stopped with tasks still ready' "$cesbranch"

  # --- a declared NON-terminal reason removes a pre-existing done-sentinel --------
  # Driven through the REAL wrapper: a run whose earlier life left a `done` sentinel
  # (a torn-down run a human --resume'd) declares `continuing` — a live run must not
  # keep "the run is over" on disk.
  ec_wake resurrect continuing 0
  printf 'com.autopilot.ec.resurrect done 1970-01-01T00:00:00Z\nreason: done\n' \
    >"$EC/resurrect/.auto-pilot/orchestrator.done"
  : >"$EC/resurrect/launchctl.log"
  STUB_LAUNCHCTL_LOG="$EC/resurrect/launchctl.log" STUB_CLAUDE_MARKER="$EC/resurrect/claude-ran" \
    STUB_SELF="$SCRIPT" STUB_RUN_DIR="$EC/resurrect" STUB_DECLARE=continuing STUB_EXIT_CODE=0 \
    bash "$EC/resurrect/launch.sh" >/dev/null 2>&1
  if [ -f "$EC/resurrect/.auto-pilot/orchestrator.done" ]; then
    bad "exit contract [resurrect]: a declared 'continuing' CLEARS the stale done-sentinel"
  else
    ok "exit contract [resurrect]: a declared 'continuing' CLEARS the stale done-sentinel"
  fi
  if grep -q 'bootout' "$EC/resurrect/launchctl.log" 2>/dev/null; then
    bad "exit contract [resurrect]: a stale sentinel never tears the live run down" \
      "$(cat "$EC/resurrect/launchctl.log")"
  else
    ok "exit contract [resurrect]: a stale sentinel never tears the live run down"
  fi

  # --- the done/deadline teardown VERIFIES its bootout ---------------------------
  # `teardown` swallows a failed `launchctl bootout` (`|| true`). Unverified, a
  # FAILED bootout leaves the job LOADED: StartInterval keeps waking a FINISHED run,
  # every wake exits 0 and re-attempts the same failing teardown, `status` says
  # relaunch=no — zero work, zero alarm. Finding #22 by another route. This stub
  # FAILS the bootout and reports the job still loaded via `print`.
  STUBF="$EC/stub-failing-bootout"
  mkdir -p "$STUBF"
  cat >"$STUBF/launchctl" <<'LCFEOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${STUB_LAUNCHCTL_LOG:?}"
case "${1:-}" in
  bootout) exit 1 ;;   # the bootout FAILS…
  print)   exit 0 ;;   # …so the job is still loaded
esac
exit 0
LCFEOF
  chmod +x "$STUBF/launchctl"
  BF="$EC/bootout-fails"
  mkdir -p "$BF/.auto-pilot"
  (cd "$BF" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf 'exit_reason: done\n'
    printf 'exit_reason_at: 9999999999\n'
    printf -- '---\n'
  } >"$BF/.auto-pilot/RUN.md"
  printf '# report\n' >"$BF/.auto-pilot/REPORT.md"
  printf 'ok\n' >"$BF/log"
  : >"$BF/launchctl.log"
  bferr="$(STUB_LAUNCHCTL_LOG="$BF/launchctl.log" PATH="$STUBF:$GUARD:/usr/bin:/bin" \
    "$SCRIPT" supervisor-check --exit-code 0 --wake-start 1 --log "$BF/log" --dir "$BF" \
    --label com.autopilot.ec.bf --state "$BF/.auto-pilot/supervisor-state" 2>&1 >/dev/null)"
  bfboots="$(grep -c 'bootout' "$BF/launchctl.log" 2>/dev/null)"
  bfboots="${bfboots:-0}"
  if [ "$bfboots" -ge 2 ] && grep -q '^print' "$BF/launchctl.log" 2>/dev/null; then
    ok "exit contract [bootout fails]: the declared-done teardown is VERIFIED (launchctl print) and retried"
  else
    bad "exit contract [bootout fails]: the declared-done teardown is VERIFIED (launchctl print) and retried" \
      "launchctl log: $(cat "$BF/launchctl.log" 2>/dev/null)"
  fi
  have "exit contract [bootout fails]: a still-loaded job is reported LOUDLY, never swallowed" \
    'STILL LOADED' "$bferr"

  # --- `die` is `exit`, and an `exit` inside a same-shell function escapes ------
  # `|| true` (task 26 / sweep after #191). `teardown` `die`s if
  # `_write_done_sentinel` fails (an unwritable run dir); `_write_supervisor_state`
  # `die`s the same way. Both sit on the halt/teardown path, right before
  # `_verify_bootout` — the ONE check that turns a still-loaded job into a LOUD
  # warning instead of a silent relaunch loop (finding #22). An unguarded `die`
  # there aborts the whole supervisor process before that warning ever prints.
  #
  # Both fixtures below BREAK the side channel (chmod the run dir read-only, so
  # every mktemp under .auto-pilot/ fails) and assert the halt/teardown still
  # completes: exit 0 (never the bare `die` exit 2), and the STILL-LOADED warning
  # still fires. Driven through the REAL `supervisor-check` entry point — the same
  # one the generated launch script's last line invokes as a separate process.

  # --- the SYSTEMIC HALT path survives a die-capable teardown ---------------
  HB="$EC/halt-unwritable-sentinel"
  mkdir -p "$HB/.auto-pilot"
  (cd "$HB" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf 'exit_reason: systemic\n'
    printf 'exit_reason_at: 9999999999\n'
    printf 'exit_reason_detail: task-26 repro — unwritable sentinel dir\n'
    printf -- '---\n'
  } >"$HB/.auto-pilot/RUN.md"
  printf '# report\n' >"$HB/.auto-pilot/REPORT.md"
  (cd "$HB" && git add -A && git -c user.name=t -c user.email=t@t commit -q -m seed)
  printf 'ok\n' >"$HB/log"
  : >"$HB/launchctl.log"
  # Break the side channel: every mktemp under .auto-pilot/ (the done-sentinel,
  # the RUN.md rewrite, the ALARM sentinel) now fails with EACCES.
  chmod -w "$HB/.auto-pilot"
  hbout="$(STUB_LAUNCHCTL_LOG="$HB/launchctl.log" PATH="$STUBF:$GUARD:/usr/bin:/bin" \
    "$SCRIPT" supervisor-check --exit-code 0 --wake-start 1 --log "$HB/log" --dir "$HB" \
    --label com.autopilot.ec.hb --state "$HB/.auto-pilot/supervisor-state" 2>&1 >/dev/null)"
  hbrc=$?
  chmod +w "$HB/.auto-pilot" # restore: the trap's rm -rf must be able to clean up
  [ "$hbrc" = 0 ] \
    && ok "halt survives unwritable sentinel: supervisor-check exits 0, never the bare teardown die (2)" \
    || bad "halt survives unwritable sentinel: supervisor-check exits 0, never the bare teardown die (2)" "exit=$hbrc"
  have "halt survives unwritable sentinel: _verify_bootout STILL runs and reports the job STILL LOADED" \
    'STILL LOADED' "$hbout"

  # --- the DECLARED-DONE teardown path survives a die-capable state write AND --
  # a die-capable teardown (same shape, different caller — supervisor_check's own
  # done|deadline branch, not the halt).
  DS="$EC/done-unwritable-sentinel"
  mkdir -p "$DS/.auto-pilot"
  (cd "$DS" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf 'exit_reason: done\n'
    printf 'exit_reason_at: 9999999999\n'
    printf -- '---\n'
  } >"$DS/.auto-pilot/RUN.md"
  printf '# report\n' >"$DS/.auto-pilot/REPORT.md"
  (cd "$DS" && git add -A && git -c user.name=t -c user.email=t@t commit -q -m seed)
  printf 'ok\n' >"$DS/log"
  : >"$DS/launchctl.log"
  chmod -w "$DS/.auto-pilot"
  dsout="$(STUB_LAUNCHCTL_LOG="$DS/launchctl.log" PATH="$STUBF:$GUARD:/usr/bin:/bin" \
    "$SCRIPT" supervisor-check --exit-code 0 --wake-start 1 --log "$DS/log" --dir "$DS" \
    --label com.autopilot.ec.ds --state "$DS/.auto-pilot/supervisor-state" 2>&1 >/dev/null)"
  dsrc=$?
  chmod +w "$DS/.auto-pilot"
  [ "$dsrc" = 0 ] \
    && ok "declared-done teardown survives unwritable sentinel: exits 0, never the bare die (2)" \
    || bad "declared-done teardown survives unwritable sentinel: exits 0, never the bare die (2)" "exit=$dsrc"
  have "declared-done teardown survives unwritable sentinel: _verify_bootout STILL runs and reports the job STILL LOADED" \
    'STILL LOADED' "$dsout"

  # --- the NO-PROGRESS halt survives a die-capable bookkeeping write -------------
  # The halt paths above are reached because the run DECLARED an exit reason. This
  # one is the backstop for a run that declares NOTHING and just keeps crashing —
  # and it is the more important of the two, because it is the only thing standing
  # between a wedged run and an infinite relaunch loop.
  #
  # `_write_supervisor_state` (the no-progress COUNTER) `die`s on a write failure,
  # and it runs immediately BEFORE the halt. Unguarded, an unwritable run dir means
  # every wake exits 2 at `mktemp failed` before the halt is ever evaluated: no
  # halt, no alarm, no teardown, job still loaded, StartInterval relaunching
  # forever. Zero work, zero alarm — finding #22's loop, reached THROUGH the guard
  # that exists to backstop it. Same shape the declared-done branch above already
  # fixed for the same function; this is the call site that was missed.
  NP26="$EC/noprogress-unwritable"
  mkdir -p "$NP26/.auto-pilot"
  (cd "$NP26" && git init -q)
  # status active, and NO exit_reason: an agent that crashed without declaring.
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf -- '---\n'
  } >"$NP26/.auto-pilot/RUN.md"
  printf '# report\n' >"$NP26/.auto-pilot/REPORT.md"
  (cd "$NP26" && git add -A && git -c user.name=t -c user.email=t@t commit -q -m seed)
  printf 'crash\n' >"$NP26/log"
  : >"$NP26/launchctl.log"
  chmod -w "$NP26/.auto-pilot"
  # BOTH streams: the halt's own line and the STILL-LOADED warning go to stderr, but
  # the `ALARM no-progress` announcement goes to stdout — an stderr-only capture
  # would silently miss the very signal this test exists to prove.
  npout="$(STUB_LAUNCHCTL_LOG="$NP26/launchctl.log" PATH="$STUBF:$GUARD:/usr/bin:/bin" \
    "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$NP26/log" --dir "$NP26" \
    --label com.autopilot.ec.np26 --no-progress-limit 1 \
    --state "$NP26/.auto-pilot/supervisor-state" 2>&1)"
  nprc=$?
  chmod +w "$NP26/.auto-pilot"
  [ "$nprc" != 2 ] \
    && ok "no-progress halt survives unwritable run dir: never the bare _write_supervisor_state die (2)" \
    || bad "no-progress halt survives unwritable run dir: never the bare _write_supervisor_state die (2)" "exit=$nprc (died at 'mktemp failed' before the halt)"
  have "no-progress halt survives unwritable run dir: the halt STILL fires" \
    'supervisor halt' "$npout"
  have "no-progress halt survives unwritable run dir: the ALARM is STILL raised" \
    'ALARM no-progress' "$npout"
  have "no-progress halt survives unwritable run dir: _verify_bootout STILL reports the job STILL LOADED" \
    'STILL LOADED' "$npout"

  # --- regression guard: the specific subshells stay in place -------------------
  # A grep, not a functional re-run: pins the EXACT fix shape (subshelled `die`
  # is `exit`" callers, task 26) so a future edit that unwraps one of these three
  # calls back to a bare `fn ... || true` fails FAST, before anyone has to
  # rediscover the unwritable-sentinel repro above to explain a red suite.
  guardbody="$(cat "$SCRIPT")"
  have "regression guard: _verify_bootout subshells its internal teardown call" \
    '(teardown --label "$label" >/dev/null) || true' "$guardbody"
  have "regression guard: _supervisor_halt subshells its teardown call" \
    'if ! (teardown --label "$label" --done-sentinel "$dir/.auto-pilot/$DONE_SENTINEL_NAME" --reason systemic >/dev/null); then' \
    "$guardbody"
  have "regression guard: supervisor-check's declared-done/deadline branch subshells _write_supervisor_state" \
    '(_write_supervisor_state "$state" 0 "$(_run_head "$dir")") \' "$guardbody"
  have "regression guard: supervisor-check's declared-done/deadline branch subshells teardown" \
    'if ! (teardown --label "$label" --done-sentinel "$dir/.auto-pilot/$DONE_SENTINEL_NAME" --reason "$declared" >/dev/null); then' \
    "$guardbody"
  have "regression guard: the no-progress guard subshells its supervisor-state write" \
    '(_write_supervisor_state "$state" "$count" "$head") \' "$guardbody"
  have "regression guard: doctor's invariant-7 guard subshells its supervisor-state write" \
    '(_write_supervisor_state "$dstate" "$count" "$head") \' "$guardbody"

  # --- a declared `systemic` PRESERVES the orchestrator's own diagnosis -----------
  # The supervisor used to pass a fixed string ("…see RUN.md pause_reason…") which
  # the halt wrote INTO pause_reason — so the human woke to an alarm pointing at
  # itself, with the concrete cause destroyed. `exit_reason_detail` exists for this.
  # The two fields carry DIFFERENT text on purpose: with the same string in both,
  # the assertion cannot tell "read exit_reason_detail" from "silently fell back to
  # pause_reason", and passes even when the detail read is broken.
  SY="$EC/systemic-detail"
  mkdir -p "$SY/.auto-pilot"
  (cd "$SY" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: circuit breaker: T-2 failed verify 3x on the same assertion\n'
    printf 'exit_reason: systemic\n'
    printf 'exit_reason_at: 9999999999\n'
    printf 'exit_reason_detail: failed invariant: base_sha moved under T-4 mid-delivery\n'
    printf -- '---\n'
  } >"$SY/.auto-pilot/RUN.md"
  printf '# report\n' >"$SY/.auto-pilot/REPORT.md"
  printf 'ok\n' >"$SY/log"
  : >"$SY/launchctl.log"
  STUB_LAUNCHCTL_LOG="$SY/launchctl.log" PATH="$STUB_PATH" \
    "$SCRIPT" supervisor-check --exit-code 0 --wake-start 1 --log "$SY/log" --dir "$SY" \
    --label com.autopilot.ec.sy --state "$SY/.auto-pilot/supervisor-state" >/dev/null 2>&1
  have "exit contract [systemic]: the orchestrator's OWN pause_reason survives the halt" \
    'pause_reason: circuit breaker: T-2 failed verify 3x on the same assertion' \
    "$(cat "$SY/.auto-pilot/RUN.md")"
  have "exit contract [systemic]: the ALARM carries exit_reason_detail's concrete cause" \
    'failed invariant: base_sha moved under T-4 mid-delivery' "$(cat "$SY/.auto-pilot/REPORT.md")"
  lack "exit contract [systemic]: the alarm did NOT silently fall back to pause_reason" \
    'circuit breaker: T-2 failed verify 3x' "$(cat "$SY/.auto-pilot/REPORT.md")"
  lack "exit contract [systemic]: the alarm never just points back at pause_reason" \
    'see RUN.md pause_reason' "$(cat "$SY/.auto-pilot/REPORT.md")"

  # …and with NO detail recorded, the fallback to the already-recorded pause_reason
  # still carries a concrete cause into the alarm (the fallback must exist, but it
  # must be a FALLBACK — the assertion above proves it isn't the only path taken).
  SYF="$EC/systemic-detail-fallback"
  mkdir -p "$SYF/.auto-pilot"
  (cd "$SYF" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: circuit breaker: T-9 failed verify 3x on the same assertion\n'
    printf 'exit_reason: systemic\n'
    printf 'exit_reason_at: 9999999999\n'
    printf 'exit_reason_detail: \n'
    printf -- '---\n'
  } >"$SYF/.auto-pilot/RUN.md"
  printf '# report\n' >"$SYF/.auto-pilot/REPORT.md"
  printf 'ok\n' >"$SYF/log"
  : >"$SYF/launchctl.log"
  STUB_LAUNCHCTL_LOG="$SYF/launchctl.log" PATH="$STUB_PATH" \
    "$SCRIPT" supervisor-check --exit-code 0 --wake-start 1 --log "$SYF/log" --dir "$SYF" \
    --label com.autopilot.ec.syf --state "$SYF/.auto-pilot/supervisor-state" >/dev/null 2>&1
  have "exit contract [systemic]: with no detail, the alarm falls back to pause_reason" \
    'circuit breaker: T-9 failed verify 3x' "$(cat "$SYF/.auto-pilot/REPORT.md")"

  # …and the TEMPLATE's inline doc comment is NOT a diagnosis. run-state.md declares
  # `pause_reason: # why the run paused/halted…`, and the supervisor's front-matter
  # reader deliberately does not strip `#` (these fields are free prose). A run that
  # never wrote a real pause_reason must not have that comment preserved as the halt's
  # cause, nor read back to the human as the alarm's concrete diagnosis.
  SYC="$EC/systemic-template-comment"
  mkdir -p "$SYC/.auto-pilot"
  (cd "$SYC" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: # why the run paused/halted; set with status=paused (rate window) or status=systemic (circuit breaker)\n'
    printf 'exit_reason: systemic\n'
    printf 'exit_reason_at: 9999999999\n'
    printf 'exit_reason_detail: \n'
    printf -- '---\n'
  } >"$SYC/.auto-pilot/RUN.md"
  printf '# report\n' >"$SYC/.auto-pilot/REPORT.md"
  printf 'ok\n' >"$SYC/log"
  : >"$SYC/launchctl.log"
  STUB_LAUNCHCTL_LOG="$SYC/launchctl.log" PATH="$STUB_PATH" \
    "$SCRIPT" supervisor-check --exit-code 0 --wake-start 1 --log "$SYC/log" --dir "$SYC" \
    --label com.autopilot.ec.syc --state "$SYC/.auto-pilot/supervisor-state" >/dev/null 2>&1
  lack "exit contract [systemic]: the template's doc comment is never preserved as pause_reason" \
    'why the run paused/halted' "$(cat "$SYC/.auto-pilot/RUN.md")"
  have "exit contract [systemic]: with only the template comment, pause_reason gets the halt's real reason" \
    'pause_reason: the orchestrator declared a systemic exit' "$(cat "$SYC/.auto-pilot/RUN.md")"
  lack "exit contract [systemic]: the alarm never quotes the template comment as the cause" \
    'why the run paused/halted' "$(cat "$SYC/.auto-pilot/REPORT.md")"

  # --- a HALT'S OWN reason is the TRUE one: never a stale pause_reason -------------
  # The "preserve the orchestrator's diagnosis" rule belongs to the declared-`systemic`
  # halt ALONE. The fatal-auth and no-progress halts have their own, true reason, and
  # `pause_reason` is durable — an earlier rate-window pause leaves one behind, and
  # `--resume` clears `status`/`paused_until` but NOT `pause_reason`. Preserving it on
  # those paths makes RUN.md assert a FALSE cause ("halted: rate window until 03:00"
  # on a run that actually died on a dead credential) and sends the operator to debug
  # the wrong thing — the same "looks like an explanation, is a lie" failure mode this
  # whole task exists to abolish.
  SP="$EC/stale-pause-reason-fatal"
  mkdir -p "$SP/.auto-pilot"
  # Same as WSF above: the halt path issues the commit these assertions read via
  # `git show HEAD:`, so a leaked hook must not reach it — the suite-wide
  # GIT_CONFIG_GLOBAL/SYSTEM pin is what guarantees that.
  (cd "$SP" && git init -q)
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: rate window until 03:00 (from an earlier pause, since resumed)\n'
    printf 'paused_until: \n'
    printf -- '---\n'
  } >"$SP/.auto-pilot/RUN.md"
  printf '# report\n' >"$SP/.auto-pilot/REPORT.md"
  : >"$SP/launchctl.log"
  STUB_LAUNCHCTL_LOG="$SP/launchctl.log" PATH="$STUB_PATH" \
    "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/auth.log" --dir "$SP" \
    --label com.autopilot.ec.sp --state "$SP/.auto-pilot/supervisor-state" >/dev/null 2>&1
  sprun="$(git -C "$SP" show HEAD:.auto-pilot/RUN.md 2>&1)"
  have "exit contract [fatal auth]: the halt records its OWN true reason" \
    'pause_reason: non-retryable auth failure' "$sprun"
  lack "exit contract [fatal auth]: a stale pause_reason is NOT preserved as the cause" \
    'rate window until 03:00' "$sprun"

  # …same for the no-progress halt.
  NP="$EC/stale-pause-reason-noprogress"
  mkdir -p "$NP/.auto-pilot"
  (cd "$NP" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'pause_reason: rate window until 03:00 (from an earlier pause, since resumed)\n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  : >"$NP/launchctl.log"
  i=0
  while [ "$i" -lt 3 ]; do
    STUB_LAUNCHCTL_LOG="$NP/launchctl.log" PATH="$STUB_PATH" \
      "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$NP" \
      --label com.autopilot.ec.np2 --state "$NP/.auto-pilot/supervisor-state" >/dev/null 2>&1
    i=$((i + 1))
  done
  nprun="$(git -C "$NP" show HEAD:.auto-pilot/RUN.md 2>&1)"
  have "exit contract [no-progress]: the halt records its OWN true reason" \
    'pause_reason: no forward progress' "$nprun"
  lack "exit contract [no-progress]: a stale pause_reason is NOT preserved as the cause" \
    'rate window until 03:00' "$nprun"

  # --- a declared `paused` needs CORROBORATION to skip the no-progress guard ------
  # Resetting the counter on the declaration alone lets a prompt/logic bug that
  # declares `paused` on every wake while dying non-zero and making no run-state
  # progress relaunch forever: the backstop can never fire, and nothing alarms.
  PB="$EC/paused-uncorroborated"
  mkdir -p "$PB/.auto-pilot"
  (cd "$PB" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'pause_reason: \n'
      printf 'exit_reason: paused\n'
      printf 'exit_reason_at: 9999999999\n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  : >"$PB/launchctl.log"
  i=0
  while [ "$i" -lt 3 ]; do
    STUB_LAUNCHCTL_LOG="$PB/launchctl.log" PATH="$STUB_PATH" \
      "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$PB" \
      --label com.autopilot.ec.pb --state "$PB/.auto-pilot/supervisor-state" >/dev/null 2>&1
    i=$((i + 1))
  done
  have "exit contract [paused, uncorroborated]: still counts against the no-progress guard (halts)" \
    'status: systemic' "$(cat "$PB/.auto-pilot/RUN.md")"
  have "exit contract [paused, uncorroborated]: the halt raises the no-progress alarm" \
    'no forward progress' "$(cat "$PB/.auto-pilot/REPORT.md")"

  # …while a REAL pause (RUN.md's own `status: paused` + `paused_until`) stays exempt.
  PC="$EC/paused-corroborated"
  mkdir -p "$PC/.auto-pilot"
  (cd "$PC" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: paused\n'
      printf 'paused_until: 2099-01-01T00:00:00\n'
      printf 'pause_reason: rate window\n'
      printf 'exit_reason: paused\n'
      printf 'exit_reason_at: 9999999999\n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  : >"$PC/launchctl.log"
  i=0
  while [ "$i" -lt 5 ]; do
    STUB_LAUNCHCTL_LOG="$PC/launchctl.log" PATH="$STUB_PATH" \
      "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$PC" \
      --label com.autopilot.ec.pc --state "$PC/.auto-pilot/supervisor-state" >/dev/null 2>&1
    i=$((i + 1))
  done
  lack "exit contract [paused, corroborated]: a real rate-window pause is still exempt from the guard" \
    'status: systemic' "$(cat "$PC/.auto-pilot/RUN.md")"

  # …and the corroboration is `status: paused` and NOTHING ELSE. `paused_until` is
  # unusable as corroboration two ways, and the RUN.md this uses is copied verbatim
  # from run-state.md's own template: (a) the template declares the key WITH an
  # inline `# comment`, and the supervisor's front-matter reader deliberately does
  # not strip `#` (pause_reason / exit_reason_detail are free prose), so the comment
  # text reads back as a non-empty VALUE and every run "corroborates" — the guard
  # could never fire; (b) it is durable across a `--resume`, so a run that paused
  # once would exempt itself from the guard forever. Declaring `paused` on every
  # wake while dying non-zero must STILL reach the halt.
  PT="$EC/paused-until-comment"
  mkdir -p "$PT/.auto-pilot"
  (cd "$PT" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'paused_until: # ISO time the orchestrator may resume past a rate-window pause; empty unless status is paused\n'
      printf 'pause_reason: # why the run paused/halted\n'
      printf 'exit_reason: paused\n'
      printf 'exit_reason_at: 9999999999\n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  : >"$PT/launchctl.log"
  i=0
  while [ "$i" -lt 4 ]; do
    STUB_LAUNCHCTL_LOG="$PT/launchctl.log" PATH="$STUB_PATH" \
      "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$PT" \
      --label com.autopilot.ec.pt --state "$PT/.auto-pilot/supervisor-state" >/dev/null 2>&1
    i=$((i + 1))
  done
  ptrun="$(git -C "$PT" show HEAD:.auto-pilot/RUN.md 2>&1)"
  have "exit contract [paused, template's commented paused_until]: an EMPTY paused_until does not corroborate — the no-progress halt still fires" \
    'status: systemic' "$ptrun"
  have "exit contract [paused, template's commented paused_until]: the halt raises the no-progress alarm" \
    'no forward progress' "$(git -C "$PT" show HEAD:.auto-pilot/REPORT.md 2>&1)"
  if grep -q 'bootout' "$PT/launchctl.log" 2>/dev/null; then
    ok "exit contract [paused, template's commented paused_until]: the job was booted out"
  else
    bad "exit contract [paused, template's commented paused_until]: the job was booted out" \
      "launchctl log: $(cat "$PT/launchctl.log" 2>/dev/null)"
  fi

  # --- task 23: the pause exemption needs an authority the agent cannot forge ---
  # (dev_docs/tasks/autopilot_hardening_plan/autopilot_hardening_task_23.md).
  # `status: paused` ALONE used to exempt the no-progress guard — corroborated
  # only by a second field (or nothing at all) the same agent writes. These
  # drive the REAL generated wrapper end to end (never supervisor-check
  # directly) precisely because a prior alarm test skipped supervisor-gate by
  # re-implementing the call sequence and missed the bug it existed to catch.
  #
  # exit_reason/exit_reason_at are committed ONCE, up front, with an
  # exit_reason_at far in the future (9999999999, same idiom as the
  # [paused, corroborated] test above) so every real wake's `--wake-start`
  # attributes the SAME declaration without the stub `claude` re-declaring (and
  # re-committing, which would move the run-state HEAD every wake and mask the
  # very no-progress condition under test).
  t23_setup() { # <name> <status> <paused_until> -> leaves $T23_DIR, writes+commits RUN.md, real write-launch.
    T23_DIR="$EC/t23-$1"
    mkdir -p "$T23_DIR/.auto-pilot"
    {
      printf -- '---\n'
      printf 'run_id: t23-%s\n' "$1"
      printf 'status: %s\n' "$2"
      printf 'paused_until: %s\n' "$3"
      printf 'pause_reason: rate window\n'
      printf 'exit_reason: paused\n'
      printf 'exit_reason_at: 9999999999\n'
      printf -- '---\n\n'
      printf '| task | phase | branch | base | base_sha | pr | notes |\n'
      printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    } >"$T23_DIR/.auto-pilot/RUN.md"
    printf '# report\n' >"$T23_DIR/.auto-pilot/REPORT.md"
    : >"$T23_DIR/.auto-pilot/orchestrator.log"
    (cd "$T23_DIR" && git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
    "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" \
      --workdir "$T23_DIR" --log "$T23_DIR/.auto-pilot/orchestrator.log" \
      --prompt-file "$BASE/prompt.txt" --label "com.autopilot.t23.$1" \
      --claude-bin "$STUB/claude" --path "$STUB_PATH" \
      --out-script "$T23_DIR/launch.sh" --out-plist "$T23_DIR/job.plist" >/dev/null 2>&1
    : >"$T23_DIR/launchctl.log"
    rm -f "$T23_DIR/claude-ran"
  }
  t23_wake() { # one wake through the REAL generated wrapper (no STUB_DECLARE: see above)
    STUB_LAUNCHCTL_LOG="$T23_DIR/launchctl.log" STUB_CLAUDE_MARKER="$T23_DIR/claude-ran" \
      STUB_EXIT_CODE=0 bash "$T23_DIR/launch.sh" >/dev/null 2>&1
  }

  # (23a) A wedged agent that writes `status: paused` on EVERY wake with NO
  # `paused_until` — the failure this task exists to catch. Must NOT be exempt:
  # 3 (the default --no-progress-limit) consecutive wakes trip the guard.
  t23_setup wedge paused ''
  i=0
  while [ "$i" -lt 3 ]; do
    t23_wake
    i=$((i + 1))
  done
  have "task 23 [status: paused, no paused_until]: the no-progress guard trips and halts" \
    'status: systemic' "$(cat "$T23_DIR/.auto-pilot/RUN.md")"
  have "task 23 [status: paused, no paused_until]: the halt raises the no-progress alarm" \
    'no forward progress' "$(cat "$T23_DIR/.auto-pilot/REPORT.md")"
  have "task 23 [status: paused, no paused_until]: the job is torn down" \
    'bootout' "$(cat "$T23_DIR/launchctl.log")"
  [ -f "$T23_DIR/claude-ran" ] && ok "task 23 [status: paused, no paused_until]: the agent WAS invoked each wake (gate stays open on an empty paused_until)" \
    || bad "task 23 [status: paused, no paused_until]: the agent WAS invoked each wake"

  # (23b) A genuine rate-window pause — `status: paused` + a parseable FUTURE
  # `paused_until` — stays exempt (task 11 must not regress): the gate closes
  # every wake and the agent is never even invoked, let alone halted.
  T23_FUTURE="$(_gate_iso $((NOW_EPOCH + 3600)))"
  t23_setup future paused "$T23_FUTURE"
  i=0
  while [ "$i" -lt 5 ]; do
    t23_wake
    i=$((i + 1))
  done
  lack "task 23 [genuine pause, future paused_until]: the guard never halts while the window is open" \
    'status: systemic' "$(cat "$T23_DIR/.auto-pilot/RUN.md")"
  lack "task 23 [genuine pause, future paused_until]: the job is never torn down" \
    'bootout' "$(cat "$T23_DIR/launchctl.log")"
  [ -f "$T23_DIR/claude-ran" ] && bad "task 23 [genuine pause, future paused_until]: the agent is NOT invoked (gate stays closed)" \
    || ok "task 23 [genuine pause, future paused_until]: the agent is NOT invoked (gate stays closed)"

  # (23c) …and relanches PAST the reset: `paused_until` just behind us (well
  # within the margin) opens the gate again, and the guard still doesn't trip —
  # the task-11 behavior this fix must not regress.
  T23_JUSTPAST="$(_gate_iso $((NOW_EPOCH - 10)))"
  t23_setup pastreset paused "$T23_JUSTPAST"
  i=0
  while [ "$i" -lt 5 ]; do
    t23_wake
    i=$((i + 1))
  done
  lack "task 23 [relaunch past the reset]: the guard does not halt just past its own paused_until" \
    'status: systemic' "$(cat "$T23_DIR/.auto-pilot/RUN.md")"
  [ -f "$T23_DIR/claude-ran" ] && ok "task 23 [relaunch past the reset]: the agent IS invoked once the gate reopens" \
    || bad "task 23 [relaunch past the reset]: the agent IS invoked once the gate reopens"

  # (23d) A pause that overran its own `paused_until` by more than the margin
  # (PAUSE_EXEMPT_MARGIN_SECONDS_DEFAULT, 1h) re-arms the guard: 3 more wakes
  # (still declaring the same stale `paused`) halt.
  T23_STALE="$(_gate_iso $((NOW_EPOCH - 3600 - 120)))"
  t23_setup stale paused "$T23_STALE"
  i=0
  while [ "$i" -lt 3 ]; do
    t23_wake
    i=$((i + 1))
  done
  have "task 23 [pause overran its margin]: the guard re-arms and halts" \
    'status: systemic' "$(cat "$T23_DIR/.auto-pilot/RUN.md")"
  have "task 23 [pause overran its margin]: the job is torn down" \
    'bootout' "$(cat "$T23_DIR/launchctl.log")"

  # --- the pause-exempt LEDGER: bounds the guard's exemption regardless of what --
  # RUN.md claims (the finding this task fixes: `status`/`paused_until` are BOTH
  # agent-written, so `_pause_exempt` corroborating one against the other proves
  # nothing about a forging agent — see the false-comment fix in supervisor_check's
  # `paused` branch). These drive the REAL generated wrapper end-to-end, same
  # posture as task 23 above, with a small --pause-exempt-max so the cap trips in
  # a couple of wakes instead of after 6 real hours — seeding `exempt_since`
  # directly (never sleeping) is what makes that deterministic.
  pel_setup() { # <name> <pause_exempt_max> -> leaves $PEL_DIR, real write-launch with the cap.
    PEL_DIR="$EC/pel-$1"
    mkdir -p "$PEL_DIR/.auto-pilot"
    pel_write_runmd active ''
    printf '# report\n' >"$PEL_DIR/.auto-pilot/REPORT.md"
    : >"$PEL_DIR/.auto-pilot/orchestrator.log"
    (cd "$PEL_DIR" && git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
    "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" \
      --workdir "$PEL_DIR" --log "$PEL_DIR/.auto-pilot/orchestrator.log" \
      --prompt-file "$BASE/prompt.txt" --label "com.autopilot.pel.$1" \
      --claude-bin "$STUB/claude" --path "$STUB_PATH" --pause-exempt-max "$2" \
      --out-script "$PEL_DIR/launch.sh" --out-plist "$PEL_DIR/job.plist" >/dev/null 2>&1
    : >"$PEL_DIR/launchctl.log"
    rm -f "$PEL_DIR/claude-ran"
  }
  pel_write_runmd() { # <status> <paused_until> -> (re)writes $PEL_DIR/.auto-pilot/RUN.md
    {
      printf -- '---\n'
      printf 'status: %s\n' "$1"
      printf 'paused_until: %s\n' "$2"
      printf 'pause_reason: \n'
      printf -- '---\n\n'
      printf '| task | phase | branch | base | base_sha | pr | notes |\n'
      printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    } >"$PEL_DIR/.auto-pilot/RUN.md"
  }
  pel_wake() { # one wake through the REAL generated wrapper.
    STUB_LAUNCHCTL_LOG="$PEL_DIR/launchctl.log" STUB_CLAUDE_MARKER="$PEL_DIR/claude-ran" \
      STUB_EXIT_CODE=0 bash "$PEL_DIR/launch.sh" >/dev/null 2>&1
  }
  pel_state() { cat "$PEL_DIR/.auto-pilot/supervisor-state" 2>/dev/null; }
  pel_exempt_since() { sed -n 's/^exempt_since: *//p' "$PEL_DIR/.auto-pilot/supervisor-state" 2>/dev/null | tail -1; }
  pel_seed_exempt_since() { # <old-epoch> -> overwrite the ledger directly (never sleeping).
    {
      printf 'count: 0\n'
      printf 'head: \n'
      printf 'exempt_since: %s\n' "$1"
    } >"$PEL_DIR/.auto-pilot/supervisor-state"
  }

  # (A) FORGED REFRESH: the agent rewrites `paused_until` to a few seconds ago on
  # EVERY wake, forever — the shape reproduced in the bug report (6 consecutive
  # zero-progress wakes, no-progress counter pinned at 0, no halt, no alarm).
  # `_pause_exempt` corroborates it every time (a few seconds ago is well within
  # the 1h margin), so nothing but the ledger's own cumulative cap can stop it.
  pel_setup forged 60
  pel_write_runmd paused "$(_gate_iso $(($(date +%s) - 5)))"
  pel_wake
  lack "pause-exempt ledger [forged refresh]: does not halt before the cap" \
    'status: systemic' "$(cat "$PEL_DIR/.auto-pilot/RUN.md")"
  [ -n "$(pel_exempt_since)" ] \
    && ok "pause-exempt ledger [forged refresh]: the ledger started a streak (exempt_since set)" \
    || bad "pause-exempt ledger [forged refresh]: the ledger started a streak (exempt_since set)" "$(pel_state)"
  # Seed the streak's start well past the 60s cap, then refresh `paused_until`
  # again (the forging agent, on its next wake) and wake once more.
  pel_seed_exempt_since "$(($(date +%s) - 120))"
  pel_write_runmd paused "$(_gate_iso $(($(date +%s) - 5)))"
  pel_wake
  have "pause-exempt ledger [forged refresh]: the cap trips and halts" \
    'status: systemic' "$(cat "$PEL_DIR/.auto-pilot/RUN.md")"
  have "pause-exempt ledger [forged refresh]: the halt raises the pause-exempt alarm" \
    'pause-exempt' "$(cat "$PEL_DIR/.auto-pilot/REPORT.md")"
  have "pause-exempt ledger [forged refresh]: REPORT.md's alarm line says a declared pause is not evidence" \
    'not evidence' "$(cat "$PEL_DIR/.auto-pilot/REPORT.md")"
  have "pause-exempt ledger [forged refresh]: the job is torn down" \
    'bootout' "$(cat "$PEL_DIR/launchctl.log")"

  # (B) FAR-FUTURE paused_until: closes supervisor-gate on EVERY wake, so
  # supervisor-check never runs at all — this is the test that proves enforcement
  # sits ABOVE the gate (in supervisor-scan), because nothing below the gate ever
  # gets a chance to run.
  pel_setup farfuture 60
  pel_write_runmd paused '2099-01-01T00:00:00Z'
  pel_wake
  [ ! -f "$PEL_DIR/claude-ran" ] \
    && ok "pause-exempt ledger [far-future paused_until]: the gate closes, claude is never invoked" \
    || bad "pause-exempt ledger [far-future paused_until]: the gate closes, claude is never invoked"
  lack "pause-exempt ledger [far-future paused_until]: does not halt before the cap" \
    'status: systemic' "$(cat "$PEL_DIR/.auto-pilot/RUN.md")"
  pel_seed_exempt_since "$(($(date +%s) - 120))"
  pel_wake
  have "pause-exempt ledger [far-future paused_until]: the cap STILL trips and halts (enforcement is above the gate)" \
    'status: systemic' "$(cat "$PEL_DIR/.auto-pilot/RUN.md")"
  have "pause-exempt ledger [far-future paused_until]: the job is torn down" \
    'bootout' "$(cat "$PEL_DIR/launchctl.log")"
  [ ! -f "$PEL_DIR/claude-ran" ] \
    && ok "pause-exempt ledger [far-future paused_until]: claude was NEVER invoked, the whole way through" \
    || bad "pause-exempt ledger [far-future paused_until]: claude was NEVER invoked, the whole way through"

  # (C) LEGITIMATE pause, well under the cap: must NOT regress task 11/23 — the
  # gate stays closed while the window is open, and relaunch past the reset
  # still invokes the agent, with no halt anywhere in the sequence.
  pel_setup legit 3600
  T_PEL_FUTURE="$(_gate_iso $(($(date +%s) + 3600)))"
  pel_write_runmd paused "$T_PEL_FUTURE"
  i=0
  while [ "$i" -lt 3 ]; do
    pel_wake
    i=$((i + 1))
  done
  lack "pause-exempt ledger [legitimate pause, under the cap]: no halt while the window is open" \
    'status: systemic' "$(cat "$PEL_DIR/.auto-pilot/RUN.md")"
  [ ! -f "$PEL_DIR/claude-ran" ] \
    && ok "pause-exempt ledger [legitimate pause, under the cap]: claude is not invoked while gated" \
    || bad "pause-exempt ledger [legitimate pause, under the cap]: claude is not invoked while gated"
  [ -n "$(pel_exempt_since)" ] \
    && ok "pause-exempt ledger [legitimate pause, under the cap]: the ledger still tracks the streak" \
    || bad "pause-exempt ledger [legitimate pause, under the cap]: the ledger still tracks the streak"
  T_PEL_PAST="$(_gate_iso $(($(date +%s) - 10)))"
  pel_write_runmd paused "$T_PEL_PAST"
  pel_wake
  [ -f "$PEL_DIR/claude-ran" ] \
    && ok "pause-exempt ledger [relaunch past the reset]: claude IS invoked once the gate reopens" \
    || bad "pause-exempt ledger [relaunch past the reset]: claude IS invoked once the gate reopens"
  lack "pause-exempt ledger [relaunch past the reset]: still no halt (well under the cap)" \
    'status: systemic' "$(cat "$PEL_DIR/.auto-pilot/RUN.md")"

  # (D) The ledger CLEARS when the run is observed not pause-exempt — a run that
  # was genuinely paused and then resumed must not carry a stale streak into an
  # unrelated future pause.
  pel_setup clears 3600
  pel_write_runmd paused "$(_gate_iso $(($(date +%s) - 10)))"
  pel_wake
  [ -n "$(pel_exempt_since)" ] \
    && ok "pause-exempt ledger [clears]: a pause-exempt wake starts the streak" \
    || bad "pause-exempt ledger [clears]: a pause-exempt wake starts the streak" "$(pel_state)"
  pel_write_runmd active ''
  pel_wake
  [ -z "$(pel_exempt_since)" ] \
    && ok "pause-exempt ledger [clears]: a non-exempt wake clears exempt_since" \
    || bad "pause-exempt ledger [clears]: a non-exempt wake clears exempt_since" "$(pel_state)"

  # fail-closed: an unknown reason is never written, and a RELAUNCHABLE reason can
  # never be smuggled into the terminal sentinel.
  o="$("$SCRIPT" exit-reason --dir "$EC/continuing" --reason bogus 2>&1)"
  ecc=$?
  [ "$ecc" = 2 ] && printf '%s' "$o" | grep -qF 'unknown exit reason' \
    && ok "exit-reason fail-closed: unknown reason" || bad "exit-reason fail-closed: unknown reason" "$o"
  o="$("$SCRIPT" teardown --label com.autopilot.ec.x --reason continuing 2>&1)"
  tdc=$?
  [ "$tdc" = 2 ] && printf '%s' "$o" | grep -qF 'must be a TERMINAL exit reason' \
    && ok "teardown fail-closed: a relaunchable reason can't mark a run terminal" \
    || bad "teardown fail-closed: a relaunchable reason can't mark a run terminal" "$o"
else
  echo "skip - exit contract: git not available"
fi

# --- heartbeat: stale (wedged) vs fresh (working) ------------------------------
# "Last heartbeat 40 min ago, per-task ceiling is 45m" is the distinction NO other
# signal in the system can make: a slow task and a hung one look identical to an
# exit code, a PID, and a log tail alike.
HB="$BASE/hb"
mkdir -p "$HB/.auto-pilot"
{
  printf -- '---\n'
  printf 'status: active\n'
  printf -- '---\n'
  printf '| task | phase | branch | base | base_sha | pr | notes |\n'
  printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
  printf '| T-1  | implementing | b1 | main | - | - | - |\n'
} >"$HB/.auto-pilot/RUN.md"

"$SCRIPT" heartbeat --dir "$HB" --note 'deliver-task:implement' >/dev/null 2>&1
hbf="$("$SCRIPT" status --label com.autopilot.hb --dir "$HB" --task-ceiling 2700 2>&1)"
have "heartbeat: a fresh beat is reported healthy" 'healthy' "$hbf"
have "heartbeat: STATUS line carries heartbeat=healthy" 'heartbeat=healthy' "$hbf"
lack "heartbeat: a fresh beat is not a stall" 'STALL' "$hbf"
have "heartbeat: the beat's sub-step note is surfaced" 'deliver-task:implement' "$hbf"

# Backdate the beat past the 45m per-task ceiling (50m ago) — the wedged case. It
# has to be backdated by hand; the alternative is a 45-minute test.
{
  printf 'at: %s\n' "$(($(date +%s) - 3000))"
  printf 'iso: 2026-07-11T00:00:00Z\n'
  printf 'note: deliver-task:implement\n'
} >"$HB/.auto-pilot/heartbeat"
hbs="$("$SCRIPT" status --label com.autopilot.hb --dir "$HB" --task-ceiling 2700 2>&1)"
have "heartbeat: a beat older than the per-task ceiling is reported as a STALL" 'STALL' "$hbs"
have "heartbeat: STATUS line carries heartbeat=stale" 'heartbeat=stale' "$hbs"
lack "heartbeat: a stalled run is not reported healthy" 'heartbeat=healthy' "$hbs"

# …and a fresh beat (through the real subcommand) clears it: the stall report
# tracks the beat, not some sticky flag.
"$SCRIPT" heartbeat --dir "$HB" --note 'loop-iteration' >/dev/null 2>&1
have "heartbeat: a new beat clears the stall" 'heartbeat=healthy' \
  "$("$SCRIPT" status --label com.autopilot.hb --dir "$HB" --task-ceiling 2700 2>&1)"

# a run with no heartbeat at all (a pre-heartbeat run) reports none, never a false stall
NOHB="$BASE/nohb"
mkdir -p "$NOHB/.auto-pilot"
{
  printf -- '---\n'
  printf 'status: active\n'
  printf -- '---\n'
} >"$NOHB/.auto-pilot/RUN.md"
have "heartbeat: absent heartbeat reports none (not a false stall)" 'heartbeat=none' \
  "$("$SCRIPT" status --label com.autopilot.nohb --dir "$NOHB" 2>&1)"

# --- the generated launch script wires the contract up (task 15) ---------------
lbody15="$(cat "$BASE/launch.sh" 2>/dev/null)"
have "launch: beats the heartbeat at the top of the wake" 'heartbeat --dir' "$lbody15"
have "launch: stamps the wake start for the freshness check" 'wake=$(date +%s)' "$lbody15"
have "launch: hands the wake start to supervisor-check" '--wake-start "$wake"' "$lbody15"
wake_ln="$(printf '%s\n' "$lbody15" | grep -n 'wake=$(date' | head -1 | cut -d: -f1)"
sbx15_ln="$(printf '%s\n' "$lbody15" | grep -n '^sandbox-exec -f' | head -1 | cut -d: -f1)"
if [ -n "$wake_ln" ] && [ -n "$sbx15_ln" ] && [ "$wake_ln" -lt "$sbx15_ln" ]; then
  ok "launch: the wake start is stamped BEFORE claude runs (else every declaration reads stale)"
else
  bad "launch: the wake start is stamped BEFORE claude runs" "wake@$wake_ln sandbox@$sbx15_ln"
fi

# --- doctor: run invariant audit (task 14, generalizing findings #22/#23) -----
# A test per invariant, asserting on OBSERVED state (the phase in RUN.md, the
# worktree gone, `status: systemic` written, the REPORT.md bullet) — not just a
# log string. Invariants 1 and 2 are the two that shipped as production
# failures and are covered explicitly.
if command -v git >/dev/null 2>&1; then
  DOC="$BASE/doctor"
  mkdir -p "$DOC"

  # A fake `gh` for I3/I4/I6: PR state/draft/labels live in flat files under
  # $DOCTOR_GH_DB, same shape as restack's fake gh above but extended with
  # isDraft/labels reads and the edit/ready writes I4's repair needs.
  #
  # `.state`/`.labels` reads always exit 0 (`; true` after the `cat`), even
  # when the backing file is missing — that models a POSITIVE gh read that
  # simply found nothing (D2's "PR number gh positively reports as
  # nonexistent" case: rc 0, empty field), which is what earns the I3/I6
  # "park" verdict. A TRANSIENT gh failure (401, rate limit, network) is a
  # different, non-zero-rc shape — see $DOCTOR_GH_FAIL below — and must never
  # be confused with this one.
  DOCTOR_GH="$DOC/gh"
  cat >"$DOCTOR_GH" <<'GHEOF'
#!/usr/bin/env bash
set -uo pipefail
db="${DOCTOR_GH_DB:?DOCTOR_GH_DB not set}"
[ "$1" = pr ] || exit 1
sub="$2"; num="$3"; shift 3
case "$sub" in
  view)
    jqexpr=""
    while [ $# -gt 0 ]; do case "$1" in --jq) jqexpr="$2"; shift 2 ;; *) shift ;; esac; done
    case "$jqexpr" in
      .state) cat "$db/$num.state" 2>/dev/null; true ;;
      .isDraft) cat "$db/$num.draft" 2>/dev/null || echo false ;;
      '[.labels[].name] | join(",")') cat "$db/$num.labels" 2>/dev/null; true ;;
      *) exit 1 ;;
    esac
    ;;
  edit)
    while [ $# -gt 0 ]; do
      case "$1" in
        --remove-label) printf '' >"$db/$num.labels"; shift 2 ;;
        --add-label) printf '%s\n' "$2" >>"$db/$num.labels"; shift 2 ;;
        *) shift ;;
      esac
    done
    ;;
  ready) printf 'false\n' >"$db/$num.draft" ;;
  *) exit 1 ;;
esac
GHEOF
  chmod +x "$DOCTOR_GH"

  # A `gh` that ALWAYS fails (simulating a transient 401/rate-limit/network
  # blip, D2): every `pr view` exits non-zero with no output, regardless of
  # which PR is asked about. Used to prove a bad gh moment never parks a task.
  DOCTOR_GH_FAIL="$DOC/gh-fail"
  cat >"$DOCTOR_GH_FAIL" <<'GHFAILEOF'
#!/usr/bin/env bash
exit 1
GHFAILEOF
  chmod +x "$DOCTOR_GH_FAIL"

  # Build a real "run root" layout: <root>/run (the run worktree) + a bare
  # origin, matching I5's "lives under the run root's workers/ directory"
  # convention. Every doctor scenario below gets its own root.
  _doctor_new_run() {
    local root="$1" run_id="$2"
    mkdir -p "$root"
    git init --bare -q "$root/origin.git"
    git init -q "$root/run"
    git -C "$root/run" remote add origin "$root/origin.git"
    git -C "$root/run" config user.email t@example.com
    git -C "$root/run" config user.name T
    git -C "$root/run" checkout -q -b main
    echo r >"$root/run/r.txt"
    git -C "$root/run" add r.txt
    git -C "$root/run" commit -q -m r
    git -C "$root/run" push -q origin main
    git -C "$root/run" checkout -q -b "auto-pilot/$run_id"
    mkdir -p "$root/run/.auto-pilot"
  }

  # A provably DEAD pid (spawned, killed, reaped) and the live pid of this very
  # test shell, with its real `ps` start-time. RUN.md's `orchestrator_pid` /
  # `orchestrator_started_at` are what the stale-orchestrator check reads, and
  # I5 gates the prune of an UNMATCHED worker worktree on that check saying the
  # orchestrator is provably dead — a LIVE one means the unmatched row could be
  # a dispatch in flight whose row hasn't been written back yet.
  _dead_pid() {
    local p
    sleep 30 &
    p=$!
    kill "$p" 2>/dev/null
    wait "$p" 2>/dev/null
    printf '%s' "$p"
  }
  LIVE_PID=$$
  LIVE_STARTED="$(ps -o lstart= -p $$)"

  # --- I1: run worktree HEAD parked off the run-state branch -> repaired ----
  D1="$DOC/i1"
  RUN_ID1="doctor-i1"
  _doctor_new_run "$D1" "$RUN_ID1"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID1"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D1/run/.auto-pilot/RUN.md"
  : >"$D1/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D1/run/.auto-pilot/REPORT.md"
  git -C "$D1/run" add .auto-pilot
  git -C "$D1/run" commit -q -m "seed run state"
  git -C "$D1/run" checkout -q main
  git -C "$D1/run" checkout -q -b bestdan/task-x
  echo work >"$D1/run/task-file"
  git -C "$D1/run" add task-file
  git -C "$D1/run" commit -q -m "task work on the wrong branch"

  d1out="$("$SCRIPT" doctor --dir "$D1/run" --run-id "$RUN_ID1" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d1rc=$?
  [ "$d1rc" = 0 ] && ok "doctor I1: exits 0 after repairing HEAD" || bad "doctor I1: exits 0 after repairing HEAD" "$d1out"
  have "doctor I1: summary reports the HEAD repair" 'I1: HEAD restored' "$d1out"
  d1head="$(git -C "$D1/run" rev-parse --abbrev-ref HEAD)"
  [ "$d1head" = "auto-pilot/$RUN_ID1" ] && ok "doctor I1: HEAD is observably back on the run-state branch" \
    || bad "doctor I1: HEAD is observably back on the run-state branch" "got $d1head"
  have "doctor I1: records the deviation in QUESTIONS.md" 'HEAD was parked on `bestdan/task-x`' "$(cat "$D1/run/.auto-pilot/QUESTIONS.md")"
  have "doctor I1: appends a REPORT.md bullet" 'I1 repaired' "$(cat "$D1/run/.auto-pilot/REPORT.md")"

  # --- I2 (halt): RUN.md unreadable/unparseable FROM THE BRANCH -------------
  D2="$DOC/i2-halt"
  RUN_ID2="doctor-i2-halt"
  _doctor_new_run "$D2" "$RUN_ID2"
  printf 'not RUN.md at all -- no front matter\n' >"$D2/run/.auto-pilot/RUN.md"
  : >"$D2/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D2/run/.auto-pilot/REPORT.md"
  git -C "$D2/run" add .auto-pilot
  git -C "$D2/run" commit -q -m "seed broken run state (no front matter)"

  # Point the notifier guard's recorder at a PER-TEST log so the doctor's own
  # notifier calls can be COUNTED. Doctor runs inside the jail, where osascript is
  # exec-denied, so the right count is ZERO — asserted positively below rather than
  # merely implied by the absence of an ALARM sentinel.
  D2_NOTIFY="$D2/notify.calls"
  : >"$D2_NOTIFY"
  d2out="$(NOTIFY_GUARD_LOG="$D2_NOTIFY" "$SCRIPT" doctor --dir "$D2/run" --run-id "$RUN_ID2" 2>&1)"
  d2rc=$?
  [ "$d2rc" = 30 ] && ok "doctor I2 halt: exits 30 (a caller gating on this cannot dispatch)" \
    || bad "doctor I2 halt: exits 30" "exit=$d2rc out=$d2out"
  have "doctor I2 halt: RUN.md status is observably systemic-attempted or REPORT.md carries the alarm" 'ALARM' "$(cat "$D2/run/.auto-pilot/REPORT.md")"
  have "doctor I2 halt: reason names invariant 2" 'invariant 2' "$(cat "$D2/run/.auto-pilot/REPORT.md")"
  # --label was NOT passed: the halt still fires (no teardown to attempt).
  have "doctor I2 halt: --label-less halt still reports itself" 'supervisor halt' "$d2out"

  # --- doctor halt -> a HUMAN is actually told (task 16's jailed seam) -------
  # Doctor runs INSIDE the jail, where osascript is exec-denied, so it must NOT
  # call `alarm` itself: `alarm` writes the ALARM SENTINEL, and the supervisor's
  # `status: systemic` scan goes SILENT whenever that sentinel exists ("already
  # screamed about this run") — an in-jail alarm would gag the one channel that
  # can actually reach a human. So doctor drops an `alarm-request` (the seam
  # task 16 built for jailed detectors) and the UN-JAILED supervisor delivers it,
  # carrying WHICH invariant failed — a diagnosis the generic systemic scan
  # cannot state. Observed end to end, not by source shape.
  [ -f "$D2/run/.auto-pilot/alarm-requests/invariant.alarm" ] \
    && ok "doctor halt: files an alarm-request the un-jailed supervisor can deliver" \
    || bad "doctor halt: files an alarm-request the un-jailed supervisor can deliver"
  have "doctor halt: the request names the failing invariant, not just 'systemic'" \
    'invariant 2' "$(cat "$D2/run/.auto-pilot/alarm-requests/invariant.alarm" 2>/dev/null)"
  # The sentinel must NOT exist yet: doctor writing it in-jail is exactly what
  # would suppress the supervisor's delivery below.
  [ ! -f "$D2/run/.auto-pilot/ALARM" ] \
    && ok "doctor halt: does NOT write the ALARM sentinel in-jail (which would gag the supervisor)" \
    || bad "doctor halt: does NOT write the ALARM sentinel in-jail"
  # The POSITIVE form of the same property, with a recording notifier on PATH: a
  # doctor halt must invoke the notifier ZERO times. In production the jail denies
  # it anyway (osascript is exec-denied), so a doctor that TRIED to notify would be
  # silently denied AND would leave the gagging sentinel behind — the count is the
  # only thing that catches that regression.
  d2_notify_n="$(wc -l <"$D2_NOTIFY" | tr -d " ")"
  [ "$d2_notify_n" = 0 ] \
    && ok "doctor halt: invokes the notifier ZERO times (it files an alarm-request instead)" \
    || bad "doctor halt: invokes the notifier ZERO times" "got $d2_notify_n call(s): $(cat "$D2_NOTIFY")"
  # Now the un-jailed side runs (as it does above the gate on every wake, and
  # from supervisor-check right after the agent exits — the SAME wake).
  D2_SCAN_NOTIFY="$D2/scan-notify.calls"
  : >"$D2_SCAN_NOTIFY"
  # This capture is also the regression guard for `_run_bounded`'s watchdog: the
  # scan bounds its status-report at REPORT_TIMEOUT_SECONDS_DEFAULT (60s), and if
  # the watchdog's `sleep` survives the kill it keeps THIS `$( )` pipe open for the
  # whole bound — the scan's own work takes well under a second. So the elapsed
  # time of the substitution, not just its output, is the assertion. It cost the
  # gate 60s a call until the watchdog was group-killed with its fds off the pipe.
  scan_t0=$SECONDS
  scanout="$(NOTIFY_GUARD_LOG="$D2_SCAN_NOTIFY" "$SCRIPT" supervisor-scan --dir "$D2/run" --label doctor-alarm-test 2>&1)"
  scan_elapsed=$((SECONDS - scan_t0))
  [ "$scan_elapsed" -lt 15 ] \
    && ok "doctor halt: a captured supervisor-scan returns as soon as the scan does (the watchdog does not hold the \$( ) pipe)" \
    || bad "doctor halt: a captured supervisor-scan returns as soon as the scan does" \
      "took ${scan_elapsed}s — the watchdog's sleep is orphaned and holding the command substitution open"
  have "doctor halt: the supervisor DELIVERS the doctor's alarm on its next scan" 'ALARM invariant' "$scanout"
  # ...and the UN-jailed side is where the notification actually happens: exactly
  # one, so the seam moved the notification rather than losing it.
  d2_scan_n="$(grep -c "^osascript: " "$D2_SCAN_NOTIFY" | tr -d " ")"
  [ "$d2_scan_n" = 1 ] \
    && ok "doctor halt: the UN-JAILED supervisor notifies exactly once (the seam moves the alarm, never drops it)" \
    || bad "doctor halt: the un-jailed supervisor notifies exactly once" "got $d2_scan_n"
  have "doctor halt: the delivered alarm names the invariant" 'invariant 2' "$scanout"
  have "doctor halt: the alarm reaches REPORT.md's very first line" 'ALARM (' "$(head -1 "$D2/run/.auto-pilot/REPORT.md")"
  [ -f "$D2/run/.auto-pilot/ALARM" ] \
    && ok "doctor halt: the supervisor's delivery writes the ALARM sentinel (idempotency key)" \
    || bad "doctor halt: the supervisor's delivery writes the ALARM sentinel"
  # And it is delivered ONCE: a second scan (the run is still `systemic`) must
  # not re-notify under a second name — that per-wake noise is what makes the
  # next real alarm ignorable.
  scanout2="$("$SCRIPT" supervisor-scan --dir "$D2/run" --label doctor-alarm-test 2>&1)"
  lack "doctor halt: a second scan never re-alarms the run it already announced" 'ALARM systemic' "$scanout2"

  # --- I2 (repair): RUN.md fine on the branch, missing from the WORKING TREE
  D2R="$DOC/i2-repair"
  RUN_ID2R="doctor-i2-repair"
  _doctor_new_run "$D2R" "$RUN_ID2R"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID2R"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D2R/run/.auto-pilot/RUN.md"
  : >"$D2R/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D2R/run/.auto-pilot/REPORT.md"
  git -C "$D2R/run" add .auto-pilot
  git -C "$D2R/run" commit -q -m "seed run state"
  rm -f "$D2R/run/.auto-pilot/RUN.md" # gone from the WORKING TREE only

  d2rout="$("$SCRIPT" doctor --dir "$D2R/run" --run-id "$RUN_ID2R" 2>&1)"
  d2rrc=$?
  [ "$d2rrc" = 0 ] && ok "doctor I2 repair: exits 0 (working-tree-only loss is a repair, not a halt)" \
    || bad "doctor I2 repair: exits 0" "$d2rout"
  have "doctor I2 repair: summary reports the RUN.md restore" 'I2: RUN.md restored from branch' "$d2rout"
  [ -f "$D2R/run/.auto-pilot/RUN.md" ] && ok "doctor I2 repair: RUN.md is observably back in the working tree" \
    || bad "doctor I2 repair: RUN.md is observably back in the working tree"

  # --- I1+I2 deadlock: HEAD parked off-branch AND RUN.md deleted -------------
  # (the acceptance criterion's own scenario / finding #23's shape). The
  # deleted RUN.md IS the dirt that used to make assert_run_head fail closed
  # before I2 ever got a chance to restore it. Assert on OBSERVED state, not
  # log strings: exit 0, HEAD back on the run-state branch, RUN.md restored,
  # REPORT.md carries the repair bullet.
  D1D="$DOC/i1-i2-deadlock"
  RUN_ID1D="doctor-i1-i2-deadlock"
  _doctor_new_run "$D1D" "$RUN_ID1D"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID1D"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D1D/run/.auto-pilot/RUN.md"
  : >"$D1D/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D1D/run/.auto-pilot/REPORT.md"
  git -C "$D1D/run" add .auto-pilot
  git -C "$D1D/run" commit -q -m "seed run state"
  git -C "$D1D/run" checkout -q main
  git -C "$D1D/run" checkout -q -b bestdan/task-x
  echo work >"$D1D/run/task-file"
  git -C "$D1D/run" add task-file
  git -C "$D1D/run" commit -q -m "task work on the wrong branch"
  rm -f "$D1D/run/.auto-pilot/RUN.md" # the literal acceptance-criterion scenario
  # D1: a real run worktree ALWAYS carries UNTRACKED .auto-pilot/ content (the
  # live run's own orchestrator.log/verify-broker.log). `git reset`/`checkout`
  # cannot discard untracked files, so without --ignore-untracked-run-state
  # this alone would keep assert_run_head fail-closed and I1 could never fire
  # in a real run — reproduce that here. mkdir first: the task branch was cut
  # from main, which carries no .auto-pilot/ (the run files live only on the
  # run-state branch), so the directory does not exist here yet.
  mkdir -p "$D1D/run/.auto-pilot"
  printf 'orchestrator log line\n' >"$D1D/run/.auto-pilot/orchestrator.log"

  d1dout="$("$SCRIPT" doctor --dir "$D1D/run" --run-id "$RUN_ID1D" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d1drc=$?
  [ "$d1drc" = 0 ] && ok "doctor I1+I2 deadlock: exits 0 (recovers instead of bailing to a human)" \
    || bad "doctor I1+I2 deadlock: exits 0" "$d1dout"
  d1dhead="$(git -C "$D1D/run" rev-parse --abbrev-ref HEAD)"
  [ "$d1dhead" = "auto-pilot/$RUN_ID1D" ] && ok "doctor I1+I2 deadlock: HEAD is observably back on the run-state branch" \
    || bad "doctor I1+I2 deadlock: HEAD is observably back on the run-state branch" "got $d1dhead"
  [ -f "$D1D/run/.auto-pilot/RUN.md" ] && ok "doctor I1+I2 deadlock: RUN.md is observably back in the working tree" \
    || bad "doctor I1+I2 deadlock: RUN.md is observably back in the working tree"
  have "doctor I1+I2 deadlock: REPORT.md gained the repair bullet" 'I1 repaired' "$(cat "$D1D/run/.auto-pilot/REPORT.md")"
  # D1: the success line reports the DISCARD path (not a bare restore), and
  # the untracked run log survives — it must never be `git clean`ed away.
  have "doctor I1+I2 deadlock: reports the discard path, not a bare restore" \
    'I1: discarded stale .auto-pilot/ dirt' "$d1dout"
  [ -f "$D1D/run/.auto-pilot/orchestrator.log" ] && grep -q 'orchestrator log line' "$D1D/run/.auto-pilot/orchestrator.log" \
    && ok "doctor I1+I2 deadlock: the untracked run log survives the repair (D1 — never git-clean'd)" \
    || bad "doctor I1+I2 deadlock: the untracked run log survives the repair"

  # --- I1 untracked-only: HEAD parked off-branch, the ONLY .auto-pilot/ -----
  # dirt is UNTRACKED (a real run's orchestrator.log/verify-broker.log; no
  # tracked change at all) — this is the literal #23-repro scenario D1 fixes:
  # before, `git reset`/`checkout` no-op on untracked files, so the discard
  # never actually unblocked assert_run_head. Assert the repair still fires
  # and the log is left in place untouched.
  D1U="$DOC/i1-untracked-only"
  RUN_ID1U="doctor-i1-untracked-only"
  _doctor_new_run "$D1U" "$RUN_ID1U"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID1U"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D1U/run/.auto-pilot/RUN.md"
  : >"$D1U/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D1U/run/.auto-pilot/REPORT.md"
  git -C "$D1U/run" add .auto-pilot
  git -C "$D1U/run" commit -q -m "seed run state"
  git -C "$D1U/run" checkout -q main
  git -C "$D1U/run" checkout -q -b bestdan/task-z
  echo work >"$D1U/run/task-file"
  git -C "$D1U/run" add task-file
  git -C "$D1U/run" commit -q -m "task work on the wrong branch"
  mkdir -p "$D1U/run/.auto-pilot" # same as above: the task branch carries no .auto-pilot/
  printf 'orchestrator log line\n' >"$D1U/run/.auto-pilot/orchestrator.log"
  printf 'verify broker log line\n' >"$D1U/run/.auto-pilot/verify-broker.log"

  d1uout="$("$SCRIPT" doctor --dir "$D1U/run" --run-id "$RUN_ID1U" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d1urc=$?
  [ "$d1urc" = 0 ] && ok "doctor I1 untracked-only: exits 0" || bad "doctor I1 untracked-only: exits 0" "$d1uout"
  d1uhead="$(git -C "$D1U/run" rev-parse --abbrev-ref HEAD)"
  [ "$d1uhead" = "auto-pilot/$RUN_ID1U" ] && ok "doctor I1 untracked-only: HEAD is observably back on the run-state branch" \
    || bad "doctor I1 untracked-only: HEAD is observably back on the run-state branch" "got $d1uhead"
  have "doctor I1 untracked-only: reports the discard path" 'I1: discarded stale .auto-pilot/ dirt' "$d1uout"
  [ -f "$D1U/run/.auto-pilot/orchestrator.log" ] && [ -f "$D1U/run/.auto-pilot/verify-broker.log" ] \
    && ok "doctor I1 untracked-only: both untracked run logs survive (never git-clean'd)" \
    || bad "doctor I1 untracked-only: both untracked run logs survive"

  # --- I1 negative: HEAD off-branch with dirt OUTSIDE .auto-pilot/ still -----
  # fails closed. Someone's real work is never silently discarded.
  D1N="$DOC/i1-negative"
  RUN_ID1N="doctor-i1-negative"
  _doctor_new_run "$D1N" "$RUN_ID1N"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID1N"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D1N/run/.auto-pilot/RUN.md"
  : >"$D1N/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D1N/run/.auto-pilot/REPORT.md"
  git -C "$D1N/run" add .auto-pilot
  git -C "$D1N/run" commit -q -m "seed run state"
  git -C "$D1N/run" checkout -q main
  git -C "$D1N/run" checkout -q -b bestdan/task-y
  echo work >"$D1N/run/task-file"
  git -C "$D1N/run" add task-file
  git -C "$D1N/run" commit -q -m "task work on the wrong branch"
  printf 'uncommitted real work\n' >"$D1N/run/important-work.txt" # dirt OUTSIDE .auto-pilot/
  rm -f "$D1N/run/.auto-pilot/RUN.md"

  d1nout="$("$SCRIPT" doctor --dir "$D1N/run" --run-id "$RUN_ID1N" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d1nrc=$?
  [ "$d1nrc" != 0 ] && ok "doctor I1 negative: real work outside .auto-pilot/ still fails closed (non-zero)" \
    || bad "doctor I1 negative: real work outside .auto-pilot/ still fails closed" "$d1nout"
  d1nhead="$(git -C "$D1N/run" rev-parse --abbrev-ref HEAD)"
  [ "$d1nhead" = "bestdan/task-y" ] && ok "doctor I1 negative: HEAD is left unchanged" \
    || bad "doctor I1 negative: HEAD is left unchanged" "got $d1nhead"
  have "doctor I1 negative: the real-work file is left untouched" 'uncommitted real work' \
    "$(cat "$D1N/run/important-work.txt")"

  # --- I3: every pr-open/in-review/iterating/handed-off task has a real, ----
  # open (or merged) PR. Covers: no PR number recorded, CLOSED, nonexistent,
  # OPEN (holds), and MERGED (holds — NOT a repair; a human merge is healthy).
  D3="$DOC/i3"
  RUN_ID3="doctor-i3"
  _doctor_new_run "$D3" "$RUN_ID3"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID3"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_nopr   | in-review | br-nopr   | main | - | -    | |\n'
    printf '| t_closed | pr-open   | br-closed | main | - | #301 | |\n'
    printf '| t_gone   | pr-open   | br-gone   | main | - | #302 | |\n'
    printf '| t_open   | pr-open   | br-open   | main | - | #303 | |\n'
    printf '| t_merged | handed-off | br-merged | main | - | #304 | |\n'
    # D3: the markdown-link cell shape RUN.md's own writer actually emits —
    # must parse to a bare PR number and hold (be left alone), not park.
    printf '| t_mdlink | pr-open   | br-mdlink | main | - | [#305](https://github.com/bestdan/workflow-skills/pull/305) | |\n'
  } >"$D3/run/.auto-pilot/RUN.md"
  : >"$D3/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D3/run/.auto-pilot/REPORT.md"
  git -C "$D3/run" add .auto-pilot
  git -C "$D3/run" commit -q -m "seed run state"
  I3DB="$D3/ghdb"
  mkdir -p "$I3DB"
  printf 'CLOSED\n' >"$I3DB/301.state"
  # 302: no state file at all -> the fake gh's `.state` read still exits 0
  # (positively reports "nonexistent") -> "does not exist"
  printf 'OPEN\n' >"$I3DB/303.state"
  printf 'false\n' >"$I3DB/303.draft"
  printf '\n' >"$I3DB/303.labels"
  printf 'MERGED\n' >"$I3DB/304.state"
  printf 'OPEN\n' >"$I3DB/305.state"
  export DOCTOR_GH_DB="$I3DB"
  d3out="$("$SCRIPT" doctor --dir "$D3/run" --run-id "$RUN_ID3" --gh "$DOCTOR_GH" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d3rc=$?
  [ "$d3rc" = 0 ] && ok "doctor I3: exits 0 (park is a repair, not a halt)" || bad "doctor I3: exits 0" "$d3out"
  d3run="$(cat "$D3/run/.auto-pilot/RUN.md")"
  have "doctor I3: no-PR row parked" $'t_nopr   | parked' "$d3run"
  have "doctor I3: CLOSED PR row parked" $'t_closed | parked' "$d3run"
  have "doctor I3: nonexistent PR row parked" $'t_gone   | parked' "$d3run"
  have "doctor I3: OPEN PR row left alone" $'t_open   | pr-open' "$d3run"
  have "doctor I3: MERGED PR row left alone (a human merge is healthy, not a violation)" $'t_merged | handed-off' "$d3run"
  lack "doctor I3: a merged row is never parked" $'t_merged | parked' "$d3run"
  have "doctor I3: REPORT.md records why each park happened" 'I3 parked' "$(cat "$D3/run/.auto-pilot/REPORT.md")"
  have "doctor I3 (D3): the markdown-link PR cell parses and holds (left alone)" $'t_mdlink | pr-open' "$d3run"
  lack "doctor I3 (D3): the markdown-link row is never parked" $'t_mdlink | parked' "$d3run"

  # --- I3 (D2): a transient gh failure must never park an in-flight task ----
  D3G="$DOC/i3-gh-fail"
  RUN_ID3G="doctor-i3-gh-fail"
  _doctor_new_run "$D3G" "$RUN_ID3G"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID3G"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_a | pr-open    | br-a | main | - | #401 | |\n'
    printf '| t_b | handed-off | br-b | main | - | #402 | |\n'
  } >"$D3G/run/.auto-pilot/RUN.md"
  : >"$D3G/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D3G/run/.auto-pilot/REPORT.md"
  git -C "$D3G/run" add .auto-pilot
  git -C "$D3G/run" commit -q -m "seed run state"
  d3gout="$("$SCRIPT" doctor --dir "$D3G/run" --run-id "$RUN_ID3G" --gh "$DOCTOR_GH_FAIL" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d3grc=$?
  [ "$d3grc" = 0 ] && ok "doctor I3 (D2): a failing gh still exits 0 (undetermined, not a halt)" \
    || bad "doctor I3 (D2): a failing gh still exits 0" "exit=$d3grc out=$d3gout"
  d3grun="$(cat "$D3G/run/.auto-pilot/RUN.md")"
  have "doctor I3 (D2): pr-open row is NOT parked on a gh failure" $'t_a | pr-open' "$d3grun"
  lack "doctor I3 (D2): pr-open row is not parked" $'t_a | parked' "$d3grun"
  have "doctor I3 (D2): handed-off row is NOT parked on a gh failure" $'t_b | handed-off' "$d3grun"
  lack "doctor I3 (D2): handed-off row is not parked" $'t_b | parked' "$d3grun"
  have "doctor I3 (D2): summary counts the gh failure as skipped, not parked" 'skipped=' "$d3gout"

  # --- I4: a handed-off repo-pr task's review signal (label + not-draft) ----
  D4="$DOC/i4"
  RUN_ID4="doctor-i4"
  _doctor_new_run "$D4" "$RUN_ID4"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID4"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_stale | handed-off | br-stale | main | - | #401 | |\n'
    printf '| t_good  | handed-off | br-good  | main | - | #402 | |\n'
  } >"$D4/run/.auto-pilot/RUN.md"
  : >"$D4/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D4/run/.auto-pilot/REPORT.md"
  git -C "$D4/run" add .auto-pilot
  git -C "$D4/run" commit -q -m "seed run state"
  I4DB="$D4/ghdb"
  mkdir -p "$I4DB"
  # t_stale: a G6/G7 crash gap -- still `task-claim`, still draft.
  printf 'OPEN\n' >"$I4DB/401.state"
  printf 'true\n' >"$I4DB/401.draft"
  printf 'task-claim\n' >"$I4DB/401.labels"
  # t_good: already carries the review signal -- must be a no-op.
  printf 'OPEN\n' >"$I4DB/402.state"
  printf 'false\n' >"$I4DB/402.draft"
  printf 'task-loop\n' >"$I4DB/402.labels"
  export DOCTOR_GH_DB="$I4DB"
  d4out="$("$SCRIPT" doctor --dir "$D4/run" --run-id "$RUN_ID4" --gh "$DOCTOR_GH" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d4rc=$?
  [ "$d4rc" = 0 ] && ok "doctor I4: exits 0" || bad "doctor I4: exits 0" "$d4out"
  have "doctor I4: summary reports the repair for the stale PR" 'I4: t_stale PR #401' "$d4out"
  lack "doctor I4: no repair reported for the already-correct PR" 'I4: t_good' "$d4out"
  have "doctor I4: swaps the label task-claim -> task-loop" 'task-loop' "$(cat "$I4DB/401.labels")"
  lack "doctor I4: removes task-claim from the label file" 'task-claim' "$(cat "$I4DB/401.labels")"
  have "doctor I4: marks the draft PR ready" 'false' "$(cat "$I4DB/401.draft")"
  have "doctor I4: REPORT.md records the repair" 'I4 repaired — t_stale' "$(cat "$D4/run/.auto-pilot/REPORT.md")"
  # t_good's files are untouched -- no edit/ready call was ever made for it.
  [ "$(cat "$I4DB/402.labels")" = "task-loop" ] && ok "doctor I4: already-correct PR's labels are untouched" \
    || bad "doctor I4: already-correct PR's labels are untouched" "$(cat "$I4DB/402.labels")"

  # --- I4: a gh WRITE that fails is never reported as a completed repair -----
  # Same D5 rule the failed `worktree remove` follows: a gh blip mid-repair that
  # still wrote "I4 repaired" into REPORT.md/QUESTIONS.md would be exactly the
  # silent lie doctor exists to eliminate. Reads succeed here (so the repair is
  # correctly ATTEMPTED); both writes fail.
  DOCTOR_GH_WFAIL="$DOC/gh-write-fail"
  cat >"$DOCTOR_GH_WFAIL" <<'GHWEOF'
#!/usr/bin/env bash
set -uo pipefail
db="${DOCTOR_GH_DB:?DOCTOR_GH_DB not set}"
[ "$1" = pr ] || exit 1
sub="$2"; num="$3"; shift 3
case "$sub" in
  view)
    jqexpr=""
    while [ $# -gt 0 ]; do case "$1" in --jq) jqexpr="$2"; shift 2 ;; *) shift ;; esac; done
    case "$jqexpr" in
      .state) cat "$db/$num.state" 2>/dev/null; true ;;
      .isDraft) cat "$db/$num.draft" 2>/dev/null || echo false ;;
      '[.labels[].name] | join(",")') cat "$db/$num.labels" 2>/dev/null; true ;;
      *) exit 1 ;;
    esac
    ;;
  edit|ready) exit 1 ;;   # the write blip
  *) exit 1 ;;
esac
GHWEOF
  chmod +x "$DOCTOR_GH_WFAIL"

  D4W="$DOC/i4-write-fail"
  RUN_ID4W="doctor-i4-write-fail"
  _doctor_new_run "$D4W" "$RUN_ID4W"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID4W"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_stale | handed-off | br-stale | main | - | #411 | |\n'
  } >"$D4W/run/.auto-pilot/RUN.md"
  : >"$D4W/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D4W/run/.auto-pilot/REPORT.md"
  git -C "$D4W/run" add .auto-pilot
  git -C "$D4W/run" commit -q -m "seed run state"
  I4WDB="$D4W/ghdb"
  mkdir -p "$I4WDB"
  printf 'OPEN\n' >"$I4WDB/411.state"
  printf 'true\n' >"$I4WDB/411.draft"
  printf 'task-claim\n' >"$I4WDB/411.labels"
  export DOCTOR_GH_DB="$I4WDB"
  d4wout="$("$SCRIPT" doctor --dir "$D4W/run" --run-id "$RUN_ID4W" --gh "$DOCTOR_GH_WFAIL" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d4wrc=$?
  [ "$d4wrc" = 0 ] && ok "doctor I4 (write blip): exits 0" || bad "doctor I4 (write blip): exits 0" "$d4wout"
  # The summary's repair note has the form `I4: <task> PR #<n>` — the FAILED
  # announcements above deliberately don't, so match the note's exact shape.
  lack "doctor I4 (write blip): a FAILED gh write is never summarized as a repair" 'I4: t_stale PR #411' "$d4wout"
  have "doctor I4 (write blip): the summary counts no repairs at all" 'repaired=0' "$d4wout"
  lack "doctor I4 (write blip): REPORT.md never records the repair that didn't happen" 'I4 repaired' "$(cat "$D4W/run/.auto-pilot/REPORT.md")"
  lack "doctor I4 (write blip): QUESTIONS.md never records the repair that didn't happen" 'missing its repo-pr review signal' "$(cat "$D4W/run/.auto-pilot/QUESTIONS.md")"
  have "doctor I4 (write blip): the failed label write is announced" 'gh pr edit` FAILED' "$d4wout"
  have "doctor I4 (write blip): the failed ready write is announced" 'gh pr ready` FAILED' "$d4wout"
  unset DOCTOR_GH_DB

  # --- I5: orphan worker worktrees under <run root>/workers/ (G2) -----------
  # D4: `pending` is deliberately NOT on the safe list any more — the phase
  # cell lags a live dispatch (it flips AFTER the orchestrator's RUN.md
  # commit+push), so a `pending` row cannot tell "never dispatched" from
  # "dispatched moments ago". Cover: a terminal (`parked`) row -> pruned; an
  # in-flight (`implementing`) row -> left alone; a `pending` row -> left
  # alone (the literal live-run reproduction: `task_14 | pending` with an
  # open PR, from this very run); a `parked` row that STILL has an OPEN PR
  # recorded -> left alone (the open-PR guard); a branch with NO RUN.md row
  # at all -> pruned, BUT only because this run's recorded orchestrator is
  # provably dead (the unmatched case's liveness gate — see the i5-live /
  # i5-dead scenarios below, which own that half of the invariant).
  D5="$DOC/i5"
  RUN_ID5="doctor-i5"
  _doctor_new_run "$D5" "$RUN_ID5"
  D5_DEAD_PID="$(_dead_pid)"
  for br in br-terminal br-unsafe br-pending br-openpr br-nomatch; do
    git -C "$D5/run" checkout -q main
    git -C "$D5/run" checkout -q -b "$br"
    echo "$br" >"$D5/run/$br.txt"
    git -C "$D5/run" add "$br.txt"
    git -C "$D5/run" commit -q -m "$br"
    git -C "$D5/run" push -q origin "$br"
  done
  # _doctor_new_run already created+checked-out auto-pilot/$RUN_ID5 — switch
  # BACK to it (no -b: it already exists) now that the branches above exist.
  git -C "$D5/run" checkout -q "auto-pilot/$RUN_ID5"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n' "$RUN_ID5"
    printf 'orchestrator_pid: %s\norchestrator_started_at: "Wed Jul  9 20:00:00 2026"\n---\n\n' "$D5_DEAD_PID"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_terminal | parked       | br-terminal | main | - | -    | |\n'
    printf '| t_unsafe   | implementing | br-unsafe   | main | - | -    | |\n'
    printf '| t_pending  | pending      | br-pending  | main | - | -    | |\n'
    printf '| t_openpr   | parked       | br-openpr   | main | - | #601 | |\n'
  } >"$D5/run/.auto-pilot/RUN.md"
  : >"$D5/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5/run/.auto-pilot/REPORT.md"
  git -C "$D5/run" add .auto-pilot
  git -C "$D5/run" commit -q -m "seed run state"
  git -C "$D5/run" push -q origin "auto-pilot/$RUN_ID5"
  mkdir -p "$D5/workers"
  git -C "$D5/run" worktree add -q "$D5/workers/w-terminal" br-terminal
  git -C "$D5/run" worktree add -q "$D5/workers/w-unsafe" br-unsafe
  git -C "$D5/run" worktree add -q "$D5/workers/w-pending" br-pending
  git -C "$D5/run" worktree add -q "$D5/workers/w-openpr" br-openpr
  git -C "$D5/run" worktree add -q "$D5/workers/w-nomatch" br-nomatch
  I5DB="$D5/ghdb"
  mkdir -p "$I5DB"
  printf 'OPEN\n' >"$I5DB/601.state"
  export DOCTOR_GH_DB="$I5DB"

  d5out="$("$SCRIPT" doctor --dir "$D5/run" --run-id "$RUN_ID5" --gh "$DOCTOR_GH" 2>&1)"
  d5rc=$?
  [ "$d5rc" = 0 ] && ok "doctor I5: exits 0" || bad "doctor I5: exits 0" "$d5out"
  have "doctor I5: reports the prune of the terminal-phase worktree" 'I5: removed w-terminal' "$d5out"
  [ ! -d "$D5/workers/w-terminal" ] && ok "doctor I5: the terminal-phase orphan worktree is observably gone" \
    || bad "doctor I5: the terminal-phase orphan worktree is observably gone"
  [ -d "$D5/workers/w-unsafe" ] && ok "doctor I5: the in-flight worktree is left alone (unsafe to prune)" \
    || bad "doctor I5: the in-flight worktree is left alone"
  # D4: `pending` no longer green-lights a prune — the exact live-run shape
  # (`task_14 | pending` with an open PR) must survive.
  [ -d "$D5/workers/w-pending" ] && ok "doctor I5 (D4): a pending-phase worktree is left alone (phase lags a live dispatch)" \
    || bad "doctor I5 (D4): a pending-phase worktree is left alone"
  # D4: an OPEN PR still recorded blocks the prune even though the phase is
  # otherwise terminal.
  [ -d "$D5/workers/w-openpr" ] && ok "doctor I5 (D4): a parked row with an OPEN PR is left alone" \
    || bad "doctor I5 (D4): a parked row with an OPEN PR is left alone"
  have "doctor I5: an in-flight worktree is reported as skipped" 'skipped (unsafe to prune)' "$d5out"
  have "doctor I5: a branch with no RUN.md row at all is pruned" 'I5: removed w-nomatch' "$d5out"
  [ ! -d "$D5/workers/w-nomatch" ] && ok "doctor I5: the no-row orphan worktree is observably gone" \
    || bad "doctor I5: the no-row orphan worktree is observably gone"

  unset DOCTOR_GH_DB

  # --- I5 (LIVE DISPATCH): the unmatched-branch data-loss regression ---------
  # The exact shape a LIVE dispatch presents, which the scenario above cannot
  # catch because it pre-bakes the `branch` cell: the orchestrator writes a
  # task's branch/phase/pr back only AFTER /deliver-task returns, so mid-flight
  # the row reads `| t_live | pending | - | … |` and matches NO worktree branch,
  # while the just-created worker worktree is clean, has no commits beyond its
  # base, and has no PR yet — i.e. every other prune condition holds. With the
  # run's orchestrator LIVE, the worktree MUST survive: removing it destroys a
  # dispatch in flight.
  D5L="$DOC/i5-live"
  RUN_ID5L="doctor-i5-live"
  _doctor_new_run "$D5L" "$RUN_ID5L"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n' "$RUN_ID5L"
    printf 'orchestrator_pid: %s\norchestrator_started_at: "%s"\n---\n\n' "$LIVE_PID" "$LIVE_STARTED"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_live | pending | - | main | - | - | |\n'
  } >"$D5L/run/.auto-pilot/RUN.md"
  : >"$D5L/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5L/run/.auto-pilot/REPORT.md"
  git -C "$D5L/run" add .auto-pilot
  git -C "$D5L/run" commit -q -m "seed run state"
  mkdir -p "$D5L/workers"
  # Exactly what /deliver-task does at claim: a fresh worker worktree on a new
  # task branch cut from the base. Nothing committed, nothing pushed, no PR.
  git -C "$D5L/run" worktree add -q -b bestdan/t-live-work "$D5L/workers/w-live" main
  # And a worker left on a DETACHED HEAD (`abbrev-ref` reads back "HEAD", which
  # no row's branch cell can equal) — the same unmatched bucket.
  git -C "$D5L/run" worktree add -q --detach "$D5L/workers/w-detached" main

  d5lout="$("$SCRIPT" doctor --dir "$D5L/run" --run-id "$RUN_ID5L" 2>&1)"
  d5lrc=$?
  [ "$d5lrc" = 0 ] && ok "doctor I5 (live dispatch): exits 0" || bad "doctor I5 (live dispatch): exits 0" "$d5lout"
  [ -d "$D5L/workers/w-live" ] && ok "doctor I5 (live dispatch): a live dispatch's worker worktree SURVIVES (unmatched row + LIVE orchestrator)" \
    || bad "doctor I5 (live dispatch): a live dispatch's worker worktree SURVIVES — it was destroyed"
  [ -d "$D5L/workers/w-detached" ] && ok "doctor I5 (live dispatch): a detached-HEAD worker worktree SURVIVES too" \
    || bad "doctor I5 (live dispatch): a detached-HEAD worker worktree SURVIVES too — it was destroyed"
  have "doctor I5 (live dispatch): reports the live worktree as skipped, not repaired" 'skipped (unsafe to prune)' "$d5lout"
  have "doctor I5 (live dispatch): the skip names the liveness gate" 'not provably dead' "$d5lout"
  lack "doctor I5 (live dispatch): nothing is reported as removed" 'I5: removed' "$d5lout"

  # --- I5 (DEAD orchestrator): the converse — invariant 5 still prunes -------
  # The same unmatched shape, but the run's recorded orchestrator is provably
  # dead: nothing can be mid-dispatch when the process that dispatches is gone,
  # so the orphan is removed as before. The liveness gate must not have been a
  # way of quietly disabling invariant 5.
  D5D="$DOC/i5-dead"
  RUN_ID5D="doctor-i5-dead"
  _doctor_new_run "$D5D" "$RUN_ID5D"
  D5D_DEAD_PID="$(_dead_pid)"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n' "$RUN_ID5D"
    printf 'orchestrator_pid: %s\norchestrator_started_at: "Wed Jul  9 20:00:00 2026"\n---\n\n' "$D5D_DEAD_PID"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_live | pending | - | main | - | - | |\n'
  } >"$D5D/run/.auto-pilot/RUN.md"
  : >"$D5D/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5D/run/.auto-pilot/REPORT.md"
  git -C "$D5D/run" add .auto-pilot
  git -C "$D5D/run" commit -q -m "seed run state"
  mkdir -p "$D5D/workers"
  git -C "$D5D/run" worktree add -q -b bestdan/t-orphan-work "$D5D/workers/w-orphan" main

  d5dout="$("$SCRIPT" doctor --dir "$D5D/run" --run-id "$RUN_ID5D" 2>&1)"
  d5drc=$?
  [ "$d5drc" = 0 ] && ok "doctor I5 (dead orchestrator): exits 0" || bad "doctor I5 (dead orchestrator): exits 0" "$d5dout"
  have "doctor I5 (dead orchestrator): reports the prune" 'I5: removed w-orphan' "$d5dout"
  [ ! -d "$D5D/workers/w-orphan" ] && ok "doctor I5 (dead orchestrator): a genuinely orphaned worktree is still observably pruned" \
    || bad "doctor I5 (dead orchestrator): a genuinely orphaned worktree is still observably pruned"

  # --- I5 (UNDETERMINED liveness): no pid recorded -> fail closed, no prune --
  # D2's posture, applied to the destructive action: an undetermined liveness
  # read is not "dead", so it never green-lights a `worktree remove --force`.
  D5U="$DOC/i5-nopid"
  RUN_ID5U="doctor-i5-nopid"
  _doctor_new_run "$D5U" "$RUN_ID5U"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID5U"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_live | pending | - | main | - | - | |\n'
  } >"$D5U/run/.auto-pilot/RUN.md"
  : >"$D5U/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5U/run/.auto-pilot/REPORT.md"
  git -C "$D5U/run" add .auto-pilot
  git -C "$D5U/run" commit -q -m "seed run state"
  mkdir -p "$D5U/workers"
  git -C "$D5U/run" worktree add -q -b bestdan/t-nopid-work "$D5U/workers/w-nopid" main

  d5uout="$("$SCRIPT" doctor --dir "$D5U/run" --run-id "$RUN_ID5U" 2>&1)"
  [ -d "$D5U/workers/w-nopid" ] && ok "doctor I5 (undetermined liveness): no orchestrator_pid recorded -> the worktree SURVIVES (fail closed)" \
    || bad "doctor I5 (undetermined liveness): no orchestrator_pid recorded -> the worktree SURVIVES"
  have "doctor I5 (undetermined liveness): reported as skipped" 'skipped (unsafe to prune)' "$d5uout"

  # --- I5 (CORRUPT git state): a failed git read must skip, not fail-open ---
  # The bug this task exists to fix: `status --porcelain`, `rev-parse HEAD`,
  # and `rev-parse --abbrev-ref HEAD` were all read with `2>/dev/null`, and an
  # EMPTY result from a FAILED read was indistinguishable from a genuinely
  # clean/unborn/detached worktree — every guard passed and a worktree with
  # true state UNKNOWN was fed straight to `git worktree remove --force`. Here
  # the worktree's `.git` link is corrupted over real uncommitted work, so
  # every one of those reads fails outright (non-zero exit, not just empty
  # stdout). The dispatch's recorded orchestrator is also provably DEAD, so
  # the ONLY thing standing between this worktree and destruction is the rc
  # check itself — this is not the liveness gate saving it. The removal must
  # never be ATTEMPTED (no "I5: removed", no "FAILED to remove" for it) —
  # distinguishing "skipped" from "attempted and failed by luck", which is
  # all that saved the WIP on unpatched main.
  D5C="$DOC/i5-corrupt"
  RUN_ID5C="doctor-i5-corrupt"
  _doctor_new_run "$D5C" "$RUN_ID5C"
  D5C_DEAD_PID="$(_dead_pid)"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n' "$RUN_ID5C"
    printf 'orchestrator_pid: %s\norchestrator_started_at: "Wed Jul  9 20:00:00 2026"\n---\n\n' "$D5C_DEAD_PID"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_corrupt | pending | - | main | - | - | |\n'
  } >"$D5C/run/.auto-pilot/RUN.md"
  : >"$D5C/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5C/run/.auto-pilot/REPORT.md"
  git -C "$D5C/run" add .auto-pilot
  git -C "$D5C/run" commit -q -m "seed run state"
  mkdir -p "$D5C/workers"
  git -C "$D5C/run" worktree add -q -b bestdan/t-corrupt-work "$D5C/workers/w-corrupt" main
  echo "uncommitted WIP" >"$D5C/workers/w-corrupt/wip.txt"
  # Corrupt the worktree's `.git` link so every read INSIDE it fails outright.
  echo "gitdir: /nonexistent/gitdir/for/w-corrupt" >"$D5C/workers/w-corrupt/.git"

  d5cout="$("$SCRIPT" doctor --dir "$D5C/run" --run-id "$RUN_ID5C" 2>&1)"
  d5crc=$?
  [ "$d5crc" = 0 ] && ok "doctor I5 (corrupt git state): exits 0" || bad "doctor I5 (corrupt git state): exits 0" "$d5cout"
  [ -d "$D5C/workers/w-corrupt" ] && ok "doctor I5 (corrupt git state): the corrupted worktree SURVIVES" \
    || bad "doctor I5 (corrupt git state): the corrupted worktree SURVIVES — it was destroyed"
  [ -f "$D5C/workers/w-corrupt/wip.txt" ] && ok "doctor I5 (corrupt git state): the uncommitted WIP file is still there" \
    || bad "doctor I5 (corrupt git state): the uncommitted WIP file is still there"
  have "doctor I5 (corrupt git state): reported as skipped — undetermined, not attempted" 'skipped (undetermined' "$d5cout"
  lack "doctor I5 (corrupt git state): removal of the corrupt worktree was never ATTEMPTED (not reported removed)" 'I5: removed w-corrupt' "$d5cout"
  lack "doctor I5 (corrupt git state): removal of the corrupt worktree was never ATTEMPTED (not reported failed)" 'FAILED to remove' "$d5cout"
  # The SUMMARY must say skipped too, not just stdout. The summary line is the
  # machine-readable one — a wrapper (or a human triaging fast) greps THAT, and
  # `ok=N ... skipped=0` is a clean bill of health for a DESTRUCTIVE invariant
  # that could not evaluate the worktree at all. I3/I6 already fold undetermined
  # into n_skipped; I5 counted it as `ok`.
  lack "doctor I5 (corrupt git state): an undetermined worktree is NOT summarised as skipped=0" \
    'skipped=0' "$d5cout"
  have "doctor I5 (corrupt git state): the summary counts it as skipped, naming the worktree" \
    'skipped=1 (I5: w-corrupt (git unreadable))' "$d5cout"

  # --- I6: a chained task's parent tip moved off its frozen base_sha --------
  # (a) the orchestrator moved the base mid-run, no merge -> park the child.
  D6="$DOC/i6"
  RUN_ID6="doctor-i6"
  _doctor_new_run "$D6" "$RUN_ID6"
  git -C "$D6/run" checkout -q main
  git -C "$D6/run" checkout -q -b br-parent
  echo p >"$D6/run/p.txt"
  git -C "$D6/run" add p.txt
  git -C "$D6/run" commit -q -m p
  git -C "$D6/run" push -q origin br-parent
  PARENT_SHA6="$(git -C "$D6/run" rev-parse br-parent)"
  echo p2 >"$D6/run/p2.txt"
  git -C "$D6/run" add p2.txt
  git -C "$D6/run" commit -q -m "parent moved (orchestrator)"
  git -C "$D6/run" push -q origin br-parent
  git -C "$D6/run" checkout -q "auto-pilot/$RUN_ID6"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID6"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_parent | pending      | br-parent | main      | - | - | |\n'
    printf '| t_child  | implementing | br-child  | br-parent | %s | - | |\n' "$PARENT_SHA6"
  } >"$D6/run/.auto-pilot/RUN.md"
  : >"$D6/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D6/run/.auto-pilot/REPORT.md"
  git -C "$D6/run" add .auto-pilot
  git -C "$D6/run" commit -q -m "seed run state"

  d6out="$("$SCRIPT" doctor --dir "$D6/run" --run-id "$RUN_ID6" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d6rc=$?
  [ "$d6rc" = 0 ] && ok "doctor I6: exits 0 (park is a repair, not a halt)" || bad "doctor I6: exits 0" "$d6out"
  have "doctor I6: parks the child whose parent's tip diverged" $'t_child  | parked' "$(cat "$D6/run/.auto-pilot/RUN.md")"
  have "doctor I6: REPORT.md explains why" 'without the parent'"'"'s PR merging' "$(cat "$D6/run/.auto-pilot/REPORT.md")"

  # (b) the SAME divergence, but the parent's PR is MERGED -> a human merge is
  # the expected trigger; the remedy is restack, never a park.
  D6M="$DOC/i6-merged"
  RUN_ID6M="doctor-i6-merged"
  _doctor_new_run "$D6M" "$RUN_ID6M"
  git -C "$D6M/run" checkout -q main
  git -C "$D6M/run" checkout -q -b br-parent
  echo p >"$D6M/run/p.txt"
  git -C "$D6M/run" add p.txt
  git -C "$D6M/run" commit -q -m p
  git -C "$D6M/run" push -q origin br-parent
  PARENT_SHA6M="$(git -C "$D6M/run" rev-parse br-parent)"
  git -C "$D6M/run" checkout -q main
  git -C "$D6M/run" merge -q --squash br-parent >/dev/null
  git -C "$D6M/run" commit -q -m "parent squash-merged"
  git -C "$D6M/run" push -q origin main
  git -C "$D6M/run" checkout -q "auto-pilot/$RUN_ID6M"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID6M"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    # D3: the parent's pr cell is the markdown-link form RUN.md's own writer
    # emits — must parse to a bare PR number, same as the bare-`#501` shape.
    printf '| t_parent | handed-off   | br-parent | main      | -  | [#501](https://github.com/bestdan/workflow-skills/pull/501) | |\n'
    printf '| t_child  | implementing | br-child  | br-parent | %s | -    | |\n' "$PARENT_SHA6M"
  } >"$D6M/run/.auto-pilot/RUN.md"
  : >"$D6M/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D6M/run/.auto-pilot/REPORT.md"
  git -C "$D6M/run" add .auto-pilot
  git -C "$D6M/run" commit -q -m "seed run state"
  I6MDB="$D6M/ghdb"
  mkdir -p "$I6MDB"
  printf 'MERGED\n' >"$I6MDB/501.state"
  export DOCTOR_GH_DB="$I6MDB"
  d6mout="$("$SCRIPT" doctor --dir "$D6M/run" --run-id "$RUN_ID6M" --gh "$DOCTOR_GH" 2>&1)"
  d6mrc=$?
  [ "$d6mrc" = 0 ] && ok "doctor I6 (merged parent): exits 0" || bad "doctor I6 (merged parent): exits 0" "$d6mout"
  have "doctor I6 (merged parent, D3 markdown-link pr cell): says the remedy is restack, not park" 'remedy is restack, not park' "$d6mout"
  lack "doctor I6 (merged parent, D3 markdown-link pr cell): the child is NOT parked" $'t_child  | parked' "$(cat "$D6M/run/.auto-pilot/RUN.md")"

  unset DOCTOR_GH_DB

  # (c) the SAME divergence, but the parent's PR state is UNREADABLE (a
  # failing gh, D2/D7) — must NOT park. Parking here would be the exact
  # violation the invariant's own comment warns against: a parent that
  # actually merged, parked anyway because its state could not be read.
  D6U="$DOC/i6-gh-unreadable"
  RUN_ID6U="doctor-i6-gh-unreadable"
  _doctor_new_run "$D6U" "$RUN_ID6U"
  git -C "$D6U/run" checkout -q main
  git -C "$D6U/run" checkout -q -b br-parent
  echo p >"$D6U/run/p.txt"
  git -C "$D6U/run" add p.txt
  git -C "$D6U/run" commit -q -m p
  git -C "$D6U/run" push -q origin br-parent
  PARENT_SHA6U="$(git -C "$D6U/run" rev-parse br-parent)"
  echo p2 >"$D6U/run/p2.txt"
  git -C "$D6U/run" add p2.txt
  git -C "$D6U/run" commit -q -m "parent moved"
  git -C "$D6U/run" push -q origin br-parent
  git -C "$D6U/run" checkout -q "auto-pilot/$RUN_ID6U"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID6U"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_parent | handed-off   | br-parent | main      | -  | #601 | |\n'
    printf '| t_child  | implementing | br-child  | br-parent | %s | -    | |\n' "$PARENT_SHA6U"
  } >"$D6U/run/.auto-pilot/RUN.md"
  : >"$D6U/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D6U/run/.auto-pilot/REPORT.md"
  git -C "$D6U/run" add .auto-pilot
  git -C "$D6U/run" commit -q -m "seed run state"
  d6uout="$("$SCRIPT" doctor --dir "$D6U/run" --run-id "$RUN_ID6U" --gh "$DOCTOR_GH_FAIL" 2>&1)"
  d6urc=$?
  [ "$d6urc" = 0 ] && ok "doctor I6 (D2/D7, gh unreadable): exits 0 (undetermined, not a halt)" \
    || bad "doctor I6 (D2/D7, gh unreadable): exits 0" "exit=$d6urc out=$d6uout"
  lack "doctor I6 (D2/D7, gh unreadable): the child is NOT parked on an unreadable parent state" \
    $'t_child  | parked' "$(cat "$D6U/run/.auto-pilot/RUN.md")"
  have "doctor I6 (D2/D7, gh unreadable): reports it as skipped/unreadable, not a park" \
    'parent PR state unreadable' "$d6uout"

  # --- I7: forward progress across ITERATIONS within one live process ------
  D7="$DOC/i7"
  RUN_ID7="doctor-i7"
  _doctor_new_run "$D7" "$RUN_ID7"
  {
    printf -- '---\nrun_id: %s\nstatus: active\npause_reason: \n---\n\n' "$RUN_ID7"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D7/run/.auto-pilot/RUN.md"
  : >"$D7/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D7/run/.auto-pilot/REPORT.md"
  git -C "$D7/run" add .auto-pilot
  git -C "$D7/run" commit -q -m "seed run state"

  "$SCRIPT" doctor --dir "$D7/run" --run-id "$RUN_ID7" --no-progress-limit 3 >/dev/null 2>&1
  "$SCRIPT" doctor --dir "$D7/run" --run-id "$RUN_ID7" --no-progress-limit 3 >/dev/null 2>&1
  d7out="$("$SCRIPT" doctor --dir "$D7/run" --run-id "$RUN_ID7" --no-progress-limit 3 2>&1)"
  d7rc=$?
  [ "$d7rc" = 30 ] && ok "doctor I7: halts after N consecutive no-progress iterations, exits 30" \
    || bad "doctor I7: halts after N consecutive no-progress iterations" "exit=$d7rc out=$d7out"
  have "doctor I7: reason names invariant 7" 'invariant 7' "$d7out"
  have "doctor I7: halt is observable in REPORT.md" 'ALARM' "$(cat "$D7/run/.auto-pilot/REPORT.md")"
  # Every doctor halt goes through the same jailed alarm seam, not just I2's.
  have "doctor I7: the halt files an alarm-request naming invariant 7" \
    'invariant 7' "$(cat "$D7/run/.auto-pilot/alarm-requests/invariant.alarm" 2>/dev/null)"

  # I7 with the side channel BROKEN (task 26). `_write_supervisor_state` — the
  # no-progress COUNTER — `die`s (an `exit`) on a write failure and runs immediately
  # BEFORE this halt, so an unwritable run dir would abort the whole doctor process
  # at `mktemp failed` before invariant 7 ever halts, and before doctor's own exit-30
  # (HALT) contract could be honoured: the caller sees a bare `2`, reads it as
  # "doctor errored" rather than "the run must stop", and a wedged run keeps being
  # dispatched. The counter itself cannot advance while the dir is unwritable — but
  # the halt for THIS iteration must still fire, and must still be an exit 30.
  D7U="$DOC/i7-unwritable"
  RUN_ID7U="doctor-i7u"
  _doctor_new_run "$D7U" "$RUN_ID7U"
  {
    printf -- '---\nrun_id: %s\nstatus: active\npause_reason: \n---\n\n' "$RUN_ID7U"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D7U/run/.auto-pilot/RUN.md"
  : >"$D7U/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D7U/run/.auto-pilot/REPORT.md"
  git -C "$D7U/run" add .auto-pilot
  git -C "$D7U/run" commit -q -m "seed run state"
  chmod -w "$D7U/run/.auto-pilot"
  d7uout="$("$SCRIPT" doctor --dir "$D7U/run" --run-id "$RUN_ID7U" --no-progress-limit 1 2>&1)"
  d7urc=$?
  chmod +w "$D7U/run/.auto-pilot"
  [ "$d7urc" = 30 ] \
    && ok "doctor I7 (unwritable run dir): still HALTS with exit 30, never the bare die (2)" \
    || bad "doctor I7 (unwritable run dir): still HALTS with exit 30, never the bare die (2)" "exit=$d7urc out=$d7uout"
  have "doctor I7 (unwritable run dir): the halt still names invariant 7" 'invariant 7' "$d7uout"

  # THE gate: a caller checking doctor's exit code cannot reach a dispatch.
  would_dispatch=1
  [ "$d7rc" = 30 ] && would_dispatch=0
  [ "$would_dispatch" = 0 ] && ok "doctor: the loop cannot advance to a dispatch while a halt is in effect (exit 30 gates it)" \
    || bad "doctor: the loop cannot advance while a halt is in effect"

  # I7 never fires while the run is legitimately paused, however many repeats.
  D7P="$DOC/i7-paused"
  RUN_ID7P="doctor-i7-paused"
  _doctor_new_run "$D7P" "$RUN_ID7P"
  {
    printf -- '---\nrun_id: %s\nstatus: paused\npaused_until: 2099-01-01T00:00:00\n---\n\n' "$RUN_ID7P"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D7P/run/.auto-pilot/RUN.md"
  : >"$D7P/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D7P/run/.auto-pilot/REPORT.md"
  git -C "$D7P/run" add .auto-pilot
  git -C "$D7P/run" commit -q -m "seed paused run state"
  i=0
  while [ "$i" -lt 5 ]; do
    "$SCRIPT" doctor --dir "$D7P/run" --run-id "$RUN_ID7P" --no-progress-limit 3 >/dev/null 2>&1
    i=$((i + 1))
  done
  lack "doctor I7: a paused run never halts, however many repeats" 'systemic' "$(cat "$D7P/run/.auto-pilot/RUN.md")"

  # --- I7 (D6): --context resume RESETS the no-progress counter instead of --
  # incrementing it. Doctor runs once at the top of --resume and again at the
  # top of the first loop iteration, with HEAD necessarily unchanged between
  # the two — without D6's fix that pair alone would put the counter at 2,
  # one strike from a spurious halt before any work runs. Drive it right up
  # to the limit under `loop` context, then prove a `resume` call resets
  # instead of tipping it over.
  D7R="$DOC/i7-resume-context"
  RUN_ID7R="doctor-i7-resume-context"
  _doctor_new_run "$D7R" "$RUN_ID7R"
  {
    printf -- '---\nrun_id: %s\nstatus: active\npause_reason: \n---\n\n' "$RUN_ID7R"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D7R/run/.auto-pilot/RUN.md"
  : >"$D7R/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D7R/run/.auto-pilot/REPORT.md"
  git -C "$D7R/run" add .auto-pilot
  git -C "$D7R/run" commit -q -m "seed run state"
  # Two no-progress `loop` iterations (count -> 2, one shy of the limit=3),
  # then a `resume` call: if resume incremented, this would halt (count=3).
  "$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context loop >/dev/null 2>&1
  "$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context loop >/dev/null 2>&1
  d7rout="$("$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context resume 2>&1)"
  d7rrc=$?
  [ "$d7rrc" = 0 ] && ok "doctor I7 (D6): a resume call never halts, even after 2 prior no-progress loop iterations" \
    || bad "doctor I7 (D6): a resume call never halts" "exit=$d7rrc out=$d7rout"
  # Follow it with TWO more `loop` iterations: if resume had reset the
  # counter as intended, this is only iterations 1-2 post-reset and must NOT
  # halt; if resume had (wrongly) incremented, the limit would already have
  # been blown past.
  "$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context loop >/dev/null 2>&1
  d7rout2="$("$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context loop 2>&1)"
  d7rrc2=$?
  [ "$d7rrc2" != 30 ] && ok "doctor I7 (D6): resume genuinely reset the counter (2 more loop iterations still don't halt)" \
    || bad "doctor I7 (D6): resume genuinely reset the counter" "exit=$d7rrc2 out=$d7rout2"

  # --- fail-closed: bad usage --------------------------------------------
  o="$("$SCRIPT" doctor --run-id x 2>&1)"
  [ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --dir' \
    && ok "doctor fail-closed: missing --dir" || bad "doctor fail-closed: missing --dir" "$o"
  o="$("$SCRIPT" doctor --dir "$D1/run" --run-id x --handler bogus 2>&1)"
  [ $? = 2 ] && printf '%s' "$o" | grep -q 'unknown --handler' \
    && ok "doctor fail-closed: unknown --handler" || bad "doctor fail-closed: unknown --handler" "$o"
  o="$("$SCRIPT" doctor --dir "$D1/run" --run-id x --context bogus 2>&1)"
  [ $? = 2 ] && printf '%s' "$o" | grep -q 'unknown --context' \
    && ok "doctor fail-closed: unknown --context" || bad "doctor fail-closed: unknown --context" "$o"
else
  echo "skip - doctor: git not available"
fi

# --- the notifier guard held (checked LAST, over the whole suite) -------------
# The acceptance criterion for the desktop-spam bug: no test, present or FUTURE,
# may reach a real notifier. Two assertions, because the guard has to be both
# IN PLACE and EFFECTIVE:
#   1. structural — `osascript`/`terminal-notifier` resolve INSIDE the guard dir,
#      so a binary outside it is unreachable by name for the whole suite. (Nothing
#      in spawn-orchestrator.sh calls a notifier by absolute path; `_alarm_notify`
#      goes through `command -v`, which is exactly what this shadows.)
#   2. behavioral — the guard log is NON-EMPTY, i.e. alarms really did route
#      through it. A guard that intercepted nothing would pass (1) while silently
#      having stopped covering the code path, which is how this leak would return.
guard_osa="$(command -v osascript || true)"
case "$guard_osa" in
  "$GUARD"/*) ok "notifier guard: osascript resolves INSIDE the guard dir, never the real binary" ;;
  *) bad "notifier guard: osascript resolves INSIDE the guard dir" "resolved to: ${guard_osa:-(not found)}" ;;
esac
guard_tn="$(command -v terminal-notifier || true)"
case "$guard_tn" in
  "$GUARD"/*) ok "notifier guard: terminal-notifier resolves INSIDE the guard dir" ;;
  *) bad "notifier guard: terminal-notifier resolves INSIDE the guard dir" "resolved to: ${guard_tn:-(not found)}" ;;
esac
#   3. STRUCTURAL — the checks above only see the INHERITED PATH. Every fixture
#      that OVERRIDES PATH (`PATH="$STUB_PATH" "$SCRIPT" …`) escapes them entirely:
#      if that PATH omits $GUARD and the stub dir has no osascript of its own, the
#      alarm resolves /usr/bin/osascript and pops a REAL notification on the
#      developer's desktop. Check (2) cannot see it — an escaped call never reaches
#      the guard log, so the count stays plausible and the suite stays green while
#      leaking. That is exactly how this leak survived: a test that asserts only
#      what it catches can never report what got away. So assert it at the source:
#      under EVERY composite PATH this suite builds, osascript must resolve to
#      something inside $BASE (a stub, or the guard) — never a real system binary.
for _pv in GT_PATH ALPATH STUB_PATH; do
  eval "_pval=\"\${$_pv:-}\""
  [ -n "$_pval" ] || continue
  _osa="$(PATH="$_pval" command -v osascript 2>/dev/null || true)"
  case "$_osa" in
    "$BASE"/*) ok "notifier guard: \$$_pv routes osascript inside the test tree, never a real binary" ;;
    *) bad "notifier guard: \$$_pv routes osascript to a REAL binary (a suite run would pop a desktop notification)" "resolved to: ${_osa:-(not found)}" ;;
  esac
done
# The two PATHs built inline rather than stored in a var (the failing-bootout and
# task-26 unwritable-sentinel fixtures) get the same assertion.
for _pv in "${STUBF:-}" "${ALFAIL:-}"; do
  [ -n "$_pv" ] || continue
  _osa="$(PATH="$_pv:$GUARD:/usr/bin:/bin" command -v osascript 2>/dev/null || true)"
  case "$_osa" in
    "$BASE"/*) ok "notifier guard: the inline stub PATH ${_pv##*/} routes osascript inside the test tree" ;;
    *) bad "notifier guard: the inline stub PATH ${_pv##*/} routes osascript to a REAL binary" "resolved to: ${_osa:-(not found)}" ;;
  esac
done

# --- status-report: the periodic heartbeat report (task 20, finding #28) -----
# Reuses this suite's existing conventions: real fixture dirs, a fake `gh`
# (flat-file DB, same shape as restack's), and everything driven through the
# REAL generated wrapper wherever the acceptance criteria demand it (NO model
# call; emitted on a gate-closed wake) — never a re-implementation of the
# wrapper's own call sequence.
SR="$BASE/status-report"
mkdir -p "$SR"

if command -v git >/dev/null 2>&1; then
  SR_REPO="$SR/repo"
  git init -q "$SR_REPO"
  git -C "$SR_REPO" config user.email test@example.com
  git -C "$SR_REPO" config user.name "Test"
  git -C "$SR_REPO" checkout -q -b main
  echo root >"$SR_REPO/root.txt"
  git -C "$SR_REPO" add root.txt
  git -C "$SR_REPO" commit -q -m root

  # branch_impl: a commit made "now" — comfortably inside a generous ceiling.
  git -C "$SR_REPO" checkout -q -b branch_impl
  echo impl >"$SR_REPO/impl.txt"
  git -C "$SR_REPO" add impl.txt
  git -C "$SR_REPO" commit -q -m "impl work"

  # branch_over: a commit stamped 1h ago — over a 2-minute ceiling, the
  # working-vs-wedged signal the report exists to surface.
  git -C "$SR_REPO" checkout -q main
  git -C "$SR_REPO" checkout -q -b branch_over
  echo over >"$SR_REPO/over.txt"
  git -C "$SR_REPO" add over.txt
  # An unambiguous "@<epoch> +0000" form — a bare "YYYY-MM-DDTHH:MM:SS" with no
  # zone is read by git as LOCAL time, which silently produced a commit git
  # thought was hours in the FUTURE on a non-UTC box (a real bug this exact
  # fixture caught once already).
  SR_OVER_EPOCH=$(($(date +%s) - 3600))
  GIT_AUTHOR_DATE="@$SR_OVER_EPOCH +0000" GIT_COMMITTER_DATE="@$SR_OVER_EPOCH +0000" \
    git -C "$SR_REPO" commit -q -m "over-ceiling work"

  git -C "$SR_REPO" checkout -q main
  for b in branch_handed branch_parked branch_child branch_claimed; do
    git -C "$SR_REPO" checkout -q -b "$b" main >/dev/null
    echo "$b" >"$SR_REPO/$b.txt"
    git -C "$SR_REPO" add "$b.txt"
    git -C "$SR_REPO" commit -q -m "$b"
  done
  # branch_fresh: claimed but NO commits beyond its base yet — every base..branch
  # range is empty, the shape whose old whole-history fallback selected the
  # repository's oldest commit ("running since repo genesis").
  git -C "$SR_REPO" branch branch_fresh main
  git -C "$SR_REPO" checkout -q main

  # Fake gh: same flat-file-DB shape as restack's fixture, extended with
  # `pr list --head <branch>` (the #23 divergence lookup status-report adds).
  SR_GHDB="$SR/ghdb"
  mkdir -p "$SR_GHDB"
  printf 'MERGED\n' >"$SR_GHDB/201.state"
  printf 'main\n' >"$SR_GHDB/201.base"
  printf 'UNKNOWN\n' >"$SR_GHDB/201.mergeable"
  printf '\n' >"$SR_GHDB/202.state"
  printf '' >"$SR_GHDB/202.base" # base ref deleted (LOUD orphan)
  printf 'OPEN\n' >"$SR_GHDB/203.state"
  printf '203\n' >"$SR_GHDB/list-branch_claimed.number"
  SR_GH="$SR/gh"
  cat >"$SR_GH" <<'GHEOF'
#!/usr/bin/env bash
set -uo pipefail
db="${SR_GHDB:?SR_GHDB not set}"
: >>"${SR_GH_CALLS:-/dev/null}"
echo "$*" >>"${SR_GH_CALLS:-/dev/null}"
[ "$1" = pr ] || exit 1
sub="$2"; shift 2
case "$sub" in
  view)
    num="$1"; shift
    jqexpr=""
    while [ $# -gt 0 ]; do case "$1" in --jq) jqexpr="$2"; shift 2 ;; *) shift ;; esac; done
    case "$jqexpr" in
      .baseRefName) f="$db/$num.base" ;;
      .state)       f="$db/$num.state" ;;
      .mergeable)   f="$db/$num.mergeable" ;;
      *) exit 1 ;;
    esac
    # Real `gh` contract (measured): a missing PR exits 1 with a GraphQL
    # "Could not resolve" line on STDERR — never exit 0 with empty output.
    if [ -f "$f" ]; then cat "$f"; else
      echo "GraphQL: Could not resolve to a PullRequest with the number of $num. (repository.pullRequest)" >&2
      exit 1
    fi
    ;;
  list)
    head=""
    while [ $# -gt 0 ]; do case "$1" in --head) head="$2"; shift 2 ;; *) shift ;; esac; done
    cat "$db/list-$head.number" 2>/dev/null || printf 'null\n'
    ;;
  *) exit 1 ;;
esac
GHEOF
  chmod +x "$SR_GH"
  export SR_GHDB

  # Fake claude-usage.sh: canned session-window JSON, no network.
  SR_USAGE="$SR/usage.sh"
  cat >"$SR_USAGE" <<'USEOF'
#!/usr/bin/env bash
printf '{"session":{"percent":42,"resets_at":"2099-01-01T00:00:00Z"},"weekly_all":{"percent":18,"resets_at":"2099-01-08T00:00:00Z"},"spend_used_minor":0}\n'
USEOF
  chmod +x "$SR_USAGE"

  # ISO-8601 UTC N seconds from now, portable BSD/GNU.
  _sr_iso() { date -u -v+"${1}"S +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "+${1} seconds" +%Y-%m-%dT%H:%M:%SZ; }

  SR_RUN="$SR/run"
  mkdir -p "$SR_RUN/.auto-pilot"
  _sr_write_run_md() {
    # $1: task_claimed's phase (claimed|implementing) so the delta test can
    # advance it; $2: task_over's branch elapsed baseline stays fixed.
    {
      printf -- '---\n'
      printf 'base_branch: main\n'
      printf 'until: %s\n' "$(_sr_iso 3600)"
      printf 'min_task_budget: 20m\n'
      printf -- '---\n\n'
      printf '| task | phase | branch | base | base_sha | pr | notes |\n'
      printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
      printf '| task_pending  | pending      | -             | main         | -   | -    | |\n'
      printf '| task_impl     | implementing | branch_impl   | main         | -   | -    | |\n'
      printf '| task_over     | implementing | branch_over   | main         | -   | -    | |\n'
      printf '| task_handed   | handed-off   | branch_handed | main         | -   | #201 | |\n'
      printf '| task_parked   | parked       | branch_parked | main         | -   | -    | |\n'
      printf '| task_child    | pr-open      | branch_child  | branch_parent| -   | #202 | |\n'
      printf '| task_claimed  | %s | branch_claimed | main       | -   | -    | |\n' "$1"
      printf '| task_fresh    | implementing | branch_fresh  | main         | -   | -    | |\n'
    } >"$SR_RUN/.auto-pilot/RUN.md"
  }
  _sr_write_run_md claimed
  # A git repo of its own (status-report reads/writes .auto-pilot/ and computes
  # `_run_head` from THIS dir, distinct from SR_REPO which supplies the task
  # branches for elapsed-time lookups).
  git init -q "$SR_RUN"
  git -C "$SR_RUN" config user.email test@example.com
  git -C "$SR_RUN" config user.name Test
  git -C "$SR_RUN" checkout -q -b auto-pilot/test-run
  git -C "$SR_RUN" add .auto-pilot/RUN.md
  git -C "$SR_RUN" commit -q -m "run state v1"

  # --- A: core rendering, direct call, --force (bypass the interval gate) ---
  srAout="$("$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 2>&1)"
  srAmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: renders the phase table" '| task_impl | implementing' "$srAmd"
  have "status-report: in-flight elapsed is reported" 'task_impl (implementing): elapsed' "$srAmd"
  have "status-report: OVER-ceiling task is flagged" 'task_over (implementing): elapsed' "$srAmd"
  have "status-report: OVER-ceiling task says OVER" 'OVER the per-task ceiling' "$srAmd"
  ! printf '%s\n' "$srAmd" | grep 'task_impl (implementing)' | grep -q 'OVER' \
    && ok "status-report: within-ceiling task is not marked OVER" \
    || bad "status-report: within-ceiling task is not marked OVER"
  # A freshly-claimed branch with NO commits beyond its base must degrade to
  # "elapsed unknown" — the old whole-history fallback selected the repo's
  # OLDEST commit and reported the task as running since repository genesis.
  have "status-report: a branch with no commits beyond base reads elapsed unknown, never repo genesis" \
    'task_fresh (implementing): elapsed unknown' "$srAmd"
  ! printf '%s\n' "$srAmd" | grep 'task_fresh (implementing)' | grep -q 'OVER' \
    && ok "status-report: the fresh branch is never marked OVER off the repo's first commit" \
    || bad "status-report: the fresh branch is never marked OVER off the repo's first commit"
  have "status-report: embeds status --label's own output" 'Live state (from `status' "$srAmd"
  have "status-report: heartbeat line present (from status)" 'heartbeat:' "$srAmd"
  have "status-report: PR state + mergeable for task_handed" '| task_handed | #201 | MERGED | UNKNOWN |' "$srAmd"
  have "status-report: rate window rendered from --usage-bin" 'session 42% consumed' "$srAmd"
  have "status-report: until remaining vs min_task_budget rendered" 'min_task_budget 20m' "$srAmd"
  have "status-report: until remaining is OK (plenty of runway)" 'OK, at least one more task likely fits' "$srAmd"
  have "status-report: first report says so (no prior state)" 'first report for this run' "$srAmd"

  # --- interval gate: a call within --report-every is a silent no-op ---------
  # (no --force). A long interval + an immediate re-call must NOT rewrite
  # STATUS.md — the whole point of "on by default, every 15m" is that most
  # wakes are cheap no-ops, not a fresh render each time.
  #
  # Assert on EXISTENCE, not on mtime/content: every timestamp available here
  # (stat mtime, last_emitted_at) is second-resolution, and these two calls are
  # sub-second apart — so an mtime comparison passes even with the gate deleted
  # outright (`if false; then`), which is exactly what the earlier version of
  # this test did. Deleting the file first makes the assertion capable of
  # failing: a gate that does not hold recreates it.
  rm -f "$SR_RUN/.auto-pilot/STATUS.md"
  "$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 --report-every 3600 >/dev/null 2>&1
  [ ! -e "$SR_RUN/.auto-pilot/STATUS.md" ] \
    && ok "status-report: a call inside --report-every (no --force) does not rewrite STATUS.md" \
    || bad "status-report: the interval gate did not hold — STATUS.md was rewritten early" "STATUS.md was recreated inside the interval"

  # --- B: the delta — no forward progress vs a phase advance -----------------
  srBout="$("$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 2>&1)"
  srBmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: no-forward-progress fires when the run-state HEAD hasn't moved" \
    'no forward progress in' "$srBmd"
  have "status-report: the no-forward-progress line names the in-flight task + ceiling" \
    'task_impl (implementing): elapsed' "$(printf '%s\n' "$srBmd" | grep 'no forward progress')"

  # Now advance task_claimed's phase and commit — the run-state HEAD moves.
  _sr_write_run_md implementing
  git -C "$SR_RUN" add .auto-pilot/RUN.md
  git -C "$SR_RUN" commit -q -m "task_claimed -> implementing"
  srCout="$("$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 2>&1)"
  srCmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: a phase advance is named in the delta" \
    'changed: task_claimed claimed -> implementing' "$srCmd"
  lack "status-report: a phase advance does NOT also claim no-forward-progress" \
    'no forward progress' "$srCmd"

  # --- B2: task-branch commits ARE forward progress (no false stall) ---------
  # RUN.md is committed only when /deliver-task returns; `implementing` means
  # local task-branch commits with the run-state HEAD parked. A task actively
  # producing commits must NOT be reported as stalled — compare the persisted
  # branch tips, not just the run-state HEAD.
  git -C "$SR_REPO" checkout -q branch_impl
  echo more >>"$SR_REPO/impl.txt"
  git -C "$SR_REPO" commit -q -am "impl keeps working"
  git -C "$SR_REPO" checkout -q main
  "$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 >/dev/null 2>&1
  srDmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: run-state HEAD parked + task-branch commits is NOT a stall" \
    'no forward progress' "$srDmd"
  have "status-report: the branch progress is named (which task advanced)" \
    'task branch(es) advanced — new commits on: task_impl' "$srDmd"
  # ...and a genuinely idle interval right after IS still a stall (the tip
  # comparison must not have destroyed the true-positive).
  "$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 >/dev/null 2>&1
  srEmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: a genuinely idle interval still reads no-forward-progress" \
    'no forward progress' "$srEmd"

  # --- D: stale RUN.md cannot read as healthy (finding #23's shape) ----------
  have "status-report: flags RUN.md lagging reality (claimed phase, live PR already open)" \
    'DIVERGENCE task_claimed' "$srAmd"
  have "status-report: divergence names the live PR" 'PR #203' "$srAmd"

  # --- E: reuses restack's OWN orphan detector, not a second reconciler ------
  have "status-report: flags the orphaned chained PR (base ref deleted, finding #25 LOUD case)" \
    'DEFECT task_child' "$srAmd"

  # --- E2: a FAILING gh must read DEGRADED, never clean ----------------------
  # Expired auth / rate limit / network: `gh` exits non-zero with empty
  # stdout. With no chained PRs the orphan scan finds nothing and the #23
  # lookup comes back empty — the old code then rendered "clean", blessing a
  # stale RUN.md it never actually cross-checked.
  SR_FAILGH="$SR/gh-failing"
  printf '#!/bin/sh\necho "HTTP 401: Bad credentials (https://api.github.com/graphql)" >&2\nexit 1\n' >"$SR_FAILGH"
  chmod +x "$SR_FAILGH"
  SR_BRUN="$SR/degraded-run"
  mkdir -p "$SR_BRUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_claimed | claimed | branch_claimed | main | - | - | |\n'
  } >"$SR_BRUN/.auto-pilot/RUN.md"
  git init -q "$SR_BRUN"
  git -C "$SR_BRUN" config user.email t@e
  git -C "$SR_BRUN" config user.name T
  git -C "$SR_BRUN" checkout -q -b auto-pilot/degraded-run
  git -C "$SR_BRUN" add .auto-pilot/RUN.md
  git -C "$SR_BRUN" commit -q -m "run state"
  srXout="$("$SCRIPT" status-report --dir "$SR_BRUN" --label com.autopilot.sr.deg --force \
    --repo "$SR_REPO" --gh "$SR_FAILGH" --task-ceiling 120 2>&1)"
  srXmd="$(cat "$SR_BRUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: a failing gh never renders the reality check as clean" \
    'clean: no divergence' "$srXmd"
  have "status-report: a failing gh renders the reality check as DEGRADED" \
    'DEGRADED:' "$srXmd"
  have "status-report: the DEGRADED result reaches the one-line digest too" \
    'reality=DEGRADED' "$srXout"
  # ...and a transient failure against a CHAINED PR is also degraded — never a
  # false DEFECT (the restack scan's UNDETERMINED path, seen from the report).
  SR_CRUN="$SR/degraded-chained"
  mkdir -p "$SR_CRUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_chained | pr-open | branch_child | branch_parent | - | #299 | |\n'
  } >"$SR_CRUN/.auto-pilot/RUN.md"
  git init -q "$SR_CRUN"
  git -C "$SR_CRUN" config user.email t@e
  git -C "$SR_CRUN" config user.name T
  git -C "$SR_CRUN" checkout -q -b auto-pilot/degraded-chained
  git -C "$SR_CRUN" add .auto-pilot/RUN.md
  git -C "$SR_CRUN" commit -q -m "run state"
  "$SCRIPT" status-report --dir "$SR_CRUN" --label com.autopilot.sr.degc --force \
    --repo "$SR_REPO" --gh "$SR_FAILGH" --task-ceiling 120 >/dev/null 2>&1
  srYmd="$(cat "$SR_CRUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: a transient gh failure on a chained PR is never a DEFECT" \
    'DEFECT' "$srYmd"
  have "status-report: the chained transient failure reads DEGRADED instead" \
    'DEGRADED:' "$srYmd"

  # --- E2c: the ORDINARY shape must degrade too — the gap E2 above missed ----
  # Both fixtures above happen to make a gh call the report already counts: a
  # `claimed` task hits the #23 divergence lookup, a CHAINED task hits the
  # orphan scan. Neither covers the everyday run: tasks in `pr-open` based
  # DIRECTLY on base_branch. There the orphan scan skips every task (unchained)
  # and the divergence loop skips every task (wrong phase), so the ONLY gh calls
  # are the PR table's state/mergeable queries — and while those discarded their
  # exit status, a totally dead gh made zero TRACKED calls: the table rendered
  # `unknown/unknown` and the reality check still asserted "clean". Fail-open,
  # in the single section this whole feature exists to make trustworthy.
  SR_ORUN="$SR/degraded-ordinary"
  mkdir -p "$SR_ORUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_open | pr-open | branch_handed | main | - | #201 | |\n'
  } >"$SR_ORUN/.auto-pilot/RUN.md"
  git init -q "$SR_ORUN"
  git -C "$SR_ORUN" config user.email t@e
  git -C "$SR_ORUN" config user.name T
  git -C "$SR_ORUN" checkout -q -b auto-pilot/degraded-ordinary
  git -C "$SR_ORUN" add .auto-pilot/RUN.md
  git -C "$SR_ORUN" commit -q -m "run state"
  srZout="$("$SCRIPT" status-report --dir "$SR_ORUN" --label com.autopilot.sr.deg0 --force \
    --repo "$SR_REPO" --gh "$SR_FAILGH" --task-ceiling 120 2>&1)"
  srZmd="$(cat "$SR_ORUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: a dead gh on an ordinary pr-open run never reads clean" \
    'clean: no divergence' "$srZmd"
  have "status-report: a dead gh on an ordinary pr-open run reads DEGRADED" \
    'DEGRADED:' "$srZmd"
  have "status-report: the ordinary-shape degrade reaches the digest" \
    'reality=DEGRADED' "$srZout"

  # --- E2d: the Open-PRs table prints its header ONCE, not once per row ------
  # printf recycles its whole format string per argument, so folding the header
  # into the row format re-printed it before EVERY row. The fixture has two PRs
  # (#201, #202), so the table was mangled in the artifact a human reads while
  # the suite's single-row substring assert sailed past it.
  srA_hdrs="$(printf '%s\n' "$srAmd" | grep -c '^| task | pr | state | mergeable |$' || true)"
  [ "$srA_hdrs" = 1 ] \
    && ok "status-report: the Open-PRs table prints its header exactly once (2 PRs in the fixture)" \
    || bad "status-report: the Open-PRs table header is repeated per row (printf format recycling)" \
      "header printed $srA_hdrs times, expected 1"

  # --- E2e: the stall duration ACCUMULATES; it is not the report interval ----
  # "no forward progress in X" measured X from last_emitted_at — rewritten on
  # every emit — so a run wedged for six hours read "no forward progress in
  # 15m" at every single tick. The number that separates slow from wedged could
  # never say so. It now measures from last_progress_at, carried forward across
  # reports that see nothing move.
  SR_SRUN="$SR/stall-run"
  mkdir -p "$SR_SRUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_stuck | implementing | branch_over | main | - | - | |\n'
  } >"$SR_SRUN/.auto-pilot/RUN.md"
  git init -q "$SR_SRUN"
  git -C "$SR_SRUN" config user.email t@e
  git -C "$SR_SRUN" config user.name T
  git -C "$SR_SRUN" checkout -q -b auto-pilot/stall-run
  git -C "$SR_SRUN" add .auto-pilot/RUN.md
  git -C "$SR_SRUN" commit -q -m "run state"
  SR_SSTATE="$SR_SRUN/.auto-pilot/status-report-state"
  # First report seeds the state; nothing has moved since, so the next one is a
  # genuine stall.
  "$SCRIPT" status-report --dir "$SR_SRUN" --label com.autopilot.sr.stall --force \
    --repo "$SR_REPO" --task-ceiling 120 >/dev/null 2>&1
  # Backdate the moment the run last MOVED to 2h ago, leaving last_emitted_at
  # recent — exactly the state an overnight wedge produces after many reports.
  SR_STALL_SINCE=$(($(date +%s) - 7200))
  sed -e "s/^last_progress_at: .*/last_progress_at: $SR_STALL_SINCE/" "$SR_SSTATE" >"$SR_SSTATE.tmp"
  mv "$SR_SSTATE.tmp" "$SR_SSTATE"
  "$SCRIPT" status-report --dir "$SR_SRUN" --label com.autopilot.sr.stall --force \
    --repo "$SR_REPO" --task-ceiling 120 >/dev/null 2>&1
  srSmd="$(cat "$SR_SRUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: a 2h stall reports 2h, not the report interval" \
    'no forward progress in 2h' "$srSmd"
  # ...and the clock is NOT reset by the act of reporting: the carried-forward
  # last_progress_at must survive, or the stall restarts from zero every emit.
  grep -q "^last_progress_at: $SR_STALL_SINCE$" "$SR_SSTATE" \
    && ok "status-report: emitting a report does not reset the stall clock" \
    || bad "status-report: the stall clock was reset by the emission" \
      "expected last_progress_at: $SR_STALL_SINCE, got $(sed -n 's/^last_progress_at: //p' "$SR_SSTATE")"

  # --- E3: duration parsing accepts digits only, never arithmetic ------------
  # `--report-every '1+1m'` used to reach bash arithmetic and be ACCEPTED —
  # the contract is off | integer seconds | <n>s|m|h, nothing else.
  #
  # `0` (and `0s`/`0m`) is in this list for a different reason than the
  # arithmetic cases: it PARSES fine, it just isn't a duration. It bakes
  # `sleep 0` into the generated wrapper's in-wake reporter — a busy-loop that
  # spins a CPU and re-queries GitHub continuously for the whole model call,
  # since the interval gate can never close against 0 either. `off` is how you
  # say "never"; there is no way to say "always".
  for badv in '1+1m' '+5s' '2 2h' '0' '0s' '0m'; do
    bdout="$("$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.bad \
      --report-every "$badv" 2>&1)"
    bdc=$?
    [ "$bdc" = 2 ] && printf '%s' "$bdout" | grep -qF 'must be off' \
      && ok "status-report: --report-every '$badv' is rejected (digits only, no arithmetic)" \
      || bad "status-report: --report-every '$badv' is rejected (digits only, no arithmetic)" "exit=$bdc $bdout"
  done

  # --- E4: a QUOTED front-matter `until:` still parses ------------------------
  # The other front-matter readers strip wrapping quotes; the report's reader
  # must too, or `until: "2026-…"` renders as "unparseable until".
  SR_QRUN="$SR/quoted-run"
  mkdir -p "$SR_QRUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf 'until: "%s"\n' "$(_sr_iso 7200)"
    printf "min_task_budget: '20m'\n"
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_pending | pending | - | main | - | - | |\n'
  } >"$SR_QRUN/.auto-pilot/RUN.md"
  git init -q "$SR_QRUN"
  git -C "$SR_QRUN" config user.email t@e
  git -C "$SR_QRUN" config user.name T
  git -C "$SR_QRUN" checkout -q -b auto-pilot/quoted-run
  git -C "$SR_QRUN" add .auto-pilot/RUN.md
  git -C "$SR_QRUN" commit -q -m "run state"
  "$SCRIPT" status-report --dir "$SR_QRUN" --label com.autopilot.sr.q --force \
    --repo "$SR_REPO" --task-ceiling 120 >/dev/null 2>&1
  srQmd="$(cat "$SR_QRUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: a quoted until: does not read as unparseable" \
    'unparseable until' "$srQmd"
  # The budget note only renders when until_epoch actually PARSED — so this is
  # the effect assertion that the quotes were stripped (2h runway vs 20m).
  have "status-report: a quoted until:+min_task_budget still yields the runway verdict" \
    'OK, at least one more task likely fits' "$srQmd"

  # --- E5: writes are atomic IN the destination dir ---------------------------
  # Temp files must be created in .auto-pilot/ and renamed (the
  # _write_done_sentinel pattern) — a TMPDIR temp + cross-filesystem `mv` is a
  # copy+delete a watcher can observe half-written. Effect asserted: with an
  # UNUSABLE TMPDIR the report must still succeed, because nothing on its
  # write path may depend on TMPDIR at all.
  SR_ARUN="$SR/atomic-run"
  rm -rf "$SR_ARUN"
  cp -R "$SR_BRUN" "$SR_ARUN"
  rm -f "$SR_ARUN/.auto-pilot/status-report-state" "$SR_ARUN/.auto-pilot/STATUS.md"
  atout="$(TMPDIR="$SR/definitely-nonexistent-tmp" "$SCRIPT" status-report --dir "$SR_ARUN" \
    --label com.autopilot.sr.atomic --force --repo "$SR_REPO" --task-ceiling 120 2>&1)"
  atc=$?
  [ "$atc" = 0 ] && [ -f "$SR_ARUN/.auto-pilot/STATUS.md" ] \
    && ok "status-report: succeeds with an unusable TMPDIR (temp files live in .auto-pilot/, renamed in place)" \
    || bad "status-report: succeeds with an unusable TMPDIR (temp files live in .auto-pilot/, renamed in place)" "exit=$atc $atout"
  leftover="$(find "$SR_ARUN/.auto-pilot" -name '.status-report*' 2>/dev/null)"
  [ -z "$leftover" ] && ok "status-report: no temp-file droppings left beside the report" \
    || bad "status-report: no temp-file droppings left beside the report" "$leftover"

  # --- F: no live gh/usage-bin call when the caller doesn't opt in -----------
  SR_LEAK="$SR/leak-bin"
  mkdir -p "$SR_LEAK"
  SR_GH_CALLED="$SR/gh-leak-called"
  printf '#!/bin/sh\n: >"%s"\nexit 1\n' "$SR_GH_CALLED" >"$SR_LEAK/gh"
  chmod +x "$SR_LEAK/gh"
  SR_USAGE_CALLED="$SR/usage-leak-called"
  printf '#!/bin/sh\n: >"%s"\nexit 1\n' "$SR_USAGE_CALLED" >"$SR_LEAK/claude-usage.sh"
  chmod +x "$SR_LEAK/claude-usage.sh"
  rm -f "$SR_GH_CALLED" "$SR_USAGE_CALLED"
  PATH="$SR_LEAK:$PATH" "$SCRIPT" supervisor-scan --dir "$SR_RUN" --label com.autopilot.sr.f \
    --report-every off >/dev/null 2>&1
  [ ! -f "$SR_GH_CALLED" ] && ok "status-report: no --gh means NO gh call is made, even if one resolves on PATH" \
    || bad "status-report: a real/PATH-resolved gh was invoked despite no --gh being passed"
  # (--report-every off above also proves the disabled path never touches gh/usage
  # at all; a second case with reporting ON but --gh/--usage-bin both omitted:)
  PATH="$SR_LEAK:$PATH" "$SCRIPT" supervisor-scan --dir "$SR_RUN" --label com.autopilot.sr.f2 \
    --report-every 1 >/dev/null 2>&1
  [ ! -f "$SR_GH_CALLED" ] && ok "status-report: reporting ON but --gh omitted still makes no gh call" \
    || bad "status-report: reporting ON but --gh omitted still called a PATH-resolved gh"
  [ ! -f "$SR_USAGE_CALLED" ] && ok "status-report: --usage-bin omitted never calls a PATH-resolved claude-usage.sh" \
    || bad "status-report: --usage-bin omitted called a PATH-resolved claude-usage.sh"

  # --- G: a status-report failure can't take supervisor-scan down with it ----
  # (die/exit containment — see the "task 26" convention comment). A garbage
  # --report-every makes status_report `die`; supervisor-scan must still exit 0.
  "$SCRIPT" supervisor-scan --dir "$SR_RUN" --label com.autopilot.sr.g --report-every 'garbage' >/dev/null 2>&1
  [ $? = 0 ] && ok "status-report: a die inside status_report does not propagate out of supervisor-scan" \
    || bad "status-report: supervisor-scan's exit code leaked status_report's die"

  # --- G2: a HUNG gh must not wedge the wake ---------------------------------
  # The subshell above contains a `die` (an EXIT). It does NOT contain a HANG.
  # status_report is the ONLY thing on the supervisor's per-wake path that makes
  # network calls, and `launch` auto-resolves a real `gh`, so production wakes do
  # reach the network. launchd will not start the next StartInterval wake while
  # this one is still running — so ONE hung `gh` (blackholed TCP, captive portal,
  # an auth prompt) wedges the supervisor PERMANENTLY: no agent, and no further
  # alarm scans or pause-exempt-ledger checks either. Finding #22's silent
  # zero-work loop, reached through the OBSERVABILITY feature. Bounded by a
  # hand-rolled watchdog (macOS ships no coreutils `timeout`).
  SR_HANG="$SR/hangbin"
  mkdir -p "$SR_HANG"
  printf '#!/bin/sh\nsleep 120\n' >"$SR_HANG/gh"
  chmod +x "$SR_HANG/gh"
  # A FRESH run dir, with no prior status-report-state: reusing $SR_RUN let the
  # interval gate SKIP the report entirely, so the hang never happened and the
  # timing assertion passed in 0s while proving nothing. (Caught only because the
  # "announced" assertion below went red — an elapsed-time bound is satisfied just
  # as well by never running the thing.)
  SR_HRUN="$SR/hangrun"
  rm -rf "$SR_HRUN"
  cp -R "$SR_RUN" "$SR_HRUN"
  rm -f "$SR_HRUN/.auto-pilot/status-report-state" "$SR_HRUN/.auto-pilot/STATUS.md" 2>/dev/null
  srh_start="$(date +%s)"
  PATH="$GUARD:$PATH" SPAWN_REPORT_TIMEOUT=2 "$SCRIPT" supervisor-scan --dir "$SR_HRUN" \
    --label com.autopilot.sr.hang --report-every 1 --gh "$SR_HANG/gh" >/dev/null 2>"$SR/hang.err"
  srh_rc=$?
  srh_el=$(($(date +%s) - srh_start))
  [ "$srh_rc" = 0 ] && ok "status-report [hung gh]: supervisor-scan still exits 0 (the wake completes)" \
    || bad "status-report [hung gh]: supervisor-scan did not complete" "rc=$srh_rc"
  [ "$srh_el" -lt 30 ] \
    && ok "status-report [hung gh]: the wake is BOUNDED (${srh_el}s), not wedged until the hang ends" \
    || bad "status-report [hung gh]: the wake WEDGED — a hung gh stops every future alarm scan and ledger check" "elapsed=${srh_el}s"
  have "status-report [hung gh]: the kill is announced, not silent" \
    'status-report exceeded' "$(cat "$SR/hang.err" 2>/dev/null)"
  # and the hung gh must not survive as an orphan, accumulating one per interval
  sleep 1
  if pgrep -f "$SR_HANG/gh" >/dev/null 2>&1; then
    bad "status-report [hung gh]: the killed report left an ORPHANED gh running" "pgrep matched"
  else
    ok "status-report [hung gh]: the whole process group is reaped — no orphaned gh"
  fi

  # --- C: NO MODEL CALL, and still emitted on a GATE-CLOSED wake --------------
  # Driven through the REAL generated wrapper (write-launch), never a
  # reimplementation of its call sequence — same discipline as the gate tests
  # above. paused_until is an hour in the future, so the gate closes and
  # `claude` must never run; the report must still be written.
  SRW="$SR/wrapper"
  mkdir -p "$SRW/.auto-pilot" "$SRW/bin"
  cp "$SR_RUN/.auto-pilot/RUN.md" "$SRW/.auto-pilot/RUN.md"
  # RUN.md needs its own paused_until for the gate; append it to the front matter.
  SRW_FUTURE="$(_sr_iso 3600)"
  awk -v p="$SRW_FUTURE" '
    /^---$/ { c++; if (c==2 && !done) { print "paused_until: " p; done=1 } }
    { print }
  ' "$SRW/.auto-pilot/RUN.md" >"$SRW/.auto-pilot/RUN.md.tmp" && mv "$SRW/.auto-pilot/RUN.md.tmp" "$SRW/.auto-pilot/RUN.md"
  SRW_CLAUDE="$SRW/bin/claude-stub"
  printf '#!/bin/sh\n: >"%s/claude-called"\nexit 0\n' "$SRW" >"$SRW_CLAUDE"
  chmod +x "$SRW_CLAUDE"
  SRW_SANDBOX="$SRW/bin/sandbox-exec"
  printf '#!/bin/sh\n: >"%s/sandbox-exec-called"\nshift 2\nexec "$@"\n' "$SRW" >"$SRW_SANDBOX"
  chmod +x "$SRW_SANDBOX"
  SRW_PATH="$SRW/bin:$GUARD:/usr/bin:/bin:/usr/sbin:/sbin"
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$SRW" \
    --log "$SRW/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.sr.wrap \
    --claude-bin "$SRW_CLAUDE" --path "$SRW_PATH" --report-every 1 --report-gh "$SR_GH" \
    --out-script "$SRW/launch.sh" --out-plist "$SRW/job.plist" >/dev/null 2>&1
  "$SRW/launch.sh" >/dev/null 2>&1
  [ ! -f "$SRW/claude-called" ] && ok "status-report: gate-closed wake never invokes claude (no model call)" \
    || bad "status-report: gate-closed wake invoked claude"
  [ -f "$SRW/.auto-pilot/STATUS.md" ] && ok "status-report: STILL emitted on a gate-closed wake" \
    || bad "status-report: STATUS.md missing after a gate-closed wake"
  have "status-report: the digest reaches the wake's log too" 'status-report:' "$(cat "$SRW/o.log" 2>/dev/null)"

  # --- H: reports keep firing WHILE claude runs (review [A]) ------------------
  # launchd does NOT fire StartInterval while an instance is still running
  # (verified empirically: StartInterval=2s + a 12s job → starts only every
  # ~14s, no concurrency, no queued firings). So the wake-start emission alone
  # goes silent for the whole duration of a model call — a wedged claude means
  # ZERO reports, on exactly the runs the report exists to monitor. The
  # generated wrapper must therefore run an in-wake background reporter while
  # claude runs, and reap it when claude exits. Driven through the REAL
  # generated wrapper, gate OPEN, with a claude that takes 3s and a 1s
  # interval: more than one report digest must land in the log.
  SRL="$SR/loop-wrapper"
  mkdir -p "$SRL/.auto-pilot" "$SRL/bin"
  cp "$SR_RUN/.auto-pilot/RUN.md" "$SRL/.auto-pilot/RUN.md" # no paused_until: gate OPEN
  SRL_CLAUDE="$SRL/bin/claude-slow"
  printf '#!/bin/sh\n: >"%s/claude-called"\nsleep 3\nexit 0\n' "$SRL" >"$SRL_CLAUDE"
  chmod +x "$SRL_CLAUDE"
  printf '#!/bin/sh\nshift 2\nexec "$@"\n' >"$SRL/bin/sandbox-exec"
  chmod +x "$SRL/bin/sandbox-exec"
  SRL_PATH="$SRL/bin:$GUARD:/usr/bin:/bin:/usr/sbin:/sbin"
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$SRL" \
    --log "$SRL/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.sr.loop \
    --claude-bin "$SRL_CLAUDE" --path "$SRL_PATH" --report-every 1 \
    --out-script "$SRL/launch.sh" --out-plist "$SRL/job.plist" >/dev/null 2>&1
  # The seam, pinned by position: the reporter starts BELOW the gate (a
  # gate-closed wake exits in seconds; launchd's own cadence covers it) and
  # BEFORE the claude invocation; the reap follows the claude invocation.
  srl_gate_ln="$(grep -n 'supervisor-gate' "$SRL/launch.sh" | head -1 | cut -d: -f1)"
  srl_loop_ln="$(grep -n 'report-tick' "$SRL/launch.sh" | head -1 | cut -d: -f1)"
  srl_claude_ln="$(grep -n 'sandbox-exec -f' "$SRL/launch.sh" | head -1 | cut -d: -f1)"
  srl_kill_ln="$(grep -n 'kill -TERM -"$rpt"' "$SRL/launch.sh" | head -1 | cut -d: -f1)"
  if [ -n "$srl_gate_ln" ] && [ -n "$srl_loop_ln" ] && [ -n "$srl_claude_ln" ] && [ -n "$srl_kill_ln" ] \
    && [ "$srl_gate_ln" -lt "$srl_loop_ln" ] && [ "$srl_loop_ln" -lt "$srl_claude_ln" ] \
    && [ "$srl_claude_ln" -lt "$srl_kill_ln" ]; then
    ok "status-report [in-wake]: reporter sits below the gate, brackets the claude invocation, reap follows it"
  else
    bad "status-report [in-wake]: reporter sits below the gate, brackets the claude invocation, reap follows it" \
      "gate@$srl_gate_ln loop@$srl_loop_ln claude@$srl_claude_ln kill@$srl_kill_ln"
  fi
  "$SRL/launch.sh" >/dev/null 2>&1
  [ -f "$SRL/claude-called" ] && ok "status-report [in-wake]: gate was OPEN — claude really ran (3s)" \
    || bad "status-report [in-wake]: gate was OPEN — claude really ran (3s)"
  srl_digests="$(grep -c 'status-report: tasks=' "$SRL/o.log" 2>/dev/null | tr -d ' ')"
  case "$srl_digests" in '' | *[!0-9]*) srl_digests=0 ;; esac
  if [ "$srl_digests" -ge 2 ]; then
    ok "status-report [in-wake]: reports kept firing DURING the model call ($srl_digests digests, wake-start alone would be 1)"
  else
    bad "status-report [in-wake]: reports kept firing DURING the model call" "digests=$srl_digests (only the wake-start emission fired — the [A] cadence hole)"
  fi
  # Poll to a deadline rather than sleeping a fixed second. The reap is a
  # process-GROUP TERM followed by a wait (`kill -TERM -"$rpt"; wait "$rpt"` in
  # spawn-orchestrator.sh), so a report-tick caught mid gh/status-report call is
  # still draining when write-launch returns. A flat `sleep 1` races that drain
  # and fails on a loaded machine for a reason that has nothing to do with the
  # property under test — observed flapping pass/fail across runs with no code
  # change. The claim is "eventually reaped", so bound the wait instead: still
  # matching after 10s is a real leaked reaper, which is what this should catch.
  # NOTE: this match is argv-based, and argv is only INHERITED across a fork —
  # it is REPLACED by exec. A plain fork (like the command-substitution and
  # job-control subshells report-tick spawns on its way to _run_bounded's job
  # and watchdog) still carries this argv, so this pgrep does happen to reach
  # them today. But anything downstream that execs (a `sleep`, a real `gh`)
  # stops matching the instant it execs, even while it is still alive and
  # still a descendant. This assertion is therefore NOT what actually covers
  # _run_bounded's job/watchdog/TERM-handler teardown — the PGID-based direct
  # test in the "reaper teardown" section below is what covers that
  # descendant set, by identity rather than by argv. The 10s here is a budget
  # for the reap of report-tick itself; spend against it knowingly.
  srl_reaped=0
  srl_tries=0
  while [ "$srl_tries" -lt 50 ]; do
    if ! pgrep -f "report-tick --dir $SRL" >/dev/null 2>&1; then
      srl_reaped=1
      break
    fi
    sleep 0.2
    srl_tries=$((srl_tries + 1))
  done
  if [ "$srl_reaped" = 1 ]; then
    ok "status-report [in-wake]: the reporter loop is reaped when claude exits"
  else
    bad "status-report [in-wake]: the reporter loop is reaped when claude exits" "pgrep still matched after 10s"
  fi
  # --report-every off must emit NO reporter loop at all.
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$SRL" \
    --log "$SRL/off.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.sr.loopoff \
    --claude-bin "$SRL_CLAUDE" --path "$SRL_PATH" --report-every off \
    --out-script "$SRL/launch-off.sh" --out-plist "$SRL/job-off.plist" >/dev/null 2>&1
  lack "status-report [in-wake]: --report-every off emits no reporter loop" \
    'report-tick' "$(cat "$SRL/launch-off.sh" 2>/dev/null)"
else
  echo "skip - status-report suite (no git available)"
fi

# --- reaper teardown: _run_bounded's TERM/INT/HUP trap (#264) -----------------
#
# Background: _run_bounded opens a NESTED `set -m` window, so the bounded job
# and its watchdog land in process groups of their OWN, outside the caller's
# group. An outer reaper (`kill -TERM -"$rpt"`, the exact shape the generated
# wrapper uses on report-tick) hits the parent shell but NOT those children
# unless the parent installs its own trap and forwards the signal — without
# it the parent shell dies blocked in `wait`, cleanup never runs, both
# children re-parent to init, and the watchdog lingers its full ~62s firing
# `gh` at a run that has already ended. That was fixed by the TERM/INT/HUP
# trap at the top of _run_bounded, which itself has ZERO test coverage: two
# wrong versions of it passed the whole suite green while still leaking, and
# the only thing that ever caught the leak was an ad-hoc `ps` watch run
# alongside the suite. This is the deterministic replacement for that watch.
#
# Design constraints, each load-bearing:
#   - The watchdog stays at its PRODUCTION default (60s, no SPAWN_REPORT_TIMEOUT
#     override). Shrinking it would make this test VACUOUS: an orphaned
#     watchdog still runs, so with e.g. a 2s timeout it would TERM-then-KILL
#     the job on its own at ~4s regardless of whether the trap works, and any
#     assertion deadline above that would see a clean process table and pass
#     even when the teardown is completely broken. We instead assert teardown
#     within a SHORT 10s deadline against a 60s watchdog — the 60≫10 gap is
#     exactly what makes a leak observable instead of masked.
#   - Processes are identified by PROCESS GROUP, never by PPID (re-parenting
#     to init on a leak is the bug itself, so PPID cannot be the signal) and
#     never by argv (inherited across `fork`, but REPLACED by `exec` — an
#     exec'd `sleep`/`gh` can survive while no longer matching any argv
#     pattern; see the corrected comment above the status-report [in-wake]
#     reap for the bug this file used to have).
#   - Every wait below is a bounded poll on an observable event, never a bare
#     `sleep` — that is what made the OLD reaper coverage read as flaky.
RTD="$BASE/reaper-teardown"
mkdir -p "$RTD"

# One shared RUN.md fixture: a single task in phase `handed-off` with a PR
# number set. That is enough to make `status_report` place a REAL, BLOCKING
# call through `--gh` (the pr-view loop at the top of the PR/reconciliation
# section) without needing a git repo at all — `status` and
# `_restack_read_run_md` only ever read this file as text. `handed-off` (not
# `pr-open`/`implementing`) is deliberate: it is NOT in the `in-flight`
# bucket, so the elapsed/tip-tracking loops above the gh calls skip it
# cleanly instead of trying (and harmlessly failing) git lookups against a
# non-repo dir.
mkdir -p "$RTD/.auto-pilot"
{
  printf -- '---\n'
  printf 'base_branch: main\n'
  printf -- '---\n\n'
  printf '| task | phase | branch | base | base_sha | pr | notes |\n'
  printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
  printf '| task_pr | handed-off | branch_pr | main | - | #201 | |\n'
} >"$RTD/.auto-pilot/RUN.md"

# A usage-bin stub that returns instantly (not the thing under test — just
# has to exist so status_report's optional usage section does not skip).
RTD_USAGE="$RTD/usage.sh"
printf '#!/bin/sh\nprintf "{}\\n"\n' >"$RTD_USAGE"
chmod +x "$RTD_USAGE"

# Snapshot -> BFS the transitive PPID closure from a root pid, returning every
# DISTINCT pgid found (one per line). This is the identity this whole section
# is built on: never PPID (flips to 1 on the leak), never argv (lost on exec).
_rtd_pgids_from() {
  local root="$1" snap
  snap="$(ps -Ao pid,ppid,pgid,stat,etime,command 2>/dev/null | tail -n +2)"
  local -a queue=("$root") seen=() pgids=()
  while [ "${#queue[@]}" -gt 0 ]; do
    local cur="${queue[0]}"
    queue=("${queue[@]:1}")
    case " ${seen[*]-} " in *" $cur "*) continue ;; esac
    seen+=("$cur")
    local line
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      local pid ppid pgid
      # shellcheck disable=SC2086 # deliberate word-split: pid/ppid/pgid are
      # the first three whitespace-separated fields; trailing words (stat,
      # etime, the command line) are discarded here and re-read via awk when
      # a failure needs to dump full rows.
      set -- $line
      pid="$1"
      ppid="$2"
      pgid="$3"
      if [ "$pid" = "$cur" ]; then
        case " ${pgids[*]-} " in *" $pgid "*) ;; *) pgids+=("$pgid") ;; esac
      fi
      if [ "$ppid" = "$cur" ]; then
        queue+=("$pid")
      fi
    done <<EOF
$snap
EOF
  done
  printf '%s\n' "${pgids[@]}"
}

# Dump every surviving row whose pgid (column 3, exact match) is in the
# recorded set — for a FAIL message, never for the pass path.
_rtd_dump_rows() {
  local pgid_list="$1" snap row pg
  snap="$(ps -Ao pid,ppid,pgid,stat,etime,command 2>/dev/null | tail -n +2)"
  while IFS= read -r row; do
    [ -n "$row" ] || continue
    pg="$(printf '%s\n' "$row" | awk '{print $3}')"
    case " $pgid_list " in *" $pg "*) printf '%s\n' "$row" ;; esac
  done <<EOF
$snap
EOF
}

# Group-kill every recorded pgid, best-effort. Called unconditionally at the
# end of each iteration (pass or fail) so a red run does not leave the very
# orphan it just detected running on the machine, and does not bleed into a
# later iteration or the end-of-suite sweep (Part 2, below).
_rtd_kill_pgids() {
  local pg
  for pg in $1; do
    kill -KILL -"$pg" 2>/dev/null
  done
}

for rtd_kind in compliant stubborn; do
  RTDK="$RTD/$rtd_kind"
  mkdir -p "$RTDK/bin"

  # Two `--gh` stubs, differing only in TERM disposition:
  #   - compliant: default disposition, so a TERM handed to its process group
  #     ends it outright — proves the trap's first `kill -TERM` alone is
  #     enough and that the escalation to KILL does not fire unnecessarily
  #     (over-killing a job that already exited cleanly would be its own bug).
  #   - stubborn: `trap '' TERM` — the shape with ZERO coverage before this
  #     file. Proves the escalation to KILL in _run_bounded's trap actually
  #     happens and actually reaches this process (which a TERM alone cannot
  #     touch by construction).
  # Both write their readiness file as the FIRST action after the trap is
  # settled (installed for stubborn, left at its default for compliant) —
  # never before — so "ready" means "a TERM sent from this point on exercises
  # the exact disposition under test", not merely "the script started".
  RTDK_READY="$RTDK/ready"
  case "$rtd_kind" in
    compliant)
      printf '#!/bin/sh\n: >"%s"\nsleep 300\n' "$RTDK_READY" >"$RTDK/bin/gh-stub"
      ;;
    stubborn)
      printf '#!/bin/sh\ntrap "" TERM\n: >"%s"\nsleep 300\n' "$RTDK_READY" >"$RTDK/bin/gh-stub"
      ;;
  esac
  chmod +x "$RTDK/bin/gh-stub"

  # Launch report-tick in ITS OWN process group, the same way the generated
  # wrapper launches the in-wake reporter — so `kill -TERM -"$rpt"` below is
  # the real production shape, not a stand-in for it.
  set -m
  "$SCRIPT" report-tick --dir "$RTD" --label "com.autopilot.reaper.$rtd_kind" \
    --gh "$RTDK/bin/gh-stub" --usage-bin "$RTD_USAGE" >>"$RTDK/o.log" 2>&1 &
  rpt=$!
  set +m

  # --- readiness handshake: bounded poll, never a sleep ----------------------
  rtd_ready=0
  rtd_tries=0
  while [ "$rtd_tries" -lt 150 ]; do
    [ -f "$RTDK_READY" ] && {
      rtd_ready=1
      break
    }
    sleep 0.1
    rtd_tries=$((rtd_tries + 1))
  done
  if [ "$rtd_ready" != 1 ]; then
    bad "reaper teardown [$rtd_kind]: fixture never reached blocked state" \
      "gh stub's readiness file never appeared within 15s"
    kill -TERM -"$rpt" 2>/dev/null
    wait "$rpt" 2>/dev/null
    continue
  fi

  # --- topology handshake: poll until the full 3-group shape is up -----------
  # (the reporter's own group, the bounded job's group, the watchdog's group).
  # Recording BEFORE signalling — and not signalling until this is observed —
  # is what closes the snapshot-before-watchdog race without a sleep.
  rtd_pgids=""
  rtd_tries=0
  while [ "$rtd_tries" -lt 150 ]; do
    rtd_pgids="$(_rtd_pgids_from "$rpt" | sort -u)"
    rtd_count="$(printf '%s\n' "$rtd_pgids" | grep -c .)"
    [ "$rtd_count" -ge 3 ] && break
    sleep 0.1
    rtd_tries=$((rtd_tries + 1))
  done
  rtd_pgid_list="$(printf '%s' "$rtd_pgids" | tr '\n' ' ')"
  if [ "$rtd_count" -lt 3 ]; then
    bad "reaper teardown [$rtd_kind]: topology never reached the expected 3 process groups" \
      "saw $rtd_count distinct pgid(s): $rtd_pgid_list"
    kill -TERM -"$rpt" 2>/dev/null
    wait "$rpt" 2>/dev/null
    _rtd_kill_pgids "$rtd_pgid_list"
    continue
  fi

  # --- signal, the production shape, then reap the direct child --------------
  kill -TERM -"$rpt" 2>/dev/null
  wait "$rpt" 2>/dev/null

  # --- assert EVENTUAL absence, never an instant and never exact timing ------
  # Deadline is 10s against a 60s watchdog default (constraint above): still
  # matching at 10s is a real leak, not a slow-but-honest teardown.
  rtd_gone=0
  rtd_tries=0
  while [ "$rtd_tries" -lt 50 ]; do
    rtd_survivors="$(_rtd_dump_rows "$rtd_pgid_list")"
    if [ -z "$rtd_survivors" ]; then
      rtd_gone=1
      break
    fi
    sleep 0.2
    rtd_tries=$((rtd_tries + 1))
  done
  if [ "$rtd_gone" = 1 ]; then
    ok "reaper teardown [$rtd_kind]: job group + watchdog group both gone within 10s of TERM"
  else
    bad "reaper teardown [$rtd_kind]: a process from a recorded pgid survived past the 10s deadline" \
      "pgids=[$rtd_pgid_list] rows: $(_rtd_dump_rows "$rtd_pgid_list" | tr '\n' ';')"
  fi

  # Self-cleaning: never leave this iteration's groups behind, pass or fail.
  _rtd_kill_pgids "$rtd_pgid_list"
done

# --- Part 2: end-of-suite fixture-process sweep -----------------------------
# Assert that no process-table row's command contains this suite's own $BASE
# (fixed-string match, never a regex over an unescaped path). Phrased as "no
# fixture-owned processes remain" — NOT "no PPID-1 processes" — because by
# this point in the suite every launcher has already returned; ANY surviving
# fixture process at end-of-suite is a leak regardless of what re-parented it.
#
# What this catches, and what it does NOT: this sweep catches the PERSISTENT
# class — a regression that orphans the reporter loop itself so it is still
# alive when the suite finishes. It would NOT have caught the bug that
# motivated this work: that watchdog lived ~57s inside a ~6-minute suite, so
# an end-of-suite sweep would have missed it entirely unless it happened to
# land in the final minute. The direct, PGID-based test above (the reaper
# teardown section) is what covers that bounded-TRANSIENT class — do not
# delete it as "redundant" with this sweep; the two catch different failure
# shapes and neither subsumes the other.
# Snapshot FIRST, filter SECOND, as two separate steps rather than one
# pipeline: `ps | grep -F -- "$BASE"` puts $BASE into the grep process's OWN
# argv, and that grep is itself alive (and captured by `ps`, since it runs
# concurrently with the pipeline's `ps` stage) at the moment the snapshot is
# taken — a self-match false positive on every run. Capturing the snapshot to
# a variable first, then filtering the captured TEXT, means the filtering
# process's argv is never itself part of what got snapshotted.
rtd_sweep_snap="$(ps -Ao pid,ppid,pgid,stat,etime,command 2>/dev/null | tail -n +2)"
rtd_sweep_rows="$(printf '%s\n' "$rtd_sweep_snap" | grep -F -- "$BASE" || true)"
if [ -z "$rtd_sweep_rows" ]; then
  ok "end-of-suite sweep: no fixture-owned processes remain"
else
  bad "end-of-suite sweep: fixture-owned process(es) survived to end-of-suite" \
    "$(printf '%s' "$rtd_sweep_rows" | tr '\n' ';')"
  # Scoped kill by pgid (column 3 of each offending row), not a blanket
  # pkill -f "$BASE" — a red run here must not poison a LATER run's process
  # table by leaving something for a coarser match to trip over either.
  rtd_sweep_pgids="$(printf '%s\n' "$rtd_sweep_rows" | awk '{print $3}' | sort -u | tr '\n' ' ')"
  _rtd_kill_pgids "$rtd_sweep_pgids"
fi

guard_hits="$(grep -c '^osascript: ' "$NOTIFY_GUARD_LOG" 2>/dev/null | tr -d ' ')"
case "$guard_hits" in '' | *[!0-9]*) guard_hits=0 ;; esac
if [ "$guard_hits" -gt 0 ]; then
  ok "notifier guard: the incidental alarms of this suite ($guard_hits) were CAUGHT by the guard, not delivered to a desktop"
else
  bad "notifier guard: the guard caught ZERO notifications — it is no longer covering the alarm path (the leak can return unseen)"
fi

# --- PRE-618 caller-repo integrity assertion ----------------------------------
# The regression test for the fixture escape (see the caller-repo snapshot at the
# top): after the ENTIRE suite, the caller's repo must be what it was before —
# same HEAD, same current branch, same tracked working tree + index, and no
# branches created. If any moved, a fixture reached outside its own temp repo and
# corrupted the caller's checkout — the exact failure this file exists to make
# impossible, and the check that was missing when it first happened. (Untracked
# files are excluded on purpose: a repo-local BASE fallback is trap-cleaned on
# exit and is not corruption.)
if [ "$CALLER_IS_GIT" = 1 ]; then
  # The after-probes must observe the caller's repo the way the caller's own
  # git does — otherwise a pinned-config view of a tree checked out under the
  # developer's own config (core.autocrlf, core.fileMode, core.attributesFile
  # all affect `status --porcelain`) can report phantom modifications that were
  # never there. `env -u` CLEARS the three rather than restoring them, so this
  # reproduces the before-snapshot exactly when the caller had none of them set
  # — the normal case — and diverges for a caller who exports them in their own
  # shell. That divergence fails loud and safe (a false positive in this
  # assertion, never a missed escape), which is why it is not worth carrying the
  # save/restore machinery to close.
  caller_head_after="$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CEILING_DIRECTORIES git -C "$CALLER_REPO" rev-parse HEAD 2>/dev/null || echo NONE)"
  caller_ref_after="$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CEILING_DIRECTORIES git -C "$CALLER_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo NONE)"
  caller_tracked_after="$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CEILING_DIRECTORIES git -C "$CALLER_REPO" status --porcelain --untracked-files=no 2>/dev/null)"
  [ "$caller_head_after" = "$CALLER_HEAD_BEFORE" ] \
    && ok "caller-repo safety: HEAD unmoved (no fixture committed into the caller's repo)" \
    || bad "caller-repo safety: HEAD MOVED — a fixture committed into the caller's repo" \
      "before=$CALLER_HEAD_BEFORE after=$caller_head_after"
  [ "$caller_ref_after" = "$CALLER_REF_BEFORE" ] \
    && ok "caller-repo safety: current branch unchanged" \
    || bad "caller-repo safety: current branch changed" "before=$CALLER_REF_BEFORE after=$caller_ref_after"
  [ "$caller_tracked_after" = "$CALLER_TRACKED_BEFORE" ] \
    && ok "caller-repo safety: tracked working tree + index unchanged" \
    || bad "caller-repo safety: tracked files changed under the caller's repo" \
      "before=[$CALLER_TRACKED_BEFORE] after=[$caller_tracked_after]"
fi

echo "test-spawn-orchestrator: $pass passed, $fail failed"
[ "$fail" = 0 ]
