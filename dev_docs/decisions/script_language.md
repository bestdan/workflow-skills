# Decision: what language do this repo's scripts get written in?

**Status:** accepted
**Date:** 2026-07-13
**Context:** raised while planning the port of `scripts/spawn-orchestrator.sh`
([`dev_docs/orchestrator-python-port.md`](../orchestrator-python-port.md)), but the decision
governs **all** non-trivial scripting in this repo, not just that port.

## Decision

1. **Anything that outgrows shell is written in Python**, run via `uv` with PEP 723 inline
   metadata (the convention `scripts/validate.py` already established).
2. **Short scripts stay in bash.** Roughly < 300 lines of glue around `git`/`gh`/`jq`, gating
   on an exit code. That is shell at its best and there is nothing to gain by moving it.
3. **Every non-trivial Python file is gated by `pyrefly` (strict) + `ruff`** in `just check`.
   Measured at 255 ms + 179 ms on 6,000 lines — a **sub-500 ms** pre-run signal, fast enough
   to sit inside an agent's edit→check loop.
4. **Closed state sets are modelled as `Literal` + `match` + `assert_never`**, so adding a
   variant without handling it everywhere is a _type error_.

**TypeScript is rejected. Go is rejected for now**, each for reasons recorded below, along
with the specific condition that would reopen Go.

## What was actually being asked

Two motivations, and they need separating because they have different answers:

- **Production speed / correctness.** Is Python the right runtime for this workload?
- **Developer and agent loop speed.** Bash is fragile and near-impossible to check before
  running. Much of the work here is agentic, where a fast, reliable "this code is wrong"
  signal _before execution_ is worth real money. Would TypeScript — whose new compiler is
  reportedly 10x faster — give a better loop?

The second is the stronger argument, and it is the one that nearly changed the outcome. It
didn't change _the language_, but it did change _the toolchain_ — see below.

## The evidence

### Startup latency is a non-factor

Mean of 20 runs, this machine:

| Runtime            | Startup |
| ------------------ | ------- |
| `bash`             | 2.9 ms  |
| `/usr/bin/python3` | 23.8 ms |
| `node`             | 26.5 ms |
| `uv run` (PEP 723) | 32.2 ms |

Python and Node are within noise of each other. All are ~10x bash and all are irrelevant to a
supervisor that wakes every few minutes. **Latency cannot be used to justify Node's cost.**

### The feedback loop: TypeScript's best argument, measured

