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

The Linear handler is selected by `handler: linear` in `dev_docs/todos/.todo-config.yml`. The full `linear:` block:

```yaml
handler: linear
linear:
  team: PreThink # required — team name (as shown in Linear) or team id/UUID.
  # The team key (e.g. PRE) is not accepted because `list_teams`
  # does not return it.
  default_project: null # optional; skips the project prompt in /add-todo
  # (explicit project id/UUID, not a name).
  default_priority: 3 # optional — 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low (default 3).
  max_estimate: 3 # optional, used by /claim-todo — exclusive upper bound on
  # `estimate`. Default 3 (i.e. claim issues with estimate 1 or 2).
  base_branch: main # optional, used by /claim-todo — branch /claim-todo branches
# from. Default main.
# labels support is deferred — the Linear MCP create_issue tool takes label UUIDs, not names,
# and resolving names → ids requires an extra tool call. Add a tag in the body for now.
```

## Linear concepts → todo concepts

- **Team** is required for every issue.
- **Project** is an optional grouping that can span teams.

## Preflight pattern

Every Linear-handled command starts the same way. Failure messages are identical across `/add-todo`, `/list-todos`, and `/claim-todo` so the user sees the same guidance regardless of which command surfaced the error.

1. Call `<linear-mcp>__list_teams` (no args).
   - If the tool errors or returns no teams, **stop** with: "Linear handler needs the Linear MCP. Connect it in Claude Code settings (`https://mcp.linear.app/mcp`), then re-run." Do not fall back to another handler.
   - Match `<linear.team>` against each returned team's `name` (case-insensitive) or `id`. `list_teams` does not return the team key (e.g. `PRE`), so a key value will not match — surface that in the error if a likely-key string (short, all-caps) is configured.
   - If no team matches, **stop** with: "Configured Linear team `<team>` is not in your accessible teams." (List the team names that were returned.)
   - Capture the resolved team `id` and pass it to the rest of the flow.

2. (Per-verb files do additional setup — e.g. `list_workflow_states` for `/list-todos` and `/claim-todo`.) The shared part is just the team check.

## Kanban mapping

Linear todos are **issues**. The seven kanban columns from `skills/todo/SKILL.md` map onto Linear's team-level issue workflow states plus four labels. **No custom workflow states are required** — the team's default Linear setup (`Backlog → Todo → In Progress → Done → Canceled`) is enough, since `needs_refinement` and `needs_review` ride on labels and on the linked GitHub PR.

Resolve state ids at runtime by Linear's state **type** (not display name — display names are user-configurable):

| Kanban column      | Linear state type | Default name                                       | Linear label(s)                                                                                                            |
| ------------------ | ----------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `new`              | `backlog`         | `Backlog`                                          | —                                                                                                                          |
| `needs_refinement` | `backlog`         | `Backlog`                                          | `human-approval-requested`                                                                                                 |
| `ready`            | `unstarted`       | `Todo`                                             | `auto-eligible` (set by promoter)                                                                                          |
| `in_progress`      | `started`         | `In Progress`                                      | `auto-claimed` (concurrency guard)                                                                                         |
| `blocked`          | `started`         | `In Progress`                                      | `blocked`                                                                                                                  |
| `needs_review`     | `started`         | `In Progress` (or `In Review` if the team has one) | — (the open PR is the review signal via Linear's GitHub integration or the explicit `links` attachment from `/claim-todo`) |
| `done`             | `completed`       | `Done`                                             | —                                                                                                                          |

Transitions:

- `/add-todo` (`linear-add.md`) creates the issue in the team's default `backlog`-type state.
- `/promote-todos` (Linear flavor) scores cards in that backlog state: HIGH → move to the `unstarted`-type state (`Todo`) and add `auto-eligible`; LOW → leave it where it is and add `human-approval-requested`.
- `/claim-todo` (`linear-claim.md`) picks issues from the `unstarted` state. On claim it moves the issue to the `started` state, adds `auto-claimed`, and (on PR open) optionally moves to an `In Review` state if one exists.
- Linear's GitHub integration moves the issue to the `completed` state on PR merge via `Closes <KEY>`. `/claim-todo` also adds an explicit `links` attachment to the issue so the PR↔issue link does not depend on branch-name matching.
- Bail path: revert to the backlog state, add `human-approval-requested`, remove `auto-claimed`, and leave a comment.

> **Hard rule for `/claim-todo`: it never moves a Linear issue to a `completed`- or `canceled`-type workflow state.** Merge is the only completion signal, handled by Linear's GitHub integration. If you are about to call `save_issue` with a `completed`-type `state` from `linear-claim.md`, you have a bug — stop.
