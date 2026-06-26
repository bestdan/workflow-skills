# Linear handler — shared reference

Shared definitions used by `linear-add.md`, `linear-list.md`, and `linear-claim.md`. This file has no commands of its own — it only defines the config schema, the preflight pattern, and the kanban mapping table that every Linear-handled command needs.

## Connection

Linear is accessed via the official MCP server connected from `https://mcp.linear.app/mcp`. All Linear-handled commands assume the MCP is reachable; each command's preflight stops with the same error message if it isn't.

> **Linear MCP tool namespace.** The same MCP can be installed two ways, producing two different tool prefixes:
>
> - Installed via `claude.ai` settings → tools are `mcp__claude_ai_Linear__list_teams`, etc.
> - Installed via `claude mcp add --transport http linear https://mcp.linear.app/mcp` (what `mcp-setup-offer.md` instructs) → tools are `mcp__linear__list_teams`, etc. (prefix is `mcp__<server-name>__`, server registered as `linear`).
>
> Use whichever prefix is loaded in the session. Tool names after the prefix (`list_teams`, `list_projects`, `list_workflow_states`, `create_issue`, `save_issue`, `get_issue`, `save_comment`, `list_issue_labels`, `create_issue_label`, `get_user`, `list_issues`) are identical across both installs. `linear-common.md` and the per-verb files write tool names as `<linear-mcp>__list_teams`, etc. — substitute the prefix loaded in your session.

## Config block

The Linear handler is selected by `handler: linear` in `dev_docs/tasks/.task-config.yml`. The full `linear:` block:

```yaml
handler: linear
linear:
  team: PreThink # required — team name (as shown in Linear) or team id/UUID.
  # The team key (e.g. PRE) is not accepted because `list_teams`
  # does not return it.
  projects: # optional — list of project scopes. Absent/empty = whole-team scope.
    - id: <project-uuid> # required — Linear project UUID.
      name: My Project   # optional — display label; resolved via list_projects when absent.
      wip_limit: 3       # optional — per-project WIP cap; inherits top-level wip_limit when absent.
      max_estimate: 3    # optional — per-project max estimate; inherits linear.max_estimate when absent.
  # Schema rules:
  # - absent/empty → whole-team scope (global wip_limit + max_estimate, no projectId filter).
  # - exactly one entry → equivalent to the old scalar project pin.
  # - per-project keys are limited to wip_limit and max_estimate.
  # - team, base_branch, default_priority are global (not per-project).
  default_priority: 3 # optional — 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low (default 3).
  max_estimate: 3 # optional, used by /do-tasks (tracker path) — exclusive upper bound on
  # `estimate` (Linear's `estimate` uses the same Fibonacci scale as our task
  # `size` — see "Task size" in skills/task/SKILL.md). Default 3 (i.e. claim issues with estimate 1 or 2).
  base_branch: main # optional, used by /do-tasks (tracker path) — branch /do-tasks branches
# from. Default main.
# labels support is deferred — the Linear MCP create_issue tool takes label UUIDs, not names,
# and resolving names → ids requires an extra tool call. Add a tag in the body for now.
```

## Linear concepts → task concepts

- **Team** is required for every issue.
- **Project** is an optional grouping that can span teams. `linear.projects` in the config controls which projects task operations are scoped to; see "Resolve configured projects" below.

## Preflight pattern

Every Linear-handled command starts the same way. Failure messages are identical across `/add-task`, `/list-tasks`, and `/do-tasks` so the user sees the same guidance regardless of which command surfaced the error.

1. Call `<linear-mcp>__list_teams` (no args).
   - If the tool errors or returns no teams, **stop** with: "Linear handler needs the Linear MCP. Connect it in Claude Code settings (`https://mcp.linear.app/mcp`), then re-run." Do not fall back to another handler.
   - Match `<linear.team>` against each returned team's `name` (case-insensitive) or `id`. `list_teams` does not return the team key (e.g. `PRE`), so a key value will not match — surface that in the error if a likely-key string (short, all-caps) is configured.
   - If no team matches, **stop** with: "Configured Linear team `<team>` is not in your accessible teams." (List the team names that were returned.)
   - Capture the resolved team `id` and pass it to the rest of the flow.

