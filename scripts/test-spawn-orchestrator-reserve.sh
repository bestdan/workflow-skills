#!/usr/bin/env bash
# shellcheck disable=SC2154 # Reads names the shared prelude defines.
# The auto-pilot pre-invoke reserve gate — `spawn-orchestrator.sh reserve-gate`
# (prose-to-code index row 5).
#
# One part of the orchestrator harness for scripts/spawn-orchestrator.sh. Every
# part is self-contained and offline: the prelude builds it a private fixture
# tree and the isolation guards, the epilogue asserts they held. See
# scripts/lib/spawn-orchestrator-test-prelude.sh for what the parts share, and
# dev_docs/gate-performance.md for why this suite is several files.
#
# What these pin is the arithmetic the prose used to ask an agent to hand-walk,
# so every case here is one of the rules in the subcommand's header comment:
# a cross-window interval is discarded rather than counted; a negative
# same-window delta clears the baseline instead of replacing it; the record
# stops at 20; a short record never lowers the floor; the pause verdict; and a
# failed read records nothing at all. Each asserts the RUN.md bytes as well as
# stdout, because the bookkeeping half of the call has no other observable.
#
# Run directly: bash scripts/test-spawn-orchestrator-reserve.sh
set -uo pipefail
SO_PART=reserve
SO_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/spawn-orchestrator-test-prelude.sh
. "$SO_LIB/spawn-orchestrator-test-prelude.sh"

RG="$BASE/reserve"
mkdir -p "$RG"

# A RUN.md carrying only what reserve-gate reads: the two bookkeeping fields
# and the configured floor. `reserve:` is written as its own argument so a case
# can exercise the RUN.md floor and the --floor override separately.
rg_fixture() { # <name> <reserve> <baseline value> <deltas value> -> path
  local d="$RG/$1"
  mkdir -p "$d/.auto-pilot"
  cat >"$d/.auto-pilot/RUN.md" <<EOF
---
run_id: $1
status: active
reserve: $2
usage_delta_baseline:$3
usage_deltas: $4
---

| task | phase | branch | base | base_sha | pr | notes |
| ---- | ----- | ------ | ---- | -------- | -- | ----- |
EOF
  printf '%s\n' "$d/.auto-pilot/RUN.md"
}

# Run the gate, capturing stdout and the exit code into $rg_out / $rg_code.
rg_run() {
  rg_out="$("$SCRIPT" reserve-gate "$@" 2>&1)"
  rg_code=$?
}

rg_field() { # <run.md> <key> -> the front-matter value, comments stripped
  awk '/^---$/{c++; next} c==1{print}' "$1" | grep -E "^$2:" | head -1 \
    | sed -e "s/^$2: *//" -e 's/[[:space:]]*$//'
}

rg_exit_is() {
  if [ "$rg_code" = "$2" ]; then ok "$1"; else bad "$1" "want exit $2 got $rg_code: $rg_out"; fi
}

# --- the empty record: no baseline means no interval to close ------------------
f="$(rg_fixture empty 15 '' '[]')"
rg_run --run-md "$f" --percent 10 --reset-epoch 111
rg_exit_is "first sample: proceeds" 0
have "first sample: floor reserve, no samples yet" "RESERVE: reserve=15 headroom=90 verdict=proceed samples=0" "$rg_out"
if [ "$(rg_field "$f" usage_deltas)" = "[]" ]; then
  ok "first sample: appends nothing (there was no baseline to close an interval against)"
else bad "first sample: appends nothing" "$(rg_field "$f" usage_deltas)"; fi
if [ "$(rg_field "$f" usage_delta_baseline)" = "{percent: 10, reset_epoch: 111}" ]; then
  ok "first sample: records the baseline"
else bad "first sample: records the baseline" "$(rg_field "$f" usage_delta_baseline)"; fi

# --- the ordinary same-window interval ----------------------------------------
f="$(rg_fixture same_window 15 ' {percent: 10, reset_epoch: 111}' '[]')"
rg_run --run-md "$f" --percent 18 --reset-epoch 111
rg_exit_is "same-window delta: proceeds" 0
if [ "$(rg_field "$f" usage_deltas)" = "[{percent: 8, reset_epoch: 111}]" ]; then
  ok "same-window delta: appends percent 8 tagged with the window"
else bad "same-window delta: appends percent 8" "$(rg_field "$f" usage_deltas)"; fi
have "same-window delta: counted as a sample" "samples=1" "$rg_out"
if [ "$(rg_field "$f" usage_delta_baseline)" = "{percent: 18, reset_epoch: 111}" ]; then
  ok "same-window delta: baseline advances to the current read"
