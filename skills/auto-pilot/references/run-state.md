# Auto-pilot run state — reference

The canonical formats and invariants for an auto-pilot run's **durable state**.
The launch, run, and resume flows all read and write these; this file is the
single definition so none of them restate a format. Formats and invariants
only — no orchestration prose.

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
| `.auto-pilot/REPORT.md`    | The rolling human-facing report the user wakes to.                                                                                                  |

### `RUN.md`

Front matter records the run's fixed parameters; a table records per-task state.

```markdown
---
run_id: 2026-07-08-auto-pilot-mode # bare id; the run-state branch is auto-pilot/<run_id>
work_source: linear:d0598803-… # linear:<projectId> or plan:<dir>
base_branch: main
verify_command: dli check # the named check (design pre-flight §5); the PINNED command the un-jailed verify broker runs (launch-runtime.md §5) — resolved once at launch, never agent-composed
exercise_path: "drive /co-review --non-interactive on a scratch PR" # end-to-end check
status: active # run-level: active | paused | systemic | done
paused_until: # ISO time the orchestrator may resume past a rate-window pause; empty unless status is paused
pause_reason: # why the run paused/halted; set with status=paused (rate window) or status=systemic (circuit breaker)
exit_reason: continuing # why the orchestrator last exited: continuing | paused | done | systemic | deadline (see "Exit contract" below)
exit_reason_at: 1783823504 # epoch seconds the reason was declared — how the supervisor tells THIS wake's declaration from a stale one
exit_reason_detail: "context exhausted mid-task T-3" # optional one-line human note
orchestrator_pid: 48213 # the spawned orchestrator's PID (launch step 7)
orchestrator_started_at: "Wed Jul  9 20:00:00 2026" # its process start-time — guards a recycled PID (launch-runtime "Orphan / stale detection")
until: 2026-07-10T06:00:00 # the run's --until deadline
min_task_budget: 20m # pre-dispatch floor, computed from the resolved reviewer set (launch step 3; run-budget.md); only written for a time-boxed (--until) run
---

| task    | phase        | branch            | base              | base_sha | pr   | notes                  |
| ------- | ------------ | ----------------- | ----------------- | -------- | ---- | ---------------------- |
| PRE-459 | handed-off   | bestdan/pre-459-… | main              | —        | #140 | 2 co-review rounds     |
| PRE-460 | implementing | bestdan/pre-460-… | bestdan/pre-459-… | a1b2c3d  | —    | chained on PRE-459 tip |
```

- `base` encodes the dependency edge for a stacked task: `main` for an
  independent task, or the parent task's branch for a chained one.
