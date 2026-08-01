---
name: auto-pilot
description: Unattended autonomous mode — "pick up this Project and grind on it overnight." Runs a task graph (a Linear project or a plan-with-docs directory) task-by-task in an isolated worktree, taking each through /deliver-task (claim → implement → PR → co-review → hand-off) with durable, crash-resumable state and no human in the loop. Use when the user wants a body of work advanced autonomously and unattended. NOTE - v1 is under construction; this entry establishes the skill home and the run-state reference. Launch, run, and resume are implemented.
---

# auto-pilot — unattended autonomous runs

"Claude, pick up this Project and grind on it overnight."

Auto-pilot advances a whole task graph without a human in the loop: an isolated
worktree, a thin orchestrator that walks the graph, and `/deliver-task` per task
(claim → implement → PR → co-review → iterate → hand-off). It composes existing,
battle-tested skills and handler protocols rather than duplicating them.

Design: [`../../dev_docs/auto-pilot.md`](../../dev_docs/auto-pilot.md).

> **Status:** v1 is being built. This SKILL.md establishes the skill home, the
> references below, the interactive **launch** phase, the unattended **run**
> loop (which the spawned orchestrator executes), and **`--resume`**'s crash
> reconciliation. The three phases compose end-to-end: launch spawns an
> orchestrator that runs the loop, and a crashed or paused run resumes into
> that same loop.

## References

- [`references/run-state.md`](references/run-state.md) — the canonical formats
  and invariants for a run's durable state: the three run files
  (`RUN.md` / `QUESTIONS.md` / `REPORT.md`), the task lifecycle phases,
  the dedicated run-state branch convention, the fixed write order
  (push code → update tracker → commit run state), and the crash-reconciliation
  table `--resume` uses. Launch, run, and resume all read this — no other
  document restates a run-state format.
- [`references/adapters.md`](references/adapters.md) — the work-source adapter
  interface: the eight verbs (`list_ready`, `dependency_graph`, `claim`,
  `link_pr`, `set_needs_review`, `flag_for_human`, `comment_progress`,
  `wip_limit`) that normalize a Linear project or a plan-with-docs directory to
  the run-state representation, each delegating to an existing handler section.
- [`references/launch-runtime.md`](references/launch-runtime.md) — the two launch
  runtime decisions: how the detached orchestrator is spawned (detached
  `claude -p`, not an in-session Agent) and the sandbox profile it runs under
  (worktree-confined writes, a default-deny network egress allowlist, and the
  non-interactive credential model). Launch's spawn step reads this.
- [`references/launch-preflight.md`](references/launch-preflight.md) — the
  ordered, fail-closed launch pre-flight (steps 1–7): worktree + run-state
  branch, non-interactive auth probes and the environment-class fingerprint,
  config resolution into non-interactive choices, the gitignore/verify-tooling
  capture, task-graph materialization with the capability-join scout, and the
  detached-orchestrator spawn. The Launch phase summarizes these; the per-step
  mechanics live here.
- [`references/run-budget.md`](references/run-budget.md) — the run's resource
  bounds: rate-window checks (proxy + rate-limit-error backstop), the
  near-cap checkpoint-then-exit-then-relaunch pause, the hard-stop before
  paid/overflow credits, per-task wall-clock and retry limits, the paid-agent
  dispatch cap, and the run-level circuit breaker. The run loop's budget
  check reads this. It also owns **the alarm**: a halted or stalled run
  actively notifies a human (OS notification + an `ALARM` sentinel + the top
  line of `REPORT.md`), from the un-jailed supervisor, in shell — silence, not
  the failure, was the expensive half of finding #22.
- [`references/resume.md`](references/resume.md) — the `--resume` reconciliation
  procedure: which pre-flight is re-run vs skipped, locating the one resumable
  run-state branch, the stale-orchestrator guard, and the per-task reconciliation
  against run-state.md's crash-reconciliation table before falling into the run
  loop. The Resume phase summarizes this; the mechanics live here.

## Script paths

