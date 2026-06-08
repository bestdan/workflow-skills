# jira handler — /promote-tasks flow

Invoked from `/promote-tasks` when `handler: jira` is configured. Scores the project's **new, un-scored** issues against the same confidence check the file path uses, then applies the kanban transitions by **status**: HIGH → transition to the configured `ready_status` (the jira analogue of moving to `Todo`/`auto-eligible`); LOW → transition to `refinement_status` and leave a comment naming the failed check.

**Shared reference:** the Atlassian MCP preflight is `commands/handlers/jira.md` step 1; the `ready_status`/`refinement_status` config keys are defined in `commands/handlers/jira-config.md`; the field-mapped confidence gate is `commands/handlers/linear-promote.md` step 6, read here against the Jira issue rather than Linear fields.

> **MCP namespace.** `<atlassian-mcp>__` is `mcp__claude_ai_Atlassian__` or `mcp__atlassian__` depending on the install (see `jira-config.md`) — substitute the prefix loaded in your session. Tool names after the prefix (`getAccessibleAtlassianResources`, `searchJiraIssuesUsingJql`, `transitionJiraIssue`, `addCommentToJiraIssue`) are identical across installs.

> **Scope: static (configured) statuses only.** This slice **requires** `ready_status` and `refinement_status` to be set in config and passes each **verbatim** to `transitionJiraIssue` as the transition target. The same value is also used as a **status name** in the step-3 candidate query (`status != "<ready_status>"`); the static slice assumes the configured value matches **both** the workflow transition and its target status name (true for the common Jira setup where the transition is named after its target). When a site's transition name differs from its target status name, the step-3 dedup backstop can miss an already-scored issue — that, along with auto-resolving a status name to its transition with no configured value (and the prompt-when-unset path), is deferred to a sibling slice. Do not attempt dynamic transition discovery here.

> **Hard rule: this path only ever touches new, un-scored issues** — issues in the project's initial (`statusCategory = "To Do"`) status that are not already sitting in `ready_status` or `refinement_status`. An issue already in either configured status has been scored and is out of the promoter's lane, exactly as the file path never touches tasks past `status: new` and `linear-promote.md` never touches a non-`backlog` issue. If you are about to `transitionJiraIssue` an already-scored or non-`To Do` issue, you have a bug — stop.

## Steps

### 1. Preflight

Run the Atlassian MCP preflight exactly as `commands/handlers/jira.md` step 1: call `<atlassian-mcp>__getAccessibleAtlassianResources` (no args) and confirm a resource whose `url` matches `https://<jira.site>`. On either failure, **stop** with the same messages ("Jira handler needs the Atlassian MCP. Install/connect it in Claude Code settings, then re-run." / "Configured Jira site `<site>` is not in your accessible Atlassian resources."). Do not fall back to another handler.

### 2. Resolve config (required keys)

Read `dev_docs/tasks/.task-config.yml`. Resolve `jira.site` and `jira.project` (as the create flow does), plus the two promote keys:

- `jira.ready_status` — the HIGH transition target.
- `jira.refinement_status` — the LOW transition target.

If either `ready_status` or `refinement_status` is unset/empty, **stop** with: "jira promote needs `ready_status` and `refinement_status` set in `dev_docs/tasks/.task-config.yml` (run /task-config jira). Dynamic status resolution is not available yet." Do not guess transition names.

### 3. Query candidates

Call `<atlassian-mcp>__searchJiraIssuesUsingJql` with:

- `cloudId`: `<jira.site>`
- `jql`: `project = "<project>" AND statusCategory = "To Do" AND status != "<ready_status>" AND status != "<refinement_status>" ORDER BY created ASC`
- `fields`: `["summary", "status", "priority", "labels", "description"]`
- `maxResults`: 50

