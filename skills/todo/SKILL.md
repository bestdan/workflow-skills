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
handler: repo-pr # repo-pr (default) | gh-issue | jira | linear
# handler-specific blocks (gh-issue / jira / linear) live under their own keys
```

Resolution: file absent or no `handler:` → `repo-pr`; unknown value → `/add-todo` stops and points to `/todo-config`. Every handler receives the same drafted todo (`title`, body, `priority`, `tags`, `source_branch`, `source_pr`, `is_blocked_by`, …) and returns the URL of what it created.

Available handlers — each owns its own auth/preflight, config schema, prerequisites, and limitations:

| Handler    | Lands the todo as…                                              | Reference file                                                                                         |
| ---------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `repo-pr`  | a committed markdown file in `dev_docs/todos/` via PR (default) | `commands/handlers/repo-pr.md`                                                                         |
| `gh-issue` | a GitHub Issue                                                  | `commands/handlers/gh-issue.md`                                                                        |
| `jira`     | a Jira work item under an epic                                  | `commands/handlers/jira.md`                                                                            |
| `linear`   | a Linear issue under a team                                     | `commands/handlers/linear-common.md` + per-verb `linear-add.md` / `linear-list.md` / `linear-claim.md` |

Set the handler with `/todo-config` (which dispatches to `commands/handlers/<handler>-config.md`).

> Different handlers support different downstream commands. `/process-todo` is file-only (`repo-pr`). `/list-todos` and `/claim-todo` dispatch to whichever handler is configured, but a handler may legitimately decline a verb (e.g. defer the user back to `/process-todo --local` for file-based todos). The handler files document what they do and don't support.

### Promote (`/promote-todos`)

1. Scans `dev_docs/todos/**/*.md` for todos in `status: new`
2. Scores each against the confidence check (see Kanban columns below)
3. HIGH confidence → flips `status: ready`. LOW confidence → flips `status: needs_refinement` and sets `human_approval_requested: true`
4. Never touches todos already past `new` — humans own demotions from `ready`

### Execute: `/process-todo` vs `/claim-todo` — which to use?

Two commands turn captured todos into PRs. Pick before you invoke; they have different shapes:

|                   | `/process-todo`                                          | `/claim-todo`                                                             |
| ----------------- | -------------------------------------------------------- | ------------------------------------------------------------------------- |
| Where it runs     | **Remote** cloud agents, one per todo                    | **Foreground** in the current session                                     |
| How many per call | Batch — one, several, or all dependency-ready            | At most one                                                               |
| Where todos live  | File-based (`repo-pr`) only                              | Tracker-side (Linear today; file-based defers to `/process-todo --local`) |
| Selection         | Anything in `status: ready` whose dependencies are clear | Model-judged feasibility — "can I finish this in-session?"                |
| Best for          | Draining a known-ready backlog headlessly (`--all`)      | Pulling one card to pair on with the agent watching                       |

#### Process (`/process-todo`)

1. Scans `dev_docs/todos/**/*.md` for todos in `status: ready` and filters out ones still waiting on `is_blocked_by`
2. For each selected todo, dispatches a remote Claude session that:
   - Claims the todo (branch `todo/<slug>`, sets `status: in_progress`)
   - Does the work described in the Task section
   - Deletes the todo file and opens a PR labeled `todo-loop` (the open PR is the implicit `needs_review` signal; merge is the implicit `done` signal — neither is written back to the file because the file is gone by then)
3. Todos with `is_blocked_by` set wait until the referenced slug no longer exists as a todo file
4. Multiple dependency-ready todos can be dispatched in parallel — each gets its own cloud VM

#### Claim (`/claim-todo`)

Pulls one tracker-side todo the current session can plausibly finish, claims it, branches, executes, and opens a PR — all in the foreground.

1. Resolves the handler from `.todo-config.yml` and dispatches to it. Handlers that don't support `/claim-todo` stop with guidance (e.g. file-based defers to `/process-todo --local <slug>`).
2. Asks the handler for unclaimed, small-enough candidates.
3. Walks candidates in priority order and asks the model "can I finish this in this session without a human?" — first feasible candidate wins. Rejected candidates get a one-line skip comment in the tracker.
4. Handler claims the chosen candidate atomically (concurrency guard against parallel claims).
5. Branches from `<base>` (default `main`) using the handler-published branch name, does the work, opens a PR with a tracker-link in the body so the merge automatically marks the work done.
6. Bail path (mid-execution infeasibility): handler unclaims and flags for human review; `/claim-todo` stops without silently rolling to the next candidate.

Handler-specific details (which states/labels are used, how the PR is linked back) live in `commands/handlers/<handler>-claim.md`.

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

| Field                      | Required | Description                                                                                   |
| -------------------------- | -------- | --------------------------------------------------------------------------------------------- |
| `title`                    | yes      | Imperative description, < 80 chars                                                            |
| `priority`                 | yes      | `low` / `medium` / `high` / `urgent` (urgent = human-only)                                    |
| `status`                   | yes      | `new` / `needs_refinement` / `ready` / `in_progress` / `blocked` / `needs_review` / `done`    |
| `created`                  | yes      | ISO date                                                                                      |
| `source_branch`            | yes      | Branch where todo was identified                                                              |
| `source_pr`                | no       | PR number if already open                                                                     |
| `related_files`            | yes      | Paths the consumer should read for context. May be empty if `tags` includes `scope: research` |
| `is_blocked_by`            | no       | Slug/id of another todo that must be completed first                                          |
| `expires`                  | yes      | ISO date. Default: 30 days from creation.                                                     |
| `tags`                     | no       | Freeform tags for filtering (e.g., `cleanup`, `tests`)                                        |
| `human_approval_requested` | no       | Forces card into `needs_refinement` until a human flips it back                               |

### Body sections

- **Context** (required) — Why this exists. What you saw.
- **Task** (required) — Concrete steps. Specific enough for an agent to execute.
- **Acceptance Criteria** (required, ≥ 1 bullet) — Definition of done. Missing or empty section blocks promotion to `ready`.
- **Open Questions** / **TBD** (optional) — Presence of either with non-empty content blocks promotion to `ready`.

## Kanban columns

The seven `status` values form a kanban flow. Cards move between columns via specific actions:

| Column             | Card enters when…                                     | Card leaves when…                                                                                                            |
| ------------------ | ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `new`              | `/add-todo` writes the card                           | `/promote-todos` scores it                                                                                                   |
| `needs_refinement` | Promoter scored LOW, or human demoted from `ready`    | Human edits the card, clears `human_approval_requested`, AND sets `status: ready` (the promoter does not re-scan past `new`) |
| `ready`            | Promoter scored HIGH                                  | `/process-todo` claims it                                                                                                    |
| `in_progress`      | Claim (branch + status flip)                          | PR opened (file is deleted in that PR)                                                                                       |
| `blocked`          | Agent or human sets it with a `Consumer Notes` reason | Blocker resolved → returns to `in_progress`                                                                                  |
| `needs_review`     | PR opened from `todo/<slug>` branch                   | PR merged or closed                                                                                                          |
| `done`             | PR merged                                             | terminal                                                                                                                     |

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
