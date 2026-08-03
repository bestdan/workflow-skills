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
# also adds `run python3 "$ROOT/scripts/research-spike.py" --root "$ROOT"
# validate --strict` to the `run` list below — see
# skills/research-spike/references/adoption.md, step 4, for the full
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

# Initialize the vendored bats submodules ONCE, before the fan-out. Both
# lint-shell.sh and test-shell.sh call ensure-bats.sh, and on a tree where
# test/vendor is unpopulated its recovery path runs `git submodule update`
# against the shared index — two of those concurrently collide on the lock and
# fail the gate in exactly the fresh-worktree case the helper exists to rescue.
# CI checks out with submodules already recursive, so the exposure is the local
# `git worktree add` flow (which does not populate submodules). Hoisting it
# here leaves both child calls on the bats_ready() fast path, and is what makes
# the "writes nothing into the repo" claim below true.
scripts/ensure-bats.sh || exit 2

# Every check below is independent — each builds its own fixtures under its own
# mktemp dir and none writes into the repo — so they run CONCURRENTLY and the
# gate costs one slowest check (~60s, test-shell.sh) instead of their sum
# (~110s). Output is buffered per check and replayed in list order afterwards,
# so an interleaved run still reads exactly like the old serial one.
tmp="$(mktemp -d "${TMPDIR:-/tmp}/check.XXXXXX")" || exit 2
trap 'rm -rf "$tmp"' EXIT
# Bash sets SIGINT to ignore for asynchronously-started commands in a
# non-job-control shell, so Ctrl-C kills this runner while every backgrounded
# check runs on to completion — a regression from the serial version, where the
# foreground child took the signal and died with the script. Kill the direct
# children explicitly. `:-` because set -u is on and this can fire before any
# pid is recorded.
trap 'kill "${pids[@]:-}" 2>/dev/null; rm -rf "$tmp"; exit 130' INT TERM

checks=()
pids=()
run() {
  local i="${#checks[@]}"
  checks+=("$*")
  "$@" >"$tmp/$i.out" 2>&1 &
  pids+=("$!")
}

run dprint check --incremental=false
run claude plugin validate . --strict
run uv run scripts/validate.py
run scripts/lint-shell.sh
run scripts/test-task-scan.sh
run scripts/test-validate.sh
run scripts/test-plan-graph.sh
run scripts/test-research-spike.sh
run scripts/test-claim-scan.sh
run scripts/test-linear-archive.sh
run scripts/test-linear-ready.sh
run scripts/test-secret-resolve.sh
# Scheduled LAST because the replay loop is strictly index-ordered: this is the
# ~60s check, and anything after it would have its output held back behind it.
# At the end, the fast checks drain as they finish and only this one blocks.
run scripts/test-shell.sh

if [[ "$with_evals" == 1 ]]; then
  if [[ -x scripts/eval.sh ]]; then
    run scripts/eval.sh
  else
    echo "→ evals: scripts/eval.sh not present yet (added in step 4) — skipping"
  fi
fi

fail=0
for i in "${!checks[@]}"; do
  wait "${pids[$i]}"
  rc=$?
  echo "→ ${checks[$i]}"
  cat "$tmp/$i.out"
  if [[ "$rc" != 0 ]]; then
    echo "  ✘ failed: ${checks[$i]}" >&2
    fail=1
  fi
done

if [[ "$fail" != 0 ]]; then
  echo "check.sh: FAIL" >&2
  exit 1
fi
echo "check.sh: OK"
