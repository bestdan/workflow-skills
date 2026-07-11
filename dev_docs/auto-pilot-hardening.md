# Auto-pilot hardening — the jail, the spawn contract, and the run's discipline

`/auto-pilot` runs a whole task graph unattended, inside a two-layer sandbox, on
a detached `launchd` job. [`auto-pilot.md`](./auto-pilot.md) is the durable
_why_ of the mode itself. This doc is the durable _why_ of what it took to make
that mode actually survive a **detached, sandboxed, unattended** run — the
contract of the spawn helper, the posture on verification, the pre-flight, and
the handful of invariants the loop must not violate.

It exists because the first fully detached run
([`autopilot-detached-run-1-findings.md`](./autopilot-detached-run-1-findings.md))
succeeded only after three fatal spawn bugs were hand-patched at launch and five
judgment calls were made live. Everything below is the graduated form of that
backlog: the design a new dev should be able to follow without reading the run
log. Finding numbers (`#N`) refer to that findings doc; task/PR numbers refer to
the hardening plan that implemented them.

## The jail

Two layers, and only two, contain the orchestrator (it runs
`bypassPermissions` — the jail, not a human, is the bound):

1. **Filesystem + process/exec** — a Seatbelt profile rendered by
   `scripts/spawn-orchestrator.sh render-profile` and applied with
   `sandbox-exec` over the whole process tree.
2. **Host network egress** — the detached `claude -p`'s own
   `sandbox.network.allowedDomains`, emitted by `render-settings`,
   deny-by-default and narrowed at launch to the hosts this run actually needs.

Those two walls are the ones that matter and they stay tight: writes are
confined to the run/worker worktrees plus `$TMPDIR`, and egress is an explicit
allowlist. Everything else in the profile is a **coarse guard**, not a
containment boundary — a distinction the first run forced into the open.

