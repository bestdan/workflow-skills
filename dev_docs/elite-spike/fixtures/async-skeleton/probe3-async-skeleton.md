# Probe 3 — Autonomous-safe / async-report walking skeleton

**Result: PENDING.** Kill sheet only — no fixture code written yet. Fixture is
built only after this sheet is approved (§7a rule 1: falsifier first, fixture
second, production component last).

Disposable spike under §0a's contract. All fixture code that lands beside this
file is **never** promoted into `/usr/local/autopilot` by renaming (rule 4). No
real credentials, no `claude` launch, no `gh`, no production App. The Slack
webhook (a secret) is **never persisted**: the durable async record is a local
sink; a real Slack POST is exercised opportunistically from an env-provided
webhook that is never written to disk (§7a rule 4).

Exercises Decision #5 (async notification, autonomous safety), §4.3 (heartbeat),
§5.1 (watcher notification contract), and the 08:00 canary as primary health
signal.

## Kill sheet (from §7a, priority 3)

### Key assumption / falsifier

> _The unattended promise — **autonomous detection + self-safe stop + honest
> async self-report, no human in the loop** — works without the full registry or
> launcher._

Falsified if any load-bearing leg below fails and cannot be recovered by
changing only cadence/threshold constants (a mechanism failure, not a tuning
one).

### Method (walking skeleton — minimal, no full registry/launcher)

A trivial **run** (a heartbeat-touching loop process with a durable runfile) and
a **watcher** (a per-user launchd job, `StartInterval`, single short-lived
`lockf -t 0` pass per §5.1) and an **08:00 canary** (a launchd
`StartCalendarInterval` job whose health is *coupled*, not a host-up ping).

Inject, in a disposable directory:

1. **Kill** — SIGKILL the run's incarnation. Watcher must detect the dead
   incarnation and drive to a safe terminal record.
2. **Wedge** — freeze the run so its heartbeat goes stale past the 6-minute
   threshold (§4.3) while the process still exists. Watcher must detect
   staleness and drive to a safe stop.
3. **Canary flip** — with the run dead/wedged, the 08:00 canary must report
   **absent/unhealthy**, not healthy.
4. **Load-bearing false-positive leg** — with the run wedged **and the watcher
   itself killed**, the canary must **NOT** report "healthy." Health must be
   *coupled* (gated on a fresh watcher pass / no run past its stop deadline /
   broker fresh), never a bare host-is-up ping.

Cadence note: production constants are watcher `StartInterval` 120 s, staleness
6 min, SLO 10 min. To iterate within the time cap the mechanism is exercised at
a **compressed cadence** (documented in the evidence), **plus one confirmation
run at production constants** showing real-time detect-and-stop < 10 min. The
pass is about the mechanism and the cadence *arithmetic*, not re-deriving the
constants.

### Pass threshold

All of:

- **Detect-and-stop < 10 min (host running)** on both kill and wedge — the
  watcher detects and drives the run to a **safe terminal/paused** state with
  **no human in the loop**. Verified at production cadence at least once.
- **Durable async notice**: the **registry record is authoritative** (append,
  dedup key `run_id+condition`); the **local Slack sink** carries the
  human-readable mirror; a real Slack POST succeeds opportunistically when a
  webhook is present (best-effort, un-acked).
- **Canary flips**: run dead/wedged → 08:00 canary reports **absent/unhealthy**.
- **False-positive leg (load-bearing)**: run wedged **and** watcher killed →
  canary is **not** "healthy."

### Not required (explicitly out of contract, Decision #5)

Waking the maintainer; acknowledged / retried-until-acked delivery; sub-10-min
*message arrival* (the 10-min bound is detect-and-stop, not delivery). An
off-host dead-man monitor is **explicitly not a probe** (§7a) — absence of the
08:00 canary is the signal.

### Inconclusive condition (rule 3 — not a fourth "nearly done" state)

Classify **inconclusive**, not pass, if any of:

- Per-user launchd cannot host the watcher/canary in the fixture, so results
  can't be attributed to the real launchd boundary rather than a hand-rolled
  loop.
- The kill/wedge cannot be injected deterministically, so detect-and-stop timing
  can't be attributed to the watcher rather than the harness.
- The canary's healthy vs unhealthy verdict can't be distinguished from a
  sink/harness artifact (e.g. can't separate "coupled-unhealthy" from "sink
  down").

A repeat requires a changed kill sheet naming the new evidence/method (rule 6).

### Evidence required (rule 4)

Checked in beside this file: the fixture command/test; sanitized raw evidence —
registry records and the kill→detect→stop timestamps for both injections, canary
output in **both** the healthy case and the wedged-**and**-watcher-dead case,
and the launchd plists used; non-secret env metadata; result; decision. **Never
persisted**: the Slack webhook, any token, any secret env value.

### Time cap

One working day — an explicit operator override of §7a rule 3's half-day
default (the probe row states none; the cap is set here). At the cap, stop and
classify `confirmed` / `falsified` / `inconclusive`; "nearly done" is not a
fourth state.

### Dependent work gated on this probe

The entire **unattended operation path**: watcher (§5.1), heartbeat tripwire
(§4.3), 08:00 canary, and the async-notice sink. Until Probe 3 is `confirmed`,
unattended scope stays blocked.

### Redirect if falsified

Stop unattended work; change **launch context, watcher primitive, cadence,
safe-stop mechanism, or the durable record sink** and rerun this probe. Do not
build watcher/broker hardening around a self-report model that can't fail
closed on its own.

## Environment (non-secret)

_To be filled from the fixture run._

## Results

_To be filled from the fixture run._
