# linear handler — /archive-tasks flow

Invoked from `/archive-tasks` when `handler: linear` is configured. Retires
terminal-state Linear issues (`completed`/`canceled` state types) older than the
resolved threshold so the workspace stays under Linear's **free-plan cap of 250
_active_ issues** — archived issues are unlimited and excluded from the cap.

**Shared reference:** see `commands/handlers/linear-common.md` for connection
details, the config schema, the preflight pattern, and the kanban → state-type
mapping this file reuses.

> **The Linear MCP has no archive (or delete) mutation.** It exposes
> `save_issue` (which cannot move an issue to archived) and `delete_*` only for
> comments/attachments/status-updates — there is no `archive_issue`. So the
> retire step **cannot** go through the MCP. The **query** side uses the MCP
> (`list_issues` + `list_workflow_states`), but the **archive** mutation goes
> through Linear's **GraphQL API directly** with a personal API key. This is the
> one handler whose retire op is not MCP-native.

## Primary fix: native team auto-archive (do this first)

Before reaching for the script, point the user at Linear's built-in
auto-archive, which is the zero-maintenance way to stay under the cap:

> **Linear → Settings → Team → Issue statuses & automations → Auto-archive.**
> Set "Automatically archive closed issues after …" to the shortest window the
> team tolerates (the menu's minimum is the floor). Archived issues drop out of
> the 250-issue cap immediately.

State plainly: **native auto-archive is the recommended primary mechanism.** Its
shortest window can still be too long for a workspace closing ~5 issues/day, so
the GraphQL backstop below exists for tighter-than-UI windows and for an
immediate one-shot cleanup when you are already at the cap. If the user only
needs steady-state hygiene, enabling native auto-archive may be all they need —
say so and stop.

## Backstop: the GraphQL `issueArchive` retire step

Use this when the user passes an explicit `--older-than <N>d` tighter than the
native window, or needs to drain a workspace that has already hit the cap.

### Preflight

1. Run the shared preflight from `linear-common.md` (call `<linear-mcp>__list_teams`,
   match `<linear.team>`, capture the team `id`). On failure, stop with the same
   error messages.
2. **Resolve the API key.** The GraphQL call needs a Linear **personal API key**
   (the MCP's OAuth session is not usable for raw GraphQL). Read it from the
   reference configured in `linear.api_key_ref` (see `linear-config.md`), never
   from a literal in the repo:

   ```bash
   LINEAR_API_KEY="$(op read "$LINEAR_API_KEY_REF")"   # e.g. op://Private/Linear API/credential
   ```

   If `linear.api_key_ref` is unset, **stop** with: "Linear archiving needs a
   personal API key. Add `linear.api_key_ref` (a 1Password `op://` reference) to
   `dev_docs/tasks/.task-config.yml` — see `commands/handlers/linear-config.md` →
   'Archive key'." Do not prompt for a pasted key and do not write one to the
   repo.

### Find candidates

3. **Resolve terminal state ids by type, not name** (display names are
   team-configurable). Call `<linear-mcp>__list_workflow_states` with the team
   `id` and collect every state whose `type` is `completed` or `canceled`.
4. **Query terminal issues.** Call `<linear-mcp>__list_issues` scoped the same way
   the rest of the Linear handler scopes — always pass `teamId`, and if
   `linear.default_project` is set (non-empty) also pass it as `projectId` so only
   that project's issues are archived; otherwise omit `projectId` and sweep the
   whole team. Pass `includeArchived: false` (already-archived issues must not be
   re-touched) and the terminal state ids from step 3.
5. **Filter by completion age.** Keep only issues whose terminal timestamp is more
   than `N` days before today: `completedAt` for `completed`-type issues,
   `canceledAt` for `canceled`-type. An issue missing both timestamps is skipped
   (cannot prove its age — never archive on an unknown date). Collect the matching
   issue `id`s and `identifier`s.

### Archive (mutation)

6. **Always print the candidate list first** (identifier + terminal date). If
   `dry-run`, stop here and report "nothing archived (dry-run)".
7. Otherwise call the GraphQL `issueArchive` mutation **once per id** — there is
   **no bulk archive mutation, so loop**. Use `trash: false` (archive, not trash):

   ```bash
   curl -sS https://api.linear.app/graphql \
     -H "Authorization: $LINEAR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"query":"mutation($id:String!){issueArchive(id:$id,trash:false){success}}","variables":{"id":"<ISSUE_ID>"}}'
   ```

   Check each response's `data.issueArchive.success`. On a `false` or an error
   payload for an id, record it and continue the loop — one failure must not abort
   the rest. (The personal API key authenticates as the user; `issueArchive` uses
   the issue **UUID** `id`, not the `identifier` like `PRE-12`.)

### Report

8. Report: the candidate count, how many archived successfully, any ids that
   failed (with the error), and a one-line "now under the 250 cap" note if you can
   compute the remaining active count cheaply (otherwise omit — do not add tool
   calls just for the count). In dry-run, report the candidates and that nothing
   was changed.

## Run it without an agent

Because the backstop needs only the API key, it can run as a standalone scheduled
job with no agent session. The query can also be done in GraphQL (Linear's API
exposes issues with `completedAt`/`canceledAt` and an `archivedAt` null filter),
so a small GitHub Action or cron script on a daily schedule keeps the workspace
under the cap unattended. Store the key as a CI secret (`LINEAR_API_KEY`), mirror
the per-id `issueArchive(trash:false)` loop, and scope the query by team/project
exactly as above. Scheduling guidance for the agent-driven path lives in
`commands/archive-tasks.md` → "Scheduling".
