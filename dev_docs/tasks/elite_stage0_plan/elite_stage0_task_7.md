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

Per the kill sheet (`dev_docs/elite-spike/kill-sheet.md`), after [[elite_stage0_task_3]] establishes the agent's Max OAuth:

- **Do not mint a new session** — use the exact test session probe 1 already ran and recorded the UUID for in `dev_docs/elite-spike/measurements.md` ([[elite_stage0_task_3]]); this probe is read-only (§0a).
- Query session-window utilization + `reset_epoch` for that session as each identity: maintainer — run `scripts/claude-usage.sh` from your own shell; agent — run the same script from an interactive agent shell (`sudo -u agent -i`, per the plan's agent-access note; no new sudoers).
- Calibrate the pass threshold first: run `scripts/claude-usage.sh` twice back-to-back under the maintainer credential and record the drift between those two runs as the **control delta**.
- Compare agent vs maintainer: identical window boundaries and `reset_epoch`; utilization values equal, or differing by no more than the control delta. Record the control measurement in the evidence.
- Also verify the §2.3 canary item: the maintainer-side credential path actually resolves (Keychain vs `~/.claude`) — record which.
- Close per the plan's probe close protocol.

## Acceptance Criteria

- **User-run:** side-by-side query outputs plus the control measurement checked into `dev_docs/elite-spike/measurements.md`, sanitized per the plan checklist (window percentages, boundaries, and `reset_epoch` kept in the clear); probe closed as `confirmed` / `falsified` / `inconclusive` per the plan's probe close protocol.
- The measurement row names which maintainer-side credential path resolved (Keychain or `~/.claude`).
- On `falsified`: the measurement row records "automatic continuation deleted from build order" so the measured revision ([[elite_stage0_task_8]]) drops §5.3 Stage-5 continuation from the plannable path.
- On load-bearing `inconclusive`: continuation stays **disabled and deferred** — it may not be planned or built until a changed kill sheet names discriminating evidence and a rerun confirms coherence (§7a rule 6). Inconclusive never admits continuation.
