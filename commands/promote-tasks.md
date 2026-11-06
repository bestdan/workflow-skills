---
description: Score new tasks against the confidence check and promote them to ready or needs_refinement
allowed-tools: Bash(git *), Bash(find *), Bash(grep *), Bash(cat *), Bash(gh *), Bash(python3 *), Bash("${CLAUDE_PLUGIN_ROOT}/scripts/task-scan.py":*), Glob, Grep, Read, Edit, AskUserQuestion, Skill, Agent, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__list_issue_labels, mcp__claude_ai_Linear__create_issue_label, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__save_comment, mcp__claude_ai_Linear__list_comments, mcp__linear__list_teams, mcp__linear__get_issue, mcp__linear__list_issues, mcp__linear__list_projects, mcp__linear__list_workflow_states, mcp__linear__list_issue_labels, mcp__linear__create_issue_label, mcp__linear__save_issue, mcp__linear__save_comment, mcp__linear__list_comments, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__getTransitionsForJiraIssue, mcp__claude_ai_Atlassian__transitionJiraIssue, mcp__claude_ai_Atlassian__addCommentToJiraIssue, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getTransitionsForJiraIssue, mcp__atlassian__transitionJiraIssue, mcp__atlassian__addCommentToJiraIssue
argument-hint: "[dry-run] [all] [backfill-only] [after=<id>[,<id>…]] (default: apply, scoped to one project/epic/milestone)"
---

# Promote Tasks

Scan `dev_docs/tasks/**/*.md` for tasks in `status: new`, score each against the confidence check, and flip status accordingly. This is the auto-promotion stage of the kanban flow — it never touches tasks past `new`. Humans own demotions from `ready`.

When the configured handler is an external tracker, the same scoring runs against the tracker's backlog instead of files (see step 0).

> **Arguments.** `$ARGUMENTS` is a set of independent, combinable tokens (order-insensitive):
>
> - `dry-run` — print proposed transitions and exit without writing.
> - `all` — on a tracker handler, score the **whole** team/project/repo backlog instead of narrowing to a single project/epic/milestone (see each tracker handler's project-filter step). No effect on the file-based `repo-pr` path, which has no sub-project scope.
> - `after=<id>[,<id>…]` — score only the **un-scored dependents** of the given ids: the items that list one of them as a blocker. It is the re-check a completion runs, so the ids are normally items just completed (`repo-pr` slugs, `gh-issue` numbers). It narrows the candidate set and changes nothing else: every gate below still runs, including the hold check, so a dependent with a second open blocker stays held. The id set is the scope, so there is **no scope prompt** and `all` has no effect, and the legacy-migration preflight **stops and reports** instead of prompting, which makes the mode safe inside an unattended caller. It only ever touches un-scored items, so a repeat run writes nothing new. Combines with `dry-run`, never with `backfill-only` (refuse the pair by name). `repo-pr` and `gh-issue` implement it (step 1 below; `commands/handlers/gh-issue-promote.md` → "`after=` candidates"). On `linear` and `jira` say so and stop. `linear` is not wired yet: its promoter has held on native relations since #1060, so it is the next handler to get the dependents read. `jira` holds only on manual flags, which a completion does not clear, so the mode would release nothing there. Design: `dev_docs/designs/2026-11-06-promote-after-complete.md`.
> - `<key>=<value>` — a **run-scoped config override**. `commands/task-config.md` → "Run-scoped overrides" owns the closed key table, the precedence and the refusal on an unknown key; do not re-derive any of it here. **No key in that table is read by this command today** — `max-estimate` is a claim-time bound (`commands/handlers/task-fill.md` → "Where the estimate gate lives") and `wip-limit` gates claiming, so `/promote-tasks max-estimate=8` is **refused by name**, not accepted and ignored. The token is parsed here so that refusal is explicit rather than silent; if a promoter later reads a bound, add it to that table and it works here with no change to this file.
> - `backfill-only` — fill missing `priority`/`size` on cards at **any** rung and write nothing else: no scoring, no transition, no `auto:` change. This is the only path to those two fields on a card the promoter has already scored, and it is not a re-score — demotion stays a human's call. `gh-issue` implements it (`commands/handlers/gh-issue-promote.md` step 7); on a handler that does not, say so and stop rather than falling back to a scoring run.
>
> Test each token with a "contains" check (e.g. `$ARGUMENTS` contains `dry-run`), not equality — `/promote-tasks dry-run all` enables both.

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/` directory exists, run the **Legacy migration** prompt from `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue. Under `after=`, do not prompt: report `legacy dev_docs/todos/ present — run /promote-tasks once attended to migrate it` and stop.

## Steps

### 0. Resolve the handler

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `commands/task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

If `$ARGUMENTS` contains `after=` and the handler is `linear` or `jira`, stop with "`/promote-tasks after=` is not supported on `<handler>` yet — run `/promote-tasks` to score the backlog." before dispatching (the reason is under **Arguments** above).

- File absent, or no `handler:` key → `repo-pr` (default). Continue to step 1 below (file-based path).
- `handler: repo-pr` → continue to step 1 below (file-based path).
- `handler: linear` → **dispatch to the Linear handler.** Read `commands/handlers/linear-common.md` (shared config/preflight/kanban mapping) and `commands/handlers/linear-promote.md` (the promote flow), passing `$ARGUMENTS` (the optional `dry-run` and `all` tokens) through. The handler owns the tracker-specific scoring and transitions. Skip steps 1–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/linear-*.md`) and Read the result.