The `status !=` clauses exclude issues already transitioned into either configured status **at query time** — mirroring how `linear-promote.md` reads candidates only from `backlog` and `gh-issue-promote.md` excludes already-labeled issues — so the 50-item window isn't consumed by already-scored issues. Read the issues from whichever key the server returns (`issues[]` or `issues.nodes[]`; the create flow reads `issues.nodes[0]`).

As a backstop to the JQL filter, set aside (do **not** score) any returned issue whose current `fields.status.name` already equals `ready_status` or `refinement_status` (e.g. if those values are status names the `status !=` filter didn't catch as transitions). Keep these in a separate `skipped` list so step 6 can report them; they receive no transition. Limit 50 — if exactly 50 are returned the page may be truncated; note possible truncation in the report and do not paginate. Report and exit if no un-scored candidates remain.

### 4. Score each candidate

For each candidate, run the **confidence check** from `skills/task/SKILL.md` — the **same judgment-based gate the file path uses** (`commands/promote-tasks.md` step 2), read against the Jira issue per the field mapping `linear-promote.md` step 6 defines. Jira issues carry a native `priority` but no `size`/`estimate` field (story points, when present, are a custom field), so size folds into the scope judgment.

**HIGH (→ promote)** requires ALL of:

- `summary` present and non-empty.
- `fields.priority.name` is **not** the project's top/urgent level (`Highest`, or `P1` under the `P`-scheme — the same mapping `jira.md`'s List section documents). A non-urgent or unset priority passes — Jira priority is coarse metadata here, like gh-issue's optional priority label.
- `description` contains acceptance-style content — a `## Acceptance Criteria` section (or an equivalent concrete, checkable outcome). A bare summary or an "investigate X" description fails with `description missing acceptance criteria`.
- `description` has no unresolved `## Open Questions` / `## TBD` content (an empty heading is fine) → otherwise `unresolved open questions`.
- **Scope fits one PR (~size 5), judgment not keywords.** Weigh the description's breadth against ~300 lines / ~5 files (see **Task size** in `skills/task/SKILL.md`); a story-points custom field, if the project exposes one, is a hint. If the scope clearly exceeds size `5`, score LOW with reason `scope exceeds size 5 — split into sub-issues`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) performs that split.

**LOW** if any HIGH condition fails. Record the first failed check as the reason (e.g. `description missing acceptance criteria`, `priority is urgent`, `unresolved open questions`).

As on the file path, the scope gate is **model judgment, not a deterministic rule** — acceptable because `/promote-tasks` is not a blocking CI gate: a misjudged issue lands in `refinement_status` for a human to confirm, never silently lost.

### 5. Apply

If `$ARGUMENTS` is `dry-run`, print the proposed transitions (per the report shape below) and exit **without** any `transitionJiraIssue`/`addCommentToJiraIssue` call.

Otherwise, for each scored candidate:

- **HIGH:**

  ```
  <atlassian-mcp>__transitionJiraIssue
    cloudId: <jira.site>
    issueIdOrKey: <KEY>
    transition: <ready_status>          # passed verbatim from config
  ```

- **LOW:**

  ```
  <atlassian-mcp>__transitionJiraIssue
    cloudId: <jira.site>
    issueIdOrKey: <KEY>
    transition: <refinement_status>     # passed verbatim from config

  <atlassian-mcp>__addCommentToJiraIssue
    cloudId: <jira.site>
    issueIdOrKey: <KEY>
    commentBody: "/promote-tasks: <failed-check>"
  ```

  The comment names the failed check so the human can fix it quickly — the jira analogue of the file path's `# promoter:` frontmatter comment and `linear-promote.md`'s LOW comment.

Pass the configured value as `transition` **verbatim** — do not look it up, remap it, or hard-code a transition id (that resolution is the sibling slice's job). If `transitionJiraIssue` errors because the value is not a valid transition for the issue, surface the error and the configured key so the user can fix the config; do not retry with a guessed transition. Never transition an issue to a `Done`/`completed`-category status here — promotion only moves an issue to `ready_status` or `refinement_status`.

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
