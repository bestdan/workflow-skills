# Auto-pilot: overview

**Auto-pilot turns a vetted task graph into reviewable PRs without requiring a
person to keep an active coding session open.** Its promise is deliberately
narrow: tasks are claimed, built, verified, reviewed, and handed off—not merged
or tracker-completed unattended.

The system earns that autonomy from three legs:

1. **Bounded blast radius.** A dedicated worktree, a run-state branch, handler
   claim protocols, and a sandbox constrain where unattended work acts.
2. **A heartbeat, not silence.** A detached supervisor, periodic status,
   heartbeat, and alarms make a stalled run observable instead of interpreting
   a quiet terminal as health.
3. **Verified trust.** Implementation is independently checked, put in a PR,
   co-reviewed when enabled, and stopped at `needs_review` for a human.

The durable design decisions are in [[auto-pilot]]; the executable contracts
are the auto-pilot skill and its references.

## The three phases

```text
interactive launch  →  detached run loop  →  explicit resume when needed
fail-closed checks      unattended delivery    reconcile reality, then continue
```

### Launch: interactive and fail-closed

`/auto-pilot <linear-project | plan-dir>` runs inside an existing Claude Code
session. Launch creates the isolated worktree and state branch, checks source,
auth, environment, coder routes, verification, and confinement, then resolves
all choices that would otherwise prompt later. Any hard failure is a no-go while
the operator can still repair it.

Only after that preparation does launch write runtime artifacts and start a
detached `claude -p` orchestrator. On macOS its relaunchable supervisor is a
`launchd` job. [Launch runtime](../skills/auto-pilot/references/launch-runtime.md)
describes the boundary between the interactive launcher and detached worker.

### Run: detached and unattended

The detached process walks the ready portion of the graph. It never implements
feature code directly; it calls `/deliver-task` for each task and relies on
durable run state to survive a new process, a rate-window pause, or an ordinary
context exit. Resource controls check Claude headroom before expensive delivery
boundaries, preserve a reserve, and pause/relaunch rather than silently crossing
a usage limit. See [run budget](../skills/auto-pilot/references/run-budget.md).

### Resume: reconcile a crash or pause

`/auto-pilot <same source> --resume` is not “start over.” It locates exactly
one resumable run, protects against a still-live orchestrator, reruns
time-sensitive checks, and reconciles task state against git, the tracker, and
durable files before returning to the loop. Ambiguity fails closed; a task that
cannot be reconciled safely is parked rather than blindly repeated. See
[resume](../skills/auto-pilot/references/resume.md).

## One task’s path

```text
claim → implement → verify → PR → co-review → iterate → needs_review
```

`/deliver-task` owns that lifecycle. It claims with the selected handler’s
distributed-lock protocol, routes implementation to a coder backend in an
isolated worker worktree, judges and verifies the integrated diff, opens a PR,
runs non-interactive co-review when the run profile enables it, and makes at
most bounded review iterations. The hand-off is `needs_review` / `handed-off`.

There is intentionally no unattended merge or tracker-complete transition.
Human review and merge happen after the runner hands off; normal merge-verified
completion remains the job of `/sweep-for-complete`.

## The run is its state branch

Every run uses a branch named:

```text
auto-pilot/<run_id>
```

Its durable memory lives under `.auto-pilot/` in three committed files:

| File           | Purpose                                                              |
| -------------- | -------------------------------------------------------------------- |
| `RUN.md`       | Fixed parameters and the per-task lifecycle table.                   |
| `QUESTIONS.md` | Decisions the runner cannot safely make on its own.                  |
| `REPORT.md`    | The rolling human-facing account of progress, pauses, and hand-offs. |

The write order is a crash-recovery invariant: **push code → update tracker →
commit run state**. If the process dies between those actions, resume observes
reality in that direction and chooses the matching reconciliation row instead
of guessing. [Run state](../skills/auto-pilot/references/run-state.md) is the
canonical format and recovery reference.

## Supervision is an active component

The supervisor is not a simple timer. A fresh detached process may be launched
after context exhaustion or a rate-window pause, but it reads the run’s exit
contract before choosing whether to relaunch, stop, or alarm:

| Exit reason  | Meaning                                            | Supervisor action                |
| ------------ | -------------------------------------------------- | -------------------------------- |
| `continuing` | Work remains; the process exhausted its context.   | Relaunch.                        |
| `paused`     | Wait for a recorded rate-window reset.             | Relaunch after the pause.        |
| `done`       | No ready work remains.                             | Tear down.                       |
| `systemic`   | Circuit breaker, fatal auth, or invariant failure. | Tear down and alarm.             |
| `deadline`   | The next task could not fit before `--until`.      | Tear down; explicit resume only. |

A heartbeat records that a wake or task boundary is alive; it is evidence for
diagnosis, not permission to ignore failure. The supervisor also scans for
stalls and terminal conditions, produces periodic status, and raises an OS
notification plus an `ALARM` sentinel and a top-of-report reason when a human
must act. That machinery exists because a silent unattended failure is not a
healthy run. [Launch runtime](../skills/auto-pilot/references/launch-runtime.md)
and [run budget](../skills/auto-pilot/references/run-budget.md) define the
relaunch, exit-contract, heartbeat, and alarm details.

## Where CAO fits

CAO is a coder-backend transport, not a second auto-pilot loop. The normal path
remains:

```text
/auto-pilot → /deliver-task → select-coder → orchestrate-coders → coder worktree
```

With `--profile less-claude`, selection is limited to CAO-dispatchable Codex or
Antigravity choices and maps them to named, model-pinned `cao-codex` or
`cao-agy` custom coders. `scripts/cao-coder.sh` passes the packet and the
already-created worker worktree to `cao-run`; it refuses any other CAO fleet
member or an absent/non-worktree target.

The profile exists to spend fewer Claude tokens on the mechanical coding leg.
It also defaults co-review off (with an optional cheap single pass) and moves
integrated-diff judgment to Sonnet, while preserving independent shell
verification and PR hand-off. CAO requires a running local `cao-server` and
the CAO working-directory mode. The profile gate checks the executables and
daemon reachability at launch and again on resume; configure the
working-directory mode before either step. See
[the CAO custom-coder template](../skills/orchestrate-coders/SKILL.md) and
[the auto-pilot profile](../skills/auto-pilot/SKILL.md).
