#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

fail=0
for test_script in scripts/test-*-live.sh; do
  # The glob matches this aggregator itself; running it would recurse forever.
  [ -f "$test_script" ] || continue
  [ "$test_script" = "scripts/test-shell-live.sh" ] && continue
  echo "→ $test_script"
  bash "$test_script" || fail=1
done

[ "$fail" -eq 0 ] || exit 1
echo "test-shell-live: OK"
