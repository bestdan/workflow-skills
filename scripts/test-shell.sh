#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

scripts/ensure-bats.sh || exit 2

# The two suites share nothing but the repo they read, so they run
# CONCURRENTLY — this costs the slower one (~60s, the orchestrator harness)
# rather than their sum. Output is buffered and replayed in order so a run
# still reads serially. Keep any new suite added here fixture-isolated.
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

run test/vendor/bats-core/bin/bats test/*.bats
run bash scripts/test-spawn-orchestrator.sh

fail=0
for i in "${!suites[@]}"; do
  wait "${pids[$i]}" || fail=1
  echo "→ ${suites[$i]}"
  cat "$tmp/$i.out"
done

[ "$fail" -eq 0 ] || exit 1
echo "test-shell: OK"
