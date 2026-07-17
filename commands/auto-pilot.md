---
description: Launch an unattended auto-pilot run — an interactive pre-flight, then spawn a detached orchestrator that advances a whole task graph (a Linear project or a plan-with-docs directory) by taking each task through /deliver-task, with durable crash-resumable run state. Nothing is merged or tracker-completed unattended.
allowed-tools: Bash, Glob, Grep, Read, Write, Edit, Agent, AskUserQuestion, Skill, mcp__linear, mcp__claude_ai_Linear
argument-hint: "<linear-project | plan-dir> [--until <time>] [--reserve <pct>] [--profile less-claude] [--resume]"
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

- `<linear-project | plan-dir>` — the work source. **Required.** Either a Linear
  project (id, name, or slug) **or** a `dev_docs/tasks/<name>_plan/` directory (a
  `/plan-with-docs` output). Detect which: an existing directory path → plan
  source; otherwise → resolve as a Linear project. The chosen adapter
  (`skills/auto-pilot/references/adapters.md`) normalizes it to the run-state
  representation.
- `--until <time>` — wall-clock deadline for the run, as an absolute ISO-8601
  time or `now+<duration>`. Enforcement is fail-soft: the run loop's
  pre-dispatch guard declines to _start_ a task once there isn't enough time
  left to plausibly finish it, so the run stops cleanly rather than a hard
  watchdog killing a task mid-delivery (see
  `skills/auto-pilot/references/run-budget.md`, "Minimum task budget"). Omit
  entirely for no wall-clock deadline — the run continues until the task graph
  is exhausted or another stop condition (e.g. budget reserve) is hit.
- `--reserve <pct>` — fixed minimum rate-window headroom before a
  Claude-consuming delivery operation. Defaults to `15`; require a numeric
  percentage from 0 through 100. The launch phase persists the resolved value
  for resume.
- `--profile less-claude` — opt in to the CAO-backed, lower-Claude run profile.
  Concretely it: routes coding dispatch through `select-coder --cao-fleet` to a
  named CAO custom coder (`cao-codex` / `cao-agy`, never a generic `cao`
  placeholder); sets `co_review_mode: off` unless dialed to `cheap-single`; and
  sets `diff_judgment_tier: sonnet` so diff judgment runs on Sonnet instead of
  Opus. Shell verification (the project's check command plus exercising the
  feature) is unchanged. See
  [`dev_docs/auto-pilot-how-to-use.md`](../dev_docs/auto-pilot-how-to-use.md)
  for more. Its resolved settings are persisted in `RUN.md`; on `--resume`,
  those recorded settings are authoritative, so this flag is not required
  again.
- `--resume` — reconcile a crashed or paused run's state against reality, then
  continue the run. Parse it, and if present, dispatch to the SKILL's **Resume
  phase** (`skills/auto-pilot/SKILL.md`, "Resume phase (--resume)", whose
  mechanics live in `skills/auto-pilot/references/resume.md`) instead of running
  the launch pre-flight; do not restate its reconciliation steps here.
  The resumed run still ends at hand-off, same as a fresh launch.

## Dispatch

Resolve the task handler from `dev_docs/tasks/.task-config.yml` (the launch
pre-flight and the linear/plan adapters read it), then run the SKILL's Launch
phase against the resolved source. Only `linear` and `plan` (repo-pr) sources
are supported in v1; any other handler → stop and say so.

The run **ends at hand-off, never done**: the orchestrator takes each task to
`needs_review` and stops. Nothing is merged and no tracker issue is completed
unattended — completion stays merge-verified via `/sweep-for-complete`.
