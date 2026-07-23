# Baseline launch/lease/registry state machine (Probe 5 input) — **v5.1**

v5.1 applies codex's fifth-pass fixes to the v5 redirect (`coreview-2026-07-22.md`
§Fifth pass), which found the **redirect sound — no architectural pivot** — but
named a bounded spec pass. v1–v4 (flat-file, falsified) and v5 are in git. This is
the **draft the fixture falsifies** (see `v5-fixture-brief.md`).

Assembly: **SQLite** state machine + **launchd** supervisor + **dedicated-`agent`-
uid** containment. Bespoke: lease/generation/takeover semantics, `p_uniqueid`
incarnation identity, and startup reconciliation. *Redirect taken ≠ validated.*

## Threat model (unchanged)

Tested: process-crash (`SIGKILL`). Reboot/power-loss: **inconclusive** without a
VM/loopback harness. A **uid-changing** helper (a subprocess under another
credential) is out of containment scope — see §Convergence.

## Authoritative state — one SQLite `state.db` (supervisor is the ONLY writer)

Bundled/pinned stock SQLite (not Apple's system build); `PRAGMA journal_mode=WAL;
synchronous=FULL; fullfsync=ON; busy_timeout=…`. One `BEGIN IMMEDIATE` transaction
per transition; SQLite serializes writers.

```sql
lease(repo_key PK, generation, run_id, manifest_digest, gen_token,
      state CHECK(state IN ('prepared','active','terminal')),
      stop_intent INT DEFAULT 0, updated_seq)
meta(k PK, v)                              -- meta['seq'] = explicit gapless counter
events(seq PK, idem_key, kind, repo_key, generation, run_id, payload,
       wall_ts, mono_ts, boot_session, writer,
       UNIQUE(idem_key))                   -- idempotency, not INSERT OR IGNORE
incarnations(repo_key, generation, role, pid, p_uniqueid, start_tvsec,
             start_tvusec, exe, boot_session,
             PRIMARY KEY(repo_key, generation, role, p_uniqueid))
```

**`append_event(idem_key, …)`** — inside the transaction: **first** `SELECT` any
event with this `idem_key`; if present, assert identical payload and return its
`seq` (no allocation — a duplicate must **not** consume a sequence number); else
`UPDATE meta SET v=v+1 WHERE k='seq'`, read it back, insert. A `UNIQUE(idem_key)`
violation **rolls the whole transaction back** (never `INSERT OR IGNORE`, which
would gap the seq). Gapless, monotonic, exactly-once durable *recording* (not
exactly-once side effects — those stay the caller's job).

**Sole writer.** The agent-uid run **never** opens `state.db`. It reports its
incarnation to the supervisor over an **inherited authenticated pipe** (write end
handed to the child at spawn); the supervisor independently reads the identity and
persists it. SQLite connection fds are `O_CLOEXEC` / never inherited across spawn.

## Supervisor, privilege & containment

- **One launchd `KeepAlive` supervisor** (maintainer uid), sole `state.db` writer.
- **Privilege path (provisioned, not root).** The supervisor is non-root and thus
  cannot spawn-as or signal the agent uid directly. Spawn and reap go through a
  **scoped `sudo -u agent` helper** (exact-command sudoers, maintainer→agent —
  the Stage-2 sudoers already in the design), so both run **as the agent uid**.
  **Never `root` `kill(-1)`** (its target set is everything) — reaping always runs
  *as* the agent uid, whose `kill(-1)` hits only that uid's processes.
- **Termination = uid-wide, bounded, verified** (as the agent uid): `kill(-1,
  SIGTERM)` → bounded wait → `kill(-1, SIGKILL)` → **re-scan the uid until zero
  remain**. Converges past `fork`/`setsid`/pgid escape (no escape *within* the
  uid). `p_uniqueid` *verifies* a specific process; it does not select the kill.
- **One globally-active lease (admission gate).** Because `kill(-1)` is uid-wide,
  the baseline permits **at most one non-terminal lease across all repos** at a
  time: a fresh LAUNCH requires zero non-terminal leases anywhere. (Generalization
  for concurrent repos = the global barrier in §Stop; out of baseline scope.) This
  makes uid-wide kill correct — it only ever affects the one active run.

## State machine (each step = one `BEGIN IMMEDIATE` txn, except the reap saga)

```
LAUNCH (fresh):
  txn: require ZERO non-terminal leases anywhere (admission gate); g := lease.gen+1
       append_event(generation_reserved); upsert lease{g, gen_token, state=prepared}
  spawn run AS agent uid (via sudo helper), inheriting {report-pipe, gate-pipe},
       carrying gen_token; the child reports its incarnation on report-pipe, then
       BLOCKS reading gate-pipe
  txn: read incarnation from report-pipe; insert incarnations(run); lease{state=active}
  write "go" on gate-pipe ── run proceeds ──
```

**Start-gate rule (closes the DB↔spawn windows):** the child treats **EOF on
gate-pipe as "exit without executing," never as "go."** So if the supervisor dies
before writing "go" (owner `prepared` or `active` with a blocked child), the child
exits on EOF; and any child still alive is caught by the uid-scan in reconciliation.

**STOP / TERMINATE / REAP — a saga, NOT one transaction (fixes "reap inside txn"):**
1. `txn`: set `lease.stop_intent = 1` (commit).
2. **outside any txn**, as the agent uid: `kill(-1)` TERM → wait → KILL → **re-scan
   until the uid has zero processes**.
3. `txn`: only after verified-zero — `append_event(observed_terminal)`;
   `lease{state=terminal}`; `append_event(lease_release)` (idempotent).
On **non-convergence** (rescan never reaches zero — uninterruptible proc, external
privileged spawner): remain in `stop_intent`, **retry next pass + alert, never
terminalize**.

**Startup reconciliation (every supervisor (re)start — re-adopt ratified):**
For the (≤1) non-terminal lease, **always uid-scan first**, then:
- **Re-adopt** iff the recorded run incarnation is **alive and `p_uniqueid`-verified**,
  `stop_intent==0`, not stalled (Probe 3), **and** the uid-scan shows no
  *unexpected* extra agent processes: register `EVFILT_PROC/NOTE_EXIT` by pid,
  **then re-read `p_uniqueid`** to close the attach/PID-reuse race, and resume
  monitoring (observation, **not** parenthood — never `wait()` a nonchild). A
  benign supervisor restart therefore does **not** kill healthy work.
- **Reap** (the saga above, terminalize only after uid-scan == zero) iff: the run
  is dead **or** identity fails **or** `stop_intent==1` **or** stalled **or** the
  uid-scan finds live processes the recorded run doesn't account for (a run that
  died after daemonizing descendants — "dead run ≠ dead workers": terminalization
  is gated on uid==zero, **never** on the recorded run's liveness alone).
- `prepared` with a gated child: EOF the gate (child exits) + reap + verify zero →
  `launch_aborted`; terminal.

**TAKEOVER** (`--take-over`, human): reap the current generation first (saga,
verify zero), then `txn` publish `g+1/prepared`. Under one-globally-active-lease,
a takeover targets the single active lease.

## Invariants (v5.1)

1. **Atomic transitions** — one transaction each; a crash is a clean rollback or
   commit, never a split/half-applied transition.
2. **Gapless monotonic seq + idempotency** — explicit counter; a duplicate
   `idem_key` returns the existing seq (no new allocation, no gap); a `UNIQUE`
   violation rolls back.
3. **No clobber** — LAUNCH requires zero non-terminal leases (admission gate).
4. **Earned release — gated on `uid==zero`.** `terminal`/release only after the
   agent-uid re-scan returns **zero** live processes, regardless of the recorded
   run's liveness. A `claimed_exit` never releases.
5. **Orphan safe-stop, no human, no false kill.** Supervisor death → launchd
   restart → reconciliation **re-adopts** a healthy run (no kill) and **reaps** a
   dead/wedged/stop_intent one. Every crash/contention outcome is a safe terminal
   or safe hold needing no mid-run human.
6. **Reap convergence or fence.** uid-wide TERM→KILL→rescan converges to zero, or
   the lease stays fenced in `stop_intent` (retry+alert), never terminalized.
7. **Generation monotonic + fenced** in `lease`; superseded events inert; no ABA.
8. **Sole writer** — only the maintainer supervisor writes `state.db`; the agent
   run reports over an inherited pipe; SQLite fds never inherited.

## Fault-injection matrix (v5.1)

| # | Injection | Required outcome |
|---|---|---|
| T1–Tn | SIGKILL at **every** transition boundary | clean rollback/commit; no half-applied transition |
| G1 | crash after `prepared` commit, before spawn | reconcile: uid-scan (zero) → launch_aborted → terminal |
| G2 | crash after spawn, before `active` commit (child blocked, unrecorded) | uid-scan finds child (gen_token) → reap → verify zero → terminal |
| G3 | crash after `active` commit, before "go" | child exits on gate EOF; uid-scan/reap → terminal |
| Idem | replay a transition (same idem_key) | returns existing seq; no new event, no gap |
| Sup-orphan | supervisor SIGKILL, run **dead**, descendants alive | reconcile: uid-scan nonzero → reap → verify zero → terminal (dead run ≠ dead workers) |
| Sup-readopt | supervisor SIGKILL/restart, run **healthy** | reconcile: identity-verified alive → **re-adopt** (NOTE_EXIT + p_uniqueid reverify); **run untouched** |
| Sup-benign | launchd throttles/updates the supervisor, run healthy | re-adopt; healthy work not killed |
| Saga1 | crash after `stop_intent`, before/during kill | resume kill → verify zero → terminal |
| Saga2 | crash after uid==zero, before terminal commit | reconcile observes zero → terminalize; no dup acted on |
| Race | concurrent LAUNCH vs LAUNCH / LAUNCH vs TAKEOVER | admission gate + `BEGIN IMMEDIATE`: exactly one proceeds |
| Tw | takeover with live gen-g workers | reap gen g (verify zero) before publishing g+1 |
| Pid | PID reuse during a liveness/adopt check | `p_uniqueid` (re-read after NOTE_EXIT register) rejects |
| Esc | run child `fork`/`setsid`-escapes | uid-wide `kill(-1)` still reaps; rescan→zero |
| Churn | fork churn during reap | TERM/KILL/rescan converges; else fenced in stop_intent |
| NoConv | uninterruptible / externally-spawned uid process | stays `stop_intent` + alert; **never** terminalizes |
| Io | ENOSPC/EIO on `state.db` | transaction fails atomically; no partial state |
| Priv | reap attempted as root (should be refused) | reaper runs **as agent uid**, never root `kill(-1)` |
| Writer | agent run attempts to write `state.db` | denied (perms) / never happens; supervisor is sole writer |
| Uid | worker spawns a helper under a **different uid** | documented **limitation** → detect (privileged uid/token scan) or admit undetectable; not a falsifier |
| PL | reboot/power-loss (VM harness) | complete prior/next state, else **inconclusive** |

## Falsification redirect (from v5.1)

If the assembly still can't give one recoverable outcome, the redirect is
**containment option 2** (workers in a Linux VM/container — cgroup-kill +
namespaces). **Do not return to the flat-file kernel.** codex's fifth pass judged
no VM redirect warranted yet; v5.1 is expected to be buildable.

## New dependency

**Bundled/pinned stock SQLite** (not Apple's system build) + the scoped
`sudo -u agent` spawn/reap helper (Stage-2 sudoers). Flagged; approved.
