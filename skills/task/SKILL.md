---
name: task
description: Capture and process follow-up work discovered during development. Use when a user notices incidental work (stale config, tech debt, dead code, test gaps) they want to defer without losing context. Provides the task file format, creation workflow, and processing logic via remote Claude sessions.
---

# Task Loop — Capture and Process Follow-Up Work

Repo-native system for capturing follow-up work with full context and processing it automatically via remote Claude sessions.

## When to use

- User notices incidental work during a feature branch (stale flags, dead code, missing tests)
- User says "task", "todo", "follow-up", "we should come back to this", "add a task for this"
- User runs `/add-task`, `/do-tasks`, or `/process-tasks`

## How it works

### Capture (`/add-task`)

1. Gathers context from the current session (branch, diff, PR, conversation)
2. Drafts a structured task, presents it for user review
3. Resolves the **handler** from `dev_docs/tasks/.task-config.yml` (absent → `repo-pr`)
4. Delivers the task via that handler and reports the artifact URL

Capture is destination-agnostic; only the handler decides where the task lands.

### Handlers and config

The delivery destination is a **handler** named in a repo-committed config file, `dev_docs/tasks/.task-config.yml`:

```yaml
handler: repo-pr # repo-pr (default) | gh-issue | jira | linear
wip_limit: 3 # optional (repo-pr) — caps /process-tasks --all batch dispatch; default 3
# handler-specific blocks (gh-issue / jira / linear) live under their own keys
```

Resolution: file absent or no `handler:` → `repo-pr`; unknown value → `/add-task` stops and points to `/task-config`. Every handler receives the same drafted task (`title`, body, `priority`, `tags`, `source_branch`, `source_pr`, `is_blocked_by`, …) and returns the URL of what it created.

`wip_limit` (repo-pr, default `3`) bounds **batch** dispatch only. `/process-tasks --all` counts current work-in-flight — tasks with `status: in_progress` plus open `task-loop` PRs (the `needs_review` queue) — and dispatches at most `wip_limit - current_wip` tasks, holding the rest so the human PR-review bottleneck stays bounded. Single-task dispatch ignores it. See `commands/handlers/repo-pr-config.md`.

Available handlers — each owns its own auth/preflight, config schema, prerequisites, and limitations:

| Handler    | Lands the task as…                                              | Reference file                                                                                         |
| ---------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `repo-pr`  | a committed markdown file in `dev_docs/tasks/` via PR (default) | `commands/handlers/repo-pr.md`                                                                         |
| `gh-issue` | a GitHub Issue                                                  | `commands/handlers/gh-issue.md`                                                                        |
| `jira`     | a Jira work item under an epic                                  | `commands/handlers/jira.md`                                                                            |
| `linear`   | a Linear issue under a team                                     | `commands/handlers/linear-common.md` + per-verb `linear-add.md` / `linear-list.md` / `linear-claim.md` |

Set the handler with `/task-config` (which dispatches to `commands/handlers/<handler>-config.md`).

> Different handlers support different downstream commands. `/process-tasks` is file-only (`repo-pr`). `/list-tasks` and `/claim-task` dispatch to whichever handler is configured, but a handler may legitimately decline a verb (e.g. defer the user back to `/process-tasks --local` for file-based tasks). The handler files document what they do and don't support.

### Promote (`/promote-tasks`)

1. Scans `dev_docs/tasks/**/*.md` for tasks in `status: new`
2. Scores each against the confidence check (see Kanban columns below)
3. HIGH confidence → flips `status: ready`. LOW confidence → flips `status: needs_refinement` and sets `human_approval_requested: true`
4. Never touches tasks already past `new` — humans own demotions from `ready`

### Execute (`/do-tasks`)

`/do-tasks` is the primary verb for turning ready tasks into PRs. It resolves the
handler from `.task-config.yml` and dispatches like `/add-task` / `/list-tasks`:

- `/do-tasks` — single highest-ranked dependency-ready task
- `/do-tasks --all` / `/do-tasks -n N` — batch (remote by default; `--local` caps at 1), bounded by `wip_limit`
- `/do-tasks --remote` (default) / `/do-tasks --local` — where execution runs

