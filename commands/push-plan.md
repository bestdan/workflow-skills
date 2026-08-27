---
description: Push a vetted local plan to the configured tracker — repo-pr is a no-op; Linear creates one issue per task under a project, gh-issue under a milestone, and jira under an Epic (with native blocker links), in dependency order
allowed-tools: Bash(git *), Bash(find *), Bash(grep *), Bash(cat *), Bash(gh *), Glob, Grep, Read, Write, Edit, AskUserQuestion, mcp__linear__list_teams, mcp__linear__list_projects, mcp__linear__save_project, mcp__linear__list_workflow_states, mcp__linear__save_issue, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__save_project, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__getJiraProjectIssueTypesMetadata, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__createJiraIssue, mcp__claude_ai_Atlassian__editJiraIssue, mcp__claude_ai_Atlassian__getIssueLinkTypes, mcp__claude_ai_Atlassian__createIssueLink, mcp__claude_ai_Atlassian__getJiraIssue, mcp__claude_ai_Atlassian__getTransitionsForJiraIssue, mcp__claude_ai_Atlassian__transitionJiraIssue, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__getJiraProjectIssueTypesMetadata, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__createJiraIssue, mcp__atlassian__editJiraIssue, mcp__atlassian__getIssueLinkTypes, mcp__atlassian__createIssueLink, mcp__atlassian__getJiraIssue, mcp__atlassian__getTransitionsForJiraIssue, mcp__atlassian__transitionJiraIssue
argument-hint: "<plan-name> [--ready-only]"
---

# Push Plan

