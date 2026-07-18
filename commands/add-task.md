---
description: Capture follow-up work as a structured task, then deliver it to the configured destination (repo PR, GitHub issue, Jira, or Linear)
allowed-tools: Bash(git *), Bash(gh *), Bash(claude *), Bash(date *), Bash(cat *), Bash(find *), Bash(mkdir *), Glob, Grep, Read, Write, AskUserQuestion, Agent, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__createJiraIssue, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__createJiraIssue, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue, mcp__linear__list_teams, mcp__linear__list_projects, mcp__linear__list_workflow_states, mcp__linear__save_issue
argument-hint: [description of the follow-up work]
---

# Add Task

Capture follow-up work with full context, then deliver it to the destination configured for this repo. Capture is destination-agnostic; the **handler** resolved from `dev_docs/tasks/.task-config.yml` decides where the task lands. With no config, the default `repo-pr` handler reproduces the original behavior: dispatch an agent to commit the task file on a branch from main and open a PR, without touching local state.

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/` directory exists (older versions stored tasks there), run the **Legacy migration** prompt from `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

> **Auto-pilot caller.** The `/auto-pilot` orchestrator (via `/deliver-task`'s iterate step) calls this command unattended to file a co-review finding that is cross-cutting or still open at the 2-round bound, tagged `auto-pilot`, with `status: new` (never `ready` — `/promote-tasks`' human-confidence gate still applies). See `skills/auto-pilot/references/run-state.md` "`REPORT.md`" for the full rule.

## Steps

### 1. Gather context

Collect automatically (run these in parallel):

- Current branch: `git rev-parse --abbrev-ref HEAD`
- Repo name: `gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null` (best-effort — omit if gh fails)
- Open PR for this branch (if any): `gh pr view --json number --jq .number 2>/dev/null`
- Current diff summary: `git diff --stat HEAD`
- Today's date: `date +%Y-%m-%d`
- Expiry date (30 days): `date -v+30d +%Y-%m-%d` (macOS) or `date -d '+30 days' +%Y-%m-%d` (Linux)

If `$ARGUMENTS` is provided, use it as the title seed. Otherwise, ask the user what follow-up work they want to capture.

Treat the `gh`-derived fields (`repo`, `source_pr`) as best-effort: if `gh` is unavailable or unauthenticated, omit them and continue. Handlers that need `gh` will re-check in their own preflight.

### 2. Generate the slug

From the title, create a kebab-case slug:

- Lowercase, strip filler words (the, a, an, for, in, on, at, to, of)
- Max 50 chars
- Example: "Remove stale zsh alias for foobar" -> `remove-stale-zsh-alias-foobar`

### 3. Check for slug collisions

Check if a task with this slug already exists:

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/tasks" -name '<slug>.md' -type f 2>/dev/null
```

If a collision is found, append `-2`, `-3`, etc. until unique.

### 4. Draft the task

Auto-populate these fields:

- `created`: today's date (ISO format)
- `source_branch`: current branch
- `source_pr`: PR number if one exists for this branch
- `status`: `new` by default (the promoter will score it and flip to `ready` or `needs_refinement`). If the user picks "Fix now" in step 5, write `status: ready` instead — the user's manual confirmation IS the human gate, so skip the promoter.
- `expires`: 30 days from today
- `priority`: `low` (default, ask user if they want different)
- `size`: Fibonacci story-point estimate of scope — `1` / `2` / `3` / `5` (infer from scope, ask the user to confirm). See **Task size** in `skills/task/SKILL.md`. If the work estimates larger than `5`, it is too big for one card — propose breaking it into sub-tasks chained with `is_blocked_by` rather than capturing it as one (the `break-down-task` skill, `skills/break-down-task/SKILL.md`, does this).
- `is_blocked_by`: omitted by default; set to another task's slug/id when this work must wait on that task

From conversation context and diff, draft:

- `title`: from user description or `$ARGUMENTS`
- `related_files`: files from current diff or conversation that are relevant
- `tags`: infer from context (e.g., `cleanup`, `tests`, `docs`)
- **Context** section: why this work was noticed
- **Task** section: concrete steps to complete it
- **Acceptance Criteria**: definition of done

If the user indicates this task depends on another task, or the dependency is obvious from context, ask whether to set `is_blocked_by` to that task's slug/id. Validate each entry against the resolved handler (step 6; read the config now if needed):

- For `repo-pr`, use local-slug existence validation: a slug must already exist somewhere under `dev_docs/tasks/**/*.md`. Also pass through entries that already match any tracker id shape from `push-plan.md` (Linear `/^[A-Z]+-\d+$/` §4.3, gh-issue in its prefixed form only `/^\S*#\d+$/` §5.3 — i.e. `#142` or `owner/repo#142`, never a bare number, jira `/^[A-Z][A-Z0-9]*-\d+$/` §5b.3), because partial migrations can rewrite kept dependents' `is_blocked_by` values to tracker ids. A bare number (e.g. a stray `3`) is a validation failure like any unknown slug, not a pass-through.
- For tracker handlers (`gh-issue`/`jira`/`linear`), accept a known local slug or a reference matching any of the three tracker id shapes as defined in `push-plan.md` (Linear §4.3, gh-issue §5.3, jira §5b.3). Existence-check only ids shaped for the configured handler; ids shaped for a different tracker pass through, because a handler switch (e.g. linear → gh-issue) can leave kept task files carrying the previous tracker's ids. Reject/flag only entries that are neither a known local slug nor id-shaped for any tracker.

