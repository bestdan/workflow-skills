# Probe 3 — Autonomous-safe / async-report walking skeleton

**Result: CONFIRMED — fully closed.** Under real per-user launchd, the watcher
autonomously detected both a killed and a wedged run, drove each to a **safe
terminal with no human in the loop**, and emitted a durable async notice
(authoritative registry record + human-readable Slack mirror). The 08:00 canary
is **health-coupled**: it reads healthy only when the watcher is fresh, the
broker is fresh, and no run is unsupervised — and the **load-bearing
false-positive leg passed** (run wedged **and** watcher killed → `unhealthy`,
never a host-is-up "healthy"). The falsification redirect is **not** triggered.

The kill sheet below is preserved as written (§7a rule 1: falsifier first,
fixture second). Evidence and classification follow in **Results**.

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

- Host: macOS 26.4.1 (25E253), arm64 mini, single uid (`danielegan`, uid 501).
  This probe is about the watcher/canary/notice **mechanism**, not the cross-uid
  boundary — that was closed by Probe 2.
- Supervision hosted by **real per-user launchd** (`launchctl bootstrap
  gui/501`, `StartInterval` watcher, `StartCalendarInterval 08:00` canary). The
  bootstrap/kickstart/bootout calls require running **unsandboxed**: launchd's
  mach service fails `EIO` under the command sandbox and returns `rc=0` outside
  it. Recorded so the production watcher install is known to need the same.
- Python 3.12; no `claude`, no `gh`, no production App, no network except an
  **optional** Slack POST from `SLACK_WEBHOOK_URL` (unset in this run — the
  webhook is never written to disk; §7a rule 4).
- Fixture: `run.py` (dummy heartbeat run, `setsid` leader), `watcher.py`
  (single-pass §5.1 shape), `canary.py` (coupled health), `common.py` (registry
  + notice sinks), `incarnation.py` (Probe 2's libproc `p_uniqueid` reader,
  copied), launchd templates, `scenarios.py` (orchestrator). Runtime state lived
  in disposable `$TMPDIR` rundirs; only sanitized evidence is checked in.

## Results

**Classification: CONFIRMED.** Cadences: **compressed** (`StartInterval` 5 s,
staleness 8 s, watcher-fresh 15 s) for mechanism iteration, plus a **production**
confirmation (`StartInterval` 120 s, staleness 360 s). Raw evidence:
`results.json`, `evidence-registry.jsonl`, `evidence-slack_outbox.jsonl`.

| Pass criterion | Result |
| --- | --- |
| Healthy baseline reads healthy (not trivially always-unhealthy) | ✅ `canary → healthy` with a beating run + live watcher |
| **Kill** → autonomous detect + safe terminal | ✅ `observed_terminal{stall_kill}`, runfile → `terminal`, detect **9.4 s** (compressed) |
| **Wedge** (SIGSTOP: alive but heartbeat stale) → detect + safe stop | ✅ `observed_terminal{wedge}`, wedged process **killed** (did not survive), detect **10.7 s** |
| Durable async notice | ✅ authoritative registry `observed_terminal`+`alert`; Slack mirror line per condition; dedup `run_id+condition` |
| Canary flips when run is in a bad state | ✅ wedged active run → `unhealthy [active_run_heartbeat_stale]` |
| **False-positive leg (load-bearing)**: run wedged **and** watcher killed | ✅ `unhealthy [watcher_stale, active_run_heartbeat_stale]` — coupled on **both** clauses, never host-is-up |
| launchd hosting | ✅ watcher `StartInterval` + canary `StartCalendarInterval 08:00` bootstrapped, fired, kickstarted, booted out (`rc=0`) |
| **Production cadence** detect-and-stop < 10 min SLO | ✅ kill detect **117 s** < 600 s |

### Safe-stop isolation (a real bug the fixture caught)

First wedge run took out the orchestrator: a `&`-backgrounded run shares its
shell's process group, so the watcher's `killpg` hit the supervisor too. Fixed
by making `run.py` a **session leader** (`setsid`, `pid == pgid == sid`) —
exactly the production topology Probe 2 confirmed for a tmux pane. With the run
isolated in its own group, `killpg` reaches only the run. This is why the
production run-shim must remain its own session leader; a run sharing the
maintainer's group would make safe-stop unsafe.

### launchd relaunch throttle (constraint, not a failure)

At compressed cadence a `watcher_slow` record appears every pass. Cause:
**launchd enforces a ~10 s minimum between relaunches** of a job, so
`StartInterval 5` is effectively ~10 s — which equals the `2 × interval` slow
threshold, so every gap trips it. This is an artifact of pushing cadence below
launchd's floor, **not** a skipped-pass defect: the detect timings (9.4 s /
10.7 s) are consistent with a ~10 s effective cadence, and production's 120 s
`StartInterval` clears the floor with wide margin (the 117 s production detect
confirms it). **Constraint recorded for production: keep `StartInterval` ≥ ~10 s
and the slow threshold relative to the effective cadence.**

### Async notice — Slack

The **registry is authoritative** and was exercised end-to-end; the local
`slack_outbox.jsonl` carried the human-readable mirror for every condition. A
**live** Slack POST is a present-but-opportunistic code path (`common._post_slack`,
best-effort, 5 s timeout, no retry-until-acked); it was **not** exercised here
(no webhook configured, and Slack's host is outside the command sandbox). This
matches Decision #5: delivery is un-acked and non-load-bearing — a miss is
recovered by the registry and the 08:00 canary, not by escalation.

### Not required (held out, per Decision #5)

No maintainer wake, no acknowledged/retried delivery, no sub-10-min message
*arrival*. No off-host dead-man monitor (§7a) — absence of the 08:00 canary is
the signal.

### What this closes / does not close

Closes: the unattended promise — **autonomous detection + self-safe stop +
health-coupled async self-report, no human in the loop** — holds on a real
launchd substrate without the full registry or launcher. Does **not** establish
the production registry schema, broker, lease/generation kernel (Probe 5),
runaway containment (Probe 5b), or GitHub authority (Probe 4); those remain
their own probes. Spike code is disposable and is never promoted by renaming
(rule 4).
