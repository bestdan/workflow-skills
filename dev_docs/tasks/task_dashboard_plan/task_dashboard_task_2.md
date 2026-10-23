---
title: task-dashboard — acli adapter (search, view, field parsing, retry, auth errors)
priority: high
size: 5
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/research/2026-10-23-task-dashboard-acli-spike.md
  - dev_docs/designs/2026-10-23-task-dashboard.md
is_blocked_by: task_dashboard_task_1
parent: task_dashboard
tags: [task-dashboard, jira, acli]
---

Part of [[task_dashboard_plan]].

## Context

All Jira reads go through `acli` (Atlassian's CLI, OAuth login held in the
macOS keychain). The spike, `dev_docs/research/2026-10-23-task-dashboard-acli-spike.md`,
fixes the constraints. **Read it before starting.** In summary:

- `acli jira workitem search` accepts only `key, summary, status, assignee,
  reporter, priority, labels, issuetype, description, creator` in `--fields`.
  Every date, link, parent and custom field is rejected client-side.
- So fields come from `acli jira workitem view <KEY> --fields "<list>" --json`.
  It takes **one key per call**, at about 0.9s each. At 8-way concurrency about
  1% fail transiently with `failed to fetch work item details`, and a serial
  retry fixes them.
- `search --paginate --json` returns every page. `--count` prints only a human
  line, `Number of work items in the search: N`, even with `--json`.
- `view --json` with stdout sent to `/dev/null` exits 1. Always capture to a
  pipe.
- Responses wrap each issue in an envelope with many null keys. Read only `key`
  and `fields`.
- The sprint custom field is an array of every sprint the issue has been in;
  keep the element with `state == "active"`. The Flagged field is `null` or
  `[{value: "Impediment"}]`. Each `issuelinks[]` entry carries the linked
  issue's key and `status.statusCategory.key` inline.
- `acli jira board list-sprints --id <board> --state active --json` returns the
  active sprints with `endDate`.

## Task

- Add `scripts/task-dashboard/jira_acli.py`:
  - **A runner seam:** one function that runs an `acli` argv with
    `subprocess`, captures stdout and stderr through pipes, applies a timeout,
    and returns `(rc, stdout, stderr)`. Tests replace it with a fake.
  - `search_keys(jql) -> list[str]` via `search --paginate --json --fields key`.
  - `view_issues(keys, fields, concurrency) -> dict[key, parsed]`, using a
    `ThreadPoolExecutor` at `concurrency`. Collect failures and retry them
    serially once. Keys that still fail are returned as a separate list. They
    do not raise.
  - `active_sprints(board_id) -> list[{id, name, endDate}]`.
  - `parse_issue(raw, field_ids) -> dict`. Normalize into the `issues` columns
    from task 1, plus `links` rows (type name, direction, other key, other
    key's status category) and the active sprint.
  - **Error classification.** Map an auth failure (stderr naming
    unauthorized, login or token; find the real text, see below) to an
    `AuthError`, and everything else to `AcliError`.
- **Capture fixtures on the work laptop and redact them.** You need: one
  `search` page, `view` output for an issue with links, parent, sprint
  history and Flagged set, and one with nulls, plus one `list-sprints`. Also
  capture the real stderr of a logged-out `acli` call (`acli jira auth logout`,
  run, then log back in) so the auth classifier matches real text. Replace
  summaries, names, emails, account ids, site URLs and the org with
  placeholders. Keep keys, shapes and field ids. Store them under
  `scripts/task-dashboard/fixtures/`.
- Add `scripts/test_task_dashboard_acli.py` and its `.sh` wrapper.

## Acceptance Criteria

**Code-enforced**

- Using the fake runner and fixtures:
  - `parse_issue` picks the active sprint out of a multi-sprint history
  - it maps Flagged to a boolean
  - it emits one `links` row per `issuelinks` entry, with direction and the
    linked issue's status category
  - it tolerates every nullable field being null
- `view_issues` with a fake that fails 2 of 20 keys once returns all 20; with a
  fake that always fails one key, it returns 19 plus that key in the failure
  list.
- The logged-out stderr fixture classifies as `AuthError`, and a transient
  fetch error does not.
- The fixtures contain no site domain, org name, email or account id (a test
  greps for `@` and `atlassian.net`).
- `just check` passes.

**User-run**

- On the work laptop, a scratch call of
  `search_keys('assignee = currentUser()')` and then `view_issues` on its first
  10 keys returns 10 parsed issues with sane sprint, flagged and link values.
