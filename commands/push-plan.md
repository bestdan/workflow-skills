---
description: Push a vetted local plan to the configured tracker — repo-pr is a no-op; Linear creates one issue per task under a project, gh-issue under a milestone, and jira under an Epic (with native blocker links), in dependency order
allowed-tools: Bash(git *), Bash(find *), Bash(grep *), Bash(cat *), Bash(gh *), Glob, Grep, Read, Write, Edit, AskUserQuestion, mcp__linear__list_teams, mcp__linear__list_projects, mcp__linear__save_project, mcp__linear__list_workflow_states, mcp__linear__save_issue, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__save_project, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__getJiraProjectIssueTypesMetadata, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__createJiraIssue, mcp__claude_ai_Atlassian__getIssueLinkTypes, mcp__claude_ai_Atlassian__createIssueLink, mcp__claude_ai_Atlassian__getJiraIssue, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__getJiraProjectIssueTypesMetadata, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__createJiraIssue, mcp__atlassian__getIssueLinkTypes, mcp__atlassian__createIssueLink, mcp__atlassian__getJiraIssue
argument-hint: "<plan-name> [--ready-only]"
---

# Push Plan

Take a plan drafted locally by `/plan-with-docs` (`dev_docs/tasks/<name>_plan/`)
and push it to the tracker configured for this repo. This is the **local-first**
flow: plans are always drafted and vetted as files first; pushing is a separate,
deliberate, re-runnable act — never an auto-sync on write.

The design that this command implements (trigger, readiness, mapping,
idempotency, reverse drift) is the spike
`dev_docs/tasks/task_loop_improvements_plan/plan_tracker_sync_design.md` — read
it for the full rationale and rejected alternatives.

This command **reuses each handler's existing add-flow** to create issues rather
than re-implementing create logic. Its own job is only: resolve the container,
order the tasks topologically, resolve `is_blocked_by` slugs to tracker ids, and
record the created ids back into the files (idempotency).

## Modes

- `/push-plan <name>` — push the whole plan (every non-epic task), creating only
  the issues that don't already exist (create-missing-only).
- `/push-plan <name> --ready-only` — push just the `status: ready` subset; hold
  the not-ready tasks for a later run.

## 1. Resolve the handler

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- File absent, no `handler:` key, or `handler: repo-pr` → **no-op** (same default
  resolution as `/add-task`). Print exactly this one line and **stop**:
  `repo-pr handler: plans already live as task files — no external tracker to push to`.
  For this handler the task files _are_ the destination (they land via the user's
  normal commit/PR), so there is nothing to sync.
- `handler: linear` → run the Linear push flow (§4).
- `handler: gh-issue` → run the gh-issue push flow (§5).
- `handler: jira` → run the jira push flow (§5b).
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

## 2. Resolve and read the plan

1. Resolve the plan directory from `<name>`: `dev_docs/tasks/<name>_plan/` under
   the repo root. If `<name>` already ends in `_plan`, don't double it. If the
   directory doesn't exist, **stop** and list the plan directories that do:

   ```bash
   find "$(git rev-parse --show-toplevel)/dev_docs/tasks" -maxdepth 1 -type d -name '*_plan'
   ```
2. Find every `*.md` under the plan directory (recurse into `phase_N/` folders).
   Split each into (frontmatter, body).
   - The **epic file** is the one with `type: epic` (normally `<name>_plan.md`).
     It is the container, **not** a task — never create an issue for it. If **no**
     file has `type: epic`, **stop** and report it (a plan needs an overview epic
     to map to the tracker container). If **multiple** do, prefer the one named
     `<name>_plan.md`; if none matches that name, **stop** and report the
     ambiguity rather than guessing.
   - **Task files** are the rest with parseable frontmatter. Files with no
     frontmatter are not task cards — skip them.

## 3. Readiness check — push by default

Readiness is a **reported check, not a hard gate** (spike §2): a plan can
legitimately ship with tasks that still need their own sub-plan.

1. **Classify** the task files: `ready` (`status: ready`) vs **not-ready**
   (`new` / `needs_refinement` / `blocked` / anything else).
2. **Confirm.** Show the user the summary of what will be created — the container
   and the ordered list of issues to create — and, separately, the **not-ready
   set**, each line carrying _what's needed to resolve it_: the task's
   `needs_refinement` reason if one is recorded, else `unscored (status: new) —
   run /promote-tasks`. Ask for confirmation via `AskUserQuestion`.
