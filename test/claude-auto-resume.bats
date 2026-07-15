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

# --- full capped iteration, end-to-end ------------------------------------
# Drive the actual resume loop (wait_until -> --continue relaunch -> flag
# cleanup -> loop cap) with a file-backed fake clock so wait_until terminates
# instantly. `date` reads the clock; the `sleep` stub jumps it far past the
# reset target so the countdown breaks after one tick.
install_fake_clock() {
  printf '%s' 1000 >"$TEST_TMPDIR/clock"
  make_stub date 'if [ "$1" = "+%s" ]; then cat "'"$TEST_TMPDIR"'/clock"; else exec /bin/date "$@"; fi'
  make_stub sleep 'c=$(cat "'"$TEST_TMPDIR"'/clock"); printf "%s" "$((c + 2000000))" >"'"$TEST_TMPDIR"'/clock"'
}

@test "resumes once when capped, then exits when the cap clears" {
  install_fake_clock
  # Stateful usage stub: first call reports the cap (100), the next reports a
  # cleared window (50) so the loop resumes exactly once then quits.
  printf '%s' 0 >"$TEST_TMPDIR/count"
  cat >"$SCRIPT_DIR/claude-usage.sh" <<EOF
#!/usr/bin/env bash
n=\$(cat "$TEST_TMPDIR/count"); printf '%s' "\$((n + 1))" >"$TEST_TMPDIR/count"
reset=\$(( \$(cat "$TEST_TMPDIR/clock") + 1000000 ))
if [ "\$n" -eq 0 ]; then echo "100 \$reset"; else echo "50 \$reset"; fi
EOF
  chmod +x "$SCRIPT_DIR/claude-usage.sh"
  make_stub claude 'printf "%s\n" "$*" >>"'"$TEST_TMPDIR"'/claude-calls.txt"' 'exit 0'
  : >"$HOME/.claude/.rl_warn" # flag present so we can assert it is cleaned up

  CAR_BUFFER=0 run bash "$CAR" --no-tmux firstprompt
  assert_success
  assert_output --partial "rate-limited"
  assert_output --partial "no active rate limit detected"
  # claude ran twice: initial prompt, then --continue on the resume.
  run cat "$TEST_TMPDIR/claude-calls.txt"
  assert_line --index 0 "firstprompt"
  assert_line --index 1 "--continue"
  assert_file_not_exist "$HOME/.claude/.rl_warn"
}

@test "stops after CAR_MAX_LOOPS resumes" {
  install_fake_clock
  # Always capped: the reset is kept ahead of the (advancing) fake clock so
  # every iteration re-arms, exercising the runaway-loop guard.
  cat >"$SCRIPT_DIR/claude-usage.sh" <<EOF
#!/usr/bin/env bash
echo "100 \$(( \$(cat "$TEST_TMPDIR/clock") + 1000000 ))"
EOF
  chmod +x "$SCRIPT_DIR/claude-usage.sh"
  make_stub claude 'printf "x\n" >>"'"$TEST_TMPDIR"'/claude-calls.txt"' 'exit 0'

  CAR_BUFFER=0 CAR_MAX_LOOPS=2 run bash "$CAR" --no-tmux
  assert_failure 1
  assert_output --partial "hit CAR_MAX_LOOPS=2"
  # Ran MAX_LOOPS + 1 times: the resume that trips the guard still launched.
  run grep -c x "$TEST_TMPDIR/claude-calls.txt"
  assert_output 3
}

# --- capped_reset_epoch, exercised directly -------------------------------
# The cases below isolate just the resume decision (no relaunch), covering the
# below-cap / at-cap / past-reset / offline branches. The resume-decision logic
# is verified by
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