Take a plan drafted locally by `/plan-with-docs` (`dev_docs/tasks/<name>_plan/`)
and push it to the tracker configured for this repo. This is the **local-first**
flow: plans are always drafted and vetted as files first; pushing is a separate,
deliberate, re-runnable act — never an auto-sync on write.

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

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `commands/task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

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
2. Else call the **"Resolve configured projects"** step in
   `commands/handlers/linear-common.md` (do **not** read the scalar
   `default_project`). A plan goes to **one** project — no per-task routing:
   - **Exactly one** configured project (the list has a single entry with a
     non-null `id`) → use it directly, no prompt.
   - **Multiple** configured projects → **prompt** via `AskUserQuestion` which one
     to push this plan into. Offer the **3 most-recently-updated** configured
     projects by `name` (resolving names lazily via `<linear-mcp>__list_projects`)
     plus the automatic **"Other"** (4th slot) to type a name, matched
     case-insensitively against the configured list — the 4-option cap (see
     `linear-add.md`) means you can't always list them all. Use the chosen
     project's `id`.
   - **Whole-team scope** (the list is the single synthetic entry with `id: null`,
     i.e. `linear.projects` absent/empty) → no pin; fall through to case 3.

   (`--project X` narrowing is deferred; don't build it here.)
3. Else **create** a project named after the epic `title` via
   `<linear-mcp>__save_project` (no `id` — that's the create primitive) with
   `teamIds: [<team id>]`, `name: <epic title>`, and `description: <overview
   body>` (the epic file's markdown below its frontmatter — Goal / Scope /
   Approach / Tasks / Open questions, so the overview survives in the tracker once
   the local file is deleted, §7). Capture the returned project `id` (and `url` if
   present).

Whenever a project is resolved in **case 2** (used directly or picked from the
prompt) or **newly created** in case 3, **write its id back** onto the epic file's
frontmatter: `tracker_id: <project id>` (+ optional `tracker_url`) — so a re-push
hits case 1 and never re-prompts or re-resolves (matching the Jira and GitHub
paths). Only case 1, which already carries the id, writes nothing new.

The **description** carries the overview. It is set unconditionally on a freshly
created container; on a reused one it is written **only when the container is
plan-dedicated**, so a shared project's description is never clobbered:

- **Case 3 (create)** always sets `description: <overview body>` (above), so a
  freshly created project owns the overview.
- **Case 2 (reuse)** **overwrites** the description with the current overview body
  **only if the project is plan-dedicated**: its `name` equals the epic `title`
  (case-insensitive). A same-title project is the plan's own container, so it is
  refreshed to the latest overview on every push — no content comparison. A
  configured project picked for a plan is normally a standing project whose name ≠
  the epic title, so this is **false** and its description is left untouched.
- Otherwise (a shared/reused project whose name ≠ the epic title, or case 1) the
  overview has **no description home**; §4.5 keeps the epic file and §7 reports it.

Set a run-local **`overview_written`** flag true whenever the description was
written this run (case 3, or a plan-dedicated case-2 reuse); §4.5 gates epic
deletion on it — **not** on the shape of `tracker_id`.

### 4.3 Order the tasks (topological)

Push in dependency order so every blocker is created before its dependents (spike
§3.3). This is `scripts/plan-graph.py`'s job — it is the ordering authority, not
a hand-executed Kahn's algorithm:

```bash
scripts/plan-graph.py "<plan dir>" --id-shape linear
```

It scans the plan dir's task files (a string or list `is_blocked_by` — it honors
both) and, for each entry, classifies it as `in-plan` (names another task file in
this plan — becomes an ordering edge), `tracker-id` (already matches Linear's id
shape `/^[A-Z]+-\d+$/` — not an ordering edge, passed through as-is), or
`unknown-slug` (matches neither — almost always a typo of an in-plan slug, not a
real external reference; **warned** on stderr, not dropped silently, so a
misspelled blocker doesn't fall through to a footer in §4.4). It topologically
sorts the `in-plan` edges (Kahn's algorithm) and emits one JSON doc: `order`,
`edges`, the classified `is_blocked_by` map, `cycles`, and a seeded
`tracker_map`. If the dependencies contain a **cycle**, the script exits
non-zero and `cycles` names the slugs involved — that's a plan bug: **stop** and
report it; do not push a partial order. A zero exit means `order` is safe to
create in.

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

### 4.5 Cleanup — delete each migrated task file

After the create walk (for jira, after the §5b.5 link pass — see §5b.6), every
successfully created task's full `Context` / `Task` / `Acceptance Criteria` body
lives in its issue, so its **local file is deleted**. This is the
migrate-then-delete contract that makes the tracker the single source of truth
(§6) and stops stale, uncommitted plan files leaking across branches. All three
paths run this same cleanup; only the timing differs for jira.

Let **migrated** = the tasks whose create succeeded this run (a `tracker_id` was
written back and the create call returned a url/id); **kept** = everything else (a
task held by `--ready-only`, or one whose create failed — no `tracker_id`).

1. **Rewrite kept dependents first.** For each migrated task `<S> → <ID>`, find
   every **kept** task file whose `is_blocked_by` names the slug `<S>` and rewrite
   that entry to `<ID>` (preserve single-value vs list shape — translate only the
   matching entry). An id-shaped entry is a pass-through in both the ordering step
   (§4.3 — not an ordering edge) and the create step (§4.4 — handed straight to
   the native `blockedBy` collector), so a kept dependent keeps its native blocker
   link on a later push and never mis-warns "unknown slug (typo?)" once `<S>`'s
   file is gone. Use the handler's own id shape: Linear `/^[A-Z]+-\d+$/` (§4.3),
   gh-issue `/^(\S*#)?\d+$/` (§5.3), jira `/^[A-Z][A-Z0-9]*-\d+$/` (§5b.3).
2. **Delete every migrated task file** — hard delete; the body now lives in the
   issue. **Kept files stay** (a failed create re-runs safely; a held task pushes
   on a later run).
3. **Delete the epic file and the plan directory — only behind the gate.** The
   overview epic is removed only when **both** hold:
   - **Every task is migrated** — no kept (held/failed) task file remains in the
     plan dir. If any does, **skip** epic deletion (the plan isn't fully migrated
     yet; a later run finishes it).
   - **The overview reached the tracker this run** — the run-local
     `overview_written` flag is set (§4.2). It is true when the description was
     written this run: on a **create** (§4.2 case 3 / §5.2 / §5b.2), or on a
     **plan-dedicated reuse** — a container whose name matches the epic title,
     whose description is overwritten with the current overview (§4.2 case 2, and
     the milestone/Epic by-title branches of §5.2 / §5b.2). The gate reads the
     flag, **not** the shape of
     `tracker_id`: a description-bearing `tracker_id` (Linear project id, gh-issue
     milestone **number**, jira Epic key) is **not** proof the overview is in the
     tracker — a shared or reused container keeps its own description — so gating
     on id shape alone would hard-delete an epic whose overview was never migrated.

   When both hold, delete the epic file, then remove the now-empty `<name>_plan/`
   directory and any empty `phase_N/` subdirectories. When the second condition
   fails — the overview was **not** written this run: the gh-issue `plan:<name>`
   **label fallback** (§5.2 step 3, no description field), a **reused, non-plan-
   dedicated project** (§4.2 case 2, name ≠ epic title), or a recorded `tracker_id`
   (§4.2 / §5.2 / §5b.2 case 1) — **keep the epic file** and **warn**
   that the overview was kept locally because it was not written to the tracker
   this run (§7). Because the epic is removed only after every task file
   is gone, a kept task's `parent` back-reference to the epic never dangles.

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

   # Only if the lookup returned nothing, create it — with the overview body as
   # the milestone description, so it survives once the local file is deleted (§7):
   gh api "repos/<repo>/milestones" -f title="<epic title>" \
     -f description="<overview body>" --jq '{number, url: .html_url}'
   ```

   (Use the resolved `<repo>`, or the current repo's `OWNER/NAME` from
   `gh repo view --json nameWithOwner -q .nameWithOwner` when `gh-issue.repo` is
   unset — the `gh api` path needs an explicit repo.) `<overview body>` is the
   epic file's markdown below its frontmatter. On **create**, the milestone owns
   the overview — set `overview_written` (§4.2). On **reuse-by-title**, the
   milestone is plan-dedicated by construction (its title equals the epic title),
   so **overwrite** its description with the current overview body (`gh api
   --method PATCH "repos/<repo>/milestones/<number>" -f description="<overview
   body>"`) and set `overview_written` — the milestone is the plan's own container,
   so it is refreshed to the latest overview on every push, no content comparison.
   Capture the milestone `number` and `html_url`. **Write them back** onto the epic
   file's frontmatter (`tracker_id: <number>`, `tracker_url: <html_url>`).
3. **Fallback (spike O2).** If milestone creation fails (e.g. the token lacks
   issues-write on milestones), fall back to a shared **`plan:<name>` label**:
   `gh label create "plan:<name>" --repo "<repo>" 2>/dev/null`, record
   `tracker_id: label:plan:<name>` on the epic file, and **note the downgrade in
   the report** (weaker grouping — issues share a label, not a milestone). A label
   has no description field, so the overview has no tracker home in this case — §7
   keeps the epic file locally and warns.

### 5.3 Order the tasks (topological)

Use `scripts/plan-graph.py`, same as **§4.3** (a task comes after every task it
is blocked by; a cycle is a plan bug — the script exits non-zero naming the
slugs — **stop** and report it; an unresolvable bare slug is `unknown-slug` —
**warn**). The only difference is the "already an id" shape: for gh-issue an
`is_blocked_by` entry that is already an issue reference matches
`/^(\S*#)?\d+$/` (`#142`, `142`, or `owner/repo#142`) and is **not** an ordering
edge:

```bash
scripts/plan-graph.py "<plan dir>" --id-shape gh
```

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

### 5.5 Cleanup — delete each migrated task file

Run the **§4.5 cleanup** unchanged, using gh-issue's slug→reference map (§5.4) and
its id shape `/^(\S*#)?\d+$/` (§5.3). Each migrated task file is deleted once its
`tracker_id` is recorded; held/failed files stay.

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
   `contentFormat: "markdown"`). The epic body (the overview markdown below the
   frontmatter) is the Jira path's container description — so, like the Linear
   project description (§4.2) and the gh-issue milestone description (§5.2), the
   overview survives in the tracker once the local file is deleted (§7). On
   **create** the Epic owns the overview — set `overview_written` (§4.2). On
   **lookup-reuse** the Epic is plan-dedicated by construction (its summary equals
   the epic title), so **overwrite** its description with the current overview body
   via `<atlassian-mcp>__editJiraIssue` (`cloudId: <jira.site>`, `issueIdOrKey:
   <epic key>`, `fields: { "description": <overview body> }`, `contentFormat:
   "markdown"`) and set `overview_written` — the Epic is the plan's own container,
   so it is refreshed to the latest overview on every push, no content comparison.
   Capture the Epic `key` and `webUrl`.

