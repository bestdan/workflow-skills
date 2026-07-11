# Auto-pilot resume — `--resume` reconciliation

How `/auto-pilot <source> --resume` (`commands/auto-pilot.md`) reconciles a
crashed or paused run's durable state against reality before falling into the
normal **Run phase** loop ([`../SKILL.md`](../SKILL.md) "Run phase"). Procedure
and invariants only — the run-state formats and the crash-reconciliation table
this leans on live in [`run-state.md`](run-state.md); pause semantics live in
[`run-budget.md`](run-budget.md).

Resume's job is to reconcile a crashed or paused run's durable state against
reality, then fall into the normal **Run phase** loop for whatever remains ready.

**HEAD guard, first — before anything else reads the worktree.** Run
`scripts/spawn-orchestrator.sh assert-run-head --dir <run-worktree> --run-id
<run_id> --questions .auto-pilot/QUESTIONS.md`
([`run-state.md`](run-state.md) "Run worktree HEAD invariant"). A run that
crashed mid-task can leave the run worktree's `HEAD` parked on a task branch
(finding #23); this restores it to `auto-pilot/<run_id>` and records the
deviation before resume trusts anything else it reads off that worktree.

**Read `RUN.md` from the branch, not the working tree.** Even with the guard
above, resume's every read of `RUN.md` uses `git show
auto-pilot/<run_id>:.auto-pilot/RUN.md` rather than the worktree's
`.auto-pilot/RUN.md` file — the belt to the guard's braces. A mis-parked
`HEAD` (or a not-yet-restored one) then can't feed resume a stale or absent
`RUN.md` in the first place, whatever state the working tree happens to be in.

**Re-run only the pre-flight that can rot; skip the launch-only steps.**
Worktree + run-state-branch creation (Launch step 1) and the **task-graph
materialization** half of Launch step 6 already exist on the run-state branch
from the original launch, so resume does not re-create them; **source
normalization still runs** — resume must normalize `<source>` to resolve which
run-state branch it reads from, even though it never re-creates that branch. What
can rot between launch and resume, and so is re-run: the non-interactive **auth
probes** and the **environment fingerprint** (Launch step 2) — a run launched
`local-full` may resume under `claude-web`, or vice versa, so step 6's _other_
half, the scout's **capability join**, must re-run against the current
environment — and **base freshness**. As at launch, a hard failure here
**BLOCKS THE RESUME**, fail-closed the same way.

**Locate the run-state branch.** `--resume` takes a `<source>`, not a `run_id`,
but run-state branches are named `auto-pilot/<run_id>`
([`run-state.md`](run-state.md) "Run-state branch") and nothing stops more than
one run existing for the same source. After normalizing `<source>`, enumerate
the `auto-pilot/*` branches whose `RUN.md` front matter records that source and
require **exactly one** in a resumable (`active` / `paused` / `systemic`) state.
Zero matches, or more than one, is **fail-closed**: report the ambiguous
`run_id`s by name and stop rather than guess which run to resume — the same
never-guess posture the reconciliation below takes.

**Stale-orchestrator guard.** Read `orchestrator_pid` / `orchestrator_started_at`
/ `until` from `RUN.md`'s front matter ([`run-state.md`](run-state.md) "`RUN.md`").
If a live orchestrator with the matching start-time is still running at that PID
([`launch-runtime.md`](launch-runtime.md) "Orphan / stale detection"), do not
start a second one — report it and stop. A dead PID, or a start-time mismatch (a
recycled PID), means it's safe to proceed; the start-time is exactly what tells
the two cases apart.

**Reconcile each non-terminal task.** Re-read `RUN.md` via the `git show`
read above.
`handed-off` and `parked` tasks are terminal and left untouched. For every other
task (`claimed` / `implementing` / `pr-open` / `in-review` / `iterating`),
observe reality in the **write order's** direction — git first, then tracker,
then run files. The freshness relation remote ≥ tracker ≥ run files
([`run-state.md`](run-state.md) "Write order") is exactly why that read order
needs no guessing: does the task branch exist locally / on the remote, is there
an open PR for its head branch, what state does the tracker show, is a worker
worktree left behind. Match the observed reality to a row of that reference's
**crash-reconciliation table** ("Crash reconciliation", rows G1–G7) and apply
that row's action, in the same fixed write order — reconcile by that table, don't
restate it here. The load-bearing invariant that makes this decidable:
`needs_review` is only ever written at the hand-off tracker write, never the
pr-open one, so a task that crashed at `pr-open` always reconciles to `started`
plus a linked PR (G5), never to hand-off.

**Idempotency.** Resume must be safe to run repeatedly. It leans on G4's
idempotency check — an existing PR for a task's head branch is detected and
adopted, never duplicated — so re-resuming never opens a duplicate PR or
re-claims a task already in flight.

**Orphaned worker worktrees.** A crash mid-`implementing`/`iterating` (G2) can
leave a worker worktree behind; resume removes it before any re-dispatch.

**Never blind-retry.** A task whose observed reality doesn't match any
reconciliation row cleanly is set to `parked` and gets a `REPORT.md` entry
describing what was found ([`run-state.md`](run-state.md) "`REPORT.md`") — resume
never guesses or retries blindly.

**Then fall into the run loop.** Once reconciliation leaves `RUN.md` accurate,
resume continues into the **Run phase** loop ([`../SKILL.md`](../SKILL.md) "Run
phase") for the remaining ready tasks; it does not re-derive that loop. If the
run was paused (`status: paused` / `paused_until` set), resume clears those
run-level pause markers before re-entering the loop, per
[`run-budget.md`](run-budget.md) "Near-cap → pause + relaunch past reset" — pause
semantics live there, not here.
