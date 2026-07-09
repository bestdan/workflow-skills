# Auto-pilot run state — reference

The canonical formats and invariants for an auto-pilot run's **durable state**.
The launch, run, and resume flows all read and write these; this file is the
single definition so none of them restate a format. Formats and invariants
only — no orchestration prose.

Design source: `dev_docs/tasks/auto-pilot-mode-design.md` §"State model".

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
| `.auto-pilot/MORNING.md`   | The rolling human-facing report the user wakes to.                                                                                                  |

### `RUN.md`

Front matter records the run's fixed parameters; a table records per-task state.

```markdown
---
run_id: auto-pilot/2026-07-08-auto-pilot-mode # matches the run-state branch
work_source: linear:d0598803-… # linear:<projectId> or plan:<dir>
base_branch: main
verify_command: dli check # the named check (design pre-flight §5)
exercise_path: "drive /co-review --non-interactive on a scratch PR" # end-to-end check
---

| task    | phase        | branch            | base            | pr   | notes                  |
| ------- | ------------ | ----------------- | --------------- | ---- | ---------------------- |
| PRE-459 | handed-off   | bestdan/pre-459-… | main            | #140 | 2 co-review rounds     |
| PRE-460 | implementing | bestdan/pre-460-… | bestdan/pre-459 | —    | chained on PRE-459 tip |
```

- `base` encodes the dependency edge for a stacked task: `main` for an
  independent task, or the parent task's branch for a chained one.
- `phase` is one of the seven values below; it is the field `--resume` reconciles.

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

### `MORNING.md`

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

## Run-state branch

The run files live on a **dedicated branch**, distinct from every task branch:

- **Name:** `auto-pilot/<run_id>` — e.g. `auto-pilot/2026-07-08-auto-pilot-mode`.
  It is created at launch and never merged into `main`.
- **Why separate:** bookkeeping commits (`RUN.md`/`QUESTIONS.md`/`MORNING.md`
  updates after every unit) must never land on a task branch, or every task PR
  would carry unrelated run-state churn. Keeping them on their own branch means
  task PRs contain only their code, and a dead orchestrator still leaves a
  complete, committed record to resume from.

## Task lifecycle phases

Seven phases. Each names exactly what exists on the tracker, in git, and on
disk while a task sits in it — which is what makes the reconciliation table
below decidable.

| Phase          | Meaning                                                                 | Tracker                    | Git / remote                              | Worker worktree |
| -------------- | ----------------------------------------------------------------------- | -------------------------- | ----------------------------------------- | --------------- |
| `claimed`      | Claim held via the handler protocol; base chosen; no code yet           | claimed / started          | task branch may exist locally, no commits | none            |
| `implementing` | Worker dispatched; diff being integrated + verified                     | started                    | local commits possible, **not pushed**    | may exist       |
| `pr-open`      | Code pushed and PR opened (draft or ready)                              | started                    | branch pushed, PR open                    | removed         |
| `in-review`    | `/co-review --non-interactive` running                                  | started                    | PR open                                   | none            |
| `iterating`    | Applying review fixes, re-pushing (≤ 2 rounds)                          | started                    | PR updated                                | none            |
| `handed-off`   | **Terminal (success):** PR linked, tracker at `needs_review`, PR frozen | needs_review               | PR open, linked                           | none            |
| `parked`       | **Terminal (blocked):** couldn't proceed or reconcile; needs a human    | started (+ reason comment) | whatever existed at the stall             | removed         |

`handed-off` is the success terminal — completion itself is merge-verified later
by `/sweep-for-complete`, never by the run. In-run dependency readiness keys off
`handed-off`, not tracker completion.

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

## Crash reconciliation

One row per write-order gap (a crash landing between two steps of the order),
plus the two pre-push edges. `--resume` matches the on-disk reality to a row and
applies its action; anything that doesn't match cleanly is set to `parked` with
a `MORNING.md` entry rather than blindly retried.

| #  | Crash point                                            | Observed reality                                               | Reconcile action                                                                                   |
| -- | ------------------------------------------------------ | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| G1 | Mid-`implementing`, worker worktree left behind        | orphan worker worktree; no pushed branch                       | remove the orphan worktree; re-dispatch from a clean base, or `parked`                             |
| G2 | After local commit, **before push**                    | local commits ahead of remote; tracker still started; no PR    | push the branch, then continue at the PR-open step                                                 |
| G3 | After **push**, before PR opened                       | remote branch exists; **no PR**; tracker not linked            | idempotency-check for an existing PR by head branch; open it if absent; then update tracker        |
| G4 | After **PR opened**, before **tracker updated**        | PR open; tracker still `started`, no PR link                   | link the PR + set `needs_review`; then commit run state                                            |
| G5 | After **tracker updated**, before **run-state commit** | tracker current (`needs_review`, linked); `RUN.md` phase stale | recompute the phase from tracker + git; commit run state                                           |
| G6 | After **claim**, before any implementation             | tracker claimed; no branch, no PR                              | verify the claim is still ours; resume `implementing`, or release + `parked` if the claim was lost |

G3's idempotency check is what makes resume safe to run repeatedly: a re-push or
a second launch never opens a duplicate PR, because an existing PR for the head
branch is detected and adopted.
