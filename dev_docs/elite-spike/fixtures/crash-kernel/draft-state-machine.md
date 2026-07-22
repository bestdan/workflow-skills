# Draft baseline launch/lease/registry state machine (Probe 5 input) — **v3**

v3 supersedes v2 after codex's second pass (`coreview-2026-07-22.md` §Second
pass), which found v2 still falsified on paper — chiefly because v2 merged two v1
fixes into a contradiction (auto-reap needed a run-long lock that the
transaction-lock split had removed). v1→`982370f`, v2→`9d43353`. This is the
**draft Probe 5 falsifies**; not the approved measured revision. Several items
here are **design-doc deltas** (§4/§5.1) flagged for ratification — see the kill
sheet's §Design-doc deltas.

Kernel scope: `prepared → active → terminal`, generation replacement/takeover,
**auto-reap** of a crashed-`active` orphan, and registry append — OS-owned locks
+ atomic publication. Continuation reservation absent (Stage 5).

## Threat model

- **Tested: process-crash** (`SIGKILL` of a writer; kernel + FS keep running).
- **Reboot/power-loss:** the protocol is **designed for** it (`F_FULLFSYNC`), but
  SIGKILL cannot prove it and the fixture runs no power-fail harness → those
  guarantees are **inconclusive, never passed**.
- **Hostile in-run escape** (a run child that deliberately `setsid`/`setpgid`es
  out of its containment) is **out of the kernel's scope** — process-level
  containment only *narrows* it; the real boundary is the Stage-2 dedicated uid +
  nono sandbox (Probes 1–2). The kernel's job: correctly reap a **cooperating**
  run's session, and **detect** an escape as a finding.

## Locks — three roles, all `O_CLOEXEC`, permanent inodes (never renamed/unlinked)

| Lock | Path | Held | Purpose |
|---|---|---|---|
| **Liveness** | `liveness/<repo-key>.live` | by the launcher, **the entire run** | proves launcher liveness: winning it non-blocking ⇒ launcher is **dead** |
| **Transaction** | `locks/<repo-key>.txn` | only across one transition | serializes owner-state transitions |
| **Registry** | `locks/registry.lock` | only across one append | serializes global seq + append |

`O_CLOEXEC` on every lock fd so a spawned child **cannot inherit** the liveness
lock and falsely keep it held after the launcher dies (codex v2 B1). **Total
lock order: liveness → transaction → registry**; reverse acquisition is
forbidden. Both launch and reap take liveness *then* transaction, so there is no
inversion; the registry lock is a leaf.

## Objects

- **Owner** `owner/<repo-key>` — retained in `terminal` as the generation
  high-water-mark. `{repo_key, generation, run_id, manifest_digest, gen_token,
  run_incarnation, worker_incarnations[], state, stop_intent, created_at}`;
  `state ∈ {prepared, active, terminal}`; `stop_intent` records an incomplete
  termination for later-pass retry.
- **gen_token** — a per-generation unique nonce. Every spawned run/worker carries
  it (env + argv/procname) so a reaper can find a process of generation *g* even
  if that process crashed **before** its incarnation was durably recorded.
- **Incarnation** — never a bare PID: `{pid, p_uniqueid, start_tvsec,
  start_tvusec, exe, sid, boot_session}` (`boot_session = kern.bootsessionuuid`,
  `p_uniqueid` = Probe 2's libproc key, `sid` = the run's session).
- **Registry** `registry/<YYYY-MM>.jsonl` — **O_APPEND of one validated JSON line**
  (not whole-file rewrite — codex v2 #7). Reader tolerates a torn *trailing* line
  (uncommitted append) and **fails closed** on a torn *interior* line. Global
  seq/HWM lives in a durable **`registry/seq.head`** (atomically published under
  the registry lock) so an append is **O(1)**, not O(history).
- Record types add **`generation_reserved`** (durable source of truth for `g`)
  and **`launch_aborted`**; plus v1's set (`launch_prepared`, `launch`,
  `observed_terminal`, `lease_release`, `claimed_exit`, …).

## Atomic publication (owner + seq.head only — small, replaced files)

`write unique temp → F_FULLFSYNC(temp) → rename → F_FULLFSYNC(dir)`. Every
`F_FULLFSYNC` (incl. the directory) is checked; a failure blocks advancement.
Complete-old-or-complete-new, never torn/absent. Registry *data* lines use
O_APPEND (above), not this primitive.

