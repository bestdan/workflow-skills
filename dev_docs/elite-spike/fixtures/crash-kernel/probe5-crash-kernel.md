# Probe 5 — Baseline crash-transaction kernel

**Flat-file kernel: FALSIFIED → storage redirect taken. v5.1 fixture RE-RUN
2026-07-27 under the real uid domain → still INCONCLUSIVE, but for a different
and much smaller reason.** The 2026-07-23 run was inconclusive because the
dedicated `agent` uid did not exist, so invariants 4/5/6 were never exercised and
six rows were BLOCKED. That gap is now closed: a dedicated agent account was
provisioned on a second machine and **all 28 rows were re-earned against the
escape-proof uid domain** — 25 PASS, and no degraded-mode evidence remains in
`results.json`. What still blocks confirmation is narrower: **mid-write ENOSPC/EIO
is untested**, and **reboot/power-loss is untested by construction and can never
be passed here**. See Results → Classification; do not read 25/28 as covering
durability. The fixture also caught a real v5.1 defect: the re-adopt rule
false-reaps any run that forks a worker (see Results → Finding).
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

| Fact                      | Value                                                                                                                                                           |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Fixture revision          | `3ea2d0f` on `bestdan/elite-probe5-crash-kernel`                                                                                                                |
| Interpreter / SQLite      | Python **3.12.13**, SQLite **3.53.4**, Homebrew stock build via `/opt/homebrew/opt/python@3.12/bin/python3.12`                                                  |
| Pragmas actually honoured | `journal_mode=wal`, `synchronous=2` (FULL), `fullfsync=1` — read from an on-disk DB                                                                             |
| Apple system SQLite       | **refused** — `kernel.assert_stock_sqlite()` fails closed on the CLT interpreter, per prior-art §1                                                              |
| launchd                   | real per-user domain `gui/501`, `KeepAlive`, `bootstrap` rc=0 **unsandboxed** (sandboxed `launchctl` and `ps` both fail; the orchestrator must run unsandboxed) |
| Incarnation identity      | Probe 2's libproc `p_uniqueid` reader, copied in unmodified; non-null on this host                                                                              |
| `kern.bootsessionuuid`    | present                                                                                                                                                         |
| **`agent` uid**           | **PRESENT — `uid=502(agent) gid=20(staff) groups=staff,apagent`, not `admin`, zero sudo**                                                                       |
| Containment domain used   | **`uid` — the real, escape-proof primitive**                                                                                                                    |

The interpreter is not incidental. `python3.12` on this host first resolved to a
`mise` build under the maintainer's `0700` home, which the agent cannot traverse
— and the spawn/measure helpers exec the interpreter _as the agent_. Homebrew's
`python@3.12` was installed specifically so the binary sits on a world-traversable
path.

### The privileged surface (trust boundary)

All root-owned and not agent-writable, verified with `stat` rather than `ls`
(a token-compressing `ls` wrapper on this host drops the owner column, which is
the one field this check exists to read — that is how the mac mini's boundary went
unnoticed):

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

### Classification: **INCONCLUSIVE**

> **INCONCLUSIVE.** Twenty-five executed process-crash/concurrency rows passed in
> the real uid containment domain. Mid-write ENOSPC/EIO remains untested. Reboot/
> power-loss durability is untested and remains inconclusive by construction; this
> result makes no power-loss durability claim.

Not falsified, and no longer inconclusive for the 07-23 reason. Invariants **4
(earned release), 5 (orphan safe-stop, no false kill), 6 (reap convergence or
fence)** and **8 (sole writer)** now have real evidence: they quantify over a
containment domain being verifiably emptied, and that domain was exercised for
real — `Esc` reaped an `exec`'d escapee invisible to token scanning, `Churn`
converged from 38 live processes, `Writer` got `EACCES` from a genuinely separate
uid. Under the degraded mode none of those were reachable.

**What still holds it open**, per the kill sheet as written rather than as we
would like it:

1. **`Io`.** The Method names IO as a required matrix row. Only a _read-only
   database_ refusal was injected, which fails before the first byte. Genuine
   ENOSPC/EIO fails **mid-write**, potentially after the WAL has been extended —
   a different class. Only power-loss carries an explicit permanent exemption, so
   this should be either tested or excluded by a **ratified scope amendment**, not
   silently downgraded to a caveat.
2. **`PL`.** Inconclusive by construction and **never** passed. `SIGKILL` cannot
   prove power-loss durability, for SQLite exactly as for flat files. The win is
   inheriting SQLite's crash-VFS corpus, not a hand-built one.

`Uid` (a uid-changing helper escaping containment) is explicitly a **documented
limitation pointing at containment option 2, not a falsifier**, and does not hold
the probe open.