2. (Per-verb files do additional setup — e.g. `list_workflow_states` for `/list-tasks` and `/do-tasks`.) The shared part is just the team check.

## Resolve configured projects

Every Linear-handled command that needs project scope calls this helper. It reads `linear.projects` from `.task-config.yml` and returns a list of `{ id, name, wip_limit, max_estimate }` objects with inheritance applied.

**Algorithm:**

1. Read `linear.projects` from `.task-config.yml`. If absent or empty, return a single synthetic entry:
   `{ id: null, name: "whole team", wip_limit: <top-level wip_limit>, max_estimate: <linear.max_estimate> }`
   This puts the command in whole-team scope — no `projectId` filter is passed to Linear queries.

2. For each entry in `linear.projects`:
   - `id`: use as-is (required; the project UUID).
   - `name`: use as-is if present; resolve lazily via `<linear-mcp>__list_projects` only when the name is needed for display. Do not call `list_projects` unless the name is absent and needed for display.
   - `wip_limit`: use the per-project value if set; else inherit the top-level `wip_limit` (default `3`).
   - `max_estimate`: use the per-project value if set; else inherit `linear.max_estimate` (default `3`).

3. Return the resolved list. Callers iterate over it to scope their queries and WIP counts, one project at a time.

**Exactly one entry** is equivalent to the old scalar `default_project` pin — the command operates in that single project's scope without prompting.

## Kanban mapping

Linear tasks are **issues**. The seven kanban columns from `skills/task/SKILL.md` map onto Linear's team-level issue workflow states plus four labels. **No custom workflow states are required** — the team's default Linear setup (`Backlog → Todo → In Progress → Done → Canceled`) is enough, since `needs_refinement` and `needs_review` ride on labels and on the linked GitHub PR.

Resolve state ids at runtime by Linear's state **type** (not display name — display names are user-configurable):

| Kanban column      | Linear state type | Default name                                       | Linear label(s)                                                                                                                       |
| ------------------ | ----------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `new`              | `backlog`         | `Backlog`                                          | —                                                                                                                                     |
| `needs_refinement` | `backlog`         | `Backlog`                                          | `human-approval-requested`                                                                                                            |
| `ready`            | `unstarted`       | `Todo`                                             | `auto-eligible` (set by promoter)                                                                                                     |
| `in_progress`      | `started`         | `In Progress`                                      | `auto-claimed` + viewer as `assignee` (the claim marker; concurrency guard)                                                           |
| `blocked`          | `started`         | `In Progress`                                      | `blocked`                                                                                                                             |
| `needs_review`     | `started`         | `In Progress` (or `In Review` if the team has one) | — (the open PR is the review signal via Linear's GitHub integration or the explicit `links` attachment from the tracker execute path) |
| `done`             | `completed`       | `Done`                                             | —                                                                                                                                     |

Transitions:

- `/add-task` (`linear-add.md`) creates the issue in the team's default `backlog`-type state.
- `/promote-tasks` (Linear flavor) scores cards in that backlog state: HIGH → move to the `unstarted`-type state (`Todo`) and add `auto-eligible`; LOW → leave it where it is and add `human-approval-requested`.
- `/do-tasks` (tracker path, `linear-claim.md`) picks issues from the `unstarted` state. Before claiming it runs a pre-flight check that skips the issue if an open PR or remote branch already exists for it (another session is already building it). On claim it moves the issue to the `started` state, adds `auto-claimed`, and sets the viewer as `assignee` (assignee + label are the claim marker), then re-reads to confirm it won the claim; on PR open it optionally moves to an `In Review` state if one exists.
- Linear's GitHub integration moves the issue to the `completed` state on PR merge via `Closes <KEY>`. `/do-tasks` also adds an explicit `links` attachment to the issue so the PR↔issue link does not depend on branch-name matching.
- Bail path: revert to the backlog state, add `human-approval-requested`, remove `auto-claimed`, and leave a comment.

> **Hard rule for the tracker execute path: it never moves a Linear issue to a `completed`- or `canceled`-type workflow state.** Merge is the only completion signal, handled by Linear's GitHub integration. If you are about to call `save_issue` with a `completed`-type `state` from `linear-claim.md`, you have a bug — stop.
