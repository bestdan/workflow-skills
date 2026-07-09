---
name: auto-pilot
description: Unattended autonomous mode — "pick up this Project and grind on it overnight." Runs a task graph (a Linear project or a plan-with-docs directory) task-by-task in an isolated worktree, taking each through /deliver-task (claim → implement → PR → co-review → hand-off) with durable, crash-resumable state and no human in the loop. Use when the user wants a body of work advanced autonomously and unattended. NOTE - v1 is under construction; this entry establishes the skill home and the run-state reference. Launch, run, and resume flows land in later tasks.
---

# auto-pilot — unattended autonomous runs

"Claude, pick up this Project and grind on it overnight."

Auto-pilot advances a whole task graph without a human in the loop: an isolated
worktree, a thin orchestrator that walks the graph, and `/deliver-task` per task
(claim → implement → PR → co-review → iterate → hand-off). It composes existing,
battle-tested skills and handler protocols rather than duplicating them.

Design: [`../../dev_docs/tasks/auto-pilot-mode-design.md`](../../dev_docs/tasks/auto-pilot-mode-design.md).

> **Status:** v1 is being built. This SKILL.md establishes the skill home, the
> references below, and the interactive **launch** phase. The unattended **run**
> loop (which the spawned orchestrator executes) and **`--resume`** land in later
> tasks; until they do, launch will spawn an orchestrator whose run loop is not
> yet implemented, so the skill is not yet invocable end-to-end.

## References

- [`references/run-state.md`](references/run-state.md) — the canonical formats
  and invariants for a run's durable state: the three run files
  (`RUN.md` / `QUESTIONS.md` / `MORNING.md`), the seven task lifecycle phases,
  the dedicated run-state branch convention, the fixed write order
  (push code → update tracker → commit run state), and the crash-reconciliation
  table `--resume` uses. Launch, run, and resume all read this — no other
  document restates a run-state format.
- [`references/adapters.md`](references/adapters.md) — the work-source adapter
  interface: the eight verbs (`list_ready`, `dependency_graph`, `claim`,
  `link_pr`, `set_needs_review`, `flag_for_human`, `comment_progress`,
  `wip_limit`) that normalize a Linear project or a plan-with-docs directory to
  the run-state representation, each delegating to an existing handler section.
- [`references/launch-runtime.md`](references/launch-runtime.md) — the two launch
  runtime decisions: how the detached orchestrator is spawned (detached
  `claude -p`, not an in-session Agent) and the sandbox profile it runs under
  (worktree-confined writes, a default-deny network egress allowlist, and the
  non-interactive credential model). Launch's spawn step reads this.

## Launch phase (interactive)

Invoked by `/auto-pilot <linear-project | plan-dir> [--until <time>] [--resume]`
(`commands/auto-pilot.md`). Launch runs **interactively, tonight, while the human
can still fix failures** — so it is **fail-closed**: any hard pre-flight failure
**BLOCKS LAUNCH** with a specific, fixable message rather than deferring the
problem to 3am. It ends by spawning the detached, unattended orchestrator and
telling the user to go to bed. The unattended **run** loop and **`--resume`** are
separate (later) tasks; the run loop is what the spawned orchestrator executes.

**Preamble — parse + resolve.** Parse `<source>`, `--until`, `--resume`. If
`--resume` is present, **stop** with the PRE-465 pointer (per
`commands/auto-pilot.md`) — it is not implemented here. Detect the source
(existing `dev_docs/tasks/<name>_plan/` dir → **plan**; else → **linear**
project) and resolve the handler from `dev_docs/tasks/.task-config.yml`; any
handler other than `linear`/`repo-pr` → stop (v1 supports linear + plan only).
Pick the matching adapter (`references/adapters.md`).

The pre-flight is an **ordered, fail-closed** sequence. Steps 2–5 are the
config/auth/output checks (fleshed out in the next task); steps 1, 6, and 7
below are complete.

### Step 1 — Worktree + run-state branch (BLOCKS LAUNCH)

Create the dedicated run **worktree** and the **run-state branch**
`auto-pilot/<run_id>` (the `<run_id>` and branch convention are defined in
[`references/run-state.md`](references/run-state.md) "Run-state branch"). Confirm
the plan / task instructions are **committed and present in the worktree** — an
**untracked plan is a launch blocker** (it is one of the real overnight failures
this pre-flight exists to catch: a plan that lives only in an uncommitted file
never reaches the detached orchestrator). If the source is a plan directory, the
plan files must be committed on the branch the run builds from; if Linear, the
project must resolve and be reachable. Fail here with the specific missing
artifact, not a generic error.

### Step 2 — Non-interactive auth probes (BLOCKS LAUNCH)