A reader in a hurry should take this away: **25/28 is not a durability result.**
Nothing here tests power loss, and one required IO fault class was never injected.

| Row                                  | Verdict                                    | Evidence                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------ | ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T1 `prepare.pre_commit`              | PASS                                       | Whole-transaction rollback: **no lease and no event published at all**. Safe terminal by vacuity.                                                                                                                                                                                                                                                                                                               |
| T2 `prepare.post_commit` (G1)        | PASS                                       | `prepared` durable, nothing spawned; reconcile scanned zero → `launch_aborted` → terminal.                                                                                                                                                                                                                                                                                                                      |
| T3 `spawn.post_spawn` (G2)           | PASS                                       | Live blocked _unrecorded_ child; supervisor death closed the gate, child exited on **EOF** ("exit, don't go"); reconcile verified zero → terminal.                                                                                                                                                                                                                                                              |
| T4 `activate.pre_commit`             | PASS                                       | Rollback; lease stayed `prepared`; recovered as G1.                                                                                                                                                                                                                                                                                                                                                             |
| T5 `activate.post_commit` (G3)       | PASS                                       | `active` durable, gate never opened; child exited on EOF; reap-to-zero → terminal.                                                                                                                                                                                                                                                                                                                              |
| T6 `stop_intent.pre_commit`          | PASS                                       | Rollback left a **healthy `active` run**; reconcile **re-adopted** it. Correctly _not_ killed — the stop is simply retried.                                                                                                                                                                                                                                                                                     |
| T7 `stop_intent.post_commit` (Saga1) | PASS                                       | Intent durable, nothing signalled; reconcile resumed the saga → zero → terminal.                                                                                                                                                                                                                                                                                                                                |
| T8 `reap.post_zero` (Saga2)          | PASS                                       | Domain empty, `terminal` uncommitted; reconcile re-observed zero and terminalized. No double-release.                                                                                                                                                                                                                                                                                                           |
| T9 `terminalize.pre_commit`          | PASS                                       | Rollback; lease stayed fenced in `stop_intent`; retried to terminal.                                                                                                                                                                                                                                                                                                                                            |
| T10 `terminalize.post_commit`        | PASS                                       | Committed terminal; replay inert.                                                                                                                                                                                                                                                                                                                                                                               |
| Idem                                 | PASS                                       | Replay returned the **existing seq**, `replayed=True`, and the counter did **not** advance (1→1); the next event took seq 2. No gap.                                                                                                                                                                                                                                                                            |
| IdemConflict                         | PASS                                       | Same `idem_key` with a different payload raised and rolled the whole transaction back; counter unchanged.                                                                                                                                                                                                                                                                                                       |
| Race                                 | PASS                                       | Two concurrent launches, **exactly one** rc=0, exactly one `generation_reserved`. Admission gate + `BEGIN IMMEDIATE`.                                                                                                                                                                                                                                                                                           |
| Pid                                  | PASS                                       | A recorded tuple bearing a live pid but a foreign `p_uniqueid` was **refused after kqueue attach** ("identity changed after attach"); the genuine tuple adopted. Now exercised against a subject **inside the real containment domain** (an agent-uid run measured through `p5-measure`), not a maintainer-owned sleeper. _Real PID wraparound was not forced_ — this exercises the guard the reuse would trip. |
| Priv                                 | PASS                                       | `signal_all` raises `ReaperRefused` when euid is 0. Exercised with a mocked euid; the fixture never acquires root.                                                                                                                                                                                                                                                                                              |
| NoConv                               | PASS                                       | A domain that never empties → `converged=False`, `terminalize()` **refused**, lease left fenced in `stop_intent`. Never terminalized on non-convergence.                                                                                                                                                                                                                                                        |
| Io                                   | **INCONCLUSIVE**                           | Only a _read-only-database_ refusal was injected, which fails before the first byte. That sub-case failed atomically (counter and event count unchanged), but genuine **ENOSPC/EIO fail mid-write**, potentially after the WAL has been extended — a different class, and untested. No small-disk-image or fault-injecting VFS harness was built.                                                               |
| Sup-readopt                          | PASS                                       | Supervisor SIGKILLed, launchd `KeepAlive` restarted it, reconciliation chose `readopt`; **run and its forked descendant both still alive**, lease still `active`, `stop_intent=0`. No false kill.                                                                                                                                                                                                               |
| Sup-orphan                           | PASS                                       | Run SIGKILLed leaving a live descendant; reconciliation chose `reap_dead_run`, reaped to zero, terminalized. **Dead run ≠ dead workers** confirmed.                                                                                                                                                                                                                                                             |
| T11 `takeover.pre_commit`            | PASS                                       | Rollback: `stop_intent` had committed but **generation 2 was never published** (`generations_reserved=[1]`). Reconcile reaped to convergence → terminal.                                                                                                                                                                                                                                                        |
| T12 `takeover.post_commit`           | PASS                                       | `gen=2/prepared` durable, `generations_reserved=[1,2]`; reconcile took the `prepared` branch → `launch_aborted` → terminal. g+1 published but never activated, so no superseded generation was acted on.                                                                                                                                                                                                        |
| Esc                                  | PASS                                       | A `setsid` + double-fork + **`exec`** descendant, invisible to token scanning (`escapee_hidden_from_token_scan=true`), was still reaped to zero by uid-wide `kill(-1)`. **The result degraded mode cannot produce.**                                                                                                                                                                                            |
| Churn                                | PASS                                       | 38 live processes at reap time → converged to zero, lease terminal. A reaper that only signalled the set it enumerated would never finish.                                                                                                                                                                                                                                                                      |
| Writer                               | PASS                                       | `write_probe=EACCES` from the agent uid. **Sole-writer is now enforced, not architectural.**                                                                                                                                                                                                                                                                                                                    |
| Uid                                  | **DOCUMENTED-LIMITATION**                  | A uid-changing helper leaves the domain. Not reachable from the agent's own authority here (`sudo -l -U agent` → not allowed), so it needs an external privileged spawner. Recorded as admitted-undetectable rather than claimed-detected. **Not a falsifier** (kill sheet) → containment option 2 if ever in scope.                                                                                            |
| Tw                                   | PASS                                       | Three live processes in; reap converged; **`domain_at_publish=[]`** — generation g verified empty _at the moment_ g+1 was published, not merely afterwards. Order checked in `state.db`, not just end state.                                                                                                                                                                                                    |
| Sup-smoke                            | PASS                                       | Disarmed first contact (`KeepAlive=false`): one reconcile pass, `readopt`, run and descendant alive; supervisor then SIGKILLed and launchd confirmed **not** to relaunch it. Exercises the cross-uid `p5-measure` path from a real launchd job — which no other row touches.                                                                                                                                    |
| PL                                   | **BLOCKED / inconclusive by construction** | Reboot/power-loss. `SIGKILL` cannot prove it; no VM or loopback power-fail harness. Per the kill sheet this is **never** passed.                                                                                                                                                                                                                                                                                |

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

