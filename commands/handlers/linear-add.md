# Linear handler — /add-todo flow

Creates a Linear issue via `<linear-mcp>__create_issue`. The new issue is filed under the configured team and optionally attached to a project.

**Shared reference:** see `linear-common.md` for the connection details, full config schema, preflight pattern, and kanban mapping. This file only documents what `/add-todo` does on top of that.

> **Required interaction:** step 2 (project selection) MUST prompt the user via `AskUserQuestion` unless `linear.default_project` is set in config. This applies in auto mode too. Treat a missing or empty `linear.default_project` (including `null`, `""`, or the key being absent from the config block) as "not set" — you MUST prompt. If you find yourself about to call `create_issue` without having asked AND without a non-empty `linear.default_project`, stop and go back to step 2.

## Steps

1. **Preflight.** Run the preflight from `linear-common.md` (call `list_teams`, match `<linear.team>`, capture team `id`). Use the same failure messages.

2. **Select the project. HARD STOP — DO NOT SKIP.** You MUST ask the user which project to attach the issue to before creating it, using `AskUserQuestion`. Do not infer the project from the title, the team, or recent activity. Do not proceed to step 3 until the user has answered. The ONLY way to skip this prompt is if `linear.default_project` is set in the config file (then use that value as-is and proceed).

   Fetch the team's active projects:

   Call `<linear-mcp>__list_projects` with:
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

4. **Create the issue.** Call `<linear-mcp>__create_issue` with:
   - `teamId`: `<resolved team id from step 1>`
   - `title`: the drafted `title`
   - `description`: the composed description from step 3
   - `projectId`: the chosen project id (omit entirely if the user picked "No project")
   - `priority`: map the drafted todo's `priority` to Linear's 0–4 scale (`urgent` → 1, `high` → 2, `medium` → 3, `low` → 4). If the drafted todo has no priority, use `<linear.default_priority>` (default `3`).
   - `state`: the team's default `Backlog` workflow state (corresponds to the `new` kanban column — see the kanban mapping in `linear-common.md`). Resolve the state id by listing the team's workflow states and matching `type: "backlog"`; if multiple, prefer the team's default.

   Labels are intentionally not passed in v1 — see the config block note in `linear-common.md`.

5. **Return the URL.** The response includes the created issue with a `url` field (and an `identifier` like `ENG-123`). Return `issue.url` directly as this handler's artifact URL for `/add-todo` step 8. (Fallback: if `url` is missing, report the `identifier` and tell the user to open it in Linear.)

This file does **not** create any `dev_docs/todos/*.md` file, branch, or PR.
