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

## The constraint that actually shapes the plan: launchd, not the sandbox

`write-launch` generates a launch script that embeds `$self` — the absolute path to
`spawn-orchestrator.sh` — and calls **back into it** on every supervisor wake:

```
supervisor-scan → heartbeat → supervisor-gate → sandbox-exec … claude → supervisor-check
```

That generated script runs under **launchd with a pinned, fingerprint-resolved minimal
`PATH`**. (`write-launch` fail-closes without `--path` for precisely this reason: "a launchd
job has a minimal PATH.") And `supervisor-scan` internally calls `status_report` — the
**second-largest function in the file, at 443 lines**.

So the boundary that matters is not the seatbelt jail; it is launchd's minimal environment.
Anything reachable from the generated script needs its interpreter resolvable there, on every
wake, forever — and if it isn't, the supervisor breaks **unattended, overnight**, which is the
exact scenario auto-pilot exists for. This splits the work:

**Tier A — outside the wake loop.** `render-profile` (249), `render-settings`, `check-profile`,
`write-launch` (327), `write-verify-broker` (126), `status` (180), `doctor` (659), `restack`
(303), `exit-reason`. This is where nearly all the pain lives, and it ports with **zero**
runtime-surface risk. Tasks 1–7.

**Tier B — inside the wake loop.** `status_report` (443), `supervisor_check` (264),
`_supervisor_halt` (150), `supervisor_scan` (114), `supervisor_gate` (60), `heartbeat` —
roughly 1,000+ lines. Deferred to a single decision task (8), where **leaving it in bash is
an explicit and recommended outcome**.

## Interpreter choice

`scripts/validate.py` already sets the repo's convention: PEP 723 inline metadata, `uv run`,
hash-locked (`validate.py.lock`), `requires-python >=3.11`. That is the default for Tier A.
It is a poor fit for Tier B, where `uv` typically lives in `~/.local/bin` and would have to be
added to the pinned launchd PATH. Three options, decided at task 8:

| Option                                                         | Result                                        | Cost                                                                                      |
| -------------------------------------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **(a) `uv` for Tier A, bash stays for Tier B** _(recommended)_ | ~80% of the value, zero risk to the wake loop | ~1,000–1,200 lines of supervisor bash survive                                             |
| **(b) stdlib-only on absolute `/usr/bin/python3`**             | kills the bash entirely                       | may be Python 3.9 on older macOS (repo's other Python needs ≥3.11); no third-party deps   |
| **(c) `uv` everywhere, added to the pinned PATH**              | cleanest code                                 | largest new runtime dependency on the security-critical unattended path — not recommended |

## Tasks

Nine, each one PR. Full cards in `dev_docs/tasks/orch_py_plan/` (local).

| # | Task                                                                            | Size |
| - | ------------------------------------------------------------------------------- | ---- |
| 1 | Golden-output corpus + the bash→Python dispatch seam, proved on `check-profile` | 5    |
| 2 | Port `render-profile` (+ network allowlist) — the seatbelt renderer             | 5    |
| 3 | Port `render-settings` — the layer-2 egress allowlist                           | 3    |
| 4 | Port `write-launch` + `write-verify-broker` — the generators                    | 5    |
| 5 | Port the read-only reporters: `status`, `classify-exit`, `exit-reason`          | 5    |
| 6 | Port (or **delete**) `doctor` — 659 lines, the worst single function            | 5    |
| 7 | Port (or defer) `restack`                                                       | 5    |
| 8 | **Decide the launchd boundary**: port Tier B, or freeze it in bash              | 5    |
| 9 | Graduate to `dev_docs/orchestrator.md`; delete the plan folder                  | 2    |

## Non-goals

- **Not** a rewrite. No behavior changes, no refactors-while-porting, no new features. A bug
  found mid-port is written down, not fixed in the same PR — a behavior change is
  indistinguishable from a port defect.
- **Not** porting the other shell scripts. They are short, correct, idiomatic shell.
- **Not** porting `scripts/test-spawn-orchestrator.sh`. It is the safety net; rewriting the net
  while moving the trapeze defeats the point.
- **Not** adding a package manager, venv, or build step for plugin consumers.

## Open questions

1. **Which interpreter, and does Tier B ever move?** See the table above. Binds at task 8;
   tasks 1–7 are unaffected. Listed first because it changes what "done" means.
2. **Sequencing against PR #202.** That PR `shfmt`-reformats every line of
   `spawn-orchestrator.sh`. Any port work started before it lands will conflict
   catastrophically. **#202 must merge first** — it is a hard prerequisite on task 1. (It is
   also what makes this plan tractable at all: it is the PR that added the lint and Bats
   infrastructure this port leans on.)
3. **Is `doctor` (659 lines) worth porting, or worth deleting?** It is a read-only diagnostic,
   and diagnostics accrete. Porting is ~a day. Deleting is ten minutes. Task 6 requires
   answering this before writing any code.
