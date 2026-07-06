#!/usr/bin/env bash
# Test harness for scripts/preflight-freshness.sh.
#
# Self-contained and offline: each test builds a real "remote" (a local bare
# repo) plus a clone in a temp dir, arranges the fresh/behind/ahead/diverged
# topology with real commits, runs the fixture, and asserts the exit code and
# structured final line. No network, no stubs.
#
# Run directly: bash scripts/test-preflight-freshness.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/preflight-freshness.sh"

# Sandboxed runs may deny the system temp dir — fall back to a repo-local base.
BASE="$(mktemp -d 2>/dev/null || mktemp -d "$ROOT/.pf-test.XXXXXX")"
trap 'rm -rf "$BASE"' EXIT
mkcase() { mktemp -d "$BASE/case.XXXXXX"; }

pass=0
fail=0

# Fixed identity, and no global/system git config: user-level hooksPath,
# aliases, or commit hooks (e.g. a block-commits-to-main hook) would otherwise
# leak into the throwaway test repos and break setup.
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

# init/clone use --template= : some sandboxes deny .git/hooks writes, and a
# partially-initialized nested repo would make git walk up and mutate the
# ENCLOSING real repo (see in_repo).
G() { git -c init.defaultBranch=main "$@"; }

# in_repo <dir> <sh-snippet> — run git-mutating commands only if <dir> really
# is its own repo root; otherwise git resolves to the enclosing repo and the
# test would commit/push against the real workflow-skills remote.
in_repo() {
  local dir="$1"; shift
  ( cd "$dir" || exit 1
    [ "$(git rev-parse --show-toplevel 2>/dev/null)" = "$(pwd -P)" ] || {
      echo "test-preflight-freshness: $dir is not a repo root, refusing to run: $*" >&2
      exit 1
    }
    sh -c "$1" )
}

# make_repos <dir> — bare "remote" at <dir>/remote.git, clone at <dir>/clone
# with one commit on main pushed to the remote.
make_repos() {
  local dir="$1"
  G init --bare -q --template= "$dir/remote.git" || return 1
  G init -q --template= "$dir/clone" || return 1
  in_repo "$dir/clone" "
    echo one >f && git add f && git commit -qm one &&
    git remote add origin '$dir/remote.git' &&
    git push -q origin main"
}

# remote_commit <dir> — advance the remote's main by one commit via a second,
# throwaway clone (so the primary clone stays behind).
remote_commit() {
  local dir="$1"
  G clone -q --template= "$dir/remote.git" "$dir/other"
  in_repo "$dir/other" \
    "echo more >>f && git add f && git commit -qm more && git push -q origin main"
  rm -rf "$dir/other"
}

# run_case <name> <clone-dir> <expected-code> <expected-final-substr> [args...]
run_case() {
  local name="$1"; shift
  local clone="$1"; shift
  local expected_code="$1"; shift
  local expected_substr="$1"; shift
  local out code
  out="$(cd "$clone" && "$SCRIPT" "$@" 2>&1)"
  code=$?
  local final; final="$(printf '%s\n' "$out" | grep '^FRESHNESS:' | tail -1)"
  if [ "$code" = "$expected_code" ] && printf '%s' "$final" | grep -qF "$expected_substr"; then
    pass=$((pass + 1))
    echo "ok   - $name"
  else
    fail=$((fail + 1))
    echo "FAIL - $name"
    echo "       expected exit=$expected_code final~='$expected_substr'"
    echo "       got      exit=$code      final='$final'"
    printf '%s\n' "$out" | sed 's/^/       | /'
  fi
}

# 1. fresh: clone up to date with the remote → exit 0.
t1="$(mkcase)"
make_repos "$t1"
run_case "up-to-date clone is fresh" "$t1/clone" 0 "FRESHNESS: fresh refs=main"
rm -rf "$t1"

# 2. stale (behind): remote advanced after the clone → exit 1.
t2="$(mkcase)"
make_repos "$t2"
remote_commit "$t2"
run_case "clone behind remote is stale" "$t2/clone" 1 "FRESHNESS: stale refs=main"
rm -rf "$t2"

# 3. ahead: local commit not yet pushed → still fresh (nothing to lose).
t3="$(mkcase)"
make_repos "$t3"
in_repo "$t3/clone" "echo local >>f && git add f && git commit -qm local"
run_case "local ahead of remote is fresh" "$t3/clone" 0 "FRESHNESS: fresh refs=main"
rm -rf "$t3"

# 4. diverged: both sides moved → stale.
t4="$(mkcase)"
make_repos "$t4"
remote_commit "$t4"
in_repo "$t4/clone" "echo local >>f && git add f && git commit -qm local"
run_case "diverged branch is stale" "$t4/clone" 1 "FRESHNESS: stale refs=main"
rm -rf "$t4"

# 5. never-pushed branch: no remote counterpart → skipped, overall fresh.
t5="$(mkcase)"
make_repos "$t5"
in_repo "$t5/clone" "git checkout -qb feature"
run_case "unpushed branch is skipped (fresh)" "$t5/clone" 0 "FRESHNESS: fresh refs=" --ref feature
rm -rf "$t5"

# 6. multi-ref: fresh base but stale current branch → stale names the ref.
t6="$(mkcase)"
make_repos "$t6"
in_repo "$t6/clone" "git checkout -qb feature && git push -q origin feature"
G clone -q --template= "$t6/remote.git" "$t6/other"
in_repo "$t6/other" "git checkout -q feature && echo more >>f && git add f && git commit -qm more && git push -q origin feature"
rm -rf "$t6/other"
run_case "multi-ref reports only the stale ref" "$t6/clone" 1 "FRESHNESS: stale refs=feature" \
  --ref main --ref feature
rm -rf "$t6"

# 7. unreachable remote (deleted path stands in for no network) → unknown, exit 3.
t7="$(mkcase)"
make_repos "$t7"
rm -rf "$t7/remote.git"
run_case "unreachable remote reports unknown" "$t7/clone" 3 "FRESHNESS: unknown reason=ls-remote-failed"
rm -rf "$t7"

echo
echo "preflight-freshness tests: $pass passed, $fail failed"
[ "$fail" = 0 ]
