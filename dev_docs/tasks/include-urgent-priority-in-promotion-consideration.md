---
title: Reconsider treating urgent priority as eligible for auto-promotion
priority: medium
size: 2
impact: 2
status: done
created: 2026-06-07
source_branch: claude/refine-urgent-priority-promotion
related_files:
  - skills/task/SKILL.md
  - commands/promote-tasks.md
  - commands/handlers/linear-promote.md
tags:
  - task-loop
  - promotion
  - semantics
expires: 2026-07-07
---

## Context

The confidence check used by `/promote-tasks` currently scores any task with
`priority: urgent` as **LOW** — one of the HIGH conditions is `priority ≠ urgent`
(`commands/promote-tasks.md` step 2; canonical rule in `skills/task/SKILL.md`
"Confidence check"). The ranking note reinforces it: _"urgent is human-only, so
the auto-execute path never selects it, but it still sorts first in
`/list-tasks`."_ So an urgent task never auto-promotes to `ready` and never flows
into headless `/do-tasks` execution — it lands in `needs_refinement` with
`human_approval_requested` instead.

Raised during review of PR #36 (Linear promote path, which mirrors this same
`priority ≠ urgent` HIGH condition): **urgent should arguably be _included_ in
the consideration set, not excluded.** The current design reads "urgent" as
"escalate to a human," but an equally valid reading is "do this fastest, by
whatever capacity is available — including an agent." Excluding urgent from
auto-promotion means the highest-priority work is the _only_ tier that can never
be picked up autonomously, which is counterintuitive.

The real tension to resolve: auto-promoting urgent makes urgent tasks eligible
for **unattended** auto-execution. We need a rule that lets urgent be considered
without silently auto-claiming work that genuinely needs human eyes.

## Task

1. Decide the intended semantics of `urgent` and document the rationale in
   `skills/task/SKILL.md`. Options to weigh (pick one, record why):
   - **Include urgent in the consideration set** (the requested direction): drop
     the `priority ≠ urgent` HIGH condition so urgent tasks auto-promote like any
     other, relying on the remaining HIGH checks (acceptance criteria, scope,
     `human_approval_requested`, etc.) as the quality gate. Urgent then both
     sorts first _and_ is auto-executable.
   - **Include but gate**: urgent auto-promotes only when it independently passes
     the other HIGH checks AND lacks an explicit `human_approval_requested: true`
     — keeping the per-task human override as the escape hatch.
   - **Keep human-only** (status quo) — only if the review surfaces a concrete
     reason the requested direction is wrong; otherwise change it.
2. Update the canonical confidence check and the ranking note in
   `skills/task/SKILL.md` (the line currently asserting "urgent is human-only, so
   the auto-execute path never selects it").
3. Update the file path: `commands/promote-tasks.md` step 2 HIGH conditions.
4. Update the Linear path: `commands/handlers/linear-promote.md` step 6 (the
   `priority is set and is not urgent` HIGH check) so both handlers stay in sync.
   (Note: `linear-promote.md` lands via PR #36 — sequence this after that merges,
   or fold the change in if #36 is still open.)
5. Keep file path and Linear path semantically identical — the whole point of the
   shared confidence check is that both handlers score the same way.

## Acceptance Criteria

- `skills/task/SKILL.md` documents a single, explicit decision on whether urgent
  is auto-promotable, with rationale and the rejected alternative.
- The `priority ≠ urgent` HIGH condition in `commands/promote-tasks.md` and the
  equivalent check in `commands/handlers/linear-promote.md` match that decision
  and each other.
- The ranking note in `skills/task/SKILL.md` no longer contradicts the chosen
  behavior.
- `just check` passes.
