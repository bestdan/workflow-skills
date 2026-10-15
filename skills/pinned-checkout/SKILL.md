---
name: pinned-checkout
description: Reference for the branch pin on a repo's main checkout — how a repo opts in and out, why this plugin's guard runs before `git` and no git hook can, the `reference-transaction` trap, which layer covers what the guard lets through by design, and the one-call override. Use on "This checkout is pinned to" or "put back on it", when a checkout or switch is refused or silently undone, when deciding whether a command should have been caught, or before changing `scripts/guard-pinned-branch.py` or the `post-checkout` hook it installs.
user-invocable: false
---

# The branch pin — reference

The load-bearing rule is one line: **in a pinned repo, branch work goes in a linked
worktree, never in the main checkout.** A refusal that opens `This checkout is
pinned to '<branch>'` is that rule working, not a fault to route around. Where the
work goes instead is the [`worktree-entry`](../worktree-entry/SKILL.md) skill's
subject; this file is why the pin exists and how its layers divide the work.

## Opting in and out

A repo opts in by setting one key:

```sh
git config --local hooks.pinnedBranch main
```

From then on the **main checkout** of that repo stays on `main`. Linked worktrees
are never pinned: the guard and the hook both compare `--git-dir` with
`--git-common-dir` and stand down when they differ. Every repo without the key is
untouched, because each layer asks git for the key and gets nothing.

`${CLAUDE_PLUGIN_ROOT}/scripts/pin-checkout.sh <branch>`, run from the repo, sets
the key and also installs the `post-checkout` backstop described below:

| exit | meaning                                                                                                          |
| ---- | ---------------------------------------------------------------------------------------------------------------- |
| `0`  | pinned, and the hook is installed (or already was)                                                               |
| `3`  | pinned, but `core.hooksPath` is set, so git never reads `.git/hooks`: nothing installed, and it says where to go |
| `4`  | refused: a different `post-checkout` already exists. Nothing is written, not even the key                        |
| `1`  | a write failed — most likely the sandbox                                                                         |
| `2`  | not run from a non-bare repo's working tree                                                                      |
| `64` | a missing or invalid branch name                                                                                 |

Run it **outside the Claude Code sandbox**, which denies writes to `.git/config`
and `.git/hooks`. From a linked worktree it still pins the main checkout, because
the key and the hook live in the shared git dir.

Opt out with `git config --unset hooks.pinnedBranch`. An installed hook then reads
no key and does nothing, so it can stay.

**Why a repo wants this.** When a machine reads one working tree — a hooks path,
tool pins, symlinks, agent config — switching that tree's branch reconfigures the
machine, and nothing errors when it happens.

## Why the guard runs before git, and why no git hook can

`githooks(5)` documents no hook that runs before a checkout. `post-checkout` runs
after the working tree is rewritten and cannot change the outcome. So
`${CLAUDE_PLUGIN_ROOT}/scripts/guard-pinned-branch.py` is a Claude Code
`PreToolUse` hook on `Bash`: denying the tool call is the only point in the
sequence that is genuinely before the mutation, and a denied call changes nothing
on disk.

**It sees Claude Code alone.** Another CLI agent, a script running git on its own,
an MCP tool, and a human terminal never reach it. That is what the `post-checkout`
hook is for.

## `reference-transaction` is the trap

It looks like the missing pre-checkout hook: a branch switch reaches it, and it
can abort the HEAD update from its `prepared` phase. **But git writes the index
and working tree before it updates HEAD.** A veto there leaves HEAD on the pin with
the other branch's files in the tree and staged — worse than the checkout it
refused, and exactly the silent reconfiguration the pin exists to prevent. It was
built, measured, and reverted. Do not reach for it again.

**It survived its own test suite because every fixture had the same content on
both branches**, so every assertion about HEAD passed while the tree was wrong.
Any test of a pin layer must build branches whose contents differ and assert the
branch, the working tree, and `git status --porcelain` together.
`test/guard-pinned-branch.bats` and `test/pin-checkout.bats` both do.

## The layers, and what each covers

| Layer                                                   | When                         | Sees                          | Effect                                     |
| ------------------------------------------------------- | ---------------------------- | ----------------------------- | ------------------------------------------ |
| The guard (`PreToolUse`)                                | before git runs              | Claude's Bash command as text | denies the call; nothing moves             |
| `post-checkout` (`scripts/lib/pinned-checkout-hook.sh`) | after git finishes           | every actor, where installed  | switches the main checkout back to the pin |
| State check (planned, #967)                             | after each Bash call returns | the repo's actual HEAD        | puts HEAD back and says why                |

**The guard judges a fixed set of plain shapes and lets the rest through by
design.** The scope rule — which shapes are in scope, which fall through, and
which review findings count as defects — lives in the guard's module docstring,
under `SCOPE`. Read it there before judging whether a command should have been
caught; it is not copied here so it cannot drift. A miss on an out-of-scope shape
is closed as by design.

**The `post-checkout` hook is the backstop for those shapes**, and for every actor
the guard cannot see. By the time it runs git has finished, so its restore is an
ordinary `git checkout <pin>` and leaves the repo consistent. It costs two things:
the other branch really is checked out for a moment and the tree is written twice,
and the restore can fail for ordinary reasons such as local changes — it then says
so loudly, because a quiet failure would leave exactly the state the pin prevents.
It runs only where it is installed, and `pin-checkout.sh` installs it only when
`core.hooksPath` is unset.

**The state check is not shipped yet.** Its design, and the three-layer reasoning
behind all of this, is
[`dev_docs/designs/2026-10-04-pinned-checkout-guard-v2.md`](../../dev_docs/designs/2026-10-04-pinned-checkout-guard-v2.md).

### Detached HEAD: the guard and the hook deliberately disagree

The **hook** leaves a detached HEAD alone, because `git rebase` and `git bisect`
detach and fire `post-checkout`, and yanking HEAD back mid-rebase is far worse
than what it would catch. The **guard** refuses an explicit `--detach`, a commit,
or a tag as a target, because it sees only the `checkout`/`switch` a caller typed
and nothing inside a rebase reaches it. They differ only where the guard is
stricter.

## The override

The refusal ends with a fixed sentence rather than a rebuilt command, because the
agent already holds the exact command it ran:

> To move this checkout anyway, re-run the same command with
> `env WORKFLOW_SKILLS_ALLOW_HEAD_MOVE=1` immediately before the `git` word.

For a refused `gh pr checkout` the word is `gh`. Only an assignment on that one
invocation counts: naming the variable anywhere else in the command disarms
nothing. The `post-checkout` hook reads the same variable from its environment,
so the one `env` prefix gets the move past both layers.

The override is for the repo's owner. Do not reach for it to get past a refusal
you hit while working; the answer to that refusal is a worktree, through
[`worktree-entry`](../worktree-entry/SKILL.md).
