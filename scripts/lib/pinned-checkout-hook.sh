#!/usr/bin/env bash
# post-checkout: keep this repo's MAIN checkout on the branch named by
# `hooks.pinnedBranch`, by switching it back whenever another branch is checked
# out there. Installed by workflow-skills' scripts/pin-checkout.sh.
#
# It restores rather than refuses because git has no pre-checkout hook, and a
# `reference-transaction` veto leaves HEAD on the pin with the other branch's
# files in the tree. By the time post-checkout runs git has finished, so a
# plain `git checkout <pin>` leaves the repo consistent.
#
# Bypass one command: env WORKFLOW_SKILLS_ALLOW_HEAD_MOVE=1 git checkout <branch>

# $3 is 0 for a file checkout, which moves no HEAD.
[ "${3-}" = 1 ] || exit 0
[ -z "${WORKFLOW_SKILLS_ALLOW_HEAD_MOVE:-}" ] || exit 0

pinned="$(git config --get hooks.pinnedBranch 2>/dev/null)"
# Empty on a detached HEAD, which is left alone: rebase and bisect detach and
# fire this hook, and yanking HEAD mid-rebase would break them.
current="$(git symbolic-ref --short -q HEAD)"
[ -n "$pinned" ] && [ -n "$current" ] && [ "$current" != "$pinned" ] || exit 0

# Only the main worktree is pinned. A linked worktree's git dir differs from the
# common dir, which also keeps `git worktree add` (it fires this hook) safe.
git_dir="$(git rev-parse --path-format=absolute --git-dir 2>/dev/null)"
common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
[ -n "$git_dir" ] && [ "$git_dir" = "$common_dir" ] || exit 0

# No recursion guard: the restore's own post-checkout finds current == pinned.
if git checkout --quiet "$pinned" 2>/dev/null; then
  {
    echo "This checkout is pinned to '$pinned' — put back on it, after '$current'."
    echo "  Work on '$current' in a worktree instead:"
    echo "    git worktree add <path> $current"
    echo "  To move this checkout anyway: env WORKFLOW_SKILLS_ALLOW_HEAD_MOVE=1 git checkout $current"
    echo "  To unpin it for good:         git config --unset hooks.pinnedBranch"
  } >&2
else
  # The checkout has already happened, so a quiet failure would leave exactly
  # the state the pin exists to prevent.
  {
    echo "This checkout is pinned to '$pinned', but switching back from '$current' FAILED."
    echo "  It is still on '$current'."
    echo "  Resolve whatever blocked the switch, then: git checkout $pinned"
  } >&2
fi
exit 0
