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
have "profile: harness /tmp scratch tree granted by uid-independent pattern" \
  "(regex #\"^$tmp_c/claude-[0-9]+(/|\$)\")" "$body"
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
# --- classify-exit: supervisor-side exit classification (task 10, #22) -------
CX="$BASE/cx"; mkdir -p "$CX"
printf 'ok\n' >"$CX/clean.log"
printf 'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}\n' >"$CX/auth.log"
printf 'API Error: 429 rate_limit_error: overloaded\n' >"$CX/rate.log"
printf 'some other unrelated crash\n' >"$CX/weird.log"

ceo="$("$SCRIPT" classify-exit --exit-code 0 --output "$CX/clean.log" 2>&1)"; cec=$?
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
lack "classify-exit: incidental 401 is NOT fatal"                     'fatal:' "$ceo"
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
lack "classify-exit: stale 401 is not sticky across wakes"         'fatal:' "$ceo"
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
o="$("$SCRIPT" classify-exit --output "$CX/clean.log" 2>&1)"; [ $? = 2 ] && printf '%s' "$o" | grep -qF 'requires --exit-code' \
  && ok "classify-exit fail-closed: missing --exit-code" || bad "classify-exit fail-closed: missing --exit-code" "$o"
o="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/nope.log" 2>&1)"; [ $? = 2 ] && printf '%s' "$o" | grep -qF 'not found' \
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
  SC="$BASE/sc-fatal"; mkdir -p "$SC/.auto-pilot"
  ( cd "$SC" && git init -q \
    && { printf -- '---\n'; printf 'status: active\n'; printf 'pause_reason: \n'; printf -- '---\n'; } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init )
  scout="$("$SCRIPT" supervisor-check --exit-code 1 --log "$CX/auth.log" --dir "$SC" \
    --label com.autopilot.test.fatal --state "$SC/.auto-pilot/supervisor-state" 2>&1)"
  have "supervisor-check: fatal halt reports itself" 'supervisor halt' "$scout"
  have "supervisor-check: fatal writes status: systemic" 'status: systemic' "$(cat "$SC/.auto-pilot/RUN.md")"
  have "supervisor-check: fatal writes a pause_reason"  'pause_reason: non-retryable auth failure' "$(cat "$SC/.auto-pilot/RUN.md")"
  have "supervisor-check: fatal appends a REPORT.md alarm" 'ALARM' "$(cat "$SC/.auto-pilot/REPORT.md")"
  scommits="$(git -C "$SC" log --oneline | wc -l | tr -d ' ')"
  [ "$scommits" = 2 ] && ok "supervisor-check: fatal halt commits the run-state change" \
    || bad "supervisor-check: fatal halt commits the run-state change" "commits=$scommits"

  # --- no-progress guard: N (default 3) consecutive non-zero, no-commit wakes halts
  SC2="$BASE/sc-noprogress"; mkdir -p "$SC2/.auto-pilot"
  ( cd "$SC2" && git init -q \
    && { printf -- '---\n'; printf 'status: active\n'; printf 'pause_reason: \n'; printf -- '---\n'; } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init )
  STATE2="$SC2/.auto-pilot/supervisor-state"
  "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC2" --label com.autopilot.test.np --state "$STATE2" >/dev/null 2>&1
  "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC2" --label com.autopilot.test.np --state "$STATE2" >/dev/null 2>&1
  npout="$("$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC2" --label com.autopilot.test.np --state "$STATE2" 2>&1)"
  have "supervisor-check: no-progress guard halts after N consecutive failures" 'no forward progress' "$npout"
  have "supervisor-check: no-progress halt also writes status: systemic" 'status: systemic' "$(cat "$SC2/.auto-pilot/RUN.md")"

  # co-review (finding #1): the guard must still fire when _run_head returns
  # EMPTY (a non-git run dir, or git missing from the launchd PATH) — an empty
  # head is sentineled so consecutive wakes still count as no progress instead of
  # resetting the counter to 1 forever and never halting.
  SC_EH="$BASE/sc-emptyhead"; mkdir -p "$SC_EH/.auto-pilot"   # deliberately NOT a git repo
  { printf -- '---\n'; printf 'status: active\n'; printf 'pause_reason: \n'; printf -- '---\n'; } >"$SC_EH/.auto-pilot/RUN.md"
  printf '# report\n' >"$SC_EH/.auto-pilot/REPORT.md"
  STATE_EH="$SC_EH/.auto-pilot/supervisor-state"
  "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC_EH" --label com.autopilot.test.eh --state "$STATE_EH" >/dev/null 2>&1
  "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC_EH" --label com.autopilot.test.eh --state "$STATE_EH" >/dev/null 2>&1
  ehout="$("$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC_EH" --label com.autopilot.test.eh --state "$STATE_EH" 2>&1)"
  have "supervisor-check: no-progress guard halts even with an empty run HEAD" 'no forward progress' "$ehout"
  have "supervisor-check: empty-HEAD halt still writes status: systemic" 'status: systemic' "$(cat "$SC_EH/.auto-pilot/RUN.md")"

  # --- a legitimate paused_until wait never trips the guard, even repeated ------
  SC3="$BASE/sc-paused"; mkdir -p "$SC3/.auto-pilot"
  ( cd "$SC3" && git init -q \
    && { printf -- '---\n'; printf 'status: paused\n'; printf 'paused_until: 2099-01-01T00:00:00\n'; printf -- '---\n'; } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init )
  STATE3="$SC3/.auto-pilot/supervisor-state"
  i=0
  while [ "$i" -lt 5 ]; do
    "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC3" --label com.autopilot.test.paused --state "$STATE3" >/dev/null 2>&1
    i=$((i + 1))
  done
  lack "supervisor-check: a paused wake never halts, however many repeats" 'systemic' "$(cat "$SC3/.auto-pilot/RUN.md")"

  # --- forward progress (a fresh run-state commit) resets the guard's counter ---
  SC4="$BASE/sc-progress"; mkdir -p "$SC4/.auto-pilot"
  ( cd "$SC4" && git init -q \
    && { printf -- '---\n'; printf 'status: active\n'; printf 'pause_reason: \n'; printf -- '---\n'; } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init )
  STATE4="$SC4/.auto-pilot/supervisor-state"
  "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC4" --label com.autopilot.test.progress --state "$STATE4" >/dev/null 2>&1
  # a task did real work between wakes: a new run-state commit lands
  ( cd "$SC4" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "task progressed" )
  "$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC4" --label com.autopilot.test.progress --state "$STATE4" >/dev/null 2>&1
  pgout="$("$SCRIPT" supervisor-check --exit-code 1 --log "$CX/weird.log" --dir "$SC4" --label com.autopilot.test.progress --state "$STATE4" 2>&1)"
  have "supervisor-check: a run-state commit resets the no-progress counter" '2/3 consecutive' "$pgout"
  lack "supervisor-check: progress in between never halts" 'systemic' "$(cat "$SC4/.auto-pilot/RUN.md")"
