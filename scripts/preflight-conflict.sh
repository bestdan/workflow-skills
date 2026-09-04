#!/usr/bin/env bash
# preflight-conflict.sh — check whether a branch would conflict with its base
# before a flow that depends on a clean merge starts.
#
# Local twin of preflight-freshness.sh: freshness only compares a branch to
# its own remote tip and cannot see a base conflict at all. This script
# answers the different question — would merging this branch into its base
# produce conflicts — using local git only, no `gh`, no network beyond a
# fetch of the base.
#
# Read-only on the working tree, index, and current branch: the only write is
# the fetch of the base, which updates FETCH_HEAD / remote-tracking refs.
#
# Usage:
#   scripts/preflight-conflict.sh [--base <branch>] [--ref <branch>] [--remote <name>] [--no-fetch]
#
#   --base     Base branch to test the merge against. Default: main.
#   --ref      Branch/commit to test. Default: the current branch (HEAD).
#   --remote   Remote to fetch the base from. Default: origin.
#   --no-fetch Skip the fetch and use whatever base tip is already local.
#
# Exit status and structured final line (parseable, mirrors preflight-freshness):
#   0  CONFLICT: clean base=<base> ref=<ref>                        merges cleanly
#   1  CONFLICT: conflicting base=<base> ref=<ref> paths=<csv> hint="git rebase <base>"
#   2  usage or dependency error
#   3  CONFLICT: unknown reason=<merge-tree-unsupported|fetch-failed|no-base-tip|merge-tree-failed>
#      offline/sandboxed/unsupported git — callers should warn and proceed,
#      not treat this as a conflict verdict
set -uo pipefail

base="main"
ref=""
remote="origin"
do_fetch=1

die() {
  echo "preflight-conflict: $*" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --base)
      [ $# -ge 2 ] || die "missing value for --base"
      base="$2"
      shift 2
      ;;
    --ref)
      [ $# -ge 2 ] || die "missing value for --ref"
      ref="$2"
      shift 2
      ;;
    --remote)
      [ $# -ge 2 ] || die "missing value for --remote"
      remote="$2"
      shift 2
      ;;
    --no-fetch)
      do_fetch=0
      shift
      ;;
    -h | --help)
      sed -n '2,35p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

command -v git >/dev/null 2>&1 || die "git is required but not found in PATH"
git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git repository"
if [ "$do_fetch" -eq 1 ]; then
  git remote get-url "$remote" >/dev/null 2>&1 || die "remote '$remote' is not configured"
fi

# git merge-tree --write-tree needs git >= 2.38. Parse the version rather than
# feature-probing by running it against real refs.
git_version="$(git --version | awk '{print $3}')"
git_major="${git_version%%.*}"
git_rest="${git_version#*.}"
git_minor="${git_rest%%.*}"
if [ "$git_major" -lt 2 ] || { [ "$git_major" -eq 2 ] && [ "$git_minor" -lt 38 ]; }; then
  echo "preflight-conflict: git $git_version is too old (need >= 2.38 for merge-tree --write-tree)" >&2
  echo "CONFLICT: unknown reason=merge-tree-unsupported"
  exit 3
fi

if [ -z "$ref" ]; then
  ref="$(git branch --show-current)"
  [ -n "$ref" ] || die "detached HEAD and no --ref given"
fi

if [ "$do_fetch" -eq 1 ]; then
  if ! fetch_err="$(git fetch --quiet "$remote" "$base" 2>&1)"; then
    echo "preflight-conflict: git fetch failed: $fetch_err" >&2
    echo "CONFLICT: unknown reason=fetch-failed"
    exit 3
  fi
  base_commit="$(git rev-parse --verify --quiet FETCH_HEAD)"
  [ -n "$base_commit" ] || {
    echo "preflight-conflict: FETCH_HEAD did not resolve after fetching $base from $remote" >&2
    echo "CONFLICT: unknown reason=fetch-failed"
    exit 3
  }
else
  base_commit="$(git rev-parse --verify --quiet "refs/remotes/$remote/$base")"
  if [ -z "$base_commit" ]; then
    base_commit="$(git rev-parse --verify --quiet "refs/heads/$base")"
  fi
  [ -n "$base_commit" ] || {
    echo "preflight-conflict: no local base tip for '$base' (tried refs/remotes/$remote/$base and refs/heads/$base)" >&2
    echo "CONFLICT: unknown reason=no-base-tip"
    exit 3
  }
fi

ref_commit="$(git rev-parse --verify --quiet "$ref^{commit}")" || die "ref '$ref' does not resolve to a commit"

git merge-tree --write-tree --quiet "$base_commit" "$ref_commit" >/dev/null 2>&1
merge_status=$?

case "$merge_status" in
  0)
    echo "preflight-conflict: $ref — clean merge into $base"
    echo "CONFLICT: clean base=$base ref=$ref"
    exit 0
    ;;
  1)
    paths="$(git merge-tree --name-only --no-messages --write-tree "$base_commit" "$ref_commit" | tail -n +2)"
    csv="$(printf '%s\n' "$paths" | tr '\n' ',' | sed 's/,$//')"
    echo "preflight-conflict: $ref — CONFLICTS with $base: $csv"
    echo "CONFLICT: conflicting base=$base ref=$ref paths=$csv hint=\"git rebase $base\""
    exit 1
    ;;
  *)
    echo "preflight-conflict: git merge-tree exited $merge_status (not 0 or 1)" >&2
    echo "CONFLICT: unknown reason=merge-tree-failed"
    exit 3
    ;;
esac
