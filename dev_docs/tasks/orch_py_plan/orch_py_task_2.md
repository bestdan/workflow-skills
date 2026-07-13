---
title: Decide the runtime for the constrained tier — before any code is ported
priority: high
size: 3
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_1
related_files:
  - dev_docs/decisions/script_language.md # the language ADR this amends
  - scripts/spawn-orchestrator.sh:711 # the Seatbelt process-exec allowlist
  - scripts/spawn-orchestrator.sh:163 # write_launch's --path fail-closed guard
  - skills/auto-pilot/SKILL.md:338 # the jailed agent's per-iteration doctor call
tags: [orchestrator, decision, launchd, seatbelt]
---

← [[orch_py_plan]]

## Context

> **Moved to the front of the plan on 2026-07-13, and split from its implementation.** This was
> originally task 8, on the theory that only the supervisor wake loop depended on it. The PR
> #205 co-review proved that theory wrong, and the task 1 audit then measured it: the
> constrained tier is **2,618 lines across 17 subcommands** — `doctor`, `status`,
> `classify-exit`, `exit-reason`, the whole supervisor and verify-broker path, and `teardown`.
> This decision now determines **how much of the file can ever move** — and whether Python is
> even the right target. Deciding it *after* building 1,458 lines of Python would be deciding it
> too late to act on. Splitting the decision from the port also resolves the promoter's "unbounded scope"
> flag: this card is now decision-only and bounded.

Task 1's audit has just produced the definitive reachability map. This task turns it into a
runtime decision. **No code is written here.** The output is a decision, recorded with its
rationale.

Task 1's table (`dev_docs/orchestrator-python-port.md`, regenerable via
`scripts/audit-orchestrator-reachability.py`) is the input. The constrained tier is reachable
from **three** contexts — **four** entry points — and any answer must satisfy all of them:

- **launchd's pinned minimal PATH** — the generated launch script's wake loop (`write-launch`
  fail-closes without `--path` precisely because a launchd job has a minimal environment).
- **The verify broker's launchd job** (`:5062`) — a *second* generated job under the same
  pinned PATH, which the audit found and both earlier hand-drawn boundaries missed.
- **The Seatbelt `process-exec` allowlist** (`spawn-orchestrator.sh:711-724`) — the jailed
  run-phase agent invokes `doctor` and `assert-run-head` every loop iteration, `exit-reason` on
  every termination, and the `verify-request`/`verify-await` handshake on every verify, from
  *inside* `sandbox-exec`. An interpreter on the PATH but **not** on the exec allowlist cannot
  run there at all.

Plus the in-process trap: `status_report` calls `status`, `supervisor_check` calls
`classify_exit`, and the supervisor halt path calls `teardown`, as **bash functions** — never
through the CLI. None can be ported and its bash deleted while its callers remain bash, under
*any* interpreter.

## Task

Present the options with the audit's real numbers and **get an explicit decision from the
user**. Do not default silently — this is the plan's load-bearing call.

- **(a) Freeze the constrained tier in bash.** Port only the renderers and generators
  (1,458 handler lines, tasks 3–7). `spawn-orchestrator.sh` ends at ~2,618+ lines of supervisor,
  reporting, and diagnostic bash — **the entire unattended runtime stays shell.** Zero new
  runtime dependency on the unattended path. **The safe default**, and note the audit made it a
  worse deal than it looked: the port buys the renderers, not the program.
- **(b) Stdlib-only Python on an absolute interpreter path.** Survives the minimal PATH and can
  be added to the exec allowlist as a literal. **Verify before choosing:** `/usr/bin/python3` is
  a Command Line Tools shim and measured **3.9.6** on this machine, against the repo's ≥3.11
  convention. Confirm what is actually resolvable rather than assuming.
- **(c) `uv` everywhere** — on the pinned `--path` *and* the exec allowlist. Cleanest code,
  largest new runtime surface on the security-critical unattended path.
- **(d) Reconsider a compiled binary (Go).** *This option is why the task moved to the front.* A
  compiled binary **satisfies both constraints at once** — no interpreter to resolve on the
  PATH, and one literal to add to the exec allowlist. It is the only option that could retire
  the bash entirely without widening the runtime surface.
  `dev_docs/decisions/script_language.md` rejected Go on distribution cost (no plugin install
  hook; a binary release pipeline for a solo repo) — but it did so **while believing the
  constrained tier was ~1,000 lines and Python could take the rest.** The audit measured 2,618 —
  **2.6x the Tier A total** — so that premise is not merely false, it is inverted. The tradeoff
  deserves one honest re-examination before 1,458 lines of Python exist.

Record the outcome by **amending `dev_docs/decisions/script_language.md`** — do not start a
competing doc. If the answer is (d), this plan is superseded and must be rewritten before
task 3 begins.

## Acceptance Criteria

**Code-enforced:**

- `dev_docs/decisions/script_language.md` carries the decision, dated, with the audit's line
  counts and the two-constraint analysis. If Go is re-rejected, the doc states **why it survived
  re-examination under the corrected numbers** — not merely that it was rejected once before.
- `dev_docs/orchestrator-python-port.md` and every downstream card reflect the decision: under
  (a), task 8's `doctor` port closes as won't-do; under (b)/(c), task 8 inherits the PATH +
  exec-allowlist requirements; under (d), the plan is rewritten.
- **No code is written.** If the diff touches `scripts/`, this task has overrun its scope.

**User-run:**

- The decision is made **by the user, explicitly** — not inferred, not defaulted. Frame the
  tradeoff plainly: what breaks at 3am if the interpreter is missing, versus how many lines of
  bash stay forever.
