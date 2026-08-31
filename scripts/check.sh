#!/usr/bin/env bash
# Canonical deterministic quality gate for the workflow-skills plugin.
#
# CI (.github/workflows/ci.yml) runs this exact script, and the `justfile`
# `check` target wraps it, so local and CI checks can never drift. Runs every
# check even if an earlier one fails, then exits non-zero if any failed.
#
# Usage: scripts/check.sh [--with-evals] [--fast]
#   --with-evals  also run the behavioral skill-triggering harness
#                 (scripts/eval.sh; needs ANTHROPIC_API_KEY)
#   --fast        edit-loop mode: skip the two long suites. NOT the gate — see
#                 the fast_skips list below for exactly what stops being checked.
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
fast=0
for arg in "$@"; do
  case "$arg" in
    --with-evals) with_evals=1 ;;
    --fast) fast=1 ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

# --fast drops the two suites that dominate wall time; --with-evals adds the
# slowest thing this script can run. Asking for both is incoherent enough that
# guessing an intent would just mislead whoever typed it.
if [[ "$fast" == 1 && "$with_evals" == 1 ]]; then
  echo "--fast and --with-evals are mutually exclusive" >&2
  exit 2
fi

# --fast is the edit-loop gate, not the pre-push one. Three things account for
# essentially all of a full run's wall time, measured on a 4-core Linux box:
# test-research-spike.sh at ~25s (305 python invocations, most of it interpreter
# startup), shellcheck inside lint-shell.sh at ~23s, and the orchestrator suite
# at ~21s. Every other check here finishes inside ~4s.
#
# Those three used to be one dominant cost and two also-rans: the orchestrator
# harness was a single 5816-line file that cost ~60s to run and, because
# ShellCheck's cost is superlinear in the size of a file's top-level scope, ~31s
# of lint-shell's own bill. Splitting it into scripts/test-spawn-orchestrator-*.sh
# cut both. What is left is three co-equal ~21-25s members and no single lever —
# see dev_docs/gate-performance.md before trying to make this faster.
#
# Coverage is SKIPPED here, not sharded or sampled: it is simply gone. So
# `just check` still has to pass before you push, and CI runs this script
# WITHOUT --fast.
# One line per entry — each is printed verbatim under a "skipped:" prefix.
# The lint entry names every command, not just shellcheck: lint-shell.sh --fast
# narrows the FILE LISTS, so bash -n and bats --count stop covering untouched
# files too. A list that says "exactly what is skipped" has to mean it.
fast_skips=(
  "scripts/test-research-spike.sh"
  "scripts/test-spawn-orchestrator.sh (via scripts/test-shell.sh --fast)"
  "every shell/bats lint (bash -n, shfmt, shellcheck, bats --count) over files this branch has not touched (via scripts/lint-shell.sh --fast)"
)
if [[ "$fast" == 1 ]]; then
  echo "→ --fast: skipping the long suites — this is NOT the full gate"
  for skipped in "${fast_skips[@]}"; do
    echo "    skipped: $skipped"
  done
fi

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
# gate costs its slowest check rather than their sum. Output is buffered per
# check and replayed in list order afterwards, so an interleaved run still reads
# exactly like the old serial one. Note the gate no longer costs exactly one
# check: three members now sit within a few seconds of each other and a 4-core
# box cannot run them all flat out, so a full run lands above the slowest.
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
run scripts/typecheck.sh
if [[ "$fast" == 1 ]]; then
  run scripts/lint-shell.sh --fast
else
  run scripts/lint-shell.sh
fi
run scripts/test-task-scan.sh
run scripts/test-validate.sh
run scripts/test-plan-graph.sh
run scripts/test-local-review.sh
[[ "$fast" == 1 ]] || run scripts/test-research-spike.sh
run scripts/test-claim-scan.sh
run scripts/test-gh-label-sync.sh
run scripts/test-gh-issue-state.sh
run scripts/test-linear-archive.sh
run scripts/test-linear-ready.sh
run scripts/test-secret-resolve.sh
run scripts/test-coreview-rule-drift.sh
run scripts/test-verify-fix.sh
# Scheduled LAST because the replay loop is strictly index-ordered: this is the
# longest check, and anything after it would have its output held back behind
# it. At the end, the fast checks drain as they finish and only this one blocks.
if [[ "$fast" == 1 ]]; then
  run scripts/test-shell.sh --fast
else
  run scripts/test-shell.sh
fi

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
if [[ "$fast" == 1 ]]; then
  # Repeated at the end, not just the top: a --fast run still prints a screenful
  # of per-check output, and the caveat is worthless if it scrolled away.
  echo "check.sh: OK (--fast — NOT the full gate)"
  for skipped in "${fast_skips[@]}"; do
    echo "    skipped: $skipped"
  done
  echo "  run \`just check\` before pushing"
  exit 0
fi
echo "check.sh: OK"
