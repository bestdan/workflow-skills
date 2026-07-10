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
  (`RUN.md` / `QUESTIONS.md` / `REPORT.md`), the seven task lifecycle phases,
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
- [`references/run-budget.md`](references/run-budget.md) — the run's resource
  bounds: rate-window checks (proxy + rate-limit-error backstop), the
  near-cap checkpoint-then-exit-then-relaunch pause, the hard-stop before
  paid/overflow credits, per-task wall-clock and retry limits, the paid-agent
  dispatch cap, and the run-level circuit breaker. The run loop's budget
  check reads this.

## Launch phase (interactive)

Invoked by `/auto-pilot <linear-project | plan-dir> [--until <time>] [--resume]`
(`commands/auto-pilot.md`). Launch runs **interactively, tonight, while the human
can still fix failures** — so it is **fail-closed**: any hard pre-flight failure
**BLOCKS LAUNCH** with a specific, fixable message rather than deferring the
problem to 3am. It ends by spawning the detached, unattended orchestrator and
telling the user the run is underway. The unattended **run** loop is what the
spawned orchestrator executes; **`--resume`** reconciles a crashed or paused
run's state and then falls into that same loop (see "Resume phase" below).

**Preamble — parse + resolve.** Parse `<source>`, `--until`, `--resume`.
`--until` accepts an absolute ISO-8601 time or a relative `now+<duration>`
offset; launch resolves either to the absolute time recorded in `RUN.md`. If
`--resume` is present, route to the **Resume phase** section below instead of
running the rest of this launch pre-flight. Detect the source
(existing `dev_docs/tasks/<name>_plan/` dir → **plan**; else → **linear**
project) and resolve `dev_docs/tasks/.task-config.yml`'s handler for
validation only; any handler other than `linear`/`repo-pr` → stop (v1 supports
linear + plan only). The run's **effective handler** — what actually gets
passed to `/deliver-task` — is source-derived, not read back off that config: a
**plan** source ⇒ `repo-pr`, a **linear** source ⇒ `linear`, per the matching
adapter (`references/adapters.md`). This keeps a plan-source run correct even
when the repo's own `.task-config.yml` default is `linear`. Pick the matching
adapter (`references/adapters.md`).

**Size `--until` realistically.** A single `/deliver-task` floors at **~20–45
min** depending on the resolved reviewer set — ~20 min with the fast set
(codex + Claude + reconciler), ~45 min+ once cloud reviewers (`devin` / `agy`,
15-min bound each) are in it (see step 3 and
[`references/run-budget.md`](references/run-budget.md) "Minimum task budget").
`--until now+40min` for anything with cloud reviewers is under-provisioned: the
pre-dispatch guard (Run phase) will simply decline to start the next task, so a
too-tight window yields fewer tasks, not a hard-killed one. Provision at least
one `min_task_budget` per task you expect to finish.

The pre-flight is an **ordered, fail-closed** sequence, steps 1–7 below. It is
**supply-and-demand**: steps 2–3 probe what the configured environment can
_supply_ (auth, resolved config), and the **scout** in step 6 checks what the
_plan_ will _demand_ (which coder each task routes to) against that supply — the
join is where a run that would otherwise pass green but die at 3am gets caught
tonight.

### Step 1 — Worktree + run-state branch (BLOCKS LAUNCH)

Create the dedicated run **worktree** and the **run-state branch**
`auto-pilot/<run_id>` (the `<run_id>` and branch convention are defined in
[`references/run-state.md`](references/run-state.md) "Run-state branch"). Confirm
the plan / task instructions are **committed and present in the worktree** — an
**untracked plan is a launch blocker** (it is one of the real overnight failures
this pre-flight exists to catch: a plan that lives only in an uncommitted file
never reaches the detached orchestrator). If the source is a plan directory, the
plan files must be committed on the branch the run builds from; if Linear, the
project must resolve and be reachable. Fail here with the specific missing
artifact, not a generic error.

