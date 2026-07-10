# Auto-pilot run budget — reference

How the detached, unattended orchestrator sees its own resource headroom and
stops before it overruns. Bounds and rationale only — the run loop that calls
these checks lives in [`../SKILL.md`](../SKILL.md) "Run phase (unattended)",
and the state formats (`parked`, write order) in
[`run-state.md`](run-state.md).

**Scope.** v1 is **rate-window discipline + per-task bounds only** — no
`--budget` dollar/token cap. The one hard money rule is the paid/overflow
stop below; everything else governs the free rate window and per-task time.

## Rate-window check

Run after every task's state update (the run loop's hook point). Headroom is
read two ways, layered because neither alone is enough:

- **Primary — a direct usage query.** `GET
  https://api.anthropic.com/api/oauth/usage`, called with `Authorization:
  Bearer <token>` and `anthropic-beta: oauth-2025-04-20`, where `<token>` is
  the Claude Code OAuth access token read from the macOS Keychain
  (`security find-generic-password -s "Claude Code-credentials" -w`, then the
  `.claudeAiOauth.accessToken` field of the JSON it prints). The response's
  `limits[]` array carries one entry per window (`kind` of `session` — the
  5-hour rate window — `weekly_all`, and `weekly_scoped` (per-model, carrying
  a `scope`), each with `percent` and `resets_at`); the rate-window read is
  `limits[kind=session].percent`
  plus its `resets_at`. This is first-best: a **structured numeric read** of
  real remaining headroom, not a guess. `~/src/dotfiles/scripts/claude_usage.sh`
  is prior art for the exact call.
- **Fallback — a conservative time/dispatch proxy, when the query is
  unavailable.** Not every orchestrator environment can make the query above
  — a `claude-web` orchestrator has no macOS Keychain, and a non-Claude
  backend may expose no equivalent usage endpoint at all. When the query
  can't run, fall back to elapsed wall-clock and dispatch count against a
  threshold tuned **below** the real cap, crossing it → "near cap" (below).
