#!/usr/bin/env bash
# shellcheck disable=SC1010,SC2034,SC2154 # Fixtures intentionally contain shell source, retain outputs for diagnostics, and read names the prelude defines.
# doctor: run invariant audit (task 14, generalizing findings #22/#23).
#
# One part of the orchestrator harness for scripts/spawn-orchestrator.sh. Every
# part is self-contained and offline: the prelude builds it a private fixture
# tree and the isolation guards, the epilogue asserts they held. See
# scripts/lib/spawn-orchestrator-test-prelude.sh for what the parts share, and
# dev_docs/gate-performance.md for why this suite is several files.
#
# Run directly: bash scripts/test-spawn-orchestrator-doctor.sh
set -uo pipefail
SO_PART=doctor
# The lib dir goes in a dedicated name, never $ROOT: fixtures below reassign
# ROOT for their own trees (the verify-branch block does ROOT="$VB/root"), so
# sourcing the epilogue off $ROOT would resolve inside a fixture instead.
SO_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/spawn-orchestrator-test-prelude.sh
. "$SO_LIB/spawn-orchestrator-test-prelude.sh"

# --- doctor: run invariant audit (task 14, generalizing findings #22/#23) -----
# A test per invariant, asserting on OBSERVED state (the phase in RUN.md, the
# worktree gone, `status: systemic` written, the REPORT.md bullet) — not just a
# log string. Invariants 1 and 2 are the two that shipped as production
# failures and are covered explicitly.
if command -v git >/dev/null 2>&1; then
  DOC="$BASE/doctor"
  mkdir -p "$DOC"

  # A fake `gh` for I3/I4/I6: PR state/draft/labels live in flat files under
  # $DOCTOR_GH_DB, same shape as restack's fake gh above but extended with
  # isDraft/labels reads and the edit/ready writes I4's repair needs.
  #
  # `.state`/`.labels` reads always exit 0 (`; true` after the `cat`), even
  # when the backing file is missing — that models a POSITIVE gh read that
  # simply found nothing (D2's "PR number gh positively reports as
  # nonexistent" case: rc 0, empty field), which is what earns the I3/I6
  # "park" verdict. A TRANSIENT gh failure (401, rate limit, network) is a
  # different, non-zero-rc shape — see $DOCTOR_GH_FAIL below — and must never
  # be confused with this one.
  DOCTOR_GH="$DOC/gh"
  cat >"$DOCTOR_GH" <<'GHEOF'
#!/usr/bin/env bash
set -uo pipefail
db="${DOCTOR_GH_DB:?DOCTOR_GH_DB not set}"
[ "$1" = pr ] || exit 1
sub="$2"; num="$3"; shift 3
case "$sub" in
  view)
    jqexpr=""
    while [ $# -gt 0 ]; do case "$1" in --jq) jqexpr="$2"; shift 2 ;; *) shift ;; esac; done
    case "$jqexpr" in
      .state) cat "$db/$num.state" 2>/dev/null; true ;;
      .isDraft) cat "$db/$num.draft" 2>/dev/null || echo false ;;
      '[.labels[].name] | join(",")') cat "$db/$num.labels" 2>/dev/null; true ;;
      *) exit 1 ;;
    esac
    ;;
  edit)
    while [ $# -gt 0 ]; do
      case "$1" in
        --remove-label) printf '' >"$db/$num.labels"; shift 2 ;;
        --add-label) printf '%s\n' "$2" >>"$db/$num.labels"; shift 2 ;;
        *) shift ;;
      esac
    done
    ;;
  ready) printf 'false\n' >"$db/$num.draft" ;;
  *) exit 1 ;;
esac
GHEOF
  chmod +x "$DOCTOR_GH"

  # A `gh` that ALWAYS fails (simulating a transient 401/rate-limit/network
  # blip, D2): every `pr view` exits non-zero with no output, regardless of
  # which PR is asked about. Used to prove a bad gh moment never parks a task.
  DOCTOR_GH_FAIL="$DOC/gh-fail"
  cat >"$DOCTOR_GH_FAIL" <<'GHFAILEOF'