**Exec breadth is coarse on purpose (#3).** Seatbelt's `process-exec` is
strictly allowlisted, and a real workload shells out to dozens of coreutils,
`git`-core helpers, `uv`→python, `node`, and the coders. An allowlist of
individual resolved binaries is both unusable (the check harness and the coders
cannot run at all) and fragile (`claude` resolves into a version-pinned path
under `~/.local/share/claude/versions/…` that breaks on the next update). So
`render-profile` grew `--exec-dir <dir>` (a `(subpath …)` grant) and
`--toolchain` (expand to the standard bin dirs that exist on this host:
`/bin`, `/usr/bin`, `/usr/sbin`, `/usr/libexec`, `/opt/homebrew/bin`,
`~/.local/bin`, `~/.local/share/claude`, `~/.codex`, `~/.nvm`). Broadening exec
was verified not to widen the two real walls — writes to `$HOME`, `/etc`, and
sibling repos stay denied, and off-allowlist egress stays denied.

**Credential stores cannot be read-only (#5).** The design originally mounted
`~/.claude`, `~/.config/gh`, and `~/.codex` read-only. That breaks a real run:
a long orchestrator and its coders _write_ their own state there (sessions,
todos, rollouts, caches). The resolution is to split the **credential file**
from the **tool-state directory**: the state dir is `--rw`, and each token file
is passed as `--cred-ro <file>`, which emits a specific
`(deny file-write* (literal …))` _after_ the RW block so it overrides the
co-located allow. Known limitation, documented rather than fail-closed: Seatbelt
matches on path, not inode, so a hard link to a credential inside an RW scope
would still be writable.

## Verify outside the jail — the central lesson

The jail denies `execve` of the repo's own `#!/usr/bin/env bash`
scripts-under-test: `bad interpreter: Operation not permitted`, exit 126. The
project's check harness is `scripts/check.sh` → `bash scripts/test-*.sh`, so
**the run's stated definition of done cannot pass inside the jail** (#4). This
is not a code defect and not a flaky test: it reproduces identically on pristine
`main`, regardless of the diff. It is environmental, and any run that treats a
126 from a `test-*.sh` gate as a real failure will chase a ghost. Nested
`sandbox-exec` (`codex --sandbox workspace-write`, and the spawn harness's own
confinement smoke) compounds it — nesting does not compose (#6).

The fix (task_4 / PR #172) is a **verify broker**: verify runs _outside_ the
jail. A sandboxed process's children inherit its profile, so the jailed
orchestrator cannot simply spawn an un-jailed verifier; the broker is therefore
a **separate, un-jailed `launchd` job** that polls a sentinel directory:

```
jailed orchestrator                un-jailed broker (launchd, NO sandbox-exec)
verify-request  ── writes ──▶  <dir>/<id>.request  {worktree, cmd_hash}
                               runs its OWN pinned command in that worktree
verify-await    ◀── reads ───  <dir>/<id>.result   {code, output}
```

Two properties make that safe to hand a `bypassPermissions` agent:

- **Command pinning.** The broker runs a fixed verify string baked in at install
  time (`write-verify-broker --verify-cmd`). The request carries only a **hash**
  of it; a request whose hash disagrees with the broker's own pin is refused. No
  request and no agent ever supplies a command.
- **Worktree confinement.** `--confine-under <run root>` — the broker refuses any
  requested worktree outside the run root.

The trust extended is exactly the trust the human already extends when they
re-run `check.sh` before merging; the broker only moves it earlier.

**The degradation protocol (what runs did before the broker existed).** Every
hardening task in this very plan shipped under it, and it remains the fallback
whenever the broker is unavailable:

1. Run the content gates that _do_ execute in-jail: `dprint check`,
   `claude plugin validate --strict`, `uv run validate.py`.
2. Confirm each harness failure **reproduces on pristine `main`** — that is what
   classifies it as environmental rather than a defect.
3. Confirm **no failing harness touches the changed files**; if one does, the
   environmental classification does not hold and the task must park.
4. Flag "**re-run `scripts/check.sh` outside the jail before merging**" in the PR
   body, every time.

That protocol is correct but relies on a human remembering step 4 — which is why
the broker, not the protocol, is the design.

## The spawn contract

`scripts/spawn-orchestrator.sh` exists so no agent hand-authors a
`bypassPermissions` jail at 3am. Every subcommand is **fail-closed**: paths must
be absolute and exist, and nothing partial is ever written.

| Subcommand            | Contract                                                                                                                                                      |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `render-profile`      | Render the layer-1 Seatbelt profile. `--rw` / `--ro` scopes, `--cred-ro <file>` (RO token inside an RW state dir), `--exec` / `--exec-dir` / `--toolchain`    |
| `check-profile`       | Confirm a rendered profile actually **compiles** (`sandbox-exec -f`), surfacing the Seatbelt parser error verbatim                                            |
| `render-settings`     | Emit the layer-2 egress allowlist as the ephemeral `claude -p --settings` JSON: `--source`, `--coder`, `--agy-host`, `--mcp-host`, `--add-task-host`, `--npm` |
| `write-launch`        | Emit the launch script + `launchd` plist: `--verbose` alongside `--output-format stream-json`, and `--path <PATH>` exported into the job                      |
| `smoke-test`          | Auth-probe **through** the exact wrapper + settings, with the same flag set the real run uses                                                                 |
| `record-handle`       | Record pid / start time / `--until` so a later `status` can detect a recycled PID                                                                             |
| `detach`              | `launchctl bootstrap` the plist                                                                                                                               |
| `launch`              | The atomic composition: write-launch → smoke-test → detach → record-handle, in that order                                                                     |
| `status --label`      | Read-only: RUN.md `status:`, the phase table, the last meaningful log event, PID liveness, the deadline, and the done-sentinel — in one call                  |
| `teardown --label`    | Write the done-sentinel atomically, **then** `launchctl bootout`                                                                                              |
| `write-verify-broker` | Install the separate, un-jailed, command-pinned verify broker (with `verify-request` / `verify-await` as its jailed-side pair)                                |

Four of those details are load-bearing and were each a fatal bug:

- **`--verbose` is mandatory (#1).** `claude -p --output-format stream-json`
  refuses to start without it, so a launch script missing it dies before doing
  anything. The `smoke-test` must therefore run the _exact_ flag set the launch
  script uses — a reduced probe (`claude -p 'ok'` with no `--output-format`)
  green-lights an invocation shape the real run never makes.
- **PATH must be injected (#2).** A `launchd` job runs with
  `PATH=/usr/bin:/bin:/usr/sbin:/sbin`. `gh`, `claude`, `codex`, `uv`, `node`
  are all invisible there, so every tool call fails command-not-found. The
  pre-flight already resolves each binary's absolute path; those dirs are fed to
  `write-launch --path`.
- **The ordering inside `launch` is the safety property (#8).** Smoke-test auth
  _through the wrapper_ before detaching: a dead credential must fail loudly now,
  not silently at 3am. `launch` was originally unusable precisely because the
  above two bugs forced hand-editing the script _between_ write-launch and
  detach; folding the fixes into `write-launch` is what makes the atomic
  subcommand usable again.
- **The done-sentinel is written before bootout (task_8 / PR #176, #17).** A
  watcher polling for completion must never observe "job gone, no done-marker."
  `teardown --done-sentinel` writes `.auto-pilot/orchestrator.done` via
  tmp-then-rename in the sentinel's own directory (same filesystem, so the rename
  is atomic) and only then boots the job out. `status` reads that same file —
  one completion mechanism, never two.

`status --label` also replaces the ad-hoc `python -c` log scraping the first run
needed for every progress check: the orchestrator log is stream-json only, and
`REPORT.md` lags per-event.

**Stop hooks (#18).** A project `Stop` hook that isn't jail-compatible fires
`stop-hook-error` on every turn-stop. It is benign — the run still succeeds — but
it floods the log. The detached launch neutralizes incompatible Stop hooks (a
minimal settings hook set) rather than leaving the noise to be re-diagnosed.

## The pre-flight

`scripts/preflight.sh` (task_5 / PR #173, #7 and #10) collapses ~20 manual
launch steps into one call: `preflight.sh --source <plan|linear> [--base <branch>]`.

Two properties define it:

- **Strictly read-only.** It never mutates git or the filesystem outside a
  private scratch dir it creates and removes. It _composes_ the existing probes
  (`probe-coders.sh`, `preflight-freshness.sh`, and `spawn-orchestrator.sh`'s own
  `render-profile` / `render-settings` for the confinement smoke) rather than
  re-implementing them.
- **Fail-closed.** An unknown or indeterminate result is a **blocker**, not a
  pass. "Base freshness could not be determined (offline)" blocks; it does not
  shrug.

Output is parseable `PREFLIGHT <KEY>: <val>` lines ending in a single
`PREFLIGHT VERDICT: go` or `PREFLIGHT VERDICT: no-go — <reason>`, exit 0 / 1 / 2.
The keys double as the environment fingerprint the launch consumes: `GH_AUTH`,
`VIEWER_PERMISSION` (draft-vs-ready capability), `ENV_CLASS`, `CODER <name>`,
`FRESHNESS`, `PATH_DIR` / `EXEC_DIR` (fed straight to `write-launch --path` and
`render-profile --exec-dir`), `DEST_HOST`, `HANDLER`, and the smoke results.

Blocker classes:

- **Auth** — `gh` missing or `gh auth status` failing; a coder that is
  _installed but logged out_ (the run would resolve it and then fail on it).
- **Base freshness** — the base branch is stale vs its remote, was never actually
  checked, or freshness is indeterminate. Tasks branch from the base; a stale
  base silently opens PRs against an outdated `main`.
- **Confinement smoke** — the profile fails to render for this host's
  fingerprint, exec through the rendered toolchain profile fails (the exec wall
  is broken, not merely narrow), a write to `$HOME` _succeeds_ through the
  profile (the filesystem wall is broken), or the egress allowlist doesn't render
  as enabled and deny-by-default.

Capability gaps that are genuinely inapplicable (no `sandbox-exec` on a non-macOS
host; nested `sandbox-exec` denied in the current environment) degrade to a
logged `skip` with a `SKIP_NOTE`, not a false pass and not a blocker.

## The plan adapter

The normal shape of an auto-pilot run is a `/plan-with-docs` plan on a working
branch, with the run-state branch cut from it. That shape breaks several
assumptions repo-pr's handler was written with, and the adapter now states the
resolutions outright rather than making each run re-derive them (task_6 / PR
#174).

- **No reservation `task-claim` PR for a single-orchestrator plan run (#12).**
  repo-pr's claim opens a draft PR seeded by an on-branch task-file status flip —
  but a plan-source code branch bases on `main`, where the task file does not
  exist, so there is nothing to seed it with; and one serialized orchestrator has
  no race to arbitrate. **The run-state branch is the lock.** Still do the
  pre-claim `gh` PR scan, for `--resume` idempotency.
- **The code PR carries only code (#3 of the dry-run).** Task files live on the
  run-state branch, so the `ready → in_progress → needs_review` status flips are
  committed **on the run-state branch**, never in the code PR's diff. repo-pr's
  "task file on `main`, deleted on merge" model does not apply.
- **Scaffolding deletion is run-level teardown, not a graph task (#13).** A plan
  whose last task is "delete the `<name>_plan/` folder" cannot be executed from a
  `main`-based PR: the folder was only ever committed on the working/run-state
  branch, so a PR branched from `main` has nothing to delete. The graduate-then-
  delete cleanup belongs to run teardown on the run-state branch (and, in
  practice, to a human follow-up) — the loop must never dispatch it as a task.
  _This doc is the "graduate" half of that cleanup._
- **Merge bottom-up, in dependency order (#14).** The stacked-PR freeze rule and
  the `base_sha` park guard protect against the _orchestrator_ moving a base; they
  do not protect against a _human_ merging a stacked child ahead of its parent. A
  child's diff is only correct relative to its parent's branch. `REPORT.md` must
  emit the explicit merge order — each chain's root first, then its children — and
  say why.

## The run loop's discipline

**HEAD discipline (#23).** The run worktree's HEAD must stay on the run-state
branch for the whole run. Task branches descend from `main`, and `main` has no
`.auto-pilot/` — so checking a task branch out in the run worktree makes the
**entire run state vanish from the working tree**: `RUN.md`, `QUESTIONS.md`,
`REPORT.md`, the log, the sentinel. It looks exactly like a catastrophic loss of
state and it is not; it is a checkout. This bit the live run. Coders work in
their own worker worktrees; the run worktree never changes branches.

**Cross-cutting findings get a tracked task, not a decision-log entry (task_7 /
PR #175, #15 and #16).** Two classes of co-review finding must not die in
`QUESTIONS.md`: a finding whose faithful fix spans every sibling script plus the
canonical spec (deferring it per-task is right; losing it is not), and a real
finding surfaced in round 2 that the 2-round co-review bound leaves unaddressed.
Both now route to a real `/add-task` follow-up, and the `QUESTIONS.md` entry
references the created task id. Because the `/add-task` handler may route to
Linear or Jira even on a plan-source run, the egress allowlist takes the resolved
destination via `render-settings --add-task-host` — otherwise the run's own
settings would deny the run's own follow-up filing.

**What already worked — keep it.** The `QUESTIONS.md` decision log (options /
call / why / reversibility) is what made an unattended run auditable. The fast
reviewer set (codex + Claude + reconciler) kept a task at ~13 minutes; cloud
reviewers would have dominated the window. The adversarial review pass caught
real bugs in workers that reported green. None of the hardening above touches
those.

## Status of the work

At the time of writing, the hardening is split across merged and open work.

Merged into `main`: `write-launch` `--verbose` + PATH propagation and the atomic
`launch` (task_1 / PR #169); `render-profile` `--exec-dir` / `--toolchain`
(task_2 / PR #170); `render-profile --cred-ro` write-scopes (task_3 / PR #177).

Still open as **draft PRs** — the code on `main` will not match these sections
until they land: the verify broker (task_4 / PR #172), `scripts/preflight.sh`
(task_5 / PR #173), the adapter and SKILL corrections (task_6 / PR #174), the
cross-cutting follow-up routing and `--add-task-host` (task_7 / PR #175), and
`status --label` plus the done-sentinel (task_8 / PR #176).

All five now target `main` and are **independent of each other** — nothing is
left stacked, so they may merge in any order. (The one chain among them, task_4
on task_1, dissolved when task_1 merged and GitHub retargeted #172 onto `main`.)
The bottom-up merge rule above still holds in general; it simply has no live
chain to constrain here.

## See also

- [`autopilot-detached-run-1-findings.md`](./autopilot-detached-run-1-findings.md)
  — the ranked findings this doc graduates. Cited inline as `#N`.
- [`autopilot-dry-run.md`](./autopilot-dry-run.md) — the earlier attended
  dry-run, which surfaced the plan-source seams (handler resolution, task files
  off `main`, `--until` sizing) before the detached run hit the jail.
- [`auto-pilot.md`](./auto-pilot.md) — the mode's own design decisions, and the
  map to the authoritative skill references.
