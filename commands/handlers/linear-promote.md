# Linear handler — /promote-tasks flow

Invoked from `/promote-tasks` when `handler: linear` is configured. Scores the team's **backlog** issues against the same confidence check the file path uses, then applies the kanban transitions: HIGH → move to the `unstarted` state (`Todo`) and tag `auto-eligible`; LOW → leave the issue where it is and tag `human-approval-requested`.

**Shared reference:** see `linear-common.md` for connection details, the full config schema, the preflight pattern, and the kanban mapping table this file applies.

> **Hard rule: this path only ever touches `backlog`-type issues.** It never scores or transitions an issue already in `unstarted`/`started`/`completed`/`canceled` — those are past the `new` column and are out of the promoter's lane, exactly as the file path never touches tasks past `status: new`. If you are about to `save_issue` against a non-`backlog` issue, you have a bug — stop.

## Steps

### 1. Preflight

Run the shared preflight from `linear-common.md` (call `<linear-mcp>__list_teams`, match `<linear.team>`, capture team `id`). On any failure, stop with the same error messages.

### 2. Resolve workflow states

Call `<linear-mcp>__list_workflow_states` with `teamId`. Cache the state-id → type map. Identify:

- the `backlog`-type state id(s) — the only states this path **reads** candidates from.
- the team's default `unstarted`-type state id (= `ready` in the kanban mapping; if multiple, prefer one named `Todo`, else the first) — the HIGH **target** state.

### 3. Resolve label ids

Call `<linear-mcp>__list_issue_labels` with `teamId`. Capture the ids for `auto-eligible` and `human-approval-requested`. If either label does not exist, create it via `<linear-mcp>__create_issue_label` (`teamId`, `name`, a recognizable color — e.g. `auto-eligible` → `#26B5CE`, `human-approval-requested` → `#F2994A`) and capture the new id. Mirrors how `linear-claim.md` resolves `auto-claimed` on demand.

### 4. Resolve the project scope

By default the promoter scores a **single** project, not the whole team backlog. Resolve one `projectId` (or, when explicitly widened, none) in this order:

1. **`all` override.** If `$ARGUMENTS` contains `all`, set **no** `projectId` — score the whole team backlog. Note `scope: whole backlog (all)` in the step-8 report and skip the rest of this step.
2. **Pinned.** Else if `linear.default_project` is set (non-empty), use it as `projectId`.
3. **Detect.** Else call `<linear-mcp>__list_projects` with `teamId` and `includeArchived: false`, and consider the **active** projects (state is not `completed`/`canceled`):
   - **Exactly one** → use its `id` as `projectId`. Note `scope: project <name>` in the report.
   - **Multiple** → ask via `AskUserQuestion` (header `Project`) which one to score: offer the most recently updated projects (cap at 3, so 3 + the escape option fits the 4-option max) plus an explicit **`All — whole backlog`** option. If the user picks `All — whole backlog`, set **no** `projectId` (as in the `all` override). Otherwise use the chosen project's `id`.
   - **Zero** → set **no** `projectId`; fall back to the whole team backlog. Note `scope: whole backlog (no projects)` in the report.

The resolved `projectId` (or its absence) feeds **both** the candidate query and the parent-rollup cross-state sweep in step 5.

### 5. Query candidates

Call `<linear-mcp>__list_issues` with:

