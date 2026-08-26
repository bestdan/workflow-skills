# Cleaning up a worktree with submodules

This repo vendors the Bats suites as git submodules under `test/vendor/`
(see `CONTRIBUTING.md` → "Local dev loop"). Once those submodules are
populated in a worktree, `git worktree remove <path>` refuses to remove it:

```
fatal: working trees containing submodules cannot be moved or removed
```

## The fix: `--force`

Confirm both the worktree **and each of its populated submodules** are clean
**first** — `--force` skips git's own uncommitted-changes check, and it
deletes gitignored paths right along with everything else, so `--porcelain`
alone isn't enough: this repo's own `.gitignore` covers real local state
(`.claude/`, `dev_docs/co-review/`, `dev_docs/tasks/*`), and a top-level
status check does not look inside a submodule at all — a submodule can carry
its own ignored files that only its own `git status` will show.

A stash needs its own check, because `git status` never reports one and the
loss is unrecoverable. A linked worktree keeps its submodule gitdirs under
`.git/worktrees/<name>/modules/`, not the shared `.git/modules/`, so forcing
destroys that submodule's objects and refs with no shared copy to recover
from:

```sh
git -C "<path>" status --porcelain --ignored
git -C "<path>" submodule foreach -q 'git status --porcelain --ignored'
git -C "<path>" submodule foreach -q --recursive 'git stash list'
```

Only once all three come back clean is `--force` actually safe:

```sh
git worktree remove --force "<path>"
```

It stays a single git-managed command that keeps git's own worktree
bookkeeping in sync as it goes (removal is still two internal steps —
checkout, then metadata — so it isn't atomic; interruption can still leave
partial state), rather than leaving `git worktree prune` for you to remember
to run separately. `scripts/spawn-orchestrator.sh` already removes worker
worktrees this way.

## If `--force` itself fails

Diagnose the error rather than reaching for `rm -rf`. In particular, a
**locked** worktree (`git worktree lock`) refuses a single `--force` on
purpose:

```
fatal: cannot remove a locked working tree, lock reason: <reason>
use 'remove -f -f' to override or unlock first
```

`rm -rf` would destroy a tree someone deliberately protected, and it leaves
the locked administrative entry behind for `git worktree prune` to trip
over instead of cleaning it up. Pass `--force` twice, or `git worktree
unlock` first, to remove it properly.

## Last resort: manual removal

Only once you've ruled out a lock (and any other diagnosable cause) is this
worth reaching for:

```sh
rm -rf "<path>"
git worktree prune
```

This drops both the submodule/lock checks and git's own bookkeeping — and
unlike `git worktree remove`, it's genuinely two separate steps: `rm -rf`
only clears the checkout, and the stale administrative entry sticks around
until `git worktree prune` runs after it. It's a last resort, not the
default.
