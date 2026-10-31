# CAO usage-aware orchestration

How a Claude orchestrator advances a task list by dispatching each task to a
**CAO worker** (Codex / Antigravity, via `cao-run`) instead of coding itself —
and pauses before it hits Claude's rate window, resuming once the window resets.

The orchestrator is the only Claude-quota consumer in this setup, so the budget
only ever tracks Claude. Worker-side limits are deliberately untracked.

## What it is built from

Nothing about pause/resume was rebuilt. The design rides the existing auto-pilot
supervisor kernel (`scripts/spawn-orchestrator.sh`'s gate/relaunch/classify plus
`scripts/claude-usage.sh`), the run-state machine, and `/deliver-task` →
`/orchestrate-coders`. What was added is configuration and knobs, not a second
execution loop:

| Piece                          | Where it lives                                               |
| ------------------------------ | ------------------------------------------------------------ |
| CAO dispatch wrapper           | `scripts/cao-coder.sh`                                       |
| "less-Claude" run profile      | `--profile less-claude`, threaded launch → `RUN.md` → resume |
| Pre-invoke reserve gate        | `/deliver-task --run-state <RUN.md>`, `--reserve`            |
| Reset-epoch validation         | `scripts/claude-usage.sh --session-status`                   |
| CAO-restricted coder selection | `--cao-fleet` in `skills/select-coder/`                      |

The task source is a `plan-with-docs` directory routed through the existing plan
adapter — a plan dir with no `is_blocked_by` edges _is_ a flat list, so no new
adapter code was needed.

`cao-server` (the local CAO session daemon) is a **runtime prerequisite, not a
managed component**: checked at launch, re-verified on each resume via the
capability-join re-check, never auto-restarted. Same posture as the existing
auth and base-freshness prerequisites.

## Design decisions and why

**Depth 1 — CAO as an `orchestrate-coders` custom coder, `/deliver-task`
unchanged.** Codex and Fable independently picked this over a lean bespoke
`cao-run` loop. Per-task Claude spend is dominated by implementation (offloaded
to CAO either way) and co-review (a knob here); a bespoke loop would only shave
the small claim/PR/hand-off bookkeeping sliver, at the cost of a duplicate
delivery/recovery loop a solo maintainer must keep in sync. Revisit only on real
per-task cost data.

**The orchestrator still judges every diff**, on a cheaper tier. This is the one
delivery step kept on Claude: it is the only way to distinguish a real success
from a stalled or empty worker. Opus stays for park/escalate.

**A conservative fixed reserve first, measured reserve later.** The fixed
reserve plus `--reserve` shipped; per-task delta instrumentation tags samples by
raw window epoch so the threshold can graduate to a measured one.

**`--session-status` is a pure fail-closed reader.** This is the subtlest
contract in the subsystem and was settled by escalation. An earlier
implementation had it emit a grace-adjusted pause epoch — or a rolling
`now+3600` fail-safe — that was wire-indistinguishable from a real reset, which
would have corrupted window-identity tagging and double-buffered
`claude-auto-resume.sh`'s wait. The ruling:

- `--session-status` emits a **raw validated `reset_epoch`**, and exits nonzero
  with no stdout on an implausible or unpersistable reset.
- **Grace** (and the 1h fallback on failed reads) applies at the pause _writer_
  — the `/deliver-task` gate and the `run-budget.md` near-cap path — written
  atomically alongside `pause_observed_at` and `pause_source`.
- Only successful validated readings are samples; a nonzero read clears the
  baseline.
- `claude-auto-resume.sh` needs no change: raw epochs restore its single
  `CAR_BUFFER` semantics.

> **stderr must never be treated as a machine interface.**

All three consumers — the reserve gate, the `usage_deltas` instrumentation, and
`claude-auto-resume.sh` — were verified to read that contract consistently on an
assembled tree.

**Gate boundaries live in `/deliver-task`, not the outer loop.** The documented
claim/verify/co-review gates cannot be enforced from outside because
`/deliver-task` is opaque. Rather than document unenforced boundaries, the skill
gained an optional auto-pilot-only `--run-state <RUN.md>` argument: when
supplied, it reads the persisted reserve, caches one usage reading per cycle,
and consults it before claim, verify, co-review, and every Step-6
re-verify/re-review. Below reserve routes to the existing near-cap checkpoint
path, not a delivery failure. Standalone `/deliver-task` is unchanged.

Caveat still open: a single cached snapshot per cycle, so freshness within a
long cycle is not guaranteed.

## Operational caveats

**`cao-run`'s worker-completion detection is unreliable; the diff is ground
truth.** A live smoke test logged `Error: Timed out after 300s waiting for
terminal` while the task had in fact succeeded — file present, exit 0. Two
consequences for an unattended run:

- Per-task CAO timeouts must be sized **well above 5 minutes**, or every
  dispatch looks like a hang. A trivial task took ≥300s wall-clock.
- The orchestrator must **not** read `cao-run`'s "Timed out" stderr as failure.

`orchestrate-coders` already treats the harvested diff as ground truth, which is
why the smoke test passed despite the bogus timeout — it validated that design
principle rather than breaking it.

**`CAO_ENABLE_WORKING_DIRECTORY=true` is required on the daemon.** `cao-run`
only honours a caller-owned worktree when `cao-server` was started with this
exported. Without it the worker edits elsewhere and the wrapper harvests an
empty diff even on success. It is documented in `orchestrate-coders`'s
runtime-prerequisite paragraph.

**Coders never commit.** The wrapper harvests the diff; the orchestrator
commits. `scripts/cao-coder.sh` guards on `rev-parse --is-inside-work-tree`
printing `true` (not merely exiting 0), and the live smoke test confirmed it
creates no rogue `cao/` worktree — the never-add-a-worktree contract held under
a real dispatch.

**Coders run sandboxed without `dprint`**, so formatting is never normalised on
a worker branch. A `dprint check` failure can therefore surface only once
branches are assembled, not on any individual branch.

**`--cao-fleet` restricts selection to codex/agy before scoring**, and carries a
containment-gate exception so a CAO-dispatched agy (isolated in CAO's own
worktree) is not wrongly dropped — which would silently collapse the fleet to
codex-only.

## Related

- [`auto-pilot.md`](auto-pilot.md) — the run loop this extends
- [`auto-pilot-hardening.md`](auto-pilot-hardening.md) — the hardening pass that preceded it
- `skills/auto-pilot/references/run-budget.md` — the reserve and near-cap mechanics
- Open follow-ups: #703 (preflight omits CAO executables), #704 (readiness gate omits the daemon env var), #706 (jail exec-denies the project interpreter)