else bad "same-window delta: baseline advances" "$(rg_field "$f" usage_delta_baseline)"; fi

# --- the same cached reading at four boundaries records ONE interval ------------
# /deliver-task reads --session-status once per cycle and calls this at each of
# its four boundaries with that one cached reading, so calls 2-4 see the
# baseline call 1 just wrote. A zero-percent interval is therefore not recorded
# — otherwise one task would deposit four entries, reach the five-sample
# threshold on its own, and evict real intervals from the 20-entry cap.
f="$(rg_fixture repeat 15 ' {percent: 10, reset_epoch: 111}' '[]')"
rg_run --run-md "$f" --percent 18 --reset-epoch 111
rg_exit_is "boundary 1: proceeds" 0
i=2
while [ "$i" -le 4 ]; do
  rg_run --run-md "$f" --percent 18 --reset-epoch 111
  rg_exit_is "boundary $i: proceeds on the same cached reading" 0
  i=$((i + 1))
done
if [ "$(rg_field "$f" usage_deltas)" = "[{percent: 8, reset_epoch: 111}]" ]; then
  ok "four boundaries, one cached reading: exactly ONE interval recorded"
else bad "four boundaries record one interval" "$(rg_field "$f" usage_deltas)"; fi
have "the repeated calls report the one sample, not four" "samples=1" "$rg_out"

# --- epoch mismatch discards ---------------------------------------------------
# The interval straddles a rate-window reset, so its percent difference means
# nothing: it is thrown away, and the new window starts from a fresh baseline.
f="$(rg_fixture cross_window 15 ' {percent: 90, reset_epoch: 111}' '[{percent: 4, reset_epoch: 111}]')"
rg_run --run-md "$f" --percent 5 --reset-epoch 222
rg_exit_is "cross-window: proceeds" 0
if [ "$(rg_field "$f" usage_deltas)" = "[{percent: 4, reset_epoch: 111}]" ]; then
  ok "epoch mismatch: the cross-window interval is DISCARDED, not appended"
else bad "epoch mismatch: discards" "$(rg_field "$f" usage_deltas)"; fi
if [ "$(rg_field "$f" usage_delta_baseline)" = "{percent: 5, reset_epoch: 222}" ]; then
  ok "epoch mismatch: baseline replaced with the current read (a fresh window)"
else bad "epoch mismatch: baseline replaced" "$(rg_field "$f" usage_delta_baseline)"; fi
have "epoch mismatch: the old window's entry is not a sample for this one" "samples=0" "$rg_out"

# --- a negative same-window delta clears ---------------------------------------
# Consumed percent cannot fall inside one window, so this reading is corrupt.
# Replacing the baseline with it would inflate the NEXT interval, which is why
# the rule clears rather than replaces.
f="$(rg_fixture negative 15 ' {percent: 40, reset_epoch: 111}' '[{percent: 4, reset_epoch: 111}]')"
rg_run --run-md "$f" --percent 30 --reset-epoch 111
rg_exit_is "negative delta: proceeds (the gate still decides on headroom)" 0
if [ "$(rg_field "$f" usage_deltas)" = "[{percent: 4, reset_epoch: 111}]" ]; then
  ok "negative delta: appends nothing"
else bad "negative delta: appends nothing" "$(rg_field "$f" usage_deltas)"; fi
if [ -z "$(rg_field "$f" usage_delta_baseline)" ]; then
  ok "negative delta: CLEARS the baseline rather than storing the anomalous read"
else bad "negative delta: clears the baseline" "$(rg_field "$f" usage_delta_baseline)"; fi

# --- read-failed records nothing ------------------------------------------------
# A non-zero usage read is not a sample and not headroom. It clears the
# baseline, leaves the record alone, and returns its own exit code so the caller
# cannot mistake it for `proceed`.
f="$(rg_fixture read_failed 15 ' {percent: 40, reset_epoch: 111}' '[{percent: 4, reset_epoch: 111}]')"
rg_run --run-md "$f" --read-failed
rg_exit_is "read-failed: exits 3, neither proceed nor a measured pause" 3
have "read-failed: verdict is fallback with no headroom claimed" "headroom=unknown verdict=fallback" "$rg_out"
if [ "$(rg_field "$f" usage_deltas)" = "[{percent: 4, reset_epoch: 111}]" ]; then
  ok "read-failed: records no delta"
else bad "read-failed: records no delta" "$(rg_field "$f" usage_deltas)"; fi
if [ -z "$(rg_field "$f" usage_delta_baseline)" ]; then
  ok "read-failed: clears the baseline (it cannot close a later interval)"
