---
title: "spike: re-plan checkpoint — plan Stage 1 and probe 5 from the approved measured revision"
priority: urgent
size: 2
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: elite_stage0_task_8
parent: elite_stage0
tags: [e-lite, spike, stage-0, re-plan]
---

Plan: [[elite_stage0_plan]]

## Context

Split out of the measured-revision task ([[elite_stage0_task_8]]) so downstream planning is graph-gated on the revision being **reviewed and merged** — a falsified probe or an unapproved revision must not leak into Stage 1 planning. §0a: only an architectural redirect or the measured revision returns for review; this checkpoint runs after that review lands.

## Task

- Confirm the measured revision PR is merged and any blocked contracts carry their named redirects.
- Run `/plan-with-docs` (new plan or append) for the next tranche, using the measured revision as source:
  - **Stage 1**: broker, disposable test App, probe 4 (GitHub authority canary), rulesets, agent gitconfig — only the paths the revision leaves unblocked.
  - **Probe 5** (baseline crash-transaction kernel): input is the draft state machine the revision published; two-working-day cap per §7a.
  - If a load-bearing result was falsified: the next plan contains only independent work plus the named redirect — dependent features are deferred, not planned around.

## Acceptance Criteria

- Next-tranche plan exists under `dev_docs/tasks/` (or an explicit recorded decision that the architecture is redirected/stopped).
- No planned task depends on a contract the measured revision marked blocked.
