# Auto-pilot resume — `--resume` reconciliation

How `/auto-pilot <source> --resume` (`commands/auto-pilot.md`) reconciles a
crashed or paused run's durable state against reality before falling into the
normal **Run phase** loop ([`../SKILL.md`](../SKILL.md) "Run phase"). Procedure
and invariants only — the run-state formats and the crash-reconciliation table
this leans on live in [`run-state.md`](run-state.md); pause semantics live in
[`run-budget.md`](run-budget.md).

Resume's job is to reconcile a crashed or paused run's durable state against
reality, then fall into the normal **Run phase** loop for whatever remains ready.

**Stale-orchestrator guard, first — before the doctor, and before anything else
touches the worktree.** ("Locate the run-state branch" below is what resolves the
`<run_id>` this and the doctor both take; it is a pure branch read that writes
nothing, so it precedes both.) Read `orchestrator_pid` / `orchestrator_started_at`
/ `until` from `RUN.md`'s front matter ([`run-state.md`](run-state.md) "`RUN.md`"),
reading it **from the run-state branch** (`git show
auto-pilot/<run_id>:.auto-pilot/RUN.md`, the same belt the doctor's invariant 2
uses) so this guard never depends on the worktree state the doctor exists to
repair. If a live orchestrator with the matching start-time is still running at
that PID ([`launch-runtime.md`](launch-runtime.md) "Orphan / stale detection"), do
not start a second one — report it and **stop here**. A dead PID, or a start-time
mismatch (a recycled PID), means it's safe to proceed; the start-time is exactly
what tells the two cases apart.

**This ordering is load-bearing, not cosmetic.** The doctor **repairs** — it
restores `HEAD`, discards stale run-state dirt, and `git worktree remove
--force`s orphan worker worktrees (invariant 5). Every one of those is a write
against a run that a live orchestrator may be _actively using_: a second
`--resume` fired against a healthy run would, with the doctor running first,
mutate that run's worktree tree out from under it. The guard must therefore
answer "is anyone else driving this run?" **before** the first repairing write,
not after. (The doctor's invariant 5 enforces the same rule independently — it
refuses to prune a worktree no `RUN.md` row claims unless the orchestrator is
_provably dead_ — because a destructive prune must not depend for its safety on
a step ordering that no code enforces. Belt and braces: the guard here, the
liveness gate there.)

**Clear the run's alarms — after the guard above, and BEFORE the doctor.** Run
`spawn-orchestrator.sh alarm-clear --dir <run-dir>`, which removes the
`.auto-pilot/ALARM` sentinel and any undelivered `alarm-requests/`. That sentinel
is the alarm's **per-run idempotency key**
([`run-budget.md`](run-budget.md) "The alarm"), and every alarm's own required
action ends "…then `/auto-pilot <source> --resume`" — so a sentinel that outlives
the resume would **suppress** the next alarm for the same condition (a token that
expires again, a base that breaks again), and the resumed run would halt in the
silence the alarm exists to end. The alarms describe the run the human just
repaired; they do not carry forward. `REPORT.md`'s alarm history **stays** — that
is what the human reads.

The ordering against the doctor is load-bearing in BOTH directions. It must run
**after** the stale-orchestrator guard (clearing a live run's alarms would retract
a warning that is still true), and **before** the doctor — because the doctor
FILES an `alarm-request` when it halts (`run-state.md` "Run doctor"), and an
`alarm-clear` running afterwards would delete the request this very resume just
filed, restoring exactly the silence both mechanisms exist to end.

**Clear the terminal exit state — likewise before the doctor and the first wake.** Run
`scripts/spawn-orchestrator.sh clear-exit-state --dir <run-worktree>`. The exit
contract is DURABLE by design ([`run-state.md`](run-state.md) "Exit contract"):
the last `exit_reason` / `exit_reason_at` / `exit_reason_detail` are committed to
the run-state branch, and a terminal reason (`done` / `systemic` / `deadline`)
also drops the done-sentinel `.auto-pilot/orchestrator.done` — the SAME file the
supervisor's relaunch gate and `status` read. `deadline` is by definition the
reason whose recovery IS this `--resume`, and `systemic`'s is a human fixing the
condition and resuming; so a resume that did not clear them would carry "the run
is over" into a live run that is actually working: `status` would report
`relaunch=no` (and, after a prior `done`, a finished run status), a
`KeepAlive`/`PathState` watcher gating on the sentinel would treat it as
complete, and the supervisor's declared-reason check could tear the resumed run
down on its very first wake. `clear-exit-state` removes the sentinel, blanks the
three `exit_reason*` fields, and commits — so the run's first real declaration is
the only one on the branch.