else bad "read-failed: clears the baseline" "$(rg_field "$f" usage_delta_baseline)"; fi
rg_run --run-md "$f" --read-failed --percent 10
rg_exit_is "read-failed rejects a --percent (a failed read has no percent)" 2

# --- the cap at 20 ---------------------------------------------------------------
# Build a full 20-entry record, then add one more: the oldest is dropped and the
# list stays exactly 20 long.
full=""
i=1
while [ "$i" -le 20 ]; do
  [ -z "$full" ] || full="$full, "
  full="$full{percent: $i, reset_epoch: 111}"
  i=$((i + 1))
done
f="$(rg_fixture cap 15 ' {percent: 50, reset_epoch: 111}' "[$full]")"
rg_run --run-md "$f" --percent 53 --reset-epoch 111
rg_exit_is "cap: proceeds" 0
after="$(rg_field "$f" usage_deltas)"
n_entries="$(printf '%s' "$after" | tr ',' '\n' | grep -c 'percent:')"
if [ "$n_entries" = 20 ]; then ok "cap: the record stays at 20 entries"; else bad "cap: stays at 20" "got $n_entries: $after"; fi
case "$after" in
  '[{percent: 2, reset_epoch: 111},'*) ok "cap: the OLDEST entry is the one dropped" ;;
  *) bad "cap: drops the oldest" "$after" ;;
esac
case "$after" in
  *'{percent: 3, reset_epoch: 111}]') ok "cap: the newest entry is appended" ;;
  *) bad "cap: appends the newest" "$after" ;;
esac

# --- below the sample floor, the reserve IS the floor ------------------------------
# Four in-window deltas, one of them large enough that 1.25x would exceed the
# floor. It must not: a short record never lowers OR raises the fixed floor.
f="$(rg_fixture short 15 '' '[{percent: 30, reset_epoch: 111}, {percent: 2, reset_epoch: 111}, {percent: 2, reset_epoch: 111}, {percent: 2, reset_epoch: 111}]')"
rg_run --run-md "$f" --percent 50 --reset-epoch 111
rg_exit_is "short record: proceeds" 0
have "four samples: reserve is the floor, not 1.25 x the worst" "reserve=15 headroom=50 verdict=proceed samples=4" "$rg_out"

# The fifth in-window sample is what switches the calculation on: worst = 30,
# ceil(30 * 1.25) = 38.
f="$(rg_fixture measured 15 ' {percent: 40, reset_epoch: 111}' '[{percent: 30, reset_epoch: 111}, {percent: 2, reset_epoch: 111}, {percent: 2, reset_epoch: 111}, {percent: 2, reset_epoch: 111}]')"
rg_run --run-md "$f" --percent 41 --reset-epoch 111
rg_exit_is "measured reserve: proceeds at 59% headroom" 0
have "five samples: reserve = ceil(worst 30 x 1.25) = 38" "reserve=38 headroom=59 verdict=proceed samples=5" "$rg_out"

# The same five samples, with the floor above the measured value: max() keeps
# the floor.
f="$(rg_fixture measured_floor 15 ' {percent: 40, reset_epoch: 111}' '[{percent: 30, reset_epoch: 111}, {percent: 2, reset_epoch: 111}, {percent: 2, reset_epoch: 111}, {percent: 2, reset_epoch: 111}]')"
rg_run --run-md "$f" --percent 41 --reset-epoch 111 --floor 60
rg_exit_is "floor above the measured reserve: pauses" 1
have "--floor overrides RUN.md's reserve and wins the max()" "reserve=60 headroom=59 verdict=pause samples=5" "$rg_out"

# --samples moves the threshold, so the same four-entry record measures.
f="$(rg_fixture samples_flag 15 '' '[{percent: 30, reset_epoch: 111}, {percent: 2, reset_epoch: 111}, {percent: 2, reset_epoch: 111}, {percent: 2, reset_epoch: 111}]')"
rg_run --run-md "$f" --percent 50 --reset-epoch 111 --samples 4
rg_exit_is "--samples 4: proceeds" 0
have "--samples lowers the sample threshold" "reserve=38 headroom=50 verdict=proceed samples=4" "$rg_out"

