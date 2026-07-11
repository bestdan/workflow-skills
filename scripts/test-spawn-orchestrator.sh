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

# --- out-of-jail launch escape is closed (toolchain exec + mach brokers) -------
# Broadening exec to whole bin dirs puts /bin/launchctl and /usr/bin/open in
# reach; a job they broker to launchd/LaunchServices runs OUTSIDE the jail. The
# template must deny both the submission mach services and exec of the binaries.
escprof="$BASE/escape.sb"
"$SCRIPT" render-profile --exec-dir /bin --rw "$RUN_WT" --out "$escprof" >/dev/null 2>&1
escbody="$(cat "$escprof" 2>/dev/null)"
have "escape: denies mach-lookup to launchd"       'com.apple.xpc.launchd'          "$escbody"
have "escape: denies mach-lookup to launchservices" 'com.apple.coreservices.launchservicesd' "$escbody"
have "escape: denies exec of launchctl"            '(literal "/bin/launchctl")'     "$escbody"
have "escape: denies exec of open"                 '(literal "/usr/bin/open")'      "$escbody"
have "escape: denies exec of osascript"            '(literal "/usr/bin/osascript")' "$escbody"
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

# --add-task-host: a plan-source run's add-task destination is allowed regardless
# of --source (a plan run whose add-task handler routes to Linear still needs egress)
"$SCRIPT" render-settings --source plan --add-task-host api.linear.app --out "$BASE/addtask.json" >/dev/null 2>&1
have "settings: plan source + add-task-host allows linear" 'api.linear.app' "$(cat "$BASE/addtask.json" 2>/dev/null)"
sfc "add-task-host bare wildcard" "invalid egress host" --source plan --add-task-host '*'
sfc "add-task-host bad chars"     "invalid egress host" --source plan --add-task-host 'evil*'
sfc "add-task-host empty"         "invalid egress host" --source plan --add-task-host ''
sfc "add-task-host JSON injection" "invalid egress host" --source plan --add-task-host 'x","*'
sfc "add-task-host embedded newline" "invalid egress host" --source plan --add-task-host $'good.com\nevil*.com'

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
lack "cred-ro: no @@tokens@@ remain"            '@@'                     "$crbody"
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
  --path "$LAUNCH_PATH" --out-script "$BASE/launch.sh" --out-plist "$BASE/job.plist" 2>&1)"
lbody="$(cat "$BASE/launch.sh" 2>/dev/null)"
have "launch: composes sandbox-exec -f"        'sandbox-exec -f'                    "$lbody"
have "launch: invokes resolved claude bin"     "$BIN"                               "$lbody"
have "launch: -p reads prompt from file"       '-p "$(cat'                          "$lbody"
have "launch: bypassPermissions flag"          '--permission-mode bypassPermissions' "$lbody"
have "launch: passes --settings"               '--settings'                         "$lbody"
have "launch: redirects to log"                ">>$BASE/o.log"                       "$lbody"
have "launch: emits --verbose"                 '--verbose'                           "$lbody"
have "launch: exports resolved PATH"           "export PATH=$LAUNCH_PATH"            "$lbody"
verbose_ln="$(printf '%s\n' "$lbody" | grep -n -- '--verbose' | head -1 | cut -d: -f1)"
sjson_ln="$(printf '%s\n' "$lbody" | grep -n -- '--output-format stream-json' | head -1 | cut -d: -f1)"
if [ -n "$verbose_ln" ] && [ -n "$sjson_ln" ] && [ "$verbose_ln" -lt "$sjson_ln" ]; then
  ok "launch: --verbose precedes --output-format stream-json"
else
  bad "launch: --verbose precedes --output-format stream-json" "verbose@$verbose_ln sjson@$sjson_ln"
fi
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
    --path "$LAUNCH_PATH" --out-script "$BASE/e.sh" --out-plist "$BASE/e.plist" >/dev/null 2>&1
  if plutil -lint "$BASE/e.plist" >/dev/null 2>&1; then ok "launch: XML-metachar path still lints (escaped)"; else bad "launch: XML-metachar path still lints (escaped)"; fi
else
  echo "skip - launch: plist lint (plutil absent)"
fi
# label injection rejected at the source (defense-in-depth on top of xml_escape)
"$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label 'a</string><key>x' --claude-bin "$BIN" \
  --path "$LAUNCH_PATH" --out-script "$BASE/i.sh" --out-plist "$BASE/i.plist" >/dev/null 2>&1 \
  && bad "launch: injecting label rejected" || ok "launch: injecting label rejected"
