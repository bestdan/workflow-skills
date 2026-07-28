#!/usr/bin/env bash

setup_test() {
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  TEST_TMPDIR="$(mktemp -d "${BATS_TEST_TMPDIR}/case.XXXXXX")"
  BIN_DIR="$TEST_TMPDIR/bin"
  mkdir -p "$BIN_DIR"
  export REPO_ROOT TEST_TMPDIR BIN_DIR
  export PATH="$BIN_DIR:$PATH"

  load "$REPO_ROOT/test/vendor/bats-support/load"
  load "$REPO_ROOT/test/vendor/bats-assert/load"
  load "$REPO_ROOT/test/vendor/bats-file/load"

  # A developer's global/system git config leaks into fixture repos too:
  # core.hooksPath (whose pre-commit hook blocks commits to main, and git init
  # names the initial branch main) can silently veto fixture commits, and
  # init.templateDir/commit.gpgsign/aliases are other injection routes. Pin
  # the config env instead of nulling it, so `git init` still deterministically
  # produces branch "main" on stock upstream git. GIT_CONFIG_COUNT/PARAMETERS
  # are command-scope env config that outranks GIT_CONFIG_GLOBAL, and
  # GIT_DIR/GIT_INDEX_FILE/etc are exported by git into every hook subprocess
  # — so a check.sh invoked from a pre-commit hook would otherwise hand
  # fixture `git add` calls the caller's repo/index. GIT_AUTHOR_*/
  # GIT_COMMITTER_* outrank the gitconfig [user] pin below and are exported
  # into hook and `git rebase -x` subprocesses. Unset the lot so no inherited
  # env can redirect a fixture git op the same way. This is the explicit
  # list, not `unset $(git rev-parse --local-env-vars)`: that dynamic form
  # fails open — a missing or broken git yields empty output, the error is
  # swallowed, and this line (whose whole purpose is to run before git is
  # trusted) would silently grant zero isolation.
  unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT \
    GIT_OBJECT_DIRECTORY GIT_DIR GIT_WORK_TREE GIT_IMPLICIT_WORK_TREE GIT_GRAFT_FILE \
    GIT_INDEX_FILE GIT_NO_REPLACE_OBJECTS GIT_REPLACE_REF_BASE GIT_PREFIX GIT_SHALLOW_FILE \
    GIT_COMMON_DIR GIT_TEMPLATE_DIR \
    GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_AUTHOR_DATE \
    GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE
  printf '[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n' >"$TEST_TMPDIR/gitconfig"
  export GIT_CONFIG_GLOBAL="$TEST_TMPDIR/gitconfig"
  export GIT_CONFIG_SYSTEM=/dev/null
  # Symmetry insurance, not a live fix: unlike the shell suites, this file has
  # no repo-local fallback if mktemp -d fails (setup just fails loudly), so
  # TEST_TMPDIR is already safer by construction. A ceiling only stops a
  # discovery walk that would cross TEST_TMPDIR; it's inert for git ops
  # against REPO_ROOT, which find .git at depth zero.
  export GIT_CEILING_DIRECTORIES="$TEST_TMPDIR"
}

teardown_test() {
  rm -rf "$TEST_TMPDIR"
}

make_stub() {
  local name="$1"
  shift
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' "$@"
  } >"$BIN_DIR/$name"
  chmod +x "$BIN_DIR/$name"
}

write_fixture() {
  local path="$1"
  shift
  printf '%s' "$*" >"$path"
}
