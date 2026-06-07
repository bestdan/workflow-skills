---
name: task
description: Capture and process follow-up work discovered during development. Use when a user notices incidental work (stale config, tech debt, dead code, test gaps) they want to defer without losing context. Provides the task file format, creation workflow, and processing logic via remote Claude sessions.
---

# Task Loop — Capture and Process Follow-Up Work

Repo-native system for capturing follow-up work with full context and processing it automatically via remote Claude sessions.

## When to use

- User notices incidental work during a feature branch (stale flags, dead code, missing tests)
- User says "task", "todo", "follow-up", "we should come back to this", "add a task for this"
- User runs `/add-task` or `/do-tasks`

## How it works

### Capture (`/add-task`)

1. Gathers context from the current session (branch, diff, PR, conversation)
2. Drafts a structured task, presents it for user review
3. Resolves the **handler** from `dev_docs/tasks/.task-config.yml` (absent → `repo-pr`)
4. Delivers the task via that handler and reports the artifact URL

Capture is destination-agnostic; only the handler decides where the task lands.

### Handlers and config

The delivery destination is a **handler** named in a repo-committed config file, `dev_docs/tasks/.task-config.yml`:

```yaml
handler: repo-pr # repo-pr (default) | gh-issue | jira | linear
wip_limit: 3 # optional — bounds /do-tasks in-flight work (repo-pr batch dispatch + linear pre-claim gate); default 3
auto_execute_max_size: 2 # optional — repo-pr batch auto-routing: auto-execute size <= this, reserve bigger for a human; default 2
# handler-specific blocks (gh-issue / jira / linear) live under their own keys
```

Resolution: file absent or no `handler:` → `repo-pr`; unknown value → `/add-task` stops and points to `/task-config`. Every handler receives the same drafted task (`title`, body, `priority`, `tags`, `source_branch`, `source_pr`, `is_blocked_by`, …) and returns the URL of what it created.

`wip_limit` (default `3`) bounds in-flight work for `/do-tasks` on both execute paths. On the `repo-pr` file path it caps **batch** dispatch: `/do-tasks --all` counts current work-in-flight — tasks with `status: in_progress` plus open `task-loop` PRs (the `needs_review` queue) — and dispatches at most `wip_limit - current_wip` tasks, holding the rest so the human PR-review bottleneck stays bounded (single-task dispatch is ungated). On the `linear` tracker path it is a **pre-claim gate** that declines a claim — including a single claim — when in-flight work already meets the limit. See `commands/handlers/repo-pr-config.md`.

`auto_execute_max_size` (default `2`, `repo-pr` only) size-gates the **batch** auto-routing for `/do-tasks --all` / `-n N`: after ranking and the WIP gate, tasks with `size <= auto_execute_max_size` are claimed and executed (headless), while bigger ones are **reserved** (`--claim-only` semantics — claimed but not executed) for a human to resume with `/do-tasks <slug> --no-claim`. This turns the small-vs-big triage into a policy instead of a manual sort. Single-task mode (`/do-tasks` / `/do-tasks <slug>`) is **not** gated — an explicit pick is always executed in full — and explicit `--claim-only` / `--no-claim` override the gate. See `commands/handlers/repo-pr-config.md`.

Available handlers — each owns its own auth/preflight, config schema, prerequisites, and limitations:

| Handler    | Lands the task as…                                              | Reference file                                                                                         |
| ---------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `repo-pr`  | a committed markdown file in `dev_docs/tasks/` via PR (default) | `commands/handlers/repo-pr.md`                                                                         |
| `gh-issue` | a GitHub Issue                                                  | `commands/handlers/gh-issue.md`                                                                        |
| `jira`     | a Jira work item under an epic                                  | `commands/handlers/jira.md`                                                                            |
| `linear`   | a Linear issue under a team                                     | `commands/handlers/linear-common.md` + per-verb `linear-add.md` / `linear-list.md` / `linear-claim.md` |

Set the handler with `/task-config` (which dispatches to `commands/handlers/<handler>-config.md`).

> Different handlers support different downstream commands. `/list-tasks` and `/do-tasks` dispatch to whichever handler is configured, but a handler may legitimately decline a verb. `/do-tasks` runs the file path for `repo-pr` and the tracker path for `linear`; `jira`/`gh-issue` have no execute path yet. The handler files document what they do and don't support.

### Promote (`/promote-tasks`)

