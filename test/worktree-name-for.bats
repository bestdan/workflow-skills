#!/usr/bin/env bats
# scripts/worktree-name-for.sh — the EnterWorktree `name` whose hook hand-back
# is the worktree at a path. reach-pr-branch.sh routes on it, so a wrong `0`
# here sends EnterWorktree to create a worktree instead of re-entering one, and
# a wrong `1` costs a path prompt nobody may be there to answer.

bats_require_minimum_version 1.5.0

setup() {
  setup_test
  WT_ROOT="$TEST_TMPDIR/worktrees"
  export WORKFLOW_SKILLS_WORKTREE_ROOT="$WT_ROOT"
  export WORKFLOW_SKILLS_BRANCH_PREFIX="tester/"
  export WT_ROOT

  MAIN="$TEST_TMPDIR/main"
  git init -q "$MAIN"
  git -C "$MAIN" commit --allow-empty -qm one
  mkdir -p "$WT_ROOT/main"
}
teardown() { teardown_test; }
load test_helper

SCRIPT="scripts/worktree-name-for.sh"

# name_for <cwd> [args...] — run the resolver from <cwd>, stderr kept apart so
# "prints nothing" is a claim about stdout alone.
name_for() {
  local dir="$1"
  shift
  run --separate-stderr bash -c 'cd "$1" && shift && "$@"' _ "$dir" "$REPO_ROOT/$SCRIPT" "$@"
}

# hook_worktree <name> [branch] — a worktree where the create hook would put it.
hook_worktree() {
  git -C "$MAIN" worktree add -q -b "${2:-tester/$1}" "$WT_ROOT/main/$1" main
}

@test "a hook-layout worktree on <prefix><n> names <n>" {
  hook_worktree feat-a
  name_for "$MAIN" "$WT_ROOT/main/feat-a"
  assert_success
  assert_output "feat-a"
}

@test "the answer is the same from inside another worktree of the repo" {
  hook_worktree feat-a
  hook_worktree feat-b
  name_for "$WT_ROOT/main/feat-b" "$WT_ROOT/main/feat-a"
  assert_success
  assert_output "feat-a"
}

@test "a path reached through a symlink still names <n>" {
  hook_worktree feat-a
  ln -s "$WT_ROOT/main/feat-a" "$TEST_TMPDIR/link"
  name_for "$MAIN" "$TEST_TMPDIR/link"
  assert_success
  assert_output "feat-a"
}

@test "a /private-style root: symlinked and real spellings both name <n>" {
  # macOS's /var is a symlink to /private/var, so the configured root, the path
  # git records, and the path a caller passes can each be either spelling.
  mkdir -p "$TEST_TMPDIR/private/var"
  ln -s "$TEST_TMPDIR/private/var" "$TEST_TMPDIR/var"
  export WORKFLOW_SKILLS_WORKTREE_ROOT="$TEST_TMPDIR/var/wt"
  mkdir -p "$TEST_TMPDIR/var/wt/main"
  git -C "$MAIN" worktree add -q -b tester/feat-p "$TEST_TMPDIR/var/wt/main/feat-p" main

  name_for "$MAIN" "$TEST_TMPDIR/var/wt/main/feat-p"
  assert_success
  assert_output "feat-p"

  name_for "$MAIN" "$TEST_TMPDIR/private/var/wt/main/feat-p"
  assert_success
  assert_output "feat-p"
}

@test "env root and prefix outrank the repo's worktree config" {
  mkdir -p "$MAIN/dev_docs/worktrees"
  printf 'root: %s\nbranch_prefix: other/\n' "$TEST_TMPDIR/elsewhere" \
    >"$MAIN/dev_docs/worktrees/.worktree-config.yml"
  hook_worktree feat-a
  name_for "$MAIN" "$WT_ROOT/main/feat-a"
  assert_success
  assert_output "feat-a"
}

@test "a different env prefix means a different name, or none" {
  hook_worktree feat-a
  WORKFLOW_SKILLS_BRANCH_PREFIX="alt/" name_for "$MAIN" "$WT_ROOT/main/feat-a"
  assert_failure 1
  assert_output ""

  git -C "$MAIN" worktree add -q -b alt/feat-z "$WT_ROOT/main/feat-z" main
  WORKFLOW_SKILLS_BRANCH_PREFIX="alt/" name_for "$MAIN" "$WT_ROOT/main/feat-z"
  assert_success
  assert_output "feat-z"
}