**Not established:** genuine **mid-write IO failure** (`Io` injected only a
pre-write refusal), and **power-loss durability**, which is untestable here by
construction and is _never_ passed. A uid-changing helper remains outside the
containment domain — a documented limitation, not a gap in the evidence.

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

### Required to close this probe

Full resume instructions, written to be machine-portable (the work may continue on
a different Mac, where none of Probes 1–4's evidence carries over):
**[`probe5-todo.md`](./probe5-todo.md)** — host provisioning and the agent checks,
the fixture changes each blocked row needs, per-row pass bars, and teardown.

**Done as of 2026-07-27** (all on the MacBook; see `probe5-todo.md` Part A for the
per-machine provisioning record): the `agent` account, the three scoped
`sudo -u agent` helpers with `closefrom_override`, the TAKEOVER transition
(`kernel.takeover_publish`, with crash points either side now driven by T11/T12),
and the full 28-row re-run under `PROBE5_DOMAIN_MODE=uid`.

**Still required to close:**

1. **`Io`** — build a genuine mid-write ENOSPC/EIO harness (`hdiutil` image or a
   fault-injecting VFS), **or** ratify a scope amendment excluding it. It is a
   named matrix row; only power-loss carries an explicit permanent exemption, so
   it must not be silently downgraded to a caveat.
2. **A coverage check that fails when an armed crash point has no row.** The
   takeover boundaries sat armed and undriven from the day they were written and
   were found by hand. Aliases should be mapped explicitly: G1–G3 = T2/T3/T5,
   Saga1/2 = T7/T8, Sup-benign = Sup-readopt + Sup-smoke.
3. **Ratify the v5.2 reconciliation fix** above. `Sup-readopt` now gives it real
   evidence under the uid domain: the domain held three processes at reconcile
   time and the supervisor adopted anyway, which is the whole content of the fix.
4. **Re-verify Probes 1 and 4.** Both certify an `agent` account on a host where
   that uid was later reassigned to a human; their evidence currently asserts
   something untrue of that machine.
5. **Part E teardown** — see `probe5-todo.md`. Leave the `agent` account in place.

Until (1)–(3), **no dependent Stage-2 work should assume power-loss durability or
mid-write IO atomicity.** Invariants 4/5/6/8 are now supported by real uid-domain
evidence and are no longer the blocker they were on 07-23.
