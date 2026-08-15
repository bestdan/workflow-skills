#!/usr/bin/env bash
#
# Usage: scripts/test-shell.sh [--fast]
#   --fast  skip the orchestrator harness (the ~23s suite). NOT the gate — see
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
# Serially the ten files cost ~17s; one job per file costs the slowest (~4s,
# utilities.bats). But a full run also carries the orchestrator harness (~23s,
# itself seven concurrent suites) in parallel, so bats is hidden behind it
# either way and the fan-out cannot move the total — 17s and 4s are both under
# it. What ten extra concurrent jobs CAN do is steal cores from the suites that
# are the critical path: when this was tried against the old single-file
# orchestrator suite, full `check.sh` measured 66.8s serial-bats vs 68.9s
# fanned, and the oversubscription pushed test/await-pr-review.bats past its
# wall-clock budget (see 1ddb13e). Re-measure with an interleaved A/B before
# widening this — see dev_docs/gate-performance.md.
#
# Under --fast the orchestrator is gone, bats becomes the critical path, and the
# fan-out is the whole difference: `check.sh --fast` measures ~7.2s fanned vs
# ~18.2s serial.
if [ "$fast" -eq 1 ]; then
  for suite in test/*.bats; do
    [ -f "$suite" ] || continue
    run test/vendor/bats-core/bin/bats "$suite"
  done
else
  run test/vendor/bats-core/bin/bats test/*.bats
  # Scheduled LAST because the replay loop below is strictly index-ordered: this
  # is the slower job, and anything after it would have its output held back
  # behind it. Same reasoning as the tail of check.sh's run list.
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
