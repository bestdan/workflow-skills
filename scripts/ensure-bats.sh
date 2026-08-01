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

BATS=test/vendor/bats-core/bin/bats

[ -x "$BATS" ] && exit 0

echo "bats submodules missing — initializing" >&2
git submodule update --init --recursive test/vendor >&2

[ -x "$BATS" ] && exit 0

echo "bats submodules unavailable — run: git submodule update --init --recursive" >&2
exit 2
