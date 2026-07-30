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

By default the promoter scores a **single** project, not the whole team backlog. Resolve scope from the configured projects (the "Resolve configured projects" step in `linear-common.md`) in this order:

1. **`all` override.** If `$ARGUMENTS` contains `all`, score **all configured** projects' backlogs (their union) — **not** the whole team. Use each configured project's `id` as a `projectId` (one query per project in step 5). Resolve each `name` lazily for the report and note `scope: all configured projects (<names>)`, then skip the rest of this step. (If **no** projects are configured, `all` degrades to the whole team backlog — set **no** `projectId` and note `scope: whole backlog (no projects)`.)
2. **No projects configured** (the helper returns the synthetic whole-team scope, `id: null`): set **no** `projectId` — score the whole team backlog. Note `scope: whole backlog (no projects)` in the report.
3. **Exactly one configured project** → use its `id` as `projectId`. Resolve its `name` lazily for the report (`scope: project <name>`).
4. **Two or more configured** → ask via `AskUserQuestion` (header `Project`) which one to score, offering an explicit **`All — all configured projects`** option alongside the projects. Stay within the 4-option max: with **3 or fewer** configured, show them all by `name` + `All`; with **4 or more**, show 2 projects by `name` + "Other" + `All`, where "Other" lets the user type a configured project name (matched case-insensitively against the configured list; re-ask on no match) or a configured project's UUID, so projects beyond the first 2 stay reachable for individual scoring. If the user picks `All`, score all configured backlogs (as in the `all` override). Otherwise use the chosen project's `id` and capture its `name` for the report.

The resolved scope — a single `projectId`, the set of all configured `projectId`s, or none (whole team) — feeds **both** the candidate query and the parent-rollup cross-state sweep in step 5.

### 5. Query candidates

Call `<linear-mcp>__list_issues` with:

- `teamId`: resolved team id
- `projectId`: from step 4 — omit when whole-team; for a single configured project use its `id`; for the all-configured scope run this query once per configured project `id` and merge the candidates (tag each with its project for the report).
- `stateId`: each `backlog`-type state id from step 2 (loop or pass as list per the tool's accepted shape)
- `includeArchived`: `false`
- Limit: 50. If more exist, note the truncation in the report; do not paginate.

Set aside (do **not** score) any candidate that **already** carries `auto-eligible` or `human-approval-requested` — the promoter, like the file path, only acts on issues that have not yet been scored (the Linear analogue of `status: new`). Keep these in a separate `skipped` list so step 8 can report them (mirroring the file path's `skipped (…, already past new)` line); they receive no `save_issue` call. Report and exit if no un-scored candidates remain.

Also set aside (do **not** score) any candidate that is a **parent rollup** — a backlog issue that has been decomposed by `/break-down-task` into child issues. Promoting a parent rollup would move an empty shell to `Todo` where `/do-tasks` would try to claim it. This is the tracker-path analogue of the file path's `type: epic` skip (see `commands/promote-tasks.md` step 1).

**Detecting parent rollups:** for each backlog candidate not already set aside as `already scored`, call `<linear-mcp>__list_issues` with `parentId` = candidate `id`, `limit: 1`, `includeArchived: false` (the `parentId` filter is server-side and takes exactly one id, so this is an exact per-candidate existence check that never truncates). A non-empty result means the candidate has at least one child — skip it as a `parent rollup`. Keep all identified parent rollups in the `skipped` list with reason `parent rollup`; they receive no `save_issue` call.

Also set aside (do **not** score) any candidate that is **blocked** — a backlog candidate carrying the `blocked` label is **held** (left in Backlog, no `save_issue`, reason `blocked`), mirroring the file path's "hold blocked cards in Backlog" rule (see `commands/promote-tasks.md`). Keep all identified blocked candidates in the `skipped` list with reason `blocked`; they receive no `save_issue` call. (Honoring native Linear "is blocked by" **relations** here — not only the `blocked` label — needs a per-candidate `<linear-mcp>__get_issue` with `includeRelations: true` (not currently in this command's allowed tools), where a `Canceled` blocker is **never-satisfiable** so only an absent or `Done`/`completed`-type blocker satisfies the dependency — see `commands/handlers/linear-reoptimize.md`. That, plus wiring the same relation gate into the claim path (`commands/handlers/assets/linear-ready.py` / `linear-claim.md`), is deferred to a follow-up.)

