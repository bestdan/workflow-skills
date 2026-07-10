---
description: Deliver ONE task through its full per-task lifecycle — claim, implement via a routed coder worker, verify, open a PR, run non-interactive co-review, iterate, and hand off at needs_review (never completed)
allowed-tools: Bash, Glob, Grep, Read, Write, Edit, Agent, AskUserQuestion, Skill, mcp__linear, mcp__claude_ai_Linear, mcp__atlassian, mcp__claude_ai_Atlassian
argument-hint: "<slug | identifier> [--base <branch>] [--questions <path>] [--handler <h>]"
---

# Deliver Task

Invoke the **deliver-task** skill with the arguments as given. The skill
(`skills/deliver-task/SKILL.md`) owns all behavior — handler resolution, the
handler's own claim protocol (referenced, never restated), worker routing via
`select-coder`, isolated dispatch + integration via `orchestrate-coders`,
base-freshness, verification, PR creation, `/co-review --non-interactive`,
bounded iterate, and hand-off at `needs_review`. This command adds nothing beyond
routing; do not re-derive or restate the workflow here.

- `<slug | identifier>` — the one task to deliver (a file slug for `repo-pr`, or a
  tracker id like `PRE-12`). Required — deliver-task never selects a task.
- `--base <branch>` — the branch the work branch is based on (default `main`); the
  `/auto-pilot` orchestrator passes a parent's frozen tip for a stacked task.
- `--questions <path>` — a `QUESTIONS.md` decision log to append deferred judgment
  calls to; omit standalone (they ride out in the hand-off summary).
- `--handler <h>` — override handler resolution (`repo-pr | linear | gh-issue |
  jira`); omit to resolve from `.task-config.yml` as today. The `/auto-pilot`
  orchestrator passes its run's effective handler.

The lifecycle is claim → do → open PR → co-review → iterate → hand-off, ending at
`needs_review` — **never** `done` (completion is merge-verified later via
`/sweep-for-complete`).
