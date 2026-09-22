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
  cwd_check "$TEST_TMPDIR/main" --ref main
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

# The skill tells the caller to take the EnterWorktree path from the worktree=
# field, so an unquoted path with a space makes the verdict line unparseable
# exactly where it is being read for a path.
@test "a worktree path with a space stays parseable" {
  git -C "$TEST_TMPDIR/main" worktree add -q -b spaced "$TEST_TMPDIR/my tree" main
  cwd_check "$TEST_TMPDIR/main" --ref spaced
  assert_failure 1
  assert_output --partial "worktree=\"$(cd "$TEST_TMPDIR/my tree" && pwd -P)\""
  assert_output --partial "cwd=\"$(cd "$TEST_TMPDIR/main" && pwd -P)\""
}

# A tab splits the intermediate records, inventing a worktree whose made-up
# branch name could match a real ref and win the holder lookup — a confident
# wrong verdict, which is the one outcome this script exists to prevent.
@test "a tab in a worktree path refuses a verdict, never fabricates one" {
  git -C "$TEST_TMPDIR/main" worktree add -q -b tabbed "$TEST_TMPDIR/tab	tree" main
  cwd_check "$TEST_TMPDIR/main" --ref tree
  assert_failure 3
  assert_output --partial 'CWD: unknown reason=unparseable-path'
  refute_output --partial 'CWD: foreign'
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
  # The attached case is inventory-only too, not a trivially-ok branch check.
  assert_output --partial 'CWD: ok ref=none cwd='
  assert_output --partial 'head=feature'
}

@test "a detached worktree is inventoried, never mistaken for holding the ref" {
  add_worktree feature
  git -C "$TEST_TMPDIR/feature" checkout -q --detach
  cwd_check "$TEST_TMPDIR/main" --ref feature
  assert_failure 1
  assert_output --partial '[DETACHED]'
  assert_output --partial 'CWD: absent ref=feature'
}

# A deleted worktree directory stays in `worktree list` with its branch and a
# `prunable` line. Treating it as a holder would answer `foreign` with an
# EnterWorktree hint for a path that is not there — the worst answer available,
# since the skill tells the caller to enter that path without re-checking.
@test "a worktree whose directory was deleted is absent, never foreign" {
  add_worktree feature
  rm -rf "$TEST_TMPDIR/feature"
  cwd_check "$TEST_TMPDIR/main" --ref feature
  assert_failure 1
  assert_output --partial 'CWD: absent ref=feature'
  assert_output --partial '(prunable)'
  refute_output --partial 'CWD: foreign'
}

# The warn-and-continue arm. A regression that turned it into a hard failure or
# a confident wrong verdict would otherwise pass the whole suite.
@test "a failing worktree list is unknown, not a verdict" {
  mkdir -p "$TEST_TMPDIR/stub"
  cat >"$TEST_TMPDIR/stub/git" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "worktree" ] && [ "$2" = "list" ]; then
  echo "fatal: stubbed failure" >&2
  exit 1
fi
exec /usr/bin/git "$@"
SH
  chmod +x "$TEST_TMPDIR/stub/git"
  run bash -c "cd '$TEST_TMPDIR/main' && PATH='$TEST_TMPDIR/stub:$PATH' '$REPO_ROOT/scripts/preflight-cwd.sh'"
  assert_failure 3
  assert_output --partial 'CWD: unknown reason=worktree-list-failed'
}

# --ref has no current-branch default on purpose: "is my current branch checked
# out here" is a tautology that can only answer ok, and defaulting would make a
# detached HEAD an error in the one mode (--local) that reviews a tree rather
# than a branch.
@test "no --ref is inventory-only: a detached HEAD exits 0 and is named" {
  git -C "$TEST_TMPDIR/main" checkout -q --detach
  cwd_check "$TEST_TMPDIR/main"
  assert_success
  assert_output --partial 'CWD: ok ref=none'
  assert_output --partial 'head=DETACHED'
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
  cwd_check "$TEST_TMPDIR/link" --ref main
  assert_success
  assert_output --partial 'CWD: ok ref=main'
}
