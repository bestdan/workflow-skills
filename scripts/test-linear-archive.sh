#!/usr/bin/env bash
# test-linear-archive.sh — hermetic tests for the linear-archive.py sweep query.
#
# Wraps scripts/test_linear_archive.py (stdlib unittest, no network: gql is
# stubbed) so the whole-team and single-project GraphQL paths are exercised by
# the same `run scripts/test-*.sh` gate as the shell tests. Covers PRE-567: a
# whole-team sweep must not declare an unused $project variable.
#
# Run directly: bash scripts/test-linear-archive.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_archive.py"
