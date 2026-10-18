#!/usr/bin/env bash
# test-eval-triage.sh — hermetic tests for scripts/eval-triage.py.
#
# Wraps scripts/test_eval_triage.py (stdlib unittest, no network, no
# subprocess, no `claude` CLI) so the eval harness's failure triage is
# exercised by the same `run scripts/test-*.sh` gate as the other hermetic
# tests — the behavioral evals themselves cost tokens and can't be.
#
# Run directly: bash scripts/test-eval-triage.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_eval_triage.py"
