#!/usr/bin/env bash
# Canonical deterministic quality gate for the workflow-skills plugin.
#
# CI (.github/workflows/ci.yml) runs this exact script, and the `justfile`
# `check` target wraps it, so local and CI checks can never drift. Runs every
# check even if an earlier one fails, then exits non-zero if any failed.
#
# Usage: scripts/check.sh [--with-evals] [--fast] [--changed] [--base <ref>] [--no-cache]
#   --with-evals  also run the behavioral skill-triggering harness
#                 (scripts/eval.sh; needs ANTHROPIC_API_KEY)
#   --fast        edit-loop mode: skip the two long suites. NOT the gate — see
#                 the fast_skips list below for exactly what stops being checked.
#   --changed     edit-loop mode: run the cheap checks, and only the test units
#                 scripts/check-select.py picks from this branch's diff. NOT the
#                 gate either; it falls back to the full gate when the selector
#                 cannot place a change. See "Choosing checks from the diff" in
#                 dev_docs/gate-performance.md.
#   --base <ref>  classify <ref>..HEAD with scripts/ci-docs-only.sh; when it
#                 says the diff is dev_docs-only, skip the shell/bats suites
#                 (see docs_skips below) instead of the --fast list. Anything
#                 other than an exact docs-only verdict runs the full gate.
#   --no-cache    run even when the tree is unchanged since the last green
#                 full run (see "Skipping an unchanged tree" below). Setting
#                 CHECK_NO_CACHE=1 does the same.
#
# research-spike: this repo does not gate on its own dev_docs/research/ tree.
# scripts/test-research-spike.sh (below) exercises the script's fixture
# harness only, under mktemp -d, never the real tree. That is deliberate, not
# an oversight: the dev_docs/research/ directories that exist here are
# research RECORDS (the dated dev_docs layout), not research-spike projects —
# none has a LEDGER.md, and `python3 scripts/research-spike.py --root . validate
# --strict` fails on each with "does not look like a research project
# directory". The gate for a real project lands in the same PR that `init`s
# one here — see skills/research-spike/references/adoption.md, step 4, for the
# full adoption sequence and why `suggest` stays out of the gate even then (a
# lexical scan's false positives have nowhere legal to go in this repo's
# check contract: no baseline file, no allowlist, no skip flag).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

with_evals=0
fast=0
changed=0
base_ref=""
no_cache=""
[[ "${CHECK_NO_CACHE:-}" == 1 ]] && no_cache=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-evals) with_evals=1 ;;
    --fast) fast=1 ;;
    --changed) changed=1 ;;
    --base)
      if [[ $# -lt 2 ]]; then
        echo "--base requires a value" >&2
        exit 2
      fi
      base_ref="$2"
      shift
      ;;
    --base=*) base_ref="${1#--base=}" ;;
    --no-cache) no_cache=1 ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

# --fast drops the two suites that dominate wall time; --with-evals adds the
# slowest thing this script can run. Asking for both is incoherent enough that
# guessing an intent would just mislead whoever typed it.
if [[ "$fast" == 1 && "$with_evals" == 1 ]]; then
  echo "--fast and --with-evals are mutually exclusive" >&2
  exit 2
fi
# --changed is its own selection; layering --fast or --base on it would make
# the skipped set the product of two lists nobody reads together.
if [[ "$changed" == 1 && ("$fast" == 1 || "$with_evals" == 1 || -n "$base_ref") ]]; then
  echo "--changed cannot be combined with --fast, --base or --with-evals" >&2
  exit 2
fi

