# Linear handler — /add-task flow

Creates a Linear issue via `<linear-mcp>__save_issue` (called without `id` — that's the create primitive). The new issue is filed under the configured team and attached to a project.

**Shared reference:** see `linear-common.md` for the connection details, full config schema, preflight pattern, and kanban mapping. This file only documents what `/add-task` does on top of that.

> **One value, two field names.** The Linear MCP is inconsistent: `list_projects` takes `teamId`, `save_issue` takes `team`. Both accept the same value — the team's UUID resolved in step 1. Pass it under whichever name the call requires; this doc uses the exact field name in each call below.

> **Required interaction:** step 2 (project selection) resolves the configured projects via the "Resolve configured projects" step in `linear-common.md` and MUST prompt the user via `AskUserQuestion` to pick among them — **unless exactly one project is configured** (use it directly, no prompt). This applies in auto mode too. When **no** projects are configured (the helper returns the synthetic whole-team scope), fall back to today's flow: prompt among the team's projects with an explicit "No project (team backlog)" opt-out. If you find yourself about to call `save_issue` without having resolved the project this way, stop and go back to step 2.

## Steps

1. **Preflight.** Run the preflight from `linear-common.md` (call `list_teams`, match `<linear.team>`, capture team `id`). Use the same failure messages.

2. **Select the project. HARD STOP — DO NOT SKIP.** Resolve the configured projects via the "Resolve configured projects" step in `linear-common.md`; the result drives the prompt. Do not infer the project from the title, the team, or recent activity. Do not proceed to step 3 until the project is resolved.

   **One or more projects configured** (the helper returns real `id`s): the issue MUST be attached to one of the **configured** projects — there is **no** "team backlog" opt-out once projects are configured.
   - **Exactly one** configured project → use its `id` directly, **skip the prompt**, proceed to step 3.
   - **Two or more** configured → ask via `AskUserQuestion` (header: "Linear project") which configured project to attach to. Each option is labeled by the project's `name` (resolved lazily via `list_projects` when absent — see the helper). `AskUserQuestion` enforces a 4-option max — show at most 3 configured projects (the first 3 in config order) plus "Other". "Other" lets the user type one of the **configured** project names (matched case-insensitively against the configured list) or a configured project's UUID. The typed name or UUID MUST match a configured project — if it matches none, push back ("`<TYPED>` is not a configured project") and re-ask. Do not offer projects outside the configured list. Capture the chosen project id.

   **No projects configured** (the helper returns the synthetic whole-team scope, `id: null`): keep today's flow. Fetch the team's active projects:

   Call `<linear-mcp>__list_projects` with:
   - `teamId`: the team UUID from step 1
   - `includeArchived`: `false`

   Present the projects to the user via `AskUserQuestion` (header: "Linear project"). Each project is an option labeled `<name>` (with state if useful, e.g. `<name> — <state>`). Include a final "No project (team backlog)" option so the user can opt out explicitly. `AskUserQuestion` enforces a 4-option max — show at most 2 project options (the 2 most recently updated) so 2 projects + "No project" + "Other" fits. The user can pick "Other" to type a specific project name or id. If they type a name, match it case-insensitively against the projects returned by `list_projects` and use the matching project id; if no match, push back ("`<TYPED>` is not a project in team `<team>`") and re-ask. If they type a UUID, use it directly. Capture the chosen project id (or `none`).

3. **Compose the description.** Use the drafted task's `body` plus a source footer. Linear natively renders markdown, so pass it through unchanged. Omit each footer line if its value is empty/null — do not render `Source PR: #` or `Source PR: #null`:

   ```
   <body>

   ---
   Source branch: <source_branch>       # omit this line entirely if source_branch is empty
   Source PR: #<source_pr>               # omit this line entirely if source_pr is empty
   Blocked by task: <is_blocked_by>     # include if is_blocked_by is another issue
                                         # (e.g. a file-based task slug like `fix-broken-import`).
                                         # If is_blocked_by is a Linear identifier (matches
                                         # /^[A-Z]+-\d+$/), do add this line — 
                                         # AND use the native blockedBy relationship in step 4.
   ```

   If all footer lines are omitted, omit the `---` separator too.

4. **Create the issue.** Call `<linear-mcp>__save_issue` (no `id` — that's how this MCP creates) with:
   - `team`: the team UUID from step 1
   - `title`: the drafted `title`
   - `description`: the composed description from step 3
   - `project`: the chosen project id (omit entirely if the user picked "No project")
   - `priority`: map the drafted task's `priority` to Linear's 0–4 scale (`urgent` → 1, `high` → 2, `medium` → 3, `low` → 4). If the drafted task has no priority, use `<linear.default_priority>` (default `3`).
   - `estimate`: the drafted task's `size`, passed through unchanged. Linear's native `estimate` is the same Fibonacci scale as `size` (`1` / `2` / `3` / `5` — see the config note in `linear-common.md`), so no conversion is needed. **Do not omit this** when `size` is set: `/do-tasks` (tracker path) filters out issues with no `estimate` (`no estimate set`), so a task created without one would never be claimable. Omit only if the drafted task genuinely has no `size`.
   - `state`: the team's default `Backlog` workflow state (corresponds to the `new` kanban column — see the kanban mapping in `linear-common.md`). Resolve the state id by listing the team's workflow states and matching `type: "backlog"`; if multiple, prefer the team's default.
   - `blockedBy`: collect every `is_blocked_by` entry that matches `/^[A-Z]+-\d+$/` (a Linear identifier like `PRE-12`) and pass them as a list so Linear renders native blocker relationships (clickable, shows up in the blocked issue's "Blocking" list, surfaces in the project view, drives downstream automation). `is_blocked_by` may be a **single value** (pass `[<is_blocked_by>]`) or a **list** (pass every matching entry, e.g. `[<id1>, <id2>]`) — the schema allows both. `save_issue` accepts identifier strings here — no UUID lookup needed. Omit this field entirely if `is_blocked_by` is empty or has no Linear-identifier entries (e.g. file-based task slugs); any non-matching entry is carried by the markdown footer line from step 3 instead.

   Labels are intentionally not passed in v1 — see the config block note in `linear-common.md`.

5. **Return the URL.** The response includes the created issue with a `url` field (and an `identifier` like `ENG-123`). Return `issue.url` directly as this handler's artifact URL for `/add-task` step 8. (Fallback: if `url` is missing, report the `identifier` and tell the user to open it in Linear.)

This file does **not** create any `dev_docs/tasks/*.md` file, branch, or PR.
