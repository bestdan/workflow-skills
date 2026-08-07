#!/usr/bin/env bats
#
# Regression guard for the mktemp fail-open bug in scripts/smoke-confinement.sh
# (lines ~20-39). Without -e, a denied `mktemp -d` leaves D=""; `cd ""` is a
# bash no-op SUCCESS, so `pwd -P` silently resolves to the script's cwd, and
# the EXIT trap's `rm -rf "$D"` then deletes wherever the script was invoked
# from. These tests run the REAL script (never the `claude -p` invocations —
# a failed mktemp now exits at the guard, which is well before the first
# `claude` call) with `mktemp` PATH-shadowed to fail, and prove it exits
# closed instead of nuking the invoking directory.

setup() {
  setup_test
  command -v sandbox-exec >/dev/null 2>&1 || skip "sandbox-exec not available"
}
teardown() { teardown_test; }
load test_helper

fail_mktemp() { make_stub mktemp 'exit 1'; }
mark_claude_if_invoked() {
  make_stub claude 'touch "'"$TEST_TMPDIR"'/claude-was-invoked"; exit 0'
}

@test "denied mktemp exits non-zero before ever invoking claude" {
  fail_mktemp
  mark_claude_if_invoked
  mkdir -p "$TEST_TMPDIR/canary"
  run bash -c "cd '$TEST_TMPDIR/canary' && '$REPO_ROOT/scripts/smoke-confinement.sh'"
  assert_failure 2
  # The exact fixture-dir message, not a bare "mktemp failed" substring:
  # spawn-orchestrator.sh has its own unrelated "mktemp failed" line, and a
  # loose partial match would pass against the UNFIXED script too (it reaches
  # that line coincidentally) — which would defeat this test's purpose.
  assert_output --partial "smoke-confinement: mktemp failed — cannot create a fixture dir"
  assert_file_not_exists "$TEST_TMPDIR/claude-was-invoked"
}

@test "denied mktemp does not resolve the fixture dir to cwd and rm -rf it" {
  fail_mktemp
  mark_claude_if_invoked
  mkdir -p "$TEST_TMPDIR/canary/keepme"
  write_fixture "$TEST_TMPDIR/canary/keepme/file.txt" "do not delete me"
  run bash -c "cd '$TEST_TMPDIR/canary' && '$REPO_ROOT/scripts/smoke-confinement.sh'"
  assert_failure 2
  assert_file_exists "$TEST_TMPDIR/canary/keepme/file.txt"
  assert_dir_exists "$TEST_TMPDIR/canary/keepme"
}
