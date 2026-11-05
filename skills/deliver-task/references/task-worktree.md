# Where the work branch lives under `--run-state`

Under `/auto-pilot` the run worktree's `HEAD` stays on the run-state branch for
the whole run ([`../../auto-pilot/references/run-state.md`](../../auto-pilot/references/run-state.md)
"Run worktree HEAD invariant"), so the work branch needs a worktree of its own.
**Where** that worktree sits decides whether step 5's co-review can reach it.

## Place it with the script, right after the claim

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/task-worktree.sh" --ref '<branch>'
```

`<branch>` is the work branch the claim acquired (gh-issue: the
`gh-issue-claim.py branch-name` value). The script checks it out at the
`WorktreeCreate` hook's layout, `<worktree root>/<repo>/<name>` on
`<prefix><name>`, and prints one `TASK_WORKTREE:` line. On `ok`, its `path=` is
the task worktree: integrate the worker's diff there and run step 3's checks
there. Coder **worker** worktrees (orchestrate-coders) are separate and may sit
anywhere, `<run root>/workers/` included; only the work branch's own worktree
has to be at the layout.

Why the layout: co-review's working-directory pre-flight reads `foreign` (the
branch is held by a worktree that is not the session's), and
`reach-pr-branch.sh` then routes by where the holder sits. At the hook's layout
it returns `enter-name`, which `EnterWorktree` takes with no prompt. Anywhere
else, such as `<run root>/workers/task-<n>`, it returns `enter-path`, which
needs an approval no one is there to give, so `--non-interactive` ends the
review before any reviewer runs and the task parks as `co-review unavailable`
(#1395).

On `none`, do not place the worktree by hand somewhere else; that only moves
the failure to step 5. Park the task through the adapter's `flag_for_human`
contract with the reason `task worktree: <reason>`:

- `not-hook-name` — the branch does not carry the configured branch prefix
  (`scripts/worktree-config.sh prefix`). Most often the handler's
  `branch_prefix` and the worktree prefix disagree.
- `held-elsewhere` — another worktree already holds the branch. Remove it first
  if it is this run's own, clean and pushed (`scripts/worktree-remove.sh`),
  then re-run the script.
- `path-exists`, `fetch-failed`, `worktree-add-failed`, `config-failed` — as
  the script header describes.

## After co-review, come back

Co-review enters the task worktree with `EnterWorktree` (`name`) and leaves the
session there. Before the next run-state write, return with `ExitWorktree`
using `keep`, which restores the run worktree as the working directory. Do not
`remove`: hand-off still needs the branch, and the worktree is cleared at
hand-off with `scripts/worktree-remove.sh <path>` once the branch is pushed.
