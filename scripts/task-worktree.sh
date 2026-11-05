#!/usr/bin/env bash
# Check a task's work branch out in its own worktree AT THE WorktreeCreate
# HOOK'S LAYOUT (<root>/<repo>/<name> on <prefix><name>), so a later
# `/co-review --non-interactive` can reach it.
#
#   scripts/task-worktree.sh --ref <branch> [--remote <name>]
#
# Why the layout matters (#1395). Under /auto-pilot the run worktree's HEAD must
# stay on the run-state branch, so /deliver-task holds the work branch in a
# second worktree. Co-review's working-directory pre-flight then reads
# `foreign`, and scripts/reach-pr-branch.sh routes it by WHERE that worktree
# sits: at the hook's layout it returns `enter-name`, which `EnterWorktree`
# takes with no prompt; anywhere else it returns `enter-path`, which needs an
# approval nobody is there to give, so the unattended review ends before any
# reviewer runs. A worktree placed by this script always yields `enter-name`.
#
# Run from inside the repo (any of its worktrees). Prints exactly one contract
# line to stdout; git chatter goes to stderr:
#
#   TASK_WORKTREE: ok path=<p> name=<n> ref=<ref>               exit 0
#     <ref> is checked out at <p>, the hook's path for name <n>: either it
#     already was, or this script just added the worktree there.
#   TASK_WORKTREE: none reason=not-hook-name ref=<ref>          exit 1
#     <ref> does not carry the configured branch prefix followed by a valid
#     hook name, so no worktree at the hook's layout can hold it. Nothing done.
#   TASK_WORKTREE: none reason=held-elsewhere path=<p> ref=<ref> exit 1
#     <ref> is already checked out at <p>, which is not the hook's path.
#     Nothing done: moving a worktree out from under its owner is not this
#     script's call.
#   TASK_WORKTREE: none reason=path-exists path=<p> ref=<ref>    exit 1
#     The hook's path is taken by something that does not hold <ref>.
#   TASK_WORKTREE: none reason=fetch-failed ref=<ref>            exit 1
#   TASK_WORKTREE: none reason=worktree-add-failed path=<p>      exit 1
#   TASK_WORKTREE: none reason=config-failed                     exit 1
#   2  usage or dependency error
#
# A ref that is not a local branch is fetched with an explicit
# refs/heads/<ref>:refs/heads/<ref> refspec and no --track, for the reason
# scripts/reach-pr-branch.sh gives: tracking writes the main checkout's
# .git/config, which the sandbox denies.
set -uo pipefail

ref=""
remote="origin"

die() {
  echo "task-worktree: $*" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
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
    -h | --help)
      sed -n '2,/^set -uo pipefail/{ /^set /!p; }' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$ref" ] || die "--ref is required"
command -v git >/dev/null 2>&1 || die "git is required but not found in PATH"
git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git repository"

script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)
# shellcheck source=lib/worktree-name.sh
. "$script_dir/lib/worktree-name.sh"
root=$(git rev-parse --show-toplevel) || die "could not resolve the working tree root"

realpath_of() (cd "$1" 2>/dev/null && pwd -P)

prefix=$(cd -- "$root" && "$script_dir/worktree-config.sh" prefix) || {
  echo "TASK_WORKTREE: none reason=config-failed"
  exit 1
}
wt_root=$(cd -- "$root" && "$script_dir/worktree-config.sh" root) || {
  echo "TASK_WORKTREE: none reason=config-failed"
  exit 1
}
common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)
repo=$(basename "$(dirname "$common")")

name=""
case "$ref" in "$prefix"?*) name="${ref#"$prefix"}" ;; esac
if ! worktree_name_valid "$name"; then
  echo "TASK_WORKTREE: none reason=not-hook-name ref=$ref"
  exit 1
fi
path="$wt_root/$repo/$name"

# Who holds the ref now? Same flattening as reach-pr-branch.sh: a
# deleted-but-still-listed (prunable) worktree does not count.
worktrees=$(git worktree list --porcelain 2>&1) || die "git worktree list failed: $worktrees"
holder=$(printf '%s\n' "$worktrees" | awk -v want="$ref" '
  function flush() { if (path != "" && branch == want && prunable == "") { print path; done = 1 } }
  /^worktree /   { if (!done) flush(); path = substr($0, 10); branch = ""; prunable = "" }
  /^branch /     { branch = substr($0, 8); sub(/^refs\/heads\//, "", branch) }
  /^prunable/    { prunable = "1" }
  END            { if (!done) flush() }
')

if [ -n "$holder" ]; then
  holder_real=$(realpath_of "$holder")
  [ -n "$holder_real" ] || holder_real="$holder"
  path_real=$(realpath_of "$path")
  if [ -n "$path_real" ] && [ "$holder_real" = "$path_real" ]; then
    echo "TASK_WORKTREE: ok path=\"$path_real\" name=$name ref=$ref"
    exit 0
  fi
  echo "TASK_WORKTREE: none reason=held-elsewhere path=\"$holder_real\" ref=$ref"
  exit 1
fi

if [ -e "$path" ]; then
  echo "TASK_WORKTREE: none reason=path-exists path=\"$path\" ref=$ref"
  exit 1
fi

if ! git show-ref -q --verify "refs/heads/$ref"; then
  echo "task-worktree: fetching $ref from $remote" >&2
  if ! git fetch "$remote" "refs/heads/$ref:refs/heads/$ref" >&2; then
    echo "TASK_WORKTREE: none reason=fetch-failed ref=$ref"
    exit 1
  fi
fi

if ! mkdir -p "$(dirname "$path")" || ! git -C "$root" worktree add "$path" "$ref" >&2; then
  echo "TASK_WORKTREE: none reason=worktree-add-failed path=\"$path\""
  exit 1
fi

echo "TASK_WORKTREE: ok path=\"$(realpath_of "$path")\" name=$name ref=$ref"
exit 0