@test "a different env root moves where <n> must be" {
  hook_worktree feat-a
  WORKFLOW_SKILLS_WORKTREE_ROOT="$TEST_TMPDIR/other-root" name_for "$MAIN" "$WT_ROOT/main/feat-a"
  assert_failure 1
  assert_output ""
}

@test "an unregistered path has no name" {
  mkdir -p "$WT_ROOT/main/feat-u"
  name_for "$MAIN" "$WT_ROOT/main/feat-u"
  assert_failure 1
  assert_output ""
}

@test "a path that does not exist has no name" {
  name_for "$MAIN" "$WT_ROOT/main/never"
  assert_failure 1
  assert_output ""
}

@test "a prunable worktree has no name, even with a directory back at its path" {
  hook_worktree feat-a
  rm -rf "$WT_ROOT/main/feat-a"
  mkdir -p "$WT_ROOT/main/feat-a"
  run git -C "$MAIN" worktree list --porcelain
  assert_output --partial "prunable"
  name_for "$MAIN" "$WT_ROOT/main/feat-a"
  assert_failure 1
  assert_output ""
}

@test "a worktree of another repo at a layout-matching path has no name" {
  other="$TEST_TMPDIR/other/main"
  git init -q "$other"
  git -C "$other" commit --allow-empty -qm other
  git -C "$other" worktree add -q -b tester/feat-o "$WT_ROOT/main/feat-o" main
  name_for "$MAIN" "$WT_ROOT/main/feat-o"
  assert_failure 1
  assert_output ""
}

@test "a registered path now holding another repo's worktree has no name" {
  # This repo's entry stays registered and non-prunable, because a .git file is
  # back at the path — only the common-git-dir check can refuse it.
  hook_worktree feat-x
  rm -rf "$WT_ROOT/main/feat-x"
  other="$TEST_TMPDIR/other/main"
  git init -q "$other"
  git -C "$other" commit --allow-empty -qm other
  git -C "$other" worktree add -q -b tester/feat-x "$WT_ROOT/main/feat-x" main
  run git -C "$MAIN" worktree list --porcelain
  refute_output --partial "prunable"
  name_for "$MAIN" "$WT_ROOT/main/feat-x"
  assert_failure 1
  assert_output ""
}

@test "an unprefixed branch has no name" {
  hook_worktree plain plain
  name_for "$MAIN" "$WT_ROOT/main/plain"
  assert_failure 1
  assert_output ""
}

@test "a prefixed branch whose remainder the hook would refuse has no name" {
  mkdir -p "$WT_ROOT/main/a"
  git -C "$MAIN" worktree add -q -b tester/a/b "$WT_ROOT/main/a/b" main
  name_for "$MAIN" "$WT_ROOT/main/a/b"
  assert_failure 1
  assert_output ""
}

@test "a prefixed branch at the wrong path has no name" {
  git -C "$MAIN" worktree add -q -b tester/feat-w "$WT_ROOT/main/not-feat-w" main
  name_for "$MAIN" "$WT_ROOT/main/not-feat-w"
  assert_failure 1
  assert_output ""
}

@test "a missing argument is a usage error with no stdout" {
  name_for "$MAIN"
  assert_failure 2
  assert_output ""
  [[ "$stderr" == *usage* ]]
}

@test "a failing worktree-config.sh is exit 2 with no stdout" {
  hook_worktree feat-a
  WORKFLOW_SKILLS_WORKTREE_ROOT="relative/root" name_for "$MAIN" "$WT_ROOT/main/feat-a"
  assert_failure 2
  assert_output ""
  [[ "$stderr" == *"could not resolve"* ]]
}

@test "outside a repository it is exit 2, not 'no name'" {
  mkdir -p "$TEST_TMPDIR/bare"
  name_for "$TEST_TMPDIR/bare" "$TEST_TMPDIR/bare"
  assert_failure 2
  assert_output ""
}
