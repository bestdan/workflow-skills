# workflow-skills

[![CI](https://github.com/bestdan/workflow-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/bestdan/workflow-skills/actions/workflows/ci.yml)

A Claude Code plugin bundling Daniel's general engineering workflow skills: collaborative PR review, durable multi-step planning, auditable quantitative analysis pipelines with independent fact-checking, and a repo-native task loop for capturing and processing follow-up work.

## Install

```sh
/plugin marketplace add bestdan/workflow-skills
/plugin install workflow-skills@workflow-skills
```

## What's in the box

15 skills, 21 commands, and 1 subagent, organized into seven workflows. Each
entry links to its own doc — that's where the flags, edge cases, and handler
support live.

### PR review

| Skill                                      | Trigger                                                               | What it does                                                                                                                                                                                                                 |
| ------------------------------------------ | --------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [**co-review**](skills/co-review/SKILL.md) | `/co-review [PR# \| --local \| --remote \| --post] [--base <branch>]` | Review a PR yourself, pull in other local agents (codex, agy, devin, copilot) as extra reviewers, reconcile everything against existing GitHub comments, auto-fix the high-confidence items, and surface the judgment calls. |

**Approve the reviewer commands once.** co-review hands each local reviewer a
fixed input file on stdin (or via `--prompt-file` for devin), so the command
string never changes and you can approve each reviewer with an exact-match,
read-only rule instead of a broad wildcard. The skill's **Permissions** section
has the exact JSON — merge it into `permissions.allow` in
`~/.claude/settings.json` or the repo's `.claude/settings.json`.

### Planning

| Skill                                                | Trigger                                                                                            | What it does                                                                                                                                     |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| [**plan-with-docs**](skills/plan-with-docs/SKILL.md) | `/plan-with-docs`, or after approving a plan in plan mode                                          | Write a multi-step plan as one markdown file per PR-sized task under `dev_docs/tasks/<name>_plan/`, then refine it through clarifying questions. |
| [**research-spike**](skills/research-spike/SKILL.md) | filing/answering a question, registering an obligation, promoting a decision, checking convergence | Run a research spike through an **obligation ledger**, so a converging question count can't hide a climbing count of deferred work.              |

### Task loop

Capture follow-up work with full context during development, then process it
automatically. The [`task`](skills/task/SKILL.md) skill auto-triggers on
deferred-work language and carries the file format, capture workflow, and
handler abstraction; the commands below do the work.

Where tasks land is configured per repo — `repo-pr` (markdown PR, default),
`gh-issue`, `jira`, or `linear`. **Handler support is jagged**, and
[`commands/task-config.md`](commands/task-config.md) holds the authoritative
capability matrix.

| Command                                                       | Trigger                                                                   | What it does                                                                                                                     |
| ------------------------------------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| [**`/add-task`**](commands/add-task.md)                       | `/add-task [description]`                                                 | Capture follow-up work and deliver it via the configured handler.                                                                |
| [**`/do-tasks`**](commands/do-tasks.md)                       | `/do-tasks [slug \| --all \| -n N] [--remote\|--local]`                   | The single execute verb — turn dependency-ready tasks into PRs, one in-session or N dispatched, bounded by `wip_limit`.          |
| [**`/list-tasks`**](commands/list-tasks.md)                   | `/list-tasks [status]`                                                    | Table of all tasks with status, priority, size, blockers, tags, and expiry.                                                      |
| [**`/promote-tasks`**](commands/promote-tasks.md)             | `/promote-tasks [dry-run \| apply]`                                       | Score `new` tasks against the confidence check and promote them to `ready` or `needs_refinement`.                                |
| [**`/push-plan`**](commands/push-plan.md)                     | `/push-plan <name> [--ready-only]`                                        | Push a vetted local plan to the tracker in dependency order, recording ids back so re-runs don't duplicate.                      |
| [**`/reoptimize-tasks`**](commands/reoptimize-tasks.md)       | `/reoptimize-tasks [project \| initiative \| team] [name]`                | The inverse of `/push-plan` — reconcile prose dependencies against native relations and fix cycles, stale links, and overlap.    |
| [**`/task-config`**](commands/task-config.md)                 | `/task-config [handler]`                                                  | Choose the handler and verify its prerequisites (writes `dev_docs/tasks/.task-config.yml`).                                      |
| [**`/complete-task`**](commands/complete-task.md)             | `/complete-task <identifier> [--dry-run]`                                 | Transition one identified work item to its tracker's completed state. All four handlers.                                         |
| [**`/archive-tasks`**](commands/archive-tasks.md)             | `/archive-tasks [--older-than <N>d] [--issues <refs>] [dry-run]`          | Retire terminal-state work past an age threshold. Refuses to mutate without a threshold.                                         |
| [**`/sweep-for-complete`**](commands/sweep-for-complete.md)   | `/sweep-for-complete [--apply] [--all] [--project <id\|name>]`            | Find started-state issues whose linked PR merged and complete exactly those. **linear-only (v1)**.                               |
| [**`/reconcile-tasks`**](commands/reconcile-tasks.md)         | `/reconcile-tasks [--apply] [--all] [--project <id\|name>]`               | Fix issues sitting in the wrong state, against a fixed rule table. **linear-only (v1)**.                                         |
| [**`/find-false-closures`**](commands/find-false-closures.md) | `/find-false-closures [--apply] [--project <uuid>] [--repo <owner/name>]` | Detect completed issues no merged PR owns (the bare-id over-close bug) and restore them. **linear-only**.                        |
| [**`/sweep-for-archive`**](commands/sweep-for-archive.md)     | `/sweep-for-archive [--since 24h] [--apply] [...]`                        | Close-out sweep: verify the window's closures, complete what merged, archive exactly what was proved delivered. **linear-only**. |
| [**`/doctor`**](commands/doctor.md)                           | `/doctor [--fix]`                                                         | Diagnose the task-loop setup — config, prerequisites, legacy dirs, schema drift — as `PASS`/`WARN`/`FAIL`.                       |
| [**break-down-task**](skills/break-down-task/SKILL.md)        | a task is too big for one PR, or "split / slice / break down this task"   | Find the natural shear points in an over-sized task and replace it with PR-sized components chained by `is_blocked_by`.          |

`/sweep-for-complete`, `/reconcile-tasks`, `/find-false-closures`, and
`/sweep-for-archive` are dry-run-by-default and safe to schedule via `/loop` or
`/schedule`.

### Coder orchestration

| Skill                                                                   | Trigger                                                                           | What it does                                                                                                                                                                                |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [**orchestrate-coders**](skills/orchestrate-coders/SKILL.md)            | `/orchestrate-coders <task> [--coder <backend>[:<model>]]... [-n N]`              | Turn the session into an orchestrator that writes no feature code: decompose into PR-sized packets, dispatch each to a coder backend in an isolated worktree, verify every diff, integrate. |
| [**select-coder**](skills/select-coder/SKILL.md)                        | `/select-coder <task> [--refresh] [-n N]`, or "which model should implement this" | Probe which backends are actually available, profile the task, and return N ranked `<backend>:<model>` specs with rationale.                                                                |
| [**assess-task**](skills/assess-task/SKILL.md)                          | `/assess-task <task>`, or "how hard / mechanical is this task"                    | Profile what a task demands — complexity, creativity, scope, autonomy, cost sensitivity, verification criticality. Never picks a model.                                                     |
| [**`/refresh-coder-comparison`**](commands/refresh-coder-comparison.md) | `/refresh-coder-comparison [backend \| model]`, or a stale matrix cache date      | Re-research the capability matrix — benchmarks, pricing, model rosters, vendor terms — and write it back into `select-coder/matrix.md`.                                                     |

### Autonomous execution

| Skill                                            | Trigger                                                             | What it does                                                                                                                                                                   |
| ------------------------------------------------ | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [**deliver-task**](skills/deliver-task/SKILL.md) | `/deliver-task <slug \| id> [--base <branch>] [--questions <path>]` | One task, all the way: claim → implement via a routed worker in an isolated worktree → verify → PR → non-interactive co-review → hand off at `needs_review`, never `done`.     |
| [**auto-pilot**](skills/auto-pilot/SKILL.md)     | `/auto-pilot <linear-project \| plan-dir>` _(in progress)_          | Advance a whole task graph unattended — a thin orchestrator walking the graph via `/deliver-task`, with crash-resumable state. See [the design notes](dev_docs/auto-pilot.md). |

### Auditable analysis

| Skill                                                            | Trigger                                                               | What it does                                                                                                                                 |
| ---------------------------------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| [**analysis-pipeline**](skills/analysis-pipeline/SKILL.md)       | `/analysis-pipeline`, or auto when building report-producing analyses | Structure quantitative work as model → template → fill pipelines, with input provenance and reproducible output. Ships a working `example/`. |
| [**review-facts**](skills/review-facts/SKILL.md)                 | `/review-facts`                                                       | Spawn the `fact-reviewer` subagent to verify links, cited values, reproducibility, number-trace, units, formulas, and the recommendation.    |
| [**analysis-conventions**](skills/analysis-conventions/SKILL.md) | auto when writing notebooks or analysis scripts                       | Conventions for analysis code: marimo vs plain scripts, `uv run`, scratchpad patterns, notebook structure. Not user-invocable.               |

### Understanding the work

| Skill                                                                  | Trigger                                                               | What it does                                                                                                                                                         |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [**tutor**](skills/tutor/SKILL.md)                                     | `/tutor [--pr <N> \| --diff [<ref>] \| <path>]`, or "quiz me on this" | Teach you the work until you can defend it: elicit first, close the gap, verify with a counterfactual quiz. An item is checked off only once you've demonstrated it. |
| [**research-spike-tutorial**](skills/research-spike-tutorial/SKILL.md) | "walk me through how the research-spike obligation ledger works"      | A hands-on tutorial against a disposable tree under a temp dir — hit the "destination must already exist" wall for real, then watch the divergence.                  |

### Bundled subagent

- [**fact-reviewer**](agents/fact-reviewer.md) — read-only auditor used by
  `/review-facts`. Fresh context, `Read`/`Glob`/`Grep`/`Bash`/`WebFetch`.
  Reports findings; never edits the analysis.

### Also included

- [**Overnight auto-resume (`car`)**](dev_docs/claude-auto-resume.md) — an
  external launcher you run instead of `claude` that survives the 5-hour usage
  cap: it detects a cap-kill, sleeps until reset, and resumes the same
  conversation.

## Why auditable analysis matters

When you ask Claude to analyze something — compare vendors, project costs, evaluate options — the default behavior is to do the math in its head and write the answer directly into prose:

> Based on the pricing, Vendor A costs $2,510/year, Vendor B costs $2,627/year,
> and Vendor C costs $2,197/year. **I recommend Vendor C**, saving $430/year.

This looks helpful but is fragile: you can't tell where the numbers came from, you can't update them when inputs change, and you can't verify the math. The `analysis-pipeline` and `review-facts` skills push Claude to structure analyses as reproducible pipelines instead — every number traces back to a named, sourced input, and a separate agent audits the result before it ships.

## Example

`skills/analysis-pipeline/example/` contains a complete working pipeline (vendor cost comparison) you can run:

```sh
cd skills/analysis-pipeline/example
uv run model.py              # compute all values -> model_output.json
uv run fill_templates.py     # fill template placeholders -> memo.filled.md
```

Then audit it:

```
/review-facts .
```

## Staying up to date

Third-party marketplaces have auto-update disabled by default. Enable auto-update in the `/plugin` UI (Marketplaces tab), or update manually:

```sh
/plugin marketplace update bestdan/workflow-skills
/reload-plugins
```

## Development

Run the full quality gate locally with `just check`. Start at
[`AGENTS.md`](AGENTS.md) for the map of the repo and the conventions that are
load-bearing, then [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev loop and the
"adding a skill" checklist.

## License

MIT
