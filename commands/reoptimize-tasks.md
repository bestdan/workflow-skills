---
description: Re-optimize an existing tracker backlog — across a project, initiative, or team — by reconciling prose dependencies against native relations, surfacing hidden cross-project blockers, detecting cycles/stale links/priority inversions and overlap, then applying the approved fixes. v1 supports the linear and gh-issue (report-only) handlers; jira is planned; repo-pr is unsupported (re-optimize the plan files directly).
allowed-tools: Bash(git *), Bash(cat *), Bash(python3 *), Bash(gh *), Glob, Read, AskUserQuestion, mcp__linear__list_teams, mcp__linear__list_projects, mcp__linear__list_initiatives, mcp__linear__list_issues, mcp__linear__get_issue, mcp__linear__list_workflow_states, mcp__linear__save_issue, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__list_initiatives, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue
argument-hint: "[project|initiative|team] [name]"
---

# Re-optimize Tasks

Take an **existing tracker backlog** and re-optimize its dependency graph and
ordering. This is the **inverse of `/push-plan`**: push-plan turns a vetted local
plan into a fresh, topologically-ordered set of tracker issues; this command
audits a backlog that has already drifted — issues added at different times, by
different runs, across projects that were each well-ordered internally but never
ordered against each other, with dependencies that live in description prose
rather than native relations.

It is **propose-then-apply**: every analysis is read-only, the findings are
presented for review, and nothing is mutated until you approve it per group.

This command is a thin dispatcher. **v1 implements the `linear` handler only** —
its analysis + apply logic lives in `commands/handlers/linear-reoptimize.md`.
`jira` and `gh-issue` re-optimize are planned follow-ups (see the capability
matrix in `commands/task-config.md`); until they land, those handlers stop with
a "planned" message rather than running a half-wired flow.

## 1. Resolve the handler

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `commands/task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

Dispatch on the `handler:` key (same resolution as `/push-plan` §1):

- `handler: linear` → follow `commands/handlers/linear-reoptimize.md` (§Linear
  below names the steps; that file has the detail). If the relative path doesn't
  resolve, find it with **Glob** (`**/commands/handlers/linear-reoptimize.md`).
- `handler: gh-issue` → follow `commands/handlers/gh-issue-reoptimize.md`.
  Dimensions 1–2 read and write the **native `blocked_by` edge** — the same one
  `/push-plan` draws and `/do-tasks` reads — creating, removing and cycle-checking
  it directly; the `Blocked by:` body footer is a human-readable echo of an edge
  that exists, never a substitute for one. Priority fixes apply through
  `gh-issue-state.py`. Two things have no native counterpart and stay prose:
  `Related:` (a cross-reference, not a blocking relation) and `duplicateOf`. If
  the relative path doesn't resolve, find it with **Glob**
  (`**/commands/handlers/gh-issue-reoptimize.md`).
- `handler: jira` → **planned, not yet implemented.** **stop** with:
  "`/reoptimize-tasks` supports the `linear` and `gh-issue` handlers today;
  `jira` re-optimize is a planned follow-up. For now, re-order in your tracker
  directly." (When it lands it'll get a `commands/handlers/jira-reoptimize.md`
  reference and the Atlassian MCP tool surface in this command's
  `allowed-tools`, mirroring `push-plan.md`.)
- `handler: repo-pr`, no `handler:` key, or file absent → **stop** with:
  `repo-pr handler: tasks live as local plan files, not a tracker graph — re-optimize the plan directly, or /push-plan first to get a tracker to re-optimize.`
- Any other value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

## 2. Resolve the scope

The scope is the set of issues to load into the graph. Resolve it from
`$ARGUMENTS` (`[project|initiative|team] [name]`); if the kind or name is missing,
ask via `AskUserQuestion` (header: "Scope").

- **project** — the named/selected project's issues.
- **initiative** — expand to the initiative's projects (`list_initiatives` with
  `includeProjects`, or `list_projects(initiative=…)`) and **union** their
  issues. This is the scope that exposes cross-project drift, so prefer it when
  the user's concern is "ordering across projects."
- **team** — every issue in the team.

Run the handler's preflight to resolve the team id (Linear: `linear-common.md`;
gh-issue: `gh auth status` + repo resolution, and note gh-issue has no
initiative-level grouping — see `gh-issue-reoptimize.md` §Load).
Match a typed name case-insensitively against the real projects/initiatives; on
no match, push back and re-ask rather than guessing.

## 3. Run the analysis (read-only)

Hand the resolved scope to the handler's **§Analysis**. It returns structured
findings across the four dimensions (repair blocking chains, hidden cross-project
deps, re-order/re-prioritize, duplicates/overlap). No mutation happens here.

## 4. Present the report

Print the findings grouped by dimension, each with the **evidence quoted** (the
prose phrase or `<issue>` mention, the conflicting native relation, the inverted
priorities) and the **proposed mutation**. Mark inferred/semantic findings as
**lower-confidence** so the user vets them. Include the recommended topological
execution order.

## 5. Apply (gated)

Group the proposed mutations by kind (add blocker links, remove stale links, fix
priority inversions, mark duplicates) and confirm each group via
`AskUserQuestion`. Apply **only** approved mutations through the handler's
§Apply. **Hard rules** (carried from the task-loop handlers): never change a
workflow **state**, never mutate a `completed`/`canceled` issue, never auto-merge
a duplicate. Cycles are reported, never auto-resolved. Print a final summary of
what was applied and what was skipped.
