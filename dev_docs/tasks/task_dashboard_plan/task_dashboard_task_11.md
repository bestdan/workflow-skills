---
title: task-dashboard — laptop session 2, install for real and run the live acceptance checks
priority: high
size: 2
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/designs/2026-10-23-task-dashboard.md
  - skills/task-dashboard/SKILL.md
is_blocked_by:
  - task_dashboard_task_8
  - task_dashboard_task_10
parent: task_dashboard
tags: [task-dashboard, laptop, acceptance]
---

Part of [[task_dashboard_plan]]. **Runs on: the work laptop.** By this point
everything is built and has been checked on the Mac mini against a fake
`acli`. This session is the real-data checks only.

It is the one heavy network step: the first full sync is about 100s of `acli`
calls (the spike measured 529 issues). Run it when the connection allows. If
it can't, the dashboard still works, just with a smaller first pull.

## Context

Each earlier task deferred its live check to here:

- **Sync:** [[task_dashboard_task_4]]
- **Server:** [[task_dashboard_task_5]]
- **Ranking:** [[task_dashboard_task_6]]
- **GitHub, work org:** [[task_dashboard_task_3]]
- **Install:** [[task_dashboard_task_8]]

`acli` and `gh` calls need network. Run them unsandboxed in a Claude session.

## Task

1. **Install.** Pull the branch and run `/task-dashboard install`. Fill
   `board_ids`, the two custom field ids (the spike found the sprint and
   Flagged ids for this site) and `github.orgs`. Leave `acli_path` at its
   default and `extra_allowed_hosts` empty.
2. **First sync.** Let `install` run its `full` sync. Note the time and any
   keys that failed twice.
3. **Incremental.** Click **Sync now**. It should finish in seconds and view
   only a handful of keys.
4. **Login survives logout.** Log out of macOS and back in. The agent
   restarts, and within five minutes the header shows a fresh sync. Run
   `/doctor`.
5. **GitHub.** Compare the work-org counts with GitHub:
   - Waiting on me ⊇ `gh search prs --review-requested @me --state open --owner <work org>`
   - my PRs ≈ `gh search prs --author @me --state open --owner <work org>`
6. **Ranking.** Read Now's top ten. Where the order surprises you, the term
   breakdown should explain why. Adjust `score.weights` in the config until it
   matches your sense of what's next. Note the final weights in this task's PR,
   with no work detail.
7. **Expired-auth path.** Run `acli jira auth logout`, then **Sync now**. The
   header names `acli jira auth login`. Log back in and sync again.

## Acceptance Criteria

**Code-enforced**

- None new. Any bug found here gets a fix with a test, in its own commit.

**User-run**

- Every step above behaves as described.
- Record the full-sync time, the incremental time and the final weights in
  the PR body, without issue content.
