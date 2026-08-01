---
description: Retire completed/canceled work items — a handler-dispatched archive/prune of terminal-state tasks past an age threshold
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(find *), Bash(grep *), Bash(mkdir *), Bash(op *), Bash(curl *), Bash(python3 *), Glob, Grep, Read, Write, Edit, AskUserQuestion, Agent, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__linear__list_teams, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__getTransitionsForJiraIssue, mcp__claude_ai_Atlassian__transitionJiraIssue, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getTransitionsForJiraIssue, mcp__atlassian__transitionJiraIssue
argument-hint: "[--older-than <N>d] [--issues <refs>] [dry-run]"
---

# Archive Tasks

The task loop **creates** work items but never **retires** them. Over a
high-velocity month the configured tracker fills with completed/canceled items
that still count as live records, and on some trackers that is a hard wall:
**Linear's free plan caps a workspace at 250 _active_ issues** (archived issues
are unlimited and excluded from the cap), so a busy loop hits
`Usage limit exceeded` and silently breaks — `/add-task` can't file and
`/push-plan` can't push. `/archive-tasks` is the generic cleanup verb that retires
terminal-state work past an age threshold.

Like `/do-tasks` and `/promote-tasks`, this command is a **thin dispatcher**: it
resolves the **handler** from `dev_docs/tasks/.task-config.yml` (absent →
`repo-pr`) and then reads and follows `commands/handlers/<handler>-archive.md`,
which owns the tracker-specific terminal-state query and the retire operation.
The mechanics live in the handler files this command **references rather than
re-specifies**, so the two cannot drift.

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/`
> directory exists, run the **Legacy migration** prompt from
> `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

## What it touches — and what it never touches

`/archive-tasks` only ever retires items in a **terminal state** (the handler's
`done`/`completed`/`canceled` equivalent) whose completion timestamp is **older
than the threshold**. It never archives `new`, `ready`, `in_progress`,
`blocked`, or `needs_review` work — open work is always left alone, regardless of
age. This is the one hard safety rule every handler file restates.

## On `linear`, the retire step never goes through the MCP

Read this **before** planning a run. Everything up to the candidate list (argument
parsing, threshold resolution, discovery) works in-session on every handler. The
**retire** step on the **`linear`** handler is the exception, and it has one hard
cause and one conditional one:

- **Hard: the Linear MCP exposes no archive mutation.** The read side is fully
  available, but there is no `issueArchive` (or delete) MCP call at all, so
  retiring goes through the GraphQL backstop —
  `commands/handlers/assets/linear-archive.py`.
- **Conditional: that backstop needs an API key in its environment.** How
  the key gets there is **not** this command's business — it is the two-ladder
  contract in `dev_docs/auth_key_access.md`, applied by `linear-common.md` →
  "Key resolution". A plaintext `linear.api_key`, an exported `$LINEAR_API_KEY`,
  and a pointer resolved by whatever resolver the machine configures are all
  supported, and only the last of those can need an unlock at all.

