---
name: todo
description: Capture and process follow-up work discovered during development. Use when a user notices incidental work (stale config, tech debt, dead code, test gaps) they want to defer without losing context. Provides the todo file format, creation workflow, and processing logic via remote Claude sessions.
---

# Todo Loop — Capture and Process Follow-Up Work

Repo-native system for capturing follow-up work with full context and processing it automatically via remote Claude sessions.

## When to use

- User notices incidental work during a feature branch (stale flags, dead code, missing tests)
- User says "todo", "follow-up", "we should come back to this", "add a todo for this"
- User runs `/add-todo` or `/process-todo`

## How it works

### Capture (`/add-todo`)

1. Gathers context from the current session (branch, diff, PR, conversation)
2. Drafts a structured todo, presents it for user review
3. Resolves the **handler** from `dev_docs/todos/.todo-config.yml` (absent → `repo-pr`)
4. Delivers the todo via that handler and reports the artifact URL

Capture is destination-agnostic; only the handler decides where the todo lands.

### Handlers and config

The delivery destination is a **handler** named in a repo-committed config file, `dev_docs/todos/.todo-config.yml`:

```yaml
handler: repo-pr   # repo-pr (default) | gh-issue | jira
# handler-specific blocks (gh-issue / jira) live under their own keys
```

Resolution: file absent or no `handler:` → `repo-pr`; unknown value → `/add-todo` stops and points to `/todo-config`. Every handler receives the same drafted todo (`title`, body, `priority`, `tags`, `source_branch`, `source_pr`, …) and returns the URL of what it created.

#### Handler: `repo-pr` (default)

Reproduces the original behavior. Dispatches an agent to:
- Create a branch from main (`todo/add/<slug>`)
- Write the todo file to `dev_docs/todos/<slug>.md`
- Open a PR labeled `todo-add` (an auto-merge workflow lands it on main, decoupled from the feature branch)

**Zero local impact.** No files written, no branches created, no staging.

**Fallback modes** (automatic cascade, `repo-pr` only): `--remote` (cloud VM) → `--subagent` (GitHub API via sub-agent, zero local git impact) → `--local` (stage into current branch). If `gh auth status` fails, skip straight to `--local`. Do NOT pass `--print` to `claude --remote`. This cascade applies only to `repo-pr`; other handlers are single foreground calls (`gh-issue` via the `gh` CLI, `jira` via the Atlassian MCP).

#### Handler: `gh-issue`

Creates a GitHub Issue via `gh issue create` (foreground, no git plumbing). Config:

```yaml
handler: gh-issue
gh-issue:
  repo: owner/name      # optional; defaults to current repo
  labels: [follow-up]   # optional
  assignees: []         # optional
```

Requires working `gh` auth; on auth failure it stops with guidance rather than falling back. The drafted todo's body plus a source-branch/PR footer becomes the issue body; the handler returns the new issue URL.

#### Handler: `jira`

Creates a Jira work item via the Atlassian MCP server (`mcp__claude_ai_Atlassian__createJiraIssue`), placed under a selected epic. Config:

```yaml
handler: jira
jira:
  site: mycompany.atlassian.net
  project: PLAT            # required
  issue_type: Task         # default Task
  default_epic: PLAT-100   # optional; skips the epic prompt
  labels: []
```

Requires the Atlassian MCP to be connected in Claude Code and the configured `site` to be in the user's accessible resources; stops with guidance otherwise. Lists the project's open epics via JQL for the user to pick a parent, maps the drafted todo to summary + description (with source footer), and returns the `https://<site>/browse/<KEY>` URL.

#### Setup: `/todo-config`

Configures the handler and writes `dev_docs/todos/.todo-config.yml`. Shows the current config, prompts for the destination, verifies prerequisites (`gh` auth for `gh-issue`; Atlassian MCP connectivity for `jira`), and delegates interactive logins to the user. Run it before using a non-default handler.

> **`/process-todo` and `/list-todos` only operate on `repo-pr` (file-based) todos.** For the `gh-issue` and `jira` handlers, lifecycle and tracking live in the external tool — read-back/sync is out of scope.

### Process (`/process-todo`)

1. Scans `dev_docs/todos/**/*.md` for unclaimed todos
2. For each selected todo, dispatches a remote Claude session that:
   - Claims the todo (branch `todo/<slug>`, sets `status: claimed`)
   - Does the work described in the Task section
   - Deletes the todo file
   - Opens a PR labeled `todo-loop`
3. Multiple todos can be dispatched in parallel — each gets its own cloud VM

### List (`/list-todos`)

Quick status check. Shows all todos with priority, status, tags, and expiry.

## Todo file format

Files live in `dev_docs/todos/` (supports subdirectories). Markdown with YAML frontmatter.

```markdown
---
title: Imperative description under 80 chars
priority: low
status: unclaimed
created: 2026-03-23
source_branch: bestdan/feat/example
source_pr: 42
related_files:
  - path/to/relevant/file.ts
  - path/to/another/file.ts
expires: 2026-04-22
tags:
  - cleanup
---

## Context

Why this exists. What you saw. Written for someone who has never seen this code.

## Task

1. Concrete step one
2. Concrete step two
3. Run tests

## Acceptance Criteria

- No remaining references to X
- Tests pass
```

### Field reference

| Field           | Required | Description                                             |
| --------------- | -------- | ------------------------------------------------------- |
| `title`         | yes      | Imperative description, < 80 chars                      |
| `priority`      | yes      | `low` / `medium` / `high`                               |
| `status`        | yes      | `unclaimed` / `claimed` / `blocked`                     |
| `created`       | yes      | ISO date                                                |
| `source_branch` | yes      | Branch where todo was identified                        |
| `source_pr`     | no       | PR number if already open                               |
| `related_files` | yes      | Paths the consumer should read for context              |
| `expires`       | yes      | ISO date. Default: 30 days from creation.               |
| `tags`          | no       | Freeform tags for filtering (e.g., `cleanup`, `tests`)  |

### Body sections

- **Context** (required) — Why this exists. What you saw.
- **Task** (required) — Concrete steps. Specific enough for an agent to execute.
- **Acceptance Criteria** (optional) — Definition of done.

## Lifecycle

```
unclaimed --> claimed --> PR opened --> merged (todo file deleted)
    |             |
    |             +--> blocked (needs manual intervention)
    |
    +--> expired (auto-pruned after 30 days)
```

## Branch naming

Two namespaces to avoid collisions:

- `todo/add/<slug>` — the PR that adds the todo file (auto-merged)
- `todo/<slug>` — the PR that does the work and deletes the todo file

## Scanning

Always scan recursively: `dev_docs/todos/**/*.md`. Subdirectories are optional organizational structure.

## Race conditions

Each remote session gets its own isolated VM with a fresh clone. Filesystem races are impossible. The only contention point is `git push`:

1. Branch names are deterministic: `todo/<slug>`
2. `git push` is atomic — second push fails
3. On push failure, skip this todo and move to the next unclaimed one

## Remote session notes

Remote sessions (`claude --remote`) run in cloud VMs and don't have access to locally-installed plugins. The `/add-todo` and `/process-todo` commands handle this by embedding all necessary instructions directly in the remote prompt. The remote agent doesn't need to know about this plugin — it just follows the instructions in its prompt.