# Skipping an unchanged tree. After a green full run, scripts/check-cache.py
# stores a fingerprint of the tree and the outside inputs it lists; when this
# run's matches, report OK without running. Tools outside that list, such as
# uv, are not fingerprinted (dev_docs/gate-performance.md says what to do). A full green
# run covers --fast, --changed and --base too, since each only drops checks from it.
# --with-evals always runs: evals call a model, and nothing here fingerprints
# that. CI never uses the cache, so every PR still gets a full run. The record
# lives in this worktree's own git dir (--absolute-git-dir, not the common
# dir), because worktrees share the common dir but not the tree.
cache_key=""
cache_record=""
# --no-cache skips only the lookup: a forced green run still refreshes the record.
if [[ -z "${GITHUB_ACTIONS:-}" && -z "${CI:-}" && "$with_evals" == 0 ]] \
  && cache_dir="$(git rev-parse --absolute-git-dir 2>/dev/null)" \
  && cache_key="$(python3 scripts/check-cache.py key)"; then
  cache_record="$cache_dir/check-green.json"
  if [[ -z "$no_cache" ]]; then
    python3 scripts/check-cache.py lookup "$cache_record" "$cache_key" && exit 0
  fi
fi

# --fast is the edit-loop gate, not the pre-push one. It drops the suites below
# because they are among the gate's slowest; where the wall time goes today is
# in dev_docs/gate-performance.md, and every run prints its own slowest checks
# at the end (scripts/check-timings.py). Read both before trying to make this
# faster.
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
# --base classifies <ref>..HEAD with scripts/ci-docs-only.sh and, only on an
# exact `docs_only=true` line, skips the shell/bats suites entirely — their
# `dev_docs` references are fixture paths under mktemp, so a diff confined to
# the real dev_docs/ tree cannot regress them. The one exception is
# scripts/test-autopilot-framing.sh, which greps the real
# dev_docs/auto-pilot*.md and so always runs; a new harness that reads the
# real dev_docs/ tree must join that exemption in the test loop below. Anything else — a non-docs-only diff, an unknown ref, or the
# classifier exiting non-zero — runs the full gate; this flag only ever
# narrows the gate on positive evidence, never on the absence of a reason not
# to. Combined with --fast: docs-only skips take precedence (they are the
# broader list below) rather than compounding with fast_skips, so the two
# flags together behave exactly like --base alone once the diff is
# docs-only, and exactly like --fast alone once it is not. --base combines
# fine with --with-evals — evals are unaffected by either flag.
docs_only=0
if [[ -n "$base_ref" ]]; then
  base_output="$(scripts/ci-docs-only.sh "$base_ref" HEAD)"
  base_rc=$?
  if [[ "$base_rc" == 0 ]] && grep -qx 'docs_only=true' <<<"$base_output"; then
    docs_only=1
  fi
fi

# Skip list for a dev_docs-only diff — broader than fast_skips because it can
# be: lint-shell.sh and test-shell.sh contribute nothing when no shell/bats
# file changed, so this drops them entirely rather than narrowing their file
# lists the way --fast does.
docs_skips=(
  "scripts/lint-shell.sh"
  "scripts/test-shell.sh"
  "scripts/test-research-spike.sh"
  "every other scripts/test-*.sh test harness except scripts/test-autopilot-framing.sh (reads the real dev_docs/)"
)
if [[ "$docs_only" == 1 ]]; then
  echo "→ --base $base_ref: dev_docs-only diff — skipping suites that cannot reach it"
  for skipped in "${docs_skips[@]}"; do
    echo "    skipped: $skipped"
  done
elif [[ -n "$base_ref" ]]; then
  echo "→ --base $base_ref: not dev_docs-only — running the full gate"
fi

# --changed asks scripts/check-select.py which test units the diff can affect.
# The selector's own answer may be the full gate (infrastructure changed, or
# code no test names), and so is any failure to run it: like --base, this only
# narrows the gate on positive evidence. A run that falls back is a real full
# run, so it times and caches as one.
selected=()
if [[ "$changed" == 1 ]]; then
  if ! select_output="$(python3 scripts/check-select.py)"; then
    echo "→ --changed: could not list the changed files — running the full gate"
    changed=0
  elif [[ "$select_output" == full* ]]; then
    echo "→ --changed: ${select_output#full } — running the full gate"
    changed=0
  else
    while IFS= read -r line; do
      [[ "$line" == "unit "* ]] && selected+=("${line#unit }")
    done <<<"$select_output"
    echo "→ --changed: running ${#selected[@]} test unit(s) the diff can reach — this is NOT the full gate"
    for unit in "${selected[@]:-}"; do
      [[ -n "$unit" ]] && echo "    selected: $unit"
    done
  fi