else
  echo "skip - supervisor-check: fatal/no-progress halt (git not available)"
fi

# --- write-launch: the generated script classifies its own exit (task 10) -----
lbody10="$(cat "$BASE/launch.sh" 2>/dev/null)"
have "launch: calls supervisor-check after claude exits" 'supervisor-check' "$lbody10"
have "launch: no longer execs claude directly"           'set +e'          "$lbody10"
lack "launch: exec sandbox-exec no longer used"          'exec sandbox-exec' "$lbody10"
# the log offset must be captured BEFORE claude runs, and handed to
# supervisor-check — else classification reads every past wake's bytes too.
have "launch: captures the log offset before the run" 'off=$(wc -c'      "$lbody10"
have "launch: passes --since-offset to supervisor-check" '--since-offset "$off"' "$lbody10"
off_ln="$(printf '%s\n' "$lbody10" | grep -n 'off=$(wc -c' | head -1 | cut -d: -f1)"
sbx_ln="$(printf '%s\n' "$lbody10" | grep -n '^sandbox-exec -f' | head -1 | cut -d: -f1)"
if [ -n "$off_ln" ] && [ -n "$sbx_ln" ] && [ "$off_ln" -lt "$sbx_ln" ]; then
  ok "launch: log offset is captured before sandbox-exec runs"
else
  bad "launch: log offset is captured before sandbox-exec runs" "off@$off_ln sandbox@$sbx_ln"
fi

