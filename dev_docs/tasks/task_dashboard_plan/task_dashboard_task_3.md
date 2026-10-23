---
title: task-dashboard — gh adapter (GraphQL PR search, CI state, Jira-key join)
priority: medium
size: 3
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/research/2026-10-23-task-dashboard-acli-spike.md
  - dev_docs/designs/2026-10-23-task-dashboard.md
is_blocked_by: task_dashboard_task_1
parent: task_dashboard
tags: [task-dashboard, github]
---

Part of [[task_dashboard_plan]].

## Context

The dashboard shows my open PRs and the PRs where my review is requested, in
the configured work orgs only. The `gh` account is personal and reaches
personal repos too: unfiltered, `--author @me` returned 220 PRs, 141 of them
personal. See **GitHub through `gh`** in the design and section 6 of the spike
record.

Constraints the spike measured:

- `gh search prs` has no `headRefName`, so it can't supply branch names.
  Instead, use one paginated GraphQL `search(query:$q, type:ISSUE,
  first:50)`, selecting `... on PullRequest { url title headRefName isDraft
  reviewDecision updatedAt repository { nameWithOwner } }`. For 68 PRs this
  took 5.7s.
- Adding `commits(last:1){…statusCheckRollup{state}}` at `first:100` made
  GitHub return an HTML error after 12s. Fetch CI state **per PR**
  (`gh pr view <url> --json statusCheckRollup`), or keep the page small.
- Titles carry upper-case Jira keys; branches mostly carry lower-case ones.

## Task

- Add `scripts/task-dashboard/github_gh.py` with the same runner seam as task
  2 (subprocess, pipes, timeout, fakeable):
  - `my_prs(orgs)` runs the query `is:pr is:open author:@me org:<org>` (one
    `org:` qualifier per configured org, or one search per org).
  - `review_requested(orgs)` runs the same with `review-requested:@me`.
  - `ci_state(url)` reduces `statusCheckRollup` to one of
    `success | failure | pending | none`. It must handle both `CheckRun`
    (`status`, `conclusion`) and `StatusContext` (`state`) entries.
  - `jira_keys(text, allowed_projects)` matches
    `[A-Za-z][A-Za-z0-9]+-\d+` case-insensitively in title and branch,
    upper-cases each match, and keeps a match only if its project prefix is in
    `allowed_projects`.
- Capture redacted fixtures on the laptop: one GraphQL page and two rollups
  (one `CheckRun`-only, one mixed). Replace org and repo names, titles and
  logins. Store them under `scripts/task-dashboard/fixtures/`.
- Add `scripts/test_task_dashboard_gh.py` and its `.sh` wrapper.

## Acceptance Criteria

**Code-enforced**

- `jira_keys`:
  - `"feat(x): thing [AB-12]"` yields `AB-12`
  - branch `bestdan/ab-12-thing` yields `AB-12`
  - `v2-1` yields nothing when `V2` is not allowed
  - a key from an unlisted project is dropped
- `ci_state` returns `failure` if any check failed, `pending` if any is still
  running and none failed, and `success` only when every check passed.
- GraphQL pagination follows `pageInfo.endCursor` until `hasNextPage` is false
  (fake runner, two pages).
- `just check` passes.

**User-run**

- On the work laptop, `my_prs([<work org>])` returns about the same count as
  `gh search prs --author @me --state open --owner <work org>`. Every returned
  PR has a `headRefName`.