1. Scans `dev_docs/tasks/**/*.md` for tasks in `status: new`
2. Scores each against the confidence check (see Kanban columns below)
3. HIGH confidence → flips `status: ready`. LOW confidence → flips `status: needs_refinement` and sets `human_approval_requested: true`
4. Never touches tasks already past `new` — humans own demotions from `ready`

### Execute (`/do-tasks`)

`/do-tasks` is the **single execute verb** for turning ready tasks into PRs. It resolves the handler from `.task-config.yml` and dispatches like `/add-task` / `/list-tasks`, then runs the file path (`repo-pr`) or the tracker path (`linear`). `jira`/`gh-issue` have no execute path yet. See `commands/do-tasks.md`; the per-handler mechanics live in `commands/handlers/repo-pr-execute.md` (file path) and `commands/handlers/linear-claim.md` (tracker path).

Flag matrix:

|                      | What it does                                                                  |
| -------------------- | ----------------------------------------------------------------------------- |
| `/do-tasks`          | execute the single highest-ranked dependency-ready task                       |
| `/do-tasks <slug>`   | execute a specific task (or, for `linear`, a specific issue id like `PRE-12`) |
| `/do-tasks --all`    | batch: all dependency-ready tasks, bounded by `wip_limit` (file path only)    |
| `/do-tasks -n N`     | batch capped at the top `N`, then bounded by `wip_limit` (file path only)     |
| `--remote` (default) | dispatch each task to its own cloud VM (file path)                            |
| `--local`            | run in the current session; caps the batch at 1 (file path)                   |
| `--claim-only`       | run only the claim step (reserve the task); no execution, no PR. Batchable    |
| `--no-claim`         | skip the claim step; execute a task this caller already claimed. Single only  |

