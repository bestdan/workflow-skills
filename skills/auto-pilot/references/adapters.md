# Auto-pilot work-source adapters — reference

`/auto-pilot` runs against either a **Linear project** or a **plan-with-docs
directory**. Both normalize to the same [run-state representation](run-state.md);
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

| Verb               | Contract                                                                                      | Failure behavior                                                             |
| ------------------ | --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `list_ready`       | Return the source's **ready, unblocked** tasks, ranked — the loop's candidate queue           | Empty list on a clean "nothing ready"; raise only on a source read failure   |
| `dependency_graph` | Return the blocker edges among in-scope tasks (used to pick each task's base + stack chains)  | Missing/partial edges → treat the task as independent (base `main`), note it |
| `claim`            | Reserve **one** task first-writer-wins; return the claim handle + the branch name to build on | Lost race / already in flight → return "not claimed" so the loop moves on    |
| `link_pr`          | Attach the opened PR to the task; **leave status unchanged** (the pr-open half of hand-off)   | Attach failure → `flag_for_human` (a built-but-unlinked PR needs a human)    |
| `set_needs_review` | Transition the task to its **needs-review** state (the hand-off half)                         | Transition failure → `flag_for_human`; never force-complete                  |
| `flag_for_human`   | Task can't proceed/reconcile: keep it in flight, **raise its priority**, leave a reason       | Best-effort; if even the flag write fails, record it in `REPORT.md`          |
| `comment_progress` | Post a progress breadcrumb visible to a human watching the source                             | Best-effort; a failed comment never blocks the loop                          |
| `wip_limit`        | Return the in-flight cap for the source's scope (the loop honors it before claiming)          | Unknown → treat as the configured default; never claim past an unknown cap   |

`set_needs_review` is the **success** hand-off; `flag_for_human` is the
**blocked** one. Both leave the task in flight (never `completed`/`canceled`) —
completion stays merge-verified by `/sweep-for-complete`, exactly as the
run-state reference requires.

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

| Verb               | Delegates to                                                                                                                                                                 |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_ready`       | `linear-claim.md` **Find candidates** (resolve `unstarted` states → filter by estimate/labels/assignee → rank), scoped by `linear-common.md` **Resolve configured projects** |
| `dependency_graph` | Native Linear `blockedBy`/`blocks` relations (`get_issue includeRelations`), assembled as in `linear-reoptimize.md` **Load — build the graph**                               |
| `claim`            | `linear-claim.md` **Pre-flight: is work already in flight?** then **Claim the issue** (the token-comment lock); build on Linear's **verbatim** `branchName`                  |
| `link_pr`          | `linear-claim.md` **Move to review on PR open** — **only** its `links` attachment (append-only); do **not** apply the state half here                                        |
| `set_needs_review` | `linear-claim.md` **Move to review on PR open** — **only** its `In Review` state transition (the `needs_review` row of `linear-common.md` **Kanban mapping**)                |
| `flag_for_human`   | `linear-claim.md` **Bail** label + comment mechanics, **diverged** — see below                                                                                               |
| `comment_progress` | `save_comment` (the same primitive `linear-claim.md` uses for the claim and PR-link comments)                                                                                |
| `wip_limit`        | `do-tasks.md` **Pre-claim WIP gate** over `linear-common.md`'s config (`wip_limit`, per-project overrides, `global_wip_limit`, the Unassigned bucket)                        |

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

| Verb               | Delegates to                                                                                                                                                                                       |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_ready`       | Plan files with `status: ready` and **every** `is_blocked_by` satisfied, per `repo-pr-execute.md` **multi-blocker readiness**; **promotion first** (`promote-tasks.md`) since tasks are born `new` |
| `dependency_graph` | The `is_blocked_by` frontmatter edges (a single slug or a list), read straight from the task files                                                                                                 |
| `claim`            | `repo-pr-execute.md` **Claim protocol** (flip `status: ready → in_progress`, open the draft `task-claim` PR); build on the deterministic `task/<slug>` branch                                      |
| `link_pr`          | `repo-pr-execute.md` — relabel the reservation `task-claim` PR → `task-loop` and fill its body; the task file's `status` stays `in_progress`                                                       |
| `set_needs_review` | Set the task file `status: needs_review`; the open `task-loop` PR **is** the review signal (`skills/task/SKILL.md` **Kanban columns**)                                                             |
| `flag_for_human`   | Set the task file `status: needs_refinement` (the plan store's human-approval column) + append the reason to the task file; leave branch/PR as-is                                                  |
| `comment_progress` | **no-op on the source** — a plan file has no comment log; the breadcrumb is routed to the run log / `REPORT.md` instead                                                                            |
| `wip_limit`        | `repo-pr-execute.md` **WIP cap** (`wip_limit` from `.task-config.yml`) when configured; otherwise **no-op** (no cap) for a bare plan directory                                                     |

## Explicit no-ops

Rather than leave any verb undefined per source, the ones that have no natural
sink are called out:

| Verb               | linear | plan                                                                  |
| ------------------ | ------ | --------------------------------------------------------------------- |
| `comment_progress` | active | **no-op** — routed to `REPORT.md` (no per-task comment log in a plan) |
| `wip_limit`        | active | **no-op** unless `.task-config.yml` sets `wip_limit` for the repo     |

Every other verb is active on both adapters. The loop calls the verb the same
way regardless; a no-op adapter simply satisfies the contract without a
source-side write.
