---
name: select-coder
description: Use when choosing which coder agent and model should execute a coding task — e.g. "which model should implement this", "pick the best coder for these packets", "what's the cheapest model that can handle X", or /select-coder. Scores the task against a capability matrix (correctness, speed, cost, creativity, autonomy, verification behavior) and the locally available agents/models, then recommends ranked `<backend>:<model>` specs. Works standalone or as a subagent; orchestrate-coders uses it for per-packet assignment overrides.
---

# select-coder — pick the right agent and model for the task

Given a task (or a set of orchestrate-coders packets), recommend which coder
backend and model should execute it. The output is one or more ranked coder
specs in orchestrate-coders syntax — `opus:claude-sonnet-5`,
`codex:gpt-5.4-mini`, `agy:Gemini 3.5 Flash (High)` — with a one-line
rationale each, directly usable as `--coder` arguments.

Two inputs drive the choice:

1. **What's available** — the cached availability block in
   `dev_docs/orchestrate-coders/.coders.yml` (pre-flight below). Never
   recommend a backend/model the machine can't run.
2. **What the task needs** — scored against the capability matrix in
   [`matrix.md`](matrix.md).

## Pre-flight: availability probe

Availability is probed once and cached — not on every invocation. The cache
lives in `dev_docs/orchestrate-coders/.coders.yml` (same local, git-excluded
config orchestrate-coders uses) under an `availability:` block:

```yaml
availability:
  probed_at: 2026-07-03
  opus: # always available (Agent tool) — models per session availableModels
    models: [
      claude-opus-4-8,
      claude-sonnet-5,
      claude-sonnet-4-6,
      claude-haiku-4-5,
    ]
  codex:
    installed: true
    default_model: gpt-5.5 # from `codex config get model` or config.toml
  agy:
    installed: true
    logged_in: true # script-probed via `agy models` (free); false when the token is dead
  devin:
    installed: true
    logged_in: true # script-probed via `devin auth status`; false when creds are dead
    tier: pro # free tier pins swe-1.6-slow; pro unlocks swe-1.6/-fast + passthroughs
```

**When to (re)probe:** block absent, `--refresh` passed, `probed_at` older
than 30 days, or a recommendation just failed because a model turned out to
be unavailable. Otherwise trust the cache.

**Probing is scripted** — run the deterministic fixture instead of ad-hoc
commands (unsandboxed if agy or devin is installed; `devin auth status` and
`agy models` touch the network — both are cheap auth probes, no quota):

```bash
scripts/probe-coders.sh
```

It emits the `availability:` block on stdout (it writes nothing). The script
auth-probes **both** cloud coders — `devin auth status` and `agy models` (a
free metadata list, no inference/quota) — so `agy.logged_in` and
`devin.logged_in` are trustworthy. This matters most for agy: over SSH it
falls back to a file-based token store that GUI logins don't refresh, so a
present-but-dead agy is common and worth catching before dispatch (it's the
same probe co-review's pre-flight uses — keep the two in sync). One field the
script can't know, fill in yourself before merging:

- `opus.models` — the script emits `[]`; populate from the session's
  `availableModels` (opus itself is always available).

A devin `tier: unknown` still resolves on first use (an `/upgrade to access
this model` error means free tier → `swe-1.6-slow` only).

Write the block back after probing. Create
`dev_docs/orchestrate-coders/.coders.yml` if absent — the file may then hold
only `availability:`, which orchestrate-coders treats as absent for its own
`default_coder` setup (it merges its keys in later) — and keep it out of git:
`git check-ignore -q dev_docs/orchestrate-coders/ || echo 'dev_docs/orchestrate-coders/' >> "$(git rev-parse --git-dir)/info/exclude"`.

## Selection

1. **Profile the task** along the matrix dimensions. Most tasks are obvious
   from the description; when genuinely ambiguous between profiles, ask one
   question rather than guessing — **unless running non-interactively** (as a
   subagent, e.g. under orchestrate-coders, where no user is reachable): then
   don't block — pick the most likely profile, and state the assumption plus
   the runner-up profile in the report so the caller can override.

2. **Map profile → candidates** using the routing table in `matrix.md`,
   filtered to what's available. Produce a ranked list (best first), max 3.

3. **Apply the operational modifiers** (also in `matrix.md`) — these come
   from real pilot runs and outrank benchmark deltas:
   - devin packets always return unverified → fine for edits, penalize when
     the task's value is in the verification.
   - codex sandbox false-FAILs on home-dir caches → orchestrator re-runs
     checks; not a reason to avoid codex, but don't pick it for tasks whose
     spec _is_ the check output.
   - agy needs the containment backstop → penalize for tasks touching many
     paths near the main checkout; fine for scoped worktree edits.
   - opus self-verifies honestly → prefer when verification honesty matters.

4. **Report**: per candidate — the coder spec, one-line why, and the cost
   tier (`$`, `$$`, `$$$` per matrix). If the top pick is unavailable but
   would clearly win, say so and name what it would take (e.g. "devin pro
   tier would unlock swe-1.6-fast").

## Invocation

```
/select-coder <task description> [--plan <name>] [--refresh] [-n N]
```

- `<task description>` — the task to route. With `--plan <name>`, score each
  task file in `dev_docs/tasks/<name>_plan/` and emit one spec per packet —
  the per-packet assignment orchestrate-coders' step 4 allows as a
  round-robin override.
- `--refresh` — force a re-probe of availability.
- `-n N` — return the top N candidates per task (default 3).

## Rules

- Never recommend an unavailable backend/model without flagging it as such.
- The matrix carries a cache date. If it is older than ~2 months, or the
  user asks about a model the matrix doesn't list, refresh per the protocol
  at the top of `matrix.md` before recommending — don't answer from a stale
  table as if it were current.
- Cost figures are directional, not billing-grade: subscription-quota
  backends (agy, devin) don't map cleanly to $/Mtok. Use the tiers.
- When two candidates are within noise on the task's primary dimension,
  prefer the cheaper/faster one and say that's the tiebreak.
- This skill recommends; it never dispatches. Execution belongs to
  orchestrate-coders (or the user).
