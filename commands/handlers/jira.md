# Handler: jira

Creates a Jira work item via the Atlassian MCP server (`mcp__claude_ai_Atlassian__*`). Foreground call, no git plumbing, no CLI install. The new ticket is placed under a selected epic.

> **Required interaction:** step 2 (epic selection) MUST prompt the user via `AskUserQuestion` unless `jira.default_epic` is set in config. This applies in auto mode too. Treat a missing or empty `jira.default_epic` (including `null`, `""`, or the key being absent from the config block) as "not set" — you MUST prompt. If you find yourself about to call `createJiraIssue` without having asked AND without a non-empty `jira.default_epic`, stop and go back to step 2.

Config block in `dev_docs/todos/.todo-config.yml`:

```yaml
handler: jira
jira:
  site: mycompany.atlassian.net   # used as cloudId and to build the browse URL
  project: PLAT                   # required — project key
  issue_type: Task                # default Task
  default_epic: PLAT-100          # optional; skips the epic prompt (explicit key, not a name)
  labels: []                      # optional — passed via additional_fields.labels
```

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

   Present the epics to the user via `AskUserQuestion` (header: "Jira epic"). Each epic is an option labeled `<KEY> — <summary>`. Include a final "No epic (top-level ticket)" option so the user can opt out explicitly. If there are more than 4 epics, show the 4 most recently updated as options — the user can pick "Other" to type a specific key. Capture the chosen `<EPIC-KEY>` (or `none`).

3. **Compose the description.** Use the drafted todo's `body` plus a source footer (omit empty lines):

   ```
   <body>

   ---
   Source branch: <source_branch>
   Source PR: #<source_pr>
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

5. **Return the URL.** The response wraps the new issue as `issues.nodes[0]`. Return `issues.nodes[0].webUrl` directly as this handler's artifact URL for `/add-todo` step 8. (Fallback: build `https://<jira.site>/browse/<issues.nodes[0].key>` if `webUrl` is missing.)

This handler does **not** create any `dev_docs/todos/*.md` file, branch, or PR.
