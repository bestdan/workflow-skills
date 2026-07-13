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
