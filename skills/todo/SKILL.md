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
handler: repo-pr   # repo-pr (default) | gh-issue | jira | linear
# handler-specific blocks (gh-issue / jira / linear) live under their own keys
```

Resolution: file absent or no `handler:` → `repo-pr`; unknown value → `/add-todo` stops and points to `/todo-config`. Every handler receives the same drafted todo (`title`, body, `priority`, `tags`, `source_branch`, `source_pr`, `is_blocked_by`, …) and returns the URL of what it created.

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

#### Handler: `linear`

Creates a Linear issue via the official Linear MCP server (`mcp__claude_ai_Linear__create_issue`), filed under a configured team and optionally attached to a project. Config:

```yaml
handler: linear
linear:
  team: ENG                # required — team key or id
  default_project: null    # optional; skips the project prompt (project UUID, not name)
  default_priority: 3      # 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low
```

Requires the Linear MCP to be connected in Claude Code (`https://mcp.linear.app/mcp`) and the configured `team` to be in the user's accessible teams; stops with guidance otherwise. Lists the team's active projects for the user to pick a parent (unless `default_project` is set), maps the drafted todo to title + markdown description (with source footer), and returns the issue's `url`.

#### Setup: `/todo-config`

Configures the handler and writes `dev_docs/todos/.todo-config.yml`. Shows the current config, prompts for the destination, verifies prerequisites (`gh` auth for `gh-issue`; Atlassian MCP connectivity for `jira`; Linear MCP connectivity for `linear`), and delegates interactive logins to the user. Run it before using a non-default handler.

> **`/process-todo` and `/list-todos` only operate on `repo-pr` (file-based) todos.** For the `gh-issue`, `jira`, and `linear` handlers, lifecycle and tracking live in the external tool — read-back/sync is out of scope.

### Promote (`/promote-todos`)

1. Scans `dev_docs/todos/**/*.md` for todos in `status: new`
2. Scores each against the confidence check (see Kanban columns below)
3. HIGH confidence → flips `status: ready`. LOW confidence → flips `status: needs_refinement` and sets `human_approval_requested: true`
4. Never touches todos already past `new` — humans own demotions from `ready`

### Process (`/process-todo`)

1. Scans `dev_docs/todos/**/*.md` for todos in `status: ready` and filters out ones still waiting on `is_blocked_by`
2. For each selected todo, dispatches a remote Claude session that:
   - Claims the todo (branch `todo/<slug>`, sets `status: in_progress`)
   - Does the work described in the Task section
   - Deletes the todo file and opens a PR labeled `todo-loop` (the open PR is the implicit `needs_review` signal; merge is the implicit `done` signal — neither is written back to the file because the file is gone by then)
3. Todos with `is_blocked_by` set wait until the referenced slug no longer exists as a todo file
4. Multiple dependency-ready todos can be dispatched in parallel — each gets its own cloud VM

### List (`/list-todos`)

Renders a kanban view grouped by `status` column with priority, dependency blockers, tags, and expiry.

## Todo file format

Files live in `dev_docs/todos/` (supports subdirectories). Markdown with YAML frontmatter.

```markdown
---
title: Imperative description under 80 chars
priority: low
status: new
created: 2026-03-23
source_branch: bestdan/feat/example
source_pr: 42
related_files:
  - path/to/relevant/file.ts
  - path/to/another/file.ts
is_blocked_by: fix-broken-import
expires: 2026-04-22
tags:
  - cleanup
human_approval_requested: false
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
| `priority`      | yes      | `low` / `medium` / `high` / `urgent` (urgent = human-only) |
| `status`        | yes      | `new` / `needs_refinement` / `ready` / `in_progress` / `blocked` / `needs_review` / `done` |
| `created`       | yes      | ISO date                                                |
| `source_branch` | yes      | Branch where todo was identified                        |
| `source_pr`     | no       | PR number if already open                               |
| `related_files` | yes      | Paths the consumer should read for context. May be empty if `tags` includes `scope: research` |
| `is_blocked_by` | no       | Slug/id of another todo that must be completed first    |
| `expires`       | yes      | ISO date. Default: 30 days from creation.               |
| `tags`          | no       | Freeform tags for filtering (e.g., `cleanup`, `tests`)  |
| `human_approval_requested` | no | Forces card into `needs_refinement` until a human flips it back |

### Body sections

- **Context** (required) — Why this exists. What you saw.
- **Task** (required) — Concrete steps. Specific enough for an agent to execute.
- **Acceptance Criteria** (required, ≥ 1 bullet) — Definition of done. Missing or empty section blocks promotion to `ready`.
- **Open Questions** / **TBD** (optional) — Presence of either with non-empty content blocks promotion to `ready`.

## Kanban columns

The seven `status` values form a kanban flow. Cards move between columns via specific actions:

| Column | Card enters when… | Card leaves when… |
|---|---|---|
| `new` | `/add-todo` writes the card | `/promote-todos` scores it |
| `needs_refinement` | Promoter scored LOW, or human demoted from `ready` | Human edits the card, clears `human_approval_requested`, AND sets `status: ready` (the promoter does not re-scan past `new`) |
| `ready` | Promoter scored HIGH | `/process-todo` claims it |
| `in_progress` | Claim (branch + status flip) | PR opened (file is deleted in that PR) |
| `blocked` | Agent or human sets it with a `Consumer Notes` reason | Blocker resolved → returns to `in_progress` |
| `needs_review` | PR opened from `todo/<slug>` branch | PR merged or closed |
| `done` | PR merged | terminal |

> **`needs_review` and `done` are PR-derived for the `repo-pr` handler.** The todo file is deleted as part of opening the PR, so it cannot carry these statuses in the file system. `/list-todos` populates these two columns by querying `gh pr list --label todo-loop --state open` (needs_review) and `--state merged` (recent done). For external handlers (Linear, Jira, GH Issues) the external tool carries the state directly.

### Confidence check (used by `/promote-todos`)

- **HIGH** (→ `ready`): all required fields present; Acceptance Criteria section has ≥ 1 bullet; body contains no `Open Questions` / `TBD` section with content; `priority` ≠ `urgent`; no scope-keyword red flags (`refactor`, `migrate`, `redesign`, `rewrite`, `overhaul`) suggesting > 5 files.
- **LOW** (→ `needs_refinement`, set `human_approval_requested: true`): any of the above fails, or `human_approval_requested` is already true.

Note: `is_blocked_by` is intentionally **not** checked here. `/process-todo`'s runtime filter already skips dependency-blocked cards, and re-evaluating blockers would strand otherwise-ready cards in `needs_refinement` forever (the promoter only scans `status: new`).

## Lifecycle

```
new --> needs_refinement <--> ready --> in_progress --> needs_review --> done
                                           |                                
                                           +--> blocked --> in_progress     
                                                                            
expired (auto-pruned once the `expires` date passes while status is non-terminal; default expires = 30 days from creation, see Field reference)
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
