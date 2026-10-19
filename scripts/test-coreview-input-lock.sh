#!/usr/bin/env bash
# test-coreview-input-lock.sh — tests for scripts/coreview-input-lock.py.
#
# Each "dispatch" below is a `bash -c` line shaped like a co-review reviewer
# call: the lock at the head of an `&&` chain, then work. The cases are the
# properties #1071 needs: a second run waits instead of sharing <INPUT>, the
# lock frees when the holding line ends (killed or not), and a run that cannot
# get it in time skips rather than proceeding.
#
# Run directly: bash scripts/test-coreview-input-lock.sh
# Also run under macOS's stock bash: /bin/bash scripts/test-coreview-input-lock.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/coreview-input-lock.py"

fail=0
pass_count=0
fail_count=0

ok() {
  pass_count=$((pass_count + 1))
  echo "  OK $1"
}

bad() {
  fail_count=$((fail_count + 1))
  fail=1
  echo "  FAIL $1" >&2
}

assert_eq() {
  # assert_eq <description> <expected> <actual>
  if [ "$2" = "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected '$2', got '$3')"
  fi
}

tmp="$(mktemp -d "${TMPDIR:-/tmp}/test-coreview-input-lock.XXXXXX" 2>/dev/null \
  || mktemp -d "$ROOT/.test-coreview-input-lock.XXXXXX")"
if [ -z "$tmp" ] || [ ! -d "$tmp" ]; then
  echo "test-coreview-input-lock: could not create a temp dir" >&2
  exit 2
fi
trap 'rm -rf "$tmp"' EXIT

# Each case gets its own lock directory: the lock file keeps its `shell_pid=…`
# line after release, so a shared one would make held() true from case 2 on.
case_n=0
new_dir() {
  case_n=$((case_n + 1))
  D="$tmp/case$case_n"
}

# hold <tail> [lock flags…] — start a dispatch line that takes the lock and then
# runs <tail>, and return once the lock is actually held. The script writes the
# lock file only after flock succeeds, so a non-empty file is the signal; this
# replaces fixed sleeps, which raced python's startup on a loaded machine.
hold() {
  local tail=$1
  shift
  bash -c "python3 '$SCRIPT' agy --dir '$D' $* && $tail" &
  holder=$!
  local i=0
  while [ ! -s "$D/co-review-input.agy.lock" ] && [ "$i" -lt 50 ]; do
    sleep 0.1
    i=$((i + 1))
  done
}

# try <wait-seconds> [agent] — exit status of one acquire attempt from its own
# shell. The trailing `&& :` is load-bearing: bash execs a lone final command,
# which would make the lock's parent this test script and hold it until the
# suite ends. A real dispatch line always has the reviewer after the lock.
try() {
  bash -c "python3 '$SCRIPT' ${2:-agy} --dir '$D' --wait $1 && :"
}

echo "coreview-input-lock: contention"

new_dir
hold "sleep 3"
err="$(try 0.3 2>&1)"
assert_eq "a second run is refused while the first line runs" 3 "$?"
case "$err" in
  *"in use by another co-review run"*) ok "the refusal says the input is in use" ;;
  *) bad "the refusal does not say the input is in use: '$err'" ;;
esac

start=$(date +%s)
try 10
rc=$?
waited=$(($(date +%s) - start))
assert_eq "a second run that waits gets the lock once the first line ends" 0 "$rc"
# The holder still had more than 2 s of its 3 s line to run, so even with
# whole-second `date` this cannot read 0 unless the lock was not held.
if [ "$waited" -ge 1 ]; then
  ok "the waiting run actually waited (${waited}s)"
else
  bad "the waiting run did not wait (${waited}s) — the lock was not held"
fi
wait "$holder"

echo "coreview-input-lock: release"

new_dir
hold "sleep 30"
kill "$holder"
wait "$holder" 2>/dev/null
try 3
assert_eq "killing the holding line frees the lock" 0 "$?"

new_dir
hold "sleep 5" --max-hold 0.5
try 3
assert_eq "--max-hold frees the lock while the line still runs" 0 "$?"
kill "$holder" 2>/dev/null
wait "$holder" 2>/dev/null

new_dir
hold "sleep 2"
try 0 devin
assert_eq "different reviewers do not share a lock" 0 "$?"
wait "$holder"

echo "coreview-input-lock: refusal gates the chain"

new_dir
hold "sleep 2"
out="$(bash -c "python3 '$SCRIPT' agy --dir '$D' --wait 0 2>/dev/null && echo dispatched")"
assert_eq "a refused lock stops the && chain before dispatch" "" "$out"
wait "$holder"

echo "coreview-input-lock: usage"

new_dir
python3 "$SCRIPT" 'agy;rm' --dir "$D" 2>/dev/null
assert_eq "an agent name outside [a-z0-9-] is a usage error" 2 "$?"

echo "test-coreview-input-lock: $pass_count passed, $fail_count failed"
exit "$fail"
