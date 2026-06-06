---
description: Process dependency-ready tasks — dispatches remote agents to claim, execute, and open PRs
allowed-tools: Bash(git *), Bash(gh *), Bash(claude *), Bash(find *), Bash(grep *), Glob, Grep, Read, Write, Edit
argument-hint: "[slug | --all | --local] or empty for highest priority"
---

# Process Tasks

Scan for dependency-ready tasks and dispatch remote Claude sessions to process them. Each task gets its own isolated cloud VM.

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/` directory exists, run the **Legacy migration** prompt from `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

## Modes

- `/process-tasks` — dispatch the highest priority dependency-ready task to a remote agent
- `/process-tasks <slug>` — dispatch a specific task
- `/process-tasks --all` — dispatch all dependency-ready tasks and skip ones still blocked by another task
- `/process-tasks --local` — process locally instead of dispatching (original behavior, useful for testing)

## Steps

### 1. Scan for tasks

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/tasks" -name '*.md' -type f 2>/dev/null
```

Parse YAML frontmatter from each file. Filter to `status: ready`. Sort by:

1. Dependency readiness: tasks whose `is_blocked_by` target is absent (or `done`) are eligible; tasks whose blocker is still active are not
2. Priority: `high` > `medium` > `low` (`urgent` is human-only and is never picked up here)
3. Age: oldest `created` date first

Treat `is_blocked_by` as a reference to another task's slug. The slug is satisfied when no task file with that slug exists under `dev_docs/tasks/**/*.md`, or it exists with `status: done`. If a referenced blocker file still exists in any other state, the dependent task must not be dispatched yet.

If no ready, dependency-ready tasks exist, report that and stop. Hint the user to run `/promote-tasks` if there are cards sitting in `new` or `needs_refinement`. If the only remaining ready tasks are waiting on dependencies, say which blocker each one is waiting for.

### 2. Select tasks to process

- Default: pick the single highest priority dependency-ready task
- With `<slug>`: find that specific task; if it is waiting on `is_blocked_by`, stop and report the blocker instead of dispatching it
- With `--all`: select all dependency-ready tasks and skip any that are still waiting on another task

### 3. Check dispatch prerequisites

Before dispatching, verify GitHub access:

```bash
gh auth status 2>&1
```

If this fails (token invalid, TLS errors, network issues), **stop** and tell the user — both remote and local modes call `gh pr create`, so a broken `gh` blocks both. If the error mentions TLS/x509/certificate, note it's likely Claude Code's sandbox blocking keychain access and suggest re-running outside sandbox mode.

### 4. Dispatch remote agents

For each selected task, read its full content (frontmatter + body), then dispatch a remote session.

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

When dispatching multiple tasks (`--all`), run the `claude --remote` commands in sequence (not background) so the user can see each session ID. Each remote session runs independently in its own cloud VM.

### 5. Report

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

If any tasks were skipped because they are waiting on another task, list them separately with their blocker slug.

## Local mode (`--local`)

When `--local` is specified or `claude --remote` is unavailable, process the task directly in the current session (`gh` is still required for `gh pr create`):

1. Create branch `task/<slug>` from current HEAD
2. Claim, execute, validate, delete, commit, push, and open PR as described above
3. Return to the original branch with `git checkout -`

This is useful for testing or when cloud sessions aren't available.
