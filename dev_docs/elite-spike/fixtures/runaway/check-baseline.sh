#!/usr/bin/env bash
# Probe 5b — "scripts/check.sh passes", made checkable.
#
# DISPOSABLE SPIKE CODE (design §0a rule 4).
#
# Plan tasks 2-6 each carry "scripts/check.sh passes" as a code-enforced
# acceptance criterion. It CANNOT pass in this environment, for reasons that
# pre-date this work and are not ours to fix: a global `core.hooksPath`
# (~/src/dotfiles/git/hooks) points every repo at a hook set whose `pre-commit`
# refuses direct commits to 'main'. It fires inside other fixtures' own scratch
# git repos, their seed commit never lands, and every downstream assertion
# cascades from "fatal: not a git repository".
#
# The maintainer confirmed (2026-07-28) that the criterion therefore means
# **no NEW failures against a baseline recorded before the change**, and asked
# that the baseline be recorded in the fixture's evidence so the claim is
# checkable rather than asserted. That is this script.
#
# Compare failing TEST NAMES, not counts. The count drifts run to run (70, 64,
# 62, 57 observed across hosts and runs) because several of the cascading tests
# are order- and timing-dependent, so a count comparison would report a
# regression on a quiet day and miss one on a noisy day. A set difference does
# neither.
#
# RUN check.sh UNSANDBOXED. Sandboxed runs add ~6 further failures because
# sandbox-exec cannot nest, so profile-compile tests fail spuriously.
#
# usage:
#   ./check-baseline.sh --record  <check.sh transcript>   # write check-baseline.txt
#   ./check-baseline.sh --compare <check.sh transcript>   # assert no NEW failures

set -euo pipefail

FIXDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASELINE="$FIXDIR/check-baseline.txt"

# `^FAIL - ` is anchored deliberately: an unanchored match also catches lines
# that merely CONTAIN the word (a passing test named "...a FAILED gh write is
# never summarized as a repair", and check.sh's own trailing "check.sh: FAIL"),
# which would inflate the set with things that are not failures.
extract() { grep '^FAIL - ' "$1" | sed 's/^FAIL - //' | sort -u; }

mode="${1:-}"
transcript="${2:-}"
[ -n "$mode" ] && [ -n "$transcript" ] || { sed -n '/^# usage:/,/--compare/p' "$0"; exit 2; }
[ -f "$transcript" ] || { echo "no such transcript: $transcript" >&2; exit 2; }

case "$mode" in
  --record)
    {
      echo "# scripts/check.sh failing tests, recorded BEFORE the probe 5b task-2 change."
      echo "# Environmental and pre-existing: a global core.hooksPath pre-commit hook"
      echo "# blocks the seed commit inside other fixtures' scratch repos."
      echo "# Run unsandboxed. Compare by NAME, never by count."
      extract "$transcript"
    } >"$BASELINE"
    echo "recorded $(extract "$transcript" | wc -l | tr -d ' ') failing test names to $(basename "$BASELINE")"
    ;;
  --compare)
    [ -f "$BASELINE" ] || { echo "no baseline recorded at $BASELINE" >&2; exit 2; }
    now="$(mktemp)"; base="$(mktemp)"
    trap 'rm -f "$now" "$base"' EXIT
    extract "$transcript" >"$now"
    grep -v '^#' "$BASELINE" | sed '/^$/d' | sort -u >"$base"
    new="$(comm -13 "$base" "$now")"
    fixed="$(comm -23 "$base" "$now")"
    echo "baseline: $(wc -l <"$base" | tr -d ' ') failing   now: $(wc -l <"$now" | tr -d ' ') failing"
    [ -n "$fixed" ] && { echo "no longer failing (informational):"; echo "$fixed" | sed 's/^/  - /'; }
    if [ -n "$new" ]; then
      echo "NEW FAILURES introduced by this change:" >&2
      echo "$new" | sed 's/^/  + /' >&2
      exit 1
    fi
    echo "OK: no new failures against the recorded baseline"
    ;;
  *) echo "unknown mode: $mode" >&2; exit 2 ;;
esac