# write-launch fail-closed on a missing input file
wlfc="$("$SCRIPT" write-launch --profile "$BASE/nope.sb" --settings "$BASE/wl.json" --workdir "$BASE/root/wt" \
  --log "$BASE/o.log" --prompt-file "$BASE/prompt.txt" --label x --claude-bin "$BIN" --path "$LAUNCH_PATH" --out-script "$BASE/x.sh" --out-plist "$BASE/x.plist" 2>&1)"
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

# --- smoke_test invariant: must use the REAL flag set (--verbose + stream-json) -
# so it can't green-light an invocation the real launch rejects. Grepped from
# source since smoke-test execs claude for real and can't run offline here.
smoke_src="$(sed -n '/^smoke_test()/,/^}/p' "$SCRIPT")"
have "smoke-test: uses --verbose"              '--verbose'                 "$smoke_src"
have "smoke-test: uses --output-format stream-json" '--output-format stream-json' "$smoke_src"

# --- task 4: verify broker (run verify OUTSIDE the jail) ----------------------
# The broker just runs `bash -c "$cmd"`, so these round-trip in-jail with no
# sandbox nesting — the pin/containment/fail-closed logic is fully exercised here.
if command -v shasum >/dev/null 2>&1; then
  VB="$BASE/vb"; mkdir -p "$VB/root/wt" "$VB/outside"; SENT="$VB/sentinel"
  ROOT="$(cd "$VB/root" && pwd -P)"; WT="$(cd "$VB/root/wt" && pwd -P)"
  CMD='echo VERIFY_RAN; exit 0'
  PIN="$(printf '%s' "$CMD" | shasum -a 256 | awk '{print $1}')"

  # round trip: request -> broker (one scan) -> await
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash "$PIN" --id rt >/dev/null 2>&1
  have "verify-request: writes the request sentinel" "" "$([ -e "$SENT/rt.request" ] && echo present)"
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --confine-under "$ROOT" >/dev/null 2>&1
  have "verify-broker: consumes the request"  "" "$([ ! -e "$SENT/rt.request" ] && echo consumed)"
  rtres="$(cat "$SENT/rt.result" 2>/dev/null)"
  have "verify-broker: result carries code 0"   'code: 0'    "$rtres"
  have "verify-broker: ran the pinned command"  'VERIFY_RAN' "$rtres"
  awo="$("$SCRIPT" verify-await --sentinel-dir "$SENT" --id rt --timeout 4 2>&1)"
  have "verify-await: reports code + output"     'code=0'     "$awo"
  have "verify-await: prints the verify output"  'VERIFY_RAN' "$awo"

  # a request whose cmd_hash != the broker's pinned hash is REFUSED, never run
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash deadbeef --id mism >/dev/null 2>&1
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --confine-under "$ROOT" >/dev/null 2>&1
  mres="$(cat "$SENT/mism.result" 2>/dev/null)"
  have "verify-broker: hash mismatch refused"    'cmd_hash mismatch' "$mres"
  lack "verify-broker: refused req did not run"  'VERIFY_RAN'        "$mres"

  # a worktree OUTSIDE the run root is REFUSED
  OUT="$(cd "$VB/outside" && pwd -P)"
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$OUT" --cmd-hash "$PIN" --id esc >/dev/null 2>&1
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --confine-under "$ROOT" >/dev/null 2>&1
  have "verify-broker: worktree escape refused"  'escapes --confine-under' "$(cat "$SENT/esc.result" 2>/dev/null)"

  # the broker's own --cmd-hash must match --verify-cmd (install/args mismatch)
  bhm="$("$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --cmd-hash deadbeef --confine-under "$ROOT" 2>&1)"; bhc=$?
  if [ "$bhc" = 2 ] && printf '%s' "$bhm" | grep -qF 'does not match'; then
    ok "verify-broker: pinned hash/cmd mismatch fails closed"
  else
    bad "verify-broker: pinned hash/cmd mismatch fails closed" "exit=$bhc"
  fi

  # the 126-vs-0 contrast the whole task exists for: a #!/usr/bin/env bash script
  # the broker runs via the pinned `bash <script>` (works) — the same script's
  # direct shebang exec is what execve-denies in-jail (finding #4).
  printf '#!/usr/bin/env bash\necho SHEBANG_OK\n' > "$WT/probe.sh"; chmod +x "$WT/probe.sh"
  CMD2='bash probe.sh'; PIN2="$(printf '%s' "$CMD2" | shasum -a 256 | awk '{print $1}')"
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash "$PIN2" --id sb >/dev/null 2>&1
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD2" --confine-under "$ROOT" >/dev/null 2>&1
  have "verify-broker: runs a shebang script via pinned bash" 'SHEBANG_OK' "$(cat "$SENT/sb.result" 2>/dev/null)"

  # verify-request fail-closed: non-hex cmd-hash, missing worktree
  fcr() { local name="$1" want="$2"; shift 2; local o c; o="$("$SCRIPT" verify-request "$@" 2>&1)"; c=$?
    if [ "$c" = 2 ] && printf '%s' "$o" | grep -qF "$want"; then ok "verify-request fail-closed: $name"; else bad "verify-request fail-closed: $name" "exit=$c msg=$o"; fi; }
  fcr "non-hex cmd-hash" "must be lowercase hex" --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash "NOTHEX"
  fcr "missing worktree"  "does not exist"        --sentinel-dir "$SENT" --worktree "$VB/nope" --cmd-hash "$PIN"
  printf 'x\n' >"$VB/notadir"
  fcr "worktree is a file" "is not a directory"   --sentinel-dir "$SENT" --worktree "$VB/notadir" --cmd-hash "$PIN"

  # write-verify-broker: renders an UN-JAILED launch script (no sandbox-exec) + a
  # valid plist, with the verify command pinned in.
  "$SCRIPT" write-verify-broker --sentinel-dir "$SENT" --verify-cmd 'bash scripts/check.sh' \
    --confine-under "$VB" --label com.autopilot.test.verify --workdir "$WT" --log "$VB/b.log" \
    --path '/usr/bin:/bin' --out-script "$VB/broker.sh" --out-plist "$VB/broker.plist" >/dev/null 2>&1
  vbody="$(cat "$VB/broker.sh" 2>/dev/null)"
  have "write-verify-broker: invokes verify-broker"     'verify-broker'  "$vbody"
  have "write-verify-broker: pins the verify command"   'bash scripts/check.sh' "$vbody"
  have "write-verify-broker: pins a cmd-hash"           '--cmd-hash'     "$vbody"
  lack "write-verify-broker: broker is UN-JAILED (no sandbox-exec)" 'sandbox-exec' "$vbody"
  if command -v plutil >/dev/null 2>&1; then
    if plutil -lint "$VB/broker.plist" >/dev/null 2>&1; then ok "write-verify-broker: plist lints"; else bad "write-verify-broker: plist lints"; fi
  else
    echo "skip - write-verify-broker: plist lint (plutil absent)"
  fi
  # write-verify-broker fail-closed: bad label
  wvbc="$("$SCRIPT" write-verify-broker --sentinel-dir "$SENT" --verify-cmd 'x' --confine-under "$VB" \
    --label 'bad label' --workdir "$WT" --log "$VB/b.log" --path '/usr/bin:/bin' \
    --out-script "$VB/x.sh" --out-plist "$VB/x.plist" 2>&1)"; wvc=$?
  if [ "$wvc" = 2 ] && printf '%s' "$wvbc" | grep -qF 'must be [A-Za-z0-9._-]'; then
    ok "write-verify-broker: bad label fails closed"
  else
    bad "write-verify-broker: bad label fails closed" "exit=$wvc"
  fi
