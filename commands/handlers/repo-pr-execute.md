# repo-pr handler — /do-tasks file-path execute flow

Invoked from `/do-tasks` when the handler is `repo-pr` (or absent). Scan for
dependency-ready task files and turn them into PRs — by default dispatching a
remote Claude session per task (each in its own isolated cloud VM), or in the
current session with `--local`.

This file holds the file-path mechanics (scan, ranking, multi-blocker readiness,
WIP cap, the remote dispatch prompt, local mode, and the report format) so
`/do-tasks` can reference it rather than restate it — symmetric with
`linear-claim.md` for the tracker path.

> **Legacy migration preflight.** `/do-tasks` runs this before invoking this file
> (if a legacy `dev_docs/todos/` directory exists, run the **Legacy migration**
> prompt from `skills/task/SKILL.md`), so assume it is already handled here.

## Argument mapping

`/do-tasks` passes its arguments straight through:

| `/do-tasks` invocation | Behavior here                                                                     |
| ---------------------- | --------------------------------------------------------------------------------- |
| `/do-tasks`            | default: select and process the single highest-ranked dependency-ready task       |
| `/do-tasks <slug>`     | process that specific task; if it is still blocked, stop and report every blocker |
| `/do-tasks --all`      | select all dependency-ready tasks, dispatch up to the WIP limit, hold the rest    |
| `/do-tasks -n N`       | like `--all`, but cap the **selected** batch at `N` before applying the WIP limit |
| `--remote` (default)   | remote dispatch (the "Dispatch remote agents" section)                            |
| `--local`              | the "Local mode" section (caps the batch at 1)                                    |

