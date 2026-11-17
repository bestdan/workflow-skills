#!/usr/bin/env bash
# test-jira-garden.sh — hermetic tests for jira-garden.py, the tracker I/O
# helper of the jira /garden-backlog handler (commands/handlers/jira-garden.md).
#
# Wraps scripts/test_jira_garden.py (stdlib unittest; the twg seam is stubbed,
# so no network and no twg binary).
#
# Run directly: bash scripts/test-jira-garden.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_jira_garden.py"
