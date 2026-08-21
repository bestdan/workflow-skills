---
name: task
description: Use when a user notices incidental work during development (stale config, tech debt, dead code, test gaps) they want to defer without losing context, says "task"/"todo"/"follow-up"/"we should come back to this", or runs /add-task, /do-tasks, /list-tasks, or /promote-tasks. ALSO use when the user starts work on an existing tracker issue by naming its key — "work on PRE-683", "let's do ABC-142", "pick up ENG-9", or pasting a Jira/Linear issue URL — because the issue must be claimed (assigned to the user, moved to its tracker's started state) before work begins.
---

# Task Loop — Capture and Process Follow-Up Work

Repo-native system for capturing follow-up work with full context and processing it automatically via remote Claude sessions.

## When to use

- User notices incidental work during a feature branch (stale flags, dead code, missing tests)
- User says "task", "todo", "follow-up", "we should come back to this", "add a task for this"
- User runs `/add-task` or `/do-tasks`
- User starts work on an existing tracker issue by naming its key or URL — see **Starting work on a named issue** below

## Starting work on a named issue

When the user opens work on an issue that already exists in the tracker — "work on
PRE-683", "let's do ABC-142", "pick up ENG-9", or a pasted Jira/Linear issue URL —
that issue must be **claimed before the first edit**: assigned to the user and moved
into its tracker's started state (`In Progress` on Jira/Linear; the `auto-claimed`
label on gh-issue, which has no status field). Without that, the board silently shows the
work as unstarted and unowned for its whole lifetime, and a parallel session can
claim the same issue.

Do not hand-roll the claim, and do not skip it because the user asked for code rather
than for bookkeeping. Route to the existing verbs:

- **`/deliver-task <KEY>`** — the default. Claims, implements, verifies, opens a PR,
  co-reviews, hands off at `needs_review`. Use when the ask is "do this issue".
- **`/do-tasks <KEY>`** — the batch-flow equivalent; same claim/execute path.
- **`/do-tasks <KEY> --claim-only`** — claim now, no execution. Use when the user
  wants to work the issue themselves (or interactively with you) but still wants the
  board correct. Resume later with `/do-tasks <KEY> --no-claim`.

The claim mechanics are the handler's, not this skill's: `commands/handlers/jira-claim.md`
("Claim the issue"), `commands/handlers/linear-claim.md`, `commands/handlers/gh-issue-claim.md`.
Each claims on a primitive that can elect one winner among sessions authenticated as the
**same** tracker account, and each refuses to move an issue to a completed status —
merge is the only completion signal.

The primitive differs per handler, and reporting a claim means reporting which one held:
jira and gh-issue create the `task/<KEY>` ref through GitHub's create-ref API, which
returns 422 rather than updating a ref that exists (a plain `git push` is **not** the
lock — see `commands/handlers/claim-lock.md`), and linear runs a
first-writer-wins election on a comment log carrying a unique per-session token. An
assignee re-read is **not** a lock on any of them: it confirms only that the final
assignee is your own account, which is identical for two sessions on the same account.
In a branch-pinned environment that cannot create `task/<KEY>`, jira and gh-issue degrade
to the same comment-token election and say so explicitly — never report an atomic claim
the run did not make.

**Resolve the handler from the merged view** — `dev_docs/tasks/.task-config.yml`
overlaid with the optional `dev_docs/tasks/.task-config.local.yml` — never from the
committed file alone (see `commands/task-config.md` → "Resolving the handler"). If the
resolved handler isn't the issue's tracker — including the missing-config case, which
resolves to `repo-pr` rather than to "no handler" — say so and offer
`/task-config jira` (or the right tracker) rather than editing the issue ad hoc over
the MCP. A handler that doesn't match the tracker is the usual reason a Jira issue
never gets claimed.

## How it works

### Capture (`/add-task`)

1. Gathers context from the current session (branch, diff, PR, conversation)
2. Drafts a structured task, presents it for user review
3. Resolves the **handler** from `dev_docs/tasks/.task-config.yml` (absent → `repo-pr`)
4. Delivers the task via that handler and reports the artifact URL

Capture is destination-agnostic; only the handler decides where the task lands.

A research spike's `defer` procedure offers `/add-task` as an alternative to a stub when the merged config resolves to an external tracker handler — see the two bridges in `skills/research-spike/SKILL.md`.

### Handlers and config

