# Probe 5 — Baseline crash-transaction kernel

**Result: PENDING (v2).** Draft + kill sheet written and **co-reviewed** (codex +
Fable, `coreview-2026-07-22.md`), which falsified v1 on paper; **no fixture run
yet**. Fixture is built only after this v2 sheet is approved (§7a rule 1). The
draft is [`draft-state-machine.md`](./draft-state-machine.md) — v2 encodes the
crashed-active **auto-reap** (decision (b)), the transaction-lock / lease-record
split, incarnation-fenced termination, idempotent recovery, a global registry
lock, an explicit process-crash threat model, and the expanded matrix.

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

In a disposable directory, implement **only** the v2 kernel: `prepared → active →
terminal`, generation replacement/takeover, the **watcher-triggered `ap-stop
--reap`** of a crashed-active orphan, and registry append — using separate
permanent `flock` lock inodes (transaction lock vs durable lease record), the
`F_FULLFSYNC` atomic-publication primitive, incarnation identity
(`p_uniqueid`+start+exe+`kern.bootsessionuuid`), and a per-run containment
process group. Then run the full **v2 matrix** (draft §Fault-injection matrix):
SIGKILL at every durable boundary (A–G), contention/clobber (H–I), the reap paths
(R1–R4, incl. the kill-TOCTOU fence and the alive-but-hung refusal),
`claimed_exit` (K), the global-`seq` races (S1–S3), and the **adversarial /
syscall-failure / re-crash** rows (X1–X5) — checking every invariant (1–10) after
each. Boundary crashes are injected **deterministically** via armed crash-points
(`PROBE5_CRASH_AT=<row>` → `os._exit()` at that exact point).

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
`F_FULLFSYNC` makes the protocol correct, but SIGKILL cannot prove it and the
fixture runs no real power-fail harness, so those guarantees are **inconclusive,
never passed**. Also inconclusive if the fixture can't prove **which
process/descriptor owns the lock** (lockf fd semantics unresolved) or can't force
**repeated crash-during-recovery** (X4).

### Evidence required (rule 4)

Checked in beside this file: the fixture command/tests, sanitized per-injection
transcripts (the durable state observed after each kill, the registry contents,
the lock state), non-secret env metadata, result, decision. No secrets exist to
persist here.

### Time cap

**Two working days** — an explicit override of §7a rule 3's half-day default,
per the probe row. At the cap, classify `confirmed` / `falsified` /
`inconclusive`.

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
