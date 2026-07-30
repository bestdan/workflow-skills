#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

@test "reserve protocol gates deliver-task lifecycle boundaries below headroom" {
  reset=$(($(date +%s) + 3600))
  resets_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$reset")"
  write_fixture "$TEST_TMPDIR/usage.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":86,\"resets_at\":\"$resets_at\"}]}"

  CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/reset-state.json" run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/usage.json" --session-status
  assert_success
  assert_output "86 $reset"

  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'headroom = 100 - percent'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'when `headroom < reserve`, do not start'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'reserve = max(fixed_floor, observed_worst_task_delta * safety)'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'are **N = 5** such deltas, set `reserve = fixed_floor`'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-state.md" '`usage_deltas` is the rolling, capped (20-entry) record'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" '**re-verify** and repeated **co-review**'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" '--run-state <RUN.md>'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'step 2, **Claim**'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'step 3, **Verify**'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'step 5, **Co-review**'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'every **re-verify** and every repeated'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'This is an expected auto-pilot pause, **not a delivery'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'paused_until = reset_epoch + grace'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'paused_until = now + 3600'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" '`pause_observed_at` and `pause_source`'
}

@test "reserve instrumentation discards a delta across a reset window" {
  first_reset=$(($(date +%s) + 300))
  second_reset=$(($(date +%s) + 3600))
  first_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$first_reset")"
  second_at="$(python3 -c 'import datetime,sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$second_reset")"
  write_fixture "$TEST_TMPDIR/before-reset.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":91,\"resets_at\":\"$first_at\"}]}"
  write_fixture "$TEST_TMPDIR/after-reset.json" "{\"limits\":[{\"kind\":\"session\",\"percent\":4,\"resets_at\":\"$second_at\"}]}"

  CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/reset-state.json" run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/before-reset.json" --session-status
  assert_success
  assert_output "91 $first_reset"
  CLAUDE_USAGE_RESET_STATE_FILE="$TEST_TMPDIR/reset-state.json" run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/after-reset.json" --session-status
  assert_success
  assert_output "4 $second_reset"

  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-state.md" '**discard the cross-window delta**'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'Fewer than five valid recorded in-window deltas means the effective'
}
