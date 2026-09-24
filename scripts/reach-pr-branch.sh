#!/usr/bin/env bash
# reach-pr-branch.sh — pick and prepare a route to a PR's head branch when
# scripts/preflight-cwd.sh has already run. It handles all three of that
# script's verdicts for a branch (ok, foreign, absent) by turning them into an
# EnterWorktree-ready route, so the caller never has to ask the user.
#
# Usage:
#   scripts/reach-pr-branch.sh --ref <branch> [--remote <name>] [--no-add]
#
#   --ref     the branch to reach. Required.
#   --remote  remote to fetch from if the branch is not local anywhere.
#             Default: origin.
#   --no-add  never create a worktree (the `absent` + unprefixed/invalid-name
#             case, step 5 below). Unattended callers need this: EnterWorktree
#             with a model-supplied path outside .claude/worktrees/ raises an
#             approval prompt nobody can answer, so such a caller must not be
#             left holding a worktree it created but cannot enter. The fetch
#             in step 3 still runs — only the worktree creation is skipped.
#
# Run from inside the repo. Prints exactly one contract line to stdout and
# nothing else there (git/fetch chatter goes to stderr):
#
#   ROUTE: here ref=<ref>                              exit 0
#     preflight-cwd's `ok` case: the ref is already checked out in THIS
#     worktree. Nothing to do.
#   ROUTE: enter-name name=<n> ref=<ref>                exit 0
#     The ref is checked out in some other worktree of this repo (foreign),
#     and that worktree sits exactly at the path and branch the WorktreeCreate
#     hook would compute for name <n> (<root>/<repo>/<n> on <prefix><n>). Call
#     EnterWorktree with name=<n> — the hook hands an existing worktree at that
#     path back as-is, no prompt, rather than erroring. Also reached with no
#     `foreign` worktree at all: the ref has this repo's branch prefix and a
#     valid hook name locally, so EnterWorktree with name=<n> makes the hook
#     create it fresh. Do NOT create a worktree yourself in either case.
#   ROUTE: enter-path path=<p> ref=<ref>                exit 0
#     Either the ref is checked out in a foreign worktree at <p> that does NOT
#     match the hook's layout (no prefix, or the remainder is not a valid hook
#     name) — nothing was created, <p> is just named; or the ref was absent
#     everywhere and this script created a worktree itself at <p>. Call
#     EnterWorktree with path=<p>.
#   ROUTE: none reason=fetch-failed ref=<ref>           exit 1
#   ROUTE: none reason=path-exists path=<p>             exit 1
#   ROUTE: none reason=worktree-add-failed path=<p>     exit 1
#   ROUTE: none reason=config-failed                    exit 1
#   ROUTE: none reason=needs-path path=<p> ref=<ref>    exit 1
#     --no-add's version of step 5: the absent ref has no usable prefixed
#     name, and this script was told not to create anything. <p> is where a
#     worktree WOULD go; nothing was created there.
#   2  usage or dependency error
#
# The fetch (step 3, only reached when the ref is absent everywhere) uses an
# explicit refs/heads/<ref>:refs/heads/<ref> refspec rather than a plain
# `git fetch <remote> <ref>` or `git branch --track`: either of those writes a
# `[branch "…"]` remote-tracking stanza into this checkout's .git/config, a
# path the sandbox denies — see scripts/worktree-create-hook.sh for the same
# constraint on the worktree it creates.
set -uo pipefail

ref=""
remote="origin"
no_add=0

die() {
  echo "reach-pr-branch: $*" >&2
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
    --no-add)
      no_add=1
      shift
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
root=$(git rev-parse --show-toplevel) || die "could not resolve the working tree root"

realpath_of() (cd "$1" 2>/dev/null && pwd -P)
cwd_top=$(realpath_of "$root")