## Lease state machine (v3)

Launcher holds the **liveness** lock for the whole run; every owner transition is
a short **transaction**-lock critical section (acquire → reread+validate → CAS →
publish → release).

```
LAUNCH:
  acquire liveness lock (O_CLOEXEC, non-blocking) ── busy? ─▶ another launcher/reaper ─▶ abort
  [txn] reread owner; require state ∈ {absent, terminal}     ◀── no clobber (I)
        g := max(owner.gen, max generation_reserved for repo) + 1
        append generation_reserved{g, gen_token}             ◀── durable g source (X5)
        publish owner{g, gen_token, state=prepared, run_incarnation=∅}   [A]
  fork run as session leader (setsid), carrying gen_token; child writes its own
    pending_incarnation (atomic, tagged gen_token) then BLOCKS on the start-gate
  [txn] read pending_incarnation; publish owner{state=active, run_incarnation}  [B]
  open the start-gate ── run proceeds ──
  ▼ active
  (stop / natural end / reap)
  [txn] reread; require state=active ∧ gen=g
        VERIFY run + workers DEAD by incarnation within the session (or drive dead)
        append observed_terminal (idempotent key {repo_key,g,run_id})    [C, after verify]
        publish owner{state=terminal}                                    [D]
        append lease_release (idempotent same key)                       [E]
  release liveness lock
```

**REAP (crashed-`active` orphan; watcher *triggers*, never signals — decision b):**
1. `ap-stop --reap <repo_key> <g>` acquires the **liveness** lock non-blocking.
   **Busy ⇒ launcher alive ⇒ refuse** (an alive-but-hung launcher lands here →
   human **alert**; a hang, not a crash). **Win ⇒ launcher dead** — this is the
   sound orphan-proof (the run-long liveness lock, held only by the launcher and
   never inheritable thanks to `O_CLOEXEC`). Reap now *holds* liveness, fencing a
   new launch.
2. `[txn]` reread owner. Per the **recovery table** below, drive dead + terminalize.
3. Drive dead: for the recorded run/worker incarnations **and** any process still
   carrying `gen_token` in the run's session, re-validate incarnation immediately
   before signaling and signal the session; re-validate dead after. Non-matching
   identity ⇒ already dead.
4. `observed_terminal(writer:reaper)` → `terminal` → `lease_release`, all idempotent.
5. Incomplete termination ⇒ set `stop_intent` durably, **retry next pass**;
   generation stays fenced (reaper still holds liveness) until done.

**Recovery table** (reaper holds liveness ⇒ launcher dead; all under txn):

| owner state | action |
|---|---|
| absent | nothing |
| prepared | kill any `gen_token`-tagged process + recorded/pending incarnation in the session; `launch_aborted`; retain gen HWM |
| active | verify+kill run+workers by incarnation & `gen_token` within the session; `observed_terminal` → `terminal` → `lease_release` (idempotent) |
| terminal | complete any missing `lease_release` (idempotent); done |

This closes both v2 gate windows: a child spawned-but-unrecorded is found by
`gen_token`; a child blocked after `active` is reaped because the **trigger is
launcher-death (liveness free), not run-staleness**, and the child is inert until
the gate opens so killing it is always safe.

**TAKEOVER** (`--take-over`, human): identical to reap's precondition (liveness
free) but publishes `g+1/prepared` instead of terminalizing. A second concurrent
takeover finds liveness held by the first and fails closed.

## Invariants (v3)

1. No torn read (interior); a torn trailing append is treated as uncommitted.
2. Fail-closed contention (lost non-blocking acquire ⇒ abort, publish nothing).
3. No clobber of a live/non-terminal owner.
4. No untracked live process: a run/worker is either recorded **or** discoverable
   by `gen_token` in its session before it can act; the start-gate keeps it inert
   until `active` is durable.
5. Earned + idempotent release: only after durable `observed_terminal` **and**
   run+workers proven dead; `claimed_exit` never releases; duplicate
   `observed_terminal`/`lease_release` for `{repo_key,g,run_id}` are no-ops.
6. `observed_terminal` follows verification.
7. Generation monotonic, HWM-durable in **both** owner and `generation_reserved`;
   no ABA even if the owner file is lost; superseded records inert.
8. Global seq correct-by-construction via `seq.head` under the registry lock; a
   protocol-**emitted** dup/gap is a **falsification**; external corruption ⇒
   fail-closed read + alert. An append that loses the non-blocking registry lock
   must record durable retry intent (no silent drop).
