---
name: worktree-entry
description: Reference for entering a worktree through this plugin's `WorktreeCreate` hook — which `EnterWorktree` call reaches a given worktree or branch without a prompt, when `ExitWorktree` (`keep`) has to come first, how to land on a branch that exists only on the remote or carries no prefix, when `EnterWorktree` with `path` is the right call, and what re-entering another session's worktree does to its teardown. Use when about to enter, re-enter, or write into a worktree; when a write into another worktree is refused and the refusal names `EnterWorktree`; or when `EnterWorktree` reports "Created worktree at …" and you need to know whether it created one or handed one back.
user-invocable: false
---

# Worktree entry — reference

The load-bearing rule is one line: **enter a worktree with `EnterWorktree` and
`name`, never with `path`, unless the worktree sits outside the hook's layout and a
person is at the terminal.** This file is the detail behind that rule: one route
per starting point, and the reasons each one is shaped the way it is. Leaving a
worktree is [`worktree-teardown`](../worktree-teardown/SKILL.md)'s subject.

## The layout the hook owns

`${CLAUDE_PLUGIN_ROOT}/scripts/worktree-create-hook.sh` runs for every
`EnterWorktree` call with `name: <n>`. It puts the worktree at
`<root>/<repo>/<n>` on the branch `<prefix><n>`, where `<root>` and `<prefix>`
come from `${CLAUDE_PLUGIN_ROOT}/scripts/worktree-config.sh` and `<repo>` is the
main checkout's directory name. `<n>` must be one path segment: letters, digits,
`.`, `_` and `-`, not starting with punctuation and never containing `..`
(`scripts/lib/worktree-name.sh`). For a given `<n>` the hook does one of four
things:

| State at `<root>/<repo>/<n>`                                    | What the hook does                                                         |
| --------------------------------------------------------------- | -------------------------------------------------------------------------- |
| nothing there, `<prefix><n>` not a local branch                 | creates the branch from the **local** default branch and adds the worktree |
| nothing there, `<prefix><n>` already a local branch             | adds the worktree on that branch: re-attaches it, commits and all          |
| a worktree of this repo on `<prefix><n>`                        | hands it back unchanged                                                    |
| anything else (another repo, another branch, a plain directory) | refuses, and the `EnterWorktree` call fails                                |

The hook supplies the path, so the harness does not treat it as model-supplied
and nothing prompts. That is the whole reason to prefer `name`.

To find the `name` for a worktree you already know the path of, ask
`${CLAUDE_PLUGIN_ROOT}/scripts/worktree-name-for.sh <path>`, run from inside the
repo. It prints `<n>` and exits `0` only when `<path>` is a registered,
non-prunable worktree of this repo, on `<prefix><n>`, at exactly
`<root>/<repo>/<n>`. Exit `1` means the worktree has no name route: the `path`
section below applies. Exit `2` is a usage or config error. Do not derive the
name from the directory's basename yourself: a worktree on a re-pointed branch
has the right basename and no name route.

## Already in a worktree? Leave it first

`EnterWorktree` with `name` is refused while the session is already in a
worktree. So when the session is in one, call `ExitWorktree` with `keep`, then
`EnterWorktree` with `name`. `keep` leaves the worktree and its branch on disk,
so this costs nothing but the turn.

