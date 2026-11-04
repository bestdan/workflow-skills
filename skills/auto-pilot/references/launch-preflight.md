# Launch pre-flight — Steps 1–7

The interactive launch phase (SKILL.md "Launch phase") runs an **ordered,
fail-closed** pre-flight before spawning the detached orchestrator. This
reference holds the full mechanics of each step; SKILL.md carries the phase
intro (invocation, preamble parse+resolve, `--until` sizing) and a one-line
summary of the seven steps that points here.

The sequence is **supply-and-demand**: steps 2–3 probe what the configured
environment can _supply_ (auth, resolved config), and the **scout** in step 6
checks what the _plan_ will _demand_ (which coder each task routes to) against
that supply — the join is where a run that would otherwise pass green but fail
with no human attached gets caught at launch.

## Step 1 — Worktree + run-state branch (BLOCKS LAUNCH)

Create the dedicated run **worktree** and the **run-state branch**
`auto-pilot/<run_id>` (branch convention:
[`run-state.md`](run-state.md) "Run-state branch") with one call, on both OSes:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/spawn-orchestrator.sh" run-clone --repo <main checkout> \
  --run-root <run root> --run-id <run_id> --base <base branch>
```

It makes the run's own git repo, a bare clone, inside `<run root>`, which must
sit outside every checkout. It then adds the run worktree from that clone and
prints both paths (`RUN_WORKTREE <path>`). The jail writes that repo and never
the main checkout's `.git`; the layout, and how to recover a run's branch from
the main checkout, are in [`launch-runtime.md`](launch-runtime.md) §1 "The
run's own git repo". Only `<base branch>` is cloned, so the plan must be
committed there, not on another local branch.
Confirm the plan / task instructions are **committed and present in the
worktree** — an **untracked plan is a launch blocker**, since a plan that
lives only in an uncommitted file never reaches the detached orchestrator. If
the source is a plan directory, the plan files must be committed on the
branch the run builds from; if Linear, the project must resolve and be
reachable; if gh-issue, the milestone must resolve to one open milestone on
`gh-issue.repo` (a title to its number), or every queried label must exist
there, matched exactly against
`gh label list --repo <repo> --limit 1000 --json name --jq '.[].name'` (the
default page is 30 labels, which a provisioned repo can exceed). Fail here with the specific missing artifact, not a generic error.

## Step 2 — Non-interactive auth probes (BLOCKS LAUNCH)

Run `"${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh" --source <plan|linear|gh-issue> --base <base branch> --run-root <the step-1 worktree>` first
— the read-only pre-flight helper this step extracts to. Its `PREFLIGHT …`
output and `PREFLIGHT VERDICT: go` / `no-go — <reason>` line cover the binary
fingerprint / environment class, coder availability, base freshness, the
resolved PATH/exec dirs, the add-task host, the launcher, the confinement
smoke, and the consent-gate probe (below). A host with no supported launcher is
a `no-go`: the smoke and consent probes there log skips, but a skipped probe is
not a launch that can happen. On Linux the launcher check is the pinned nono
(`scripts/nono-install.sh check`: checksum, version, Landlock ABI 4+), and the
smoke is `scripts/smoke-nono.sh`, one `PREFLIGHT SMOKE_<CHECK>` line per
assertion. Pass the token command the launch will use as `--claude-token-cmd`,
so the smoke proves the credential file stays outside the jail. The Linux
launcher also needs `systemd-run`, a reachable systemd user manager, and
`Linger=yes`, and a run worktree made by `run-clone` (step 1;
`PREFLIGHT RUN_CLONE`), because the jail writes only the run's own git repo. A
`no-go` **BLOCKS LAUNCH** with the reason it names; treat its output as the
source of truth rather than re-deriving these facts by hand.

Probe every credential the run will need, each **non-interactively** — a probe
that would open a prompt (a browser OAuth, a biometric `op signin`) is itself the
failure (see [`launch-runtime.md`](launch-runtime.md) §3).
The same pre-flight call carries the **consent-gate probe**, which enforces that
rule for the gate class no credential probe can see: it re-runs the entry path
under **launchd attribution** (not this terminal's) and blocks on TCC folder
access or Full Disk Access against the run's own paths. It covers **filesystem**
consent only — Keychain, biometric and browser OAuth remain covered by the
credential probes in this step, which run under the terminal's attribution
([`launch-runtime.md`](launch-runtime.md) §3, "What is enforced, and what is
not"). **`--run-root <path>` is required** — pass the run worktree from step 1.
The probe tests that checkout and its `--git-common-dir`, and the flag has no
default on purpose: `$ROOT` is the installed plugin's directory, never the run's
tree, and a `$PWD` fallback would certify whatever checkout the caller happened
to stand in. Omitting it exits 2 with a usage message, the way omitting
`--source` does, rather than reporting a `go` for the wrong tree.
The probe path depends on the **environment class** (below): `local-full`
authenticates through CLIs, `claude-web` through **MCP**. Probe whichever applies:

- **GitHub** — `local-full`: `gh auth status` (PRs + git push). `claude-web`:
  confirm the **GitHub MCP** is connected (no `gh` CLI exists there).
- **gh-issue** (gh-issue source) — `local-full` only: the GitHub probe above
  (`gh auth status`) is the whole credential check, because every adapter verb is
  a `gh` call or a script that runs one, so there is no second token to resolve.
  `claude-web` has no `gh` CLI, so a gh-issue source there **BLOCKS LAUNCH**,
  naming the missing `gh`.
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

Record the consent probe's three lines alongside it — `ATTRIBUTION_BIN` (the
resolved binary, not the symlink), `RUN_ROOT` (the path actually probed, which
is what makes the verdict readable at all) and `CONSENT_PROTECTED`. Attribution
is an environment fact like the rest: a run launched from one machine and
resumed on another re-runs this step and re-checks it.

Also confirm **unattended viability** here, up front while the human is
present rather than at spawn: a `local-full` run needs the machine to stay
awake for the run's duration — a human judgment call, not a probe, so it
stays here rather than in `"${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh"`
([`launch-runtime.md`](launch-runtime.md) "Laptop
sleep"). If it can't be guaranteed, **BLOCKS LAUNCH**.

**Less-claude CAO gate (BLOCKS LAUNCH).** A `--profile less-claude` run routes
every task through the CAO fleet, so this gate is folded into the scout's one
call in Step 6 below rather than duplicated here — there is nothing to check
yet at this point, since the task graph (and each task's `coder`) doesn't
exist until Step 6 materializes it.

## Step 3 — Resolve config into non-interactive choices (BLOCKS LAUNCH)

Collapse every config decision the unattended run could hit into a fixed choice,
so nothing prompts when no human can answer:

- **Co-review reviewer set** — resolve `/co-review`'s reviewer set from
  `.co-review.yml` into the concrete list that will run under `--non-interactive`
  (bounded per-reviewer timeouts; the reviewer prompt is never asked mid-run).
  For a **time-boxed run** (`--until` set), default to a **fast reviewer set**
  (codex + Claude + reconciler) over one that includes the slower cloud
  reviewers (`devin` / `agy`); cloud reviewers are **optional/skippable** in
  this set — a skipped reviewer is never fatal and is recorded (`REPORT.md`
  review classes). Then run
  `"${CLAUDE_PLUGIN_ROOT}/scripts/min-task-budget.sh"` with the resolved
  `local_reviewers` names (pass nothing for a Claude-only set) — the script
  owns the formula; [`run-budget.md`](run-budget.md) "Minimum task budget"
  carries the reasoning — and write its output to `RUN.md` front matter
  (step 6) so the run loop's pre-dispatch deadline guard reads a concrete
  number.
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
write-verify-broker` registers a second, un-jailed job (launchd on macOS) that runs the
**pinned** `verify_command` in a run-root-confined worktree, and each task's
verify becomes a `verify-request` → `verify-await` handshake
([`launch-runtime.md`](launch-runtime.md) §5). On Linux, pass the run's
nono profile as `--nono-profile` and start the broker with `detach --label
<broker label> --script <broker script>`. Pass the broker's label to the
orchestrator's `launch` as `--broker-label`: every supervisor teardown then
stops the broker too.

