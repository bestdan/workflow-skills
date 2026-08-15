#!/usr/bin/env bash
#
# Usage: scripts/test-shell.sh [--fast]
#   --fast  skip the orchestrator harness (the ~21s suite). NOT the gate — see
#           the fast_skips list in scripts/check.sh for what that costs you.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

fast=0
for arg in "$@"; do
  case "$arg" in
    --fast) fast=1 ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

scripts/ensure-bats.sh || exit 2

# The suites share nothing but the repo they read, so they run CONCURRENTLY —
# this costs the slowest one rather than their sum. Output is buffered and
# replayed in order so a run still reads serially. Keep any new suite added
# here fixture-isolated.
tmp="$(mktemp -d "${TMPDIR:-/tmp}/test-shell.XXXXXX")" || exit 2
trap 'rm -rf "$tmp"' EXIT
# Asynchronous children ignore SIGINT in a non-job-control shell, so Ctrl-C
# would otherwise leave the long orchestrator suite running after this wrapper
# exits. See the matching trap in check.sh.
trap 'kill "${pids[@]:-}" 2>/dev/null; rm -rf "$tmp"; exit 130' INT TERM

suites=()
pids=()
run() {
  local i="${#suites[@]}"
  suites+=("$*")
  "$@" >"$tmp/$i.out" 2>&1 &
  pids+=("$!")
}

# The bats files fan out ONLY when the orchestrator suite is skipped, because
# only then are they on the critical path.
#
# Serially the ten files cost ~15s; one job per file costs the slowest (~5s). In
# a full run they sit alongside the orchestrator suite, which is ~21s since it
# was split into scripts/test-spawn-orchestrator-*.sh — so serial bats still
# finishes inside it and the fan-out cannot shorten this wrapper. Re-measured
# after the split, interleaved A/B on a 4-core Linux box: full `check.sh`
# averaged 38.8s with bats serial vs 40.5s fanned. Still no better, and for the
# same reason as before — the extra jobs contend with the suite that is actually
# the critical path, which now runs seven of its own parts concurrently.
#
# But the margin is thin now, not structural. It used to be 17s of bats against
# a 60s suite; it is 15s against 21s. Anything that takes another few seconds
# off the orchestrator parts flips this, so re-measure rather than assume — and
# see the wall-clock hazard in dev_docs/gate-performance.md before adding jobs,
# because oversubscription is what pushed test/await-pr-review.bats past its
# budget (see 1ddb13e).
#
# Under --fast the orchestrator is gone, bats becomes the critical path, and the
# fan-out is the whole difference: `check.sh --fast` measures ~6.7s fanned vs
# ~18s serial.
if [ "$fast" -eq 1 ]; then
  for suite in test/*.bats; do
    [ -f "$suite" ] || continue
    run test/vendor/bats-core/bin/bats "$suite"
  done
else
  run test/vendor/bats-core/bin/bats test/*.bats
  # Scheduled LAST because the replay loop below is strictly index-ordered: this
  # is the longer of the two, and anything after it would have its output held
  # back behind it. Same reasoning as the tail of check.sh's run list.
  run bash scripts/test-spawn-orchestrator.sh
fi

fail=0
for i in "${!suites[@]}"; do
  wait "${pids[$i]}" || fail=1
  echo "→ ${suites[$i]}"
  cat "$tmp/$i.out"
done

[ "$fail" -eq 0 ] || exit 1
if [ "$fast" -eq 1 ]; then
  echo "test-shell: OK (--fast: SKIPPED scripts/test-spawn-orchestrator.sh)"
else
  echo "test-shell: OK"
fi
