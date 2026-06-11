# jira handler — /promote-tasks flow

Invoked from `/promote-tasks` when `handler: jira` is configured. Scores the project's **new, un-scored** issues against the same confidence check the file path uses, then applies the kanban transitions by **status**: HIGH → transition to `ready_status` (the jira analogue of moving to `Todo`/`auto-eligible`); LOW → transition to `refinement_status` and leave a comment naming the failed check. Both target statuses are taken from config when set, or resolved dynamically and prompted for when unset (step 3a).

**Shared reference:** the Atlassian MCP preflight is `commands/handlers/jira.md` step 1; the `ready_status`/`refinement_status` config keys are defined in `commands/handlers/jira-config.md`; the field-mapped confidence gate is `commands/handlers/linear-promote.md` step 6, read here against the Jira issue rather than Linear fields.

> **MCP namespace.** `<atlassian-mcp>__` is `mcp__claude_ai_Atlassian__` or `mcp__atlassian__` depending on the install (see `jira-config.md`) — substitute the prefix loaded in your session. Tool names after the prefix (`getAccessibleAtlassianResources`, `searchJiraIssuesUsingJql`, `getTransitionsForJiraIssue`, `transitionJiraIssue`, `addCommentToJiraIssue`) are identical across installs.

> **Target statuses: configured or resolved-and-prompted.** `ready_status` and `refinement_status` are both **target status names**, and both are **optional**. When set in config they are used two ways: as a **status name** in the step-3 candidate query (`status != "<ready_status>"`), and — because `transitionJiraIssue` takes a transition **id**, not a status name — resolved to a transition id at apply time (step 5) by matching the configured name against the issue's available transitions. When **unset**, step 3a resolves the project's reachable statuses **dynamically** (from a candidate's available transitions) and prompts the user to pick via `AskUserQuestion`, so no status id or name is ever hard-coded on either path. The step-3 `status !=` filter matches on status name; if a site's transition name differs from its target status name the step-5 lookup still resolves correctly (it matches the transition's target status `to.name`), and the step-3 dedup backstop covers the query-filter edge regardless.

> **Hard rule: this path only ever touches new, un-scored issues** — issues in the project's initial (`statusCategory = "To Do"`) status that are not already sitting in `ready_status` or `refinement_status`. An issue already in either configured status has been scored and is out of the promoter's lane, exactly as the file path never touches tasks past `status: new` and `linear-promote.md` never touches a non-`backlog` issue. If you are about to `transitionJiraIssue` an already-scored or non-`To Do` issue, you have a bug — stop.

## Steps

### 1. Preflight

Run the Atlassian MCP preflight exactly as `commands/handlers/jira.md` step 1: call `<atlassian-mcp>__getAccessibleAtlassianResources` (no args) and confirm a resource whose `url` matches `https://<jira.site>`. On either failure, **stop** with the same messages ("Jira handler needs the Atlassian MCP. Install/connect it in Claude Code settings, then re-run." / "Configured Jira site `<site>` is not in your accessible Atlassian resources."). Do not fall back to another handler.

### 2. Resolve config (optional keys)

Read `dev_docs/tasks/.task-config.yml`. Resolve `jira.site` and `jira.project` (as the create flow does), plus the two promote keys and the optional blocked-status list:

- `jira.ready_status` — the HIGH transition target.
- `jira.refinement_status` — the LOW transition target.
- `jira.blocked_statuses` — optional list of status names that mean "blocked" on this board (e.g. `["Blocked", "On Hold/Blocked"]`); excluded from the step-3 candidate set. Omit or leave empty if the board has none.

Both keys are **optional**. Classify the run:

- **Configured path** — both `ready_status` and `refinement_status` are set/non-empty. Use them as the target status names; step 3 includes both `status !=` filters and step 3a is skipped.
- **Prompt-when-unset path** — either key is unset/empty. Do **not** stop and do **not** guess a status name. Defer resolution to **step 3a**, which resolves the project's reachable statuses dynamically and prompts the user. For the step-3 query, omit the `status !=` clause for whichever key is still unset (you cannot filter on a value you do not yet have); the unset key(s) are filled in by step 3a before any transition is applied.

### 3. Query candidates

Call `<atlassian-mcp>__searchJiraIssuesUsingJql` with:

- `cloudId`: `<jira.site>`
- `jql`: `project = "<project>" AND statusCategory = "To Do" AND Flagged IS EMPTY`, plus an `AND status != "<status>"` clause for **each promote key that is set** (both clauses on the configured path; only the set key — or neither — on the prompt-when-unset path), plus an `AND status NOT IN ("<blocked-status>", …)` clause when `jira.blocked_statuses` is non-empty, then `ORDER BY created ASC`
- `fields`: `["summary", "status", "priority", "labels", "description"]`
- `maxResults`: 50

The `status !=` clauses exclude issues already transitioned into a configured status **at query time** — mirroring how `linear-promote.md` reads candidates only from `backlog` and `gh-issue-promote.md` excludes already-labeled issues — so the 50-item window isn't consumed by already-scored issues. On the prompt-when-unset path the clause for an unset key is omitted (its value isn't known yet); step 3a's backstop sets those aside once the status is chosen. Read the issues from whichever key the server returns (`issues[]` or `issues.nodes[]`; the create flow reads `issues.nodes[0]`).

The `Flagged IS EMPTY` and `status NOT IN (…)` clauses keep **blocked** issues out of the candidate set — without them, an issue someone marked blocked but left in the project's initial (`statusCategory = "To Do"`) category would be scored as fresh backlog and transitioned to `ready_status`. `Flagged` is a native, site-level Jira field, so `Flagged IS EMPTY` is safe to apply on any board unconditionally. A dedicated **blocked status** is the other common encoding, but the same status name can sit in different `statusCategory` values across boards (`Blocked` lands in `To Do` on some, `In Progress` on others), so it can't be hard-coded — list those names in `jira.blocked_statuses` (`jira-config.md`) and the `status NOT IN (…)` clause is added only when that key is non-empty. Both clauses are server-side. (The `blocked` **label** is the least reliable signal and is not used here; an unresolved `is blocked by` link is the most precise but isn't expressible in core JQL.)

> **Parent rollup guard — JQL finding (PRE-129).** This flow does not yet skip **parent rollups** (issues `/break-down-task` has decomposed into children), the way `linear-promote.md` step 5 does. When that skip is wired in, it can run **server-side** — unlike gh-issue (whose sub-issue link isn't bulk-filterable, see `gh-issue-promote.md` step 3), Jira exposes the parent relationship to JQL:
>
> - `parent IS EMPTY` / `parent IS NOT EMPTY`, `parent = <KEY>`, and `parent IN (…)` are **native** JQL — the unified `parent` field covers both sub-tasks and epic children on modern Jira Cloud; `issuetype IN subTaskIssueTypes()` is a native function for the sub-task issue types.
> - There is **no native "has subtasks" predicate** ([JRACLOUD-67108](https://jira.atlassian.com/browse/JRACLOUD-67108)) — `hasSubtasks()`, `subtasksOf()`, `parentsOf()`, and `linkedIssuesOf()` are ScriptRunner `issueFunction` add-on functions and must **not** be assumed present.
> - So the native pattern mirrors `linear-promote.md`'s cross-state `parentId` sweep: after the step-3 query, run one extra `searchJiraIssuesUsingJql` over the project for children (`project = "<project>" AND parent IS NOT EMPTY`, `fields: ["parent"]`, `maxResults: 100`), collect the distinct `fields.parent.key` values into a parent-key set, and skip any step-3 candidate whose `key` is in that set (reason `parent rollup`). If that sweep returns exactly 100 the page may be truncated, so fall back to a per-candidate `parent = "<KEY>"` check (`maxResults: 1`) for any candidate not yet confirmed a parent — the same truncation fallback `linear-promote.md` step 5 applies to its 100-item `parentId` sweep. The sweep is itself server-side filtered; only the set-membership test is client-side — exactly as in `linear-promote.md` step 5.

As a backstop to the JQL filter, set aside (do **not** score) any returned issue whose current `fields.status.name` already equals `ready_status` or `refinement_status` (e.g. if those values are status names the `status !=` filter didn't catch as transitions). Keep these in a separate `skipped` list so step 6 can report them; they receive no transition. Limit 50 — if exactly 50 are returned the page may be truncated; note possible truncation in the report and do not paginate. Report and exit if no un-scored candidates remain.

### 3a. Resolve statuses & prompt (prompt-when-unset path only)

Runs only when step 2 classified the run as **prompt-when-unset** and step 3 returned at least one candidate. (If step 3 returned no candidates there is nothing to promote — report and exit; no prompt is needed.) On the configured path, skip this step.

1. **Enumerate reachable statuses dynamically.** Pick the first candidate from step 3 as a representative and call:

   ```
   <atlassian-mcp>__getTransitionsForJiraIssue
     cloudId: <jira.site>
     issueIdOrKey: <KEY>
   ```

   Collect the distinct **target status names** (`transitions[].to.name`) — the statuses reachable from the project's new/initial status, resolved at runtime with **no hard-coded ids or names**. Drop the distinct current statuses (`fields.status.name`) of **all** step-3 candidates from the options — not just the representative's — so the user can't pick an initial status as a target (that would filter out every new issue sitting in it — see `jira-config.md`). Because the candidate query spans `statusCategory = "To Do"`, which can hold more than one status, excluding only the representative's status would leave another candidate's initial status offerable; the full candidate set is already in hand from step 3, so exclude every one of them.

2. **Prompt.** For **each unset key**, ask the user via `AskUserQuestion` (one question per unset key — header `Ready status` for `ready_status`, `Refinement status` for `refinement_status`) which target status a HIGH (ready) / LOW (refinement) issue should move to. Offer the distinct target status names from step 1 as options, surfacing the most plausible first: if **4 or fewer** were enumerated, offer all of them (the list is exhaustive — no "Other" needed); if **more than 4**, offer the 3 most plausible plus "Other" so the total fits the 4-option `AskUserQuestion` max. When offered, "Other" lets the user type a status name, which you **re-validate** against the enumerated target statuses — reject a value no transition leads to rather than guessing. The two targets must differ from each other and from the initial status. When **both** keys are unset, prompt `ready_status` first; for the `refinement_status` prompt, exclude the just-chosen `ready_status` from the offered options and reject it if entered via "Other", so the two resolved targets are guaranteed distinct.

3. **Persist (optional).** Offer to write the chosen value(s) back to the `jira:` block in `dev_docs/tasks/.task-config.yml` (`ready_status` / `refinement_status`) so the prompt does not recur. If the user declines, use the choice for this run only.

4. **Re-apply the dedup backstop.** Now that both targets are known, set aside (do **not** score) any step-3 candidate whose current `fields.status.name` already equals the chosen `ready_status` or `refinement_status` — the prompt-when-unset query could not exclude them up front. Keep them in the `skipped` list for step 6.

After step 3a, `ready_status` and `refinement_status` are both resolved for the rest of the run; steps 4–6 proceed identically to the configured path.

### 4. Score each candidate

For each candidate, run the **confidence check** from `skills/task/SKILL.md` — the **same judgment-based gate the file path uses** (`commands/promote-tasks.md` step 2), read against the Jira issue per the field mapping `linear-promote.md` step 6 defines. Jira issues carry a native `priority` but no `size`/`estimate` field (story points, when present, are a custom field), so size folds into the scope judgment.

**HIGH (→ promote)** requires ALL of:

- `fields.summary` present and non-empty.
- `fields.priority.name` is **not** the project's top/urgent level (`Highest`, or `P1` under the `P`-scheme — the same mapping `jira.md`'s List section documents). A non-urgent or unset priority passes — Jira priority is coarse metadata here, like gh-issue's optional priority label.
- `fields.description` contains acceptance-style content — a `## Acceptance Criteria` section (or an equivalent concrete, checkable outcome). A bare summary or an "investigate X" description fails with `description missing acceptance criteria`.
- `fields.description` has no unresolved `## Open Questions` / `## TBD` content (an empty heading is fine) → otherwise `unresolved open questions`.
- **Scope fits one PR (~size 5), judgment not keywords.** Weigh the description's breadth against ~300 lines / ~5 files (see **Task size** in `skills/task/SKILL.md`); a story-points custom field, if the project exposes one, is a hint. If the scope clearly exceeds size `5`, score LOW with reason `scope exceeds size 5 — split into sub-issues`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) performs that split.

**LOW** if any HIGH condition fails. Record the first failed check as the reason (e.g. `description missing acceptance criteria`, `priority is urgent`, `unresolved open questions`).

As on the file path, the scope gate is **model judgment, not a deterministic rule** — acceptable because `/promote-tasks` is not a blocking CI gate: a misjudged issue lands in `refinement_status` for a human to confirm, never silently lost.

### 5. Apply

If `$ARGUMENTS` is `dry-run`, print the proposed transitions (per the report shape below) and exit **without** any `transitionJiraIssue`/`addCommentToJiraIssue` call.

Otherwise, for each scored candidate, first **resolve the target status name to a transition id** — `transitionJiraIssue` accepts a transition **id** (`transition: { id: "<id>" }`), not a status name:

```
<atlassian-mcp>__getTransitionsForJiraIssue
  cloudId: <jira.site>
  issueIdOrKey: <KEY>
```

From the returned `transitions[]`, pick the entry whose **target status** matches the resolved target status name (`ready_status`/`refinement_status`, whether set in config or chosen in step 3a) — `to.name == <status>` (fall back to the transition's own `name == <status>` for sites where they coincide). `<status>` is `ready_status` for HIGH, `refinement_status` for LOW. Capture its `id` as `<transition-id>`. If no transition matches, **do not guess** — surface the configured key and the available transition names so the user can fix the config, and skip that issue.

Then transition:

- **HIGH:**

  ```
  <atlassian-mcp>__transitionJiraIssue
    cloudId: <jira.site>
    issueIdOrKey: <KEY>
    transition: { id: "<transition-id>" }   # id resolved from ready_status
  ```

- **LOW:**

  ```
  <atlassian-mcp>__transitionJiraIssue
    cloudId: <jira.site>
    issueIdOrKey: <KEY>
    transition: { id: "<transition-id>" }   # id resolved from refinement_status

  <atlassian-mcp>__addCommentToJiraIssue
    cloudId: <jira.site>
    issueIdOrKey: <KEY>
    commentBody: "/promote-tasks: <failed-check>"
  ```

  The comment names the failed check so the human can fix it quickly — the jira analogue of the file path's `# promoter:` frontmatter comment and `linear-promote.md`'s LOW comment.

The configured value names the **target status**; the transition id is resolved per-issue from `getTransitionsForJiraIssue` (transitions are issue- and workflow-specific, so resolve for each candidate rather than caching one id across the batch). Never transition an issue to a `Done`/`completed`-category status here — promotion only moves an issue to `ready_status` or `refinement_status`.

### 6. Report

Print the same summary shape as the file path (`commands/promote-tasks.md` step 4), keyed by issue key:

```
Promoted 4 of 6 candidates:
  ready (3):
    - PLAT-142  Fix broken import
    - PLAT-145  Bump eslint config
    - PLAT-148  Remove stale alias
  needs_refinement (1):
    - PLAT-151  Restructure auth module  (scope exceeds size 5 — split into sub-issues)
  skipped (2, already scored):
    - PLAT-109
    - PLAT-110
```

Append the truncation note from step 3 if it applied.
