# Baseline launch/lease/registry state machine (Probe 5 input) — **v5 (redirect taken)**

v5 is the **redirect** four review rounds pointed to (`coreview-2026-07-22.md`,
`prior-art-research.md`): the flat-file crash kernel (v1–v4, in git —
`982370f`→`bc47b1b`) is **falsified**; it was hand-rebuilding a transactional
database + a process-ownership kernel from files, advisory locks, PIDs, and repair
rules. v5 stands the same *semantics* on proven substrate:

- **SQLite** (bundled/pinned stock build) for all authoritative state — replaces
  the registry, `seq.head`, lease files, tail-reconciliation, and the atomic-file
  publish protocol.
- **launchd `KeepAlive`** for one supervisor job — replaces the lock-as-liveness
  beacon (a canonically broken pattern).
- **Dedicated `agent` uid** (already provisioned, Probe 1) + `kill(-1)` for
  termination/containment — replaces the pgid/sid session-signal machinery.

The **bespoke** remainder is the point of the probe: the lease/generation/takeover
state machine, incarnation identity, and **startup reconciliation**. *Redirect
taken ≠ replacement validated* — this draft is what the v5 fixture falsifies.

## Threat model (unchanged)

Tested: **process-crash** (`SIGKILL`). Reboot/power-loss: **inconclusive** unless
a real power-fail harness (VM/loopback) runs — SIGKILL can't prove durability, for
SQLite the same as for flat files (the win is inheriting SQLite's crash-VFS test
corpus, not a hand-built one). Hostile **uid-changing** escape (a helper/XPC under
another credential) is out of scope — the uid boundary contains one credential.

## Authoritative state — one SQLite database (`state.db`)

Opened once with `PRAGMA journal_mode=WAL; synchronous=FULL; fullfsync=ON;
busy_timeout=…`. **Bundle a known-good stock SQLite** — do not rely on the Apple
system build (some Apple builds have historically mapped `F_FULLFSYNC` →
`F_BARRIERFSYNC`; treat only a pinned build as durable). SQLite serializes writers;
every transition is `BEGIN IMMEDIATE` … `COMMIT` — one transaction that allocates
the sequence, enforces idempotency, moves the lease/generation, and appends the
event **atomically**, so "seq committed but state not published" is structurally
impossible.

```sql
-- one row per repo; the lease + current generation live here
lease(repo_key PK, generation, run_id, manifest_digest, gen_token,
      state CHECK(state IN ('prepared','active','terminal')),
      stop_intent INT DEFAULT 0, updated_seq)
-- explicit gapless global counter (NOT autoincrement — failed inserts gap it)
meta(k PK, v)            -- meta['seq'] is the monotonic sequence high-water
-- append-only event log; idempotent by construction
events(seq PK, kind, repo_key, generation, run_id, payload,
       wall_ts, mono_ts, boot_session, writer,
       UNIQUE(repo_key, generation, kind, idem_key))
-- run + worker identities for a generation
incarnations(repo_key, generation, role, pid, p_uniqueid, start_tvsec,
             start_tvusec, exe, pgid, sid, boot_session,
             PRIMARY KEY(repo_key, generation, role, p_uniqueid))
```

