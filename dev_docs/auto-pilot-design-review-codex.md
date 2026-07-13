# Verdict

Yes: the architecture is manufacturing these bugs.

`RUN.md` is not itself the root cause. The root cause is that the system calls it a “cache + report” while using it as a privileged control plane. The agent writes the facts that determine whether the agent is healthy, paused, finished, or entitled to run again. The 4,500-line shell supervisor then tries to reconstruct truth from those claims.

That is why the same shapes recur. You are adding increasingly careful guards around an authority boundary that is backwards.

The highest-leverage change is:

> Stop running a long-lived model as the orchestrator. Make a deterministic, supervisor-owned controller walk the graph and invoke the model as a bounded worker for one task attempt at a time.

Keep the no-model supervisor. Delete most of what it currently supervises.

## 1. `RUN.md` must become output, never control input

The design already says the tracker and Git are authoritative and the run files are only “cache + report” ([run-state.md](../skills/auto-pilot/references/run-state.md#L8)). But the same file contains `status`, `paused_until`, `exit_reason`, PID identity, deadline, and phases that directly drive relaunch and teardown ([run-state.md](../skills/auto-pilot/references/run-state.md#L31)). The agent declares why it stopped, and—except for fatal-auth inference—“declaring beats inferring” ([run-state.md](../skills/auto-pilot/references/run-state.md#L115)).

That is the contradiction.

Task 23 proves it decisively: two agent-written fields were found not to be independent authority ([task 23](tasks/autopilot_hardening_plan/autopilot_hardening_task_23.md#L19)). While I was reviewing, its fix landed and added roughly 700 lines across code/tests/docs, including a special `supervisor-state` ledger protected by a per-file Seatbelt denial. The implementation itself now says a declared pause “is not evidence” ([spawn-orchestrator.sh](../scripts/spawn-orchestrator.sh#L2286)).

That fix has discovered the right architecture one field at a time: supervisor-owned state outside the agent’s write authority. Generalize it instead of repeating it.

The correct substrate is:

- A supervisor-owned run database or append-only journal outside the jail’s writable tree.
- Supervisor-owned run configuration: deadline, pause budget, retry policy, current task, attempt number.
- A lease written before every dispatch: task, child PID/start time, start time, base SHA, attempt deadline.
- Observations gathered after dispatch: branch SHA, PR state, tracker state, verification result, child exit.
- Agent output stored only as a claim: “I completed,” “I paused,” “PR #123.” Claims do not cause transitions until corroborated.
- `RUN.md`, `REPORT.md`, and `STATUS.md` generated as projections of that state. Humans may edit neither to steer the controller.

Do not try to derive everything solely from commits and PRs. Before a push, absence is ambiguous. The supervisor-owned dispatch lease supplies the missing fact: “I started worker X at T.” Git, GitHub, tracker state, and verification then provide outcome evidence.

Delete from agent authority:

- `status`, `paused_until`, `pause_reason`, `exit_reason*`, PID fields, and operational deadlines in `RUN.md`.
- `exit-reason` and `clear-exit-state`.
- Agent-authored completion sentinels.
- Agent heartbeat as a health verdict. A heartbeat may remain diagnostic, but it cannot grant exemptions.
- Doctor no-progress state and the agent-to-supervisor `alarm-request` protocol.
- Destructive live-loop orphan cleanup. Clean workers only when the controller’s own lease proves no child owns them.

The documented write order—push → tracker → run file—is useful recovery discipline ([run-state.md](../skills/auto-pilot/references/run-state.md#L402)), but it should update a projection, not define the controller’s memory.

## 2. “No model in the supervisor” is right; 4,500 lines of Bash is not

A model call would not fix any of these defects. It would add another fallible self-reporting layer and fail exactly when auth or rate limits fail. The availability argument remains sound.

The mistake is letting the supervisor grow into all of these at once:

- sandbox compiler;
- generated-program compiler;
- launchd manager;
- state-machine parser;
- crash reconciler;
- doctor and repair engine;
- GitHub classifier;
- restacker;
- verify broker;
- status renderer;
- alarm router.

This is a deterministic controller implemented as shell fragments and Markdown parsing, while still being described as a supervisor.

The minimal model-free controller needs four states: `waiting`, `running`, `backoff`, `terminal`. On each tick it should:

1. Record the tick and inspect its own state.
2. Reconcile the last attempt from its lease plus external observations.
3. Alarm or terminate if policy requires.
4. If eligible, choose one ready task, record a new attempt, and spawn one bounded worker.
5. Record the child exit and schedule the next tick.

The worker does not decide whether the run is “done” or “continuing.” The controller knows whether the graph has ready work. Context exhaustion becomes a failed/incomplete attempt, not a fifth exit vocabulary.

Use a small typed implementation with atomic persistence—Python plus SQLite would be enough. Shell should remain only for:

- rendering/checking the Seatbelt profile;
- the thin macOS launchd adapter;
- perhaps notification delivery.

Move restacking into a separate post-merge reconciler. Move verification into the controller or a controller-owned broker whose result path the agent cannot write. The current broker explicitly admits the agent can forge `code: 0` because its result lives in the writable run tree ([launch-runtime.md](../skills/auto-pilot/references/launch-runtime.md#L414)).

## 3. The gate seam is a design smell: make it two programs

The generated wrapper currently has a hand-maintained “above/below” topology:

- scan;
- heartbeat;
- gate;
- early `exit 0`;
- agent;
- post-agent check.

The code needs extensive comments merely to preserve that topology ([spawn-orchestrator.sh](../scripts/spawn-orchestrator.sh#L1093)). The post-mortem shows two independent PRs landing on the wrong side without conflicts ([review feedback](auto-pilot-developer-review-feedback.md#L62)).

That is not an implementation accident. An early exit in the middle of a generated program creates a permanent bypass hazard.

Make these separate executables:

- `autopilot-watch <run>`: always runs on every launchd tick; owns reconciliation, time, budgets, alarms, state, and scheduling.
- `autopilot-attempt <attempt-id>`: runs only when `watch` decides an attempt is allowed; invokes the jailed model and returns its raw result.

Then there is no “above the gate.” The watcher is not gated. It conditionally spawns the attempt program.

Delete the generated shell control flow. The launchd plist should call one stable command with a run ID and supervisor-state path.

## 4. Detachment should be a deployment mode, not the architecture

Detachment is worth retaining, but it should not be the default for the common partially attended case.

A foreground, attended-but-idle controller loses only:

- survival after the terminal/session exits;
- unattended restart after reboot;
- OS timer ownership when the terminal is gone.

It does not need to lose resumability, alarms, state durability, time bounds, or sandboxing. Those belong to the controller regardless of how it is hosted.

Make the same controller available as:

- `--foreground`: default for attended and partially attended work;
- `--detach`: explicit launchd deployment after a real detached smoke test.

This makes launchd an adapter rather than a second execution semantics. Foreground mode avoids much of the TCC attribution surprise, makes auth failures visible, and keeps the live status surface naturally visible. Truly unattended runs still use launchd and the jail.

Do not delete detached mode; delete the assumption that every autonomous run must pay its complexity tax.

## 5. The test suite is proving components, not the system

The review feedback is unusually conclusive:

- fixtures disagreed with production precisely at the invariant boundary;
- fake `gh` had impossible exit semantics;
- tests bypassed the generated gate;
- notifier guards could not observe their own bypass;
- settled-state fixtures missed the dangerous window between writes ([review feedback](auto-pilot-developer-review-feedback.md#L13)).

The suite is not useless. Its sandbox renderer and pure parser tests have value. But 600 shell assertions cannot establish that the composed program works, especially when the composed program is generated.

The cheapest dominating harness is a scenario runner around the real controller, with:

- real Git repositories and worktrees;
- an injected clock;
- a fake Claude executable;
- explicit fake GitHub/tracker/launchd/notifier adapters—not ambient `PATH` shadowing;
- the exact production configuration and entry point.

A small scenario set would catch most of the defect history:

1. Worker exits `0` repeatedly with no external progress → halt and alarm.
2. Valid pause, missing pause bound, expired pause, and far-future pause → correct spawn count and bounded exemption.
3. Crash after every controller transaction → deterministic reconciliation, no duplicate PR, no live-worktree deletion.
4. GitHub `NOT_FOUND` versus transient 401/rate limit → distinct outcomes using a boundary contract matching real `gh`.
5. Parent merge/restack → verify and co-review are actually rerun before renewed hand-off.
6. Teardown partial failures → no “job gone, no terminal record, no alarm” outcome.
7. One macOS canary using real launchd and real Seatbelt with a fake model—testing the actual detached artifact end to end.

Keep one opt-in contract probe against real `gh`. Keep the jail smoke. Delete most string-shape tests for generated wrapper lines once the wrapper disappears.

Also delete the proposed restack line-survival audit. Your own evidence shows a clean three-way rebase cannot silently drop the parent’s added line in the claimed way; overlap conflicts first. The real invariant is that behavioral evidence is invalidated. Re-run verification, co-review, and the PR’s acceptance criterion. Do not manufacture another unreachable guard.

## The structural win you were missing

You have been treating Claude as both:

- the workflow engine that owns the state machine; and
- the unreliable worker the supervisor must police.

Those roles cannot share authority.

Invert them:

> Deterministic controller owns time, graph state, retries, leases, reconciliation, verification verdicts, and alarms. The model receives one bounded task attempt and a writable worktree.

That removes the need for the exit contract, most of the doctor, the gate seam, agent-authored health, and much of resume reconciliation. It also turns “failure modes look like success” from a philosophical problem into a simple policy:

> Exit 0 without the externally required effect is no progress.

This is not an ordinary bug cluster. The repeated shapes are the architecture telling you where the missing boundary is.
