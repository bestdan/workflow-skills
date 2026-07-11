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
have()    { if grep -qF -- "$2" <<<"$3"; then ok "$1"; else bad "$1"; fi; }
lack()    { if grep -qF -- "$2" <<<"$3"; then bad "$1"; else ok "$1"; fi; }
count_is() { local n; n="$(grep -cF -- "$3" <<<"$4")"; if [ "$n" = "$2" ]; then ok "$1"; else bad "$1" "want $2 got $n"; fi; }

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

# --- A1 regression: an exec symlink must resolve to its real target -----------
# (Seatbelt matches process-exec against the resolved vnode path, not the link;
# `command -v` on Homebrew binaries returns the symlink, so the renderer must
# canonicalize it or the exec is silently denied.)
ln -s tool "$BASE/bin/tool-link"   # relative symlink → $BASE/bin/tool
symprof="$BASE/sym.sb"
"$SCRIPT" render-profile --rw "$RUN_WT" --exec "$BASE/bin/tool-link" --out "$symprof" >/dev/null 2>&1
symbody="$(cat "$symprof" 2>/dev/null)"
have "exec symlink resolves to real target" "(literal \"$BIN\")" "$symbody"
lack "exec symlink literal not emitted"     "tool-link"          "$symbody"

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

fc "exec-dir /"           "refusing --exec-dir /"     --exec-dir /
fc "exec-dir plain file"  "not a directory"           --exec-dir "$PLAIN"

if command -v sandbox-exec >/dev/null 2>&1; then
  EDBIN=""
  # Prefer no-arg zero-exit binaries so the success check is portable — BSD/macOS
  # `sed --version` exits non-zero (unknown flag), which would false-fail even when
  # exec-dir confinement is working.
  for cand in /usr/bin/true /bin/echo /usr/bin/env; do
    if command -v "$cand" >/dev/null 2>&1; then EDBIN="$cand"; break; fi
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

# --- render-settings: layer-2 egress allowlist narrowing (task 2) -------------
sj="$BASE/settings.json"
"$SCRIPT" render-settings --source linear --coder codex --out "$sj" >/dev/null 2>&1
sbody="$(cat "$sj" 2>/dev/null)"
have "settings: sandbox enabled"          '"enabled":true'          "$sbody"
have "settings: deny-default allowlist"   '"allowedDomains"'        "$sbody"
have "settings: loopback bind off by default" '"allowLocalBinding":false' "$sbody"
have "settings: {codex,linear} has openai" 'api.openai.com'          "$sbody"
have "settings: {codex,linear} has linear" 'api.linear.app'          "$sbody"
have "settings: always has anthropic"      'api.anthropic.com'       "$sbody"
lack "settings: codex run omits devin"     'api.devin.ai'            "$sbody"
lack "settings: no broad googleapis"       'googleapis'              "$sbody"
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
  local name="$1" want="$2"; shift 2
  local t="$BASE/sfc.json"; rm -f "$t"
  local o c; o="$("$SCRIPT" render-settings "$@" --out "$t" 2>&1)"; c=$?
  if [ "$c" = 2 ] && [ ! -e "$t" ] && printf '%s' "$o" | grep -qF "$want"; then
    ok "settings fail-closed: $name"
  else
    bad "settings fail-closed: $name" "exit=$c wrote=$([ -e "$t" ] && echo YES || echo no) msg=$o"
  fi
}
sfc "agy needs --agy-host"  "requires --agy-host"  --source plan --coder agy
sfc "agy rejects wildcard"  "never a wildcard"     --source plan --coder agy --agy-host '*.googleapis.com'
sfc "unknown source"        "unknown --source"     --source bogus --coder codex
# host-value injection: a per-run --mcp-host must not smuggle a bare wildcard or JSON
sfc "mcp bare wildcard"     "invalid egress host"  --source plan --coder codex --mcp-host '*'
sfc "mcp JSON injection"    "invalid egress host"  --source plan --coder codex --mcp-host 'x","*'
sfc "mcp bad chars"         "invalid egress host"  --source plan --coder codex --mcp-host 'evil;rm'
# a well-formed mcp host and a legit subdomain wildcard are accepted
"$SCRIPT" render-settings --source plan --coder codex --mcp-host mcp.example.com --out "$BASE/mcp.json" >/dev/null 2>&1
have "settings: valid mcp host accepted" 'mcp.example.com' "$(cat "$BASE/mcp.json" 2>/dev/null)"
have "settings: github wildcard kept"    '*.githubusercontent.com' "$(cat "$BASE/mcp.json" 2>/dev/null)"

# --- hardening: --confine-under bounds write scopes (task 3, Fable #3) ---------
mkdir -p "$BASE/root/wt"
"$SCRIPT" render-profile --confine-under "$BASE/root" --rw "$BASE/root/wt" --exec "$BIN" --out "$BASE/cf.sb" >/dev/null 2>&1 \
  && ok "confine-under: rw inside root accepted" || bad "confine-under: rw inside root accepted"
