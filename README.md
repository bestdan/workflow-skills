# workflow-skills

[![CI](https://github.com/bestdan/workflow-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/bestdan/workflow-skills/actions/workflows/ci.yml)

A Claude Code plugin bundling Daniel's general engineering workflow skills: collaborative PR review, durable multi-step planning, auditable quantitative analysis pipelines with independent fact-checking, and a repo-native task loop for capturing and processing follow-up work.

## Install

```sh
/plugin marketplace add bestdan/workflow-skills
/plugin install workflow-skills@workflow-skills
```

## What's in the box

7 skills, 7 commands, and 1 subagent, organized into four workflows:

### PR review

| Skill         | Trigger                                                               | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **co-review** | `/co-review [PR# \| --local \| --remote \| --post] [--base <branch>]` | Produce your own review of a PR, optionally pull in other local agents (gemini, codex, …) as extra reviewers, reconcile everything against existing GitHub bot/human comments via an independent subagent, auto-fix high-confidence items, surface judgment calls back to you, and then commit and push the changes. `--local` reviews local changes (committed + uncommitted) with no PR; `--remote` skips local agents for a plain PR review; `--post` reviews someone else's PR and, after you vet the findings, posts them back as a batched GitHub review instead of editing local files. |

**Approve the reviewer commands once.** co-review sends the rubric, your extra requests, and the diff to local reviewers on **stdin** with a fixed prompt argument, so the command never changes — you approve each reviewer once with an exact-match, read-only rule (no broad wildcard). Merge them into the `permissions.allow` array in `~/.claude/settings.json` (or the repo's `.claude/settings.json`) — don't paste over an existing file. The co-review skill's **Permissions** section has the exact JSON to copy.

### Planning

| Skill              | Trigger                                                   | What it does                                                                                                                                                                                                                                                                                   |
| ------------------ | --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **plan-with-docs** | `/plan-with-docs`, or after approving a plan in plan mode | Write a multi-step implementation plan as markdown files under `dev_docs/tasks/<name>_plan/` (one file per PR-sized task, in the canonical task format) instead of printing it inline, then refine the plan through clarifying questions. Default for plans >3 tasks or spanning multiple PRs. |

### Task loop

Capture follow-up work with full context during development, then process it automatically. The `task` skill auto-triggers when you mention deferred work; the commands handle capture, listing, processing, and destination configuration.

| Command / Skill      | Trigger                                                                                                                  | What it does                                                                                                                                                                                                                                                                                                                                                                                                     |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`/add-task`**      | `/add-task [description]`                                                                                                | Capture follow-up work and deliver it via the configured handler. Defaults to the `repo-pr` handler: dispatches a remote agent to commit the task as a markdown file on a branch from main and open a PR, with zero local impact.                                                                                                                                                                                |
| **`/do-tasks`**      | `/do-tasks [slug \| --all \| -n N] [--remote\|--local]`                                                                  | The single execute verb. Resolves the handler and turns dependency-ready tasks into PRs. For `repo-pr`: default = highest-ranked task; `--all` / `-n N` batch dispatch (each to its own cloud VM) bounded by `wip_limit`; `--local` runs in-session. For `linear`: claims and executes one issue in the current session (single/foreground, pre-claim `wip_limit` gate). jira/gh-issue have no execute path yet. |
| **`/list-tasks`**    | `/list-tasks [status]`                                                                                                   | Show a table of all tasks in `dev_docs/tasks/` with status, priority, size, dependency blockers, tags, and expiry. Filter by status (`unclaimed`, `claimed`, `blocked`, `expired`, `all`).                                                                                                                                                                                                                       |
| **`/push-plan`**     | `/push-plan <name> [--ready-only]`                                                                                       | Push a vetted local plan (`dev_docs/tasks/<name>_plan/`) to the configured tracker. `repo-pr` is a no-op (the files are the destination); `linear` creates one issue per task under a project, in dependency order, translating `is_blocked_by` to native blockers. Create-missing-only — records tracker ids back into the files, so re-runs don't duplicate. jira/gh-issue not supported yet.                  |
| **`/task-config`**   | `/task-config [handler]`                                                                                                 | Configure where `/add-task` delivers (writes `dev_docs/tasks/.task-config.yml`). Handlers: `repo-pr` (default, markdown PR), `gh-issue` (GitHub Issue), `jira` (Jira work item via Atlassian MCP), `linear` (Linear issue via Linear MCP). Verifies prerequisites before writing the config.                                                                                                                     |
| **`/promote-tasks`** | `/promote-tasks [dry-run \| apply]`                                                                                      | Score `new` tasks against the confidence check and promote them to `ready` or `needs_refinement`.                                                                                                                                                                                                                                                                                                                |
| **`/doctor`**        | `/doctor [--fix]`                                                                                                        | Diagnose the task-loop setup — config validity, handler prerequisites, legacy dirs, schema drift, and hygiene — reporting `PASS`/`WARN`/`FAIL` per check. Read-only by default; `--fix` applies the safe mechanical repairs (run the migration, prune expired, fill defaulted fields) and leaves judgment calls as warnings.                                                                                     |
| **break-down-task**  | a task is too big for one PR, `/promote-tasks` flags `scope exceeds size 5`, or "split / slice / break down this task"   | Take an existing over-sized task, find natural shear points (vertical/horizontal slices, preparatory refactor, interface-then-impl), and on a HIGH-confidence split replace it with PR-sized component tasks chained by `is_blocked_by`. The resolution of the promote-tasks size gate; sibling of `plan-with-docs` (which slices a fresh idea).                                                                 |
| **`task` skill**     | mentions of "task", "todo", "follow-up", "we should come back to this", or any of the `/add-task` / `/do-tasks` commands | Auto-trigger skill: provides the task file format, capture workflow, handler abstraction, and processing logic. Loaded in-context whenever Claude sees deferred-work language.                                                                                                                                                                                                                                   |

### Auditable analysis

| Skill                    | Trigger                                                               | What it does                                                                                                                                                                                                                                                                                                         |
| ------------------------ | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **analysis-pipeline**    | `/analysis-pipeline`, or auto when building report-producing analyses | Structure quantitative work as model → template → fill pipelines. Enforces input provenance, separation of narrative and computation, and reproducible output. Includes a working `example/` to copy from.                                                                                                           |
| **review-facts**         | `/review-facts`                                                       | Independent fact-check of a completed analysis. Spawns the bundled `fact-reviewer` subagent (fresh context, read-only tools) to verify links resolve, cited values match sources, output is reproducible, narrative numbers trace to the model, units/formulas are correct, and the recommendation matches the data. |
| **analysis-conventions** | Auto when writing notebooks or analysis scripts                       | Coding conventions for analysis work: when to use plain scripts vs marimo, `uv run` for execution, scratchpad patterns, notebook structure. Not user-invocable — loads in context.                                                                                                                                   |

### Bundled subagent

- **fact-reviewer** — Read-only auditor used by `/review-facts`. Runs in a fresh context with `Read`, `Glob`, `Grep`, `Bash`, and `WebFetch`. Reports findings; never edits the analysis.

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

Run the full quality gate locally with `just check` (formatting, manifest
validation, and repo-native structural checks). See [`CONTRIBUTING.md`](CONTRIBUTING.md)
for the dev loop, the "adding a skill" checklist, and the opt-in behavioral evals.

## License

MIT
