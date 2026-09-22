---
title: Strict-clean the Linear handler assets (consumer-facing runtime code)
priority: high
size: 5
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: py_toolchain
is_blocked_by: py_toolchain_task_1
related_files:
  - commands/handlers/assets/linear-relations.py # 334 lines
  - commands/handlers/assets/linear-ready.py # 310 lines
  - commands/handlers/assets/linear-scan.py # 255 lines
  - commands/handlers/assets/linear-archive.py # 206 lines
tags: [python, toolchain, linear, consumer-facing]
---

← [[py_toolchain_plan]]

## Context

The four Linear handler assets are **1,105 lines of consumer-facing runtime code** — the
sharpest end of the existing Python surface. They run in a user's session, not in CI, so a
defect here fails in front of someone. And they consume **dynamically-shaped JSON from the
Linear GraphQL API**, which is precisely the territory where types earn their keep and where
untyped Python quietly does the wrong thing with a `None` or a renamed field.

This is why they are first: they are the highest-risk, highest-payoff group.

Task 1 has landed the gate with these files suppressed. This task removes their suppressions
and fixes what falls out.

## Task

- Remove the per-file suppressions for the four `linear-*.py` assets.
- Annotate and fix until `pyrefly` (strict) and `ruff` pass clean.
- Model the **API response shapes** explicitly (`TypedDict`, or dataclasses at the parse
  boundary) rather than passing raw `dict[str, Any]` through the call graph. The whole value of
  this task is turning "the API might not have that field" from a runtime `KeyError` into a
  type error. If the annotations bottom out in `Any` at every boundary, the task has not
  actually been done.
- Where a field is genuinely optional in the API, model it as optional and handle it — do not
  paper over it with a cast.
- **Do not change behavior.** This is annotation and type-correctness work. If a real bug
  surfaces (and it plausibly will — that is the point), **file it with `/add-task` and leave it
  alone**; fixing it here makes the diff unreviewable and conflates two kinds of change.

## Acceptance Criteria

**Code-enforced:**

- `pyrefly` (strict) and `ruff` pass with **zero suppressions** for all four files.
- No new `Any`/`cast` escapes introduced at API boundaries beyond what is genuinely
  unavoidable; any that remain carry a one-line comment saying why.
- `just check` green; `just lint-py` still under its time budget.

**User-run:**

- Exercise each handler against real Linear data (`/list-tasks`, `/reoptimize-tasks`, or the
  handler's own entry point) and confirm identical behavior to before the change. Type
  annotations are erased at runtime, so a green checker proves nothing about behavior — this
  step is the only thing that does.
- Any bug found during the port is filed as a task, not silently fixed.
