#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

probe() {
  make_stub fake-probe "cat <<'YAML'" 'availability:' '  probed_at: 2026-01-01' '  opus:' '    models: []' '  codex:' '    installed: true' '    default_model: test' '  agy:' "    installed: $1" "    logged_in: $2" '  devin:' '    installed: true' '    logged_in: true' '    tier: pro' 'YAML'
}

@test "always emits a verdict and rejects missing source" {
  probe true true
  run env PREFLIGHT_PROBE_CODERS="$BIN_DIR/fake-probe" bash "$REPO_ROOT/scripts/preflight.sh" --source plan --base main --run-root "$REPO_ROOT"
  assert_output --partial 'PREFLIGHT VERDICT:'
  refute_output --partial 'PREFLIGHT BLOCKER: coder'
  run bash "$REPO_ROOT/scripts/preflight.sh" --base main
  assert_failure 2
  assert_output --partial '--source'
  # --run-root has no default on purpose: a fallback would certify whatever tree
  # the caller stood in, so omitting it must fail the way omitting --source does.
  run bash "$REPO_ROOT/scripts/preflight.sh" --source plan --base main
  assert_failure 2
  assert_output --partial '--run-root'
}

@test "logged-out required coder is a named blocker" {
  probe true false
  run env PREFLIGHT_PROBE_CODERS="$BIN_DIR/fake-probe" bash "$REPO_ROOT/scripts/preflight.sh" --source plan --base main --run-root "$REPO_ROOT"
  assert_failure
  assert_output --partial "PREFLIGHT BLOCKER: coder 'agy'"
  assert_output --partial 'PREFLIGHT VERDICT: no-go'
}

@test "crashed coder probe fails closed" {
  make_stub fake-probe 'echo boom >&2' 'exit 3'
  run env PREFLIGHT_PROBE_CODERS="$BIN_DIR/fake-probe" bash "$REPO_ROOT/scripts/preflight.sh" --source plan --base main --run-root "$REPO_ROOT"
  assert_failure
  assert_output --partial 'PREFLIGHT BLOCKER: coder probe failed'
}
