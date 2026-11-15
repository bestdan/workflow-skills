#!/usr/bin/env bats
#
# scripts/work-tmp.sh — the per-branch temp directory resolver (#1539).

setup() {
  setup_test
  ROOT="$TEST_TMPDIR/root"
  export WORK_TMP_ROOT="$ROOT"
  unset CLAUDE_CODE_SESSION_ID
  REPO="$TEST_TMPDIR/myrepo"
  mkdir -p "$REPO"
  git -C "$REPO" init -q
  git -C "$REPO" commit -q --allow-empty -m init
}
teardown() { teardown_test; }
load test_helper

wt() { run "$REPO_ROOT/scripts/work-tmp.sh" "$@"; }

@test "the directory is <root>/<repo>/<branch>, created, mode 0700" {
  cd "$REPO"
  wt
  assert_success
  assert_output "$ROOT/myrepo/main"
  assert_dir_exist "$output"
  [ "$(stat -c %a "$output" 2>/dev/null || stat -f %Lp "$output")" = 700 ]
  [ "$(stat -c %a "$ROOT/myrepo" 2>/dev/null || stat -f %Lp "$ROOT/myrepo")" = 700 ]
}

@test "two branches get different paths, and a slash stays nested" {
  cd "$REPO"
  git checkout -q -b bestdan/task-1
  wt
  assert_output "$ROOT/myrepo/bestdan/task-1"
  git checkout -q -b 'odd#name'
  wt
  assert_output "$ROOT/myrepo/odd_name"
}

@test "two worktrees of one repo share the repo component, differ by branch" {
  git -C "$REPO" worktree add -q -b other "$TEST_TMPDIR/wt-other"
  cd "$REPO"
  wt
  first="$output"
  cd "$TEST_TMPDIR/wt-other"
  wt
  assert_output "$ROOT/myrepo/other"
  [ "$output" != "$first" ]
}

@test "the second call prints the same path" {
  cd "$REPO"
  wt
  first="$output"
  wt
  assert_output "$first"
}

@test "detached HEAD falls back to the short sha" {
  cd "$REPO"
  git checkout -q --detach
  wt
  assert_output "$ROOT/myrepo/_detached/$(git rev-parse --short HEAD)"
}

@test "detached HEAD with a session id uses the session id" {
  cd "$REPO"
  git checkout -q --detach
  CLAUDE_CODE_SESSION_ID='abc/12 3' wt
  assert_output "$ROOT/myrepo/_session/abc_12_3"
}

@test "outside a repo, a session id scopes it" {
  cd "$TEST_TMPDIR"
  CLAUDE_CODE_SESSION_ID=sess1 wt
  assert_success
  assert_output "$ROOT/_norepo/_session/sess1"
}

@test "outside a repo with no session id is exit 3 naming --key" {
  cd "$TEST_TMPDIR"
  wt
  assert_failure 3
  assert_output --partial "--key"
}

@test "--key replaces the branch and --sub appends a component" {
  cd "$REPO"
  wt --key run-7 --sub co-review
  assert_output "$ROOT/myrepo/run-7/co-review"
  wt --sub co-review
  assert_output "$ROOT/myrepo/main/co-review"
}

@test "--root overrides the root" {
  cd "$REPO"
  wt --root "$TEST_TMPDIR/elsewhere"
  assert_output "$TEST_TMPDIR/elsewhere/myrepo/main"
}

@test "usage errors are exit 2" {
  cd "$REPO"
  wt --bogus
  assert_failure 2
  wt --key
  assert_failure 2
}
