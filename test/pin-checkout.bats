#!/usr/bin/env bats
# scripts/pin-checkout.sh — sets hooks.pinnedBranch and installs the
# post-checkout backstop (scripts/lib/pinned-checkout-hook.sh). Every case runs
# against throwaway repos and real checkouts, so the hook is exercised by git.

setup() {
  setup_test
  # A developer's global config must not leak a hooksPath or a pin in.
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
  unset WORKFLOW_SKILLS_ALLOW_HEAD_MOVE
  SCRIPT="$REPO_ROOT/scripts/pin-checkout.sh"
  repo="$TEST_TMPDIR/repo"
  git init -q -b main "$repo"
  git -C "$repo" config user.name test
  git -C "$repo" config user.email test@example.com
  echo main >"$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit -q -m init
  git -C "$repo" checkout -q -b other
  echo other >"$repo/file.txt"
  git -C "$repo" commit -q -am other
  git -C "$repo" checkout -q main
  hook="$repo/.git/hooks/post-checkout"
}
teardown() { teardown_test; }
load test_helper

pin() { (cd "${2:-$repo}" && "$SCRIPT" "$1"); }
# Not --short, which prints heads/<name> when a tag shares the name.
branch_of() { git -C "$1" symbolic-ref -q HEAD | sed 's|^refs/heads/||'; }

@test "sets the key, installs an executable hook, and a checkout ends back on the pin" {
  run pin main
  assert_success
  assert_equal "$(git -C "$repo" config --get hooks.pinnedBranch)" main
  assert_file_executable "$hook"

  run git -C "$repo" checkout other
  assert_success
  assert_output --partial "This checkout is pinned to 'main'"
  assert_equal "$(branch_of "$repo")" main
  assert_equal "$(cat "$repo/file.txt")" main

  run git -C "$repo" checkout -b new
  assert_equal "$(branch_of "$repo")" main
}

@test "re-running is idempotent" {
  pin main
  run pin main
  assert_success
  assert_output --partial "already installed"
}

@test "the bypass variable lets the checkout stand" {
  pin main
  WORKFLOW_SKILLS_ALLOW_HEAD_MOVE=1 git -C "$repo" checkout -q other
  assert_equal "$(branch_of "$repo")" other
}

@test "a detached HEAD is left alone" {
  pin main
  git -C "$repo" checkout -q --detach other
  assert_equal "$(branch_of "$repo")" ""
  assert_equal "$(cat "$repo/file.txt")" other
}

@test "an existing post-checkout hook is not overwritten and nothing is written" {
  mkdir -p "$repo/.git/hooks"
  printf '#!/bin/sh\necho mine\n' >"$hook"
  run pin main
  assert_failure 4
  assert_output --partial "refusing to overwrite"
  assert_equal "$(cat "$hook")" "$(printf '#!/bin/sh\necho mine\n')"
  run git -C "$repo" config --get hooks.pinnedBranch
  assert_failure
}

@test "a set core.hooksPath installs nothing and names where the hook belongs" {
  git -C "$repo" config core.hooksPath "$TEST_TMPDIR/myhooks"
  run pin main
  assert_failure 3
  assert_output --partial "$TEST_TMPDIR/myhooks/post-checkout"
  assert_file_not_exists "$hook"
  assert_file_not_exists "$TEST_TMPDIR/myhooks/post-checkout"
  assert_equal "$(git -C "$repo" config --get hooks.pinnedBranch)" main
}

@test "an empty core.hooksPath counts as set: git skips .git/hooks then too" {
  git -C "$repo" config core.hooksPath ""
  run pin main
  assert_failure 3
  assert_file_not_exists "$hook"
  assert_output --partial "set but empty"
  refute_output --partial "fatal:"
}

@test "from a linked worktree it pins the main checkout, and the worktree stays free" {
  wt="$TEST_TMPDIR/wt"
  git -C "$repo" worktree add -q "$wt" -b feature
  run pin main "$wt"
  assert_success
  assert_file_executable "$hook"
  assert_equal "$(git -C "$repo" config --get hooks.pinnedBranch)" main

  git -C "$wt" checkout -q other
  assert_equal "$(branch_of "$wt")" other

  git -C "$repo" checkout -q -b elsewhere 2>/dev/null
  assert_equal "$(branch_of "$repo")" main
}

@test "a failed restore says so loudly" {
  pin main
  # main tracks only-main.txt and the other branches do not, so an untracked
  # copy blocks the restore.
  echo x >"$repo/only-main.txt"
  git -C "$repo" add only-main.txt
  git -C "$repo" commit -q -m only-main
  WORKFLOW_SKILLS_ALLOW_HEAD_MOVE=1 git -C "$repo" checkout -q other
  git -C "$repo" branch third
  echo untracked >"$repo/only-main.txt"
  run git -C "$repo" checkout third
  assert_output --partial "switching back from 'third' FAILED"
  assert_equal "$(branch_of "$repo")" third
}

@test "a tag named like the pin does not make the restore recurse" {
  pin main
  git -C "$repo" tag main
  # Unfixed, the restore re-fires itself forever; the alarm turns that into a
  # failure. perl rather than timeout(1), which stock macOS does not ship.
  run perl -e 'alarm shift; exec @ARGV' 20 git -C "$repo" checkout -q other
  assert_success
  assert_equal "$(branch_of "$repo")" main
}

@test "a shorthand like @{-1} is pinned as the branch it names" {
  git -C "$repo" checkout -q other
  git -C "$repo" checkout -q main
  run pin '@{-1}'
  assert_success
  assert_equal "$(git -C "$repo" config --get hooks.pinnedBranch)" other
}

@test "a failed restore shows git's reason" {
  pin nosuch
  run git -C "$repo" checkout other
  assert_output --partial "switching back from 'other' FAILED"
  # git's own error line, which the hook used to discard.
  assert_output --partial "did not match"
}

@test "refuses a bad branch name, and a bare repo" {
  run pin 'bad..name'
  assert_failure 64
  run pin ''
  assert_failure 64
  git init -q --bare "$TEST_TMPDIR/bare.git"
  run pin main "$TEST_TMPDIR/bare.git"
  assert_failure 2
}
