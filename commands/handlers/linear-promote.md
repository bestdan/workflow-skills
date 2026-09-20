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

Also set aside (do **not** score) any candidate that is **blocked** — a backlog candidate carrying the `blocked` label is **held** (left in Backlog, no `save_issue`, reason `blocked`), mirroring the file path's "hold blocked cards in Backlog" rule (see `commands/promote-tasks.md`) and using the same `blocked` reason string as the `Ready-candidate selection` gate (`commands/handlers/assets/_linear_rank.py`). Keep all identified blocked candidates in the `skipped` list with reason `blocked`; they receive no `save_issue` call. (Honoring native Linear "is blocked by" **relations** here — not only the `blocked` label — needs a per-candidate `<linear-mcp>__get_issue` with `includeRelations: true` (not currently in this command's allowed tools), where a `Canceled` blocker is **never-satisfiable** so only an absent or `Done`/`completed`-type blocker satisfies the dependency — see `commands/handlers/linear-reoptimize.md`. That, plus wiring the same relation gate into the claim path (`commands/handlers/assets/linear-ready.py` / `linear-claim.md`), is deferred to a follow-up.)

### 6. Score each candidate

**Backfill `priority` and `estimate` first,** before scoring. The rules are defined once in `commands/handlers/task-fill.md` — the static `medium` default, the model-produced Fibonacci estimate, the over-ceiling rule, reason precedence, the held-card exception, and the fact that a backfilled value is trusted downstream. Read it and apply it; do not re-derive any of it here.

Its **`linear` adapter row** is this path's half — the 0–4 priority encoding, the estimate ladder, and the provenance comment all live there, and this file deliberately does not repeat them. The one thing worth saying twice is the write: both fields ride out on step 8's `save_issue` alongside the state/label transition, never a second call. `dry-run` reports the intended backfills without writing them.

If a relative path doesn't resolve, find it with **Glob** (`**/commands/handlers/task-fill.md`) and Read it.

Then, for each candidate, run the **confidence check** from `skills/task/SKILL.md` — the **same judgment-based gate the file path uses** (`commands/promote-tasks.md` step 2), read against Linear fields rather than frontmatter, **using the backfilled `priority`/`estimate` values** from above:

**HIGH (→ promote)** requires ALL of:

- `title` present and non-empty.
- `priority` is set (Linear `priority` ≠ `0`). Linear's `none` (`0`) counts as "no priority set", but backfill above always sets it to `medium` first, so this check never fails in practice — it stays as a defensive check against a backfill bug rather than a live gate. `urgent` (`1`) is not excluded — it passes this check like any other priority.
- `estimate` is set and is one of `1` / `2` / `3` / `5` (Linear's `estimate` is the same Fibonacci scale as our task `size` — see `linear-common.md`). An unset or out-of-scale estimate fails (the analogue of a missing/invalid `size`) — this only still fails when the honest backfill estimate exceeded `5` and was left unset.
- `description` is non-empty and contains acceptance-style content — a concrete, checkable outcome (the analogue of the file path's `## Acceptance Criteria` requirement). A bare title with an empty or "investigate X" description fails.
- `description` has no unresolved open-questions / TBD content.
- **Scope fits estimate 5 (judgment, not keywords).** Assess whether the described scope plausibly fits within estimate `5` (~300 lines / ~5 files — see **Task size** in `skills/task/SKILL.md`), weighing the stated `estimate` against the breadth of the description. A word like "migrate" or "refactor" is not itself disqualifying; a description implying multi-module rework is. If the scope clearly exceeds `5`, score LOW with reason `scope exceeds estimate 5 — split into sub-issues`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) performs that split on the Linear path — convert the issue into a parent and create the components as child issues.

**LOW** if any HIGH condition fails. Record the first failed check as the reason (e.g. `no estimate set`, `description missing acceptance criteria`, `no priority set`) — **except** when backfill deliberately left `estimate` unset because the honest estimate exceeded `5`, where `task-fill.md`'s **reason precedence** gives the slot to `scope exceeds estimate 5 — split into sub-issues` instead.

> **`max_estimate` is deliberately absent from the list above.** It is a claim-time bound on this handler (`linear-ready.py` / `_linear_rank.py`), not a promote-time one, and since v2.59 `gh-issue` matches — see `commands/handlers/task-fill.md` → "Where the estimate gate lives", which owns the rule for both. The scale check above is a different thing: it asks whether the estimate is a legal Fibonacci value, not whether it is under an operator's bound.

As on the file path, this scope gate is **model judgment, not a deterministic rule** — acceptable because `/promote-tasks` is not a blocking CI gate: a misjudged issue lands tagged `human-approval-requested` for a human to confirm, never silently lost.

### 7. Report workspace issue-quota headroom (advisory — never holds a promotion)

Linear's free plan caps a workspace at a fixed number of **non-archived issues of every state** — `250` as of 2026-09, sourced from `linear.active_issue_quota` (see `linear-common.md`, which owns the figure and says why the repo cannot verify it). Backlog, Todo, In Progress, Done and Canceled all count; **archiving is the only thing that removes one**, and completing or cancelling an issue frees nothing. Over the cap, Linear refuses to **create** new issues — existing issues still update normally.

**So a promotion spends no quota.** Step 8 moves an issue `backlog` → `unstarted`, and that issue was already counted in both states. This step therefore **reports** headroom and never holds a candidate back.

> **It used to hold them, on a premise that was wrong.** The earlier text read "promoting a batch of backlog cards is functionally creating that many new active issues from the quota's point of view", and counted only `unstarted` + `started` against the 250. Both halves were wrong: the count omitted the Backlog, Done and Canceled issues that do consume the cap, and the conclusion gated an operation that consumes none of it. The `held (quota)` outcome is gone with it — a promotion is never refused for quota, so nothing is ever held for it.

**Run this step on every invocation, including one that promotes nothing.** It used to be skipped when no candidate scored HIGH, on the reasoning that a LOW-scored candidate never leaves Backlog and so cannot affect the count — true of the gate, and irrelevant now. Workspace fullness is a fact about the workspace, not about this run's candidates, and a run where everything scored LOW is if anything the one most likely to be sitting on a backlog worth archiving. The cost is one `list_issues` call.

1. **Resolve and validate the quota.** Read `linear.active_issue_quota`. The supported domain is an integer `0`–`250`, whose ceiling is the observed free-plan figure `linear-common.md` records. Default (unset) is `250`. `0` disables the report, e.g. on a paid plan with no cap — skip the rest of this step. Any other value outside `0`–`250` (negative, non-integer, non-numeric, or `> 250`) is a config error: warn once (`linear.active_issue_quota <value> is invalid (must be an integer 0-250) — quota report disabled for this run`) and skip the rest of this step. Do not clamp a too-large value down to `250` silently; that would silently report against a smaller quota than configured. Nothing here is fail-open or fail-closed any more — the step writes nothing either way.
2. **Count non-archived issues, workspace-wide.** The cap is per-workspace, not per-team, so omit `team`/`teamId` (a configured project scope doesn't narrow it either — every project draws on the same workspace cap). Call `<linear-mcp>__list_issues` **once**, with **no `state` filter**, `includeArchived: false`, `limit: <active_issue_quota>`, and take the result count as `issue_count`. A `state` filter is exactly the bug this step used to have: every state counts, so filtering to any of them undercounts. If the call returns `hasNextPage: true`, the workspace already holds `>= active_issue_quota` non-archived issues (the call was capped at exactly the quota), so treat it as at-or-over without paginating further.

   **Both halves of that are measured, not assumed** — probed 2026-09-16 against the live PreThink workspace. An unfiltered call with `includeArchived: false` returned issues of every state type, `completed` among them (132 issues, `hasNextPage: false`), so omitting the `state` filter does not silently narrow to active states. And `limit: 10` returned exactly 10 with `hasNextPage: true`, confirming the count saturates at `limit` — which is why step 3 renders a lower bound rather than an exact figure.
3. **Report, and promote regardless.** Let `remaining = active_issue_quota - issue_count`.
   - `remaining > 0` → note the headroom in step 9's report and proceed.
   - `remaining <= 0` → the workspace is at or over the cap. **Report it as a lower bound:** step 2's call is capped at `limit: <active_issue_quota>`, so `issue_count` saturates there and can never exceed it — render `≥<active_issue_quota>/<active_issue_quota>` whenever step 2 saw `hasNextPage: true`, never an exact-looking figure that understates. **Every HIGH candidate still promotes** — the transition is legal and costs nothing. Lead step 9's report with one of two lines, chosen by whether the configured threshold **is** Linear's cap. The domain allows any value `0`–`250`, so a sub-250 threshold is an operator's own early-warning line and must never be reported as a refusal Linear will not make:

     - `active_issue_quota` **is 250** (Linear's real cap) — the next `/add-task` or `/push-plan` **will** be refused, so say so:

       `/promote-tasks: workspace at ≥250/250 non-archived issues — Linear will refuse to CREATE new issues until you run /archive-tasks. Promotions are unaffected by this cap.`

     - `active_issue_quota` **is below 250** — report the threshold as the operator's own, and name Linear's actual cap so the reader can tell the two apart:

       `/promote-tasks: workspace at <issue_count>/<active_issue_quota> non-archived issues — your configured warning threshold (Linear's hard cap is 250). Run /archive-tasks to free room. Promotions are unaffected by this cap.`
4. **Carry `issue_count`/`active_issue_quota`** into step 9 (Report) — this is one read, not a per-candidate query.

### 8. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions **and the intended backfills** (per the report shape below) and exit **without** calling `save_issue`. Include step 7's quota reading in the dry-run output too — it is a fact about the workspace worth surfacing whether or not anything is applied.

**Batch writes — never fire them all in parallel.** The Linear MCP transport has limited concurrency headroom; issuing `save_issue`/`save_comment` for every scored candidate at once (a "dozens of tool calls in one turn" pattern) has caused cascading timeouts and partial-state corruption in practice — some candidates land transitioned while their siblings silently fail mid-batch. Apply writes serially, or in small concurrent groups — 2–5 candidates at a time is a judgment call, not a measured limit, and what matters is that the fan-out is bounded (the incident behind this rule fired ~36 `save_issue` calls in a single turn). Wait for each group to finish before starting the next.

Otherwise, for each scored candidate call `<linear-mcp>__save_issue` with `id` = candidate `id`, including any backfilled `priority`/`estimate` from step 6 regardless of HIGH/LOW (a LOW-scored issue still gets its backfilled fields saved, so the human has less to fix):

- **HIGH (promoted):** `state` = the `unstarted`-type target state id from step 2; `labels` = the issue's existing label ids **plus** `auto-eligible` (the `save_issue` field is named `labels` and **replaces** the set — include existing labels to avoid clobbering); `priority`/`estimate` = the backfilled value(s) from step 6, if any.

  **A promotion is not refused for quota, so there is no quota race to handle here.** The issue already counts against the cap in both its old and new state, and Linear's limit refuses _creates_, not updates. An `exceeded free issue limit` error on this `save_issue` would therefore contradict the documented rule — treat it as a genuine unexpected failure and surface it, rather than converting the batch to a held outcome. (The earlier text instructed exactly that conversion, inferred from the wrong premise this step used to carry; nothing ever measured it.)

- **LOW:** **do not** change `state` (leave the issue in backlog); `labels` = existing label ids **plus** `human-approval-requested`; `priority`/`estimate` = the backfilled value(s) from step 6, if any. Call `<linear-mcp>__save_comment` with `issueId` = candidate `id` (the `save_comment` field is named `issueId`, not `id` — same as `linear-claim.md`) and `body` = a one-line reason (`/promote-tasks: <failed-check>`) so the human can fix it quickly — mirrors the file path's `# promoter:` comment; if any field was backfilled, note it in the same comment (e.g. `promoter auto-set priority to Medium, estimate to 2`).

If a candidate had no other failed check but **did** get a backfill, still call `save_comment` to note what was auto-set (there is no failed-check reason in that case, just the backfill note) — mirrors the file path's provenance comment being appended even when the card is otherwise HIGH.

Never move an issue to a `completed`- or `canceled`-type state, and never touch a non-`backlog` issue.

### 9. Report

Print the same summary shape as the file path (`commands/promote-tasks.md` step 4), keyed by Linear identifier, annotating any issue that got a backfill. Lead with the resolved scope from step 4 (`scope: project <name>` / `scope: all configured projects (<names>)` / `scope: whole backlog (no projects)`), and — if step 5's collection phase hit the shared 500-candidate budget — a prominent warning line **before** the summary (not a trailing footnote), naming the resolved scope and, for the all-configured union, which project the budget ran out on (candidates from later projects in the fixed query order were never collected). Because the budget is shared across the whole collection phase — never 500 per project — the `Promoted N of M candidates` line directly below always carries the true count: on a hit `M` **is** the budget, whatever the scope, so the warning states the truncation and the summary states the number. Neither repeats the other:

```
scope: project Payments revamp
⚠ workspace at ≥250/250 non-archived issues — Linear will refuse to CREATE new issues until you run /archive-tasks. Promotions are unaffected by this cap.
⚠ candidate query for project Payments revamp hit the 500-candidate cap — some backlog issues may not have been scored this run.
Promoted 5 of 8 candidates:
  ready (4):
    - PRE-12  Fix broken import  (backfilled: estimate)
    - PRE-15  Bump eslint config
    - PRE-18  Remove stale alias  (backfilled: priority, estimate)
    - PRE-22  Add retry to sync job
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

Skipped issues are reported with their reason — `already scored`, `parent rollup`, or `blocked`. **There is no `held (quota)` section**: a promotion spends no quota, so nothing is ever held for one (see step 7). When step 7 finds the workspace at or over the cap, its warning **leads** the report — the constraint is real, it just binds the next `/add-task` or `/push-plan` rather than this run. The 500-cap warning (if it applied) leads too; print both, quota first.

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
