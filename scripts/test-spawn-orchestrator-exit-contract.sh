#!/usr/bin/env bash
# shellcheck disable=SC1010,SC2034,SC2154 # Fixtures intentionally contain shell source, retain outputs for diagnostics, and read names the prelude defines.
# The exit contract (declared reason) + the heartbeat (task 15).
#
# One part of the orchestrator harness for scripts/spawn-orchestrator.sh. Every
# part is self-contained and offline: the prelude builds it a private fixture
# tree and the isolation guards, the epilogue asserts they held. See
# scripts/lib/spawn-orchestrator-test-prelude.sh for what the parts share, and
# dev_docs/gate-performance.md for why this suite is several files.
#
# Run directly: bash scripts/test-spawn-orchestrator-exit-contract.sh
set -uo pipefail
SO_PART=exit-contract
# The lib dir goes in a dedicated name, never $ROOT: fixtures below reassign
# ROOT for their own trees (the verify-branch block does ROOT="$VB/root"), so
# sourcing the epilogue off $ROOT would resolve inside a fixture instead.
SO_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/spawn-orchestrator-test-prelude.sh
. "$SO_LIB/spawn-orchestrator-test-prelude.sh"

# Two classify-exit log fixtures. In the monolith these were built by the
# classify-exit tests ~1300 lines earlier and read here as leftovers — the one
# fixture dependency the split had to rebuild rather than hoist, because the
# supervisor part asserts on them and this part only feeds them to
# supervisor-check. Two printfs are cheaper than a shared fixture nobody owns.
CX="$BASE/cx"
mkdir -p "$CX"
printf 'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}\n' >"$CX/auth.log"
printf 'some other unrelated crash\n' >"$CX/weird.log"

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
      if grep -q 'bootout' <<<"$lc"; then
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
      if grep -q 'bootout' <<<"$lc"; then
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
  # Stored whole rather than composed at each call site, so the epilogue's
  # notifier-guard loop can assert against the PATH these fixtures really run
  # under. A reconstruction there would have to re-inject $GUARD to pass, which
  # makes the assertion unfalsifiable — see the epilogue's enumeration.
  STUBF_PATH="$STUBF:$GUARD:/usr/bin:/bin"
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
  bferr="$(STUB_LAUNCHCTL_LOG="$BF/launchctl.log" PATH="$STUBF_PATH" \
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
  hbout="$(STUB_LAUNCHCTL_LOG="$HB/launchctl.log" PATH="$STUBF_PATH" \
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
  dsout="$(STUB_LAUNCHCTL_LOG="$DS/launchctl.log" PATH="$STUBF_PATH" \
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
  npout="$(STUB_LAUNCHCTL_LOG="$NP26/launchctl.log" PATH="$STUBF_PATH" \
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
  [ "$ecc" = 2 ] && grep -qF 'unknown exit reason' <<<"$o" \
    && ok "exit-reason fail-closed: unknown reason" || bad "exit-reason fail-closed: unknown reason" "$o"
  o="$("$SCRIPT" teardown --label com.autopilot.ec.x --reason continuing 2>&1)"
  tdc=$?
  [ "$tdc" = 2 ] && grep -qF 'must be a TERMINAL exit reason' <<<"$o" \
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

# shellcheck source=scripts/lib/spawn-orchestrator-test-epilogue.sh
. "$SO_LIB/spawn-orchestrator-test-epilogue.sh"
