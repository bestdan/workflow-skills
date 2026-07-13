---
title: Graduate the architecture to dev_docs/orchestrator.md and delete the plan folder
priority: low
size: 2
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_8
related_files:
  - dev_docs/tasks/orch_py_plan/
  - dev_docs/orchestrator.md # to be created
tags: [orchestrator, docs, cleanup]
---

← [[orch_py_plan]]

## Context

`dev_docs/tasks/<name>_plan/` is temporary project-tracker scaffolding, not permanent
documentation. Once the port is done the plan folder is residue — but the *decisions* made
along the way are exactly what a future reader (or agent) will need, and they must not be
deleted with it.

## Task

Write `dev_docs/orchestrator.md` capturing the durable knowledge:

- **The architecture as it ended up:** which subcommands are Python, which are bash, and the
  dispatch seam in `spawn-orchestrator.sh` that routes between them.
- **The launchd boundary and why it exists.** This is the single most important thing to
  record: the generated launch script runs under a pinned minimal PATH, so anything it calls
  back into has a hard runtime constraint. Include the task-4 grep test as the guard that
  keeps the boundary from being widened by accident, and record whichever option (a)/(b)/(c)
  task 2 chose, **with its rationale** — a future reader will otherwise "clean up" the
  inconsistency and break an unattended 3am wake.
- **The golden corpus:** what `test/golden/` is, how to regenerate it, and why it is committed.
- **Gotchas hit during the port** — quoting in the generated script, fail-closed semantics,
  the exec-symlink resolution rule, anything that bit you.
- Any bug found-but-not-fixed during the port (the tasks forbid fixing behavior mid-port).
  These become follow-up tasks; file them rather than losing them.

Then delete `dev_docs/tasks/orch_py_plan/` in full.

## Acceptance Criteria

**Code-enforced:**

- `dev_docs/orchestrator.md` exists and covers: the bash/Python split, the launchd boundary
  and its rationale, the golden corpus, and the port gotchas.
- `dev_docs/tasks/orch_py_plan/` no longer exists.
- Any deferred bug found during the port has been filed as a task (`/add-task`), not dropped.
- `just check` green (`validate.py` enforces task-file frontmatter under `dev_docs/tasks/`,
  so a half-deleted plan folder will fail the gate).
