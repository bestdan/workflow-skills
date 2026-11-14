---
description: Handler procedure read by its dispatching command; not run directly.
disable-model-invocation: true
user-invocable: false
---

# jira handler — the twg backend

The jira handler files name each Jira call as an Atlassian MCP tool
(`<atlassian-mcp>__<tool>`). When `jira-config.md` → "Backend" resolves to
`twg`, make each of those calls with the `twg` command in the table below
instead, and read its response through the shape notes after it. Everything
else in a handler — its JQL, its gates, its order, its stop messages — is
unchanged. `twg` is Atlassian's Teamwork Graph CLI.

**Every call takes the same frame.** Run it **unsandboxed**: it needs
`api.atlassian.com`, and a sandboxed call fails as a proxy or auth error that
says nothing about the login. Spell it

```
twg --mode agent --site <jira.site> <command> -o json --output-summary none
```

`--mode agent` is the CLI's agent opt-in; it is used in place of the
`TWG_AGENT_DEFAULTS=1` environment prefix so the call matches a
`Bash(twg *)` permission rule. `--output-summary none` puts the whole response
on stdout instead of a summary envelope that points at a file.

## Calls

| MCP tool                           | twg command                                                                                                                                                          |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `getAccessibleAtlassianResources`  | `whoami` — the preflight; see "Backend" in `jira-config.md`                                                                                                          |
| `atlassianUserInfo`                | `whoami` → `data.accountId`                                                                                                                                          |
| `searchJiraIssuesUsingJql`         | `jira workitem query --jql '<jql>' --fields <f1,f2> --limit <maxResults>`                                                                                            |
| `getJiraIssue`                     | `jira workitem get <KEY> --fields <f1,f2>`; add `--comments` when the step reads comments                                                                            |
| `createJiraIssue`                  | `jira workitem create --space <project> --type <issue_type> --summary '<title>' --description "$(cat <file>)" --description-format markdown`, plus the options below |
| `editJiraIssue` (assignee)         | `jira workitem update --id <KEY> --assignee <accountId>`; `--assignee none` unassigns                                                                                |
| `editJiraIssue` (other fields)     | `jira workitem update --id <KEY> --fields-json '<fields object>'`                                                                                                    |
| `getTransitionsForJiraIssue`       | `jira workitem transitions query --id <KEY>`                                                                                                                         |
| `transitionJiraIssue`              | `jira workitem transition --id <KEY> --transition-id <id>`                                                                                                           |
| `addCommentToJiraIssue`            | `jira workitem comment create --issue-id <KEY> --body "$(cat <file>)" --body-format markdown`                                                                        |
| `getIssueLinkTypes`                | `jira workitem link-types query`                                                                                                                                     |
| `createIssueLink`                  | `jira workitem link workitem --id <inwardIssue> --target-id <outwardIssue> --link-type-id <type name>`                                                               |
| `getVisibleJiraProjects`           | `jira space query --limit 50`                                                                                                                                        |
| `getJiraProjectIssueTypesMetadata` | `jira workitem types query --project <project>`                                                                                                                      |
| `getJiraIssueTypeMetaWithFields`   | `jira workitem field create-metadata --space <project> --type <issue_type>`                                                                                          |

**Create options.** `parent` → `--parent <EPIC-KEY>`. `labels` →
`--labels a,b`. The rest of `additional_fields` → `--fields-json '<mapping>'`,
passed through verbatim exactly as the MCP path passes it, so the shape rules in
`jira-config.md` → "Team and other custom fields" apply unchanged. Write a
description or comment body to a file under `/tmp/claude/` first and pass it
with `"$(cat …)"`: an inline multi-line body is a quoting hazard.

**Link direction is the MCP's.** `--id` is sent as `inwardIssue` and
`--target-id` as `outwardIssue` (read from the CLI's own request code, twg
1.4.4), so a step that says `inwardIssue: <B>`, `outwardIssue: <A>` is
`--id <B> --target-id <A>`.

## Reading the responses

twg returns Jira's REST objects inside an envelope, with each issue's `fields`
**flattened onto the issue**. Where a handler step reads `fields.<name>`, read
`<name>` on the issue itself: `fields.status.statusCategory.key` is
`status.statusCategory.key`, `fields.assignee.accountId` is
`assignee.accountId`, `fields.issuelinks` is `issuelinks`. Field values are the
REST shapes the MCP returns, so nothing below the top level differs.

- **Search** → `data.issues[]`. Each issue also carries a ready-made `url`, the
  counterpart of the MCP's `webUrl`.
- **Get** → with one key, `data[]` holding that issue. With several keys,
  `data.items[]`, one `{input, ok, data}` or `{input, ok: false, error}` per
  key, and the call exits **3** when any key failed, with the rest still
  printed. With `--comments`, each issue has a complete `comments[]` list
  (bodies are ADF) beside a `comment` page that can come back empty; read
  `comments[]`.
- **Create** → `data.issue.key`. There is no URL in the response, so build
  `https://<jira.site>/browse/<key>`.
- **Transitions** → `data.transitions[]`, each with `id`, `name`, `toName` and
  the target category's **name** (`To Do` / `In Progress` / `Done`) rather than
  its key. Pipe the response to `jira-resolve-transition.py` with `--twg` added
  to the step's other arguments; the script maps the names onto the keys its
  `--category` takes, and stops on a name it does not know rather than guess.
- **Projects** → `data.projects[]` (`key`, `name`). **Issue types** → `data[]`
  (`id`, `name`, `hierarchyLevel`). **Create metadata** → `data.fields[]`, each
  with `id`, `name`, `required` and `schema`.
- **A failed call** exits non-zero with the error on stderr. Report it the way
  the step reports the MCP tool's error.

The create, transition, comment, link and update shapes come from the CLI's
code and its help; the reads above were checked against a live site
(2026-11-14). The first write a flow makes over twg on a new site is the place
to confirm them.
