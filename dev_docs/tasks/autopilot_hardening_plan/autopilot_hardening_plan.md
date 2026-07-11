---
type: epic
title: "Auto-pilot hardening — fixes from the first detached run"
status: active
owner: dp.egan
created: 2026-07-10
---

# Auto-pilot hardening

## Goal

Turn the 18 ranked findings from the first fully-detached auto-pilot run
([`dev_docs/autopilot-detached-run-1-findings.md`](../../autopilot-detached-run-1-findings.md))
into shipped fixes. The run **succeeded** (6/6 handed off in ~1h05m), but only
after hand-patching three fatal spawn bugs and working around a genuine conflict
between the sandbox and the project's own verify contract. This plan makes the
detached spawn work **out of the box** — no hand-patching — and closes the
launch-ergonomics, plan-adapter, and observability gaps.

## Scope / non-goals

- **In:** `scripts/spawn-orchestrator.sh` + its templates (the P0 spawn bugs),
  the sandbox↔verify conflict, a real end-to-end pre-flight helper, the
  reference/SKILL doc corrections, co-review follow-up routing, and observability.
- **Out (deliberately):**
  - The predictive/capability scout, required-vs-preferred coder routing, and
    the `--resume` reconciler — pre-existing planned follow-ups, not run-#1 findings.
  - A dollar/token `--budget` cap (run-budget.md v1 is rate-window only, unchanged).
  - Per-worker credential subtraction as a full implementation — task 3 documents
    the v1 posture and does the file-RO/state-RW split; full subtraction is noted
    as a later follow-up.
  - Any change to the task loop / `/deliver-task` per-task lifecycle beyond the
    verify-step and follow-up-routing seams named below.

## Approach

The findings cluster cleanly, so the plan mirrors them: the **P0 spawn bugs**
(tasks 1–2) are small, independent generator fixes that unblock everything and are
the highest priority. The **sandbox↔verify conflict** (task 4) is the one real
design decision — resolved to **run the verify command outside the jail** (rather
than widening the jail to exec every repo script), so the two containment walls
that held (write + network) stay tight. The rest — pre-flight helper (5), doc
corrections (6), follow-up routing (7), observability (8) — are independent and can
land in any order. Task 9 graduates the durable design and deletes the scaffolding.

Several tasks edit `scripts/spawn-orchestrator.sh` but in **different functions**
(`write_launch` vs `render_profile` vs a new `status` subcommand), so they merge
cleanly; each task names the function it owns and the rebase-order note.

**Design decisions (settled after a Fable design review — see task bodies):**

- **Task 4 verify = out-of-jail *broker*, not a naive subprocess.** A jailed
  process's children **inherit** the seatbelt profile, so the orchestrator cannot
  spawn an unjailed child. Verify runs via the **launchd supervisor** (already
  present): the orchestrator drops a pinned verify-request sentinel, the unjailed
  supervisor runs the fixed `verify_command` and writes back a result. This makes
  task 4 `is_blocked_by` task 1 (the supervisor/plist). Codex's own sub-sandbox is
  **disabled inside the jail** (the outer jail is strictly stronger) — folded here.
- **Task 2 exec = subpath over per-run-resolved bin dirs**, drift-proof; a literal
  list re-breaks on every tool update (the `versions/2.1.207` trap). With reads and
  exec both broad, **confidentiality rests entirely on the egress allowlist** —
  which includes `github.com`, an unbounded exfil channel; named explicitly in
  `launch-runtime.md`.
- **Task 7 follow-up = `/add-task` to the tracker AND a REPORT.md index**, deduped
  + capped + `auto-pilot`-tagged, gated by `/promote-tasks`' confidence check. The
  add-task destination host must be added to the egress allowlist at pre-flight or
  a plan-source run's own settings deny it.

**Named risks the tasks now carry:** task 3's RW state-dir flip must emit an
explicit `(deny file-write* <cred-file>)` after the subpath allow (else it
un-protects a co-located credential); tasks 1–4 need a **confinement regression
smoke** (exec of `sed`/`git`/`env bash` succeeds; `$HOME` write + off-list egress
still denied) since the existing harnesses can't run in-jail; task 8's done-sentinel
must be the **same** file as the launchd relaunch sentinel.

## Tasks

1. [[autopilot_hardening_task_1]] — P0: `write-launch` emits `--verbose` + injects `PATH`; smoke-test uses the real flag set; the atomic `launch` works end-to-end.
2. [[autopilot_hardening_task_2]] — P0: `render-profile` toolchain-exec mode (subpath bin dirs; symlink/version-drift-proof) so the real verify/coder toolchain can exec.
3. [[autopilot_hardening_task_3]] — P1: profile write-scopes — split credential *files* (RO) from tool *state dirs* (RW) so a long run's tools can write their own state.
4. [[autopilot_hardening_task_4]] — P1: resolve the sandbox↔verify conflict — run the verify command outside the jail (recommended) so `check.sh` reaches "done".
5. [[autopilot_hardening_task_5]] — P2: `scripts/preflight.sh` — one end-to-end read-only pre-flight (probes + base-freshness) emitting go/no-go + the resolved paths the generators consume.
6. [[autopilot_hardening_task_6]] — P2/P3: reference + SKILL corrections (pending phase, helper-script paths, reservation-PR note, scaffolding-cleanup teardown, merge-bottom-up guidance).
7. [[autopilot_hardening_task_7]] — P4: route a deferred **cross-cutting** co-review finding to a tracked follow-up task, not just a `QUESTIONS.md` entry.
8. [[autopilot_hardening_task_8]] — P5: observability — a `status --label` subcommand + a done-sentinel, and neutralize jail-incompatible `Stop` hooks in the detached run.
9. [[autopilot_hardening_task_9]] — Cleanup: graduate the durable design to `dev_docs/auto-pilot-hardening.md` and delete this plan scaffolding.

