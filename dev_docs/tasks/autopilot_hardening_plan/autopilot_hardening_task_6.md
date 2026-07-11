---
title: "docs: reference + SKILL corrections from run #1 (phase, paths, adapter, merge order)"
priority: 3
size: 3
status: new
created: 2026-07-10
source_branch: main
related_files:
  - skills/auto-pilot/references/run-state.md
  - skills/auto-pilot/references/adapters.md
  - skills/auto-pilot/SKILL.md
is_blocked_by:
parent: autopilot_hardening
tags: [auto-pilot, docs, p2, p3]
---

[[autopilot_hardening_plan]]

## Context

Documentation/reference corrections surfaced by run #1 (findings **P2 #9, #11** and
**P3 #12, #13, #14**). Doc-only — no code. Each is a small, specific edit to the
auto-pilot SKILL/references.

## Task

- **#11 — pre-claim phase (`run-state.md`).** The seven lifecycle phases start at
  `claimed`; a materialized-but-unclaimed task has no phase (I used an informal
  `pending`, and the earlier dry-run hit the same gap). Either add a `pending`/
  `ready` pre-claim marker to the phase table, or state that `phase` covers only
  in-flight tasks and graph readiness is computed separately.
- **#9 — helper-script paths (`SKILL.md` + references).** `probe-coders.sh`,
  `claude-usage.sh`, `preflight-freshness.sh`, `spawn-orchestrator.sh` are cited as
  if under `skills/auto-pilot/scripts/`; they live at **repo-root** `scripts/`.
  Make every reference point at the real location.
- **#12 — reservation PR for a plan source (`adapters.md` plan adapter).** State
  outright: a single-orchestrator plan run **skips** the repo-pr reservation
  `task-claim` PR (the code branch bases on `main`, where the task file doesn't
  exist, so there's nothing to seed it, and one serialized claimer has no race);
  the run-state branch is the lock; still do the pre-claim `gh` PR scan for resume
  idempotency. (This is what the orchestrator had to derive as decision Q5.)
- **#13 — scaffolding cleanup as run-level teardown (`SKILL.md` + `adapters.md`).**
  The plan-lifecycle "delete the `<name>_plan/` folder" step assumes the task file
  is on `main`; for an auto-pilot plan source it lives only on the working/run-state
  branch and a `main`-based code PR **cannot** delete it (task_6 of the last run hit
  this). Document scaffolding cleanup as a **run-level teardown** responsibility
  (on the run-state/working branch), not a graph task.
- **#14 — human-merge vs stacked bases (`run-state.md` + `SKILL.md`).** The
  freeze/`base_sha` park logic guards the *orchestrator* moving a base, not a
  *human* merging one mid-run. Add guidance that the run emit an explicit "merge the
  PRs bottom-up, in dependency order" note (with the ordering + one-line why), and
  document the human-merge/stacked-base interaction.

## Acceptance Criteria

**Code-enforced:**
- `bash scripts/check.sh` green (`dprint check` + `claude plugin validate --strict`
  cover the doc formatting/structure).

**User-run:**
- Read each edited section: the pre-claim phase is defined, every helper-script
  path resolves to `scripts/…` at repo root, the plan adapter states the no-
  reservation-PR rule, scaffolding cleanup is a teardown step, and the merge-order
  guidance is present.
