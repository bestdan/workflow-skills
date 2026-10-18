#!/usr/bin/env bash
# test-kanban-classify.sh — hermetic tests for kanban-classify.py's shared
# section classification and ordering over linear/gh-issue/jira rows.
#
# Wraps scripts/test_kanban_classify.py (stdlib unittest, no network: the
# script under test never touches the network itself). Run directly:
#   bash scripts/test-kanban-classify.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_kanban_classify.py"
