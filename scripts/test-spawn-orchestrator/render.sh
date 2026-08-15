#!/usr/bin/env bash
# shellcheck disable=SC1010,SC2034 # Fixtures intentionally contain shell source and retain outputs for diagnostics.
# Profile rendering: token blocks, exec/toolchain grants, egress narrowing,
# write-launch generation, and the fail-closed paths.
#
# One of the scripts/test-spawn-orchestrator/*.sh suites; see _prelude.sh for
# what they share and dev_docs/gate-performance.md for why they are separate
# files. Runnable on its own: bash scripts/test-spawn-orchestrator/render.sh
# shellcheck source=scripts/test-spawn-orchestrator/_prelude.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_prelude.sh"

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
if [ "$SEATBELT_OK" = 1 ]; then
  if o="$("$SCRIPT" check-profile "$prof" 2>&1)"; then
    ok "check-profile: rendered profile compiles"
  else
    bad "check-profile: rendered profile compiles" "$o"
  fi
  # a malformed profile must be rejected. This assertion is a DENIAL check —
  # without SEATBELT_OK it would "pass" whenever sandbox_apply itself is
  # refused (nested sandbox), which is a denial for the wrong reason: the
  # profile's own malformed content is never parsed at all.
  echo '(version 1) (this-is-not-valid' >"$BASE/bad.sb"
  if "$SCRIPT" check-profile "$BASE/bad.sb" >/dev/null 2>&1; then
    bad "check-profile: rejects a malformed profile"
  else
    ok "check-profile: rejects a malformed profile"
  fi
elif command -v sandbox-exec >/dev/null 2>&1; then
  skipped "check-profile: rendered profile compiles (sandbox-exec present but cannot apply a profile here, nested sandbox)"
  skipped "check-profile: rejects a malformed profile (sandbox-exec present but cannot apply a profile here, nested sandbox)"
else
  skipped "check-profile: rendered profile compiles (sandbox-exec not available on this host, non-macOS)"
  skipped "check-profile: rejects a malformed profile (sandbox-exec not available on this host, non-macOS)"
fi

# --- exec-dir: toolchain-exec mode (subpath, coarser than --exec) -------------
edprof="$BASE/execdir.sb"
"$SCRIPT" render-profile --rw "$RUN_WT" --exec-dir /usr/bin --out "$edprof" >/dev/null 2>&1
edbody="$(cat "$edprof" 2>/dev/null)"
have "exec-dir: emits subpath for /usr/bin" '(subpath "/usr/bin")' "$edbody"

if [ "$SEATBELT_OK" = 1 ]; then
  if o="$("$SCRIPT" check-profile "$edprof" 2>&1)"; then
    ok "check-profile: --exec-dir profile compiles"
  else
    bad "check-profile: --exec-dir profile compiles" "$o"
  fi
elif command -v sandbox-exec >/dev/null 2>&1; then
  skipped "check-profile: --exec-dir profile compiles (sandbox-exec present but cannot apply a profile here, nested sandbox)"
else
  skipped "check-profile: --exec-dir profile compiles (sandbox-exec not available)"
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

if [ "$SEATBELT_OK" = 1 ]; then
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
    #
    # This needs its OWN profile, exec-granting the shell's dir rather than
    # $EDDIR. Under $edcprof the only exec grant is $EDDIR (/usr/bin, from
    # /usr/bin/true), and macOS bash is /bin/bash — so sandbox-exec denied the
    # EXEC and never reached the write, leaving this assertion green on
    # evidence it never gathered ("execvp() of '/bin/bash' failed").
    #
    # Assert on POSITIVE evidence, not on the target's absence. Absence is the
    # observable for EVERY way this can fail to run — an unrendered profile, a
    # malformed one, a denied exec — so inferring "the write rule fired" from
    # it is unsound, and greppng for execvp would patch only one of those. The
    # shell's own refusal message names the target, so require that instead;
    # every not-run mode then fails loudly rather than passing silently.
    #
    # /bin/bash explicitly, NOT `command -v bash`: on a host with Homebrew
    # bash first on PATH that resolves into the Cellar symlink farm, and
    # Seatbelt matches the RESOLVED path — so --exec-dir would not cover it
    # and the exec would be denied (see the Cellar grant above, same hazard).
    # This block only runs when SEATBELT_OK=1, which implies macOS.
    edwbin=/bin/bash
    edwprof="$BASE/execdir-write.sb"
    if ! "$SCRIPT" render-profile --exec-dir "$(dirname "$edwbin")" --rw "$RUN_WT" --out "$edwprof" >/dev/null 2>&1; then
      bad "exec-dir: write outside rw scope still denied" "render-profile failed — assertion never ran"
    else
      # Target passed as a quoted positional so a space/metachar in $BASE
      # cannot reparse the redirect (the convention the cred-ro block below
      # documents); $1 expands to the real path, so the grep matches exactly.
      edwout="$(sandbox-exec -f "$edwprof" "$edwbin" -c 'echo x > "$1"' _ "$BASE/exec-dir-denied" 2>&1)"
      if [ ! -f "$BASE/exec-dir-denied" ] \
        && printf '%s' "$edwout" | grep -qF "$BASE/exec-dir-denied: Operation not permitted"; then
        ok "exec-dir: write outside rw scope still denied"
      else
        bad "exec-dir: write outside rw scope still denied" "$edwout"
      fi
    fi
    rm -f "$BASE/exec-dir-denied" 2>/dev/null
    if [ -x "$BIN" ] && [ "$(dirname "$BIN")" != "$EDDIR" ]; then
      if sandbox-exec -f "$edcprof" "$BIN" >/dev/null 2>&1; then
        bad "exec-dir: exec outside allowed dirs still denied"
      else
        ok "exec-dir: exec outside allowed dirs still denied"
      fi
    else
      skipped "exec-dir: exec outside allowed dirs still denied (no distinct fixture binary)"
    fi
  else
    skipped "exec-dir: exec inside allowed dir succeeds (no true/echo/env fixture binary found)"
    skipped "exec-dir: write outside rw scope still denied (no true/echo/env fixture binary found)"
    skipped "exec-dir: exec outside allowed dirs still denied (no true/echo/env fixture binary found)"
  fi
