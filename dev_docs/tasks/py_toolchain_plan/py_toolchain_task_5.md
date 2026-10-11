---
title: Graduate to dev_docs/python-toolchain.md and delete the plan folder
priority: low
size: 2
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: py_toolchain
is_blocked_by: py_toolchain_task_4
related_files:
  - dev_docs/tasks/py_toolchain_plan/
  - dev_docs/python-toolchain.md # to be created
  - dev_docs/decisions/script_language.md
tags: [python, toolchain, docs, cleanup]
---

← [[py_toolchain_plan]]

## Context

`dev_docs/tasks/<name>_plan/` is temporary scaffolding. Once the toolchain is adopted, the plan
folder is residue — but the conventions it established are exactly what the next contributor
(or agent) writing Python here needs, and they must not be deleted with it.

Note the division of labour: [`dev_docs/decisions/script_language.md`](../../decisions/script_language.md)
records **why** the toolchain is what it is (and why not TypeScript or Go). That doc stays and
should not be duplicated. This new doc records **how to work within it**.

## Task

Write `dev_docs/python-toolchain.md` covering the practical conventions:

- **How to run the gate**: `just lint-py` for the fast inner loop, `just check` for the full
  one. State the time budget and why it exists (a gate slower than ~500 ms stops being usable
  in an agent's edit→check loop, which is half the reason it was adopted).
- **The house style**: PEP 723 inline metadata + `uv`, hash-locked; `TypedDict` at API/parse
  boundaries; `Literal` + `match` + `assert_never` for closed state sets, with a worked example
  — this is the pattern the whole decision rests on and it should be copy-pasteable.
- **The `pyrefly` config-silence trap**: a misconfigured `pyrefly` checks *nothing* and reports
  `0 errors`. Document the guard test that proves the checker is live, and say plainly that a
  green result from an unproven config means nothing.
- **What stays in bash and why** (pointing at the decision doc, not restating it).
- Any bug found-but-not-fixed during tasks 2–3 — confirm each was filed as a task rather than
  lost.

Then delete `dev_docs/tasks/py_toolchain_plan/`.

## Acceptance Criteria

**Code-enforced:**

- `dev_docs/python-toolchain.md` exists and covers: running the gate, the house style with a
  worked `assert_never` example, the config-silence trap, and the bash boundary.
- `dev_docs/tasks/py_toolchain_plan/` no longer exists.
- Every bug deferred during tasks 2–3 exists as a task.
- `just check` green (`validate.py` enforces task-file frontmatter under `dev_docs/tasks/`, so a
  half-deleted plan folder fails the gate).