**Always invoke a plugin-shipped helper script as
`"${CLAUDE_PLUGIN_ROOT}/scripts/<name>.sh"`** — `preflight.sh`,
`spawn-orchestrator.sh`, `probe-coders.sh`, `claude-usage.sh`, and the rest of
this repo's `scripts/`. The orchestrator runs with cwd = the **target repo**,
not this plugin, so a bare `scripts/<name>.sh` resolves against the target
repo's tree, where the file doesn't exist — and it fails as "not found" rather
than as "wrong repo", which is how it went undiagnosed. If
`$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
`**/scripts/<name>.sh`.

This rule governs **invocation**. These files also mention the same scripts in
prose by bare name (`spawn-orchestrator.sh status`, `claude-usage.sh
--session-status`) when naming a subcommand rather than giving a command to
run; those mean the same plugin-shipped script and resolve the same way.

The rule does **not** extend to the **target repo's own** scripts — e.g.
`bash scripts/check.sh` in
[`references/launch-runtime.md`](references/launch-runtime.md) "Verify broker",
which is the repo-under-automation's declared verify command and is correctly
repo-relative.

## Launch phase (interactive)

Invoked by `/auto-pilot <linear-project | plan-dir> [--until <time>]
[--reserve <pct>] [--profile less-claude] [--resume]`
(`commands/auto-pilot.md`). Launch runs **interactively, tonight, while the human
can still fix failures** — so it is **fail-closed**: any hard pre-flight failure
**BLOCKS LAUNCH** with a specific, fixable message rather than deferring the
problem to 3am. It ends by spawning the detached, unattended orchestrator and
telling the user the run is underway. The unattended **run** loop is what the
spawned orchestrator executes; **`--resume`** reconciles a crashed or paused
run's state and then falls into that same loop (see "Resume phase" below).

**Preamble — parse + resolve.** Parse `<source>`, `--until`, `--reserve`,
`--profile less-claude`, `--resume`. `--profile` is optional and only accepts
`less-claude`; without it, preserve the existing launch behavior exactly. On a
fresh less-claude launch, resolve and persist the profile settings in step 3.
On `--resume`, read those settings from the selected `RUN.md` rather than
re-parsing a profile choice. `--reserve <pct>` is the run's fixed rate-window headroom floor;
default it to `15`, require a numeric percentage from 0 through 100, and
persist the resolved value in `RUN.md`. On resume, use that persisted value
unless an explicit, validated `--reserve` replaces it. `--until` accepts an
absolute ISO-8601 time or a relative `now+<duration>`
offset; launch resolves either to the absolute time recorded in `RUN.md`. If
`--resume` is present, route to the **Resume phase** section below instead of
running the rest of this launch pre-flight. Detect the source (existing
`dev_docs/tasks/<name>_plan/` dir → **plan**; else → **linear** project) and
resolve `dev_docs/tasks/.task-config.yml`'s handler for validation only; any
handler other than `linear`/`repo-pr` → stop (v1 supports linear + plan
only). The run's **effective handler** — what actually gets passed to
`/deliver-task` — is source-derived, not read back off that config: a
**plan** source ⇒ `repo-pr`, a **linear** source ⇒ `linear`, per the
matching adapter (`references/adapters.md`) — this keeps a plan-source run
correct even when the repo's own `.task-config.yml` default is `linear`.

**Size `--until` realistically.** A single `/deliver-task` floors well above a
naive "a few minutes" guess once the resolved reviewer set includes a cloud
reviewer (step 3 computes the exact number as `min_task_budget`; the formula
is [`references/run-budget.md`](references/run-budget.md) "Minimum task
budget"). An under-provisioned `--until` isn't fatal: the pre-dispatch guard
(Run phase) just declines to start the next task, so a too-tight window
yields fewer tasks, not a hard-killed one. Provision at least one
`min_task_budget` per task you expect to finish.

The pre-flight is an **ordered, fail-closed** sequence, steps 1–7 below. It is
**supply-and-demand**: steps 2–3 probe what the configured environment can
_supply_ (auth, resolved config), and the **scout** in step 6 checks what the
_plan_ will _demand_ (which coder each task routes to) against that supply — the
join is where a run that would otherwise pass green but die at 3am gets caught
tonight.

The full mechanics of each step are in
[`references/launch-preflight.md`](references/launch-preflight.md); in brief:

1. **Worktree + run-state branch** (BLOCKS LAUNCH) — create the run worktree
   and the `auto-pilot/<run_id>` branch; an untracked plan is a blocker.
2. **Non-interactive auth probes** (BLOCKS LAUNCH) — `"${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh"`
   plus per-credential probes (GitHub, Linear, coder CLIs, MCP), each through
   the run's sandbox wrapper; capture the environment fingerprint / class; a
   probe that would prompt is itself the failure. Includes the less-claude CAO
   gate and the machine-stays-awake check.
3. **Resolve config into non-interactive choices** (BLOCKS LAUNCH) — reviewer
   set + `min_task_budget`, `select-coder` per task, the less-claude profile
   fields, and the custom-commands posture; anything left unresolved would
   prompt at 3am, so it blocks.
4. **Gitignore sanity check** — record ignored output paths for `git add -f`;
   warn-only, never blocks.
5. **Record verify tooling + exercise path** — pin `verify_command` /
   `exercise_path` into `RUN.md` and install the un-jailed verify broker.
6. **Materialize the task graph into run state** — write `RUN.md` /
   `QUESTIONS.md` / `REPORT.md`, then the deterministic capability-join
   **scout** (BLOCKS LAUNCH on a route-vs-environment gap).
7. **Spawn the detached orchestrator** — launch script + sandbox wrapper, the
   auth smoke test through that wrapper, OS-level detach with PID/start-time
   recorded, and print where state lives.

## Resume phase (--resume)

Invoked by `/auto-pilot <source> --resume` (`commands/auto-pilot.md`). Resume
reconciles a crashed or paused run's durable state against reality, then falls
into the normal **Run phase** loop below for whatever remains ready. The full
procedure is in [`references/resume.md`](references/resume.md); in short:

- **Re-run only the pre-flight that can rot** — the auth probes, the environment
  fingerprint, the scout's capability join, and base freshness (a run launched
  `local-full` may resume under `claude-web`). Worktree + run-state-branch
  creation and task-graph materialization already exist on the branch and are
  not re-created; source normalization still runs. A hard failure **BLOCKS THE
  RESUME**, fail-closed.
- **Locate exactly one resumable run-state branch** for the normalized source
  (zero or many is fail-closed), and **guard against a live orchestrator** at the
  recorded PID + start-time — **before the doctor**, which _repairs_ and so must
  never run against a run someone else is still driving.
- **Clear the run's alarms** (`spawn-orchestrator.sh alarm-clear --dir
  <run-dir>`) — after that guard, **before the doctor**, so clearing never
  deletes an `alarm-request` a doctor halt is about to file
  ([`references/resume.md`](references/resume.md)).
- **Clear the terminal exit state too** (`spawn-orchestrator.sh
  clear-exit-state --dir <run-worktree>`) — the done-sentinel and
  `exit_reason*` are durable and would otherwise make a resumed live run read
  back as finished ([`references/resume.md`](references/resume.md)).
- **Then run the doctor.** A **HALT** (exit 30) stops here.
- **Reconcile each non-terminal task** by observing reality in the write order's
  direction (git → tracker → run files) and matching it to `run-state.md`'s
  crash-reconciliation table (rows G1–G7) — idempotent (adopts an existing PR,
  never duplicates), removes orphaned worker worktrees, and **parks** anything
  that matches no row cleanly rather than blind-retrying.
- **Then fall into the run loop**, clearing any run-level pause markers first
  ([`references/run-budget.md`](references/run-budget.md) owns pause semantics).

## Run phase (unattended)

This is what the detached orchestrator spawned by launch (step 7) executes,
alone, with no human watching. It never writes code itself — every code change
happens inside a `/deliver-task` call — and it reads and writes only the
run-state formats defined in
[`references/run-state.md`](references/run-state.md). It advances a task as
far as `handed-off` and stops there; it never merges a PR or completes a
tracker item (that is `/sweep-for-complete`'s job, later, with a human's PR
approval in between).

The loop is deliberately thin:

```
while unblocked tasks remain and inside budget bounds:
    heartbeat (and again at every /deliver-task sub-step boundary)
    doctor   # run invariant audit; repair/park/halt — HALT (exit 30) stops here
    pick next unblocked task (phase-based readiness)
    if until is set and now + min_task_budget > until:   # pre-dispatch deadline guard
        stop the loop cleanly (record "N left, M min to deadline, not starting")
    /deliver-task it --run-state .auto-pilot/RUN.md (with per-task wall-clock + retry bounds)
    update run state on the run-state branch
    check rate-window usage
