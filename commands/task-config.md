---
description: Configure where /add-task delivers tasks (repo PR, GitHub issue, Jira, or Linear)
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(mkdir *), Read, Write, Glob, AskUserQuestion, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__getVisibleJiraProjects, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__getVisibleJiraProjects, mcp__atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__linear__list_teams, mcp__linear__list_projects
argument-hint: [repo-pr | gh-issue | jira | linear]
---

# Task Config

Set up the destination handler for `/add-task` in this repo. Writes `dev_docs/tasks/.task-config.yml`, which `/add-task` reads to decide where a captured task lands. For every handler **except `repo-pr`**, treat this as **local** config: the setup step below adds `dev_docs/tasks/` to the repo's local git exclude (`.git/info/exclude`) so it stays out of `git status` and accidental commits, and each person configures their own destination.

> **Why `repo-pr` is excepted.** The `repo-pr` handler commits task markdown into `dev_docs/tasks/` (via `git add dev_docs/tasks/<slug>.md`) — that's how the loop shares work. Adding `dev_docs/tasks/` to the local exclude would make `git add` of a new task file fail without `-f`, so the setup step **skips the exclude entirely when the handler is `repo-pr`** and the config is meant to be committed and shared. The exclude applies only to the external-tracker handlers (`gh-issue`, `jira`, `linear`), where nothing under `dev_docs/tasks/` is committed.

This command is a thin dispatcher. The per-handler setup logic (preflight checks, prompts, config block shape) lives in `commands/handlers/<handler>-config.md`. The runtime delivery logic for each handler lives in `commands/handlers/<handler>.md`.

## Handler capability matrix

Handler feature parity is jagged — only `repo-pr` runs the full loop. This table is the **single source of truth** for which verbs each handler supports; step 5 reads it to warn the user about what they're opting out of. Keep it in sync as handlers gain verbs (see "Adding a handler" in `CONTRIBUTING.md`).

| Verb (command)                      | `repo-pr` | `gh-issue` | `jira` | `linear` |
| ----------------------------------- | --------- | ---------- | ------ | -------- |
| capture (`/add-task`)               | yes       | yes        | yes    | yes      |
| list (`/list-tasks`)                | yes       | yes        | yes    | yes      |
| promote (`/promote-tasks`)          | yes       | yes        | yes    | yes      |
| do — single (`/do-tasks`)           | yes       | yes        | yes    | yes      |
| process — batch (`/do-tasks --all`) | yes       | no         | no     | no       |
| archive (`/archive-tasks`)          | yes       | hygiene    | opt    | yes      |
| reoptimize (`/reoptimize-tasks`)    | no        | no         | no     | yes      |

`repo-pr` is the only full-loop handler. `jira`, `gh-issue`, and `linear` all add list, promote, and single `do` (but not batch process). Unsupported verbs aren't broken — the work just lives in the external tracker (your Jira board, `gh issue list`, Linear) instead of through these commands.

**Archive** (`/archive-tasks`) retires terminal-state work past an age threshold; support is jagged: `repo-pr` moves stale `done` files to `dev_docs/tasks/_archive/`; `linear` is the load-bearing case (native team auto-archive plus a GraphQL `issueArchive` backstop) because Linear's free plan caps a workspace at **250 active issues**; `gh-issue` is **hygiene only** (GitHub has no cap and no true archive — it just labels long-closed issues `archived`); `jira` transitions terminal issues to a configured `archive_status` where the project has one and is otherwise an **opt-in no-op** (native archival is Jira Premium). See each handler's `*-archive.md`.

`reoptimize` (`/reoptimize-tasks`) is the exception that runs _against_ the tracker graph rather than the local files. **v1 implements `linear` only** (relation edits, priority, duplicates applied natively); `jira` and `gh-issue` re-optimize are planned follow-ups, and `repo-pr` is `no` because its tasks are local plan files — re-optimize those directly (or `/push-plan` to a tracker first).

`/do-tasks` is the single execute verb across handlers: executes a single task by default, `--all` / `-n N` for batch dispatch on `repo-pr`. On `linear`, `gh-issue`, and `jira` it claims and executes one issue in the current session.

