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
`<prefix><name>`, and prints one `TASK_WORKTREE:` line. Only at that layout can
co-review reach the branch unattended: anywhere else its router needs an
approval no one is there to give, and the review ends before any reviewer runs
(the script's header has the detail). Coder **worker** worktrees
(orchestrate-coders) are separate and may sit anywhere, `<run root>/workers/`
included; only the work branch's own worktree has to be at the layout.

On `none`, do not place the worktree by hand somewhere else; that only moves
the failure to step 5. Park the task through the adapter's `flag_for_human`
contract with the reason `task worktree: <reason>`:

- `not-hook-name` — the branch does not carry the configured branch prefix
  (`scripts/worktree-config.sh prefix`). Most often the handler's
  `branch_prefix`, or the tracker's published branch name, and the worktree
  prefix disagree.
- `held-elsewhere` — another worktree already holds the branch. Remove it first
  if it is this run's own, clean and pushed (`scripts/worktree-remove.sh`),
  then re-run the script; otherwise the task stays parked.
- `path-exists`, `fetch-failed`, `worktree-add-failed`, `config-failed` — as
  the script header describes.

## Stand in it for steps 3–6, then come back once

On `ok`, before entering:

1. **Make the run-state paths absolute.** `/auto-pilot` passes `--run-state`
   and `--questions` relative to the run worktree (`.auto-pilot/RUN.md`), and
   the task branch has no `.auto-pilot/`. Resolve both against the run
   worktree now, and use the absolute paths for every `reserve-gate` call,
   `co_review_mode` read and `QUESTIONS.md` append until the task ends. This
   skill commits neither file; the orchestrator does, from the run worktree.
2. **Enter the task worktree** with `EnterWorktree` using `name: <name>` from
   the `ok` line. The hook hands the existing worktree back with no prompt.

Do steps 3 to 6 there: integrate the worker's diff, verify, open the PR, run
co-review and iterate. Co-review's working-directory pre-flight then reads `ok`
and does not move the session. Never write into the task worktree from the run
worktree with `cd <path> && …` or `git -C <path>`.

When the task ends (hand-off, park or bail), return with `ExitWorktree` using
`keep`. That puts the session back in the directory it was in before
`EnterWorktree`, which under `/auto-pilot` is the run worktree. Do not use
`remove`: hand-off still needs the branch. Clear the worktree at hand-off with
`scripts/worktree-remove.sh <path>` once the branch is pushed.
