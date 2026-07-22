# Draft baseline launch/lease/registry state machine (Probe 5 input) — **v4**

v4 applies Fable's third-pass fixes to v3 (`coreview-2026-07-22.md` §Third pass),
which found 3 on-paper CRITs the v3 matrix would not have caught. History:
v1→`982370f`, v2→`9d43353`, v3→`8223815`. This is the **draft Probe 5 falsifies**;
several items are **design-doc deltas** (§4/§5.1) flagged in the kill sheet.

Kernel scope: `prepared → active → terminal`, generation replacement/takeover,
**auto-reap** of a crashed-`active` orphan, and registry append — OS-owned locks
+ atomic publication. Continuation reservation absent (Stage 5).

## Threat model

- **Tested: process-crash** (`SIGKILL` of a writer; kernel + FS keep running).
- **Reboot/power-loss:** the protocol is **designed for** it (`F_FULLFSYNC`), but
  SIGKILL cannot prove it → **inconclusive, never passed**.
- **Hostile in-run escape** (a run child that deliberately `setsid`/`setpgid`es
  out) is **out of the kernel's scope** — the real fence is the Stage-2 dedicated
  uid + nono sandbox (Probes 1–2). The kernel reaps a **cooperating** run's
  session and **detects** an escape (R4b).

## Locks — three roles, permanent inodes (never renamed/unlinked)

| Lock | Path | Held by | Purpose |
|---|---|---|---|
| **Liveness** | `liveness/<repo-key>.live` | the **run-long supervisor** process, the whole run | winning it non-blocking ⇒ supervisor **dead** (the orphan-proof) |
| **Transaction** | `locks/<repo-key>.txn` | whoever mutates owner state, per-transition | serializes owner transitions |
| **Registry** | `locks/registry.lock` | whoever appends, per-append | serializes global seq + append |

**Liveness primitive: `fcntl`/POSIX record lock (`F_SETLK`).** POSIX locks are
**not inherited across `fork`**, so a forked run/worker can never hold — and thus
never *fake* — liveness (this is stronger than the v3 `O_CLOEXEC` claim, which
only covered `exec`, not `fork`; macOS has no OFD locks). Discipline: the
supervisor opens the lock file **once** and never reopens/closes it early (POSIX
locks drop on the *first* close of *any* fd to the file). The transaction and
registry locks may use `flock` (short-lived, single-holder).

