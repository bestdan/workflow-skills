---
description: Process unclaimed todos — dispatches remote agents to claim, execute, and open PRs
allowed-tools: Bash(git *), Bash(gh *), Bash(claude *), Bash(find *), Bash(grep *), Glob, Grep, Read, Write, Edit
argument-hint: [slug | --all | --local] or empty for highest priority
---

# Process Todo

Scan for unclaimed todos and dispatch remote Claude sessions to process them. Each todo gets its own isolated cloud VM.

## Modes

- `/process-todo` — dispatch the highest priority unclaimed todo to a remote agent
- `/process-todo <slug>` — dispatch a specific todo
- `/process-todo --all` — dispatch all unclaimed todos in parallel (one remote session each)
- `/process-todo --local` — process locally instead of dispatching (original behavior, useful for testing)

## Steps

### 1. Scan for todos

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/todos" -name '*.md' -type f 2>/dev/null
```

Parse YAML frontmatter from each file. Filter to `status: unclaimed`. Sort by:
1. Priority: `high` > `medium` > `low`
2. Age: oldest `created` date first

If no unclaimed todos exist, report that and stop.

### 2. Select todos to process

- Default: pick the single highest priority todo
- With `<slug>`: find that specific todo
- With `--all`: select all unclaimed todos

### 3. Check dispatch prerequisites

Before dispatching, verify GitHub access:

```bash
gh auth status 2>&1
```

If this fails (token invalid, TLS errors, network issues):
1. Check if the error mentions TLS/x509/certificate — this usually means Claude Code's sandbox is blocking keychain access.
2. If it looks like a sandbox issue, tell the user: "gh is failing due to sandbox TLS restrictions. You can re-run this command outside sandbox mode, or I'll fall back to local processing."
3. Fall back to `--local` mode automatically unless the user opts to exit sandbox.

### 4. Dispatch remote agents

For each selected todo, read its full content (frontmatter + body), then dispatch a remote session.

The remote session prompt must be self-contained because the remote VM won't have this plugin installed. Include the todo content and all processing instructions inline.

**Important:** Do NOT pass `--print` to `claude --remote` — it is not supported.

```bash
claude --remote "You are processing a todo for the todo plugin system.

## The todo file

The file is at dev_docs/todos/<slug>.md with this content:

<paste full todo file content>

## Instructions

1. CLAIM: Create branch todo/<slug> and update the todo file status from 'unclaimed' to 'claimed'. Commit and push immediately.

   git checkout -b todo/<slug>
   # Edit dev_docs/todos/<slug>.md: change status: unclaimed -> status: claimed
   git add dev_docs/todos/<slug>.md
   git commit -m 'claim todo: <slug>'
   git push -u origin todo/<slug>

   If push fails because the branch exists, STOP — another agent claimed it.

2. EXECUTE: Read the Context and Task sections. Read all files listed in related_files. Do the work described in the Task section.

3. VALIDATE: Look for test infrastructure (Makefile, justfile, package.json). Run tests if available. Check acceptance criteria.

4. DELETE the todo file:
   git rm dev_docs/todos/<slug>.md

5. COMMIT and PUSH:
   git add only the files you changed (do NOT use git add -A — it may pick up untracked artifacts)
   git commit -m 'chore(todo): <title from frontmatter>

   Automated follow-up from todo <slug>.md.
   Source branch: <source_branch>'
   git push

6. OPEN PR:
   gh label create todo-loop --description 'Auto-generated from todo-loop' --color '0E8A16' 2>/dev/null
   gh pr create --title 'chore(todo): <title>' --label todo-loop --body '## Summary

   Automated follow-up from todo <slug>.md, created during branch <source_branch>.

   <bulleted list of what you did>

   ## Original Context

   > <quote the Context section from the todo>

   ## Test Plan

   - [ ] Tests pass
   - [ ] No new lint errors
   - [ ] Acceptance criteria met

   ---
   Source todo: dev_docs/todos/<slug>.md (deleted in this PR)'

## If you cannot complete the task

1. Edit the todo: change status to 'blocked'
2. Add a '## Consumer Notes' section explaining what you tried and what went wrong
3. Commit and push, but do NOT open a PR"
```

When dispatching multiple todos (`--all`), run the `claude --remote` commands in sequence (not background) so the user can see each session ID. Each remote session runs independently in its own cloud VM.

### 5. Report

For each dispatched todo, tell the user:
- The slug and title
- That a remote session has been started
- They can monitor all sessions with `/tasks`

Example output:
```
Dispatched 3 todos to remote agents:
  - remove-stale-alias (low) — remote session started
  - fix-broken-import (medium) — remote session started
  - add-missing-parser-test (high) — remote session started

Monitor with /tasks. Each will open a PR when complete.
```

## Local mode (`--local`)

When `--local` is specified, `gh auth status` fails, or `claude --remote` is unavailable, process the todo directly in the current session:

1. Create branch `todo/<slug>` from current HEAD
2. Claim, execute, validate, delete, commit, push, and open PR as described above
3. Return to the original branch with `git checkout -`

This is useful for testing or when cloud sessions aren't available.
