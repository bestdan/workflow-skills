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

  run grep -F 'headroom = 100 - percent' "$REPO_ROOT/skills/auto-pilot/references/run-budget.md"
  assert_success
  run grep -F 'when `headroom < reserve`, do not start' "$REPO_ROOT/skills/auto-pilot/references/run-budget.md"
  assert_success
  run grep -F '**re-verify** and repeated **co-review**' "$REPO_ROOT/skills/auto-pilot/references/run-budget.md"
  assert_success
  run grep -F -- '--run-state <RUN.md>' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
  run grep -F 'step 2, **Claim**' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
  run grep -F 'step 3, **Verify**' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
  run grep -F 'step 5, **Co-review**' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
  run grep -F 'every **re-verify** and every repeated' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
  run grep -F 'This is an expected auto-pilot pause, **not a delivery' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
  run grep -F 'paused_until = reset_epoch + grace' "$REPO_ROOT/skills/auto-pilot/references/run-budget.md"
  assert_success
  run grep -F 'paused_until = now + 3600' "$REPO_ROOT/skills/auto-pilot/references/run-budget.md"
  assert_success
  run grep -F '`pause_observed_at` and `pause_source`' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
}
