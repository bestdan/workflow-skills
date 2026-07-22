# Draft baseline launch/lease/registry state machine (Probe 5 input) — **v2**

v2 supersedes v1 after the codex + Fable co-review (`coreview-2026-07-22.md`),
which falsified v1 on paper. v1 is in git history (commit `982370f`). This is the
**draft Probe 5 falsifies** — a consolidation of §4.1–4.2 plus the co-review
fixes; not the approved measured revision.

Kernel scope (only this): `prepared → active → terminal`, generation replacement/
takeover, the **automated reap** of a crashed-`active` orphan, and registry
append — using OS-owned locks and atomic publication. Continuation reservation is
absent (Stage 5).

## Threat model (stated explicitly — co-review finding #4)

- **Tested: process-crash** — `SIGKILL` of a writer while the kernel and
  filesystem keep running. This is the probe's falsification target.
- **Reboot / power-loss: out of the fixture's reach.** SIGKILL leaves page-cache
  writes intact, so it cannot prove durability. The **protocol** uses
  `F_FULLFSYNC` (plain `fsync` on macOS only flushes to the drive's volatile
  cache — see `fsync(2)`), so it is *correct* for power-loss; but the fixture
  cannot *prove* it without a real power-fail harness (VM/loopback image). Those
  guarantees are therefore classified **inconclusive**, never "passed."

## Objects & locks (co-review findings #3, #6, #H)

- **Lock inodes are separate, permanent files** — never renamed, truncated by
  replacement, or unlinked. `locks/<repo-key>.lock` (one per repo) and
  `locks/registry.lock` (one global). Held with `flock(LOCK_EX|LOCK_NB)` on an
  fd kept open for the critical section only. (macOS `lockf(1)` is BSD `flock`;
  the fixture uses `flock` directly and never the `-k`-less CLI that unlinks.)
  **Data files are distinct paths** from lock files and are published by rename.
- **Two distinct lock roles, never conflated:**
  - the **transaction lock** (`<repo-key>.lock`) — held only across one
    transition's critical section (acquire → re-read+validate → CAS → publish →
    release), **not** for the whole run;
  - the **durable lease record** — the owner file's `state`. "Holding the lease"
    is a property of the durable record, not of an OS lock.
- **Owner record** `owner/<repo-key>` — retained even in `terminal` as the
  repo's **generation high-water-mark** (co-review #9, no ABA):
  `{repo_key, generation, run_id, manifest_digest, gen_token, run_incarnation,
  worker_incarnations[], state, created_at}`.