## Steps

### 1. Show current config

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

If it exists, show the user what's currently configured so they see what they're changing. If not, say there's no config yet (so `/add-task` currently defaults to `repo-pr`).

### 2. Choose the handler

If `$ARGUMENTS` names a handler (`repo-pr`, `gh-issue`, `jira`, or `linear`), use it. Otherwise ask via `AskUserQuestion` (header: "Destination") which one they want:

- **`repo-pr`** (recommended, first) — commit the task as a markdown file via PR (the default; works with `/do-tasks` and `/list-tasks`)
- **`gh-issue`** — create a GitHub Issue
- **`jira`** — create a Jira work item under an epic
- **`linear`** — create a Linear issue under a team (optionally attached to a project)

### 3. Dispatch to the per-handler setup file

Read `commands/handlers/<handler>-config.md` and follow it. The handler-config file:

- Runs all prerequisite checks (auth, MCP connectivity) and stops with guidance on failure — **do not fall back to writing a config that can't deliver**.
- Prompts for any handler-specific fields, validating values against the source of truth (real projects, real epics, etc.) rather than accepting free-text.
- Returns the config block to be written.

If the per-handler file dispatches to `mcp-setup-offer.md` (the shared MCP install subroutine for `jira` and `linear`), follow that and stop — the user needs to restart Claude Code before the new tools are available, and there's nothing to retry inline.

If the relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/<handler>-config.md`) and Read the result.

### 4. Write the config

Ensure the directory exists and write the file:

```bash
mkdir -p "$(git rev-parse --show-toplevel)/dev_docs/tasks"
```

Then, **unless the chosen handler is `repo-pr`**, keep the directory out of git by adding it to the repo's local exclude (`.git/info/exclude` is per-clone and never committed). The `git check-ignore` guard makes this idempotent — safe to re-run without duplicating the entry. **For `repo-pr`, do not run this** — it commits task files under `dev_docs/tasks/` (see the note at the top):

```bash
# skip for repo-pr
git check-ignore -q dev_docs/tasks/ || echo 'dev_docs/tasks/' >> "$(git rev-parse --git-dir)/info/exclude"
```

Write the config block returned by the per-handler file to `dev_docs/tasks/.task-config.yml`. Examples of the shape each handler returns:

```yaml
# repo-pr
handler: repo-pr
# archive_after: 30          # optional, top-level — default /archive-tasks age threshold (days)
```

```yaml
# gh-issue
handler: gh-issue
gh-issue:
  repo: owner/name
  labels: [follow-up]
  assignees: []
# archive_after: 30          # optional, top-level — default /archive-tasks age threshold (days)
```

```yaml
# jira
handler: jira
jira:
  site: mycompany.atlassian.net
  project: PLAT
  issue_type: Task
  default_epic: PLAT-100
  labels: []
  # archive_status: Archived # optional — terminal-category status /archive-tasks transitions to
# archive_after: 30          # optional, top-level — default /archive-tasks age threshold (days)
```

```yaml
# linear
handler: linear
wip_limit: 3 # top-level — per-project default each linear.projects entry inherits unless overridden
linear:
  team: PreThink # team NAME (as shown in Linear) or UUID id — never the team key like "PRE"
  default_priority: 3
  projects: # replaces scalar default_project; absent/empty → whole team
    - id: ebbc284b-0000-0000-0000-000000000000 # required id/UUID; optional per-entry wip_limit/max_estimate
