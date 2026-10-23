---
title: task-dashboard — laptop session 1, capture real acli fixtures and fix the parser against them
priority: high
size: 2
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - scripts/task-dashboard/jira_acli.py
  - scripts/task-dashboard/fixtures/acli/
  - dev_docs/research/2026-10-23-task-dashboard-acli-spike.md
is_blocked_by: task_dashboard_task_2
parent: task_dashboard
tags: [task-dashboard, jira, acli, laptop]
---

Part of [[task_dashboard_plan]]. **Runs on: the work laptop**, which has
`acli` and the Jira login. Its connection is poor, so this task is one short
session:

1. pull the branch
2. run a few small `acli` calls
3. commit the redacted fixtures and any parser fix
4. push

There is no full sync and no bulk download.

## Context

[[task_dashboard_task_2]] built the `acli` adapter on the Mac mini against
**synthetic** fixtures, hand-written from the shapes in the spike record. This
session checks those shapes against real output, and captures the one thing
the Mac mini couldn't: the real stderr of an `acli` call that isn't logged in.
The auth-error classifier was written against a guess.

`acli` calls need network to Atlassian. Run them unsandboxed if this is a
Claude session.

## Task

- Capture these, writing each to a file. Never send `view --json` output to
  `/dev/null`, where acli exits 1.
  - `acli jira workitem search --jql "assignee = currentUser()" --limit 5 --json`
  - `view` (with the design's field list) of four issues: one with links in
    both directions, a parent, a multi-sprint history and Flagged set; one
    with mostly null fields; and two others
  - `acli jira board list-sprints --id <board> --state active --json`
  - the stderr of a logged-out call: `acli jira auth logout`, then one
    `search`, then `acli jira auth login` again
- **Redact before anything is staged.**
  - Replace summaries, descriptions, display names, emails, account ids, avatar
    URLs, the site domain and the org/team names with placeholders.
  - Keep issue keys, project keys, field ids, enum values, dates and the JSON
    structure.
  - Save the results beside the synthetic ones in
    `scripts/task-dashboard/fixtures/acli/` with a `real-` prefix, and note
    them in that folder's `README.md`.
- Run the acli tests. Then replace the classifier's auth patterns in
  `jira_acli.py` with the real logged-out text, and keep a synthetic
  "unauthorized" case only if it still applies.
- Do a scratch live run: `search_keys('assignee = currentUser()')`, then
  `view_issues` on its first 10 keys. Sanity-check the parsed sprint, flagged
  and link values.

## Acceptance Criteria

**Code-enforced**

- `scripts/test-task-dashboard-acli.sh` passes over both the synthetic and the
  `real-` fixtures.
- The leak guard passes: no `@` and no `atlassian.net` anywhere in the
  fixtures.
- The real logged-out stderr classifies as `AuthError`.
- `just check` passes.

**User-run**

- The 10-issue scratch run returns 10 parsed issues whose sprint, flagged and
  link values match what Jira's web UI shows for them.
- Before pushing, read through the `real-` fixture diff once to confirm the
  redaction. Public repo.