# --- the pause verdict -------------------------------------------------------------
f="$(rg_fixture pause 15 '' '[]')"
rg_run --run-md "$f" --percent 90 --reset-epoch 111
rg_exit_is "headroom below the floor: exits 1" 1
have "pause verdict is printed, not only signalled by the exit code" "reserve=15 headroom=10 verdict=pause samples=0" "$rg_out"
# The boundary is strict: headroom == reserve proceeds, one less pauses.
f="$(rg_fixture boundary 15 '' '[]')"
rg_run --run-md "$f" --percent 85 --reset-epoch 111
rg_exit_is "headroom exactly equal to the reserve proceeds" 0
f="$(rg_fixture boundary2 15 '' '[]')"
rg_run --run-md "$f" --percent 86 --reset-epoch 111
rg_exit_is "one point below the reserve pauses" 1

# A paused boundary still does its bookkeeping — the pause is the verdict, not
# an abort before the rewrite.
f="$(rg_fixture pause_writes 15 ' {percent: 80, reset_epoch: 111}' '[]')"
rg_run --run-md "$f" --percent 90 --reset-epoch 111
rg_exit_is "pause: exits 1" 1
if [ "$(rg_field "$f" usage_deltas)" = "[{percent: 10, reset_epoch: 111}]" ]; then
  ok "pause: the interval is still recorded"
else bad "pause: records the interval" "$(rg_field "$f" usage_deltas)"; fi

# --- malformed input is exit 2, never a silent proceed --------------------------------
f="$(rg_fixture bad 15 '' '[]')"
rg_run --run-md "$f" --percent 101 --reset-epoch 111
rg_exit_is "percent above 100 is rejected" 2
rg_run --run-md "$f" --percent abc --reset-epoch 111
rg_exit_is "non-numeric percent is rejected" 2
rg_run --run-md "$f" --percent 10 --reset-epoch x1
rg_exit_is "non-numeric reset-epoch is rejected" 2
rg_run --run-md "$f" --percent 10
rg_exit_is "a missing --reset-epoch is rejected" 2
rg_run --run-md "$f" --percent 10 --reset-epoch 111 --samples 0
rg_exit_is "--samples 0 is rejected" 2
rg_run --run-md "$f" --percent 10 --reset-epoch 111 --bogus 1
rg_exit_is "an unknown flag is rejected" 2
rg_run --run-md "$RG/does-not-exist/RUN.md" --percent 10 --reset-epoch 111
rg_exit_is "a missing RUN.md is fail-closed" 2

# A RUN.md without the bookkeeping keys is not a file this may rewrite: it
# fails closed rather than silently no-op-ing the bookkeeping half of the call.
mkdir -p "$RG/nokeys/.auto-pilot"
printf -- '---\nrun_id: nokeys\nreserve: 15\n---\n' >"$RG/nokeys/.auto-pilot/RUN.md"
rg_run --run-md "$RG/nokeys/.auto-pilot/RUN.md" --percent 10 --reset-epoch 111
rg_exit_is "a RUN.md missing usage_delta_baseline/usage_deltas is fail-closed" 2

f="$(rg_fixture corrupt 15 '' '[{percent: eight, reset_epoch: 111}]')"
rg_run --run-md "$f" --percent 10 --reset-epoch 111
rg_exit_is "a corrupt usage_deltas value is fail-closed" 2

f="$(rg_fixture corrupt_baseline 15 ' 42' '[]')"
rg_run --run-md "$f" --percent 10 --reset-epoch 111
rg_exit_is "a corrupt usage_delta_baseline value is fail-closed" 2

f="$(rg_fixture bad_floor abc '' '[]')"
rg_run --run-md "$f" --percent 10 --reset-epoch 111
rg_exit_is "a non-numeric RUN.md reserve floor is fail-closed" 2

# --- the rewrite is atomic and leaves nothing behind -----------------------------------
# The temp file lands beside RUN.md and is renamed over it, so a run directory
# must never accumulate .runmd.* residue.
f="$(rg_fixture residue 15 '' '[]')"
rg_run --run-md "$f" --percent 10 --reset-epoch 111
leftovers="$(find "$(dirname "$f")" -name '.runmd.*' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$leftovers" = 0 ]; then ok "atomic rewrite: no temp-file residue beside RUN.md"; else bad "atomic rewrite: no residue" "$leftovers left"; fi
# Everything outside the two fields survives the rewrite.
if grep -q '^| task | phase | branch | base | base_sha | pr | notes |$' "$f" \
  && grep -q '^status: active$' "$f"; then
  ok "atomic rewrite: the task table and the other front-matter fields are preserved"
else bad "atomic rewrite: preserves the rest of RUN.md" "$(cat "$f")"; fi

# shellcheck source=scripts/lib/spawn-orchestrator-test-epilogue.sh
. "$SO_LIB/spawn-orchestrator-test-epilogue.sh"
