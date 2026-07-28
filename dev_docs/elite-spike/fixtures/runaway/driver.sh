#!/usr/bin/env bash
# Probe 5b driver — drives the REAL supervisor path against a scratch run dir.
#
# DISPOSABLE SPIKE CODE (design §0a rule 4). Never promoted to production by
# renaming.
#
# Only the agent is a surrogate. `supervisor-scan`, `supervisor-gate`,
# `supervisor-check`, `alarm` and the pause-exempt ledger are the repo's real
# scripts/spawn-orchestrator.sh (§7a rule 2: exercise the assumption through the
# real boundary). Nothing here launches `claude`, reaches a network, touches a
# real remote or writes a tracker.
#
# CONTAINMENT. Probe 5's incident record (dev_docs/tasks/probe5-incident-evidence/,
# which outlives this spike and must not be touched) is a supervisor bootstrapped
# in uid mode against the maintainer's own uid, which reaped every SSH login for
# four days. This fixture spawns processes and asserts they get reaped: the same
# hazard class. So every rule below is a construction-time assertion that fails
# CLOSED, not a prohibition an implementer could satisfy while still being unsafe:
#
#   * unprivileged, no sudo, no launchd bootstrap into a real uid domain — wakes
#     come from the loop below, never from launchctl;
#   * pgid(surrogate) != pgid(driver) AND pgid(surrogate) == pid(surrogate);
#   * same_incarnation(recorded, live) immediately before EVERY signal;
#   * no uid resolved numerically;
#   * a unique random launchd label, asserted absent from `launchctl list`
#     before use, because spawn-orchestrator's teardown runs
#     `launchctl bootout gui/<uid>/<label>` unconditionally (:1694) and a
#     colliding label would boot out a live job this fixture never created;
#   * TWO independent deadline mechanisms — a parent-death FIFO and a watchdog
#     in its own group, spawned FIRST — because a wedged driver enforces nothing
#     and the surrogate is deliberately long-lived and uncooperative.
#
# No pgid is ever formatted into a shell word here. `kill -- -$pgid` with an
# empty variable signals the CALLER'S OWN group, which is the outage's exact
# symptom shape, so every signal goes through common.py's validated path
# instead. That is why this file contains no `kill` at all.
#
# NB: this tree is deliberately excluded from dprint and shfmt (dprint.json,
# scripts/lint-shell.sh) because results.json pins sha256s of it. Do not format.

set -euo pipefail

FIXDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$FIXDIR/../../../.." && pwd)"
ORCH="$REPO_ROOT/scripts/spawn-orchestrator.sh"

LEG="smoke"
WAKES=6
UNTIL_DELTA=900
PAUSE_EXEMPT_MAX=60
PARK_LIMIT=3
PAUSE_SECONDS=4
COMPLETE_AFTER=3
HEARTBEAT_EVERY=1
# The watchdog's absolute bound, and a real one — not a formality. Legs 1 and 2
# run six and three rows respectively, each of which drives its own wake loop, so
# the smoke leg's 180s would fire mid-leg and be indistinguishable from a
# containment failure. Raised, never removed: this is the mechanism that keeps
# the runaway probe from becoming the runaway.
ABSOLUTE_DEADLINE_SECONDS=900
EVIDENCE=""
SCRATCH=""
KEEP_SCRATCH=0
ACCOUNT=""
PYBIN=""
BASELINE=""

die() { echo "driver: FAIL-CLOSED: $*" >&2; exit 2; }

