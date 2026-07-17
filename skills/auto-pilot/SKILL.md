---
name: auto-pilot
description: Unattended autonomous mode — "pick up this Project and grind on it overnight." Runs a task graph (a Linear project or a plan-with-docs directory) task-by-task in an isolated worktree, taking each through /deliver-task (claim → implement → PR → co-review → hand-off) with durable, crash-resumable state and no human in the loop. Use when the user wants a body of work advanced autonomously and unattended. Launch, run, and resume all ship; tasks hand off at needs_review — nothing is merged or tracker-completed unattended.
---

# auto-pilot — unattended autonomous runs

"Claude, pick up this Project and grind on it overnight."

Auto-pilot advances a whole task graph without a human in the loop: an isolated
worktree, a thin orchestrator that walks the graph, and `/deliver-task` per task
(claim → implement → PR → co-review → iterate → hand-off). It composes existing,
battle-tested skills and handler protocols rather than duplicating them.

Design: [`../../dev_docs/auto-pilot.md`](../../dev_docs/auto-pilot.md).

> **Status:** v1 has shipped. This SKILL.md establishes the skill home, the
> references below, the interactive **launch** phase, the unattended **run**
> loop (which the spawned orchestrator executes), and **`--resume`**'s crash
> reconciliation. The three phases compose end-to-end: launch spawns an
> orchestrator that runs the loop, and a crashed or paused run resumes into
> that same loop. Its boundaries remain fixed: sources are a Linear project or
> a `plan-with-docs` directory, and every task hands off at `needs_review` —
> nothing is merged or tracker-completed unattended.

## References

- [`references/run-state.md`](references/run-state.md) — the canonical formats
  and invariants for a run's durable state: the three run files
  (`RUN.md` / `QUESTIONS.md` / `REPORT.md`), the task lifecycle phases,
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
- [`references/run-budget.md`](references/run-budget.md) — the run's resource
  bounds: rate-window checks (proxy + rate-limit-error backstop), the
  near-cap checkpoint-then-exit-then-relaunch pause, the hard-stop before
  paid/overflow credits, per-task wall-clock and retry limits, the paid-agent
  dispatch cap, and the run-level circuit breaker. The run loop's budget
  check reads this. It also owns **the alarm**: a halted or stalled run
  actively notifies a human (OS notification + an `ALARM` sentinel + the top
  line of `REPORT.md`), from the un-jailed supervisor, in shell — silence, not
  the failure, was the expensive half of finding #22.
- [`references/resume.md`](references/resume.md) — the `--resume` reconciliation
  procedure: which pre-flight is re-run vs skipped, locating the one resumable
  run-state branch, the stale-orchestrator guard, and the per-task reconciliation
  against run-state.md's crash-reconciliation table before falling into the run
  loop. The Resume phase summarizes this; the mechanics live here.

## Launch phase (interactive)

Invoked by `/auto-pilot <linear-project | plan-dir> [--until <time>]
[--reserve <pct>] [--profile less-claude] [--resume]`
(`commands/auto-pilot.md`). Launch runs **interactively, tonight, while the human
can still fix failures** — so it is **fail-closed**: any hard pre-flight failure
**BLOCKS LAUNCH** with a specific, fixable message rather than deferring the
problem to 3am. It ends by spawning the detached, unattended orchestrator and
telling the user the run is underway. The unattended **run** loop is what the
spawned orchestrator executes; **`--resume`** reconciles a crashed or paused
run's state and then falls into that same loop (see "Resume phase" below).