For the `repo-pr` handler it fully subsumes `/process-tasks` (same scan, ranking,
multi-blocker readiness, WIP cap, and remote/`--local` mechanics). The tracker
path is being folded in; until then, the `linear` handler defers to `/claim-task`
(jira/gh-issue have no execute path yet). See `commands/do-tasks.md`.

> `/process-tasks` and `/claim-task` still work and remain the authoritative
> reference for the file and tracker mechanics respectively (they are removed in a
> later task). The comparison below documents that underlying split.

#### `/process-tasks` vs `/claim-task` — the underlying split

Two commands turn captured tasks into PRs. Pick before you invoke; they have different shapes:

|                   | `/process-tasks`                                         | `/claim-task`                                                              |
| ----------------- | -------------------------------------------------------- | -------------------------------------------------------------------------- |
| Where it runs     | **Remote** cloud agents, one per task                    | **Foreground** in the current session                                      |
| How many per call | Batch — one, several, or all dependency-ready            | At most one                                                                |
| Where tasks live  | File-based (`repo-pr`) only                              | Tracker-side (Linear today; file-based defers to `/process-tasks --local`) |
| Selection         | Anything in `status: ready` whose dependencies are clear | Model-judged feasibility — "can I finish this in-session?"                 |
| Best for          | Draining a known-ready backlog headlessly (`--all`)      | Pulling one card to pair on with the agent watching                        |

#### Process (`/process-tasks`)

1. Scans `dev_docs/tasks/**/*.md` for tasks in `status: ready` and filters out ones still waiting on `is_blocked_by` (a task is dependency-ready only when **every** blocker is resolved)
2. For each selected task, dispatches a remote Claude session that:
   - Claims the task (branch `task/<slug>`, sets `status: in_progress`)
   - Does the work described in the Task section
   - Deletes the task file and opens a PR labeled `task-loop` (the open PR is the implicit `needs_review` signal; merge is the implicit `done` signal — neither is written back to the file because the file is gone by then)
3. Tasks with `is_blocked_by` set wait until **all** referenced slugs are resolved (each no longer exists as a task file, or exists with `status: done`); `is_blocked_by` may be a single slug or a list (`[a, b]`)
4. Multiple dependency-ready tasks can be dispatched in parallel — each gets its own cloud VM

#### Claim (`/claim-task`)

Pulls one tracker-side task the current session can plausibly finish, claims it, branches, executes, and opens a PR — all in the foreground.

1. Resolves the handler from `.task-config.yml` and dispatches to it. Handlers that don't support `/claim-task` stop with guidance (e.g. file-based defers to `/process-tasks --local <slug>`).
2. Asks the handler for unclaimed, small-enough candidates.
3. Walks candidates in priority order and asks the model "can I finish this in this session without a human?" — first feasible candidate wins. Rejected candidates get a one-line skip comment in the tracker.
4. Handler claims the chosen candidate atomically (concurrency guard against parallel claims).
5. Branches from `<base>` (default `main`) using the handler-published branch name, does the work, opens a PR with a tracker-link in the body so the merge automatically marks the work done.
6. Bail path (mid-execution infeasibility): handler unclaims and flags for human review; `/claim-task` stops without silently rolling to the next candidate.

Handler-specific details (which states/labels are used, how the PR is linked back) live in `commands/handlers/<handler>-claim.md`.

### List (`/list-tasks`)

Renders a kanban view grouped by `status` column with priority, dependency blockers, tags, and expiry.

## Task size

Every task carries a **size** — a Fibonacci story-point estimate of its scope: `1`, `2`, `3`, or `5`. `5` is the ceiling: **one task = one PR**, roughly ≤ ~300 lines of diff across ≤ ~5 files. If a task estimates larger than `5`, it is too big to capture as one card — break it into sub-tasks and chain them with `is_blocked_by`.

| size  | meaning                                                        |
| ----- | -------------------------------------------------------------- |
| `1`   | trivial — a few lines in one file                              |
| `2`   | small — a contained change across one or two files             |
| `3`   | moderate — several files, still one focused PR                 |
| `5`   | large — the upper bound of a single PR (~300 lines / ~5 files) |
| `> 5` | too big — do **not** capture as one task; split into sub-tasks |

