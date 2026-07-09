---
type: design
title: Auto-pilot mode — /auto-pilot + /deliver-task
status: draft
created: 2026-07-08
revised: 2026-07-08 # after gpt-5.5 high-effort design review (14 findings folded in)
supersedes-notes: autonomos-mode.md
---

# Auto-pilot mode design

"Claude, pick up this Project and grind autonomously on it overnight."

Grounded in the learnings in `autonomos-mode.md`. Two new skills:

- **`/deliver-task`** — a standalone primitive that takes ONE task through the full
  lifecycle: claim → do → PR → co-review → iterate → hand off. Useful
  interactively on its own; the auto-pilot orchestrator's per-task unit.
- **`/auto-pilot`** — a two-part skill: an interactive **launch phase** (pre-flight
  + spawn) and an unattended **run phase** (a thin orchestrator that walks the
  task graph calling `/deliver-task`).

## Design principle: compose, never duplicate

The existing skills and handler protocols are battle-tested. `/deliver-task` and
`/auto-pilot` are **compositions over them** — they invoke the existing verb or
handler section for every discrete step and add only orchestration. Where an
existing skill almost fits, we tweak that skill (e.g. add a non-interactive
mode); we do not fork its logic into auto-pilot-specific prose. Concretely:

| Step | Reuses |
|---|---|
| Claim | handler claim protocols (`linear-claim.md` token-comment election; `repo-pr-execute.md` claim-PR markers) — never a bespoke "mark started" |
| Implement | `select-coder` routing + `orchestrate-coders` dispatch rules (workers in their own worktrees) |
| Review | `/co-review` (with a non-interactive mode, see tweaks below) |
| Complete | **nothing in-run** — completion stays merge-verified via `/sweep-for-complete`; the execute path never completes an issue (`linear-claim.md` hard rule) |
| Plan source | `plan-with-docs` output via the repo-pr/file handler semantics |
| Pause/wake | `/loop` machinery |

Required tweaks to existing skills (small, PR-able independently):
- `co-review`: a `--non-interactive` mode — no config prompts, bounded reviewer
  timeouts, custom commands disabled unless pre-approved at launch.
- `select-coder`/`orchestrate-coders`: accept a resolved coder config instead of
  prompting when defaults are missing.

## Decisions (locked)

| Dimension | Decision |
|---|---|
| Work source | Unified: a Linear project **or** a plan-with-docs directory, via adapters |
| Topology | Long-lived orchestrator + fresh worker subagents per task |
| PR shape | Dependency-driven: independent tasks → PRs off main; dependent chains → stacked (with the freeze rule below) |
| Permissions | Sandboxed yolo: bypassPermissions inside the OS sandbox (worktree-confined writes, allowlisted network) |
| Pre-authorized | Push branches, open PRs, update Linear, spawn paid coder agents (bounded, see budget) |
| NOT authorized | Merging PRs, completing tracker issues — nothing lands or closes unattended |
| Budget | Rate-window aware + per-task bounds; hard-stop before paid overflow |
| Morning UX | `MORNING.md` report + evidence-carrying PR bodies, all in git |
| Packaging | Skills in this plugin; launch phase runs in the user's interactive session |

## State model (single source of truth)

Three stores exist; only one is authoritative.

- **The tracker is authoritative for task status** (Linear, or plan files for
  the plan source). Run files never disagree with it on purpose.
- **Run files are a cache + report**: `RUN.md` (task graph, per-task lifecycle
  *phase*), `QUESTIONS.md` (decision log), `MORNING.md` (rolling report). They
  live on a **dedicated run-state branch** in the worktree — never on task
  branches, so bookkeeping commits can't pollute task PRs.
- **Fixed write order per transition**: push code → update tracker → commit run
  state. A crash between steps therefore always leaves the tracker at-or-behind
  git, and run state at-or-behind both — reconciliation reads in that order.
- **Per-task phase field** in `RUN.md`: `claimed | implementing | pr-open |
  in-review | iterating | handed-off | parked`. Updated at every transition.

### Crash / resume

Not "automated recovery" — but resumable by design. `/auto-pilot --resume`
re-reads the run-state branch, reconciles each task's phase against reality
(does the branch exist? is the PR open? does the tracker show the claim?),
completes or rolls back the half-done transition using the same write order,
and continues. A crash costs at most one *phase* of one task, not the night.
Tasks that can't be reconciled cleanly are `parked` with a morning-report entry
rather than retried blindly.

## `/deliver-task` — the per-task lifecycle primitive

One task in, one reviewed PR out. Handler-dispatched like the other task
skills. Ends at **hand-off, never done**: the task's terminal in-run state is
`needs_review` (PR open, reviewed, evidence attached). Completion happens
later, merge-verified, via `/sweep-for-complete`.

1. **Claim** — via the handler's own claim protocol (Linear token-comment
   election with read-lag mitigation; repo-pr claim-PR markers for file
   sources). Branch base comes from the dependency graph: main, or the tip of
   the chain branch it depends on.
2. **Do** — `git fetch` + base-freshness check first (launch-time freshness is
   stale by 3am). Dispatch implementation to a worker chosen by `select-coder`;
   per `orchestrate-coders` rules the worker edits in **its own worktree**,
   never the orchestrator's; the orchestrator integrates the diff, owns the
   task branch, and runs verification: the project's named check command
   **and** exercising the feature itself, evidence captured.
3. **Open PR** — orchestrator opens it. Ready-for-review on repos the user
   owns; draft otherwise. PR body carries evidence and, for human-judgment
   items, exact how-to-evaluate steps. If the repo's review bots won't run on
   drafts, note it: bot review is skipped, not awaited (see step 4 fallback).
