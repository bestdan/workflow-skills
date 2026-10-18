# shellcheck shell=bash
# The one definition of a worktree name the WorktreeCreate hook accepts.
#
# Sourced by scripts/worktree-create-hook.sh, which enforces it on the name the
# model supplies, and by every script that has to predict what the hook would
# do with a name — scripts/worktree-name-for.sh and scripts/reach-pr-branch.sh.
# A second copy of the rule in a predictor drifts from the hook silently: the
# predictor then answers `name` for a value the hook refuses, or `path` for one
# it would accept.
#
# The name lands in a path and in a branch name, so it is one path segment,
# nothing git would reject, and nothing that climbs out of the parent.
worktree_name_valid() { # name
  [[ -n "$1" && "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && "$1" != *..* ]]
}