### Added from detached run #2 (findings 19–23)

Run #2 — the run that executed tasks 1–9 of *this* plan — surfaced five new findings
by living through them. Four become tasks (the fifth, #21, folded into task 8):

10. [[autopilot_hardening_task_10]] — **P0**: supervisor classifies a **401/auth** exit as **fatal, non-retryable** — halt + alarm, plus a no-progress guard. (#22)
11. [[autopilot_hardening_task_11]] — P2: supervisor gates the relaunch on `paused_until` **in shell** — a pause must cost **zero** model calls. (#19)
12. [[autopilot_hardening_task_12]] — **P1**: jail permits the harness's own `$TMPDIR` mux socket + cwd files — **stops every Bash exit code being poisoned to 1**. (#20)
13. [[autopilot_hardening_task_13]] — P2: run loop must never check out a task branch in the run worktree; `--resume` reads run state from the **branch**, not the working tree. (#23)

### The class, not the instances — "failure modes look like success"

Tasks 10–13 fix the four specific bugs run #2 hit. Tasks 14–16 fix the **property**
that let all of them run unchecked. Both production failures exited `0`,
`is_error: false`, `terminal_reason: completed` — and neither tripped a single
existing safeguard, because the circuit breaker only counts *delivery* failures and
neither failure was one. A run can be thoroughly broken while every signal the system
checks reads green, and the only thing that actually caught both was a human asking.

14. [[autopilot_hardening_task_14]] — **P1**: a **run doctor** — assert the run's own invariants every iteration (HEAD, run state readable from the branch, phases match git/PR reality, no orphan worktrees, forward progress); repair or halt, never drift. Generalizes #22/#23.
15. [[autopilot_hardening_task_15]] — P2: an **exit contract** — `continuing` / `paused` / `done` / `systemic` / `deadline`, plus a **heartbeat**, so "finished" and "wedged mid-task" stop being the same observable event (`exit 0`). Makes task 8's done-sentinel load-bearing.
16. [[autopilot_hardening_task_16]] — **P1**: an **alarm channel** — a halted *or merely stalled* run must actively notify a human, from the **supervisor in shell** (a dead agent can't alert anyone), saying what to *do*. #22 cost 4h14m of pure silence.

## What run #2 taught us (findings 19–23)

Tasks 10–13 come from a different source than tasks 1–9: not a review of run #1's
log, but **run #2 breaking in new ways while executing this very plan**. Two are
serious enough to restate:

- **#22 (task 10) is the most valuable thing either run produced.** An expired OAuth
  credential killed run #2 mid-flight; the supervisor then relaunched into the same
  non-retryable `401` **~52 times over 4.3 hours**, doing nothing, raising nothing.
  No launch-time probe could have caught it (auth was live and smoke-tested at
  launch — it expired *hours later*). The circuit breaker counts *delivery*
  failures, so a process dying *before* it dispatches never trips it; and the
  rate-limit backstop's premise — "back off, the window resets" — is simply **false**
  for an expired credential. The gap is structural: the supervisor can classify
  "retry later" but has no bucket for **"stop, a human must act."**
- **#20 (task 12) silently corrupts verification.** The jail denies the harness's own
  cwd-tracking write, which makes **every Bash call return exit 1 regardless of what
  actually happened**. An agent verifying by exit code is reading noise. Note this is
  *not* fixed by task 4 (which addresses `execve` of repo scripts) — both must land
  before in-jail verify can be trusted, and the confinement smoke needs an assertion
  that the jail can even *report an exit code correctly*.

The through-line for #19/#22 (tasks 10, 11, and the supervisor half of 15/16): **the
supervisor should decide in shell what it can decide in shell.** A timestamp
comparison, an exit-code classification, and firing an alarm need no model — and an
agent that is rate-limited or auth-dead cannot run its own bookkeeping anyway, so it
must not be the thing deciding whether to stop, or the thing responsible for saying
so.

**The deeper lesson (tasks 14–16): auto-pilot's failure modes look like success.**
Every production failure so far — the 401 relaunch loop, the vanished run state, an
orchestrator exiting mid-task — presented as `exit 0`, `is_error: false`,
`terminal_reason: completed`. None tripped a safeguard, because the circuit breaker
counts *delivery* failures and none of these was one. Two consequences the plan now
carries:

- **Green is not evidence of health** (task 14). The run must continuously assert its
  own invariants, because "nothing reported an error" demonstrably does not mean
  nothing is wrong.
- **An unattended run that fails silently has no failure mode at all** (task 16). The
  breaker, the fatal-auth halt, and the doctor are each worthless if their output is
  a file on a branch nobody reads at 3am. #23 was caught within 7 minutes only
  because a human had just set up a heartbeat; #22 went unnoticed for 4h14m because
  no one had.

## Open questions

Q1 (verify posture), Q2 (exec breadth), Q3 (follow-up sink) are **settled** — see
**Design decisions** above. Remaining:

- **Task 3 — per-worker credential subtraction depth.** v1 does the file-RO /
  state-RW split (with the explicit cred-file deny); full §4 per-worker subtraction
  (a codex worker never sees `gh`/`op`/`~/.claude`) stays a **tracked follow-up**.
  Confirm that's the right cut, or pull subtraction into scope now.
- **Task 4 — thin verify-jail now or later?** v1 ships the *unjailed* broker; the
  tighter "verify in its own fresh single-layer profile" is a follow-up (the two
  `test-*.sh` harnesses that call `sandbox-exec` won't nest regardless). OK to defer?
- **Sequencing.** Recommend landing **task 1 → task 2** first (they unblock a
  hand-patch-free relaunch and are prerequisites for 3/4), then the rest in parallel.