- `handler: gh-issue` → **dispatch to the gh-issue handler.** Read `commands/handlers/gh-issue-promote.md` (the promote flow; it cites the `## List` section of `commands/handlers/gh-issue.md` for the shared label vocabulary), passing `$ARGUMENTS` (the optional `dry-run`, `all`, `backfill-only` and `after=` tokens) through. The handler owns the gh-issue scoring and label transitions. Skip steps 1–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/gh-issue-promote.md`) and Read the result.

- `handler: jira` → **dispatch to the jira handler.** Read `commands/handlers/jira-promote.md` (the promote flow; it cites `commands/handlers/jira.md` step 1 for the shared Atlassian MCP preflight and `commands/handlers/jira-config.md` for the optional `ready_status`/`refinement_status` config keys it uses — prompting for them when unset), passing `$ARGUMENTS` (the optional `dry-run` and `all` tokens) through. The handler owns the jira scoring and status transitions. Skip steps 1–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/jira-promote.md`) and Read the result.

- Any other (unknown) value → stop with: "Unknown task handler `<value>` in dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

### 1. Find candidates

Run the deterministic scanner and take its `cards.new` group as the candidate set:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/task-scan.py" "$(git rev-parse --show-toplevel)/dev_docs/tasks"
```

**Under `after=`,** narrow `cards.new` to the cards whose `is_blocked_by` names one of the given slugs. No other read is needed: a completed blocker is a file that is gone or `done`, and the hold check below already treats both as satisfied. The scan reads the checkout it runs in, so run it where the completion is visible (`/deliver-task` runs it on the task branch). Report and exit if the narrowed set is empty, naming the slugs.

The script excludes `_archive/` (where `/archive-tasks` parks stale `done` files — see `commands/handlers/repo-pr-archive.md`), `type: epic` files (epic rollups are never scored — see **Epics** in `skills/task/SKILL.md`), and files with no frontmatter, and **fails closed** on malformed frontmatter. The tracker path applies the analogous epic skip: backlog issues with sub-issues (children) are treated as **parent rollups** and are never scored — see `commands/handlers/linear-promote.md` step 5. Report and exit if `cards.new` is empty.

### 2. Backfill, then score each candidate

**Backfill `priority` and `size` first,** before scoring. The rules are defined once in `commands/handlers/task-fill.md` — the static `medium` default, the model-produced Fibonacci estimate, the over-ceiling rule, reason precedence, the held-card exception, and the fact that a backfilled value is trusted downstream. Read it and apply it; do not re-derive any of it here.

Its **`repo-pr` adapter row** is this path's half — the encoding, the ladder, the write and the provenance comment all live there, and this file deliberately does not repeat them. Record what you intend to backfill for each candidate so step 3 can write it; `dry-run` reports the intended backfills and writes nothing.

If a relative path doesn't resolve, find it with **Glob** (`**/commands/handlers/task-fill.md`) and Read it.

**Score.** The **confidence check** (from `skills/task/SKILL.md`) is 6 deterministic checks plus 1 judgment call. The scanner already computes the **6 deterministic checks** for every `new` card and reports them under that card's `promote_gate`: `checks` (the per-check booleans below) and `high` (true only when all 6 pass). **These were computed before backfill** — `scripts/task-scan.py` is a deterministic reporter over the file as it was found, and it is not re-run after backfilling. So after backfilling, re-evaluate `required_fields_present` and `size_valid` yourself against the **backfilled** values rather than trusting `promote_gate.high` as-is — a candidate that failed one of these two only because `priority` or `size` was missing now passes it, once you account for the value you're about to write. Every other check in `promote_gate` is unaffected by backfill and stays trusted as computed.

- `required_fields_present` — `title`, `priority`, `size`, `created`, `source_branch`, `expires` all present (re-evaluate against the backfilled `priority`/`size` — see above)
- `size_valid` — `size` is one of `1` / `2` / `3` / `5` (see **Task size** — `> 5` means the task should be split into sub-tasks); re-evaluate against the backfilled `size` unless the honest estimate exceeded `5`, in which case this still fails. When it fails _for that reason_, `task-fill.md`'s **reason precedence** decides what the `# promoter:` comment step 3 writes: `scope exceeds size 5 — split into sub-tasks`, not the `size_valid` / `required_fields_present` failure
- `related_files_or_research` — `related_files` has ≥ 1 entry, OR `tags` includes `scope: research`
- `has_acceptance_criteria` — body has a `## Acceptance Criteria` section with ≥ 1 bullet
- `no_open_questions_or_tbd` — body has no `## Open Questions` or `## TBD` section with non-empty content (an empty heading is fine)
- `human_approval_not_requested` — `human_approval_requested` is unset or false

