#!/usr/bin/env bash
# test-tutorial-root-guard.sh — fixture-based tests for
# scripts/tutorial-root-guard.sh.
#
# Builds a fixture git repo under a temp dir (mktemp -d) so nothing here
# touches the real repo, and asserts the guard's four cases:
#   - empty path: exit 1
#   - relative path: exit 1
#   - path inside a fixture repo (the repo root itself, and a subdirectory
#     of it): exit 1, run with cwd inside that repo
#   - path outside the fixture repo: exit 0
#
# Run directly: bash scripts/test-tutorial-root-guard.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/tutorial-root-guard.sh"

# Bare `mktemp -d` (no template) ignores $TMPDIR on macOS, so the first arm
# isn't a real $TMPDIR attempt; try $TMPDIR explicitly before falling back to
# repo-local, where a sandboxed git init can't copy its hook templates.
BASE="$(mktemp -d 2>/dev/null \
  || mktemp -d "${TMPDIR:-/tmp}/tutorial-root-guard-test.XXXXXX" 2>/dev/null \
  || mktemp -d "$ROOT/.tutorial-root-guard-test.XXXXXX")"
# Fail closed: an empty BASE would make the `cd` below a no-op (bash `cd ""`
# exits 0), leaving BASE pointing at the repo root for the EXIT trap to delete.
[ -n "$BASE" ] && [ -d "$BASE" ] || {
  echo "test-tutorial-root-guard: could not create a temp dir" >&2
  exit 2
}
# Canonicalize to the physical path (mktemp -d can land under macOS's
# /var -> /private/var symlink) and stop git's upward repo-discovery walk at
# BASE, so a git op inside a fixture dir can never resolve to the caller's
# repo when the mktemp fallback above lands BASE inside this checkout.
BASE="$(cd "$BASE" && pwd -P)" || exit 2
trap 'rm -rf "$BASE"' EXIT
export GIT_CEILING_DIRECTORIES="$BASE"

# A developer's global/system git config leaks into these fixture repos too:
# core.hooksPath (whose pre-commit hook blocks commits to main, and git init
# names the initial branch main) can silently veto fixture commits, and
# init.templateDir/commit.gpgsign/aliases are other injection routes. Pin the
# config env instead of nulling it, so `git init` still deterministically
# produces branch "main" on stock upstream git.
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT \
  GIT_OBJECT_DIRECTORY GIT_DIR GIT_WORK_TREE GIT_IMPLICIT_WORK_TREE GIT_GRAFT_FILE \
  GIT_INDEX_FILE GIT_NO_REPLACE_OBJECTS GIT_REPLACE_REF_BASE GIT_PREFIX GIT_SHALLOW_FILE \
  GIT_COMMON_DIR GIT_TEMPLATE_DIR \
  GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_AUTHOR_DATE \
  GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE
printf '[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n' >"$BASE/gitconfig"
export GIT_CONFIG_GLOBAL="$BASE/gitconfig"
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

assert_exit() {
  # assert_exit <description> <expected-rc> <actual-rc>
  if [ "$2" = "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected exit $2, got $3)"
  fi
}

# --- Fixture repo -------------------------------------------------------
REPO="$BASE/repo"
mkdir -p "$REPO/sub"
(cd "$REPO" && git init -q)

OUTSIDE="$BASE/outside"
mkdir -p "$OUTSIDE"

# --- Case 1: empty path --------------------------------------------------
"$SCRIPT" "" >/dev/null 2>"$BASE/empty.stderr"
rc1=$?
assert_exit "empty path: exits 1" 1 "$rc1"
if [ -s "$BASE/empty.stderr" ]; then
  ok "empty path: stderr names the failing predicate"
else
  bad "empty path: stderr was empty"
fi

# --- Case 2: relative path -----------------------------------------------
(cd "$OUTSIDE" && "$SCRIPT" "relative/path" >/dev/null 2>"$BASE/relative.stderr")
rc2=$?
assert_exit "relative path: exits 1" 1 "$rc2"
if grep -q "not absolute" "$BASE/relative.stderr"; then
  ok "relative path: stderr names the predicate"
else
  bad "relative path: stderr did not name the predicate: $(cat "$BASE/relative.stderr")"
fi

# --- Case 3: path inside a fixture repo (run with cwd inside it) --------
(cd "$REPO" && "$SCRIPT" "$REPO" >/dev/null 2>"$BASE/inside-root.stderr")
rc3="$?"
assert_exit "path is the fixture repo root: exits 1" 1 "$rc3"

(cd "$REPO/sub" && "$SCRIPT" "$REPO/sub" >/dev/null 2>"$BASE/inside-sub.stderr")
rc3b="$?"
assert_exit "path is a subdirectory of the fixture repo: exits 1" 1 "$rc3b"

# --- Case 4: path outside the fixture repo -------------------------------
(cd "$OUTSIDE" && "$SCRIPT" "$OUTSIDE" >/dev/null 2>"$BASE/outside.stderr")
rc4="$?"
assert_exit "path outside the fixture repo: exits 0" 0 "$rc4"

echo
echo "test-tutorial-root-guard: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-tutorial-root-guard: OK"
