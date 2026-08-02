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
#
# research-spike: this repo does not gate on its own dev_docs/research/ tree.
# scripts/test-research-spike.sh (below) exercises the script's fixture
# harness only, under mktemp -d, never the real tree. That is deliberate, not
# an oversight: this repo has no dev_docs/research/ tree yet, and a
# `validate` gate over a tree that does not exist measures nothing. The
# moment a real project is initialized here, the same PR that runs `init`
# also adds `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py"
# --root "$(git rev-parse --show-toplevel)" validate --strict` to this list —
# see skills/research-spike/references/adoption.md, step 4, for the full
# adoption sequence and why `suggest` stays out of the gate even then (a
# lexical scan's false positives have nowhere legal to go in this repo's
# check contract: no baseline file, no allowlist, no skip flag).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

with_evals=0
for arg in "$@"; do
  case "$arg" in
    --with-evals) with_evals=1 ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
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

run dprint check --incremental=false
run claude plugin validate . --strict
run uv run scripts/validate.py
run scripts/lint-shell.sh
run scripts/test-shell.sh
run scripts/test-task-scan.sh
run scripts/test-validate.sh
run scripts/test-plan-graph.sh
run scripts/test-research-spike.sh
run scripts/test-claim-scan.sh
run scripts/test-linear-archive.sh
run scripts/test-linear-ready.sh
run scripts/test-secret-resolve.sh

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