So the practical rule: **probe, don't guess.** The shipped helper answers the
question without ever printing the key, and honors a configured non-default
resolver:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/_secret_resolve.py" --probe LINEAR_API_KEY
```

Exit 0 means the backstop runs in-session like any other command. Otherwise the
**category tells you which problem you have**, and they are not the same problem:

- `no-session` — the only genuinely session-bound case. Run the unlock in your
  own terminal (for the default `op` resolver, `op signin`), then re-probe; or
  move the step outside the session (`!` prefix, cron, GitHub Action — headless
  paths use `OP_SERVICE_ACCOUNT_TOKEN` or a CI secret instead).
- `denied` — the resolver ran and refused. With an **approval-per-read** resolver
  this is the ordinary "nobody answered the dialog" outcome, not a fault: approve
  it and re-probe. It is also the **catch-all** for any stderr the helper can't
  classify, so treat it as "read the resolver's own message" rather than as a
  specific diagnosis.
- `not-found` — the resolver worked but the reference points at nothing (renamed
  or deleted item). Fix the reference, not the session.
- `unconfigured` — no key or reference is set anywhere. A config task, not a
  session one; moving the step elsewhere fixes nothing.
- `malformed-ref` / `unknown-resolver` — a bad value in the local config. These
  are **deliberately distinct** failures: before the shared resolver landed they
  looked identical to a keyless host, so a typo silently floored every run.

Only `no-session` and `denied` are ever fixed by changing _where_ you run the
step. The other four are configuration, and will fail identically from a cron
job.

Two things **not** to conclude from a failure. It is not "the agent is forbidden
from calling the secret tool" — it isn't (this command's own `allowed-tools`
includes `Bash(op *)`). And it is not "you must use 1Password" — `op` is the
default resolver, not the only one, and a plaintext or exported key needs no
resolver at all. Take the mechanism from `auth_key_access.md`, never from this
section. See "Run it without an agent — the shipped script" in
`commands/handlers/linear-archive.md` for the invocation, and §3 below for
scheduling it on a cadence.

The other handlers are unaffected: `repo-pr` archives by moving files, and
`gh-issue`/`jira` mutate through their own tool surfaces, all in-session.

## Arguments

`$ARGUMENTS` is a set of independent, combinable tokens (order-insensitive). Test
each with a **"contains" check, never equality** — the same arg-parsing rule
`/promote-tasks` documents (`$ARGUMENTS` contains `dry-run`, not
`$ARGUMENTS == "dry-run"`), so `/archive-tasks --older-than 14d dry-run` enables
both. A bare `dry-run`, a bare `--older-than 14d`, and both together must all
parse.

- **`--older-than <N>d`** — the age threshold. Only terminal items whose
  completion timestamp is more than `N` days ago are candidates. Accept `14d` or
  a bare `14` (both mean 14 days). This **overrides** the config default.
- **`--issues <refs>`** — archive **these specific items**, whatever their age.
  A comma-separated list of tracker identifiers (`PRE-12`). This is the
  **named-issue mode**: it replaces the age threshold rather than narrowing it,
  so the no-threshold refusal below does **not** apply, and `--older-than` is
  ignored if also passed. Everything else still holds — terminal state is still
  required (a named item that is not `done`/`completed`/`canceled` is reported
  and skipped, never archived), and `dry-run` still works.
  **`linear` handler only** for now; on `repo-pr`/`gh-issue`/`jira`, stop and
  say the handler has no named-issue mode rather than falling back to a sweep.
- **`dry-run`** — list the candidates and stop. Change nothing. (A run with no
  resolvable threshold **and** no `--issues` is dry-run-only regardless — see
  below.)

### Resolving the threshold (and the no-threshold safety default)

**Skip this whole section when `--issues <refs>` was passed** — it resolves the
**sweep's** bound, and a named-issue run is already bounded by the list the user
typed. Go straight to the handler with those refs.

Otherwise resolve the effective threshold in this order:

1. **`--older-than <N>d`** from `$ARGUMENTS`, if present.
2. else the **`archive_after`** key (days) from `dev_docs/tasks/.task-config.yml`
   (top-level, the same level as `wip_limit`).
3. else **no threshold** → **refuse to mutate.** Without a cutoff there is no
   candidate list to compute, so **stop** and explain: "No archive threshold set.
   Pass `--older-than <N>d`, or set `archive_after: <N>` in
   `dev_docs/tasks/.task-config.yml`, then re-run." This guards against a
   surprise bulk archive when someone runs the bare command.

So a **sweep** mutates only with an explicit `--older-than` **or** a configured
`archive_after`, **and** the absence of `dry-run`. The other way to mutate is
`--issues <refs>`, where the typed list is the bound the threshold would
otherwise supply. Always **print the candidate list first**; in dry-run, stop
there.

## 1. Resolve the handler

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

- File absent, or `handler: repo-pr` → read and follow
  **`commands/handlers/repo-pr-archive.md`** (move stale `done` task files to an
  archive dir).
- `handler: linear` → read and follow **`commands/handlers/linear-archive.md`**
  (native auto-archive guidance + the GraphQL `issueArchive` backstop; uses
  `commands/handlers/linear-common.md` for preflight/state-type mapping).
- `handler: gh-issue` → read and follow
  **`commands/handlers/gh-issue-archive.md`** (close/label stale completed
  issues; hygiene-only, GitHub has no cap).
- `handler: jira` → read and follow **`commands/handlers/jira-archive.md`**
  (transition terminal issues to an archived status where one exists; default to
  a documented no-op otherwise).
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

If the relative path doesn't resolve, find the handler file with **Glob**
(`**/commands/handlers/<handler>-archive.md`) and Read the result. Pass the
resolved threshold — **or the `--issues` refs** — and the `dry-run` flag
through.

## 2. Report

The handler file owns its report format, but every handler reports the same
skeleton:

- **Candidates** — the terminal-state items older than the threshold, each with
  its identifier/slug and completion date. Always printed first.
- **dry-run** — the candidate list and nothing else; an explicit "nothing
  archived (dry-run)".
- **applied** — what was archived (count + identifiers), and for `linear`
  whether it went through native auto-archive (no-op here) or the GraphQL
  backstop.
- **none** — "no terminal-state items older than `<N>d` — nothing to archive."

## 3. Scheduling (handler-agnostic)

`/archive-tasks` is meant to run on a cadence so the tracker never drifts back to
the cap. Two ways, neither handler-specific:

- **`/schedule`** — a cloud routine that runs `/archive-tasks --older-than <N>d`
  on a cron (e.g. daily). Best for keeping a Linear workspace permanently under
  the 250-issue cap.
- **`/loop`** — `/loop 24h /archive-tasks --older-than 30d` in a session you keep
  open.

For the **Linear** GraphQL backstop specifically, the retire step needs only a
personal API key, so it can alternatively run as a standalone scheduled job (a
GitHub Action or cron) **independent of an agent session** — see the "Run it
without an agent" note in `commands/handlers/linear-archive.md`. Keep the
scheduling decision here; the handler files own only the per-tracker retire
mechanics.