Then apply the **7th check yourself** — it is deliberately left out of the script because it is model judgment, not a keyword scan:

- **Scope fits size 5 (judgment, not keywords).** Assess whether the task's described scope plausibly fits within size `5` (~300 lines / ~5 files — see **Task size** in `skills/task/SKILL.md`), weighing the stated `size`, the breadth of the `## Task` steps, and the `related_files` count together. A title containing a word like "migrate" or "refactor" is not itself disqualifying ("Migrate one constant to the new config key" is size `1`); a title like "Restructure the auth module" that implies multi-file rework is disqualifying. If the scope clearly exceeds size `5`, score LOW with reason `scope exceeds size 5 — split into sub-tasks`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) is how that split gets done: it slices the card into PR-sized components and replaces the original. When a card's scope is genuinely hard to eyeball, `/assess-task` (`skills/assess-task/SKILL.md`) gives a structured `complexity` + `scope` read to inform this judgment — advisory input, not a replacement for it.

**HIGH (→ `ready`)** when all 6 deterministic checks pass — using `promote_gate.high` where backfill made no difference, or your own re-evaluation of `required_fields_present`/`size_valid` against backfilled values (above) where it did — **and** the scope judgment passes. **LOW (→ `needs_refinement`, set `human_approval_requested: true`)** if any deterministic check fails or the scope judgment fails.

This scope gate is **model judgment, not a deterministic rule** — acceptable here because `/promote-tasks` is not a blocking CI gate; a misjudged card lands in `needs_refinement` for a human to confirm, never silently lost. Every other HIGH check above stays deterministic (the scanner computes them).

