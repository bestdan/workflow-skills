# workflow-skills

A Claude Code plugin bundling Daniel's general engineering workflow skills: collaborative PR review, durable multi-step planning, auditable quantitative analysis pipelines with independent fact-checking, and a repo-native todo loop for capturing and processing follow-up work.

## Install

```sh
/plugin marketplace add bestdan/workflow-skills
/plugin install workflow-skills@workflow-skills
```

## What's in the box

6 skills, 4 commands, and 1 subagent, organized into four workflows:

### PR review

| Skill         | Trigger            | What it does                                                                                                                                                                                  |
| ------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **co-review** | `/co-review [PR# \| --local \| --remote] [--base <branch>]` | Produce your own review of a PR, optionally pull in other local agents (gemini, codex, …) as extra reviewers, reconcile everything against existing GitHub bot/human comments via an independent subagent, auto-fix high-confidence items, and surface judgment calls back to you. `--local` reviews local changes (committed + uncommitted) with no PR; `--remote` skips local agents for a plain PR review. |

### Planning

| Skill              | Trigger                                                   | What it does                                                                                                                                                                                                                                                    |
| ------------------ | --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **plan-with-docs** | `/plan-with-docs`, or after approving a plan in plan mode | Write a multi-step implementation plan as markdown files under `dev_docs/todo/<name>_plan/` (one file per PR-sized step) instead of printing it inline, then refine the plan through clarifying questions. Default for plans >3 steps or spanning multiple PRs. |

### Todo loop

Capture follow-up work with full context during development, then process it automatically. The `todo` skill auto-triggers when you mention deferred work; the commands handle capture, listing, processing, and destination configuration.

| Command / Skill   | Trigger                       | What it does                                                                                                                                                                                                                                          |
| ----------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`/add-todo`**   | `/add-todo [description]`     | Capture follow-up work and deliver it via the configured handler. Defaults to the `repo-pr` handler: dispatches a remote agent to commit the todo as a markdown file on a branch from main and open a PR, with zero local impact.                     |
| **`/process-todo`** | `/process-todo [slug \| --all \| --local]` | Claim and execute dependency-ready `repo-pr` todos. Default dispatches the highest-priority eligible todo to a remote Claude session; `--all` dispatches every eligible todo and skips ones still blocked by another todo; `--local` processes in the current session.                  |
| **`/list-todos`** | `/list-todos [status]`        | Show a table of all todos in `dev_docs/todos/` with status, priority, dependency blockers, tags, and expiry. Filter by status (`unclaimed`, `claimed`, `blocked`, `expired`, `all`).                                                                                       |
| **`/todo-config`** | `/todo-config [handler]`     | Configure where `/add-todo` delivers (writes `dev_docs/todos/.todo-config.yml`). Handlers: `repo-pr` (default, markdown PR), `gh-issue` (GitHub Issue), `jira` (Jira work item via Atlassian MCP), `linear` (Linear issue via Linear MCP). Verifies prerequisites before writing the config.   |
| **`todo` skill**  | mentions of "todo", "follow-up", "we should come back to this", or any of the `/add-todo` / `/process-todo` commands | Auto-trigger skill: provides the todo file format, capture workflow, handler abstraction, and processing logic. Loaded in-context whenever Claude sees deferred-work language. |

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

## License

MIT