**Then run the doctor — before anything else reads the worktree.** Run
`scripts/spawn-orchestrator.sh doctor --dir <run-worktree> --run-id <run_id>
--questions .auto-pilot/QUESTIONS.md [--handler <h>]`
([`run-state.md`](run-state.md) "Run doctor"). `--resume`'s reconciliation pass
**is** the doctor, run once at the top of resume — the same seven-invariant
audit the run loop runs every iteration, not a second, divergent
reconciliation. Its first two invariants are exactly the two belts a crashed
resume used to hand-roll: invariant 1 restores the run worktree's `HEAD` when a
crash left it parked on a task branch (finding #23), and invariant 2 reads
`RUN.md`/`QUESTIONS.md`/`REPORT.md` via `git show
auto-pilot/<run_id>:.auto-pilot/<file>` rather than the worktree's copy —
halting `systemic` if even the BRANCH read fails or the front matter doesn't
parse (the run has no memory), repairing a working-tree-only loss by
restoring `.auto-pilot/` from the branch. A doctor **HALT** (exit 30) means
resume stops here, before it trusts anything else it reads off that worktree.

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

**Less-claude capability join.** Read `run_profile` and its profile fields from
the selected `RUN.md` before re-running the join. When it is `less-claude`,
re-verify `cao`, `cao-run`, and `cao-server` on `PATH` and `nc -z localhost
9889`; a missing binary or non-responding `cao-server` **BLOCKS THE RESUME**.
Re-apply the launch phase's **working-directory gate** too — require
`[ "${CAO_ENABLE_WORKING_DIRECTORY:-}" = "true" ]` in the resume environment (the
same launch-shell proxy, and the same caveat that macOS makes the daemon's real
env uninspectable; see [`../SKILL.md`](../SKILL.md) "Less-claude CAO gate"); an
unset value **BLOCKS THE RESUME**. Then re-check every recorded
`cao_coder_mapping` route against the current CAO fleet. Do not restart the
daemon or downgrade the profile during resume.

**Locate the run-state branch.** `--resume` takes a `<source>`, not a `run_id`,
but run-state branches are named `auto-pilot/<run_id>`
([`run-state.md`](run-state.md) "Run-state branch") and nothing stops more than
one run existing for the same source. After normalizing `<source>`, enumerate
the `auto-pilot/*` branches whose `RUN.md` front matter records that source and
require **exactly one** in a resumable (`active` / `paused` / `systemic`) state.
Zero matches, or more than one, is **fail-closed**: report the ambiguous
`run_id`s by name and stop rather than guess which run to resume — the same
never-guess posture the reconciliation below takes.

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
plus a linked PR (G5), never to hand-off. The doctor call above already
**mechanizes** the two rows a G6/G7 gap most often leaves stale: its
invariant 3 catches a `pr-open`/`in-review`/`iterating`/`handed-off` task
whose PR vanished or was never linked, and its invariant 4 catches a
`handed-off` PR still carrying `task-claim` or still draft (the review-signal
half of G6) — this section's own reconciliation covers the OTHER rows (G1–G5),
not a re-derivation of what doctor already checked.

**Idempotency.** Resume must be safe to run repeatedly. It leans on G4's
idempotency check — an existing PR for a task's head branch is detected and
adopted, never duplicated — so re-resuming never opens a duplicate PR or
re-claims a task already in flight.

**Orphaned worker worktrees.** A crash mid-`implementing`/`iterating` (G2) can
leave a worker worktree behind; the doctor call above already removes any that
are safe to prune (invariant 5) before resume does anything else with the
worktree tree.

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
