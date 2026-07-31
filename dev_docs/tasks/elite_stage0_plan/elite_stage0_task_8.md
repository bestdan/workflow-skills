---
title: "spike: fold probe results into the measured design revision"
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

Design §0a + §7 Stage 0 gate. The spike's deliverable is not code — it's a **measured revision** of the design that turns empirical results into completed contracts. Re-planning (Stage 1, probe 4, probe 5) is deliberately **not** part of this task — it lives in [[elite_stage0_task_11]], gated on this revision being reviewed and merged, so an unapproved or falsification-laden revision cannot leak into downstream planning.

Prerequisite state: every started tranche-1 probe closed as `confirmed` / `falsified` / `inconclusive`; `measurements.md` complete for every baseline empirical question Stage 2 needs.

## Task

- Verify the Stage-0 gate: every baseline load-bearing question classified; any `falsified`/`inconclusive` result mapped to its named redirect or a deferred feature (a blocked dependent path is recorded, not worked around).
- Write the measured revision (update `dev_docs/auto-pilot-e-lite-design-2026-07-21.md` or a successor doc) covering, from evidence, the full §0a design-choice list:
  - the trusted run-manifest interface (§4.1),
  - the launch/lease state machine with atomic prepared/active/terminal transitions, lease-generation replacement, and the **rollback table** for every prepared-to-active boundary,
  - the incarnation-identity definition selected by the topology + binding probes,
  - the baseline registry JSON schema (§4.2, published before any Stage-2 code),
  - **registration and teardown of the baseline in-tree worker topology**,
  - **the clock-skew fail-closed policy** (§2.1's 60-second rule, made concrete),
  - the run-shim implementation choice,
  - continuation: on a failed or inconclusive coherence probe, **deleted from the build order**; on a confirmed probe it stays **disabled and deferred to its own Stage-5 design + evidence gate** — a pass never makes it specified work in this revision.
- **Falsification handling:** a contract whose supporting probe was falsified or inconclusive is recorded as **blocked** with its redirect — never completed from assumption. The revision is valid with blocked contracts; it is invalid if it papers over one.
- Draft the baseline launch/lease/registry state machine document that probe 5 (crash-transaction kernel) will falsify — probe 5's input per §7a.

## Acceptance Criteria

- **Code-enforced:** `dprint check` passes on the revised docs.
- Measured revision PR merged, with each contract either completed (linked to its measurement rows as evidence) or explicitly blocked with its named redirect.
- Every probe has a terminal classification; no probe is carried as open work into the next tranche without a changed kill sheet (§7a rule 6).
