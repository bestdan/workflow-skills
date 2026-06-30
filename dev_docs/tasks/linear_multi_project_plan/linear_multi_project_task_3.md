---
title: /do-tasks multi-project execution — candidate query + per-project & global WIP
priority: high
size: 5
status: new
created: 2026-06-30
source_branch: main
related_files:
  - commands/handlers/linear-claim.md # Find candidates query (~L24-49); per-project projectId scoping
  - commands/do-tasks.md # §3 Tracker path: pre-claim WIP gate, --all batch slack, --project flag
is_blocked_by: [linear_multi_project_task_1]
parent: linear_multi_project
tags: [linear, do-tasks, wip]
---

> Plan: [[linear_multi_project_plan]]

## Context

The core execution path. Today `linear-claim.md` "Find candidates" passes a single
`projectId` (or omits it for whole-team), and `do-tasks.md` §3 "Pre-claim WIP gate"
counts in-flight once against one scope. This task makes both iterate the **resolved
projects list** (Task 1's helper) and enforce **per-project** WIP plus the optional
**global ceiling**.

This is the meatiest task (size 5) because the two files are interdependent: the claim's
candidate query and the do-tasks WIP gate share the per-project counting. Keep it one PR
so there's no awkward half-migrated intermediate state.

Design refs: spec §"Selection semantics" (`/do-tasks` rows), §"Per-project WIP enforcement".

## Task

In `commands/handlers/linear-claim.md` "Find candidates":

- Query candidates across **all** configured projects (loop the `list_issues` call per
  `projectId`, or pass the set if the tool accepts it), tagging each candidate with its
  source project. Whole-team synthetic scope (projects absent) behaves as today.
- Ranking still merges across projects; carry each candidate's `project` (id + name +
  resolved `wip_limit`) through so the gate below can check the right cap.

In `commands/do-tasks.md` §3 "Pre-claim WIP gate" / "Claim and execute":

- **Per-project counting:** for the chosen candidate, count in-flight in **its** project
  and compare to **that project's** `wip_limit`. If full, skip to the next ranked
  candidate (possibly in another project) rather than declining outright.
- **`--all` batch:** dispatch up to `Σ max(0, slack_p)` across projects, no project over
  its own cap; held-overflow report names each held task's project.
- **Global ceiling:** if `linear.global_wip_limit` is set, also require
  `Σ in_flight < global_wip_limit` before any claim; clamp `--all` to remaining global
  slack. Decline messages gain the project name / global form per spec.

> **`--project X` narrowing is deferred to a follow-up** (planning decision) — v1 always
> operates across all configured projects. Don't build the flag here.

## Acceptance Criteria

- **Code-enforced:** `just check` passes. `rg` confirms `do-tasks.md` §3 and
  `linear-claim.md` no longer reference a single `default_project` for scoping (they go
  through the resolved list / per-candidate project).
- **User-run:** with a 2-project test config (A `wip_limit: 1`, B `wip_limit: 2`), trace
  `/do-tasks --all` by hand against a known board and confirm: A dispatches ≤1, B ≤2,
  a full project is skipped not declined, and (with `global_wip_limit: 2`) the run stops
  at 2 total even though per-project slack would allow 3.
