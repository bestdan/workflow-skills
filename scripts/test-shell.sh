#!/usr/bin/env bash
#
# Usage: scripts/test-shell.sh [--fast]
#   --fast  skip the orchestrator harness. NOT the gate — see
#           the fast_skips list in scripts/check.sh for what that costs you.
set -uo pipefail

# The first link in the Bash 3.2 floor: CI invokes this script as
# /bin/bash, and it passes that interpreter on as "$BASH". Assert arrival here
# too, so a broken pin fails at the entry point rather than only inside the
# orchestrator parts. Opt-in — see the matching block in
# scripts/lib/spawn-orchestrator-test-prelude.sh.
if [ "${SO_TEST_REQUIRE_BASH3:-0}" = 1 ] && [ "${BASH_VERSINFO[0]}" -ne 3 ]; then
  echo "FAIL - Bash floor: test-shell.sh is running under ${BASH_VERSION}, not the 3.2 floor." >&2
  exit 1
fi

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

# One job per bats file, in every mode. Run serially, the bats files are the
# gate's critical path by minutes, and most of that time is waiting rather than
# CPU — dev_docs/gate-performance.md has the measurement. More jobs raise the
# wall-clock hazard written up there (oversubscription is what pushed
# test/await-pr-review.bats past its budget, see 1ddb13e), so a test that
# measures itself against elapsed time must be made time-independent, not
# given a serial run.
if [ "${TEST_SHELL_BATS_SERIAL:-0}" = 1 ]; then
  # MEASUREMENT ONLY (#1452): the pre-#1488 full-run shape, one serial process.
  run test/vendor/bats-core/bin/bats --timing test/*.bats
else
  for suite in test/*.bats; do
    [ -f "$suite" ] || continue
    run test/vendor/bats-core/bin/bats --timing "$suite"
  done
fi
if [ "$fast" -eq 0 ]; then
  # Scheduled LAST because the replay loop below is strictly index-ordered and
  # this is one of the longest jobs: anything after it would have its output
  # held back behind it. Same reasoning as the tail of check.sh's run list.
  # "$BASH", not `bash`: a bare `bash` re-resolves through PATH, which would
  # drop the interpreter CI pinned when it invoked this wrapper as
  # /bin/bash (the Bash 3.2 floor — see dev_docs/testing.md).
  run "$BASH" scripts/test-spawn-orchestrator.sh
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
