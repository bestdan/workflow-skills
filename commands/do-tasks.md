---
description: Execute ready tasks — the unified, handler-dispatched verb for turning ready tasks into PRs
allowed-tools: Bash(git *), Bash(gh *), Bash(claude *), Bash(find *), Bash(grep *), Bash(cat *), Glob, Grep, Read, Write, Edit, AskUserQuestion, Agent, mcp__linear, mcp__claude_ai_Linear
argument-hint: "[slug | --all | -n N] [--remote|--local]"
---

# Do Tasks

The single verb for executing captured tasks. It resolves the **handler** from
`dev_docs/tasks/.task-config.yml` (absent → `repo-pr`) and dispatches the same
way `/add-task` and `/list-tasks` do, then turns dependency-ready tasks into PRs.

`/do-tasks` unifies what `/process-tasks` and `/claim-task` do today. This is the
primary execute verb; the older commands still work (they are removed in a later
task) and remain the authoritative reference for the file-path mechanics, which
this command intentionally **references rather than re-specifies** so the two
cannot drift.

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/`
> directory exists, run the **Legacy migration** prompt from
> `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

## Modes

- `/do-tasks` — execute the single highest-ranked dependency-ready task
- `/do-tasks <slug>` — execute a specific task
- `/do-tasks --all` — execute dependency-ready tasks up to the WIP limit, holding the overflow
- `/do-tasks -n N` — dispatch up to `N` tasks, **each to its own session (one task per session)**, bounded by the WIP limit
- `/do-tasks --remote` / `/do-tasks --local` — choose where execution runs (default: remote dispatch)

**Scope of `--all` / `-n N`.** Batch is meaningful only for **remote** dispatch
(each task gets its own cloud VM). Foreground pairing is inherently single, so
`--local` caps the **batch** (`--all` / `-n N`) at **1** — it processes the single
highest-ranked task and reports the rest as held. `/do-tasks <slug> --local` still
runs the named slug.

For the **tracker** (`linear`) handler, execution is single and foreground
(it runs in the current session, like `/claim-task`): `--remote`/`--local` do not
apply, and `--all` / `-n N` degrades to a single claim with a one-line note. See
section 3.

## 1. Resolve the handler

Read `dev_docs/tasks/.task-config.yml`:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- File absent, or `handler: repo-pr` → **file path** (section 2 below).
- `handler: linear` → **tracker path** (section 3 below). Follow
  `commands/handlers/linear-claim.md` (with `commands/handlers/linear-common.md`
  for config/preflight/kanban mapping) for the full claim flow.
- `handler: jira | gh-issue` → **stop**: "execution not supported for
  `<handler>`; pull an issue manually and open a PR, or switch the handler to
  `linear` in `dev_docs/tasks/.task-config.yml`." (`/claim-task` stops on these
  too, with the same guidance.)
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

## 2. File path (`repo-pr` / absent handler)

This path fully subsumes `/process-tasks`. Rather than restate the scan, ranking,
multi-blocker readiness, WIP cap, remote dispatch prompt, and `--local` mechanics,
**follow `commands/process-tasks.md`** — every step there applies verbatim, with
the argument mapping below. Behavior must match `/process-tasks` for the same
inputs.

### Argument mapping

| `/do-tasks` invocation | `/process-tasks` step to follow                                                                  |
| ---------------------- | ------------------------------------------------------------------------------------------------ |
| `/do-tasks`            | default: select the single highest-ranked dependency-ready task (step 2)                         |
| `/do-tasks <slug>`     | `<slug>` mode (step 2) — if it is waiting on `is_blocked_by`, stop and report every blocker      |
| `/do-tasks --all`      | `--all` mode — select all dependency-ready, dispatch up to the WIP limit (step 4), hold the rest |
| `/do-tasks -n N`       | like `--all`, but cap the **selected** batch at `N` before applying the WIP limit (see below)    |
| `--remote` (default)   | remote dispatch (step 4)                                                                         |
| `--local`              | Local mode section of `commands/process-tasks.md` (caps the batch at 1)                          |

### `-n N`

`-n N` is `--all` with an explicit ceiling. After selecting the dependency-ready
tasks and ranking them (priority → age, exactly as `/process-tasks` step 1), keep
the top `N`, then apply the WIP limit from step 4 (`wip_limit - current_wip`). The
effective batch is `min(N, wip_limit - current_wip)`. Report any selected task you
did not dispatch — distinguishing `held (-n N ceiling)` from
`held (WIP limit reached)` — so the user knows why each was left behind.

**One task per session.** `N` bounds the number of **separate** single-task
sessions launched, **not** how many tasks any one session takes. Each selected
task is dispatched to its own remote session that claims and executes exactly that
one task (its own branch and VM, per `/process-tasks` step 4) — never instruct a
single agent to claim or work multiple tasks. So `-n 3` means up to three
independent agents each doing one task, capped further by the WIP ceiling; it is
the **total in-flight** that the limit protects, regardless of how the batch is
split.

### WIP cap and multi-blocker semantics

Both are carried through **unchanged** from `/process-tasks`:

- **WIP cap** — `/process-tasks` step 4. Resolve `wip_limit` from
  `.task-config.yml` (default `3`), count current WIP (`in_progress` files + open
  `task-loop` PRs), and dispatch at most `wip_limit - current_wip`. Single-task
  mode (`/do-tasks` / `/do-tasks <slug>`) is not gated.

  **Check the WIP slack first (batch only).** Before scanning/ranking, confirm
  `gh` auth (the WIP count itself calls `gh`), then compute
  `wip_limit - current_wip` (per `/process-tasks` step 4). If it is `≤ 0`, report
  `WIP limit <n> reached (<current_wip> in flight) — nothing dispatched` and stop,
  skipping ranking and dispatch entirely (a light frontmatter scan is still needed
  to count `in_progress`; you need not enumerate held tasks — point the user at
  `/list-tasks`). Only with positive slack do you rank and dispatch the top
  `min(N, slack)`. Single-task mode is not gated and skips this guard.
- **Multi-blocker readiness** — a task is dependency-ready only when **every**
  `is_blocked_by` entry is satisfied (target absent or `done`); `is_blocked_by`
  may be a single slug or a list (`[a, b]`). See `/process-tasks` step 1.

## 3. Tracker path (`linear` handler)

This path fully subsumes `/claim-task`. Rather than restate the feasibility
judgment, atomic claim (concurrency guard), branch-name-verbatim rule, PR↔issue
linking, move-to-review, and bail mechanics, **follow
`commands/handlers/linear-claim.md`** (with `commands/handlers/linear-common.md`
for config/preflight/kanban mapping) — every phase there applies verbatim. The
sub-steps below are exactly `/claim-task` steps 2–9; `/do-tasks` runs them in the
**current session**. Read both handler files; if the relative paths don't resolve,
find them with **Glob** (`**/commands/handlers/linear-claim.md`,
`**/commands/handlers/linear-common.md`).

**Single by nature.** Tracker execution is foreground, so `--remote`/`--local` do
not apply and `--all` / `-n N` is **not supported** — a batch flag degrades to a
single claim with a one-line note ("batch isn't supported for tracker handlers;
claiming one issue"). `/do-tasks <identifier>` (a specific Linear id, e.g.
`PRE-12`) claims that one issue.

### Pre-claim WIP gate

The WIP limit carries over from the file path (`/process-tasks` step 4), but since
tracker execution is single/foreground it is a **pre-claim gate**, not a batch
cap. After the preflight resolves the team and workflow states, **before** judging
feasibility or claiming:

1. Resolve `wip_limit` from the top-level `wip_limit` key in
   `dev_docs/tasks/.task-config.yml` (default `3` — the same key the repo-pr
   handler uses).
2. Count current in-flight work = Linear issues in any `started`-type state
   (e.g. `In Progress`, `In Review`) for the configured team, via
   `<linear-mcp>__list_issues` (resolve by state **type**, not display name —
   names are team-configurable). The started-type issue is the canonical
   in-flight unit: an open PR is already reflected by its issue sitting in a
   started state, so do **not** add open PRs separately — that double-counts.
3. If that count is **≥ `wip_limit`**, decline: report
   `WIP limit <n> reached (<count> in flight) — no issue claimed` and stop. Do not
   claim another card.

Single-issue mode (`/do-tasks <identifier>`) is gated too — the limit protects
total in-flight work regardless of how the claim was initiated.

### Claim and execute

With positive WIP slack, run `commands/handlers/linear-claim.md` end to end,
exactly as `/claim-task` does:

1. **Preflight** — `linear-common.md` preflight (resolve team) + `linear-claim.md`
   "Find candidates" (resolve workflow states, query unstarted issues, filter by
   `estimate`/labels/assignee, rank). Also confirm `gh auth status`, a clean
   working tree, and fetch the base branch (`linear.base_branch`, default `main`).
2. **Judge feasibility** — `/claim-task` step 4: take candidates in ranked order,
   one at a time, and stop at the first this session can finish without a human;
   comment `Skipped by /do-tasks: <reason>` on each one rejected.
3. **Claim** — `linear-claim.md` "Claim the issue": add `auto-claimed` (creating
   the label if absent — the concurrency guard), move to the `started`-type state,
   comment the branch name. On an `auto-claimed` race, fall back to the next
   candidate.
4. **Branch + execute** — branch with Linear's **verbatim** `branchName` (never
   reconstruct it when the field is present), do the work, run the project's
   tests/lints (`just check` here).
5. **PR** — `gh pr create` with the Linear identifier in brackets in the title
   (`[PRE-12] …`) and `Closes <identifier>` on its own line in the body; post the
   PR URL as a Linear comment.
6. **Move to review** — `linear-claim.md` "Move to review on PR open": attach the
   PR via `links` and move to `In Review` if the team has one. **Never move the
   issue to a `completed`/`canceled` state** — merge is the only completion signal,
   handled by Linear's GitHub integration. This hard rule from `linear-claim.md`
   carries over unchanged.
7. **Bail** — if the work proves infeasible mid-execution, `linear-claim.md`
   "Bail": `git stash push -u` the WIP, remove `auto-claimed`, add
   `human-approval-requested`, revert the issue to the `backlog`-type state, and
   comment what tripped the bail. Stop — do not auto-pick another candidate.

## 4. Report

For the **file path**, report by dispatch mode:

- **remote** dispatch — as `/process-tasks` step 5: list each dispatched task
  (slug, title, that a remote session started) and point the user at `/tasks` to
  monitor.
- **`--local`** — there is no remote session; report the PR opened in-session
  instead.

In both file-path modes, list separately any tasks skipped because they are
waiting on another task (with every unresolved blocker) or **held** by the `-n N`
ceiling or the WIP limit.

For the **tracker path**, report as `/claim-task` step 9: on success print the
issue identifier, the PR URL, and a one-line summary; on bail print the identifier,
why it bailed, and the Linear comment URL; on the WIP gate declining, print the
limit and the in-flight count.