9. **Sound orphan-proof:** reap acts only while holding the run-long liveness
   lock; winning it non-blocking is a complete proof the launcher is dead
   (`O_CLOEXEC` ⇒ no inheritor), and holding it fences launch/takeover.
10. Identity-bound, session-scoped termination; escape from the session is
    **detected** (kernel narrows; the uid/sandbox is the real fence).
11. OS-owned liveness + automated recovery: every crash/contention outcome is a
    safe terminal or a safe hold needing no mid-run human (auto-reap); the only
    human path is an alive-but-hung launcher → alert.

## Fault-injection matrix (v3 — one deterministic crash point per row)

| # | Injection | Required outcome |
|---|---|---|
| A | crash after liveness acquire, before generation_reserved | owner absent; liveness auto-released; next launch proceeds |
| Br | crash after generation_reserved, before prepared publish | reserved g recorded, no owner; next launch derives g+1 (no reuse) |
| B1 | crash mid prepared publish (temp, pre-rename) | owner = prior/absent; never torn |
| C1 | crash after prepared, before fork | reap(prepared): nothing to kill; launch_aborted |
| C2 | crash after fork, before pending_incarnation written | reap finds child by gen_token, kills it; launch_aborted |
| C3 | crash after pending_incarnation, before active publish | reap(prepared) kills recorded child; launch_aborted |
| C4 | crash after active publish, before opening gate | reap(active) kills inert blocked child by incarnation; terminal |
| D1 | crash mid active publish | owner = prepared/active, never torn |
| D2 | crash mid terminal publish | owner = active/terminal, never torn |
| E1 | crash after observed_terminal, before terminal publish | reap completes terminal+release; no dup acted on |
| E2 | crash after terminal, before lease_release | next holder completes release; idempotent, no double |
| E3 | crash after lease_release, before liveness release | second actor sees release logged ⇒ no-op |
| H | two launches race the liveness lock | exactly one proceeds; other fails closed |
| I | launch vs a live non-terminal owner (liveness free? no — held) | launch refuses; no clobber |
| R1 | **reap orphan** (launcher dead, workers alive) | reap wins liveness → kills workers by incarnation/session → terminal; no human |
| R2 | reap vs takeover racing liveness | exactly one wins; other fails closed |
| R3 | **alive-but-hung launcher** holds liveness | reap refuses → human alert (hang, Decision #5-ok) |
| R4a | worker PID reused between verify and signal, still in session | session signal + re-validate defeats mis-target |
| R4b | run child `setsid`-escapes the session | **detected** as an escape finding (needs uid/sandbox fence) |
| R5 | **false-reap guard**: healthy active run, reaper attempts reap | liveness **busy** ⇒ reap refuses; healthy run untouched |
| K | agent writes claimed_exit while alive | lease still held; alert; no release |
| S1 | two repos append registry concurrently | registry lock serializes; seq contiguous via seq.head |
| S2 | crash mid registry append (partial trailing line) | reader treats as uncommitted; seq.head unmoved; no gap |
| S3 | month rollover during concurrent appends | no dup/gap across month files |
| S4 | crash after append, before seq.head publish | line present but uncommitted (seq>head) → reader ignores; retried |
| X1 | adversarial: hand-injected torn interior line / dup seq / gap | reader fails closed + alerts; protocol never emits |
| X2a | F_FULLFSYNC(dir) fails after rename | not logged as durable success; boundary not advanced |
| X2b | ENOSPC / EIO on a temp write | abort before rename; no partial owner |
| X3 | lock fd: fork/dup/inherited descriptor (test O_CLOEXEC holds) | only the launcher holds liveness; no inheritor keeps it |
| X4 | crash **during reap**, repeated | converges to safe terminal; no double action across re-crashes |
| X5 | owner file lost, then fresh acquire | g from generation_reserved HWM; no ABA/reset |

## Falsification redirect

Any invariant failure **falsifies this draft** (independent of the redirect
question). Whether a *revised* flat-file design can hold determines the redirect:
simplify, or evaluate **SQLite** (WAL + its own `F_FULLFSYNC`) for the parts it
subsumes (seq/idempotent-release/generation) — but the orphan-authority,
containment, and lock-identity holes survive a storage swap, so SQLite is not a
substitute for the liveness-lock/session-fence design.
