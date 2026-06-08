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

5. **Resolve `default_epic` against real epics — do not accept free-text.** This field is optional but, when set, must be a valid epic key in the chosen project (otherwise it silently breaks `/add-task`, which uses it to skip the per-task epic prompt).

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

6. **Optional `labels`** — ask the user as plain text (comma-separated list); skip if blank. Do not use `AskUserQuestion` here.

7. **Return the config block** to `/task-config`:

   ```yaml
   handler: jira
   jira:
     site: mycompany.atlassian.net
     project: PLAT
     issue_type: Task
     default_epic: PLAT-100
     labels: []
     ready_status: To Do
     refinement_status: Needs Refinement
   ```

   Omit any optional key the user didn't set.

## Promote keys (`ready_status` / `refinement_status`)

`/promote-tasks` (the `jira-promote.md` flow) transitions scored issues to configured statuses, so it needs two extra keys:

- **`ready_status`** — the target status a HIGH-confidence issue is transitioned to (the jira analogue of Linear's `Todo`/`auto-eligible`).
- **`refinement_status`** — the target status a LOW-confidence (underspecified) issue is transitioned to (the jira analogue of `needs_refinement`/`human-approval-requested`).

Both are **transition targets passed verbatim** to `transitionJiraIssue` — set each to the name of an available workflow transition (or its target status) in your project. This slice **requires** both keys when `/promote-tasks` runs under the `jira` handler; if either is unset, the promote flow stops with a clear message. Auto-resolving a status name to its transition (no configured value needed) and the prompt-when-unset path are deferred to a sibling slice. The keys are optional in `/add-task`-only setups — they only matter for promote.
