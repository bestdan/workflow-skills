#!/usr/bin/env bash
# test-ci-docs-only.sh — fixture-based tests for scripts/ci-docs-only.sh.
#
# Each case commits a change on top of a base commit in a fresh `git init`
# fixture and asserts the one line the classifier prints. The cases that
# matter most are the fail-safe ones: an empty diff, an unknown ref, a shell
# file under dev_docs/, and a rename out of scripts/ into dev_docs/ must all
# read as code, because a wrong `true` skips CI.
#
# Run directly: bash scripts/test-ci-docs-only.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/ci-docs-only.sh"

BASE="$(mktemp -d "${TMPDIR:-/tmp}/ci-docs-only-test.XXXXXX")" || {
  echo "test-ci-docs-only: could not create a temp dir" >&2
  exit 2
}
trap 'rm -rf "$BASE"' EXIT
export GIT_CEILING_DIRECTORIES="$BASE"

# Pin the fixture's git config so it cannot inherit the developer's global
# config (a global pre-commit hook refusing commits to main would trip here).
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_SYSTEM=/dev/null

fail=0
pass_count=0
fail_count=0

ok() {
  pass_count=$((pass_count + 1))
  echo "  ✔ $1"
}

bad() {
  fail_count=$((fail_count + 1))
  fail=1
  echo "  ✘ $1" >&2
}

gitf() {
  git -C "$fixture" -c user.name=t -c user.email=t@example.com "$@"
}

# new_fixture: a repo with one base commit holding a script and a doc.
new_fixture() {
  fixture="$BASE/$1"
  mkdir -p "$fixture/scripts" "$fixture/dev_docs"
  git init -q -b main "$fixture"
  echo 'echo hi' >"$fixture/scripts/foo.sh"
  echo '# doc' >"$fixture/dev_docs/doc.md"
  gitf add -A
  gitf commit -q -m base
}

commit_all() {
  gitf add -A
  gitf commit -q --allow-empty -m change
}

# expect <description> <expected line> [base] [head]
expect() {
  local got
  got="$(cd "$fixture" && "$SCRIPT" "${3:-HEAD^1}" "${4:-HEAD}" 2>/dev/null)"
  if [ "$got" = "$2" ]; then
    ok "$1"
  else
    bad "$1 (expected '$2', got '$got')"
  fi
}

new_fixture docs
echo more >>"$fixture/dev_docs/doc.md"
echo '{}' >"$fixture/dev_docs/new.json"
commit_all
expect "dev_docs-only change is docs" "docs_only=true"

new_fixture code
echo more >>"$fixture/dev_docs/doc.md"
echo more >>"$fixture/scripts/foo.sh"
commit_all
expect "a change outside dev_docs/ is code" "docs_only=false"

new_fixture shell
echo 'echo x' >"$fixture/dev_docs/probe.sh"
commit_all
expect "a shell file under dev_docs/ is code" "docs_only=false"

new_fixture bats
echo '@test x { :; }' >"$fixture/dev_docs/probe.bats"
commit_all
expect "a bats file under dev_docs/ is code" "docs_only=false"

new_fixture rename
gitf mv scripts/foo.sh dev_docs/foo.md
commit_all
expect "a rename out of scripts/ into dev_docs/ is code" "docs_only=false"

new_fixture empty
commit_all
expect "an empty diff is code, not vacuously docs" "docs_only=false"

new_fixture badref
expect "an unknown base ref is code" "docs_only=false" deadbeef HEAD

new_fixture prefix
mkdir -p "$fixture/dev_docs_extra"
echo x >"$fixture/dev_docs_extra/a.md"
commit_all
expect "a sibling directory sharing the prefix is code" "docs_only=false"

echo "test-ci-docs-only: $pass_count passed, $fail_count failed"
exit "$fail"