## Step 6 — Materialize the task graph into run state

Run the adapter's `list_ready` and `dependency_graph`
([`adapters.md`](adapters.md)) to build the run's task
graph and its blocker edges. Print the candidate count and the task ids
`list_ready` returned (for gh-issue, the issue numbers) before writing
anything, so a source broader than intended — a loose label query — is
visible while the human can still abort. On a gh-issue source, also run
`gh-issue-claim.py stale-claims` as a dry run, the same call `/doctor` makes
(`commands/doctor.md`, "Stale gh-issue claims"), and **warn, never block**, on
any `dead` line: a dead claim is outside the run's candidates, so it costs the
run nothing, but a crash in this run would add another dead claim, and
`/sweep-stale-claims --apply` clears them. Exit `4` means the sweep could not
read GitHub: say the stale-claims check did not run, and still do not block. Write `.auto-pilot/RUN.md` — front matter
(`run_id`, `work_source`, `base_branch`, `verify_command`/`exercise_path` from
step 5, `min_task_budget` from step 3) plus the per-task table, in the exact
format defined in
[`run-state.md`](run-state.md) "`RUN.md`" — do **not**
restate that format here. A less-claude launch writes its profile fields; an
ordinary launch omits them and uses their documented defaults, preserving its
existing `RUN.md` bytes. Also seed empty `.auto-pilot/QUESTIONS.md` and
`.auto-pilot/REPORT.md`. **Commit** all three to the run-state branch (the
first write under the run-state branch's fixed write order).

**Scout — per-task capability join (BLOCKS LAUNCH).** Record each task's
resolved `<backend>` (step 3) in its `coder` cell as the table is written
above (`run-state.md` "`RUN.md`"). Then run:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh" --scout-run-md .auto-pilot/RUN.md
```

against the environment fingerprint (step 2) — the demand side the auth
probes structurally can't see: for each task, does its declared backend
**exist in this environment** — the motivating case is a `codex` task in a
`claude-web` run with no `codex` binary — and, only when any task's backend
is `cao`, is the less-claude CAO gate (`cao`/`cao-run`/`cao-server` on `PATH`,
the daemon responding at `localhost:9889`, every `cao_coder_mapping` route in
the fixed CAO fleet) satisfied. Its `SCOUT VERDICT: go` / `no-go` line and any
`BLOCKS LAUNCH: <task> -> <backend> (missing)` lines are the source of truth;
a `no-go` **BLOCKS LAUNCH** with the reason it names — install the backend, or
re-route the task. This is a **deterministic** check only: it blocks on a
provable route-vs-environment gap, never a guess about what a task's prose
might need — inferring demands from task _text_ is a separate, warn-only
predictive scout, deliberately not here.

**v1 treats every route as _required_** — an absent backend blocks. A planned
follow-up softens this to **required-vs-preferred** (a _preferred_ backend
that's absent warns and falls back to the next-ranked `select-coder` spec
instead of blocking): the fallback order already exists, the missing piece is
the require/prefer bit on the task route.

## Step 7 — Spawn the detached orchestrator

Per [`launch-runtime.md`](launch-runtime.md):

1. Write the self-contained **launch script** — env, the sandbox wrapper (the
   two-layer profile: seatbelt/nono for filesystem+process, the harness network
   allowlist narrowed to this run's tools for host egress), and log redirection
   to `.auto-pilot/orchestrator.log`. The egress allowlist also gets the
   pre-flight's resolved `/add-task` destination host via `render-settings
   --add-task-host`, so the run's own settings never deny its own follow-up
   filing regardless of work source. Grant the run root `--rw`, which
   covers the run clone, the run worktree and `workers/`. On Linux, also pass
   the nono profile `--git-worktree <run worktree>`, and write it and the launch script outside
   every write grant (`write-launch` refuses otherwise): see
   [`launch-runtime.md`](launch-runtime.md) "Linux: the nono profile".
2. Run the **auth smoke test through that exact sandbox wrapper + env** (not
   bare) — a failure here is a launch blocker (ties to step 2). Machine-stays-
   awake was already confirmed in the pre-flight (step 2).
3. **Detach** via the OS-appropriate primitive (`launchd`/`launchctl` on macOS,
   a `systemd-run --user` timer on Linux) so the orchestrator outlives this
   session; record its **PID + process start-time + `--until` deadline** on the run-state branch for
   later stale-run detection (the start-time guards against a recycled PID being
   mistaken for a live run).
4. Print **where state lives** — the run-state branch name, the `.auto-pilot/`
   files, and the log path — and tell the user the run is going.
