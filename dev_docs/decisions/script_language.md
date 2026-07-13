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

> **This condition has already been triggered.** Co-review of PR #205 found that the constrained
> tier is **~2,000+ lines, not ~1,000** — it also holds `doctor`, `status`, `classify_exit`, and
> `exit_reason`, and it is gated by **two** constraints, not one (the Seatbelt exec allowlist was
> missed entirely here). This section rejected Go while assuming Python could take everything
> except a small supervisor loop. **That assumption was false.** The orchestrator port's
> **task 2** now re-examines Go explicitly, before any Python is written. If it survives that
> re-examination, amend this section with the reason it survived under the corrected numbers —
> do not simply cite this original rejection.

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
- **The supervisor wake loop may permanently remain bash.** An accepted outcome, not a failure.
- **Reopen this** if Claude Code bundles a runtime, gains a plugin install hook, or if `ty`
  reaches 1.0 (which would revisit the checker, not the language).
