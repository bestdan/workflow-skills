#!/usr/bin/env bats
# scripts/reach-pr-branch.sh — picks and prepares a route to a PR's head
# branch after scripts/preflight-cwd.sh has run, so co-review's foreign/absent
# cases become automatic instead of a stop-and-ask.

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

SCRIPT="scripts/reach-pr-branch.sh"

reach() {
  local dir="$1"
  shift
  run bash -c "cd '$dir' && '$REPO_ROOT/$SCRIPT' $*"
}

@test "usage error on a missing --ref exits 2" {
  reach "$TEST_TMPDIR/main"
  assert_failure 2
  assert_output --partial "--ref is required"
}

@test "an unknown argument is rejected" {
  reach "$TEST_TMPDIR/main" --ref foo --nope
  assert_failure 2
  assert_output --partial "unknown argument"
}

@test "-h prints help and exits 0" {
  reach "$TEST_TMPDIR/main" -h
  assert_success
  assert_output --partial "Usage:"
}

@test "the ref checked out right here is 'here'" {
  reach "$TEST_TMPDIR/main" --ref main
  assert_success
  assert_output --partial "ROUTE: here ref=main"
}

@test "foreign at the hook layout routes to enter-name, creating nothing" {
  git -C "$TEST_TMPDIR/main" branch "tester/feat-a" main
  mkdir -p "$WT_ROOT/main"
  git -C "$TEST_TMPDIR/main" worktree add -q "$WT_ROOT/main/feat-a" "tester/feat-a"
  reach "$TEST_TMPDIR/main" --ref "tester/feat-a"
  assert_success
  assert_output --partial "ROUTE: enter-name name=feat-a ref=tester/feat-a"
}

@test "foreign elsewhere routes to enter-path with the holder's path" {
  git -C "$TEST_TMPDIR/main" worktree add -q -b elsewhere "$TEST_TMPDIR/elsewhere" main
  reach "$TEST_TMPDIR/main" --ref elsewhere
  assert_success
  assert_output --partial "ROUTE: enter-path path=\"$(cd "$TEST_TMPDIR/elsewhere" && pwd -P)\" ref=elsewhere"
  # nothing new was created under the worktree root by this script
  assert [ ! -e "$WT_ROOT/main/elsewhere" ]
}

@test "a local prefixed branch with no worktree yet routes to enter-name" {
  git -C "$TEST_TMPDIR/main" branch "tester/feat-b" main
  reach "$TEST_TMPDIR/main" --ref "tester/feat-b"
  assert_success
  assert_output --partial "ROUTE: enter-name name=feat-b ref=tester/feat-b"
  assert [ ! -e "$WT_ROOT/main/feat-b" ]
}

@test "an unprefixed local branch routes to enter-path and creates the worktree" {
  git -C "$TEST_TMPDIR/main" branch plainbranch main
  reach "$TEST_TMPDIR/main" --ref plainbranch
  assert_success
  assert_output --partial "ROUTE: enter-path path=\"$WT_ROOT/main/plainbranch\" ref=plainbranch"
  assert [ -d "$WT_ROOT/main/plainbranch" ]
  run git -C "$WT_ROOT/main/plainbranch" rev-parse --abbrev-ref HEAD
  assert_output "plainbranch"
}

@test "an invalid-name prefixed branch falls back to enter-path" {
  git -C "$TEST_TMPDIR/main" branch "tester/a/b" main
  reach "$TEST_TMPDIR/main" --ref "tester/a/b"
  assert_success
  assert_output --partial "ROUTE: enter-path path="
  assert_output --partial "ref=tester/a/b"
}

@test "an already-existing path refuses with path-exists and creates nothing new" {
  git -C "$TEST_TMPDIR/main" branch plainbranch main
  mkdir -p "$WT_ROOT/main/plainbranch"
  reach "$TEST_TMPDIR/main" --ref plainbranch
  assert_failure 1
  assert_output --partial "ROUTE: none reason=path-exists path=\"$WT_ROOT/main/plainbranch\""
}

@test "a branch that exists only on a bare remote is fetched then routed" {
  remote="$TEST_TMPDIR/remote.git"
  git init -q --bare -b main "$remote"
  seed="$TEST_TMPDIR/seed"
  git clone -q "$remote" "$seed"
  git -C "$seed" checkout -q -b "tester/feat-r"
  git -C "$seed" commit -q --allow-empty -m work
  git -C "$seed" push -q origin "tester/feat-r"

  repo="$TEST_TMPDIR/repo"
  git clone -q "$remote" "$repo"

  run bash -c "cd '$repo' && '$REPO_ROOT/$SCRIPT' --ref tester/feat-r"
  assert_success
  assert_output --partial "ROUTE: enter-name name=feat-r ref=tester/feat-r"
  run git -C "$repo" show-ref -q --verify "refs/heads/tester/feat-r"
  assert_success
  # No tracking config was written to this checkout's .git/config.
  run git -C "$repo" config --get "branch.tester/feat-r.remote"
  assert_failure
}

@test "a branch on neither local nor remote fails as fetch-failed" {
  reach "$TEST_TMPDIR/main" --ref never-existed
  assert_failure 1
  assert_output --partial "ROUTE: none reason=fetch-failed ref=never-existed"
}

@test "--no-add on an unprefixed absent branch reports needs-path and creates nothing" {
  git -C "$TEST_TMPDIR/main" branch plainbranch main
  reach "$TEST_TMPDIR/main" --ref plainbranch --no-add
  assert_failure 1
  assert_output --partial "ROUTE: none reason=needs-path path=\"$WT_ROOT/main/plainbranch\" ref=plainbranch"
  assert [ ! -e "$WT_ROOT/main/plainbranch" ]
  assert [ ! -d "$WT_ROOT" ] || assert [ ! -e "$WT_ROOT/main" ]
}

@test "--no-add still lets a prefixed absent branch route to enter-name" {
  git -C "$TEST_TMPDIR/main" branch "tester/feat-c" main
  reach "$TEST_TMPDIR/main" --ref "tester/feat-c" --no-add
  assert_success
  assert_output --partial "ROUTE: enter-name name=feat-c ref=tester/feat-c"
}

@test "outside a repository it fails as usage, not as a route" {
  mkdir -p "$TEST_TMPDIR/bare"
  reach "$TEST_TMPDIR/bare" --ref anything
  assert_failure 2
  assert_output --partial "not inside a git repository"
}
