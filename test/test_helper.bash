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
  # fixture `git add` calls the caller's repo/index. Unset the lot so no
  # inherited env can redirect a fixture git op the same way.
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_TEMPLATE_DIR GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT
  printf '[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n' >"$TEST_TMPDIR/gitconfig"
  export GIT_CONFIG_GLOBAL="$TEST_TMPDIR/gitconfig"
  export GIT_CONFIG_SYSTEM=/dev/null
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
