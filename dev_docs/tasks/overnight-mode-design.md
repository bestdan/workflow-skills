---
type: design
title: Overnight mode — /overnight + /run-task
status: draft
created: 2026-07-08
supersedes-notes: autonomos-mode.md
---

# Overnight mode design

"Claude, pick up this Project and grind autonomously on it overnight."

Grounded in the learnings in `autonomos-mode.md`. Two new skills:

- **`/run-task`** — a standalone primitive that takes ONE task through the full
  lifecycle: claim → do → PR → co-review → iterate → done. Useful interactively
  on its own; the overnight orchestrator's per-task unit.
- **`/overnight`** — a two-part skill: an interactive **launch phase** (pre-flight
  + spawn) and an unattended **run phase** (a thin orchestrator that walks the
  task graph calling `/run-task`).

## Decisions (locked)

| Dimension | Decision |
|---|---|
| Work source | Unified: a Linear project **or** a plan-with-docs directory, via adapters |
| Topology | Long-lived orchestrator + fresh worker subagents per task |
| PR shape | Dependency-driven: independent tasks → PRs off main; dependent chains → stacked |
| Permissions | Sandboxed yolo: bypassPermissions inside the OS sandbox (worktree-confined writes, allowlisted network) |
| Pre-authorized | Push branches, open PRs, update Linear, spawn paid coder agents |
| NOT authorized | Merging PRs — nothing lands in main unattended |
| Budget | Rate-window aware; pause + schedule wake-up past reset; hard-stop before paid overflow. No per-run cap in v1 |
| Morning UX | `MORNING.md` report + evidence-carrying PR bodies, all in git |
| Packaging | Skills in this plugin; launch phase runs in the user's interactive session |

## `/run-task` — the per-task lifecycle primitive

One task in, one reviewed PR out. Handler-dispatched like the other task skills
(works against a Linear issue or a plan-file task).

1. **Claim** — transition the task to started (Linear state / plan-file status);
   pick the branch base from the dependency graph (main, or the tip of the chain
   it depends on).
2. **Do** — dispatch the implementation to a worker via select-coder routing
   (cheap model for mechanical work, strong model for judgment); verify with the
   project's named check command **and** by exercising the feature itself
   (evidence captured: screenshots, output).
3. **Open PR** — ready-for-review on repos the user owns, draft otherwise. PR
   body carries the evidence and, for human-judgment items, exact
   how-to-evaluate steps.
4. **Co-review** — run `/co-review` on the PR. If a Copilot (or other bot)
   review is requested on GitHub, poll until it lands — bot-waiting is a cheap
   poll, not a deadlock.
5. **Iterate** — apply high-confidence findings; log judgment calls to
   `QUESTIONS.md`; re-verify; re-push. Bounded at 2 iterations, then record
   remaining findings in the report and move on.
6. **Done** — update tracker state, link the PR, append the morning-report
   entry.

Definition of done (verbatim from the notes doc): fewer tasks genuinely
finished beats all tasks superficially touched; you exercised the feature
itself, not just its tests.

## `/overnight` — launch phase (interactive, tonight)

`/overnight <linear-project | plan-dir> [--until <time>]`

Pre-flight is automated and runs while the human is still awake to fix
failures. Any hard failure **blocks launch**.

1. Create a dedicated worktree; confirm the plan/instructions are **committed
   and present in it** (worktrees check out tracked files only).
2. Probe every auth non-interactively: `gh auth status`, Linear key via `op`,
   any MCP the tasks touch. Interactive-only auth = launch blocker.
3. Gitignore sanity check against the file types the tasks will produce.
4. Record the verify tooling (`dli check` / project equivalent) and the
   end-to-end exercise path in the run file.
5. Normalize the work source into the run state (see adapters), generate the
   orchestrator's run instructions, commit both to the worktree branch.
6. Spawn the detached orchestrator session under sandboxed yolo; report where
   state lives; user goes to bed.

### Work-source adapters

Both normalize to one in-worktree representation — `RUN.md` (human-readable
status) backed by a task list with IDs, dependencies, definition-of-done, and
verify commands.

- **linear**: ready issues from the named project (reusing `.task-config.yml`
  handler config); native blocker relations = dependency graph; writes state,
  comments, and PR links back to Linear.
- **plan**: a `dev_docs/tasks/<name>_plan/` directory (plan-with-docs output);
  the plan's ordering = dependency graph; status written back to plan files.

Everything downstream of the adapter is source-agnostic.

## `/overnight` — run phase (unattended orchestrator)

A thin loop; it never writes code itself.

```
while tasks remain and inside budget window:
    pick next unblocked task from the graph
    call /run-task on it
    update RUN.md + MORNING.md, commit state
    check rate-window usage
```

- **Non-blocking decisions**: pre-authorized to make reversible calls; every
  decision is an indexed `QUESTIONS.md` entry (question, options, call, why,
  reversibility) and the run **proceeds** — never waits on a human. Prefer the
  reversible option when uncertain.
- **Human checkpoints produce artifacts**: get the feature working end-to-end,
  write how-to-evaluate into the PR body and `MORNING.md`, proceed.
- **Durable state beats conversation memory**: `RUN.md`, `QUESTIONS.md`, and
  `MORNING.md` update after every unit and are committed — a compaction or
  crash loses at most one task, and a dead orchestrator still leaves a
  complete record.
- **Budget/liveness**: after each task, check usage; near a rate-window cap →
  write state, schedule a wake-up past the reset, pause. Hard-stop before
  paid/overflow credits — pausing always beats spending.

## Morning interface

- `MORNING.md`: per-task outcome (done / blocked / skipped + why), decision-log
  highlights, evidence links, the how-to-evaluate queue, spend summary.
- PRs: dependency-shaped (independent off main, chains stacked), each carrying
  its own evidence. Nothing merged.
- Linear (when that's the source): states, comments, and PR links current.

## Relationship to existing skills

Composer, not replacement: linear adapter ≈ scoped `do-tasks` semantics;
worker dispatch = `select-coder`/`orchestrate-coders`; plan source =
`plan-with-docs` output; PR review = `/co-review`; pause/wake = `/loop`
machinery. `/schedule` remains the recurring-cloud case; `/overnight` is the
single-night, local-worktree case. `/do-tasks` is untouched in v1; it may later
delegate its per-task body to `/run-task`.

## Out of scope (v1)

- Merging anything unattended.
- `--budget` dollar/token caps (window discipline only).
- jira/gh-issue adapters for the run state (linear + plan only).
- Automated recovery of a crashed orchestrator (state makes manual resume easy).

## Open implementation questions (for the plan)

- Spawn mechanics for the detached orchestrator: background `claude -p` vs a
  backgrounded Agent from the launch session.
- Exact sandbox profile (network allowlist contents) shipped with the skill.
- How `/run-task` bounds Copilot polling (timeout → proceed with local
  reviewers only).
