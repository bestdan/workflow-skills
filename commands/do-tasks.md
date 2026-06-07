---
description: Execute dependency-ready tasks — the unified, handler-dispatched verb for turning ready tasks into PRs (file/repo-pr today; tracker path arriving)
allowed-tools: Bash(git *), Bash(gh *), Bash(claude *), Bash(find *), Bash(grep *), Bash(cat *), Glob, Grep, Read, Write, Edit
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
- `/do-tasks -n N` — execute up to `N` dependency-ready tasks, still bounded by the WIP limit
- `/do-tasks --remote` / `/do-tasks --local` — choose where execution runs (default: remote dispatch)

**Scope of `--all` / `-n N`.** Batch is meaningful only for **remote** dispatch
(each task gets its own cloud VM). Foreground pairing is inherently single, so
`--local` caps the **batch** (`--all` / `-n N`) at **1** — it processes the single
highest-ranked task and reports the rest as held. `/do-tasks <slug> --local` still
runs the named slug.

## 1. Resolve the handler

Read `dev_docs/tasks/.task-config.yml`:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- File absent, or `handler: repo-pr` → **file path** (section 2 below).
- `handler: linear` → **tracker path**. The tracker dispatch is added to this
  command in a later task. **For now, defer:** stop and tell the user to use
  `/claim-task`, which dispatches to the Linear handler.
- `handler: jira | gh-issue` → **tracker path**, not yet supported by any execute
  verb (`/claim-task` stops on these too). Stop and tell the user to pull the
  issue and execute it manually, or switch the handler to `linear` in
  `dev_docs/tasks/.task-config.yml` — the same guidance `/claim-task` gives.
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

### WIP cap and multi-blocker semantics

Both are carried through **unchanged** from `/process-tasks`:

- **WIP cap** — `/process-tasks` step 4. Resolve `wip_limit` from
  `.task-config.yml` (default `3`), count current WIP (`in_progress` files + open
  `task-loop` PRs), and dispatch at most `wip_limit - current_wip`. Single-task
  mode (`/do-tasks` / `/do-tasks <slug>`) is not gated.
- **Multi-blocker readiness** — a task is dependency-ready only when **every**
  `is_blocked_by` entry is satisfied (target absent or `done`); `is_blocked_by`
  may be a single slug or a list (`[a, b]`). See `/process-tasks` step 1.

## 3. Report

For **remote** dispatch, report as `/process-tasks` step 5: list each dispatched
task (slug, title, that a remote session started) and point the user at `/tasks`
to monitor. For **`--local`**, there is no remote session — report the PR opened
in-session instead. In both cases, list separately any tasks skipped because they
are waiting on another task (with every unresolved blocker) or **held** by the
`-n N` ceiling or the WIP limit.
