# Cleaning up a worktree with submodules

This repo vendors the Bats suites as git submodules under `test/vendor/`
(see `CONTRIBUTING.md` → "Local dev loop"). Once those submodules are
populated in a worktree, `git worktree remove <path>` refuses to remove it:

```
fatal: working trees containing submodules cannot be moved or removed
```

## The fix: `--force`

```sh
git worktree remove --force "<path>"
```

Confirm `git status --porcelain --ignored` is clean in that worktree
**first** — `--force` skips git's own uncommitted-changes check, and it
deletes gitignored paths right along with everything else, so `--porcelain`
alone isn't enough: this repo's own `.gitignore` covers real local state
(`.claude/`, `dev_docs/co-review/`, `dev_docs/tasks/*`), and a submodule can
carry its own ignored files too — check inside it as well before you
force-remove. Only once both come back clean is `--force` actually safe.
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
