# Auto-pilot mode — architecture

"Claude, pick up this Project and advance it independently — work the
workstream without a person in the loop."

Auto-pilot is two skills, not one:

- **`/deliver-task`** — the per-task lifecycle primitive: claim → implement →
  open PR → co-review → iterate → hand off. Useful on its own, interactively;
  it is also the per-task unit `/auto-pilot` calls.
- **`/auto-pilot`** — an interactive **launch** phase (pre-flight, then spawn)
  followed by an unattended **run** phase: a thin orchestrator that walks a
  task graph, calling `/deliver-task` once per task, with durable,
  crash-resumable state and no human in the loop.

Full mechanics live in the skills:
[`skills/deliver-task`](../skills/deliver-task) and
[`skills/auto-pilot/SKILL.md`](../skills/auto-pilot/SKILL.md) plus its four
references. This doc is the durable "why," not the reference formats — it
links to those rather than restating them.

## Design principle: compose, never duplicate

`/deliver-task` and `/auto-pilot` are compositions over existing, battle-tested
skills and handler protocols, not a fork of their logic. Each lifecycle step
reuses an existing verb: claim reuses the handler's own claim protocol
(Linear token-comment election, repo-pr claim-PR markers); implement reuses
`select-coder` + `orchestrate-coders`; review reuses `/co-review` (given a
`--non-interactive` mode); completion is explicitly **not** done in-run — it
stays merge-verified via `/sweep-for-complete`. Where an existing skill almost
fit, that skill was tweaked (e.g. co-review's non-interactive mode) rather
than having its behavior re-implemented in auto-pilot prose.

## `/deliver-task` — the per-task lifecycle

One task in, one reviewed PR out. Ends at **hand-off, never done**: the
task's terminal in-run state is `needs_review` (PR open, reviewed, evidence
attached). Completion is a separate, later, merge-verified step.

Steps: claim → do (worker dispatch via `select-coder`/`orchestrate-coders`,
verified against the project's check command and an end-to-end exercise) →
open PR → co-review (non-interactive, bounded reviewer timeouts) → iterate
(≤ 2 rounds) → hand off (tracker → `needs_review`, phase → `handed-off`,
**freeze rule**: a handed-off PR is frozen for the rest of the run — no more
edits land on it, so a stacked child's base never moves under it mid-run).

## `/auto-pilot` — launch, run, resume

**Launch** (interactive, while a human can still fix failures) is
fail-closed: worktree + run-state branch setup, non-interactive auth probes,
resolving every config decision the run could hit into a fixed non-prompting
choice, then spawning a detached orchestrator. See
[`skills/auto-pilot/SKILL.md`](../skills/auto-pilot/SKILL.md) "Launch phase"
and [`skills/auto-pilot/references/launch-runtime.md`](../skills/auto-pilot/references/launch-runtime.md)
for the spawn mechanics (detached `claude -p`, not an in-session agent) and
the sandbox profile.

**Run** (unattended) is a thin loop that never writes code itself: pick the
next unblocked task, `/deliver-task` it (bounded wall-clock + retry), update
run state, check budget headroom. See
[`skills/auto-pilot/references/run-budget.md`](../skills/auto-pilot/references/run-budget.md)
for the rate-window, pause/relaunch, hard-stop, and circuit-breaker rules.

**Resume** reconciles the run-state branch against git and the tracker after
a crash, using the write order and the crash-reconciliation table defined in
the run-state reference (see State model below).

## State model

Three stores exist; only one is authoritative for a given fact. The tracker
is authoritative for task status; git is authoritative for code/PR existence;
the run files (`RUN.md` / `QUESTIONS.md` / `REPORT.md`, on a dedicated
`auto-pilot/<run_id>` branch, never a task branch) are a cache + report and
are always allowed to be behind the tracker and git, never ahead.

Every phase advance writes in a fixed order — **push code → update tracker →
commit run state** — which is the whole basis for crash recovery: after any
crash, `remote ≥ tracker ≥ run files` always holds, so `--resume` reconciles
by trusting git first, then the tracker, then rewriting the run files to
match.

Authoritative formats, the seven task-lifecycle phases, and the full
crash-reconciliation table live in
[`skills/auto-pilot/references/run-state.md`](../skills/auto-pilot/references/run-state.md)
— this doc does not restate them.

## Work-source adapters

`/auto-pilot` runs against either a **Linear project** or a
**plan-with-docs directory**; both normalize to the same run-state
representation via an adapter, so nothing downstream branches on source.
Each adapter implements the same eight verbs — `list_ready`,
`dependency_graph`, `claim`, `link_pr`, `set_needs_review`, `flag_for_human`,
`comment_progress`, `wip_limit` — by delegating to its handler's existing
sections; source-specific differences (WIP caps, labels, MCP read-lag,
branch naming) live inside the adapter, never downstream.

Verb-by-verb delegation for the `linear` and `plan` adapters lives in
[`skills/auto-pilot/references/adapters.md`](../skills/auto-pilot/references/adapters.md).

## Decisions (locked)

- **Compose, never duplicate** — see above. This is the load-bearing
  constraint on every future change to these skills: a new lifecycle need
  gets solved by tweaking (or adding a mode to) an existing skill, not by
  writing bespoke auto-pilot prose that could drift from it.
- **Hand-off ≠ done.** `/deliver-task` and every adapter's `set_needs_review`
  stop at `needs_review`; nothing in-run merges a PR or completes a tracker
  item. Completion is merge-verified later, by `/sweep-for-complete`, with a
  human's PR approval in between.
- **Stacked-PR freeze rule.** Once a task hands off, its PR is frozen: late
  findings are logged for the morning report, never applied mid-run. This is
  what lets a chained child branch from a guaranteed-stable parent tip — the
  run-loop's stacked-PR check compares the parent's live tip against the
  child's recorded frozen-tip SHA and parks the child if they've diverged.
- **Sandboxed yolo.** The orchestrator runs `bypassPermissions` — no
  per-action prompts — but only inside a two-layer sandbox (OS-level
  filesystem/process confinement, plus a host network egress allowlist
  narrowed at launch to the tools this run actually uses). The jail, not a
  human, is what bounds it. Full profile in
  [`skills/auto-pilot/references/launch-runtime.md`](../skills/auto-pilot/references/launch-runtime.md)
  "Sandbox profile."
- **Claim stays per-handler, not centralized.** Each adapter's `claim` verb
  delegates to its own handler's claim protocol (Linear's token-comment
  election, repo-pr's claim-PR markers) rather than a shared claim skill.
  Only two verbs reference claim logic today, so abstracting it now would
  guess at a shape a third caller might not fit — the rule of three: don't
  extract a shared abstraction until a third consumer actually shows up. A
  future shared claim skill, once a third claim-referencing verb exists, is
  tracked as PRE-467.

## Out of scope (v1)

Merging or tracker-completing anything unattended; `--budget` dollar/token
caps (rate-window discipline + per-task bounds only); jira/gh-issue adapters
(linear + plan only); reopening a frozen PR in-run; multi-night continuation
beyond `--resume`.
