# Linear handler — /list-tasks flow

Invoked from `/list-tasks` when `handler: linear` is configured. Read-only — no state changes, no claims, no edits. Renders a one-shot snapshot of the team's active issues as a vertical kanban, matching the layout `/list-tasks` uses for the file-based path.

**Shared reference:** see `linear-common.md` for connection details, config schema, preflight pattern, and the kanban mapping table this file reverses.

## Steps

1. **Preflight.** Run the shared preflight from `linear-common.md` (call `list_teams`, match `<linear.team>`, capture team `id`). On any failure, stop with the same error messages.

2. **Resolve project scope.** Resolve the configured projects via the "Resolve configured projects" step in `linear-common.md`.
   - **No projects configured** (the helper returns the synthetic whole-team scope, `id: null`): omit `projectId` — list across all of the team's active issues. No prompt (unchanged from today).
   - **Projects configured and `$ARGUMENTS` is `all`:** scope is the **union** of all configured projects. Use each configured project's `id` as a `projectId` filter (one query per project in step 3) and tag each issue with its project so step 5 can group/label by project.
   - **Projects configured, no `all`:**
     - **Exactly one** configured → use its `id` as the `projectId` filter, no prompt.
     - **Two or more** → ask via `AskUserQuestion` (header: "Linear project") which configured project to view, offering an explicit **`All — all configured projects`** option alongside the projects. Stay within the 4-option max: with **3 or fewer** configured, show them all by `name` + `All`; with **4 or more**, show 2 projects by `name` + "Other" + `All`, where "Other" lets the user type a configured project name (matched case-insensitively against the configured list; re-ask on no match). If the user picks `All`, behave as the `all` argument (union, grouped). Otherwise use the chosen project's `id` as the `projectId` filter.

   The configured-project pick is the only place `/list-tasks` prompts; outside it the command stays a read-only snapshot.

3. **Query issues.** Call `<linear-mcp>__list_issues` with:
   - `teamId`: resolved team id from step 1
   - `projectId`: from step 2 — omit when whole-team; for a single configured project use its `id`; for the `all`/union scope run this query once per configured project `id` and merge the results.
   - `includeArchived`: `false`
   - Limit: 20 **per query** (so for the `all`/union scope each configured project is capped at 20 independently, matching the per-project render in step 5). If a query indicates more issues exist, render a `(showing first 20 of N — narrow to a single project or pass a section filter like "/list-tasks ready")` note at the end of that scope's summary line (per project under the union scope). Pagination/cursor handling is out of scope for v1.

   The goal is "everything still active in the team's kanban." Pull all non-archived issues in the `backlog`, `unstarted`, `started`, and recently-`completed` state types. To avoid over-fetching when `list_issues` doesn't accept a state-type filter directly, first resolve the team's workflow states by calling `<linear-mcp>__list_workflow_states` (with `teamId`), then pass the matching state ids into `list_issues` for each relevant type. Cache the state-id → type map for step 4's grouping.

4. **Group into kanban sections.** Reverse the kanban mapping in `linear-common.md`. For each issue, classify by **state type** (not display name) and label presence:

   | Section            | Match rule                                                                                                                                            |
   | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
   | `new`              | state type `backlog`, no `human-approval-requested` label                                                                                             |
   | `needs_refinement` | state type `backlog`, has `human-approval-requested` label                                                                                            |
   | `ready`            | state type `unstarted` (optionally tagged `auto-eligible`)                                                                                            |
   | `in_progress`      | state type `started`, no `blocked` label, no open linked PR                                                                                           |
   | `blocked`          | state type `started`, has `blocked` label                                                                                                             |
   | `needs_review`     | state type `started`, has an open linked GitHub PR (via Linear's GitHub integration or the explicit `links` attachment from the tracker execute path) |
   | `done`             | state type `completed` — limit to the 10 most recent by `completedAt`                                                                                 |

   If an issue could match both `blocked` and `needs_review`, prefer `blocked` (the more actionable signal).

   **If the `list_issues` payload does not include linked GitHub PR data** (attachments/integrations), treat `needs_review` as best-effort: leave such issues in `in_progress` and skip the `PR #<n> open` annotation. Do **not** call additional tools to enrich PR data — `list-tasks` is read-only and scoped to one snapshot call.

5. **Render** as stacked vertical sections in this fixed order, omitting empty sections:

   `new` → `needs_refinement` → `ready` → `in_progress` → `blocked` → `needs_review` → `done`

   When the scope is the **union** of all configured projects (`/list-tasks all` or the `All` pick), repeat this section layout once per configured project under a `# <project name>` header (omitting projects with no active issues), so each project's kanban is labeled and grouped separately. For a single-project or whole-team scope, render one un-grouped kanban as before.

   Use the same `## <section> (N)` header, single-line bullet, `---` separator layout as the file-based path in `commands/list-tasks.md` step 4. Sort within each section by Linear priority — **urgent first, then high, medium, low, then none last**. Note that Linear stores `none` as `0`, so do NOT sort numerically ascending; map `0` to the lowest rank (after `4=low`). Then by `updatedAt` (oldest first).

   Card line format:

   ```
   - [high] PRE-12 Fix broken import — assignee dan
   ```

   Field mapping (vs. the `repo-pr` card line, which uses slug + frontmatter):

   | Field       | Source                                                                        |
   | ----------- | ----------------------------------------------------------------------------- |
   | Priority    | Linear `priority` mapped back to urgent/high/medium/low (0 = none sorts last) |
   | Identifier  | Linear `identifier` (e.g. `PRE-12`)                                           |
   | Title       | Linear issue `title`                                                          |
   | Assignee    | Linear `assignee.displayName` (omit `— assignee …` if unassigned)             |
   | Annotations | See below                                                                     |

   Inline annotations to surface when present (comma-separated, after the assignee):
   - `human-approval-requested` (label present)
   - `blocked` — bare annotation when the `blocked` label is set. (Reason text is intentionally not surfaced here — no comment-fetching tool is in this command's `allowed-tools`. Check the issue in Linear for context.)
   - `PR #<n> open` — for `needs_review` cards, the linked PR number. If the linked PR is detected but the number is missing from the payload, render `PR open` instead. Omit entirely if no PR link is present.
   - `auto-claimed` (label present, in `in_progress`)

6. **Summary line.** Same shape as the file-based path:

   ```
   8 issues (1 new, 1 needs_refinement, 2 ready, 1 in_progress, 0 blocked, 2 needs_review, 1 done)
   ```

   Append the truncation note from step 3 if applicable.

7. **Filter argument.** If `$ARGUMENTS` is a section name (`new|needs_refinement|ready|in_progress|blocked|needs_review|done`), render only that section. `all` is **not** a section filter — it is the project-scope widener handled in step 2 (union of all configured projects). Default: every non-empty section.