This single scale governs the promotion confidence check (below), the `/claim-task` feasibility gate (which maps to a tracker's native estimate — Linear's `estimate` is the same Fibonacci scale), and the granularity rule in `plan-with-docs`. They point here rather than restating a threshold.

## Task file format

Files live in `dev_docs/tasks/` (supports subdirectories). Markdown with YAML frontmatter.

```markdown
---
title: Imperative description under 80 chars
priority: low
size: 2
impact: 3
status: new
created: 2026-03-23
source_branch: bestdan/feat/example
source_pr: 42
related_files:
  - path/to/relevant/file.ts
  - path/to/another/file.ts
is_blocked_by: [fix-broken-import, other-task]
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

| Field                      | Required | Description                                                                                                                |
| -------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------- |
| `title`                    | yes      | Imperative description, < 80 chars                                                                                         |
| `priority`                 | yes      | `low` / `medium` / `high` / `urgent` (urgent = human-only)                                                                 |
| `assignee`                 | no       | Human or agent accountable for the task. Mirrors gh-issue/Linear assignee for handler parity                               |
| `size`                     | yes      | Fibonacci story points: `1` / `2` / `3` / `5`. Larger ⇒ split into sub-tasks. See **Task size**                            |
| `impact`                   | no       | Fibonacci value estimate: `1` / `2` / `3` / `5`, mirroring `size`. Used for value/effort ranking. See **Task size**        |
| `status`                   | yes      | `new` / `needs_refinement` / `ready` / `in_progress` / `blocked` / `needs_review` / `done`                                 |
| `created`                  | yes      | ISO date                                                                                                                   |
| `source_branch`            | yes      | Branch where task was identified                                                                                           |
| `source_pr`                | no       | PR number if already open                                                                                                  |
| `related_files`            | yes      | Paths the consumer should read for context. May be empty if `tags` includes `scope: research`                              |
| `is_blocked_by`            | no       | Slug/id of a blocker, or a list of slugs (`[a, b]`). A single string stays valid. Ready only when **all** blockers resolve |
| `parent`                   | no       | Slug of an epic this task belongs to (grouping, distinct from `is_blocked_by` ordering)                                    |
| `expires`                  | yes      | ISO date. Default: 30 days from creation.                                                                                  |
| `tags`                     | no       | Freeform tags for filtering (e.g., `cleanup`, `tests`)                                                                     |
| `human_approval_requested` | no       | Forces card into `needs_refinement` until a human flips it back                                                            |

### Body sections

- **Context** (required) — Why this exists. What you saw.
- **Task** (required) — Concrete steps. Specific enough for an agent to execute.
- **Acceptance Criteria** (required, ≥ 1 bullet) — Definition of done. Missing or empty section blocks promotion to `ready`.
- **Open Questions** / **TBD** (optional) — Presence of either with non-empty content blocks promotion to `ready`.

## Kanban columns

The seven `status` values form a kanban flow. Cards move between columns via specific actions:

| Column             | Card enters when…                                     | Card leaves when…                                                                                                            |
| ------------------ | ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `new`              | `/add-task` writes the card                           | `/promote-tasks` scores it                                                                                                   |
| `needs_refinement` | Promoter scored LOW, or human demoted from `ready`    | Human edits the card, clears `human_approval_requested`, AND sets `status: ready` (the promoter does not re-scan past `new`) |
| `ready`            | Promoter scored HIGH                                  | `/process-tasks` claims it                                                                                                   |
| `in_progress`      | Claim (branch + status flip)                          | PR opened (file is deleted in that PR)                                                                                       |
| `blocked`          | Agent or human sets it with a `Consumer Notes` reason | Blocker resolved → returns to `in_progress`                                                                                  |
| `needs_review`     | PR opened from `task/<slug>` branch                   | PR merged or closed                                                                                                          |
| `done`             | PR merged                                             | terminal                                                                                                                     |

> **`needs_review` and `done` are PR-derived for the `repo-pr` handler.** The task file is deleted as part of opening the PR, so it cannot carry these statuses in the file system. `/list-tasks` populates these two columns by querying `gh pr list --label task-loop --state open` (needs_review) and `--state merged` (recent done). For external handlers (Linear, Jira, GH Issues) the external tool carries the state directly.

### Confidence check (used by `/promote-tasks`)

- **HIGH** (→ `ready`): all required fields present; `size` is one of `1` / `2` / `3` / `5`; Acceptance Criteria section has ≥ 1 bullet; body contains no `Open Questions` / `TBD` section with content; `priority` ≠ `urgent`; and the promoter **judges** the described scope to plausibly fit within size `5` (~300 lines / ~5 files — see **Task size**), weighing the stated `size`, the `## Task` steps, and `related_files` breadth. This last gate is model judgment, not a keyword scan: a title merely containing "migrate"/"refactor" does not fail it, while one implying multi-file rework that exceeds size `5` does (reason: `scope exceeds size 5 — split into sub-tasks`).
- **LOW** (→ `needs_refinement`, set `human_approval_requested: true`): any of the above fails, or `human_approval_requested` is already true.

