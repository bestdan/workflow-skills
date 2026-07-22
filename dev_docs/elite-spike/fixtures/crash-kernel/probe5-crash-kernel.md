# Probe 5 — Baseline crash-transaction kernel

**Result: PENDING.** Draft state machine + kill sheet written; **no fixture run
yet**. Fixture is built only after this sheet is approved (§7a rule 1). Probe 5
"runs only after the draft exists" — the draft is
[`draft-state-machine.md`](./draft-state-machine.md), a consolidation of §4.1–4.2
into an explicit, fault-injectable `prepared → active → terminal` + generation +
registry-append spec.

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

Falsified if any injection in the draft's matrix (A–J) yields a **torn read**, a
**double-release**, a **silently-accepted duplicate/gap `seq`**, a **superseded
generation acted on**, a **crash-persistent lock needing manual removal**, or an
**ambiguous outcome that requires a human mid-run** — and it cannot be fixed by
tightening the flat-file protocol (i.e. it needs the SQLite redirect).

### Method

In a disposable directory, implement **only** the kernel: `prepared → active →
terminal`, generation replacement/takeover, and registry append — using
`lockf -t 0` OS locks and the atomic-publication primitive (temp → fsync →
rename → dir-fsync). Then **kill the writer at every durable boundary** (A–F) and
**run concurrent contenders** (G–J) per the draft's fault-injection matrix, and
check every invariant (draft §Invariants 1–7) after each.

### Pass threshold

Across the **entire** matrix A–J: all seven invariants hold, and **every**
crash/contention outcome is a **safe terminal or a safe hold that needs no
mid-run human action**. Specifically: no torn read; fail-closed contention (one
writer proceeds, the rest abort); release only after durable `observed_terminal`
+ all incarnations dead, and a claim never releases; generation monotonic and
superseded records inert; `seq` global-monotonic with duplicates/gaps **alerted,
not repaired**; and OS-owned locks auto-release on death (no stale-mutex manual
cleanup).

### Inconclusive condition (rule 3)

Classify **inconclusive**, not pass, if: a boundary crash can't be injected
**deterministically** at the intended point (so the outcome can't be attributed
to the protocol vs timing luck); or `lockf`/`fsync`/`rename` durability can't be
exercised faithfully in the fixture (so "atomic publication" is unproven); or a
contention race can't be forced (so serialization is untested).

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