**Hold blocked cards.** Independently of the HIGH/LOW score, **hold** any card with an unresolved blocker — an `is_blocked_by` entry whose target card is still present and not `done`. A blocker whose target is **absent or `done`** counts as satisfied (the same readiness rule `/do-tasks` applies at runtime; see `commands/do-tasks.md` and the Field reference in `skills/task/SKILL.md`). A held card is **left in `status: new`** — not promoted to `ready`, not demoted to `needs_refinement` — so it stays in the scanned pool and is re-evaluated next run, promoting automatically once every blocker resolves. Resolve each blocker slug against the scanned card set (the scan reports every `dev_docs/tasks/**` card): a blocker is satisfied when no present card carries that slug, or the present card's `status` is `done`. Holding in `new` rather than `needs_refinement` is deliberate — the promoter only scans `status: new`, so a demoted card would never be re-checked when its blocker clears. Report held cards under `held (N, blocked)`.

**Forecast the tier, then confirm the Intent.** For each candidate still HIGH and not held, follow `skills/task/references/intent-confirmation.md`: list the paths the card will change (its `## Task` and `## Context`, plus `related_files`), run `tier-forecast.py`, and on `human` draft any missing Intent lines and dispatch the confirmer. A `human` card the confirmer does not confirm at or above `consult_bar` turns LOW, with reason `intent not confirmed: <crux>`. `skipped` and `auto` leave the score as it is. Record for step 3 the forecast, its first reason when `human`, the Intent block when you drafted any of it, and the verdict. Under `dry-run`, forecast and draft but do not dispatch the confirmer; a `human` card reports as `ready if confirmed`.

If a relative path doesn't resolve, find the reference with **Glob** (`**/skills/task/references/intent-confirmation.md`) and Read it.

### 3. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions, **the intended backfills, and each forecast with any drafted Intent block**, and exit without writing. (`all` has no effect on this file path — there is no sub-project scope to widen. Neither does `backfill-only`: it is a tracker-handler mode, and on this path a card past `new` is a file a human can edit directly.)

Otherwise, for each scored candidate, use `Edit` to update the YAML frontmatter in place:

- Backfill first: write any `priority`/`size` value from step 2, with the provenance comment(s) `task-fill.md`'s `repo-pr` adapter names. This happens for every backfilled candidate, HIGH or LOW.
- HIGH: set `status: ready`
- LOW: set `status: needs_refinement`, set `human_approval_requested: true` (add the field if missing). Append a one-line `# promoter:` comment to the frontmatter naming which check failed (e.g., `# promoter: missing acceptance_criteria`) so the human can fix quickly — in addition to, not instead of, any backfill provenance comment already appended above.
- Held (blocked): no write at all. Leave the card untouched in `status: new`, including any backfill it would otherwise have gotten — held cards are re-evaluated next run once their blocker clears.
- Forecast (every card that reached it, HIGH or LOW): set `tier_forecast: auto|human|skipped`. For `human`, append `# promoter: tier human — <first reason>`, write any drafted lines into the body's `## Intent` section (adding the section after `## Context` if it is missing), and append the verdict as `# promoter: intent confirmed by <model> at <confidence>%` or `# promoter: intent not confirmed (<confidence>%): <crux>`. For an unconfirmed card that verdict line is its LOW reason, so the LOW bullet writes no separate `# promoter:` comment for it.

Do not touch any other fields, and no other part of the body. Do not move the file. Do not stage or commit — the next git operation (manual or `/do-tasks`) will pick up the changes.

### 4. Report

Under `after=`, lead with `dependents re-checked (N) after <ids>:`, where `N` counts every dependent found, and use the same sections below it.

Print a summary table, annotating any card that got a backfill, and every card that reached the forecast with its tier:

```
Promoted 5 of 8 candidates:
  ready (3):
    - remove-stale-foobar-alias  (tier: auto)
    - fix-broken-import  (tier: auto; backfilled: size)
    - bump-eslint-config  (tier: human, intent confirmed 90%; backfilled: priority, size)
  needs_refinement (2):
    - restructure-auth-module  (scope exceeds size 5 — split into sub-tasks)
    - reword-claim-skill  (tier: human; intent not confirmed: <crux>)
  held (1, blocked):
    - some-slug  (blocked by other-slug)
  skipped (2, already past new):
    - <slug>
    - <slug>
backfilled (2):
  - fix-broken-import  (size)
  - bump-eslint-config  (priority, size)
```
