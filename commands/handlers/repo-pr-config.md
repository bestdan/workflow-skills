# repo-pr handler — /task-config setup

The `repo-pr` handler is the default. It captures tasks as markdown files via PR (works with `/do-tasks` and `/list-tasks`). There are no prerequisites to verify here — `gh` auth and git plumbing are exercised at the time of `/add-task`, not at config time.

## Steps

1. **No verification.** Nothing to check at config time. (At `/add-task` time, the handler will fall through `--remote` → `--subagent` → `--local` as needed; if `gh` auth is broken it lands on `--local` and stages into the current branch.)

2. **Mention the optional auto-merge workflow.** Tell the user that if they want `task-add` PRs to land on `main` automatically without manual review, they can install a GitHub Action that auto-merges PRs with the `task-add` label. This is optional; without it, `task-add` PRs just queue up for normal review. A minimal workflow looks like:

   ```yaml
   # .github/workflows/task-add-automerge.yml
   on:
     pull_request:
       types: [opened, labeled]
   jobs:
     automerge:
       if: contains(github.event.pull_request.labels.*.name, 'task-add')
       runs-on: ubuntu-latest
       permissions: { pull-requests: write, contents: write }
       steps:
         - run: gh pr merge --auto --squash "$PR"
           env: {
             PR: "${{ github.event.pull_request.html_url }}",
             GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}",
           }
   ```

   The user can paste this into their repo if they want it; do not write it for them from `/task-config`.

3. **Return the config block** to `/task-config` so it can write the file:

   ```yaml
   handler: repo-pr
   # wip_limit: 3            # optional — caps how many tasks /do-tasks --all dispatches at once
   # auto_execute_max_size: 2 # optional — batch auto-routing: auto-execute size <= this, reserve bigger for a human
   # archive_after: 30       # optional, top-level — default /archive-tasks age threshold (days)
   ```

   Two optional settings (plus the shared top-level `archive_after`):

   - **`wip_limit`** (default `3`) bounds batch dispatch: `/do-tasks --all` counts
     work already in flight — tasks with `status: in_progress` plus open
     `task-loop` PRs (the `needs_review` queue) — and dispatches only up to
     `wip_limit - current_wip` tasks, holding the rest. This keeps the human
     PR-review bottleneck from being flooded.
   - **`auto_execute_max_size`** (default `2`) size-gates batch auto-routing in
     `/do-tasks --all` / `-n N`: after ranking and the WIP gate, tasks with
     `size <= auto_execute_max_size` are claimed and executed, while bigger ones
     (`size > auto_execute_max_size`) are **reserved** (`--claim-only` semantics —
     claimed but not executed) for a human to pick up. The default of `2`
     auto-does size `1`–`2` and reserves size `3`+. Omit the key to accept it.
   - **`archive_after`** (shared top-level key, days) is the default age threshold
     when `/archive-tasks` runs without `--older-than`. `/archive-tasks` `git mv`s
     stale `done` task files into the fixed `dev_docs/tasks/_archive/` (not
     configurable — the `/promote-tasks`, `/do-tasks`, and `/list-tasks` scans
     hardcode the matching `-not -path '*/_archive/*'` exclusion, so the location
     must not drift). See `commands/handlers/repo-pr-archive.md`.

   Omit any key to accept its default. Single-task dispatch (`/do-tasks` or
   `/do-tasks <slug>`) ignores the WIP/size keys — an explicit pick is always
   executed in full.
