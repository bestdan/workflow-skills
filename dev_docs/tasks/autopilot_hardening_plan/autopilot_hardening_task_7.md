---
title: "co-review: route cross-cutting deferred findings to a tracked follow-up task"
priority: 3
size: 3
status: new
created: 2026-07-10
source_branch: main
related_files:
  - skills/auto-pilot/SKILL.md
  - commands/deliver-task.md
  - commands/add-task.md
  - scripts/spawn-orchestrator.sh
  - skills/auto-pilot/references/run-state.md
is_blocked_by:
parent: autopilot_hardening
tags: [auto-pilot, co-review, p4]
---

[[autopilot_hardening_plan]]

## Context

Findings **P4 #15, #16**. Several co-review "medium" findings in run #1 (decisions
Q7 unbounded `attachments`, Q8 `duplicateOf` direction, Q9 unbounded nested
connections, Q10 vacuous smoke assertions) were correctly **deferred** — because
the faithful fix spans **every sibling script + the canonical spec in lockstep**,
not one task's diff, and/or the 2-round co-review bound was reached. Right call,
but such cross-cutting findings currently live only in `QUESTIONS.md`, where they
risk being lost. A cross-cutting finding is real work that should enter the backlog,
not just a decision-log footnote.

## Task

- In the run loop's non-blocking-decision handling (`SKILL.md` "Run phase" →
  "Non-blocking decisions", and the co-review disposition inside
  `commands/deliver-task.md`), when a deferred finding is judged **cross-cutting**
  (touches files/specs beyond the current task's diff) or is a **remaining finding
  at the 2-round bound**, additionally route it to a tracked follow-up via
  `/add-task` (the configured handler), not only a `QUESTIONS.md` entry.
- **Egress (load-bearing — the naive version is dead-on-arrival on a plan source).**
  `render_network_allowlist` only adds `api.linear.app` when `--source linear`; run
  #1 was a **plan** source, so an in-jail `/add-task` to Linear would be **denied by
  the run's own settings**. Pre-flight (task 5) must **resolve the add-task
  destination host** and include it in the egress allowlist (`render_settings`)
  regardless of source — otherwise the fallback path is the only path. (Light
  coupling to tasks 5 + the render-settings egress emitter.)
- **Guardrails against unattended spam** (Q7/Q8/Q9 were three findings against
  *one* shared spec — a naive per-finding filer creates near-duplicates): **dedupe
  within the run**, **cap** filed follow-ups per run, **tag** them `auto-pilot`, and
  rely on `/promote-tasks`' human-confidence gate (auto-filed tasks land in
  triage/new, never ready) to catch a hallucinated finding before it enters work.
- **REPORT.md "Follow-ups" ships as the index, not the alternative.** List every
  filed task there (with its id) *and* file it to the tracker — not either/or. The
  `QUESTIONS.md` entry links the created task id; `/add-task` failure falls back to
  a REPORT.md bullet only, never fatal.
- Define the **cross-cutting** test crisply (e.g. the fix would edit a file not in
  the task's `related_files`, or a spec block another consumer cites) so the
  orchestrator applies it deterministically, not by vibe.

## Acceptance Criteria

**Code-enforced:**
- `bash scripts/check.sh` green (doc/structure gates).
- If any behavioral eval harness covers the run loop, add a case asserting a
  cross-cutting deferral produces both a `QUESTIONS.md` entry and an `/add-task`
  call (or the documented fallback).

**User-run:**
- In a scratch run where co-review surfaces a cross-cutting medium, confirm a
  follow-up task is created in the backlog (or a `REPORT.md` "Follow-ups" bullet on
  `/add-task` failure) and the `QUESTIONS.md` entry references it.