`append_event()`: within the transaction, `UPDATE meta SET v=v+1 WHERE k='seq'`,
read it back, insert the event with that `seq`. Gapless, monotonic, exactly-once
durable **recording** (not exactly-once worker *execution* — idempotent side
effects remain the caller's job).

## Supervisor & containment (no locks)

- **One launchd-managed supervisor** (maintainer LaunchAgent, `KeepAlive`).
  launchd answers "who restarts the supervisor"; it does **not** re-adopt the
  supervisor's former child processes — the restarted supervisor reconciles them
  itself (below).
- **Liveness by parenthood + reconciliation, not a lock.** While alive, the
  supervisor is the run's parent and learns of exit via `wait()`/kqueue
  `NOTE_EXIT`. If the supervisor dies, launchd restarts it; the new instance owns
  no live children, so any `active` lease is by definition unsupervised.
- **Termination = uid-wide, bounded, verified.** The `agent` uid is exclusive to
  the worker cohort. Reap drives, as the agent uid: `kill(-1, SIGTERM)` → bounded
  wait → `kill(-1, SIGKILL)` → **re-scan until zero processes of the uid remain**.
  This converges regardless of `fork`/`setsid`/pgid games (no escape *within* the
  uid) and retires the pgid-reuse / kill-TOCTOU / fork-churn hazards. Incarnation
  identity (`p_uniqueid`+start+exe+boot_session) is used to *verify* a specific
  process (the graceful fast path and "is the run alive?" checks), not to select
  the kill target. **Caveat, stated:** uid-wide kill is not per-repo/per-generation
  — for the single-agent-per-host baseline that means "stop all agent work," which
  is the accepted contract; it does not contain a credential-changing helper.

## State machine

Each transition is one `BEGIN IMMEDIATE` transaction over `state.db`.

```
LAUNCH (fresh):
  BEGIN IMMEDIATE; require lease.state ∈ {absent, terminal}    ◀── no clobber
    g := lease.generation + 1
    append_event(generation_reserved, g, gen_token)
    upsert lease{g, gen_token, state=prepared, run_incarnation=∅}
  COMMIT
  spawn run under the agent uid, carrying gen_token, into its own session,
    BLOCKED on a start-gate; it registers its incarnation, then waits
  BEGIN IMMEDIATE; insert incarnations(run); upsert lease{state=active} COMMIT
  open the start-gate ── run proceeds (heartbeat via wait()/kqueue) ──
  terminate (stop / natural / reap):
  BEGIN IMMEDIATE; require state=active ∧ generation=g
    VERIFY run+workers dead (reap drives them dead, uid-wide) ◀── before terminal
    append_event(observed_terminal); upsert lease{state=terminal}
    append_event(lease_release)                               ◀── idempotent by UNIQUE
  COMMIT
```

**Startup reconciliation (runs on every supervisor (re)start — the core bespoke
logic):** for each `lease` not `terminal`:
1. Is the recorded run incarnation **alive** (by `p_uniqueid`, not bare pid)?
2. **Not alive** → the run crashed → drive the recovery table (drive-dead is a
   no-op; terminalize + release).
3. **Alive but not my child** → the *supervisor* died and left an orphan → **reap**
   (uid-wide TERM→KILL→verify-zero), then terminalize + release. (We never resume
   an orphan — no unsupervised run persists; Decision #5.)
4. `prepared` with no run → `launch_aborted`; terminalize. Any `stop_intent` set
   → resume TERM→KILL→verify, then terminalize.
Because launchd restarts the supervisor within seconds, "supervisor died, workers
alive" is detected and safe-stopped automatically, no human, no liveness lock.

**Takeover** (`--take-over`, human): reconcile/reap generation *g* first (drives
live gen-g workers dead, terminalizes), **then** `BEGIN IMMEDIATE` publish `g+1`.

## Invariants

1. **Atomic transitions.** A crash mid-transition rolls back (SQLite ACID); the DB
   is always at a complete prior or complete next state — never torn, never a
   half-applied seq/lease/event. (This is SQLite's guarantee; the fixture *tests*
   that we use one transaction per transition, i.e. never split a transition.)
2. **Gapless monotonic seq**, exactly-once durable recording via the explicit
   counter + `UNIQUE` idempotency; a duplicate transition is inert (no-op insert).
3. **No clobber** of a live/non-terminal lease.
4. **Earned release**: `terminal`/`lease_release` only after run+workers **verified
   dead** (uid re-scan returns zero); a `claimed_exit` never releases.
5. **Orphan safe-stop, no human.** Supervisor death → launchd restart →
   reconciliation → uid-reap → terminalize. Every crash/contention outcome is a
   safe terminal or a safe hold needing no mid-run human. (Alive-but-hung run: the
   watcher's stall detection — Probe 3 — triggers reap; the supervisor is alive so
   it reaps directly.)
6. **Reap convergence**: uid-wide TERM→KILL→re-scan terminates at zero agent-uid
   processes, defeating `fork`/`setsid`/pgid escape and fork churn; a
   uid-*changing* helper is out of scope (detected, not contained).
7. **Generation monotonic + fenced** in `lease` (durable HWM); superseded events
   inert; no ABA.

## Fault-injection matrix (v5 — the irreducible target)

| # | Injection | Required outcome |
|---|---|---|
| T1–Tn | SIGKILL at **every** transition boundary (before/mid/after each COMMIT) | DB at complete prior or next state; no torn seq/lease/event (SQLite rollback) |
| Idem | replay a transition (duplicate idem_key) | inert; no duplicate event, no double state move |
| Sup | **supervisor SIGKILL with live workers** | launchd restarts → reconciliation → uid-reap (verify zero) → terminal; no human |
| Rec | crash **during** reconciliation, repeated | idempotent re-entry; converges to terminal; stop_intent resumed |
| Race | concurrent LAUNCH vs LAUNCH, and LAUNCH vs TAKEOVER | `BEGIN IMMEDIATE` serializes; exactly one proceeds; other fails closed |
| Tw | takeover with **live gen-g workers** | reap gen g (verify zero) before publishing g+1 |
| Pid | PID reuse during a liveness check | `p_uniqueid` incarnation identity rejects the reused pid |
| Esc | run child `fork`/`setsid`-escapes its session | uid-wide `kill(-1)` still reaps it; re-scan→zero |
| Churn | runaway fork churn during reap | TERM→KILL→re-scan loop converges to zero |
| Io | ENOSPC / EIO / disk-full on the DB | transaction fails atomically; no partial state; surfaced |
| Uid | (documented limitation) worker spawns a helper under a **different uid** | **not contained** → detected + reported (needs VM/container = containment option 2) |
| PL | **reboot / power-loss** (VM/loopback harness) | complete prior/next state after power return — else classified **inconclusive** |

## Falsification redirect (from v5)

If the SQLite/launchd/uid assembly still cannot give one recoverable outcome
(e.g. reconciliation can't distinguish orphan-alive from healthy, or uid-reap
can't converge), the remaining redirect is **containment option 2** (workers in a
Linux VM / container boundary, where cgroup kill + a namespace make tree-kill and
power-fail testing first-class) — accepting the added weight. Do not return to the
flat-file kernel.

## New dependency

**Bundled/pinned stock SQLite** (not Apple's system build). Flagged per repo
convention; approved as the redirect substrate.
