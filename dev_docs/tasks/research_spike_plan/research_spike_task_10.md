---
title: "research-spike: the two task-loop bridges, via receipt cards"
priority: medium
size: 3
status: done
created: 2026-08-01
completed: 2026-08-02
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - skills/research-spike/SKILL.md
  - skills/task/SKILL.md
  - skills/plan-with-docs/SKILL.md
  - commands/push-plan.md
is_blocked_by: research_spike_task_9
parent: research_spike
expires: 2026-08-31
tags: [research-spike, skill, integration]
---

[[research_spike_plan]]

## Context

The spike machinery **stands alone and works in any repo**. Where the
workflow-skills task loop is configured, two bridges exist — and both were
redesigned in review, because their first drafts violated the
destination-must-exist invariant. Tracker handlers return **URLs, not paths**;
and `/push-plan` **deletes** plan directories after migration, so an obligation
pointing at one rots on first push.

Both bridges therefore go through **receipt cards** (`kind: receipt`, landed in
task 3). The obligation's `destination` points at the card file — a path that
exists — while the external reference lives in card content the validator never
path-checks and, being offline by contract, could not verify anyway.

## Task

Extend `skills/research-spike/SKILL.md` (and `references/record-grammar.md`
where the field detail belongs):

- **`defer` → task loop.** When `dev_docs/tasks/.task-config.yml` is present,
  `defer` offers "`/add-task` this instead of creating a stub". On acceptance
  the procedure writes a `kind: receipt` card carrying `handler:`,
  `tracker_id:` and the returned `url:`, and points the obligation's
  `destination` at that card. Show the exact card and the exact obligation
  block, side by side.
- **`decided` → plan-with-docs.** A decided decision's `decided_in:` points at
  **durable** evidence: an ADR, a permanent design doc, or a receipt card
  recording the plan handoff. Spell out why a plan directory is not durable
  (`/push-plan` deletes it; task 5's validator rejects it) and note that a
  handoff receipt survives migration because the card is **updated** to the
  tracker URL rather than deleted.
- **No auto-promotion in either direction.** Promotion is an explicit act, for
  the same reason ledger rewriting is.
- **No reverse reconciliation in v1.** Completing a bridged task in the tracker
  does **not** discharge the local obligation; the ledger measures declared
  debt as last hand-updated. Say so under Known limits, and name the receipt
  cards as what makes a later `sweep` verb possible — walk receipts, check
  tracker state, propose discharges — while stating that verb is deliberately
  not in v1.
- Add a one-line pointer from `skills/task/SKILL.md` and
  `skills/plan-with-docs/SKILL.md` to the skill, so someone in the task loop
  discovers the bridge from the side they are standing on. Keep both edits to a
  sentence — these files are already near their budgets.

## Acceptance Criteria

**Code-enforced:**

- `bash scripts/check.sh` green, including the SKILL.md body-length cap after
  the additions (move detail into `references/record-grammar.md` if it pushes
  over).
- A fixture in `scripts/test-research-spike.sh` proves the bridge shape is
  valid: an obligation whose `destination` is a receipt card carrying a URL
  validates clean, and the same obligation pointing directly at a URL fails.

**User-run:**

- With `handler: linear` configured, run `defer` and choose `/add-task`;
  confirm the resulting receipt card carries the Linear id and URL, the
  obligation resolves to the card, and `validate` is clean.
- Confirm the docs nowhere imply that closing the Linear issue discharges the
  obligation.