### 6. Score each candidate

**Backfill missing estimates first.** Before scoring, backfill two fields on every remaining candidate — unconditionally, even one that will fail some other check and get tagged `human-approval-requested` anyway — mirroring the file path's backfill (`commands/promote-tasks.md` step 2):

- `priority` is `none` (`0`) → set to `medium` (Linear priority `3`). A flat static default is correct here: priority only orders work, it never gates anything. Never auto-set `urgent` (`1`).
- `estimate` is unset or not one of `1` / `2` / `3` / `5` → **estimate it** from the issue `description` (Fibonacci `1`/`2`/`3`/`5` — `estimate` is the same scale as the file path's `size`, see `linear-common.md`). This is deliberately not a static default: `estimate` feeds the same size-driven routing `size` does on the file path, so a blind constant could misroute; producing the number is the same judgment the scope-fit check below already requires, backfill just records it. If the honest estimate would exceed `5`, do not write a bogus `5` — leave `estimate` unset and let the scope-fit check below score LOW with reason `scope exceeds estimate 5 — split into sub-issues`.

Backfilled values are written in step 7's `save_issue` call alongside the state/label transition (not a separate write), with a one-line issue comment noting which field(s) the promoter auto-set so a human can cheaply correct a bad guess. `dry-run` reports the intended backfills without writing them. An auto-estimated `estimate` is fully trusted downstream exactly like a human-set one — see the file path's equivalent note in `skills/task/SKILL.md`'s Confidence check section.

Then, for each candidate, run the **confidence check** from `skills/task/SKILL.md` — the **same judgment-based gate the file path uses** (`commands/promote-tasks.md` step 2), read against Linear fields rather than frontmatter, **using the backfilled `priority`/`estimate` values** from above:

**HIGH (→ promote)** requires ALL of:

- `title` present and non-empty.
- `priority` is set (Linear `priority` ≠ `0`). Linear's `none` (`0`) counts as "no priority set", but backfill above always sets it to `medium` first, so this check never fails in practice — it stays as a defensive check against a backfill bug rather than a live gate. `urgent` (`1`) is not excluded — it passes this check like any other priority.
- `estimate` is set and is one of `1` / `2` / `3` / `5` (Linear's `estimate` is the same Fibonacci scale as our task `size` — see `linear-common.md`). An unset or out-of-scale estimate fails (the analogue of a missing/invalid `size`) — this only still fails when the honest backfill estimate exceeded `5` and was left unset.
- `description` is non-empty and contains acceptance-style content — a concrete, checkable outcome (the analogue of the file path's `## Acceptance Criteria` requirement). A bare title with an empty or "investigate X" description fails.
- `description` has no unresolved open-questions / TBD content.
- **Scope fits estimate 5 (judgment, not keywords).** Assess whether the described scope plausibly fits within estimate `5` (~300 lines / ~5 files — see **Task size** in `skills/task/SKILL.md`), weighing the stated `estimate` against the breadth of the description. A word like "migrate" or "refactor" is not itself disqualifying; a description implying multi-module rework is. If the scope clearly exceeds `5`, score LOW with reason `scope exceeds estimate 5 — split into sub-issues`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) performs that split on the Linear path — convert the issue into a parent and create the components as child issues.

**LOW** if any HIGH condition fails. Record the first failed check as the reason (e.g. `no estimate set`, `description missing acceptance criteria`, `no priority set`) — **except** when backfill deliberately left `estimate` unset because the honest estimate exceeded `5`. There the scope-fit reason wins: record `scope exceeds estimate 5 — split into sub-issues`, not `no estimate set`. Ordering by first-failure would otherwise bury the one reason that tells the human what to actually do (split the issue) behind a reason that reads as "fill in a number".

As on the file path, this scope gate is **model judgment, not a deterministic rule** — acceptable because `/promote-tasks` is not a blocking CI gate: a misjudged issue lands tagged `human-approval-requested` for a human to confirm, never silently lost.

### 7. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions **and the intended backfills** (per the report shape below) and exit **without** calling `save_issue`.

Otherwise, for each scored candidate call `<linear-mcp>__save_issue` with `id` = candidate `id`, including any backfilled `priority`/`estimate` from step 6 regardless of HIGH/LOW (a LOW-scored issue still gets its backfilled fields saved, so the human has less to fix):

- **HIGH:** `state` = the `unstarted`-type target state id from step 2; `labels` = the issue's existing label ids **plus** `auto-eligible` (the `save_issue` field is named `labels` and **replaces** the set — include existing labels to avoid clobbering); `priority`/`estimate` = the backfilled value(s) from step 6, if any.
- **LOW:** **do not** change `state` (leave the issue in backlog); `labels` = existing label ids **plus** `human-approval-requested`; `priority`/`estimate` = the backfilled value(s) from step 6, if any. Call `<linear-mcp>__save_comment` with `issueId` = candidate `id` (the `save_comment` field is named `issueId`, not `id` — same as `linear-claim.md`) and `body` = a one-line reason (`/promote-tasks: <failed-check>`) so the human can fix it quickly — mirrors the file path's `# promoter:` comment; if any field was backfilled, note it in the same comment (e.g. `promoter auto-set priority to Medium, estimate to 2`).

If a candidate had no other failed check but **did** get a backfill, still call `save_comment` to note what was auto-set (there is no failed-check reason in that case, just the backfill note) — mirrors the file path's provenance comment being appended even when the card is otherwise HIGH.

Never move an issue to a `completed`- or `canceled`-type state, and never touch a non-`backlog` issue.

### 8. Report

Print the same summary shape as the file path (`commands/promote-tasks.md` step 4), keyed by Linear identifier, annotating any issue that got a backfill. Lead with the resolved scope from step 4 (`scope: project <name>` / `scope: all configured projects (<names>)` / `scope: whole backlog (no projects)`) so it's clear what the run covered:

```
scope: project Payments revamp
Promoted 4 of 7 candidates:
  ready (3):
    - PRE-12  Fix broken import  (backfilled: estimate)
    - PRE-15  Bump eslint config
    - PRE-18  Remove stale alias  (backfilled: priority, estimate)
  needs_refinement (1):
    - PRE-21  Restructure auth module  (scope exceeds estimate 5 — split into sub-issues)
  skipped (3):
    - PRE-09  (already scored)
    - PRE-10  (parent rollup)
    - PRE-11  (blocked)
backfilled (2):
  - PRE-12  (estimate)
  - PRE-18  (priority, estimate)
```

Skipped issues are reported with their reason — `already scored`, `parent rollup`, or `blocked`. Append the truncation note from step 5 if it applied.

**Out-of-scope backlog note.** When the resolved scope is **narrower than the whole team** — a single project (step 4 cases 3–4), or the all-configured union (which still excludes unconfigured projects and unassigned issues) — append a one-line note that backlog outside the scored scope was **not** examined this run, so the run's success isn't mistaken for "the whole backlog is triaged". Make the remediation **scope-aware**, and note that the whole-team backlog has **no per-run override** — it is scored only when **no** projects are configured (step 4 cases 1–2), a config-level state, not a flag. For example:

- Single project scored:

  ```
  note: scored project Payments revamp only — backlog in other configured projects / unassigned was not scored. Pass `all` to score the union of all configured projects; the whole-team backlog (incl. unconfigured projects / unassigned) is scored only when no projects are configured.
  ```

- All configured projects scored (the `all` union):

  ```
  note: scored all configured projects — backlog in unconfigured projects / unassigned was not scored. Those are reached only when no projects are configured (whole-team scope); there is no per-run flag for it.
  ```

This note is **informational only — do not auto-widen** the scope to pull those issues in. Omit the note when the run already covered everything (`scope: whole backlog (no projects)`).
