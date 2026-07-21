---
title: "ops: create the dedicated spike test repository — a harmless target for probe fixtures"
priority: high
size: 1
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by:
parent: elite_stage0
tags: [e-lite, spike, stage-0, ops, user-run]
---

Plan: [[elite_stage0_plan]]

## Context

§0a: the spike runs from a disposable directory and a **dedicated test repository** — probe 1 dispatches a worker against it, probe 2's tmux fixtures want a harmless checkout, and Stage 1's probe 4 (GitHub authority canary, planned later) will install the disposable test App on it. No production repo is ever a spike target.

## Task

- Create `bestdan/autopilot-spike-target` (or similar): private, trivial contents (README + a small script + a test), no branch protection initially (probe 4 adds rulesets later, per its own kill sheet).
- Clone it under `/Users/agent/work/` once the agent user exists — but repo creation itself has no dependency and can happen first.
- Record the repo name in the spike evidence directory (`dev_docs/elite-spike/`).

## Acceptance Criteria

- **User-run:** repo exists and is cloneable; name recorded in `dev_docs/elite-spike/`; contains enough content for a worker to make a trivial verifiable edit.
