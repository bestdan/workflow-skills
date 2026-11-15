#!/usr/bin/env bash
# shellcheck disable=SC2016,SC2034,SC2154 # Fixtures hold literal shell source, retain outputs for diagnostics, and read names the prelude defines.
# The Linux supervisor (#1350): `launch` starts the wrapper as a transient
# `systemd-run --user` timer + service pair, `teardown` stops it, the supervisor
# verifies the stop, and the nono profile is pinned by checksum.
#
# One part of the orchestrator harness for scripts/spawn-orchestrator.sh. Every
# part is self-contained and offline: systemd-run, systemctl, nono and claude
# are stubs on PATH, and AUTO_PILOT_HOST_OS=Linux drives the Linux branch on
# any host. The real units were measured on the Linux test host (see the
# comment above _systemd_start). See scripts/lib/spawn-orchestrator-test-prelude.sh
# for what the parts share.
#
# Run directly: bash scripts/test-spawn-orchestrator-systemd.sh
set -uo pipefail
SO_PART=systemd
SO_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/spawn-orchestrator-test-prelude.sh
. "$SO_LIB/spawn-orchestrator-test-prelude.sh"

SD="$BASE/systemd"
SDSTUB="$SD/stub"
mkdir -p "$SDSTUB" "$SD/tmp" "$SD/launch"
SDTMP="$(cd "$SD/tmp" && pwd -P)"

# systemctl: records every call. `is-active` answers from $STUB_TIMER_ACTIVE
# (default: inactive, i.e. the stop took); `show -p MainPID` prints $STUB_PID;
# `show -p InvocationID` prints $STUB_OWN_INVOCATION.
cat >"$SDSTUB/systemctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"${STUB_SYSTEMCTL_LOG:?}"
case "$*" in
  *is-active*) [ "${STUB_TIMER_ACTIVE:-no}" = yes ] && exit 0; exit 3 ;;
  *"-p MainPID"*) echo "${STUB_PID:-0}" ;;
  *"-p InvocationID"*) echo "${STUB_OWN_INVOCATION:-}" ;;
