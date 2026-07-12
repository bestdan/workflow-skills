---
title: "auto-pilot SKILL.md is at its 500-line cap — cut before the next change has to"
priority: medium
size: 2
status: new
created: 2026-07-12
source_branch: main
related_files:
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/
parent: autopilot_hardening
tags: [auto-pilot, docs, tech-debt, p2]
---

[[autopilot_hardening_plan]]

## Context

`skills/auto-pilot/SKILL.md` has a hard 500-line body cap. After tasks 15 and 16 merged, `main` sat at **497**. Task 14 needed to add 17 lines and had **3** of headroom, so it compressed its **own** prose to fit — merging the HEAD-invariant paragraph into the doctor paragraph (defensible: invariant 1 *is* that assertion) and landing at exactly 500.

That worked once. It does not work again. **The file is full**, and the next auto-pilot change must delete before it can add — under deadline, in a PR about something else, by an author who did not write the paragraph they are cutting. That is how a doc silently loses the one sentence that mattered.

The cap itself is right (SKILL.md is loaded into context; it must stay lean). The problem is that nothing forces the eviction decision to be made **deliberately**, so it gets made **incidentally** by whoever is unlucky.

## Task

- Move content that belongs in `references/` out of `SKILL.md`. The skill body should say what the loop *does* and when to read which reference — not restate the reference. Candidates: the detailed exit-contract prose, the invariant table (`run-state.md` owns it), and the budget/pause mechanics (`run-budget.md` owns them).
- Target meaningful headroom (~420–450 lines), not 499.
- Verify that nothing evicted is *only* stated in `SKILL.md` — if a line is load-bearing and lives nowhere else, it moves to a reference, it does not disappear.
- Consider making the cap enforced with a **budget warning** (e.g. `check.sh` warns past 450), so the squeeze is visible before it is urgent.

## Acceptance Criteria

- `SKILL.md` body is comfortably under the cap with room for the next several changes.
- Every claim removed from `SKILL.md` is present in a reference, and `SKILL.md` points at it.
- No behavior change; the skill still describes the loop accurately end to end.
- `bash scripts/check.sh` green (including the line-cap check).
