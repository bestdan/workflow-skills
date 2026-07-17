---
title: Close out Finding #5 — verify the shipped auto-pilot supervisor covers it
priority: medium
size: 2
status: ready
created: 2026-07-17
expires: 2026-08-16
source_branch: claude/sleepy-ride-8d4bjx
related_files:
  - dev_docs/tasks/deterministic-code-opportunity.md # Finding #5 + prioritized-recommendations list to update
  - scripts/spawn-orchestrator.sh # classify-exit / supervisor-check / supervisor-gate / supervisor-scan
  - scripts/claude-auto-resume.sh # rate-limit-wall relaunch/resume
  - scripts/claude-usage.sh # near-cap threshold + --session-status
  - skills/auto-pilot/references/run-budget.md # the supervisor/backoff/pause spec Finding #5 pointed at
parent: deterministic_scripts
impact: 2
tags: [docs, auto-pilot, audit]
---

# Task 5 — Close out Finding #5 (auto-pilot supervisor)

Part of [[deterministic_scripts_plan]]. **Docs / verification only — not a
build.** Independent of the other tasks.

## Context

The audit's Finding #5 said `scripts/auto-pilot-supervisor.sh` (relaunch wrapper
+ backoff + pause-marker) was "a required build when auto-pilot v1 lands." **It
has since landed** — the supervisor already exists, spread across the shipped
auto-pilot scripts:

- `scripts/spawn-orchestrator.sh` — `classify-exit` (exit-code + captured-output
  classification, no model call), `supervisor-check`, `supervisor-gate`, and
  `supervisor-scan` (`--park-limit`, `--pause-exempt-max`, `--report-every`;
  paused/continuing/done exit classes; `paused_until` handling and the
  pause-exempt ledger backstop). This **is** the outer relaunch/backoff
  supervisor Finding #5 described.
- `scripts/claude-auto-resume.sh` — owns the 5-hour rate-limit-wall: on process
  death it decides (via `claude-usage.sh --session-status`) whether the wall
  killed it, sleeps until reset, and resumes the same conversation.
- `scripts/claude-usage.sh` — already has the near-cap threshold comparison and
  `--session-status`, covering the small `--near-cap` add-on Finding #5 floated.

So Finding #5's recommendation is **delivered**, not open. This task records
that and closes the loop in the audit doc so the prioritized list doesn't keep
advertising a build that already exists.

## Task

1. Read `spawn-orchestrator.sh` (the `supervisor-*` / `classify-exit`
   subcommands), `claude-auto-resume.sh`, and `claude-usage.sh` against Finding
   #5's spec in `run-budget.md` (the outer supervisor: inspect exit codes,
   classify rate-limit errors, compute resume, exponential backoff 30m→1h→2h cap
   ~6h, consecutive-pause stop condition; plus the `--near-cap` add-on).
2. Confirm coverage point-by-point, and record any **genuine residual gap**
   (e.g. if the shipped backoff schedule differs from the 30m→1h→2h→cap the
   audit specified, or the consecutive-pause stop condition isn't present). If a
   real gap exists, file it as a **follow-up task** (chain via `is_blocked_by`)
   rather than expanding this one — keep this task docs-only.
3. Update `dev_docs/tasks/deterministic-code-opportunity.md`:
   - Finding #5 — mark **delivered**, naming the shipped scripts and the
     subcommands that satisfy it.
   - The **prioritized-recommendations** list (item 5) and the
     `## Update` status block — move #5 from "open/deferred" to "delivered."
4. No code changes unless step 2 surfaces a real residual gap (then it's a
   separate follow-up task, not this one).

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes (`dprint check` on the edited markdown).

**User-run:**

- Read the updated Finding #5 and confirm it accurately maps each element of the
  original recommendation to a shipped script/subcommand, and that any residual
  gap (if found) is either filed as a follow-up or explicitly stated as "none."