If validation fails, stop and ask the user to correct the dependency or remove it rather than silently writing a dangling reference.

### 5. Present for review

Show the user the full draft and ask for confirmation. They can adjust priority, size, add/remove files, or edit the task steps.

If the resolved handler (step 6) is `repo-pr`, also ask: **"File for later, or fix now?"**

- **File for later** (default): creates the task file on main for `/do-tasks` to pick up
- **Fix now**: creates the task file AND immediately dispatches a processing agent to do the work

(Other handlers deliver to an external tracker and have no fix-now option.)

### The drafted task (handler input)

Once the user confirms, you hold a normalized **drafted task** that every handler consumes. This is the stable contract between capture and delivery:

| Field           | Description                                                    |
| --------------- | -------------------------------------------------------------- |
| `title`         | Imperative, < 80 chars                                         |
| `body`          | The Context / Task / Acceptance Criteria markdown              |
| `priority`      | `low` / `medium` / `high` / `urgent` (urgent = human-only)     |
| `size`          | Fibonacci story points — `1` / `2` / `3` / `5` (`> 5` ⇒ split) |
| `tags`          | List of freeform tags                                          |
| `slug`          | Kebab-case slug from step 2                                    |
| `created`       | ISO date                                                       |
| `expires`       | ISO date                                                       |
| `source_branch` | Branch where the task was identified                           |
| `source_pr`     | PR number for that branch, if any                              |
| `related_files` | Paths relevant to the work                                     |
| `is_blocked_by` | Optional slug/id of another task that must be completed first  |

Every handler **must report back an artifact identifier** so step 8 can show it. Normally this is the URL of the created PR, issue, or work item. The one exception is `repo-pr` Mode 3 (local staging), which has no remote artifact yet — it returns the staged file path and branch name instead, and step 8 reports that the file is staged locally and will land via the user's own PR.

### 6. Resolve the handler

The destination is configurable.

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

Resolve the handler name:

- File absent, or no `handler:` key → **`repo-pr`** (the default — preserves the original behavior).
- `handler: repo-pr | gh-issue | jira | linear` → use that handler.
- Any other (unknown) value → **stop** and tell the user: "Unknown task handler `<value>` in dev_docs/tasks/.task-config.yml. Valid values: repo-pr, gh-issue, jira, linear. Run /task-config to set it." Do not silently fall back.

### 7. Deliver via the handler

Each handler's full instructions live in sibling file(s) under `commands/handlers/`:

- `handler: repo-pr | gh-issue | jira` → Read `commands/handlers/<handler>.md` and follow it.
- `handler: linear` → Read `commands/handlers/linear-common.md` (shared config/preflight/kanban mapping) and `commands/handlers/linear-add.md` (the create flow), and follow them.

Use the **Read** tool, then follow it, passing the drafted task from step 5. The handler file(s) own everything about how the task lands — preflight checks, dispatch, parsing, and the artifact URL returned.

If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/<handler>.md` or `**/commands/handlers/linear-*.md`) and Read the result.

Do not embed handler logic here; do not read handler files for handlers other than the resolved one.

### 8. Report

Tell the user which handler ran and the artifact it returned. For PR / issue / work item handlers, that's the URL. For `repo-pr` Mode 3 (local staging), there is no URL yet — report the staged file path and branch, and tell the user the task will land via whatever PR they open from that branch. For `repo-pr`, also include what was dispatched (file only, or file + processing) and the dispatch mode used, and that they can monitor with `/tasks`.
