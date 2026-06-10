# jira handler — /do-tasks execute flow

Invoked from `/do-tasks` (section 5, "jira path") when `handler: jira` is configured. This file holds the full jira claim/execute flow, run in the current session over the Atlassian MCP: **find candidates** (read-only), **judge feasibility** (read-only), **claim the issue** (mutating, before work starts), **branch + execute**, **PR**, and **move to review on PR open** (mutating, after the PR is opened). A separate **bail** phase runs when work proves infeasible mid-execution. It mirrors the tracker flow in `commands/handlers/linear-claim.md`, over the Atlassian MCP instead of the Linear MCP — the same way `gh-issue-claim.md` mirrors it over the `gh` CLI.

**Shared reference:** the Atlassian MCP preflight is `commands/handlers/jira.md` step 1; the `ready_status` config key (the jira analogue of the Linear `Todo`/`unstarted` ready lane) is defined in `commands/handlers/jira-config.md`; `commands/handlers/linear-claim.md` is the structural template.

> **MCP namespace.** `<atlassian-mcp>__` is `mcp__claude_ai_Atlassian__` or `mcp__atlassian__` depending on the install (see `jira-config.md`) — substitute the prefix loaded in your session. Tool names after the prefix (`getAccessibleAtlassianResources`, `searchJiraIssuesUsingJql`, `getJiraIssue`, `editJiraIssue`, `atlassianUserInfo`, `getTransitionsForJiraIssue`, `transitionJiraIssue`, `addCommentToJiraIssue`) are identical across installs.

> **Scope.** This is the **core** claim/execute flow. The `--claim-only`/`--no-claim` split and the pre-claim WIP gate that `linear-claim.md` and `gh-issue-claim.md` carry are **not yet wired for jira** — they land in the sibling slice. Until then `/do-tasks` runs the atomic claim-and-execute path below.

> **Hard rule for every phase below: never transition a jira issue to a `Done`/`completed`-category status, and never close it manually.** Merge is the only completion signal — Jira's GitHub integration (or a smart commit on merge) closes the issue automatically when the PR, whose title carries `[<KEY>]` and whose body names `<KEY>`, merges. If you are about to `transitionJiraIssue` to a `Done`-category status from this file, you have a bug — stop.

## Config

Read `dev_docs/tasks/.task-config.yml`. The jira claim flow reads:

- `jira.site` — cloudId / site URL (as the create and promote flows use).
- `jira.project` — project key.
- `jira.ready_status` — **required here.** The status the ready lane lives in (the jira analogue of Linear's `Todo`). `/do-tasks` pulls candidates from this status. If it is unset/empty, **stop** with: "jira `/do-tasks` needs `jira.ready_status` set in dev_docs/tasks/.task-config.yml (the status promoted issues land in). Set it, or run `/task-config jira`." Do not guess a status name.
- `jira.base_branch` — optional; the branch `/do-tasks` branches from (default: the repo's default branch).

## Find candidates

1. **Preflight.** Run the Atlassian MCP preflight exactly as `commands/handlers/jira.md` step 1: call `<atlassian-mcp>__getAccessibleAtlassianResources` (no args) and confirm a resource whose `url` matches `https://<jira.site>`. On either failure, **stop** with the same messages ("Jira handler needs the Atlassian MCP. Install/connect it in Claude Code settings, then re-run." / "Configured Jira site `<site>` is not in your accessible Atlassian resources."). Do not fall back to another handler. Also confirm `gh auth status`, a clean working tree, and fetch the base branch.

2. **Query.** Call `<atlassian-mcp>__searchJiraIssuesUsingJql` with:
   - `cloudId`: `<jira.site>`
   - `jql`: `project = "<project>" AND status = "<ready_status>" AND assignee IS EMPTY ORDER BY priority DESC, updated ASC`
   - `fields`: `["summary", "status", "priority", "labels", "description", "assignee"]`
   - `maxResults`: 50

   `assignee IS EMPTY` skips anything already claimed (the jira analogue of Linear's "pull only from `unstarted`" and gh-issue's `no:assignee`). The `ORDER BY` ranks by priority then age up front. Read the issues from whichever key the server returns (`issues[]` or `issues.nodes[]`; the create flow reads `issues.nodes[0]`). Limit 50 — if exactly 50 are returned the page may be truncated; note it in the report and do not paginate.

3. **Filter.** From the returned issues, drop any that carry a `human-approval-requested` or `blocked` label (a defensive filter — `assignee IS EMPTY` already excludes claimed work). Jira has no native `estimate`/size field (story points, when present, are a custom field), so there is no estimate gate here — scope is judged in the next phase, exactly as `jira-promote.md` folds size into the scope judgment.

Return the ranked list to **Judge feasibility**. Each entry needs `key`, `fields.summary`, `fields.description`, `fields.priority`, `fields.labels`, and the issue's `webUrl` (or build `https://<jira.site>/browse/<key>`). If no candidate remains, report that and stop.

If the `/do-tasks` argument was a specific issue key (e.g. `PLAT-142`), skip the query and call `<atlassian-mcp>__getJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`, the same `fields`) for that one issue. Apply the step-3 filter and the `assignee IS EMPTY` / `status = <ready_status>` gates to it; if it fails any gate, return the failure reason rather than the issue. Do not auto-override the gates from a direct key — `/do-tasks` surfaces the reason and stops.

## Judge feasibility

Take candidates in ranked order, **one at a time** — stop at the first feasible one. For each, read the full issue description and decide whether this session can finish it without a human (a concrete outcome, identifiable files, a PR landable in ~1 hour, no product/design call or inaccessible infra needed).

If feasible: continue with this candidate (proceed to "Claim the issue"). If not: leave a one-line skip comment and move to the next:

```
<atlassian-mcp>__addCommentToJiraIssue
  cloudId: <jira.site>
  issueIdOrKey: <KEY>
  commentBody: "Skipped by /do-tasks: <reason>"
```

**Do not claim it.** If every candidate is rejected, summarize the reasons and stop — do not lower the bar. Print the chosen issue's key, summary, and a one-sentence rationale, then proceed.

## Claim the issue

Jira has no transactional claim, so use a **read-then-write guard** (the analogue of `linear-claim.md`'s concurrency guard and `gh-issue-claim.md`'s assignee guard): self-assign and move the issue to an In-Progress status, then re-read to confirm no one raced in.

1. **Resolve your account id.** Call `<atlassian-mcp>__atlassianUserInfo` (no args) and capture the current user's `account_id`. (`@me` has no JQL/edit equivalent in Jira — assignment is by account id.)

2. **Re-read** the chosen issue — `<atlassian-mcp>__getJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`, `fields: ["assignee", "status"]`). If it now has an assignee, or its status is no longer `ready_status`, **another session beat you** — return `race`, fall back to the next candidate.

3. **Assign yourself.** Call `<atlassian-mcp>__editJiraIssue` with:
   - `cloudId`: `<jira.site>`
   - `issueIdOrKey`: `<KEY>`
   - `fields`: `{ "assignee": { "accountId": "<account_id>" } }`

4. **Transition to In Progress.** `transitionJiraIssue` takes a transition **id**, not a status name, so resolve it per issue:

   ```
   <atlassian-mcp>__getTransitionsForJiraIssue
     cloudId: <jira.site>
     issueIdOrKey: <KEY>
   ```

   From the returned `transitions[]`, pick the entry whose target status is in the `indeterminate` (In Progress) category — `to.statusCategory.key == "indeterminate"`, preferring one named `In Progress` if several exist. Capture its `id` and transition:

   ```
   <atlassian-mcp>__transitionJiraIssue
     cloudId: <jira.site>
     issueIdOrKey: <KEY>
     transition: { id: "<transition-id>" }
   ```

   If no `indeterminate` transition is available, **do not guess** — surface the available transition names so the user can fix the workflow, unassign yourself, and stop.

