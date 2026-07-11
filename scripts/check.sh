#!/usr/bin/env bash
# Canonical deterministic quality gate for the workflow-skills plugin.
#
# CI (.github/workflows/ci.yml) runs this exact script, and the `justfile`
# `check` target wraps it, so local and CI checks can never drift. Runs every
# check even if an earlier one fails, then exits non-zero if any failed.
#
# Usage: scripts/check.sh [--with-evals]
#   --with-evals  also run the behavioral skill-triggering harness
#                 (scripts/eval.sh; needs ANTHROPIC_API_KEY)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

with_evals=0
for arg in "$@"; do
  case "$arg" in
    --with-evals) with_evals=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

fail=0
run() {
  echo "→ $*"
  if ! "$@"; then
    echo "  ✘ failed: $*" >&2
    fail=1
  fi
}

run dprint check
run claude plugin validate . --strict
run uv run scripts/validate.py
run bash scripts/test-await-pr-review.sh
run bash scripts/test-preflight-freshness.sh
run bash scripts/test-claude-usage.sh
run bash scripts/test-spawn-orchestrator.sh
run bash scripts/test-linear-ready-live.sh
run bash scripts/test-linear-relations-live.sh
run bash scripts/test-linear-scan-live.sh

if [[ "$with_evals" == 1 ]]; then
  if [[ -x scripts/eval.sh ]]; then
    run scripts/eval.sh
  else
    echo "→ evals: scripts/eval.sh not present yet (added in step 4) — skipping"
  fi
fi

if [[ "$fail" != 0 ]]; then
  echo "check.sh: FAIL" >&2
  exit 1
fi
echo "check.sh: OK"