- `teamId`: resolved team id
- `projectId`: from step 4 (omit if not set)
- `stateId`: each `backlog`-type state id from step 2 (loop or pass as list per the tool's accepted shape)
- `includeArchived`: `false`
- Limit: 50. If more exist, note the truncation in the report; do not paginate.

Set aside (do **not** score) any candidate that **already** carries `auto-eligible` or `human-approval-requested` — the promoter, like the file path, only acts on issues that have not yet been scored (the Linear analogue of `status: new`). Keep these in a separate `skipped` list so step 8 can report them (mirroring the file path's `skipped (…, already past new)` line); they receive no `save_issue` call. Report and exit if no un-scored candidates remain.

Also set aside (do **not** score) any candidate that is a **parent rollup** — a backlog issue that has been decomposed by `/break-down-task` into child issues. Promoting a parent rollup would move an empty shell to `Todo` where `/do-tasks` would try to claim it. This is the tracker-path analogue of the file path's `type: epic` skip (see `commands/promote-tasks.md` step 1).

**Detecting parent rollups:** after the backlog query, call `<linear-mcp>__list_issues` with the same `teamId` (and `projectId` if set), `includeArchived: false`, and `limit: 100` — **without** a `stateId` filter — to collect `parentId` values across all states. Build a `parentIds` set from every non-null `parentId` field in that response. Any backlog candidate whose `id` appears in `parentIds` has at least one child — skip it as a `parent rollup`. For projects where the cross-state query truncates at 100, fall back to calling `<linear-mcp>__list_issues` with `parentId` = candidate `id` and `limit: 1` for any candidate not yet confirmed safe. Keep all identified parent rollups in the `skipped` list with reason `parent rollup`; they receive no `save_issue` call.

### 6. Score each candidate

For each candidate, run the **confidence check** from `skills/task/SKILL.md` — the **same judgment-based gate the file path uses** (`commands/promote-tasks.md` step 2), read against Linear fields rather than frontmatter:

**HIGH (→ promote)** requires ALL of:

- `title` present and non-empty.
- `priority` is set and is **not** `urgent` (Linear `priority` `1`). Linear's `none` (`0`) counts as "no priority set" → fails this check.
- `estimate` is set and is one of `1` / `2` / `3` / `5` (Linear's `estimate` is the same Fibonacci scale as our task `size` — see `linear-common.md`). An unset or out-of-scale estimate fails (the analogue of a missing/invalid `size`).
- `description` is non-empty and contains acceptance-style content — a concrete, checkable outcome (the analogue of the file path's `## Acceptance Criteria` requirement). A bare title with an empty or "investigate X" description fails.
- `description` has no unresolved open-questions / TBD content.
- **Scope fits estimate 5 (judgment, not keywords).** Assess whether the described scope plausibly fits within estimate `5` (~300 lines / ~5 files — see **Task size** in `skills/task/SKILL.md`), weighing the stated `estimate` against the breadth of the description. A word like "migrate" or "refactor" is not itself disqualifying; a description implying multi-module rework is. If the scope clearly exceeds `5`, score LOW with reason `scope exceeds estimate 5 — split into sub-issues`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) performs that split on the Linear path — convert the issue into a parent and create the components as child issues.

**LOW** if any HIGH condition fails. Record the first failed check as the reason (e.g. `no estimate set`, `description missing acceptance criteria`, `priority is urgent`).

As on the file path, this scope gate is **model judgment, not a deterministic rule** — acceptable because `/promote-tasks` is not a blocking CI gate: a misjudged issue lands tagged `human-approval-requested` for a human to confirm, never silently lost.

### 7. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions (per the report shape below) and exit **without** calling `save_issue`.

Otherwise, for each scored candidate call `<linear-mcp>__save_issue` with `id` = candidate `id`:

- **HIGH:** `state` = the `unstarted`-type target state id from step 2; `labels` = the issue's existing label ids **plus** `auto-eligible` (the `save_issue` field is named `labels` and **replaces** the set — include existing labels to avoid clobbering).
- **LOW:** **do not** change `state` (leave the issue in backlog); `labels` = existing label ids **plus** `human-approval-requested`. Optionally call `<linear-mcp>__save_comment` with `issueId` = candidate `id` (the `save_comment` field is named `issueId`, not `id` — same as `linear-claim.md`) and `body` = a one-line reason (`/promote-tasks: <failed-check>`) so the human can fix it quickly — mirrors the file path's `# promoter:` comment.

Never move an issue to a `completed`- or `canceled`-type state, and never touch a non-`backlog` issue.

### 8. Report

Print the same summary shape as the file path (`commands/promote-tasks.md` step 4), keyed by Linear identifier. Lead with the resolved scope from step 4 (`scope: project <name>` / `scope: whole backlog (all)` / `scope: whole backlog (no projects)`) so it's clear what the run covered:

```
scope: project Payments revamp
Promoted 4 of 6 candidates:
  ready (3):
    - PRE-12  Fix broken import
    - PRE-15  Bump eslint config
    - PRE-18  Remove stale alias
  needs_refinement (1):
    - PRE-21  Restructure auth module  (scope exceeds estimate 5 — split into sub-issues)
  skipped (2):
    - PRE-09  (already scored)
    - PRE-10  (parent rollup)
```

Skipped issues are reported with their reason — `already scored` or `parent rollup`. Append the truncation note from step 5 if it applied.
