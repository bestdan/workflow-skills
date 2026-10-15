# Auto-pilot work-source adapters — reference

`/auto-pilot` runs against a **Linear project**, a **GitHub milestone or label
query** (the `gh-issue` handler), or a **plan-with-docs directory**. All three
normalize to the same [run-state representation](run-state.md);
everything downstream — the launch pre-flight, the run loop, `--resume` — reads
that representation and never branches on which source it came from.

An **adapter** is the seam that makes that true. Each adapter implements the
eight verbs below by **delegating to its handler's existing sections** — it adds
no new tracker logic of its own. The source-specific differences (WIP caps,
labels, MCP read-lag, branch naming, what "needs a human" looks like) live
**inside the adapter**, so no downstream phase ever has to know them.

> **Compose, never duplicate.** Every verb row cites the handler file + section
> it delegates to, or is marked an explicit **no-op**. If a verb ever grows its
> own copy of claim/promote/WIP logic here, that is the bug — the logic lives in
> the handler, and the adapter only wires this run's inputs into it.

## The interface

Eight verbs. Each has a contract (what the run loop expects back) and a failure
behavior (what the adapter does when the underlying handler step fails).

| Verb               | Contract                                                                                      | Failure behavior                                                                           |
| ------------------ | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `list_ready`       | Return the source's **ready, unblocked** tasks, ranked — the loop's candidate queue           | Empty list on a clean "nothing ready"; raise only on a source read failure                 |
| `dependency_graph` | Return the blocker edges among in-scope tasks (used to pick each task's base + stack chains)  | Missing/partial edges → treat the task as independent (base `main`), note it               |
| `claim`            | Reserve **one** task first-writer-wins; return the claim handle + the branch name to build on | Lost race / in flight / blocked → return "not claimed" + reason; the loop writes `skipped` |
| `link_pr`          | Attach the opened PR to the task; **leave status unchanged** (the pr-open half of hand-off)   | Attach failure → `flag_for_human` (a built-but-unlinked PR needs a human)                  |
| `set_needs_review` | Transition the task to its **needs-review** state (the hand-off half)                         | Transition failure → `flag_for_human`; never force-complete                                |
| `flag_for_human`   | Task can't proceed/reconcile: keep it in flight, **raise its priority**, leave a reason       | Best-effort; if even the flag write fails, record it in `REPORT.md`                        |
| `comment_progress` | Post a progress breadcrumb visible to a human watching the source                             | Best-effort; a failed comment never blocks the loop                                        |
| `wip_limit`        | Return the in-flight cap for the source's scope (the loop honors it before claiming)          | Unknown → treat as the configured default; never claim past an unknown cap                 |

`set_needs_review` is the **success** hand-off; `flag_for_human` is the
**blocked** one. Both leave the task in flight (never `completed`/`canceled`) —
completion (reaching `done`) is out of the run's scope, verified later by the
source's own completion path (`/sweep-for-complete` for `linear`; repo-pr's
native merge-derived signal for a plan source), exactly as the run-state
reference requires.

