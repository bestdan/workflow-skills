# Probe 5 — Baseline crash-transaction kernel

**Flat-file kernel: FALSIFIED → storage redirect taken. Now PENDING (v5.1),
buildable.** Four review rounds falsified the hand-rolled flat-file design (v1–v4)
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

_To be filled at execution._

## Results

_To be filled at execution._