else
  echo "skip - verify broker: shasum not available (needed for the command pin)"
fi

# --- status: read-only run inspection (task 8) ---------------------------------
RUNDIR="$BASE/run"; mkdir -p "$RUNDIR/.auto-pilot"
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

sout="$("$SCRIPT" status --label com.autopilot.test --dir "$RUNDIR" 2>&1)"; sc=$?
[ "$sc" = 0 ] && ok "status: exits 0 on a well-formed run dir" || bad "status: exits 0 on a well-formed run dir" "$sout"
have "status: prints the phase table"   'implementing'          "$sout"
have "status: prints a STATUS: line"    'STATUS: active'        "$sout"
have "status: STATUS line has tasks=2"  'tasks=2'               "$sout"
have "status: STATUS line has until"    'until=2026-07-10T06:00:00' "$sout"

# a bogus/dead recorded PID (999999 — never a real live process) reports not-live
have "status: dead pid reports pid=dead" 'pid=dead' "$sout"

# a live pid (this test process's own $$) with a WRONG recorded start-time must
# still report not-live: kill -0 succeeds, but the start-time can't match a
# fabricated value (and ps is unavailable in some jails, which also falls to
# the not-live branch) — either way this must never read "pid=live".
RUNMD2="$BASE/run2/.auto-pilot"; mkdir -p "$RUNMD2"
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
RUNMD3="$BASE/run3/.auto-pilot"; mkdir -p "$RUNMD3"
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
have "status: quoted+commented until parsed bare"  'until=2026-12-31T00:00:00' "$sout4"
lack "status: until does not match paused_until"   'until=2020-01-01T00:00:00' "$sout4"
lack "status: until value keeps no quotes"         'until="2026'               "$sout4"

