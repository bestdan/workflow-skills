---
title: Decide the runtime for the constrained tier — before any code is ported
priority: high
size: 3
status: done
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
> even the right target. Deciding it *after* building 1,422 lines of Python would be deciding it
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
  (1,422 handler lines, tasks 3–7). `spawn-orchestrator.sh` ends at ~2,618+ lines of supervisor,
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
  **1.8x the Tier A total** — so that premise is not merely false, it is inverted. The tradeoff
  deserves one honest re-examination before 1,422 lines of Python exist.

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

## Outcome (2026-07-13)

**Decision: (b) — stdlib-only Python on a pinned, pre-flight-resolved absolute interpreter
(≥3.11). Go re-rejected. Nothing is frozen in bash; the whole file is portable.** Recorded by
amending `dev_docs/decisions/script_language.md` ("The constrained tier's runtime"), per this
card's instruction not to start a competing doc.

**The plan's load-bearing premise was false, and nobody had tested it.** Both this plan and the
language ADR assumed no interpreter could satisfy launchd's pinned PATH *and* the Seatbelt exec
allowlist at once — which is what made a compiled binary uniquely qualified, and what made
"freeze Tier B in bash" the expected answer. Exercised against a real rendered profile:

| Candidate | Runs in the jail? |
| --------- | ----------------- |
| `uv run` | **No** — needs `~/.cache/uv` writable: a write grant outside the worktree confinement. |
| `/usr/bin/python3` | **No** un-granted (CLT shim), and 3.9.6 vs the repo's ≥3.11. |
| **uv-managed CPython 3.11.14, absolute path** | **Yes** — one exec grant, no cache writes, no PATH lookup, full stdlib. |
| Go binary | **Yes** — but this was supposed to be the *only* thing that could. |

The distinction nobody had drawn: **`uv run` is not the interpreter.** `uv run` is a package
manager (cache + network); the CPython binary uv installs is just an interpreter, and by absolute
path it needs no PATH entry, no cache, and no writes.

**Why Go lost.** Not on distribution cost — on the collapse of its differentiator. Once an
interpreter satisfies both constraints, Go's remaining case is a release pipeline for a solo repo
with no plugin install hook, against a language that is worse at this program's actual workload
(S-expr/plist/shell templating, dynamically-shaped `gh` JSON). The ADR states this explicitly
rather than citing the original rejection, as this card required.

**What it bought.** The expected outcome was "the port buys the renderers, not the program"
(1,422 of 4,040 handler lines). The real outcome is that **all 2,618 constrained lines are
portable too** — the supervisor, `doctor`, `status`, the verify broker. Task 8 is unblocked
rather than closed as won't-do.

**Requirements passed downstream** (task 8 inherits; not new patterns — `--claude-bin` and the
fingerprint-resolved `--path` already work this way at `spawn-orchestrator.sh:1363`):

1. Pre-flight **resolve + assert ≥3.11, fail-closed at launch**, not at 3am. A version
   requirement cannot conjure an interpreter — it converts a silent break into a loud one.
2. **Bake the resolved absolute path** into the launch script and the exec grant. Never a PATH
   lookup.
3. Grant the interpreter **directory as a subpath**, not a version-stamped literal — a
   `cpython-3.11.14-…` literal re-creates detached-run finding #3's version-drift trap. Verified:
   one subpath grant covers 3.11.14 and 3.14.2.

**Accepted residual risk:** a soft dependency on a uv-managed interpreter existing on the host,
on the unattended path. Requirement 1 is the mitigation.

**Found while testing, filed separately — `git` cannot exec inside the jail.** `/usr/bin/git` is
a CLT shim that re-execs `/Library/Developer/CommandLineTools/usr/bin/git`, which no profile
grants; `smoke-confinement.sh` passes `--exec "$(command -v git)"` but never *runs* git jailed,
so it has never caught this. Independent of the runtime decision and **not fixed here** (this
card is decision-only): see `dev_docs/tasks/orch_py_plan/orch_py_task_10.md`.
