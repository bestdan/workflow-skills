---
description: Launch an unattended auto-pilot run — an interactive pre-flight, then spawn a detached orchestrator that advances a whole task graph (a Linear project, a GitHub milestone or label query, or a plan-with-docs directory) by taking each task through /deliver-task, with durable crash-resumable run state. Nothing outside the auto merge tier is merged or tracker-completed unattended.
allowed-tools: Bash, Glob, Grep, Read, Write, Edit, Agent, AskUserQuestion, Skill, mcp__linear, mcp__claude_ai_Linear
argument-hint: "<linear-project | gh-milestone | label:<name>[,label:<name>…] | plan-dir> [--until <time>] [--reserve <pct>] [--profile less-claude] [--resume]"
---

# Auto-pilot

Invoke the **auto-pilot** skill's **launch phase** with the arguments as given.
The skill (`skills/auto-pilot/SKILL.md`, "Launch phase") owns all behavior — the
fail-closed pre-flight, work-source normalization via the adapters, materializing
run state, and spawning the detached orchestrator. This command adds nothing
beyond routing and argument parsing; do not re-derive or restate the launch
steps here. If the relative path doesn't resolve, find it with **Glob**
(`**/skills/auto-pilot/SKILL.md`) and read it.

## Arguments

- `<linear-project | gh-milestone | label-query | plan-dir>` — the work source.
  **Required.** A `dev_docs/tasks/<name>_plan/` directory (a `/plan-with-docs`
  output); under `handler: gh-issue`, a GitHub milestone (number or title) or a
  label query `label:<name>[,label:<name>…]` on `gh-issue.repo`; otherwise a
  Linear project (id, name, or slug). An existing plan directory always wins;
  the detection order is the SKILL's ("Launch phase", "Preamble — parse +
  resolve"). The chosen adapter
  (`skills/auto-pilot/references/adapters.md`) normalizes it to the run-state
  representation.
- `--until <time>` — wall-clock bound for the run; the orchestrator stops itself
  at this time (and a hard outer watchdog enforces it — see
  `skills/auto-pilot/references/launch-runtime.md`). Omit for the skill's default
  bound.
- `--reserve <pct>` — fixed minimum rate-window headroom before a
  Claude-consuming delivery operation. Defaults to `15`; require a numeric
  percentage from 0 through 100. The launch phase persists the resolved value
  for resume.
- `--profile less-claude` — opt in to the CAO-backed, lower-Claude run profile.
  Its resolved settings are persisted in `RUN.md`; on `--resume`, those
  recorded settings are authoritative, so this flag is not required again.
- `--resume` — reconcile a crashed or paused run's state against reality, then
  continue the run. Parse it, and if present, dispatch to the SKILL's **Resume
  phase** (`skills/auto-pilot/SKILL.md`, "Resume phase (--resume)", whose
  mechanics live in `skills/auto-pilot/references/resume.md`) instead of running
  the launch pre-flight; do not restate its reconciliation steps here.
  The resumed run still ends at hand-off, same as a fresh launch.

## Dispatch

Resolve the task handler from `dev_docs/tasks/.task-config.yml` (the launch
pre-flight and the adapters read it), then run the SKILL's Launch
phase against the resolved source. Supported sources are `plan` (repo-pr),
`gh-issue`, and `linear`; any other handler (`jira`) → stop and say so.

The run **ends at hand-off**: the orchestrator takes each task to
`needs_review` and stops. Nothing outside the `auto` merge tier is merged or
completed unattended: `/deliver-task`'s lander lands an `auto`-tier PR only
while the repo's `AGENT_LAND` is `on`, and every other task's completion stays
merge-verified via `/sweep-for-complete`.