5. **Confirm.** Re-read the issue's `assignee` (`getJiraIssue`, `fields: ["assignee"]`). The claim holds **iff** `assignee.accountId` equals your `account_id` from step 1. If anyone else appears, a concurrent claimer raced in — clear the assignee (`editJiraIssue` with `fields: { "assignee": null }`), revert the status to `ready_status` (resolve its transition id the same way), and fall back to the next candidate.

6. **Comment** the branch name via `addCommentToJiraIssue` (`commentBody: "Claimed by /do-tasks. Working on branch \`task/<KEY>\`; PR link will follow."`).

Return the issue key and url so `/do-tasks` can branch and execute.

## Branch + execute

1. **Branch** — `task/<KEY>` (Jira has no native branch primitive, so the handler publishes this deterministic name). Branch from `jira.base_branch` (default: the repo's default branch):

   ```bash
   git fetch origin && git switch -c "task/<KEY>" "origin/${BASE:-main}"
   ```

2. **Execute** — do the work, then run the project's quality gate (`just check` here). Keep the diff scoped to this one issue.

## PR

```bash
gh pr create --title "[<KEY>] <summary>" --body "<KEY>: <jira issue URL>

<summary of the change>"
```

The `[<KEY>]` title prefix and the `<KEY>` / issue URL in the body are the links Jira's GitHub integration (and smart commits) match on to associate and close the issue on merge — the jira analogue of `Closes #<n>` (gh-issue) and `Closes <identifier>` (Linear). Then post the PR URL back to the issue:

```
<atlassian-mcp>__addCommentToJiraIssue
  cloudId: <jira.site>
  issueIdOrKey: <KEY>
  commentBody: "PR opened: <PR URL>"
```

## Move to review on PR open

Called from `/do-tasks` immediately after `gh pr create` succeeds. Transition the issue to an In-Review status if the workflow has one; otherwise leave it In Progress.

1. **Resolve the transition.** Call `getTransitionsForJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`). Look for a transition whose target status is still in the `indeterminate` category and whose `to.name` (case-insensitive) is `In Review` (or `Review`). If found, capture its id; if none exists, **skip the transition** — the issue stays In Progress, and the open PR plus the PR-URL comment are the review signal. Never pick a `done`-category transition here, regardless of how done the work feels.

2. **Transition** (only if step 1 found one):

   ```
   <atlassian-mcp>__transitionJiraIssue
     cloudId: <jira.site>
     issueIdOrKey: <KEY>
     transition: { id: "<transition-id>" }
   ```

The PR-URL comment posted right after `gh pr create` is the user-facing signal that review has started; the status transition is the structural one.

## Bail (when execution proves infeasible mid-flight)

```bash
git stash push -u
```

Then, over the Atlassian MCP:

1. **Unassign** — `editJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`, `fields: { "assignee": null }`).
2. **Transition back** to `ready_status` (resolve its transition id via `getTransitionsForJiraIssue`, matching `to.name == <ready_status>`) so the issue returns to the lane it came from. If a `human-approval-requested`-style label is configured on the board, add it via `editJiraIssue` so a human knows to look; otherwise the comment below carries that signal.
3. **Comment** the bail reason via `addCommentToJiraIssue` (`commentBody: "Bailed by /do-tasks: <what was tried, what tripped the bail>"`).

Stop — do not auto-pick another candidate after a bail; a human should look before more work is auto-claimed.

## Report

`/do-tasks` prints the outcome:

- **On success:** the issue key, the PR URL, and a one-line summary of what changed.
- **On bail:** the issue key, why it bailed, and a note that the bail comment was posted.
- **On no feasible candidate:** say so (with the skip reasons recorded on each rejected issue).