**Claim / execute split (`--claim-only`, `--no-claim`).** `/do-tasks` claims and executes atomically by default. These two **mutually exclusive** flags split that into composable steps so a claim now plus a `--no-claim` execute later add up to one normal run. `--claim-only` runs the claim half and stops (`repo-pr`: run the claim protocol — flip `status: ready → in_progress` and open the draft `task-claim` PR that names the slug — no file delete, no `task-loop` review PR; `linear`: run only "Claim the issue" — the atomic `auto-claimed` guard, move to the `started`-type state, record the branch name — then stop). `--no-claim` skips claiming and executes a task the caller already claimed, guarding that it is already `in_progress` (`repo-pr`, which then checks out the open `task-claim` PR's head branch to resume) or assigned to the caller in a `started` state (`linear`) — otherwise it stops, since executing an unclaimed task reopens the claim race. `--claim-only` is the one execute-family action safe to batch (`--all` / `-n N`, bounded by the WIP gate); `--no-claim` is always single. See `commands/do-tasks.md`.

Per-handler support:

|                   | `repo-pr` (file path)                                    | `linear` (tracker path)                                                                           |
| ----------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Where it runs     | **Remote** cloud agents (one per task), or `--local`     | **Foreground** in the current session                                                             |
| How many per call | Batch — one, several, or all dependency-ready (`--all`)  | At most one for execution; `--all` / `-n N --claim-only` may reserve several (pre-claim WIP gate) |
| Selection         | Anything in `status: ready` whose dependencies are clear | Model-judged feasibility — "can I finish this in-session?"                                        |
| WIP cap           | Batch dispatch bounded by `wip_limit`                    | Pre-claim gate: declines if in-flight work ≥ `wip_limit`                                          |

**File path (`repo-pr`).** See `commands/handlers/repo-pr-execute.md`.

1. Scans `dev_docs/tasks/**/*.md` for tasks in `status: ready` and filters out ones still waiting on `is_blocked_by` (a task is dependency-ready only when **every** blocker is resolved); `is_blocked_by` may be a single slug or a list (`[a, b]`).
2. For each selected task, dispatches a remote Claude session (or runs in-session with `--local`) that:
   - Claims the task via the **branch-name-independent claim protocol** (sets `status: in_progress`, then opens a **draft PR labeled `task-claim`** that names the slug — that open draft PR is the lock; see **Race conditions**). The claim no longer relies on being the first to push `task/<slug>`, so it works in branch-pinned environments.
   - Does the work described in the Task section
   - Deletes the task file and **converts the claim PR into the review PR** — relabels it `task-claim` → `task-loop` and marks it ready (the open `task-loop` PR is the implicit `needs_review` signal; merge is the implicit `done` signal — neither is written back to the file because the file is gone by then)
3. `--all` / `-n N` dispatch multiple dependency-ready tasks, each to its own cloud VM, bounded by `wip_limit` and then size-routed: tasks with `size <= auto_execute_max_size` (default `2`) execute, bigger ones are reserved (claimed, not executed) for a human. The executed and reserved groups are reported separately.

**Tracker path (`linear`).** See `commands/handlers/linear-claim.md`. Pulls one tracker-side issue the current session can plausibly finish, claims it, branches, executes, and opens a PR — all in the foreground.

1. Asks the handler for unclaimed, small-enough candidates.
2. Walks candidates in priority order and asks the model "can I finish this in this session without a human?" — first feasible candidate wins. Rejected candidates get a one-line skip comment in the tracker.
3. Handler claims the chosen candidate atomically (concurrency guard against parallel claims), branches from `<base>` (default `main`) using the handler-published branch name, does the work, opens a PR with a tracker-link in the body so the merge automatically marks the work done.
4. Bail path (mid-execution infeasibility): handler unclaims and flags for human review; `/do-tasks` stops without silently rolling to the next candidate.

### List (`/list-tasks`)

Renders a kanban view grouped by `status` column with priority, dependency blockers, tags, and expiry.

## Task size

Every task carries a **size** — a Fibonacci story-point estimate of its scope: `1`, `2`, `3`, or `5`. `5` is the ceiling: **one task = one PR**, roughly ≤ ~300 lines of diff across ≤ ~5 files. If a task estimates larger than `5`, it is too big to capture as one card — break it into sub-tasks and chain them with `is_blocked_by`.

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

1. **`priority`** — `urgent` > `high` > `medium` > `low` (urgent is human-only, so the auto-execute path never selects it, but it still sorts first in `/list-tasks`).
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

| Field                      | Required | Description                                                                                                                |
| -------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------- |
| `title`                    | yes      | Imperative description, < 80 chars                                                                                         |
| `priority`                 | yes      | `low` / `medium` / `high` / `urgent` (urgent = human-only)                                                                 |
| `assignee`                 | no       | Human or agent accountable for the task. Mirrors gh-issue/Linear assignee for handler parity                               |
| `size`                     | yes      | Fibonacci story points: `1` / `2` / `3` / `5`. Larger ⇒ split into sub-tasks. See **Task size**                            |
| `impact`                   | no       | Fibonacci value estimate: `1` / `2` / `3` / `5`, mirroring `size`. Used for value/effort ranking. See **Task size**        |
| `status`                   | yes      | `new` / `needs_refinement` / `ready` / `in_progress` / `blocked` / `needs_review` / `done`                                 |
| `created`                  | yes      | ISO date                                                                                                                   |
| `source_branch`            | yes      | Branch where task was identified                                                                                           |
| `source_pr`                | no       | PR number if already open                                                                                                  |
| `related_files`            | yes      | Paths the consumer should read for context. May be empty if `tags` includes `scope: research`                              |
| `is_blocked_by`            | no       | Slug/id of a blocker, or a list of slugs (`[a, b]`). A single string stays valid. Ready only when **all** blockers resolve |
| `parent`                   | no       | Slug of an epic this task belongs to (grouping, distinct from `is_blocked_by` ordering)                                    |
| `expires`                  | yes      | ISO date. Default: 30 days from creation.                                                                                  |
| `tags`                     | no       | Freeform tags for filtering (e.g., `cleanup`, `tests`)                                                                     |
| `human_approval_requested` | no       | Forces card into `needs_refinement` until a human flips it back                                                            |

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

### Confidence check (used by `/promote-tasks`)

- **HIGH** (→ `ready`): all required fields present; `size` is one of `1` / `2` / `3` / `5`; Acceptance Criteria section has ≥ 1 bullet; body contains no `Open Questions` / `TBD` section with content; `priority` ≠ `urgent`; and the promoter **judges** the described scope to plausibly fit within size `5` (~300 lines / ~5 files — see **Task size**), weighing the stated `size`, the `## Task` steps, and `related_files` breadth. This last gate is model judgment, not a keyword scan: a title merely containing "migrate"/"refactor" does not fail it, while one implying multi-file rework that exceeds size `5` does (reason: `scope exceeds size 5 — split into sub-tasks`).
- **LOW** (→ `needs_refinement`, set `human_approval_requested: true`): any of the above fails, or `human_approval_requested` is already true.

The scope gate is judgment, not a deterministic rule — acceptable because `/promote-tasks` is not a blocking CI gate; a misjudged card waits in `needs_refinement` for a human rather than being lost. The other HIGH checks remain deterministic.

Note: `is_blocked_by` is intentionally **not** checked here. `/do-tasks`'s runtime filter already skips dependency-blocked cards, and re-evaluating blockers would strand otherwise-ready cards in `needs_refinement` forever (the promoter only scans `status: new`).

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

Earlier versions of this system stored tasks under `dev_docs/todos/` (and `plan-with-docs` wrote plans under `dev_docs/todo/`). There is no dual-path support — instead, migrate once on contact.

If a command finds a legacy `dev_docs/todos/` (task store) or `dev_docs/todo/` (plans) directory, pause before proceeding and prompt once:

> Found legacy `dev_docs/todos/`. Migrate to `dev_docs/tasks/`? [migrate / skip once]

- **migrate**: `dev_docs/tasks/` may already exist (e.g. it holds `plan-with-docs` output), so do **not** `git mv` the legacy directory onto it — that nests the source inside the target (`dev_docs/tasks/todos/`) instead of merging. Instead: `mkdir -p dev_docs/tasks`, then `git mv` each **child** of the legacy directory into `dev_docs/tasks/` (resolving any name collision by keeping both, e.g. suffixing the incoming file), rename `.todo-config.yml` → `.task-config.yml` if present, and remove the now-empty legacy directory. Migrate `dev_docs/todos/` (task store) and `dev_docs/todo/` (plans) the same way, reporting each separately. Leave in-flight branches (`todo/<slug>`) and the `todo-loop` PR label untouched — they are historical and harmless.
- **skip once**: proceed without migrating and do not re-prompt during this invocation.

## Race conditions

Each remote session gets its own isolated VM with a fresh clone. Filesystem races are impossible. The contention point is **claiming the same `ready` task twice**. The claim lock is an **open draft PR labeled `task-claim` that names the slug** (carried in the body as `Claims-task: <slug>`), queried from the GitHub API — _not_ the first push of `task/<slug>`. Full mechanics: the **Claim protocol** in `commands/handlers/repo-pr-execute.md`.

The claim is acquired in four steps: **pre-claim check** (bail if an open `task-claim`, `task-loop`, **or `task-blocked`** PR already names the slug — a `task-blocked` match means the task is parked for a human, so it is never re-claimed) → **acquire** (flip `status: ready → in_progress`, push to whatever branch the environment allows) → **open the draft `task-claim` PR** → **reconcile** (if two PRs ended up naming the same slug, the lowest PR number wins; the others close and bail).

- **Visible from a fresh clone.** The claim lives in the GitHub API (`gh pr list`), so a later scanner sees it immediately — unlike the old `status: in_progress` flip, which sat on the claimer's unmerged branch and was invisible to a fresh clone of `main`.
- **Branch-name independent → safe in branch-pinned environments.** Claude Code on the web pins each session to a fixed `claude/<session>` branch and forbids pushing elsewhere, so such a session can never create `task/<slug>`. Because the lock is the draft PR (which can be opened from _any_ head branch) and not the branch name, two such sessions can no longer both claim the same task: the second observes the first's `task-claim` PR in the pre-claim check (or, if both share one fixed branch, GitHub rejects the second `gh pr create` — one open PR per head→base pair) and bails.
- **Race window and its closer.** Two sessions on _different_ branches can both pass the pre-claim check concurrently and both open a draft PR. The **reconcile** step closes this window deterministically: re-query, lowest PR number wins, the rest `gh pr close` and bail. No coordination or locking service is needed.
- **Belt-and-suspenders.** Where branches _are_ settable, `git push -u origin task/<slug>` still fails fast on a second push, an early-out before the PR steps — but it is no longer the _only_ collision point, so the loser-must-control-the-branch failure mode is gone.
- **Blocked / bail.** A task that cannot be finished goes to `status: blocked` (file kept) and its draft PR is **relabeled `task-claim` → `task-blocked` and left open** — _not_ closed. The `blocked` flip lives only on the unmerged branch (`main` still shows `ready`), so closing the PR would leave no visible marker and the next scanner would re-claim the same failing task in a loop. The open `task-blocked` PR keeps the block visible with its `Consumer Notes`, and the pre-claim check treats a `task-blocked` PR as a block marker — it **stops and skips claiming that slug** (it does not ignore the PR) — so nothing re-claims the task until a human resolves the block (pushes a fix or closes the PR to release it).

## Remote session notes

Remote sessions (`claude --remote`) run in cloud VMs and don't have access to locally-installed plugins. The `/add-task` and `/do-tasks` commands handle this by embedding all necessary instructions directly in the remote prompt. The remote agent doesn't need to know about this plugin — it just follows the instructions in its prompt.
