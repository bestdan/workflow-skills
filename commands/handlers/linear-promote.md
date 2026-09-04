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
- `projectId`: from step 4 — omit when whole-team; for a single configured project use its `id`; for the all-configured scope run this query once per configured project `id`, **in a fixed order**, and merge the candidates (tag each with its project for the report) — subject to the single shared budget below, so a project queried later in that order can end up contributing zero candidates once the budget is already spent.
- `stateId`: each `backlog`-type state id from step 2 (loop or pass as list per the tool's accepted shape)
- `includeArchived`: `false`
- Limit: 250 (the tool's max) per call, and **paginate to exhaustion** against **one shared 500-candidate budget for the whole collection phase — not 500 per project.** Maintain a single running total across every project query and every page within it (a single project's collection is just the degenerate one-project case of the same budget). While a page's `hasNextPage` is `true` **and** the running total is still under 500, re-call with `cursor` set to the returned cursor and merge the results; the instant the running total reaches 500 — mid-page, at a page boundary, or moving on to the next project in the all-configured union — stop the **entire** collection phase (no further pages, no further projects), not just the current project's loop. This is deliberately a single global ceiling rather than 500-per-query: an all-configured union of `N` projects must not admit up to `500×N` candidates just because each project's own query is capped at 500 — that would make the ceiling's advertised magnitude (matching `gh-issue-promote.md`'s single 500-issue cap) meaningless for any run scoring more than one project, and would make step 9's "`M` is the ceiling on a hit" claim false for exactly the union case. Hitting the shared budget earns its own prominent report line in step 9 (not a footnote), not a silent stop. Kept well short of the tool's practical reach deliberately: step 5's parent-rollup check below still costs one serial `list_issues` call per surviving candidate, and the Linear MCP is token-expensive (`linear-common.md`) — a higher ceiling would multiply that fan-out directly. A batched check (one bulk GraphQL query over `children(first: 1)` per candidate, mirroring `linear-ready.py`'s fast path) would remove that constraint but is out of scope here; this ceiling is the interim bound.

Set aside (do **not** score) any candidate that **already** carries `auto-eligible` or `human-approval-requested` — the promoter, like the file path, only acts on issues that have not yet been scored (the Linear analogue of `status: new`). Keep these in a separate `skipped` list so step 9 can report them (mirroring the file path's `skipped (…, already past new)` line); they receive no `save_issue` call. Report and exit if no un-scored candidates remain.

Also set aside (do **not** score) any candidate that is a **parent rollup** — a backlog issue that has been decomposed by `/break-down-task` into child issues. Promoting a parent rollup would move an empty shell to `Todo` where `/do-tasks` would try to claim it. This is the tracker-path analogue of the file path's `type: epic` skip (see `commands/promote-tasks.md` step 1).

**Detecting parent rollups:** for each backlog candidate not already set aside as `already scored`, call `<linear-mcp>__list_issues` with `parentId` = candidate `id`, `limit: 1`, `includeArchived: false` (the `parentId` filter is server-side and takes exactly one id, so this is an exact per-candidate existence check that never truncates). A non-empty result means the candidate has at least one child — skip it as a `parent rollup`. Keep all identified parent rollups in the `skipped` list with reason `parent rollup`; they receive no `save_issue` call.

Also set aside (do **not** score) any candidate that is **blocked** — a backlog candidate carrying the `blocked` label is **held** (left in Backlog, no `save_issue`, reason `blocked`), mirroring the file path's "hold blocked cards in Backlog" rule (see `commands/promote-tasks.md`). Keep all identified blocked candidates in the `skipped` list with reason `blocked`; they receive no `save_issue` call. (Honoring native Linear "is blocked by" **relations** here — not only the `blocked` label — needs a per-candidate `<linear-mcp>__get_issue` with `includeRelations: true` (not currently in this command's allowed tools), where a `Canceled` blocker is **never-satisfiable** so only an absent or `Done`/`completed`-type blocker satisfies the dependency — see `commands/handlers/linear-reoptimize.md`. That, plus wiring the same relation gate into the claim path (`commands/handlers/assets/linear-ready.py` / `linear-claim.md`), is deferred to a follow-up.)

### 6. Score each candidate

**Backfill missing estimates first.** Before scoring, backfill two fields on every remaining candidate — unconditionally, even one that will fail some other check and get tagged `human-approval-requested` anyway — mirroring the file path's backfill (`commands/promote-tasks.md` step 2):

- `priority` is `none` (`0`) → set to `medium` (Linear priority `3`). A flat static default is correct here: priority only orders work, it never gates anything. Never auto-set `urgent` (`1`).
- `estimate` is unset or not one of `1` / `2` / `3` / `5` → **estimate it** from the issue `description` (Fibonacci `1`/`2`/`3`/`5` — `estimate` is the same scale as the file path's `size`, see `linear-common.md`). This is deliberately not a static default: `estimate` feeds the same size-driven routing `size` does on the file path, so a blind constant could misroute; producing the number is the same judgment the scope-fit check below already requires, backfill just records it. If the honest estimate would exceed `5`, do not write a bogus `5` — leave `estimate` unset and let the scope-fit check below score LOW with reason `scope exceeds estimate 5 — split into sub-issues`.

Backfilled values are written in step 8's `save_issue` call alongside the state/label transition (not a separate write), with a one-line issue comment noting which field(s) the promoter auto-set so a human can cheaply correct a bad guess. `dry-run` reports the intended backfills without writing them. An auto-estimated `estimate` is fully trusted downstream exactly like a human-set one — see the file path's equivalent note in `skills/task/SKILL.md`'s Confidence check section.

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

### 7. Precheck workspace active-issue quota

Linear's free plan caps the **workspace-wide** count of **active** issues (states of type `unstarted` + `started` — Backlog, Done, and Canceled are explicitly excluded) at 250 by default, and a HIGH transition in step 8 (`backlog` → `unstarted`) is exactly what grows that count: promoting a batch of backlog cards is functionally "creating" that many new active issues from the quota's point of view, and running into the cap **mid-batch** is the failure this step exists to prevent. Skip this step entirely if **no** candidate scored HIGH in step 6 — a LOW-scored candidate never leaves Backlog, so it can't affect the count.

1. **Resolve and validate the quota.** Read `linear.active_issue_quota`. The supported domain is an integer `0`–`250` — `250` is not an arbitrary ceiling, it is Linear's actual free-plan hard cap, and the count in step 2 below is calibrated against it (see there for why a larger value isn't safely checkable with this tool). Default (unset) is `250`. `0` disables the check entirely, e.g. on a paid plan with no cap — skip the rest of this step. Any other value outside `0`–`250` (negative, non-integer, non-numeric, or `> 250`) is a config error: warn once (`linear.active_issue_quota <value> is invalid (must be an integer 0-250) — quota check disabled for this run`) and skip the rest of this step. Be clear about which way that fails: skipping the check is **fail open** on the quota — the batch then promotes unguarded, unlike an invalid `orphan_claim_hours`, which fails _closed_ because disabling that row withholds a write rather than permitting one. It is acceptable here only because step 8's live-race handling is the backstop: Linear enforces the real cap itself, and a rejected write becomes `held (quota)` rather than a crash. Do not clamp a too-large value down to `250` silently; that would silently check a smaller quota than configured.
2. **Count current active issues, workspace-wide.** The cap is per-workspace, not per-team, so omit `team`/`teamId` from both calls below (a configured project scope doesn't narrow this either — a promotion in one project still spends from the same shared quota); the MCP's `state` filter accepts a state **type** directly (`"unstarted"` / `"started"`) and resolves it workspace-wide without needing a per-team state id, the same way the GraphQL fast path filters on `state.type` — see `linear-common.md` "In-flight scan". Call `<linear-mcp>__list_issues` twice — `state: "unstarted"` and `state: "started"`, `includeArchived: false`, `limit: <active_issue_quota>` (**not** a hardcoded `250` — this is what makes the shortcut below exact at any validated quota) — and sum the two result counts as `active_count`. If either call returns `hasNextPage: true`, that state alone already holds `>= active_issue_quota` active issues (the call was capped at exactly the quota), so treat `active_count` as at-or-over the quota without paginating further or reading the second call.
3. **Compare against this run's batch.** Let `promoting` = the number of HIGH-scored candidates from step 6. When the quota forces a partial cut, **order them by Linear `priority` (most urgent first), then by ascending `estimate`, then by identifier** — using the step 6 backfilled values, so every candidate has both. Do **not** use the order they were scored: step 5 runs one query per configured project and merges, so scoring order is a query artifact, and cutting on it would spend scarce quota on an arbitrary subset while looking deliberate. `remaining = active_issue_quota - active_count`.
   - `remaining >= promoting` → plenty of room — proceed to Apply normally, no change to any candidate's outcome.
   - `0 < remaining < promoting` → **partial**: only the first `remaining` HIGH candidates **in that priority order** actually promote in step 8; the rest stay HIGH-scored but move to a `held (quota)` outcome — Apply must not touch their `state` or add `auto-eligible`, though their step 6 backfills (if any) still get saved (see step 8).
   - `remaining <= 0` → **none**: every HIGH candidate this run becomes `held (quota)` — Apply skips the `state`/`auto-eligible` transition for all of them.
4. **Carry `active_count`/`active_issue_quota` and the held identifiers** into step 8 (Apply) and step 9 (Report) — this is a read done once here, not re-queried per candidate.

### 8. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions **and the intended backfills** (per the report shape below) and exit **without** calling `save_issue`. Include the quota precheck's projected outcome in the dry-run output too — the point of checking it upfront is to surface the constraint before a real run ever hits it mid-batch.

**Batch writes — never fire them all in parallel.** The Linear MCP transport has limited concurrency headroom; issuing `save_issue`/`save_comment` for every scored candidate at once (a "dozens of tool calls in one turn" pattern) has caused cascading timeouts and partial-state corruption in practice — some candidates land transitioned while their siblings silently fail mid-batch. Apply writes serially, or in small concurrent groups — 2–5 candidates at a time is a judgment call, not a measured limit, and what matters is that the fan-out is bounded (the incident behind this rule fired ~36 `save_issue` calls in a single turn). Wait for each group to finish before starting the next.

Otherwise, for each scored candidate call `<linear-mcp>__save_issue` with `id` = candidate `id`, including any backfilled `priority`/`estimate` from step 6 regardless of HIGH/LOW/held (a LOW-scored or quota-held issue still gets its backfilled fields saved, so the human has less to fix):

- **HIGH (promoted):** `state` = the `unstarted`-type target state id from step 2; `labels` = the issue's existing label ids **plus** `auto-eligible` (the `save_issue` field is named `labels` and **replaces** the set — include existing labels to avoid clobbering); `priority`/`estimate` = the backfilled value(s) from step 6, if any.

  **Step 7's count is a snapshot, not a reservation** — another promotion, a concurrent `/add-task`, or another `/promote-tasks` run can spend the remaining quota between the read in step 7 and this write, so this `save_issue` can still hit Linear's own "exceeded free issue limit" error even after the precheck said there was room. Treat that specific error as live confirmation the quota is now exhausted, not a mid-execution failure to halt on: convert **this** candidate and **every remaining not-yet-applied** HIGH candidate in the batch to `held (quota)` (same treatment as the precomputed case below, with a comment noting it was caught live rather than precomputed) and continue the loop — still apply the LOW candidates and anything already promoted earlier in this same batch.
- **HIGH, held (quota):** whether flagged by step 7's precheck or caught live by the race above, **do not** change `state` or `labels` (leave the issue in Backlog, un-tagged) — this is the one case that skips the `auto-eligible` label a HIGH score would otherwise get, precisely because the issue isn't actually moving to `unstarted`; `priority`/`estimate` = the backfilled value(s) from step 6, if any. Call `<linear-mcp>__save_comment` with `issueId` = candidate `id` and `body`:
  - Precomputed by step 7: `/promote-tasks: held — active-issue quota <active_count>/<active_issue_quota> reached. Run /archive-tasks to free capacity, then re-run /promote-tasks to pick this up.`
  - Caught live by the race above: `/promote-tasks: held — active-issue quota reached while applying this batch (Linear rejected the promotion). Run /archive-tasks to free capacity, then re-run /promote-tasks to pick this up.`

  (append the backfill note in the same comment when one applies, same shape as the LOW comment below).

  **Do not re-post an identical held comment.** A held issue keeps its Backlog state and takes no label, so step 5 sees it as un-scored again on the next run and re-scores it — that is deliberate, it is how the issue gets picked up once capacity frees. But it means an over-quota state that persists until someone runs `/archive-tasks` would otherwise stack one identical comment per run, and this command composes with `/loop` and `/schedule`. So before calling `save_comment`, read the issue's comments (`<linear-mcp>__list_comments`) and skip the call when its most recent `/promote-tasks:` comment is already a `held — active-issue quota` comment. Post it again once anything else has intervened — a promotion, a LOW reason, or a backfill note — since the held state is then news rather than a repeat. The comment's value is delivered the first time; the repeats are noise. (The LOW path needs no such check: `human-approval-requested` makes step 5 skip the issue entirely on the next run.)
- **LOW:** **do not** change `state` (leave the issue in backlog); `labels` = existing label ids **plus** `human-approval-requested`; `priority`/`estimate` = the backfilled value(s) from step 6, if any. Call `<linear-mcp>__save_comment` with `issueId` = candidate `id` (the `save_comment` field is named `issueId`, not `id` — same as `linear-claim.md`) and `body` = a one-line reason (`/promote-tasks: <failed-check>`) so the human can fix it quickly — mirrors the file path's `# promoter:` comment; if any field was backfilled, note it in the same comment (e.g. `promoter auto-set priority to Medium, estimate to 2`).

If a candidate had no other failed check but **did** get a backfill, still call `save_comment` to note what was auto-set (there is no failed-check reason in that case, just the backfill note) — mirrors the file path's provenance comment being appended even when the card is otherwise HIGH.

Never move an issue to a `completed`- or `canceled`-type state, and never touch a non-`backlog` issue.

### 9. Report

Print the same summary shape as the file path (`commands/promote-tasks.md` step 4), keyed by Linear identifier, annotating any issue that got a backfill. Lead with the resolved scope from step 4 (`scope: project <name>` / `scope: all configured projects (<names>)` / `scope: whole backlog (no projects)`), and — if step 5's collection phase hit the shared 500-candidate budget — a prominent warning line **before** the summary (not a trailing footnote), naming the resolved scope and, for the all-configured union, which project the budget ran out on (candidates from later projects in the fixed query order were never collected). Because the budget is shared across the whole collection phase — never 500 per project — the `Promoted N of M candidates` line directly below always carries the true count: on a hit `M` **is** the budget, whatever the scope, so the warning states the truncation and the summary states the number. Neither repeats the other:

```
scope: project Payments revamp
⚠ candidate query for project Payments revamp hit the 500-candidate cap — some backlog issues may not have been scored this run.
Promoted 4 of 8 candidates:
  ready (3):
    - PRE-12  Fix broken import  (backfilled: estimate)
    - PRE-15  Bump eslint config
    - PRE-18  Remove stale alias  (backfilled: priority, estimate)
  needs_refinement (1):
    - PRE-21  Restructure auth module  (scope exceeds estimate 5 — split into sub-issues)
  held (quota) (1):
    - PRE-22  Add retry to sync job  (active-issue quota 247/250 — run /archive-tasks to free capacity)
  skipped (3):
    - PRE-09  (already scored)
    - PRE-10  (parent rollup)
    - PRE-11  (blocked)
backfilled (2):
  - PRE-12  (estimate)
  - PRE-18  (priority, estimate)
```

Skipped issues are reported with their reason — `already scored`, `parent rollup`, or `blocked`. **`held (quota)`** is a separate section from `skipped`: these scored HIGH and would otherwise have promoted, but step 7's quota precheck held them back — every one names the `active_count`/`active_issue_quota` reading and points at `/archive-tasks`, so the note in step 7 is not silently lost between the check and the report. Omit the section entirely when nothing was held. The 500-cap warning (if it applied) leads the report per above, not a trailing footnote.

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
