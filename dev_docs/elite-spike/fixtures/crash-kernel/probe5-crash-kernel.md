# Probe 5 — Baseline crash-transaction kernel

**Result: PENDING (v3).** Draft + kill sheet **co-reviewed twice** (codex + Fable,
`coreview-2026-07-22.md`): the first pass falsified v1, codex's second pass
falsified v2 (auto-reap contradicted the transaction-lock split). **No fixture
run yet.** The draft is [`draft-state-machine.md`](./draft-state-machine.md) — v3
resolves the contradiction with a **run-long liveness lock** (`O_CLOEXEC`,
separate from the short-lived transaction lock) as the sound orphan-proof, plus
`gen_token`-tagged session containment, a durable generation-reservation record +
seq-head, an explicit recovery table, and a de-bundled one-crash-point-per-row
matrix. Several items are **design-doc deltas** (below) awaiting ratification.

Disposable spike under §0a's contract (rule 4 — never promoted by renaming). Runs
**in a disposable directory**, not `/usr/local/autopilot`. No tmux, GitHub, or
Claude coupling — this probe falsifies the state model **in isolation**, before
those seams exist. Surrogate "runs" are trivial processes; no real credentials.

## Kill sheet (from §7a, priority 5)

### Key assumption / falsifier

> _The baseline control-plane state model has **one recoverable outcome under
> crash and concurrency** — every outcome a **safe terminal or a safe hold
> needing no mid-run human action** (Decision #5) — before it is coupled to tmux,
> GitHub, or Claude._

**Any invariant failure falsifies this draft** — decoupled from the redirect
question (co-review pass-bar fix). Concretely, falsified if any matrix injection
yields: a torn read; a clobber of a live owner; an untracked live process; a
double-release; a **protocol-emitted** duplicate/gap `seq` (not merely detected —
emitted); a superseded generation acted on; a mis-targeted kill (PID reuse
escaping the containment fence); a crash-persistent lock needing manual removal;
or any outcome that requires a human **mid-run** (an *alive-but-hung* launcher
escalating to a human **alert** is a hang, not a crash outcome, and is allowed).
Whether a *revised* flat-file design can hold the invariants is a **separate**
conclusion that determines the redirect — it does not gate falsification.

### Method

In a disposable directory, implement **only** the v3 kernel: `prepared → active →
terminal`, generation replacement/takeover, the **watcher-triggered `ap-stop
--reap`** of a crashed-active orphan, and registry append — using three permanent
`O_CLOEXEC` lock inodes (**liveness** held for the whole run, short-lived
**transaction**, global **registry**; total order liveness→transaction→registry),
the `F_FULLFSYNC` atomic-publication primitive for owner/seq-head, **O_APPEND +
`seq.head`** for registry lines, incarnation identity (`p_uniqueid`+start+exe+`sid`
+`kern.bootsessionuuid`), `gen_token`-tagged **session** containment, and the
recovery table. Then run the full **v3 matrix** (draft §Fault-injection matrix),
**one deterministic crash point per row** via armed crash-points
(`PROBE5_CRASH_AT=<row>` → `os._exit()`): the boundary crashes (A–E3), contention
(H, I), the reap paths incl. the **false-reap guard R5**, the session/PID-reuse
fences (R4a/R4b), the alive-but-hung refusal (R3), `claimed_exit` (K), the
registry/seq races (S1–S4), and the adversarial/syscall/re-crash/ABA rows
(X1–X5) — checking every invariant (1–11) after each.

### Pass threshold

Across the **entire v2 matrix**: all ten invariants hold, and **every**
crash/contention outcome is a **safe terminal or a safe hold that needs no
mid-run human action** — a crashed-active orphan is **auto-reaped** with no human
(the only human path is an alive-but-hung launcher → alert). Specifically: no
torn read; no clobber of a live owner; no untracked live process; fail-closed
contention; release only after a durable `observed_terminal` **and** run+workers
proven dead **by incarnation**, idempotently, and a `claimed_exit` never
releases; generation HWM-derived and superseded records inert; the protocol
**never emits** a duplicate/gap `seq` (a detected external corruption alerts and
the reader fails closed); and identity-bound termination never mis-targets a
reused PID. The best positive result is **"not falsified in the tested
(process-crash) environment"** — never "proven."

### Inconclusive condition (rule 3)

Classify **inconclusive**, not pass, if: a boundary crash can't be injected
**deterministically** at the intended point; or a contention race can't be
forced (serialization untested). And — always partially inconclusive by
construction (co-review #4) — **reboot / power-loss durability is untested**:
`F_FULLFSYNC` is what the protocol is **designed for**, but SIGKILL cannot prove
it and the fixture runs no real power-fail harness, so those guarantees are
**inconclusive, never passed**. Also inconclusive if the fixture can't prove
**which fd owns the liveness lock** (that `O_CLOEXEC` holds, X3) or can't force
**repeated crash-during-reap** (X4).

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

## Design-doc deltas (need §4/§5.1 ratification before the measured revision)

The co-review surfaced items that change the **production** spec, not just the
spike. Flagged for sign-off:

1. **Multi-lock model (§4.1).** §4.1 currently describes a single lock held
   "through the entire run" *and* per-transition — the contradiction codex found.
   Ratify the split: a **run-long `O_CLOEXEC` liveness lock** (the orphan-proof)
   distinct from the short-lived **transaction lock**, plus the global **registry
   lock**, total order liveness→transaction→registry.
2. **Generation-reservation record + seq-head (§4.2).** Add `generation_reserved`
   as a record type (durable `g` source of truth) and a `registry/seq.head` HWM;
   registry appends become **O_APPEND + seq-head**, not whole-file rewrite.
3. **Session-anchored containment + `gen_token` (§4.1, §3).** Runs spawn as
   session leaders carrying a `gen_token`; state explicitly that **hostile in-run
   escape is fenced by the Stage-2 uid/sandbox, not the kernel** (the kernel
   narrows + detects).
4. **Recovery table (§4.1)** as the normative reconciliation spec the measured
   revision must enumerate.
5. **Reap authority (§5.1).** The watcher never signals a process, but **may
   trigger a generation-scoped `ap-stop --reap`** on kernel-defined conditions;
   `ap-stop` stays the sole terminalization mechanism.

### Dependent work gated on this probe

The Stage-2 control plane: `ap-launch`, `ap-stop`, the watcher's lease/registry
transitions, and the typed registry writer are built **around** this proven
kernel. A falsified kernel blocks that build.

### Redirect if falsified

Simplify the protocol, or evaluate a **transactional store (SQLite** with its own
WAL/atomic commit) before writing `ap-launch`/`ap-stop`/the watcher. Do not build
the control plane around a flat-file protocol that can't hold the invariants.

## Environment (non-secret)

_To be filled at execution._

## Results

_To be filled at execution._
