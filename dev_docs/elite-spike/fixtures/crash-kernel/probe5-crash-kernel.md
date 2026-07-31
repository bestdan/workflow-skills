# Probe 5 — Baseline crash-transaction kernel

**Flat-file kernel: FALSIFIED → storage redirect taken. v5.1 fixture →
CONFIRMED 2026-07-27, in the kill sheet's specific and limited sense: _not
falsified in the tested process-crash environment_. That is the ceiling, not
"proven correct", and it is explicitly NOT a power-loss durability claim.**

The 2026-07-23 run was INCONCLUSIVE because the dedicated `agent` uid did not
exist, so invariants 4/5/6 were never exercised and six rows were BLOCKED. All 28
rows have since been earned against the real escape-proof uid domain, in **one
pass at a single clean fixture revision (`c5eb8fd`)**: **26 PASS, 1
DOCUMENTED-LIMITATION (`Uid`, explicitly not a falsifier), 1 BLOCKED (`PL`, never
passable here)**.

**Reboot/power-loss durability is untested and remains inconclusive by
construction.** So is EIO proper (bad media). Do not read 26/28 as covering
either. See Results → Classification.

The fixture also caught a real v5.1 defect: the re-adopt rule false-reaps any run
that forks a worker (see Results → Finding).
Four review rounds falsified the hand-rolled flat-file design (v1–v4)
on the same two things — exact-once ordered registry sequence under crash, and
macOS advisory-lock liveness; prior-art research (`prior-art-research.md`)
confirmed both are **solved wheels**. v5 took the redirect (SQLite + launchd +
dedicated-uid); **codex's fifth pass judged the redirect sound — no architectural
pivot** — and named a bounded spec pass, now applied as **v5.1**
([`draft-state-machine.md`](./draft-state-machine.md)): uid-scan-gated recovery
(terminalize only after `uid==zero`), a `stop_intent → kill → terminal` **saga**
outside the transaction, **one-globally-active-lease** admission gate (uid-wide
kill is correct), a scoped `sudo -u agent` privilege path (never root `kill(-1)`),
sole-writer IPC, `idem_key` idempotency, and **monitored re-adoption** on
supervisor restart (a benign restart does not kill healthy work). **Redirect taken
≠ validated** — the v5.1 fixture still runs; see `v5-fixture-brief.md`.

Disposable spike under §0a's contract (rule 4 — never promoted by renaming). Runs
**in a disposable directory** against a disposable `state.db`. Surrogate "runs"
are trivial processes under the `agent` uid; no tmux/GitHub/Claude coupling.

## Kill sheet (from §7a, priority 5)

### Key assumption / falsifier

