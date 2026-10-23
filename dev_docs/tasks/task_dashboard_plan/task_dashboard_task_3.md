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

Part of [[task_dashboard_plan]]. **Runs on: the Mac mini**, live against the
personal GitHub account.

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
  - `my_prs(owners)` runs the query `is:pr is:open author:@me <owner qualifier>`,
    one search per owner. Config `github.orgs` supplies the owners.
    - Build the qualifier per owner, and check on this machine which form
      GitHub accepts for an org versus a user account (`org:` or `user:`).
    - The live check below runs against the personal account `bestdan`, so
      the adapter must accept a user as an owner as well as an org.
  - `review_requested(owners)` runs the same with `review-requested:@me`.
  - `ci_state(url)` reduces `statusCheckRollup` to one of
    `success | failure | pending | none`. It must handle both `CheckRun`
    (`status`, `conclusion`) and `StatusContext` (`state`) entries.
  - `jira_keys(text, allowed_projects)` matches
    `[A-Za-z][A-Za-z0-9]+-\d+` case-insensitively in title and branch,
    upper-cases each match, and keeps a match only if its project prefix is in
    `allowed_projects`.
- **Capture fixtures on the Mac mini, from the personal account's own PRs.**
  These are public `bestdan/*` repos, so there is no work data in them. You
  need:
  - one GraphQL search page, trimmed to a few nodes
  - two `statusCheckRollup` responses: one `CheckRun`-only, one mixing
    `CheckRun` and `StatusContext`, if such a PR exists

    If no mixed one exists, hand-write it from the spike record's shape and
    note it in `scripts/task-dashboard/fixtures/gh/README.md`.

  Store them under `scripts/task-dashboard/fixtures/gh/`.
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

- On the Mac mini, run `my_prs(["bestdan"])`. It should return about the
  same count as `gh search prs --author @me --state open --owner bestdan`, and
  every returned PR should have a `headRefName`. The same check against the
  work org is part of [[task_dashboard_task_11]].
