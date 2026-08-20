---
description: Render tasks as a kanban board grouped into vertical sections by status — dispatches to the configured handler (repo-pr files, or external trackers like Linear)
allowed-tools: Bash(git *), Bash(gh *), Bash(find *), Bash(grep *), Bash(cat *), Glob, Grep, Read, AskUserQuestion, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__linear__list_teams, mcp__linear__list_projects, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__searchJiraIssuesUsingJql
argument-hint: [
  filter: new|needs_refinement|ready|in_progress|blocked|needs_review|expired|all,
]
---

# List Tasks

Render tasks in `dev_docs/tasks/` as a vertical kanban view — one section per status, stacked top to bottom. Text UIs don't render side-by-side columns reliably, so the kanban "columns" are presented as sequential sections instead.

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/` directory exists, run the **Legacy migration** prompt from `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

## Steps

### 1. Resolve the handler

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `commands/task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

- File absent, or no `handler:` key → `repo-pr` (default). Continue to step 2 below (file-based path).
- `handler: repo-pr` → continue to step 2 below (file-based path).
- `handler: gh-issue | jira` → **dispatch to the handler.** Read `commands/handlers/<handler>.md` and follow its `## List` section, passing `$ARGUMENTS` (the optional status filter) through. Both handlers ship a `## List` section (gh-issue via `gh issue list`, jira via a JQL query). The handler owns all tracker-specific querying and renders the same vertical-section kanban layout described in step 4. Skip steps 2–4 of this file.

- `handler: linear` → **dispatch to the Linear handler.** Read `commands/handlers/linear-common.md` (shared config/preflight/kanban mapping) and `commands/handlers/linear-list.md` (the list flow), passing `$ARGUMENTS` through. Skip steps 2–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/<handler>.md` or `**/commands/handlers/linear-*.md`) and Read the result. Do not read handler files for handlers other than the resolved one.

- Any other (unknown) value → stop with: "Unknown task handler `<value>` in dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

### 2. Find task files

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/tasks" -name '*.md' -type f \
  -not -path '*/_archive/*' 2>/dev/null
```

The `-not -path '*/_archive/*'` guard skips `dev_docs/tasks/_archive/`, where
`/archive-tasks` parks retired `done` task files (see
`commands/handlers/repo-pr-archive.md`).

If the directory doesn't exist or is empty, report "No tasks found in this repo."

### 3. Parse and filter

Run the deterministic scanner — the single executable implementation of the file-path scan → parse → classify → **readiness** → **expiry** → **rank** procedure (canonical rules in `skills/task/SKILL.md`), so this view no longer re-derives that arithmetic by hand:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/task-scan.py" "$(git rev-parse --show-toplevel)/dev_docs/tasks"
```

It emits one JSON document. From it:

- **Task cards** come already grouped under `cards` by status, each carrying `title`, `priority`, `size`, `impact`, `status`, `created`, `expires`, `tags`, `is_blocked_by`, `parent`, `human_approval_requested`, plus the computed `rank` (within its status group), `dependency_ready` + `unresolved_blockers`, and `expired`. `type: epic` files and files with no frontmatter are excluded automatically; a malformed frontmatter is a **fail-closed non-zero exit** (a hard stop, not a silent skip). `assignee` is not in the JSON — read it from the card's frontmatter when annotating (step 4).
- **`expired`** is already computed (`expires` < today while `status` is non-terminal) — no need to recompute it.
- **Dependency-blocked** is `dependency_ready: false`; the still-active blockers are in `unresolved_blockers` (the script resolves each `is_blocked_by` slug — a string or a list — against `dev_docs/tasks/**/*.md`, satisfied when the target is absent or `done`). This is distinct from `status: blocked`, which means someone tried to process the task and hit a problem. Surface **all** `unresolved_blockers` in the annotation (e.g. `waiting on a, b`).

**Epic rollups.** The script's `epics` array already rolls up each epic's file-based members (by `parent:` and by plan-directory-tree membership, recursive) with `done`/`in_progress`/`blocked`/`member_count` tallies from member **files**. That covers the file side; the PR-derived supplement below (merged/open `task-loop` PRs for deleted-on-merge task files) is **not** in the script — combine it in yourself. For each epic, its slug and members come from two sources, keyed by **task slug** (a member's slug is its task-file stem, and its work-PR branch is `task/<slug>`):

- **Task files** — the members the script's `epics[].members` already lists: cards whose `parent` equals the epic slug, **or** that live anywhere in the epic's plan directory tree (`<name>_plan/`, recursive — nested `phase_N/` subdirectories count too), with the script's file-based `done`/`in_progress`/`blocked` tallies.
- **Epic PRs** — `task-loop` PRs whose head branch matches the prefix `task/<epic_slug>_` (the `plan-with-docs` `<name>_task_N` naming). Reuse the same `gh pr list --label task-loop` results step 4 already fetches (both include `headRefName`). A **merged** matched PR means that member is `done`; an **open** matched PR means it is in flight (in review).

Combine the two sources by slug so each member is counted once, preferring the most-advanced signal (merged PR `done` > open PR in-flight > file status). Then tally:

- `done` = matched merged PRs (+ any member file explicitly `status: done`, uncommon)
- `in_progress` = matched open PRs + member files with `status: in_progress`
- `blocked` = member files with `status: blocked`
- `total` = distinct members across both sources (present files + matched PRs)

Because the merged-PR query is bounded (step 4 fetches a recent window), `done` is best-effort for very old epics. And branch matching only catches the `<name>_task_N` plan naming: a standalone task added to an epic via `parent:` (branch `task/<own-slug>`) counts as `done` only while its file is present, then drops once merged-and-deleted. Plan-generated epics — the common case — roll up accurately (see **Epics** in `skills/task/SKILL.md`).

If `$ARGUMENTS` is provided, filter to that status (or `expired`). Default: show every section that has cards.

### 4. Display as stacked sections

**Epics rollup (top of board).** When epic files are present and the view is unfiltered — the default view or `$ARGUMENTS` = `all` (omit it when filtering to a single status) — print an `## Epics` section **above** the `new` section, one line per epic:

```
## Epics

- Task Loop Improvements (task_loop_improvements): 0/14 done (2 in progress, 1 blocked) — owner @dan, active
```

Format each line as `<title> (<slug>): <done>/<total> done (<in_progress> in progress, <blocked> blocked) — owner @<owner>, <status>`. Omit the `(… in progress, … blocked)` parenthetical when both are zero. When the epic has no `owner`, drop the `owner @<owner>` segment entirely — render the trailing as `… — <status>` (never `@` with no handle or `@None`). Separate the Epics section from the status sections with a horizontal rule (`---`). Omit the whole section when there are no epic files.

Then print one section per status in this fixed order, top to bottom, omitting empty sections:

`new` → `needs_refinement` → `ready` → `in_progress` → `blocked` → `needs_review` → `done`

The first five sections come from task files (status field). `needs_review` and `done` are **PR-derived** — the file is deleted when `/do-tasks` opens the PR, so there is no file to source those statuses from. Populate them by running, in parallel with the file scan:

```bash
gh pr list --label task-loop --state open  --json number,title,headRefName,updatedAt   # → needs_review
gh pr list --label task-loop --state merged --limit 30 --json number,title,headRefName,mergedAt  # → done (recent) + epic rollup
```

Skip those two `gh` calls (and the two sections) if `gh` is unavailable or unauthenticated. When they're skipped, the **Epics rollup degrades to file-only counts**: `done` reflects only member files explicitly `status: done`, and `in_progress`/`blocked`/`total` come from present member files (the PR-derived done/in-flight contributions are simply absent).

Within each section, order cards by the scanner's `rank` (ascending) — it already encodes priority (urgent > high > medium > low), then **value/effort score** `impact / size` descending (a card with no `impact` set, or a missing/invalid `size`, has no score and sorts last within its priority tier), then age (oldest first), matching the **Ranking** in `skills/task/SKILL.md` and `/do-tasks` selection. Render each card as a single line, including its `size` (Fibonacci points), and `assignee` inline as `— @<name>` when present. Separate sections with a horizontal rule (`---`) so they're clearly distinct in a terminal:

```
## ready (2)

- [high] (size 3) Fix broken import in utils.ts — @dan — created 2026-03-20, expires 2026-04-19  [cleanup]
- [low]  (size 1) Remove stale foobar alias — waiting on fix-broken-import  [cleanup, zsh]

---

## needs_refinement (1)

- [medium] (size 5) Add missing test for parser — human-approval-requested  [tests]

---

## in_progress (1)

- [high] (size 2) Migrate config loader — @dan — claimed on bestdan/migrate-config
```

Annotations to surface inline when present: `@<name>` (assignee), `human-approval-requested`, `waiting on <slug>`, `expired`, `claimed on <branch>`.

Finish with a summary line:

```
8 cards (1 new, 1 needs_refinement, 2 ready, 1 in_progress, 0 blocked, 2 needs_review, 1 done; 1 expired, 1 waiting on dependencies)
```
