---
description: Score new tasks against the confidence check and promote them to ready or needs_refinement
allowed-tools: Bash(git *), Bash(find *), Bash(grep *), Bash(cat *), Bash(gh *), Glob, Grep, Read, Edit, AskUserQuestion, Skill, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__list_issue_labels, mcp__claude_ai_Linear__create_issue_label, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__save_comment, mcp__linear__list_teams, mcp__linear__list_issues, mcp__linear__list_projects, mcp__linear__list_workflow_states, mcp__linear__list_issue_labels, mcp__linear__create_issue_label, mcp__linear__save_issue, mcp__linear__save_comment, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__getTransitionsForJiraIssue, mcp__claude_ai_Atlassian__transitionJiraIssue, mcp__claude_ai_Atlassian__addCommentToJiraIssue, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getTransitionsForJiraIssue, mcp__atlassian__transitionJiraIssue, mcp__atlassian__addCommentToJiraIssue
argument-hint: "[dry-run] [all] (default: apply, scoped to one project/epic/milestone)"
---

# Promote Tasks

Scan `dev_docs/tasks/**/*.md` for tasks in `status: new`, score each against the confidence check, and flip status accordingly. This is the auto-promotion stage of the kanban flow — it never touches tasks past `new`. Humans own demotions from `ready`.

When the configured handler is an external tracker, the same scoring runs against the tracker's backlog instead of files (see step 0).

