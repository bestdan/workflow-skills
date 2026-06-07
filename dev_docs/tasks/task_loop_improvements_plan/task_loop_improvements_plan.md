# Task Loop Improvements — Plan

## Goal

Close the gap between the current task loop (a strong capture-and-execute kanban) and a fuller project-management process: objective prioritization, dependency depth, epic rollups, handler parity, and a single unified execute verb. Derived from the lifecycle review on 2026-06-07.

## Scope / non-goals

**In scope** (the reviewed recommendations, plus follow-ups added during planning):

- Task-format additions: `assignee`, `impact`, multi-value `is_blocked_by`, `parent`/`epic`.
- Value/effort ranking in selection and listing.
- WIP limit on batch dispatch.
- Handler capability warnings at config time.
- Model-judgment scope check in promotion.
- Epic hierarchy with rollup.
- Unified `/do-tasks` verb (single by default, `--all` batch, WIP-bounded); `/process-tasks` and `/claim-task` are **removed** in favor of it. Claim and execute are exposed as composable steps (`--claim-only` / `--no-claim`), and the batch path size-gates auto-execution so the headless runner drains small tasks while big ones are reserved for a human.
- Handler parity: Linear promote path + `/list-tasks` for gh-issue and jira.
- Plan→tracker sync, **local-first** (draft → vet → explicit push), scoped via a design spike first.
- A `/doctor` health command: explicit config/schema diagnostics and fixes, complementing (not replacing) migrate-on-contact.

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
4. [[task_4]] — Epic hierarchy: first-class `type: epic` file + `parent` field + rollup in `/list-tasks`. (after 1)
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
15. [[task_15]] — Add `--claim-only` / `--no-claim` flags to `/do-tasks` (claim and do as composable steps). (after 11)
16. [[task_16]] — Size-gate `/do-tasks --all` auto-routing: headless-execute small tasks, reserve big ones for a human. (after 15)
17. [[task_17]] — `/doctor` config + schema health command (validate handler/prereqs, detect schema drift, delegate legacy migration, hygiene). (after 1)

> **Dependency-slug caveat.** Ordering is encoded with `is_blocked_by` pointing at task **filename stems** (`task_1`, `task_5`, …), because `commands/process-tasks.md` resolves blockers by bare slug **globally** across `dev_docs/tasks/**/*.md`. Those stems are not unique across coexisting plans (e.g. `evals_and_ci_plan/` also has `task_1`…`task_5`), so process this plan in isolation, or keep task slugs unique across in-flight plans, until [[task_3]] (multi-blocker semantics) or a future change makes resolution path-aware.

## Resolved decisions

- **`impact` scale (Rec 1):** Fibonacci `1`/`2`/`3`/`5`, mirroring `size` and Linear's Fibonacci `estimate`; rank by `impact/size`. Linear has no native value/impact field, so value/effort ranking is file-handler-only — the Linear handler keeps ranking by `priority` + `estimate`. (See [[task_2]].)
- **WIP default (Rec 2):** cap concurrent in-flight work at **3**, configurable in `.task-config.yml`. (See [[task_5]].)
- **Old execute commands (Rec 9):** `/process-tasks` and `/claim-task` are **removed immediately** in favor of `/do-tasks` — no alias/deprecation period. (See [[task_12]].)
- **Epic identity (Rec 7):** a **first-class epic file** with its own frontmatter (`type: epic`, title, status, owner), not just a directory name. Promote/execute scans must skip `type: epic` files. (See [[task_4]].)
- **Claim/do separation (added 2026-06-07):** keep claim and execute as **composable steps**, not separate commands — `/do-tasks` stays the single verb, with `--claim-only` (reserve, don't execute) and `--no-claim` (execute an already-claimed task) flags. Default remains atomic claim+do. The standalone-claim primitive is gated behind an actual human-in-the-loop need rather than built speculatively. (See [[task_15]], [[task_16]].)
- **`/doctor` framing (added 2026-06-07):** a health/diagnostics command (config validity, handler prerequisites, schema drift, hygiene), **not** a migration wrapper — legacy migration is one check it delegates to the existing `skills/task/SKILL.md` procedure. It complements migrate-on-contact (the implicit auto-heal) rather than replacing it, so stale setups still work without invoking it. Deferred until after [[task_1]], when schema additions give the drift check real work. (See [[task_17]].)