esac
exit "${STUB_SYSTEMCTL_RC:-0}"
EOF
cat >"$SDSTUB/systemd-run" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"${STUB_SYSTEMD_RUN_LOG:?}"
exit "${STUB_SYSTEMD_RUN_RC:-0}"
EOF
# nono: drop everything up to `--` and run the rest, recording the token.
cat >"$SDSTUB/nono" <<'EOF'
#!/bin/sh
printf 'token=%s tmpdir=%s\n' "${CLAUDE_CODE_OAUTH_TOKEN:-<none>}" "${TMPDIR:-}" >>"${STUB_NONO_LOG:?}"
while [ $# -gt 0 ] && [ "$1" != -- ]; do shift; done
shift
exec "$@"
EOF
# claude: one stream-json line, as the smoke expects.
cat >"$SDSTUB/claude" <<'EOF'
#!/bin/sh
printf '{"type":"result","result":"ok"}\n'
EOF
chmod +x "$SDSTUB/systemctl" "$SDSTUB/systemd-run" "$SDSTUB/nono" "$SDSTUB/claude"
SD_PATH="$SDSTUB:$PATH"
NBIN="$SDSTUB/nono"
export STUB_SYSTEMCTL_LOG="$SD/systemctl.log" STUB_SYSTEMD_RUN_LOG="$SD/systemd-run.log" STUB_NONO_LOG="$SD/nono.log"

# A rendered nono profile and nono settings.
SDP="$SD/launch/profile.json"
AUTO_PILOT_HOST_OS=Linux "$SCRIPT" render-nono-profile --rw "$RUN_WT" --tmpdir "$SD/tmp" --out "$SDP" >/dev/null 2>&1
"$SCRIPT" render-settings --jail nono --source gh-issue --out "$SD/launch/settings.json" >/dev/null 2>&1
printf 'run the graph\n' >"$SD/launch/prompt.txt"
mkdir -p "$RUN_WT/.auto-pilot"

# write-launch, Linux, nono. Extra args last.
sdwl() { # <out-script> [args...]
  local out="$1"
  shift
  AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" "$SCRIPT" write-launch --jail nono --nono-bin "$NBIN" \
    --profile "$SDP" --settings "$SD/launch/settings.json" --workdir "$RUN_WT" \
    --log "$RUN_WT/.auto-pilot/orchestrator.log" --prompt-file "$SD/launch/prompt.txt" \
    --label com.autopilot.test.sd --claude-bin "$SDSTUB/claude" --path "/usr/bin:/bin" \
    --out-script "$out" "$@"
}

# --- write-launch: no plist on Linux, the profile pin, the write-grant refusal --
if [ ! -f "$SDP" ]; then
  bad "systemd: the fixture nono profile rendered" "render-nono-profile failed"
else
  out="$(sdwl "$SD/launch/launch.sh" 2>&1)"
  have "write-launch linux: needs no --out-plist" "launch written $SD/launch/launch.sh" "$out"
  if [ -e "$SD/launch/launch.plist" ]; then bad "write-launch linux: writes no plist"; else ok "write-launch linux: writes no plist"; fi
  w="$(cat "$SD/launch/launch.sh" 2>/dev/null)"
  sum="$(sha256sum <"$SDP" 2>/dev/null | awk '{print $1}')"
  [ -n "$sum" ] || sum="$(shasum -a 256 <"$SDP" | awk '{print $1}')"
  have "write-launch nono: the wrapper re-checks the profile's checksum every wake" \
    "check-nono-profile --dir $RUN_WT --label com.autopilot.test.sd --profile $SDP --sha256 $sum" "$w"
  pin_line="$(grep -n 'check-nono-profile' "$SD/launch/launch.sh" | cut -d: -f1)"
  jail_line="$(grep -n ' run --silent --profile ' "$SD/launch/launch.sh" | cut -d: -f1)"
  if [ -n "$pin_line" ] && [ -n "$jail_line" ] && [ "$pin_line" -lt "$jail_line" ]; then
    ok "write-launch nono: the pin runs before the jail starts"
  else
    bad "write-launch nono: the pin runs before the jail starts" "pin ${pin_line:-none}, jail ${jail_line:-none}"
  fi
  if o="$(AUTO_PILOT_HOST_OS=Darwin PATH="$SD_PATH" "$SCRIPT" write-launch --jail nono --nono-bin "$NBIN" \
    --profile "$SDP" --settings "$SD/launch/settings.json" --workdir "$RUN_WT" \
    --log "$RUN_WT/.auto-pilot/orchestrator.log" --prompt-file "$SD/launch/prompt.txt" \
    --label com.autopilot.test.sd --claude-bin "$SDSTUB/claude" --path "/usr/bin:/bin" \
    --out-script "$SD/launch/mac.sh" 2>&1)"; then
    bad "write-launch macOS: still requires --out-plist" "write-launch succeeded"
  else
    have "write-launch macOS: still requires --out-plist" "requires --out-script and --out-plist" "$o"
  fi
  if o="$(sdwl "$RUN_WT/launch.sh" 2>&1)"; then
    bad "write-launch nono: refuses a wrapper inside a write grant" "write-launch succeeded"
  else
    have "write-launch nono: refuses a wrapper inside a write grant" "inside the profile's write grant" "$o"
  fi
  cp "$SDP" "$RUN_WT/profile.json"
  if o="$(sdwl "$SD/launch/x.sh" --profile "$RUN_WT/profile.json" 2>&1)"; then
    bad "write-launch nono: refuses a profile inside a write grant" "write-launch succeeded"
  else
    have "write-launch nono: refuses a profile inside a write grant" "inside the profile's write grant" "$o"
  fi
  rm -f "$RUN_WT/profile.json"
  cp "$SCRIPT" "$RUN_WT/so.sh"
  if o="$(sdwl "$SD/launch/y.sh" --self "$RUN_WT/so.sh" 2>&1)"; then
    bad "write-launch nono: refuses a supervisor script inside a write grant" "write-launch succeeded"
  else
    have "write-launch nono: refuses a supervisor script inside a write grant" "pass --claude-token-cmd" "$o"
  fi
  rm -f "$RUN_WT/so.sh"
fi

# --- check-nono-profile --------------------------------------------------------
# A real git run dir, so a halt's run-state commit works for real.
sdrun() { # <dir>
  mkdir -p "$1/.auto-pilot"
  (cd "$1" && git init -q)
  printf -- '---\nstatus: active\npause_reason: \nexit_reason: continuing\n---\n' >"$1/.auto-pilot/RUN.md"
  printf '# report\n' >"$1/.auto-pilot/REPORT.md"
  (cd "$1" && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
}
if command -v git >/dev/null 2>&1 && [ -f "$SDP" ]; then
  sum="$(sha256sum <"$SDP" 2>/dev/null | awk '{print $1}')"
  [ -n "$sum" ] || sum="$(shasum -a 256 <"$SDP" | awk '{print $1}')"
  sdrun "$SD/pin-ok"
  if AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" "$SCRIPT" check-nono-profile --dir "$SD/pin-ok" \
    --label com.autopilot.test.sd --profile "$SDP" --sha256 "$sum" >/dev/null 2>&1; then
    ok "check-nono-profile: a matching profile passes"
  else
    bad "check-nono-profile: a matching profile passes"
  fi
  pin_fail() { # <name> <profile> <reason substring>
    local d rc o
    d="$SD/pin-$(printf '%s' "$1" | tr -c 'a-z0-9' '-')"
    sdrun "$d"
    : >"$STUB_SYSTEMCTL_LOG"
    o="$(AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" "$SCRIPT" check-nono-profile --dir "$d" \
      --label com.autopilot.test.sd --profile "$2" --sha256 "$sum" 2>&1)"
    rc=$?
    if [ "$rc" = 3 ]; then ok "check-nono-profile: $1 halts (exit 3)"; else bad "check-nono-profile: $1 halts (exit 3)" "rc=$rc $o"; fi
    have "check-nono-profile: $1 — RUN.md reads systemic" "status: systemic" "$(cat "$d/.auto-pilot/RUN.md")"
    have "check-nono-profile: $1 — REPORT.md says why" "$3" "$(head -3 "$d/.auto-pilot/REPORT.md")"
    have "check-nono-profile: $1 — the halt stops the timer" "--user stop com.autopilot.test.sd.timer" "$(cat "$STUB_SYSTEMCTL_LOG")"
  }
  cp "$SDP" "$SD/changed.json"
  printf ' ' >>"$SD/changed.json"
  pin_fail "a changed profile" "$SD/changed.json" "changed since launch"
  pin_fail "a missing profile" "$SD/absent.json" "is missing"
fi

# --- launch: systemd-run --user, the PID from systemctl, the handle -----------
if [ -f "$SDP" ]; then
  : >"$STUB_SYSTEMCTL_LOG"
  : >"$STUB_SYSTEMD_RUN_LOG"
  : >"$STUB_NONO_LOG"
  out="$(AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" STUB_PID="$$" "$SCRIPT" launch --jail nono --nono-bin "$NBIN" \
    --claude-token-cmd 'printf tok-sd' --profile "$SDP" --settings "$SD/launch/settings.json" \
    --workdir "$RUN_WT" --log "$RUN_WT/.auto-pilot/orchestrator.log" --prompt-file "$SD/launch/prompt.txt" \
    --label com.autopilot.test.sdl --claude-bin "$SDSTUB/claude" --path "/usr/bin:/bin" --interval 120 \
    --out-script "$SD/launch/l.sh" --handle "$SD/launch/handle" --report-gh "$GUARD/gh" \
    --handoff-dir "$SD/handoffs" 2>&1)"
  have "launch linux: reports the launched pid" "launched pid=$$ label=com.autopilot.test.sdl" "$out"
  have "launch linux: one transient timer + service pair, first wake now, later wakes after each ends" \
    "--user --unit=com.autopilot.test.sdl --description=auto-pilot orchestrator com.autopilot.test.sdl --on-active=0 --on-unit-inactive=120s --property=CollectMode=inactive-or-failed /bin/bash $SD/launch/l.sh" \
    "$(cat "$STUB_SYSTEMD_RUN_LOG")"
  have "launch linux: the PID comes from systemctl --user show" "--user show -p MainPID --value com.autopilot.test.sdl.service" "$(cat "$STUB_SYSTEMCTL_LOG")"
  have "launch linux: the handle records the wrapper's pid" "orchestrator_pid: $$" "$(cat "$SD/launch/handle" 2>/dev/null)"
  have "launch linux: the smoke ran through nono with the token" "token=tok-sd tmpdir=$SDTMP" "$(cat "$STUB_NONO_LOG")"
  first_stop="$(grep -n 'stop com.autopilot.test.sdl.timer' "$STUB_SYSTEMCTL_LOG" | head -1 | cut -d: -f1)"
  if [ -n "$first_stop" ]; then ok "launch linux: clears a stale pair before starting"; else bad "launch linux: clears a stale pair before starting" "$(cat "$STUB_SYSTEMCTL_LOG")"; fi

  : >"$STUB_SYSTEMCTL_LOG"
  if o="$(AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" STUB_PID=0 "$SCRIPT" launch --jail nono --nono-bin "$NBIN" \
    --profile "$SDP" --settings "$SD/launch/settings.json" --workdir "$RUN_WT" \
    --log "$RUN_WT/.auto-pilot/orchestrator.log" --prompt-file "$SD/launch/prompt.txt" \
    --label com.autopilot.test.sdn --claude-bin "$SDSTUB/claude" --path "/usr/bin:/bin" \
    --out-script "$SD/launch/n.sh" --handle "$SD/launch/handle-n" --report-gh "$GUARD/gh" \
    --handoff-dir "$SD/handoffs" 2>&1)"; then
    bad "launch linux: no PID tears the pair down and fails" "launch succeeded"
  else
    have "launch linux: no PID tears the pair down and fails" "could not read the orchestrator PID from systemctl --user show" "$o"
  fi

  out="$(AUTO_PILOT_HOST_OS=Linux "$SCRIPT" launch --dry-run --label com.autopilot.test.sdd --handle "$SD/h" \
    --out-script "$SD/d.sh" --interval 90 2>&1)"
  have "launch --dry-run linux: names systemd-run, not launchctl" \
    "detach        (systemd-run --user --unit=com.autopilot.test.sdd --on-active=0 --on-unit-inactive=90s)" "$out"