usage() {
  cat <<EOF
usage: driver.sh [options]
  --leg NAME               leg to run (default: smoke; only armed legs allowed)
  --wakes N                supervisor wakes per variant (default: $WAKES)
  --until-delta SECONDS    how far ahead the run's real --until sits (default: $UNTIL_DELTA)
  --pause-exempt-max SEC   supervisor-scan --pause-exempt-max (default: $PAUSE_EXEMPT_MAX)
  --pause-seconds SEC      near-miss pause length (default: $PAUSE_SECONDS)
  --complete-after SEC     near-miss early completion (default: $COMPLETE_AFTER)
  --deadline SECONDS       the fixture's own absolute deadline (default: $ABSOLUTE_DEADLINE_SECONDS)
  --evidence PATH          evidence JSONL (default: \$FIXDIR/evidence-<leg>.jsonl)
  --baseline PATH          the pre-change scripts/check.sh transcript to pin
  --account NAME           an account to resolve BY NAME (never a uid number)
  --python PATH            interpreter for the surrogate (must exec under the profile)
  --keep-scratch           do not remove the scratch rundir on exit
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --leg) LEG="${2:?}"; shift 2 ;;
    --wakes) WAKES="${2:?}"; shift 2 ;;
    --until-delta) UNTIL_DELTA="${2:?}"; shift 2 ;;
    --pause-exempt-max) PAUSE_EXEMPT_MAX="${2:?}"; shift 2 ;;
    --pause-seconds) PAUSE_SECONDS="${2:?}"; shift 2 ;;
    --complete-after) COMPLETE_AFTER="${2:?}"; shift 2 ;;
    --deadline) ABSOLUTE_DEADLINE_SECONDS="${2:?}"; shift 2 ;;
    --evidence) EVIDENCE="${2:?}"; shift 2 ;;
    --baseline) BASELINE="${2:?}"; shift 2 ;;
    --account) ACCOUNT="${2:?}"; shift 2 ;;
    --python) PYBIN="${2:?}"; shift 2 ;;
    --keep-scratch) KEEP_SCRATCH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

# ---------------------------------------------------------------------------
# Construction-time assertions. Everything here runs BEFORE anything is spawned.
# ---------------------------------------------------------------------------

# 1. Unprivileged. Probe 5's teardown removed the privileged helper tree and the
#    sudoers fragment, and they stay removed.
[ "$(id -u)" != "0" ] || die "running as root. This probe is unprivileged by construction: no sudo, no root helpers, no launchd bootstrap into a real uid domain."
[ -z "${SUDO_USER:-}" ] || die "invoked under sudo (SUDO_USER is set). This probe is unprivileged by construction."
if [ "$(id -u)" != "$(id -ur)" ]; then
  die "effective uid differs from real uid (setuid context). This probe is unprivileged by construction."
fi

# 2. Never resolve a uid numerically. The `agent` account's uid differs per host
#    (502 on the mini), and this fixture neither creates nor deletes it.
if [ -n "$ACCOUNT" ]; then
  case "$ACCOUNT" in
    ''|*[!0-9]*) ;;
    *) die "--account was given numerically ($ACCOUNT). Accounts are resolved BY NAME here; a uid number means a different account on a different host." ;;
  esac
  id -u -- "$ACCOUNT" >/dev/null 2>&1 || die "--account '$ACCOUNT' does not resolve by name on this host"
fi

# 3. The driver must be in a process group it can name, so that every later
#    "is this the driver's own group?" check has a real answer to compare to.
DRIVER_PGID="$(ps -o pgid= -p $$ | tr -d ' ')"
case "$DRIVER_PGID" in
  ''|*[!0-9]*) die "could not determine the driver's own pgid (got '$DRIVER_PGID'). Refusing to arm: every containment assertion below compares against it, and an unknown value would make them vacuously true." ;;
esac
[ "$DRIVER_PGID" -gt 1 ] || die "driver pgid is not a positive non-init group: $DRIVER_PGID"

[ -x "$ORCH" ] || die "spawn-orchestrator.sh not found or not executable: $ORCH"

# 4. The leg must be ARMED in the registry. A leg the driver knows about but the
#    registry does not is exactly the "armed injection point with no recorded
#    row" hazard, one step earlier.
"$FIXDIR/scenarios.py" variants --leg "$LEG" >/dev/null \
  || die "leg '$LEG' is not armed in scenarios.py (tasks 3-5 arm the rest)"

