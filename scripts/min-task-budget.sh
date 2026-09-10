#!/usr/bin/env bash
# min-task-budget.sh — compute `min_task_budget`, the pre-dispatch deadline
# guard's floor (auto-pilot's run loop; see run-budget.md "Minimum task
# budget"). Launch step 3 (launch-preflight.md) runs this against the
# resolved co-review `local_reviewers` set and writes the output into RUN.md
# front matter.
#
# THIS SCRIPT IS THE ONE HOME FOR THE FORMULA. Don't restate it elsewhere.
#
#   min_task_budget = OVERHEAD + ROUNDS * max(term(r) for r in reviewers)
#   OVERHEAD = 18   # minutes: coder implement + independent verify + PR open
#   ROUNDS   = 3    # initial co-review + up to 2 iterate rounds, each waiting
#                    # on the slowest reviewer
#   term(codex)   = 2    # measured: stateless, sandboxed, ~1-2 min
#   term(agy)     = 15   # cloud reviewer; --non-interactive waits the 15-min
#                          # CLI bound
#   term(devin)   = 15   # same
#   term(copilot) = 15   # same
#   term(crush)   = 15   # local CLI, unmeasured; take the same 15-min bound
#   term(gemini)  = ignored (retired; co-review silently skips it)
#   no reviewers  = Claude-only set (main agent + reconciler are in-session,
#                    already inside OVERHEAD) -> max term 0
#
# Usage:
#   scripts/min-task-budget.sh [<reviewer> ...]
#
#   <reviewer>  a resolved `local_reviewers` name (codex, agy, devin,
#               copilot, crush, gemini), lowercase, one per argument. Zero
#               args means the Claude-only set.
#
# Prints exactly one line "<N>m" on stdout — a value
# `_parse_duration_or_off` (spawn-orchestrator.sh) accepts.
#
# Exit status:
#   0  ok, "<N>m" printed
#   2  usage error, or an unrecognized reviewer name (fails CLOSED: a guessed
#      floor for an unknown reviewer would let the deadline guard start a
#      task the --until kill would sever)
set -uo pipefail

OVERHEAD=18
ROUNDS=3

usage() {
  sed -n '2,37p' "$0"
}

case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
esac

max_term=0
for reviewer in "$@"; do
  case "$reviewer" in
    gemini) continue ;;
    codex) term=2 ;;
    agy | devin | copilot | crush) term=15 ;;
    *)
      echo "min-task-budget: unknown reviewer '$reviewer'" >&2
      exit 2
      ;;
  esac
  if [ "$term" -gt "$max_term" ]; then
    max_term="$term"
  fi
done

echo "$((OVERHEAD + ROUNDS * max_term))m"
