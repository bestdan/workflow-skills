#!/usr/bin/env bash
# verify-fix.sh — the standard post-fix verification sequence, as one
# invocation instead of hand-composed shell.
#
# Ad-hoc verification (full suite, then a stability repeat, then a process
# watch, then a confinement smoke, then a final gate check, with `ps` dumped
# by hand on failure) is not reproducible across sessions/agents — two people
# "verifying the same way" can easily run different command sequences, and
# the diagnostics step is the one that gets forgotten under time pressure.
# This script is that sequence, hardcoded, so it always runs the same way and
# never skips the diagnostics.
#
# Usage:
#   scripts/verify-fix.sh [--runs N] [--skip-confinement] <description>
#
#   --runs N            Number of gate rounds in the stability check
#                        (see STAGE 2 below). Default 3. Must be >= 1.
#   --skip-confinement  Skip STAGE 3 (smoke-confinement) even when
#                        scripts/smoke-confinement.sh is present.
#   <description>       Free-text description of the fix being verified —
#                        required, printed in the summary and on failure so
#                        the run is identifiable in scrollback/CI logs.
#
# Exit status: 0 only if every stage below passed (or was legitimately
# skipped — confinement is macOS-only). Non-zero on the first stage failure,
# after dumping diagnostics for it.
#
# STAGES (run in this order; each is its own thing, not a synonym for another):
#
#   1. Full test suite run — round 1 of the resolved gate command
#      (`resolve_check_command` below), the same one CI/`just check` runs.
#      This is the baseline: does the fix pass at all.
#   2. N-run stability check — rounds 2..N of the same command (default
#      N=3 total), to catch flakiness a single green run can't. Each round
#      is separately timed and separately diagnosed on failure.
#   3. Process/orphan watch — wraps EVERY round above (1 and 2), not a
#      separate round of its own. See watch_processes() below: it snapshots
#      which live processes are parented to init (pid 1) before and after
#      each round and flags any that newly appeared, i.e. survived their
#      parent and got reparented — the textbook definition of an orphan.
#      This is a coarse, whole-system heuristic (like smoke-confinement.sh's
#      own documented scope limits, this only claims what it actually
#      checks): it cannot attribute an orphan to a specific line of the run,
#      only that one appeared during it.
#   4. Smoke-confinement run — scripts/smoke-confinement.sh, if present.
#      macOS-only (sandbox-exec); skipped with a note everywhere else, and
#      skippable outright with --skip-confinement.
#   5. Final gate check — one more round of the resolved gate command,
#      run AFTER stage 4. Not a repeat of stage 1/2 for its own sake: the
#      confinement smoke exercises launchd bootstrap/teardown and other
#      host-level side effects (cleaned up on exit, but real mutations
#      nonetheless), so this is the check that nothing it did left the
#      checkout in a broken state. This round's result is what the final
#      summary line reports.
#
# On any stage failure: dump diagnostics (the failing round's captured
# output, plus a live `ps` snapshot) and exit non-zero immediately — later
# stages do not run. A round that fails is not `rm`'d until diagnostics have
# printed.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

usage() {
  cat >&2 <<'EOF'
usage: scripts/verify-fix.sh [--runs N] [--skip-confinement] <description>
EOF
}

# resolve_check_command — the project's named check command, `dli check` ->
# `just check` -> `scripts/check.*`, the same precedence
# skills/auto-pilot/references/launch-preflight.md documents for the
# orchestrator's own verify_command resolution. Kept in lockstep with that
# file's step 5 by construction (same three-rung ladder, same order); a repo
# with none of the three has nothing this script can run.
resolve_check_command() {
  if command -v dli >/dev/null 2>&1 \
    && { [ -f "$ROOT/dli.toml" ] || [ -f "$ROOT/.dli.yml" ] || [ -f "$ROOT/.dli.yaml" ]; }; then
    echo "dli check"
    return 0
  fi
  if command -v just >/dev/null 2>&1; then
    local jf=""
    for cand in justfile Justfile .justfile; do
      [ -f "$ROOT/$cand" ] || continue
      jf="$ROOT/$cand"
      break
    done
    if [ -n "$jf" ] && grep -qE '^check(\s|:)' "$jf"; then
      echo "just check"
      return 0
    fi
  fi
  if [ -x "$ROOT/scripts/check.sh" ]; then
    echo "scripts/check.sh"
    return 0
  fi
  if [ -x "$ROOT/scripts/check.py" ]; then
    echo "scripts/check.py"
    return 0
  fi
  return 1
}