fi
is_selected() {
  local unit
  for unit in "${selected[@]:-}"; do
    [[ "$unit" == "$1" ]] && return 0
  done
  return 1
}

if [[ "$fast" == 1 && "$docs_only" != 1 ]]; then
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
#
# Before killing them, name every check still running and replay what it has
# written so far. Output is buffered per check, so without this a hung check
# killed by CI's timeout-minutes leaves a log that says nothing at all.
report_unfinished() {
  local i
  for i in "${!pids[@]}"; do
    kill -0 "${pids[$i]}" 2>/dev/null || continue
    echo "→ still running when interrupted: ${checks[$i]}" >&2
    cat "$tmp/$i.out" >&2
  done
}
#
# Each pid is the timing subshell in run(), not the check itself, so killing the
# pid alone would orphan the check; kill_checks takes the check down first.
kill_checks() {
  local pid
  for pid in "${pids[@]:-}"; do
    [[ -n "$pid" ]] || continue
    pkill -TERM -P "$pid" 2>/dev/null
    kill "$pid" 2>/dev/null
  done
}
trap 'report_unfinished; kill_checks; rm -rf "$tmp"; exit 130' INT TERM

# Each check runs in a subshell that writes its wall time to $tmp/<i>.secs, for
# the timing report at the end. Whole seconds: $SECONDS is all Bash 3.2 has,
# and the warnings it feeds are about tens of seconds.
gate_start=$SECONDS
checks=()
pids=()
run() {
  local i="${#checks[@]}"
  checks+=("$*")
  (
    start=$SECONDS
    "$@"
    rc=$?
    echo "$((SECONDS - start))" >"$tmp/$i.secs"
    exit "$rc"
  ) >"$tmp/$i.out" 2>&1 &
  pids+=("$!")
}

run dprint check --incremental=false
run claude plugin validate . --strict
run uv run scripts/validate.py
run scripts/dev-docs-layout.sh
run scripts/typecheck.sh
run scripts/lint-python.sh
if [[ "$docs_only" == 1 ]]; then
  : # skipped — see docs_skips above
elif [[ "$fast" == 1 || "$changed" == 1 ]]; then
  run scripts/lint-shell.sh --fast
else
  run scripts/lint-shell.sh
fi
# Self-registering: every scripts/test-*.sh harness is picked up by this glob,
# so a new one needs no edit here (the exact gap PRE-650 exists to close).
# A file named scripts/test-*.sh is by definition a standalone harness that
# exits non-zero on failure; shared helpers and fixture preludes live in
# scripts/lib/ (see scripts/lib/spawn-orchestrator-test-prelude.sh) and are
# never matched here.
# Three families are excluded on purpose, not forgotten:
#   - scripts/test-*-live.sh — opt-in live-API smoke tests (see
#     scripts/test-shell-live.sh's own aggregating glob); never part of the
#     deterministic gate.
#   - scripts/test-spawn-orchestrator*.sh — test-spawn-orchestrator.sh and its
#     scripts/test-spawn-orchestrator-<part>.sh members are invoked by
#     scripts/test-shell.sh, not directly by this gate.
#   - scripts/test-shell.sh itself — scheduled last below, with its own --fast
#     flag; see that comment for why.
for test_script in scripts/test-*.sh; do
  case "$test_script" in
    scripts/test-*-live.sh | scripts/test-spawn-orchestrator*.sh | scripts/test-shell.sh) continue ;;
    scripts/test-research-spike.sh) [[ "$fast" == 1 ]] && continue ;;
    # Each reads the REAL dev_docs/ tree (auto-pilot*.md; the tracked
    # .handoffs/), so a dev_docs-only diff can regress it; each is one cheap
    # call, so it always runs.
    scripts/test-autopilot-framing.sh | scripts/test-handoffs-untracked.sh)
      run "$test_script"
      continue
      ;;
  esac
  # docs_only drops every remaining scripts/test-*.sh — see docs_skips above.
  [[ "$docs_only" == 1 ]] && continue
  [[ "$changed" == 1 ]] && ! is_selected "$test_script" && continue
  run "$test_script"
