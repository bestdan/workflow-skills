# jira handler — /task-config setup

Configures the `jira` handler, which creates Jira work items via the Atlassian MCP server at `/add-task` time. This file owns the Atlassian MCP preflight and the site/project/issue_type/default_epic/labels prompts; the actual create flow lives in `jira.md`.

> **Atlassian MCP tool namespace.** Like Linear, the Atlassian MCP can be installed via `claude.ai` settings (tools prefixed `<atlassian-mcp>__`) or via `claude mcp add` (tools prefixed `mcp__<server-name>__`, where `<server-name>` is the name passed at install — `mcp-setup-offer.md` uses `atlassian`, so `mcp__atlassian__`). Use whichever prefix is loaded. Tool names after the prefix (`getAccessibleAtlassianResources`, `getVisibleJiraProjects`, `searchJiraIssuesUsingJql`, `createJiraIssue`) are identical. This file writes tool names as `<atlassian-mcp>__getAccessibleAtlassianResources`, etc.

## Steps

1. **Atlassian MCP preflight.** Call `<atlassian-mcp>__getAccessibleAtlassianResources` (no args) to discover accessible sites.
   - If the tool isn't available at all (the `<atlassian-mcp>__*` namespace isn't loaded), the Atlassian MCP isn't connected yet. **Dispatch to `mcp-setup-offer.md`** with:
     - `server`: `atlassian`
     - `add-command`: `! claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp/authv2`
     - `handler`: `jira`

     The setup offer stops the run; the user restarts Claude Code and re-invokes `/task-config jira` from a fresh session.
   - If the tool is available but returns no resources, **stop** with: "Atlassian MCP is connected but no sites are accessible. Authenticate via `/mcp` (pick `atlassian`), then re-run `/task-config jira`." Do not write the config.

