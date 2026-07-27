#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

make_worktree() {
  local dir="$1"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" config user.email test@example.com
  git -C "$dir" config user.name Test
  # A developer's global core.hooksPath applies to every repo, including this
  # throwaway fixture; if it blocks commits to `main` (git init's default
  # branch), the commit below is silently vetoed and the fixture is left
  # commit-less for reasons unrelated to cao-coder.
  git -C "$dir" config core.hooksPath /dev/null
  touch "$dir/tracked"
  git -C "$dir" add tracked
  git -C "$dir" commit -qm initial
}

@test "cao-coder forwards a caller-owned worktree to cao-run" {
  worktree="$TEST_TMPDIR/worktree"
  spec="$TEST_TMPDIR/spec.md"
  make_worktree "$worktree"
  write_fixture "$spec" 'make a small change'
  make_stub cao-run 'printf "profile=%s model=%s worktree=%s task=%s\\n" "$@"'

  run "$REPO_ROOT/scripts/cao-coder.sh" "$spec" "$worktree" codex:gpt-5.6-terra

  assert_success
  assert_output "profile=dev-codex model=gpt-5.6-terra worktree=$worktree task=@$spec"
}

@test "cao-coder rejects backends outside the CAO fleet" {
  worktree="$TEST_TMPDIR/worktree"
  spec="$TEST_TMPDIR/spec.md"
  make_worktree "$worktree"
  write_fixture "$spec" task

  run "$REPO_ROOT/scripts/cao-coder.sh" "$spec" "$worktree" opus:claude-opus-4-8

  assert_failure 1
  assert_output --partial 'not in the CAO fleet'

  run "$REPO_ROOT/scripts/cao-coder.sh" "$spec" "$worktree" devin:swe-1.6

  assert_failure 1
  assert_output --partial 'not in the CAO fleet'
}

@test "cao-coder refuses an absent worktree before cao-run can create one" {
  spec="$TEST_TMPDIR/spec.md"
  write_fixture "$spec" task
  make_stub cao-run 'echo should-not-run; exit 99'

  run "$REPO_ROOT/scripts/cao-coder.sh" "$spec" "$TEST_TMPDIR/absent" codex:gpt-5.6-terra

  assert_failure 2
  assert_output --partial 'must already exist; refusing to create one'
}

@test "cao-coder rejects a bare repository as a non-worktree input" {
  bare_repo="$TEST_TMPDIR/bare.git"
  spec="$TEST_TMPDIR/spec.md"
  git init --bare -q "$bare_repo"
  write_fixture "$spec" task
  make_stub cao-run 'echo should-not-run; exit 99'

  run "$REPO_ROOT/scripts/cao-coder.sh" "$spec" "$bare_repo" codex:gpt-5.6-terra

  assert_failure 2
  assert_output --partial "worktree '$bare_repo' is not a git worktree"
}

@test "cao-coder rejects a missing spec file as invalid input" {
  worktree="$TEST_TMPDIR/worktree"
  missing_spec="$TEST_TMPDIR/missing.md"
  make_worktree "$worktree"

  run "$REPO_ROOT/scripts/cao-coder.sh" "$missing_spec" "$worktree" codex:gpt-5.6-terra

  assert_failure 2
  assert_output --partial "spec file '$missing_spec' not found"
}

@test "cao-coder rejects malformed coder specs" {
  worktree="$TEST_TMPDIR/worktree"
  spec="$TEST_TMPDIR/spec.md"
  make_worktree "$worktree"
  write_fixture "$spec" task

  for coder_spec in codex :model codex:; do
    run "$REPO_ROOT/scripts/cao-coder.sh" "$spec" "$worktree" "$coder_spec"

    assert_failure 2
    assert_output --partial 'usage: cao-coder.sh <spec-file> <existing-worktree> <backend:model>'
  done
}

@test "cao-coder reports an absent cao-run after validating inputs" {
  worktree="$TEST_TMPDIR/worktree"
  spec="$TEST_TMPDIR/spec.md"
  isolated_bin="$TEST_TMPDIR/isolated-bin"
  mkdir "$isolated_bin"
  ln -s "$(command -v bash)" "$isolated_bin/bash"
  ln -s "$(command -v git)" "$isolated_bin/git"
  make_worktree "$worktree"
  write_fixture "$spec" task

  run env PATH="$isolated_bin" "$REPO_ROOT/scripts/cao-coder.sh" "$spec" "$worktree" codex:gpt-5.6-terra

  assert_failure 1
  assert_output --partial 'cao-run is required but not found in PATH'
}
