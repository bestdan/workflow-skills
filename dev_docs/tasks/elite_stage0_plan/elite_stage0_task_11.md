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

Split out of the measured-revision task ([[elite_stage0_task_8]]) so downstream planning is graph-gated on the revision being **reviewed and merged** — a falsified or load-bearing-inconclusive probe, or an unapproved revision, must not leak into Stage 1 planning. §0a: only an architectural redirect or the measured revision returns for review; this checkpoint runs after that review lands.

## Task

- Confirm the measured-revision PR (linked from [[elite_stage0_task_8]]'s close-out) is merged — `gh pr view <that PR> --json state,mergedAt` — and that any blocked contracts carry their named redirects, as listed in the measured revision's blocked-contracts list (the explicitly-blocked §0a design-choice contracts recorded by [[elite_stage0_task_8]]).
- Run `/plan-with-docs` for the next tranche, using the measured revision as source — either a new `dev_docs/tasks/elite_stage1_plan/` or an append to this plan; record which in the plan overview:
  - **Stage 1** (§7 Stage 1's full build list: maintainer-owned identity/token directory skeleton, App + rulesets, broker, token file, fixed `gh`/git helpers, agent gitconfig — plus the disposable test App and probe 4, the GitHub authority canary) — only the paths the revision leaves unblocked.
  - **Probe 5** (baseline crash-transaction kernel): input is the draft state machine the revision published; two-working-day cap per §7a.
  - If a load-bearing result was falsified or inconclusive: the next plan contains only independent work plus the named redirect (or the deferral, per §7a rule 5) — dependent features are deferred, not planned around.

## Acceptance Criteria

- The measured-revision PR (number recorded in [[elite_stage0_task_8]]'s close-out) is `MERGED` per `gh pr view <that PR> --json state` before any plan file is created.
- Next-tranche plan exists under `dev_docs/tasks/`, or a decision record exists at `dev_docs/elite-spike/replan-decision.md` stating redirected/stopped, the triggering probe result, and the named §7a redirect taken.
- If a next-tranche plan is produced: the probe-5 plan task states its input (the revision's draft launch/lease/registry state machine), its two-working-day cap (§7a row 5), and requires a pre-written kill-sheet row (falsifier, pass threshold, inconclusive condition, evidence required, time cap, dependent work, redirect — §7a rule 1) before any fixture.
- No planned task depends on a contract the measured revision marked blocked.
