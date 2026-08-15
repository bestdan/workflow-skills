#!/usr/bin/env bash
# Runs every scripts/test-spawn-orchestrator/*.sh suite and reports one total.
#
# The suites are the test harness for scripts/spawn-orchestrator.sh. They were a
# single 5816-line file until it became the quality gate's critical path twice
# over — the slowest check AND, because ShellCheck's cost is superlinear in file
# size, most of the lint cost. Splitting it into files (rather than adding a
# shard flag) is what cut both. See dev_docs/gate-performance.md.
#
# Each suite is self-contained and runnable on its own; this driver exists to
# run them CONCURRENTLY, to add up their counters, and to make the one assertion
# no single suite can make (see the pooled guard log below).
#
# Run one suite: bash scripts/test-spawn-orchestrator/doctor.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

tmp="$(mktemp -d "${TMPDIR:-/tmp}/so-suites.XXXXXX")" || exit 2
trap 'rm -rf "$tmp"' EXIT
# Asynchronous children ignore SIGINT in a non-job-control shell, so Ctrl-C
# would otherwise kill this driver and leave seven suites running. Same trap as
# scripts/check.sh and scripts/test-shell.sh.
trap 'kill "${pids[@]:-}" 2>/dev/null; rm -rf "$tmp"; exit 130' INT TERM

# The pooled notifier-guard log. "The guard caught N incidental alarms" is a
# whole-RUN property: the alarms are raised in three different suites, so a
# per-suite version of it would fail every suite that legitimately raises none.
# Exporting one log here lets each suite append to it (see _prelude.sh) and lets
# this driver assert over all of them at once, exactly as the single file did.
# It lives in $tmp, not in a suite's BASE, because each suite deletes its own.
NOTIFY_GUARD_LOG="$tmp/guard-notify.calls"
: >"$NOTIFY_GUARD_LOG"
export NOTIFY_GUARD_LOG

# Globbed, not listed: adding a suite file should not also mean registering it.
# `[!_]` skips _prelude.sh, which is sourced rather than run.
suites=()
pids=()
for suite in scripts/test-spawn-orchestrator/[!_]*.sh; do
  [ -f "$suite" ] || continue
  i="${#suites[@]}"
  suites+=("$suite")
  bash "$suite" >"$tmp/$i.out" 2>&1 &
  pids+=("$!")
done
if [ "${#suites[@]}" -eq 0 ]; then
  echo "test-spawn-orchestrator: no suites found under scripts/test-spawn-orchestrator/" >&2
  exit 2
fi

pass=0
fail=0
skip=0
rcfail=0
for i in "${!suites[@]}"; do
  wait "${pids[$i]}" || rcfail=1
  cat "$tmp/$i.out"
  # Each suite's finish() prints "test-spawn-orchestrator/<name>: N passed, M
  # failed, K skipped". A suite that died before printing one contributes
  # nothing here, which is why the exit status is tracked separately above.
  counts="$(sed -n 's/^test-spawn-orchestrator\/[^:]*: \([0-9]*\) passed, \([0-9]*\) failed, \([0-9]*\) skipped$/\1 \2 \3/p' "$tmp/$i.out" | tail -1)"
  if [ -z "$counts" ]; then
    echo "test-spawn-orchestrator: ${suites[$i]} produced no summary line (it died early)" >&2
    rcfail=1
    continue
  fi
  set -- $counts
  pass=$((pass + $1))
  fail=$((fail + $2))
  skip=$((skip + $3))
done

# The whole-run notifier assertion, over every suite's alarms at once. A count
# of zero means the guard stopped covering the alarm path — the suites would
# still pass while the leak returned unseen, which is exactly how it survived
# the first time.
guard_hits="$(grep -c '^osascript: ' "$NOTIFY_GUARD_LOG" 2>/dev/null | tr -d ' ')"
case "$guard_hits" in '' | *[!0-9]*) guard_hits=0 ;; esac
if [ "$guard_hits" -gt 0 ]; then
  pass=$((pass + 1))
  echo "ok   - notifier guard: the incidental alarms of this run ($guard_hits) were CAUGHT by the guard, not delivered to a desktop"
else
  fail=$((fail + 1))
  echo "FAIL - notifier guard: the guard caught ZERO notifications — it is no longer covering the alarm path (the leak can return unseen)"
fi

echo "test-spawn-orchestrator: $pass passed, $fail failed, $skip skipped (${#suites[@]} suites)"
[ "$fail" = 0 ] && [ "$rcfail" = 0 ]
