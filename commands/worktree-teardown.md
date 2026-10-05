---
description: Tear down a git worktree, or delete a merged branch, through this plugin's teardown scripts — and the mechanics when a removal fails, refuses, or half-completes, covering the submodule force gates, the PR-evidence branch delete and why a plain `-d` is unsafe in a squash-merging repo, and the half-deleted and locked-worktree recovery cases. Use to remove a worktree no session owns (one a `claude -p` run or a subagent left behind), to delete a merged branch, when deciding whether a branch is safe to delete, or on "working trees containing submodules cannot be moved or removed", "use 'remove -f -f' to override or unlock first", or a retry refused as "contains modified or untracked files".
argument-hint: "[<worktree-path> | --branch <branch> [<repo-root>]]"
allowed-tools:
  - Bash("${CLAUDE_PLUGIN_ROOT}/scripts/worktree-remove.sh":*)
  - Bash("${CLAUDE_PLUGIN_ROOT}/scripts/branch-remove.sh":*)
  - Read
---

# Worktree teardown

Arguments: `$ARGUMENTS`

- **A worktree path** → remove it:

  ```bash
  "${CLAUDE_PLUGIN_ROOT}/scripts/worktree-remove.sh" "<path>"
  ```

- **`--branch <branch> [<repo-root>]`** → delete the branch alone, on PR evidence:

  ```bash
  "${CLAUDE_PLUGIN_ROOT}/scripts/branch-remove.sh" "<branch>"
  "${CLAUDE_PLUGIN_ROOT}/scripts/branch-remove.sh" "<branch>" "<repo-root>"
  ```

  The root defaults to the repository containing the cwd; pass it when the cwd is
  not inside that repository.

- **No argument** → this is a lookup, not a teardown. Read
  `${CLAUDE_PLUGIN_ROOT}/skills/worktree-teardown/SKILL.md` and answer from it.

Pass the path as a literal absolute path, and run from outside the worktree being
removed: the script refuses while the cwd is inside it. In a repo that versions its
own agent config, run unsandboxed from the first call; the reference says why.

Report the exit status and whatever the script printed to stderr. On a refusal or a
failure, Read `${CLAUDE_PLUGIN_ROOT}/skills/worktree-teardown/SKILL.md` and follow the
case that matches the message. Never fall back to `rm -rf` or a bare
`git branch -d`.

**The `allowed-tools` grant above applies only when this command is typed** as
`/worktree-teardown`. Reached any other way — through the Skill tool, or with the
agent running a script itself — this command's grant does not apply. The call is
then approved only by the slash command that opened the turn, if its
`allowed-tools` lists `Bash` (as `/deliver-task` does), or else by a user allow
rule naming the versioned script path:
[`../skills/co-review/references/permissions.md`](../skills/co-review/references/permissions.md#plugin-scripts).
