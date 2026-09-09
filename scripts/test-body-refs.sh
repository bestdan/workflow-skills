#!/usr/bin/env bash
# test-body-refs.sh — hermetic tests for _body_refs.py's dependency-phrase
# parser, shared by gh-issue-graph.py and linear-relations.py.
#
# Wraps scripts/test_body_refs.py (stdlib unittest, no network, no `gh`/API
# calls — the module is pure text parsing) so the phrase table, the
# `unblocks` reversal, self-id exclusion and the code-span decision are all
# exercised by the same `run scripts/test-*.sh` gate as the other hermetic
# tests.
#
# Run directly: bash scripts/test-body-refs.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_body_refs.py"
