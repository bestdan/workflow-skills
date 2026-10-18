#!/usr/bin/env bash
# worktree-name-for.sh — the EnterWorktree `name` that hands back the worktree
# at <path>, if there is one.
#
# Usage:
#   scripts/worktree-name-for.sh <path>
#
# Run from inside the repo. `EnterWorktree` with `name: <n>` returns an existing
# worktree with no prompt only when it sits where the WorktreeCreate hook
# (scripts/worktree-create-hook.sh) would put it: <root>/<repo>/<n> on branch
# <prefix><n>. This script answers whether <path> is such a worktree, so every
# caller that needs the answer asks one place instead of keeping a copy.
#
# <path> has a name only when all of these hold:
#   - it is a registered, non-prunable worktree (`git worktree list`);
#   - its common git dir is the cwd's. The hook resolves <repo> from the cwd,
#     so a worktree of another repo would get a name that creates a new
#     worktree in the wrong repo;
#   - its branch is <prefix><n> and <n> passes the hook's own name rule
#     (scripts/lib/worktree-name.sh);
#   - its real path is the real path of <root>/<repo>/<n>.
# <root> and <prefix> come from scripts/worktree-config.sh, read from the repo
# root exactly as the hook reads them.
#
# Exit:
#   0  prints <n>, and nothing else, on stdout
#   1  <path> has no name; prints nothing
#   2  usage, dependency, or config error; prints nothing on stdout
set -uo pipefail

die() {
  echo "worktree-name-for: $*" >&2
  exit 2
}

[ $# -eq 1 ] && [ -n "$1" ] || die "usage: worktree-name-for.sh <path>"
target="$1"

command -v git >/dev/null 2>&1 || die "git is required but not found in PATH"
script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)
# shellcheck source=lib/worktree-name.sh
. "$script_dir/lib/worktree-name.sh"

top=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git repository"
common=$(git -C "$top" rev-parse --path-format=absolute --git-common-dir) \
  || die "could not resolve the common git dir"
repo=$(basename "$(dirname "$common")")

prefix=$(cd -- "$top" && "$script_dir/worktree-config.sh" prefix) \
  || die "could not resolve the branch prefix"
wt_root=$(cd -- "$top" && "$script_dir/worktree-config.sh" root) \
  || die "could not resolve the worktree root"

realpath_of() (cd "$1" 2>/dev/null && pwd -P)

target_real=$(realpath_of "$target")
[ -n "$target_real" ] || exit 1

# One "<path>\t<branch>\t<prunable>" record per worktree, so a deleted-but-
# still-listed worktree cannot match.
worktrees=$(git worktree list --porcelain 2>&1) || die "git worktree list failed: $worktrees"
records=$(printf '%s\n' "$worktrees" | awk '
  function flush() { if (path != "") print path "\t" branch "\t" prunable }
  /^worktree /   { flush(); path = substr($0, 10); branch = ""; prunable = "" }
  /^branch /     { branch = substr($0, 8); sub(/^refs\/heads\//, "", branch) }
  /^prunable/    { prunable = "1" }
  END            { flush() }
')

branch=""
found=0
while IFS="$(printf '\t')" read -r wt_path wt_branch wt_prunable; do
  [ -n "$wt_path" ] || continue
  [ "$(realpath_of "$wt_path")" = "$target_real" ] || continue
  found=1
  [ -z "$wt_prunable" ] && branch="$wt_branch"
  break
done <<EOF
$records
EOF

[ "$found" = 1 ] && [ -n "$branch" ] || exit 1

wt_common=$(git -C "$target_real" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 1
[ "$(realpath_of "$wt_common")" = "$(realpath_of "$common")" ] || exit 1

case "$branch" in
  "$prefix"?*) name="${branch#"$prefix"}" ;;
  *) exit 1 ;;
esac
worktree_name_valid "$name" || exit 1

expected_real=$(realpath_of "$wt_root/$repo/$name")
[ -n "$expected_real" ] && [ "$expected_real" = "$target_real" ] || exit 1

printf '%s\n' "$name"
exit 0