- **Incarnation identity** (co-review #11/#12) — never a bare PID:
  `{pid, p_uniqueid, start_tvsec, start_tvusec, exe, boot_session}` where
  `boot_session = sysctl kern.bootsessionuuid` and `p_uniqueid` is Probe 2's
  libproc key. A recorded incarnation is **dead** iff the pid is absent, or its
  live `p_uniqueid`/start-time/exe/boot_session differ.
- **Containment boundary** (co-review #10, the kill-TOCTOU fence) — every run and
  worker is spawned into the run's **own process group / session**, established
  **before** `active`, so no unregistered descendant survives and termination can
  fence PID reuse between check and signal.
- **Registry** `registry/<YYYY-MM>.jsonl` — published by **whole-file rewrite
  under the global registry lock** (atomic-publication primitive); a reader
  **fails closed** on any invalid line. Global monotonic `seq` derived under that
  same lock across list→validate→max→append→publish.

## Atomic publication primitive (every durable write)

`write temp (unique name) → F_FULLFSYNC(temp) → rename(temp, target) →
F_FULLFSYNC(dir)`. Atomic replacement: a reader sees the complete old or complete
new file, never torn, never absent-when-it-previously-existed (co-review m1).
Unique temp names + GC of stale temps under the lock (co-review #13).

## Lease state machine (v2)

Every transition is a short critical section under the transaction lock:
**acquire → re-read+validate owner → generation-CAS → mutate → release.**

```
LAUNCH (fresh):
  acquire repo lock ── busy? ─▶ a launcher is live ─▶ NOT a fresh launch ─▶ abort
  re-read owner; require state ∈ {absent, terminal}   ◀── #2 no clobber of a live owner
  g := max(owner.generation, registry max gen for repo) + 1     ◀── #9 HWM, no ABA
  publish owner{g, gen_token:=fresh nonce, state=prepared, run_incarnation=∅}
       │                                                        ◀── boundary A
  spawn run INTO its own pgroup, carrying gen_token, BLOCKED on a start-gate
  record run_incarnation durably; publish owner{state=active}   ◀── boundary B  (#2 fence:
       │                                                            the run does nothing
  open the start-gate ── run proceeds ──                            until active is durable)
       ▼
     active
       │  (stop / natural termination / reap)
       ▼
  acquire repo lock; re-read; require state=active ∧ generation=g   ◀── successor fence
  VERIFY run + every worker DEAD by incarnation (or drive them dead — see Reap)
       │                                                        ◀── boundary C-verify
  append observed_terminal (registry)  ── only AFTER verified dead ── ◀── #11, boundary C
  publish owner{state=terminal}                               ◀── boundary D
  append lease_release keyed idempotently on {repo_key,g,run_id}  ◀── #3, boundary E
     (a second lease_release for the same key is a no-op)
  release repo lock
```

**REAP (crashed-`active` orphan — co-review #1, decision (b)):** the watcher
detects `owner.state==active` with a stale/absent run and **triggers `ap-stop
--reap <repo_key> <g>`**; the watcher never signals a process itself.
`ap-stop --reap`:
1. `flock -NB` the repo lock. **Busy ⇒ a launcher (of some generation) is alive
   ⇒ not an orphan ⇒ refuse** (an *alive-but-hung* launcher lands here → human
   **alert**, a hang not a crash). Winning the lock is the **structural proof**
   of orphan-hood (the launcher holds the lock its whole life) and freezes
   successor admission (takeover also publishes g+1 under this lock).
2. re-read owner; require `state=active ∧ generation=g` (else signal nothing).
3. drive dead **by incarnation + containment**: for the run and each worker,
   re-validate the incarnation immediately before signaling and signal the run's
   **own process group** (never a stored pgid blindly); re-validate dead after.
   If any target's identity no longer matches, treat as already dead.
4. only after containment + every recorded incarnation are proven dead: the
   normal `observed_terminal(writer:reaper) → terminal → lease_release` sequence.
5. if termination is incomplete, write **durable stop-intent** and **retry on a
   later pass**; the generation stays admission-fenced meanwhile.

**TAKEOVER (`--take-over`, human):** same lock; verify prior incarnation dead;
publish `g+1`. A second concurrent takeover observes `g+1` already published and
fails closed (co-review m2).

## Invariants (v2)

1. **No torn read.** No partial owner/registry line is ever accepted.
2. **Fail-closed contention.** A writer that loses `flock -NB` aborts, publishes
   nothing, reports — never blocks/spins/retries in-invocation.
3. **No clobber of a live/non-terminal owner.** Fresh launch requires
   `state ∈ {absent, terminal}` under the lock.
4. **No untracked live process.** A spawned run/worker performs no irreversible
   action until its incarnation is durably recorded (start-gate fence).
5. **Release is earned & idempotent.** Release only after a durable
   `observed_terminal` **and** run+workers observed dead; a `claimed_exit`
   (agent-written) never releases/suppresses/authorizes; a duplicate
   `lease_release` for the same `{repo_key,g,run_id}` is a no-op.
6. **`observed_terminal` follows verification**, never precedes it.
7. **Generation monotonic + HWM + fenced.** g derived under the lock from
   max(owner, registry); terminal owner retained as HWM; no actor mutates a
   generation it does not hold under the lock; superseded records inert.
8. **Global `seq` correct-by-construction.** Under the global registry lock, seq
   is contiguous and monotonic across months. A protocol-**emitted** duplicate or
   gap is a **falsification** (not an alert); alerts are only for externally
   corrupted records, on which the reader fails closed.
9. **Identity-bound termination.** Targets selected only by incarnation
   (`p_uniqueid`+start+exe+boot_session) within the containment boundary; never a
   bare PID or a blindly-stored pgid; the check→signal window is fenced by the
   pgroup so PID reuse cannot mis-target.
10. **OS-owned liveness, automated recovery.** A dead writer's lock is released by
    the OS; every crash/contention outcome is a **safe terminal** or a **safe
    hold needing no mid-run human** — a crashed-`active` orphan is auto-reaped
    (b); the only human path is an *alive-but-hung* launcher, which is a hang, not
    a crash outcome.

## Fault-injection matrix (v2)

| # | Injection | Required outcome |
|---|---|---|
| A | SIGKILL after repo-lock acquire, before prepared publish | owner unchanged; lock auto-released; next launch proceeds |
| B1 | SIGKILL mid prepared publish (temp written, before rename) | owner = prior committed value; never torn |
| B2 | SIGKILL after rename, before dir-fsync (process-crash) | new owner readable; never torn/absent (reboot durability = inconclusive) |
| C | SIGKILL after prepared+spawn, before active (start-gate shut) | run is blocked/inert; reap drives it dead; safe |
| D | SIGKILL after active, mid-run | orphan → **reap** path (see R rows) |
| E | SIGKILL mid active/terminal publish | owner = prepared/active/terminal, never torn |
| F | SIGKILL after observed_terminal, before lease_release | terminal durable; next actor completes release; **no double** |
| G | SIGKILL after lease_release append, before unlock | second actor sees release logged → **no-op**; idempotent |
| H | Two fresh launches race the repo lock | exactly one proceeds; other fails closed |
| I | Fresh launch vs a live/non-terminal owner (lock free) | launch **refuses** (invariant 3); no clobber |
| R1 | **Reap orphan**: launcher dead, workers alive | reap wins lock → drives workers dead by incarnation → terminal, no human |
| R2 | Reap vs takeover racing the lock | exactly one wins; the other fails closed |
| R3 | **Alive-but-hung launcher** holds lock | reap **refuses** → human alert (Decision #5-compatible hang) |
| R4 | **Kill-TOCTOU**: worker PID reused between verify and kill | containment pgroup fence prevents wrong-target kill (else **falsifier**) |
| K | Agent writes `claimed_exit` while run/workers alive | lease still held; alert fires; no release |
| S1 | Two repos append registry concurrently | global lock serializes; seq contiguous |
| S2 | Month rollover during concurrent appends | no duplicate/gap seq across month files |
| S3 | Corrupt/truncated historical month file | reader fails closed; derivation refuses |
| X1 | Adversarial: hand-injected torn line / dup seq / gap / stray temp | reader rejects/alerts; protocol never emits these |
| X2 | Syscall failure (ENOSPC/EIO/failed fsync/rename/dir-sync) | no advancement past the boundary; never logged as durable success |
| X3 | Lockf fd semantics: wrapper vs child vs pgroup death; dup/fork/inherited fd | lock lifetime matches the intended owner only |
| X4 | Crash **during recovery**, repeated | converges to a safe terminal; no double action across re-crashes |
| X5 | Owner-file loss then fresh acquire | g derived from registry HWM; no ABA / gen reset |

## Falsification redirect

Any invariant failure **falsifies this draft**. Whether a *revised* flat-file
design can hold determines the redirect: simplify the protocol, or evaluate a
transactional store (**SQLite** with WAL + its own `F_FULLFSYNC` handling — which
would subsume seq allocation, idempotent release, and generation derivation)
before writing `ap-launch`/`ap-stop`/the watcher. Per the co-review, SQLite is
**not** warranted for the top holes (orphan authority, live-owner clobber, lock
inode, incarnation identity), which survive a storage swap.