**Preamble — parse + resolve.** Parse `<source>`, `--until`, `--reserve`,
`--profile less-claude`, `--resume`. `--profile` is optional and only accepts
`less-claude`; without it, preserve the existing launch behavior exactly. On a
fresh less-claude launch, resolve and persist the profile settings in step 3.
On `--resume`, read those settings from the selected `RUN.md` rather than
re-parsing a profile choice. `--reserve <pct>` is the run's fixed rate-window headroom floor;
default it to `15`, require a numeric percentage from 0 through 100, and
persist the resolved value in `RUN.md`. On resume, use that persisted value
unless an explicit, validated `--reserve` replaces it. `--until` accepts an
absolute ISO-8601 time or a relative `now+<duration>`
offset; launch resolves either to the absolute time recorded in `RUN.md`. If
`--resume` is present, route to the **Resume phase** section below instead of
running the rest of this launch pre-flight. Detect the source (existing
`dev_docs/tasks/<name>_plan/` dir → **plan**; else → **linear** project) and
resolve `dev_docs/tasks/.task-config.yml`'s handler for validation only; any
handler other than `linear`/`repo-pr` → stop (v1 supports linear + plan
only). The run's **effective handler** — what actually gets passed to
`/deliver-task` — is source-derived, not read back off that config: a
**plan** source ⇒ `repo-pr`, a **linear** source ⇒ `linear`, per the
matching adapter (`references/adapters.md`) — this keeps a plan-source run
correct even when the repo's own `.task-config.yml` default is `linear`.

**Size `--until` realistically.** A single `/deliver-task` floors well above a
naive "a few minutes" guess once the resolved reviewer set includes a cloud
reviewer (step 3 computes the exact number as `min_task_budget`; the formula
is [`references/run-budget.md`](references/run-budget.md) "Minimum task
budget"). An under-provisioned `--until` isn't fatal: the pre-dispatch guard
(Run phase) just declines to start the next task, so a too-tight window
yields fewer tasks, not a hard-killed one. Provision at least one
`min_task_budget` per task you expect to finish.

The pre-flight is an **ordered, fail-closed** sequence, steps 1–7 below. It is
**supply-and-demand**: steps 2–3 probe what the configured environment can
_supply_ (auth, resolved config), and the **scout** in step 6 checks what the
_plan_ will _demand_ (which coder each task routes to) against that supply — the
join is where a run that would otherwise pass green but die at 3am gets caught
tonight.

### Step 1 — Worktree + run-state branch (BLOCKS LAUNCH)

Create the dedicated run **worktree** and the **run-state branch**
`auto-pilot/<run_id>` (branch convention:
[`references/run-state.md`](references/run-state.md) "Run-state branch").
Confirm the plan / task instructions are **committed and present in the
worktree** — an **untracked plan is a launch blocker**, since a plan that
lives only in an uncommitted file never reaches the detached orchestrator. If
the source is a plan directory, the plan files must be committed on the
branch the run builds from; if Linear, the project must resolve and be
reachable. Fail here with the specific missing artifact, not a generic error.

### Step 2 — Non-interactive auth probes (BLOCKS LAUNCH)

Run `scripts/preflight.sh --source <plan|linear> --base <base branch>` first
— the read-only pre-flight helper this step extracts to. Its `PREFLIGHT …`
output and `PREFLIGHT VERDICT: go` / `no-go — <reason>` line cover the binary
fingerprint / environment class, coder availability, base freshness, the
resolved PATH/exec dirs, the add-task host, and the confinement smoke. A
`no-go` **BLOCKS LAUNCH** with the reason it names; treat its output as the
source of truth rather than re-deriving these facts by hand.

Probe every credential the run will need, each **non-interactively** — a probe
that would open a prompt (a browser OAuth, a biometric `op signin`) is itself the
failure (see [`references/launch-runtime.md`](references/launch-runtime.md) §3).
The probe path depends on the **environment class** (below): `local-full`
authenticates through CLIs, `claude-web` through **MCP**. Probe whichever applies:

- **GitHub** — `local-full`: `gh auth status` (PRs + git push). `claude-web`:
  confirm the **GitHub MCP** is connected (no `gh` CLI exists there).
- **Linear** (linear source) — `local-full`: resolve the API key from its
  `op://` reference (`api_key_ref` in `commands/handlers/linear-common.md`) via
  `op read`. `claude-web`: use the **Linear MCP** connection (no `op`/CLI).
  Either way, run `linear-common.md`'s shared **preflight** (`list_teams` → match
  the team) to confirm auth actually works, not just that it resolves.
