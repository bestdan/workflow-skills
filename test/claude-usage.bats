#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

@test "parses and rounds session usage" {
  write_fixture "$TEST_TMPDIR/usage.json" '{"limits":[{"kind":"session","percent":42.6,"resets_at":"2026-07-10T05:00:00Z"}],"spend":{"used":{"amount_minor":32261}}}'
  run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/usage.json"
  assert_success
  assert_output --partial '"percent":43'
  assert_output --partial '"resets_at":"2026-07-10T05:00:00Z"'
  assert_output --partial '"spend_used_minor":32261'
  run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/usage.json" --session-percent
  assert_success
  assert_output '43'
}

@test "fails closed for absent or invalid session data" {
  for value in '{"limits":[]}' '{"limits":[{"kind":"session","percent":"oops"}]}' 'not json' ''; do
    write_fixture "$TEST_TMPDIR/usage.json" "$value"
    run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/usage.json"
    assert_failure 1
  done
  run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/missing"
  assert_failure 1
}

@test "rejects invalid arguments" {
  run bash "$REPO_ROOT/scripts/claude-usage.sh" --nope
  assert_failure 2
  run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file
  assert_failure 2
}