# --- restack: post-merge restack of stacked PRs (task 18, finding #25) -------
# Builds a REAL git repo (a bare "origin" + a working clone) with a
# squash-merged parent + a stacked child, and a FAKE `gh` (offline — no
# GitHub calls) so the git mechanics run for real while every GitHub
# read/write is mocked and inspectable.
if command -v git >/dev/null 2>&1; then
  RS="$BASE/restack"; mkdir -p "$RS"
  ORIGIN="$RS/origin.git"; WORK="$RS/work"
  git init --bare -q "$ORIGIN"
  git init -q "$WORK"
  git -C "$WORK" remote add origin "$ORIGIN"
  git -C "$WORK" config user.email test@example.com
  git -C "$WORK" config user.name "Test"
  git -C "$WORK" checkout -q -b main
  echo root >"$WORK/root.txt"; git -C "$WORK" add root.txt; git -C "$WORK" commit -q -m root
  git -C "$WORK" push -q origin main

  # parent branch (task_parent, PR #100): adds parent.txt
  git -C "$WORK" checkout -q -b parent-branch
  printf 'line1\n' >"$WORK/parent.txt"; git -C "$WORK" add parent.txt; git -C "$WORK" commit -q -m "parent change"
  git -C "$WORK" push -q origin parent-branch
  PARENT_SHA="$(git -C "$WORK" rev-parse parent-branch)"   # the child's frozen base_sha

  # child branch (task_child, PR #101): stacked on parent-branch, touches ONLY
  # child.txt — must restack cleanly regardless of what else happens to main.
  git -C "$WORK" checkout -q -b child-branch
  echo child >"$WORK/child.txt"; git -C "$WORK" add child.txt; git -C "$WORK" commit -q -m "child change"
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
  git -C "$WORK" add parent.txt; git -C "$WORK" commit -q -m "parent: post-hand-off review fix"
  git -C "$WORK" push -q origin main

  # Fake gh: PR state lives in flat files under $FAKE_GH_DB; `pr edit --base`
  # rewrites the base file (so a second restack observes the retarget) and
  # appends to edits.log (so the test can assert exactly what was retargeted).
  FAKE_GH_DB="$RS/ghdb"; mkdir -p "$FAKE_GH_DB"
  printf 'MERGED\n' >"$FAKE_GH_DB/100.state"; printf 'main\n' >"$FAKE_GH_DB/100.base"
  printf 'OPEN\n'   >"$FAKE_GH_DB/101.state"; printf 'parent-branch\n' >"$FAKE_GH_DB/101.base"
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
      .baseRefName) cat "$db/$num.base" 2>/dev/null ;;
      .state)       cat "$db/$num.state" 2>/dev/null ;;
      *) exit 1 ;;
    esac
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

  RUNDIR_RS="$RS/run"; mkdir -p "$RUNDIR_RS/.auto-pilot"
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

  rsout="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"; rsc=$?
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
  have "restack: appends a Restack section to REPORT.md"      '## Restack'        "$rsreport"
  have "restack: REPORT.md demands re-verify for the child"   'Re-verify required' "$rsreport"
  have "restack: REPORT.md flags the co-review as STALE"      'STALE'              "$rsreport"

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
  rsout2="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"; rsc2=$?
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
  git -C "$WORK" add parent.txt; git -C "$WORK" commit -q -m "child edits the same line"
  git -C "$WORK" push -q origin conflict-child
  printf 'OPEN\n' >"$FAKE_GH_DB/102.state"; printf 'parent-branch\n' >"$FAKE_GH_DB/102.base"

  git -C "$WORK" checkout -q parent-branch
  git -C "$WORK" checkout -q -b clean2
  echo clean2 >"$WORK/clean2.txt"; git -C "$WORK" add clean2.txt; git -C "$WORK" commit -q -m "clean2 change"
  git -C "$WORK" push -q origin clean2
  printf 'OPEN\n' >"$FAKE_GH_DB/103.state"; printf 'parent-branch\n' >"$FAKE_GH_DB/103.base"

  {
    printf '| task_conflict | handed-off | conflict-child | parent-branch | %s | #102 | |\n' "$PARENT_SHA2"
    printf '| task_clean2   | handed-off | clean2         | parent-branch | %s | #103 | |\n' "$PARENT_SHA2"
  } >>"$RUNDIR_RS/.auto-pilot/RUN.md"

  # Park HEAD somewhere deliberate (main) so a stray checkout is unmistakable.
  git -C "$WORK" checkout -q main
  head_ref_1="$(git -C "$WORK" rev-parse --abbrev-ref HEAD)"
  head_sha_1="$(git -C "$WORK" rev-parse HEAD)"

  precommit_tip="$(git -C "$ORIGIN" rev-parse conflict-child)"
  rsout3="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"; rsc3=$?
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
  dirty_out="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"; dirty_c=$?
  if [ "$dirty_c" = 2 ] && printf '%s' "$dirty_out" | grep -qF 'worktree is dirty'; then
    ok "restack: dirty caller worktree fails closed"
  else
    bad "restack: dirty caller worktree fails closed" "exit=$dirty_c msg=$dirty_out"
  fi
  rm -f "$WORK/uncommitted.txt"

  # --- orphaned-child detector: a merged/deleted base is a flagged defect ----
  RSD="$RS/detect-run"; mkdir -p "$RSD/.auto-pilot"
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
  printf 'OPEN\n' >"$FAKE_GH_DB/200.state"   # no 200.base file at all -> baseRefName lookup fails
  printf 'OPEN\n' >"$FAKE_GH_DB/201.state"; printf 'parent-branch\n' >"$FAKE_GH_DB/201.base"

  dsout="$("$SCRIPT" restack --run-dir "$RSD" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"; dsc=$?
  [ "$dsc" = 2 ] && ok "restack: orphan-detector run reports the missing-base_sha failure" \
    || bad "restack: orphan-detector run reports the missing-base_sha failure" "exit=$dsc"
  have "restack: flags a deleted/unreadable base as a defect" 'DEFECT task_deleted' "$dsout"
  have "restack: flags a PR still targeting a merged branch as a defect" 'DEFECT task_quiet' "$dsout"
  have "restack: defect summary count is non-zero" 'defects=2' "$dsout"

  # === co-review scenarios: cascade (3-deep), retarget-failure, closed child ===
  # A fresh bare origin + clone so prior mutations don't bleed in.
  C_ORIGIN="$RS/c-origin.git"; C_WORK="$RS/c-work"
  git init --bare -q "$C_ORIGIN"
  git init -q "$C_WORK"
  git -C "$C_WORK" remote add origin "$C_ORIGIN"
  git -C "$C_WORK" config user.email t@e; git -C "$C_WORK" config user.name T
  git -C "$C_WORK" checkout -q -b main
  echo r >"$C_WORK/r.txt"; git -C "$C_WORK" add r.txt; git -C "$C_WORK" commit -q -m r; git -C "$C_WORK" push -q origin main
  # chain A <- B <- C (each touches only its own file).
  git -C "$C_WORK" checkout -q -b br-a; echo a >"$C_WORK/a.txt"; git -C "$C_WORK" add a.txt; git -C "$C_WORK" commit -q -m a; git -C "$C_WORK" push -q origin br-a
  A_SHA="$(git -C "$C_WORK" rev-parse br-a)"
  git -C "$C_WORK" checkout -q -b br-b; echo b >"$C_WORK/b.txt"; git -C "$C_WORK" add b.txt; git -C "$C_WORK" commit -q -m b; git -C "$C_WORK" push -q origin br-b
  B_SHA="$(git -C "$C_WORK" rev-parse br-b)"
  git -C "$C_WORK" checkout -q -b br-c; echo c >"$C_WORK/c.txt"; git -C "$C_WORK" add c.txt; git -C "$C_WORK" commit -q -m c; git -C "$C_WORK" push -q origin br-c
  # A squash-merges to main.
  git -C "$C_WORK" checkout -q main; git -C "$C_WORK" merge -q --squash br-a >/dev/null; git -C "$C_WORK" commit -q -m "a squashed"; git -C "$C_WORK" push -q origin main
  git -C "$C_WORK" checkout -q main
  C_DB="$RS/c-ghdb"; mkdir -p "$C_DB"
  printf 'MERGED\n' >"$C_DB/1.state"; printf 'main\n' >"$C_DB/1.base"        # A merged
  printf 'OPEN\n'   >"$C_DB/2.state"; printf 'br-a\n' >"$C_DB/2.base"        # B on parent branch
  printf 'OPEN\n'   >"$C_DB/3.state"; printf 'br-b\n' >"$C_DB/3.base"        # C on B's branch
  C_RUN="$RS/c-run"; mkdir -p "$C_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_a | handed-off | br-a | main | - | #1 | |\n'
    printf '| t_b | handed-off | br-b | br-a | %s | #2 | |\n' "$A_SHA"
    printf '| t_c | handed-off | br-c | br-b | %s | #3 | |\n' "$B_SHA"
  } >"$C_RUN/.auto-pilot/RUN.md"
  export FAKE_GH_DB="$C_DB"
  cout="$("$SCRIPT" restack --run-dir "$C_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"; ccode=$?
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
  git -C "$C_WORK" checkout -q -b br-x; echo x >"$C_WORK/x.txt"; git -C "$C_WORK" add x.txt; git -C "$C_WORK" commit -q -m x; git -C "$C_WORK" push -q origin br-x
  X_SHA="$(git -C "$C_WORK" rev-parse br-x)"
  git -C "$C_WORK" checkout -q -b br-y; echo y >"$C_WORK/y.txt"; git -C "$C_WORK" add y.txt; git -C "$C_WORK" commit -q -m y; git -C "$C_WORK" push -q origin br-y
  Y_SHA="$(git -C "$C_WORK" rev-parse br-y)"
  git -C "$C_WORK" checkout -q -b br-z; echo z >"$C_WORK/z.txt"; git -C "$C_WORK" add z.txt; git -C "$C_WORK" commit -q -m z; git -C "$C_WORK" push -q origin br-z
  git -C "$C_WORK" checkout -q main; git -C "$C_WORK" merge -q --squash br-x >/dev/null; git -C "$C_WORK" commit -q -m "x squashed"; git -C "$C_WORK" push -q origin main
  git -C "$C_WORK" checkout -q main
  printf 'MERGED\n' >"$C_DB/30.state"; printf 'main\n' >"$C_DB/30.base"
  printf 'OPEN\n'   >"$C_DB/31.state"; printf 'br-x\n' >"$C_DB/31.base"
  printf 'OPEN\n'   >"$C_DB/32.state"; printf 'br-y\n' >"$C_DB/32.base"
  # Phase 1: RUN.md WITHOUT Z, so only Y restacks (Z is never seen this run).
  P1_RUN="$RS/p1-run"; mkdir -p "$P1_RUN/.auto-pilot"
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
  P2_RUN="$RS/p2-run"; mkdir -p "$P2_RUN/.auto-pilot"
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
  printf 'MERGED\n' >"$C_DB/10.state"; printf 'main\n' >"$C_DB/10.base"
  printf 'OPEN\n'   >"$C_DB/11.state"; printf 'br-a\n' >"$C_DB/11.base"; : >"$C_DB/11.editfail"
  git -C "$C_WORK" checkout -q br-a; git -C "$C_WORK" checkout -q -b br-rt; echo rt >"$C_WORK/rt.txt"; git -C "$C_WORK" add rt.txt; git -C "$C_WORK" commit -q -m rt; git -C "$C_WORK" push -q origin br-rt
  git -C "$C_WORK" checkout -q main
  RT_RUN="$RS/rt-run"; mkdir -p "$RT_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_p | handed-off | br-a | main | - | #10 | |\n'
    printf '| t_rt | handed-off | br-rt | br-a | %s | #11 | |\n' "$A_SHA"
  } >"$RT_RUN/.auto-pilot/RUN.md"
  rtout="$("$SCRIPT" restack --run-dir "$RT_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"; rtcode=$?
  have "restack retarget-fail: reports a DEFECT" 'DEFECT — rebased and force-pushed' "$rtout"
  lack "restack retarget-fail: does NOT report the child done" 'restack t_rt done' "$rtout"
  [ "$rtcode" = 2 ] && ok "restack retarget-fail: exits non-zero" || bad "restack retarget-fail: exits non-zero" "exit=$rtcode"

  # closed child (fix 3): a CLOSED child PR is a LOUD orphan — flag it, and NEVER
  # force-push its branch.
  printf 'MERGED\n' >"$C_DB/20.state"; printf 'main\n' >"$C_DB/20.base"
  printf 'CLOSED\n' >"$C_DB/21.state"; printf 'br-a\n' >"$C_DB/21.base"
  git -C "$C_WORK" checkout -q br-a; git -C "$C_WORK" checkout -q -b br-closed; echo cl >"$C_WORK/cl.txt"; git -C "$C_WORK" add cl.txt; git -C "$C_WORK" commit -q -m cl; git -C "$C_WORK" push -q origin br-closed
  git -C "$C_WORK" checkout -q main
  CLOSED_TIP_BEFORE="$(git -C "$C_WORK" rev-parse origin/br-closed)"
  CL_RUN="$RS/cl-run"; mkdir -p "$CL_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_p2 | handed-off | br-a | main | - | #20 | |\n'
    printf '| t_cl | handed-off | br-closed | br-a | %s | #21 | |\n' "$A_SHA"
  } >"$CL_RUN/.auto-pilot/RUN.md"
  clout="$("$SCRIPT" restack --run-dir "$CL_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"; clcode=$?
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

echo "test-spawn-orchestrator: $pass passed, $fail failed"
[ "$fail" = 0 ]