fi

# --- teardown: timer first, the service only from outside it ------------------
: >"$STUB_SYSTEMCTL_LOG"
AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" "$SCRIPT" teardown --label com.autopilot.test.td >/dev/null 2>&1
td="$(cat "$STUB_SYSTEMCTL_LOG")"
if [ "$(sed -n 1p <<<"$td")" = "--user stop com.autopilot.test.td.timer" ]; then
  ok "teardown linux: stops the timer first"
else
  bad "teardown linux: stops the timer first" "$td"
fi
have "teardown linux: from outside, stops the service too" "--user stop com.autopilot.test.td.service" "$td"
have "teardown linux: clears a failed pair so the label can relaunch" "--user reset-failed com.autopilot.test.td.timer com.autopilot.test.td.service" "$td"
: >"$STUB_SYSTEMCTL_LOG"
AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" INVOCATION_ID=abc STUB_OWN_INVOCATION=abc \
  "$SCRIPT" teardown --label com.autopilot.test.td >/dev/null 2>&1
td="$(cat "$STUB_SYSTEMCTL_LOG")"
have "teardown linux: from inside the wake, still stops the timer" "--user stop com.autopilot.test.td.timer" "$td"
lack "teardown linux: from inside the wake, never stops its own service (that kills the wake mid-teardown)" \
  "stop com.autopilot.test.td.service" "$td"
