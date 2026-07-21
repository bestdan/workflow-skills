---
title: "spike: fold probe results into the measured design revision + re-plan checkpoint (Stage 1, probe 5)"
priority: urgent
size: 5
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: [elite_stage0_task_3, elite_stage0_task_5, elite_stage0_task_6, elite_stage0_task_7]
parent: elite_stage0
tags: [e-lite, spike, stage-0, design, re-plan]
---

Plan: [[elite_stage0_plan]]

## Context

Design §0a + §7 Stage 0 gate. The spike's deliverable is not code — it's a **measured revision** of the design that turns empirical results into completed contracts. This task is also the deliberate **re-plan checkpoint**: Stage 1+, probe 4 (GitHub authority canary), and probe 5 (crash-transaction kernel) were intentionally not pre-planned, because probe outcomes can delete or redirect that work.

Prerequisite state: every started tranche-1 probe closed as `confirmed` / `falsified` / `inconclusive`; `measurements.md` complete for every baseline empirical question Stage 2 needs.

## Task

- Verify the Stage-0 gate: every baseline load-bearing question classified; any `falsified`/`inconclusive` result mapped to its named redirect or a deferred feature (a blocked dependent path is recorded, not worked around).
- Write the measured revision (update `dev_docs/auto-pilot-e-lite-design-2026-07-21.md` or a successor doc) completing, from evidence:
  - the trusted run-manifest interface (§4.1),
  - the launch/lease state machine with atomic prepared/active/terminal transitions and the **rollback table** for every prepared-to-active boundary,
  - the incarnation-identity definition selected by the topology + binding probes,
  - the baseline registry JSON schema (§4.2, published before any Stage-2 code),
  - the run-shim implementation choice,
  - continuation kept or deleted per the coherence probe.
- Draft the baseline launch/lease/registry state machine document that probe 5 (crash-transaction kernel) will falsify — probe 5's input per §7a.
- **Re-plan:** run `/plan-with-docs` (append or new plan) for the next tranche: Stage 1 (broker, disposable test App, probe 4, rulesets) and probe 5, using the measured revision as source.

## Acceptance Criteria

- **Code-enforced:** `dprint check` passes on the revised docs.
- Measured revision PR merged, containing all five contract completions above with explicit links to measurement rows as evidence.
- Every probe has a terminal classification; no probe is carried as open work into the next tranche without a changed kill sheet (§7a rule 6).
- The next-tranche plan exists (or an explicit decision that the architecture is redirected/stopped).
