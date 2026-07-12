#!/usr/bin/env bash
# smoke-confinement.sh — the PRE-484 confinement smoke, as an executable so it
# never has to be pasted. Proves the generated jail actually REFUSES disallowed
# filesystem writes/reads, unlisted exec, and — the load-bearing test — raw-socket
# network egress. check.sh proves generation+compile; THIS proves confinement.
#
# Run:  bash scripts/smoke-confinement.sh
# macOS only (sandbox-exec / launchctl). Runs a few real `claude -p` invocations
# under the jail (uses your credentials + a little usage). Exits non-zero if any
# confinement check fails.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SO="$ROOT/scripts/spawn-orchestrator.sh"
command -v sandbox-exec >/dev/null 2>&1 || { echo "sandbox-exec required (macOS only)"; exit 2; }

D="$(mktemp -d)"; D="$(cd "$D" && pwd -P)"
trap 'launchctl bootout "gui/$(id -u)/com.autopilot.smoke" 2>/dev/null; rm -rf "$D"' EXIT
mkdir -p "$D/run/wt" "$D/creds"; echo secret >"$D/creds/token"

pass=0; fail=0; indet=0
PASS(){ pass=$((pass+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
FAIL(){ fail=$((fail+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
INDET(){ indet=$((indet+1)); printf '  \033[33m????\033[0m  %s\n' "$1"; }

echo "== 0. Build the jail =="
CLAUDE="$(command -v claude || true)"; [ -n "$CLAUDE" ] || { echo "claude not on PATH"; exit 2; }
"$SO" render-profile --confine-under "$D/run" \
  --rw "$D/run/wt" --ro "$ROOT" --ro "$HOME/.claude" \
  --exec "$CLAUDE" --exec "$(command -v bash)" --exec "$(command -v git)" \
  --out "$D/profile.sb" || { echo "render-profile failed"; exit 2; }
"$SO" check-profile "$D/profile.sb" || { echo "profile does not compile"; exit 2; }
"$SO" render-settings --source plan --coder codex --out "$D/settings.json" || exit 2
echo "  profile + settings built"

# denied() runs a single action under the seatbelt profile ONLY and passes when it
# is refused (non-zero). allowed() passes when it succeeds (zero).
run_jailed(){ sandbox-exec -f "$D/profile.sb" "$@" >/dev/null 2>&1; }
denied(){ local d="$1"; shift; if run_jailed "$@"; then FAIL "$d (was ALLOWED — breach)"; else PASS "$d (denied)"; fi; }
allowed(){ local d="$1"; shift; if run_jailed "$@"; then PASS "$d (allowed)"; else FAIL "$d (was denied)"; fi; }

echo "== 1. Layer 1 — filesystem + exec =="
denied  "write outside the worktree"  bash -c "echo x > $HOME/AUTOPILOT_SMOKE_SHOULD_NOT_EXIST"
allowed "write inside the worktree"   bash -c "echo x > $D/run/wt/ok"
denied  "read /etc/sudoers"           bash -c "cat /etc/sudoers"
denied  "exec unlisted /usr/bin/python3" /usr/bin/python3 -c "print(1)"
# The harness-runtime grant (task 12) opens ~/.claude/session-env for writes. It
# must NOT have opened the rest of ~/.claude — a blanket state-dir write would
# put the credential file in a writable scope (the very thing --cred-ro exists to
# prevent). Assert the neighbouring write is still refused.
denied  "write elsewhere under ~/.claude (session-env grant is not blanket)" \
        bash -c "echo x > $HOME/.claude/AUTOPILOT_SMOKE_SHOULD_NOT_EXIST"
rm -f "$HOME/AUTOPILOT_SMOKE_SHOULD_NOT_EXIST" "$HOME/.claude/AUTOPILOT_SMOKE_SHOULD_NOT_EXIST" 2>/dev/null

# --- exit-code integrity (task 12 / finding #20) --------------------------
# A jail that can't report a correct exit code is a BROKEN jail. When the
# harness's OWN runtime surface is denied, the Bash tool dies with EPERM BEFORE
# it ever runs the command (it can't mkdir its per-session dirs), and every Bash
# tool call reports exit 1 regardless of the command's real result — so an agent
# verifying by `$?` is reading pure noise. This is the real regression guard for
# task 12: it FAILS against the pre-fix profile and PASSES once the harness's own
# runtime paths are permitted (the renderer emits them; see @@HARNESS_RUNTIME@@).
# It must run THROUGH `claude -p` itself, not bare sandbox-exec — only the real
# harness exercises the session-env/scratch mkdirs and the mux socket.
echo "== 1b. Exit-code integrity through the harness (claude -p) =="
JSON="$(cat "$D/settings.json")"
EC_LOG="$D/exit-code.log"
exit_code_check(){ # <desc> <expect-rc> <shell-command> <rcfile-suffix>
  local desc="$1" expect="$2" cmd="$3" name="rc_ec_$4"
  local rcf="$D/run/wt/$name"; rm -f "$rcf"
  sandbox-exec -f "$D/profile.sb" "$CLAUDE" -p \
    "Run exactly this one bash command and then stop, nothing else: { $cmd ; } ; printf '%s' \$? > $rcf" \
    --permission-mode bypassPermissions --settings "$JSON" --max-turns 4 \
    --verbose --output-format stream-json >>"$EC_LOG" 2>&1
  # No rc file is NOT indeterminate here — it is the exact pre-fix symptom (the
  # Bash tool EPERM'd before running anything), so it must FAIL, not INDET.
  if [ ! -s "$rcf" ]; then
    FAIL "$desc (no rc recorded — the Bash tool never ran; harness runtime paths denied?)"
    return
  fi
  local rc; rc="$(cat "$rcf")"
  [ "$rc" = "$expect" ] && PASS "$desc (rc=$rc)" || FAIL "$desc (rc=$rc, expected $expect — the jail is lying about exit codes)"
}
exit_code_check "true reports exit 0"  0 true  true
exit_code_check "false reports exit 1" 1 false false
# The inner sandbox must actually initialize — a denied mux-socket bind/listen
# makes the harness silently disable its own sandboxing, degrading the documented
# two-layer posture to one layer.
if grep -q 'Sandbox is enabled but failed to initialize' "$EC_LOG" 2>/dev/null; then
  FAIL "inner sandbox initializes (found 'failed to initialize' — mux socket denied)"
else
  PASS "inner sandbox initializes (no 'failed to initialize' line)"
fi

echo "== 2. Layer 2 — network egress (through claude --settings) =="
# egress_check writes the curl/socket exit code to a file in the RW worktree from
# INSIDE the jailed claude, so we read a deterministic rc instead of parsing prose.
egress_check(){ # <desc> <expect: reach|block> <rcfile-name> <shell-command>
  local desc="$1" expect="$2" name="$3" cmd="$4"
  local rcf="$D/run/wt/$name"; rm -f "$rcf"
  sandbox-exec -f "$D/profile.sb" "$CLAUDE" -p \
    "Run exactly this one bash command and then stop, nothing else: { $cmd ; } ; printf '%s' \$? > $rcf" \
    --permission-mode bypassPermissions --settings "$JSON" --max-turns 4 >/dev/null 2>&1
  if [ ! -s "$rcf" ]; then INDET "$desc (claude didn't record an rc — check manually)"; return; fi
  local rc; rc="$(cat "$rcf")"
  case "$expect" in
    reach) [ "$rc" = 0 ] && PASS "$desc (reached, rc=0)" || FAIL "$desc (rc=$rc — allowlisted host was blocked)" ;;
    block) [ "$rc" != 0 ] && PASS "$desc (blocked, rc=$rc)" || FAIL "$desc (rc=0 — EGRESS ESCAPED)" ;;
  esac
}
egress_check "(a) allowlisted api.github.com" reach rc_a 'curl -sS --max-time 8 -o /dev/null https://api.github.com'
egress_check "(b) non-allowlisted example.com" block rc_b 'curl -sS --max-time 8 -o /dev/null https://example.com'
egress_check "(c) raw IP 1.1.1.1 [decider]"     block rc_c 'curl -sS --max-time 8 -o /dev/null http://1.1.1.1'
egress_check "(d) raw socket /dev/tcp [decider]" block rc_d 'exec 3<>/dev/tcp/1.1.1.1/80'

echo "== 3. Detach + supervisor lifecycle =="
printf 'noop\n' >"$D/run/wt/prompt.txt"
if "$SO" write-launch --profile "$D/profile.sb" --settings "$D/settings.json" \
     --workdir "$D/run/wt" --log "$D/orch.log" --prompt-file "$D/run/wt/prompt.txt" \
     --until T --label com.autopilot.smoke --claude-bin "$CLAUDE" --path "$PATH" --tmpdir "$D/tmp" \
     --out-script "$D/launch.sh" --out-plist "$D/job.plist" >/dev/null 2>&1 \
   && plutil -lint "$D/job.plist" >/dev/null 2>&1; then
  PASS "write-launch produced a valid plist"
else
  FAIL "write-launch / plist"
fi
if launchctl bootstrap "gui/$(id -u)" "$D/job.plist" 2>/dev/null \
   && launchctl print "gui/$(id -u)/com.autopilot.smoke" 2>/dev/null | grep -q 'pid = '; then
  PASS "job bootstrapped with a pid"
else
  INDET "launchd bootstrap (may need Full Disk Access / a real login session)"
fi
"$SO" teardown --label com.autopilot.smoke >/dev/null 2>&1
launchctl print "gui/$(id -u)/com.autopilot.smoke" >/dev/null 2>&1 \
  && FAIL "teardown left the job loaded" || PASS "teardown removed the job"

echo
echo "== Summary =="
printf '  %d passed, %d failed, %d indeterminate\n' "$pass" "$fail" "$indet"
[ "$fail" = 0 ] && echo "  ✅ confinement holds — the (c)/(d) egress deciders passed if listed above." \
                || echo "  ❌ a wall did NOT hold — see FAIL lines (a (c)/(d) FAIL means raw egress escaped → layer-1 fix needed)."
[ "$fail" = 0 ]