The delivery destination is a **handler** named in `dev_docs/tasks/.task-config.yml` — for `repo-pr` this is committed and shared; for the external-tracker handlers (`gh-issue`/`jira`/`linear`) `/task-config` adds it to the repo's local git exclude as local config (see that command for the `repo-pr` exception):

```yaml
handler: repo-pr # repo-pr (default) | gh-issue | jira | linear
wip_limit: 3 # optional — bounds /do-tasks in-flight work (repo-pr batch dispatch: shared; tracker pre-claim gates: per-operator); default 3
auto_execute_max_size: 2 # optional — repo-pr batch auto-routing: auto-execute size <= this, reserve bigger for a human; default 2
# handler-specific blocks (gh-issue / jira / linear) live under their own keys
```

Resolution: file absent or no `handler:` → `repo-pr`; unknown value → `/add-task` stops and points to `/task-config`. Every handler receives the same drafted task (`title`, body, `priority`, `tags`, `source_branch`, `source_pr`, `is_blocked_by`, …) and returns the URL of what it created.

`wip_limit` (default `3`) bounds in-flight work for `/do-tasks`. On the `repo-pr` file path it caps **batch** dispatch (`--all` / `-n N`) to `wip_limit - current_wip` so the human review bottleneck stays bounded; single-task dispatch is ungated. On the `gh-issue`, `jira`, and `linear` paths it's a **pre-claim gate** that runs on every claiming run, single ones included, and declines once in-flight work meets the limit (see the next paragraph for the one case that offers an override instead). How `current_wip` is counted: `commands/handlers/repo-pr-execute.md` (`repo-pr`), `commands/handlers/gh-issue-claim.md`, `commands/handlers/jira-claim.md`, and `commands/do-tasks.md` (`linear`).

**When the limit is met, a batch is bounded and a present human is asked.** The gate always runs, but what it does at the limit depends on the action. A **batch** — `--all`, `-n N`, a `--claim-only` batch, any batch remote dispatch — is bounded or declined unconditionally: presence at dispatch says nothing about the pull requests that land later. (It is the batch, never the transport, that decides: `repo-pr` dispatches single tasks remotely and those stay ungated.) A **single** claim in an attended session gets a one-keystroke override, because the human the cap protects is right there and can see the queue. The gate is never simply skipped, so an unattended run that misjudges itself declines rather than flooding the queue. `commands/handlers/attendedness.md` owns the rule and the reasoning; `--non-interactive` declares a run unattended outright.

