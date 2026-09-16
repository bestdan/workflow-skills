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

  # The gate is a subcommand now, so the behavior is pinned in
  # scripts/test-spawn-orchestrator-reserve.sh. What stays a doc assertion is
  # the wiring: every boundary makes the call, and the pause path is unchanged.
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" '--run-state <RUN.md>'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'reserve-gate \'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" '--run-md <RUN.md> --percent <percent> --reset-epoch <reset_epoch>'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" '--run-md <RUN.md> --read-failed'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'Call it immediately before step 2 **Claim**, step 3 **Verify**, step 5'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" '**Co-review**, and — in step 6 **Iterate** — every **re-verify** and every'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" 'An expected auto-pilot pause, **not a delivery failure**'
  assert_doc_contains "$REPO_ROOT/skills/deliver-task/SKILL.md" '`pause_observed_at` and `pause_source`'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'paused_until = reset_epoch + grace'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'paused_until = now + 3600'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'every iterate-round **re-verify** and repeated'
}

@test "the reserve formula has exactly one home, and the prose points at it" {
  # The defect this replaced: the same rules specified in run-budget.md AND
  # run-state.md, hand-walked at four /deliver-task boundaries. The header
  # comment of the subcommand is now the only place the arithmetic is written
  # down, so assert both halves — it is there, and the two reference files
  # carry a pointer instead of a copy.
  assert_doc_contains "$REPO_ROOT/scripts/spawn-orchestrator.sh" 'reserve        = max(floor, ceil(observed_worst * 1.25))'
  assert_doc_contains "$REPO_ROOT/scripts/spawn-orchestrator.sh" 'headroom       = 100 - percent      → pause when headroom < reserve'
  assert_doc_contains "$REPO_ROOT/scripts/spawn-orchestrator.sh" 'Fewer than --samples (default 5) in-window deltas means'
  assert_doc_contains "$REPO_ROOT/scripts/spawn-orchestrator.sh" 'DISCARD it; append nothing; baseline :='

  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-budget.md" 'is specified there and nowhere else'
  assert_doc_contains "$REPO_ROOT/skills/auto-pilot/references/run-state.md" 'is the only writer and the only'

  # And the copies really are gone: no skills/ file may restate an update rule.
  run grep -rn 'reset_epoch match' "$REPO_ROOT/skills"
  assert_failure
  run grep -rn 'retain only the newest 20' "$REPO_ROOT/skills"
  assert_failure
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

  # The two readings above straddle a reset, which is the case the gate must
  # throw away. Drive the real subcommand with them rather than asserting prose.
  mkdir -p "$TEST_TMPDIR/run/.auto-pilot"
  cat >"$TEST_TMPDIR/run/.auto-pilot/RUN.md" <<EOF
---
run_id: bats
status: active
reserve: 15
usage_delta_baseline:
usage_deltas: []
---
EOF
  run bash "$REPO_ROOT/scripts/spawn-orchestrator.sh" reserve-gate \
    --run-md "$TEST_TMPDIR/run/.auto-pilot/RUN.md" --percent 91 --reset-epoch "$first_reset"
  # 9% headroom is below the 15% floor, so this boundary pauses — but the
  # bookkeeping still runs, which is what the second call needs.
  assert_failure 1
  run bash "$REPO_ROOT/scripts/spawn-orchestrator.sh" reserve-gate \
    --run-md "$TEST_TMPDIR/run/.auto-pilot/RUN.md" --percent 4 --reset-epoch "$second_reset"
  assert_success
  run grep -F 'usage_deltas: []' "$TEST_TMPDIR/run/.auto-pilot/RUN.md"
  assert_success
  run grep -F "usage_delta_baseline: {percent: 4, reset_epoch: $second_reset}" "$TEST_TMPDIR/run/.auto-pilot/RUN.md"
  assert_success
}
