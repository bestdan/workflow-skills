---
type: handoff
title: Handoff — Probe 5b task 3 (legs 1 and 2)
status: active
owner: Daniel Egan
expires_with: probe5b_task_3
---

# Handoff — Probe 5b, task 3

**DELETE THIS FILE when task 3 is complete** (`git rm -f`, say so in the task-3
commit). It is scaffolding: it carries pointers and two host traps into a fresh
context, and goes stale the moment task 3 lands. Anything here still worth
keeping moves into the kill sheet or a task file first.

---

Continue the auto-pilot "E-lite" workstream, Probe 5b, task 3.

Repo:     github.com/bestdan/workflow-skills
Branch:   bestdan/autopilot-e-lite-design  (PR #243, draft)
Worktree: ~/src/worktrees/workflow-skills/e-lite
Design:   dev_docs/auto-pilot-e-lite-design-2026-07-21.md

STATE: Row 5b was FALSIFIED by inventory on 2026-07-28, before any fixture ran;
the design doc carries the verdict, the redirect and the consequences. **Do not
re-litigate it — no leg can change it.** The kill sheet is approved. Tasks 1 and
2 are `done`: the harness and its smoke leg are built, run, and committed
(`ca0d710`). The time cap is **one day**, extended once, and **no further
extension is authorized**.

READ FIRST (source of truth; this file deliberately does not restate them):
  dev_docs/elite-spike/fixtures/runaway/probe5b-runaway.md
      <- THE governing document. Every threshold, the verdict lattice, the
         evidence schema, the containment preconditions, and — under Results —
         the task 2 construction record, which is what task 2 measured so you
         do not have to re-measure it.
  dev_docs/tasks/probe5b_plan/probe5b_task_3.md   <- your task
  dev_docs/tasks/probe5b_plan/probe5b_plan.md     <- decisions (A)-(D)
  dev_docs/elite-spike/fixtures/runaway/          <- the harness you are extending
  dev_docs/tasks/probe5-incident-evidence/
      <- READ BEFORE WRITING ANYTHING THAT SPAWNS OR SIGNALS. Four days of
         reaped SSH logins. Do not modify; it outlives the spike.

YOUR TASK: task 3 only — legs 1 and 2. **Run leg 2 FIRST**, then leg 1; that is
the kill sheet's run order and it doubles as the degradation order. Legs 3 and 4
are tasks 4-5; do not build them. Stop at task 3's acceptance criteria.

INHERIT THESE — task 2 measured them; do not rediscover:
- The rendered profile's `supervisor-state` write-deny IS in force: **EPERM
  (errno 1)** from inside the jail, with the control write to the same directory
  succeeding. EACCES belongs to the rule-6 filesystem substitute, not here.
- `/usr/bin/python3` CANNOT exec under the profile; the pinned Homebrew build
  can. `driver.sh` smoke-tests this before every run.
- The jailed surrogate cannot write the checked-in fixture tree. Its evidence
  goes to `$RUNDIR/.auto-pilot/surrogate-evidence.jsonl` and the unjailed driver
  merges it, tagged `in_jail`.
- Containment is built and drilled: `common.py` owns every spawn and signal,
  re-validates `p_uniqueid` immediately before each one, and no pgid is ever
  formatted into a shell word. `./parent-death-drill.sh` asserts the deadline
  predicate — **run it before any leg**; it has caught a real defect once.
- `scenarios.py` is the leg registry. Arm a leg by flipping `armed` and adding
  its variants; `verify` then hard-errors if an armed variant produces no row.

WHAT TASK 3 MUST ADD:
- `runaway.py` behaviours for leg 1's five variants (`polite`, `far-future`,
  `deleted`, `garbage`, `done-forgery`) and leg 2's (`recent-past`,
  `far-future`, `LedgerWrite`). Today it only has the smoke behaviours.
- Leg 2's `recent-past` has a PINNED tuning constraint: keep
  `paused_until ∈ (now − 3600, now)`. Outside it the run is not exempt, the
  ledger clears instead of halting, and the resulting "no halt" row reads as a
  falsification when it is really mis-tuned apparatus — `error — fixture
  defect`, per the sheet's attribution table. Assert `exempt_since` was stamped
  before counting any wake toward the cap.
- Leg 1's four forgery variants are EXPECTED to falsify. **Do not soften them**
  — that falsification is what sizes the redirect: the ceiling must not read
  agent-written state. `done-forgery`'s prediction is specific (ledger clears,
  gate returns rc 20 having run teardown, surrogate still alive, zero subsequent
  wakes); anything vaguer is a fixture defect, not a soft pass.
- Record `family_verdict` and `measurement` as SEPARATE fields, never one
  collapsed outcome.

TWO HOST TRAPS (the reason this file exists):
- **Run `scripts/check.sh` UNSANDBOXED.** Sandboxed runs add ~6 spurious
  failures because `sandbox-exec` cannot nest. It cannot pass here regardless:
  the maintainer's ruling is **no NEW failures against the recorded baseline**,
  checked with `./check-baseline.sh --compare <transcript>` (compares failing
  test NAMES; the count drifts run to run). Baseline is 57.
- **`$TMPDIR` differs sandboxed vs unsandboxed.** A file written under one is
  invisible under the other, and it looks like the file vanished.

TWO THINGS TO EXPECT THAT THE SMOKE LEG NEVER HIT:
- Legs 1 and 2 are supposed to HALT, and every halt fires a real macOS desktop
  notification via `_alarm_notify`'s `osascript`. That is the real supervisor
  behaving correctly, not a fixture bug.
- A halt COMMITS the run-state change. `driver.sh` already makes its scratch
  repo hermetic against this host's global `core.hooksPath` — leave that alone;
  without it a host pre-commit hook would reject the commit and read as the
  supervisor failing to record its own halt.

CONSTRAINTS: spike code is disposable and never promoted by renaming (§0a rule
4); `dev_docs/elite-spike/fixtures/` is excluded from dprint and shfmt ON
PURPOSE (results.json pins sha256s) — do not "fix" that; `dev_docs/tasks/` needs
`git add -f`; never commit to main; no real `claude`, no network, no GitHub App,
no real remote or tracker.