### Step 2 — Non-interactive auth probes (BLOCKS LAUNCH)

Probe every credential the run will need, each **non-interactively** — a probe
that would open a prompt (a browser OAuth, a biometric `op signin`) is itself the
failure (see [`references/launch-runtime.md`](references/launch-runtime.md) §3).
Probe, per dependency:

The probe path depends on the **environment class** (below): a `local-full` run
authenticates through CLIs; a `claude-web` run has **no local CLIs** and
authenticates through **MCP** instead. Probe whichever applies:

- **GitHub** — `local-full`: `gh auth status` (PRs + git push). `claude-web`:
  confirm the **GitHub MCP** is connected (no `gh` CLI exists there).
- **Linear** (linear source) — `local-full`: resolve the API key from its
  `op://` reference (`api_key_ref` in `commands/handlers/linear-common.md`) via
  `op read`. `claude-web`: use the **Linear MCP** connection (no `op`/CLI).
  Either way, run `linear-common.md`'s shared **preflight** (`list_teams` →
  match the team) to confirm auth actually works, not just that it resolves.
- **Coder CLIs** (`local-full` only — a `claude-web` run has none) — run each
  configured coder's auth probe via `scripts/probe-coders.sh`, the **single
  source of truth** for how each coder is probed; don't restate its per-coder
  commands here (they would drift from the script). A logged-out coder the run
  depends on is a blocker, not a silent skip.
- **MCP** — any MCP the tasks touch: one cheap read call to confirm the token is
  live.

Each probe runs **through the sandbox wrapper** the orchestrator will use (per
§"Step 7"), so a probe can't pass outside the jail while failing inside it. Any
interactive-only or failing auth **BLOCKS LAUNCH** with the specific dependency
named.

While probing, capture the **environment fingerprint** — which coder/tool
binaries exist on `PATH` (`codex`, `devin`, `agy`, `op`, `gh`) and the resulting
**environment class** (`local-full` when the local CLIs are present; `claude-web`
when running in the cloud/web environment, which has **no local CLIs** and a
narrower permission surface). **Detect** the facts (a `command -v` probe can't
lie); a declared class may be recorded too, but **detection wins on conflict**.
Record the fingerprint on the run-state branch — the step-6 scout joins against
it, and `--resume` in a different environment (launched local, resumed from web)
re-runs that join.

Also confirm **unattended viability** here, up front while the human is present
rather than at spawn: a `local-full` run needs the machine to stay awake for the
run's duration (lid-open, or a tested clamshell/power setup — `caffeinate` alone
does not survive lid-close; see
[`references/launch-runtime.md`](references/launch-runtime.md) "Laptop sleep").
If it can't be guaranteed, **BLOCKS LAUNCH**. (This and the auth/binary probes
are good candidates to extract into a small pre-flight helper script.)

### Step 3 — Resolve config into non-interactive choices (BLOCKS LAUNCH)

Collapse every config decision the unattended run could hit into a fixed choice,
so nothing prompts at 3am:

- **Co-review reviewer set** — resolve `/co-review`'s reviewer set from
  `.co-review.yml` into the concrete list that will run under `--non-interactive`
  (bounded per-reviewer timeouts; the reviewer prompt is never asked mid-run).
  For a **time-boxed run** (`--until` set), default to a **fast reviewer set**
  (codex + Claude + reconciler): codex is ~1–2 min, while cloud reviewers
  (`devin` / `agy`) each hit the `--non-interactive` 15-min bound and dominate a
  short window. Cloud reviewers are **optional/skippable** in this set — a
  skipped reviewer is never fatal and is recorded (`REPORT.md` review classes).
  Then **compute `min_task_budget` from this resolved set** — the pre-dispatch
  floor is coupled to reviewer latency, not a constant (~20 min fast set,
  ~45 min+ with cloud reviewers; formula in
  [`references/run-budget.md`](references/run-budget.md) "Minimum task budget") —
  and write it to `RUN.md` front matter (step 6) so the run loop's pre-dispatch
  deadline guard reads a concrete number.