#!/usr/bin/env bash
exit 1
GHFAILEOF
  chmod +x "$DOCTOR_GH_FAIL"

  # Build a real "run root" layout: <root>/run (the run worktree) + a bare
  # origin, matching I5's "lives under the run root's workers/ directory"
  # convention. Every doctor scenario below gets its own root.
  _doctor_new_run() {
    local root="$1" run_id="$2"
    mkdir -p "$root"
    git init --bare -q "$root/origin.git"
    git init -q "$root/run"
    git -C "$root/run" remote add origin "$root/origin.git"
    git -C "$root/run" config user.email t@example.com
    git -C "$root/run" config user.name T
    git -C "$root/run" checkout -q -b main
    echo r >"$root/run/r.txt"
    git -C "$root/run" add r.txt
    git -C "$root/run" commit -q -m r
    git -C "$root/run" push -q origin main
    git -C "$root/run" checkout -q -b "auto-pilot/$run_id"
    mkdir -p "$root/run/.auto-pilot"
  }

  # A provably DEAD pid (spawned, killed, reaped) and the live pid of this very
  # test shell, with its real `ps` start-time. RUN.md's `orchestrator_pid` /
  # `orchestrator_started_at` are what the stale-orchestrator check reads, and
  # I5 gates the prune of an UNMATCHED worker worktree on that check saying the
  # orchestrator is provably dead — a LIVE one means the unmatched row could be
  # a dispatch in flight whose row hasn't been written back yet.
  _dead_pid() {
    local p
    sleep 30 &
    p=$!
    kill "$p" 2>/dev/null
    wait "$p" 2>/dev/null
    printf '%s' "$p"
  }
  LIVE_PID=$$
  LIVE_STARTED="$(ps -o lstart= -p $$)"

  # --- I1: run worktree HEAD parked off the run-state branch -> repaired ----
  D1="$DOC/i1"
  RUN_ID1="doctor-i1"
  _doctor_new_run "$D1" "$RUN_ID1"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID1"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D1/run/.auto-pilot/RUN.md"
  : >"$D1/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D1/run/.auto-pilot/REPORT.md"
  git -C "$D1/run" add .auto-pilot
  git -C "$D1/run" commit -q -m "seed run state"
  git -C "$D1/run" checkout -q main
  git -C "$D1/run" checkout -q -b bestdan/task-x
  echo work >"$D1/run/task-file"
  git -C "$D1/run" add task-file
  git -C "$D1/run" commit -q -m "task work on the wrong branch"

  d1out="$("$SCRIPT" doctor --dir "$D1/run" --run-id "$RUN_ID1" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d1rc=$?
  [ "$d1rc" = 0 ] && ok "doctor I1: exits 0 after repairing HEAD" || bad "doctor I1: exits 0 after repairing HEAD" "$d1out"
  have "doctor I1: summary reports the HEAD repair" 'I1: HEAD restored' "$d1out"
  d1head="$(git -C "$D1/run" rev-parse --abbrev-ref HEAD)"
  [ "$d1head" = "auto-pilot/$RUN_ID1" ] && ok "doctor I1: HEAD is observably back on the run-state branch" \
    || bad "doctor I1: HEAD is observably back on the run-state branch" "got $d1head"
  have "doctor I1: records the deviation in QUESTIONS.md" 'HEAD was parked on `bestdan/task-x`' "$(cat "$D1/run/.auto-pilot/QUESTIONS.md")"
  have "doctor I1: appends a REPORT.md bullet" 'I1 repaired' "$(cat "$D1/run/.auto-pilot/REPORT.md")"

  # --- I2 (halt): RUN.md unreadable/unparseable FROM THE BRANCH -------------
  D2="$DOC/i2-halt"
  RUN_ID2="doctor-i2-halt"
  _doctor_new_run "$D2" "$RUN_ID2"
  printf 'not RUN.md at all -- no front matter\n' >"$D2/run/.auto-pilot/RUN.md"
  : >"$D2/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D2/run/.auto-pilot/REPORT.md"
  git -C "$D2/run" add .auto-pilot
  git -C "$D2/run" commit -q -m "seed broken run state (no front matter)"

  # Point the notifier guard's recorder at a PER-TEST log so the doctor's own
  # notifier calls can be COUNTED. Doctor runs inside the jail, where osascript is
  # exec-denied, so the right count is ZERO — asserted positively below rather than
  # merely implied by the absence of an ALARM sentinel.
  D2_NOTIFY="$D2/notify.calls"
  : >"$D2_NOTIFY"
  d2out="$(NOTIFY_GUARD_LOG="$D2_NOTIFY" "$SCRIPT" doctor --dir "$D2/run" --run-id "$RUN_ID2" 2>&1)"
  d2rc=$?
  [ "$d2rc" = 30 ] && ok "doctor I2 halt: exits 30 (a caller gating on this cannot dispatch)" \
    || bad "doctor I2 halt: exits 30" "exit=$d2rc out=$d2out"
  have "doctor I2 halt: RUN.md status is observably systemic-attempted or REPORT.md carries the alarm" 'ALARM' "$(cat "$D2/run/.auto-pilot/REPORT.md")"
  have "doctor I2 halt: reason names invariant 2" 'invariant 2' "$(cat "$D2/run/.auto-pilot/REPORT.md")"
  # --label was NOT passed: the halt still fires (no teardown to attempt).
  have "doctor I2 halt: --label-less halt still reports itself" 'supervisor halt' "$d2out"

  # --- doctor halt -> a HUMAN is actually told (task 16's jailed seam) -------
  # Doctor runs INSIDE the jail, where osascript is exec-denied, so it must NOT
  # call `alarm` itself: `alarm` writes the ALARM SENTINEL, and the supervisor's
  # `status: systemic` scan goes SILENT whenever that sentinel exists ("already
  # screamed about this run") — an in-jail alarm would gag the one channel that
  # can actually reach a human. So doctor drops an `alarm-request` (the seam
  # task 16 built for jailed detectors) and the UN-JAILED supervisor delivers it,
  # carrying WHICH invariant failed — a diagnosis the generic systemic scan
  # cannot state. Observed end to end, not by source shape.
  [ -f "$D2/run/.auto-pilot/alarm-requests/invariant.alarm" ] \
    && ok "doctor halt: files an alarm-request the un-jailed supervisor can deliver" \
    || bad "doctor halt: files an alarm-request the un-jailed supervisor can deliver"
  have "doctor halt: the request names the failing invariant, not just 'systemic'" \
    'invariant 2' "$(cat "$D2/run/.auto-pilot/alarm-requests/invariant.alarm" 2>/dev/null)"
  # The sentinel must NOT exist yet: doctor writing it in-jail is exactly what
  # would suppress the supervisor's delivery below.
  [ ! -f "$D2/run/.auto-pilot/ALARM" ] \
    && ok "doctor halt: does NOT write the ALARM sentinel in-jail (which would gag the supervisor)" \
    || bad "doctor halt: does NOT write the ALARM sentinel in-jail"
  # The POSITIVE form of the same property, with a recording notifier on PATH: a
  # doctor halt must invoke the notifier ZERO times. In production the jail denies
  # it anyway (osascript is exec-denied), so a doctor that TRIED to notify would be
  # silently denied AND would leave the gagging sentinel behind — the count is the
  # only thing that catches that regression.
  d2_notify_n="$(wc -l <"$D2_NOTIFY" | tr -d " ")"
  [ "$d2_notify_n" = 0 ] \
    && ok "doctor halt: invokes the notifier ZERO times (it files an alarm-request instead)" \
    || bad "doctor halt: invokes the notifier ZERO times" "got $d2_notify_n call(s): $(cat "$D2_NOTIFY")"
  # Now the un-jailed side runs (as it does above the gate on every wake, and
  # from supervisor-check right after the agent exits — the SAME wake).
  D2_SCAN_NOTIFY="$D2/scan-notify.calls"
  : >"$D2_SCAN_NOTIFY"
  # This capture is also the regression guard for `_run_bounded`'s watchdog: the
  # scan bounds its status-report at REPORT_TIMEOUT_SECONDS_DEFAULT (60s), and if
  # the watchdog's `sleep` survives the kill it keeps THIS `$( )` pipe open for the
  # whole bound — the scan's own work takes well under a second. So the elapsed
  # time of the substitution, not just its output, is the assertion. It cost the
  # gate 60s a call until the watchdog was group-killed with its fds off the pipe.
  scan_t0=$SECONDS
  scanout="$(NOTIFY_GUARD_LOG="$D2_SCAN_NOTIFY" "$SCRIPT" supervisor-scan --dir "$D2/run" --label doctor-alarm-test 2>&1)"
  scan_elapsed=$((SECONDS - scan_t0))
  [ "$scan_elapsed" -lt 15 ] \
    && ok "doctor halt: a captured supervisor-scan returns as soon as the scan does (the watchdog does not hold the \$( ) pipe)" \
    || bad "doctor halt: a captured supervisor-scan returns as soon as the scan does" \
      "took ${scan_elapsed}s — the watchdog's sleep is orphaned and holding the command substitution open"
  have "doctor halt: the supervisor DELIVERS the doctor's alarm on its next scan" 'ALARM invariant' "$scanout"
  # ...and the UN-jailed side is where the notification actually happens: exactly
  # one, so the seam moved the notification rather than losing it.
  d2_scan_n="$(grep -c "^osascript: " "$D2_SCAN_NOTIFY" | tr -d " ")"
  [ "$d2_scan_n" = 1 ] \
    && ok "doctor halt: the UN-JAILED supervisor notifies exactly once (the seam moves the alarm, never drops it)" \
    || bad "doctor halt: the un-jailed supervisor notifies exactly once" "got $d2_scan_n"
  have "doctor halt: the delivered alarm names the invariant" 'invariant 2' "$scanout"
  have "doctor halt: the alarm reaches REPORT.md's very first line" 'ALARM (' "$(head -1 "$D2/run/.auto-pilot/REPORT.md")"
  [ -f "$D2/run/.auto-pilot/ALARM" ] \
    && ok "doctor halt: the supervisor's delivery writes the ALARM sentinel (idempotency key)" \
    || bad "doctor halt: the supervisor's delivery writes the ALARM sentinel"
  # And it is delivered ONCE: a second scan (the run is still `systemic`) must
  # not re-notify under a second name — that per-wake noise is what makes the
  # next real alarm ignorable.
  scanout2="$("$SCRIPT" supervisor-scan --dir "$D2/run" --label doctor-alarm-test 2>&1)"
  lack "doctor halt: a second scan never re-alarms the run it already announced" 'ALARM systemic' "$scanout2"

  # --- I2 (repair): RUN.md fine on the branch, missing from the WORKING TREE
  D2R="$DOC/i2-repair"
  RUN_ID2R="doctor-i2-repair"
  _doctor_new_run "$D2R" "$RUN_ID2R"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID2R"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D2R/run/.auto-pilot/RUN.md"
  : >"$D2R/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D2R/run/.auto-pilot/REPORT.md"
  git -C "$D2R/run" add .auto-pilot
  git -C "$D2R/run" commit -q -m "seed run state"
  rm -f "$D2R/run/.auto-pilot/RUN.md" # gone from the WORKING TREE only

  d2rout="$("$SCRIPT" doctor --dir "$D2R/run" --run-id "$RUN_ID2R" 2>&1)"
  d2rrc=$?
  [ "$d2rrc" = 0 ] && ok "doctor I2 repair: exits 0 (working-tree-only loss is a repair, not a halt)" \
    || bad "doctor I2 repair: exits 0" "$d2rout"
  have "doctor I2 repair: summary reports the RUN.md restore" 'I2: RUN.md restored from branch' "$d2rout"
  [ -f "$D2R/run/.auto-pilot/RUN.md" ] && ok "doctor I2 repair: RUN.md is observably back in the working tree" \
    || bad "doctor I2 repair: RUN.md is observably back in the working tree"

  # --- I1+I2 deadlock: HEAD parked off-branch AND RUN.md deleted -------------
  # (the acceptance criterion's own scenario / finding #23's shape). The
  # deleted RUN.md IS the dirt that used to make assert_run_head fail closed
  # before I2 ever got a chance to restore it. Assert on OBSERVED state, not
  # log strings: exit 0, HEAD back on the run-state branch, RUN.md restored,
  # REPORT.md carries the repair bullet.
  D1D="$DOC/i1-i2-deadlock"
  RUN_ID1D="doctor-i1-i2-deadlock"
  _doctor_new_run "$D1D" "$RUN_ID1D"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID1D"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D1D/run/.auto-pilot/RUN.md"
  : >"$D1D/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D1D/run/.auto-pilot/REPORT.md"
  git -C "$D1D/run" add .auto-pilot
  git -C "$D1D/run" commit -q -m "seed run state"
  git -C "$D1D/run" checkout -q main
  git -C "$D1D/run" checkout -q -b bestdan/task-x
  echo work >"$D1D/run/task-file"
  git -C "$D1D/run" add task-file
  git -C "$D1D/run" commit -q -m "task work on the wrong branch"
  rm -f "$D1D/run/.auto-pilot/RUN.md" # the literal acceptance-criterion scenario
  # D1: a real run worktree ALWAYS carries UNTRACKED .auto-pilot/ content (the
  # live run's own orchestrator.log/verify-broker.log). `git reset`/`checkout`
  # cannot discard untracked files, so without --ignore-untracked-run-state
  # this alone would keep assert_run_head fail-closed and I1 could never fire
  # in a real run — reproduce that here. mkdir first: the task branch was cut
  # from main, which carries no .auto-pilot/ (the run files live only on the
  # run-state branch), so the directory does not exist here yet.
  mkdir -p "$D1D/run/.auto-pilot"
  printf 'orchestrator log line\n' >"$D1D/run/.auto-pilot/orchestrator.log"

  d1dout="$("$SCRIPT" doctor --dir "$D1D/run" --run-id "$RUN_ID1D" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d1drc=$?
  [ "$d1drc" = 0 ] && ok "doctor I1+I2 deadlock: exits 0 (recovers instead of bailing to a human)" \
    || bad "doctor I1+I2 deadlock: exits 0" "$d1dout"
  d1dhead="$(git -C "$D1D/run" rev-parse --abbrev-ref HEAD)"
  [ "$d1dhead" = "auto-pilot/$RUN_ID1D" ] && ok "doctor I1+I2 deadlock: HEAD is observably back on the run-state branch" \
    || bad "doctor I1+I2 deadlock: HEAD is observably back on the run-state branch" "got $d1dhead"
  [ -f "$D1D/run/.auto-pilot/RUN.md" ] && ok "doctor I1+I2 deadlock: RUN.md is observably back in the working tree" \
    || bad "doctor I1+I2 deadlock: RUN.md is observably back in the working tree"
  have "doctor I1+I2 deadlock: REPORT.md gained the repair bullet" 'I1 repaired' "$(cat "$D1D/run/.auto-pilot/REPORT.md")"
  # D1: the success line reports the DISCARD path (not a bare restore), and
  # the untracked run log survives — it must never be `git clean`ed away.
  have "doctor I1+I2 deadlock: reports the discard path, not a bare restore" \
    'I1: discarded stale .auto-pilot/ dirt' "$d1dout"
  [ -f "$D1D/run/.auto-pilot/orchestrator.log" ] && grep -q 'orchestrator log line' "$D1D/run/.auto-pilot/orchestrator.log" \
    && ok "doctor I1+I2 deadlock: the untracked run log survives the repair (D1 — never git-clean'd)" \
    || bad "doctor I1+I2 deadlock: the untracked run log survives the repair"

  # --- I1 untracked-only: HEAD parked off-branch, the ONLY .auto-pilot/ -----
  # dirt is UNTRACKED (a real run's orchestrator.log/verify-broker.log; no
  # tracked change at all) — this is the literal #23-repro scenario D1 fixes:
  # before, `git reset`/`checkout` no-op on untracked files, so the discard
  # never actually unblocked assert_run_head. Assert the repair still fires
  # and the log is left in place untouched.
  D1U="$DOC/i1-untracked-only"
  RUN_ID1U="doctor-i1-untracked-only"
  _doctor_new_run "$D1U" "$RUN_ID1U"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID1U"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D1U/run/.auto-pilot/RUN.md"
  : >"$D1U/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D1U/run/.auto-pilot/REPORT.md"
  git -C "$D1U/run" add .auto-pilot
  git -C "$D1U/run" commit -q -m "seed run state"
  git -C "$D1U/run" checkout -q main
  git -C "$D1U/run" checkout -q -b bestdan/task-z
  echo work >"$D1U/run/task-file"
  git -C "$D1U/run" add task-file
  git -C "$D1U/run" commit -q -m "task work on the wrong branch"
  mkdir -p "$D1U/run/.auto-pilot" # same as above: the task branch carries no .auto-pilot/
  printf 'orchestrator log line\n' >"$D1U/run/.auto-pilot/orchestrator.log"
  printf 'verify broker log line\n' >"$D1U/run/.auto-pilot/verify-broker.log"

  d1uout="$("$SCRIPT" doctor --dir "$D1U/run" --run-id "$RUN_ID1U" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d1urc=$?
  [ "$d1urc" = 0 ] && ok "doctor I1 untracked-only: exits 0" || bad "doctor I1 untracked-only: exits 0" "$d1uout"
  d1uhead="$(git -C "$D1U/run" rev-parse --abbrev-ref HEAD)"
  [ "$d1uhead" = "auto-pilot/$RUN_ID1U" ] && ok "doctor I1 untracked-only: HEAD is observably back on the run-state branch" \
    || bad "doctor I1 untracked-only: HEAD is observably back on the run-state branch" "got $d1uhead"
  have "doctor I1 untracked-only: reports the discard path" 'I1: discarded stale .auto-pilot/ dirt' "$d1uout"
  [ -f "$D1U/run/.auto-pilot/orchestrator.log" ] && [ -f "$D1U/run/.auto-pilot/verify-broker.log" ] \
    && ok "doctor I1 untracked-only: both untracked run logs survive (never git-clean'd)" \
    || bad "doctor I1 untracked-only: both untracked run logs survive"

  # --- I1 negative: HEAD off-branch with dirt OUTSIDE .auto-pilot/ still -----
  # fails closed. Someone's real work is never silently discarded.
  D1N="$DOC/i1-negative"
  RUN_ID1N="doctor-i1-negative"
  _doctor_new_run "$D1N" "$RUN_ID1N"
  {
    printf -- '---\nrun_id: %s\nstatus: active\n---\n\n' "$RUN_ID1N"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D1N/run/.auto-pilot/RUN.md"
  : >"$D1N/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D1N/run/.auto-pilot/REPORT.md"
  git -C "$D1N/run" add .auto-pilot
  git -C "$D1N/run" commit -q -m "seed run state"
  git -C "$D1N/run" checkout -q main
  git -C "$D1N/run" checkout -q -b bestdan/task-y
  echo work >"$D1N/run/task-file"
  git -C "$D1N/run" add task-file
  git -C "$D1N/run" commit -q -m "task work on the wrong branch"
  printf 'uncommitted real work\n' >"$D1N/run/important-work.txt" # dirt OUTSIDE .auto-pilot/
  rm -f "$D1N/run/.auto-pilot/RUN.md"

  d1nout="$("$SCRIPT" doctor --dir "$D1N/run" --run-id "$RUN_ID1N" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d1nrc=$?
  [ "$d1nrc" != 0 ] && ok "doctor I1 negative: real work outside .auto-pilot/ still fails closed (non-zero)" \
    || bad "doctor I1 negative: real work outside .auto-pilot/ still fails closed" "$d1nout"
  d1nhead="$(git -C "$D1N/run" rev-parse --abbrev-ref HEAD)"
  [ "$d1nhead" = "bestdan/task-y" ] && ok "doctor I1 negative: HEAD is left unchanged" \
    || bad "doctor I1 negative: HEAD is left unchanged" "got $d1nhead"
  have "doctor I1 negative: the real-work file is left untouched" 'uncommitted real work' \
    "$(cat "$D1N/run/important-work.txt")"

  # --- I3: every pr-open/in-review/iterating/handed-off task has a real, ----
  # open (or merged) PR. Covers: no PR number recorded, CLOSED, nonexistent,
  # OPEN (holds), and MERGED (holds — NOT a repair; a human merge is healthy).
  D3="$DOC/i3"
  RUN_ID3="doctor-i3"
  _doctor_new_run "$D3" "$RUN_ID3"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID3"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_nopr   | in-review | br-nopr   | main | - | -    | |\n'
    printf '| t_closed | pr-open   | br-closed | main | - | #301 | |\n'
    printf '| t_gone   | pr-open   | br-gone   | main | - | #302 | |\n'
    printf '| t_open   | pr-open   | br-open   | main | - | #303 | |\n'
    printf '| t_merged | handed-off | br-merged | main | - | #304 | |\n'
    # D3: the markdown-link cell shape RUN.md's own writer actually emits —
    # must parse to a bare PR number and hold (be left alone), not park.
    printf '| t_mdlink | pr-open   | br-mdlink | main | - | [#305](https://github.com/bestdan/workflow-skills/pull/305) | |\n'
  } >"$D3/run/.auto-pilot/RUN.md"
  : >"$D3/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D3/run/.auto-pilot/REPORT.md"
  git -C "$D3/run" add .auto-pilot
  git -C "$D3/run" commit -q -m "seed run state"
  I3DB="$D3/ghdb"
  mkdir -p "$I3DB"
  printf 'CLOSED\n' >"$I3DB/301.state"
  # 302: no state file at all -> the fake gh's `.state` read still exits 0
  # (positively reports "nonexistent") -> "does not exist"
  printf 'OPEN\n' >"$I3DB/303.state"
  printf 'false\n' >"$I3DB/303.draft"
  printf '\n' >"$I3DB/303.labels"
  printf 'MERGED\n' >"$I3DB/304.state"
  printf 'OPEN\n' >"$I3DB/305.state"
  export DOCTOR_GH_DB="$I3DB"
  d3out="$("$SCRIPT" doctor --dir "$D3/run" --run-id "$RUN_ID3" --gh "$DOCTOR_GH" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d3rc=$?
  [ "$d3rc" = 0 ] && ok "doctor I3: exits 0 (park is a repair, not a halt)" || bad "doctor I3: exits 0" "$d3out"
  d3run="$(cat "$D3/run/.auto-pilot/RUN.md")"
  have "doctor I3: no-PR row parked" $'t_nopr   | parked' "$d3run"
  have "doctor I3: CLOSED PR row parked" $'t_closed | parked' "$d3run"
  have "doctor I3: nonexistent PR row parked" $'t_gone   | parked' "$d3run"
  have "doctor I3: OPEN PR row left alone" $'t_open   | pr-open' "$d3run"
  have "doctor I3: MERGED PR row left alone (a human merge is healthy, not a violation)" $'t_merged | handed-off' "$d3run"
  lack "doctor I3: a merged row is never parked" $'t_merged | parked' "$d3run"
  have "doctor I3: REPORT.md records why each park happened" 'I3 parked' "$(cat "$D3/run/.auto-pilot/REPORT.md")"
  have "doctor I3 (D3): the markdown-link PR cell parses and holds (left alone)" $'t_mdlink | pr-open' "$d3run"
  lack "doctor I3 (D3): the markdown-link row is never parked" $'t_mdlink | parked' "$d3run"

  # --- I3 (D2): a transient gh failure must never park an in-flight task ----
  D3G="$DOC/i3-gh-fail"
  RUN_ID3G="doctor-i3-gh-fail"
  _doctor_new_run "$D3G" "$RUN_ID3G"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID3G"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_a | pr-open    | br-a | main | - | #401 | |\n'
    printf '| t_b | handed-off | br-b | main | - | #402 | |\n'
  } >"$D3G/run/.auto-pilot/RUN.md"
  : >"$D3G/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D3G/run/.auto-pilot/REPORT.md"
  git -C "$D3G/run" add .auto-pilot
  git -C "$D3G/run" commit -q -m "seed run state"
  d3gout="$("$SCRIPT" doctor --dir "$D3G/run" --run-id "$RUN_ID3G" --gh "$DOCTOR_GH_FAIL" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d3grc=$?
  [ "$d3grc" = 0 ] && ok "doctor I3 (D2): a failing gh still exits 0 (undetermined, not a halt)" \
    || bad "doctor I3 (D2): a failing gh still exits 0" "exit=$d3grc out=$d3gout"
  d3grun="$(cat "$D3G/run/.auto-pilot/RUN.md")"
  have "doctor I3 (D2): pr-open row is NOT parked on a gh failure" $'t_a | pr-open' "$d3grun"
  lack "doctor I3 (D2): pr-open row is not parked" $'t_a | parked' "$d3grun"
  have "doctor I3 (D2): handed-off row is NOT parked on a gh failure" $'t_b | handed-off' "$d3grun"
  lack "doctor I3 (D2): handed-off row is not parked" $'t_b | parked' "$d3grun"
  have "doctor I3 (D2): summary counts the gh failure as skipped, not parked" 'skipped=' "$d3gout"

  # --- I4: a handed-off repo-pr task's review signal (label + not-draft) ----
  D4="$DOC/i4"
  RUN_ID4="doctor-i4"
  _doctor_new_run "$D4" "$RUN_ID4"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID4"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_stale | handed-off | br-stale | main | - | #401 | |\n'
    printf '| t_good  | handed-off | br-good  | main | - | #402 | |\n'
  } >"$D4/run/.auto-pilot/RUN.md"
  : >"$D4/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D4/run/.auto-pilot/REPORT.md"
  git -C "$D4/run" add .auto-pilot
  git -C "$D4/run" commit -q -m "seed run state"
  I4DB="$D4/ghdb"
  mkdir -p "$I4DB"
  # t_stale: a G6/G7 crash gap -- still `task-claim`, still draft.
  printf 'OPEN\n' >"$I4DB/401.state"
  printf 'true\n' >"$I4DB/401.draft"
  printf 'task-claim\n' >"$I4DB/401.labels"
  # t_good: already carries the review signal -- must be a no-op.
  printf 'OPEN\n' >"$I4DB/402.state"
  printf 'false\n' >"$I4DB/402.draft"
  printf 'task-loop\n' >"$I4DB/402.labels"
  export DOCTOR_GH_DB="$I4DB"
  d4out="$("$SCRIPT" doctor --dir "$D4/run" --run-id "$RUN_ID4" --gh "$DOCTOR_GH" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d4rc=$?
  [ "$d4rc" = 0 ] && ok "doctor I4: exits 0" || bad "doctor I4: exits 0" "$d4out"
  have "doctor I4: summary reports the repair for the stale PR" 'I4: t_stale PR #401' "$d4out"
  lack "doctor I4: no repair reported for the already-correct PR" 'I4: t_good' "$d4out"
  have "doctor I4: swaps the label task-claim -> task-loop" 'task-loop' "$(cat "$I4DB/401.labels")"
  lack "doctor I4: removes task-claim from the label file" 'task-claim' "$(cat "$I4DB/401.labels")"
  have "doctor I4: marks the draft PR ready" 'false' "$(cat "$I4DB/401.draft")"
  have "doctor I4: REPORT.md records the repair" 'I4 repaired — t_stale' "$(cat "$D4/run/.auto-pilot/REPORT.md")"
  # t_good's files are untouched -- no edit/ready call was ever made for it.
  [ "$(cat "$I4DB/402.labels")" = "task-loop" ] && ok "doctor I4: already-correct PR's labels are untouched" \
    || bad "doctor I4: already-correct PR's labels are untouched" "$(cat "$I4DB/402.labels")"

  # --- I4: a gh WRITE that fails is never reported as a completed repair -----
  # Same D5 rule the failed `worktree remove` follows: a gh blip mid-repair that
  # still wrote "I4 repaired" into REPORT.md/QUESTIONS.md would be exactly the
  # silent lie doctor exists to eliminate. Reads succeed here (so the repair is
  # correctly ATTEMPTED); both writes fail.
  DOCTOR_GH_WFAIL="$DOC/gh-write-fail"
  cat >"$DOCTOR_GH_WFAIL" <<'GHWEOF'
