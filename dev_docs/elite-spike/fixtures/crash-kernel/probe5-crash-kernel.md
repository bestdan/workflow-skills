# Probe 5 — Baseline crash-transaction kernel

**Flat-file kernel: FALSIFIED → storage redirect taken. v5.1 fixture RUN
2026-07-23 → INCONCLUSIVE** — the transaction kernel held across every injection
(10/10 boundary crashes, idempotency, admission race, re-adoption, orphan reap),
but the dedicated `agent` uid the containment half depends on **no longer exists
on the host**, so invariants 4/5/6 were never exercised and six rows are BLOCKED.
The fixture also caught a real v5.1 defect: the re-adopt rule false-reaps any run
that forks a worker (see Results → Finding). Four review rounds falsified the hand-rolled flat-file design (v1–v4)
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
escaping containment is a *documented limitation* (→ option 2), not a falsifier.
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
*outside* the transaction → `terminal` commit **only after uid==zero**; and a
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

Executed 2026-07-23 on the mac mini (macOS 26.4.1), maintainer uid 501.

| Fact | Value |
| --- | --- |
| SQLite | **3.53.3, Homebrew stock build** via `/opt/homebrew/opt/python@3.12/bin/python3.12` |
| Pragmas actually honoured | `journal_mode=wal`, `synchronous=2` (FULL), `fullfsync=1` |
| Apple system SQLite | **refused** — `kernel.assert_stock_sqlite()` fails closed on the CLT interpreter (3.51.0), per prior-art §1 |
| launchd | real per-user domain `gui/501`, `KeepAlive`, `bootstrap` rc=0 **unsandboxed** (sandboxed `launchctl` and `ps` both fail; the orchestrator must run unsandboxed) |
| Incarnation identity | Probe 2's libproc `p_uniqueid` reader, copied in unmodified |
| `kern.bootsessionuuid` | present |
| **`agent` uid** | **ABSENT — see the blocker below** |
| Containment domain used | **`gentoken` (DEGRADED, not escape-proof)** |

### Blocker — the dedicated `agent` uid does not exist

`provisioning.md` and Probe 4's `results.json` both record `uid=502(agent)`
confirmed on 2026-07-22. On the same host on 2026-07-23:

```
id agent            → no such user
dscl . -list /Users → daemon 1, danielegan 501, jennygrange 502, nobody -2, root 0
/Users/agent        → No such file or directory
/Groups/apagent     → eDSRecordNotFound
```

**uid 502 now belongs to `jennygrange`, an unrelated human account.** This is not
only a missing prerequisite but a hazard: the draft's containment primitive is
`kill(-1)` executed *as uid 502*, so a fixture that pinned the number rather than
the name would signal every process of a live user's account. No uid-wide kill
was run. The fixture pins the domain by configuration and refuses to signal as
root (row Priv).

Consequence: the fixture carries a second domain mode, `gentoken`, which
enumerates by scanning argv for the run's token. That is the draft's
*pre-registration discovery* mechanism, **not a containment boundary** — a
descendant that `exec`s without the token, or that the scan races, is invisible
to it. Every row below ran in this degraded mode.

## Results

Full evidence: [`results.json`](./results.json) (sanitized; the fixture handles no
credentials, so nothing required redaction beyond machine-specific paths).
Reproduce with `python3.12 scenarios.py` **unsandboxed**.

### Classification: **INCONCLUSIVE**

Not falsified — but not confirmed either, and the gap is not a technicality. The
pass threshold requires **all eight invariants** to hold. Invariants **4 (earned
release), 5 (orphan safe-stop, no false kill) and 6 (reap convergence or fence)**
are all statements about a containment domain being verifiably emptied, and the
escape-proof domain those invariants are written against was unavailable. What
the degraded mode can show is that the *logic* gated on `domain == zero` behaves
correctly; what it cannot show is that `domain == zero` means what the draft needs
it to mean. Per rule 3, a contention/reap-convergence case that cannot be forced
is inconclusive, not a pass.

