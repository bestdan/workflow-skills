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

This file is the interface and the rules every adapter shares. A run uses one
adapter; read this file plus that adapter's file for the full contract:

| Source                        | Effective handler | Adapter                                        |
| ----------------------------- | ----------------- | ---------------------------------------------- |
| Linear project                | `linear`          | [`adapters/linear.md`](adapters/linear.md)     |
| GitHub milestone or label     | `gh-issue`        | [`adapters/gh-issue.md`](adapters/gh-issue.md) |
| `dev_docs/tasks/<name>_plan/` | `repo-pr`         | [`adapters/plan.md`](adapters/plan.md)         |

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

## Rules every adapter shares

**The WIP gate always binds an auto-pilot run.** `commands/handlers/attendedness.md`
lets a present human override a single claim at the limit, which would be wrong here —
an orchestrator run is unattended by definition and is exactly the throughput the cap
exists to bound. It is provably unreachable rather than merely unlikely: the
orchestrator job exports `$AUTO_PILOT_UNTIL` (`scripts/spawn-orchestrator.sh`), which
is a hard negative on that file's pre-check list, so the override is skipped
before it can be offered. Do not add an escape hatch here.

### `flag_for_human`: the shared park contract

Each tracker handler has a **Bail** that releases the task back to the backlog —
reverting its state and clearing the assignee. A parked task must instead stay
**visible and in flight**, so every adapter's `flag_for_human` reuses only its
handler's Bail _label + comment_ mechanics and diverges on the rest:

- **Keep** the task in its started state — its run-state phase is `parked`,
  which the phases table pins to tracker `started`.
- Swap the "automation may take this" marker for the "a human must look" one,
  exactly as Bail does.
- **Raise its priority** so the parked task surfaces at the top of the board — a
  human should review it before the run's other output.
- Post the reason (what was tried, what tripped the park) where a human watching
  the source will see it.

Each adapter file names the exact labels, priority value, and write it uses.

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