: >"$STUB_SYSTEMCTL_LOG"
AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" INVOCATION_ID=abc STUB_OWN_INVOCATION=other \
  "$SCRIPT" teardown --label com.autopilot.test.td >/dev/null 2>&1
have "teardown linux: inside a different unit, stops the service" "--user stop com.autopilot.test.td.service" "$(cat "$STUB_SYSTEMCTL_LOG")"

# --- the supervisor tears the pair down on done, deadline and systemic --------
# Through the real generated wrapper: the stub claude declares the exit reason
# exactly as the orchestrator prompt does.
cat >"$SDSTUB/claude-declare" <<'EOF'
#!/bin/sh
printf '{"type":"assistant","message":{"content":[{"type":"text","text":"stub wake"}]}}\n'
[ -n "${STUB_DECLARE:-}" ] && "${STUB_SELF:?}" exit-reason --dir "${STUB_RUN_DIR:?}" --reason "$STUB_DECLARE" >/dev/null 2>&1
exit 0
EOF
chmod +x "$SDSTUB/claude-declare"
if command -v git >/dev/null 2>&1 && [ -f "$SDP" ]; then
  sd_wake() { # <name> <declared reason>
    local d="$SD/wake-$1"
    sdrun "$d"
    : >"$d/.auto-pilot/orchestrator.log"
    AUTO_PILOT_HOST_OS=Linux "$SCRIPT" render-nono-profile --rw "$d" --tmpdir "$SD/tmp" --out "$SD/launch/p-$1.json" >/dev/null 2>&1
    AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" "$SCRIPT" write-launch --jail nono --nono-bin "$NBIN" \
      --profile "$SD/launch/p-$1.json" --settings "$SD/launch/settings.json" --workdir "$d" \
      --log "$d/.auto-pilot/orchestrator.log" --prompt-file "$SD/launch/prompt.txt" \
      --label "com.autopilot.test.w$1" --claude-bin "$SDSTUB/claude-declare" --path "$SD_PATH" \
      --report-every off --out-script "$SD/launch/w-$1.sh" >/dev/null 2>&1
    : >"$STUB_SYSTEMCTL_LOG"
    AUTO_PILOT_HOST_OS=Linux STUB_SELF="$SCRIPT" STUB_RUN_DIR="$d" STUB_DECLARE="$2" \
      bash "$SD/launch/w-$1.sh" >/dev/null 2>&1
  }
  for r in "done" deadline systemic; do
    sd_wake "$r" "$r"
    have "supervisor linux [$r]: the wake stopped the timer" "--user stop com.autopilot.test.w$r.timer" "$(cat "$STUB_SYSTEMCTL_LOG")"
    if [ -f "$SD/wake-$r/.auto-pilot/orchestrator.done" ]; then
      ok "supervisor linux [$r]: the done-sentinel is written"
    else
      bad "supervisor linux [$r]: the done-sentinel is written"
    fi
  done
  sd_wake continuing continuing
  lack "supervisor linux [continuing]: no teardown, the timer wakes it again" "stop com.autopilot.test.wcontinuing" "$(cat "$STUB_SYSTEMCTL_LOG")"

  # A stop that fails leaves the timer active: the halt warns and still finishes.
  d="$SD/stop-fails"
  mkdir -p "$d/.auto-pilot"
  (cd "$d" && git init -q)
  printf -- '---\nstatus: active\npause_reason: \nexit_reason: done\nexit_reason_at: 9999999999\n---\n' >"$d/.auto-pilot/RUN.md"
  printf '# report\n' >"$d/.auto-pilot/REPORT.md"
  printf 'ok\n' >"$d/log"
  : >"$STUB_SYSTEMCTL_LOG"
  err="$(AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" STUB_TIMER_ACTIVE=yes STUB_SYSTEMCTL_RC=1 \
    "$SCRIPT" supervisor-check --exit-code 0 --wake-start 1 --log "$d/log" --dir "$d" \
    --label com.autopilot.test.sf --state "$SD/sf-state" 2>&1 >/dev/null)"
  rc=$?
  have "supervisor linux [stop fails]: a still-active timer is reported LOUDLY with the command to run" \
    "remove it by hand: systemctl --user stop com.autopilot.test.sf.timer com.autopilot.test.sf.service" "$err"
  if [ "$rc" = 0 ] && [ -f "$d/.auto-pilot/orchestrator.done" ]; then
    ok "supervisor linux [stop fails]: the warning does not abort the halt (exit 0, sentinel written)"
  else
    bad "supervisor linux [stop fails]: the warning does not abort the halt (exit 0, sentinel written)" "rc=$rc"
  fi
  count_is "supervisor linux [stop fails]: the stop is retried once" 2 "stop com.autopilot.test.sf.timer" "$(cat "$STUB_SYSTEMCTL_LOG")"