# 5. Pin an interpreter that can actually exec under the rendered profile.
#    /usr/bin/python3 re-execs into CommandLineTools/Library/Frameworks/..., and
#    Seatbelt matches the RESOLVED path (the renderer documents this defect class
#    at spawn-orchestrator.sh:828-:840), which falls outside the granted
#    CommandLineTools/usr subpath — so it is refused at exec. A Homebrew build
#    resolves into the granted /opt/homebrew/Cellar. Smoke-tested below, before
#    any leg, because this is a construction-time wall that would otherwise eat
#    the time box and yield `inconclusive - boundary not in force` for a reason
#    that is neither the boundary nor the fixture logic.
if [ -z "$PYBIN" ]; then
  for c in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
           /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3; do
    [ -x "$c" ] && { PYBIN="$c"; break; }
  done
fi
[ -n "$PYBIN" ] && [ -x "$PYBIN" ] || die "no Homebrew python found. Pass --python <path>; /usr/bin/python3 cannot exec under the rendered profile."
case "$(cd "$(dirname "$PYBIN")" && pwd -P)/" in
  /opt/homebrew/*) ;;
  *) echo "driver: WARNING: --python is not under /opt/homebrew; the profile exec smoke test below is the real check" >&2 ;;
esac

command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not found: the rendered profile could not be applied, so the supervisor-state write-deny would never be exercised. Per the kill sheet that is `inconclusive - boundary not in force`, not a pass — refusing to run and report one."

[ -n "$EVIDENCE" ] || EVIDENCE="$FIXDIR/evidence-$LEG.jsonl"
: >"$EVIDENCE"

# 6. Evidence hygiene, before anything is emitted: the metadata block is an
#    allowlist of FIELD NAMES and the signal gate refuses every shape of the
#    outage's symptom.
"$FIXDIR/common.py" selftest || die "common.py selftest failed"

# ---------------------------------------------------------------------------
# Scratch rundir. The ONLY thing this fixture writes outside its own tree.
# ---------------------------------------------------------------------------

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/probe5b-XXXXXX")"
RUNDIR="$SCRATCH/run"
mkdir -p "$RUNDIR/.auto-pilot" "$SCRATCH/tmp" "$SCRATCH/stub-bin"

# A unique random label, asserted ABSENT from launchctl list before use. This
# fixture never bootstraps a job — but spawn-orchestrator's halt path calls
# `teardown --label`, which runs `launchctl bootout gui/<uid>/<label>`
# unconditionally whenever launchctl exists (:1694). A colliding label would
# boot out a live job this fixture never created.
LABEL="com.probe5b.fixture.$$.$(od -An -N4 -tx4 /dev/urandom | tr -d ' ')"
if command -v launchctl >/dev/null 2>&1; then
  if launchctl list 2>/dev/null | awk '{print $3}' | grep -qxF "$LABEL"; then
    die "generated launchd label '$LABEL' already exists in launchctl list. Refusing to run: teardown would boot out a job this fixture never created."
  fi
fi

# No real `claude`, no `gh`, no network — as a MECHANISM, not an assertion. Any
# of these being reached is a construction-time hard error, so the stubs record
# the attempt and fail rather than emulating success.
# The stub body is written from a QUOTED heredoc and reads its configuration
# from the environment. An unquoted heredoc collapses the `\"` in the JSON row
# below into bare quotes, so the stub would emit malformed JSON at exactly the
# moment it fires — i.e. the evidence would be corrupt only on the runs where it
# matters, which is the worst possible time to find out.
export PROBE5B_FIXDIR="$FIXDIR" PROBE5B_EVIDENCE="$EVIDENCE" PROBE5B_LEG="$LEG"
for stub in claude gh git-remote-http git-remote-https curl wget nc ssh; do
  cat >"$SCRATCH/stub-bin/$stub" <<'STUBEOF'
#!/bin/sh
tool="$(basename "$0")"
echo "probe5b: HARD ERROR: '$tool' was invoked during a leg run ($*)" >&2
"$PROBE5B_FIXDIR/common.py" emit --evidence "$PROBE5B_EVIDENCE" \
  --row "$(printf '{"row":"forbidden_exec","leg":"%s","tool":"%s"}' "$PROBE5B_LEG" "$tool")" || true
exit 97
STUBEOF
  chmod +x "$SCRATCH/stub-bin/$stub"
done
PATH="$SCRATCH/stub-bin:$PATH"
export PATH

# The parent-death channel. The driver holds a write end; every surrogate and
# worker holds a READ end and exits on EOF. `<>` (not `>`) so opening it does
# not block waiting for a reader — a `>` here deadlocks the driver before the
# first wake. Children get `9>&-` so they never inherit the WRITE end: a child
# holding one would keep the pipe open after the driver dies, and the channel
# would silently never fire.
FIFO="$SCRATCH/parent-death.fifo"
mkfifo "$FIFO"
exec 9<>"$FIFO"

# The fd above is NOT sufficient on its own, and this was measured rather than
# reasoned about: any child that inherits fd 9 keeps a write end open, so a
# `kill -9` of the driver produces no EOF and the surrogate is stranded. A plain
# `sleep` spawned without `9>&-` reproduces it. So the driver ALSO records its
# own identity, and every downstream process polls it through the same
# proc_pidinfo authority the signal path uses — a detector that depends on no fd
# hygiene whatsoever. `$$` (not the recorder's own pid): this runs as a CHILD of
# the driver, and recording the child would tell every watcher the driver had
# died a millisecond later.
DRIVER_INC="$SCRATCH/driver.incarnation.json"
"$FIXDIR/common.py" record-incarnation --incarnation-file "$DRIVER_INC" --pid "$$" >/dev/null \
  || die "could not record the driver's own incarnation; refusing to spawn anything that would then be unguarded"

ABSOLUTE_DEADLINE_EPOCH=$(( $(date +%s) + ABSOLUTE_DEADLINE_SECONDS ))

cleanup() {
  local rc=$?
  set +e
  if [ -n "${SURR_INC:-}" ] && [ -f "${SURR_INC:-}" ]; then
    "$FIXDIR/common.py" signal --incarnation-file "$SURR_INC" \
      --driver-pgid "$DRIVER_PGID" --evidence "$EVIDENCE" --leg "$LEG" \
      --signal TERM >/dev/null 2>&1
    sleep 1
    "$FIXDIR/common.py" signal --incarnation-file "$SURR_INC" \
      --driver-pgid "$DRIVER_PGID" --evidence "$EVIDENCE" --leg "$LEG" \
      --signal KILL >/dev/null 2>&1
  fi
  if [ -n "${WATCHDOG_INC:-}" ] && [ -f "${WATCHDOG_INC:-}" ]; then
    "$FIXDIR/common.py" signal --incarnation-file "$WATCHDOG_INC" \
      --driver-pgid "$DRIVER_PGID" --evidence "$EVIDENCE" --leg "$LEG" \
      --signal TERM >/dev/null 2>&1
  fi
  exec 9>&-
  if [ "$KEEP_SCRATCH" = 0 ] && [ -n "$SCRATCH" ]; then
    rm -rf "$SCRATCH"
  else
    echo "driver: scratch retained: $SCRATCH" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Pre-leg smoke: the rendered profile, and the interpreter under it.
# ---------------------------------------------------------------------------

PROFILE="$SCRATCH/profile.sb"
"$ORCH" render-profile --out "$PROFILE" --workdir "$RUNDIR" \
  --rw "$RUNDIR" --rw "$SCRATCH/tmp" --tmpdir "$SCRATCH/tmp" --toolchain >/dev/null \
  || die "render-profile failed. The supervisor-state write-deny would never be exercised: that is \`inconclusive - boundary not in force\`, not a pass."

sandbox-exec -f "$PROFILE" "$PYBIN" -c 'print("exec-ok")' >/dev/null 2>&1 \
  || die "the pinned interpreter ($PYBIN) cannot exec under the rendered profile. Pass a Homebrew build with --python; /usr/bin/python3 re-execs into CommandLineTools and Seatbelt matches the RESOLVED path."

# ---------------------------------------------------------------------------
# The watchdog — spawned FIRST, in its OWN group, before anything it guards.
# ---------------------------------------------------------------------------
#
# "The driver enforces its own absolute deadline and self-terminates" is too
# weak: a crashed or wedged driver enforces nothing, and the surrogate is
# deliberately long-lived and backgrounded so it does not cooperate. This is the
# second, independent mechanism. It reads the surrogate's incarnation LAZILY at
# fire time — being spawned first, it cannot have it yet, and arming it after
# the thing it guards would leave a window where nothing guards it.

WATCHDOG_INC="$SCRATCH/watchdog.incarnation.json"
SURR_INC="$SCRATCH/surrogate.incarnation.json"

"$FIXDIR/common.py" spawn --incarnation-file "$WATCHDOG_INC" -- \
  "$PYBIN" "$FIXDIR/common.py" watchdog \
  --incarnation-file "$SURR_INC" --driver-pgid "$DRIVER_PGID" \
  --evidence "$EVIDENCE" --deadline "$ABSOLUTE_DEADLINE_EPOCH" --leg "$LEG" \
  --parent-death-fifo "$FIFO" --driver-incarnation-file "$DRIVER_INC" 9>&- &
WATCHDOG_PID=$!

for _ in $(seq 1 50); do [ -f "$WATCHDOG_INC" ] && break; sleep 0.1; done
[ -f "$WATCHDOG_INC" ] || die "watchdog did not record its incarnation; refusing to run without the independent deadline mechanism"
"$FIXDIR/common.py" assert-containment --incarnation-file "$WATCHDOG_INC" \
  --driver-pgid "$DRIVER_PGID" --evidence "$EVIDENCE" --leg "$LEG" >/dev/null \
  || die "watchdog failed its containment assertion"

# ---------------------------------------------------------------------------
# Header row
# ---------------------------------------------------------------------------

BASELINE_JSON="null"
if [ -n "$BASELINE" ] && [ -f "$BASELINE" ]; then
  BASELINE_JSON="{\"path\":\"$(basename "$BASELINE")\",\"failing_tests\":$(grep -c '^FAIL - ' "$BASELINE" || true),\"sha256\":\"$(shasum -a 256 "$BASELINE" | awk '{print $1}')\"}"
fi

"$FIXDIR/common.py" header --evidence "$EVIDENCE" --leg "$LEG" \
  --wakes "$WAKES" --driver-pgid "$DRIVER_PGID" \
  --extra "{\"label\":\"$LABEL\",\"pause_exempt_max\":$PAUSE_EXEMPT_MAX,\"park_limit\":$PARK_LIMIT,\"until_delta\":$UNTIL_DELTA,\"absolute_deadline_seconds\":$ABSOLUTE_DEADLINE_SECONDS,\"check_baseline\":$BASELINE_JSON,\"orchestrator_sha256\":\"$(shasum -a 256 "$ORCH" | awk '{print $1}')\",\"pinned_surrogate_python\":\"$PYBIN\",\"pinned_surrogate_python_resolved\":\"$("$PYBIN" -c 'import os,sys;print(os.path.realpath(sys.executable))')\"}" >/dev/null

# ---------------------------------------------------------------------------
# One variant
# ---------------------------------------------------------------------------

# The REAL deadline is derived from an epoch the driver computes and keeps, and
# the ISO string handed to the surrogate is rendered FROM that same epoch. Two
# independent renderings could disagree by a second, and the fixture-clock
# overshoot — the one number leg 1 exists to produce — would inherit the drift.
iso_at() { date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }

emit_row() { "$FIXDIR/common.py" emit --evidence "$EVIDENCE" --row "$1"; }

run_variant() {
  # Tuning arrives per VARIANT from the registry, not from this file's defaults:
  # leg 1 needs a deadline short enough to blow inside the wake loop, leg 2 needs
  # one long enough that the deadline halt cannot win the race and be misread as
  # the ledger's. Pre-registered there, applied here.
  local variant="$1" behaviour="$2" sub_case="$3" wakes="$4" until_delta="$5" \
    forge_after="$6" pause_exempt_max="$7" workers="$8" enforce_exempt="$9"
  local rowname="$variant"
  [ -n "$sub_case" ] && rowname="$variant/$sub_case"
  echo "driver: === leg=$LEG variant=$rowname behaviour=$behaviour wakes=$wakes until_delta=$until_delta pause_exempt_max=$pause_exempt_max workers=$workers ==="

  # A fresh rundir per variant: the supervisor-state ledger and the ALARM
  # sentinel are both one-per-run, so a shared dir would let one variant's
  # bookkeeping decide the next one's outcome.
  rm -rf "$RUNDIR"
  mkdir -p "$RUNDIR/.auto-pilot"
  printf '# report\n' >"$RUNDIR/.auto-pilot/REPORT.md"

  # A real git checkout: `_run_head` reads it for the no-progress guard, and the
  # halt path commits the run-state change.
  #
  # The scratch repo is made HERMETIC against this host's git hooks, and that is
  # load-bearing rather than tidiness. Measured on this host: a global
  # `core.hooksPath` (~/src/dotfiles/git/hooks) points every repo at a delegating
  # hook set, whose `reference-transaction` hook runs `git rev-parse` against a
  # repo that does not exist yet (the "fatal: not a git repository" line on
  # stderr), and whose `pre-commit` refuses direct commits to 'main'. That
  # pre-commit is the confirmed environmental cause of the pre-existing
  # scripts/check.sh failures this run's baseline pins — it fires inside other
  # fixtures' scratch repos, their seed commit never lands, and every later
  # assertion cascades from "not a git repository".
  #
  # This fixture must not inherit that. `_supervisor_halt` COMMITS the run-state
  # change, so a host pre-commit hook rejecting it would read as the supervisor
  # failing to record its own halt — a fixture defect wearing the costume of a
  # finding, in exactly the legs (1-3) where the halt is the measurement.
  # `core.hooksPath` overrides `--template=`, so both are set: the config for the
  # hooks, the empty template so nothing else is copied in either.
  mkdir -p "$SCRATCH/nohooks"
  git -c "core.hooksPath=$SCRATCH/nohooks" init -q --template= "$RUNDIR"
  git -C "$RUNDIR" config core.hooksPath "$SCRATCH/nohooks"
  git -C "$RUNDIR" config user.email probe5b@example.invalid
  git -C "$RUNDIR" config user.name "probe5b fixture"
  git -C "$RUNDIR" checkout -q -b "auto-pilot/probe5b-$variant" 2>/dev/null || true

  local until_epoch until_iso
  until_epoch=$(( $(date +%s) + until_delta ))
  until_iso="$(iso_at "$until_epoch")"
  local acc="$SCRATCH/acc-$variant${sub_case:+-$sub_case}.json"
  rm -f "$acc"
  local fake_log="$SCRATCH/claude-$variant.log"
  # The canned stream-json shape scripts/test-spawn-orchestrator.sh already
  # fakes (:4893) rather than a new one. classify-exit reads this; no `claude`
  # ever produces it.
  printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}' >"$fake_log"

  # The surrogate's evidence goes inside its own RW scope, NOT into the
  # checked-in fixture tree: it runs under the rendered profile, so the tree is
  # read-only to it (a write there fails with EPERM — the boundary working). The
  # unjailed driver merges these rows in, tagged `in_jail`, after the wakes.
  local jail_evidence="$RUNDIR/.auto-pilot/surrogate-evidence.jsonl"

  rm -f "$SURR_INC"
  "$FIXDIR/common.py" spawn --incarnation-file "$SURR_INC" -- \
    sandbox-exec -f "$PROFILE" "$PYBIN" "$FIXDIR/runaway.py" \
    --rundir "$RUNDIR" --evidence "$jail_evidence" --leg "$LEG" \
    --variant "$variant" --sub-case "$sub_case" \
    --behaviour "$behaviour" --until "$until_iso" \
    --pause-seconds "$PAUSE_SECONDS" --complete-after "$COMPLETE_AFTER" \
    --forge-after "$forge_after" --workers "$workers" \
    --heartbeat-every "$HEARTBEAT_EVERY" --parent-death-fifo "$FIFO" \
    --driver-incarnation-file "$DRIVER_INC" 9>&- &
  local surr_pid=$!

  local i
  for i in $(seq 1 50); do [ -f "$SURR_INC" ] && break; sleep 0.1; done
  [ -f "$SURR_INC" ] || die "surrogate did not record its incarnation; refusing to fire a wake at an unidentified process"

  # THE containment gate. Both halves, measured from the kernel, before the
  # first wake and therefore before anything could ever be signalled.
  "$FIXDIR/common.py" assert-containment --incarnation-file "$SURR_INC" \
    --driver-pgid "$DRIVER_PGID" --evidence "$EVIDENCE" --leg "$LEG" \
    || die "surrogate failed its containment assertion"

  # Let it write a first RUN.md before the first wake: a wake against a missing
  # RUN.md measures the fixture's startup race, not the supervisor.
  for i in $(seq 1 50); do [ -f "$RUNDIR/.auto-pilot/RUN.md" ] && break; sleep 0.1; done

  local gate_rc=0 wake
  local state="$RUNDIR/.auto-pilot/supervisor-state"
  for wake in $(seq 1 "$wakes"); do
    local scan_out gate_out check_out
    scan_out="$("$ORCH" supervisor-scan --dir "$RUNDIR" --label "$LABEL" \
      --park-limit "$PARK_LIMIT" --pause-exempt-max "$pause_exempt_max" 2>&1 || true)"

    gate_rc=0
    gate_out="$("$ORCH" supervisor-gate --dir "$RUNDIR" --label "$LABEL" 2>&1)" || gate_rc=$?

    check_out=""
    if [ "$gate_rc" = 0 ]; then
      check_out="$("$ORCH" supervisor-check --exit-code 0 --wake-start "$(date +%s)" \
        --log "$fake_log" --dir "$RUNDIR" --label "$LABEL" --state "$state" \
        --park-limit "$PARK_LIMIT" 2>&1 || true)"
    fi

    # legs.py owns the measurement, and the accumulator is how it stays
    # consistent across wakes: first-halt index, the ledger's exempt_since
    # stamping and the live worker count are all stateful, and re-deriving them
    # from the evidence file on every wake is how a fixture starts disagreeing
    # with itself. Halts are detected from REPORT.md's per-halt section rather
    # than by grepping stdout for "supervisor halt" — that string also appears in
    # the halt's own best-effort FAILURE messages, so a broken write would have
    # counted as a phantom halt.
    SCAN="$scan_out" GATE="$gate_out" CHECK="$check_out" \
      "$PYBIN" "$FIXDIR/legs.py" wake --evidence "$EVIDENCE" --acc "$acc" \
      --rundir "$RUNDIR" --leg "$LEG" --variant "$variant" --sub-case "$sub_case" \
      --wake "$wake" --gate-rc "$gate_rc" --now-epoch "$(date +%s)" \
      --real-until-epoch "$until_epoch" \
      --enforce-exempt-window "$enforce_exempt" \
      || die "legs.py wake failed on wake $wake of $rowname"

    sleep 1
  done

  # Measured BEFORE the fixture signals anything: leg 1's `done-forgery`
  # prediction is that the ORCHESTRATOR's own `teardown --label` ran and the
  # surrogate is still alive afterwards — it is not a launchd job, so nothing in
  # that teardown reaches it. Taking this after the fixture's own reap would
  # measure the fixture instead of the finding.
  local alive_after_orch
  alive_after_orch="$("$PYBIN" "$FIXDIR/legs.py" alive --incarnation-file "$SURR_INC")"

  # Teardown, then measure survivors AFTER it. Every signal re-validates
  # identity first; a mismatch is recorded escaped/already-dead and NOT chased.
  # One killpg reaps the workers too: they are deliberately left in the
  # surrogate's group rather than given their own.
  "$FIXDIR/common.py" signal --incarnation-file "$SURR_INC" \
    --driver-pgid "$DRIVER_PGID" --evidence "$EVIDENCE" --leg "$LEG" \
    --signal TERM >/dev/null
  sleep 1
  "$FIXDIR/common.py" signal --incarnation-file "$SURR_INC" \
    --driver-pgid "$DRIVER_PGID" --evidence "$EVIDENCE" --leg "$LEG" \
    --signal KILL >/dev/null 2>&1 || true
  wait "$surr_pid" 2>/dev/null || true
  sleep 1

  # Merge before the scratch dir is measured or removed — the in-jail rows carry
  # the boundary probe, which is what says whether the profile was in force at
  # all, and a leg reported without it is `inconclusive`, not a pass.
  "$FIXDIR/common.py" merge --evidence "$EVIDENCE" --source "$jail_evidence" \
    --leg "$LEG" >/dev/null || die "could not merge the surrogate's in-jail evidence"

  "$PYBIN" "$FIXDIR/legs.py" finish --evidence "$EVIDENCE" --acc "$acc" \
    --rundir "$RUNDIR" --leg "$LEG" --variant "$variant" --sub-case "$sub_case" \
    --surrogate-inc "$SURR_INC" --wakes "$wakes" --final-gate-rc "$gate_rc" \
    --real-until-epoch "$until_epoch" --pause-exempt-max "$pause_exempt_max" \
    --surrogate-alive-after-orchestrator-teardown "$alive_after_orch" \
    || die "legs.py finish failed for $rowname"
}

# ---------------------------------------------------------------------------
# Run every variant the registry declares for this leg
# ---------------------------------------------------------------------------

> "$SCRATCH/rows.txt"
while IFS= read -r spec; do
  # One line of fields per ROW, rendered in Python rather than picked apart in
  # shell: every value here is a tuning parameter the registry pre-registered,
  # and a field silently defaulting to this file's smoke-leg value would run a
  # leg at the wrong deadline and record the number as if it were pinned.
  SPEC="$spec" "$PYBIN" -c '
import json, os
s = json.loads(os.environ["SPEC"])
t = s.get("tuning") or {}
print(" ".join(str(x) for x in (
    s["variant"],
    s["behaviour"],
    s.get("sub_case") or "-",
    t.get("wakes", '"$WAKES"'),
    t.get("until_delta", '"$UNTIL_DELTA"'),
    t.get("forge_after", 0),
    t.get("pause_exempt_max", '"$PAUSE_EXEMPT_MAX"'),
    t.get("workers", 0),
    1 if s.get("require_exempt_since_before_cap") else 0,
)))' >>"$SCRATCH/rows.txt"
done < <("$FIXDIR/scenarios.py" variants --leg "$LEG")

while read -r v b sc wk ud fa pem wrk eew; do
  [ "$sc" = "-" ] && sc=""
  run_variant "$v" "$b" "$sc" "$wk" "$ud" "$fa" "$pem" "$wrk" "$eew"
done <"$SCRATCH/rows.txt"

# The family verdict — a SEPARATE record from every measurement above, per the
# kill sheet: a censored measurement never censors a verdict, and collapsing the
# two is what let the sheet's first draft assign one outcome to both `falsified`
# and `inconclusive` at once.
if [ "$LEG" != "smoke" ]; then
  "$PYBIN" "$FIXDIR/legs.py" family --evidence "$EVIDENCE" --leg "$LEG" \
    || die "could not record the family verdict for leg $LEG"
fi

# The hard-error check: an armed injection point with no recorded row fails
# here, read back from the evidence rather than from the driver's own memory of
# what it ran.
"$FIXDIR/scenarios.py" verify --evidence "$EVIDENCE" --leg "$LEG"

echo "driver: OK — evidence at $EVIDENCE"
