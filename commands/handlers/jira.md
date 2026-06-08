# Handler: jira

Creates a Jira work item via the Atlassian MCP server (`mcp__claude_ai_Atlassian__*`). Foreground call, no git plumbing, no CLI install. The new ticket is placed under a selected epic.

> **Required interaction:** step 2 (epic selection) MUST prompt the user via `AskUserQuestion` unless `jira.default_epic` is set in config. This applies in auto mode too. Treat a missing or empty `jira.default_epic` (including `null`, `""`, or the key being absent from the config block) as "not set" — you MUST prompt. If you find yourself about to call `createJiraIssue` without having asked AND without a non-empty `jira.default_epic`, stop and go back to step 2.

Config block in `dev_docs/tasks/.task-config.yml`:

```yaml
handler: jira
jira:
  site: mycompany.atlassian.net # used as cloudId and to build the browse URL
  project: PLAT # required — project key
  issue_type: Task # default Task
  default_epic: PLAT-100 # optional; skips the epic prompt (explicit key, not a name)
  labels: [] # optional — passed via additional_fields.labels
  ready_status: Selected for Development # used by /promote-tasks — target status for HIGH-confidence promotions (must differ from the initial/new status)
  refinement_status: Needs Refinement # used by /promote-tasks — target status for LOW-confidence (underspecified) issues
```

The `ready_status` / `refinement_status` keys are consumed only by the promote flow (`jira-promote.md`); `/add-task` ignores them.

`site` is passed directly as `cloudId` to the MCP tools (they accept either a UUID or a site URL/hostname).

## Steps

1. **Preflight.** Confirm the Atlassian MCP is reachable and the configured site is accessible:

   Call `mcp__claude_ai_Atlassian__getAccessibleAtlassianResources` (no args).
   - If the tool errors or returns no resources, **stop** with: "Jira handler needs the Atlassian MCP. Install/connect it in Claude Code settings, then re-run." Do not fall back to another handler.
   - If the response does not include a resource whose `url` matches `https://<jira.site>`, **stop** with: "Configured Jira site `<site>` is not in your accessible Atlassian resources." (List the URLs that were returned.)

2. **Select the epic. HARD STOP — DO NOT SKIP.** You MUST ask the user which epic to attach the ticket to before creating it, using `AskUserQuestion`. Do not infer the epic from the title, the project, or recent activity. Do not proceed to step 3 until the user has answered the `AskUserQuestion` call in this step. The ONLY way to skip this prompt is if `jira.default_epic` is set in the config file (then use that key as-is and proceed).

   Fetch the project's open epics:

   Call `mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql` with:
   - `cloudId`: `<jira.site>`
   - `jql`: `project = "<project>" AND issuetype = Epic AND statusCategory != Done ORDER BY updated DESC`
   - `fields`: `["summary", "status"]`
   - `maxResults`: 50

   The response wraps issues as `issues.nodes[]`; each node has `key`, `fields.summary`, `fields.status.name`, and a ready-made `webUrl`.

   Present the epics to the user via `AskUserQuestion` (header: "Jira epic"). Each epic is an option labeled `<KEY> — <summary>`. Include a final "No epic (top-level ticket)" option so the user can opt out explicitly. `AskUserQuestion` enforces a 4-option max — show at most 2 epic options (the 2 most recently updated) so 2 epics + "No epic" + "Other" fits. The user can pick "Other" to type a specific key. Capture the chosen `<EPIC-KEY>` (or `none`).

3. **Compose the description.** Use the drafted task's `body` plus a source footer (omit empty lines):

   ```
   <body>

   ---
   Source branch: <source_branch>
   Source PR: #<source_pr>
   Blocked by task: <is_blocked_by>
   ```

4. **Create the work item.** Call `mcp__claude_ai_Atlassian__createJiraIssue` with:
   - `cloudId`: `<jira.site>`
   - `projectKey`: `<project>`
   - `issueTypeName`: `<issue_type>` (default `Task`)
   - `summary`: the drafted `title`
   - `description`: the composed description from step 3
   - `contentFormat`: `"markdown"`
   - `parent`: the chosen `<EPIC-KEY>` (omit entirely if the user picked "No epic")
   - `additional_fields`: `{ "labels": <jira.labels list> }` (omit if no labels configured)