done
# Scheduled LAST because the replay loop is strictly index-ordered: this is the
# longest check, and anything after it would have its output held back behind
# it. At the end, the fast checks drain as they finish and only this one blocks.
if [[ "$docs_only" == 1 ]]; then
  : # skipped — see docs_skips above
elif [[ "$changed" == 1 ]]; then
  # The selected units that scripts/test-shell.sh would otherwise run, one job
  # each, as that script runs them; ensure-bats.sh already ran above.
  for unit in "${selected[@]:-}"; do
    case "$unit" in
      test/*.bats) run test/vendor/bats-core/bin/bats "$unit" ;;
      scripts/test-spawn-orchestrator.sh) run "$BASH" "$unit" ;;
    esac
  done
elif [[ "$fast" == 1 ]]; then
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
failed=""
for i in "${!checks[@]}"; do
  wait "${pids[$i]}"
  rc=$?
  echo "→ ${checks[$i]}"
  cat "$tmp/$i.out"
  if [[ "$rc" != 0 ]]; then
    echo "  ✘ failed: ${checks[$i]}" >&2
    fail=1
    failed="${failed:+$failed, }${checks[$i]}"
  fi
done

# Timing report: the slowest checks, and an advisory warning when the gate is
# over its budget or a check jumped against this machine's recent green runs.
# It never fails the gate (`|| true`): wall time is too noisy to gate on. See
# scripts/check-timings.py for both signals, and dev_docs/gate-performance.md
# for where the budgets come from — revisit them there, with a measurement.
if [[ "$docs_only" == 1 ]]; then
  timing_mode=docs budget=60
elif [[ "$changed" == 1 ]]; then
  timing_mode=changed budget=120
elif [[ "$fast" == 1 ]]; then
  timing_mode=fast budget=180
else
  timing_mode=full budget=300
fi
for i in "${!checks[@]}"; do
  [[ -f "$tmp/$i.secs" ]] && printf '%s\t%s\n' "$(cat "$tmp/$i.secs")" "${checks[$i]}"
done >"$tmp/times.tsv"
timing_args=(--times "$tmp/times.tsv" --total "$((SECONDS - gate_start))"
  --mode "$timing_mode" --budget "$budget")
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  # A fresh runner has no history to compare against; annotate instead.
  timing_args+=(--github)
elif history_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
  # The common dir, so every worktree of this clone shares one history: a fresh
  # worktree per task would otherwise never have enough samples to compare.
  timing_args+=(--history "$history_dir/check-timings.tsv")
  [[ "$fail" == 0 && "$with_evals" == 0 ]] && timing_args+=(--record)
fi
python3 scripts/check-timings.py "${timing_args[@]}" || true

if [[ "$fail" != 0 ]]; then
  echo "check.sh: FAIL ($failed)" >&2
  exit 1
fi
if [[ "$docs_only" == 1 ]]; then
  # Repeated at the end, not just the top: a --base run still prints a
  # screenful of per-check output, and the caveat is worthless if it
  # scrolled away. Takes precedence over the --fast message below — see the
  # --base/--fast precedence comment above.
  echo "check.sh: OK (dev_docs-only — slow suites skipped)"
  for skipped in "${docs_skips[@]}"; do
    echo "    skipped: $skipped"
  done
  exit 0
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
if [[ "$changed" == 1 ]]; then
  # Repeated at the end for the same reason as --fast's caveat above.
  echo "check.sh: OK (--changed — ${#selected[@]} selected test unit(s); NOT the full gate)"
  echo "  run \`just check\` before pushing"
  exit 0
fi
# Only a full run stores the fingerprint; store re-checks it, so an edit made
# while the gate ran is not cached as green.
if [[ -n "$cache_record" ]]; then
  python3 scripts/check-cache.py store "$cache_record" "$cache_key" || true
fi
echo "check.sh: OK"
