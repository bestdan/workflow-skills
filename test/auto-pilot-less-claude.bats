#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

@test "less-claude profile documents durable CAO delivery settings" {
  run grep -F 'run_profile: default # default | less-claude' "$REPO_ROOT/skills/auto-pilot/references/run-state.md"
  assert_success
  run grep -F 'cao_coder_mapping: {} # default; less-claude: {codex: cao-codex, agy: cao-agy}' "$REPO_ROOT/skills/auto-pilot/references/run-state.md"
  assert_success
  run grep -F 'co_review_mode: default # default | off | cheap-single' "$REPO_ROOT/skills/auto-pilot/references/run-state.md"
  assert_success
  run grep -F 'diff_judgment_tier: orchestrator # default | sonnet' "$REPO_ROOT/skills/auto-pilot/references/run-state.md"
  assert_success
}

@test "less-claude launch is CAO fail-closed and resume rechecks it" {
  run grep -F 'require `cao`, `cao-run`, and `cao-server` on `PATH`' "$REPO_ROOT/skills/auto-pilot/references/launch-preflight.md"
  assert_success
  run grep -F 'nc -z localhost' "$REPO_ROOT/skills/auto-pilot/references/launch-preflight.md"
  assert_success
  run grep -F 're-verify `cao`, `cao-run`, and `cao-server` on `PATH`' "$REPO_ROOT/skills/auto-pilot/references/resume.md"
  assert_success
}

@test "profile composes with the single run-state delivery path" {
  run grep -F -- '--cao-fleet' "$REPO_ROOT/skills/select-coder/SKILL.md"
  assert_success
  run grep -F -- '--run-state .auto-pilot/RUN.md' "$REPO_ROOT/skills/auto-pilot/SKILL.md"
  assert_success
  run grep -F 'co-review skipped (profile)' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
  run grep -F 'subagent with `model: sonnet`' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
  run grep -F '## Auto-pilot reserve gate' "$REPO_ROOT/skills/deliver-task/SKILL.md"
  assert_success
}