5. **Return the URL.** The response wraps the new issue as `issues.nodes[0]`. Return `issues.nodes[0].webUrl` directly as this handler's artifact URL for `/add-task` step 8. (Fallback: build `https://<jira.site>/browse/<issues.nodes[0].key>` if `webUrl` is missing.)

This handler does **not** create any `dev_docs/tasks/*.md` file, branch, or PR.

## List

Invoked from `/list-tasks` when `handler: jira` is configured. Read-only — one `searchJiraIssuesUsingJql` query, no edits, no claims. Renders the configured project's issues as the same vertical-section kanban the file-based path uses, so `$ARGUMENTS` and the layout match `commands/list-tasks.md` step 4.

> **Coverage note.** `jira` is a capture-only handler in this loop — there is no jira promote/claim/execute flow that sets status labels — so in practice most issues land in `new` (status category To Do) or `done` (status category Done). The status-label mapping below is honored when those labels happen to be present (e.g. set by hand or a board automation), but a plain jira setup renders a two-section `new`/`done` board, which is the expected v1 behavior — exactly like the gh-issue `## List` path.

1. **Preflight.** Reuse the create flow's step 1 preflight: call `mcp__claude_ai_Atlassian__getAccessibleAtlassianResources` (no args) and confirm a resource whose `url` matches `https://<jira.site>`. On either failure, **stop** with the same messages ("Jira handler needs the Atlassian MCP…" / "Configured Jira site `<site>` is not in your accessible Atlassian resources."). Do not fall back to another handler.

