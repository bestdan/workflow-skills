---
name: auto-pilot
description: Unattended autonomous mode — "pick up this Project and grind on it overnight." Runs a task graph (a Linear project or a plan-with-docs directory) task-by-task in an isolated worktree, taking each through /deliver-task (claim → implement → PR → co-review → hand-off) with durable, crash-resumable state and no human in the loop. Use when the user wants a body of work advanced autonomously and unattended. NOTE - v1 is under construction; this entry establishes the skill home and the run-state reference. Launch, run, and resume flows land in later tasks.
---

# auto-pilot — unattended autonomous runs

"Claude, pick up this Project and grind on it overnight."

Auto-pilot advances a whole task graph without a human in the loop: an isolated
worktree, a thin orchestrator that walks the graph, and `/deliver-task` per task
(claim → implement → PR → co-review → iterate → hand-off). It composes existing,
battle-tested skills and handler protocols rather than duplicating them.

Design: [`../../dev_docs/tasks/auto-pilot-mode-design.md`](../../dev_docs/tasks/auto-pilot-mode-design.md).

> **Status:** v1 is being built. This SKILL.md establishes the skill home and
> the run-state reference below. The interactive **launch** phase, the unattended
> **run** loop, and **`--resume`** are added by the remaining plan tasks; until
> then this skill is not yet invocable end-to-end.

## References

- [`references/run-state.md`](references/run-state.md) — the canonical formats
  and invariants for a run's durable state: the three run files
  (`RUN.md` / `QUESTIONS.md` / `MORNING.md`), the seven task lifecycle phases,
  the dedicated run-state branch convention, the fixed write order
  (push code → update tracker → commit run state), and the crash-reconciliation
  table `--resume` uses. Launch, run, and resume all read this — no other
  document restates a run-state format.
