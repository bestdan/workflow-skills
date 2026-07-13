#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

fail=0
run() {
  echo "→ $*"
  "$@" || fail=1
}

run test/vendor/bats-core/bin/bats test/*.bats
run bash scripts/test-spawn-orchestrator.sh

[ "$fail" -eq 0 ] || exit 1
echo "test-shell: OK"