3. **Push by default.** On confirm, push the whole plan (create-missing-only,
   §6). Not-ready tasks are still created — it's fine for a tracker issue to need
   further breakdown as long as its gap is recorded — and they're echoed back as
   the follow-up set in the report. With `--ready-only`, push just the `ready`
   subset; the held tasks land on a later re-push (safe, create-missing-only).

   > **`--ready-only` caveat — a held blocker's native link is lost.** If a
   > pushed `ready` task is blocked by a task held back this run, that blocker's
   > slug isn't in the map yet, so it falls through to the markdown footer rather
   > than the handler's resolved form — no native `blockedBy` on Linear, a slug
   > instead of `Blocked by: #<number>` on gh-issue. Because v1 never updates an
   > existing issue (§6), that resolved link stays missing even after the held
   > task is pushed later. **Warn** when this happens so the user can push the
   > whole plan instead if the resolved links matter.

Already-pushed tasks (those with a `tracker_id`) are skipped regardless — they
still appear in the summary as `already pushed (<tracker_id>)`.

## 4. Linear path

### 4.1 Preflight

Run the `linear-common.md` preflight (call `<linear-mcp>__list_teams`, match
`<linear.team>`, capture the team `id`; reuse its failure messages). Read
`commands/handlers/linear-common.md` for the MCP namespace note (`<linear-mcp>__`
is `mcp__linear__` or `mcp__claude_ai_Linear__` depending on the install) and the
config schema. If the relative path doesn't resolve, find it with **Glob**
(`**/commands/handlers/linear-common.md`).

### 4.2 Resolve the container (epic → Linear project)

The overview epic maps to a Linear **project** (spike §3.1). Resolve it in this
order, and **reuse before create** so re-push never duplicates the container:

1. If the **epic file** already records a `tracker_id`, reuse that project id.
2. Else if `linear.default_project` is set in config, use it.
3. Else **create** a project named after the epic `title` via
   `<linear-mcp>__save_project` (no `id` — that's the create primitive) with
   `teamIds: [<team id>]` and `name: <epic title>`. Capture the returned project
   `id` (and `url` if present).

Whenever a project is newly created (case 3), **write its id back** onto the epic
file's frontmatter: `tracker_id: <project id>` (+ optional `tracker_url`). Cases
1–2 write nothing new.

### 4.3 Order the tasks (topological)

Push in dependency order so every blocker is created before its dependents (spike
§3.3). Build the order from `is_blocked_by` (a string or a list — honor both):
a task comes after every task it is blocked by. Restrict edges to **slugs that
name another task file in this plan** — entries that are already tracker ids
(`/^[A-Z]+-\d+$/`) or point outside the plan are not ordering edges here. A bare
slug (not a tracker id) that matches **no** task file in the plan is almost always
a typo of an in-plan slug, not a real external reference — **warn** (name the
offending slug and the task that references it) so a misspelled blocker isn't
silently dropped to a footer in §4.4. If the dependencies contain a **cycle**,
that's a plan bug — **stop** and report the cycle (the slugs involved); do not
push a partial order.

### 4.4 Create issues (reuse `linear-add.md`, create-missing-only)

Maintain a live **slug → tracker-id map**. **Seed it first** from every task
file that already has a `tracker_id` (including the skipped, already-pushed ones)
— otherwise a freshly added task that depends on an already-pushed one can't
resolve its blocker (spike §3.3).

Walk the tasks in topological order. For each:

1. **Skip if already pushed.** A task with a non-empty `tracker_id` is **not**
   recreated (create-missing-only). Its id is already in the map from the seed
   step. Report it as `already pushed`.
2. **Skip if held.** With `--ready-only`, skip non-`ready` tasks (don't create,
   don't record — they're the held follow-up set).
3. **Build the drafted task** — the normalized contract from `commands/add-task.md`
   step 5 (`title`, `body` = the Context/Task/Acceptance markdown, `priority`,
   `size`, `tags`, `source_branch`, `source_pr`, `related_files`,
   `is_blocked_by`). **Translate `is_blocked_by` through the map before handing it
   off, preserving its shape:** rewrite each entry that names a plan task to that
   task's tracker id; **pass through unchanged** any entry that already matches
   `/^[A-Z]+-\d+$/` or isn't in the map (an external/manual reference) — never
   fail on those. A single-value `is_blocked_by` stays a single value; a list
   stays a list (each entry translated independently), so a task with **multiple**
   blockers hands off a list of resolved ids.
4. **Create the issue** by following `commands/handlers/linear-add.md` steps 3–5
   (compose description, `save_issue`, return the url) with the resolved team and
   the **container project id from §4.2**. Because the container is already
   resolved here, **skip `linear-add.md` step 2's project prompt** — pass the
   project id directly. `linear-add` step 4 collects every `is_blocked_by` entry
   matching `/^[A-Z]+-\d+$/` and renders them as native `blockedBy` relationships
   — for both a single id and a **list** of them, which is exactly what the map
   translation produces, so every resolved blocker links natively.
5. **Record the id back** into the task file's frontmatter: `tracker_id:
   <identifier>` (e.g. `PRE-12`) and optional `tracker_url`, and add
   `<slug> → <identifier>` to the map so later dependents resolve.

## 5. gh-issue path

The gh-issue path mirrors the Linear path (§4) — same topological order, same
live slug→id map, same create-missing-only idempotency (§6). Only the container
and the blocker encoding differ: gh-issue groups under a **milestone** and has
**no native dependency edge**, so blockers become a `Blocked by: #<number>` body
footer (spike §3.1 / §3.3).

> **Value note (spike §3 / O1).** `/do-tasks` execution is Linear-only, so a
> gh-issue push produces a board you then work **manually** — it does not become
> auto-executable. The grouping + blocker footer are still worth pushing for
> visibility; just don't expect the headless runner to drain it.

### 5.1 Preflight

Run the `gh-issue` create-flow preflight: `gh auth status 2>&1`. On failure use
the create-flow's handling (TLS/x509 → sandbox keychain hint; otherwise report
the auth failure) and **stop** — do not fall back to another handler. Read
`commands/handlers/gh-issue.md` for the auth and create steps reused below; if
the relative path doesn't resolve, find it with **Glob**
(`**/commands/handlers/gh-issue.md`). Resolve `<repo>` from `gh-issue.repo`
(omit `--repo` to use the current repo, matching the create flow).

