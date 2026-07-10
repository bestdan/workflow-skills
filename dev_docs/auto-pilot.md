# Auto-pilot mode — design decisions

Auto-pilot advances a whole task graph unattended. This doc is the durable
**why** — the cross-cutting decisions made while building it, and a map to where
the mechanics live. It is deliberately not an "architecture" restatement: the
skills and their references below are authoritative for _how_ things work, and
this doc never duplicates their formats (which would only drift out of sync).

## The two skills

- **`/deliver-task`** — the per-task lifecycle primitive: claim → implement →
  open PR → co-review → iterate → hand off. Useful standalone, interactively; it
  is also the per-task unit `/auto-pilot` calls.
- **`/auto-pilot`** — interactive **launch** (a fail-closed pre-flight, then
  spawn a detached orchestrator) → unattended **run** loop (walk the graph,
  `/deliver-task` per task, within budget bounds) → **`--resume`** (reconcile a
  crashed or paused run against git + the tracker, then re-enter the loop).

## Where the mechanics live

| Concern                                                                      | Authoritative source                                                                                  |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Per-task lifecycle                                                           | [`skills/deliver-task/SKILL.md`](../skills/deliver-task/SKILL.md)                                     |
| Launch / run / resume phases                                                 | [`skills/auto-pilot/SKILL.md`](../skills/auto-pilot/SKILL.md)                                         |
| Run-state formats, the seven phases, write order, crash-reconciliation table | [`skills/auto-pilot/references/run-state.md`](../skills/auto-pilot/references/run-state.md)           |
| Work-source adapter interface (the eight verbs)                              | [`skills/auto-pilot/references/adapters.md`](../skills/auto-pilot/references/adapters.md)             |
| Orchestrator spawn mechanics + sandbox profile                               | [`skills/auto-pilot/references/launch-runtime.md`](../skills/auto-pilot/references/launch-runtime.md) |
| Budget bounds (rate window, pause/relaunch, circuit breaker)                 | [`skills/auto-pilot/references/run-budget.md`](../skills/auto-pilot/references/run-budget.md)         |

## Decisions (locked)

The rationale a future dev can't reconstruct from any single file — including the
things deliberately _not_ built.

- **Compose, never duplicate.** `/deliver-task` and `/auto-pilot` are
  compositions over existing, battle-tested skills and handler protocols, not a
  fork of their logic: claim reuses each handler's own claim protocol, implement
  reuses `select-coder` + `orchestrate-coders`, review reuses `/co-review`. Where
  an existing skill almost fit, it was tweaked (e.g. co-review gained a
  `--non-interactive` mode) rather than re-implemented in auto-pilot prose. This
  is the load-bearing constraint on every future change: solve a new need by
  extending an existing skill, not by restating its behavior here.
- **Hand-off ≠ done.** `/deliver-task` and every adapter's `set_needs_review`
  stop at `needs_review`; nothing in-run merges a PR or completes a tracker item.
  Completion is merge-verified later, by `/sweep-for-complete`, with a human's PR
  approval in between. This keeps an unattended run from ever declaring victory
  on unreviewed code.
- **Stacked-PR freeze rule.** Once a task hands off, its PR is frozen: late
  findings are logged for the morning report, never applied mid-run. This is what
  lets a chained child branch from a guaranteed-stable parent tip — the run loop
  compares the parent's live tip against the child's recorded frozen-tip SHA and
  parks the child if they've diverged.
- **Sandboxed yolo.** The orchestrator runs `bypassPermissions` — no per-action
  prompts — but only inside a two-layer sandbox (OS-level filesystem/process
  confinement, plus a host network egress allowlist narrowed at launch to the
  tools this run actually uses). The jail, not a human, is what bounds it.
- **Claim stays per-handler, not centralized.** Each adapter's `claim` verb
  delegates to its own handler's claim protocol (Linear's token-comment election,
  repo-pr's claim-PR markers) rather than a shared claim skill. Only two verbs
  reference claim logic today, so abstracting it now would guess at a shape a
  third caller might not fit — the rule of three: don't extract a shared
  abstraction until a third consumer shows up. A future shared claim skill, once
  a third claim-referencing verb exists, is tracked as PRE-467.

## Out of scope (v1)

Merging or tracker-completing anything unattended; `--budget` dollar/token caps
(rate-window discipline + per-task bounds only); jira/gh-issue adapters (linear +
plan only); reopening a frozen PR in-run; multi-night continuation beyond
`--resume`.