> **Arguments.** `$ARGUMENTS` is a set of independent, combinable tokens (order-insensitive):
>
> - `dry-run` — print proposed transitions and exit without writing.
> - `all` — on a tracker handler, score the **whole** team/project/repo backlog instead of narrowing to a single project/epic/milestone (see each tracker handler's project-filter step). No effect on the file-based `repo-pr` path, which has no sub-project scope.
>
> Test each token with a "contains" check (e.g. `$ARGUMENTS` contains `dry-run`), not equality — `/promote-tasks dry-run all` enables both.

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/` directory exists, run the **Legacy migration** prompt from `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

## Steps

### 0. Resolve the handler

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

- File absent, or no `handler:` key → `repo-pr` (default). Continue to step 1 below (file-based path).
- `handler: repo-pr` → continue to step 1 below (file-based path).
- `handler: linear` → **dispatch to the Linear handler.** Read `commands/handlers/linear-common.md` (shared config/preflight/kanban mapping) and `commands/handlers/linear-promote.md` (the promote flow), passing `$ARGUMENTS` (the optional `dry-run` and `all` tokens) through. The handler owns the tracker-specific scoring and transitions. Skip steps 1–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/linear-*.md`) and Read the result.

- `handler: gh-issue` → **dispatch to the gh-issue handler.** Read `commands/handlers/gh-issue-promote.md` (the promote flow; it cites the `## List` section of `commands/handlers/gh-issue.md` for the shared label vocabulary), passing `$ARGUMENTS` (the optional `dry-run` and `all` tokens) through. The handler owns the gh-issue scoring and label transitions. Skip steps 1–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/gh-issue-promote.md`) and Read the result.

- `handler: jira` → **dispatch to the jira handler.** Read `commands/handlers/jira-promote.md` (the promote flow; it cites `commands/handlers/jira.md` step 1 for the shared Atlassian MCP preflight and `commands/handlers/jira-config.md` for the optional `ready_status`/`refinement_status` config keys it uses — prompting for them when unset), passing `$ARGUMENTS` (the optional `dry-run` and `all` tokens) through. The handler owns the jira scoring and status transitions. Skip steps 1–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/jira-promote.md`) and Read the result.

- Any other (unknown) value → stop with: "Unknown task handler `<value>` in dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

### 1. Find candidates

Run the deterministic scanner and take its `cards.new` group as the candidate set:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/task-scan.py" "$(git rev-parse --show-toplevel)/dev_docs/tasks"
```

The script excludes `_archive/` (where `/archive-tasks` parks stale `done` files — see `commands/handlers/repo-pr-archive.md`), `type: epic` files (epic rollups are never scored — see **Epics** in `skills/task/SKILL.md`), and files with no frontmatter, and **fails closed** on malformed frontmatter. The tracker path applies the analogous epic skip: backlog issues with sub-issues (children) are treated as **parent rollups** and are never scored — see `commands/handlers/linear-promote.md` step 5. Report and exit if `cards.new` is empty.

### 2. Score each candidate

The **confidence check** (from `skills/task/SKILL.md`) is 7 deterministic checks plus 1 judgment call. The scanner already computes the **7 deterministic checks** for every `new` card and reports them under that card's `promote_gate`: `checks` (the per-check booleans below) and `high` (true only when all 7 pass).

- `required_fields_present` — `title`, `priority`, `size`, `created`, `source_branch`, `expires` all present
- `size_valid` — `size` is one of `1` / `2` / `3` / `5` (see **Task size** — `> 5` means the task should be split into sub-tasks)
- `related_files_or_research` — `related_files` has ≥ 1 entry, OR `tags` includes `scope: research`
- `has_acceptance_criteria` — body has a `## Acceptance Criteria` section with ≥ 1 bullet
- `no_open_questions_or_tbd` — body has no `## Open Questions` or `## TBD` section with non-empty content (an empty heading is fine)
- `priority_not_urgent` — `priority` ≠ `urgent`
- `human_approval_not_requested` — `human_approval_requested` is unset or false

Then apply the **8th check yourself** — it is deliberately left out of the script because it is model judgment, not a keyword scan:

- **Scope fits size 5 (judgment, not keywords).** Assess whether the task's described scope plausibly fits within size `5` (~300 lines / ~5 files — see **Task size** in `skills/task/SKILL.md`), weighing the stated `size`, the breadth of the `## Task` steps, and the `related_files` count together. A title containing a word like "migrate" or "refactor" is not itself disqualifying ("Migrate one constant to the new config key" is size `1`); a title like "Restructure the auth module" that implies multi-file rework is disqualifying. If the scope clearly exceeds size `5`, score LOW with reason `scope exceeds size 5 — split into sub-tasks`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) is how that split gets done: it slices the card into PR-sized components and replaces the original. When a card's scope is genuinely hard to eyeball, `/assess-task` (`skills/assess-task/SKILL.md`) gives a structured `complexity` + `scope` read to inform this judgment — advisory input, not a replacement for it.

**HIGH (→ `ready`)** when `promote_gate.high` is true **and** the scope judgment passes. **LOW (→ `needs_refinement`, set `human_approval_requested: true`)** if any deterministic check fails (`promote_gate.high` is false) or the scope judgment fails.

This scope gate is **model judgment, not a deterministic rule** — acceptable here because `/promote-tasks` is not a blocking CI gate; a misjudged card lands in `needs_refinement` for a human to confirm, never silently lost. Every other HIGH check above stays deterministic (the scanner computes them).

**Hold blocked cards.** Independently of the HIGH/LOW score, **hold** any card with an unresolved blocker — an `is_blocked_by` entry whose target card is still present and not `done`. A blocker whose target is **absent or `done`** counts as satisfied (the same readiness rule `/do-tasks` applies at runtime; see `commands/do-tasks.md` and the Field reference in `skills/task/SKILL.md`). A held card is **left in `status: new`** — not promoted to `ready`, not demoted to `needs_refinement` — so it stays in the scanned pool and is re-evaluated next run, promoting automatically once every blocker resolves. Resolve each blocker slug against the scanned card set (the scan reports every `dev_docs/tasks/**` card): a blocker is satisfied when no present card carries that slug, or the present card's `status` is `done`. Holding in `new` rather than `needs_refinement` is deliberate — the promoter only scans `status: new`, so a demoted card would never be re-checked when its blocker clears. Report held cards under `held (N, blocked)`.

### 3. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions and exit without writing. (`all` has no effect on this file path — there is no sub-project scope to widen.)

Otherwise, for each scored candidate, use `Edit` to update the YAML frontmatter in place:

- HIGH: set `status: ready`
- LOW: set `status: needs_refinement`, set `human_approval_requested: true` (add the field if missing). Append a one-line `# promoter:` comment to the frontmatter naming which check failed (e.g., `# promoter: missing acceptance_criteria`) so the human can fix quickly.
- Held (blocked): no write at all. Leave the card untouched in `status: new`.

Do not touch any other fields. Do not move the file. Do not stage or commit — the next git operation (manual or `/do-tasks`) will pick up the changes.

### 4. Report

Print a summary table:

```
Promoted 4 of 6 candidates:
  ready (3):
    - remove-stale-foobar-alias
    - fix-broken-import
    - bump-eslint-config
  needs_refinement (1):
    - restructure-auth-module  (scope exceeds size 5 — split into sub-tasks)
  held (1, blocked):
    - some-slug  (blocked by other-slug)
  skipped (2, already past new):
    - <slug>
    - <slug>
```
