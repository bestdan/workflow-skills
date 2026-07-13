# Decision: what language do this repo's scripts get written in?

**Status:** accepted
**Date:** 2026-07-13
**Context:** raised while planning the port of `scripts/spawn-orchestrator.sh`
([`dev_docs/orchestrator-python-port.md`](../orchestrator-python-port.md)), but the decision
governs **all** non-trivial scripting in this repo, not just that port.

## Decision

**Python, run via `uv` with PEP 723 inline metadata**, for anything that outgrows shell.
Short scripts (roughly < 300 lines of glue around `git`/`gh`/`jq`) **stay in bash**.

Static typing is taken as an explicit obligation, not left to discipline: any Python that
grows past a few hundred lines must pass **`pyrefly`** (strict) plus **`ruff`** in
`just check`. That claims the one real advantage TypeScript held over Python without
incurring its costs.

> **Not `pyright`.** An earlier draft of this decision specified `pyright --strict`. Measured
> on a 6,000-line codebase it takes **1,014 ms** — 5x slower than TypeScript 7's compiler, and
> 4x slower than `pyrefly`. Mandating it would have conceded the fast-feedback argument to
> TypeScript on the very axis this decision claims to win. See the benchmark below.

**TypeScript is rejected. Go is rejected for now**, with the specific condition under which
it would be revisited recorded below.

## The question

The orchestrator port raised a fair challenge: _"should it be Python? What about TypeScript —
the new 7 seems fast? I'm happy to rewrite existing Python if something is better."_ Since the
answer commits nine tasks of work and the repo's existing Python (`validate.py`,
`bump-version.py`), it was worth settling with evidence rather than taste.

## Why not TypeScript

**1. The "TS 7 is fast" premise measures the wrong thing.** TypeScript 7 is a Go rewrite of
the **compiler and language service** — parser, checker, emitter, LSP. Its ~10x is
**type-checking, build, and editor-load latency**. TypeScript still emits ordinary JavaScript
which runs on V8 at exactly the same speed as before; there is no TypeScript runtime to make
faster.

- <https://devblogs.microsoft.com/typescript/typescript-native-port/>
- <https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/>

For a ~6,000-line CLI, typecheck latency was never the bottleneck. The headline feature
optimizes an axis this repo does not use.

**2. It is very new, and incomplete where it matters for tooling.** TS 7.0 reached GA on
**2026-07-08** — five days before this decision — and ships **without a stable programmatic
compiler API**. Vue, Astro, Svelte, and Angular tooling cannot use it yet and are waiting on
7.1. Fine for an app codebase; a poor bet for a security-critical orchestrator.

**3. Decisively: Node is now the _least_ safe runtime assumption available to us.** Claude
Code is distributed as a **native binary** — the local `claude` resolves to a Mach-O arm64
executable under `~/.local/share/claude/versions/`. The official docs lead with the native
installer, list **no Node.js** among system requirements, and state that even the npm package
"installs the same native binary" and that the installed binary "does not itself invoke Node."

- <https://code.claude.com/docs/en/setup>

**A Claude Code user is therefore no longer guaranteed to have Node at all.** Choosing
TypeScript would add back, for consumers of this plugin, a runtime dependency that Claude Code
itself just shed. (Note: guides advising "Claude Code requires Node, so write hooks in `.mjs`"
are stale and now backwards.)

## Why Python

**It is already paid for.** Users of `/auto-pilot` already need macOS, `claude`, and **`gh`**
(a Homebrew install) — a third-party dependency is already accepted in this exact code path.
And the plugin **already mandates `uv run` at consumer runtime**: the `analysis-pipeline` and
`analysis-conventions` skills both require it explicitly. `scripts/validate.py` already
establishes the convention — PEP 723 inline metadata, `uv run`, hash-locked in
`validate.py.lock`.

Python is not a new dependency class here. `uv` is the same class of ask as `gh`.

**It fits the workload.** The orchestrator's work is string templating (seatbelt `.sb`
S-expressions, launchd plists, generated shell), JSON, and subprocess orchestration (`git` and
`gh`, 107 calls each). That is Python's stdlib sweet spot — and it deletes the 16 `jq`
shell-outs outright. No build step; single-file scripts stay single files.

**Startup latency does not break the tie.** Measured on the dev machine, mean of 20 runs:

| Runtime            | Startup |
| ------------------ | ------- |
| `bash`             | 2.9 ms  |
| `/usr/bin/python3` | 23.8 ms |
| `node`             | 26.5 ms |
| `uv run` (PEP 723) | 32.2 ms |

Python and Node are within noise of each other. All are ~10x bash, and all are irrelevant to a
supervisor that wakes every few minutes. Latency picks no winner — so it cannot be used to
justify the runtime-dependency cost of Node.

## Developer and agent feedback speed (the strongest argument for TypeScript)

The best case for TypeScript is not production speed — it is **loop speed**: bash is fragile
and near-impossible to check before running, and a lot of the work here is agentic, where a
fast, reliable "this code is wrong" signal before execution is worth real money. If TypeScript
gave a materially faster or stronger pre-run check, that would be a legitimate reason to eat
the Node dependency.

**It does not.** Measured on this machine — two equivalent **~6,000-line** codebases (typed
records, a closed `Literal` state union modelled on the orchestrator's real exit
classification, dict/JSON munging), mean of 5 warm runs:

| Check                              | Language   | Time         | Catches the unhandled-variant bug?  |
| ---------------------------------- | ---------- | ------------ | ----------------------------------- |
| `tsc` 7.0.2 (native/Go)            | TypeScript | **191 ms**   | yes                                 |
| `ty` 0.0.59 (Rust, **beta**)       | Python     | **197 ms**   | yes                                 |
| `pyrefly` 1.1.1 (Rust, **stable**) | Python     | **255 ms**   | yes                                 |
| `pyright` 1.1.411 (Node)           | Python     | 1,014 ms     | yes                                 |
| `ruff` 0.15.21 (lint, Rust)        | Python     | 179 ms       | n/a                                 |
| —                                  |            |              |                                     |
| `shellcheck` (the status quo)      | **Bash**   | **2,421 ms** | **no — it cannot see types at all** |
| `bash -n` (syntax only)            | Bash       | 17 ms        | no                                  |

Three things fall out of this, and they reframe the question:

1. **TypeScript 7 and Python's Rust checkers are a dead heat at this scale** (191 ms vs
   197 ms). TS 7's ~10x is real, but it is measured against _tsc-on-Node_ on codebases 100x
   this size. Python's toolchain had the same Rust rewrite — `ty` (Astral, same vendor as the
   `uv` we already depend on) and `pyrefly` (Meta, stable since v1.0 in May 2026). The gap TS 7
   opened over Python was closed before we got here.
2. **The exhaustiveness check — the property that actually matters — works in Python.** Adding
   a sixth variant to the exit-classification union and failing to handle it is caught by
   `tsc`, `ty`, **and** `pyrefly` alike (via `Literal` + `match` + `assert_never`). This is the
   "add a case, the compiler forces you to handle it everywhere" workflow, and Python has it.
   Verified, not assumed.
3. **The status quo is the outlier, and it loses on both axes.** ShellCheck on the real
   6,284-line orchestrator takes **2,421 ms** — slower than every type checker here — and
   catches **zero** type errors, because bash has no types to check. A script asserting
   `[ "$n" = "five" ]` against an integer passes ShellCheck clean.

**So the fast-feedback argument is correct, and it is an argument for leaving bash — not for
choosing TypeScript over Python.** Both candidates serve it equally; only bash fails it. The
argument's real force is that it makes the port _more_ urgent, and it fixes which checker we
gate on.

Practical consequence: `ruff` (179 ms) + `pyrefly` (255 ms) gives a **sub-500 ms** full
pre-run gate on a 6k-line port — fast enough to sit in an agent's inner loop, and roughly
**5x faster than the ShellCheck pass it replaces**, while catching an entire class of error
ShellCheck is structurally blind to.

**Why `pyrefly` and not `ty`:** `ty` is marginally faster (197 vs 255 ms — noise in an agent
loop) and comes from the same vendor as `uv`, but it is **beta** (`0.0.59`) with ~53–67%
typing-spec conformance, against `pyrefly`'s stable v1.x and ~87–92%. For a security-critical
port, a checker that _silently misses_ errors is a worse failure than one that takes another
58 ms. Revisit when `ty` hits 1.0.

- <https://astral.sh/blog/ty> · <https://github.com/astral-sh/ty>
- <https://pyrefly.org/blog/speed-and-memory-comparison/> · <https://github.com/facebook/pyrefly>

## Why not Go (the serious alternative)

Go, not TypeScript, was the real contender, and it is rejected on cost rather than merit.

**Its genuine appeal:** the orchestrator port's whole Tier A / Tier B split exists _only_
because the generated launch script runs under **launchd with a pinned minimal PATH** and
cannot be trusted to resolve an interpreter at 3am. **A compiled binary has no interpreter to
resolve.** Go would let the entire file port — supervisor loop included — with startup faster
than bash, real static typing over 6k lines, and no `jq`.

**Why not, today:**

- **Distribution.** Claude Code has **no install or postinstall hook**. The documented options
  are committing per-architecture binaries under `bin/` (which _is_ added to the Bash tool's
  PATH — verified in the [plugins reference](https://code.claude.com/docs/en/plugins-reference))
  or a `SessionStart` hook that downloads into `${CLAUDE_PLUGIN_DATA}`. A macOS universal
  binary (`lipo`) would reduce this to one artifact, but it is still a release pipeline to
  build and maintain for a solo repo.
- **It is worse at this specific workload.** Generating S-expressions, plists, and shell with
  `text/template` is clumsy next to f-strings, and dynamically-shaped `gh api` JSON fights
  static typing into `any`-unmarshaling boilerplate.

**Revisit Go if** eliminating the last ~1,000 lines of supervisor bash becomes a goal in its
own right — that is the one thing Python cannot do without putting an interpreter on the
launchd PATH, and it is exactly the tradeoff task 8 of the orchestrator port exists to decide.

## Consequences

- The orchestrator port proceeds in Python (`uv`, PEP 723), per
  [`dev_docs/orchestrator-python-port.md`](../orchestrator-python-port.md).
- **`pyrefly` (strict) + `ruff` become part of `just check`** for non-trivial Python, giving a
  sub-500 ms pre-run gate. Without a type checker, this decision silently concedes
  TypeScript's only real advantage; with a _slow_ one (`pyright`, 1,014 ms) it concedes the
  fast-feedback argument instead. Both failure modes are avoidable.
- The port is worth doing **partly for the feedback loop itself**, not only for readability:
  it replaces a 2.4 s type-blind ShellCheck pass with a sub-500 ms gate that catches errors
  bash cannot express. That is a direct win for agentic work on this codebase.
- Short scripts stay bash. This decision is not a licence to rewrite `claude-usage.sh`,
  `await-pr-review.sh`, or `preflight-freshness.sh` — at 137–312 lines of `git`/`gh`/`jq`
  glue, they are shell at its best.
- The supervisor wake loop may permanently remain bash. That is an accepted outcome, not a
  failure — see the port plan's task 8.
- If Claude Code ever bundles a runtime, or gains a plugin install hook, this decision is worth
  reopening.
