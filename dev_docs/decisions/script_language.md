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
grows past a few hundred lines must pass **`pyright --strict`** in `just check`. That claims
the one real advantage TypeScript held over Python without incurring its costs.

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
- `pyright --strict` becomes part of `just check` for non-trivial Python. Without it, this
  decision has silently conceded TypeScript's only real advantage.
- Short scripts stay bash. This decision is not a licence to rewrite `claude-usage.sh`,
  `await-pr-review.sh`, or `preflight-freshness.sh` — at 137–312 lines of `git`/`gh`/`jq`
  glue, they are shell at its best.
- The supervisor wake loop may permanently remain bash. That is an accepted outcome, not a
  failure — see the port plan's task 8.
- If Claude Code ever bundles a runtime, or gains a plugin install hook, this decision is worth
  reopening.