Two equivalent **~6,000-line** codebases (typed records, a closed `Literal` state union
modelled on the orchestrator's real exit classification, dict/JSON munging). Mean of 5 warm
runs. The last column is the property that matters: **add a sixth variant to the union, leave
it unhandled — is that a compile error?**

| Check                              | Language   | Time         | Catches the unhandled variant?      |
| ---------------------------------- | ---------- | ------------ | ----------------------------------- |
| `tsc` 7.0.2 (native/Go)            | TypeScript | **191 ms**   | yes                                 |
| `ty` 0.0.59 (Rust, **beta**)       | Python     | **197 ms**   | yes                                 |
| `pyrefly` 1.1.1 (Rust, **stable**) | Python     | **255 ms**   | yes                                 |
| `pyright` 1.1.411 (Node)           | Python     | 1,014 ms     | yes                                 |
| `ruff` 0.15.21 (lint, Rust)        | Python     | 179 ms       | n/a                                 |
| —                                  |            |              |                                     |
| `shellcheck` (**the status quo**)  | **Bash**   | **2,421 ms** | **no — it cannot see types at all** |
| `bash -n` (syntax only)            | Bash       | 17 ms        | no                                  |

Three conclusions:

1. **TypeScript 7 and Python's Rust checkers are a dead heat** (191 ms vs 197 ms). TS 7's ~10x
   is real but is measured against _tsc-on-Node_, on codebases 100x this size. Python's
   toolchain had the same Rust rewrite — `ty` (Astral, the vendor behind the `uv` we already
   depend on) and `pyrefly` (Meta, stable since v1.0, May 2026). The gap TS 7 opened over
   Python was closed before we arrived.
2. **Python gets the exhaustiveness property.** `Literal` + `match` + `assert_never` is
   rejected by `tsc`, `ty`, and `pyrefly` alike when a variant goes unhandled. This is the
   "add a case, the compiler forces you to handle it everywhere" workflow, and it was verified
   here, not assumed.
3. **Bash is the outlier and loses on both axes.** ShellCheck on the real 6,284-line
   orchestrator takes **2,421 ms** — slower than every type checker above — and catches
   **zero** type errors, because bash has no types. `[ "$n" = "five" ]` against an integer
   passes it clean.

**So the loop-speed argument is correct, and it is an argument for leaving bash — not for
choosing TypeScript over Python.** Both candidates serve it equally; only the status quo
fails it. Its real force is that it makes the port _more_ urgent, and it dictates which
checker we gate on.

### Why `pyrefly`, not `pyright` or `ty`

- **`pyright` is disqualified on speed**: 1,014 ms, 5x slower than the TypeScript compiler we
  declined. Gating on it would concede the fast-feedback argument on the very axis this
  decision claims to win.
- **`ty` is faster (197 ms) and shares a vendor with `uv`**, but is **beta** (`0.0.59`) at
  ~53–67% typing-spec conformance, versus `pyrefly`'s stable v1.x at ~87–92%. For
  security-critical code, a checker that _silently misses_ errors is a worse failure than one
  costing another 58 ms. **Revisit `ty` when it reaches 1.0.**

Sources: <https://astral.sh/blog/ty> · <https://pyrefly.org/blog/speed-and-memory-comparison/>

## Why not TypeScript

**1. Its headline speed is on an axis we don't use.** TypeScript 7 is a Go rewrite of the
**compiler and language service** — parser, checker, emitter, LSP. The ~10x is type-checking
and editor latency. TypeScript still emits ordinary JavaScript that runs on V8 exactly as fast
as before; there is no TypeScript runtime to speed up. And per the benchmark above, at our
scale it does not even lead.

- <https://devblogs.microsoft.com/typescript/typescript-native-port/>
- <https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/>

**2. It is brand new and incomplete where tooling depends on it.** TS 7.0 reached GA on
**2026-07-08**, five days before this decision, and ships **without a stable programmatic
compiler API**; Vue, Astro, Svelte, and Angular tooling cannot use it yet and await 7.1. Fine
for an app codebase, a poor bet for a security-critical orchestrator.

**3. Decisively — Node is now the _least_ safe runtime assumption available.** Claude Code
ships as a **native binary** (the local `claude` is a Mach-O arm64 executable under
`~/.local/share/claude/versions/`). The docs lead with the native installer, list **no
Node.js** in system requirements, and state that even the npm package "installs the same
native binary" and "does not itself invoke Node."

- <https://code.claude.com/docs/en/setup>

**A Claude Code user is no longer guaranteed to have Node at all.** Choosing TypeScript would
hand plugin consumers back a runtime dependency that Claude Code itself just shed. (Guides
advising "Claude Code requires Node, so write hooks in `.mjs`" are stale and now backwards.)

## Why Python

**It is already paid for.** `/auto-pilot` users already need macOS, `claude`, and **`gh`** (a
Homebrew install), so a third-party dependency is already accepted in this exact code path.
The plugin **already mandates `uv run` at consumer runtime** — `analysis-pipeline` and
`analysis-conventions` both require it — and `scripts/validate.py` already establishes PEP 723

- `uv` + a hash-locked lockfile. `uv` is the same class of ask as `gh`. This is the incumbent,
  not a new dependency class.

**It fits the workload.** The orchestrator's work is string templating (seatbelt `.sb`
S-expressions, launchd plists, generated shell), JSON, and subprocess orchestration (`git` and
`gh`, 107 calls each). That is Python's stdlib sweet spot, and it deletes the 16 `jq`
shell-outs outright. No build step; single-file scripts stay single files.

## Why not Go (the serious alternative)

Go, not TypeScript, was the real contender. It is rejected on cost, not merit.

**Its genuine appeal:** the orchestrator port's Tier A / Tier B split exists _only_ because the
generated launch script runs under **launchd with a pinned minimal PATH** and cannot be trusted
to resolve an interpreter at 3am. **A compiled binary has no interpreter to resolve.** Go would
let the entire file port — supervisor loop included — with startup faster than bash, static
typing, and no `jq`.

**Why not, today:**

