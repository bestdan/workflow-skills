#!/usr/bin/env bash
# test-jira-pr-sources.sh — hermetic tests for jira-pr-sources.py, which turns
# the plugin's own `PR opened:` / `Working on branch` Jira comments into
# linear-pr-resolve.py input for the jira /sweep-for-complete path.
#
# Wraps scripts/test_jira_pr_sources.py (stdlib unittest; the acli seam is
# stubbed, so no network and no acli binary).
#
# Run directly: bash scripts/test-jira-pr-sources.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_jira_pr_sources.py"
