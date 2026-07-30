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

### 2. Backfill, then score each candidate

**Backfill missing estimates.** Before scoring, backfill two fields on **every** `status: new` candidate — unconditionally, even one that will fail some other check and land in `needs_refinement` anyway, so the human has less to redo when it gets there:

- **`priority` missing → set `priority: medium`.** A flat static default is correct here: `priority` only orders work, it never gates anything. Never auto-set `urgent`.
- **`size` missing or not one of `1`/`2`/`3`/`5` → estimate it.** Read the card body (`## Task` steps) and `related_files` breadth, and produce a Fibonacci estimate (`1`/`2`/`3`/`5` — see **Task size** in `skills/task/SKILL.md`). This is deliberately not a static default: `size` feeds the one-task-one-PR ceiling and `auto_execute_max_size` routing downstream, so a blind constant could misroute work. Producing this number is the same judgment the scope-fit check below (the "8th check") already requires you to make — you are already deciding whether the scope fits size `5` — this just records the number instead of only judging it in the moment.
  - If the honest estimate would **exceed `5`**, do **not** write a bogus `5`. Leave `size` as-is (missing/invalid) and fall through to the scope-fit check below, which scores LOW with reason `scope exceeds size 5 — split into sub-tasks`.

Record what you intend to backfill for each candidate (field(s) and value(s)) — step 3 writes it as a `# promoter:` frontmatter comment (`# promoter: priority defaulted to medium`, `# promoter: size auto-estimated`, or both) so a human can cheaply spot and correct a bad guess. `dry-run` reports the intended backfills and writes nothing.

An auto-estimated `size` is **fully trusted downstream**, exactly like a human-set one: it is eligible for `auto_execute_max_size` headless batch auto-execution, with no reserve-only carve-out for auto-estimated cards. The auto-execute path still ends in a PR a human reviews, and a mis-estimated task beats a permanently blocked one.

**Score.** The **confidence check** (from `skills/task/SKILL.md`) is 6 deterministic checks plus 1 judgment call. The scanner already computes the **6 deterministic checks** for every `new` card and reports them under that card's `promote_gate`: `checks` (the per-check booleans below) and `high` (true only when all 6 pass). **These were computed before backfill** — `scripts/task-scan.py` is a deterministic reporter over the file as it was found, and it is not re-run after backfilling. So after backfilling, re-evaluate `required_fields_present` and `size_valid` yourself against the **backfilled** values rather than trusting `promote_gate.high` as-is — a candidate that failed one of these two only because `priority` or `size` was missing now passes it, once you account for the value you're about to write. Every other check in `promote_gate` is unaffected by backfill and stays trusted as computed.

- `required_fields_present` — `title`, `priority`, `size`, `created`, `source_branch`, `expires` all present (re-evaluate against the backfilled `priority`/`size` — see above)
- `size_valid` — `size` is one of `1` / `2` / `3` / `5` (see **Task size** — `> 5` means the task should be split into sub-tasks); re-evaluate against the backfilled `size` unless the honest estimate exceeded `5`, in which case this still fails
- `related_files_or_research` — `related_files` has ≥ 1 entry, OR `tags` includes `scope: research`
- `has_acceptance_criteria` — body has a `## Acceptance Criteria` section with ≥ 1 bullet
- `no_open_questions_or_tbd` — body has no `## Open Questions` or `## TBD` section with non-empty content (an empty heading is fine)
- `human_approval_not_requested` — `human_approval_requested` is unset or false

Then apply the **7th check yourself** — it is deliberately left out of the script because it is model judgment, not a keyword scan:

