#!/usr/bin/env bash
# Make the vendored bats submodules available, initializing them if needed.
#
# `git worktree add` does not populate submodules, so every fresh worktree failed
# lint-shell.sh and test-shell.sh until someone ran the init by hand. Both of
# those delegate here so the recovery lives in one place.
#
# Exits 0 once bats is usable (already present, or freshly initialized) and 2
# with the manual command when it cannot be — the clone needs network, so
# sandboxed and offline runs still land on an actionable message rather than a
# bare failure.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# All four submodules matter, not just bats-core: test_helper.bash loads
# bats-support, bats-assert, and bats-file too. Checking only the runner would
# call a partial init a success — and since the same check gates the next run,
# the half-initialized tree would never be repaired, just fail at load time.
bats_ready() {
  [ -x test/vendor/bats-core/bin/bats ] || return 1
  local helper
  for helper in bats-support bats-assert bats-file; do
    [ -f "test/vendor/$helper/load.bash" ] || return 1
  done
}

bats_ready && exit 0

echo "bats submodules missing — initializing" >&2
git submodule update --init --recursive test/vendor >&2

bats_ready && exit 0

echo "bats submodules unavailable — run: git submodule update --init --recursive" >&2
exit 2
