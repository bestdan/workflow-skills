---
title: task-dashboard — HTTP server core, security checks, Sync now, header, Board view
priority: high
size: 5
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/designs/2026-10-23-task-dashboard.md
  - scripts/local-review/server.py
  - commands/handlers/assets/kanban-classify.py
  - scripts/task-dashboard/sync.py
is_blocked_by: task_dashboard_task_4
parent: task_dashboard
tags: [task-dashboard, server, security]
---

Part of [[task_dashboard_plan]]. **Runs on: the Mac mini**, against the fake
`acli` from [[task_dashboard_task_4]].

## Context

**One process:** a stdlib `http.server` (threaded) plus a sync thread.
`scripts/local-review/server.py` is the in-repo precedent for a stdlib server
that renders HTML. Read it for structure, not to copy its diff UI. The design
sections that govern this task:

- **Architecture:** a `threading.Event` wakes the sync thread, and a lock
  allows one sync at a time.
- **Manual sync:**
  - A `POST /sync` wakes the loop and returns immediately.
  - A request during a run joins that run.
  - A reconcile requested during an incremental run is queued after it.
  - A manual run resets the interval timer.
  - Shift-click means reconcile.
- **Hosting and access:**
  - Bind to `127.0.0.1` only.
  - Refuse any request whose `Host` is not exactly `127.0.0.1:<port>` or
    `localhost:<port>`. This blocks DNS rebinding, which could otherwise read
    work Jira data.
  - Every `POST` needs the per-install token embedded in the page, plus a
    matching `Origin`.
- **Failure / expired auth:**
  - The header shows how old the last successful sync is.
  - It turns red past three intervals.
  - An `auth_failed` run shows "run `acli jira auth login`" instead of a
    generic error.

The **Board** view reuses `commands/handlers/assets/kanban-classify.py`, so
its sections match `/list-tasks`. Read that script's header for its input row
shape and render order.

## Task

- Add `scripts/task-dashboard/server.py` and a `serve` subcommand on
  `dashboard.py`:
  - **Request guard:** check `Host` on every request (403 otherwise). On
    `POST`, also check `Origin` and the token. Generate the token once and
    store it in `meta`.
    - Add a `server.extra_allowed_hosts` config list, empty by default. Each
      entry is a `host[:port]` that the `Host` and `Origin` checks also
      accept.
    - This is what lets the Mac mini be tested through `ts-share.sh` now, and
      it is what the later tailnet port needs. Find out which `Host` header
      Tailscale Serve actually forwards before relying on it. Leave the
      laptop's config empty.
  - **Sync thread:**
    - interval loop on `Event.wait(timeout)`
    - single-flight lock
    - a pending-reconcile flag consumed after the current run
    - timer reset after a manual run
  - **`POST /sync`** (body `mode=incremental|reconcile`) returns 202 with the
    run state. **`GET /status`** returns JSON with the last run, running
    state and age, which the page polls while _Syncing…_.
  - **Header partial on every page:**
    - last successful sync age
    - red when stale
    - an auth-expired message
    - **Sync now** button (shift-click sends `reconcile`)
  - **Board view:** `issues` in the active sprint, mapped to
    kanban-classify rows and rendered as its sections. Load the classifier by
    file path relative to the plugin root; don't copy it.
- Minimal, dependency-free HTML and CSS: no JS framework, a few lines of inline
  JS for the button and the polling.
- Add `scripts/test_task_dashboard_server.py` and its `.sh` wrapper. Start the
  server on an ephemeral port in-process, with a temp DB and a fake sync
  function.

## Acceptance Criteria

**Code-enforced**

- A `GET` with `Host: evil.example:PORT` gets 403. With `Host:
  127.0.0.1:PORT`, it gets 200.
- A `POST /sync` without the token, or with a foreign `Origin`, gets 403.
- Two `POST /sync` calls during one fake run start exactly one run. A
  reconcile requested during an incremental run starts exactly one reconcile,
  after it.
- With the latest run `auth_failed`, the header contains `acli jira auth login`.
- The Board view's section order matches kanban-classify's render order for
  a fixture DB.
- `just check` passes.

**User-run**

- On the Mac mini, with the fake `acli` configured, run
  `uv run --no-project --python ">=3.11" scripts/task-dashboard/dashboard.py serve`.
  Publish it with `ts-share.sh 8765` (with the tailnet host in
  `extra_allowed_hosts`) and open the link on the phone.
  - The Board shows the fixture sprint.
  - **Sync now** shows _Syncing…_, then a fresh "last synced" time.
  - With `FAKE_ACLI_MODE=auth_fail`, the header shows the `acli jira auth
    login` message.
  - Run `ts-share.sh off 8765` afterwards.
- The same check against real Jira is in [[task_dashboard_task_11]].
