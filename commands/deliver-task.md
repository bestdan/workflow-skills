---
description: Deliver ONE task through its per-task lifecycle — claim, implement via a routed coder worker, and verify — leaving a verified diff on a task branch with evidence captured (the claim→do half; PR/co-review/hand-off are the review half)
allowed-tools: Bash, Glob, Grep, Read, Write, Edit, Agent, AskUserQuestion, Skill, mcp__linear, mcp__claude_ai_Linear, mcp__atlassian, mcp__claude_ai_Atlassian
argument-hint: "<slug | identifier> [--base <branch>]"
---

# Deliver Task

Invoke the **deliver-task** skill with the arguments as given. The skill
(`skills/deliver-task/SKILL.md`) owns all behavior — handler resolution, the
handler's own claim protocol (referenced, never restated), worker routing via
`select-coder`, isolated dispatch + integration via `orchestrate-coders`,
base-freshness, verification, and the seam where it stops. This command adds
nothing beyond routing; do not re-derive or restate the workflow here.

- `<slug | identifier>` — the one task to deliver (a file slug for `repo-pr`, or a
  tracker id like `PRE-12`). Required — deliver-task never selects a task.
- `--base <branch>` — the branch the work branch is based on (default `main`); the
  `/auto-pilot` orchestrator passes a parent's frozen tip for a stacked task.

Scope is claim → do, ending at "verified diff on the task branch, evidence
captured." PR creation, `/co-review`, iterate, and hand-off are the review half
and are out of scope for this command.
