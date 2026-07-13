# Porting `spawn-orchestrator.sh` to Python

**Status:** proposed
**Created:** 2026-07-13
**Task scaffolding:** `dev_docs/tasks/orch_py_plan/` (local-only — `dev_docs/tasks/*` is
git-ignored; run `/promote-tasks` to score the nine tasks)

## The problem

`scripts/spawn-orchestrator.sh` is **6,284 lines of Bash 3.2**, with a **5,292-line** test
harness. Together that is ~82% of all shell in the repo. Every _other_ script here is
137–312 lines and is perfectly good shell — `claude-usage`, `await-pr-review`,
`preflight-freshness` shell out to `git`/`gh`/`jq`, gate on an exit code, print a line. That
is bash at its best, and this plan does not touch them.

The orchestrator is a different animal, and the evidence is in the codebase rather than in
taste:

- When ShellCheck was first run across the repo (PR #202), the **only** real defects it found
  anywhere were in this file: two `SC2155` bugs (`local x="$(cmd)"` silently discards the
  command's exit status — a genuine silent-failure class) and a **file-level `SC2034`
  disable**, whose stated justification is that _"state arrays are consumed indirectly by
  generated scripts."_ A program with keyed state, indirection, and code generation has
  outgrown the language.
- It holds a deliberate **Bash 3.2 floor** (macOS system bash), so it has **no associative
  arrays**, no namerefs, no `mapfile`. The source carries the scar tissue
  (`# empty-safe under bash 3.2 set -u`). It manages run state and renders three file formats
  without a hash map.
- It shells out to `jq` 16 times to do JSON work that is one stdlib import in Python.

## Approach: strangler fig, not a rewrite

`spawn-orchestrator.sh` **keeps its 29-subcommand CLI and stays the entrypoint**. It becomes
a thin dispatcher: subcommands on a `PORTED` list route to Python, the rest fall through to
the existing bash function. Each task ports one group, deletes the bash it replaces, and
leaves the suite green. The file shrinks monotonically; it is never in a broken intermediate
state.

Two existing properties make this unusually safe, and the plan leans on both:

1. **The CLI is already the seam.** `scripts/test-spawn-orchestrator.sh` and
   `scripts/smoke-confinement.sh` drive the orchestrator purely through its subcommands. They
   are a characterization suite pinned to a _contract_, not to an implementation — so they
   keep working, unedited, across the entire port. (If a test needs editing, the seam is
   wrong. That is the signal.)
2. **Most of the mass is pure.** The renderers are flags-in / file-out. Task 1 captures golden
   outputs from today's bash **before** anything changes, and every later task must reproduce
   them **byte-for-byte**.

## The constraint that shapes the plan: TWO constrained contexts, not one

> **Corrected 2026-07-13 after co-review (PR #205).** An earlier draft of this plan claimed the
> only constraint was launchd's PATH, and put `status`, `classify-exit`, `exit-reason`, and
> `doctor` in the freely-portable tier. **That was wrong**, and it would have broken the wake
> loop. The reachability analysis below is the corrected one. It is recorded rather than
> quietly edited because the mistake is instructive: the CLI is _not_ the only seam — bash
> functions call each other in-process, and the jailed agent calls back in.

A subcommand is **constrained** if it is reachable from a context that cannot freely resolve a
Python interpreter. There are **three** such paths, and the original plan modelled only the
first.

**1. The generated launch script → launchd's pinned minimal PATH.** `write-launch` generates a
script embedding `$self` (the absolute path to `spawn-orchestrator.sh`) and calls back into it
on every wake: `supervisor-scan → heartbeat → supervisor-gate → sandbox-exec … claude →
supervisor-check`. It runs under **launchd with a pinned, fingerprint-resolved minimal PATH**
(`write-launch` fail-closes without `--path` for exactly this reason).

**2. In-process bash function calls.** Constrained bash functions call other subcommands' bash
functions _directly, in-process_ — not through the CLI. Verified:

- `status_report` → `status` (`spawn-orchestrator.sh:4233`)
- `supervisor_check` → `classify_exit` (`:3195`)

Porting `status` or `classify-exit` and **deleting their bash bodies breaks these callers
outright.** The CLI seam does not protect you here, because these calls never go through it.

**3. The jailed agent → the Seatbelt exec allowlist.** The run-phase agent runs _inside_
`sandbox-exec` and invokes the orchestrator back out (`skills/auto-pilot/SKILL.md`):

- `doctor` — **every loop iteration** ("Every iteration opens with the run doctor"; HALT on exit 30)
- `exit-reason` — on every termination
- `heartbeat`, `alarm-clear`

The rendered profile carries a **`(allow process-exec` allowlist** (`:711-724`, fed by
`--exec`/`--exec-dir`/`--toolchain`). Any subcommand invoked from inside the jail needs its
interpreter **on that allowlist** — a constraint entirely separate from PATH resolvability.

### The corrected tiers

**Tier A — genuinely unconstrained.** Reached only by humans, skills, and launch-time code:

| Subcommand                             | Lines    | Notes                   |
| -------------------------------------- | -------- | ----------------------- |
| `render-profile` (+ network allowlist) | 337      | the seatbelt renderer   |
| `write-launch`                         | 327      | the generator           |
| `restack`                              | 303 + 71 | post-merge, run outside |
| `write-verify-broker`                  | 126      |                         |
| `render-settings`                      | —        |                         |
| `check-profile`                        | ~20      |                         |

Roughly **1,200 lines** — the renderers and generators, which is still where the worst quoting
and list-building code lives. These port with genuinely zero runtime-surface risk. **Tasks 1–4, 7.**

**Tier B — constrained.** Every one of these is reachable from launchd's PATH, the Seatbelt
exec allowlist, or an in-process call from something that is:

| Subcommand                              | Lines | Constrained by                           |
| --------------------------------------- | ----- | ---------------------------------------- |
| `doctor`                                | 659   | **jail** (every loop iteration)          |
| `status_report`                         | 443   | launchd (via `supervisor-scan`)          |
| `supervisor_check` / `_supervisor_halt` | 414   | launchd                                  |
| `status`                                | 180   | **in-process** (from `status_report`)    |
| `supervisor_scan` / `supervisor_gate`   | 174   | launchd                                  |
| `classify_exit`                         | 73    | **in-process** (from `supervisor_check`) |
| `exit_reason`                           | 69    | **jail** (on termination)                |
| `heartbeat`, `alarm-clear`              | —     | launchd + jail                           |

Roughly **2,000+ lines**, all gated on the interpreter decision. **This is a materially bigger
share than the original plan assumed** — the honest value of the unconstrained port is now
"the renderers and generators," not "80% of the file."

**Consequence for sequencing:** the interpreter decision (task 8) now gates _more_ of the work
than it used to. See **Open questions**.

## Interpreter choice

**Why Python at all** — and why not TypeScript or Go — is settled separately in
[`dev_docs/decisions/script_language.md`](decisions/script_language.md). Short version: TS 7's
10x is type-checking speed, not runtime, and Node is now the _least_ safe assumption available
(Claude Code ships as a native binary and no longer guarantees a Node runtime). Go was the real
contender and is rejected on distribution cost, with the condition for revisiting it recorded
there. Read that before relitigating this.

`scripts/validate.py` already sets the repo's convention: PEP 723 inline metadata, `uv run`,
hash-locked (`validate.py.lock`), `requires-python >=3.11`. That is the default for Tier A.
It is a poor fit for Tier B, where `uv` typically lives in `~/.local/bin` and would have to be
added to the pinned launchd PATH. Three options, decided at task 8:

| Option                                                               | Result                                      | Cost                                                                                                                                                 |
| -------------------------------------------------------------------- | ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **(a) `uv` for Tier A, bash stays for Tier B** _(recommended)_       | the renderers and generators (~1,200 lines) | ~2,000+ lines of constrained bash survive — more than first assumed                                                                                  |
| **(b) stdlib-only on absolute `/usr/bin/python3`**                   | kills the bash entirely                     | `/usr/bin/python3` is a Command Line Tools shim (and is 3.9 on this machine, vs the repo's ≥3.11); still needs adding to the Seatbelt exec allowlist |
| **(c) `uv` everywhere, on the pinned PATH _and_ the exec allowlist** | cleanest code                               | largest new runtime dependency on the security-critical unattended path — not recommended                                                            |

Note both (b) and (c) must satisfy **two** constraints, not one: resolvable on launchd's pinned
PATH **and** permitted by the Seatbelt `process-exec` allowlist.

## Tasks

Nine, each one PR. Full cards in `dev_docs/tasks/orch_py_plan/` (local).

| # | Task                                                                            | Tier | Size |
| - | ------------------------------------------------------------------------------- | ---- | ---- |
| 1 | Golden-output corpus + the bash→Python dispatch seam, proved on `check-profile` | A    | 5    |
| 2 | Port `render-profile` (+ network allowlist) — the seatbelt renderer             | A    | 5    |
| 3 | Port `render-settings` — the layer-2 egress allowlist                           | A    | 3    |
| 4 | Port `write-launch` + `write-verify-broker` — the generators                    | A    | 5    |
| 5 | ~~Port status / classify-exit / exit-reason~~ → **audit the reachability set**  | B    | 5    |
| 6 | ~~Port (or delete) `doctor`~~ → **blocked: jail-invoked every loop**            | B    | 5    |
| 7 | Port `restack`                                                                  | A    | 5    |
| 8 | **Decide the interpreter question** — now gates tasks 5, 6, and the wake loop   | B    | 5    |
| 9 | Graduate to `dev_docs/orchestrator.md`; delete the plan folder                  | —    | 2    |

**Tasks 5 and 6 are no longer straight ports.** Both target constrained subcommands (`status`
and `classify-exit` are called in-process from Tier B; `exit-reason` and `doctor` are invoked
from inside the jail), so neither can proceed until task 8 decides the interpreter question.
Task 5 is rewritten as a **reachability audit** — the analysis that should have preceded this
plan — and task 6 is blocked on it.

## Non-goals

- **Not** a rewrite. No behavior changes, no refactors-while-porting, no new features. A bug
  found mid-port is written down, not fixed in the same PR — a behavior change is
  indistinguishable from a port defect.
- **Not** porting the other shell scripts. They are short, correct, idiomatic shell.
- **Not** porting `scripts/test-spawn-orchestrator.sh`. It is the safety net; rewriting the net
  while moving the trapeze defeats the point.
- **Not** adding a package manager, venv, or build step for plugin consumers.

## Open questions

1. **Which interpreter — and should that decision move to the FRONT of the plan?** See the table
   above. The corrected reachability analysis means this decision now gates **tasks 5, 6, and 8**
   (~2,000 lines), not just task 8. The original sequencing put it last on the theory that only
   the wake loop depended on it; that theory was wrong. Two options:
   - **Keep it at task 8.** Tasks 1–4 and 7 (the renderers and generators, ~1,200 lines) are
     genuinely unconstrained and can land first, which is real value delivered before any hard
     call is made. Tasks 5–6 then wait.
   - **Move it to task 1.5, before any porting.** It determines how much of the file can _ever_
     move, so deciding it early stops the plan from optimistically porting toward a boundary
     that may not hold. It would also let a Go-shaped answer (a compiled binary satisfies both
     the PATH and exec-allowlist constraints at once — see the decision doc) be considered
     before 1,200 lines of Python exist.

   **Recommendation: keep it at task 8**, because tasks 1–4 are unconditionally safe and worth
   banking regardless of the answer — but the decision is now materially more consequential than
   this plan first claimed, and it deserves a deliberate call rather than a default.
2. **Sequencing against PR #202.** That PR `shfmt`-reformats every line of
   `spawn-orchestrator.sh`. Any port work started before it lands will conflict
   catastrophically. **#202 must merge first** — it is a hard prerequisite on task 1. (It is
   also what makes this plan tractable at all: it is the PR that added the lint and Bats
   infrastructure this port leans on.)
3. **Is `doctor` (659 lines) worth porting, or worth deleting?** It is a read-only diagnostic,
   and diagnostics accrete. Porting is ~a day. Deleting is ten minutes. Task 6 requires
   answering this before writing any code.
