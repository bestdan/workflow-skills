#!/usr/bin/env bats

setup() {
  setup_test
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
  git -c init.defaultBranch=main init --bare -q --template= "$TEST_TMPDIR/remote.git"
  git -c init.defaultBranch=main init -q --template= "$TEST_TMPDIR/clone"
  git -C "$TEST_TMPDIR/clone" commit --allow-empty -qm one
  git -C "$TEST_TMPDIR/clone" remote add origin "$TEST_TMPDIR/remote.git"
  git -C "$TEST_TMPDIR/clone" push -q origin main
}
teardown() { teardown_test; }
load test_helper

freshness() { run bash -c "cd '$TEST_TMPDIR/clone' && '$REPO_ROOT/scripts/preflight-freshness.sh' $*"; }
advance_remote() {
  git clone -q --template= "$TEST_TMPDIR/remote.git" "$TEST_TMPDIR/other"
  git -C "$TEST_TMPDIR/other" commit --allow-empty -qm remote
  git -C "$TEST_TMPDIR/other" push -q origin main
}

@test "reports fresh, behind, ahead, and diverged topology" {
  freshness
  assert_success
  assert_output --partial 'FRESHNESS: fresh refs=main'
  advance_remote
  freshness
  assert_failure 1
  assert_output --partial 'FRESHNESS: stale refs=main'
  git -C "$TEST_TMPDIR/clone" commit --allow-empty -qm local
  freshness
  assert_failure 1
  assert_output --partial 'FRESHNESS: stale refs=main'
}

@test "an ahead-only branch is fresh and an unpushed branch is skipped" {
  git -C "$TEST_TMPDIR/clone" commit --allow-empty -qm local
  freshness
  assert_success
  git -C "$TEST_TMPDIR/clone" checkout -qb feature
  freshness --ref feature
  assert_success
  assert_output --partial 'FRESHNESS: fresh refs='
}

@test "unreachable remote is unknown" {
  rm -rf "$TEST_TMPDIR/remote.git"
  freshness
  assert_failure 3
  assert_output --partial 'FRESHNESS: unknown reason=ls-remote-failed'
}
