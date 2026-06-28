# jira handler — /archive-tasks flow

Invoked from `/archive-tasks` when `handler: jira` is configured. Transitions
terminal Jira issues older than the threshold to an **archived status** where the
project defines one; otherwise it is a documented no-op.

> **Native Jira issue archival is a Jira Premium feature** and is not reachable
> through the Atlassian MCP's transition tools. So this handler does **not** call
> a true archive API. Where a project has modeled "archived" as an extra
> **workflow status** in a terminal category, this moves qualifying issues there
> (dropping them out of the default board view). Where it hasn't, the retire op
> is a **no-op** — report that and stop. Either way, completion state is
> unchanged: an issue only becomes a candidate once it is already terminal.

**Shared reference:** see `commands/handlers/jira.md` step 1 for the Atlassian MCP
preflight and `commands/handlers/jira-config.md` for the config keys.

## What counts as terminal here

A Jira issue is terminal when its `statusCategory` is `Done` (covers both the
completed and canceled equivalents — Jira models cancellation as a `Done`-category
status, e.g. `Won't Do`). Candidates are `Done`-category issues whose resolution
date is older than the threshold. Never touch issues in the `To Do` or
`In Progress` categories.

## Steps

1. **Preflight.** Run the Atlassian MCP preflight from `jira.md` step 1 (resolve
   `cloudId` from the configured `site`). Read the `jira:` block for `project`,
   `labels` (the task-loop labels), and the optional **`archive_status`** key (the
   name of the terminal-category status to move archived issues to — see
   `jira-config.md` → "Archive status").

2. **No archive status configured → no-op.** If `jira.archive_status` is unset,
   **stop** and report: "jira has no archive status configured and native Jira
   archival is a Premium feature — nothing to do. Set `jira.archive_status` to a
   terminal-category status name if your project has one, or rely on the board's
   Done column." Do not transition anything.

3. **Find candidates — scope to task-loop issues.** Query terminal issues older
   than the threshold via JQL, **AND-filtered by the configured `jira.labels`** so
   the sweep only touches loop-created issues, never unrelated Done work in the
   project (jira has no issue cap, so an over-broad sweep is pure downside). This
   mirrors the label filter `jira.md`'s List path uses. Append one
   `AND labels = "<label>"` clause per configured label:

   ```text
   project = "<project>" AND statusCategory = Done AND resolutiondate <= "-<N>d"
   AND status != "<archive_status>" AND labels = "<label1>" [AND labels = "<label2>"]
   ORDER BY resolutiondate ASC
   ```

   When `jira.labels` is empty/unset, there is no durable task-loop marker — do
   **not** sweep the whole project. **Stop** and report that archiving needs at
   least one configured label to distinguish loop issues from the rest of the
   project. Call `<atlassian-mcp>__searchJiraIssuesUsingJql` with `cloudId`, that
   `jql`, `fields: ["summary","status","resolutiondate"]`, and a sensible
   `maxResults`.
   Excluding `status != "<archive_status>"` keeps already-archived issues out.

4. **Always print the candidate list first** (key + summary + resolution date). If
   `dry-run`, stop here and report "nothing archived (dry-run)".

5. **Transition.** For each candidate, resolve the transition that leads to
   `archive_status` — call `<atlassian-mcp>__getTransitionsForJiraIssue` for the
   issue, match the transition whose target status name equals
   `jira.archive_status` (names are resolved per-issue; `transitionJiraIssue`
   takes a transition id, not a status name — same pattern as `jira-promote.md`),
   then call `<atlassian-mcp>__transitionJiraIssue`. If no available transition
   reaches `archive_status` for a given issue, record it as skipped and continue —
   one unreachable issue must not abort the rest.

6. **Report.** Count transitioned, any skipped (no reachable transition), and in
   dry-run the candidate list plus "nothing archived".