# watch_processes <label> <logfile> <command...> — run a command with its
# output captured to <logfile>, flagging any process that is alive and
# parented to init (pid 1) afterward but was not before. See STAGE 3 in the
# header comment for what this does and does not prove.
#
# The command's own stdout/stderr are redirected to <logfile> IN HERE, not
# by the caller — so this function's own "possible orphan" warning stays on
# the script's real stdout instead of being captured into a per-round log
# that gets deleted on success (and the warning silently lost with it).
watch_processes() {
  local label="$1" logfile="$2"
  shift 2
  local before after new_orphans
  before="$(ps -eo pid=,ppid= 2>/dev/null | awk '$2==1{print $1}' | sort -n)"
  "$@" >"$logfile" 2>&1
  local rc=$?
  after="$(ps -eo pid=,ppid= 2>/dev/null | awk '$2==1{print $1}' | sort -n)"
  new_orphans="$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") 2>/dev/null)"
  if [ -n "$new_orphans" ]; then
    echo "  ⚠ process watch: new process(es) reparented to init during $label — possible orphan(s):"
    while IFS= read -r pid; do
      [ -n "$pid" ] || continue
      ps -p "$pid" -o pid,ppid,etime,command 2>/dev/null
    done <<<"$new_orphans"
  fi
  return "$rc"
}

# dump_diagnostics <label> <logfile> — the automatic on-failure dump: the
# failing round's captured output plus a live `ps` snapshot. Never skipped —
# that is the whole reason this exists as code instead of re-typed by hand.
dump_diagnostics() {
  local label="$1" logfile="$2"
  echo
  echo "---- diagnostics: $label ----"
  echo "-- captured output (last 100 lines) --"
  if [ -s "$logfile" ]; then
    tail -n 100 "$logfile"
  else
    echo "(no output captured)"
  fi
  echo "-- process snapshot (ps -ef) --"
  ps -ef 2>/dev/null | head -n 200
  echo "---- end diagnostics: $label ----"
}

runs=3
skip_confinement=0
description=""
while [ $# -gt 0 ]; do
  case "$1" in
    --runs)
      [ $# -ge 2 ] || {
        usage
        exit 2
      }
      runs="$2"
      shift 2
      ;;
    --skip-confinement)
      skip_confinement=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "verify-fix: unknown flag: $1" >&2
      usage
      exit 2
      ;;
    *)
      break
      ;;
  esac
done
description="$*"

[ -n "$description" ] || {
  echo "verify-fix: <description> is required" >&2
  usage
  exit 2
}
case "$runs" in
  '' | *[!0-9]*)
    echo "verify-fix: --runs must be a positive integer, got: $runs" >&2
    exit 2
    ;;
esac
[ "$runs" -ge 1 ] || {
  echo "verify-fix: --runs must be >= 1, got: $runs" >&2
  exit 2
}

CHECK_CMD="$(resolve_check_command)" || {
  echo "verify-fix: no check command found (looked for dli check, just check, scripts/check.sh, scripts/check.py)" >&2
  exit 2
}

echo "verify-fix: $description"
echo "verify-fix: resolved gate command: $CHECK_CMD"
echo "verify-fix: stability rounds: $runs"

run_round() {
  local label="$1" logfile
  logfile="$(mktemp "${TMPDIR:-/tmp}/verify-fix.XXXXXX.log")" || {
    echo "verify-fix: mktemp failed" >&2
    exit 2
  }
  echo "== $label: $CHECK_CMD =="
  if watch_processes "$label" "$logfile" bash -c "$CHECK_CMD"; then
    echo "  ✅ $label passed"
    rm -f "$logfile"
    return 0
  fi
  echo "  ❌ $label FAILED"
  dump_diagnostics "$label" "$logfile"
  rm -f "$logfile"
  return 1
}

# STAGE 1 + 2: full run, then the stability repeats.
n=1
while [ "$n" -le "$runs" ]; do
  if [ "$n" -eq 1 ]; then
    run_round "full test suite run (round 1/$runs)" || exit 1
  else
    run_round "stability round $n/$runs" || exit 1
  fi
  n=$((n + 1))
done

# STAGE 4 (numbered per the header comment; there is no separate stage for
# the process watch — it already wrapped every round above).
if [ "$skip_confinement" -eq 1 ]; then
  echo "== smoke-confinement: skipped (--skip-confinement) =="
elif [ ! -x "$ROOT/scripts/smoke-confinement.sh" ]; then
  echo "== smoke-confinement: skipped (scripts/smoke-confinement.sh not present) =="
else
  logfile="$(mktemp "${TMPDIR:-/tmp}/verify-fix.XXXXXX.log")" || {
    echo "verify-fix: mktemp failed" >&2
    exit 2
  }
  echo "== smoke-confinement =="
  if bash "$ROOT/scripts/smoke-confinement.sh" >"$logfile" 2>&1; then
    echo "  ✅ smoke-confinement passed"
    rm -f "$logfile"
  elif grep -q "sandbox-exec required" "$logfile" 2>/dev/null; then
    echo "  ⏭ smoke-confinement skipped (macOS/sandbox-exec only, not available on this host)"
    rm -f "$logfile"
  else
    echo "  ❌ smoke-confinement FAILED"
    dump_diagnostics "smoke-confinement" "$logfile"
    rm -f "$logfile"
    exit 1
  fi
fi

# STAGE 5: the final gate check, run after the confinement smoke's host-level
# side effects — see the header comment for why this is not a redundant
# repeat of stage 1/2.
run_round "final gate check" || exit 1

echo
echo "✅ verify-fix passed: $description"
