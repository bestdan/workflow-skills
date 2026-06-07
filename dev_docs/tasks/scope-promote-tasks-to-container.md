---
title: Scope /promote-tasks to a single container per handler (project / epic / milestone / plan dir)
priority: high
size: 3
status: new
created: 2026-06-07
source_branch: bestdan/promote-tasks-container-scope
related_files:
  - commands/promote-tasks.md
  - commands/handlers/linear-promote.md
  - commands/handlers/linear-common.md
  - commands/handlers/gh-issue.md
  - commands/handlers/jira.md
  - commands/task-config.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - safety
---

## Context

`/promote-tasks` resolves the handler (`commands/promote-tasks.md` step 0) and, for trackers, scores the **whole backlog** of the configured team/project. On the Linear path (`commands/handlers/linear-promote.md`), if `linear.default_project` is unset it queries **every** `backlog`-type issue for the team and would move them all to `Todo` + `auto-eligible`. That is a real footgun: a repo whose `/add-task` files to a shared team can mass-promote unrelated work in one command.

This was hit in practice: pushing the `handler_parity_followups_plan` to a new Linear project (PRE-111…118) and then running `/promote-tasks` would, by the letter of the handler doc, have scored all of the team's backlog (PRE-1…110). It only stayed safe because the run was manually scoped to the new project.

Every handler already has a natural **container** concept, surfaced by `/push-plan` when it pushes a plan:

- **linear** → a **project** (`linear.default_project`).
- **jira** → an **epic** (`jira.default_epic`).
- **gh-issue** → a **milestone** (or the `plan:<name>` label fallback `/push-plan` uses).
- **repo-pr** (file) → a **plan directory** (`dev_docs/tasks/<name>_plan/`) instead of all of `dev_docs/tasks/**`.

`/promote-tasks` should honor the same container so promotion is always bounded to one workstream.

> Note: the gh-issue and jira promote handlers do not exist yet — they are planned in the `handler_parity_followups_plan` (Linear PRE-111 / PRE-112). This task defines the **scoping requirement** those tasks must satisfy and implements it now for the **linear** and **repo-pr** promote paths, which already exist.

## Task

1. **Add a container argument to `/promote-tasks`.** Accept an optional container token in `$ARGUMENTS` (alongside the existing `dry-run`), e.g. `/promote-tasks <container>` / `/promote-tasks <container> dry-run`, where `<container>` is a project (linear), epic key (jira), milestone/label (gh-issue), or plan-dir name (repo-pr). Document the per-handler meaning in `commands/promote-tasks.md`.
2. **Resolve a default container from config when the arg is omitted.** Per handler: `linear.default_project`, `jira.default_epic`, a new `gh-issue.milestone` (or `plan` label), and a new `repo-pr.plan_dir` (optional). Document these in `commands/task-config.md` and the relevant handler config files.
3. **Require an explicit scope for trackers, or warn loudly.** On the linear/jira/gh-issue paths, if neither an arg nor a config default resolves a container, **stop with a warning** rather than scoring the entire team/project backlog — promoting all of a shared tracker's backlog should be an explicit, opted-into action (e.g. a `--all-backlog` escape hatch), not the default. Update `commands/handlers/linear-promote.md` step 4/5 so the unscoped case warns/stops instead of silently scanning everything.
4. **Scope the repo-pr (file) path too.** When a container (plan dir) is given, restrict the `find` in `commands/promote-tasks.md` step 1 to `dev_docs/tasks/<plan_dir>/**` instead of all of `dev_docs/tasks/**`. With no container, the current whole-tree scan is acceptable for the file path (it only flips local frontmatter, not a shared remote), but document the option.
5. **Carry the requirement to the unbuilt handlers.** Note in the `handler_parity_followups_plan` promote tasks (PRE-111 gh-issue, PRE-112 jira) that their promote handlers must accept the container scope (milestone/label for gh-issue, epic for jira) from day one.

## Acceptance Criteria

**Code-enforced**
- `commands/promote-tasks.md` documents an optional container argument and the per-handler container meaning, plus the config-default resolution order.
- `commands/handlers/linear-promote.md` no longer scores the whole team backlog when no project is resolved — it stops/warns and requires an explicit project (arg or `linear.default_project`) unless an explicit all-backlog escape hatch is passed.
- The repo-pr file path can restrict its scan to a single plan directory.
- New config keys (gh-issue milestone/label, repo-pr plan dir) are documented in `commands/task-config.md`.
- `just check` passes.

**User-run**
- With `handler: linear` and no `default_project`/arg, `/promote-tasks` refuses to score the whole team backlog and explains how to scope it.
- `/promote-tasks <project>` (or with `default_project` set) scores only that project's backlog.
- `/promote-tasks <plan-dir>` on the file path scores only tasks under `dev_docs/tasks/<plan-dir>/`.