## 1. Scan for tasks

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/tasks" -name '*.md' -type f 2>/dev/null
```

Parse YAML frontmatter from each file. Skip any file with `type: epic` (those are
epic rollups, not task cards) and any with no frontmatter (e.g. a plan overview).
Filter to `status: ready`. Sort by:

1. Dependency readiness: a task is eligible only when **every** `is_blocked_by` entry is satisfied (target absent or `done`); a task with any still-active blocker is not
2. Priority: `high` > `medium` > `low` (`urgent` is human-only and is never picked up here)
3. Value/effort score: `impact / size` descending (`impact` and `size` are both Fibonacci `1`/`2`/`3`/`5`); a task with no `impact` set has no score and ranks **last within its priority tier** (never dropped). See **Ranking** in `skills/task/SKILL.md`.
4. Age: oldest `created` date first

Treat `is_blocked_by` as a reference to another task's slug, **or a list of slugs** (`[a, b]`). A single string behaves exactly as a one-element list. Each slug is satisfied when no task file with that slug exists under `dev_docs/tasks/**/*.md`, or it exists with `status: done`. A task is dependency-ready only when **all** of its blockers are satisfied; if any referenced blocker file still exists in another state, the dependent task must not be dispatched yet. When reporting a blocked task, list **every** unresolved blocker (e.g. `waiting on b, c`).

If no ready, dependency-ready tasks exist, report that and stop. Hint the user to run `/promote-tasks` if there are cards sitting in `new` or `needs_refinement`. If the only remaining ready tasks are waiting on dependencies, say which blockers each one is waiting for.

## 2. Select tasks to process

- Default: pick the single highest-ranked dependency-ready task
- With `<slug>`: find that specific task; if it is waiting on `is_blocked_by`, stop and report every unresolved blocker instead of dispatching it
- With `--all`: select all dependency-ready tasks and skip any that are still waiting on another task. The WIP limit in step 4 then caps how many of these are actually dispatched.
- With `-n N`: like `--all`, but after ranking keep only the top `N` before the WIP limit applies. The effective batch is `min(N, wip_limit - current_wip)`. Report any selected task you did not dispatch, distinguishing `held (-n N ceiling)` from `held (WIP limit reached)`.

## 3. Check dispatch prerequisites

Before dispatching, verify GitHub access:

```bash
gh auth status 2>&1
```

If this fails (token invalid, TLS errors, network issues), **stop** and tell the user — both remote and local modes call `gh pr create`, so a broken `gh` blocks both. If the error mentions TLS/x509/certificate, note it's likely Claude Code's sandbox blocking keychain access and suggest re-running outside sandbox mode.

## 4. Dispatch remote agents

**WIP limit (batch only — `--all` / `-n N`).** Before dispatching a batch, bound it by the kanban WIP limit so you don't flood the human PR-review bottleneck (the `needs_review` column). Single-task mode (`/do-tasks` / `/do-tasks <slug>`) is **not** gated — skip this paragraph there.

1. Resolve `wip_limit` from `dev_docs/tasks/.task-config.yml` (the repo-pr handler config). Default to `3` if the key is absent.
2. Count current WIP = (task files with `status: in_progress`) + (open `task-loop` PRs):

   ```bash
   gh pr list --label task-loop --state open --json number --jq 'length'
   ```

   If the `gh pr list` query fails (API error or rate limit — step 3 has already confirmed `gh` is installed and authenticated), count only the `in_progress` files and note in the report that the count may undercount open PRs (so the effective cap is looser than intended).
3. Dispatch only the top `wip_limit - current_wip` selected tasks, highest-ranked first (priority, then value/effort score, then age — as sorted in step 1). For `-n N` the batch is `min(N, wip_limit - current_wip)`. If that slack is `0` or negative, dispatch nothing and report `WIP limit <wip_limit> reached (<current_wip> in flight) — nothing dispatched`.
4. Report every task you did **not** dispatch as `held (WIP limit N reached)` or `held (-n N ceiling)`, listed under the dispatched ones in step 5.

**Size-gate auto-routing (batch only — `--all` / `-n N`).** The intended operating
model is **both**: drain small tasks autonomously and reserve bigger ones for a
human. After the WIP cap fixes the dispatch set above, split it by `size` so the
batch self-routes instead of being hand-sorted. Single-task mode (`/do-tasks` /
`/do-tasks <slug>`) is **not** size-gated — an explicit pick is an explicit
instruction to do it in full.

1. Resolve `auto_execute_max_size` from `dev_docs/tasks/.task-config.yml` (the
   repo-pr handler config). Default to `2` if the key is absent (auto-do size
   `1`–`2`; reserve `3`+).
2. For each task in the WIP-bounded dispatch set, route by its `size`:
   - `size <= auto_execute_max_size` → **execute**: dispatch normally (the claim +
     execute remote session below, or the in-session run under `--local`).
   - `size > auto_execute_max_size` → **reserve** (`--claim-only` semantics from
     `/do-tasks`): claim it — branch `task/<slug>`, flip the file
     `status: ready → in_progress`, commit, and push — but do **not** execute,
     delete the file, or open a PR. Leave it `in_progress` for a human to resume
     (e.g. with `/do-tasks <slug> --no-claim`).
3. A reserved task still consumed a WIP slot when it was selected, so it counts
   against the cap exactly as an executed one does — the gate routes the batch, it
   does not enlarge it.
4. **Explicit flags override the gate.** A `--claim-only` run reserves every
   selected task regardless of size; a `--no-claim` run executes the named
   already-claimed task regardless of size. The size split applies only to the
   default (atomic claim + execute) batch path.

Report the two groups distinctly in step 5: executed/dispatched vs.
`reserved for human (size N > auto_execute_max_size)`.

For each selected task routed to **execute**, read its full content (frontmatter + body), then dispatch a remote session.

The remote session prompt must be self-contained because the remote VM won't have this plugin installed. Include the task content and all processing instructions inline.

**Important:** Do NOT pass `--print` to `claude --remote` — it is not supported.

```bash
claude --remote "You are processing a task for the task plugin system.

## The task file

The file is at dev_docs/tasks/<slug>.md with this content:

<paste full task file content>

## Instructions

1. CLAIM: Create branch task/<slug> and update the task file status from 'ready' to 'in_progress'. Commit and push immediately.

   git checkout -b task/<slug>
   # Edit dev_docs/tasks/<slug>.md: change status: ready -> status: in_progress
   git add dev_docs/tasks/<slug>.md
   git commit -m 'claim task: <slug>'
   git push -u origin task/<slug>

   If push fails because the branch exists, STOP — another agent claimed it.