cfo="$("$SCRIPT" render-profile --confine-under "$BASE/root" --rw / --out "$BASE/cfx.sb" 2>&1)"; cfc=$?
if [ "$cfc" = 2 ] && [ ! -e "$BASE/cfx.sb" ] && printf '%s' "$cfo" | grep -qF 'refusing --rw /'; then
  ok "confine-under: rw / fails closed"
else
  bad "confine-under: rw / fails closed" "exit=$cfc"
fi
# floor holds even WITHOUT --confine-under (the guard is opt-in; the floor isn't)
rfo="$("$SCRIPT" render-profile --rw / --out "$BASE/rf.sb" 2>&1)"; rfc=$?
[ "$rfc" = 2 ] && [ ! -e "$BASE/rf.sb" ] && printf '%s' "$rfo" | grep -qF 'refusing --rw /' \
  && ok "floor: rw / refused with no --confine-under" || bad "floor: rw / refused with no --confine-under" "exit=$rfc"
# a sibling that shares a prefix but is NOT under the root is rejected (literal prefix)
mkdir -p "$BASE/rootX/wt"
sib="$("$SCRIPT" render-profile --confine-under "$BASE/root" --rw "$BASE/rootX/wt" --out "$BASE/sib.sb" 2>&1)"; sibc=$?
[ "$sibc" = 2 ] && printf '%s' "$sib" | grep -qF 'escapes --confine-under' \
  && ok "confine-under: prefix-sibling rejected" || bad "confine-under: prefix-sibling rejected" "exit=$sibc"

# --- cred-ro: a credential file stays RO inside an RW state dir (task 3, P1 #5) --
# A tool state dir is --rw (its sessions/caches must be writable), but its own
# token must not be — the (subpath) write allow would otherwise cover it.
STATE="$BASE/state"; mkdir -p "$STATE"
CREDF="$STATE/auth.json"; printf '{"token":"secret"}\n' >"$CREDF"
crprof="$BASE/cred.sb"
"$SCRIPT" render-profile --rw "$STATE" --cred-ro "$CREDF" --exec "$BIN" --out "$crprof" >/dev/null 2>&1
crbody="$(cat "$crprof" 2>/dev/null)"
have "cred-ro: emits a deny file-write* block"  '(deny file-write*'      "$crbody"
have "cred-ro: denies the credential literal"   "(literal \"$CREDF\")"   "$crbody"
have "cred-ro: state dir still write-granted"   "(subpath \"$STATE\")"   "$crbody"
lack "cred-ro: no @@tokens@@ remain"            '@@'                     "$crbody"
# ordering is load-bearing: Seatbelt takes the LAST matching rule, so the cred
# deny MUST appear after the state-dir file-write* allow to override it.
awln="$(printf '%s\n' "$crbody" | grep -n 'allow file-write\*' | head -1 | cut -d: -f1)"
dnln="$(printf '%s\n' "$crbody" | grep -n 'deny file-write\*'  | head -1 | cut -d: -f1)"
if [ -n "$awln" ] && [ -n "$dnln" ] && [ "$dnln" -gt "$awln" ]; then
  ok "cred-ro: deny ordered after the write allow"
else
  bad "cred-ro: deny ordered after the write allow" "allow@$awln deny@$dnln"
fi
# no --cred-ro → placeholder comment, no stray deny form
"$SCRIPT" render-profile --rw "$STATE" --exec "$BIN" --out "$BASE/nocred.sb" >/dev/null 2>&1
lack "cred-ro: absent → no deny form" '(deny file-write*' "$(cat "$BASE/nocred.sb" 2>/dev/null)"
# fail-closed: a missing file / a directory writes nothing and exits 2
fc "cred-ro missing file" "does not exist" --cred-ro "$BASE/nope-cred"
fc "cred-ro is a dir"     "not a file"     --cred-ro "$STATE"

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
  if sandbox-exec -f "$crxprof" "$BASHBIN" -c "echo x > $STATE/session" >/dev/null 2>&1; then
    ok "cred-ro: write to the state dir is allowed"
  else
    bad "cred-ro: write to the state dir is allowed"
  fi
  if sandbox-exec -f "$crxprof" "$BASHBIN" -c "echo y > $CREDF" >/dev/null 2>&1; then
    bad "cred-ro: write to the credential file is denied"
  else
    ok "cred-ro: write to the credential file is denied"
  fi
  if sandbox-exec -f "$crxprof" "$BASHBIN" -c "cat $CREDF" >/dev/null 2>&1; then
    ok "cred-ro: the credential file is still readable"
  else
    bad "cred-ro: the credential file is still readable"
  fi
  rm -f "$STATE/session" 2>/dev/null
