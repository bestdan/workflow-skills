#!/usr/bin/env bash
# Driver for the orchestrator harness — runs every
# scripts/test-spawn-orchestrator-<part>.sh and reports one verdict.
#
# The harness was a single 5816-line file until it became both the gate's
# slowest suite and, because ShellCheck's cost is superlinear in the size of a
# file's top-level scope, most of its lint bill. Splitting it into per-topic
# parts cut both at once; dev_docs/gate-performance.md carries the measurements
# and the reasoning.
#
# Each part is independently runnable (`bash scripts/test-spawn-orchestrator-doctor.sh`)
# and owns its whole lifecycle: the shared prelude builds it a private fixture
# tree, the shared epilogue asserts the isolation guards held. This driver adds
# only what no single part can see — the run's aggregate counts, and the one
# whole-suite assertion that survives sharding (see the tally below).
#
# Run directly: bash scripts/test-spawn-orchestrator.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# Ordered slowest-first so the long poles start immediately: the replay loop
# below is strictly index-ordered, but the RUN order is what sets wall time, and
# a short part scheduled ahead of a long one just leaves a core idle at the end.
parts=(
  exit-contract
  doctor
  status-report
  profile
  supervisor
  restack
  alarm
)

tally="$(mktemp -d "${TMPDIR:-/tmp}/so-tally.XXXXXX")" || exit 2
tmp="$(mktemp -d "${TMPDIR:-/tmp}/so-parts.XXXXXX")" || exit 2
trap 'rm -rf "$tally" "$tmp"' EXIT
# Asynchronous children ignore SIGINT in a non-job-control shell, so Ctrl-C
# would otherwise leave every part running after this driver exits. Same trap as
# check.sh and test-shell.sh.
trap 'kill "${pids[@]:-}" 2>/dev/null; rm -rf "$tally" "$tmp"; exit 130' INT TERM
export SO_TEST_TALLY_DIR="$tally"

pids=()
for i in "${!parts[@]}"; do
  bash "scripts/test-spawn-orchestrator-${parts[$i]}.sh" >"$tmp/$i.out" 2>&1 &
  pids+=("$!")
done

fail=0
for i in "${!parts[@]}"; do
  wait "${pids[$i]}" || fail=1
  cat "$tmp/$i.out"
done

# Aggregate the parts' own counts rather than re-deriving them from the output:
# a part that died before printing its summary contributes nothing here, and the
# missing-counts check below turns that into a failure instead of a quiet zero.
total_pass=0
total_fail=0
total_skip=0
for part in "${parts[@]}"; do
  if [ ! -f "$tally/$part.counts" ]; then
    echo "test-spawn-orchestrator: part '$part' wrote no counts — it died before finishing" >&2
    fail=1
    continue
  fi
  read -r p f s <"$tally/$part.counts"
  total_pass=$((total_pass + p))
  total_fail=$((total_fail + f))
  total_skip=$((total_skip + s))
done

# The whole-suite half of the notifier guard (the structural halves are per-part,
# in the epilogue). The acceptance criterion for the desktop-spam bug is that
# alarms really do route through the guard rather than a real notifier — a guard
# that intercepted NOTHING would pass every structural check while having
# silently stopped covering the alarm path, which is how the leak would return.
# Only some parts raise an incidental alarm, so this is a claim about the run,
# not about any one part, and it is asserted here over the sum.
guard_total=0
for part in "${parts[@]}"; do
  [ -f "$tally/$part.hits" ] || continue
  read -r h <"$tally/$part.hits"
  guard_total=$((guard_total + h))
done
if [ "$guard_total" -gt 0 ]; then
  echo "ok   - notifier guard: the incidental alarms of this suite ($guard_total) were CAUGHT by the guard, not delivered to a desktop"
  total_pass=$((total_pass + 1))
else
  echo "FAIL - notifier guard: the guard caught ZERO notifications across every part — it is no longer covering the alarm path (the leak can return unseen)"
  total_fail=$((total_fail + 1))
  fail=1
fi

echo "test-spawn-orchestrator: $total_pass passed, $total_fail failed, $total_skip skipped (${#parts[@]} parts)"
[ "$fail" = 0 ]