> **Why `link_pr` and `set_needs_review` are separate verbs, not one.** The
> [write order](run-state.md#write-order) links the PR at the **pr-open**
> transition (status stays `started`) and only writes **needs-review** at the
> later **hand-off** transition, after co-review and iterate. A crash between
> them must reconcile to "PR linked, still `started`" (gap **G5**), not to
> hand-off. Fusing the two would make that state unreachable, so the adapter
> exposes them as two verbs the loop calls at two different moments.

## linear adapter

Source: a Linear project named in `dev_docs/tasks/.task-config.yml`
(`handler: linear`, `linear.projects`). Ready issues come from the project's
`unstarted` state; native blocker relations are the dependency graph; project
WIP caps are respected. Every verb delegates to a `commands/handlers/linear-*.md`
section — none are no-ops.

| Verb               | Delegates to                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_ready`       | `linear-claim.md` **Find candidates** (resolve `unstarted` states → filter by estimate/labels/assignee/blockers → rank), scoped by `linear-common.md` **Resolve configured projects**. Pass `--stack-in-set`: a blocker that is itself a surviving candidate becomes a stack edge for `dependency_graph`, while an issue waiting on any other open blocker — one outside the run's scope included — is dropped. On the MCP floor, first do **Pre-flight** step 6's reads 1–2 for every candidate (the relation reads can be shared with `dependency_graph`), then feed the whole set, with `blockedBy` attached, through `linear-rank.py --stack-in-set` |
| `dependency_graph` | Native Linear `blockedBy`/`blocks` relations (`get_issue includeRelations`), assembled as in `linear-reoptimize.md` **Load — build the graph**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `claim`            | `linear-claim.md` **Pre-flight: is work already in flight?** then **Claim the issue** (the token-comment lock); build on Linear's **verbatim** `branchName`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `link_pr`          | `linear-claim.md` **Move to review on PR open** — **only** its `links` attachment (append-only); do **not** apply the state half here                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `set_needs_review` | `linear-claim.md` **Move to review on PR open** — **only** its `In Review` state transition (the `needs_review` row of `linear-common.md` **Kanban mapping**)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `flag_for_human`   | `linear-claim.md` **Bail** label + comment mechanics, **diverged** — see below                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `comment_progress` | `save_comment` (the same primitive `linear-claim.md` uses for the claim and PR-link comments)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `wip_limit`        | `do-tasks.md` **Pre-claim WIP gate** over `linear-common.md`'s config (`wip_limit`, per-project overrides, `global_wip_limit`, the Unassigned bucket)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

**The WIP gate always binds an auto-pilot run.** `commands/handlers/attendedness.md`
lets a present human override a single claim at the limit, which would be wrong here —
an orchestrator run is unattended by definition and is exactly the throughput the cap
exists to bound. It is provably unreachable rather than merely unlikely: the
orchestrator job exports `$AUTO_PILOT_UNTIL` (`scripts/spawn-orchestrator.sh`), which
is a hard negative on that file's pre-check list, so the override is skipped
before it can be offered. Do not add an escape hatch here.

**`flag_for_human` diverges from `Bail` on purpose.** `linear-claim.md` **Bail**
reverts the issue to a `backlog` state and clears the assignee. A parked task
must instead stay **visible and in flight**, so this verb reuses only Bail's
_label + comment_ mechanics and diverges on the rest:

- **Keep** the issue in its `started` state (do **not** revert to backlog) — its
  run-state phase is `parked`, which the phases table pins to tracker `started`.
- Swap `auto-claimed` → `human-approval-requested`, exactly as Bail does.
- **Raise `priority` to High** so the parked task surfaces at the top of the
  board — a human should review it before the run's other output.
- Post the reason comment (what was tried, what tripped the park) via
  `save_comment`. Leave the assignee as the run's viewer so it's clear who
  touched it.

**MCP read-lag** is inherited, not re-solved: `claim` uses `linear-claim.md`'s
jittered election + live-window bound, and any adapter read that immediately
follows a write tolerates the same eventual consistency (re-poll rather than
trust a single lagging read).

## gh-issue adapter

Source: a GitHub **milestone**, or an explicit **label query**, in the repo
`gh-issue.repo` names (`handler: gh-issue`); the source syntax is `SKILL.md`'s.
Either one only **narrows** the handler's candidate query: a ready issue still
needs `status:2_ready` **and** `auto:eligible`, so a human can withhold an issue
from the run exactly as from `/do-tasks`. Native GitHub issue dependencies
(`blocked_by`) are the dependency graph. Every verb delegates to a
`commands/handlers/gh-issue-claim.md` section or a `commands/handlers/assets/`
script — none are no-ops.

| Verb               | Delegates to                                                                                                                                                                                                                                                                                                                  |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_ready`       | `gh-issue-claim.md` **Find candidates**, with the source ANDed into its `--search` (`milestone:"<title>"`, or one `label:"<name>"` term per queried label) — never replacing its `status:2_ready` + `auto:eligible` terms — then `gh-issue-ready.py --issue <n>:<est> … --max-estimate <bound>` over exactly those candidates |
| `dependency_graph` | `gh-issue-graph.py --repo <repo> --json`, scoped with `--milestone` or `--label` to match the source (the same reader `gh-issue-reoptimize.md` **Load — build the graph** uses)                                                                                                                                               |
| `claim`            | `gh-issue-claim.md` **Pre-flight: is work already in flight?** then **Claim the issue** (the ref lock — see below); build on `gh-issue-claim.py branch-name --issue <n> --prefix "<branch_prefix>"`, never a hand-built name                                                                                                  |
| `link_pr`          | `gh-issue-claim.md` **PR** — **only** its `PR opened: <url>` issue comment; the rung stays `status:3_started`. The PR body's `Closes #<n>` is the native link                                                                                                                                                                 |
| `set_needs_review` | `gh pr ready <pr>`, then `gh-issue-claim.md` **Move to review on PR open** (`gh-issue-state.py` → `status:4_needs_review`)                                                                                                                                                                                                    |
| `flag_for_human`   | `gh-issue-claim.md` **Bail (when execution proves infeasible mid-flight)** label + comment mechanics, **diverged** — see below                                                                                                                                                                                                |
| `comment_progress` | `gh issue comment <n> --repo <repo>`                                                                                                                                                                                                                                                                                          |
| `wip_limit`        | `gh-issue-claim.md` **Pre-claim WIP gate** (`gh-issue-claim.py wip`, which counts this operator's `status:3_started` + `status:4_needs_review`)                                                                                                                                                                               |

**Claim mode: the ref lock, not the comment election.** `/do-tasks`'s batch
dispatch puts its sessions on `claim-lock.md`'s comment-token election because
nothing releases a ref a crashed unattended session acquired (`commands/do-tasks.md`
§4 step 6). An auto-pilot run is different in the two ways that matter: one
serialized orchestrator claims, so there is no claim race to elect over, and a
crashed run that is resumed reconciles its own claim through `--resume`'s **G1**
row ([run-state.md](run-state.md#crash-reconciliation)) — keep the ref if the claim
is still ours, release it and park if not. What this does not cover is a crashed
run **nobody resumes**: its `<branch_prefix>task-<n>` ref stays on origin and every
later session skips the issue, until the crashed-claim sweep in #760 exists. Do not
launch unattended runs you do not intend to resume until it does.

**The PR is a draft until hand-off, and that is what keeps G5 and G6 true.**
`.github/workflows/gh-issue-pr-sync.yml` writes `status:4_needs_review` from a PR's
non-draft `opened` event and from `ready_for_review` (`gh-issue-claim.md` **Move to
review on PR open**). A PR opened ready would put the issue in needs-review before
co-review ran — the state the [write order](run-state.md#write-order) forbids. The
run opens the PR as a **draft** (`/deliver-task` on the gh-issue handler), so the
Action stays silent at pr-open and `link_pr` is the URL comment alone. At hand-off
`set_needs_review` marks the PR ready and writes the rung itself; the Action then
fires on `ready_for_review`, finds the issue already off `status:3_started`, and
writes nothing. So **G5** (PR open, still `started`) and **G6** (reviewed, still
`started` → set `needs_review`) reconcile exactly as written; for G6, "set
`needs_review`" is this verb, mark-ready included.

**The WIP gate always binds**, for the reason the linear adapter gives:
`$AUTO_PILOT_UNTIL` is a hard negative in `commands/handlers/attendedness.md`, so the
single-claim override is never offered. A **parked** issue still counts toward it —
it stays `status:3_started` — so enough parks stop further claims. That is
deliberate: a parked issue is in flight, waiting on a human.

**`flag_for_human` diverges from Bail on purpose.** **Bail** releases the lock ref,
moves the issue to `status:1_needs_refinement`, and clears the assignee. A parked
issue must stay visible and in flight instead:

- **Keep** `status:3_started`, the assignee, and the lock ref — the run-state phase
  is `parked`, which pins the tracker to `started`.
- Swap the `auto:` rung to `auto:human-review-needed`, as Bail does, and **set
  `prio:0`** so the issue tops the board. Both in one `gh-issue-state.py --apply`
  carrying the full managed set (`status:3_started,auto:human-review-needed,prio:0`
  plus its `est:` label), never `gh issue edit --add-label`.
- Post the reason comment (what was tried, what tripped the park) with
  `gh issue comment`.

**No stacking within the run.** linear's `list_ready` passes `--stack-in-set`, so a
candidate blocked only by another candidate becomes a stack edge. `gh-issue-ready.py`
has no such mode: it drops any issue with an open blocker, and a blocker stays open
until its PR merges — after the run has handed it off. So a gh-issue run works the
**unblocked frontier** of its source; a dependent waits for the next run.

## plan adapter

Source: a `dev_docs/tasks/<name>_plan/` directory (a `/plan-with-docs` output,
`handler: repo-pr`). This is the run's effective handler regardless of the
repo's own `.task-config.yml` default, and it's what the run loop passes to
`/deliver-task` via `--handler` (`SKILL.md` "The per-task step") so
`/deliver-task` never re-derives it from that file. The dependency graph is
the tasks' `is_blocked_by` frontmatter; status lives in each task file's
frontmatter (the vocabulary is `skills/task/SKILL.md` **Kanban columns**).
Plan tasks are born `status: new`, so `list_ready` runs promotion first — the
run only ever sees ready tasks.

| Verb               | Delegates to                                                                                                                                                                                                                     |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_ready`       | Plan files with `status: ready` and **every** `is_blocked_by` satisfied, per `repo-pr-execute.md` **multi-blocker readiness**; **promotion first** (`promote-tasks.md`) since tasks are born `new`                               |
| `dependency_graph` | The `is_blocked_by` frontmatter edges (a single slug or a list), read straight from the task files                                                                                                                               |
| `claim`            | `repo-pr-execute.md` **Claim protocol**'s pre-claim `gh` PR scan only (no reservation PR — see below); the `status: ready → in_progress` flip is committed on the **run-state branch** (see the note below), not the code branch |
| `link_pr`          | `repo-pr-execute.md` mechanics — open the code PR labeled `task-loop` directly (no draft `task-claim` PR to relabel — see below) and fill its body; the task file's `status` stays `in_progress`                                 |
| `set_needs_review` | Set the task file `status: needs_review` on the **run-state branch** (see the note below); the open `task-loop` PR **is** the review signal (`skills/task/SKILL.md` **Kanban columns**)                                          |
| `flag_for_human`   | Set the task file `status: needs_refinement` (the plan store's human-approval column) on the **run-state branch** (see the note below) + append the reason to the task file; leave the code branch/PR as-is                      |
| `comment_progress` | **no-op on the source** — a plan file has no comment log; the breadcrumb is routed to the run log / `REPORT.md` instead                                                                                                          |
| `wip_limit`        | `repo-pr-execute.md` **WIP cap** (`wip_limit` from `.task-config.yml`) when configured; otherwise **no-op** (no cap) for a bare plan directory                                                                                   |

**Task-file status transitions live on the run-state branch, not the code PR.**
`repo-pr-execute.md` was written for `/do-tasks`, where the task file lives on
`main` and the **code PR deletes it** as the merged-PR "done" signal
(`repo-pr-execute.md:277,322` — "the merged PR is the 'done' signal … the task
file has been deleted in this PR"). An auto-pilot plan source has a different
branch layout: the `/plan-with-docs` files are committed on a **working branch**,
the **run-state branch** (`auto-pilot/<run_id>`, [`run-state.md`](run-state.md)
**Run-state branch**) is cut from it, and each task's code branches from `main`
(where the package belongs). The code PR therefore **cannot** carry the task
file — so this adapter decouples the two:

- The task-file status flips (`ready → in_progress` at `claim`, `→ needs_review`
  at `set_needs_review`, or `→ needs_refinement` at `flag_for_human`) are
  committed on the **run-state branch**, alongside `RUN.md`/`QUESTIONS.md`, in the
  same [write order](run-state.md#write-order) — **never** inside the code PR's
  diff.
- The **code PR carries only code**; it never adds or deletes a
  `dev_docs/tasks/…` file. repo-pr's "task file deleted in this PR" done-signal
  does **not** apply here — the `needs_review` flip on the run-state branch is the
  hand-off signal, and the run's success terminal is `handed-off`
  (`needs_review`). Completion to `done` is out of the run's scope: the **merged
  code PR** is repo-pr's native done-signal, not `/sweep-for-complete` (which is
  `linear`-only in v1, per `skills/task/SKILL.md`).

This is why the `claim` / `set_needs_review` rows above delegate to
`repo-pr-execute.md` for the **status vocabulary** but **not** its on-`main`,
deleted-in-PR file model.

**No reservation PR.** `repo-pr-execute.md`'s **Claim protocol** normally opens
a draft `task-claim` PR as a first-writer-wins reservation against claim
races. A single-orchestrator plan-source run **skips that PR outright**: its
code branch bases on `main`, where the task file doesn't exist (it lives only
on the working/run-state branch) — there is nothing to seed a reservation
PR's diff with — and one serialized orchestrator has no claim race to guard
against in the first place. The **run-state branch** commit (the
`ready → in_progress` flip, above) is the lock instead. The adapter still runs
the pre-claim `gh` PR scan (`repo-pr-execute.md` **Claim protocol**) for
**resume idempotency** — adopting an existing PR by head branch rather than
ever opening a duplicate one, exactly as gap **G4** requires.

**Scaffolding cleanup is a run-level teardown, not a graph task.**
`/plan-with-docs`'s own convention bakes the `<name>_plan/` folder's
graduate-then-delete cleanup in as the plan's own final task
(`skills/plan-with-docs/SKILL.md` "A `<name>_plan/` folder…"), on the
assumption that the deleting code PR can see the folder on `main`. An
auto-pilot plan source breaks that assumption: the folder lives only on the
working/run-state branch and was **never** on `main`, so a `main`-based code
PR cannot delete it. This adapter therefore does not surface that cleanup as
a task `list_ready` can return — it is a **run-level teardown** on the
run-state/working branch, and ultimately a human follow-up once the run-state
branch's fate is decided.

## Explicit no-ops

Rather than leave any verb undefined per source, the ones that have no natural
sink are called out:

| Verb               | linear | gh-issue | plan                                                                  |
| ------------------ | ------ | -------- | --------------------------------------------------------------------- |
| `comment_progress` | active | active   | **no-op** — routed to `REPORT.md` (no per-task comment log in a plan) |
| `wip_limit`        | active | active   | **no-op** unless `.task-config.yml` sets `wip_limit` for the repo     |

Every other verb is active on every adapter. The loop calls the verb the same
way regardless; a no-op adapter simply satisfies the contract without a
source-side write.
