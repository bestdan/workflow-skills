#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

@test "less-claude profile documents durable CAO delivery settings" {
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-state.md" 'run_profile: default # default | less-claude'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-state.md" 'cao_coder_mapping: {} # default; less-claude: {codex: cao-codex, agy: cao-agy}'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-state.md" 'co_review_mode: default # default | off | cheap-single'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-state.md" 'diff_judgment_tier: orchestrator # default | sonnet'
}

@test "less-claude launch is CAO fail-closed and resume rechecks it, via one scout invocation" {
  # The CAO probe list (cao/cao-run/cao-server on PATH, nc -z localhost 9889,
  # cao_coder_mapping vs the fixed fleet) has one home now: preflight.sh's
  # --scout-run-md scout. Launch step 6 and resume both call it rather than
  # each restating the probe list — see run-state.md "RUN.md" table's `coder`
  # column and the two callers below.
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/launch-preflight.md" '"${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh" --scout-run-md'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/resume.md" '"${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh" --scout-run-md'
  assert_doc_contains "$REPO_ROOT/scripts/preflight.sh" 'nc -z localhost 9889'
  assert_doc_contains "$REPO_ROOT/scripts/preflight.sh" 'cao cao-run cao-server'
}

@test "profile composes with the single run-state delivery path" {
  assert_doc_contains "$REPO_ROOT/skills/select-coder/SKILL.md" '--cao-fleet'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/SKILL.md" '--run-state .auto-pilot/RUN.md'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'co-review skipped (profile)'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'subagent with `model: sonnet`'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" '## Auto-pilot reserve gate'
}