elif command -v sandbox-exec >/dev/null 2>&1; then
  skipped "exec-dir: exec inside allowed dir succeeds (sandbox-exec present but cannot apply a profile here, nested sandbox)"
  skipped "exec-dir: write outside rw scope still denied (sandbox-exec present but cannot apply a profile here, nested sandbox)"
  skipped "exec-dir: exec outside allowed dirs still denied (sandbox-exec present but cannot apply a profile here, nested sandbox)"
else
  skipped "exec-dir: exec inside allowed dir succeeds (sandbox-exec not available)"
  skipped "exec-dir: write outside rw scope still denied (sandbox-exec not available)"
  skipped "exec-dir: exec outside allowed dirs still denied (sandbox-exec not available)"
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
if [ "$SEATBELT_OK" = 1 ] && [ -x /bin/launchctl ]; then
  # /bin is exec-allowed here, so only the explicit process-exec deny can block
  # launchctl — a clean signal the escape binary is walled off, not merely absent.
  if sandbox-exec -f "$escprof" /bin/launchctl help >/dev/null 2>&1; then
    bad "escape: launchctl exec denied even with /bin allowed"
  else
    ok "escape: launchctl exec denied even with /bin allowed"
  fi
elif command -v sandbox-exec >/dev/null 2>&1 && [ "$SEATBELT_OK" != 1 ]; then
  skipped "escape: launchctl exec denied even with /bin allowed (sandbox-exec present but cannot apply a profile here, nested sandbox)"
elif ! command -v sandbox-exec >/dev/null 2>&1; then
  skipped "escape: launchctl exec denied even with /bin allowed (sandbox-exec not available)"
else
  # Seatbelt works but the escape binary itself is missing. Ordered LAST on
  # purpose: tested first, this arm swallowed every Linux run (no launchctl
  # there either) and attributed the skip to a missing binary rather than to
  # the missing seatbelt — which both undercounted the skip tally by one and
  # left the no-seatbelt arms below unreachable on the hosts they describe.
  skipped "escape: launchctl exec denied even with /bin allowed (launchctl absent on this host)"
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
if [ "$SEATBELT_OK" = 1 ]; then
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
elif command -v sandbox-exec >/dev/null 2>&1; then
  skipped "check-profile: cred-ro profile compiles (sandbox-exec present but cannot apply a profile here, nested sandbox)"
  skipped "cred-ro: write to the state dir is allowed (sandbox-exec present but cannot apply a profile here, nested sandbox)"
  skipped "cred-ro: write to the credential file is denied (sandbox-exec present but cannot apply a profile here, nested sandbox)"
  skipped "cred-ro: the credential file is still readable (sandbox-exec present but cannot apply a profile here, nested sandbox)"
else
  skipped "check-profile: cred-ro profile compiles (sandbox-exec not available)"
  skipped "cred-ro: write to the state dir is allowed (sandbox-exec not available)"
  skipped "cred-ro: write to the credential file is denied (sandbox-exec not available)"
  skipped "cred-ro: the credential file is still readable (sandbox-exec not available)"
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
if [ "$SEATBELT_OK" = 1 ]; then
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
elif command -v sandbox-exec >/dev/null 2>&1; then
  skipped "check-profile: --workdir profile compiles (sandbox-exec present but cannot apply a profile here, nested sandbox)"
  skipped "--workdir: write elsewhere in the run worktree is allowed (sandbox-exec present but cannot apply a profile here, nested sandbox)"
  skipped "--workdir: write to the supervisor-state ledger is denied (sandbox-exec present but cannot apply a profile here, nested sandbox)"
else
  skipped "check-profile: --workdir profile compiles (sandbox-exec not available)"
  skipped "--workdir: write elsewhere in the run worktree is allowed (sandbox-exec not available)"
  skipped "--workdir: write to the supervisor-state ledger is denied (sandbox-exec not available)"
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
# plist injection: an XML-metachar path must still yield a VALID plist (escaped).
# Rendered unconditionally: the content assertion below is the only guard CI has
# against the patsub_replacement corruption, and CI is Linux (no plutil), on the
# very bash >= 5.2 that reproduces it.
mkdir -p "$BASE/a&b<x"
"$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$BASE/a&b<x" \
  --log "$BASE/a&b<x/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.esc --claude-bin "$BIN" \
  --path "$LAUNCH_PATH" --tmpdir "$BASE/root/wt/tmp" --out-script "$BASE/e.sh" --out-plist "$BASE/e.plist" >/dev/null 2>&1
have "launch: XML-metachar path is escaped, not mangled" "a&amp;b&lt;x" "$(cat "$BASE/e.plist" 2>/dev/null)"
if command -v plutil >/dev/null 2>&1; then
  if plutil -lint "$BASE/job.plist" >/dev/null 2>&1; then ok "launch: plist lints"; else bad "launch: plist lints"; fi
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

finish
