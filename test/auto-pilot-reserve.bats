#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

@test "reserve protocol gates deliver-task lifecycle boundaries below headroom" {
  write_fixture "$TEST_TMPDIR/usage.json" '{"limits":[{"kind":"session","percent":86,"resets_at":"2026-07-10T05:00:00Z"}]}'

  run bash "$REPO_ROOT/scripts/claude-usage.sh" --from-file "$TEST_TMPDIR/usage.json" --session-status
  assert_success
  assert_output '86 1783659600'

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
}
