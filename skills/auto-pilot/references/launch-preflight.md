# Launch pre-flight — Steps 1–7

The interactive launch phase (SKILL.md "Launch phase") runs an **ordered,
fail-closed** pre-flight before spawning the detached orchestrator. This
reference holds the full mechanics of each step; SKILL.md carries the phase
intro (invocation, preamble parse+resolve, `--until` sizing) and a one-line
summary of the seven steps that points here.

The sequence is **supply-and-demand**: steps 2–3 probe what the configured
environment can _supply_ (auth, resolved config), and the **scout** in step 6
checks what the _plan_ will _demand_ (which coder each task routes to) against
that supply — the join is where a run that would otherwise pass green but die
at 3am gets caught tonight.

## Step 1 — Worktree + run-state branch (BLOCKS LAUNCH)

Create the dedicated run **worktree** and the **run-state branch**
`auto-pilot/<run_id>` (branch convention:
[`run-state.md`](run-state.md) "Run-state branch").
Confirm the plan / task instructions are **committed and present in the
worktree** — an **untracked plan is a launch blocker**, since a plan that
lives only in an uncommitted file never reaches the detached orchestrator. If
the source is a plan directory, the plan files must be committed on the
branch the run builds from; if Linear, the project must resolve and be
reachable. Fail here with the specific missing artifact, not a generic error.

## Step 2 — Non-interactive auth probes (BLOCKS LAUNCH)

Run `"${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh" --source <plan|linear> --base <base branch>` first
— the read-only pre-flight helper this step extracts to. Its `PREFLIGHT …`
output and `PREFLIGHT VERDICT: go` / `no-go — <reason>` line cover the binary
fingerprint / environment class, coder availability, base freshness, the
resolved PATH/exec dirs, the add-task host, and the confinement smoke. A
`no-go` **BLOCKS LAUNCH** with the reason it names; treat its output as the
source of truth rather than re-deriving these facts by hand.

Probe every credential the run will need, each **non-interactively** — a probe
that would open a prompt (a browser OAuth, a biometric `op signin`) is itself the
failure (see [`launch-runtime.md`](launch-runtime.md) §3).
The probe path depends on the **environment class** (below): `local-full`
authenticates through CLIs, `claude-web` through **MCP**. Probe whichever applies:

- **GitHub** — `local-full`: `gh auth status` (PRs + git push). `claude-web`:
  confirm the **GitHub MCP** is connected (no `gh` CLI exists there).
- **Linear** (linear source) — `local-full`: confirm the API key resolves with
  `python3 commands/handlers/assets/_secret_resolve.py --probe LINEAR_API_KEY`,
  which honors the configured resolver and never prints the key (contract:
  `dev_docs/auth_key_access.md`). An auto-pilot run is **unattended**, so the
  resolver must be one that works without a UI — `op` with an authorized session
  or `$OP_SERVICE_ACCOUNT_TOKEN`, or `$LINEAR_API_KEY` injected directly. An
  approval-based resolver such as `opx` fails closed here **by design**; if the
  probe reports `timeout` or `denied`, that is the cause, and the fix is to set
  `$LINEAR_API_KEY_RESOLVER=op` (or inject the key) for the run rather than to
  wait on a dialog no one will answer. `claude-web`: use the **Linear MCP**
  connection (no `op`/CLI).
  Either way, run `linear-common.md`'s shared **preflight** (`list_teams` → match
  the team) to confirm auth actually works, not just that it resolves.
- **Coder CLIs** (`local-full` only — a `claude-web` run has none) — run each
  configured coder's auth probe via `"${CLAUDE_PLUGIN_ROOT}/scripts/probe-coders.sh"`, the **single
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
stays here rather than in `"${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh"`
([`launch-runtime.md`](launch-runtime.md) "Laptop
sleep"). If it can't be guaranteed, **BLOCKS LAUNCH**.

**Less-claude CAO gate (BLOCKS LAUNCH).** Only when `--profile less-claude` is
present, require `cao`, `cao-run`, and `cao-server` on `PATH`, then prove the
already-running daemon responds at `localhost:9889` with `nc -z localhost
9889`. Any missing binary or failed port probe **BLOCKS LAUNCH**; name the
missing prerequisite and do not start or restart `cao-server`.

## Step 3 — Resolve config into non-interactive choices (BLOCKS LAUNCH)

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
  [`run-budget.md`](run-budget.md) "Minimum task
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

## Step 4 — Gitignore sanity check

Check the file types the tasks will produce (the plan's `related_files` and
any expected build/output artifacts) against the repo's ignore rules with
`git check-ignore`. A match does **not** block launch, but **record the matched
paths in run state** so the run phase commits them with `git add -f` and the work
lands despite the ignore rule. Surface the list in the launch summary too, so the
human can still catch a genuinely wrong ignore while awake.

## Step 5 — Record verify tooling + exercise path

Resolve the project's named check command (`dli check` → `just check` →
`scripts/check.*`, the same precedence the repo's check tooling uses) and the
end-to-end **exercise path** (how a task's feature is driven, not just its
tests). Write both into `RUN.md`'s `verify_command` and `exercise_path`
front-matter fields (format per
[`run-state.md`](run-state.md) "`RUN.md`"), so every
task's `/deliver-task` verifies the same way.

Because the orchestrator runs **jailed** and `verify_command` can't pass
inside the jail (execve-deny, exit 126), install the **verify broker** here
so verify runs **outside** the jail: `"${CLAUDE_PLUGIN_ROOT}/scripts/spawn-orchestrator.sh"
write-verify-broker` registers a second, un-jailed launchd job that runs the
**pinned** `verify_command` in a run-root-confined worktree, and each task's
verify becomes a `verify-request` → `verify-await` handshake
([`launch-runtime.md`](launch-runtime.md) §5). Tear
the broker job down alongside the orchestrator supervisor at loop
termination.

## Step 6 — Materialize the task graph into run state

Run the adapter's `list_ready` and `dependency_graph`
([`adapters.md`](adapters.md)) to build the run's task
graph and its blocker edges. Write `.auto-pilot/RUN.md` — front matter
(`run_id`, `work_source`, `base_branch`, `verify_command`/`exercise_path` from
step 5, `min_task_budget` from step 3) plus the per-task table, in the exact
format defined in
[`run-state.md`](run-state.md) "`RUN.md`" — do **not**
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

## Step 7 — Spawn the detached orchestrator

Per [`launch-runtime.md`](launch-runtime.md):

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