else
  echo "skip - cred-ro: behavioral checks (sandbox-exec not available)"
fi

# allowLocalBinding flag flips to true (task 3, Fable #6)
"$SCRIPT" render-settings --source plan --coder codex --allow-local-binding --out "$BASE/lb.json" >/dev/null 2>&1
have "settings: --allow-local-binding sets true" '"allowLocalBinding":true' "$(cat "$BASE/lb.json" 2>/dev/null)"

# --- write-launch: launch script + plist generation (task 3) ------------------
# Pass --claude-bin "$BIN" (a fixture) so the harness needs no real claude on PATH.
printf 'run the graph\n' >"$BASE/prompt.txt"
"$SCRIPT" render-settings --source plan --coder codex --out "$BASE/wl.json" >/dev/null 2>&1
wlout="$("$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --until 'T' --label com.autopilot.test --claude-bin "$BIN" \
  --out-script "$BASE/launch.sh" --out-plist "$BASE/job.plist" 2>&1)"
lbody="$(cat "$BASE/launch.sh" 2>/dev/null)"
have "launch: composes sandbox-exec -f"        'sandbox-exec -f'                    "$lbody"
have "launch: invokes resolved claude bin"     "$BIN"                               "$lbody"
have "launch: -p reads prompt from file"       '-p "$(cat'                          "$lbody"
have "launch: bypassPermissions flag"          '--permission-mode bypassPermissions' "$lbody"
have "launch: passes --settings"               '--settings'                         "$lbody"
have "launch: redirects to log"                ">>$BASE/o.log"                       "$lbody"
if command -v plutil >/dev/null 2>&1; then
  if plutil -lint "$BASE/job.plist" >/dev/null 2>&1; then ok "launch: plist lints"; else bad "launch: plist lints"; fi
  # plist injection: an XML-metachar path must still yield a VALID plist (escaped).
  mkdir -p "$BASE/a&b<x"
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/a&b<x" \
    --log "$BASE/a&b<x/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.esc --claude-bin "$BIN" \
    --out-script "$BASE/e.sh" --out-plist "$BASE/e.plist" >/dev/null 2>&1
  if plutil -lint "$BASE/e.plist" >/dev/null 2>&1; then ok "launch: XML-metachar path still lints (escaped)"; else bad "launch: XML-metachar path still lints (escaped)"; fi
else
  echo "skip - launch: plist lint (plutil absent)"
fi
# label injection rejected at the source (defense-in-depth on top of xml_escape)
"$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label 'a</string><key>x' --claude-bin "$BIN" \
  --out-script "$BASE/i.sh" --out-plist "$BASE/i.plist" >/dev/null 2>&1 \
  && bad "launch: injecting label rejected" || ok "launch: injecting label rejected"
# write-launch fail-closed on a missing input file
wlfc="$("$SCRIPT" write-launch --profile "$BASE/nope.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label x --claude-bin "$BIN" --out-script "$BASE/x.sh" --out-plist "$BASE/x.plist" 2>&1)"
[ $? = 2 ] && printf '%s' "$wlfc" | grep -qF 'not found' && ok "launch: missing profile fails closed" || bad "launch: missing profile fails closed"

# --- record-handle: dead pid / non-numeric pid fail closed (task 3) -----------
rho="$("$SCRIPT" record-handle --pid 999999 --out "$BASE/h.txt" 2>&1)"
[ $? = 2 ] && [ ! -e "$BASE/h.txt" ] && printf '%s' "$rho" | grep -qF 'no live process' && ok "record-handle: dead pid fails closed" || bad "record-handle: dead pid fails closed"
"$SCRIPT" record-handle --pid abc --out "$BASE/h.txt" >/dev/null 2>&1 && bad "record-handle: non-numeric pid fails" || ok "record-handle: non-numeric pid fails"

# --- launch --dry-run: the safety-critical ordering (smoke BEFORE detach) ------
dro="$("$SCRIPT" launch --dry-run --out-script "$BASE/l.sh" --out-plist "$BASE/l.plist" --label com.x --handle "$BASE/h2.txt" 2>&1)"
smoke_ln="$(printf '%s\n' "$dro" | grep -n smoke-test | cut -d: -f1)"
detach_ln="$(printf '%s\n' "$dro" | grep -n detach | cut -d: -f1)"
if [ -n "$smoke_ln" ] && [ -n "$detach_ln" ] && [ "$smoke_ln" -lt "$detach_ln" ]; then
  ok "launch: smoke-test ordered before detach"
else
  bad "launch: smoke-test ordered before detach" "smoke@$smoke_ln detach@$detach_ln"
fi

echo "test-spawn-orchestrator: $pass passed, $fail failed"
[ "$fail" = 0 ]
