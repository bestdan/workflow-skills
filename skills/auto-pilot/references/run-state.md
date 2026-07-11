# Auto-pilot run state — reference

The canonical formats and invariants for an auto-pilot run's **durable state**.
The launch, run, and resume flows all read and write these; this file is the
single definition so none of them restate a format. Formats and invariants
only — no orchestration prose.

## Authority

Three stores exist; only one is authoritative for a given fact.

- **The tracker is authoritative for task status** (Linear issue state, or a
  plan file's `status`). The run files never contradict it on purpose.
- **Git is authoritative for code and PR existence** (the pushed branch and the
  open PR are the ground truth that a task's work happened).
- **The run files are a cache + report** — a fast local read of the graph and a
  human-facing morning summary. They are always allowed to be _behind_ the
  tracker and git, never ahead (see **Write order**).

## The three files

All run files live under `.auto-pilot/` in the worktree and are committed to
the **run-state branch** (below) — never to a task branch. Paths:

| File                       | Role                                                                                                                                                |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.auto-pilot/RUN.md`       | The task graph + each task's current lifecycle **phase** and the run's verify tooling. The machine-readable state the run loop and `--resume` read. |
| `.auto-pilot/QUESTIONS.md` | The decision log — one indexed entry per reversible call the run made without a human.                                                              |
| `.auto-pilot/REPORT.md`    | The rolling human-facing report the user wakes to.                                                                                                  |

### `RUN.md`

Front matter records the run's fixed parameters; a table records per-task state.

```markdown
---
run_id: 2026-07-08-auto-pilot-mode # bare id; the run-state branch is auto-pilot/<run_id>
work_source: linear:d0598803-… # linear:<projectId> or plan:<dir>
base_branch: main
verify_command: dli check # the named check (design pre-flight §5); the PINNED command the un-jailed verify broker runs (launch-runtime.md §5) — resolved once at launch, never agent-composed
exercise_path: "drive /co-review --non-interactive on a scratch PR" # end-to-end check
status: active # run-level: active | paused | systemic | done
paused_until: # ISO time the orchestrator may resume past a rate-window pause; empty unless status is paused
pause_reason: # why the run paused/halted; set with status=paused (rate window) or status=systemic (circuit breaker)
orchestrator_pid: 48213 # the spawned orchestrator's PID (launch step 7)
orchestrator_started_at: "Wed Jul  9 20:00:00 2026" # its process start-time — guards a recycled PID (launch-runtime "Orphan / stale detection")
until: 2026-07-10T06:00:00 # the run's --until deadline
min_task_budget: 20m # pre-dispatch floor, computed from the resolved reviewer set (launch step 3; run-budget.md); only written for a time-boxed (--until) run
---

| task    | phase        | branch            | base              | base_sha | pr   | notes                  |
| ------- | ------------ | ----------------- | ----------------- | -------- | ---- | ---------------------- |
| PRE-459 | handed-off   | bestdan/pre-459-… | main              | —        | #140 | 2 co-review rounds     |
| PRE-460 | implementing | bestdan/pre-460-… | bestdan/pre-459-… | a1b2c3d  | —    | chained on PRE-459 tip |
```

- `base` encodes the dependency edge for a stacked task: `main` for an
  independent task, or the parent task's branch for a chained one.
- `base_sha` is the parent branch's **frozen-tip SHA**, recorded when the parent
  reaches `handed-off`. A chained child's stacked-PR check compares the parent
  branch's _current_ tip against this recorded SHA to detect a moved base (→ park);
  empty (`—`) for an independent task, whose `base` is `main`. This guard only
  catches the **orchestrator** moving a base mid-run — it says nothing about a
  **human** merging a stacked PR out of order while the run is live. A child
  PR's diff is only correct relative to its parent's branch, so a human merge
  out of dependency order corrupts the stack or produces a confusing diff; the
  run's summary should tell the human to **merge bottom-up, in dependency
  order** (each chain's root PR first, then its children in order).
- `status` / `paused_until` / `pause_reason` are the **run-level** fields the run
  loop writes: `paused_until` (+ reason) at a rate-window pause, `status: systemic`
  (+ reason) when the circuit breaker halts, `status: paused` (+ reason,
  `paused_until` empty) when the pre-dispatch deadline guard stops with ready
  tasks left — resumable only by an explicit `--resume`, never a timer — and
  `status: done` at a clean end-of-run (no ready tasks or a budget hard-stop).
- `orchestrator_pid` / `orchestrator_started_at` / `until` are the operational
  record launch writes at spawn (launch-runtime.md "Orphan / stale detection");
  `--resume` and a fresh launch read them to detect a stale orchestrator.
- `min_task_budget` is the **pre-dispatch floor** the run loop's deadline guard
  reads: `now + min_task_budget > until` → stop before starting a task the
  `--until` kill would sever mid-delivery. Present only when `until` is set — a
  run with no `--until` has no deadline to guard. Launch step 3 computes it from
  the resolved co-review reviewer set (it is reviewer-latency-coupled, not a
  constant) and writes it here; formula and defaults live in
  [`run-budget.md`](run-budget.md) "Minimum task budget".
- `phase` is one of the seven in-flight/terminal values below, or the pre-claim
  `pending` marker (see "Task lifecycle phases"); of those, only the seven
  in-flight/terminal values are what `--resume` reconciles (a `pending` task has
  no in-flight transaction to reconcile).

### `QUESTIONS.md`

Append-only, indexed. One entry per reversible decision the run made on its own
(the non-blocking decision log). Shape:

```markdown
## Q3 — PRE-460 — which config key name for the reviewer timeout

- **Options:** `reviewer_timeout_s` | `timeouts.reviewer`
- **Call:** `reviewer_timeout_s` (flat key, matches existing config style)
- **Why:** consistency with `wip_limit`; no nesting elsewhere in the config
- **Reversible:** yes — rename before merge if wrong
```

Every entry carries: the question, the options considered, the call made, the
reasoning, and the reversibility. Prefer the reversible option when uncertain.
A deferred co-review finding that was also filed as a tracked follow-up (see
`REPORT.md` "Follow-ups" below) references the created task id in its entry.

### `REPORT.md`

The report, rewritten after every unit of work. Sections:

1. **Outcomes** — per task: `handed-off` / `parked` / `skipped` + one line why.
2. **Decisions** — the highlights from `QUESTIONS.md` worth a human's eye.
3. **Evidence** — links to the check output, screenshots, and exercised-feature
   artifacts each PR carries.
4. **How-to-evaluate queue** — for each human-judgment checkpoint: exactly how
   to evaluate it and what a "no" would invalidate.
5. **Review classes per PR** — which reviewer classes ran / timed-out / skipped
   for each PR (the summary `/co-review --non-interactive` emits).
6. **Spend** — usage against the rate window and any per-task bounds hit.
7. **Follow-ups** — the index of every co-review finding filed as a tracked
   task this run: task id, source PR, and the one-line finding. A finding is
   auto-filed via `/add-task` (tagged `auto-pilot`) when it is deferred **and**
   either **cross-cutting** (its faithful fix would touch a file outside the
   task's `related_files`, or change a spec/section another consumer cites) or
   **still open at the hard 2-round co-review bound** — this is the index of
   what got filed, not an alternative to filing it: every entry here was both
   listed and delivered to the tracker. Guardrails against unattended spam:
   dedupe within the run (one follow-up per underlying finding/spec, not one
   per occurrence), and cap auto-filed follow-ups at **5 per run** — excess
   findings are recorded here as a plain bullet (task id `none`) instead of
   filed. Auto-filed tasks land in the tracker's triage/new state, never
   `ready`: `/promote-tasks`' human-confidence gate still has to clear them
   before they enter work, so a hallucinated finding is caught before it costs
   anything. If `/add-task` itself fails, the run does not fail — the finding
   is recorded here as a plain bullet (task id `none`) instead.

## Run-state branch

The run files live on a **dedicated branch**, distinct from every task branch:

- **Name:** `auto-pilot/<run_id>` — e.g. `auto-pilot/2026-07-08-auto-pilot-mode`.
  It is created at launch and never merged into `main`.
- **Why separate:** bookkeeping commits (`RUN.md`/`QUESTIONS.md`/`REPORT.md`
  updates after every unit) must never land on a task branch, or every task PR
  would carry unrelated run-state churn. Keeping them on their own branch means
  task PRs contain only their code, and a dead orchestrator still leaves a
  complete, committed record to resume from.

## Task lifecycle phases

`phase` spans all materialized tasks, but only the seven **in-flight/terminal**
values below — from the moment a task is claimed to its terminal state —
participate in crash reconciliation. A task materialized into the graph but not
yet claimed carries the pre-claim marker `pending` instead; it is not one of
those seven and is never a target of the crash-reconciliation table (there is
nothing mid-transaction to reconcile before a claim exists). Which `pending`
tasks are **eligible to claim next** — graph readiness — is not encoded in a
`pending` task's own `phase`; it is computed from the graph edges and the
blockers' phases by the adapter's `list_ready`/`dependency_graph` verbs
([`adapters.md`](adapters.md)).

| Phase     | Meaning                                                                     | Tracker                                                      | Git / remote | Worker worktree |
| --------- | --------------------------------------------------------------------------- | ------------------------------------------------------------ | ------------ | --------------- |
| `pending` | Materialized into the graph, not yet claimed; readiness computed separately | new / materialized (plan `new`\|`ready`; linear `unstarted`) | no branch    | none            |

Seven in-flight/terminal phases follow, once a task is claimed. Each names
exactly what exists on the tracker, in git, and on disk while a task sits in
it — which is what makes the reconciliation table below decidable.

| Phase          | Meaning                                                                 | Tracker                    | Git / remote                              | Worker worktree |
| -------------- | ----------------------------------------------------------------------- | -------------------------- | ----------------------------------------- | --------------- |
| `claimed`      | Claim held via the handler protocol; base chosen; no code yet           | claimed / started          | task branch may exist locally, no commits | none            |
| `implementing` | Worker dispatched; diff being integrated + verified                     | started                    | local commits possible, **not pushed**    | may exist       |
| `pr-open`      | Code pushed and PR opened (draft or ready)                              | started                    | branch pushed, PR open                    | removed         |
| `in-review`    | `/co-review --non-interactive` running                                  | started                    | PR open                                   | none            |
| `iterating`    | Applying review fixes, re-pushing (≤ 2 rounds)                          | started                    | PR updated                                | may exist       |
| `handed-off`   | **Terminal (success):** PR linked, tracker at `needs_review`, PR frozen | needs_review               | PR open, linked                           | none            |
| `parked`       | **Terminal (blocked):** couldn't proceed or reconcile; needs a human    | started (+ reason comment) | whatever existed at the stall             | removed         |

`handed-off` is the success terminal — completion itself is verified later by
the source's own completion path (`/sweep-for-complete` for a `linear` source;
repo-pr's native merge-derived signal — the merged code PR — for a plan source),
never by the run. In-run dependency readiness keys off
`handed-off`, not tracker completion. The tracker state `needs_review` here means
**co-review is already complete** (it ran during `/deliver-task`); the remaining
gate is a **human** reviewer/merger — the automated review is not what
`needs_review` is waiting on.

## Write order

Every phase advance that touches more than one store writes in this fixed order:

> **push code → update tracker → commit run state**

This ordering is the whole basis for crash recovery. Because it never varies,
after any crash the freshness relation always holds:

> **remote ≥ tracker ≥ run files**

Git is at-or-ahead of the tracker; the tracker is at-or-ahead of the run files.
So `--resume` reconciles by reading in that same order — trust git first, then
the tracker, then rewrite the run files to match — and never has to guess which
store "won."

The **"update tracker" step is phase-specific**, which is what makes a crash's
reconcile action depend on _which_ transition it interrupted:

- at the **pr-open** transition it **links the PR** and leaves the status
  `started` (review hasn't happened yet);
- only at the **hand-off** transition — after co-review and iterate — does it set
  `needs_review`.

So `needs_review` is never written before review; a crash at pr-open reconciles
to `started` + a linked PR, not to hand-off.

## Crash reconciliation

One row per write-order gap (a crash landing between two steps of the order).
Because the tracker write is phase-specific (above), the rows are ordered by
which transition the crash interrupted — the pr-open tracker write (G5) links
the PR and stays `started`, while `needs_review` is written only at the hand-off
tracker write (G6). `--resume` matches the on-disk reality to a row and applies
its action; anything that doesn't match cleanly is set to `parked` with a
`REPORT.md` entry rather than blindly retried.

| #  | Crash point                                                         | Observed reality                                               | Reconcile action                                                                                                  |
| -- | ------------------------------------------------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| G1 | After **claim**, before any implementation                          | tracker claimed; no branch, no PR                              | verify the claim is still ours; resume `implementing`, or release + `parked` if the claim was lost                |
| G2 | Mid-`implementing`/`iterating`, worker worktree left behind         | orphan worker worktree; commits maybe unpushed                 | remove the orphan worktree; re-verify; re-dispatch from a clean base, or `parked`                                 |
| G3 | After local commit, **before push**                                 | local commits ahead of remote; tracker `started`; no/stale PR  | push the branch, then continue to the open/refresh-PR step                                                        |
| G4 | After **push**, before PR opened/refreshed                          | remote branch exists; **no (or stale) PR**; tracker not linked | idempotency-check for an existing PR by head branch; open/refresh it if absent; then do the pr-open tracker write |
| G5 | pr-open transition: after PR opened, **before tracker link**        | PR open; tracker still `started`, PR not linked                | **link the PR; keep the status `started`** (review hasn't run); phase → `pr-open`; commit run state               |
| G6 | hand-off transition: after review+iterate, **before tracker write** | PR open and reviewed; tracker still `started`                  | set `needs_review` + ensure the PR is linked; phase → `handed-off`; commit run state                              |
| G7 | After **any tracker write**, before **run-state commit**            | tracker current (any status); `RUN.md` phase stale             | recompute the phase from tracker + git; commit run state                                                          |

G4's idempotency check is what makes resume safe to run repeatedly: a re-push or
a second launch never opens a duplicate PR, because an existing PR for the head
branch is detected and adopted.
