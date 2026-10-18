#!/usr/bin/env bats
#
# Regression guard for the mktemp fail-open bug in the four shell test
# harnesses that build fixtures under a temp BASE and delete it from an EXIT
# trap. Without -e, a failing `mktemp -d` leaves BASE=""; `cd ""` is a bash
# no-op SUCCESS, so the `|| exit 2` never fires, `pwd -P` resolves BASE to the
# script's cwd — the repo root when run via scripts/check.sh — and the trap's
# `rm -rf "$BASE"` then deletes the checkout. scripts/test-verify-fix.sh was
# fixed for this in #367; the same bug was still live in the other three and
# was fixed in #390, which is the recurrence this file exists to stop.
#
# Same shape as test/smoke-confinement.bats, which guards the identical bug in
# scripts/smoke-confinement.sh.

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

# Each harness prints "<name>: could not create a temp dir" and exits 2.
HARNESSES=(
  test-claim-scan
  test-plan-graph
  test-research-spike
  test-verify-fix
)

# Runs one harness from a canary directory and asserts it exited closed
# without touching that directory. The diagnostic is matched exactly, not as a
# bare "could not create" substring: every harness exits 2 for other reasons
# too (a missing jq, a missing script), so a loose match would pass against an
# UNFIXED harness that never reached the guard at all.
assert_fails_closed() {
  local name="$1"
  mkdir -p "$TEST_TMPDIR/canary/keepme"
  write_fixture "$TEST_TMPDIR/canary/keepme/file.txt" "do not delete me"
  run bash -c "cd '$TEST_TMPDIR/canary' && '$REPO_ROOT/scripts/$name.sh'"
  assert_failure 2
  assert_output --partial "$name: could not create a temp dir"
  assert_file_exists "$TEST_TMPDIR/canary/keepme/file.txt"
  assert_dir_exists "$TEST_TMPDIR/canary/keepme"
}

@test "a denied mktemp exits closed without deleting the invoking directory" {
  make_stub mktemp 'exit 1'
  for h in "${HARNESSES[@]}"; do
    assert_fails_closed "$h"
  done
}

# Distinct branch: the test above only ever takes the `[ -d "$BASE" ]` arm, so
# deleting the non-empty check would leave it green — even though
# zero-status/empty-output is precisely the shape that produces the `cd ""`
# no-op success this whole fix exists to close.
@test "an mktemp that succeeds with empty output is caught by the non-empty check" {
  make_stub mktemp 'exit 0'
  for h in "${HARNESSES[@]}"; do
    assert_fails_closed "$h"
  done
}
