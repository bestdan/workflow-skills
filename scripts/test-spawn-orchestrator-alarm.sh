#!/usr/bin/env bash
# shellcheck disable=SC1010,SC2034,SC2154 # Fixtures intentionally contain shell source, retain outputs for diagnostics, and read names the prelude defines.
# The ALARM: a halted or stalled run must actively TELL a human (task 16).
#
# One part of the orchestrator harness for scripts/spawn-orchestrator.sh. Every
# part is self-contained and offline: the prelude builds it a private fixture
# tree and the isolation guards, the epilogue asserts they held. See
# scripts/lib/spawn-orchestrator-test-prelude.sh for what the parts share, and
# dev_docs/gate-performance.md for why this suite is several files.
#
# Run directly: bash scripts/test-spawn-orchestrator-alarm.sh
set -uo pipefail
SO_PART=alarm
# The lib dir goes in a dedicated name, never $ROOT: fixtures below reassign
# ROOT for their own trees (the verify-branch block does ROOT="$VB/root"), so
# sourcing the epilogue off $ROOT would resolve inside a fixture instead.
SO_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/spawn-orchestrator-test-prelude.sh
. "$SO_LIB/spawn-orchestrator-test-prelude.sh"

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

# shellcheck source=scripts/lib/spawn-orchestrator-test-epilogue.sh
. "$SO_LIB/spawn-orchestrator-test-epilogue.sh"
