---
title: "probe: Max-window coherence — agent and maintainer usage queries agree on the exact same test session"
priority: high
size: 2
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
  - scripts/claude-usage.sh
is_blocked_by: elite_stage0_task_3
parent: elite_stage0
tags: [e-lite, spike, stage-0, probe, user-run]
---

Plan: [[elite_stage0_plan]]

## Context

Design §7a "two cheap probes" + §5.3 coherence prerequisite. Continuation (Stage 5+) is disabled unless the maintainer and agent credentials return the **same** session-window utilization and `reset_epoch` for one exact Claude test session — merely proving both queries succeed is insufficient. Failure deletes automatic continuation from the build order but does **not** block Stages 1–4's stop-and-notify baseline (i.e. a `falsified` here narrows scope, it doesn't stop the plan).

`claude-usage.sh` resolves the *invoking* user's credentials (Keychain or `~/.claude`), so the two sides genuinely exercise two credential paths. Read-only; consumes no meaningful usage.

## Task

Per the kill sheet, after [[elite_stage0_task_3]] establishes the agent's Max OAuth:

- Run one minimal, identifiable Claude test session as `agent`.
- Query session-window utilization + `reset_epoch` as `agent` (its own credential) and as the maintainer (observation credential) — `scripts/claude-usage.sh` from each identity.
- Compare: same window boundaries, same utilization (within one query's drift), same `reset_epoch`, attributable to the same subscription.
- Also verify the §2.3 canary item: the maintainer-side credential path actually resolves (Keychain vs `~/.claude`) — record which.
- Close against the kill sheet.

## Acceptance Criteria

- **User-run:** side-by-side query outputs (sanitized) checked into the measurement table; probe closed as `confirmed` / `falsified` / `inconclusive`.
- On `falsified`: the measurement row records "automatic continuation deleted from build order" so the measured revision ([[elite_stage0_task_8]]) drops §5.3 Stage-5 continuation from the plannable path.
