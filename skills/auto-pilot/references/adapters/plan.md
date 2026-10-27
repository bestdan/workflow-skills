# Auto-pilot plan adapter

The rules shared by every adapter, the eight verbs' contracts, and the WIP gate
are in the [adapter interface](../adapters.md). This file holds only what is
specific to a plan-with-docs source.

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
the **run-state branch** (`auto-pilot/<run_id>`, [`run-state.md`](../run-state.md)
**Run-state branch**) is cut from it, and each task's code branches from `main`
(where the package belongs). The code PR therefore **cannot** carry the task
file — so this adapter decouples the two:

- The task-file status flips (`ready → in_progress` at `claim`, `→ needs_review`
  at `set_needs_review`, or `→ needs_refinement` at `flag_for_human`) are
  committed on the **run-state branch**, alongside `RUN.md`/`QUESTIONS.md`, in the
  same [write order](../run-state.md#write-order) — **never** inside the code PR's
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

**`flag_for_human` has no rung or priority to move.** A plan file carries
neither, so the [shared park contract](../adapters.md#flag_for_human-the-shared-park-contract)
is met by the `needs_refinement` column (the plan store's human-approval column)
and the reason appended to the task file.

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