fi

# --- the verify broker on Linux (#1351) ----------------------------------------
# write-verify-broker writes only the script; detach starts it as its own
# transient pair; every supervisor teardown stops it too.
VBL="$SD/broker"
mkdir -p "$VBL/sent"
sdvb() { # [args...]
  AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" "$SCRIPT" write-verify-broker --sentinel-dir "$VBL/sent" \
    --verify-cmd 'bash scripts/check.sh' --confine-under "$RUN_WT" --label com.autopilot.test.vb \
    --workdir "$RUN_WT" --log "$VBL/broker.log" --path "/usr/bin:/bin" "$@"
}
if [ -f "$SDP" ]; then
  out="$(sdvb --nono-profile "$SDP" --out-script "$VBL/broker.sh" 2>&1)"
  have "write-verify-broker linux: needs no --out-plist" "verify-broker written $VBL/broker.sh (pin" "$out"
  have "write-verify-broker linux: the script runs the pinned broker" "verify-broker" "$(cat "$VBL/broker.sh" 2>/dev/null)"
  lack "write-verify-broker linux: the broker is unjailed (no nono)" " run --silent --profile " "$(cat "$VBL/broker.sh" 2>/dev/null)"
  if o="$(sdvb --out-script "$VBL/b2.sh" 2>&1)"; then
    bad "write-verify-broker linux: requires --nono-profile" "write-verify-broker succeeded"
  else
    have "write-verify-broker linux: requires --nono-profile" "requires --nono-profile" "$o"
  fi
  if o="$(sdvb --nono-profile "$SDP" --out-script "$RUN_WT/broker.sh" 2>&1)"; then
    bad "write-verify-broker linux: refuses a broker script inside a write grant" "write-verify-broker succeeded"
  else
    have "write-verify-broker linux: refuses a broker script inside a write grant" "inside the profile's write grant" "$o"
  fi
  rm -f "$RUN_WT/broker.sh"

  : >"$STUB_SYSTEMCTL_LOG"
  : >"$STUB_SYSTEMD_RUN_LOG"
  out="$(AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" "$SCRIPT" detach --label com.autopilot.test.vb \
    --script "$VBL/broker.sh" --interval 10 2>&1)"
  have "detach linux: the broker is its own transient timer + service pair" \
    "--user --unit=com.autopilot.test.vb --description=auto-pilot orchestrator com.autopilot.test.vb --on-active=0 --on-unit-inactive=10s --property=CollectMode=inactive-or-failed /bin/bash $VBL/broker.sh" \
    "$(cat "$STUB_SYSTEMD_RUN_LOG")"
  have "detach linux: clears a stale broker pair first" "--user stop com.autopilot.test.vb.timer" "$(cat "$STUB_SYSTEMCTL_LOG")"
  if o="$(AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" "$SCRIPT" detach --script "$VBL/broker.sh" 2>&1)"; then
    bad "detach linux: requires --label" "detach succeeded"
  else
    have "detach linux: requires --label" "requires --label" "$o"
  fi

  # teardown stops the broker beside the supervisor.
  : >"$STUB_SYSTEMCTL_LOG"
  AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" AUTO_PILOT_BROKER_LABEL=com.autopilot.test.vb \
    "$SCRIPT" teardown --label com.autopilot.test.td >/dev/null 2>&1
  td="$(cat "$STUB_SYSTEMCTL_LOG")"
  have "teardown linux: stops the supervisor's timer" "--user stop com.autopilot.test.td.timer" "$td"
  have "teardown linux: stops the broker's timer alongside" "--user stop com.autopilot.test.vb.timer" "$td"
  have "teardown linux: stops the broker's service too" "--user stop com.autopilot.test.vb.service" "$td"
  : >"$STUB_SYSTEMCTL_LOG"
  AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" AUTO_PILOT_BROKER_LABEL='bad label;x' \
    "$SCRIPT" teardown --label com.autopilot.test.td >/dev/null 2>&1
  lack "teardown linux: ignores a broker label outside the label charset" "bad label" "$(cat "$STUB_SYSTEMCTL_LOG")"

  # macOS: the same teardown boots both jobs out, the broker FIRST. A bootout
  # of the job teardown runs inside may end it before a second bootout runs.
  # The prelude's launchctl guard stub records every call to NOTIFY_GUARD_LOG.
  : >"$SD/launchctl.log"
  NOTIFY_GUARD_LOG="$SD/launchctl.log" AUTO_PILOT_HOST_OS=Darwin AUTO_PILOT_BROKER_LABEL=com.autopilot.test.vb \
    "$SCRIPT" teardown --label com.autopilot.test.td >/dev/null 2>&1
  lc="$(grep '^launchctl: bootout' "$SD/launchctl.log")"
  if [ "$(sed -n 1p <<<"$lc")" = "launchctl: bootout gui/$(id -u)/com.autopilot.test.vb" ] \
    && [ "$(sed -n 2p <<<"$lc")" = "launchctl: bootout gui/$(id -u)/com.autopilot.test.td" ]; then
    ok "teardown macOS: boots the broker out first, then the supervisor"
  else
    bad "teardown macOS: boots the broker out first, then the supervisor" "${lc:-<no bootout>}"
  fi

  # write-launch bakes the broker label into the wrapper, and a real wake that
  # declares done stops both.
  sdwl "$SD/launch/bl.sh" --broker-label com.autopilot.test.vb >/dev/null 2>&1
  have "write-launch: bakes the broker label into the wrapper" "export AUTO_PILOT_BROKER_LABEL=com.autopilot.test.vb" \
    "$(cat "$SD/launch/bl.sh" 2>/dev/null)"
  if o="$(sdwl "$SD/launch/bl2.sh" --broker-label com.autopilot.test.sd 2>&1)"; then
    bad "write-launch: refuses a broker label equal to the supervisor's" "write-launch succeeded"
  else
    have "write-launch: refuses a broker label equal to the supervisor's" "must differ from --label" "$o"
  fi
  if command -v git >/dev/null 2>&1; then
    d="$SD/wake-broker"
    sdrun "$d"
    : >"$d/.auto-pilot/orchestrator.log"
    AUTO_PILOT_HOST_OS=Linux "$SCRIPT" render-nono-profile --rw "$d" --tmpdir "$SD/tmp" --out "$SD/launch/p-broker.json" >/dev/null 2>&1
    AUTO_PILOT_HOST_OS=Linux PATH="$SD_PATH" "$SCRIPT" write-launch --jail nono --nono-bin "$NBIN" \
      --profile "$SD/launch/p-broker.json" --settings "$SD/launch/settings.json" --workdir "$d" \
      --log "$d/.auto-pilot/orchestrator.log" --prompt-file "$SD/launch/prompt.txt" \
      --label com.autopilot.test.wbroker --claude-bin "$SDSTUB/claude-declare" --path "$SD_PATH" \
      --report-every off --broker-label com.autopilot.test.vb --out-script "$SD/launch/w-broker.sh" >/dev/null 2>&1
    : >"$STUB_SYSTEMCTL_LOG"
    AUTO_PILOT_HOST_OS=Linux STUB_SELF="$SCRIPT" STUB_RUN_DIR="$d" STUB_DECLARE="done" \
      bash "$SD/launch/w-broker.sh" >/dev/null 2>&1
    have "supervisor linux [done]: the wake stops the broker's timer too" "--user stop com.autopilot.test.vb.timer" "$(cat "$STUB_SYSTEMCTL_LOG")"
  fi
fi

# shellcheck source=scripts/lib/spawn-orchestrator-test-epilogue.sh
. "$SO_LIB/spawn-orchestrator-test-epilogue.sh"
