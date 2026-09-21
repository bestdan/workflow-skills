#!/usr/bin/env bash
# preflight-cwd.sh — check that the session is STANDING IN the tree it is
# about to read and write, before a flow that does either starts.
#
# Third of the co-review pre-flights. preflight-freshness.sh asks whether a
# branch is behind its remote; preflight-conflict.sh asks whether it conflicts
# with its base. Both are about the *branch*. This one is about the *working
# directory*: is the branch under review checked out somewhere other than here?
#
# The failure it replaces is silent in both directions. Reading: the reconciler
# and every repo-reading step resolve against the invoking checkout, so a
# review of a worktree branch judges findings against main's tree. Writing: a
# run that reaches into the worktree with `cd <path> && …` or `git -C <path>`
# edits, commits and pushes to a tree the user's shell, editor, prompt and
# build are all pointed away from — so their verification verifies nothing and
# their editor shows stale content, with no shared signal that anything is off.
#
# Read-only and local: it runs `git worktree list` and `git rev-parse`, no
# network, no fetch, nothing mutated. Safe to run sandboxed.
#
# Usage:
#   scripts/preflight-cwd.sh [--ref <branch>]
#
#   --ref  Branch the flow is about to read/write. Omit it for inventory only.
#          It does NOT default to the current branch the way the siblings' --ref
#          does: "is my current branch stale / conflicting" is a real question,
#          but "is my current branch checked out here" is a tautology that can
#          only answer ok. So a defaulted ref would check nothing and would
#          additionally make a detached HEAD an error in the one mode (--local)
#          that has no branch to name.
#
# Verdicts (exit status and structured final line, mirroring the sibling
# pre-flights):
#   0  CWD: ok ref=<b> cwd=<path>
#      The ref is checked out right here. Proceed.
#   0  CWD: ok ref=none cwd=<path> head=<branch-or-DETACHED>
#      No --ref was given, so nothing was checked: the inventory is the answer,
#      and head= names what this tree is on. A detached HEAD is fine here.
#   1  CWD: foreign ref=<b> worktree=<path> cwd=<path> hint="enter <path> and re-run"
#      The ref is checked out in a DIFFERENT worktree of this repo. Enter it —
#      do not reach into it.
#   1  CWD: absent ref=<b> cwd=<path> head=<branch-or-DETACHED>
#      The ref is checked out in no worktree at all, so this tree is on
#      something else. Check it out here (or enter a tree that has it) first.
#   2  usage or dependency error
#   3  CWD: unknown reason=worktree-list-failed
#      `git worktree list` failed — callers should warn and let the user
#      decide, not hard-fail.
#
# Every run also prints one `preflight-cwd: worktree …` line per worktree of
# this repo, marking the session's own with `<- cwd`. That inventory is the
# point in a mode with no ref to check (`--local`): it names the tree the flow
# is standing in and shows the siblings it is not, which is what makes a
# review of the wrong tree visible in a run summary.
set -uo pipefail

ref=""

die() {
  echo "preflight-cwd: $*" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ref)
      [ $# -ge 2 ] || die "missing value for --ref"
      ref="$2"
      shift 2
      ;;
    -h | --help)
      sed -n '2,46p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

command -v git >/dev/null 2>&1 || die "git is required but not found in PATH"
git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git repository"

# Resolve physical paths on both sides of every comparison. macOS puts the
# usual scratch roots behind a /tmp -> /private/tmp symlink, and `git worktree
# list` and `git rev-parse --show-toplevel` do not always agree on which form
# they print — comparing the raw strings reports the session's own worktree as
# foreign.
realpath_of() (cd "$1" 2>/dev/null && pwd -P)

cwd_top="$(git rev-parse --show-toplevel)" || die "could not resolve the working tree root"
cwd_top="$(realpath_of "$cwd_top")"

if ! worktrees="$(git worktree list --porcelain 2>&1)"; then
  echo "preflight-cwd: git worktree list failed: $worktrees" >&2
  echo "CWD: unknown reason=worktree-list-failed"
  exit 3
fi

head_branch="$(git branch --show-current)"

# `git worktree list --porcelain` emits a blank-line-separated block per
# worktree: a `worktree <path>` line, a `HEAD <sha>` line, and then either
# `branch refs/heads/<name>` or `detached`. A worktree whose directory has been
# deleted is still listed, with its branch, plus a `prunable <reason>` line —
# so the `prunable` flag has to be carried through. Without it the stale record
# wins the holder lookup and the caller is told to enter a path that no longer
# exists, which is the worst answer this script can give. Flatten each block to
# one "<path>\t<branch>\t<prunable>" record; a detached worktree gets an empty
# branch field.
records="$(printf '%s\n' "$worktrees" | awk '
  function flush() { if (path != "") print path "\t" branch "\t" prunable }
  /^worktree /   { flush(); path = substr($0, 10); branch = ""; prunable = "" }
  /^branch /     { branch = substr($0, 8); sub(/^refs\/heads\//, "", branch) }
  /^prunable/    { prunable = "1" }
  END            { flush() }
')"

# Print the inventory, and find the worktree holding the ref. Both in one pass
# so the physical-path resolution is paid once per worktree.
holder=""
while IFS="$(printf '\t')" read -r wt_path wt_branch wt_prunable; do
  [ -n "$wt_path" ] || continue
  wt_real="$(realpath_of "$wt_path")"
  [ -n "$wt_real" ] || wt_real="$wt_path"
  marker=""
  [ "$wt_real" = "$cwd_top" ] && marker="  <- cwd"
  [ -n "$wt_prunable" ] && marker="  (prunable)$marker"
  echo "preflight-cwd: worktree $wt_real [${wt_branch:-DETACHED}]$marker"
  # A prunable record is inventoried but never wins the holder lookup: its
  # directory is gone, so `foreign` would name an unenterable path. Letting it
  # fall through to `absent` is the honest verdict.
  if [ -n "$ref" ] && [ -n "$wt_branch" ] && [ "$wt_branch" = "$ref" ] && [ -z "$wt_prunable" ] && [ -z "$holder" ]; then
    holder="$wt_real"
  fi
done <<EOF
$records
EOF

# No ref to check: the inventory above is the whole answer. This is the
# `--local` shape, where the flow reviews whatever tree it stands in rather
# than a named branch — so a detached HEAD is a valid input, not an error.
if [ -z "$ref" ]; then
  echo "preflight-cwd: no ref to check — inventory only (standing in $cwd_top on ${head_branch:-DETACHED})"
  echo "CWD: ok ref=none cwd=\"$cwd_top\" head=${head_branch:-DETACHED}"
  exit 0
fi

if [ -z "$holder" ]; then
  echo "preflight-cwd: $ref — ABSENT (checked out in no worktree of this repo)" >&2
  echo "CWD: absent ref=$ref cwd=\"$cwd_top\" head=${head_branch:-DETACHED}"
  exit 1
fi

if [ "$holder" != "$cwd_top" ]; then
  echo "preflight-cwd: $ref — FOREIGN (checked out at $holder, not here)" >&2
  echo "CWD: foreign ref=$ref worktree=\"$holder\" cwd=\"$cwd_top\" hint=\"enter $holder and re-run\""
  exit 1
fi

echo "preflight-cwd: $ref — ok (checked out here)"
echo "CWD: ok ref=$ref cwd=\"$cwd_top\""
exit 0