- **Backstop — a rate-limit _error_, classified by the supervisor, not the
  agent.** A rate-limit denies exactly the capability an in-band handler would
  need: if the orchestrator _itself_ is rate-limited, its reasoning can't run to
  checkpoint and pause. So the backstop lives in the **outer supervisor** that
  wraps `claude -p` (the relaunchable supervisor from
  [`launch-runtime.md`](launch-runtime.md) "Spawn mechanics" — "Relaunchable, not
  one-shot"): with **no model call** it inspects the process's exit code / stderr
  for a rate-limit signal (429 / `rate_limit_error` / overloaded) and owns the
  reschedule. The agent never self-handles a rate-limit — if a subagent dispatch
  returns one, it simply exits non-zero and lets the supervisor classify it.

The same response's `spend.used.amount_minor` feeds the hard-stop below
("Hard-stop before paid/overflow credits") — one query serves both checks.

**Why a usage query or a real error, not CLI-output scraping.** _Parsing a CLI's
human-readable usage text_ stays rejected: it is unversioned UI that drifts
silently across releases. The usage endpoint above is a **structured JSON
query**, not scraped text — that, and a real rate-limit error (authoritative
backstop), are both things the orchestrator can trust in code; a scraped
console string is not.

**Caveats, stated rather than hidden:**

- **Strands headroom on light nights.** The proxy fallback will sometimes
  pause a run that had capacity left. This is the deliberate safe direction
  — pausing early wastes idle time; pausing late risks the wall.
- **The direct query is account-wide, closing the proxy's blind spot.** The
  usage endpoint reports the account's actual consumption regardless of who
  drove it, so it stays accurate if a human uses the same account mid-window.
  The proxy fallback does not: it has no visibility into usage outside its
  own dispatches, so treat it as a lower bound on real remaining headroom,
  not an exact reading, whenever the direct query isn't available.

## Near-cap → pause + relaunch past reset

When the proxy crosses its near-cap threshold, or the error backstop fires:

1. **Write state first**, per [`run-state.md`](run-state.md) "Write order" —
   including `paused_until: <reset>` and the pause reason — then **exit**.
   This is a **checkpoint-then-exit**, not an in-process sleep: the process
   holding hours of context does not sit and wait, it dies and lets a
   relaunch reconstruct everything from the run-state branch. When the
   triggering signal carries **no explicit reset time** — a rate-limit error
   frequently won't — fall back to a default pause of **1 hour** so
   `paused_until` is always a schedulable timestamp, never empty.
2. A relaunch past `paused_until` starts a **fresh** process. Per
   [`launch-runtime.md`](launch-runtime.md) "Spawn mechanics" ("Relaunchable,
   not one-shot"), a relaunchable supervisor (macOS `launchd` timer/`KeepAlive`,
   Linux `systemd` timer/`at`) wakes the job past the reset rather than the
   process itself sleeping through it.
3. **Guard the wake.** Before resuming work, re-check the current time
   against the recorded `--until` deadline (don't wake at 6:00 into a 6:05
   deadline — there's nothing left to do) and re-verify the orchestrator's
   PID + start-time record so a stale or double-launched process can't run
   concurrently with the fresh one.

**Why exit rather than sleep.** The run-state branch is the orchestrator's
only memory; nothing else needs to survive a pause. A process that exits is
always safe to kill — crash mid-wait just means the relaunch timer still
fires. An in-process `sleep` holding hours of accumulated context is not
safe to kill, and buys nothing a relaunch doesn't already give for free.

**Two pause kinds — who writes the checkpoint.** The proxy pause above is an
**agent pause**: the orchestrator still has headroom, so it writes both
`paused_until` and `status: paused` itself and exits 0, and the supervisor
relaunches past the reset. A rate-limit backstop is a **supervisor pause**: the
agent died
rate-limited and wrote nothing this cycle, so the _supervisor_ records the pause
on the run-state branch — a minimal marker (reason, timestamp, `attempt`, and a
`resume_at` it computes mechanically: the error's `retry-after` if present, else
the top of the next window) — and reschedules. The relaunched fresh process reads
that marker first, treats the last in-flight task as dirty and reconciles it
(that reconciliation is `--resume`'s job — PRE-465), then clears the marker and
continues. This is why "the branch is the only memory" still holds under a
rate-limit: the agent is simply **not its only writer**.

**Crash-loop guard.** The supervisor never relaunches immediately on a rate-limit
exit — a limit that recurs at once would spin a tight relaunch loop. It backs off
exponentially keyed on the marker's `attempt` (e.g. 30 min → 1 h → 2 h, capped
~6 h), reset on any successful agent cycle. After N consecutive supervisor pauses
(default 4) it stops relaunching, sets run-level `status: systemic` with the
rate-limit reason, and surfaces the alarm in `REPORT.md` — the same stalled-run
terminal the circuit breaker reaches, arrived at from the resource side.

## Hard-stop before paid/overflow credits

The absolute stop, distinct from the pause above. "Paid/overflow" means
spend **beyond the plan's included rate-window allotment** — real money —
as opposed to exhausting the free window, which only triggers a pause.
The orchestrator never silently spends real money to keep a run going:
hitting this bound writes the final report and exits cleanly (the run loop's
end-of-run exit path in [`../SKILL.md`](../SKILL.md) "Run phase (unattended)"),
rather than pausing for a relaunch. There is no
relaunch-past-this-reset, because there is no reset to wait for — only a
human raising the budget can resume.

## Per-task wall-clock limit — 45 min default → park

A **cooperative** bound, not preemption. `/deliver-task` runs in-process, so
there is no external timer killing it mid-step; instead it self-checks
elapsed time at sub-step / worker-dispatch boundaries and parks the task
(phase `parked`, per [`run-state.md`](run-state.md) "Task lifecycle phases")
if the bound is exceeded at a checkpoint. The real long pole is the
**killable** coder dispatch inside `orchestrate-coders` — that dispatch, not
the orchestrator's own bookkeeping, is what can actually run away. 45
minutes is a stated default, overridable per run.

## Per-task retry limit — 1 re-dispatch → park

A failed delivery gets **one** re-dispatch; a second failure parks the task.
This is distinct from `/deliver-task`'s own co-review **iterate** bound
(≤ 2 rounds, per [`run-state.md`](run-state.md) "Task lifecycle phases"
`iterating`): iterate rounds are re-work on a delivery that is still making
progress through review, while this retry bound covers a delivery that
**failed outright** and is being re-dispatched from scratch.

## Paid-agent dispatch cap per run

A per-run ceiling on how many times a paid coder-agent backend can be
dispatched. At the cap:

- Re-run `select-coder` for each remaining task with the paid backend
  **masked**.
- A viable free routing scores above the task's confidence bar → dispatch
  there, annotated `downgraded: paid cap` in the run state / decision log.
- No viable free routing → **park** the task as blocked-on-budget.

**Why mask-and-rescore, not a hard stop.** Hitting the paid cap says
nothing about tasks that were always free-eligible; hard-stopping the whole
run at the cap would punish those tasks for a policy that doesn't apply to
them. Re-scoring per task keeps the run moving on everything the cap
doesn't actually block.

## Run-level circuit breaker

**N consecutive genuine failures (default 3) → halt the run with run-level
`status: systemic`** (per [`run-state.md`](run-state.md) "`RUN.md`"), plus one
clear alarm entry in `REPORT.md`, instead of continuing to the next ready task.
Count only **genuine delivery failures** — a crashed/non-zero `/deliver-task`, or
a task parked because its verify never passed — toward the breaker. **Exclude
expected, graceful parks** (a moved-base mismatch, or a paid-cap park with no free
routing): those are healthy run outcomes, not a signal that something systemic is
wrong, and counting them would halt a perfectly good run.

**Why.** The per-task bounds above cap one runaway task each, but nothing
caps _systemic_ failure — a broken `main`, a dead network, expired `gh`
auth — where every task the loop tries parks for the same underlying
reason. Left unchecked, the run would cheerfully burn the whole night
parking task after task, each eating up to 45 minutes of window, and wake
the human to a graveyard of parked tasks with no single signal pointing at
the real cause. Halting after a small consecutive-failure streak is cheap
to implement and turns that graveyard into one legible alarm.