- **Distribution.** Claude Code has **no install or postinstall hook**. The documented options
  are committing per-architecture binaries under `bin/` (which _is_ added to the Bash tool's
  PATH — [plugins reference](https://code.claude.com/docs/en/plugins-reference)) or a
  `SessionStart` hook that downloads into `${CLAUDE_PLUGIN_DATA}`. A `lipo` universal binary
  would reduce that to one artifact, but it is still a release pipeline to build and maintain
  for a solo repo.
- **It is worse at this specific workload.** Generating S-expressions, plists, and shell with
  `text/template` is clumsy next to f-strings, and dynamically-shaped `gh api` JSON fights
  static typing into `any`-unmarshaling boilerplate.

**Revisit Go if** eliminating the constrained bash becomes a goal in its own right — a compiled
binary is the only option that satisfies _both_ the launchd pinned-PATH and the Seatbelt
`process-exec` allowlist constraints at once.

> **That condition triggered, Go was re-examined, and it is re-rejected — but not for the reason
> above.** See "The constrained tier's runtime" below. The italicised claim in this section —
> _"a compiled binary is the only option that satisfies both constraints at once"_ — was
> **tested and found false** (2026-07-13). That was Go's entire differentiator. It is retained
> here, struck through in spirit, because the reasoning error is the instructive part: the
> constraint was assumed, never exercised.

## The constrained tier's runtime (orchestrator port, task 2)

**Decision (2026-07-13): stdlib-only Python on a pinned, pre-flight-resolved absolute
interpreter (≥3.11). Go is re-rejected. The constrained tier ports; no bash is frozen.**

### What forced the question

Task 1's audit (`scripts/audit-orchestrator-reachability.py`, PR #206) measured the constrained
tier: **17 subcommands, 2,618 handler lines** — the supervisor, `doctor`, `status`, `teardown`,
the verify broker — against **1,422 lines** of freely-portable renderers. Constrained means
reachable from a context that cannot freely resolve an interpreter: launchd's pinned minimal
PATH (two generated jobs), the Seatbelt `process-exec` allowlist (the jailed run-phase agent),
or an in-process bash call from something that is. At 1.8x the portable tier, "Python takes the
renderers and bash keeps the program" was the likely outcome — so the runtime was settled
**before** any Python was written.

### The claim that collapsed

Both this ADR and the port plan assumed an interpreter could not satisfy the launchd PATH **and**
the Seatbelt exec allowlist at once, making a compiled binary uniquely qualified. **Neither
constraint was ever exercised.** Both fail on contact with the actual jail:

| Candidate                                     | Result, tested under a real rendered profile                                                  |
| --------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `uv run` (option c)                           | **Fails.** Needs `~/.cache/uv` writable — a write grant outside the worktree confinement.     |
| `/usr/bin/python3` (the original option b)    | **Fails** un-granted, and is a CLT shim pinned at **3.9.6**.                                  |
| **uv-managed CPython 3.11.14, absolute path** | **Runs.** One `--exec` grant, no cache writes, no PATH lookup, full stdlib (incl. `tomllib`). |
| Go binary (option d)                          | **Runs.** One `--exec` literal.                                                               |

The decisive distinction is **`uv run` versus the interpreter uv installs.** `uv run` is a
package manager: it wants a cache and a network. The CPython binary it drops at
`~/.local/share/uv/python/cpython-<ver>-.../bin/python3.11` is just an interpreter — invoked by
absolute path it needs no PATH entry, no cache, and no writes. It exec'd inside the jail under a
single grant. So an interpreter **does** satisfy both constraints at once, and Go's sole
differentiator is gone.

With that gone, Go's costs stand unopposed: a release pipeline for a solo repo with no plugin
install hook, and a language that is worse at this program's actual workload (generating
S-expressions, plists, and shell — `text/template` against f-strings — plus dynamically-shaped
`gh api` JSON, which fights static typing into `any`-unmarshaling). **Go is re-rejected on the
corrected numbers, not by citing the original rejection.**

### What the decision requires

Three requirements, all inherited by the constrained-tier port (task 8). They are not new
patterns — they are the ones `write-launch` already uses for `--claude-bin` and `--path`
(resolve absolute, fail closed: `spawn-orchestrator.sh:1363`):

1. **Pre-flight resolve + assert, fail-closed at launch.** Resolve the interpreter and assert
   **≥3.11** _at launch_. A version requirement cannot conjure an interpreter — it can only turn
   a silent break into a loud pre-flight one. **Scope it honestly:** this catches the interpreter
   being _absent or too old at launch_. It cannot see drift that happens _after_ launch — see
   requirements 2–3 and the residual risk below.
2. **Bake the _stable_ path into the launch script — never the version-stamped one.** The launch
   script is written **once** (`write_launch`, `spawn-orchestrator.sh:1471`) and re-executed by
   launchd unchanged on **every** `StartInterval` wake. So baking
   `…/cpython-3.11.14-…/bin/python3.11` means a `uv` upgrade leaves every later wake execing a
   path that no longer exists — silently, at 3am, forever. Bake **`~/.local/bin/python3.11`**,
   the stable symlink `uv python install` maintains and repoints across patch upgrades. This is
   the exact pattern `--claude-bin` already relies on: it bakes the stable `claude` symlink, not
   `versions/2.1.207`.
3. **Grant the symlink's _resolve target_ directory as a subpath.** Seatbelt resolves a symlink
   and checks the **target** against the exec allowlist — granting `~/.local/bin` alone is **not
   enough** and fails with `Operation not permitted` (verified). Grant a subpath over
   `~/.local/share/uv/python`, which covers every installed version — verified with 3.11.14 and
   3.14.2 under one grant. A version-stamped `(literal …cpython-3.11.14…)` grant would re-create
   detached-run finding #3's drift trap on the jail side.

   This is **the same defect class as the `git` CLT-shim bug** (`orch_py_task_10`): a grant on the
   path you _invoke_ is worthless if Seatbelt is checking the path it _resolves to_. Getting one
   of these right and the other wrong is the easy mistake.

**Accepted residual risk — stated in full, because an earlier draft of this section understated
it.** Requirements 2–3 make the setup survive a `uv` **upgrade**. They do **not** survive the
interpreter being **removed** mid-run (`uv python uninstall`, a pruned cache): the symlink
dangles, and requirement 1 — a _launch-time_ assert — cannot see it. A wake would then fail at
exec with no classification, and launchd would relaunch forever doing nothing: finding #22's
silent-success failure class (`spawn-orchestrator.sh:3349`).

**Be honest about the ledger:** this is a drift surface a **compiled binary would not have at
all** — Go needs none of requirements 1–3. It is accepted anyway because it takes a _manual_ `uv`
operation, mid-run, on a single-user host to trigger (uv does not auto-upgrade), and because it
is still a far smaller surface than `uv run` (no cache, no network, no resolver). **Mitigation
for task 8:** have the generated wake script test `[ -x "$interpreter" ]` and route a missing
interpreter through the supervisor's halt path, so it lands as a _classified halt with an alarm_
rather than an unclassified exec failure. That converts the residual from silent to loud, which
is the property requirement 1 was claimed to provide and, on its own, does not.

**Stdlib-only, and that is now cheap.** At ≥3.11 the stdlib covers the whole job (`json`, `re`,
`subprocess`, `pathlib`, `tomllib`), so the constrained tier needs no third-party packages and
therefore no resolver in the jail. `uv`+PEP 723 remains the convention for **unconstrained**
scripts (`validate.py`, the Tier A renderers), where a dependency is free.

## Consequences

- **The toolchain is adopted repo-wide**, over the 1,845 lines of Python that already exist —
  not just for new code. Planned in
  [`dev_docs/tasks/py_toolchain_plan/`](../tasks/py_toolchain_plan/py_toolchain_plan.md).
- **The orchestrator port proceeds in Python**, per
  [`dev_docs/orchestrator-python-port.md`](../orchestrator-python-port.md), and depends on the
  toolchain landing first.
- **The port is worth doing partly for the feedback loop itself**, not only for readability: it
  replaces a 2.4 s type-blind ShellCheck pass with a sub-500 ms gate that catches an entire
  class of error bash cannot express. That is a direct win for agentic work here.
- **Short scripts stay bash.** This is not a licence to rewrite `claude-usage.sh`,
  `await-pr-review.sh`, or `preflight-freshness.sh` — at 137–312 lines of `git`/`gh`/`jq` glue,
  they are shell at its best.
- ~~**The supervisor wake loop may permanently remain bash.**~~ **Superseded 2026-07-13 by "The
  constrained tier's runtime" above.** The wake loop was expected to stay bash because no
  interpreter was thought able to satisfy the launchd PATH and the Seatbelt exec allowlist at
  once. Tested, that turned out to be false: a pinned absolute CPython runs in the jail under one
  grant. **The whole file is portable**, and the constrained tier is no longer frozen.
- **Two Python invocation styles, on purpose.** Unconstrained scripts use `uv` + PEP 723
  (`validate.py`, the Tier A renderers) — dependencies are free there. Constrained code is
  **stdlib-only on a pinned absolute interpreter**: no `uv run`, no resolver, no cache, nothing
  to resolve at 3am. The split is the constraint, not a style preference.
- **Reopen this** if Claude Code bundles a runtime, gains a plugin install hook, or if `ty`
  reaches 1.0 (which would revisit the checker, not the language).
