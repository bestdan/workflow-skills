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
D="$tmp/locks"

# try <wait-seconds> — exit status of one acquire attempt from its own shell.
# The trailing `&& :` is load-bearing: bash execs a lone final command, which
# would make the lock's parent this test script and hold it until the suite
# ends. A real dispatch line always has the reviewer after the lock.
try() {
  bash -c "python3 '$SCRIPT' agy --dir '$D' --wait $1 && :"
}

echo "coreview-input-lock: contention"

# A holder that keeps its line running for 2 s.
bash -c "python3 '$SCRIPT' agy --dir '$D' && sleep 2" &
holder=$!
sleep 0.7

try 0.3
assert_eq "a second run is refused while the first line runs" 3 "$?"

start=$(date +%s)
try 10
rc=$?
waited=$(($(date +%s) - start))
assert_eq "a second run that waits gets the lock once the first line ends" 0 "$rc"
if [ "$waited" -ge 1 ]; then
  ok "the waiting run actually waited (${waited}s)"
else
  bad "the waiting run did not wait (${waited}s) — the lock was not held"
fi
wait "$holder"

echo "coreview-input-lock: release"

bash -c "python3 '$SCRIPT' agy --dir '$D' && sleep 30" &
holder=$!
sleep 0.7
kill "$holder"
wait "$holder" 2>/dev/null
sleep 0.6
try 0.3
assert_eq "killing the holding line frees the lock" 0 "$?"

bash -c "python3 '$SCRIPT' agy --dir '$D' --max-hold 0.5 && sleep 3" &
holder=$!
sleep 1.2
try 0.3
assert_eq "--max-hold frees the lock while the line still runs" 0 "$?"
wait "$holder"

bash -c "python3 '$SCRIPT' agy --dir '$D' && sleep 2" &
holder=$!
sleep 0.7
bash -c "python3 '$SCRIPT' devin --dir '$D' --wait 0 && :"
assert_eq "different reviewers do not share a lock" 0 "$?"
wait "$holder"

echo "coreview-input-lock: refusal gates the chain"

bash -c "python3 '$SCRIPT' agy --dir '$D' && sleep 2" &
holder=$!
sleep 0.7
out="$(bash -c "python3 '$SCRIPT' agy --dir '$D' --wait 0 2>/dev/null && echo dispatched")"
assert_eq "a refused lock stops the && chain before dispatch" "" "$out"
wait "$holder"

echo "coreview-input-lock: usage"

python3 "$SCRIPT" 'agy;rm' --dir "$D" 2>/dev/null
assert_eq "an agent name outside [a-z0-9-] is a usage error" 2 "$?"

echo "test-coreview-input-lock: $pass_count passed, $fail_count failed"
exit "$fail"