> _The **SQLite + launchd + dedicated-uid** assembly gives the lease/registry
> control-plane **one recoverable outcome under crash and concurrency** — every
> outcome a **safe terminal or a safe hold needing no mid-run human action**
> (Decision #5) — with only the lease/generation/takeover state machine,
> incarnation identity, and startup reconciliation hand-written._

**Any invariant failure falsifies v5.** Concretely, falsified if any matrix
injection yields: a torn/half-applied transition (a transition split across more
than one SQLite transaction); a duplicate/gap `seq` or a double state-move; a
clobber of a live lease; a release before run+workers are **verified dead**
(uid re-scan ≠ zero); an **orphan that needs a human** (supervisor death not
auto-detected+reaped); a **non-converging reap** (uid-wide TERM→KILL→re-scan never
reaches zero — e.g. a within-uid escape); a superseded generation acted on; or a
`p_uniqueid` liveness check fooled by PID reuse. A **uid-changing** helper
escaping containment is a _documented limitation_ (→ option 2), not a falsifier.
Whether the assembly can be tightened, or must move to containment option 2, is a
conclusion that determines the redirect — it does not gate falsification.

### Method

In a disposable directory, implement **only** the v5.1 assembly: a **bundled stock
SQLite** `state.db` (WAL, `synchronous=FULL`, `fullfsync=ON`) — **sole writer** the
maintainer supervisor; **one `BEGIN IMMEDIATE` transaction per transition** with
`idem_key` idempotency (duplicate → existing seq, never a gap); the run spawned
**as the agent uid** via a scoped `sudo -u agent` helper, reporting its incarnation
over an **inherited pipe** and blocking on a **start-gate** (EOF = "exit, don't
go"); a **launchd `KeepAlive`** supervisor whose startup **reconciliation
re-adopts** an identity-verified healthy run (`NOTE_EXIT` + `p_uniqueid` reverify)
and **reaps** a dead/wedged/`stop_intent` one; termination as a **saga** —
`stop_intent` commit → `kill(-1)` (as agent uid) TERM→wait→KILL→**rescan-to-zero**
_outside_ the transaction → `terminal` commit **only after uid==zero**; and a
**one-globally-active-lease** admission gate. Then run the **v5.1 matrix** (draft
§Fault-injection matrix) via armed crash-points (`PROBE5_CRASH_AT=<point>` →
`os._exit()`): every transition boundary (T1–Tn), the DB↔spawn gate windows
(G1–G3), idempotency replay (Idem), supervisor-kill with a **dead** run + live
descendants (Sup-orphan), supervisor-kill/restart with a **healthy** run
(Sup-readopt, Sup-benign), the saga crash points (Saga1/2), races (Race, Tw),
PID-reuse (Pid), escape (Esc), churn (Churn), non-convergence fencing (NoConv),
IO (Io), the never-root-`kill(-1)` guard (Priv), sole-writer (Writer), the
uid-changing limitation (Uid), and power-loss (PL, else inconclusive). Check every
invariant (1–8) after each.

### Pass threshold

Across the **v5.1 matrix**: all eight invariants hold, and **every**
crash/contention outcome is a **safe terminal or a safe hold needing no mid-run
human**. Specifically: no transition split across two SQLite transactions (crash =
clean rollback/commit); gapless+monotonic seq and an idempotent replay (existing
seq, no gap); admission gate blocks a second active lease; **`terminal`/release
only after the agent-uid rescan returns zero** (dead run ≠ dead workers); a benign
supervisor restart **re-adopts** the healthy run (no false kill); reap either
**converges** to zero or stays **fenced** in `stop_intent` (never terminalizes on
non-convergence); PID reuse rejected by `p_uniqueid`; reaping runs **as the agent
uid** (never root `kill(-1)`); the supervisor is the sole `state.db` writer;
generation fenced, no ABA. Best positive result: **"not falsified in the tested
process-crash environment"** — reboot/power-loss stays inconclusive without a VM
harness.

### Inconclusive condition (rule 3)

Classify **inconclusive**, not pass, if: a boundary crash can't be injected
**deterministically** at the intended transaction boundary; or a
contention/reap-convergence race can't be forced. And — always partially
inconclusive by construction — **reboot / power-loss durability is untested**:
a bundled SQLite with `fullfsync=ON` is what durability is **designed for**, but
SIGKILL cannot prove it and (absent a VM/loopback power-fail harness) those
guarantees are **inconclusive, never passed** — for SQLite exactly as for flat
files; the win is inheriting SQLite's crash-VFS test corpus, not a hand-built one.

### Evidence required (rule 4)

Checked in beside this file: the fixture command/tests, sanitized per-injection
transcripts (the durable state observed after each kill, the registry contents,
the lock state), non-secret env metadata, result, decision. No secrets exist to
persist here.

### Time cap

**Two working days** — an explicit override of §7a rule 3's half-day default,
per the probe row. At the cap, classify `confirmed` / `falsified` /
`inconclusive`. For this probe **`confirmed` means "not falsified in the tested
process-crash environment"** — the ceiling (reboot/power-loss stays
`inconclusive`), not "proven correct."

## Design-doc deltas (the redirect changes the production spec — need ratification)

v5 is a larger change to §3/§4/§5.1 than the flat-file drafts. Flagged for sign-off:

1. **Registry & lease → SQLite (§4.1–4.2).** Replace the flat-file registry,
   `seq.head`, lease files, and atomic-publish protocol with a bundled/pinned
   SQLite `state.db` (WAL, `synchronous=FULL`, `fullfsync=ON`); one `BEGIN
   IMMEDIATE` transaction per transition; gapless seq via an explicit counter;
   idempotency via `UNIQUE`. **New dependency** (bundled SQLite) — approved as the
   redirect substrate; must not be Apple's system build.
2. **Supervision → launchd (§5.1).** The watcher/supervisor is a launchd
   `KeepAlive` job; there is **no liveness lock**. Orphan detection is **startup
   reconciliation** (roster in SQLite vs live incarnations), not a lock probe.
   launchd restarts the supervisor; the supervisor reaps + terminalizes.
3. **Containment → dedicated `agent` uid (§3, §5.1).** Termination is uid-wide
   `kill(-1)` (TERM→bounded-wait→KILL→re-scan-until-zero) as the agent uid — the
   containment **primitive**, not a footnote. Ratify: uid-wide = "stop all agent
   work" (single-agent-per-host baseline), needs the one-time admin-provisioned
   `agent` uid (Probe 1), and does **not** contain a uid-changing helper (→
   option 2 if that's ever in scope).
4. **Reconciliation is the normative recovery spec (§4.1)** — the measured
   revision enumerates the startup-reconciliation table, not a lock-recovery table.

### Dependent work gated on this probe

The Stage-2 control plane (`ap-launch`, `ap-stop`, the watcher, the state writer)
is built **around** this proven assembly.

### Redirect if falsified

If the SQLite/launchd/uid assembly still can't give one recoverable outcome, the
remaining redirect is **containment option 2** — move workers into a Linux
VM/container boundary (cgroup-kill + namespaces make tree-kill and power-fail
testing first-class), accepting the weight. **Do not return to the flat-file
kernel** (falsified four times).

## Environment (non-secret)

**The run of record is 2026-07-27, on a MacBook Pro** (macOS 26.4.1, build
25E253), maintainer `danielegan` uid 501. Every row in `results.json` was
produced here. The 2026-07-23 mac mini run is superseded and retained only as
history below.

| Fact                      | Value                                                                                                                                                                                                                                                  |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Fixture revision          | **`c5eb8fd`** on `bestdan/elite-probe5-crash-kernel`, clean tree — the revision that PRODUCED the evidence, not HEAD. Recorded per row in `results.json` as `fixture_revision`/`fixture_dirty`, so the binding is checkable rather than asserted here. |
| Interpreter / SQLite      | Python **3.12.13**, SQLite **3.53.4**, Homebrew stock build via `/opt/homebrew/opt/python@3.12/bin/python3.12`                                                                                                                                         |
| Pragmas actually honoured | `journal_mode=wal`, `synchronous=2` (FULL), `fullfsync=1` — read from an on-disk DB                                                                                                                                                                    |
| Apple system SQLite       | **refused** — `kernel.assert_stock_sqlite()` fails closed on the CLT interpreter, per prior-art §1                                                                                                                                                     |
| launchd                   | real per-user domain `gui/501`, `KeepAlive`, `bootstrap` rc=0 **unsandboxed** (sandboxed `launchctl` and `ps` both fail; the orchestrator must run unsandboxed)                                                                                        |
| Incarnation identity      | Probe 2's libproc `p_uniqueid` reader, copied in unmodified; non-null on this host                                                                                                                                                                     |
| `kern.bootsessionuuid`    | present                                                                                                                                                                                                                                                |
| **`agent` uid**           | **PRESENT — `uid=502(agent) gid=20(staff) groups=staff,apagent`, not `admin`, zero sudo**                                                                                                                                                              |
| Containment domain used   | **`uid` — the real, escape-proof primitive**                                                                                                                                                                                                           |

The interpreter is not incidental. `python3.12` on this host first resolved to a
`mise` build under the maintainer's `0700` home, which the agent cannot traverse
— and the spawn/measure helpers exec the interpreter _as the agent_. Homebrew's
`python@3.12` was installed specifically so the binary sits on a world-traversable
path.

### The privileged surface (trust boundary)

Owner, mode and **sha256** per helper — all root-owned and not agent-writable,
verified with `stat` rather than `ls` (a token-compressing `ls` wrapper on this
host drops the owner column, which is the one field this check exists to read —
that is how the mac mini's boundary went unnoticed). The same values are recorded
per run under `environment.provenance.helpers` in `results.json`, so the surface
that produced the evidence is checkable and not merely transcribed here:

```
root:wheel drwxr-xr-x  /usr/local/probe5
root:wheel -rwxr-xr-x  p5-spawn    352b1c6bce3d2b1b45c11390850f78c1e9e7bf72b8816dfd92abf5a8df5ebc2e
root:wheel -rwxr-xr-x  p5-reap     ed20fe8ce6168ae6e5159e4763932a8870e50221694b759d6e4d959689e90812
root:wheel -rwxr-xr-x  p5-measure  df17c472563cd791ea69b7dd9d622c03333004e58e8db1a4e5b940bd08a76172
root:wheel -rwxr-xr-x  runsurrogate.py  a384d0300be8dd6edd905d63752d735b133736178ffc492fbb221946b542bd73
root:wheel -rwxr-xr-x  incarnation.py   1d3da09c2b55741b4ee3b702c0e964107f841439fb9ed96f60a0440594dc4731
```

`/etc/sudoers.d/probe5` (0440 root:wheel) grants the maintainer these three
commands as runas **`(agent)` only**, NOPASSWD, with `closefrom_override` so the
inherited report pipe (fd 3) and start gate (fd 4) survive `sudo`.

**`p5-measure` is not optional.** `proc_pidinfo` is EPERM across uids, so the
maintainer-side supervisor cannot verify an agent-uid run directly. Measured on
this host, same pid, two callers:

| Caller                           | Result                                         |
| -------------------------------- | ---------------------------------------------- |
| `sudo -u agent p5-measure <pid>` | `{"alive": true, "p_uniqueid": 14319054, ...}` |
| maintainer, directly             | `{"alive": false, "errno": 1}` (EPERM)         |

Without the helper the supervisor reads **every** healthy run as dead and reaps
it — the same false-reap class as the v5.2 defect, by another route, and it fails
_closed_, so the symptom is "nothing ever adopts" rather than an error.

### Superseded — the 2026-07-23 mac mini run

Retained because it explains the incident and the uid-by-name rule. On that host
`provisioning.md` and Probe 4's `results.json` both recorded `uid=502(agent)`
confirmed 2026-07-22, but by 07-23 the account was gone and **uid 502 belonged to
an unrelated human**. The containment primitive is `kill(-1)` executed _as_ that
uid, so a fixture pinning the number rather than the name would have signalled
every process of a live user's account. Every row in that run used the degraded
`gentoken` domain, which enumerates by scanning argv for the run's token — the
draft's _pre-registration discovery_ mechanism, **not a containment boundary**:
a descendant that `exec`s without the token is invisible to it.

That evidence does **not** transfer and none of it survives in `results.json`.
Note the uid differs by host (503 on the mini, 502 here) and this is not a
discrepancy to reconcile — the fixture resolves `agent` by name, and pinning
`PROBE5_AGENT_UID` by hand is the incident's mistake by another route.

## Results

Full evidence: [`results.json`](./results.json) (sanitized; the fixture handles no
credentials, so nothing required redaction beyond machine-specific paths).
Reproduce with `python3.12 scenarios.py` **unsandboxed**.

### Classification: **CONFIRMED** — with the kill sheet's meaning, not the word's

> **CONFIRMED**, defined by the kill sheet as **"not falsified in the tested
> process-crash environment"** and nothing more. Twenty-six executed
> process-crash/concurrency rows passed in the real uid containment domain, in one
> pass at fixture revision `c5eb8fd` with a clean tree. `Uid` is a documented
> limitation, not a falsifier. **Reboot/power-loss durability is untested and
> remains inconclusive by construction; this result makes no power-loss durability
> claim.** EIO proper (bad media) is likewise not injected.

Verdicts: **26 PASS / 1 DOCUMENTED-LIMITATION / 1 BLOCKED.**

Invariants **4 (earned release), 5 (orphan safe-stop, no false kill), 6 (reap
convergence or fence)** and **8 (sole writer)** have real evidence for the first
time: they quantify over a containment domain being verifiably emptied, and that
domain was exercised for real — `Esc` reaped an `exec`'d escapee invisible to
token scanning, `Churn` converged from 38 live processes, `Writer` got `EACCES`
from a genuinely separate uid. Under the degraded mode none were reachable.

#### What CONFIRMED here does not mean

1. **Not power-loss durable.** `PL` is inconclusive by construction and **never**
   passed. `SIGKILL` cannot prove power-loss durability, for SQLite exactly as for
   flat files. The win is inheriting SQLite's crash-VFS corpus, not a hand-built
   one. A green matrix says nothing about this row.
2. **Not full IO coverage.** `Io` now injects a genuine ENOSPC that struck _after_
   the WAL had been extended, and the transaction was atomic with
   `integrity_check` clean. Precisely scoped, that establishes
   **failure-after-writing-began** — _not_ that an individual `write(2)` was
   partial, since an earlier write may have succeeded and a later write, sync or
   allocation failed. Localising to a syscall needs a fault-injecting VFS or a
   trace. **EIO (bad media) follows distinct SQLite error paths and is untested.**
3. **Not portable evidence.** Every row is machine-specific. None of this
   transfers to another host; Part A must be redone per machine.
4. **Not a uid-changing containment claim.** A helper that acquires a different
   credential leaves the domain. Documented limitation → containment option 2.

#### Why the verdicts can be trusted more than the previous run's

The first pass at this classification was rejected twice on review, and the
rejections were right. Three specific weaknesses were fixed before this run:

- **`T11`/`T12` could not tell their boundary from its opposite.** Both used the
  generic "reached a safe shape" predicate, so a `takeover.pre_commit` crash that
  wrongly published g+1 — and a `takeover.post_commit` crash that wrongly rolled
  it back — would both still reach a safe terminal and both still PASS. They now
  assert the durable post-crash generation, lease state and full
  `generation_reserved` list, and were verified by feeding each row the other's
  outcome (both reject).
- **Reconciliation was never checked for inertness.** It runs at every supervisor
  start; a second pass that moved state would mean every launchd restart mutates
  the record, and the crash rows would never have seen it. All 12 now reconcile
  twice and require seq, event count and lease unchanged.
- **Recorded ≠ gated.** `Io` reported `wal_grew` without gating on it, so a future
  ENOSPC striking before any WAL write would still have passed while claiming the
  mid-write class. Now gated, with the filler's errno checked and verification
  reopening the database from disk.

An armed crash point with no row is now a hard error (`assert_crash_point_coverage`,
AST-based across every fixture module), because two takeover boundaries sat armed
and undriven from the day they were written and were found only by hand.

`Uid` (a uid-changing helper escaping containment) is explicitly a **documented
limitation pointing at containment option 2, not a falsifier**, and does not hold
the probe open.

A reader in a hurry should take this away: **25/28 is not a durability result.**
Nothing here tests power loss, and one required IO fault class was never injected.

| Row                                  | Verdict                                    | Evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------ | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T1 `prepare.pre_commit`              | PASS                                       | Whole-transaction rollback: **no lease and no event published at all**. Safe terminal by vacuity.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| T2 `prepare.post_commit` (G1)        | PASS                                       | `prepared` durable, nothing spawned; reconcile scanned zero → `launch_aborted` → terminal.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| T3 `spawn.post_spawn` (G2)           | PASS                                       | Live blocked _unrecorded_ child; supervisor death closed the gate, child exited on **EOF** ("exit, don't go"); reconcile verified zero → terminal.                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| T4 `activate.pre_commit`             | PASS                                       | Rollback; lease stayed `prepared`; recovered as G1.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| T5 `activate.post_commit` (G3)       | PASS                                       | `active` durable, gate never opened; child exited on EOF; reap-to-zero → terminal.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| T6 `stop_intent.pre_commit`          | PASS                                       | Rollback left a **healthy `active` run**; reconcile **re-adopted** it. Correctly _not_ killed — the stop is simply retried.                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| T7 `stop_intent.post_commit` (Saga1) | PASS                                       | Intent durable, nothing signalled; reconcile resumed the saga → zero → terminal.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| T8 `reap.post_zero` (Saga2)          | PASS                                       | Domain empty, `terminal` uncommitted; reconcile re-observed zero and terminalized. No double-release.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| T9 `terminalize.pre_commit`          | PASS                                       | Rollback; lease stayed fenced in `stop_intent`; retried to terminal.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| T10 `terminalize.post_commit`        | PASS                                       | Committed terminal; replay inert.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Idem                                 | PASS                                       | Replay returned the **existing seq**, `replayed=True`, and the counter did **not** advance (1→1); the next event took seq 2. No gap.                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| IdemConflict                         | PASS                                       | Same `idem_key` with a different payload raised and rolled the whole transaction back; counter unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Race                                 | PASS                                       | Two concurrent launches, **exactly one** rc=0, exactly one `generation_reserved`. Admission gate + `BEGIN IMMEDIATE`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Pid                                  | PASS                                       | A recorded tuple bearing a live pid but a foreign `p_uniqueid` was **refused after kqueue attach** ("identity changed after attach"); the genuine tuple adopted. Now exercised against a subject **inside the real containment domain** (an agent-uid run measured through `p5-measure`), not a maintainer-owned sleeper. _Real PID wraparound was not forced_ — this exercises the guard the reuse would trip.                                                                                                                                                                             |
| Priv                                 | PASS                                       | `signal_all` raises `ReaperRefused` when euid is 0. Exercised with a mocked euid; the fixture never acquires root.                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| NoConv                               | PASS                                       | A domain that never empties → `converged=False`, `terminalize()` **refused**, lease left fenced in `stop_intent`. Never terminalized on non-convergence.                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Io                                   | PASS                                       | Two sub-cases. (a) read-only dir: refusal before the first byte, atomic. (b) **genuine ENOSPC** on a 6MB HFS+ `hdiutil` volume filled to 40960 bytes (filler `errno=28` verified), then a transaction far larger than the remaining space: **WAL 70072 → 110592 bytes before the failure**, so the write had begun; transaction atomic (events 1→1, seq 1→1) and `integrity_check` **ok**, verified from a reopened connection. Gated on `wal_grew`, not merely reporting it. **Scope: establishes failure-after-writing-began, not a partial `write(2)`. EIO (bad media) still untested.** |
| Sup-readopt                          | PASS                                       | Supervisor SIGKILLed, launchd `KeepAlive` restarted it, reconciliation chose `readopt`; **run and its forked descendant both still alive**, lease still `active`, `stop_intent=0`. No false kill.                                                                                                                                                                                                                                                                                                                                                                                           |
| Sup-orphan                           | PASS                                       | Run SIGKILLed leaving a live descendant; reconciliation chose `reap_dead_run`, reaped to zero, terminalized. **Dead run ≠ dead workers** confirmed.                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| T11 `takeover.pre_commit`            | PASS                                       | Rollback: `stop_intent` had committed but **generation 2 was never published** (`generations_reserved=[1]`). Reconcile reaped to convergence → terminal.                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| T12 `takeover.post_commit`           | PASS                                       | `gen=2/prepared` durable, `generations_reserved=[1,2]`; reconcile took the `prepared` branch → `launch_aborted` → terminal. g+1 published but never activated, so no superseded generation was acted on.                                                                                                                                                                                                                                                                                                                                                                                    |
| Esc                                  | PASS                                       | A `setsid` + double-fork + **`exec`** descendant, invisible to token scanning (`escapee_hidden_from_token_scan=true`), was still reaped to zero by uid-wide `kill(-1)`. **The result degraded mode cannot produce.**                                                                                                                                                                                                                                                                                                                                                                        |
| Churn                                | PASS                                       | 38 live processes at reap time → converged to zero, lease terminal. A reaper that only signalled the set it enumerated would never finish.                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Writer                               | PASS                                       | `write_probe=EACCES` from the agent uid. **Sole-writer is now enforced, not architectural.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Uid                                  | **DOCUMENTED-LIMITATION**                  | A uid-changing helper leaves the domain. Not reachable from the agent's own authority here (`sudo -l -U agent` → not allowed), so it needs an external privileged spawner. Recorded as admitted-undetectable rather than claimed-detected. **Not a falsifier** (kill sheet) → containment option 2 if ever in scope.                                                                                                                                                                                                                                                                        |
| Tw                                   | PASS                                       | Three live processes in; reap converged; **`domain_at_publish=[]`** — generation g verified empty _at the moment_ g+1 was published, not merely afterwards. Order checked in `state.db`, not just end state.                                                                                                                                                                                                                                                                                                                                                                                |
| Sup-smoke                            | PASS                                       | Disarmed first contact (`KeepAlive=false`): one reconcile pass, `readopt`, run and descendant alive; supervisor then SIGKILLed and launchd confirmed **not** to relaunch it. Exercises the cross-uid `p5-measure` path from a real launchd job — which no other row touches.                                                                                                                                                                                                                                                                                                                |
| PL                                   | **BLOCKED / inconclusive by construction** | Reboot/power-loss. `SIGKILL` cannot prove it; no VM or loopback power-fail harness. Per the kill sheet this is **never** passed.                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

### Finding — a real defect in draft v5.1, caught by the fixture

The first `Sup-orphan` run failed, and it was the draft, not the harness. On the
supervisor's **first, entirely benign** reconciliation pass — before anything had
been killed — it logged:

```
domain_live=[23139, 23140]  alive_verified=true
unexpected_domain_procs=[23140]  →  action="reap_unaccounted_procs"
```

23140 was the healthy run's own forked child. v5.1's re-adopt condition requires
"the uid-scan shows no _unexpected_ extra agent processes" — but **a uid-wide scan
cannot distinguish a live run's legitimate descendants from a dead run's orphans**;
both are simply processes of the uid. Gating adoption on an empty scan therefore
false-reaps **every run that forks a worker**, on the first supervisor start —
exactly the failure monitored re-adoption was introduced (fifth pass, HIGH) to
prevent. Any real run forks workers, so this would fire essentially always.

**Fix applied (proposed as v5.2):** drop the "no unexpected extras" conjunct. The
recorded incarnation is the discriminator — if the run is alive and
`p_uniqueid`-verified, the extras are its children; if it is not, every process in
the domain is an orphan and is reaped regardless of how the roster looks (which
is the existing `reap_dead_run` path and preserves "dead run ≠ dead workers"). The
one-globally-active-lease admission gate is what makes this sound: no prior
generation can still be running. With the conjunct removed, Sup-readopt and
Sup-orphan both pass, and Sup-readopt was strengthened to fork a descendant so it
can no longer pass vacuously.

### What this does and does not establish

**Established** (in the tested process-crash environment, **real uid
containment**): invariants **1** (atomic transitions — 12/12 boundary crashes were
a clean rollback or a clean commit, now including both takeover boundaries),
**2** (gapless monotonic seq + idempotent replay), **3** (admission gate),
**7** (generation monotonic), and — new as of 2026-07-27, and the whole point of
the re-run — **4** (earned release), **5** (orphan safe-stop with no false kill),
**6** (reap convergence or fence) and **8** (sole writer, _enforced_ via `EACCES`
across a real uid boundary).

The v5.1 transaction kernel is buildable as specified and the old flat-file
failure class — exact-once ordered sequence under crash — did not reappear in any
row. `BEGIN IMMEDIATE` made it structural, as the redirect predicted.

**Not established:** **power-loss durability**, untestable here by construction and
_never_ passed; **EIO (bad media)**, which follows distinct SQLite error paths and
is not injected; and **syscall-level localisation** of the IO failure — `Io`
establishes that the failure came after writing began, not that a particular
`write(2)` was torn. A uid-changing helper remains outside the containment domain
— a documented limitation, not a gap in the evidence.

**Caveats a future reader should not lose:**

- Row evidence is **per-machine**. None of the mac mini's rows transfer, and none
  of these transfer to a third host. Part A must be re-done per machine.
- Three rows judged the reap on a **raw post-hoc domain scan**, which macOS
  refills with respawned per-user daemons after a converged reap; each produced a
  false result until it partitioned survivors the way invariant 4 does. `reap()`'s
  own rescan is the authority on convergence. Treat a raw-scan assertion in this
  fixture as a defect on sight.
- `Tw`'s `domain_at_publish` is still such a raw scan, in a millisecond window. It
  came back genuinely empty, so the row stands on the strictest reading — but a
  future failure there showing only system daemons is that confound, not a broken
  ordering guarantee.

### Closing this probe — done

**The probe is closed as of 2026-07-27, teardown included.** The record of how it
was run — host provisioning, the agent checks, per-row pass bars, and teardown —
is **[`probe5-todo.md`](./probe5-todo.md)**. That file is history now, not
instructions: Part E removed the helper tree, the sudoers fragment and every
rundir, so none of it is runnable without redoing Part A from scratch on a fresh
host.

**Done as of 2026-07-27** (all on the MacBook; see `probe5-todo.md` Part A for the
per-machine provisioning record): the `agent` account, the three scoped
`sudo -u agent` helpers with `closefrom_override`, the TAKEOVER transition
(`kernel.takeover_publish`, with crash points either side now driven by T11/T12),
and the full 28-row re-run under `PROBE5_DOMAIN_MODE=uid`.

**Done since:** the `Io` ENOSPC harness; the armed-crash-point coverage check
(AST-based, aliases mapped: G1–G3 = T2/T3/T5, Saga1/2 = T7/T8, Sup-benign =
Sup-readopt + Sup-smoke); the takeover boundary rows T11/T12; per-row revision and
helper provenance in `results.json`; and the single-revision run of record.

**Also done — the four items that were open at classification time:**

1. **The v5.2 reconciliation fix is ratified** in `draft-state-machine.md`
   (`37fcf01`). `Sup-readopt` gave it real uid-domain evidence: the domain held
   three processes at reconcile time and the supervisor adopted anyway, which is
   the entire content of the fix.
2. **§7a row 5** of `dev_docs/auto-pilot-e-lite-design-2026-07-21.md` was brought
   in line with the probe it describes (`95e906c`).
3. **Probes 1 and 4 no longer assert the reassigned uid as fact** (`e96a046`,
   `73ef132`). Both had certified an `agent` account on a host where that uid was
   later reassigned to a human.
4. **Part E teardown is complete** (2026-07-27). The `agent` account was left in
   place, deliberately — deleting and recreating it is what produced the
   uid-reassignment hazard in the first place.

**Standing constraint for dependent Stage-2 work:** nothing here licenses an
assumption of power-loss durability or of EIO behaviour. Invariants 4/5/6/8 are
supported by real uid-domain evidence and are no longer the blocker they were on
07-23.
