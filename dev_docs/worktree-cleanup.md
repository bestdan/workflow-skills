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

Confirm `git status --porcelain` is clean in that worktree **first** —
`--force` also skips git's own uncommitted-changes check, so a clean status
is the one thing making this safe. It stays a single git-managed command that
keeps git's own worktree bookkeeping in sync as it goes (removal is still two
internal steps — checkout, then metadata — so it isn't atomic; interruption
can still leave partial state), rather than leaving `git worktree prune` for
you to remember to run separately. `scripts/spawn-orchestrator.sh` already
removes worker worktrees this way.

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

This drops both the submodule/lock checks and git's own bookkeeping in one
step — it's a last resort, not the default.
