#!/usr/bin/env bash
# Claude Code PreToolUse hook on ExitWorktree: refuse `discard_changes: true` on
# a hook-created worktree unless the tree is clean and its branch has landed.
#
# Why the flag needs a guard at all. ExitWorktree's remove refuses every
# worktree worktree-create-hook.sh makes with "Could not verify worktree state",
# because a hook-based session records no start commit, so the flag is the only
# way through, and it skips the harness check entirely (#980). Passing it becomes
# a reflex. Behind it, worktree-remove.sh still refuses a dirty tree, but that
# refusal is invisible: ExitWorktree reports "removed" for a worktree the hook
# kept. This hook runs the checks the teardown reference asks for before the
# flag, and denies with the reason where the model can read it.
#
# Why here and not in input rewriting: the refusal is the tool's input
# validation, which runs before any PreToolUse hook, so a hook cannot add the
# flag. With the flag already set, validation passes and this hook sees the call.
#
# Scope: only a worktree the create hook stamped for this session. Anything
# else is the harness's own business, and a call without the flag is left to
# the harness check. Every pass is silent; only a deny prints.
#
# The worktree is read from the payload's `cwd`, which in a worktree session is
# the worktree. That holds for WorktreeRemove (verified); for PreToolUse on
# ExitWorktree it is unverified until the first end-to-end run.
#
# Covered by test/exit-worktree-guard.bats.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)

payload=$(python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
if not isinstance(d, dict):
    d = {}
t = d.get("tool_input") or {}
if not isinstance(t, dict):
    t = {}
print(d.get("tool_name") or "")
print(t.get("action") or "")
print("1" if t.get("discard_changes") is True else "")
for k in ("session_id", "cwd"):
    print(str(d.get(k) or "").replace("\n", " "))
')
tool=$(sed -n 1p <<<"$payload")
action=$(sed -n 2p <<<"$payload")
discard=$(sed -n 3p <<<"$payload")
session=$(sed -n 4p <<<"$payload")
cwd=$(sed -n 5p <<<"$payload")

if [[ "$tool" != ExitWorktree || "$action" != remove || -z "$discard" ]]; then
  exit 0
fi

deny() {
  python3 -c '
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": sys.argv[1],
}}))' "$1"
  exit 0
}

real=$(cd -- "${cwd:-$PWD}" 2>/dev/null && pwd -P) || true
[[ -n "$real" ]] || exit 0
top=$(git -C "$real" rev-parse --show-toplevel 2>/dev/null) || true
gitdir=$(git -C "$real" rev-parse --path-format=absolute --git-dir 2>/dev/null) || true
common=$(git -C "$real" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || true
if [[ -z "$top" || -z "$gitdir" || -z "$common" || "$gitdir" == "$common" ]]; then
  exit 0
fi

# The same stamp worktree-remove-hook.sh gates on. Without it, or with another
# session's id or `shared`, the remove hook keeps the tree anyway.
owner=$(cat -- "$gitdir/claude-session-owner" 2>/dev/null) || true
if [[ -z "$owner" || -z "$session" || "$owner" != "$session" ]]; then
  exit 0
fi

keep="Use ExitWorktree with action \"keep\" instead; a kept worktree costs one scripts/worktree-remove.sh later."

dirty=$(git -C "$top" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [[ "$dirty" != 0 ]]; then
  deny "discard_changes refused: $top has $dirty uncommitted or untracked path(s), which the removal would refuse silently while ExitWorktree reports success. Commit or drop them first. $keep"
fi

branch=$(git -C "$top" symbolic-ref --quiet --short HEAD 2>/dev/null) || true
if [[ -z "$branch" ]]; then
  deny "discard_changes refused: $top is on a detached HEAD, so there is no branch to prove landed. $keep"
fi

root=$(dirname -- "$common")
if ! why=$("$script_dir/branch-remove.sh" --check "$branch" "$root" 2>&1 >/dev/null); then
  why=$(tr '\n' ' ' <<<"$why" | sed 's/  */ /g; s/^ *//; s/ *$//')
  deny "discard_changes refused: branch $branch has not landed on the default branch. branch-remove.sh --check: $why $keep"
fi
exit 0
