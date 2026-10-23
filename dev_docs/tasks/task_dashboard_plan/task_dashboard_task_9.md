---
title: task-dashboard — graduate the design to dev_docs/task-dashboard.md and delete plan scaffolding
priority: low
size: 1
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/designs/2026-10-23-task-dashboard.md
  - dev_docs/research/2026-10-23-task-dashboard-acli-spike.md
  - dev_docs/tasks/task_dashboard_plan/
is_blocked_by: task_dashboard_task_8
parent: task_dashboard
tags: [task-dashboard, docs, cleanup]
---

Part of [[task_dashboard_plan]].

## Context

A design under `dev_docs/designs/` is a proposal, deleted when it lands, and a
`<name>_plan/` folder is temporary scaffolding (see `dev_docs/README.md` and
the plan-with-docs lifecycle). Once tasks 1–8 have merged, the lasting
knowledge moves into a live doc and the scaffolding goes. The dated spike
record in `dev_docs/research/` stays: it is evidence, allowed to go stale.

## Task

- Write `dev_docs/task-dashboard.md`, an undated live doc kept current. It
  covers:
  - the architecture as built
  - the two-step `acli` fetch, and why (cite the spike record)
  - the sync modes and the cursor rule
  - the security model (Host, Origin and token)
  - the per-site config (field ids, boards, orgs)
  - operating notes: install and uninstall, reading `sync_runs`, the
    expired-auth fix, and plan C (REST `/search/jql`) if the `view` fan-out
    degrades

  Point to the code rather than restating it.
- Delete `dev_docs/designs/2026-10-23-task-dashboard.md` and
  `dev_docs/tasks/task_dashboard_plan/`, and remove the
  `!dev_docs/tasks/task_dashboard_plan/` negation, with its comment, from
  `.gitignore`.
- Update any links into the deleted design, for example from the spike
  record, so they point at `dev_docs/task-dashboard.md`.

## Acceptance Criteria

**Code-enforced**

- `bash scripts/dev-docs-layout.sh` passes, and
  `rg -n "designs/2026-10-23-task-dashboard|task_dashboard_plan" dev_docs .gitignore`
  returns nothing.
- `just check` passes.

**User-run**

- None.
