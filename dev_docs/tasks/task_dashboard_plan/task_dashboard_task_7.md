---
title: task-dashboard — overlay writes (pins, order, snooze, notes) and export/import
priority: medium
size: 3
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/designs/2026-10-23-task-dashboard.md
  - scripts/task-dashboard/server.py
  - scripts/task-dashboard/db.py
is_blocked_by: task_dashboard_task_5
parent: task_dashboard
tags: [task-dashboard, overlay]
---

Part of [[task_dashboard_plan]]. **Runs on: the Mac mini.**

## Context

The overlay is the user's own state: pins, manual order within the pinned set,
snooze-until dates and private notes. It is keyed by issue key or PR URL and
stored only in SQLite. **The sync never touches it**, and it is the only data
on the machine that can't be rebuilt from Jira. Overlay rows for an issue that
has left the mirror are kept but hidden, so they reappear if the issue comes
back. See **Mirror vs. overlay** in the design.

Every write is a `POST` behind task 5's guard: `Host`, `Origin`, and the
per-install token.

## Task

- Add to `server.py`:
  - `POST /overlay/pin` and `/overlay/unpin`
  - `POST /overlay/order`, taking an ordered list of pinned keys
  - `POST /overlay/snooze`, taking a key and an ISO date, or empty to clear
  - `POST /overlay/note`, taking a key and text, or empty to delete
- Add matching controls on each card (pin toggle, snooze picker, note editor)
  and up/down reordering of pinned cards. Keep JS minimal and inline.
- Add `dashboard.py export [path]` and `dashboard.py import <path>`:
  - `export` writes the four overlay tables to versioned JSON (stdout by
    default).
  - `import` replaces the overlay with the file's contents in one
    transaction, after validating it.
- Add `scripts/test_task_dashboard_overlay.py` and its `.sh` wrapper.

## Acceptance Criteria

**Code-enforced**

- An export → wipe → import round trip leaves the overlay tables identical.
- An overlay note for a key absent from `issues` survives a reconcile that
  drops that key (fake sync).
- An overlay `POST` without the token gets 403 (reusing task 5's harness).
- A malformed import file is rejected with the overlay unchanged.
- `just check` passes.

**User-run**

- On the Mac mini, against the fake `acli`: pin an issue, add a note, snooze
  another until tomorrow, then restart the server. All three persist, and
  `dashboard.py export` prints them.
