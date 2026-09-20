---
name: worktree-teardown
description: Reference for the mechanics behind this plugin's worktree teardown — `scripts/worktree-remove.sh` and `scripts/branch-remove.sh` — covering the submodule force gates, the PR-evidence branch delete and why a plain `-d` is unsafe in a squash-merging repo, and the half-deleted and locked-worktree recovery cases. Use when a worktree removal fails, refuses, or half-completes, when deciding whether a branch is safe to delete, or on "working trees containing submodules cannot be moved or removed", "use 'remove -f -f' to override or unlock first", or a retry refused as "contains modified or untracked files".
user-invocable: false
---

# Worktree teardown — reference

The load-bearing directives are short, and they belong in whatever always-loaded
instructions your setup carries: **isolate write-work in a worktree, tear it down with
`${CLAUDE_PLUGIN_ROOT}/scripts/worktree-remove.sh <path>` and never `rm -rf`, and `cd` out of the
worktree first.** This file is the detail behind them — the failure modes, the gates,
and the reasons.

Two scripts do the work, and both are callable by hand:

| Script                                             | Job                                                                               | Exits                                                                                                                                                   |
| -------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `${CLAUDE_PLUGIN_ROOT}/scripts/worktree-remove.sh` | remove one worktree, prune the admin entry, then hand the branch to the one below | `0` removed (or already gone) · `1` refused by the cwd guard or a write probe · `64` nothing named, or no repository found · otherwise git's own status |
| `${CLAUDE_PLUGIN_ROOT}/scripts/branch-remove.sh`   | the branch half, standing alone                                                   | `0` deleted (or already gone, stanza cleared) · `3` kept, with the reason on stderr · `64` nothing deletable named                                      |

The session's own teardown normally runs through the `WorktreeRemove` hook rather than
through either script typed by hand — see **What the exit hook covers** below.

## What the script does, and why not raw git

`git worktree remove` cleans up after itself, but deleting the directory directly strands `.git/worktrees/<name>/` (its `config.worktree` included) in the main checkout, where it accumulates silently and keeps showing up in `git worktree list` as `prunable`. The script pairs the remove with a `git worktree prune`, and prunes even when the remove fails — which is exactly the already-`rm -rf`'d case. A genuine failure (dirty or locked worktree) still surfaces as a nonzero exit and a warning, not a silent success.

**The `cd`-out guardrail is the script's, not git's.** It refuses while your cwd is inside the target, because deleting your own cwd leaves the calling shell wedged (every later command dies in `getcwd`), and a script runs in its own process so it cannot cd the caller out. A bare `git worktree remove --force .` run from inside the target deletes the checkout and exits 0, leaving the calling shell wedged with no warning. The fix for the refusal is `cd` to the main checkout and re-run.

## Failure cases

### A locked worktree

A **locked** worktree (`git worktree lock`) is the one failure a single `--force` will not clear: git answers `use 'remove -f -f' to override or unlock first`, and it means it. Run `git worktree unlock <path>` first, then re-run the script — it has no locked-worktree override and forwards no flags, so `remove -f -f` has to be typed raw, and typing it skips the write probe, the paired prune, and the PR-gated branch delete. Never `rm -rf`, which destroys a tree someone deliberately protected and strands its locked administrative entry.

### A sandboxed removal that half-deletes

This is the remove-side twin of the create-side denial that makes `scripts/worktree-create-hook.sh` exist — the same protected agent-config paths, denied on unlink instead of on checkout. It is **not** the `.git/config` denial described at the end of this file, which has a different cause. `git worktree remove` unlinks files one at a time with no transaction, so a denial partway through leaves ~20 files gone and the rest in place. The sandbox is not blanket-denying the worktree — the configured worktree root is on the write allowlist — it is protecting the _session's_ anchored agent-config paths (`.claude/settings.json`, `.claude/hooks/`, `agents/CLAUDE.md`), which a repo that versions its own agent config checks out into every worktree.

The nasty part is the retry: git refuses it as "contains modified or untracked files", which reads as your uncommitted work when the only changes are deletions the failed attempt just made. The script write-probes both sides first — the worktree's protected paths, and the main checkout's `.git/worktrees/<name>/` admin dir described next — and aborts before touching anything, and tells you when a refusal is self-inflicted — but the probe can't prove every unlink will be permitted, so if a removal does fail partway, check the tree holds nothing but deletions and confirm the branch merged via PR state before forcing.

**The removal has a second half, and it lands in the main checkout.** Git also deletes the worktree's `.git/worktrees/<name>/` admin dir, so a denial there leaves the checkout gone and the entry stranded as `prunable` — after which `git branch -D` insists the branch is still checked out at a path that no longer exists. That is the other half the probe above covers.

### Run teardown unsandboxed on the first call, in a repo that versions its own agent config