2. **Resolve `site`** from the response (extract hostname from each resource's `url`):
   - Exactly one resource → use that site directly; tell the user which one you picked.
   - Multiple resources → ask the user which site to use via `AskUserQuestion` (one option per resource).

   Never prompt the user to type a site that doesn't appear in the accessible-resources list.

3. **Resolve `project`** against visible projects. Call `<atlassian-mcp>__getVisibleJiraProjects` with `cloudId: <site>`.
   - Exactly one project → use it; tell the user which one you picked.
   - Multiple projects → ask via `AskUserQuestion` (one option per project, labeled `<KEY> — <name>`; cap at 3 so 3 projects + "Other" fits the 4-option max. "Other" lets the user type a key, which you then re-validate against the visible-projects list).

   Never prompt the user to type a project key blind.

4. **Ask for `issue_type`** via `AskUserQuestion` (header: "Issue type") with options `Task` (recommended, first), `Story`, `Bug`. Use "Other" for anything else. Default is `Task` if the user skips.

5. **Resolve `default_epic` against real epics — do not accept free-text.** This field is optional but, when set, must be a valid epic key in the chosen project (otherwise it silently breaks `/add-task`, which uses it to skip the per-task epic prompt). When set it also **pins** `/promote-tasks` to that epic's children; when unset the promote flow detects one open epic to scope to (`jira-promote.md` step 2a).

   Call `<atlassian-mcp>__searchJiraIssuesUsingJql` with:
   - `cloudId`: `<site>`
   - `jql`: `project = "<project>" AND issuetype = Epic AND statusCategory != Done ORDER BY updated DESC`
   - `fields`: `["summary"]`
   - `maxResults`: 50

   Present the result via `AskUserQuestion` (header: "Default epic"):
   - One option per epic, labeled `<KEY> — <summary>` (cap at 2 epic options so 2 epics + "None" + "Other" fits the 4-option max; show the 2 most recently updated).
   - A `"None — prompt me per-task"` option (this is the recommended default if the user is unsure).
   - "Other" lets the user type a specific key; if they pick "Other", validate the typed value by calling `searchJiraIssuesUsingJql` again with `jql: project = "<project>" AND key = "<TYPED>" AND issuetype = Epic`. If the result is empty, push back ("`<TYPED>` is not an epic key in `<project>`") and re-ask. Do not write the config with an unvalidated key.

   If the user picks "None — prompt me per-task", omit `default_epic` from the written config. Otherwise write the validated key as-is.

6. **Optional `labels`** — ask the user as plain text (comma-separated list); skip if blank. Do not use `AskUserQuestion` here. Do not prompt for `additional_fields` — it needs site-specific custom-field ids the user is unlikely to have to hand; point them at the "Team and other custom fields" section below instead.

7. **Return the config block** to `/task-config`:

   ```yaml
   handler: jira
   jira:
     site: mycompany.atlassian.net
     project: PLAT
     issue_type: Task
     default_epic: PLAT-100
     labels: []
     # additional_fields: {}      # optional — extra createJiraIssue fields (Team, components, custom fields)
     blocked_statuses: []
     ready_status: Selected for Development
     refinement_status: Needs Refinement
     # archive_status: Archived   # optional — /archive-tasks target (see "Archive status")
   # archive_after: 30            # optional, top-level — default /archive-tasks age threshold (days)
   ```

   Omit any optional key the user didn't set.

## Promote keys (`ready_status` / `refinement_status`)

`/promote-tasks` (the `jira-promote.md` flow) transitions scored issues to configured statuses, so it needs two extra keys:

- **`ready_status`** — the target status a HIGH-confidence issue is transitioned to (the jira analogue of Linear's `Todo`/`auto-eligible`). **`/add-task` reads this key too** — see "Landing status at capture" below.
- **`refinement_status`** — the target status a LOW-confidence (underspecified) issue is transitioned to (the jira analogue of `needs_refinement`/`human-approval-requested`).

Both are **target status names** — set each to the name of the status you want the issue moved to in your project. The promote flow resolves each name to the matching workflow transition id per issue at apply time (`transitionJiraIssue` takes a transition id, not a status name), so the name just has to match a status an available transition leads to. Each **must differ from the project's initial/new status** (the status new issues enter): the candidate query excludes issues already in `ready_status`/`refinement_status`, so pointing either at the initial status would filter out every new issue and promote nothing. Both keys are **optional**. When a key is unset, `/promote-tasks` (the `jira-promote.md` flow) resolves the project's reachable statuses dynamically from a candidate's available transitions and prompts you to pick via `AskUserQuestion`, then offers to persist the choice back here so the prompt does not recur — no status id or name is ever hard-coded. `refinement_status` is unused in an `/add-task`-only setup; `ready_status` is not — the create flow reads it as well.

## Landing status at capture

Jira has no "create in status X" — `createJiraIssue` always drops the issue in the project's **initial** status, whatever the board calls it. On a board whose initial status is a refinement lane (`Needs Refinement`, `Triage`, `Inbox`), that means a fully-specified task captured by `/add-task` lands as if nobody had written it yet, and stays there until someone runs `/promote-tasks`.

So when `ready_status` is set, `commands/handlers/jira.md` **step 5** transitions the new issue to it — but only when the captured task is genuinely ready: no `is_blocked_by` entry, and the deterministic half of the confidence check passes (title, body, priority, a valid `size`, a non-empty Acceptance Criteria section). Anything else is left in the initial status for `/promote-tasks` to score, and so is every issue when `ready_status` is unset. A create that lands but cannot transition is reported, never rolled back.

`/push-plan`'s jira path inherits this, so a pushed plan's complete, unblocked tasks land ready and the rest do not.

**Watch the initial status.** The rule that `ready_status` and `refinement_status` must each differ from the project's initial status bites hardest on a board whose initial status is literally named `Needs Refinement`: setting `refinement_status` to that same name empties the promote candidate query (it filters out every issue already in the configured statuses), so `/promote-tasks` finds nothing to score. Leave `refinement_status` unset on such a board — a LOW-confidence issue is already sitting where it belongs — and set only `ready_status`.

## Archive status (`archive_status`)

`archive_status` is an **optional** status **name** used only by `/archive-tasks`. **Native Jira issue archival is a Jira Premium feature** and is not reachable through the Atlassian MCP, so this handler instead transitions terminal (`statusCategory = Done`) issues older than the threshold to a status you've modeled as "archived" — typically an extra status in the `Done` category that your board's default filter hides. Set it to that status's name (e.g. `Archived`). The archive flow resolves the name to a per-issue transition id the same way the promote keys do (`transitionJiraIssue` takes a transition id, not a name).

**When `archive_status` is unset, `/archive-tasks` is a no-op** for jira — it reports that there's nothing to do rather than guessing. The shared top-level **`archive_after`** (days) is the default age threshold when `/archive-tasks` is run without `--older-than`. See `commands/handlers/jira-archive.md`.

## Team and other custom fields (`additional_fields`)

`additional_fields` is an **optional** mapping passed verbatim to `createJiraIssue` (merged with `labels`), for board fields this plugin does not model: the Advanced Roadmaps **Team** field, `components`, `fixVersions`, or any custom field. There is no dedicated `team:` key because Jira exposes Team as a **site-specific custom-field id** (`customfield_NNNNN`) that differs between sites, so a named key would hard-code one org's schema.

Resolve the id for a field with `<atlassian-mcp>__getJiraIssueTypeMetaWithFields` (`cloudId: <site>`, the chosen project and issue type) — the response names every field the create screen accepts, and gives each one's `schema.type`, which is what decides the value shape below. Then write the ids as a plain mapping:

```yaml
jira:
  additional_fields:
    customfield_10001: "<team-id>" # Team — the id, never the display name
    components: [{ name: "Billing" }]
    fixVersions: [{ name: "2026.09" }]
```

**Resolving an id is only half the job — the value shape is the other half, and getting it wrong looks exactly like getting the id wrong.** Values are passed through untouched, so each has to match what its field expects, and the shapes differ by `schema.type`:

- A **`team`** field (Advanced Roadmaps / Atlassian Teams, `schema.custom` ending `:atlassian-team`) takes the team's **id**, not its name. Passing the display name is rejected with `Cannot assign a non-existing team.` — a message that reads like the team is missing when the real problem is that a name was sent where an id belongs. Verified against a live board.
- **`components`** and **`fixVersions`** are arrays of `{ "name": … }` objects.
- Most other custom fields take a plain string, a number, or an `{ "id": … }` object.

When a value is rejected, read the error's own `expectedShape` and `currentValue` — Jira returns both, and together they say whether the id or the shape is wrong. If `createJiraIssue` rejects a field, the create flow stops and reports it rather than silently creating the issue without it.

## Blocked statuses (`blocked_statuses`)

`blocked_statuses` is an **optional** list of status **names** that mean "blocked" on your board (e.g. `["Blocked", "On Hold/Blocked"]`). Two flows read it:

- **`/promote-tasks`** (the `jira-promote.md` flow) excludes them from its candidate query so a blocked issue isn't scored as fresh backlog and transitioned to `ready_status`.
- **`/do-tasks`** (the `jira-claim.md` pre-claim WIP gate) excludes them from its in-flight count so a parked issue doesn't consume a WIP slot.

Both need it for the same reason: a board can place a `Blocked`-type status in **any** `statusCategory` — the **initial / To-Do** category, where it is indistinguishable from new work to the promoter's filter, or the **`indeterminate` / In Progress** category, where it is indistinguishable from real work to the gate's filter. The same status name sits in a different category on another board, so it can't be hard-coded. Leave it empty or omit it if your board has no such status. (Issues flagged with Jira's native **Flagged**/Impediment field are excluded automatically by both flows, with no config — `blocked_statuses` is only for boards that encode blocked-ness as a _status_.)
