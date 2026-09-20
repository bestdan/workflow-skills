#!/usr/bin/env bash
# Claude Code WorktreeCreate hook: create the worktree where this plugin's
# worktree config says worktrees live, from the LOCAL default branch, and hand
# the path back to the harness.
#
# Why a hook and not the two-call recipe (`git worktree add` + `EnterWorktree`
# with `path`): the harness never auto-approves `EnterWorktree` with a
# model-supplied path outside .claude/worktrees/ — no allow rule lifts that
# prompt — and a prompt is a hard stop in a remote-control session. A worktree
# the hook creates is not model-supplied, so `EnterWorktree` with `name` runs
# with no prompt. Verified 2026-09-04, Claude Code 2.1.260, headless with
# prompts auto-denied: the path form was denied, the hook form ran.
#
# Why a LOCAL start point and not origin/<default>: a remote-tracking start
# point makes git record upstream tracking in the main checkout's .git/config,
# which the sandbox denies as a protected path that no allowWrite entry can
# lift. A local start point writes nothing there, so the add runs sandboxed.
# The branch has no upstream, so the first push is `git push -u origin HEAD`.
#
# The harness's default location is not an option in a repo that versions its
# own agent config: the checkout under .claude/worktrees/ recreates the
# session's protected relative paths and the sandbox aborts it with
# `Operation not permitted`, leaving no worktree at all.
#
# Where the worktree lands and what its branch is called are NOT hardcoded —
# both come from scripts/worktree-config.sh, because both are one person's
# convention rather than a fact about worktrees and this plugin installs
# anywhere. See that script for the precedence.
#
# Fires for every worktree the harness creates — EnterWorktree, --worktree,
# `isolation: worktree` subagents, background sessions. Any non-zero exit
# aborts the creation; stdout must be the worktree path and nothing else.
# A hook replaces the built-in creation wholesale, so `.worktreeinclude` is
# not applied: untracked files such as .env are not copied into the worktree.
#
# A re-used name is not an error. scripts/worktree-remove.sh keeps the branch
# when no merged PR vouches for it, so the branch outliving its directory is
# the routine case: re-attach it. A worktree already registered at the path on
# that branch is handed back as-is.
#
# Covered by test/worktree-create-hook.bats.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)

# The payload, in one pass. Fields the harness may omit come back empty rather
# than as the string "None", and malformed JSON fails here — under `set -e`
# that aborts the creation, which is the right answer for a payload nobody can
# read. Python rather than jq: this plugin's helpers already require python3
# and do not require jq.
payload=$(python3 -c '
import json, sys
d = json.load(sys.stdin)
for k in ("name", "cwd", "session_id"):
    v = d.get(k) or ""
    print(str(v).replace("\n", " "))
')
name=$(sed -n 1p <<<"$payload")
cwd=$(sed -n 2p <<<"$payload")
session=$(sed -n 3p <<<"$payload")
[[ -n "$cwd" ]] || cwd=$PWD

# The name is model-supplied and lands in a path and a branch name. One path
# segment, nothing git would reject, nothing that climbs out of the parent.
if [[ -z "$name" || ! "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ || "$name" == *..* ]]; then
  printf 'worktree-create: refusing worktree name %q\n' "$name" >&2
  exit 1
fi

root=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || {
  printf 'worktree-create: %s is not inside a git repository\n' "$cwd" >&2
  exit 1
}
# Worktrees of one repo share the common dir; name the parent after the main
# checkout, not after whichever worktree the session happens to be in.
common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)
repo=$(basename "$(dirname "$common")")

# The resolver reads the repo's own config, so it runs with the repo as its
# cwd. A resolver that dies (a set-but-invalid value) takes the creation down
# with it rather than resolving somewhere else — see worktree-config.sh.
parent=$(cd -- "$root" && "$script_dir/worktree-config.sh" root) || {
  printf 'worktree-create: could not resolve the worktree root\n' >&2
  exit 1
}
prefix=$(cd -- "$root" && "$script_dir/worktree-config.sh" prefix) || {
  printf 'worktree-create: could not resolve the branch prefix\n' >&2
  exit 1
}

# The local default branch. origin/HEAD names it when the remote was cloned
# normally; a repo with no remote gets `main`.
base=$(git -C "$root" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null || true)
base=${base#origin/}
: "${base:=main}"
git -C "$root" show-ref -q --verify "refs/heads/$base" || {
  printf 'worktree-create: no local branch %s to start from\n' "$base" >&2
  exit 1
}

# Ownership marker for worktree-remove-hook.sh, written at two call sites with
# two different meanings. Creating a worktree is a claim: nobody else can be in
# a directory that did not exist a moment ago, so the creating session owns it.
# Entering one that already exists is not a claim, and must never become one —
# an existing worktree may hold a live session, and a marker naming the newest
# enterer would let that session's exit delete the other's checkout.
#
# So the hand-back path only ever weakens ownership: a worktree two different
# sessions have entered is stamped the sticky literal `shared`, which matches no
# session, so the remove hook keeps it whichever way the two exit. A worktree
# carrying no marker predates this scheme and is left unmarked, which the remove
# hook also keeps. The cost either way is one manual
# `scripts/worktree-remove.sh`.
stamp_new_owner() { # dir — the hook created it in this invocation
  local gd
  [[ -n "$session" ]] || return 0
  gd=$(git -C "$1" rev-parse --path-format=absolute --git-dir 2>/dev/null) || return 0
  printf '%s\n' "$session" >"$gd/claude-session-owner" 2>/dev/null || true
}

mark_handback() { # dir — the hook handed an existing worktree back
  local gd existing
  [[ -n "$session" ]] || return 0
  gd=$(git -C "$1" rev-parse --path-format=absolute --git-dir 2>/dev/null) || return 0
  existing=$(cat -- "$gd/claude-session-owner" 2>/dev/null) || true
  # Unmarked stays unmarked, and this session's own re-entry changes nothing.
  [[ -n "$existing" && "$existing" != "$session" ]] || return 0
  printf 'shared\n' >"$gd/claude-session-owner" 2>/dev/null || true
}

dir="$parent/$repo/$name"
branch="$prefix$name"
if [[ -e "$dir" ]]; then
  # Same repo and same branch, or it is not the worktree this name means.
  dir_common=$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
  dir_branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
  if [[ "$dir_common" == "$common" && "$dir_branch" == "$branch" ]]; then
    mark_handback "$dir"
    printf '%s\n' "$dir"
    exit 0
  fi
  printf 'worktree-create: %s exists and is not a worktree of this repo on %s\n' "$dir" "$branch" >&2
  exit 1
fi
mkdir -p "$(dirname "$dir")"
# stdout is the contract, so git's own chatter goes to stderr.
if git -C "$root" show-ref -q --verify "refs/heads/$branch"; then
  git -C "$root" worktree add "$dir" "$branch" >&2
else
  git -C "$root" worktree add -b "$branch" "$dir" "$base" >&2
fi
stamp_new_owner "$dir"
printf '%s\n' "$dir"