# Full schema — per-entry overrides, linear.global_wip_limit, linear.api_key_ref —
# in commands/handlers/linear-common.md ("Config block" + "Resolve configured projects").
# archive_after: 30          # optional, top-level — default /archive-tasks age threshold (days)
```

**`archive_after`** is a top-level key (sibling of `wip_limit`), shared by every handler: the default age threshold `/archive-tasks` uses when `--older-than` is omitted. With neither set, `/archive-tasks` refuses to mutate (dry-run-only) to avoid a surprise bulk archive. The per-handler archive keys (`jira.archive_status`, `linear.api_key_ref`) are documented in each handler's `*-config.md`. (`repo-pr` has no archive key — it parks files in the fixed `dev_docs/tasks/_archive/`.)

Omit optional keys the user didn't set (the per-handler file already handles this in what it returns).

### Local override (`.task-config.local.yml`)

Alongside the committed `.task-config.yml`, commands also read an **optional, gitignored** `dev_docs/tasks/.task-config.local.yml`. It uses the **same schema** and is **deep-merged over** the committed file. The merge is **recursive for mappings**: a local `linear:` block that sets only `api_key_ref` overlays that one leaf and **preserves** the committed `linear.team`/`linear.projects` — you do not restate the rest of `linear:`. At each leaf the local value wins; scalars and lists are replaced wholesale; keys absent from the local file are kept. Every command that reads the task config reads this **merged view** — `.task-config.yml` overlaid with `.task-config.local.yml`.

Its purpose is **personal, machine-specific, or secret** keys that must not be committed — chiefly `linear.api_key_ref` (a pointer to a full-account Linear bearer token). Put those there, not in the shared `.task-config.yml`. It is never written by `/task-config`'s setup flow (which only writes the committed file); the user maintains it by hand. When `handler: linear` and the repo excludes `dev_docs/tasks/` entirely (the non-`repo-pr` default), `api_key_ref` can live in either file since neither is committed — but the local override is the canonical home so the shared file stays portable. Ensure `.task-config.local.yml` is git-ignored:

```bash
git check-ignore -q dev_docs/tasks/.task-config.local.yml || echo 'dev_docs/tasks/.task-config.local.yml' >> "$(git rev-parse --git-dir)/info/exclude"
```

### Resolving the handler (merged view)

Every command or skill that dispatches on `handler:` resolves it from the **merged view**, not the committed file alone — otherwise a local override of `handler:` would be silently ignored. The canonical resolution step (pasted identically into each dispatching command's and skill's "Resolve the handler" section, and matching what `doctor.md` does) is:

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config (mappings merge recursively, local leaf values win, per "Local override" above), then read `handler:` from the result. A missing or empty value → the default `repo-pr`. Commands and skills must not resolve `handler:` from `.task-config.yml` alone.

### 5. Confirm

Tell the user:

- Which handler is now configured and where the file lives.
- **The handler's supported and unsupported verbs**, read from the capability matrix above. Name them explicitly so the user knows what they've opted into. For example:
  - `repo-pr`: "`repo-pr` runs the full loop: /add-task, /list-tasks, /promote-tasks, /do-tasks, /archive-tasks (moves stale done files to dev_docs/tasks/_archive/)."
  - `jira`: "`jira` supports: /add-task, /list-tasks, /promote-tasks (uses `ready_status`/`refinement_status` if set, else prompts), /do-tasks (single — needs `ready_status` set), /archive-tasks (only when `archive_status` is set — native Jira archival is Premium). Not supported: batch /do-tasks --all. You can still manage these in Jira directly."
  - `gh-issue`: "`gh-issue` supports: /add-task, /list-tasks, /promote-tasks, /do-tasks (single), /archive-tasks (hygiene only — GitHub has no issue cap; it just labels long-closed issues). Not supported: batch /do-tasks --all. You can still manage these in GitHub directly."
  - `linear`: "`linear` supports: /add-task, /list-tasks, /promote-tasks, /do-tasks (single), /archive-tasks (native auto-archive + a GraphQL backstop to stay under Linear's 250-active-issue cap). Not supported: batch /do-tasks --all. You can still manage these in Linear directly."
- **For `repo-pr`:** that the config (and the task files under `dev_docs/tasks/`) are meant to be **committed and shared** — no exclude was added — so they should commit `.task-config.yml` for teammates to pick up the same destination.
- **For `gh-issue` / `jira` / `linear`:** that `dev_docs/tasks/` was added to the repo's local git exclude, so the config stays local and out of `git status` (rerun the guarded `git check-ignore … || echo …` command above on any other clone). If they instead want teammates to share this destination, they can **commit** `.task-config.yml` rather than excluding it.
- They can now run `/add-task`, or re-run `/task-config` to switch handlers.
