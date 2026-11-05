#!/usr/bin/env bats
# scripts/task-worktree.sh — checks a task's work branch out at the
# WorktreeCreate hook's layout, so co-review's reach-pr-branch.sh routes to it
# by name (#1395).

setup() {
  setup_test
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
  WT_ROOT="$TEST_TMPDIR/worktrees"
  export WORKFLOW_SKILLS_WORKTREE_ROOT="$WT_ROOT"
  export WORKFLOW_SKILLS_BRANCH_PREFIX="tester/"
  export WT_ROOT

  git -c init.defaultBranch=main init -q --template= "$TEST_TMPDIR/main"
  git -C "$TEST_TMPDIR/main" commit --allow-empty -qm one
}
teardown() { teardown_test; }
load test_helper

SCRIPT="scripts/task-worktree.sh"

tw() {
  local dir="$1"
  shift
  run bash -c "cd '$dir' && '$REPO_ROOT/$SCRIPT' $*"
}

@test "usage error on a missing --ref exits 2" {
  tw "$TEST_TMPDIR/main"
  assert_failure 2
  assert_output --partial "--ref is required"
}

@test "an unknown argument is rejected" {
  tw "$TEST_TMPDIR/main" --ref foo --nope
  assert_failure 2
  assert_output --partial "unknown argument"
}

@test "a local prefixed branch is added at the hook's path" {
  git -C "$TEST_TMPDIR/main" branch "tester/task-7" main
  tw "$TEST_TMPDIR/main" --ref "tester/task-7"
  assert_success
  assert_output --partial "TASK_WORKTREE: ok path=\"$(cd "$WT_ROOT/main/task-7" && pwd -P)\" name=task-7 ref=tester/task-7"
  run git -C "$WT_ROOT/main/task-7" branch --show-current
  assert_output "tester/task-7"
}

@test "co-review's router then reaches it by name, from the run worktree" {
  # The run worktree: a sibling at the hook layout whose HEAD stays put.
  git -C "$TEST_TMPDIR/main" branch "tester/run" main
  mkdir -p "$WT_ROOT/main"
  git -C "$TEST_TMPDIR/main" worktree add -q "$WT_ROOT/main/run" "tester/run"
  git -C "$TEST_TMPDIR/main" branch "tester/task-7" main
  tw "$WT_ROOT/main/run" --ref "tester/task-7"
  assert_success
  run bash -c "cd '$WT_ROOT/main/run' && '$REPO_ROOT/scripts/reach-pr-branch.sh' --ref tester/task-7 --no-add"
  assert_success
  assert_output --partial "ROUTE: enter-name name=task-7 ref=tester/task-7"
  run git -C "$WT_ROOT/main/run" branch --show-current
  assert_output "tester/run"
}

@test "a second call on the same branch is ok and adds nothing" {
  git -C "$TEST_TMPDIR/main" branch "tester/task-7" main
  tw "$TEST_TMPDIR/main" --ref "tester/task-7"
  assert_success
  tw "$TEST_TMPDIR/main" --ref "tester/task-7"
  assert_success
  assert_output --partial "TASK_WORKTREE: ok "
  run bash -c "git -C '$TEST_TMPDIR/main' worktree list | grep -c task-7"
  assert_output "1"
}

@test "a remote-only branch is fetched without an upstream, then added" {
  git -c init.defaultBranch=main clone -q "$TEST_TMPDIR/main" "$TEST_TMPDIR/clone"
  git -C "$TEST_TMPDIR/main" branch "tester/task-9" main
  tw "$TEST_TMPDIR/clone" --ref "tester/task-9"
  assert_success
  assert_output --partial "name=task-9 ref=tester/task-9"
  assert [ -d "$WT_ROOT/clone/task-9" ]
  run git -C "$TEST_TMPDIR/clone" config --get branch.tester/task-9.remote
  assert_failure
}

@test "a branch without the prefix is refused, creating nothing" {
  git -C "$TEST_TMPDIR/main" branch plain main
  tw "$TEST_TMPDIR/main" --ref plain
  assert_failure 1
  assert_output --partial "TASK_WORKTREE: none reason=not-hook-name ref=plain"
  assert [ ! -e "$WT_ROOT/main" ]
}

@test "a branch held by a worktree outside the layout is reported, not moved" {
  git -C "$TEST_TMPDIR/main" branch "tester/task-7" main
  mkdir -p "$WT_ROOT/main/workers"
  git -C "$TEST_TMPDIR/main" worktree add -q "$WT_ROOT/main/workers/task-7" "tester/task-7"
  tw "$TEST_TMPDIR/main" --ref "tester/task-7"
  assert_failure 1
  assert_output --partial "reason=held-elsewhere path=\"$(cd "$WT_ROOT/main/workers/task-7" && pwd -P)\""
  assert [ ! -e "$WT_ROOT/main/task-7" ]
}

@test "an occupied hook path is refused" {
  git -C "$TEST_TMPDIR/main" branch "tester/task-7" main
  mkdir -p "$WT_ROOT/main/task-7"
  tw "$TEST_TMPDIR/main" --ref "tester/task-7"
  assert_failure 1
  assert_output --partial "reason=path-exists"
}

@test "a branch missing locally and on the remote is fetch-failed" {
  tw "$TEST_TMPDIR/main" --ref "tester/nope"
  assert_failure 1
  assert_output --partial "TASK_WORKTREE: none reason=fetch-failed ref=tester/nope"
}