- `base_sha` is the parent branch's **frozen-tip SHA**, recorded when the parent
  reaches `handed-off`. A chained child's stacked-PR check compares the parent
  branch's _current_ tip against this recorded SHA to detect a moved base (→ park);
  empty (`—`) for an independent task, whose `base` is `main`. **This guard
  models one specific actor: the ORCHESTRATOR moving a base mid-run** — it is
  never meant to fire on a **human** reviewing/merging a parent PR, which moves
  the parent's tip by design every time (finding #25's clarification: _"there is
  always my review in between hand-off and merging. Files being changed should
  be an expected part of the process."_). Do not park on that divergence — see
  "Restack" below, which is the actual remedy: a human merge is the trigger,
  never the error, and out-of-order/orphaned stacks are fixed mechanically
  rather than by asking a human to merge bottom-up from memory (run #1's
  finding #14, closed by automation, not by prose).
- `status` / `paused_until` / `pause_reason` are the **run-level** fields the run
  loop writes: `status: paused` + `paused_until` (+ reason) at a rate-window pause,
  `status: systemic` (+ reason) when the circuit breaker halts, `status: paused`
  (+ reason, `paused_until` empty) when the pre-dispatch deadline guard stops with
  ready tasks left — resumable only by an explicit `--resume`, never a timer — and
  `status: done` at a clean end-of-run (no ready tasks or a budget hard-stop).
  `status: paused` is **required** on every pause, not optional — but by itself
  it is not enough to exempt the no-progress guard (task 23): `status: paused`
  is the agent's own write, and the guard exists to catch exactly the agent
  that is wedged. The guard's ONE exemption rule requires `status: paused`
  backed by a **parseable, still-live `paused_until`** — a missing, garbage, or
  expired-past-its-margin `paused_until` does not exempt, so a wedged agent
  that declares `paused` on every wake with no bound still trips the guard.
- `exit_reason` / `exit_reason_at` / `exit_reason_detail` are the **exit
  contract** the supervisor reads to decide relaunch-vs-teardown — see below.
- `orchestrator_pid` / `orchestrator_started_at` / `until` are the operational
  record launch writes at spawn (launch-runtime.md "Orphan / stale detection");
  `--resume` and a fresh launch read them to detect a stale orchestrator.
- `min_task_budget` is the **pre-dispatch floor** the run loop's deadline guard
  reads: `now + min_task_budget > until` → stop before starting a task the
  `--until` kill would sever mid-delivery. Present only when `until` is set — a
  run with no `--until` has no deadline to guard. Launch step 3 computes it from
  the resolved co-review reviewer set (it is reviewer-latency-coupled, not a
  constant) and writes it here; formula and defaults live in
  [`run-budget.md`](run-budget.md) "Minimum task budget".
- `phase` is one of the seven in-flight/terminal values below, or the pre-claim
  `pending` marker (see "Task lifecycle phases"); of those, only the seven
  in-flight/terminal values are what `--resume` reconciles (a `pending` task has
  no in-flight transaction to reconcile).

### Exit contract — why the orchestrator stopped

A single `claude -p` orchestrator does **not** finish a whole run. It works until
it runs out of turn/context, then exits **cleanly** with tasks still pending, and
a fresh one picks up from this branch. That is fine — but it made the system's
most important distinction invisible:

> **"I exited because I finished the run"** and
> **"I exited because I ran out of context mid-task"**
> were the _same observable event_: `exit 0`.

Everything downstream was guessing: the supervisor relaunched on a **blind timer**
because it could not tell them apart, and a human reading `exit code = 0` could not
tell whether the run was done or dead. So the orchestrator **declares** its reason —
`spawn-orchestrator.sh exit-reason --dir <run worktree> --reason <r> [--detail …]` —
**before** it exits, on **every** termination path. The reason is written to
`RUN.md`'s front matter and committed to the run-state branch (it must outlive the
process; a local file would be invisible to `--resume` and to a human reading the
branch).

| reason       | means                                             | supervisor does                |
| ------------ | ------------------------------------------------- | ------------------------------ |
| `continuing` | work remains, context exhausted                   | **relaunch**                   |
| `paused`     | rate window / `paused_until` set                  | **relaunch** past the reset    |
| `done`       | no ready tasks remain                             | **tear down**                  |
| `systemic`   | circuit breaker / fatal auth / failed invariant   | **tear down + alarm**          |
| `deadline`   | pre-dispatch guard stopped with tasks still ready | **tear down**; `--resume` only |

The supervisor reads that reason **in shell, with no model call**
(`supervisor-check`, launch-runtime.md "Relaunchable, not one-shot") and acts on
it instead of relaunching on a timer. Three rules keep it honest:

- **Declaring beats inferring.** The agent knows why it stopped; `classify-exit`'s
  exit-code+log inference is the **fallback** for a wake that declared nothing (a
  hard-killed agent, an older prompt).
- **Only THIS wake's declaration counts.** The reason lives on the branch, so it
  outlives the wake that wrote it — `exit_reason_at` is compared against the wake's
  start. Without that, a wake that declared `continuing` and was then killed by a
  dead credential would have its stale reason out-vote the fatal classification,
  reviving finding #22's 52-relaunch loop through a durable file.
- **Fail safe.** An unknown, missing, or garbage reason never tears a live run
  down — it falls back to inference. And a `fatal` auth classification halts
  regardless of what was declared: over-halting is the safe direction.

