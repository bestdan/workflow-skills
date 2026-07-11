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