- **Coder config** — run `select-coder` once to resolve each task's
  `<backend>:<model>` from the capability matrix, so `orchestrate-coders`
  dispatches without prompting for a missing default.
- **Custom/local commands** are **disabled** for the run unless explicitly
  approved at this step (untrusted-config posture, matching co-review's rule).

Any decision that can't be resolved here — and would therefore prompt mid-run —
**BLOCKS LAUNCH**.

### Step 4 — Gitignore sanity check

Check the file types the tasks will produce (the plan's `related_files` and any
expected build/output artifacts) against the repo's ignore rules with
`git check-ignore`. A match — an **intended output that is git-ignored** — does
**not** block launch: an ignored directory is common and usually fine to force
through. Record that the orchestrator commits those paths with `git add -f` so
the work lands despite the ignore rule, and surface the list in the launch
summary so the human can still catch a genuinely wrong ignore (an output that
should never be committed) while awake.

### Step 5 — Record verify tooling + exercise path

Resolve the project's named check command (`dli check` → `just check` →
`scripts/check.*`, the same precedence the repo's check tooling uses) and the
end-to-end **exercise path** (how a task's feature is driven, not just its tests
— the work's definition of done). Write both into `RUN.md`'s `verify_command` and
`exercise_path` front-matter fields (format per
[`references/run-state.md`](references/run-state.md) "`RUN.md`"), so every task's
`/deliver-task` verifies the same way.

### Step 6 — Materialize the task graph into run state

Run the adapter's `list_ready` and `dependency_graph`
([`references/adapters.md`](references/adapters.md)) to build the run's task graph
and its blocker edges. Write `.auto-pilot/RUN.md` — front matter (`run_id`,
`work_source`, `base_branch`, `verify_command`/`exercise_path` from step 5, and
`min_task_budget` from step 3) plus the per-task table with each task's initial **phase** and its `base` edge (main
for an independent task, the parent's branch for a chained one) — in the exact
format defined in [`references/run-state.md`](references/run-state.md) "`RUN.md`".
Also seed empty `.auto-pilot/QUESTIONS.md` and `.auto-pilot/REPORT.md`. **Commit**
all three to the run-state branch (the first write under the run-state branch's
fixed write order). Do **not** restate the run-state formats here — they live in
that reference.

**Scout — per-task capability join (BLOCKS LAUNCH).** With the graph now
materialized and each task's coder resolved (step 3) against the environment
fingerprint (step 2), check the **demand** side the auth probes structurally
can't see: for **each** task, take the `<backend>` it routes to and confirm that
backend **exists in this environment**. A task routed to a backend absent
here — the motivating case is a `codex` task in a `claude-web` run with no
`codex` binary — **BLOCKS LAUNCH**, naming the task, the missing backend, and the
fix (install it, or re-route the task in the `select-coder` config). This is a
**deterministic** check only: it blocks solely on a provable route-vs-environment
gap, never on a guess about what a task's prose might need. (Inferring
capability demands from task _text_ — "needs a DB", "needs network" — is the
predictive scout, a warn-only follow-up; it is deliberately **not** here, so
nothing blocks launch on a speculative read.)

**v1 treats every route as _required_** — an absent backend blocks. Softening
this to **required-vs-preferred** (a _preferred_ backend that's absent warns and
falls back to the next-ranked `select-coder` spec instead of blocking, while a
_required_ one still blocks) is a planned follow-up — `select-coder` already
returns ranked specs, so the fallback order exists; the missing piece is the
require/prefer bit on the task route.

### Step 7 — Spawn the detached orchestrator

Per [`references/launch-runtime.md`](references/launch-runtime.md):

1. Write the self-contained **launch script** — env, the sandbox wrapper (the
   two-layer profile: seatbelt/bwrap for filesystem+process, the harness network
   allowlist narrowed to this run's tools for host egress), and log redirection
   to `.auto-pilot/orchestrator.log`.
2. Run the **auth smoke test through that exact sandbox wrapper + env** (not
   bare) — a failure here is a launch blocker (ties to step 2). Machine-stays-
   awake was already confirmed in the pre-flight (step 2).
3. **Detach** via the OS-appropriate primitive (`launchd`/`launchctl` on macOS,
   `setsid` on Linux) so the orchestrator outlives this session; record its
   **PID + process start-time + `--until` deadline** on the run-state branch for
   later stale-run detection (the start-time guards against a recycled PID being
   mistaken for a live run).
4. Print **where state lives** — the run-state branch name, the `.auto-pilot/`
   files, and the log path — and tell the user the run is going.

## Resume phase (--resume)

Invoked by `/auto-pilot <source> --resume` (`commands/auto-pilot.md`). Resume's
job is to reconcile a crashed or paused run's durable state against reality,
then fall into the normal **Run phase** loop below for whatever remains ready.

**Re-run only the pre-flight that can rot; skip the launch-only steps.**
Worktree + run-state-branch creation (step 1) and the **task-graph
materialization** half of step 6 already exist on the run-state branch from the
original launch, so resume does not re-create them; **source normalization
still runs** — resume must normalize `<source>` to resolve which run-state
branch it reads from, even though it never re-creates that branch. What can rot
between launch and resume, and so is re-run: the non-interactive **auth probes**
and the **environment fingerprint** (Launch step 2) — a run launched
`local-full` may resume under `claude-web`, or vice versa, so step 6's _other_
half, the scout's **capability join**, must re-run against the current
environment — and **base freshness**. As at launch, a hard failure here
**BLOCKS THE RESUME**, fail-closed the same way.

**Locate the run-state branch.** `--resume` takes a `<source>`, not a `run_id`,
but run-state branches are named `auto-pilot/<run_id>`
([`references/run-state.md`](references/run-state.md) "Run-state branch") and
nothing stops more than one run existing for the same source. After normalizing
`<source>`, enumerate the `auto-pilot/*` branches whose `RUN.md` front matter
records that source and require **exactly one** in a resumable (`active` /
`paused` / `systemic`) state. Zero matches, or more than one, is **fail-closed**:
report the ambiguous `run_id`s by name and stop rather than guess which run to
resume — the same never-guess posture the reconciliation below takes.

**Stale-orchestrator guard.** Read `orchestrator_pid` / `orchestrator_started_at`
/ `until` from `RUN.md`'s front matter
([`references/run-state.md`](references/run-state.md) "`RUN.md`"). If a live
orchestrator with the matching start-time is still running at that PID
([`references/launch-runtime.md`](references/launch-runtime.md) "Orphan / stale
detection"), do not start a second one — report it and stop. A dead PID, or a
start-time mismatch (a recycled PID), means it's safe to proceed; the
start-time is exactly what tells the two cases apart.

**Reconcile each non-terminal task.** Re-read `RUN.md` from the run-state
branch. `handed-off` and `parked` tasks are terminal and left untouched. For
every other task (`claimed` / `implementing` / `pr-open` / `in-review` /
`iterating`), observe reality in the **write order's** direction — git first,
then tracker, then run files. The freshness relation remote ≥ tracker ≥ run
files ([`references/run-state.md`](references/run-state.md) "Write order") is
exactly why that read order needs no guessing: does the task branch exist
locally / on the remote, is there an open PR for its head branch, what state
does the tracker show, is a worker worktree left behind. Match the observed
reality to a row of that reference's **crash-reconciliation table**
("Crash reconciliation", rows G1–G7) and apply that row's action, in the same
fixed write order — reconcile by that table, don't restate it here. The
load-bearing invariant that makes this decidable: `needs_review` is only ever
written at the hand-off tracker write, never the pr-open one, so a task that
crashed at `pr-open` always reconciles to `started` plus a linked PR (G5),
never to hand-off.

**Idempotency.** Resume must be safe to run repeatedly. It leans on G4's
idempotency check — an existing PR for a task's head branch is detected and
adopted, never duplicated — so re-resuming never opens a duplicate PR or
re-claims a task already in flight.

**Orphaned worker worktrees.** A crash mid-`implementing`/`iterating` (G2) can
leave a worker worktree behind; resume removes it before any re-dispatch.

**Never blind-retry.** A task whose observed reality doesn't match any
reconciliation row cleanly is set to `parked` and gets a `REPORT.md` entry
describing what was found ([`references/run-state.md`](references/run-state.md)
"`REPORT.md`") — resume never guesses or retries blindly.

**Then fall into the run loop.** Once reconciliation leaves `RUN.md` accurate,
resume continues into the **Run phase** loop below for the remaining ready
tasks; it does not re-derive that loop. If the run was paused (`status: paused`
/ `paused_until` set), resume clears those run-level pause markers before
re-entering the loop, per
[`references/run-budget.md`](references/run-budget.md) "Near-cap → pause +
relaunch past reset" — pause semantics live there, not here.

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
    pick next unblocked task (phase-based readiness)
    if until is set and now + min_task_budget > until:   # pre-dispatch deadline guard
        stop the loop cleanly (record "N left, M min to deadline, not starting")
    /deliver-task it (with per-task wall-clock + retry bounds)
    update run state on the run-state branch
    check rate-window usage
```

**Pre-dispatch deadline guard.** The budget-bounds condition alone can't protect
the `--until` deadline: `--until` is otherwise only consulted at spawn (record)
and at a paused resume's wake, so without this check a task claimed at 22:40
under a 22:45 deadline gets **hard-killed** mid-delivery, leaving a half-built
`claimed`/`implementing` task — the worst `--resume` state. So **before claiming**
each task, **when `--until` is set**, if `now + min_task_budget > until`, stop the
loop cleanly instead of starting work that can't finish, and record why in `REPORT.md` (e.g. "2 tasks
left, 12 min to deadline, not starting — resume tomorrow"). `min_task_budget` is
the reviewer-set-coupled floor resolved at launch (step 3) and read from `RUN.md`
front matter — not a constant; see
[`references/run-budget.md`](references/run-budget.md) "Minimum task budget". This
is a clean stop, not a park: the un-started tasks stay ready for a later
`--resume`.

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
/deliver-task <id> --base <branch> --handler <handler> --questions .auto-pilot/QUESTIONS.md
```

where `<branch>` is the task's `base` from `RUN.md` — `main` for an
independent task, the parent task's branch for a chained one — and `<handler>`
is the run's effective handler resolved from the run's (normalized) source
(plan ⇒ `repo-pr`, linear ⇒ `linear`), never re-derived by `/deliver-task`
itself. `/deliver-task`
([`commands/deliver-task.md`](../../commands/deliver-task.md)) owns the entire
per-task lifecycle — claim, implement, PR, co-review, iterate, hand-off — the
run loop does not re-derive any of it. The `--questions` path is where the
non-blocking decision protocol below appends entries. If a `/deliver-task` call
**fails outright** — a crash or non-zero exit, rather than a clean hand-off or
park — the orchestrator applies the per-task retry bound (one re-dispatch, then
**park**; [`references/run-budget.md`](references/run-budget.md) "Per-task retry
limit") and continues to the next ready task; a failed delivery never aborts the
loop or leaves run state half-written.

**Non-blocking decisions.** The run **never waits** on a human. For any
reversible call, the orchestrator (or `/deliver-task` beneath it) picks the
**reversible option** when uncertain, appends an indexed `QUESTIONS.md` entry
(format per [`references/run-state.md`](references/run-state.md)
"`QUESTIONS.md`"), and proceeds. Two sources feed the same log:
`/deliver-task`'s own deferred judgment calls (written via its `--questions`
path above) and orchestrator-level decisions made in this loop itself — e.g.
skip vs park, a stacked-PR base-reconciliation choice.

**Human checkpoints produce artifacts, then proceed.** When a task hits
something that genuinely needs human judgment, the run still does not block:
`/deliver-task` ensures the PR carries a working end-to-end state plus a
how-to-evaluate note, the orchestrator records the same entry in `REPORT.md`'s
_How-to-evaluate queue_, and the loop moves on. Nothing waits for a reply.

**Rolling `REPORT.md` update.** After every task's state update above, rewrite
`REPORT.md` from the current `RUN.md` + `QUESTIONS.md` state — the six
sections in [`references/run-state.md`](references/run-state.md)'s "`REPORT.md`"
order. Commit it on the run-state branch as part of that state update's commit,
under the write order's last step. Its _Spend_ section stays a one-line
pointer to [`references/run-budget.md`](references/run-budget.md) rather than
restating budget rules, which that reference owns.

**Stacked-PR handling.** Chains are processed in dependency order; a chained
task's work branch must start from the **parent's frozen tip**, which the
readiness rule guarantees is stable (the parent is already `handed-off`
before a child becomes ready). Before dispatching a chained task, the
orchestrator compares the parent branch's **current tip SHA** against the child's
recorded `base_sha` — the parent's frozen tip captured at its hand-off (per
[`references/run-state.md`](references/run-state.md) "`RUN.md`"); comparing the
`base` branch _name_ to a tip could never detect movement. If they differ, the
parent has moved since the base was frozen and the child would build on a stale
base — **park** the task instead, record it, and continue to the next ready task.

**State update after each task.** After `/deliver-task` returns, update
`RUN.md` with the task's observed `phase`, `branch`, and `pr`, then commit to
the run-state branch, following the fixed **write order** in
[`references/run-state.md`](references/run-state.md) "Write order".
`/deliver-task` already performed that order's push + tracker-write steps; the
orchestrator's remaining job here is the _run-state commit_ — the order's
last step — reconciling `RUN.md` to the phase it just observed.

After each task's state update, apply the budget checks in
[`references/run-budget.md`](references/run-budget.md). A hard-stop, a
near-cap pause, or a circuit-breaker halt writes state and exits per that
reference.

**Loop termination.** The loop ends when no ready task remains, a budget
hard-stop fires, or the **pre-dispatch deadline guard** above stops it with ready
tasks still left. The first two are a **finished run** — the final `REPORT.md`
sets run-level `status: done`. The deadline-guard stop is **not** done: it sets
`status: paused` with `pause_reason: "--until deadline reached; N tasks still
ready"` and `paused_until` **empty**, keeping the run in `--resume`'s resumable
set (those tasks stay ready for a later `--resume`) without any timer auto-wake.
In every case the orchestrator writes and commits the final `REPORT.md` on the
run-state branch, then
**tears down its relaunch supervisor** — the recurring `launchd`/`systemd` timer,
if one was registered ([`references/launch-runtime.md`](references/launch-runtime.md)
"Relaunchable, not one-shot") — so no run is re-woken by a timer (a
deadline-stopped run resumes only by an explicit `--resume`), and finally
**exits cleanly**, emitting a **one-line summary** to `.auto-pilot/orchestrator.log`
([`references/launch-runtime.md`](references/launch-runtime.md) "Logs /
observability") — e.g. `auto-pilot done: 4 handed-off, 1 parked, 0 skipped —
see REPORT.md`. Nothing is merged or tracker-completed by this exit: every
task sits at whatever phase it reached (`handed-off` on the tracker as
`needs_review`, or `started` for a `parked` task), ready for
`/sweep-for-complete` once a human has reviewed and merged.