### 5.2 Resolve the container (epic → milestone)

The overview epic maps to a GitHub **milestone** (spike §3.1). Resolve it
**reuse-before-create** so re-push never duplicates the container:

1. If the **epic file** already records a `tracker_id`, reuse that milestone
   (`<number>` for a milestone, or the `plan:<name>` label sentinel from the
   fallback below — honor whichever is recorded).
2. Else **look up, then create.** First check whether a milestone with the epic
   `title` already exists (a manual creation, or a prior run that didn't record
   the id), and **reuse it** if so — creating it again would 422, which would
   wrongly trip the label fallback in step 3:

   ```bash
   # Reuse an existing milestone with this title, if any (empty output ⇒ none).
   gh api "repos/<repo>/milestones?state=all" \
     --jq '.[] | select(.title == "<epic title>") | {number, url: .html_url}'

   # Only if the lookup returned nothing, create it:
   gh api "repos/<repo>/milestones" -f title="<epic title>" \
     --jq '{number, url: .html_url}'
   ```

   (Use the resolved `<repo>`, or the current repo's `OWNER/NAME` from
   `gh repo view --json nameWithOwner -q .nameWithOwner` when `gh-issue.repo` is
   unset — the `gh api` path needs an explicit repo.) Capture the milestone
   `number` and `html_url`. **Write them back** onto the epic file's frontmatter
   (`tracker_id: <number>`, `tracker_url: <html_url>`).
3. **Fallback (spike O2).** If milestone creation fails (e.g. the token lacks
   issues-write on milestones), fall back to a shared **`plan:<name>` label**:
   `gh label create "plan:<name>" --repo "<repo>" 2>/dev/null`, record
   `tracker_id: label:plan:<name>` on the epic file, and **note the downgrade in
   the report** (weaker grouping — issues share a label, not a milestone).

### 5.3 Order the tasks (topological)

Use the same ordering algorithm as **§4.3** (a task comes after every task it is
blocked by; a cycle is a plan bug — **stop** and report it; an unresolvable bare
slug — **warn**). The only difference is the "already an id" shape: for gh-issue
an `is_blocked_by` entry that is already an issue reference matches
`/^(\S*#)?\d+$/` (`#142`, `142`, or `owner/repo#142`) and is **not** an ordering
edge.

### 5.4 Create issues (reuse `gh-issue.md` create-flow, create-missing-only)

Maintain a live **slug → issue-reference map**, seeded first from every task file
that already has a `tracker_id` (including skipped, already-pushed ones), so a
freshly added task can resolve a blocker that was pushed on an earlier run.

Walk the tasks in topological order. For each:

1. **Skip if already pushed.** A task with a non-empty `tracker_id` is **not**
   recreated; report it as `already pushed (<tracker_id>)`. Its id is already in
   the map from the seed step.