2. EXECUTE: Read the Context and Task sections. Read all files listed in related_files. If `is_blocked_by` is present, treat it as already satisfied before proceeding; if the blocking task file still exists in the checkout (and is not in status: done) for any reason, STOP rather than doing work out of order. Do the work described in the Task section.

3. VALIDATE: Look for test infrastructure (Makefile, justfile, package.json). Run tests if available. Check acceptance criteria.

4. DELETE the task file (the merged PR is the 'done' signal; no need to flip status: done in the file):
   git rm dev_docs/tasks/<slug>.md

5. COMMIT and PUSH:
   git add only the files you changed (do NOT use git add -A — it may pick up untracked artifacts)
   git commit -m 'chore(task): <title from frontmatter>

   Automated follow-up from task <slug>.md.
   Source branch: <source_branch>'
   git push

6. OPEN PR:
   gh label create task-loop --description 'Auto-generated from task-loop' --color '0E8A16' 2>/dev/null
   gh pr create --title 'chore(task): <title>' --label task-loop --body '## Summary

   Automated follow-up from task <slug>.md, created during branch <source_branch>.

   <bulleted list of what you did>

   ## Original Context

   > <quote the Context section from the task>

   ## Test Plan

   - [ ] Tests pass
   - [ ] No new lint errors
   - [ ] Acceptance criteria met

   ---
   Source task: dev_docs/tasks/<slug>.md (deleted in this PR)'

## If you cannot complete the task

1. Edit the task: change status to 'blocked'
2. Add a '## Consumer Notes' section explaining what you tried and what went wrong
3. Commit and push, but do NOT open a PR

## After opening the PR

The PR is the 'needs_review' signal. The task file has been deleted in this PR, so it no longer appears in /list-tasks under in_progress. The merged PR is the implicit 'done' transition."
```

When dispatching multiple tasks (`--all` / `-n N`), run the `claude --remote` commands in sequence (not background) so the user can see each session ID. Each remote session runs independently in its own cloud VM, and claims and executes **exactly one** task — never instruct a single agent to claim or work multiple tasks.

## 5. Report

For each dispatched task, tell the user:

- The slug and title
- That a remote session has been started
- They can monitor all sessions with `/tasks`

Example output:

```
Dispatched 3 tasks to remote agents:
  - remove-stale-alias (low) — remote session started
  - fix-broken-import (medium) — remote session started
  - add-missing-parser-test (high) — remote session started

Monitor with /tasks. Each will open a PR when complete.
```

If any tasks were **reserved for a human** by the size gate (the Size-gate auto-routing block above), list them as their own group — e.g. `reserved for human (size 5 > auto_execute_max_size 2): <slug>` — so the user knows they were claimed but not executed and can resume them with `/do-tasks <slug> --no-claim`.

If any tasks were skipped because they are waiting on another task, list them separately with every unresolved blocker slug. If any dependency-ready tasks were **held by the WIP limit** or the `-n N` ceiling (step 4), list those too — e.g. `held (WIP limit 3 reached): <slug> (high)` — so the user knows they are eligible and will dispatch on the next `--all` once in-flight work clears.

## Local mode (`--local`)

When `--local` is specified or `claude --remote` is unavailable, process the task directly in the current session (`gh` is still required for `gh pr create`). `--local` caps the batch at **1** — it processes the single highest-ranked task and reports the rest as held; `/do-tasks <slug> --local` still runs the named slug.

The size gate still applies to the batch path: `/do-tasks --all --local` with a highest-ranked task whose `size > auto_execute_max_size` **reserves** it (`--claim-only` semantics — claim and push, no execution) and reports it as reserved rather than executing it. A named `/do-tasks <slug> --local` is single-task mode and is never size-gated.

1. Create branch `task/<slug>` from current HEAD
2. Route by the size gate (batch path only): if `size <= auto_execute_max_size`, claim, execute, validate, delete, commit, push, and open PR as described above; if `size > auto_execute_max_size`, **reserve** it (claim + push only — no execute, delete, or PR). A named `/do-tasks <slug> --local` is single-task mode and always executes in full.
3. Return to the original branch with `git checkout -`

There is no remote session in this mode — report the PR opened in-session instead.

This is useful for testing or when cloud sessions aren't available.