Call `ExitWorktree` only when the session **is** in a worktree it entered with
`EnterWorktree`. Otherwise it is a no-op, and it returns `is_error: true` with a
message that says so ("No-op: there is no active EnterWorktree session to exit …
No filesystem changes were made."). The agent carries on after it, so it does no
harm, but it reads as a failure in a transcript. Measured by the #1001 probe:
[`dev_docs/research/2026-10-14-enterworktree-deny-probe/`](../../dev_docs/research/2026-10-14-enterworktree-deny-probe/README.md).

## The routes

**New work, or a `<prefix>` branch that is already local.** `EnterWorktree` with
`name` set to the branch name minus `<prefix>`. Nothing else is needed.

**A `<prefix>` branch that exists only on the remote** (pushed from another
machine, or the head of a PR). `EnterWorktree` with `name` as above. The hook
cannot see the remote branch, so it cuts a fresh `<prefix><n>` from the local
default branch. Inside the worktree, move that branch onto the remote one:

```bash
git fetch origin <prefix><n>
git switch -C <prefix><n> --no-track origin/<prefix><n>
```

`-C` resets an existing branch, which is safe here only because the hook created
that branch a moment ago and nothing is on it. `--no-track` matters because
recording an upstream writes the main checkout's `.git/config`, which the Claude
Code sandbox denies. The first push sets the upstream instead: `git push -u
origin HEAD`. The branch still matches the directory, so the remove hook treats
this worktree as its own. `gh pr view <pr> --json headRefName` names a PR's
branch.

**An existing worktree in the hook's layout** (one you left with `keep`, or one
another session made and has finished with). `EnterWorktree` with its `name`,
never its `path`. The hook hands it back as-is. Do not enter a worktree a live
session is still writing in: two agents in one checkout is the collision
worktrees exist to prevent.

**A branch without `<prefix>`, or a commit.** No `name` produces that branch.
Enter under a name of your choosing, then inside the worktree run one of:

- `git switch <branch>` for a local branch;
- `git fetch origin <branch>` then `git switch -c <branch> --no-track
  origin/<branch>` for a remote-only branch (a plain switch would record an
  upstream);
- `git switch --detach <commit>` to inspect a commit.

The worktree is no longer on `<prefix><n>`, so the remove hook keeps it on exit
("on `<branch>`, not `<prefix><n>`"). `ExitWorktree` still reports it removed;
the keep shows up only in the harness's debug log. The `<prefix><n>` branch the
hook cut stays behind as well. Clear the worktree by hand with
`${CLAUDE_PLUGIN_ROOT}/scripts/worktree-remove.sh <path>`.

## When `path` is right

`EnterWorktree` with `path` is the route only for a worktree **outside** the
hook's layout, which is any worktree `worktree-name-for.sh` exits `1` on, and
only when a person is at the terminal. The test is the layout, not whether the
worktree exists. `${CLAUDE_PLUGIN_ROOT}/scripts/guard-enter-by-path.py` enforces
it: a `path` call on a worktree that has a name is refused, and the refusal
names the `name` route.

A model-supplied path outside `.claude/worktrees/` raises an approval prompt.
Neither a `permissions.allow` rule nor a `PreToolUse` hook answering `allow`
lifts it. A Remote Control session stops at the prompt with nobody to answer
it, and under `claude -p` the prompt comes back as a permission denial, so the
call fails. Never `git worktree add` a branch yourself and then enter it by
`path`: that is this prompt every time, for a worktree the hook would have made
for you.

## Re-entering another session's worktree

The hook stamps a session id on each worktree it creates, and the remove hook
tears down only a worktree whose stamp matches the exiting session. When a
**different** session enters an existing worktree, the hook rewrites the stamp to
`shared`, which matches no session. A worktree with no stamp stays unstamped.
Your own re-entry changes nothing.

Either way, neither session's `ExitWorktree` removes a shared or unstamped
worktree. When the work is done, clear it with
`${CLAUDE_PLUGIN_ROOT}/scripts/worktree-remove.sh <path>`, run from outside it.

## The message is not proof

`EnterWorktree` with `name` reports "Created worktree at …" whether the hook
created a worktree or handed an existing one back. Check what you got before
writing: `git branch --show-current` for the branch, `git log -1` for the tip,
and `git status` for the tree. A hand-back carries the previous session's
commits and any uncommitted changes.

Check the reported path too. Where the hook is not registered (the plugin is not
installed, or the session predates it), the same call silently creates a fresh
worktree under `.claude/worktrees/` on a new branch, instead of the one you
meant. In a repo that versions its own agent config, the sandbox aborts that
checkout with `Operation not permitted` and leaves nothing behind.
