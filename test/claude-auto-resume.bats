#!/usr/bin/env bats

setup() {
  setup_test
  # The wrapper resolves its sibling usage script via "$(dirname "$0")", so
  # stub claude-usage.sh must live next to a copy of the wrapper, not just on
  # PATH.
  SCRIPT_DIR="$TEST_TMPDIR/scripts"
  mkdir -p "$SCRIPT_DIR"
  cp "$REPO_ROOT/scripts/claude-auto-resume.sh" "$SCRIPT_DIR/claude-auto-resume.sh"
  chmod +x "$SCRIPT_DIR/claude-auto-resume.sh"
  CAR="$SCRIPT_DIR/claude-auto-resume.sh"

  # Isolate ~/.claude/.rl_warn from the real home directory.
  export HOME="$TEST_TMPDIR/home"
  mkdir -p "$HOME/.claude"

  export CLAUDE_BIN="$BIN_DIR/claude"
  export CAR_TMUX=0
}
teardown() { teardown_test; }
load test_helper

write_usage_stub() {
  # $1: stdout line to print (e.g. "10 9999999999"); non-empty means success (rc 0).
  # $2: exit code (default 0).
  local line="$1" rc="${2:-0}"
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf 'echo %q\n' "$line"
    printf 'exit %s\n' "$rc"
  } >"$SCRIPT_DIR/claude-usage.sh"
  chmod +x "$SCRIPT_DIR/claude-usage.sh"
}

@test "--no-tmux forwards remaining args to claude and never invokes tmux" {
  make_stub claude 'printf "%s\n" "$@" > "'"$TEST_TMPDIR"'/claude-args.txt"' 'exit 0'
  make_stub tmux 'touch "'"$TEST_TMPDIR"'/tmux-invoked"' 'exit 1'
  write_usage_stub "10 9999999999"

  run bash "$CAR" --no-tmux --resume abc --foo bar
  assert_success
  assert_file_exist "$TEST_TMPDIR/claude-args.txt"
  run cat "$TEST_TMPDIR/claude-args.txt"
  assert_output $'--resume\nabc\n--foo\nbar'
  assert_file_not_exist "$TEST_TMPDIR/tmux-invoked"
}

@test "propagates claude's exit status when not rate-limited" {
  make_stub claude 'exit 42'
  write_usage_stub "10 9999999999"

  run bash "$CAR" --no-tmux
  assert_failure 42
  assert_output --partial "no active rate limit detected"
}

@test "near-cap (92%) is treated as a voluntary quit, not a resume" {
  make_stub claude 'exit 0'
  write_usage_stub "92 9999999999"

  run bash "$CAR" --no-tmux
  assert_success
  assert_output --partial "no active rate limit detected"
}

# --- capped_reset_epoch, exercised directly -------------------------------
# Resuming (looping back into claude) can't be tested end-to-end without
# actually sleeping/relaunching, so the resume-decision logic is verified by
# extracting and sourcing just that function.

load_capped_reset_epoch() {
  sed -n '/^capped_reset_epoch()/,/^}/p' "$REPO_ROOT/scripts/claude-auto-resume.sh" >"$TEST_TMPDIR/fn.sh"
  # shellcheck disable=SC1090
  source "$TEST_TMPDIR/fn.sh"
}

@test "capped_reset_epoch: live below cap with future reset does not resume" {
  load_capped_reset_epoch
  CAP_PCT=100
  FLAG="$TEST_TMPDIR/home/.claude/.rl_warn"
  future=$(($(date +%s) + 3600))
  write_usage_stub "92 $future"
  USAGE="$SCRIPT_DIR/claude-usage.sh"
  now() { date +%s; }

  run capped_reset_epoch
  assert_failure
}

@test "capped_reset_epoch: live at cap with future reset resumes" {
  load_capped_reset_epoch
  CAP_PCT=100
  FLAG="$TEST_TMPDIR/home/.claude/.rl_warn"
  future=$(($(date +%s) + 3600))
  write_usage_stub "100 $future"
  USAGE="$SCRIPT_DIR/claude-usage.sh"
  now() { date +%s; }

  run capped_reset_epoch
  assert_success
  assert_output "$future"
}

@test "capped_reset_epoch: live at cap with past reset does not resume" {
  load_capped_reset_epoch
  CAP_PCT=100
  FLAG="$TEST_TMPDIR/home/.claude/.rl_warn"
  past=$(($(date +%s) - 3600))
  write_usage_stub "100 $past"
  USAGE="$SCRIPT_DIR/claude-usage.sh"
  now() { date +%s; }

  run capped_reset_epoch
  assert_failure
}

@test "capped_reset_epoch: offline with a future flag resumes" {
  load_capped_reset_epoch
  CAP_PCT=100
  FLAG="$TEST_TMPDIR/home/.claude/.rl_warn"
  future=$(($(date +%s) + 3600))
  {
    printf '5h_pct=95\n'
    printf '5h_reset=%s\n' "$future"
  } >"$FLAG"
  USAGE="$SCRIPT_DIR/nonexistent-usage.sh"
  now() { date +%s; }

  run capped_reset_epoch
  assert_success
  assert_output "$future"
}

@test "capped_reset_epoch: offline with no flag does not resume" {
  load_capped_reset_epoch
  CAP_PCT=100
  FLAG="$TEST_TMPDIR/home/.claude/.rl_warn"
  USAGE="$SCRIPT_DIR/nonexistent-usage.sh"
  now() { date +%s; }

  run capped_reset_epoch
  assert_failure
}
