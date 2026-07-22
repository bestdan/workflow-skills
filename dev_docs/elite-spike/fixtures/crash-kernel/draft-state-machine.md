# Draft baseline launch/lease/registry state machine (Probe 5 input)

This is the **draft** Probe 5 falsifies (§7a row 5: "runs only after the draft
exists"). It consolidates the lifecycle prose in §4.1–4.2 of the E-lite design
into an explicit, fault-injectable spec. It is a **draft for falsification**, not
the approved measured revision — Probe 5 exists to break it before it is coupled
to tmux, GitHub, or Claude.

Scope of the kernel (only this — everything else is out): `prepared → active →
terminal`, generation replacement, and registry append, using OS-owned locks and
atomic publication. **Continuation reservation is deliberately absent** (Stage 5).

## Objects

- **Lease** at `<state>/lock/<repo-key>` (`repo-key` = hash of the canonical
  remote URL, not a local path). Owner record:
  `{generation, run_id, manifest_digest, run_incarnation, worker_incarnations[],
  created_at, state}` where `state ∈ {prepared, active, terminal}`.
- **Registry** at `<state>/registry/` — versioned JSONL, one file per month.
  Global monotonic `seq` (max existing across all files + 1; never resets at
  rollover). Records carry `schema, seq, wall_ts, mono_ts, boot_id, writer,
  prior_seq`, and run-scoped records also `run_id, repo_key, generation`.
- **Locks** are `lockf -t 0` (non-blocking) on maintainer-owned lock files. OS
  ownership ends on process death. **No crash-persistent `mkdir` mutex.**

## Atomic publication primitive (every durable write)

`write temp in same dir → fsync(temp) → rename(temp, target) → fsync(dir)`.
A reader therefore never sees a torn record; a crash before `rename` leaves the
target at its prior committed value; a crash after `rename` but before dir-fsync
is still atomic for the file (dir-fsync only guarantees the name survives reboot).

## Lease state machine

```
        (none)
          │  acquire lockf -t 0 on repo lock   ── busy? ─▶ FAIL CLOSED
          │                                     (abort, publish nothing, report)
          ▼
   [T1] publish owner{generation=g, state=prepared}          ◀── durable boundary A
          ▼
       prepared
          │  record run_incarnation
          ▼
   [T2] publish owner{state=active}                          ◀── durable boundary B
          ▼
        active   ── run executes ──
          │  (stop or natural termination)
          ▼
   [T3] append observed_terminal to registry (durable)       ◀── durable boundary C
          │  verify current-generation pane AND every worker incarnation DEAD
          ▼
   [T4] publish owner{state=terminal}                        ◀── durable boundary D
          ▼
   [T5] append lease_release; release lockf                  ◀── durable boundary E
          ▼
        (none)
```

**Generation replacement / takeover** (`ap-launch --take-over`, human-run): under
the same OS lock, verify the recorded incarnation + workers are dead, record the
abandoned/prepared generation, then atomically publish `generation = g+1`.
Records from a superseded generation are recognizable and **never acted on**.
Generation compare under the lock prevents any writer from releasing a successor.

## Invariants (must hold after a crash at ANY boundary or under contention)

1. **No torn read.** No partially-written owner record or registry line is ever
   accepted as valid.
2. **Fail-closed contention.** A writer that loses the non-blocking acquisition
   aborts, publishes nothing, reports — never blocks, spins, or retries in the
   same invocation.
3. **Release is earned.** The lease releases only after a durable
   `observed_terminal` AND the current-generation pane + every registered worker
   are observed dead. A `claimed_exit` (agent-written) **never** releases, never
   suppresses an alert, never authorizes cleanup.
4. **Generation monotonic + superseded-safe.** No writer releases or mutates a
   generation other than the one it holds under the lock; superseded-generation
   records are inert.
5. **seq is global + monotonic.** No duplicate, no regression, no reset at file
   rollover. A duplicate/out-of-order record is a **watcher alert, not a silent
   repair**.
6. **Decision #5 — no ambiguous hold.** Every crash/contention outcome is either
   a **safe terminal** or a **safe hold that needs no mid-run human action**. A
   crashed `prepared` generation is a safe hold recoverable by takeover; it never
   blocks a human mid-run.
7. **OS-owned liveness.** A dead writer's lock is released by the OS; recovery
   never depends on a crash-persistent mutex or a stale lockfile needing manual
   removal.

## Fault-injection matrix (what Probe 5 must exercise)

| # | Injection | Required outcome |
|---|---|---|
| A | SIGKILL after lock acquire, before prepared publish | lease still (none); lock auto-released; next writer proceeds |
| B1 | SIGKILL mid prepared publish (temp written, before rename) | target unchanged; no prepared owner; safe |
| B2 | SIGKILL after rename, before dir-fsync | prepared owner valid (or absent after reboot) — never torn |
| C | SIGKILL after prepared, before active publish | `prepared` safe-hold; recoverable by takeover; no human needed mid-run |
| D | SIGKILL mid active publish | owner is prepared or active, never torn |
| E | SIGKILL after observed_terminal, before lease_release | terminal is durable; lease still held → watcher/ap-stop completes release; no double-release |
| F | SIGKILL mid registry append (temp/rename/fsync) | no torn line; seq has no duplicate/gap accepted silently |
| G | Two writers race the repo lock | exactly one proceeds; the other fails closed |
| H | ap-stop vs watcher both terminalizing | single observed_terminal acted on; generation compare prevents releasing a successor; no double-release |
| I | Takeover vs a live/abandoned generation | g+1 published atomically; superseded-generation records inert |
| J | Concurrent registry appenders | serialized by lockf; global seq contiguous; duplicates/gaps → alert |

## Falsification redirect (if the flat-file protocol cannot hold these)

Simplify the protocol, or evaluate a transactional store (e.g. **SQLite** with
its own WAL/atomic-commit) before writing `ap-launch`, `ap-stop`, or the watcher
around it.