The scope gate is judgment, not a deterministic rule — acceptable because `/promote-tasks` is not a blocking CI gate; a misjudged card waits in `needs_refinement` for a human rather than being lost. The other HIGH checks remain deterministic.

Note: `is_blocked_by` is intentionally **not** checked here. `/process-tasks`'s runtime filter already skips dependency-blocked cards, and re-evaluating blockers would strand otherwise-ready cards in `needs_refinement` forever (the promoter only scans `status: new`).

## Lifecycle

```
new --> needs_refinement <--> ready --> in_progress --> needs_review --> done
                                           |                                
                                           +--> blocked --> in_progress     
                                                                            
expired (auto-pruned once the `expires` date passes while status is non-terminal; default expires = 30 days from creation, see Field reference)
```

## Branch naming

Two namespaces to avoid collisions:

- `task/add/<slug>` — the PR that adds the task file (auto-merged)
- `task/<slug>` — the PR that does the work and deletes the task file

## Scanning

Always scan recursively: `dev_docs/tasks/**/*.md`. Subdirectories are optional organizational structure.

## Legacy migration

Earlier versions of this system stored tasks under `dev_docs/todos/` (and `plan-with-docs` wrote plans under `dev_docs/todo/`). There is no dual-path support — instead, migrate once on contact.

If a command finds a legacy `dev_docs/todos/` (task store) or `dev_docs/todo/` (plans) directory, pause before proceeding and prompt once:

> Found legacy `dev_docs/todos/`. Migrate to `dev_docs/tasks/`? [migrate / skip once]

- **migrate**: `dev_docs/tasks/` may already exist (e.g. it holds `plan-with-docs` output), so do **not** `git mv` the legacy directory onto it — that nests the source inside the target (`dev_docs/tasks/todos/`) instead of merging. Instead: `mkdir -p dev_docs/tasks`, then `git mv` each **child** of the legacy directory into `dev_docs/tasks/` (resolving any name collision by keeping both, e.g. suffixing the incoming file), rename `.todo-config.yml` → `.task-config.yml` if present, and remove the now-empty legacy directory. Migrate `dev_docs/todos/` (task store) and `dev_docs/todo/` (plans) the same way, reporting each separately. Leave in-flight branches (`todo/<slug>`) and the `todo-loop` PR label untouched — they are historical and harmless.
- **skip once**: proceed without migrating and do not re-prompt during this invocation.

## Race conditions

Each remote session gets its own isolated VM with a fresh clone. Filesystem races are impossible. The only contention point is `git push`:

1. Branch names are deterministic: `task/<slug>`
2. `git push` is atomic — second push fails
3. On push failure, skip this task and move to the next unclaimed one

## Remote session notes

Remote sessions (`claude --remote`) run in cloud VMs and don't have access to locally-installed plugins. The `/add-task` and `/process-tasks` commands handle this by embedding all necessary instructions directly in the remote prompt. The remote agent doesn't need to know about this plugin — it just follows the instructions in its prompt.