declare the exit reason, then exit          # every termination path, no exceptions
```

**The exit contract.** Before exiting, for any reason, run
`"${CLAUDE_PLUGIN_ROOT}/scripts/spawn-orchestrator.sh" exit-reason --dir <run worktree> --reason <r>`
(`continuing` | `paused` | `done` | `systemic` | `deadline`), and beat the
heartbeat (`spawn-orchestrator.sh heartbeat --dir <run worktree> --note
<where>`) at each loop iteration and `/deliver-task` sub-step boundary. Full
semantics, precedence, and fail-safe rules:
[`references/run-state.md`](references/run-state.md) "Exit contract" and
"Heartbeat".

**Every iteration opens with the run doctor**, `"${CLAUDE_PLUGIN_ROOT}/scripts/spawn-orchestrator.sh"
doctor` — a cheap, deterministic, no-model-call audit of seven invariants
(HALT on `status: systemic`, exit 30 — the loop must not dispatch). The
invariant table, each one's repair, the exit codes, and the finding-#22/#23
rationale: [`references/run-state.md`](references/run-state.md) "Run doctor".

**Pre-dispatch deadline guard.** Before claiming each task, when `--until` is
set, if `now + min_task_budget > until`, stop the loop cleanly and record why
in `REPORT.md` (e.g. "2 tasks left, 12 min to deadline, not starting — resume
tomorrow") — a clean stop, not a park; the un-started tasks stay ready for a
later `--resume`. `min_task_budget` is the reviewer-set-coupled floor resolved
at launch (step 3) and read from `RUN.md` front matter, never a constant; why
this guard exists and the floor's derivation:
[`references/run-budget.md`](references/run-budget.md) "Minimum task budget".

**Pre-invoke reserve gate.** The outer loop dispatches each delivery with its
`RUN.md` path; `/deliver-task`, as lifecycle owner, applies
[`references/run-budget.md`](references/run-budget.md) "Pre-invoke reserve"
at its internal claim, verify, co-review, re-verify, and repeated co-review
boundaries. The loop does not attempt to intercept those opaque substeps.

**Readiness + ordering.** Walk the `RUN.md` task graph
([`references/run-state.md`](references/run-state.md) "`RUN.md`"). A task is
**ready** when every task it is blocked by is at phase `handed-off` — never
tracker done-state (per that reference's phase table, `handed-off` is the
success terminal the run keys off, not `needs_review`'s eventual completion).
Pick the next ready task in dependency order. Each task's `base` column
encodes whether it is independent (`main`) or chained (the parent task's
branch) — that distinction drives the stacked-PR handling below.

**The per-task step.** Dispatch exactly one call per task:

```
/deliver-task <id> --base <branch> --handler <handler> --questions .auto-pilot/QUESTIONS.md --run-state .auto-pilot/RUN.md
```

where `<branch>` is the task's `base` from `RUN.md` and `<handler>` is the
run's effective handler resolved from the run's (normalized) source (plan ⇒
`repo-pr`, linear ⇒ `linear`), never re-derived by `/deliver-task` itself.
`/deliver-task` ([`commands/deliver-task.md`](../../commands/deliver-task.md))
owns the entire per-task lifecycle — claim, implement, PR, co-review, iterate,
hand-off — including the internal reserve boundaries enabled by `--run-state`;
the outer loop does not intercept those opaque substeps. The `--questions` path
is where the non-blocking decision protocol below appends entries. A
`/deliver-task` call that **fails outright** gets the per-task retry bound
(one re-dispatch, then **park**;
[`references/run-budget.md`](references/run-budget.md) "Per-task retry
limit") and the loop continues to the next ready task.

**Non-blocking decisions.** The run **never waits** on a human. For any
reversible call, the orchestrator (or `/deliver-task` beneath it) picks the
**reversible option** when uncertain, appends an indexed `QUESTIONS.md` entry,
and proceeds — format and the two feeding sources (deliver-task's own deferred
calls plus orchestrator-level ones like skip vs park):
[`references/run-state.md`](references/run-state.md) "`QUESTIONS.md`".

**Deferred co-review findings that are cross-cutting or round-bound also get a
task.** When `/deliver-task`'s iterate step defers a finding that is
**cross-cutting** or **still open at the hard 2-round bound**, it files a
tracked follow-up via `/add-task` (tagged `auto-pilot`) in addition to the
`QUESTIONS.md` entry. Dedupe/cap rules and the `REPORT.md` **Follow-ups**
index: [`references/run-state.md`](references/run-state.md) "`REPORT.md`".

**Human checkpoints produce artifacts, then proceed.** When a task needs
genuine human judgment, the run still does not block: `/deliver-task` ensures
the PR carries a working end-to-end state plus a how-to-evaluate note, the
orchestrator records the same entry in `REPORT.md`'s _How-to-evaluate queue_,
and the loop moves on.

**Rolling `REPORT.md` update.** After every task's state update above, rewrite
`REPORT.md` from the current `RUN.md` + `QUESTIONS.md` state — the seven
sections in [`references/run-state.md`](references/run-state.md)'s "`REPORT.md`"
order. Commit it on the run-state branch as part of that state update's commit,
under the write order's last step. Its _Spend_ section stays a one-line
pointer to [`references/run-budget.md`](references/run-budget.md) rather than
restating budget rules, which that reference owns.

**Stacked-PR handling.** Chains are processed in dependency order, from the
**parent's frozen tip** (the readiness rule guarantees it is stable — the
parent is already `handed-off` before a child becomes ready). Before
dispatching a chained task, the orchestrator compares the parent branch's
**current tip SHA** against the child's recorded `base_sha`
([`references/run-state.md`](references/run-state.md) "`RUN.md`"); if they
differ, the parent has moved since the base was frozen — **park** the task
instead, record it, and continue to the next ready task. This guard models
the **orchestrator** moving a base mid-run and must never fire on a **human**
merging a parent PR, which is restack's normal trigger, not an error; the
mechanized remedy and what re-verification a restacked child still needs:
[`references/run-state.md`](references/run-state.md) "Restack (post-merge
stacked-PR repair)".

**State update after each task.** After `/deliver-task` returns, update
`RUN.md` with the task's observed `phase`, `branch`, and `pr`, then commit to
the run-state branch — the **write order**'s last step
([`references/run-state.md`](references/run-state.md) "Write order");
`/deliver-task` already performed that order's push + tracker-write steps.
Then apply the budget checks in
[`references/run-budget.md`](references/run-budget.md): a hard-stop, a
near-cap pause, or a circuit-breaker halt writes state and exits per that
reference.

**Loop termination.** The loop ends when no ready task remains, a budget
hard-stop fires, or the pre-dispatch deadline guard above stops it with ready
tasks still left. The first two are a finished run (`status: done`); the
deadline-guard stop leaves the run `paused`, with the un-started tasks still
ready for a later `--resume` — the `status`/`paused_until` contract and what
that does to the relaunch supervisor:
[`references/run-state.md`](references/run-state.md) "`RUN.md`" and
[`references/launch-runtime.md`](references/launch-runtime.md) "Relaunchable,
not one-shot".

In every case the orchestrator writes and commits the final `REPORT.md`, then
declares its exit reason and exits cleanly, emitting a one-line summary to
`.auto-pilot/orchestrator.log` — e.g. `auto-pilot done: 4 handed-off, 1
parked, 0 skipped — see REPORT.md`
([`references/launch-runtime.md`](references/launch-runtime.md) "Logs /
observability"). Nothing is merged or tracker-completed by this exit: every
task sits at whatever phase it reached (`handed-off` on the tracker as
`needs_review`, or `started` for a `parked` task), ready for
`/sweep-for-complete` once a human has reviewed and merged.

For a **plan** source, the exit also does **not** delete the plan's
`<name>_plan/` scaffolding — that graduate-then-delete cleanup is a **run-level
teardown** and ultimately a human follow-up, never a graph task the loop
dispatches (see [`references/adapters.md`](references/adapters.md) "plan adapter").
