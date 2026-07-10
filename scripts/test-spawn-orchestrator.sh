#!/usr/bin/env bash
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

# Sandboxed runs may deny the system temp dir — fall back to a repo-local base.
# Resolve to the physical path (pwd -P): the renderer canonicalizes every path,
# so fixtures must be canonical too or /var vs /private/var would mismatch.
BASE="$(mktemp -d 2>/dev/null || mktemp -d "$ROOT/.so-test.XXXXXX")"
BASE="$(cd "$BASE" && pwd -P)"
trap 'rm -rf "$BASE"' EXIT

pass=0
fail=0
ok()   { pass=$((pass + 1)); echo "ok   - $1"; }
bad()  { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && echo "       $2"; return 0; }
# assert helpers use if/else — `cond && bad || ok` double-fires when bad returns non-zero.
have()    { if grep -qF "$2" <<<"$3"; then ok "$1"; else bad "$1"; fi; }
lack()    { if grep -qF "$2" <<<"$3"; then bad "$1"; else ok "$1"; fi; }
count_is() { local n; n="$(grep -cF "$3" <<<"$4")"; if [ "$n" = "$2" ]; then ok "$1"; else bad "$1" "want $2 got $n"; fi; }

# Fixtures: real dirs + a real executable + a real plain file.
RUN_WT="$BASE/run-wt"; WORKER_WT="$BASE/worker-wt"; REPO_RO="$BASE/repo-ro"
mkdir -p "$RUN_WT" "$WORKER_WT" "$REPO_RO"
PLAIN="$BASE/plain"; : >"$PLAIN"
BIN="$BASE/bin/tool"; mkdir -p "$BASE/bin"; printf '#!/bin/sh\n:\n' >"$BIN"; chmod +x "$BIN"

# --- render happy path: assert the token blocks are filled correctly ----------
prof="$BASE/happy.sb"
if out="$("$SCRIPT" render-profile --rw "$RUN_WT" --rw "$WORKER_WT" --ro "$REPO_RO" --exec "$BIN" --out "$prof" 2>&1)" \
   && [ -f "$prof" ]; then
  ok "render-profile: exit 0 and writes --out"
else
  bad "render-profile: exit 0 and writes --out" "$out"
fi

body="$(cat "$prof" 2>/dev/null)"
have "profile: deny-default present"      '(deny default)'         "$body"
have "profile: RW run worktree present"   "(subpath \"$RUN_WT\")"  "$body"
have "profile: exec binary as literal"    "(literal \"$BIN\")"     "$body"
# An RW path appears twice (file-read* + file-write*); an RO path once (read only).
count_is "profile: RW path is read+write" 2 "$RUN_WT"   "$body"
count_is "profile: RO path is read-only"  1 "$REPO_RO"  "$body"
# no unrendered template tokens remain
lack "profile: no @@tokens@@ remain"      '@@'                     "$body"

# --- fail-closed: bad inputs exit 2 and write nothing -------------------------
fc() { # <name> <expected-substr> <args...>
  local name="$1" want="$2"; shift 2
  local target="$BASE/fc.sb"; rm -f "$target"
  local o c
  o="$("$SCRIPT" render-profile "$@" --out "$target" 2>&1)"; c=$?
  if [ "$c" = 2 ] && [ ! -e "$target" ] && printf '%s' "$o" | grep -qF "$want"; then
    ok "fail-closed: $name"
  else
    bad "fail-closed: $name" "exit=$c wrote=$([ -e "$target" ] && echo YES || echo no) msg=$o"
  fi
}
fc "relative rw"        "must be absolute"          --rw "relative/path"
fc "missing rw"         "does not exist"            --rw "$BASE/nope"
fc "exec is a dir"      "not an executable file"    --exec "$REPO_RO"
fc "exec non-exec file" "not an executable file"    --exec "$PLAIN"

# --out required
o="$("$SCRIPT" render-profile --rw "$RUN_WT" 2>&1)"; [ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --out' \
  && ok "fail-closed: --out required" || bad "fail-closed: --out required" "$o"

# unknown subcommand
o="$("$SCRIPT" bogus 2>&1)"; [ $? = 2 ] && printf '%s' "$o" | grep -q 'unknown subcommand' \
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

echo "test-spawn-orchestrator: $pass passed, $fail failed"
[ "$fail" = 0 ]
