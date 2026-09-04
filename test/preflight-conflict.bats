#!/usr/bin/env bats

setup() {
  setup_test
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
  git -c init.defaultBranch=main init --bare -q --template= "$TEST_TMPDIR/remote.git"
  git -c init.defaultBranch=main init -q --template= "$TEST_TMPDIR/clone"
  echo base >"$TEST_TMPDIR/clone/f.txt"
  git -C "$TEST_TMPDIR/clone" add f.txt
  git -C "$TEST_TMPDIR/clone" commit -qm one
  git -C "$TEST_TMPDIR/clone" remote add origin "$TEST_TMPDIR/remote.git"
  git -C "$TEST_TMPDIR/clone" push -q origin main
}
teardown() { teardown_test; }
load test_helper

conflict() { run bash -c "cd '$TEST_TMPDIR/clone' && '$REPO_ROOT/scripts/preflight-conflict.sh' $*"; }

@test "a cleanly-mergeable branch reports clean" {
  git -C "$TEST_TMPDIR/clone" checkout -qb feature
  echo new >"$TEST_TMPDIR/clone/g.txt"
  git -C "$TEST_TMPDIR/clone" add g.txt
  git -C "$TEST_TMPDIR/clone" commit -qm feature
  conflict --ref feature
  assert_success
  assert_output --partial 'CONFLICT: clean base=main ref=feature'
}

@test "a genuinely conflicting branch reports the conflicted path" {
  git -C "$TEST_TMPDIR/clone" checkout -qb feature
  echo feature-change >"$TEST_TMPDIR/clone/f.txt"
  git -C "$TEST_TMPDIR/clone" commit -qam feature
  git -C "$TEST_TMPDIR/clone" checkout -q main
  echo main-change >"$TEST_TMPDIR/clone/f.txt"
  git -C "$TEST_TMPDIR/clone" commit -qam main-change
  git -C "$TEST_TMPDIR/clone" push -q origin main
  conflict --ref feature
  assert_failure 1
  assert_output --partial 'CONFLICT: conflicting base=main ref=feature'
  assert_output --partial 'paths=f.txt'
  assert_output --partial 'hint="git rebase main"'
}

@test "a branch behind the base but not conflicting is clean, not stale" {
  git -C "$TEST_TMPDIR/clone" checkout -qb behind
  git -C "$TEST_TMPDIR/clone" checkout -q main
  echo main-change >"$TEST_TMPDIR/clone/f.txt"
  git -C "$TEST_TMPDIR/clone" commit -qam main-change
  git -C "$TEST_TMPDIR/clone" push -q origin main
  conflict --ref behind
  assert_success
  assert_output --partial 'CONFLICT: clean base=main ref=behind'
}

@test "--no-fetch with no base tip available reports unknown" {
  git -C "$TEST_TMPDIR/clone" checkout -qb feature
  git -C "$TEST_TMPDIR/clone" remote remove origin
  conflict --no-fetch --base does-not-exist --ref feature
  assert_failure 3
  assert_output --partial 'CONFLICT: unknown reason=no-base-tip'
}

@test "a fetch failure reports unknown, not a conflict verdict" {
  git -C "$TEST_TMPDIR/clone" checkout -qb feature
  git -C "$TEST_TMPDIR/clone" remote set-url origin "$TEST_TMPDIR/no-such-remote"
  conflict --ref feature
  assert_failure 3
  assert_output --partial 'CONFLICT: unknown reason=fetch-failed'
}

@test "the working tree and current branch are unchanged across a run" {
  git -C "$TEST_TMPDIR/clone" checkout -qb feature
  echo feature-change >"$TEST_TMPDIR/clone/f.txt"
  git -C "$TEST_TMPDIR/clone" commit -qam feature
  before_status="$(git -C "$TEST_TMPDIR/clone" status --porcelain)"
  before_head="$(git -C "$TEST_TMPDIR/clone" rev-parse HEAD)"
  conflict --ref feature
  assert_success
  after_status="$(git -C "$TEST_TMPDIR/clone" status --porcelain)"
  after_head="$(git -C "$TEST_TMPDIR/clone" rev-parse HEAD)"
  [ "$before_status" = "$after_status" ]
  [ "$before_head" = "$after_head" ]
}

@test "an unknown flag is a usage error" {
  conflict --bogus
  assert_failure 2
}

@test "a tag sharing the branch name does not shadow the branch" {
  # The branch tip merges cleanly; the same-named tag points at a commit that
  # conflicts. Git's disambiguation prefers refs/tags/<name>, so a bare
  # resolution would test the tag and report a conflict about the wrong commit.
  git -C "$TEST_TMPDIR/clone" checkout -qb shared
  echo new >"$TEST_TMPDIR/clone/g.txt"
  git -C "$TEST_TMPDIR/clone" add g.txt
  git -C "$TEST_TMPDIR/clone" commit -qm "branch tip merges cleanly"
  git -C "$TEST_TMPDIR/clone" checkout -q main
  echo main-side >"$TEST_TMPDIR/clone/f.txt"
  git -C "$TEST_TMPDIR/clone" commit -qam "main moves f.txt"
  git -C "$TEST_TMPDIR/clone" push -q origin main
  git -C "$TEST_TMPDIR/clone" checkout -q -b tagged main~1
  echo tag-side >"$TEST_TMPDIR/clone/f.txt"
  git -C "$TEST_TMPDIR/clone" commit -qam "tag tip conflicts"
  git -C "$TEST_TMPDIR/clone" tag shared HEAD
  git -C "$TEST_TMPDIR/clone" checkout -q shared

  conflict --ref shared
  assert_success
  assert_output --partial 'CONFLICT: clean base=main ref=shared'
}