The denial above is structural, not occasional: those paths are in every worktree of such a repo, so the sandboxed attempt is guaranteed to fail its probe and the round-trip buys nothing. Skip it and run the teardown unsandboxed from the first call. Elsewhere, sandboxed is still correct; don't reach for the escape by default.

Whether that call is **pre-approved** rather than classifier-judged — which is what keeps teardown working in an unattended run with nobody available to approve it — depends on the `permissions.allow` entries of the machine you are on, not on anything this plugin ships. On the machines that carry [`bestdan/dotfiles`](https://github.com/bestdan/dotfiles), the allowlist reasoning lives in that repo's `sandbox-escapes` skill; it is deliberately not duplicated here, because a sandbox and its allowlist are a property of one harness's configuration while these scripts install anywhere. The sibling `worktree-isolation-guard` skill stays there for the same reason: it explains why a command that plainly stays inside a worktree is refused, which is the harness's guard rather than this teardown's behaviour.

## A populated submodule makes teardown need `--force`

`git worktree remove` refuses any worktree holding an initialized submodule — `working trees containing submodules cannot be moved or removed` — however clean the tree is; an *un*initialized one removes normally, so this is a refusal, not a property of the repo. The refusal is a pre-check, so nothing is deleted when it fires.

`scripts/worktree-remove.sh` decides the force for you: it forces through when the tree was clean going in and no submodule holds a stash, and refuses with the reason otherwise. Both gates matter — a linked worktree keeps its submodule gitdirs under `.git/worktrees/<name>/modules/` rather than the shared `.git/modules/`, so forcing destroys that submodule's objects and refs with no shared copy to recover from.

What the gates do and do not cover:

- **Cleanliness** covers dirty content, untracked files, and a commit that moved the submodule's checked-out HEAD — the superproject reports all three as `M <path>`.
- **A stash** is invisible to `status`, so it gets its own recursive check — a **submodule** stash, specifically. A stash made in the worktree itself lands in the shared `refs/stash` in the common dir and survives the removal intact, so it needs no gate, and gating on it would refuse teardowns for nothing. Only the submodule case loses data, for the gitdir reason above.
- **A submodule walk that fails** is treated as "there might be a stash" and refuses, for the same reason: an empty capture means "no stashes" and a failed walk produces one too.
- **Other branches, tags, and reflog-only commits inside the submodule** are uncovered deliberately, because a gitlink only tracks HEAD, and a reflog is never empty — gating on one would refuse every teardown.

Merge status does not gate the _force_: the branch and its commits survive removal untouched, so it decides nothing there. It gates the branch delete instead.

## The branch delete runs on PR evidence, and never at the cost of the removal

Teardown's real residue is the merged branch, and deleting it by hand costs approvals a remote-control session cannot supply. So the script finishes the job: it reads the worktree's branch before the checkout goes, asks `gh pr list --head <branch> --state merged --json headRefOid,baseRefName`, and deletes the branch only if its current tip is one of those merged heads.

**Matching the name alone is not enough.** A merged PR proves some commit under that name landed, not that the commit the name points at now did, so a reused name or a commit pushed after the merge would read as merged and lose work. The tip comparison costs nothing normally: a squash-merge builds a new commit on the base and never touches the head ref, so the local tip still equals what the PR merged.

A worktree someone already `rm -rf`'d has no checkout left to read, so the branch comes from its surviving `git worktree list --porcelain` entry instead. That entry is erased by the script's own prune, so this is the only window it can be read in — and it is the case that most needs the branch tidied, because whoever deleted the directory by hand is not coming back to run `git branch -D`.

The gate is PR state, not ancestry: a squash-merging repo makes `--is-ancestor` call every merged branch unmerged, which is also why the delete is `-D` — `-d` answers a question the gate cannot depend on. It deletes while `origin/<branch>` still holds the merged tip and refuses once that ref is pruned, so the same merged branch succeeds or fails by how recently a fetch ran.

### `scripts/branch-remove.sh` is the same gate with no worktree attached

When there is no worktree to remove — a branch left behind by a session that tore its checkout down elsewhere, or one that never had one — `scripts/branch-remove.sh <branch> [root]` runs this same code standing alone. Prefer it to a bare `git branch -d`, which is not a safe delete in a squash-merging repo and fails toward losing work. The optional second argument is the repository root, for the case the cwd is a directory the teardown just deleted; `worktree-remove.sh` passes its own pinned root for exactly that reason.

**`-d` proves "merged" two ways and both are broken here.** The ancestry test calls every squash-merged branch unmerged, because the squash builds a new commit that the original is no ancestor of. So the only test that ever passes is the fallback — does the branch match its upstream — which ignores whether the work landed at all. A branch pushed and never merged matches its upstream; so does one that merged and then took new commits. `-d` deletes both and reports them as merged.

The script adds one gate the worktree path's description above leaves implicit: the merged PR must have merged **into the default branch**. A stacked PR merged into its parent is therefore kept, and the refusal names the parent to wait on — otherwise tearing down the bottom of a stack would delete a branch whose work has not reached the default branch. Which branch that is comes from `origin/HEAD` when it exists and from `gh repo view` otherwise, and every refusal names the source, because a stale `origin/HEAD` pointing at a _switched_ default is the one case nothing local detects.

**Called from a teardown, every path through this step exits 0.** The checkout is already gone by then, so a nonzero status would report a finished teardown as one to retry — `worktree-remove.sh` therefore discards `branch-remove.sh`'s status. Run on its own, the same refusals exit `3` and say why, which is what makes it usable as a standalone command. A gate that cannot be evaluated — sandboxed with no network, or no `gh` auth — keeps the branch and says so; that is the same answer as "not merged", and a kept branch costs one `git branch -D` later while a wrongly deleted one costs the work. A detached HEAD skips the lookup entirely, and so does a branch that is checked out somewhere else, which is reported as such rather than as a failed delete.

The delete is judged by whether the ref went away, not by git's exit status, because a sandboxed `git branch -D` deletes the ref and _still_ returns 0 after failing to lock `.git/config`. That failed lock is why the `[branch "..."]` stanza can outlive the ref: a sandbox that denies that one write for the session's own repo — which is always the repo the branch lives in — leaves it behind. The script clears the stanza when it can, reports the leftover when it cannot, and moves on; a stale stanza is inert, its only effect being that a future branch of the same name inherits the dead upstream. Handed a branch whose ref is already gone but whose stanza survives, it clears the stanza and calls that the whole job.

## What the exit hook covers, and what it cannot reach

`scripts/worktree-remove-hook.sh` runs the same script when the harness dispatches `WorktreeRemove`, so an exit through the `ExitWorktree` tool or the interactive exit dialog needs nothing typed. It runs from the main checkout, because `worktree-remove.sh` refuses to remove the worktree its caller is standing in and the hook's own cwd is that worktree.

It removes only what `scripts/worktree-create-hook.sh` made for the exiting session: the create hook stamps a session id only on a worktree it actually created, and the remove hook keeps any worktree that is outside the configured root, is not a linked worktree, whose branch no longer matches its directory, whose owner differs, or which carries no stamp at all. That ownership gate is not paranoia — the create hook hands a re-used name back rather than refusing it, so two sessions can share one checkout, and the stamp is what stops one session's exit from deleting the other's live working tree. Entering an existing worktree never claims it: a second, different session rewrites the marker to the sticky `shared`, which matches no session, and an unmarked worktree stays unmarked. Either way neither exit order can remove it, and clearing it is a manual `scripts/worktree-remove.sh`.

A refusal here is **safe but invisible**: a non-zero exit does not block the harness, which re-stats the path and logs `WorktreeRemove hook did not remove worktree, kept at: <path>` to the debug log and nowhere else. That is why every keep above prints its reason to stderr.

**The hook does not reach an unattended run.** A `claude -p` session that exits inside a worktree fires `SessionEnd` and nothing else, subagent worktrees are never handed to it, and both of the harness's own sweeps skip a hook-created worktree by construction. So worktrees still accumulate under the configured root, and clearing those is still a manual `scripts/worktree-remove.sh`. Verified against Claude Code 2.1.260; see bestdan/dotfiles#677 and #679.

A separate `PostToolUse` hook, `scripts/worktree-teardown-reminder.py`, fires when `ExitWorktree` returns and says teardown ends there — the repo's _other_ worktrees are parallel work, not a cleanup to-do list. It is registered on the `ExitWorktree` matcher alone and is silent for every other tool.

## Why the create hook starts from local `main`

`scripts/worktree-create-hook.sh` starts the worktree from the **local** default branch — the one `origin/HEAD` names, or `main` when there is no remote — never from `origin/<default>`, so the add runs sandboxed. The same applies to any `git worktree add` you run by hand — a second worktree for a review, say.

`git worktree add -b <prefix><name> <root>/<repo>/<name> main` succeeds inside the sandbox. The same command with `origin/main` as the start point fails with `could not lock config file .git/config: Operation not permitted` / `unable to write upstream branch configuration`, because a remote-tracking start point makes git record upstream tracking in the main checkout's `.git/config`, and the sandbox denies `config` inside `.git` as a protected path that no `allowWrite` entry can lift. Verified 2026-09-04 by running both forms in `workflow-skills`.

A local start point writes nothing to `.git/config`, so the add needs no unsandboxed retry and raises no prompt. The branch has no upstream as a result, so the first push is `git push -u origin HEAD`. The cost is a stale base when the local default branch is behind: `git -C <main checkout> pull --ff-only` before creating the worktree when it matters. See bestdan/dotfiles#666 and #398.

Where the worktree lands and what its branch is called are not hardcoded — both come from `${CLAUDE_PLUGIN_ROOT}/scripts/worktree-config.sh`, which is also what `worktree-remove-hook.sh` asks before deciding a worktree is one of its own.