prefix=$(cd -- "$root" && "$script_dir/worktree-config.sh" prefix) || {
  echo "ROUTE: none reason=config-failed"
  exit 1
}
wt_root=$(cd -- "$root" && "$script_dir/worktree-config.sh" root) || {
  echo "ROUTE: none reason=config-failed"
  exit 1
}
common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)
repo=$(basename "$(dirname "$common")")

is_valid_name() { # value
  [ -n "$1" ] && [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] && [[ "$1" != *..* ]]
}

prefixed_remainder() { # ref
  case "$1" in
    "$prefix"?*) printf '%s\n' "${1#"$prefix"}" ;;
  esac
}

# Same block-flattening as preflight-cwd.sh: one "<path>\t<branch>\t<prunable>"
# record per worktree, so a deleted-but-still-listed worktree does not win.
worktrees=$(git worktree list --porcelain 2>&1) || die "git worktree list failed: $worktrees"
records=$(printf '%s\n' "$worktrees" | awk '
  function flush() { if (path != "") print path "\t" branch "\t" prunable }
  /^worktree /   { flush(); path = substr($0, 10); branch = ""; prunable = "" }
  /^branch /     { branch = substr($0, 8); sub(/^refs\/heads\//, "", branch) }
  /^prunable/    { prunable = "1" }
  END            { flush() }
')

holder=""
while IFS="$(printf '\t')" read -r wt_path wt_branch wt_prunable; do
  [ -n "$wt_path" ] || continue
  if [ -z "$wt_prunable" ] && [ -n "$wt_branch" ] && [ "$wt_branch" = "$ref" ] && [ -z "$holder" ]; then
    holder="$wt_path"
  fi
done <<EOF
$records
EOF

if [ -n "$holder" ]; then
  holder_real=$(realpath_of "$holder")
  [ -n "$holder_real" ] || holder_real="$holder"

  if [ "$holder_real" = "$cwd_top" ]; then
    echo "ROUTE: here ref=$ref"
    exit 0
  fi

  remainder=$(prefixed_remainder "$ref")
  expected_real=""
  if is_valid_name "$remainder"; then
    expected_real=$(realpath_of "$wt_root/$repo/$remainder")
  fi

  if [ -n "$expected_real" ] && [ "$expected_real" = "$holder_real" ]; then
    echo "ROUTE: enter-name name=$remainder ref=$ref"
    exit 0
  fi

  echo "ROUTE: enter-path path=\"$holder_real\" ref=$ref"
  exit 0
fi

if ! git show-ref -q --verify "refs/heads/$ref"; then
  echo "reach-pr-branch: fetching $ref from $remote" >&2
  if ! git fetch "$remote" "refs/heads/$ref:refs/heads/$ref"; then
    echo "ROUTE: none reason=fetch-failed ref=$ref"
    exit 1
  fi
fi

remainder=$(prefixed_remainder "$ref")
if is_valid_name "$remainder"; then
  echo "ROUTE: enter-name name=$remainder ref=$ref"
  exit 0
fi

# A name derived from the ref for the fallback path only — it need not be a
# valid hook name, since this script (not the hook) creates the worktree here.
name=$(printf '%s' "$ref" | sed -e 's/[^A-Za-z0-9._-]/-/g' -e 's/^[^A-Za-z0-9]*//')
[ -n "$name" ] || name="branch"
path="$wt_root/$repo/$name"

if [ "$no_add" = 1 ]; then
  echo "ROUTE: none reason=needs-path path=\"$path\" ref=$ref"
  exit 1
fi

if [ -e "$path" ]; then
  echo "ROUTE: none reason=path-exists path=\"$path\""
  exit 1
fi

mkdir -p "$(dirname "$path")" || {
  echo "ROUTE: none reason=worktree-add-failed path=\"$path\""
  exit 1
}
if ! git -C "$root" worktree add "$path" "$ref" >&2; then
  echo "ROUTE: none reason=worktree-add-failed path=\"$path\""
  exit 1
fi

echo "ROUTE: enter-path path=\"$path\" ref=$ref"
exit 0