The three terminal reasons also drop the **done-sentinel**
(`.auto-pilot/orchestrator.done`) — the **same single file** that `teardown
--done-sentinel` writes and `status` reads (launch-runtime.md "Logs /
observability"), never a second marker. It carries `reason: <r>`, which is what
keeps `done` and `systemic` distinguishable inside one file. The orchestrator never
boots the `launchd` job out itself: it is jailed, and exec of `launchctl` is denied
by construction. Writing the sentinel is its half; the supervisor, outside the jail,
does the bootout.

### Heartbeat — telling _slow_ from _wedged_

`.auto-pilot/heartbeat` is a timestamp the orchestrator touches
(`spawn-orchestrator.sh heartbeat --dir <run worktree> --note <where>`) at **each
loop iteration** and **each `/deliver-task` sub-step boundary**; the launch wrapper
also beats it at the top of every wake, so a `claude` that wedges before its first
iteration still leaves an ageable timestamp. `status` ages it against the per-task
ceiling (`--task-ceiling`, default 2700s = 45m) and reports `healthy` or a
**STALL** — "last heartbeat 40 minutes ago, per-task ceiling is 45m" is a
distinction **no other signal in the system can make**: to an exit code, a PID, and
a log tail, a slow task and a hung one are identical.

It is deliberately **not committed** to the run-state branch: it beats many times
per task and would drown the run's durable record in churn. Like `supervisor-state`
(the supervisor's own no-progress counter) it is wake-local liveness, not part of
the run's record — which is exactly why the **exit reason**, which _is_ part of the
record, goes in `RUN.md` instead.

### Restack (post-merge stacked-PR repair)

Squash-merging a parent orphans a chained child two ways: **loudly** (GitHub
deletes the parent's branch → the child, still based on it, gets closed) or
**quietly** (the child still targets the parent's _branch_, so merging it lands
on that branch, never on `base_branch` — the PR looks healthy while doing
nothing). Finding #25 hit the loud case for real, on a P1 PR. `restack`
(`scripts/spawn-orchestrator.sh restack --run-dir <dir> --repo <path>`) reads
this table's `base`/`base_sha`/`pr` columns and, for every chained task whose
parent PR has merged, runs the incantation a human would otherwise have to
remember at the exact right moment: fetch, `rebase --onto <base_branch>
<base_sha> <branch>` (dropping the parent's now-squashed commits), `push
--force-with-lease`, `gh pr edit --base <base_branch>` — in dependency order,
idempotent, and fail-closed on a conflict (aborts, reports, never
force-pushes). It also flags — as a defect for `REPORT.md`, and with a **non-zero
exit** so a supervisor sees it — an orphaned child (a PR whose live base is a
merged or deleted branch), a **closed** child (LOUD-orphaned; never force-pushed,
left for a human to reopen), and a child that was rebased and pushed but whose
`gh pr edit --base` **failed** (the QUIET case: it still points at the dead
branch). Cascades are **resumable across runs**: when a parent was rewritten by
an earlier run, a later run rebases the grandchild onto the parent's current
**remote** tip rather than relying on in-memory state — a partial run never
silently strands a deeper child. All of this rather than waiting for a human to
notice by diffing the open-PR list by hand.

Two invariants the implementation holds, both worth stating because they are
easy to get wrong:

- **Restack never moves the run worktree's HEAD.** `git rebase --onto X Y
  <branch>` _checks out_ `<branch>`, so a restack run in the run worktree would
  park the orchestrator's HEAD on a task branch — finding #23 exactly, the thing
  the run-HEAD guard exists to prevent. Every rebase therefore happens in a
  throwaway worktree, removed on success, conflict, and push-rejection alike,
  and the caller's HEAD is asserted unchanged on exit. Restack also fails closed
  on a dirty or mid-rebase caller worktree rather than rebasing over it.
- **Force-pushing a parent rewrites it, so its own children must cascade.** When
  a chain is deeper than one link, restacking a parent onto `base_branch` leaves
  the _grandchild_ carrying the parent's old, now-rewritten commits. The
  grandchild is rebased onto the parent's **new tip** (its PR base stays the
  parent branch — retargeting it to `base_branch` would re-propose the parent's
  whole changeset). Without this, the run that fixes orphaned children orphans
  one itself.

**A human merging the parent is restack's normal trigger, never an error** —
see the `base_sha` note above. What restack must NOT treat as proof of
correctness: **a clean rebase**. The child was co-reviewed against the
**pre-review** parent; if the parent's post-hand-off review commits touched a
file the child also touches, a clean auto-merge can silently drop or
contradict a fix the reviewer just added. So every restacked child is a
**re-verification trigger**, not just a git operation:

1. **Re-run verify** against the new base — the child's previous green ran
   against the old one.
2. **Diff-audit the child against the parent's post-hand-off review commits**:
   confirm no line those commits added is removed or contradicted by the
   child's own changes.
3. **Flag the child's co-review as stale** whenever the parent changed during
   human review, and re-run co-review on the child if the parent's review
   touched files the child also touches — the child's existing approval refers
   to code that no longer exists.

**Status: the mechanism is enforced; the re-verification is not (yet).** The
git/GitHub mechanics above — rebase, force-push, retarget, orphan-detect,
fail-closed, HEAD invariant — are implemented and covered by tests.
`restack` **announces** the three requirements above into `REPORT.md` per
restacked child (so a human cannot miss that a child's green and its co-review
are both stale), and emits the exact commands copy-pasteably. But **nothing
executes them**: re-running verify, diff-auditing the child against the
parent's review commits, and re-running co-review are orchestrator-layer
actions the run loop must take, and today they are a documented contract, not
an enforced one — the same "a rule with no enforcement is a comment" pattern
this reference warns about elsewhere. Wiring them into the run loop /
`/deliver-task` is the follow-up that closes it.

The pre-flight should also warn when a stacked run's repo has
`delete_branch_on_merge: true` — that setting is what turns a recoverable
restack into an already-closed PR.

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
A deferred co-review finding that was also filed as a tracked follow-up (see
`REPORT.md` "Follow-ups" below) references the created task id in its entry.

### `REPORT.md`

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
7. **Follow-ups** — the index of every co-review finding filed as a tracked
   task this run: task id, source PR, and the one-line finding. A finding is
   auto-filed via `/add-task` (tagged `auto-pilot`) when it is deferred **and**
   either **cross-cutting** (its faithful fix would touch a file outside the
   task's `related_files`, or change a spec/section another consumer cites) or
   **still open at the hard 2-round co-review bound** — this is the index of
   what got filed, not an alternative to filing it: every entry here was both
   listed and delivered to the tracker. Guardrails against unattended spam:
   dedupe within the run (one follow-up per underlying finding/spec, not one
   per occurrence), and cap auto-filed follow-ups at **5 per run** — excess
   findings are recorded here as a plain bullet (task id `none`) instead of
   filed. Auto-filed tasks land in the tracker's triage/new state, never
   `ready`: `/promote-tasks`' human-confidence gate still has to clear them
   before they enter work, so a hallucinated finding is caught before it costs
   anything. If `/add-task` itself fails, the run does not fail — the finding
   is recorded here as a plain bullet (task id `none`) instead.

## Run-state branch

The run files live on a **dedicated branch**, distinct from every task branch:

- **Name:** `auto-pilot/<run_id>` — e.g. `auto-pilot/2026-07-08-auto-pilot-mode`.
  It is created at launch and never merged into `main`.
- **Why separate:** bookkeeping commits (`RUN.md`/`QUESTIONS.md`/`REPORT.md`
  updates after every unit) must never land on a task branch, or every task PR
  would carry unrelated run-state churn. Keeping them on their own branch means
  task PRs contain only their code, and a dead orchestrator still leaves a
  complete, committed record to resume from.

## Run worktree HEAD invariant

**The run worktree's `HEAD` stays on the run-state branch `auto-pilot/<run_id>`
for the entire run.** Task code is written in a **separate worker worktree**
(`/deliver-task` creates and removes its own — `commands/deliver-task.md`); the
orchestrator never runs `git checkout <task-branch>` in the run worktree
itself. Finding #23: an orchestrator that instead switches the run worktree
onto a task branch to do the work, then switches back to commit run state,
_works_ — task_2 and task_3 both shipped that way — which is exactly why it's
dangerous: the run loop and `--resume` both assume the run worktree **is** the
run-state branch checkout, since that's where `.auto-pilot/RUN.md` lives ("The
three files" above). A crash while `HEAD` is parked on a task branch —
precisely when a crash is most likely, since that's when the real work
happens — leaves `--resume` reading a stale or absent `RUN.md`, and
uncommitted task-branch edits can block the checkout back, wedging recovery.

**Guard.** Each run-loop iteration, and the top of `--resume`
([`resume.md`](resume.md)), calls
`scripts/spawn-orchestrator.sh assert-run-head --dir <run-worktree> --run-id
<run_id> --questions .auto-pilot/QUESTIONS.md`. It asserts `git rev-parse
--abbrev-ref HEAD` in the run worktree equals `auto-pilot/<run_id>`; if not, it
restores that branch and appends a `QUESTIONS.md` entry recording the
deviation (format above) — a run that finds itself on the wrong branch has
already violated its recovery contract and must not silently continue. That
entry reaches `REPORT.md`'s **Decisions** section through the normal rolling
rewrite; no separate `REPORT.md` format exists for it. It fails closed when the
run worktree is **dirty** at the deviation (restoring would silently carry or
lose the uncommitted task-branch edits) or when git itself refuses the restore —
a **clean** deviation the guard can repair is never a reason to halt an
otherwise-recoverable run.

## Task lifecycle phases

`phase` spans all materialized tasks, but only the seven **in-flight/terminal**
values below — from the moment a task is claimed to its terminal state —
participate in crash reconciliation. A task materialized into the graph but not
yet claimed carries the pre-claim marker `pending` instead; it is not one of
those seven and is never a target of the crash-reconciliation table (there is
nothing mid-transaction to reconcile before a claim exists). Which `pending`
tasks are **eligible to claim next** — graph readiness — is not encoded in a
`pending` task's own `phase`; it is computed from the graph edges and the
blockers' phases by the adapter's `list_ready`/`dependency_graph` verbs
([`adapters.md`](adapters.md)).

| Phase     | Meaning                                                                     | Tracker                                                      | Git / remote | Worker worktree |
| --------- | --------------------------------------------------------------------------- | ------------------------------------------------------------ | ------------ | --------------- |
| `pending` | Materialized into the graph, not yet claimed; readiness computed separately | new / materialized (plan `new`\|`ready`; linear `unstarted`) | no branch    | none            |

Seven in-flight/terminal phases follow, once a task is claimed. Each names
exactly what exists on the tracker, in git, and on disk while a task sits in
it — which is what makes the reconciliation table below decidable.

| Phase          | Meaning                                                                 | Tracker                    | Git / remote                              | Worker worktree |
| -------------- | ----------------------------------------------------------------------- | -------------------------- | ----------------------------------------- | --------------- |
| `claimed`      | Claim held via the handler protocol; base chosen; no code yet           | claimed / started          | task branch may exist locally, no commits | none            |
| `implementing` | Worker dispatched; diff being integrated + verified                     | started                    | local commits possible, **not pushed**    | may exist       |
| `pr-open`      | Code pushed and PR opened (draft or ready)                              | started                    | branch pushed, PR open                    | removed         |
| `in-review`    | `/co-review --non-interactive` running                                  | started                    | PR open                                   | none            |
| `iterating`    | Applying review fixes, re-pushing (≤ 2 rounds)                          | started                    | PR updated                                | may exist       |
| `handed-off`   | **Terminal (success):** PR linked, tracker at `needs_review`, PR frozen | needs_review               | PR open, linked                           | none            |
| `parked`       | **Terminal (blocked):** couldn't proceed or reconcile; needs a human    | started (+ reason comment) | whatever existed at the stall             | removed         |

`handed-off` is the success terminal — completion itself is verified later by
the source's own completion path (`/sweep-for-complete` for a `linear` source;
repo-pr's native merge-derived signal — the merged code PR — for a plan source),
never by the run. In-run dependency readiness keys off
`handed-off`, not tracker completion. The tracker state `needs_review` here means
**co-review is already complete** (it ran during `/deliver-task`); the remaining
gate is a **human** reviewer/merger — the automated review is not what
`needs_review` is waiting on.

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

The **"update tracker" step is phase-specific**, which is what makes a crash's
reconcile action depend on _which_ transition it interrupted:

- at the **pr-open** transition it **links the PR** and leaves the status
  `started` (review hasn't happened yet);
- only at the **hand-off** transition — after co-review and iterate — does it set
  `needs_review`.

So `needs_review` is never written before review; a crash at pr-open reconciles
to `started` + a linked PR, not to hand-off.

## Crash reconciliation

One row per write-order gap (a crash landing between two steps of the order).
Because the tracker write is phase-specific (above), the rows are ordered by
which transition the crash interrupted — the pr-open tracker write (G5) links
the PR and stays `started`, while `needs_review` is written only at the hand-off
tracker write (G6). `--resume` matches the on-disk reality to a row and applies
its action; anything that doesn't match cleanly is set to `parked` with a
`REPORT.md` entry rather than blindly retried.

| #  | Crash point                                                         | Observed reality                                               | Reconcile action                                                                                                  |
| -- | ------------------------------------------------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| G1 | After **claim**, before any implementation                          | tracker claimed; no branch, no PR                              | verify the claim is still ours; resume `implementing`, or release + `parked` if the claim was lost                |
| G2 | Mid-`implementing`/`iterating`, worker worktree left behind         | orphan worker worktree; commits maybe unpushed                 | remove the orphan worktree; re-verify; re-dispatch from a clean base, or `parked`                                 |
| G3 | After local commit, **before push**                                 | local commits ahead of remote; tracker `started`; no/stale PR  | push the branch, then continue to the open/refresh-PR step                                                        |
| G4 | After **push**, before PR opened/refreshed                          | remote branch exists; **no (or stale) PR**; tracker not linked | idempotency-check for an existing PR by head branch; open/refresh it if absent; then do the pr-open tracker write |
| G5 | pr-open transition: after PR opened, **before tracker link**        | PR open; tracker still `started`, PR not linked                | **link the PR; keep the status `started`** (review hasn't run); phase → `pr-open`; commit run state               |
| G6 | hand-off transition: after review+iterate, **before tracker write** | PR open and reviewed; tracker still `started`                  | set `needs_review` + ensure the PR is linked; phase → `handed-off`; commit run state                              |
| G7 | After **any tracker write**, before **run-state commit**            | tracker current (any status); `RUN.md` phase stale             | recompute the phase from tracker + git; commit run state                                                          |

G4's idempotency check is what makes resume safe to run repeatedly: a re-push or
a second launch never opens a duplicate PR, because an existing PR for the head
branch is detected and adopted.

## Run doctor

Findings #22 and #23 both hit a live run and **both presented as a clean
`exit 0`** — the 401 loop exited 0 fifty-two times; the vanished-run-state exit
was `exit 0`, `terminal_reason: completed`. Neither tripped the circuit
breaker, because the circuit breaker counts _delivery_ failures and neither of
these was one. The run never asked "am I still a valid run?" — `doctor`
(`scripts/spawn-orchestrator.sh doctor --dir <run worktree> --run-id <run_id>
[--label <launchd label>] [--questions <path>] [--handler repo-pr|linear]
[--gh <path>] [--no-progress-limit N] [--context loop|resume]`) is that
question, asked seven ways, run at the **top of every run-loop iteration**
(`--context loop`, the default) and at the **top of `--resume`**
(`--context resume`) — a run's world can drift between wakes just as easily
as within one. It is cheap, deterministic, and needs no model call. Every
invariant below has a stated **repair** or a **halt** — none may be silently
ignored, and a **halt** exit means the loop must not dispatch.

**Exit codes**, a caller gates the run loop on these:

| Exit | Meaning                                                                         |
| ---- | ------------------------------------------------------------------------------- |
| `0`  | every invariant holds, or was repaired/parked — the loop may proceed            |
| `30` | **HALT** — an invariant demanded `status: systemic`; the loop must NOT dispatch |
| `2`  | bad usage, or an unrepairable fail-closed condition (`die`)                     |

**A doctor HALT tells a human — through the jailed seam, never `alarm` directly.**
Doctor runs **inside** the jail, where `osascript` is exec-denied, so it files an
**`alarm-request`** (condition `invariant`, reason naming _which_ invariant
failed) and the **un-jailed supervisor delivers it** — `supervisor-scan` drains
requests on every wake, and `supervisor-check` drains them right after the agent
exits, i.e. in the same wake the halt happened in ("The alarm",
[`run-budget.md`](run-budget.md)). Calling `alarm` from in-jail would be strictly
**worse than silence**: the notification is denied by the sandbox, but `alarm`
still writes the `ALARM` **sentinel**, and the supervisor's own `status: systemic`
scan goes quiet whenever that sentinel exists ("already announced") — the denied
attempt would gag the one channel that can actually reach a human. The generic
systemic scan is the backstop, but only the request carries the **diagnosis**:
which invariant failed. `--resume`'s `alarm-clear` therefore runs **before** the
doctor, never after ([`resume.md`](resume.md)) — clearing afterwards would delete
the request the doctor just filed.

**The seven invariants**, each with its violation remedy:

1. **Run worktree `HEAD` is on `auto-pilot/<run_id>`.** → **repair**, delegated
   entirely to `assert-run-head` (task 13): it asserts, restores, and records the
   deviation itself. Before delegating, doctor checks whether a HEAD deviation's
   dirt is confined to `.auto-pilot/`: that content is never authoritative on a
   task branch (the run-state branch is the run's single memory), so doctor
   discards TRACKED `.auto-pilot/` changes there and lets `assert-run-head`
   restore HEAD normally, recording the discard as its own repair. UNTRACKED
   `.auto-pilot/` content — a real run's own `orchestrator.log`,
   `verify-broker.log`, and doctor's own `doctor-state` — is **never**
   `git clean`ed away (that would destroy the run's only forensic record); it
   is instead tolerated via `assert-run-head --ignore-untracked-run-state`
   rather than treated as blocking dirt, since `git reset`/`git checkout`
   cannot discard untracked files in the first place. Dirt outside
   `.auto-pilot/` still fails closed, verbatim — this is what stops invariants
   1 and 2 from deadlocking on exactly the state they exist to repair (a
   parked HEAD with a deleted `RUN.md`).
2. **`RUN.md` / `QUESTIONS.md` / `REPORT.md` are readable _from the branch_**
   (`git show <branch>:<path>`) **and `RUN.md`'s front matter parses** (`run_id` +
   `status`). → A failed branch read, or front matter that doesn't parse, is a
   **halt** (`systemic`): the run has no memory, so it must not guess. A
   working-tree-only loss, where the branch is intact, is instead a **repair** —
   `git checkout <branch> -- .auto-pilot/`. Reading the working tree rather than
   the branch is precisely what let finding #23 continue into a stateless void.
3. **Every task at `pr-open` / `in-review` / `iterating` / `handed-off` has a PR
   that actually exists.** → An **open** PR holds. A **merged** PR also holds — a
   human merging post-hand-off is the expected, healthy end state, never a
   violation, and a doctor that "repaired" it would be fighting the human. A
   **positively read** closed (unmerged) PR, or a phase claiming a PR with no
   number/unparseable cell recorded → **repair**: phase → `parked`, never a
   silent re-dispatch. A gh call that returns a **non-zero exit code**
   (401, rate limit, network blip) is **undetermined**, never a positive
   "the PR is gone" signal — parking on it would park every in-flight task on
   one transient gh hiccup (the same 401 shape as finding #22). An
   undetermined read is left alone and counted `skipped` in the summary, not
   `parked`; the PR cell may be a bare number, a `#`-prefixed number, or the
   markdown-link form RUN.md itself writes (`[#188](https://…/pull/188)`) — one
   shared parser (`_pr_number`, also used by `restack`) handles all three.
4. **Every `handed-off` task carries its handler's review signal.** → For
   `repo-pr` that signal is the PR itself: labeled `task-loop` (not `task-claim`)
   and not a draft, because deliver-task's step 7 makes the ready `task-loop` PR
   the review signal (the task file is deleted in the PR rather than flipped to
   `needs_review`). A G6/G7 crash gap — still `task-claim`, still draft — is a
   **repair**: swap the label, `gh pr ready`. Each of those two `gh` **writes**
   has its exit code checked, and only a write that actually succeeded is
   reported as a repair — a gh blip that still recorded "I4 repaired" would be
   the same silent lie invariant 5 forbids for a failed `worktree remove`; a
   failed write is announced and left for the next pass, which re-reads the
   still-stale state. Skipped for a merged PR (labels no
   longer matter). Other handlers stay behind `--handler` as future work.
5. **No orphan worker worktrees from a dead dispatch** (G2). → **repair**, remove
   them — but only when removal is provably safe: the worktree is under
   `<run root>/workers/`; its `RUN.md` row is at a phase that is unambiguously
   TERMINAL for that worktree (`parked` / `handed-off`), **or** no row matches
   the worktree's branch _and_ the run's orchestrator is **provably dead** (see
   below); it has no uncommitted changes; its branch tip is already pushed to
   `origin` **or** carries no commits beyond its base; and no **OPEN** PR is
   recorded for it. `pending` is deliberately **not** on the safe list: RUN.md's
   phase cell is written by a commit the orchestrator makes **after** it
   dispatches, so there is a window — right after each RUN.md commit+push —
   where a worker worktree is genuinely live (dispatched, possibly with an open
   PR already) while its row still reads `pending`. `pending` therefore can't
   distinguish "never dispatched" from "dispatched moments ago," so it is never
   safe to prune on its own.

   Each of those git reads (branch, uncommitted changes, pushed-ness) must
   itself **succeed** — a non-zero exit is **undetermined**, not the safe
   value an empty result would otherwise read as (a failed `status
   --porcelain` is not "clean"; a failed `rev-parse HEAD` is not "no
   commits"; a failed `rev-parse --abbrev-ref HEAD` is not "unmatched"). An
   undetermined read fails this invariant closed to `skipped (undetermined)`,
   same D2 posture as the liveness check below — "provably safe" means the
   reads came back positive, not merely that git didn't error.

   **An UNMATCHED worktree is not self-evidently abandoned either** — and
   treating it as such was a data-loss bug. The orchestrator writes a task's
   `branch` / `phase` / `pr` cells back only **after** `/deliver-task` returns
   ([`../SKILL.md`](../SKILL.md) "State update after each task"), so for the
   whole of a live dispatch the row reads `| t | pending | - | … |` and matches
   **nothing** — while a freshly-created worker worktree is clean, carries no
   commits beyond its base, and has no PR yet, i.e. satisfies every other
   condition above. (A worker left on a **detached HEAD** lands in the same
   bucket: its `rev-parse --abbrev-ref HEAD` reads back the literal `HEAD`,
   which no row's `branch` cell can equal.) An unmatched worktree is therefore
   pruned **only when the run's orchestrator is provably dead** — nothing can be
   mid-dispatch if the process that dispatches is gone. That is the same
   stale-orchestrator machinery `--resume` gates on ([`resume.md`](resume.md)
   "Stale-orchestrator guard"; [`launch-runtime.md`](launch-runtime.md) "Orphan /
   stale detection"): `RUN.md`'s `orchestrator_pid` + `orchestrator_started_at`,
   where a **recycled** pid (start-time mismatch) counts as dead but an
   **undetermined** read — no pid recorded, `ps` unreadable — does **not**, and
   fails closed to `skipped`, the same D2 posture as invariants 3 and 6. This
   duplicates `resume.md`'s own guard on purpose: a `git worktree remove
   --force` must not depend for its safety on a step ordering that no code
   enforces.

   Anything failing one of those conditions is left alone and reported
   `skipped (unsafe to prune)`; a `git worktree remove` that itself fails is
   reported as a **failed** prune, never as a completed repair — leaving an
   orphan worktree behind is harmless, deleting a live one destroys work, so
   every condition must hold, not most of them.
6. **A chained task's parent tip still equals its frozen `base_sha`.** → **park**
   the child — but only when the parent's own PR is _positively read_ as _not_
   merged. The `base_sha` freeze guard models the **orchestrator** moving a base
   mid-run (see "`base_sha`" above), never a **human** merging the parent; that
   case's remedy is `restack`, and doctor says so instead of parking. When the
   parent's merge state **can't be determined** — `gh` isn't resolvable at all,
   or a `gh` call fails — doctor fails closed toward **not** parking (same
   posture as invariant 3): a guess is no better than a stale read, and parking
   a child whose parent actually merged is exactly the violation this invariant
   exists to avoid. Reported `skipped (parent state unreadable)`, not parked.
7. **The run made forward progress since the last doctor iteration.** → After
   `--no-progress-limit` (default 3) consecutive no-progress iterations, **halt**
   (`systemic`). Skipped entirely while the run is legitimately paused
   (`_run_is_paused`). Also **reset** (never incremented) when doctor is called
   with `--context resume`: doctor runs once at the top of `--resume` and again
   at the top of the first loop iteration, with the run HEAD necessarily
   unchanged between the two calls — incrementing on the resume call would put
   the counter at 2 before any work is even attempted, one strike from a
   spurious halt. A resume is a fresh start by definition, not a stalled
   iteration.

**Ownership split with task 10's supervisor guard** (say it here because it is
easy to conflate): `supervisor-check`'s no-progress guard owns no-progress
**across wakes** — process-level, keyed on the run-state HEAD not moving
between launchd relaunches (the agent process itself died or hung). Doctor's
invariant 7 owns no-progress **across iterations within one live agent
process** — the agent is alive and looping, but nothing advances. Different
scopes, different state files (`supervisor-state` vs. `doctor-state`, both
under `.auto-pilot/`, neither committed to the run-state branch — wake/
iteration-scratch, not durable record) — the two can never both halt the same
run for the same reason.

**Composition, not reimplementation.** Invariant 1 calls `assert-run-head`
rather than re-deriving the HEAD check; invariant 2 reads via `git show`, the
same belt `--resume` already uses; invariants 3–6 read/write `RUN.md`'s table
through the one shared parser (`_restack_read_run_md`, extended with the
`phase` column); every halt goes through `_supervisor_halt` (now `--label`-
optional, so a bare `--resume` with no launchd job registered yet still halts
correctly — it just skips the teardown step, since there is nothing loaded to
boot out).

**Reporting.** One line to stdout per run, e.g. `spawn-orchestrator: doctor: 7
invariants — ok=5 repaired=1 (I1: HEAD restored) parked=1 (I6: task_20)
halt=0 skipped=0` — a stall is visible in the log without parsing stream-json.
The five counters (`ok`/`repaired`/`parked`/`halt`/`skipped`) are **per
invariant**, not per task row or per worktree: each of the seven invariants
contributes at most one bucket increment, so the total across all five never
exceeds 7, however many task rows or worktrees an invariant happened to touch.
Per-row/per-worktree detail (which task, which worktree) still appears
parenthetically, sourced from the same repair/park/skip notes. `skipped`
covers the two **undetermined** cases above (invariants 3 and 6, a `gh` call
that could not be positively read) — neither a pass nor a violation, just
unknown this pass. Every **repair** and **park** also appends a dated bullet
to `REPORT.md` (a run that had to repair itself is a signal even when it
recovers) and, when `--questions` is passed, an entry in that decision log in
the same format as `assert-run-head`'s.