Probe every credential the run will need, each **non-interactively** — a probe
that would open a prompt (a browser OAuth, a biometric `op signin`) is itself the
failure (see [`references/launch-runtime.md`](references/launch-runtime.md) §3).
Probe, per dependency:

- **GitHub** — `gh auth status` (PRs + git push).
- **Linear** (linear source) — resolve the API key from its `op://` reference
  (`api_key_ref` in `commands/handlers/linear-common.md`) via `op read`, and run
  that file's shared **preflight** (`list_teams` → match the team) to confirm the
  key works, not just that it resolves.
- **Coder CLIs** — the configured coders' auth probes, exactly as
  `scripts/probe-coders.sh` runs them (codex is stateless; `agy`/`devin` have the
  rc-gated `agy models` / `devin auth status` probes). A logged-out coder = a
  blocker, not a silent skip, since the run depends on it.
- **MCP** — any MCP the tasks touch: one cheap read call to confirm the token is
  live.

Each probe runs **through the sandbox wrapper** the orchestrator will use (per
§"Step 7"), so a probe can't pass outside the jail while failing inside it. Any
interactive-only or failing auth **BLOCKS LAUNCH** with the specific dependency
named.

### Step 3 — Resolve config into non-interactive choices (BLOCKS LAUNCH)

Collapse every config decision the unattended run could hit into a fixed choice,
so nothing prompts at 3am:

- **Co-review reviewer set** — resolve `/co-review`'s reviewer set from
  `.co-review.yml` into the concrete list that will run under `--non-interactive`
  (bounded per-reviewer timeouts; the reviewer prompt is never asked mid-run).
- **Coder config** — run `select-coder` once to resolve each task's
  `<backend>:<model>` from the capability matrix, so `orchestrate-coders`
  dispatches without prompting for a missing default.
- **Custom/local commands** are **disabled** for the run unless explicitly
  approved at this step (untrusted-config posture, matching co-review's rule).

Any decision that can't be resolved here — and would therefore prompt mid-run —
**BLOCKS LAUNCH**.

### Step 4 — Gitignore sanity check (BLOCKS LAUNCH)

Check the file types the tasks will produce (the plan's `related_files` and any
expected build/output artifacts) against the repo's ignore rules with
`git check-ignore`. An **intended output that is git-ignored** — the "gitignored
output" overnight failure, where the work runs but the artifact never commits —
**BLOCKS LAUNCH** with the offending path + pattern.

### Step 5 — Record verify tooling + exercise path

Resolve the project's named check command (`dli check` → `just check` →
`scripts/check.*`, the same precedence the repo's check tooling uses) and the
end-to-end **exercise path** (how a task's feature is driven, not just its tests
— the work's definition of done). Write both into `RUN.md`'s `verify_command` and
`exercise_path` front-matter fields (format per
[`references/run-state.md`](references/run-state.md) "`RUN.md`"), so every task's
`/deliver-task` verifies the same way.

### Step 6 — Materialize the task graph into run state

Run the adapter's `list_ready` and `dependency_graph`
([`references/adapters.md`](references/adapters.md)) to build the run's task graph
and its blocker edges. Write `.auto-pilot/RUN.md` — front matter (`run_id`,
`work_source`, `base_branch`, `verify_command`/`exercise_path` from step 5) plus
the per-task table with each task's initial **phase** and its `base` edge (main
for an independent task, the parent's branch for a chained one) — in the exact
format defined in [`references/run-state.md`](references/run-state.md) "`RUN.md`".
Also seed empty `.auto-pilot/QUESTIONS.md` and `.auto-pilot/MORNING.md`. **Commit**
all three to the run-state branch (the first write under the run-state branch's
fixed write order). Do **not** restate the run-state formats here — they live in
that reference.

### Step 7 — Spawn the detached orchestrator

Per [`references/launch-runtime.md`](references/launch-runtime.md):

1. Write the self-contained **launch script** — env, the sandbox wrapper (the
   two-layer profile: seatbelt/bwrap for filesystem+process, the harness network
   allowlist narrowed to this run's tools for host egress), and log redirection
   to `.auto-pilot/orchestrator.log`.
2. Run the **auth smoke test through that exact sandbox wrapper + env** (not
   bare) — a failure here is a launch blocker (ties to step 2).
3. Confirm the machine will stay awake for the run (lid-open / tested clamshell);
   if it can't be guaranteed, **block** (see the reference's "Laptop sleep").
4. **Detach** via the OS-appropriate primitive (`launchd`/`launchctl` on macOS,
   `setsid` on Linux) so the orchestrator outlives this session; record its
   **PID + `--until` deadline** on the run-state branch for later stale-run
   detection.
5. Print **where state lives** — the run-state branch name, the `.auto-pilot/`
   files, and the log path — and tell the user the run is going and to go to bed.