2. **Skip if held.** With `--ready-only`, skip non-`ready` tasks.
3. **Build the drafted task** — the normalized contract from `commands/add-task.md`
   step 5. **Translate `is_blocked_by` through the map before handing it off:**
   rewrite each entry that names a plan task to that task's issue reference
   (`#<number>`); **pass through unchanged** any entry already shaped like an
   issue ref or absent from the map. Render the resolved blockers as the footer
   `Blocked by: #<number>[, #<number>…]` (see `gh-issue.md` step 2 — passing
   issue-ref values makes its footer read `Blocked by: …`). Blockers held back by
   `--ready-only` aren't in the map yet, so they fall through to a slug footer and
   never gain the `#<number>` form on a later create-missing-only run — **warn**,
   matching the Linear `--ready-only` caveat in §3.
4. **Create the issue** by following `gh-issue.md` steps 2–5 (build body + footer,
   ensure labels, `gh issue create`, return the URL), adding the container from
   §5.2: `--milestone "<milestone number>"` (milestone path — pass the resolved
   **number**, not the title, so a title with shell-unsafe characters or a later
   rename can't break it) or `--label "plan:<name>"` (fallback path), alongside
   any configured `gh-issue.labels`.
5. **Record the id back** into the task file's frontmatter: `tracker_id`
   (`owner/repo#<number>`, or `#<number>` when `gh-issue.repo` is unset) and
   `tracker_url` (the printed issue URL), and add `<slug> → #<number>` to the map
   so later dependents resolve.

## 5b. jira path

The jira path mirrors the Linear path (§4) — same topological order, same live
slug→id map, same create-missing-only idempotency (§6). It uses the Atlassian MCP
(`<atlassian-mcp>__*`, **not** a CLI), so it needs that MCP connected rather than
any local tooling. Two things differ from Linear: the container is a Jira **Epic**
(children set their `parent` to its key), and blockers become native issue links
created in a **second pass** — because `createJiraIssue` has no link parameter,
every issue must exist before its `is_blocked_by` edges can be drawn.

> **MCP namespace.** `<atlassian-mcp>__` is `mcp__claude_ai_Atlassian__` or
> `mcp__atlassian__` depending on the install (see `commands/handlers/jira-config.md`),
> parallel to `<linear-mcp>__` in §4 — substitute the prefix loaded in your session.
> The `allowed-tools` front-matter names both concrete prefixes.

> **Value note (spike §3 / O1).** `/do-tasks` execution is Linear-only, so — like
> the gh-issue push — a jira push produces a board you then work **manually**. The
> epic grouping + native blocker links are still worth pushing for visibility.

### 5b.1 Preflight

Run the `jira` create-flow preflight (`commands/handlers/jira.md` step 1): call
`<atlassian-mcp>__getAccessibleAtlassianResources` and confirm a resource
whose `url` matches `https://<jira.site>`. On failure, reuse the create-flow's
messages and **stop** — do not fall back to another handler. Read
`commands/handlers/jira.md` for the config block (`jira.site`, `jira.project`,
`jira.issue_type`, `jira.labels`) and the create/link steps reused below; if the
relative path doesn't resolve, find it with **Glob** (`**/commands/handlers/jira.md`).
Confirm the project has an `Epic` issue type (and the configured `jira.issue_type`)
via `<atlassian-mcp>__getJiraProjectIssueTypesMetadata`
(`cloudId: <jira.site>`, `projectIdOrKey: <jira.project>`); if `Epic` is absent, **stop** and report it (no
container type to map the overview epic to).

### 5b.2 Resolve the container (epic → Jira Epic)

The overview epic maps to a Jira **Epic** issue (spike §3.1). Resolve it
**reuse-before-create** so re-push never duplicates the container:

1. If the **epic file** already records a `tracker_id`, reuse that Epic key.
2. Else **look up, then create.** First check whether an Epic with the epic
   `title` already exists (a manual creation, or a prior run that didn't record the
   key), and reuse it if so — call `<atlassian-mcp>__searchJiraIssuesUsingJql`
   with `cloudId: <jira.site>`, `jql: project = "<project>" AND issuetype = Epic AND summary ~ "<epic title>"`,
   and `fields: ["summary"]`. If a result's `summary` matches the epic title
   exactly, reuse its `key`. Otherwise create the Epic via
   `<atlassian-mcp>__createJiraIssue` (`cloudId: <jira.site>`, `projectKey: <project>`,
   `issueTypeName: "Epic"`, `summary: <epic title>`, `description:` the epic body,
   `contentFormat: "markdown"`). Capture the new Epic `key` and `webUrl`.

Whenever an Epic is resolved by **lookup or create** (case 2 — i.e. any time case 1
didn't already supply it), **write its key back** onto the epic file's frontmatter:
`tracker_id: <epic key>` (+ optional `tracker_url`), so a later run skips the lookup
(matching gh-issue §5.2, which writes back on both reuse and create). Case 1 writes
nothing new.

### 5b.3 Order the tasks (topological)

Use the same ordering algorithm as **§4.3** (a task comes after every task it is
blocked by; a cycle is a plan bug — **stop** and report it; an unresolvable bare
slug — **warn**). The only difference is the "already an id" shape: for jira an
`is_blocked_by` entry that is already an issue key matches `/^[A-Z][A-Z0-9]*-\d+$/`
(e.g. `PLAT-142`, or a single-letter project key like `X-1`) and is **not** an ordering edge.

### 5b.4 Create issues (reuse `jira.md` create-flow, create-missing-only)

Maintain a live **slug → issue-key map**, seeded first from every task file that
already has a `tracker_id` (including skipped, already-pushed ones), so a freshly
added task can resolve a blocker pushed on an earlier run.

Walk the tasks in topological order. For each:

1. **Skip if already pushed.** A task with a non-empty `tracker_id` is **not**
   recreated; report it as `already pushed (<tracker_id>)`. Its key is already in
   the map from the seed step.
2. **Skip if held.** With `--ready-only`, skip non-`ready` tasks.
3. **Build the drafted task** — the normalized contract from `commands/add-task.md`
   step 5. The blocker translation is **deferred to §5b.5** (links are a second
   pass), so nothing about `is_blocked_by` is passed at create time.
4. **Create the issue** by following `jira.md` steps 3–5 (compose description,
   `createJiraIssue`, return the url) with `issueTypeName: <jira.issue_type>`
   (default `Task`) and **`parent: <epic key from §5b.2>`**. Because the container
   is already resolved here, **skip `jira.md` step 2's epic-selection prompt** —
   pass the epic key directly as `parent`. Apply `jira.labels` via
   `additional_fields` exactly as the create flow does.
5. **Record the key back** into the task file's frontmatter: `tracker_id: <key>`
   (e.g. `PLAT-142`) and optional `tracker_url`, and add `<slug> → <key>` to the
   map so later dependents resolve.

### 5b.5 Second pass — native blocker links

After **every** issue in the batch exists (so all blocker keys are in the map),
walk the tasks again and translate each `is_blocked_by` through the map: for a task
A blocked by B, follow `jira.md`'s **`## Link`** section to create the native
`Blocks` link (`inwardIssue: <B>` the blocker, `outwardIssue: <A>` the blocked). A
task with a **list** of blockers gets one link per resolved blocker. Entries that
stay bare slugs (a blocker held back by `--ready-only`, or an out-of-plan
reference) have no key to link to — **warn** and skip them, matching the Linear
`--ready-only` caveat in §3. The `## Link` step is itself create-missing-only, so a
re-push adds no duplicate edges.

## 6. Idempotency

The behavior above is **create-missing-only** and safe to re-run (spike §4):

- A file with a non-empty `tracker_id` is skipped, never duplicated.
- A recorded container id is reused, never duplicated.
- Skipped files still feed the slug→id map, so newly added tasks resolve their
  blockers on a later push.
- For jira, the §5b.5 link pass is create-missing-only too: it checks the
  dependent's existing `Blocks` links before adding one, so a re-push draws no
  duplicate "is blocked by" edges.
- v1 **never updates or deletes** remote issues, and **never deletes** the local
  plan files — they keep their `tracker_id` as a traceable back-link (spike §5).
  Local `status:` goes stale after push; the tracker becomes the source of truth.

## 7. Report

Print:

- The container: created (`<title>` → `<project / milestone / Epic id>`) or reused
  (`<id>`); for gh-issue, note when the `plan:<name>` label fallback was used; for
  jira, note any blocker links skipped because the `Blocks` link type was absent
  (§5b.5).
- **Created:** one line per new issue — `<slug> → <identifier> (<url>)`, with its
  resolved blockers if any.
- **Already pushed:** skipped files with their `tracker_id`.
- **Follow-up set:** not-ready tasks that were pushed anyway (whole-plan mode),
  or held (`--ready-only`), each with what's needed to resolve it.

A re-run with no changes should report only "already pushed" lines and "container
reused" — the signal that idempotency held.