- **Scope fits size 5 (judgment, not keywords).** Assess whether the task's described scope plausibly fits within size `5` (~300 lines / ~5 files — see **Task size** in `skills/task/SKILL.md`), weighing the stated `size`, the breadth of the `## Task` steps, and the `related_files` count together. A title containing a word like "migrate" or "refactor" is not itself disqualifying ("Migrate one constant to the new config key" is size `1`); a title like "Restructure the auth module" that implies multi-file rework is disqualifying. If the scope clearly exceeds size `5`, score LOW with reason `scope exceeds size 5 — split into sub-tasks`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) is how that split gets done: it slices the card into PR-sized components and replaces the original. When a card's scope is genuinely hard to eyeball, `/assess-task` (`skills/assess-task/SKILL.md`) gives a structured `complexity` + `scope` read to inform this judgment — advisory input, not a replacement for it.

**HIGH (→ `ready`)** when all 6 deterministic checks pass — using `promote_gate.high` where backfill made no difference, or your own re-evaluation of `required_fields_present`/`size_valid` against backfilled values (above) where it did — **and** the scope judgment passes. **LOW (→ `needs_refinement`, set `human_approval_requested: true`)** if any deterministic check fails or the scope judgment fails.

This scope gate is **model judgment, not a deterministic rule** — acceptable here because `/promote-tasks` is not a blocking CI gate; a misjudged card lands in `needs_refinement` for a human to confirm, never silently lost. Every other HIGH check above stays deterministic (the scanner computes them).

**Hold blocked cards.** Independently of the HIGH/LOW score, **hold** any card with an unresolved blocker — an `is_blocked_by` entry whose target card is still present and not `done`. A blocker whose target is **absent or `done`** counts as satisfied (the same readiness rule `/do-tasks` applies at runtime; see `commands/do-tasks.md` and the Field reference in `skills/task/SKILL.md`). A held card is **left in `status: new`** — not promoted to `ready`, not demoted to `needs_refinement` — so it stays in the scanned pool and is re-evaluated next run, promoting automatically once every blocker resolves. Resolve each blocker slug against the scanned card set (the scan reports every `dev_docs/tasks/**` card): a blocker is satisfied when no present card carries that slug, or the present card's `status` is `done`. Holding in `new` rather than `needs_refinement` is deliberate — the promoter only scans `status: new`, so a demoted card would never be re-checked when its blocker clears. Report held cards under `held (N, blocked)`.

### 3. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions **and the intended backfills** and exit without writing. (`all` has no effect on this file path — there is no sub-project scope to widen.)

Otherwise, for each scored candidate, use `Edit` to update the YAML frontmatter in place:

- Backfill first: write any `priority`/`size` value from step 2, and append the matching `# promoter:` provenance comment(s) (`# promoter: priority defaulted to medium`, `# promoter: size auto-estimated` — both if both were backfilled). This happens for every backfilled candidate, HIGH or LOW.
- HIGH: set `status: ready`
- LOW: set `status: needs_refinement`, set `human_approval_requested: true` (add the field if missing). Append a one-line `# promoter:` comment to the frontmatter naming which check failed (e.g., `# promoter: missing acceptance_criteria`) so the human can fix quickly — in addition to, not instead of, any backfill provenance comment already appended above.
- Held (blocked): no write at all. Leave the card untouched in `status: new`, including any backfill it would otherwise have gotten — held cards are re-evaluated next run once their blocker clears.

Do not touch any other fields. Do not move the file. Do not stage or commit — the next git operation (manual or `/do-tasks`) will pick up the changes.

### 4. Report

Print a summary table, annotating any card that got a backfill:

```
Promoted 4 of 7 candidates:
  ready (3):
    - remove-stale-foobar-alias
    - fix-broken-import  (backfilled: size)
    - bump-eslint-config  (backfilled: priority, size)
  needs_refinement (1):
    - restructure-auth-module  (scope exceeds size 5 — split into sub-tasks)
  held (1, blocked):
    - some-slug  (blocked by other-slug)
  skipped (2, already past new):
    - <slug>
    - <slug>
backfilled (2):
  - fix-broken-import  (size)
  - bump-eslint-config  (priority, size)
```