# fail-closed: missing --label / missing RUN.md
o="$("$SCRIPT" status --dir "$RUNDIR" 2>&1)"; [ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --label' \
  && ok "status fail-closed: missing --label" || bad "status fail-closed: missing --label" "$o"
o="$("$SCRIPT" status --label x --dir "$BASE/no-such-dir" 2>&1)"; [ $? = 2 ] && printf '%s' "$o" | grep -q 'no run state found' \
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
HEAD_REPO="$BASE/head-repo"; mkdir -p "$HEAD_REPO"
git -C "$HEAD_REPO" init -q -b main
git -C "$HEAD_REPO" config user.email test@example.com
git -C "$HEAD_REPO" config user.name test
: >"$HEAD_REPO/seed"; git -C "$HEAD_REPO" add seed; git -C "$HEAD_REPO" commit -q -m seed

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
QFILE="$BASE/QUESTIONS.md"; : >"$QFILE"
gout="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions "$QFILE" 2>&1)"; gc=$?
[ "$gc" = 0 ] && ok "assert-run-head: exits 0 after restoring" || bad "assert-run-head: exits 0 after restoring" "$gout"
have "assert-run-head: reports the deviation" 'HEAD DEVIATION restored' "$gout"
restored="$(git -C "$HEAD_REPO" rev-parse --abbrev-ref HEAD)"
[ "$restored" = "auto-pilot/$RUN_ID" ] && ok "assert-run-head: HEAD restored to the run-state branch" \
  || bad "assert-run-head: HEAD restored to the run-state branch" "got $restored"
qbody="$(cat "$QFILE")"
have "assert-run-head: records the deviation in QUESTIONS.md" 'HEAD was parked on `auto-pilot/hardening-task_3`' "$qbody"
have "assert-run-head: QUESTIONS.md entry is reversible" '**Reversible:** yes' "$qbody"

# idempotent: running it again with HEAD already correct is a silent no-op.
gout2="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions "$QFILE" 2>&1)"; gc2=$?
[ "$gc2" = 0 ] && ok "assert-run-head: exits 0 when HEAD already correct" || bad "assert-run-head: exits 0 when HEAD already correct" "$gout2"
have "assert-run-head: reports HEAD OK on a clean run" 'HEAD OK' "$gout2"
qcount="$(grep -cE '^## Q[0-9]+' "$QFILE")"
[ "$qcount" = 1 ] && ok "assert-run-head: no duplicate entry once HEAD is already correct" \
  || bad "assert-run-head: no duplicate entry once HEAD is already correct" "got $qcount entries"

# fail-closed: not a git worktree at all
mkdir -p "$BASE/plain-dir-not-a-repo"
o="$("$SCRIPT" assert-run-head --dir "$BASE/plain-dir-not-a-repo" --run-id "$RUN_ID" 2>&1)"; [ $? = 2 ] \
  && printf '%s' "$o" | grep -q 'not a git worktree' \
  && ok "assert-run-head fail-closed: not a git worktree" || bad "assert-run-head fail-closed: not a git worktree" "$o"

# fail-closed: missing required args
o="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" 2>&1)"; [ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --dir and --run-id' \
  && ok "assert-run-head fail-closed: missing --run-id" || bad "assert-run-head fail-closed: missing --run-id" "$o"

# fail-closed: a DIRTY deviation must NOT restore (a non-conflicting checkout
# would silently carry the uncommitted task-branch edits onto the run-state branch).
git -C "$HEAD_REPO" checkout -q "auto-pilot/hardening-task_3"
printf 'uncommitted edit\n' >>"$HEAD_REPO/task-file"
o="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" 2>&1)"; drc=$?
dhead="$(git -C "$HEAD_REPO" rev-parse --abbrev-ref HEAD)"
[ "$drc" = 2 ] && printf '%s' "$o" | grep -q 'uncommitted changes' && [ "$dhead" = "auto-pilot/hardening-task_3" ] \
  && ok "assert-run-head fail-closed: dirty deviation is not restored" \
  || bad "assert-run-head fail-closed: dirty deviation is not restored" "rc=$drc head=$dhead msg=$o"
git -C "$HEAD_REPO" checkout -q -- task-file   # drop the dirty edit for later checks

# numbering uses the MAX existing index, not a count — a non-contiguous
# QUESTIONS.md (Q9, Q10) yields Q11, never a colliding low number.
git -C "$HEAD_REPO" checkout -q "auto-pilot/hardening-task_3"
QFILE2="$BASE/Q-noncontig.md"; printf '## Q9 — X — a\n\n## Q10 — X — b\n' >"$QFILE2"
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

echo "test-spawn-orchestrator: $pass passed, $fail failed"
[ "$fail" = 0 ]
