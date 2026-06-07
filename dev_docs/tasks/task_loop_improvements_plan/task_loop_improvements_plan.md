# Task Loop Improvements — Plan

## Goal

Close the gap between the current task loop (a strong capture-and-execute kanban) and a fuller project-management process: objective prioritization, dependency depth, epic rollups, handler parity, and a single unified execute verb. Derived from the lifecycle review on 2026-06-07.

## Scope / non-goals

**In scope** (9 of the 10 reviewed recommendations):

- Task-format additions: `assignee`, `impact`, multi-value `is_blocked_by`, `parent`/`epic`.
- Value/effort ranking in selection and listing.
- WIP limit on batch dispatch.
- Handler capability warnings at config time.
- Model-judgment scope check in promotion.
- Epic hierarchy with rollup.
- Unified `/do-tasks` verb (single by default, `--all` batch, WIP-bounded); `/process-tasks` and `/claim-task` are **removed** in favor of it.
- Handler parity: Linear promote path + `/list-tasks` for gh-issue and jira.
- Plan→tracker sync, **local-first** (draft → vet → explicit push), scoped via a design spike first.

**Non-goals:**

- Metrics / cycle-time / archive ledger (Rec 6 — explicitly skipped this round; the file-deletion-on-PR design stays as-is).
- Recurring/scheduled tasks, Slack/email intake, semantic dedup, SLA-by-priority (raised in review, deferred).

## Approach

- **Schema first.** One foundational task adds all new frontmatter fields and updates `scripts/validate.py` so every later behavior task builds on a stable format and the gate stays green. Behavior that _consumes_ each field lands as its own task.
- **Behavior changes land before the structural merge.** WIP cap (Rec 2) and multi-blocker semantics (Rec 3) ship against today's `/process-tasks`, then the `/do-tasks` merge inherits them — keeping the big refactor PR smaller.
- **`/do-tasks` is split into three** (file path → tracker path → removal of old commands + docs) so no single PR exceeds the size-5 budget.
- **Plan→tracker is a spike, not a guess.** The local-first-then-push round-trip has real design questions (when to push, how to reconcile edits made in the tracker, idempotency); a research task settles them before implementation.

Main tradeoff considered: bundling all format fields into one task (chosen — they all touch the same field-reference table, so splitting just creates merge conflicts) vs. one field per task (rejected).

## Tasks

1. [[task_1]] — Extend the canonical task frontmatter schema (`assignee`, `impact`, `parent`, list-valued `is_blocked_by`) + update validator.
2. [[task_2]] — Value/effort ranking in `/process-tasks` & `/list-tasks`; surface `assignee`. (after 1)
3. [[task_3]] — Multi-blocker dependency semantics across scan/list/process. (after 1)
4. [[task_4]] — Epic hierarchy: `parent` field + `_plan/` as epic + rollup in `/list-tasks`. (after 1)
5. [[task_5]] — WIP limit on batch dispatch in `/process-tasks --all`.
6. [[task_6]] — Handler capability warnings at `/task-config`.
7. [[task_7]] — Model-judgment scope check in `/promote-tasks`.
8. [[task_8]] — Linear promote path (`linear-promote.md`) + handler dispatch in `/promote-tasks`. (after 7)
9. [[task_9]] — `/list-tasks` support for gh-issue and jira handlers.
10. [[task_10]] — Introduce `/do-tasks` (file/repo-pr path; subsumes `/process-tasks`, WIP-bounded). (after 3, 5)
11. [[task_11]] — Add tracker dispatch to `/do-tasks` (subsumes `/claim-task`). (after 10)
12. [[task_12]] — Remove `/process-tasks` & `/claim-task` in favor of `/do-tasks` + docs/README. (after 10, 11)
13. [[task_13]] — Design spike: local-first plan→tracker sync.
14. [[task_14]] — Implement vetted-plan push to the configured handler. (after 13)

## Resolved decisions

- **`impact` scale (Rec 1):** Fibonacci `1`/`2`/`3`/`5`, mirroring `size` and Linear's Fibonacci `estimate`; rank by `impact/size`. Linear has no native value/impact field, so value/effort ranking is file-handler-only — the Linear handler keeps ranking by `priority` + `estimate`. (See [[task_2]].)
- **WIP default (Rec 2):** cap concurrent in-flight work at **3**, configurable in `.task-config.yml`. (See [[task_5]].)
- **Old execute commands (Rec 9):** `/process-tasks` and `/claim-task` are **removed immediately** in favor of `/do-tasks` — no alias/deprecation period. (See [[task_12]].)
- **Epic identity (Rec 7):** a **first-class epic file** with its own frontmatter (`type: epic`, title, status, owner), not just a directory name. Promote/execute scans must skip `type: epic` files. (See [[task_4]].)
