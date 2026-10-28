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

# hold [lock flags…] — start a dispatch line that takes the lock and then runs
# until release(), and return once the lock is actually held. The script writes
# the lock file only after flock succeeds, so a non-empty file is the signal.
# Neither side is timed: a line that slept a fixed time ended early under CI
# load, before the next assertion's own python had started (#1136).
hold() {
  bash -c "python3 '$SCRIPT' agy --dir '$D' $* && until [ -e '$D/release' ]; do sleep 0.05; done" &
  holder=$!
  local i=0
  while [ ! -s "$D/co-review-input.agy.lock" ] && [ "$i" -lt 300 ]; do
    sleep 0.1
    i=$((i + 1))
  done
  if [ ! -s "$D/co-review-input.agy.lock" ]; then
    bad "the holding line never took the lock"
  fi
}

# release — let the holding line end, and wait for it.
release() {
  touch "$D/release"
  wait "$holder"
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
hold
err="$(try 0.3 2>&1)"
assert_eq "a second run is refused while the first line runs" 3 "$?"
case "$err" in
  *"in use by another co-review run"*) ok "the refusal says the input is in use" ;;
  *) bad "the refusal does not say the input is in use: '$err'" ;;
esac

(
  sleep 1
  touch "$D/release"
) &
releaser=$!
try 10
rc=$?
assert_eq "a second run that waits gets the lock once the first line ends" 0 "$rc"
# The lock frees only after the release file exists, so a run that got it with
# no such file got a lock nobody held.
if [ -e "$D/release" ]; then
  ok "the waiting run waited for the first line to end"
else
  bad "the waiting run did not wait — the lock was not held"
fi
wait "$releaser"
wait "$holder"

echo "coreview-input-lock: release"

new_dir
hold
kill "$holder"
wait "$holder" 2>/dev/null
try 3
assert_eq "killing the holding line frees the lock" 0 "$?"

new_dir
hold --max-hold 0.5
try 10
assert_eq "--max-hold frees the lock while the line still runs" 0 "$?"
release

new_dir
hold
try 0 devin
assert_eq "different reviewers do not share a lock" 0 "$?"
release

echo "coreview-input-lock: refusal gates the chain"

new_dir
hold
out="$(bash -c "python3 '$SCRIPT' agy --dir '$D' --wait 0 2>/dev/null && echo dispatched")"
assert_eq "a refused lock stops the && chain before dispatch" "" "$out"
release

echo "coreview-input-lock: usage"

new_dir
python3 "$SCRIPT" 'agy;rm' --dir "$D" 2>/dev/null
assert_eq "an agent name outside [a-z0-9-] is a usage error" 2 "$?"

echo "test-coreview-input-lock: $pass_count passed, $fail_count failed"
exit "$fail"
