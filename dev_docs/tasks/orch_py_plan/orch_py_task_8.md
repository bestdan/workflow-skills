---
title: Port doctor — the 661-line run repair tool (Tier B — unblocked by task 2)
priority: medium
size: 5
status: needs_refinement
human_approval_requested: true
# promoter: scope exceeds size 5 (661-line function) — split before starting. Tier B (jail-invoked every loop); UNBLOCKED by task 2 (2026-07-13): pinned stdlib Python >=3.11 reaches the jail.
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_7
related_files:
  - scripts/spawn-orchestrator.sh:282 # doctor docs
  - scripts/spawn-orchestrator.sh:711 # the Seatbelt process-exec allowlist
  - skills/auto-pilot/SKILL.md:338 # "Every iteration opens with the run doctor"
tags: [orchestrator, python, port, seatbelt, blocked]
---

← [[orch_py_plan]]

## Context

> **Re-scoped 2026-07-13 after co-review of PR #205.** This card originally sat in Tier A and
> offered "just delete it" as an option. **Both premises were wrong**, and the card is now
> blocked on task 2 (the runtime decision) rather than on the audit.

`doctor` is **661 lines** — the single largest function in the file. It is **not** a read-only
diagnostic (an earlier draft of this card said so): it parks tasks via `_set_task_phase` (writing
`RUN.md`), removes orphaned worktrees (`:6066`), and halts the supervisor (`_doctor_halt`,
`:5744`). It is the run's self-repair, and any port or deletion has to answer to that stateful
contract — a stdout/rc golden corpus would capture none of it.
Two things changed:

**1. It is Tier B, not Tier A.** The jailed run-phase agent invokes it from *inside*
`sandbox-exec`: *"Every iteration opens with the run doctor"* (`skills/auto-pilot/SKILL.md:338`;
HALT on `status: systemic`, exit 30 — the loop must not dispatch). Porting it to Python
therefore requires the interpreter to be **on the Seatbelt `process-exec` allowlist**
(`spawn-orchestrator.sh:711-724`) — which is exactly the decision task 2 makes. It cannot be
ported before then.

**2. The "just delete it" option is dead.** It rested on `doctor` possibly being unused. It is
not: it is called on **every single loop iteration** of every auto-pilot run, and it can halt
the run. Deleting it would also have contradicted the plan's own contracts — an unchanged
29-subcommand CLI, and "the harness passes unedited" (`orch_py_task_3.md`). A deletion is a
behavior change and would need to be its own scoped task; it is not an option here.

## Task

**Task 2 decided (2026-07-13): stdlib-only Python on a pinned, pre-flight-resolved absolute
interpreter (≥3.11).** This card is **unblocked** — `doctor` ports. It is *not* frozen in bash.
Port it under the three requirements task 2 imposes
([`decisions/script_language.md`](../../decisions/script_language.md) → "The constrained tier's
runtime"):

- **Pre-flight resolve + assert ≥3.11, fail-closed at launch.** A `doctor` that cannot exec
  inside the jail halts every run on iteration one. This catches an absent/too-old interpreter
  *at launch*; it **cannot** see drift afterwards.
- **Bake the _stable_ symlink (`~/.local/bin/python3.11`) into the launch script — never the
  version-stamped target.** The script is written once (`:1471`) and re-run by launchd unchanged
  on every wake; a baked `…/cpython-3.11.14-…` dies silently on the next `uv` upgrade.
- **Grant the symlink's _resolve target_ dir as a subpath** (`~/.local/share/uv/python`), and the
  **narrowest** one — `render-profile` must emit it. Seatbelt checks the **resolved** path, so
  granting `~/.local/bin` alone fails `Operation not permitted` (verified). **Same defect class as
  the `git` CLT-shim bug** ([[orch_py_task_10]], fixed in **PR #208**) — which grants `<dev>/usr`,
  not the whole CLT tree, because the broad grant would have silently made a second interpreter
  executable in the jail. **Do not widen to `~/.local/share`.** Follow #208's smoke pattern: assert
  the interpreter *runs* under the rendered profile, not merely that it is granted.
- **Handle the residual: the wake script must test `[ -x "$interpreter" ]`** and route a missing
  interpreter through the supervisor halt path — a classified halt + alarm, never an unclassified
  exec failure that relaunches forever (finding #22's class). A `uv python uninstall` mid-run
  dangles the symlink, and the launch-time assert cannot see it.

Behavioural contract to preserve:

- Reproduce output byte-for-byte. Preserve every check, its ordering, its verdict text, and the
  **exit-30 HALT contract** the run loop depends on.
- **`doctor` is NOT read-only — do not assert that it is.** (An earlier draft of this card
  demanded a "read-only guarantee … assert it with a before/after snapshot of the run dir." That
  is false and would have failed the port outright.) It is the run's **self-repair**: it parks
  tasks via `_set_task_phase` (writing `RUN.md`), removes orphaned worktrees (`:6066`), files an
  `alarm-request` via `_doctor_halt` (`:5504`), and halts the supervisor. **Every mutation is part
  of the contract** and needs its own acceptance criterion — a stdout/rc golden corpus captures
  none of it.
  - Given 661 lines, **split this card** before starting (it exceeds size 5 on its own).

## Acceptance Criteria

**Code-enforced:**

- Golden corpus reproduces `doctor`'s stdout/rc byte-for-byte across its branches, and the
  **exit-30 HALT path is explicitly tested**.
- **Each mutation has its own criterion — `doctor` is NOT read-only** (an earlier draft of this
  card demanded "a test asserts the run directory is unchanged after a `doctor` run", which
  contradicts the contract above and would have failed every valid repair): task-parking writes
  `RUN.md` correctly, orphaned worktrees are removed, `alarm-request` is filed via `_doctor_halt`,
  and the supervisor halts. A stdout/rc corpus captures none of this.
- `render-profile` emits the interpreter's **resolve-target** dir on the exec allowlist, and
  `sandbox-exec` can actually run the interpreter under the rendered profile (assert the *run*,
  not just the grant — that is the `git` bug's lesson).
- The wake script fails **loud** on a missing interpreter (classified halt + alarm), not silent.
- Harness passes unchanged; `just check` green.

**User-run (mandatory if ported):**

- Run a **real auto-pilot loop** and confirm `doctor` executes inside the jail on every
  iteration and can still HALT the run. A green unit suite proves nothing here — the failure
  mode is "the jailed agent cannot exec the interpreter", which only appears in a real run.