4. **Co-review** — `/co-review --non-interactive` with bounded timeouts per
   reviewer class. Bot reviews (Copilot) are polled with a hard timeout; on
   timeout or bots-can't-run (draft on non-owned repo), fall back to local
   reviewers only and record which classes ran.
5. **Iterate** — apply high-confidence findings; log judgment calls to
   `QUESTIONS.md`; re-verify; re-push. Bounded at 2 iterations, then record
   remaining findings and move on.
6. **Hand off** — tracker → `needs_review` state, PR linked (handler's own
   linking), phase → `handed-off`, morning-report entry. **Freeze rule**: once
   a task hands off, its PR is frozen for the rest of the run — late-arriving
   bot findings are logged for morning, never applied, so stacked children
   never go stale mid-run.

Definition of done for the *work* (verbatim from the notes doc): fewer tasks
genuinely finished beats all tasks superficially touched; you exercised the
feature itself, not just its tests.

### Stacked-PR rules

- A chain is processed in dependency order; child branches from the parent's
  frozen tip, so the base commit is stable by construction.
- In-run dependency unblocking keys off **phase = handed-off** (PR open on the
  chain branch) — never off tracker done-state, which no longer exists in-run.
- If a parent must be reopened (it can't, per the freeze rule — but if a human
  intervenes overnight), the orchestrator detects the moved base at the child's
  pre-flight fetch and parks the child instead of building on a stale base.

## Work-source adapters

Both normalize to the run-state representation. The adapter interface is
explicit — each adapter implements these verbs by delegating to its handler's
existing sections, and their behavioral differences (WIP caps, labels, MCP
read-lag, branch naming) live inside the adapter, not downstream:

`list_ready` · `dependency_graph` · `claim` · `link_pr` · `set_needs_review` ·
`park` · `comment_progress` · `wip_limit`

- **linear**: ready issues from the named project (`.task-config.yml` config);
  native blocker relations = dependency graph; respects project WIP caps.
- **plan**: a `dev_docs/tasks/<name>_plan/` directory (plan-with-docs output).
  Plan tasks are born `status: new`; launch pre-flight runs the promotion
  check (`promote-tasks` semantics) so the run only ever sees ready tasks.

## `/auto-pilot` — launch phase (interactive, tonight)

`/auto-pilot <linear-project | plan-dir> [--until <time>] [--resume]`

Pre-flight is automated and runs while the human is awake to fix failures.
Any hard failure **blocks launch**.

1. Create the dedicated worktree + run-state branch; confirm plan/instructions
   are **committed and present in it**.
2. Probe every auth non-interactively: `gh auth status`, Linear key via `op`,
   coder CLIs, any MCP the tasks touch. Interactive-only auth = launch blocker.
3. **Resolve every config decision the run could hit into non-interactive
   choices**: co-review reviewer set, coder defaults, select-coder config.
   Custom/local commands are disabled unless explicitly approved here.
4. Gitignore sanity check against the file types the tasks will produce.
5. Record the verify tooling (`dli check` / equivalent) and the end-to-end
   exercise path in `RUN.md`.
6. Normalize the work source (adapter), materialize the task graph and per-task
   phases into run state, commit.
7. Spawn the detached orchestrator under sandboxed yolo; report where state
   lives; user goes to bed.

## `/auto-pilot` — run phase (unattended orchestrator)

A thin loop; it never writes code itself.

```
while unblocked tasks remain and inside budget bounds:
    pick next unblocked task (phase-based readiness)
    /deliver-task it (with per-task wall-clock + retry bounds)
    update run state on the run-state branch
    check rate-window usage
```

- **Non-blocking decisions**: reversible calls pre-authorized; every decision
  is an indexed `QUESTIONS.md` entry and the run proceeds. Prefer the
  reversible option when uncertain.
- **Human checkpoints produce artifacts**: working end-to-end + how-to-evaluate
  in the PR body and `MORNING.md`, then proceed.
- **Budget bounds** (all explicit): rate-window check after each task, pause +
  scheduled wake-up past reset when near a cap, hard-stop before paid/overflow
  credits; **per-task wall-clock limit** (default ~45 min, then park);
  **per-task retry limit** (1 re-dispatch, then park); **paid-agent dispatch
  cap** per run. A runaway task parks itself; it cannot eat the night.

## Morning interface

- `MORNING.md`: per-task outcome (handed-off / parked / skipped + why),
  decision-log highlights, evidence links, the how-to-evaluate queue, which
  review classes ran per PR, spend summary.
- PRs: dependency-shaped, frozen, each carrying its own evidence. Nothing
  merged; nothing completed in the tracker.
- Linear (when source): states at `needs_review`/started, comments and PR
  links current — ready for `/sweep-for-complete` after human merges.

## Out of scope (v1)

- Merging or tracker-completing anything unattended.
- `--budget` dollar/token caps (window discipline + per-task bounds only).
- jira/gh-issue adapters (linear + plan only).
- Reopening frozen PRs in-run; multi-night continuation beyond `--resume`.

## Open implementation questions (for the plan)

- Spawn mechanics for the detached orchestrator: background `claude -p` vs a
  backgrounded Agent from the launch session.
- Exact sandbox profile: network egress allowlist contents, credential access
  model (op/gh/coder CLIs), and how worker-CLI network needs (codex, devin)
  compose with the orchestrator's sandbox.
- Concrete co-review timeout values per reviewer class.