**Who is the supervisor:** the process that must live exactly as long as the run
is supervised — i.e. the long-lived run process (Probe 2's tmux-pane run-shim),
**not** a transient `ap-launch` that exits after spawning. **Consequence, stated
as an intended fail-safe:** liveness ≡ "supervisor alive" ≡ "run supervised", so
a logout / SIGHUP / terminal-close that kills the supervisor **reaps a healthy
run**. That is the desired autopilot behavior (no unsupervised run persists), not
a bug — but it is an explicit outcome, not a surprise.

**Total lock order: liveness → transaction → registry** (registry is a leaf);
reverse acquisition forbidden. Launch and reap both take liveness then
transaction, so no inversion.

## Objects

- **Owner** `owner/<repo-key>` — retained in `terminal` as the generation HWM:
  `{repo_key, generation, run_id, manifest_digest, gen_token, run_incarnation,
  worker_incarnations[], state, stop_intent, created_at}`;
  `state ∈ {prepared, active, terminal}`.
- **gen_token** — per-generation nonce carried in every spawned process's
  env+argv/procname, so a process of generation *g* is discoverable even if it
  crashed **before** its incarnation was recorded.
- **Incarnation** — `{pid, p_uniqueid, start_tvsec, start_tvusec, exe, pgid, sid,
  boot_session}` (`p_uniqueid` = Probe 2's libproc key; `sid`/`pgid` = the run's
  session/group). Dead iff pid absent or any identity field differs.
- **Registry** `registry/<YYYY-MM>.jsonl` — **O_APPEND of one validated JSON
  line**; global HWM in a durable **`registry/seq.head`**. See tail-reconciliation
  below.

## Registry append with tail-reconciliation (fixes CRIT-1, CRIT-2)

Under the **registry lock**, every append does, in order:
1. **Reconcile the tail** (never blind-append): validate the last line; if it is
   a torn/partial trailing line, `ftruncate` it off; if it is a *complete valid*
   line with `seq > seq.head` (a committed append whose head-publish crashed),
   **adopt** it — roll `seq.head` forward over it. Repeat until the tail is clean.
3. Derive `seq := seq.head + 1`; `O_APPEND` the line; `F_FULLFSYNC`.
4. Atomically publish `seq.head := seq`.
A reader still fails closed on a torn **interior** line — but the protocol now
never *produces* one (a crash mid-append is truncated by the next writer before
its own append), and a crashed head-publish is adopted rather than duplicated.

## Atomic publication (owner + seq.head — small replaced files)

`write unique temp → F_FULLFSYNC(temp) → rename → F_FULLFSYNC(dir)`, every
`F_FULLFSYNC` checked; a failure blocks advancement. Complete-old-or-complete-new.

## Lease state machine (v4)

Supervisor holds **liveness** the whole run; each owner transition is a short
**transaction**-lock critical section.

```
LAUNCH:
  acquire liveness (fcntl F_SETLK, non-blocking) ── busy? ─▶ another supervisor/reaper ─▶ abort
  [txn] reread owner; require state ∈ {absent, terminal}          ◀── no clobber (I)
        g := max(owner.gen, max generation_reserved for repo) + 1 ◀── reads UNCOMMITTED reserved
                                                                     lines too (HIGH-5: no owner was
                                                                     published, so a higher g is safe)
        append generation_reserved{g, gen_token}
        publish owner{g, gen_token, state=prepared, run_incarnation=∅}   [A]
  fork run as session leader (setsid), carrying gen_token; child writes its own
    pending_incarnation (atomic, gen_token-tagged) then BLOCKS on the start-gate
  [txn] read pending_incarnation; publish owner{state=active, run_incarnation}  [B]
  open start-gate ── run proceeds ──
  ▼ active → (stop / natural end / reap)
  [txn] reread; require state=active ∧ gen=g; VERIFY run+workers DEAD by incarnation
        append observed_terminal (idempotent {repo_key,g,run_id})  [C, after verify]
        publish owner{state=terminal}                              [D]
        append lease_release (idempotent same key)                 [E]
  release liveness
```

**REAP (crashed-`active` orphan; watcher *triggers*, never signals — decision b):**
1. `ap-stop --reap <repo_key> <g>` acquires **liveness** non-blocking. **Busy ⇒
   supervisor alive ⇒ refuse** (a hung-but-alive supervisor → the watcher's alert
   path; see R3). **Win ⇒ supervisor dead** (sound: fcntl liveness is never
   fork-inherited). Reap now holds liveness, fencing launch/takeover.
2. `[txn]` reread owner → drive the **recovery table**.
3. **Drive dead** (specifies "signal the session"): enumerate the run's process
   groups within its `sid`; for each, re-validate identity immediately before,
   then `kill(-pgid)`; re-validate dead after. **Recorded incarnations** use
   their `pgid`. **gen_token-discovered unrecorded** processes (C2 path): confirm
   `sid`-membership, then signal by **`-pgid`** (never `kill(pid)` — a scanned pid
   can die+reuse before the signal). **Re-scan until clean** (defeats
   fork-during-scan). Escaped (`sid`-changed) processes → **R4b detection**, not
   killed here.
4. `observed_terminal(writer:reaper)` → `terminal` → `lease_release`, idempotent.
5. Incomplete ⇒ set `stop_intent` durably, **retry next pass** (reaper keeps
   liveness). `stop_intent` is cleared by the pass that reaches `terminal`.

**Recovery table** (reaper holds liveness ⇒ supervisor dead; under txn):

| owner state | action |
|---|---|
| absent | nothing |
| prepared | drive dead (gen_token processes + recorded/pending incarnation); `launch_aborted`; **publish owner{state=terminal}** (retain gen HWM) ◀── fixes CRIT-3 |
| active | verify+kill run+workers (§drive-dead); `observed_terminal` → `terminal` → `lease_release` |
| terminal | complete any missing `lease_release`; done |

`launch_aborted` and the terminal/release records are **appended-but-inert on
duplicate** (a re-crash may append twice; consumers dedup on `{repo_key,g}` /
`{repo_key,g,run_id}` — a duplicate is never *acted on*, which is what "no-op"
means for an append-only log).

**TAKEOVER** (`--take-over`, human): acquires liveness (supervisor dead), then
**runs the reap recovery table for gen g first** (drives any live gen-g workers
dead + terminalizes — fixes HIGH-6, no live orphan across a takeover), *then*
publishes `g+1/prepared` and hands liveness to the new run's supervisor. A second
concurrent takeover finds liveness held and fails closed.

## Invariants (v4)

1. No torn **interior** read; a torn trailing append is truncated by the next
   writer (tail-reconciliation), so the protocol never emits one.
2. Fail-closed contention.
3. No clobber of a live/non-terminal owner.
4. No untracked live process (gen_token discovery + inert-until-gate).
5. Earned + idempotent release; `claimed_exit` never releases; duplicate
   terminal/release/`launch_aborted` records are appended-but-inert.
6. `observed_terminal` follows verification.
7. Generation monotonic; HWM durable in owner **and** `generation_reserved`
   (read incl. uncommitted); no ABA; superseded records inert.
8. Global seq correct-by-construction via `seq.head` + tail-reconciliation; a
   protocol-**emitted** dup/gap is a falsification; external corruption ⇒
   fail-closed read + alert; a lost registry lock ⇒ durable retry intent, no
   silent drop.
9. **(Hypothesis under test, not asserted — X3.)** Reap is sound only if the
   liveness primitive (fcntl F_SETLK) is neither fork- nor exec-inherited by run
   children and is held for exactly the supervised lifetime. The probe *tests*
   this; it is not assumed.
10. Identity-bound, session-scoped termination via `-pgid`; session escape is
    detected (kernel narrows; uid/sandbox is the fence).
11. OS-owned liveness + automated recovery: every crash/contention outcome is a
    safe terminal or safe hold needing no mid-run human — except an alive-but-hung
    supervisor, whose sanctioned resolution is a human `kill` (the "hang, not
    crash" carve-out) triggered by the watcher's alert.

## Fault-injection matrix (v4 — one deterministic crash point per row; `+` = crash-then-continue)

| # | Injection | Required outcome |
|---|---|---|
| A | crash after liveness acquire, before generation_reserved | owner absent; liveness auto-released; next launch proceeds |
| Br | crash after generation_reserved append, before its seq.head publish | reserved line read (uncommitted-ok) → next launch derives g+1, no reuse |
| B1 | crash mid prepared publish | owner = prior/absent; never torn |
| C1 | crash after prepared, before fork | reap(prepared): launch_aborted; **owner→terminal; next launch proceeds** |
| C2 | crash after fork, before pending_incarnation | reap finds child by gen_token, `-pgid` kills it; owner→terminal; next launch proceeds |
| C3 | crash after pending_incarnation, before active | reap kills recorded child; owner→terminal; next launch proceeds |
| C4 | crash after active, before opening gate | reap(active) kills inert blocked child; terminal |
| D1 | crash mid active publish | owner = prepared/active, never torn |
| D2 | crash mid terminal publish | owner = active/terminal, never torn |
| E1 | crash after observed_terminal, before terminal | reap completes; duplicate observed_terminal inert |
| E2 | crash after terminal, before lease_release | next holder completes; idempotent, no double |
| E3 | crash after lease_release, before liveness release | second actor sees release logged ⇒ inert |
| H | two launches race liveness | exactly one proceeds; other fails closed |
| I | launch vs a live non-terminal owner | launch refuses; no clobber |
| R1 | reap orphan (supervisor dead, workers alive) | reap wins liveness → `-pgid` kills workers → terminal; no human |
| R2 | reap vs takeover racing liveness | exactly one wins; other fails closed |
| R3 | alive-but-hung supervisor holds liveness | reap **refuses with reason=liveness-held** (alert is the watcher's job; hang → sanctioned human kill; depends on Probe 3) |
| R4a | worker PID reused between verify and signal | `-pgid` (not kill(pid)) + re-validate defeats mis-target |
| R4b | run child `setsid`-escapes the session | **detected** (proc_listpids over uid + KERN_PROCARGS2 gen_token, sid≠run) as a finding needing the uid/sandbox fence |
| R5 | healthy active run, reaper attempts reap | liveness **busy** ⇒ refuse; run untouched |
| Tw | **takeover with live gen-g workers** | takeover reaps gen g (kills workers, terminal) **before** publishing g+1 |
| K | agent writes claimed_exit while alive | lease held; alert; no release |
| S1 | two repos append registry concurrently | registry lock serializes; seq contiguous |
| S2+ | crash mid append (partial trailing), **then next append** | next writer truncates the torn tail before appending; no interior torn line |
| S4+ | crash after append, before seq.head publish, **then next append** | next writer **adopts** the committed line (rolls head); no duplicate seq |
| S3 | month rollover during concurrent appends | no dup/gap across month files |
| X1 | adversarial: hand-injected torn interior / dup seq / gap | reader fails closed + alerts; protocol never emits |
| X2a | F_FULLFSYNC(dir) fails after rename | not logged durable; boundary not advanced |
| X2b | ENOSPC/EIO on temp write | abort before rename; no partial owner |
| X3 | liveness fd: fork/exec/second-open/close (test invariant 9) | only the supervisor holds it; no run child fakes it; no early-close drop |
| X4+ | crash **during reap**, repeated | converges to safe terminal; idempotent across re-crashes (incl. mid-append via S4+) |
| X5 | owner file lost, then fresh acquire | g from generation_reserved HWM; no ABA/reset |
| Tkr | gen_token-scanned pid dies+reused between scan and signal | `-pgid`/sid re-check prevents foreign-process kill |

## Falsification redirect

Any invariant failure **falsifies this draft** (independent of the redirect).
Whether a *revised* flat-file design can hold determines the redirect: **SQLite**
(WAL + `F_FULLFSYNC`) would subsume the registry seq/idempotency holes (CRIT-1/2
class) but **not** the orphan-authority, liveness-primitive, or session-fence
holes — so it is not a substitute for the fcntl-liveness / session design.
