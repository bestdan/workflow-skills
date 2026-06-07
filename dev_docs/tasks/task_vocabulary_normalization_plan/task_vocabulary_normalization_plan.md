# Task Vocabulary Normalization — Plan

> **Historical record.** This plan drove PR #18 and is complete — every task landed in that PR (their files carry `status: done`). It is kept as a worked example of the task/plan format, not an active backlog, so `/promote-tasks` and `/process-tasks` skip it.

## Goal

Unify the plugin on a single noun — **task** — for "one PR-sized unit of work." Today the repo has two systems that both look like "todos": the kanban-style **todo-loop** (`skills/todo/`, `/add-todo` … `dev_docs/todos/`) and **plan-with-docs** (which writes `step_N.md` files into the colliding `dev_docs/todo/`). Rename the whole todo-loop to "task" vocabulary, fold plan-with-docs into the same `dev_docs/tasks/` tree (so plan output flows through `/promote-tasks` and `/process-tasks`), and normalize the **sizing** language to one canonical definition.

## Scope / non-goals

In scope:

- Clean hard rename of the todo-loop: skill, six slash commands, 13 handlers, config file, on-disk path, branch namespaces, PR label, manifests, evals, README/CONTRIBUTING.
- plan-with-docs: output unit `step` → `task`, output dir → shared `dev_docs/tasks/<name>_plan/`, emit canonical task frontmatter, tasks born `status: new`.
- One canonical **task size** definition and one **legacy-migration** procedure, both defined once in `skills/task/SKILL.md` and referenced elsewhere.

Non-goals:

- No backward-compat aliases for old command names or `.todo-config.yml` (clean hard rename). The only concession to existing repos is a **one-time migration prompt** (Task 5/Task 1), not dual code paths.
- No change to the _behavior_ of promote/process/claim/list beyond the rename and the new task source (plan-with-docs).
- Not renaming the `plan-with-docs` skill itself (only its vocabulary/output).

## Approach

Rename in dependency order so each PR references already-known new names (all new names are deterministic, so a referencing file can point at a new name before its target PR lands; the only cost of stopping mid-sequence is stale prose, never a broken CI build — `validate.py` does not link-check). Source of truth first (`skills/task/SKILL.md`, which also houses the canonical **task size** and **legacy-migration** sections), then the command surface, then handlers, then plan-with-docs integration, then the docs/manifests/evals sweep.

`"step"` is retained **only** as a procedural word ("step 1, step 2" inside skill instructions). It is removed as a _unit-of-work_ noun. Do not rewrite procedural "step" usages in other skills (analysis-pipeline, co-review).

## Tasks

1. [[task_vocabulary_normalization_task_1]] — Rename the task skill (source of truth); add canonical **Task size** + **Legacy migration** sections.
2. [[task_vocabulary_normalization_task_2]] — Rename the six slash commands (`/add-todo` → `/add-task`, etc.).
3. [[task_vocabulary_normalization_task_3]] — Rename handler content, part 1: `repo-pr*`, `gh-issue*`, `mcp-setup-offer`.
4. [[task_vocabulary_normalization_task_4]] — Rename handler content, part 2: `jira*`, `linear*`.
5. [[task_vocabulary_normalization_task_5]] — Fold plan-with-docs into the task system + migrate the in-repo example plan + add legacy-migration preflight.
6. [[task_vocabulary_normalization_task_6]] — Consistency sweep: README, CONTRIBUTING, manifests (+ version bump), evals, validator comment; final no-stray-`todo` check.

## Decisions (resolved)

- **Command number scheme** (Task 2): mixed — singular for one-card ops (`/add-task`, `/claim-task`), plural for board/batch ops (`/promote-tasks`, `/process-tasks`, `/list-tasks`), plus `/task-config`.
- **plan-with-docs task status** (Task 5): tasks born `status: new` so dependency/blocking relationships are evaluated as they flow through `/promote-tasks`.
- **Example plan** (Task 5): fully converted to the canonical task format (not just moved), so the repo models the convention accurately.
- **Version bump** (Task 6): `0.7.0` → `1.0.0` (major — breaking rename of every command name).
- **This plan's home**: moved to `dev_docs/tasks/task_vocabulary_normalization_plan/` as part of Task 5.
- **Size field formalized** (cross-cutting; discovered during Task 2): `size` becomes a required Fibonacci story-point field (`1` / `2` / `3` / `5`); `> 5` ⇒ split into sub-tasks. Defined canonically in `skills/task/SKILL.md` **Task size** (Task 1, amended), drafted by `/add-task` and gated by `/promote-tasks` (Task 2), surfaced by `/list-tasks` (Task 2), mapped to Linear's native `estimate` in the linear handler (Task 4), and estimated per task by `plan-with-docs` (Task 5). Replaces the prior undocumented `size: small/medium/large` field.