Whenever an Epic is resolved by **lookup or create** (case 2 — i.e. any time case 1
didn't already supply it), **write its key back** onto the epic file's frontmatter:
`tracker_id: <epic key>` (+ optional `tracker_url`), so a later run skips the lookup
(matching gh-issue §5.2, which writes back on both reuse and create). Case 1 writes
nothing new.

### 5b.3 Order the tasks (topological)

Use `scripts/plan-graph.py`, same as **§4.3** (a task comes after every task it
is blocked by; a cycle is a plan bug — the script exits non-zero naming the
slugs — **stop** and report it; an unresolvable bare slug is `unknown-slug` —
**warn**). The only difference is the "already an id" shape: for jira an
`is_blocked_by` entry that is already an issue key matches `/^[A-Z][A-Z0-9]*-\d+$/`
(e.g. `PLAT-142`, or a single-letter project key like `X-1`) and is **not** an
ordering edge:

```bash
scripts/plan-graph.py "<plan dir>" --id-shape jira
```

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
   step 5. The blocker **translation** is deferred to §5b.5 (links are a second
   pass), so no blocker key is passed at create time. The task's own
   `is_blocked_by` value still travels on the drafted task, because `jira.md`
   step 5 reads it to decide whether the new issue may land in `ready_status` —
   that decision only needs to know a blocker exists, not which key it became.
4. **Create the issue** by following `jira.md` steps 3–6 (compose description,
   `createJiraIssue`, transition to ready status, return the url) with
   `issueTypeName: <jira.issue_type>` (default `Task`) and
   **`parent: <epic key from §5b.2>`**. Because the container is already resolved
   here, **skip `jira.md` step 2's epic-selection prompt** — pass the epic key
   directly as `parent`. Apply `jira.labels` and `jira.additional_fields` via
   `additional_fields` exactly as the create flow does. Step 5 runs per issue as
   the walk creates it, so a **complete, unblocked** plan task lands in
   `ready_status`; a blocked one — or one still carrying open questions — waits in
   the initial status for its links (§5b.5) and a human.
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

### 5b.6 Cleanup — delete each migrated task file

Run the **§4.5 cleanup**, using jira's slug→key map (§5b.4) and its id shape
`/^[A-Z][A-Z0-9]*-\d+$/` (§5b.3). For jira this **must run after the §5b.5 link
pass** — the link pass walks every task to draw its native `Blocks` edges, so the
files (and the in-memory map) must still be intact when it runs; only once all
links exist are the migrated files deleted.

## 6. Idempotency and the migrate-then-delete contract

Each plan file is **deleted once its migration is confirmed** (§4.5): a task file
when its `tracker_id` is recorded, the epic file and plan directory when every task
is migrated and the overview was written to the tracker this run (§4.2
`overview_written`). After a
**fully** migrated push the plan directory is gone and the tracker is the **only**
source of truth — re-pushing is a no-op because nothing remains locally, and new
work for the plan goes straight to the tracker via `/add-task`, not back into
deleted files.

A **partial** push (anything held by `--ready-only`, or a create that failed) still
re-runs safely — this is **create-missing-only** (spike §4):

- A file with a non-empty `tracker_id` is skipped, never duplicated — though after
  cleanup a fully-migrated task no longer has a file at all; it is simply absent.
- A recorded container id is reused, never duplicated. The container's description
  is written on **create**, and overwritten with the current overview on a
  **plan-dedicated reuse** (a same-title container — §4.2); a shared container
  (name ≠ epic title) is **never** overwritten.
- Kept files still feed the slug→id map, and a deleted blocker's id survives on its
  kept dependents because §4.5 rewrote their `is_blocked_by` slug to that id — so a
  held task resolves its blockers (and keeps native links) on a later push.
- For jira, the §5b.5 link pass is create-missing-only too: it checks the
  dependent's existing `Blocks` links before adding one, so a re-push draws no
  duplicate "is blocked by" edges.
- v1 **never updates or deletes** remote **issues** (tasks). The one remote write
  on reuse is narrow and container-only: the overview is written into (overwriting)
  a **plan-dedicated** container's description (§4.2), never onto a shared one and
  never onto a task issue. It now **does** delete local plan files, but only after
  their content is safely in the tracker (the migrated issue body, or the
  container description for the overview).

## 7. Report

Print:

- The container: created (`<title>` → `<project / milestone / Epic id>`) or reused
  (`<id>`); for gh-issue, note when the `plan:<name>` label fallback was used; for
  jira, note any blocker links skipped because the `Blocks` link type was absent
  (§5b.5).
- **Created:** one line per new issue — `<slug> → <identifier> (<url>)`, with its
  resolved blockers if any.
- **Deleted locally:** each migrated task file removed by the §4.5 cleanup —
  `<slug> → <identifier> (<url>)`. Plus the epic file + plan directory when the
  whole plan migrated.
- **Kept locally:** files **not** deleted, each with why — a task held by
  `--ready-only` or whose create failed (re-runs safely), and the epic file when
  it was kept because the overview was not written to the tracker this run — the
  container was reused and **not** plan-dedicated: the gh-issue `plan:<name>` label
  fallback, a reused non-plan-dedicated configured project (§4.2 case 2, name ≠
  epic title), or a recorded `tracker_id` (§4.5).
- **Already pushed:** skipped files with their `tracker_id`.
- **Follow-up set:** not-ready tasks that were pushed anyway (whole-plan mode),
  or held (`--ready-only`), each with what's needed to resolve it.

After a **fully** migrated push the plan directory is gone — the **Deleted
locally** block (ending in the epic + directory) is the signal that the plan fully
landed and the tracker now owns it. A **partial** re-run reports the remaining
"created"/"deleted" lines plus "container reused"; once nothing is left to push,
the directory is removed.