- **Coder CLIs** (`local-full` only — a `claude-web` run has none) — run each
  configured coder's auth probe via `scripts/probe-coders.sh`, the **single
  source of truth** (don't restate its per-coder commands here — they'd drift).
  A logged-out coder the run depends on is a blocker, not a silent skip.
- **MCP** — any MCP the tasks touch: one cheap read call to confirm a live token.

Each probe runs **through the sandbox wrapper** the orchestrator will use (per
§"Step 7"), so a probe can't pass outside the jail while failing inside it. Any
interactive-only or failing auth **BLOCKS LAUNCH**, naming the dependency.

While probing, capture the **environment fingerprint** — which coder/tool
binaries exist on `PATH` (`codex`, `devin`, `agy`, `op`, `gh`) and the resulting
**environment class** (`local-full` = CLIs on `PATH`; `claude-web` = cloud/web,
no local CLIs, narrower permission surface). **Detect** the facts (a
`command -v` probe can't lie) rather than trusting a declared class. Record it
on the run-state branch — the step-6 scout joins against it, and `--resume` in
a different environment re-runs that join.

Also confirm **unattended viability** here, up front while the human is
present rather than at spawn: a `local-full` run needs the machine to stay
awake for the run's duration — a human judgment call, not a probe, so it
stays here rather than in `scripts/preflight.sh`
([`references/launch-runtime.md`](references/launch-runtime.md) "Laptop
sleep"). If it can't be guaranteed, **BLOCKS LAUNCH**.

**Less-claude CAO gate (BLOCKS LAUNCH).** Only when `--profile less-claude` is
present, require `cao`, `cao-run`, and `cao-server` on `PATH`, then prove the
already-running daemon responds at `localhost:9889` with `nc -z localhost
9889`. Any missing binary or failed port probe **BLOCKS LAUNCH**; name the
missing prerequisite and do not start or restart `cao-server`.

### Step 3 — Resolve config into non-interactive choices (BLOCKS LAUNCH)

Collapse every config decision the unattended run could hit into a fixed choice,
so nothing prompts at 3am:

- **Co-review reviewer set** — resolve `/co-review`'s reviewer set from
  `.co-review.yml` into the concrete list that will run under `--non-interactive`
  (bounded per-reviewer timeouts; the reviewer prompt is never asked mid-run).
  For a **time-boxed run** (`--until` set), default to a **fast reviewer set**
  (codex + Claude + reconciler) over one that includes the slower cloud
  reviewers (`devin` / `agy`); cloud reviewers are **optional/skippable** in
  this set — a skipped reviewer is never fatal and is recorded (`REPORT.md`
  review classes). Then **compute `min_task_budget` from this resolved
  set** — the pre-dispatch floor is coupled to reviewer latency, not a
  constant; formula in
  [`references/run-budget.md`](references/run-budget.md) "Minimum task
  budget" — and write it to `RUN.md` front matter (step 6) so the run loop's
  pre-dispatch deadline guard reads a concrete number.
- **Coder config** — run `select-coder` once to resolve each task's
  `<backend>:<model>` from the capability matrix, so `orchestrate-coders`
  dispatches without prompting for a missing default.
- **Less-claude profile** — only for `--profile less-claude`, resolve every
  task through `select-coder --cao-fleet`, which admits only `codex` / `agy`,
  and record its matching named CAO dispatch mapping (`codex` → `cao-codex`,
  `agy` → `cao-agy`). Set `orchestrate-coders`' `default_coder` to a CAO named
  entry, never a generic `cao` placeholder. Set `co_review_mode: off` unless
  the user selects the `cheap-single` dial during this launch, and set
  `diff_judgment_tier: sonnet`.
- **Custom/local commands** are **disabled** for the run unless explicitly
  approved at this step (untrusted-config posture, matching co-review's rule).

Any decision that can't be resolved here — and would therefore prompt mid-run —
**BLOCKS LAUNCH**.

### Step 4 — Gitignore sanity check

Check the file types the tasks will produce (the plan's `related_files` and
any expected build/output artifacts) against the repo's ignore rules with
`git check-ignore`. A match does **not** block launch, but **record the matched
paths in run state** so the run phase commits them with `git add -f` and the work
lands despite the ignore rule. Surface the list in the launch summary too, so the
human can still catch a genuinely wrong ignore while awake.

### Step 5 — Record verify tooling + exercise path

Resolve the project's named check command (`dli check` → `just check` →
`scripts/check.*`, the same precedence the repo's check tooling uses) and the
end-to-end **exercise path** (how a task's feature is driven, not just its
tests). Write both into `RUN.md`'s `verify_command` and `exercise_path`
front-matter fields (format per
[`references/run-state.md`](references/run-state.md) "`RUN.md`"), so every
task's `/deliver-task` verifies the same way.

Because the orchestrator runs **jailed** and `verify_command` can't pass
inside the jail (execve-deny, exit 126), install the **verify broker** here
so verify runs **outside** the jail: `scripts/spawn-orchestrator.sh
write-verify-broker` registers a second, un-jailed launchd job that runs the
**pinned** `verify_command` in a run-root-confined worktree, and each task's
verify becomes a `verify-request` → `verify-await` handshake
([`references/launch-runtime.md`](references/launch-runtime.md) §5). Tear
the broker job down alongside the orchestrator supervisor at loop
termination.

### Step 6 — Materialize the task graph into run state

Run the adapter's `list_ready` and `dependency_graph`
([`references/adapters.md`](references/adapters.md)) to build the run's task
graph and its blocker edges. Write `.auto-pilot/RUN.md` — front matter
(`run_id`, `work_source`, `base_branch`, `verify_command`/`exercise_path` from
step 5, `min_task_budget` from step 3) plus the per-task table, in the exact
format defined in
[`references/run-state.md`](references/run-state.md) "`RUN.md`" — do **not**
restate that format here. A less-claude launch writes its profile fields; an
ordinary launch omits them and uses their documented defaults, preserving its
existing `RUN.md` bytes. Also seed empty `.auto-pilot/QUESTIONS.md` and
`.auto-pilot/REPORT.md`. **Commit** all three to the run-state branch (the
first write under the run-state branch's fixed write order).

**Scout — per-task capability join (BLOCKS LAUNCH).** With the graph now
materialized and each task's coder resolved (step 3) against the environment
fingerprint (step 2), check the **demand** side the auth probes structurally
can't see: for **each** task, take the `<backend>` it routes to and confirm
it **exists in this environment** — the motivating case is a `codex` task in
a `claude-web` run with no `codex` binary. A task routed to an absent backend
**BLOCKS LAUNCH**, naming the task, the missing backend, and the fix (install
it, or re-route the task). This is a **deterministic** check only: it blocks
on a provable route-vs-environment gap, never a guess about what a task's
prose might need — inferring demands from task _text_ is a separate,
warn-only predictive scout, deliberately not here.

**v1 treats every route as _required_** — an absent backend blocks. A planned
follow-up softens this to **required-vs-preferred** (a _preferred_ backend
that's absent warns and falls back to the next-ranked `select-coder` spec
instead of blocking): the fallback order already exists, the missing piece is
the require/prefer bit on the task route.

### Step 7 — Spawn the detached orchestrator

Per [`references/launch-runtime.md`](references/launch-runtime.md):

1. Write the self-contained **launch script** — env, the sandbox wrapper (the
   two-layer profile: seatbelt/bwrap for filesystem+process, the harness network
   allowlist narrowed to this run's tools for host egress), and log redirection
   to `.auto-pilot/orchestrator.log`. The egress allowlist also gets the
   pre-flight's resolved `/add-task` destination host via `render-settings
   --add-task-host`, so the run's own settings never deny its own follow-up
   filing regardless of work source.
2. Run the **auth smoke test through that exact sandbox wrapper + env** (not
   bare) — a failure here is a launch blocker (ties to step 2). Machine-stays-
   awake was already confirmed in the pre-flight (step 2).
3. **Detach** via the OS-appropriate primitive (`launchd`/`launchctl` on macOS,
   `setsid` on Linux) so the orchestrator outlives this session; record its
   **PID + process start-time + `--until` deadline** on the run-state branch for
   later stale-run detection (the start-time guards against a recycled PID being
   mistaken for a live run).
4. Print **where state lives** — the run-state branch name, the `.auto-pilot/`
   files, and the log path — and tell the user the run is going.

## Resume phase (--resume)

Invoked by `/auto-pilot <source> --resume` (`commands/auto-pilot.md`). Resume
reconciles a crashed or paused run's durable state against reality, then falls
into the normal **Run phase** loop below for whatever remains ready. The full
procedure is in [`references/resume.md`](references/resume.md); in short:

- **Re-run only the pre-flight that can rot** — the auth probes, the environment
  fingerprint, the scout's capability join, and base freshness (a run launched
  `local-full` may resume under `claude-web`). Worktree + run-state-branch
  creation and task-graph materialization already exist on the branch and are
  not re-created; source normalization still runs. A hard failure **BLOCKS THE
  RESUME**, fail-closed.
- **Locate exactly one resumable run-state branch** for the normalized source
  (zero or many is fail-closed), and **guard against a live orchestrator** at the
  recorded PID + start-time — **before the doctor**, which _repairs_ and so must
  never run against a run someone else is still driving.
- **Clear the run's alarms** (`spawn-orchestrator.sh alarm-clear --dir
  <run-dir>`) — after that guard, **before the doctor**, so clearing never
  deletes an `alarm-request` a doctor halt is about to file
  ([`references/resume.md`](references/resume.md)).
- **Clear the terminal exit state too** (`spawn-orchestrator.sh
  clear-exit-state --dir <run-worktree>`) — the done-sentinel and
  `exit_reason*` are durable and would otherwise make a resumed live run read
  back as finished ([`references/resume.md`](references/resume.md)).
- **Then run the doctor.** A **HALT** (exit 30) stops here.
- **Reconcile each non-terminal task** by observing reality in the write order's
  direction (git → tracker → run files) and matching it to `run-state.md`'s
  crash-reconciliation table (rows G1–G7) — idempotent (adopts an existing PR,
  never duplicates), removes orphaned worker worktrees, and **parks** anything
  that matches no row cleanly rather than blind-retrying.
- **Then fall into the run loop**, clearing any run-level pause markers first
  ([`references/run-budget.md`](references/run-budget.md) owns pause semantics).

## Run phase (unattended)

This is what the detached orchestrator spawned by launch (step 7) executes,
alone, with no human watching. It never writes code itself — every code change
happens inside a `/deliver-task` call — and it reads and writes only the
run-state formats defined in
[`references/run-state.md`](references/run-state.md). It advances a task as
far as `handed-off` and stops there; it never merges a PR or completes a
tracker item (that is `/sweep-for-complete`'s job, later, with a human's PR
approval in between).

The loop is deliberately thin:

```
while unblocked tasks remain and inside budget bounds:
    heartbeat (and again at every /deliver-task sub-step boundary)
    doctor   # run invariant audit; repair/park/halt — HALT (exit 30) stops here
    pick next unblocked task (phase-based readiness)
    if until is set and now + min_task_budget > until:   # pre-dispatch deadline guard
        stop the loop cleanly (record "N left, M min to deadline, not starting")
    /deliver-task it --run-state .auto-pilot/RUN.md (with per-task wall-clock + retry bounds)
    update run state on the run-state branch
    check rate-window usage
declare the exit reason, then exit          # every termination path, no exceptions
```

**The exit contract.** Before exiting, for any reason, run
`scripts/spawn-orchestrator.sh exit-reason --dir <run worktree> --reason <r>`
(`continuing` | `paused` | `done` | `systemic` | `deadline`), and beat the
heartbeat (`spawn-orchestrator.sh heartbeat --dir <run worktree> --note
<where>`) at each loop iteration and `/deliver-task` sub-step boundary. Full
semantics, precedence, and fail-safe rules:
[`references/run-state.md`](references/run-state.md) "Exit contract" and
"Heartbeat".

**Every iteration opens with the run doctor**, `scripts/spawn-orchestrator.sh
doctor` — a cheap, deterministic, no-model-call audit of seven invariants
(HALT on `status: systemic`, exit 30 — the loop must not dispatch). The
invariant table, each one's repair, the exit codes, and the finding-#22/#23
rationale: [`references/run-state.md`](references/run-state.md) "Run doctor".

**Pre-dispatch deadline guard.** Before claiming each task, when `--until` is
set, if `now + min_task_budget > until`, stop the loop cleanly and record why
in `REPORT.md` (e.g. "2 tasks left, 12 min to deadline, not starting — resume
tomorrow") — a clean stop, not a park; the un-started tasks stay ready for a
later `--resume`. `min_task_budget` is the reviewer-set-coupled floor resolved
at launch (step 3) and read from `RUN.md` front matter, never a constant; why
this guard exists and the floor's derivation:
[`references/run-budget.md`](references/run-budget.md) "Minimum task budget".

**Pre-invoke reserve gate.** The outer loop dispatches each delivery with its
`RUN.md` path; `/deliver-task`, as lifecycle owner, applies
[`references/run-budget.md`](references/run-budget.md) "Pre-invoke reserve"
at its internal claim, verify, co-review, re-verify, and repeated co-review
boundaries. The loop does not attempt to intercept those opaque substeps.

**Readiness + ordering.** Walk the `RUN.md` task graph
([`references/run-state.md`](references/run-state.md) "`RUN.md`"). A task is
**ready** when every task it is blocked by is at phase `handed-off` — never
tracker done-state (per that reference's phase table, `handed-off` is the
success terminal the run keys off, not `needs_review`'s eventual completion).
Pick the next ready task in dependency order. Each task's `base` column
encodes whether it is independent (`main`) or chained (the parent task's
branch) — that distinction drives the stacked-PR handling below.

**The per-task step.** Dispatch exactly one call per task:

```
/deliver-task <id> --base <branch> --handler <handler> --questions .auto-pilot/QUESTIONS.md --run-state .auto-pilot/RUN.md
```

where `<branch>` is the task's `base` from `RUN.md` and `<handler>` is the
run's effective handler resolved from the run's (normalized) source (plan ⇒
`repo-pr`, linear ⇒ `linear`), never re-derived by `/deliver-task` itself.
`/deliver-task` ([`commands/deliver-task.md`](../../commands/deliver-task.md))
owns the entire per-task lifecycle — claim, implement, PR, co-review, iterate,
hand-off — including the internal reserve boundaries enabled by `--run-state`;
the outer loop does not intercept those opaque substeps. The `--questions` path
is where the non-blocking decision protocol below appends entries. A
`/deliver-task` call that **fails outright** gets the per-task retry bound
(one re-dispatch, then **park**;
[`references/run-budget.md`](references/run-budget.md) "Per-task retry
limit") and the loop continues to the next ready task.

**Non-blocking decisions.** The run **never waits** on a human. For any
reversible call, the orchestrator (or `/deliver-task` beneath it) picks the
**reversible option** when uncertain, appends an indexed `QUESTIONS.md` entry,
and proceeds — format and the two feeding sources (deliver-task's own deferred
calls plus orchestrator-level ones like skip vs park):
[`references/run-state.md`](references/run-state.md) "`QUESTIONS.md`".

**Deferred co-review findings that are cross-cutting or round-bound also get a
task.** When `/deliver-task`'s iterate step defers a finding that is
**cross-cutting** or **still open at the hard 2-round bound**, it files a
tracked follow-up via `/add-task` (tagged `auto-pilot`) in addition to the
`QUESTIONS.md` entry. Dedupe/cap rules and the `REPORT.md` **Follow-ups**
index: [`references/run-state.md`](references/run-state.md) "`REPORT.md`".

**Human checkpoints produce artifacts, then proceed.** When a task needs
genuine human judgment, the run still does not block: `/deliver-task` ensures
the PR carries a working end-to-end state plus a how-to-evaluate note, the
orchestrator records the same entry in `REPORT.md`'s _How-to-evaluate queue_,
and the loop moves on.

**Rolling `REPORT.md` update.** After every task's state update above, rewrite
`REPORT.md` from the current `RUN.md` + `QUESTIONS.md` state — the seven
sections in [`references/run-state.md`](references/run-state.md)'s "`REPORT.md`"
order. Commit it on the run-state branch as part of that state update's commit,
under the write order's last step. Its _Spend_ section stays a one-line
pointer to [`references/run-budget.md`](references/run-budget.md) rather than
restating budget rules, which that reference owns.

**Stacked-PR handling.** Chains are processed in dependency order, from the
**parent's frozen tip** (the readiness rule guarantees it is stable — the
parent is already `handed-off` before a child becomes ready). Before
dispatching a chained task, the orchestrator compares the parent branch's
**current tip SHA** against the child's recorded `base_sha`
([`references/run-state.md`](references/run-state.md) "`RUN.md`"); if they
differ, the parent has moved since the base was frozen — **park** the task
instead, record it, and continue to the next ready task. This guard models
the **orchestrator** moving a base mid-run and must never fire on a **human**
merging a parent PR, which is restack's normal trigger, not an error; the
mechanized remedy and what re-verification a restacked child still needs:
[`references/run-state.md`](references/run-state.md) "Restack (post-merge
stacked-PR repair)".

**State update after each task.** After `/deliver-task` returns, update
`RUN.md` with the task's observed `phase`, `branch`, and `pr`, then commit to
the run-state branch — the **write order**'s last step
([`references/run-state.md`](references/run-state.md) "Write order");
`/deliver-task` already performed that order's push + tracker-write steps.
Then apply the budget checks in
[`references/run-budget.md`](references/run-budget.md): a hard-stop, a
near-cap pause, or a circuit-breaker halt writes state and exits per that
reference.

**Loop termination.** The loop ends when no ready task remains, a budget
hard-stop fires, or the pre-dispatch deadline guard above stops it with ready
tasks still left. The first two are a finished run (`status: done`); the
deadline-guard stop leaves the run `paused`, with the un-started tasks still
ready for a later `--resume` — the `status`/`paused_until` contract and what
that does to the relaunch supervisor:
[`references/run-state.md`](references/run-state.md) "`RUN.md`" and
[`references/launch-runtime.md`](references/launch-runtime.md) "Relaunchable,
not one-shot".

In every case the orchestrator writes and commits the final `REPORT.md`, then
declares its exit reason and exits cleanly, emitting a one-line summary to
`.auto-pilot/orchestrator.log` — e.g. `auto-pilot done: 4 handed-off, 1
parked, 0 skipped — see REPORT.md`
([`references/launch-runtime.md`](references/launch-runtime.md) "Logs /
observability"). Nothing is merged or tracker-completed by this exit: every
task sits at whatever phase it reached (`handed-off` on the tracker as
`needs_review`, or `started` for a `parked` task), ready for
`/sweep-for-complete` once a human has reviewed and merged.

For a **plan** source, the exit also does **not** delete the plan's
`<name>_plan/` scaffolding — that graduate-then-delete cleanup is a **run-level
teardown** and ultimately a human follow-up, never a graph task the loop
dispatches (see [`references/adapters.md`](references/adapters.md) "plan adapter").
