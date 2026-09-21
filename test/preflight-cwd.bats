#!/usr/bin/env bats

setup() {
  setup_test
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
  git -c init.defaultBranch=main init -q --template= "$TEST_TMPDIR/main"
  git -C "$TEST_TMPDIR/main" commit --allow-empty -qm one
}
teardown() { teardown_test; }
load test_helper

# Run the pre-flight with <dir> as the working directory, the way a session
# standing in that tree would.
cwd_check() {
  local dir="$1"
  shift
  run bash -c "cd '$dir' && '$REPO_ROOT/scripts/preflight-cwd.sh' $*"
}

add_worktree() {
  git -C "$TEST_TMPDIR/main" worktree add -q -b "$1" "$TEST_TMPDIR/$1" main
}

@test "the branch checked out here is ok" {
  cwd_check "$TEST_TMPDIR/main"
  assert_success
  assert_output --partial 'CWD: ok ref=main'
}

@test "a branch checked out in another worktree is foreign, and the hint names it" {
  add_worktree feature
  cwd_check "$TEST_TMPDIR/main" --ref feature
  assert_failure 1
  assert_output --partial 'CWD: foreign ref=feature'
  assert_output --partial "hint=\"enter $(cd "$TEST_TMPDIR/feature" && pwd -P) and re-run\""
}

@test "the same check from inside that worktree is ok" {
  add_worktree feature
  cwd_check "$TEST_TMPDIR/feature" --ref feature
  assert_success
  assert_output --partial 'CWD: ok ref=feature'
}

@test "a branch no worktree has is absent, and names the head it found instead" {
  git -C "$TEST_TMPDIR/main" branch shelved main
  cwd_check "$TEST_TMPDIR/main" --ref shelved
  assert_failure 1
  assert_output --partial 'CWD: absent ref=shelved'
  assert_output --partial 'head=main'
}

@test "a nonexistent ref is absent too, not an error" {
  cwd_check "$TEST_TMPDIR/main" --ref never-existed
  assert_failure 1
  assert_output --partial 'CWD: absent ref=never-existed'
}

# The inventory is the whole verdict in --local mode, which has no ref to
# check: it names the tree the flow stands in and the siblings it does not.
@test "every run prints the worktree inventory and marks the session's own" {
  add_worktree feature
  cwd_check "$TEST_TMPDIR/feature"
  assert_success
  assert_output --partial '[main]'
  assert_output --partial "[feature]  <- cwd"
}

@test "a detached worktree is inventoried, never mistaken for holding the ref" {
  add_worktree feature
  git -C "$TEST_TMPDIR/feature" checkout -q --detach
  cwd_check "$TEST_TMPDIR/main" --ref feature
  assert_failure 1
  assert_output --partial '[DETACHED]'
  assert_output --partial 'CWD: absent ref=feature'
}

@test "detached HEAD with no --ref is a usage error, not a verdict" {
  git -C "$TEST_TMPDIR/main" checkout -q --detach
  cwd_check "$TEST_TMPDIR/main"
  assert_failure 2
  refute_output --partial 'CWD:'
}

@test "outside a repository it fails as usage, not as a verdict" {
  mkdir -p "$TEST_TMPDIR/bare"
  cwd_check "$TEST_TMPDIR/bare"
  assert_failure 2
  assert_output --partial 'not inside a git repository'
}

@test "an unknown argument is rejected" {
  cwd_check "$TEST_TMPDIR/main" --nope
  assert_failure 2
  assert_output --partial 'unknown argument'
}

# A symlinked path is the macOS default for a scratch root ($TMPDIR lives
# behind /private), and comparing raw strings there reports the session's own
# worktree as foreign — the exact false alarm that would train callers to
# ignore this check.
@test "a symlinked path to the same tree is ok, not foreign" {
  ln -s "$TEST_TMPDIR/main" "$TEST_TMPDIR/link"
  cwd_check "$TEST_TMPDIR/link"
  assert_success
  assert_output --partial 'CWD: ok ref=main'
}