**What counts as in-flight: this caller's own work, and only work that is actually moving.** Every tracker gate scopes its count to the claiming account (`assignee = currentUser()` in jira, `assignee:@me` in gh-issue, the viewer's id as `assignee` in linear) and drops parked issues (Jira's Impediment flag, and any status named in `jira.blocked_statuses`). A project-wide count is not a usable bound: on a shared team board other people's work alone exceeds any plausible limit, so the gate never opens and nothing is ever claimed. The trade is explicit — the limit budgets one operator, so N operators on one board hold N × `wip_limit` between them.

`auto_execute_max_size` (default `2`, `repo-pr` only) size-gates **batch** auto-routing: after ranking and the WIP gate, tasks with `size <= auto_execute_max_size` are claimed and executed (headless); bigger ones are **reserved** (claimed but not executed, `--claim-only` semantics) for a human to resume with `/do-tasks <slug> --no-claim`. Single-task mode is never gated, and explicit `--claim-only` / `--no-claim` override it. See `commands/handlers/repo-pr-config.md`.

Available handlers — each owns its own auth/preflight, config schema, prerequisites, and limitations:

| Handler    | Lands the task as…                                              | Reference file                                                                                                                                                                     |
| ---------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repo-pr`  | a committed markdown file in `dev_docs/tasks/` via PR (default) | `commands/handlers/repo-pr.md`                                                                                                                                                     |
| `gh-issue` | a GitHub Issue                                                  | `commands/handlers/gh-issue.md`                                                                                                                                                    |
| `jira`     | a Jira work item under an epic                                  | `commands/handlers/jira.md` + per-verb `jira-claim.md` / `jira-promote.md` / `jira-archive.md`                                                                                     |
| `linear`   | a Linear issue under a team                                     | `commands/handlers/linear-common.md` + per-verb `linear-add.md` / `linear-list.md` / `linear-claim.md` / `linear-complete.md` / `linear-sweep-complete.md` / `linear-reconcile.md` |

Set the handler with `/task-config` (which dispatches to `commands/handlers/<handler>-config.md`).

> Different handlers support different downstream commands. `/list-tasks` and `/do-tasks` dispatch to whichever handler is configured, but a handler may legitimately decline a verb. `/do-tasks` runs the file path for `repo-pr`, the tracker path for `linear`, the gh-issue path for `gh-issue`, and the jira path for `jira` (single only — needs `jira.ready_status` set). Of the reconciler verbs, `/complete-task` supports all four handlers (`linear`, `gh-issue`, `repo-pr`, and `jira`); `/sweep-for-complete` and `/reconcile-tasks` remain `linear`-only in v1. The handler files document what they do and don't support.

### Promote (`/promote-tasks`)

1. Scans `dev_docs/tasks/**/*.md` for tasks in `status: new`
2. Scores each against the confidence check (see Kanban columns below)
3. HIGH confidence → flips `status: ready`. LOW confidence → flips `status: needs_refinement` and sets `human_approval_requested: true`
4. Never touches tasks already past `new` — humans own demotions from `ready`

### Execute (`/do-tasks`)

`/do-tasks` is the **single execute verb** for turning ready tasks into PRs. It resolves the handler from `.task-config.yml` and dispatches like `/add-task` / `/list-tasks`, then runs the file path (`repo-pr`), the tracker path (`linear`), the gh-issue path (`gh-issue`), or the jira path (`jira`). See `commands/do-tasks.md`; the per-handler mechanics live in `commands/handlers/repo-pr-execute.md` (file path), `commands/handlers/linear-claim.md` (tracker path), `commands/handlers/gh-issue-claim.md` (gh-issue path), and `commands/handlers/jira-claim.md` (jira path).

Flag matrix:

|                      | What it does                                                                                        |
| -------------------- | --------------------------------------------------------------------------------------------------- |
| `/do-tasks`          | execute the single highest-ranked dependency-ready task                                             |
| `/do-tasks <slug>`   | execute a specific task (or, for `linear`, a specific issue id like `PRE-12`)                       |
| `/do-tasks --all`    | batch: all dependency-ready tasks, bounded by `wip_limit` (file path only)                          |
| `/do-tasks -n N`     | batch capped at the top `N`, then bounded by `wip_limit` (file path only)                           |
| `--remote` (default) | dispatch each task to its own cloud VM (file path)                                                  |
| `--local`            | run in the current session; caps the batch at 1 (file path)                                         |
| `--claim-only`       | run only the claim step (reserve the task); no execution, no PR. Batchable                          |
| `--no-claim`         | skip the claim step; execute a task this caller already claimed. Single only                        |
| `--non-interactive`  | declare no human present: never prompt, and the WIP gate declines rather than offering its override |

**Claim / execute split (`--claim-only`, `--no-claim`).** `/do-tasks` claims and executes atomically by default. These two **mutually exclusive** flags split that into composable steps: a `--claim-only` now plus a `--no-claim` later add up to one normal run. `--claim-only` runs only the claim half and stops (no file delete, no review PR). `--no-claim` skips claiming and executes a task the caller already claimed, guarding that it's already `in_progress` (`repo-pr`) or assigned in a `started` state (`linear`) — otherwise it stops, since executing an unclaimed task reopens the claim race. `--claim-only` is the one execute-family action safe to batch (bounded by the WIP gate); `--no-claim` is always single. Per-handler steps: `commands/do-tasks.md`.

Per-handler support:

|                   | `repo-pr` (file path)                                    | `linear` (tracker path)                                                                           |
| ----------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Where it runs     | **Remote** cloud agents (one per task), or `--local`     | **Foreground** in the current session                                                             |
| How many per call | Batch — one, several, or all dependency-ready (`--all`)  | At most one for execution; `--all` / `-n N --claim-only` may reserve several (pre-claim WIP gate) |
| Selection         | Anything in `status: ready` whose dependencies are clear | Model-judged feasibility — "can I finish this in-session?"                                        |
| WIP cap           | Batch dispatch bounded by `wip_limit`                    | Pre-claim gate: declines if **this caller's** in-flight work ≥ `wip_limit`                        |

**File path (`repo-pr`).** See `commands/handlers/repo-pr-execute.md`.

1. Scans `dev_docs/tasks/**/*.md` for tasks in `status: ready` and filters out ones still waiting on `is_blocked_by` (a task is dependency-ready only when **every** blocker is resolved); `is_blocked_by` may be a single slug or a list (`[a, b]`).
2. For each selected task, dispatches a remote Claude session (or runs in-session with `--local`) that:
   - Claims the task via the claim protocol — the lock is an open draft PR labeled `task-claim` naming the slug (see **Race conditions** and `commands/handlers/repo-pr-execute.md`).
   - Does the work described in the Task section
   - Deletes the task file and **converts the claim PR into the review PR** — relabels it `task-claim` → `task-loop` and marks it ready (the open `task-loop` PR is the implicit `needs_review` signal; merge is the implicit `done` signal — neither is written back to the file because the file is gone by then)
3. `--all` / `-n N` dispatch multiple dependency-ready tasks, each to its own cloud VM, bounded by `wip_limit` and then size-routed: tasks with `size <= auto_execute_max_size` (default `2`) execute, bigger ones are reserved (claimed, not executed) for a human. The executed and reserved groups are reported separately.

**Tracker path (`linear`).** See `commands/handlers/linear-claim.md`. Pulls one tracker-side issue the current session can plausibly finish, claims it, branches, executes, and opens a PR — all in the foreground.

1. Asks the handler for unclaimed, small-enough candidates.
2. Walks candidates in priority order and asks the model "can I finish this in this session without a human?" — first feasible candidate wins. Rejected candidates get a one-line skip comment in the tracker.
3. Handler claims the chosen candidate atomically (concurrency guard against parallel claims), branches from `<base>` (default `main`) using the handler-published branch name, does the work, opens a PR with a tracker-link in the body so the follow-on reconciler flow can complete the issue once that linked PR merges.
4. Bail path (mid-execution infeasibility): handler unclaims and flags for human review; `/do-tasks` stops without silently rolling to the next candidate.

### List (`/list-tasks`)

Renders a kanban view grouped by `status` column with priority, dependency blockers, tags, and expiry.

### Diagnostics (`/doctor`)

`/doctor` is the **explicit** "diagnose and fix my setup" entry point (see `commands/doctor.md`). It runs a set of checks — config validity (known `handler:`), handler prerequisites (gh auth / MCP reachability), legacy dirs, schema drift (reusing `scripts/validate.py`'s rules), and hygiene (expired tasks, orphan branches) — and reports `PASS`/`WARN`/`FAIL` per check. It is **read-only by default**; `/doctor --fix` applies the safe mechanical repairs (run the legacy migration, prune expired tasks, fill defaulted fields) and leaves judgment calls (unknown handler, failing auth) as reported warnings.

It **complements, not replaces, migrate-on-contact** (below): that implicit preflight keeps stale setups working without anyone invoking `/doctor`, while `/doctor` surfaces the same drift — and more — in one place on demand. Both reference the single migration procedure in this file.

## Task size

Every task carries a **size** — a Fibonacci story-point estimate of its scope: `1`, `2`, `3`, or `5`. `5` is the ceiling: **one task = one PR**, roughly ≤ ~300 lines of diff across ≤ ~5 files. If a task estimates larger than `5`, it is too big to capture as one card — break it into sub-tasks and chain them with `is_blocked_by`. When an _existing_ card turns out too big (or `/promote-tasks` flags it `scope exceeds size 5`), the `break-down-task` skill (`skills/break-down-task/SKILL.md`) finds the shear points and replaces it with the sub-tasks.

| size  | meaning                                                        |
| ----- | -------------------------------------------------------------- |
| `1`   | trivial — a few lines in one file                              |
| `2`   | small — a contained change across one or two files             |
| `3`   | moderate — several files, still one focused PR                 |
| `5`   | large — the upper bound of a single PR (~300 lines / ~5 files) |
| `> 5` | too big — do **not** capture as one task; split into sub-tasks |

This single scale governs the promotion confidence check (below), the `/do-tasks` tracker-path feasibility gate (which maps to a tracker's native estimate — Linear's `estimate` is the same Fibonacci scale), and the granularity rule in `plan-with-docs`. They point here rather than restating a threshold.

## Ranking

When a command picks the "next" task or orders a list, it ranks tasks by, in order:

1. **`priority`** — `urgent` > `high` > `medium` > `low` (urgent both sorts first in `/list-tasks` and is auto-promotable/auto-executable like any other tier — see the Confidence check's urgent-eligibility decision).
2. **Value/effort score** — `impact / size`, **descending** (`size` is effort, `impact` is value; both Fibonacci `1`/`2`/`3`/`5`). A task with no `impact` set, or a missing/invalid `size` (e.g. an unpromoted card that hasn't passed validation), has no score and ranks **last within its priority tier** — never dropped.
3. **Age** — oldest `created` first.

This ordering applies within each status section in `/list-tasks` (all tasks, including ones waiting on a blocker), while `/do-tasks` first filters to dependency-ready tasks and then applies the same order for selection. This is the **file-handler (`repo-pr`) ranking**. Linear has no native value/impact field, so the `linear` handler ranks by `priority` + `estimate` instead and does not compute a value/effort score.

## Task file format

Files live in `dev_docs/tasks/` (supports subdirectories). Markdown with YAML frontmatter.

```markdown
---
title: Imperative description under 80 chars
priority: low
size: 2
impact: 3
status: new
created: 2026-03-23
source_branch: bestdan/feat/example
source_pr: 42
related_files:
  - path/to/relevant/file.ts
  - path/to/another/file.ts
is_blocked_by: [fix-broken-import, other-task]
expires: 2026-04-22
tags:
  - cleanup
human_approval_requested: false
---

## Context

Why this exists. What you saw. Written for someone who has never seen this code.

## Task

1. Concrete step one
2. Concrete step two
3. Run tests

## Acceptance Criteria

- No remaining references to X
- Tests pass
```

### Field reference

| Field                      | Required | Description                                                                                                                                                                                                                                |
| -------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `title`                    | yes      | Imperative description, < 80 chars                                                                                                                                                                                                         |
| `priority`                 | yes      | `low` / `medium` / `high` / `urgent`                                                                                                                                                                                                       |
| `assignee`                 | no       | Human or agent accountable for the task. Mirrors gh-issue/Linear assignee for handler parity                                                                                                                                               |
| `size`                     | yes      | Fibonacci story points: `1` / `2` / `3` / `5`. Larger ⇒ split into sub-tasks. See **Task size**                                                                                                                                            |
| `impact`                   | no       | Fibonacci value estimate: `1` / `2` / `3` / `5`, mirroring `size`. Used for value/effort ranking. See **Task size**                                                                                                                        |
| `status`                   | yes      | `new` / `needs_refinement` / `ready` / `in_progress` / `blocked` / `needs_review` / `done`                                                                                                                                                 |
| `created`                  | yes      | ISO date                                                                                                                                                                                                                                   |
| `source_branch`            | yes      | Branch where task was identified                                                                                                                                                                                                           |
| `source_pr`                | no       | PR number if already open                                                                                                                                                                                                                  |
| `related_files`            | yes      | Paths the consumer should read for context. May be empty if `tags` includes `scope: research`                                                                                                                                              |
| `is_blocked_by`            | no       | Slug/id of a blocker, or a list of slugs (`[a, b]`). A single string stays valid. Ready only when **all** blockers resolve                                                                                                                 |
| `parent`                   | no       | Slug of an epic this task belongs to (grouping, distinct from `is_blocked_by` ordering)                                                                                                                                                    |
| `tracker_id`               | no       | Tracker issue id recorded when a vetted plan is pushed to a tracker (Linear `PRE-12`, Jira `PLAT-123`, gh-issue `owner/repo#45`). Its presence makes re-push skip the task (create-missing-only). Written by the planned `/push-plan` flow |
| `tracker_url`              | no       | Web URL of the pushed tracker issue, recorded alongside `tracker_id`                                                                                                                                                                       |
| `expires`                  | yes      | ISO date. Default: 30 days from creation.                                                                                                                                                                                                  |
| `tags`                     | no       | Freeform tags for filtering (e.g., `cleanup`, `tests`)                                                                                                                                                                                     |
| `human_approval_requested` | no       | Forces card into `needs_refinement` until a human flips it back                                                                                                                                                                            |

### Body sections

- **Context** (required) — Why this exists. What you saw.
- **Task** (required) — Concrete steps. Specific enough for an agent to execute.
- **Acceptance Criteria** (required, ≥ 1 bullet) — Definition of done. Missing or empty section blocks promotion to `ready`.
- **Open Questions** / **TBD** (optional) — Presence of either with non-empty content blocks promotion to `ready`.

## Epics

An **epic** groups related tasks so the board can show a rollup. Tasks are otherwise flat — `is_blocked_by` gives ordering but no grouping. An epic is a first-class file, distinguished from a task card by `type: epic` in its frontmatter:

```yaml
---
type: epic
title: Task Loop Improvements
status: active # active | done | abandoned
owner: dan # optional — omit when unknown
created: 2026-06-07
---
```

A `plan-with-docs` overview (`<name>_plan.md`) is written as this epic file — see `skills/plan-with-docs/SKILL.md`.

When a plan is pushed to a tracker, the epic file records the grouping container's id (Linear project, Jira epic, gh-issue milestone) in the same optional `tracker_id` / `tracker_url` fields a task uses for its issue id — written by the `/push-plan` flow. This recorded id is transient: once the whole plan has migrated, `/push-plan` deletes the epic file and the plan directory along with the migrated task files, leaving the tracker container as the only source of truth. On the `repo-pr` handler nothing is pushed or deleted — the files stay.

**Epic slug.** An epic's slug is its filename stem with a trailing `_plan` removed (so `task_loop_improvements_plan` → `task_loop_improvements`); a standalone epic file not named `*_plan.md` uses its bare stem.

**Membership.** A task belongs to an epic when **either**:

- its `parent` field equals the epic slug, **or**
- it lives anywhere in the epic's plan directory tree (`<name>_plan/`) — membership is **recursive**, so tasks nested in `phase_N/` subdirectories count too, not just direct siblings of the overview file.

`parent` is grouping; it is distinct from `is_blocked_by` (ordering). A task may have a `parent` and no blockers, or vice versa.

**Rollup.** `/list-tasks` renders one line per epic — `<done>/<total> done`, plus in-progress and blocked, with the epic's owner and status. Members are drawn from **two** sources, keyed by task slug: present task **files** (by `parent` or plan directory), and `task-loop` **PRs** whose work branch matches `task/<epic-slug>_` (the `<name>_task_N` plan naming). A merged matched PR counts as `done`, an open one as in-flight; the two sources are de-duplicated by slug so a member is counted once with its most-advanced signal. This recovers true `done` progress even though the `repo-pr` handler deletes a task file when its PR opens. Two limits: the merged-PR query is a recent window (so `done` is best-effort for very old epics), and branch matching only catches the plan naming — a standalone task added via `parent:` drops from the rollup once its file is merged-and-deleted.

**Scans skip epics.** `/promote-tasks` and `/do-tasks` (and the `repo-pr` execute scan) ignore any file with `type: epic` — an epic is never scored, ranked, or executed as a task. `scripts/validate.py` checks epic files against the epic shape (`title` and `status` required; `owner` optional but, when present, a non-empty string), not the task shape.

## Kanban columns

The seven `status` values form a kanban flow. Cards move between columns via specific actions:

| Column             | Card enters when…                                      | Card leaves when…                                                                                                            |
| ------------------ | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `new`              | `/add-task` writes the card                            | `/promote-tasks` scores it                                                                                                   |
| `needs_refinement` | Promoter scored LOW, or human demoted from `ready`     | Human edits the card, clears `human_approval_requested`, AND sets `status: ready` (the promoter does not re-scan past `new`) |
| `ready`            | Promoter scored HIGH                                   | `/do-tasks` claims it                                                                                                        |
| `in_progress`      | Claim (status flip + open draft `task-claim` PR)       | Review PR readied (claim PR relabeled `task-claim`→`task-loop`; file deleted in that PR)                                     |
| `blocked`          | Agent or human sets it with a `Consumer Notes` reason  | Blocker resolved → returns to `in_progress`                                                                                  |
| `needs_review`     | Claim PR readied as a `task-loop` PR (any head branch) | PR merged or closed                                                                                                          |
| `done`             | PR merged                                              | terminal                                                                                                                     |

> **`needs_review` and `done` are PR-derived for the `repo-pr` handler.** The task file is deleted as part of readying the review PR, so it cannot carry these statuses in the file system. `/list-tasks` populates these two columns by querying `gh pr list --label task-loop --state open` (needs_review) and `--state merged` (recent done). An in-flight **claim** uses the separate `task-claim` label (and keeps its `in_progress` file), so it does **not** appear in `needs_review` — the claim PR only becomes a `task-loop` PR once the work is done and the file is deleted. For external handlers (Linear, Jira, GH Issues) the external tool carries the state directly.
>
> For the `linear` handler specifically, `done` is not entered by a native merge integration in v1. The issue reaches `done` when `/sweep-for-complete` or `/reconcile-tasks` detects that issue's own linked PR merged and then drives `/complete-task`. The `repo-pr`, `gh-issue`, and `jira` paths keep their native merge-derived completion, with `/complete-task` as the explicit fallback when that auto-close doesn't fire — for `gh-issue` when `Closes #<n>` didn't close the issue, and for `jira` when its GitHub integration or smart commits aren't configured or didn't transition it. The jira **execute** path still deliberately never transitions an issue to a `Done`-category status itself (see `commands/handlers/jira-claim.md`); `/complete-task` → `commands/handlers/jira-complete.md` is the one place allowed to.

### Confidence check (used by `/promote-tasks`)

**Decision: `urgent` is auto-promotable.** `priority: urgent` is scored exactly
like `high`/`medium`/`low` — there is no `priority ≠ urgent` HIGH condition.
Reading `urgent` as "escalate to a human" made the highest-priority tier the
_only_ one that could never be picked up autonomously, which is backwards: an
equally valid reading is "do this fastest, by whatever capacity is available,
including an agent." The one real tension — auto-promoting urgent makes it
eligible for **unattended** auto-execution — is already covered by the
existing per-task `human_approval_requested: true` HIGH check
(`human_approval_not_requested`): a task that genuinely needs human eyes is
held by that escape hatch regardless of priority, so no separate urgent-only
gate is needed. **Rejected alternative:** keep urgent human-only (status quo)
— rejected because it inverted the priority signal (highest priority ⇒ least
autonomous), for no benefit `human_approval_requested` didn't already provide.

**Backfill (before scoring).** The promoter backfills two fields on every `status: new` candidate **except one held on an unresolved blocker**, which is left entirely untouched (see the held-card rule below). Otherwise it is unconditional — even a candidate that will fail some other check and land in `needs_refinement` anyway is backfilled, so the human has less to redo:

- `priority` missing → defaulted to `medium`. A flat static default is correct here: `priority` only orders work, it never gates anything. `urgent` is never auto-set.
- `size` missing or not one of `1` / `2` / `3` / `5` → **estimated** by the promoter (Fibonacci `1`/`2`/`3`/`5`) from the card body and `related_files` breadth. This is deliberately not a static default: `size` feeds the one-task-one-PR ceiling and `auto_execute_max_size` routing downstream, so a blind constant could misroute work. Producing the number is the same judgment the scope-fit check below already requires — deciding whether the scope fits size `5` — backfill just records it. If the honest estimate would exceed `5`, no value is written; the scope-fit check below scores LOW instead (reason: `scope exceeds size 5 — split into sub-tasks`).

Both backfills are recorded with provenance a human can cheaply correct: a `# promoter:` frontmatter comment on the file path (`# promoter: priority defaulted to medium`, `# promoter: size auto-estimated`), or a one-line issue comment on the Linear path. `dry-run` reports the intended backfills and writes nothing.

**An auto-estimated `size` is fully trusted downstream**, exactly like a human-set one: it is eligible for `auto_execute_max_size` headless batch auto-execution, with no reserve-only carve-out for auto-estimated cards. The auto-execute path still ends in a PR a human reviews, and a mis-estimated task beats a permanently blocked one.

- **HIGH** (→ `ready`): all required fields present (after backfill); `size` is one of `1` / `2` / `3` / `5` (after backfill); Acceptance Criteria section has ≥ 1 bullet; body contains no `Open Questions` / `TBD` section with content; and the promoter **judges** the described scope to plausibly fit within size `5` (~300 lines / ~5 files — see **Task size**), weighing the stated `size`, the `## Task` steps, and `related_files` breadth. This last gate is model judgment, not a keyword scan: a title merely containing "migrate"/"refactor" does not fail it, while one implying multi-file rework that exceeds size `5` does (reason: `scope exceeds size 5 — split into sub-tasks`).
- **LOW** (→ `needs_refinement`, set `human_approval_requested: true`): any of the above fails, or `human_approval_requested` is already true.

The scope gate is judgment, not a deterministic rule — acceptable because `/promote-tasks` is not a blocking CI gate; a misjudged card waits in `needs_refinement` for a human rather than being lost. The other HIGH checks remain deterministic.

On the file path, `scripts/task-scan.py` computes `promote_gate` from the file **before** backfill and is not re-run after — so the promoter must re-evaluate `required_fields_present` and `size_valid` itself against the backfilled values rather than trusting `promote_gate.high` as-is; every other check in `promote_gate` is unaffected by backfill and stays trusted (see `commands/promote-tasks.md` step 2).

A card with an unresolved `is_blocked_by` entry (target card present and not `done`) is **held** instead of scored: it is left in `status: new`, not promoted to `ready` and not demoted to `needs_refinement`, so it stays in the scanned pool and auto-promotes once the blocker clears. Demoting to `needs_refinement` is deliberately avoided — the promoter only scans `status: new`, so a demoted card would never be re-checked.

## Lifecycle

```
new --> needs_refinement <--> ready --> in_progress --> needs_review --> done
                                           |                                
                                           +--> blocked --> in_progress     
                                                                            
expired (auto-pruned once the `expires` date passes while status is non-terminal; default expires = 30 days from creation, see Field reference)
```

## Branch naming

Two namespaces to avoid collisions:

- `task/add/<slug>` — the PR that adds the task file (auto-merged)
- `task/<slug>` — the PR that does the work and deletes the task file

## Scanning

Always scan recursively: `dev_docs/tasks/**/*.md`. Subdirectories are optional organizational structure.

## Legacy migration

Earlier versions of this system stored tasks under `dev_docs/todos/` (and `plan-with-docs` wrote plans under `dev_docs/todo/`). There is no dual-path support — instead, migrate once on contact. (`/doctor` exposes the same procedure as an explicit **Legacy dirs** check — reported by default, run under `--fix` — but the on-contact preflight below stays the implicit path so a stale setup heals without invoking `/doctor`.)

If a command finds a legacy `dev_docs/todos/` (task store) or `dev_docs/todo/` (plans) directory, pause before proceeding and prompt once:

> Found legacy `dev_docs/todos/`. Migrate to `dev_docs/tasks/`? [migrate / skip once]

- **migrate**: `dev_docs/tasks/` may already exist (e.g. it holds `plan-with-docs` output), so do **not** `git mv` the legacy directory onto it — that nests the source inside the target (`dev_docs/tasks/todos/`) instead of merging. Instead: `mkdir -p dev_docs/tasks`, then `git mv` each **child** of the legacy directory into `dev_docs/tasks/` (resolving any name collision by keeping both, e.g. suffixing the incoming file), rename `.todo-config.yml` → `.task-config.yml` if present, and remove the now-empty legacy directory. Migrate `dev_docs/todos/` (task store) and `dev_docs/todo/` (plans) the same way, reporting each separately. Leave in-flight branches (`todo/<slug>`) and the `todo-loop` PR label untouched — they are historical and harmless.
- **skip once**: proceed without migrating and do not re-prompt during this invocation.

## Race conditions

Each remote session gets an isolated VM with a fresh clone, so filesystem races are impossible. The only contention is **claiming the same `ready` task twice**. The claim lock is an **open draft PR labeled `task-claim` that names the slug** (body carries `Claims-task: <slug>`), queried via the GitHub API — _not_ the first push of `task/<slug>`. Because the lock is the PR and not the branch name, it works even in branch-pinned environments (e.g. Claude Code on the web, which forbids pushing off its fixed `claude/<session>` branch). Full mechanics: the **Claim protocol** in `commands/handlers/repo-pr-execute.md`.

The claim is acquired in four steps:

1. **Pre-claim check** — bail if an open `task-claim`, `task-loop`, **or `task-blocked`** PR already names the slug (a `task-blocked` match means the task is parked for a human — stop and skip, never re-claim).
2. **Acquire** — flip `status: ready → in_progress`, push to whatever branch the environment allows.
3. **Open the draft `task-claim` PR.**
4. **Reconcile** — if two PRs named the same slug, the lowest PR number wins; the others `gh pr close` and bail. This deterministically closes the window where two sessions on different branches both pass the pre-claim check.

**Blocked / bail.** A task that cannot be finished goes to `status: blocked` (file kept) and its draft PR is **relabeled `task-claim` → `task-blocked` and left open** — _not_ closed. The `blocked` flip lives only on the unmerged branch (`main` still shows `ready`), so closing the PR would leave no visible marker and the next scanner would re-claim the failing task in a loop. The open `task-blocked` PR keeps the block visible (with its `Consumer Notes`), and the pre-claim check treats it as a marker to skip claiming that slug until a human resolves the block (pushes a fix or closes the PR to release it).

## Remote session notes

Remote sessions (`claude --remote`) run in cloud VMs and don't have access to locally-installed plugins. The `/add-task` and `/do-tasks` commands handle this by embedding all necessary instructions directly in the remote prompt. The remote agent doesn't need to know about this plugin — it just follows the instructions in its prompt.
