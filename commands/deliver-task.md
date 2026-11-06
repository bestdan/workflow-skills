---
description: Deliver ONE task through its full per-task lifecycle — claim, implement via a routed coder worker, verify, open a PR, run non-interactive co-review, iterate, hand off at needs_review, and land the PR when its merge tier is auto and AGENT_LAND is on (unmerged work is never completed)
allowed-tools: Bash, Glob, Grep, Read, Write, Edit, Agent, AskUserQuestion, Skill, mcp__linear, mcp__claude_ai_Linear, mcp__atlassian, mcp__claude_ai_Atlassian
argument-hint: "<slug | identifier> [--base <branch>] [--questions <path>] [--run-state <RUN.md>] [--handler <h>] [--no-land]"
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
  calls to; omit standalone (they ride out in the hand-off summary). When passed,
  a deferred finding that is cross-cutting or still open at the 2-round bound is
  also filed via `/add-task` (see the skill's "Iterate (bounded)").
- `--run-state <RUN.md>` — transparent auto-pilot pass-through for its durable
  run state; omit for standalone delivery.
- `--handler <h>` — override handler resolution (`repo-pr | linear | gh-issue |
  jira`); omit to resolve from `.task-config.yml` as today. The `/auto-pilot`
  orchestrator passes its run's effective handler.
- `--no-land` — run only the shadow lander at hand-off, never the real one. The
  `/auto-pilot` orchestrator passes it for a task another task in the run is
  stacked on, so landing cannot delete a branch a later child is based on.

The lifecycle is claim → do → open PR → co-review → iterate → hand-off → land,
ending at `needs_review` unless the lander merges an `auto`-tier PR (only while
the repo's `AGENT_LAND` is `on`). Unmerged work is **never** `done`: completion
follows the lander's merge, or is merge-verified later via `/sweep-for-complete`.

This command **creates and removes its own worker worktree** for the
implementation (the "Do" step's dispatch-in-isolation-then-integrate, via
`orchestrate-coders`) — it never touches the caller's own worktree. Under
`/auto-pilot`, that caller is the run worktree, whose `HEAD` must stay on the
run-state branch for the entire run
(`skills/auto-pilot/references/run-state.md` "Run worktree HEAD invariant");
this command's worker worktree is what keeps task work off that branch.
