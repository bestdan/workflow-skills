# Handler: linear

Creates a Linear issue via the official Linear MCP server (`mcp__claude_ai_Linear__*`, connected from `https://mcp.linear.app/mcp`). Foreground call, no git plumbing, no CLI install. The new issue is filed under a configured team and optionally attached to a project.

> **Required interaction:** step 2 (project selection) MUST prompt the user via `AskUserQuestion` unless `linear.default_project` is set in config. This applies in auto mode too. Treat a missing or empty `linear.default_project` (including `null`, `""`, or the key being absent from the config block) as "not set" — you MUST prompt. If you find yourself about to call `create_issue` without having asked AND without a non-empty `linear.default_project`, stop and go back to step 2.

Config block in `dev_docs/todos/.todo-config.yml`:

```yaml
handler: linear
linear:
  team: PreThink                  # required — team name (as shown in Linear) or team id/UUID. The team key (e.g. PRE) is not accepted because `list_teams` does not return it.
  default_project: null           # optional; skips the project prompt (explicit project id/UUID, not a name)
  default_priority: 3             # optional — 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low (default 3)
# labels support is deferred — the Linear MCP create_issue tool takes label UUIDs, not names,
# and resolving names → ids requires an extra tool call. Add a tag in the body for now.
```

Linear concepts → todo concepts:
- **Team** is required for every issue (like Jira's `project`).
- **Project** is an optional grouping that can span teams (like Jira's `epic`).

## Steps

1. **Preflight.** Confirm the Linear MCP is reachable and the configured team exists:

   Call `mcp__claude_ai_Linear__list_teams` (no args).
   - If the tool errors or returns no teams, **stop** with: "Linear handler needs the Linear MCP. Connect it in Claude Code settings (`https://mcp.linear.app/mcp`), then re-run." Do not fall back to another handler.
   - Match `<linear.team>` against each returned team's `name` (case-insensitive) or `id`. `list_teams` does not return the team key (e.g. `PRE`), so a key value will not match — surface that in the error if a likely-key string (short, all-caps) is configured.
   - If no team matches, **stop** with: "Configured Linear team `<team>` is not in your accessible teams." (List the team names that were returned.)
   - Capture the resolved team `id` for step 4.

2. **Select the project. HARD STOP — DO NOT SKIP.** You MUST ask the user which project to attach the issue to before creating it, using `AskUserQuestion`. Do not infer the project from the title, the team, or recent activity. Do not proceed to step 3 until the user has answered. The ONLY way to skip this prompt is if `linear.default_project` is set in the config file (then use that value as-is and proceed).

   Fetch the team's active projects:

   Call `mcp__claude_ai_Linear__list_projects` with:
   - `teamId`: `<resolved team id from step 1>`
   - `includeArchived`: `false`

   Present the projects to the user via `AskUserQuestion` (header: "Linear project"). Each project is an option labeled `<name>` (with state if useful, e.g. `<name> — <state>`). Include a final "No project (team backlog)" option so the user can opt out explicitly. `AskUserQuestion` enforces a 4-option max — show at most 2 project options (the 2 most recently updated) so 2 projects + "No project" + "Other" fits. The user can pick "Other" to type a specific project name or id. If they type a name, match it case-insensitively against the projects returned by `list_projects` and use the matching project id; if no match, push back ("`<TYPED>` is not a project in team `<team>`") and re-ask. If they type a UUID, use it directly. Capture the chosen project id (or `none`).

3. **Compose the description.** Use the drafted todo's `body` plus a source footer. Linear natively renders markdown, so pass it through unchanged. Omit each footer line if its value is empty/null — do not render `Source PR: #` or `Source PR: #null`:

   ```
   <body>

   ---
   Source branch: <source_branch>       # omit this line entirely if source_branch is empty
   Source PR: #<source_pr>               # omit this line entirely if source_pr is empty
   Blocked by todo: <is_blocked_by>     # omit this line entirely if is_blocked_by is empty
   ```

   If all footer lines are omitted, omit the `---` separator too.

4. **Create the issue.** Call `mcp__claude_ai_Linear__create_issue` with:
   - `teamId`: `<resolved team id from step 1>`
   - `title`: the drafted `title`
   - `description`: the composed description from step 3
   - `projectId`: the chosen project id (omit entirely if the user picked "No project")
   - `priority`: map the drafted todo's `priority` to Linear's 0–4 scale (`urgent` → 1, `high` → 2, `medium` → 3, `low` → 4). If the drafted todo has no priority, use `<linear.default_priority>` (default `3`).
   - `state`: `New` (corresponds to the `new` kanban column — see the Kanban mapping section below). Resolve the state id by listing the team's workflow states and matching by name; if `New` is not present, fall back to `Backlog`.

   Labels are intentionally not passed in v1 — see the config block note.

5. **Return the URL.** The response includes the created issue with a `url` field (and an `identifier` like `ENG-123`). Return `issue.url` directly as this handler's artifact URL for `/add-todo` step 8. (Fallback: if `url` is missing, report the `identifier` and tell the user to open it in Linear.)

This handler does **not** create any `dev_docs/todos/*.md` file, branch, or PR.

## Kanban mapping

Linear's workflow states are fixed: `Backlog`, `New`, `Planned`, `In Progress`, `Completed`, `Canceled`. The seven-column kanban model from `skills/todo/SKILL.md` maps onto those existing states plus a small set of labels — **no custom workflow states are added**.

| Kanban column      | Linear state    | Linear label(s)                                  |
|--------------------|-----------------|--------------------------------------------------|
| `new`              | `New`           | —                                                |
| `needs_refinement` | `New`           | `human-approval-requested`                       |
| `ready`            | `Planned`       | `auto-eligible` (set by promoter)                |
| `in_progress`      | `In Progress`   | `auto-claimed` (concurrency guard)               |
| `blocked`          | `In Progress`   | `blocked`                                        |
| `needs_review`     | `In Progress`   | — (the open PR is the review signal via Linear's GitHub integration) |
| `done`             | `Completed`     | —                                                |

Transitions:
- `/add-todo` (this handler) creates the issue in `New`.
- `/promote-todos` (Linear flavor) scores cards in `New`: HIGH → move to `Planned` and add `auto-eligible`; LOW → leave in `New` and add `human-approval-requested`.
- `/process-todo` (or the nightly job) picks issues in `Planned` with `auto-eligible` and without `auto-claimed`. On claim it moves the issue to `In Progress` and adds `auto-claimed`.
- Linear's GitHub integration moves the issue to `Completed` on PR merge via `Closes <KEY>`. `needs_review` is implicit while the linked PR is open.
- Bail path: revert to `New`, add `human-approval-requested`, remove `auto-claimed`, and leave a comment.

If the team's workflow does not have `New` or `Planned` (Linear teams can customize), the handler should fall back: `New` → `Backlog`, `Planned` → `Todo`. Resolve state ids by listing the team's workflow states (`mcp__claude_ai_Linear__list_*`) and matching by name at runtime.
