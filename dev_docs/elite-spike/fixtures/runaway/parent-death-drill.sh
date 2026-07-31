#!/usr/bin/env bash
# Probe 5b — the parent-death predicate, as a runnable drill.
#
# DISPOSABLE SPIKE CODE (design §0a rule 4).
#
# The kill sheet states this as a predicate to run before any leg:
#
#     kill -9 the driver mid-run; ALL surrogates and workers exit within N
#     seconds. A failure means the fixture can outlive its own deadline — the
#     runaway probe becoming the runaway.
#
# It ships as a script rather than as an instruction because it has already
# failed once, in a way nothing else caught. The first implementation used only
# the FIFO-EOF channel, and a child that inherits the driver's write-end fd
# keeps the FIFO open, so the EOF never arrives: after `kill -9` of the driver
# the surrogate was still alive 30 seconds later, reparented to init, blocked in
# read(). That is precisely the hazard class of the incident record. The fix
# (common.py's second detector — polling the driver's recorded incarnation) is
# only trustworthy for as long as this drill keeps passing, so it is a check,
# not a claim.
#
# SIGKILL is the point: the driver's EXIT trap does NOT run, so nothing here is
# rescued by the driver's own cleanup. Only the parent-death channel and the
# watchdog can end the run, which is exactly what is being asserted.

set -euo pipefail

# `pgrep` exits 1 when nothing matches, which under `set -e`/`pipefail` is the
# script's own death rather than the answer "nothing is running" — and a drill
# that dies silently reads exactly like a drill that passed.
alive() { pgrep -f "$PATTERN" | tr '\n' ' ' || true; }

FIXDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
N="${1:-15}"
LOG="$(mktemp "${TMPDIR:-/tmp}/probe5b-drill-XXXXXX.log")"
PATTERN='runaway.py|common.py watchdog'

cleanup() { rm -f "$LOG"; }
trap cleanup EXIT

pre="$(alive)"
if [ -n "$pre" ]; then
  echo "drill: REFUSING TO RUN: fixture processes are already alive ($pre)." >&2
  echo "drill: a survivor from an earlier run would make this drill pass or fail for the wrong reason." >&2
  exit 2
fi

"$FIXDIR/driver.sh" --evidence "$LOG.jsonl" --keep-scratch >"$LOG" 2>&1 &
DRIVER=$!

# Wait until the surrogate is actually armed. Killing the driver before that
# would assert nothing: there would be nothing downstream to strand.
for _ in $(seq 1 150); do
  grep -q 'containment OK: pid' "$LOG" 2>/dev/null && break
  sleep 0.2
done
before="$(alive)"
if [ -z "$before" ]; then
  echo "drill: INCONCLUSIVE: nothing downstream was running to strand." >&2
  kill -9 "$DRIVER" 2>/dev/null || true
  exit 2
fi
echo "drill: downstream before the kill: $before"

start="$(date +%s)"
kill -9 "$DRIVER"
echo "drill: driver $DRIVER SIGKILLed (its EXIT trap does NOT run)"

for _ in $(seq 1 "$N"); do
  [ -z "$(alive | tr -d ' ')" ] && break
  sleep 1
done

left="$(alive)"
elapsed=$(( $(date +%s) - start ))
rm -f "$LOG.jsonl"
rm -rf "${TMPDIR:-/tmp}"/probe5b-* 2>/dev/null || true

if [ -n "$left" ]; then
  echo "drill: FAIL: still alive ${N}s after the driver died: $left" >&2
  echo "drill: the fixture can outlive its own deadline. Do NOT run a leg." >&2
  exit 1
fi
echo "drill: PASS — all downstream processes exited ${elapsed}s after the driver was killed (budget ${N}s)"
