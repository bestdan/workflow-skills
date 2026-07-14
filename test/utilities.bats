#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

@test "archive builder creates upload-ready skill zips" {
  command -v zip >/dev/null || skip "zip is not installed"
  run bash "$REPO_ROOT/scripts/build-claude-ai-zips.sh" "$TEST_TMPDIR/out"
  assert_success
  assert_dir_exists "$TEST_TMPDIR/out"
  assert_file_exists "$TEST_TMPDIR/out/task.zip"
  assert_file_exists "$TEST_TMPDIR/out/review-facts.zip"
}

@test "coder probe reports missing tools as data" {
  make_stub date 'echo 2026-01-01'
  make_stub sed 'exec /usr/bin/sed "$@"'
  make_stub head 'exec /usr/bin/head "$@"'
  make_stub cat 'exec /bin/cat "$@"'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/probe-coders.sh"
  assert_success
  assert_output --partial 'codex:'
  assert_output --partial 'installed: false'
}

@test "coder probe classifies codex auth from stderr" {
  make_stub date 'echo 2026-01-01'
  make_stub sed 'exec /usr/bin/sed "$@"'
  make_stub head 'exec /usr/bin/head "$@"'
  make_stub cat 'exec /bin/cat "$@"'
  # `codex login status` prints to STDERR. If the probe ever drops its 2>&1 it
  # reads empty and silently classifies every install as `unknown`, so assert
  # the stderr path specifically.
  make_stub codex 'if [ "$1 $2" = "login status" ]; then echo "Logged in using ChatGPT" >&2; fi'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/probe-coders.sh"
  assert_success
  assert_output --partial 'auth: chatgpt'
}

@test "eval rejects a missing claude dependency" {
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/eval.sh"
  assert_failure 2
  assert_output --partial 'claude CLI not found'
}

@test "confinement smoke rejects non-macOS environments" {
  command -v sandbox-exec >/dev/null && skip "requires a host without sandbox-exec"
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/smoke-confinement.sh"
  assert_failure 2
  assert_output --partial 'sandbox-exec required'
}
