---
title: Replace keyword scope-check in /promote-tasks with model judgment
priority: medium
size: 2
status: in_progress
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/promote-tasks.md
  - skills/task/SKILL.md
tags:
  - task-loop
  - promotion
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]].

## Context

The promotion confidence check (`commands/promote-tasks.md` step 2; mirrored in `skills/task/SKILL.md` "Confidence check") blocks tasks whose title/body contain literal scope keywords (`refactor`, `migrate`, `redesign`, `rewrite`, `overhaul`). This is brittle: "Restructure the auth module" passes, "Migrate one constant" fails. The promoter already runs inside an agent, so it can judge scope directly.

Replace the keyword red-flag with a model-judgment gate: "does this task plausibly fit within size 5 (~300 lines / ~5 files)?" Keep every other deterministic check (required fields, acceptance criteria present, no open-questions section, priority ≠ urgent) — only the scope heuristic changes.

## Task

1. `commands/promote-tasks.md` step 2: remove the keyword-list bullet; add a judgment bullet — the promoter assesses whether the task's described scope fits the size budget (see **Task size**), considering the stated `size`, the Task steps, and `related_files` breadth. If it clearly exceeds size 5, score LOW with reason `scope exceeds size 5 — split into sub-tasks`.
2. Keep the `# promoter:` frontmatter comment behavior (name the failed check) for LOW scores.
3. `skills/task/SKILL.md` "Confidence check": update the scope bullet to describe the judgment gate instead of the keyword list.
4. Note in both files that this gate is judgment, not deterministic — acceptable because `/promote-tasks` is not a blocking CI gate.

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run:** A genuinely large task titled "Restructure the auth module" (multi-file, size would be >5) scores `needs_refinement` with a scope reason; a small task whose title merely contains the word "migrate" (e.g. "Migrate one constant to the new config key", size 1) scores `ready`. No keyword list remains in either file.