| Row | Verdict | Evidence |
| --- | --- | --- |
| T1 `prepare.pre_commit` | PASS | Whole-transaction rollback: **no lease and no event published at all**. Safe terminal by vacuity. |
| T2 `prepare.post_commit` (G1) | PASS | `prepared` durable, nothing spawned; reconcile scanned zero → `launch_aborted` → terminal. |
| T3 `spawn.post_spawn` (G2) | PASS | Live blocked *unrecorded* child; supervisor death closed the gate, child exited on **EOF** ("exit, don't go"); reconcile verified zero → terminal. |
| T4 `activate.pre_commit` | PASS | Rollback; lease stayed `prepared`; recovered as G1. |
| T5 `activate.post_commit` (G3) | PASS | `active` durable, gate never opened; child exited on EOF; reap-to-zero → terminal. |
| T6 `stop_intent.pre_commit` | PASS | Rollback left a **healthy `active` run**; reconcile **re-adopted** it. Correctly *not* killed — the stop is simply retried. |
| T7 `stop_intent.post_commit` (Saga1) | PASS | Intent durable, nothing signalled; reconcile resumed the saga → zero → terminal. |
| T8 `reap.post_zero` (Saga2) | PASS | Domain empty, `terminal` uncommitted; reconcile re-observed zero and terminalized. No double-release. |
| T9 `terminalize.pre_commit` | PASS | Rollback; lease stayed fenced in `stop_intent`; retried to terminal. |
| T10 `terminalize.post_commit` | PASS | Committed terminal; replay inert. |
| Idem | PASS | Replay returned the **existing seq**, `replayed=True`, and the counter did **not** advance (1→1); the next event took seq 2. No gap. |
| IdemConflict | PASS | Same `idem_key` with a different payload raised and rolled the whole transaction back; counter unchanged. |
| Race | PASS | Two concurrent launches, **exactly one** rc=0, exactly one `generation_reserved`. Admission gate + `BEGIN IMMEDIATE`. |
| Pid | PASS | A recorded tuple bearing a live pid but a foreign `p_uniqueid` was **refused after kqueue attach** ("identity changed after attach"); the genuine tuple adopted. *Real PID wraparound was not forced* — this exercises the guard the reuse would trip. |
| Priv | PASS | `signal_all` raises `ReaperRefused` when euid is 0. Exercised with a mocked euid; the fixture never acquires root. |
| NoConv | PASS | A domain that never empties → `converged=False`, `terminalize()` **refused**, lease left fenced in `stop_intent`. Never terminalized on non-convergence. |
| Io | **INCONCLUSIVE** | Only a *read-only-database* refusal was injected, which fails before the first byte. That sub-case failed atomically (counter and event count unchanged), but genuine **ENOSPC/EIO fail mid-write**, potentially after the WAL has been extended — a different class, and untested. No small-disk-image or fault-injecting VFS harness was built. |
| Sup-readopt | PASS | Supervisor SIGKILLed, launchd `KeepAlive` restarted it, reconciliation chose `readopt`; **run and its forked descendant both still alive**, lease still `active`, `stop_intent=0`. No false kill. |
| Sup-orphan | PASS | Run SIGKILLed leaving a live descendant; reconciliation chose `reap_dead_run`, reaped to zero, terminalized. **Dead run ≠ dead workers** confirmed. |
| Esc | **BLOCKED** | A `setsid` + double-fork descendant escapes gen_token scanning by construction. Only uid-wide `kill(-1)` catches it. |
| Churn | **BLOCKED** | Fork-churn convergence is only meaningful against an escape-proof domain; "converged" would otherwise be an artifact of the scan. |
| Writer | **BLOCKED** | Proving the run *cannot* write `state.db` needs a second uid. Same-uid, sole-writer is architectural here, not enforced. |
| Uid | **BLOCKED** | The uid-changing-helper limitation is not observable without the dedicated uid. |
| Tw | **BLOCKED ×2** | Needs an escape-proof domain **and** the TAKEOVER transition, which is not implemented (`kernel.py` has no `takeover_publish`). |
| PL | **BLOCKED / inconclusive by construction** | Reboot/power-loss. `SIGKILL` cannot prove it; no VM or loopback power-fail harness. Per the kill sheet this is **never** passed. |

### Finding — a real defect in draft v5.1, caught by the fixture

The first `Sup-orphan` run failed, and it was the draft, not the harness. On the
supervisor's **first, entirely benign** reconciliation pass — before anything had
been killed — it logged:

```
domain_live=[23139, 23140]  alive_verified=true
unexpected_domain_procs=[23140]  →  action="reap_unaccounted_procs"
```

23140 was the healthy run's own forked child. v5.1's re-adopt condition requires
"the uid-scan shows no *unexpected* extra agent processes" — but **a uid-wide scan
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

**Established** (in the tested process-crash environment, degraded containment):
invariants **1** (atomic transitions — 10/10 boundary crashes were a clean
rollback or a clean commit), **2** (gapless monotonic seq + idempotent replay),
**3** (admission gate), **7** (generation monotonic), and the *logic* of 4/5/6.
The v5.1 transaction kernel is buildable as specified and the old flat-file
failure class — exact-once ordered sequence under crash — did not reappear in any
row. `BEGIN IMMEDIATE` made it structural, as the redirect predicted.

**Not established:** invariants **4, 5, 6** in the sense the kill sheet requires,
because the containment boundary they quantify over was never exercised; **8**
(sole writer) as an *enforced* property; power-loss durability (untestable here by
construction); and genuine mid-write IO failure.

### Required to close this probe

Full resume instructions, written to be machine-portable (the work may continue on
a different Mac, where none of Probes 1–4's evidence carries over):
**[`probe5-todo.md`](./probe5-todo.md)** — host provisioning and the agent checks,
the fixture changes each blocked row needs, per-row pass bars, and teardown.

In brief:

1. Re-provision the `agent` account — **by name, and not uid 502**, which is taken
   — then re-verify Probe 1's and Probe 4's evidence, both of which certify an
   account that has since vanished.
2. Add the scoped `sudo -u agent` spawn/reap helper (Stage-2 sudoers) and pin the
   exact invocation; it is a runtime privileged-mediation dependency. Note `sudo`
   closes fds ≥3 by default, which would destroy the inherited report pipe and
   start gate — `closefrom_override` is required.
3. Implement the TAKEOVER transition (absent from `kernel.py`).
4. Re-run with `PROBE5_DOMAIN_MODE=uid` to turn on the real `kill(-1)` path, and
   complete Esc, Churn, Writer, Uid, Tw — **plus the 19 passing rows**, whose
   evidence was gathered under a different domain implementation and does not
   transfer unexamined.
5. Ratify the v5.2 reconciliation fix above.

Until (1)–(5), **no dependent Stage-2 work should be built on invariants 4/5/6.**