2. **Query issues.** Call `mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql` with:
   - `cloudId`: `<jira.site>`
   - `jql`: `project = "<project>"`, plus one `AND labels = "<label>"` clause per entry in `jira.labels` when it is non-empty (AND-joined, so the board shows only issues carrying **all** configured labels — matching the gh-issue handler's per-`--label` AND filter; `labels in (...)` would be OR), then `ORDER BY created DESC`
   - `fields`: `["summary", "status", "assignee", "priority", "labels", "created"]`
   - `maxResults`: 50

   The response is an `issues[]` array — each element has `key`, `fields.summary`, `fields.status.statusCategory.key` (`new` / `indeterminate` / `done`), `fields.assignee.displayName` (or `null` when unassigned), `fields.priority.name`, `fields.labels[]` (plain strings), and `fields.created`. Some Atlassian MCP servers wrap the array as `issues.nodes[]` instead (the create flow above reads `issues.nodes[0]`); read whichever key the server returns.

3. **Group into kanban sections.** Classify each issue by its `statusCategory.key` (the coarse split, like gh-issue's open/closed `state`) plus label presence. Reuse the same status-label vocabulary as the gh-issue `## List` and the Linear mapping in `linear-common.md` so a board behaves consistently across trackers:

   | Section            | Match rule                                                                                     |
   | ------------------ | ---------------------------------------------------------------------------------------------- |
   | `new`              | statusCategory `new`, none of the status labels below present                                  |
   | `needs_refinement` | statusCategory `new`, has `human-approval-requested`                                           |
   | `ready`            | statusCategory `new`, has `auto-eligible`                                                      |
   | `in_progress`      | statusCategory `indeterminate`, no `blocked` or `needs-review` (the default for indeterminate) |
   | `blocked`          | statusCategory `indeterminate`, has `blocked`                                                  |
   | `needs_review`     | statusCategory `indeterminate`, has `needs-review`                                             |
   | `done`             | statusCategory `done` — select the 10 most recent by `created`, then sort per step 4           |

`in_progress` is the default for any `indeterminate` issue (work is underway by definition); the `blocked` and `needs-review` labels override it into their respective sections. Unlike gh-issue's binary open/closed `state`, Jira's `statusCategory` distinguishes To Do from In Progress natively, so no claim label is needed to land in `in_progress`. If an issue matches more than one rule, prefer the more actionable signal in this order: `blocked` > `needs_review` > `in_progress` > `ready` > `needs_refinement`.

4. **Render** as stacked vertical sections in the fixed order `new → needs_refinement → ready → in_progress → blocked → needs_review → done`, using the same `## <section> (N)` header, single-line bullet, and `---` separator layout as `commands/list-tasks.md` step 4 (don't re-specify it). Card line:

   ```
   - [high] PLAT-142 Fix broken import — assignee dan
   ```

   Field mapping (vs. the `repo-pr` card line, which uses slug + frontmatter):

   | Field       | Source                                                                                                                                                                                                                                                                                                                 |
   | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | Priority    | `fields.priority.name` mapped to `urgent`/`high`/`medium`/`low`. Default scheme: Highest→urgent, High→high, Medium→medium, Low/Lowest→low. `P`-scheme (custom, common): P1→urgent, P2→high, P3→medium, P4→low (match the leading `P<n>`, e.g. `P4-Trivial`). Any other custom name, or no priority, → `[—]`, sort last |
   | Identifier  | issue `key`                                                                                                                                                                                                                                                                                                            |
   | Title       | `fields.summary`                                                                                                                                                                                                                                                                                                       |
   | Assignee    | `fields.assignee.displayName` (omit `— assignee …` when unassigned)                                                                                                                                                                                                                                                    |
   | Annotations | `human-approval-requested`, `blocked`, `needs-review` — bare label name when present, comma-separated and appended after the assignee with `—`                                                                                                                                                                         |

   Sort within each section by priority (`urgent > high > medium > low`, none last), then `created` (oldest first).

5. **Summary line.** Same shape as the file-based path:

   ```
   8 issues (1 new, 1 needs_refinement, 2 ready, 1 in_progress, 0 blocked, 2 needs_review, 1 done)
   ```

6. **Filter argument.** If `$ARGUMENTS` is a section name (`new|needs_refinement|ready|in_progress|blocked|needs_review|done|all`), render only that section. Default: every non-empty section.

7. **Empty board.** If the query returns no issues, report `No tasks found in <project>.` rather than erroring.

## Link

Invoked from `/push-plan` (§5b) to translate a task's `is_blocked_by` into a native Jira **issue link** — the capability the create flow above lacks (`createJiraIssue` has no link parameter). Each call adds one "blocks / is blocked by" edge between two existing issues, so it runs in a **second pass** after every issue in the batch exists and its key is known.

> **MCP namespace.** `<atlassian-mcp>__` is `mcp__claude_ai_Atlassian__` or `mcp__atlassian__` depending on the install (see `jira-config.md`) — substitute the prefix loaded in your session.

1. **Resolve the link type** into `<blocks-link-type>`. The blocker relationship is Jira's built-in `Blocks` type (`inward: "is blocked by"`, `outward: "blocks"`), so default `<blocks-link-type>` to `Blocks`. If a site has renamed or removed it, call `<atlassian-mcp>__getIssueLinkTypes` (`cloudId: <jira.site>`) and set `<blocks-link-type>` to the `name` of the type whose `inward` is `is blocked by`; if none exists, **skip linking** and note it in the report (the board still has the parent/child epic structure, just no blocker edges). Use `<blocks-link-type>` — not the literal `Blocks` — in steps 2 and 3 so a renamed type still links.

2. **Create the link.** For "**A is blocked by B**" (A's `is_blocked_by` names B), call `<atlassian-mcp>__createIssueLink` with:
   - `cloudId`: `<jira.site>`
   - `type`: `<blocks-link-type>`
   - `inwardIssue`: `<B>` — the **blocker** (the issue that blocks)
   - `outwardIssue`: `<A>` — the **blocked** issue

   Direction matters: `inwardIssue` is the blocker, `outwardIssue` is the dependent. Reversing them records the relationship backwards. A task with multiple blockers gets one `createIssueLink` call per blocker.

3. **Idempotency (create-missing-only).** Before linking, read A's existing links — `<atlassian-mcp>__getJiraIssue` (`cloudId`, `issueIdOrKey: <A>`, `fields: ["issuelinks"]`) — and **skip** if an `issuelinks[]` entry already has `type.name == <blocks-link-type>` with `inwardIssue.key == <B>`. This keeps a re-pushed plan from stacking duplicate "is blocked by" edges, matching the create-missing-only contract in `/push-plan` §6.
