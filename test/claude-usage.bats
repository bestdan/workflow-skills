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

@test "reports session status as '<percent> <epoch>'" {
  reset=$(($(date +%s) + 3600))
  resets_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$reset")"
  write_fixture "$TEST_TMPDIR/usage.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":42.6,\"resets_at\":\"$resets_at\"}],\"spend\":{\"used\":{\"amount_minor\":32261}}}"
  CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/reset-state.json" run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/usage.json" --session-status
  assert_success
  assert_output "42 $reset"
}

@test "session status floors the percent so near-cap does not read as 100" {
  reset=$(($(date +%s) + 3600))
  resets_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$reset")"
  write_fixture "$TEST_TMPDIR/usage.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":99.6,\"resets_at\":\"$resets_at\"}]}"
  CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/reset-state.json" run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/usage.json" --session-status
  assert_success
  assert_output "99 $reset"
}

@test "session status emits the raw reset regardless of writer grace configuration" {
  reset=$(($(date +%s) + 3600))
  resets_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$reset")"
  write_fixture "$TEST_TMPDIR/usage.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":90,\"resets_at\":\"$resets_at\"}]}"
  CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/reset-state.json" CLAUDE_USAGE_RESUME_GRACE_SECONDS=180 run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/usage.json" --session-status
  assert_success
  assert_output "90 $reset"
  run grep -F '"source":"session-status"' "$TEST_TMPDIR/reset-state.json"
  assert_success
}

@test "session status fails closed for past or absurd future resets" {
  for reset in $(($(date +%s) - 60)) $(($(date +%s) + 21601)); do
    resets_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$reset")"
    write_fixture "$TEST_TMPDIR/usage.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":90,\"resets_at\":\"$resets_at\"}]}"
    CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/reset-state.json" run bash -c 'bash "$1" --from-file "$2" --session-status 2>"$3"' _ "$REPO_ROOT/scripts/claude-usage.sh" "$TEST_TMPDIR/usage.json" "$TEST_TMPDIR/stderr"
    assert_failure 1
    assert_output ''
    run grep -F 'implausible session reset' "$TEST_TMPDIR/stderr"
    assert_success
  done
}

@test "session status fails closed for a same-window backward reset" {
  first_reset=$(($(date +%s) + 7200))
  second_reset=$((first_reset - 60))
  first_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$first_reset")"
  second_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$second_reset")"
  write_fixture "$TEST_TMPDIR/first.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":90,\"resets_at\":\"$first_at\"}]}"
  write_fixture "$TEST_TMPDIR/second.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":90,\"resets_at\":\"$second_at\"}]}"
  CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/reset-state.json" run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/first.json" --session-status
  assert_success
  CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/reset-state.json" run bash -c 'bash "$1" --from-file "$2" --session-status 2>"$3"' _ "$REPO_ROOT/scripts/claude-usage.sh" "$TEST_TMPDIR/second.json" "$TEST_TMPDIR/stderr"
  assert_failure 1
  assert_output ''
  run grep -F 'implausible session reset' "$TEST_TMPDIR/stderr"
  assert_success
}

@test "session status fails closed when it cannot persist its observation" {
  reset=$(($(date +%s) + 3600))
  resets_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$reset")"
  write_fixture "$TEST_TMPDIR/usage.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":90,\"resets_at\":\"$resets_at\"}]}"
  CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/missing/reset-state.json" run bash -c 'bash "$1" --from-file "$2" --session-status 2>"$3"' _ "$REPO_ROOT/scripts/claude-usage.sh" "$TEST_TMPDIR/usage.json" "$TEST_TMPDIR/stderr"
  assert_failure 1
  assert_output ''
  run grep -F 'cannot persist reset observation' "$TEST_TMPDIR/stderr"
  assert_success
}

@test "fails closed for --session-status with no resets_at" {
  write_fixture "$TEST_TMPDIR/usage.json" '{"limits":[{"kind":"session","percent":42.6}]}'
  run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/usage.json" --session-status
  assert_failure 1
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
