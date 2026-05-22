---
description: Configure where /add-todo delivers todos (repo PR, GitHub issue, or Jira)
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(mkdir *), Read, Write, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__getVisibleJiraProjects, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql
argument-hint: [repo-pr | gh-issue | jira]
---

# Todo Config

Set up the destination handler for `/add-todo` in this repo. Writes `dev_docs/todos/.todo-config.yml`, which `/add-todo` reads to decide where a captured todo lands. The file is **repo-committed and shared by the team** — everyone in the repo files to the same destination.

See the handler files in `commands/handlers/<handler>.md` for what each destination does.

## Steps

### 1. Show current config

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/todos/.todo-config.yml" 2>/dev/null
```

If it exists, show the user what's currently configured so they see what they're changing. If not, say there's no config yet (so `/add-todo` currently defaults to `repo-pr`).

### 2. Choose the handler

If `$ARGUMENTS` names a handler (`repo-pr`, `gh-issue`, or `jira`), use it. Otherwise ask which destination they want:

- **`repo-pr`** — commit the todo as a markdown file via PR (the default; works with `/process-todo`)
- **`gh-issue`** — create a GitHub Issue
- **`jira`** — create a Jira work item under an epic

### 3. Collect settings + verify prerequisites

Per handler. **Verify before writing — don't write a config that can't deliver.**

#### repo-pr

No prerequisites. Mention the optional auto-merge workflow (see `README.md`) for landing `todo-add` PRs automatically. Proceed to write.

#### gh-issue

1. Verify auth: `gh auth status 2>&1`. If it fails:
   - TLS/x509/certificate error → likely the sandbox blocking keychain access; tell the user to re-run outside sandbox mode.
   - Otherwise → ask the user to authenticate. `gh auth login` is interactive: have them run it via the session prefix, e.g. `! gh auth login`, then continue.
   - Do not write the config until `gh auth status` succeeds.
2. Prompt for the following optional settings:
   - `repo` — default the current repo (`gh repo view --json nameWithOwner --jq .nameWithOwner`)
   - `labels` — list, e.g. `[follow-up]`
   - `assignees` — list of GitHub usernames

#### jira

The `jira` handler delivers via the Atlassian MCP server (`mcp__claude_ai_Atlassian__*`) — no CLI to install.

1. Call `mcp__claude_ai_Atlassian__getAccessibleAtlassianResources` (no args) to discover accessible sites.
   - If the tool errors or returns no resources, **stop** with: "Jira handler needs the Atlassian MCP. Install/connect it in Claude Code settings, then re-run `/todo-config jira`." Do not write the config.
2. Resolve `site` from the response (extract hostname from each resource's `url`):
   - Exactly one resource → use that site directly; tell the user which one you picked.
   - Multiple resources → ask the user which site to use via `AskUserQuestion` (one option per resource).
   Never prompt the user to type a site that doesn't appear in the accessible-resources list.
3. Resolve `project` against visible projects. Call `mcp__claude_ai_Atlassian__getVisibleJiraProjects` with `cloudId: <site>`.
   - Exactly one project → use it; tell the user which one you picked.
   - Multiple projects → ask via `AskUserQuestion` (one option per project, labeled `<KEY> — <name>`; cap at 4, "Other" lets the user type a key, which you then re-validate against the visible-projects list).
   - Never prompt the user to type a project key blind.
4. Ask for `issue_type` via `AskUserQuestion` (header: "Issue type") with options `Task` (recommended, first), `Story`, `Bug`. Use "Other" for anything else. Default is `Task` if the user skips.
5. **Resolve `default_epic` against real epics — do not accept free-text.** This field is optional but, when set, must be a valid epic key in the chosen project (otherwise it silently breaks `/add-todo`, which uses it to skip the per-todo epic prompt).

   Call `mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql` with:
   - `cloudId`: `<site>`
   - `jql`: `project = "<project>" AND issuetype = Epic AND statusCategory != Done ORDER BY updated DESC`
   - `fields`: `["summary"]`
   - `maxResults`: 50

   Present the result via `AskUserQuestion` (header: "Default epic"):
   - One option per epic, labeled `<KEY> — <summary>` (cap at 3 epic options so "None" and "Other" fit within the 4-option limit; show the 3 most recently updated).
   - A `"None — prompt me per-todo"` option (this is the recommended default if the user is unsure).
   - "Other" lets the user type a specific key; if they pick "Other", validate the typed value by calling `searchJiraIssuesUsingJql` again with `jql: project = "<project>" AND key = "<TYPED>" AND issuetype = Epic`. If the result is empty, push back ("`<TYPED>` is not an epic key in `<project>`") and re-ask. Do not write the config with an unvalidated key.

   If the user picks "None — prompt me per-todo", omit `default_epic` from the written config. Otherwise write the validated key as-is.

6. Optional `labels` — ask the user as plain text (comma-separated list); skip if blank. Do not use `AskUserQuestion` here.

> **AskUserQuestion rule:** every call needs ≥2 options. If a step would only have one (e.g. a single visible project or site), use it directly and tell the user — don't try to ask.

> **Interactive auth caveat:** `gh auth login` is interactive — never run it headless from inside this command. Always have the user run it with the `!` session prefix, then continue once they report success.

### 4. Write the config

Ensure the directory exists and write the file:

```bash
mkdir -p "$(git rev-parse --show-toplevel)/dev_docs/todos"
```

Write `dev_docs/todos/.todo-config.yml` with the chosen handler and its block. Examples:

```yaml
# repo-pr
handler: repo-pr
```

```yaml
# gh-issue
handler: gh-issue
gh-issue:
  repo: owner/name
  labels: [follow-up]
  assignees: []
```

```yaml
# jira
handler: jira
jira:
  site: mycompany.atlassian.net
  project: PLAT
  issue_type: Task
  default_epic: PLAT-100
  labels: []
```

Omit optional keys the user didn't set.

### 5. Confirm

Tell the user:
- Which handler is now configured and where the file lives.
- That the file is repo-committed and shared — they should **commit it** so teammates pick up the same destination.
- They can now run `/add-todo`, or re-run `/todo-config` to switch handlers.
