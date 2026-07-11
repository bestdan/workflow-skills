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

- **Primary — a direct usage query.** [`scripts/claude-usage.sh`](../../../scripts/claude-usage.sh)
  performs it and is the orchestrator's entry point: it queries `GET
  https://api.anthropic.com/api/oauth/usage` (headers `Authorization: Bearer
  <token>` and `anthropic-beta: oauth-2025-04-20`) and emits the session
  window as compact JSON (`--session-percent` for just the number). The
  `<token>` is the Claude Code OAuth access token, resolved OS-appropriately —
  the macOS Keychain (`security find-generic-password -s "Claude
  Code-credentials" -w`) or, on Linux, `~/.claude/.credentials.json` — and read
  as the `.claudeAiOauth.accessToken` field. The response's `limits[]` array
  carries one entry per window (`kind` of `session` — the 5-hour rate window —
  `weekly_all`, and `weekly_scoped` (per-model, carrying a `scope`), each with
  `percent` and `resets_at`); the rate-window read is
  `limits[kind=session].percent` plus its `resets_at`. **`percent` is percent
  _consumed_, not remaining** — it rises toward 100 as the window is spent, so
  "near cap" fires as it **approaches** the threshold (real headroom is
  `100 - percent`); don't invert the comparison. This is first-best: a
  **structured numeric read** of true usage, not a guess.
- **Fallback — a conservative time/dispatch proxy, when the query is
  unavailable.** Not every orchestrator environment can make the query above —
  a `claude-web` orchestrator has no local credential store, and a non-Claude
  backend may expose no equivalent usage endpoint at all. Any such failure —
  a missing token store, a token read that fails or is denied, an unreachable
  endpoint, or an unexpected response shape — makes
  [`scripts/claude-usage.sh`](../../../scripts/claude-usage.sh) **exit
  non-zero**, which is the signal to **fail closed to** elapsed wall-clock and
  dispatch count against a threshold tuned **below** the real cap, crossing it
  → "near cap" (below). The script treats the OAuth token as a secret — it is
  passed to `curl` via a stdin config, never on the command line, and never
  logged; a caller wiring the query directly must do the same.
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

## A third terminal kind — the supervisor halt (finding #22)

The two pause kinds above are not the whole story: both assume the failure is
**retryable** — waiting (or a token bucket resetting) fixes it. An expired
OAuth credential is not retryable. In detached run #2, the launching user's
Anthropic OAuth credential expired mid-run; every subsequent `claude -p` turn
died on `401 Invalid authentication credentials`, and the supervisor —
knowing only "retry later" — relaunched into the **same** 401 on every one of
its ~52 wakes, over **4.3 hours**, doing zero work and raising zero signal.
The circuit breaker above didn't catch it (the process died before it could
dispatch a task, so it never counted as a _delivery_ failure), and the
rate-limit backstop's premise — "back off, the window resets" — is simply
**false** for a dead credential.

So the supervisor carries a **third** kind, neither an agent pause nor a
supervisor pause: a **supervisor halt**. It is implemented entirely in shell,
before or on `claude -p`'s exit — a rate-limited or auth-dead agent cannot run
its own bookkeeping, so the supervisor decides in shell what it can decide in
shell (`scripts/spawn-orchestrator.sh classify-exit` / `supervisor-check`,
called by the generated launch script after every wake):

- **Fatal, non-retryable** — the exit's captured stdout+stderr names an auth
  failure (`401`, `authentication_failed`, `Invalid authentication
  credentials`, `OAuth token has expired`): halt **immediately**, on the
  first occurrence.
- **Unknown/unclassified repeated failure — the no-progress guard.** Even
  without a string match, N consecutive supervisor wakes (default 3) that
  exit non-zero with **no run-state commit** between them (the run-state
  branch's HEAD hasn't moved) halt too. This is the general backstop that
  would have caught #22 even if its 401 text had never matched — the run
  making no forward progress is the actual signal, the 401 string is just the
  fast path to the same conclusion. A wake that lands while RUN.md's own
  `status:` is `paused` never counts against this guard — a paused wake makes
  no progress **by design** (the orchestrator is deliberately waiting out
  `paused_until`; see task 11's shell-level pause gate), not a stall.

A supervisor halt writes run-level `status: systemic` + a `pause_reason`
naming the failure to `RUN.md`, appends one alarm entry to `REPORT.md`,
commits both to the run-state branch, and **tears the supervisor job down**
(`launchctl bootout`) — the same `status: systemic` terminal the circuit
breaker and the rate-limit crash-loop guard above reach, arrived at from a
third direction. Unlike those two, nothing about a supervisor halt is
resumable by a timer: only a human fixing the underlying condition (typically
re-authenticating) and then an explicit `--resume` moves the run forward
again.

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

## Minimum task budget — the pre-dispatch floor (reviewer-set-coupled)

The wall-clock limit above is the **ceiling** that parks a runaway task; this is
the **floor** — the smallest wall-clock a single `/deliver-task` can plausibly
need to reach `handed-off`, used by the run loop's **pre-dispatch deadline
guard** ([`../SKILL.md`](../SKILL.md) "Run phase (unattended)"). Before claiming
the next task, **if a `--until` deadline is set**, the loop checks
`now + min_task_budget > until`; if so it stops
cleanly rather than starting work a hard `--until` kill would sever mid-delivery,
leaving a half-built `claimed`/`implementing` task to `--resume` — the worst wake
state.

`min_task_budget` is **not a constant**. A single delivery is coder dispatch +
independent verify + PR + co-review (bounded **per reviewer**) + up to 2 iterate
rounds, and the co-review term is dominated by the **resolved reviewer set's**
latency:

- **Fast set** (codex + Claude + reconciler): codex is ~1–2 min, stateless and
  sandboxed; the floor is ~**20 min**.
- **With cloud reviewers** (`devin` / `agy`): each hits the `--non-interactive`
  **15-min bound**, so they dominate a short run and push the floor to ~**45 min+**.

As an approximation, `min_task_budget ≈ delivery overhead (coder implement +
verify + PR, ~18 min) + co-review latency`, where the co-review term is the
**slowest per-reviewer bound in the resolved set** carried across the initial
review plus up to 2 iterate rounds: a ~2-min codex term keeps the fast-set floor
near **20 min**, while a 15-min cloud-reviewer bound pushes it past **45 min**.

Because it is coupled to that set, `min_task_budget` is **computed at launch from
the resolved reviewer set** (Launch step 3) and recorded in `RUN.md`'s front
matter ([`run-state.md`](run-state.md) "`RUN.md`"), so the pre-dispatch guard
reads a concrete number rather than re-deriving it each iteration. A time-boxed
run defaulting to the fast reviewer set is exactly what keeps this floor small
enough that short windows can still fit a task.

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