#!/usr/bin/env bash
set -uo pipefail
db="${DOCTOR_GH_DB:?DOCTOR_GH_DB not set}"
[ "$1" = pr ] || exit 1
sub="$2"; num="$3"; shift 3
case "$sub" in
  view)
    jqexpr=""
    while [ $# -gt 0 ]; do case "$1" in --jq) jqexpr="$2"; shift 2 ;; *) shift ;; esac; done
    case "$jqexpr" in
      .state) cat "$db/$num.state" 2>/dev/null; true ;;
      .isDraft) cat "$db/$num.draft" 2>/dev/null || echo false ;;
      '[.labels[].name] | join(",")') cat "$db/$num.labels" 2>/dev/null; true ;;
      *) exit 1 ;;
    esac
    ;;
  edit|ready) exit 1 ;;   # the write blip
  *) exit 1 ;;
esac
GHWEOF
  chmod +x "$DOCTOR_GH_WFAIL"

  D4W="$DOC/i4-write-fail"
  RUN_ID4W="doctor-i4-write-fail"
  _doctor_new_run "$D4W" "$RUN_ID4W"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID4W"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_stale | handed-off | br-stale | main | - | #411 | |\n'
  } >"$D4W/run/.auto-pilot/RUN.md"
  : >"$D4W/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D4W/run/.auto-pilot/REPORT.md"
  git -C "$D4W/run" add .auto-pilot
  git -C "$D4W/run" commit -q -m "seed run state"
  I4WDB="$D4W/ghdb"
  mkdir -p "$I4WDB"
  printf 'OPEN\n' >"$I4WDB/411.state"
  printf 'true\n' >"$I4WDB/411.draft"
  printf 'task-claim\n' >"$I4WDB/411.labels"
  export DOCTOR_GH_DB="$I4WDB"
  d4wout="$("$SCRIPT" doctor --dir "$D4W/run" --run-id "$RUN_ID4W" --gh "$DOCTOR_GH_WFAIL" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d4wrc=$?
  [ "$d4wrc" = 0 ] && ok "doctor I4 (write blip): exits 0" || bad "doctor I4 (write blip): exits 0" "$d4wout"
  # The summary's repair note has the form `I4: <task> PR #<n>` — the FAILED
  # announcements above deliberately don't, so match the note's exact shape.
  lack "doctor I4 (write blip): a FAILED gh write is never summarized as a repair" 'I4: t_stale PR #411' "$d4wout"
  have "doctor I4 (write blip): the summary counts no repairs at all" 'repaired=0' "$d4wout"
  lack "doctor I4 (write blip): REPORT.md never records the repair that didn't happen" 'I4 repaired' "$(cat "$D4W/run/.auto-pilot/REPORT.md")"
  lack "doctor I4 (write blip): QUESTIONS.md never records the repair that didn't happen" 'missing its repo-pr review signal' "$(cat "$D4W/run/.auto-pilot/QUESTIONS.md")"
  have "doctor I4 (write blip): the failed label write is announced" 'gh pr edit` FAILED' "$d4wout"
  have "doctor I4 (write blip): the failed ready write is announced" 'gh pr ready` FAILED' "$d4wout"
  unset DOCTOR_GH_DB

  # --- I5: orphan worker worktrees under <run root>/workers/ (G2) -----------
  # D4: `pending` is deliberately NOT on the safe list any more — the phase
  # cell lags a live dispatch (it flips AFTER the orchestrator's RUN.md
  # commit+push), so a `pending` row cannot tell "never dispatched" from
  # "dispatched moments ago". Cover: a terminal (`parked`) row -> pruned; an
  # in-flight (`implementing`) row -> left alone; a `pending` row -> left
  # alone (the literal live-run reproduction: `task_14 | pending` with an
  # open PR, from this very run); a `parked` row that STILL has an OPEN PR
  # recorded -> left alone (the open-PR guard); a branch with NO RUN.md row
  # at all -> pruned, BUT only because this run's recorded orchestrator is
  # provably dead (the unmatched case's liveness gate — see the i5-live /
  # i5-dead scenarios below, which own that half of the invariant).
  D5="$DOC/i5"
  RUN_ID5="doctor-i5"
  _doctor_new_run "$D5" "$RUN_ID5"
  D5_DEAD_PID="$(_dead_pid)"
  for br in br-terminal br-unsafe br-pending br-openpr br-nomatch; do
    git -C "$D5/run" checkout -q main
    git -C "$D5/run" checkout -q -b "$br"
    echo "$br" >"$D5/run/$br.txt"
    git -C "$D5/run" add "$br.txt"
    git -C "$D5/run" commit -q -m "$br"
    git -C "$D5/run" push -q origin "$br"
  done
  # _doctor_new_run already created+checked-out auto-pilot/$RUN_ID5 — switch
  # BACK to it (no -b: it already exists) now that the branches above exist.
  git -C "$D5/run" checkout -q "auto-pilot/$RUN_ID5"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n' "$RUN_ID5"
    printf 'orchestrator_pid: %s\norchestrator_started_at: "Wed Jul  9 20:00:00 2026"\n---\n\n' "$D5_DEAD_PID"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_terminal | parked       | br-terminal | main | - | -    | |\n'
    printf '| t_unsafe   | implementing | br-unsafe   | main | - | -    | |\n'
    printf '| t_pending  | pending      | br-pending  | main | - | -    | |\n'
    printf '| t_openpr   | parked       | br-openpr   | main | - | #601 | |\n'
  } >"$D5/run/.auto-pilot/RUN.md"
  : >"$D5/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5/run/.auto-pilot/REPORT.md"
  git -C "$D5/run" add .auto-pilot
  git -C "$D5/run" commit -q -m "seed run state"
  git -C "$D5/run" push -q origin "auto-pilot/$RUN_ID5"
  mkdir -p "$D5/workers"
  git -C "$D5/run" worktree add -q "$D5/workers/w-terminal" br-terminal
  git -C "$D5/run" worktree add -q "$D5/workers/w-unsafe" br-unsafe
  git -C "$D5/run" worktree add -q "$D5/workers/w-pending" br-pending
  git -C "$D5/run" worktree add -q "$D5/workers/w-openpr" br-openpr
  git -C "$D5/run" worktree add -q "$D5/workers/w-nomatch" br-nomatch
  I5DB="$D5/ghdb"
  mkdir -p "$I5DB"
  printf 'OPEN\n' >"$I5DB/601.state"
  export DOCTOR_GH_DB="$I5DB"

  d5out="$("$SCRIPT" doctor --dir "$D5/run" --run-id "$RUN_ID5" --gh "$DOCTOR_GH" 2>&1)"
  d5rc=$?
  [ "$d5rc" = 0 ] && ok "doctor I5: exits 0" || bad "doctor I5: exits 0" "$d5out"
  have "doctor I5: reports the prune of the terminal-phase worktree" 'I5: removed w-terminal' "$d5out"
  [ ! -d "$D5/workers/w-terminal" ] && ok "doctor I5: the terminal-phase orphan worktree is observably gone" \
    || bad "doctor I5: the terminal-phase orphan worktree is observably gone"
  [ -d "$D5/workers/w-unsafe" ] && ok "doctor I5: the in-flight worktree is left alone (unsafe to prune)" \
    || bad "doctor I5: the in-flight worktree is left alone"
  # D4: `pending` no longer green-lights a prune — the exact live-run shape
  # (`task_14 | pending` with an open PR) must survive.
  [ -d "$D5/workers/w-pending" ] && ok "doctor I5 (D4): a pending-phase worktree is left alone (phase lags a live dispatch)" \
    || bad "doctor I5 (D4): a pending-phase worktree is left alone"
  # D4: an OPEN PR still recorded blocks the prune even though the phase is
  # otherwise terminal.
  [ -d "$D5/workers/w-openpr" ] && ok "doctor I5 (D4): a parked row with an OPEN PR is left alone" \
    || bad "doctor I5 (D4): a parked row with an OPEN PR is left alone"
  have "doctor I5: an in-flight worktree is reported as skipped" 'skipped (unsafe to prune)' "$d5out"
  have "doctor I5: a branch with no RUN.md row at all is pruned" 'I5: removed w-nomatch' "$d5out"
  [ ! -d "$D5/workers/w-nomatch" ] && ok "doctor I5: the no-row orphan worktree is observably gone" \
    || bad "doctor I5: the no-row orphan worktree is observably gone"

  unset DOCTOR_GH_DB

  # --- I5 (LIVE DISPATCH): the unmatched-branch data-loss regression ---------
  # The exact shape a LIVE dispatch presents, which the scenario above cannot
  # catch because it pre-bakes the `branch` cell: the orchestrator writes a
  # task's branch/phase/pr back only AFTER /deliver-task returns, so mid-flight
  # the row reads `| t_live | pending | - | … |` and matches NO worktree branch,
  # while the just-created worker worktree is clean, has no commits beyond its
  # base, and has no PR yet — i.e. every other prune condition holds. With the
  # run's orchestrator LIVE, the worktree MUST survive: removing it destroys a
  # dispatch in flight.
  D5L="$DOC/i5-live"
  RUN_ID5L="doctor-i5-live"
  _doctor_new_run "$D5L" "$RUN_ID5L"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n' "$RUN_ID5L"
    printf 'orchestrator_pid: %s\norchestrator_started_at: "%s"\n---\n\n' "$LIVE_PID" "$LIVE_STARTED"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_live | pending | - | main | - | - | |\n'
  } >"$D5L/run/.auto-pilot/RUN.md"
  : >"$D5L/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5L/run/.auto-pilot/REPORT.md"
  git -C "$D5L/run" add .auto-pilot
  git -C "$D5L/run" commit -q -m "seed run state"
  mkdir -p "$D5L/workers"
  # Exactly what /deliver-task does at claim: a fresh worker worktree on a new
  # task branch cut from the base. Nothing committed, nothing pushed, no PR.
  git -C "$D5L/run" worktree add -q -b bestdan/t-live-work "$D5L/workers/w-live" main
  # And a worker left on a DETACHED HEAD (`abbrev-ref` reads back "HEAD", which
  # no row's branch cell can equal) — the same unmatched bucket.
  git -C "$D5L/run" worktree add -q --detach "$D5L/workers/w-detached" main

  d5lout="$("$SCRIPT" doctor --dir "$D5L/run" --run-id "$RUN_ID5L" 2>&1)"
  d5lrc=$?
  [ "$d5lrc" = 0 ] && ok "doctor I5 (live dispatch): exits 0" || bad "doctor I5 (live dispatch): exits 0" "$d5lout"
  [ -d "$D5L/workers/w-live" ] && ok "doctor I5 (live dispatch): a live dispatch's worker worktree SURVIVES (unmatched row + LIVE orchestrator)" \
    || bad "doctor I5 (live dispatch): a live dispatch's worker worktree SURVIVES — it was destroyed"
  [ -d "$D5L/workers/w-detached" ] && ok "doctor I5 (live dispatch): a detached-HEAD worker worktree SURVIVES too" \
    || bad "doctor I5 (live dispatch): a detached-HEAD worker worktree SURVIVES too — it was destroyed"
  have "doctor I5 (live dispatch): reports the live worktree as skipped, not repaired" 'skipped (unsafe to prune)' "$d5lout"
  have "doctor I5 (live dispatch): the skip names the liveness gate" 'not provably dead' "$d5lout"
  lack "doctor I5 (live dispatch): nothing is reported as removed" 'I5: removed' "$d5lout"

  # --- I5 (DEAD orchestrator): the converse — invariant 5 still prunes -------
  # The same unmatched shape, but the run's recorded orchestrator is provably
  # dead: nothing can be mid-dispatch when the process that dispatches is gone,
  # so the orphan is removed as before. The liveness gate must not have been a
  # way of quietly disabling invariant 5.
  D5D="$DOC/i5-dead"
  RUN_ID5D="doctor-i5-dead"
  _doctor_new_run "$D5D" "$RUN_ID5D"
  D5D_DEAD_PID="$(_dead_pid)"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n' "$RUN_ID5D"
    printf 'orchestrator_pid: %s\norchestrator_started_at: "Wed Jul  9 20:00:00 2026"\n---\n\n' "$D5D_DEAD_PID"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_live | pending | - | main | - | - | |\n'
  } >"$D5D/run/.auto-pilot/RUN.md"
  : >"$D5D/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5D/run/.auto-pilot/REPORT.md"
  git -C "$D5D/run" add .auto-pilot
  git -C "$D5D/run" commit -q -m "seed run state"
  mkdir -p "$D5D/workers"
  git -C "$D5D/run" worktree add -q -b bestdan/t-orphan-work "$D5D/workers/w-orphan" main

  d5dout="$("$SCRIPT" doctor --dir "$D5D/run" --run-id "$RUN_ID5D" 2>&1)"
  d5drc=$?
  [ "$d5drc" = 0 ] && ok "doctor I5 (dead orchestrator): exits 0" || bad "doctor I5 (dead orchestrator): exits 0" "$d5dout"
  have "doctor I5 (dead orchestrator): reports the prune" 'I5: removed w-orphan' "$d5dout"
  [ ! -d "$D5D/workers/w-orphan" ] && ok "doctor I5 (dead orchestrator): a genuinely orphaned worktree is still observably pruned" \
    || bad "doctor I5 (dead orchestrator): a genuinely orphaned worktree is still observably pruned"

  # --- I5 (UNDETERMINED liveness): no pid recorded -> fail closed, no prune --
  # D2's posture, applied to the destructive action: an undetermined liveness
  # read is not "dead", so it never green-lights a `worktree remove --force`.
  D5U="$DOC/i5-nopid"
  RUN_ID5U="doctor-i5-nopid"
  _doctor_new_run "$D5U" "$RUN_ID5U"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID5U"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_live | pending | - | main | - | - | |\n'
  } >"$D5U/run/.auto-pilot/RUN.md"
  : >"$D5U/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5U/run/.auto-pilot/REPORT.md"
  git -C "$D5U/run" add .auto-pilot
  git -C "$D5U/run" commit -q -m "seed run state"
  mkdir -p "$D5U/workers"
  git -C "$D5U/run" worktree add -q -b bestdan/t-nopid-work "$D5U/workers/w-nopid" main

  d5uout="$("$SCRIPT" doctor --dir "$D5U/run" --run-id "$RUN_ID5U" 2>&1)"
  [ -d "$D5U/workers/w-nopid" ] && ok "doctor I5 (undetermined liveness): no orchestrator_pid recorded -> the worktree SURVIVES (fail closed)" \
    || bad "doctor I5 (undetermined liveness): no orchestrator_pid recorded -> the worktree SURVIVES"
  have "doctor I5 (undetermined liveness): reported as skipped" 'skipped (unsafe to prune)' "$d5uout"

  # --- I5 (PS UNREADABLE): pid alive, start time unreadable -> `unknown`, ----
  # NOT `mismatch`, and the worktree must still SURVIVE. The data-loss bug this
  # test guards: `ps` is missing/restricted on a sandbox, stripped container,
  # or hardened host, so `ps -o lstart=` in `_pid_state` comes back empty even
  # though `kill -0` says the pid is very much alive. Reading that emptiness as
  # a confirmed `mismatch` (a recycled pid) let doctor treat a live orchestrator
  # as provably dead and prune a live worker's worktree out from under it.
  # Shadow `ps` with a stub that prints nothing and exits non-zero — the exact
  # shape an unreadable `ps` takes — without depending on an actual sandbox.
  PSSTUB="$DOC/ps-unreadable-bin"
  mkdir -p "$PSSTUB"
  printf '#!/bin/sh\nexit 1\n' >"$PSSTUB/ps"
  chmod +x "$PSSTUB/ps"
  # Prepend rather than replace: unlike the other stub sites here, this PATH is
  # used to run `doctor`, which shells out to git constantly. A fixed list would
  # hide git on any host that keeps it outside /usr/bin. $GUARD is already on
  # $PATH (exported above), and $PSSTUB still wins for `ps` by being first.
  PSSTUB_PATH="$PSSTUB:$PATH"

  # _pid_state itself, isolated from doctor: a live pid whose start time can't
  # be read must report `unknown`, never `mismatch` and never `live`.
  RUNMD5PS="$DOC/run-ps-unreadable/.auto-pilot"
  mkdir -p "$RUNMD5PS"
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'orchestrator_pid: %s\n' "$LIVE_PID"
    printf 'orchestrator_started_at: "%s"\n' "$LIVE_STARTED"
    printf 'until: 2026-07-10T06:00:00\n'
    printf -- '---\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| T-1  | claimed | b1   | main | -        | -  | -     |\n'
  } >"$RUNMD5PS/RUN.md"
  psout="$(PATH="$PSSTUB_PATH" "$SCRIPT" status --label com.autopilot.test5ps --dir "$DOC/run-ps-unreadable" 2>&1)"
  have "_pid_state via status: ps-unreadable live pid reports pid=unknown" 'pid=unknown' "$psout"
  lack "_pid_state via status: ps-unreadable live pid never reports mismatch" 'pid=mismatch' "$psout"
  lack "_pid_state via status: ps-unreadable live pid never reports live" 'pid=live' "$psout"

  # The other half of the same undetermined bucket, and the one a working `ps`
  # does NOT rescue: the pid was recorded but the start time was not (a
  # truncated or hand-edited RUN.md). `ps` reads fine, so `actual` is populated
  # and the comparison against an EMPTY recorded value fails — which used to
  # print `mismatch` and hand doctor a prune. Nothing was contradicted here, so
  # the verdict must be `unknown`. Deliberately NOT run under PSSTUB_PATH: the
  # point is that this misreads even where `ps` works perfectly.
  RUNMD5NS="$DOC/run-no-started-at/.auto-pilot"
  mkdir -p "$RUNMD5NS"
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'orchestrator_pid: %s\n' "$LIVE_PID"
    printf 'orchestrator_started_at: ""\n'
    printf 'until: 2026-07-10T06:00:00\n'
    printf -- '---\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| T-1  | claimed | b1   | main | -        | -  | -     |\n'
  } >"$RUNMD5NS/RUN.md"
  nsout="$("$SCRIPT" status --label com.autopilot.test5ns --dir "$DOC/run-no-started-at" 2>&1)"
  have "_pid_state via status: live pid with NO recorded start time reports pid=unknown" 'pid=unknown' "$nsout"
  lack "_pid_state via status: live pid with NO recorded start time never reports mismatch" 'pid=mismatch' "$nsout"
  lack "_pid_state via status: live pid with NO recorded start time never reports live" 'pid=live' "$nsout"

  D5PS="$DOC/i5-ps-unreadable"
  RUN_ID5PS="doctor-i5-ps-unreadable"
  _doctor_new_run "$D5PS" "$RUN_ID5PS"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n' "$RUN_ID5PS"
    printf 'orchestrator_pid: %s\norchestrator_started_at: "%s"\n---\n\n' "$LIVE_PID" "$LIVE_STARTED"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_live | pending | - | main | - | - | |\n'
  } >"$D5PS/run/.auto-pilot/RUN.md"
  : >"$D5PS/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5PS/run/.auto-pilot/REPORT.md"
  git -C "$D5PS/run" add .auto-pilot
  git -C "$D5PS/run" commit -q -m "seed run state"
  mkdir -p "$D5PS/workers"
  git -C "$D5PS/run" worktree add -q -b bestdan/t-ps-unreadable-work "$D5PS/workers/w-ps-unreadable" main

  d5psout="$(PATH="$PSSTUB_PATH" "$SCRIPT" doctor --dir "$D5PS/run" --run-id "$RUN_ID5PS" 2>&1)"
  [ -d "$D5PS/workers/w-ps-unreadable" ] \
    && ok "doctor I5 (ps unreadable): a live orchestrator's worktree SURVIVES when ps can't confirm its start time" \
    || bad "doctor I5 (ps unreadable): a live orchestrator's worktree SURVIVES — it was destroyed"
  have "doctor I5 (ps unreadable): reported as skipped" 'skipped (unsafe to prune)' "$d5psout"
  have "doctor I5 (ps unreadable): the skip names the actual pid_state (unknown), not mismatch" \
    'orchestrator is unknown, not provably dead' "$d5psout"
  lack "doctor I5 (ps unreadable): nothing is reported as removed" 'I5: removed' "$d5psout"

  # --- I5 (CORRUPT git state): a failed git read must skip, not fail-open ---
  # The bug this task exists to fix: `status --porcelain`, `rev-parse HEAD`,
  # and `rev-parse --abbrev-ref HEAD` were all read with `2>/dev/null`, and an
  # EMPTY result from a FAILED read was indistinguishable from a genuinely
  # clean/unborn/detached worktree — every guard passed and a worktree with
  # true state UNKNOWN was fed straight to `git worktree remove --force`. Here
  # the worktree's `.git` link is corrupted over real uncommitted work, so
  # every one of those reads fails outright (non-zero exit, not just empty
  # stdout). The dispatch's recorded orchestrator is also provably DEAD, so
  # the ONLY thing standing between this worktree and destruction is the rc
  # check itself — this is not the liveness gate saving it. The removal must
  # never be ATTEMPTED (no "I5: removed", no "FAILED to remove" for it) —
  # distinguishing "skipped" from "attempted and failed by luck", which is
  # all that saved the WIP on unpatched main.
  D5C="$DOC/i5-corrupt"
  RUN_ID5C="doctor-i5-corrupt"
  _doctor_new_run "$D5C" "$RUN_ID5C"
  D5C_DEAD_PID="$(_dead_pid)"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n' "$RUN_ID5C"
    printf 'orchestrator_pid: %s\norchestrator_started_at: "Wed Jul  9 20:00:00 2026"\n---\n\n' "$D5C_DEAD_PID"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_corrupt | pending | - | main | - | - | |\n'
  } >"$D5C/run/.auto-pilot/RUN.md"
  : >"$D5C/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D5C/run/.auto-pilot/REPORT.md"
  git -C "$D5C/run" add .auto-pilot
  git -C "$D5C/run" commit -q -m "seed run state"
  mkdir -p "$D5C/workers"
  git -C "$D5C/run" worktree add -q -b bestdan/t-corrupt-work "$D5C/workers/w-corrupt" main
  echo "uncommitted WIP" >"$D5C/workers/w-corrupt/wip.txt"
  # Corrupt the worktree's `.git` link so every read INSIDE it fails outright.
  echo "gitdir: /nonexistent/gitdir/for/w-corrupt" >"$D5C/workers/w-corrupt/.git"

  d5cout="$("$SCRIPT" doctor --dir "$D5C/run" --run-id "$RUN_ID5C" 2>&1)"
  d5crc=$?
  [ "$d5crc" = 0 ] && ok "doctor I5 (corrupt git state): exits 0" || bad "doctor I5 (corrupt git state): exits 0" "$d5cout"
  [ -d "$D5C/workers/w-corrupt" ] && ok "doctor I5 (corrupt git state): the corrupted worktree SURVIVES" \
    || bad "doctor I5 (corrupt git state): the corrupted worktree SURVIVES — it was destroyed"
  [ -f "$D5C/workers/w-corrupt/wip.txt" ] && ok "doctor I5 (corrupt git state): the uncommitted WIP file is still there" \
    || bad "doctor I5 (corrupt git state): the uncommitted WIP file is still there"
  have "doctor I5 (corrupt git state): reported as skipped — undetermined, not attempted" 'skipped (undetermined' "$d5cout"
  lack "doctor I5 (corrupt git state): removal of the corrupt worktree was never ATTEMPTED (not reported removed)" 'I5: removed w-corrupt' "$d5cout"
  lack "doctor I5 (corrupt git state): removal of the corrupt worktree was never ATTEMPTED (not reported failed)" 'FAILED to remove' "$d5cout"
  # The SUMMARY must say skipped too, not just stdout. The summary line is the
  # machine-readable one — a wrapper (or a human triaging fast) greps THAT, and
  # `ok=N ... skipped=0` is a clean bill of health for a DESTRUCTIVE invariant
  # that could not evaluate the worktree at all. I3/I6 already fold undetermined
  # into n_skipped; I5 counted it as `ok`.
  lack "doctor I5 (corrupt git state): an undetermined worktree is NOT summarised as skipped=0" \
    'skipped=0' "$d5cout"
  have "doctor I5 (corrupt git state): the summary counts it as skipped, naming the worktree" \
    'skipped=1 (I5: w-corrupt (git unreadable))' "$d5cout"

  # --- I6: a chained task's parent tip moved off its frozen base_sha --------
  # (a) the orchestrator moved the base mid-run, no merge -> park the child.
  D6="$DOC/i6"
  RUN_ID6="doctor-i6"
  _doctor_new_run "$D6" "$RUN_ID6"
  git -C "$D6/run" checkout -q main
  git -C "$D6/run" checkout -q -b br-parent
  echo p >"$D6/run/p.txt"
  git -C "$D6/run" add p.txt
  git -C "$D6/run" commit -q -m p
  git -C "$D6/run" push -q origin br-parent
  PARENT_SHA6="$(git -C "$D6/run" rev-parse br-parent)"
  echo p2 >"$D6/run/p2.txt"
  git -C "$D6/run" add p2.txt
  git -C "$D6/run" commit -q -m "parent moved (orchestrator)"
  git -C "$D6/run" push -q origin br-parent
  git -C "$D6/run" checkout -q "auto-pilot/$RUN_ID6"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID6"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_parent | pending      | br-parent | main      | - | - | |\n'
    printf '| t_child  | implementing | br-child  | br-parent | %s | - | |\n' "$PARENT_SHA6"
  } >"$D6/run/.auto-pilot/RUN.md"
  : >"$D6/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D6/run/.auto-pilot/REPORT.md"
  git -C "$D6/run" add .auto-pilot
  git -C "$D6/run" commit -q -m "seed run state"

  d6out="$("$SCRIPT" doctor --dir "$D6/run" --run-id "$RUN_ID6" --questions .auto-pilot/QUESTIONS.md 2>&1)"
  d6rc=$?
  [ "$d6rc" = 0 ] && ok "doctor I6: exits 0 (park is a repair, not a halt)" || bad "doctor I6: exits 0" "$d6out"
  have "doctor I6: parks the child whose parent's tip diverged" $'t_child  | parked' "$(cat "$D6/run/.auto-pilot/RUN.md")"
  have "doctor I6: REPORT.md explains why" 'without the parent'"'"'s PR merging' "$(cat "$D6/run/.auto-pilot/REPORT.md")"

  # (b) the SAME divergence, but the parent's PR is MERGED -> a human merge is
  # the expected trigger; the remedy is restack, never a park.
  D6M="$DOC/i6-merged"
  RUN_ID6M="doctor-i6-merged"
  _doctor_new_run "$D6M" "$RUN_ID6M"
  git -C "$D6M/run" checkout -q main
  git -C "$D6M/run" checkout -q -b br-parent
  echo p >"$D6M/run/p.txt"
  git -C "$D6M/run" add p.txt
  git -C "$D6M/run" commit -q -m p
  git -C "$D6M/run" push -q origin br-parent
  PARENT_SHA6M="$(git -C "$D6M/run" rev-parse br-parent)"
  git -C "$D6M/run" checkout -q main
  git -C "$D6M/run" merge -q --squash br-parent >/dev/null
  git -C "$D6M/run" commit -q -m "parent squash-merged"
  git -C "$D6M/run" push -q origin main
  git -C "$D6M/run" checkout -q "auto-pilot/$RUN_ID6M"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID6M"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    # D3: the parent's pr cell is the markdown-link form RUN.md's own writer
    # emits — must parse to a bare PR number, same as the bare-`#501` shape.
    printf '| t_parent | handed-off   | br-parent | main      | -  | [#501](https://github.com/bestdan/workflow-skills/pull/501) | |\n'
    printf '| t_child  | implementing | br-child  | br-parent | %s | -    | |\n' "$PARENT_SHA6M"
  } >"$D6M/run/.auto-pilot/RUN.md"
  : >"$D6M/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D6M/run/.auto-pilot/REPORT.md"
  git -C "$D6M/run" add .auto-pilot
  git -C "$D6M/run" commit -q -m "seed run state"
  I6MDB="$D6M/ghdb"
  mkdir -p "$I6MDB"
  printf 'MERGED\n' >"$I6MDB/501.state"
  export DOCTOR_GH_DB="$I6MDB"
  d6mout="$("$SCRIPT" doctor --dir "$D6M/run" --run-id "$RUN_ID6M" --gh "$DOCTOR_GH" 2>&1)"
  d6mrc=$?
  [ "$d6mrc" = 0 ] && ok "doctor I6 (merged parent): exits 0" || bad "doctor I6 (merged parent): exits 0" "$d6mout"
  have "doctor I6 (merged parent, D3 markdown-link pr cell): says the remedy is restack, not park" 'remedy is restack, not park' "$d6mout"
  lack "doctor I6 (merged parent, D3 markdown-link pr cell): the child is NOT parked" $'t_child  | parked' "$(cat "$D6M/run/.auto-pilot/RUN.md")"

  unset DOCTOR_GH_DB

  # (c) the SAME divergence, but the parent's PR state is UNREADABLE (a
  # failing gh, D2/D7) — must NOT park. Parking here would be the exact
  # violation the invariant's own comment warns against: a parent that
  # actually merged, parked anyway because its state could not be read.
  D6U="$DOC/i6-gh-unreadable"
  RUN_ID6U="doctor-i6-gh-unreadable"
  _doctor_new_run "$D6U" "$RUN_ID6U"
  git -C "$D6U/run" checkout -q main
  git -C "$D6U/run" checkout -q -b br-parent
  echo p >"$D6U/run/p.txt"
  git -C "$D6U/run" add p.txt
  git -C "$D6U/run" commit -q -m p
  git -C "$D6U/run" push -q origin br-parent
  PARENT_SHA6U="$(git -C "$D6U/run" rev-parse br-parent)"
  echo p2 >"$D6U/run/p2.txt"
  git -C "$D6U/run" add p2.txt
  git -C "$D6U/run" commit -q -m "parent moved"
  git -C "$D6U/run" push -q origin br-parent
  git -C "$D6U/run" checkout -q "auto-pilot/$RUN_ID6U"
  {
    printf -- '---\nrun_id: %s\nstatus: active\nbase_branch: main\n---\n\n' "$RUN_ID6U"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_parent | handed-off   | br-parent | main      | -  | #601 | |\n'
    printf '| t_child  | implementing | br-child  | br-parent | %s | -    | |\n' "$PARENT_SHA6U"
  } >"$D6U/run/.auto-pilot/RUN.md"
  : >"$D6U/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D6U/run/.auto-pilot/REPORT.md"
  git -C "$D6U/run" add .auto-pilot
  git -C "$D6U/run" commit -q -m "seed run state"
  d6uout="$("$SCRIPT" doctor --dir "$D6U/run" --run-id "$RUN_ID6U" --gh "$DOCTOR_GH_FAIL" 2>&1)"
  d6urc=$?
  [ "$d6urc" = 0 ] && ok "doctor I6 (D2/D7, gh unreadable): exits 0 (undetermined, not a halt)" \
    || bad "doctor I6 (D2/D7, gh unreadable): exits 0" "exit=$d6urc out=$d6uout"
  lack "doctor I6 (D2/D7, gh unreadable): the child is NOT parked on an unreadable parent state" \
    $'t_child  | parked' "$(cat "$D6U/run/.auto-pilot/RUN.md")"
  have "doctor I6 (D2/D7, gh unreadable): reports it as skipped/unreadable, not a park" \
    'parent PR state unreadable' "$d6uout"

  # --- I7: forward progress across ITERATIONS within one live process ------
  D7="$DOC/i7"
  RUN_ID7="doctor-i7"
  _doctor_new_run "$D7" "$RUN_ID7"
  {
    printf -- '---\nrun_id: %s\nstatus: active\npause_reason: \n---\n\n' "$RUN_ID7"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D7/run/.auto-pilot/RUN.md"
  : >"$D7/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D7/run/.auto-pilot/REPORT.md"
  git -C "$D7/run" add .auto-pilot
  git -C "$D7/run" commit -q -m "seed run state"

  "$SCRIPT" doctor --dir "$D7/run" --run-id "$RUN_ID7" --no-progress-limit 3 >/dev/null 2>&1
  "$SCRIPT" doctor --dir "$D7/run" --run-id "$RUN_ID7" --no-progress-limit 3 >/dev/null 2>&1
  d7out="$("$SCRIPT" doctor --dir "$D7/run" --run-id "$RUN_ID7" --no-progress-limit 3 2>&1)"
  d7rc=$?
  [ "$d7rc" = 30 ] && ok "doctor I7: halts after N consecutive no-progress iterations, exits 30" \
    || bad "doctor I7: halts after N consecutive no-progress iterations" "exit=$d7rc out=$d7out"
  have "doctor I7: reason names invariant 7" 'invariant 7' "$d7out"
  have "doctor I7: halt is observable in REPORT.md" 'ALARM' "$(cat "$D7/run/.auto-pilot/REPORT.md")"
  # Every doctor halt goes through the same jailed alarm seam, not just I2's.
  have "doctor I7: the halt files an alarm-request naming invariant 7" \
    'invariant 7' "$(cat "$D7/run/.auto-pilot/alarm-requests/invariant.alarm" 2>/dev/null)"

  # I7 with the side channel BROKEN (task 26). `_write_supervisor_state` — the
  # no-progress COUNTER — `die`s (an `exit`) on a write failure and runs immediately
  # BEFORE this halt, so an unwritable run dir would abort the whole doctor process
  # at `mktemp failed` before invariant 7 ever halts, and before doctor's own exit-30
  # (HALT) contract could be honoured: the caller sees a bare `2`, reads it as
  # "doctor errored" rather than "the run must stop", and a wedged run keeps being
  # dispatched. The counter itself cannot advance while the dir is unwritable — but
  # the halt for THIS iteration must still fire, and must still be an exit 30.
  D7U="$DOC/i7-unwritable"
  RUN_ID7U="doctor-i7u"
  _doctor_new_run "$D7U" "$RUN_ID7U"
  {
    printf -- '---\nrun_id: %s\nstatus: active\npause_reason: \n---\n\n' "$RUN_ID7U"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D7U/run/.auto-pilot/RUN.md"
  : >"$D7U/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D7U/run/.auto-pilot/REPORT.md"
  git -C "$D7U/run" add .auto-pilot
  git -C "$D7U/run" commit -q -m "seed run state"
  chmod -w "$D7U/run/.auto-pilot"
  d7uout="$("$SCRIPT" doctor --dir "$D7U/run" --run-id "$RUN_ID7U" --no-progress-limit 1 2>&1)"
  d7urc=$?
  chmod +w "$D7U/run/.auto-pilot"
  [ "$d7urc" = 30 ] \
    && ok "doctor I7 (unwritable run dir): still HALTS with exit 30, never the bare die (2)" \
    || bad "doctor I7 (unwritable run dir): still HALTS with exit 30, never the bare die (2)" "exit=$d7urc out=$d7uout"
  have "doctor I7 (unwritable run dir): the halt still names invariant 7" 'invariant 7' "$d7uout"

  # THE gate: a caller checking doctor's exit code cannot reach a dispatch.
  would_dispatch=1
  [ "$d7rc" = 30 ] && would_dispatch=0
  [ "$would_dispatch" = 0 ] && ok "doctor: the loop cannot advance to a dispatch while a halt is in effect (exit 30 gates it)" \
    || bad "doctor: the loop cannot advance while a halt is in effect"

  # I7 never fires while the run is legitimately paused, however many repeats.
  D7P="$DOC/i7-paused"
  RUN_ID7P="doctor-i7-paused"
  _doctor_new_run "$D7P" "$RUN_ID7P"
  {
    printf -- '---\nrun_id: %s\nstatus: paused\npaused_until: 2099-01-01T00:00:00\n---\n\n' "$RUN_ID7P"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D7P/run/.auto-pilot/RUN.md"
  : >"$D7P/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D7P/run/.auto-pilot/REPORT.md"
  git -C "$D7P/run" add .auto-pilot
  git -C "$D7P/run" commit -q -m "seed paused run state"
  i=0
  while [ "$i" -lt 5 ]; do
    "$SCRIPT" doctor --dir "$D7P/run" --run-id "$RUN_ID7P" --no-progress-limit 3 >/dev/null 2>&1
    i=$((i + 1))
  done
  lack "doctor I7: a paused run never halts, however many repeats" 'systemic' "$(cat "$D7P/run/.auto-pilot/RUN.md")"

  # --- I7 (D6): --context resume RESETS the no-progress counter instead of --
  # incrementing it. Doctor runs once at the top of --resume and again at the
  # top of the first loop iteration, with HEAD necessarily unchanged between
  # the two — without D6's fix that pair alone would put the counter at 2,
  # one strike from a spurious halt before any work runs. Drive it right up
  # to the limit under `loop` context, then prove a `resume` call resets
  # instead of tipping it over.
  D7R="$DOC/i7-resume-context"
  RUN_ID7R="doctor-i7-resume-context"
  _doctor_new_run "$D7R" "$RUN_ID7R"
  {
    printf -- '---\nrun_id: %s\nstatus: active\npause_reason: \n---\n\n' "$RUN_ID7R"
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t1 | pending | - | main | - | - | |\n'
  } >"$D7R/run/.auto-pilot/RUN.md"
  : >"$D7R/run/.auto-pilot/QUESTIONS.md"
  printf '# report\n' >"$D7R/run/.auto-pilot/REPORT.md"
  git -C "$D7R/run" add .auto-pilot
  git -C "$D7R/run" commit -q -m "seed run state"
  # Two no-progress `loop` iterations (count -> 2, one shy of the limit=3),
  # then a `resume` call: if resume incremented, this would halt (count=3).
  "$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context loop >/dev/null 2>&1
  "$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context loop >/dev/null 2>&1
  d7rout="$("$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context resume 2>&1)"
  d7rrc=$?
  [ "$d7rrc" = 0 ] && ok "doctor I7 (D6): a resume call never halts, even after 2 prior no-progress loop iterations" \
    || bad "doctor I7 (D6): a resume call never halts" "exit=$d7rrc out=$d7rout"
  # Follow it with TWO more `loop` iterations: if resume had reset the
  # counter as intended, this is only iterations 1-2 post-reset and must NOT
  # halt; if resume had (wrongly) incremented, the limit would already have
  # been blown past.
  "$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context loop >/dev/null 2>&1
  d7rout2="$("$SCRIPT" doctor --dir "$D7R/run" --run-id "$RUN_ID7R" --no-progress-limit 3 --context loop 2>&1)"
  d7rrc2=$?
  [ "$d7rrc2" != 30 ] && ok "doctor I7 (D6): resume genuinely reset the counter (2 more loop iterations still don't halt)" \
    || bad "doctor I7 (D6): resume genuinely reset the counter" "exit=$d7rrc2 out=$d7rout2"

  # --- fail-closed: bad usage --------------------------------------------
  o="$("$SCRIPT" doctor --run-id x 2>&1)"
  [ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --dir' \
    && ok "doctor fail-closed: missing --dir" || bad "doctor fail-closed: missing --dir" "$o"
  o="$("$SCRIPT" doctor --dir "$D1/run" --run-id x --handler bogus 2>&1)"
  [ $? = 2 ] && printf '%s' "$o" | grep -q 'unknown --handler' \
    && ok "doctor fail-closed: unknown --handler" || bad "doctor fail-closed: unknown --handler" "$o"
  o="$("$SCRIPT" doctor --dir "$D1/run" --run-id x --context bogus 2>&1)"
  [ $? = 2 ] && printf '%s' "$o" | grep -q 'unknown --context' \
    && ok "doctor fail-closed: unknown --context" || bad "doctor fail-closed: unknown --context" "$o"
else
  echo "skip - doctor: git not available"
fi

# shellcheck source=scripts/lib/spawn-orchestrator-test-epilogue.sh
. "$SO_LIB/spawn-orchestrator-test-epilogue.sh"
