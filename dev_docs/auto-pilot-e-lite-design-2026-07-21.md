---
title: Auto-pilot E-lite — design proposal (identity-first, no-VM substrate)
created: 2026-07-21
status: proposal — v4
context: A maintainer-owned control plane paired with an agent-owned execution plane, so trust flows one way (Max subscription for everything; a dedicated macOS agent user; GitHub App identity; tmux-hosted orchestrator for remote attach).
audience: reviewer, then implementer
related:
  - ./auto-pilot-architecture-review-2026-07-21.md
  - ./auto-pilot-architecture-review-2026-07-21-codex.md
  - ./auto-pilot-option-e-research-2026-07-21.md
  - ./auto-pilot-e-lite-design-review-codex.md
  - ./auto-pilot-e-lite-design-review-codex-r2.md
  - ./auto-pilot-e-lite-design-review-codex-r3.md
  - ./auto-pilot-problem-statement.md
---

# Auto-pilot E-lite: design proposal

## 0. Summary and the one-way trust rule

Replace the hand-built substrate (Seatbelt renderer + launchd wake cycle +
6.3k-line bash supervisor) with a small **maintainer-owned control plane**
and an **agent-owned execution plane**:

| Plane | Owner (uid) | Components |
| --- | --- | --- |
| Control | maintainer | launcher (`ap-launch`), stopper (`ap-stop`), token broker, registry, watcher, continuation wrapper (Stage 5+) |
| Execution | `agent` | tmux server + run shim + Claude session + workers + clones/worktrees |

**The one-way trust rule (the organizing principle):** the control plane
never executes agent-controlled code, never reads agent-controlled
*configuration*, and never accepts agent input as authoritative. Everything
the agent writes (heartbeat, exit files, run notes) is a **claim**; only
control-plane observations (its own process checks, its own API queries) are
**facts**. There is no privileged API the agent can invoke — no sudo hooks,
no on-demand mint. Trust flows one way.

**The standing anti-spiral rule:** watcher, broker, launcher,
and wrapper each have a one-sentence job and are forbidden to interpret,
classify, repair, or resume. Special cases accumulating in any of them is a
stop-and-reassess event.

The delivery loop (`/deliver-task`, adapters, co-review, freeze rule) is
kept. Run-state/resume references are replaced, not retained (§6). The old
harness is deleted only after Stage 5 + a dependency audit.

## 1. Locked decisions (2026-07-21)

| # | Decision | Consequence |
| --- | --- | --- |
| 1 | **Claude Max subscription for everything.** | Budget control = usage windows. Reserve gate (`claude-usage.sh`) kept, re-verified under the agent user. Rate-limit exits handled by the control plane (§5.3). No server-side spend cap; parallelism + per-task bounds are client-side substitutes. The Max OAuth credential is an agent-readable bearer secret, explicitly in the threat model (blast radius: the subscription's usage window; revocable in console). |
| 2 | **Trial a dedicated `agent` macOS user.** | Headless service account; workflow §3.1. Permanent only if Stage-2 friction is acceptable. |
| 3 | **GitHub App identity.** | Key never readable by agent (§2.1); authorization policy incl. no-bypass rulesets (§2.2). |
| 4 | **tmux-hosted orchestrator** (remote attach, always-on mac mini). | Plain tmux on a pinned socket; launched by the maintainer-owned launcher (§4). |

## 2. Identity layer

### 2.1 Token broker (maintainer-owned, fixed-configuration)

- **App setup** (one-time): App `bestdan-autopilot`; permissions Contents
  RW, Pull requests RW, Issues RW, Checks Read; installed on target repos
  only.
- **Broker contract**: a maintainer-owned script run by
  a maintainer launchd job every **45 minutes**. Its App ID, installation
  ID, key path, token path, and log path are **hard-coded constants in the
  script**; it takes no arguments, reads no environment beyond PATH-fixed
  absolute binary paths, follows no symlinks (it verifies each path's
  owner, mode, and non-symlink status before use, and refuses otherwise).
  There is **no agent-invokable mint** — an on-demand mint the agent could
  trigger would make the 1-hour TTL meaningless, since the agent could
  re-invoke at will. With a 45-minute refresh of a 60-minute token, the
  published token normally has ≥15 minutes of validity — but a failed
  refresh makes staleness unbounded, so broker health is monitored
  independently: the **watcher alerts when the token file's mtime exceeds
  50 minutes** (i.e. before expiry, not after), and the agent-side helpers
  apply a **minimum-validity admission rule** — they read the expiry from
  the broker log and refuse to *start* a push/PR operation with < 5
  minutes of validity remaining (failing closed to stop-and-notify). A
  long write that loses authentication mid-flight falls under the
  unknown-outcome reconciliation rule below. A stale token file means the
  broker is dead; the response is stop-and-notify — never a fallback
  credential.
- **Publication protocol**: all control-plane state lives under a neutral
  maintainer-owned root, `/usr/local/autopilot/` (`maintainer:apagent`,
  dirs 0750 — the agent traverses but cannot write or substitute paths).
  The broker writes the token to a temp file in the same directory, sets
  `maintainer:apagent 0640`, fsyncs, then **renames** onto
  `/usr/local/autopilot/gh-token` — readers never observe a partial or
  empty token. Issuance metadata (time, installation, expiry, serial) is
  appended to the broker log (0640, agent-readable, maintainer-writable).
- **Consumption** (agent side): git's `credential.helper` and a `gh` shim
  read the token file per operation — no long-lived `GH_TOKEN` in any
  environment, so refresh needs no coordination with running shells.
- **Failure protocol**: broker refreshes proactively; the agent never
  triggers minting. On an authenticated failure the orchestrator may retry
  an **idempotent read** once (the file may have just rotated); a **write
  with unknown outcome is never re-fired** — reconcile observable
  GitHub/Linear state first (the `pr-fix-guard.sh` discipline). Stale
  token + dead broker → stop-and-notify.
- **Positive no-fallback evidence**: the Stage-1 gate runs
  the canary with instrumented helpers — capturing which credential helper
  responded, which `gh` binary resolved, and which token was presented —
  not merely with personal credentials absent.

### 2.2 GitHub authorization policy

Server-side write control, stated and tested:

- Default branch of every installed repo protected by a **ruleset with an
  empty bypass list** — the App is *not* a bypass actor; PRs only.
- The agent pushes only branches matching **`bestdan/ap/**`** (+ the
  run-state branch prefix); a ruleset restricts other branch creation and
  non-fast-forward pushes where supported.
- Installation set is the trust boundary: repos of differing sensitivity
  are not installed under the same App without a deliberate decision.
- **Denial tests in the Stage-1 gate**: direct default-branch push denied;
  push to a non-matching branch name denied; any operation on a
  non-installed repo denied; an org-level operation (e.g. team read)
  denied; the GraphQL reads `gh`'s real workflow performs succeed.

### 2.3 Linear + Claude + git config

Linear bot key (team-scoped, Read+Write, no Admin) in
the agent env file; Claude Max auth established once for the agent user
(interactive OAuth preferred; `setup-token` fallback; the
credentials.json + claude.json pairing caveat is a Stage-2 canary item).
The agent user has exactly one `~/.gitconfig` (bot identity,
`hooksPath=/dev/null`, credential helper → §2.1); run repos are fresh
agent-owned clones; per-task worktrees inside them.

**Credential inventory** (normative):

| Credential | Owner | Location | Agent-readable | Scope | TTL | Rotation | Revocation test |
| --- | --- | --- | --- | --- | --- | --- | --- |
| App private key | maintainer | `~danielegan/.autopilot/app.pem` 0600 | **No** (Stage-1 gate) | mint-only | until rotated | App settings | delete key → next broker run alerts |
| Installation token | broker | `/usr/local/autopilot/gh-token` 0640 | Yes | installed repos, PR/contents/issues | 1 h (≥15 min fresh) | broker 45 min | uninstall App |
| Linear bot key | agent | agent env file 0600 | Yes | team-scoped RW | static | manual | revoke in Linear |
| Claude Max OAuth (run) | agent | agent `~/.claude` | Yes | subscription | long-lived | re-auth | console sign-out |
| Claude Max OAuth (observation) | maintainer | maintainer Keychain / `~/.claude` | No | subscription, usage queries only (§5.3) | long-lived | re-auth | console sign-out |

The observation credential is your existing login — inventoried because
the watcher's usage queries (§5.3) run as maintainer and would otherwise
have no credential path (`claude-usage.sh` reads the *invoking* user's
Keychain), which would make continuation permanently fail closed.

## 3. Containment layer

### 3.1 The `agent` user

Non-admin headless account, work under
`/Users/agent/work/`, per-user tool caches (Homebrew binaries are shared;
caches/globals are agent-local — availability is a Stage-2 assertion),
secrets 0600/0640 with audited ownership, no shared writable directories,
no ACL leakage from your home.

**sudoers**: because launch/stop/registry are
maintainer-owned programs run *by you* (§4), the only rule is
maintainer → `agent` for the fixed launch/attach/exec wrappers (exact
command paths, NOPASSWD). The agent user has **no sudo rules at all** —
nothing to invoke, nothing to abuse.

### 3.2 Native sandbox and the `gh` hole

Sandboxed Bash `failIfUnavailable`, network allowlist
(`api.anthropic.com`, `github.com`, `api.linear.app`, loopback). The `gh`
exclusion remains a named hole bounded server-side (§2.2's policy is the
compensating control, now with denial tests). Stage 2 tests effective
policy per execution path (plain Bash, excluded `gh`, each worker
backend), never trusting sandbox startup success.

### 3.3 CAO (evidence-gated)

`cao-server` as `agent` via launchd
(`CAO_ENABLE_WORKING_DIRECTORY=true` in the unit); participates only after
the Stage-2 in-worker canary (uid, HOME, CWD, env inventory, sentinel
unreadability, sandbox behavior — plus **worker parentage**:
record each worker's PID, PPID, and process tree so §4's stop contract
knows what tmux teardown does *not* kill). Fallback if it fails: disable
`less-claude` for auto-pilot; workers are sandboxed Claude sessions.

## 4. Run lifecycle (maintainer-owned launcher)

An agent-owned entry script appending to a maintainer registry via sudo
hooks would be an un-designed privileged API. Instead, **the launcher is
maintainer-owned and maintainer-run**, and derives every registry fact
from its own observations.

### 4.1 Process topology

```
you (maintainer shell, local or ssh)
└─ ap-launch <repo> <source> [flags]          maintainer uid
   ├─ acquires lease                           (control plane)
   ├─ starts the session via ap-agent-exec (see below)
   ├─ OBSERVES the pane PID via ap-agent-exec pane-pid,
   │   resolves {pid, starttime} from the process table itself,
   │   then writes the launch record + lease incarnation   (facts)
   └─ session started as:
      tmux server                              agent uid (long-lived, all runs)
      └─ pane: run-shim                        agent uid
         ├─ setsid → records own {pid, pgid, starttime}
         │   to /Users/agent/work/<run_id>/.runfile   (a CLAIM — corroboration only)
         └─ exec claude --session-id <uuid> …  (orchestrator session)
             └─ workers (subagents; CAO workers live OUTSIDE this tree)
```

**The agent-side command boundary** is a single narrow wrapper,
`ap-agent-exec`, with a **fixed verb interface** — `start-session
<run_id>`, `pane-pid <run_id>`, `list-sessions`, `attach <run_id>`,
`kill-session <run_id>` — and nothing else. It is maintainer-owned,
invoked as `sudo -u agent env -i /usr/local/autopilot/bin/ap-agent-exec
…` (scrubbed environment; absolute maintainer-owned paths for `tmux` and
`run-shim` hard-coded inside it; fixed working directory; `run_id`
validated against `^[a-z0-9-]+$` before use). The generic `sudo -iu
agent <anything>` form exists nowhere in sudoers — the lifecycle boundary
gets the same fixed-configuration treatment as the broker.

- **Run identity**: `run_id` = timestamp+slug. Session `ap-<run_id>` on
  the single pinned socket `/usr/local/autopilot/tmux.sock` (parent dir
  maintainer-owned 0750 group `apagent`; the socket itself is created by
  the agent tmux server; the watcher checks it via the sudo'd exec
  wrapper). Stale-socket handling: if the socket exists but no agent tmux
  server holds it, `ap-launch` removes it before starting (an observation
  it makes itself, as maintainer).
- **Incarnation identity**: every recorded process is stored as
  `{pid, starttime}` pairs (from `ps -o lstart=`), never bare PIDs — PID
  reuse cannot impersonate a recorded process. **The run's incarnation is
  the pane/shim `{pid, starttime}` the launcher observed itself** — not
  the launcher's own PID (which exits immediately after launch and would
  make every live run look takeover-eligible), and not the runfile's
  claim.
- **Session identity**: `ap-launch` generates the Claude session UUID,
  passes it via `--session-id`, and records it in the launch record. Any
  future resume targets exactly that session — never "the most recent
  conversation in this directory."
- **Lease** (maintainer-owned, generation-based): the lock at
  `/usr/local/autopilot/lock/<repo-key>` (`<repo-key>` = canonical remote
  URL hash, not a local path) is created by atomic `mkdir` and holds an
  owner record `{generation, run_id, run incarnation, registered CAO
  worker ids, created_at}` — the incarnation written by the launcher
  *after* it observes the started pane. **Release** happens in exactly two
  places: `ap-stop` (after teardown), or reclamation by the next
  `ap-launch`/`--resume`. A lease left held after an observed-terminal is
  expected and harmless — the watcher never releases it (it clears
  nothing); the next launch reclaims it through the takeover path.
  **Takeover** (`ap-launch --take-over`, human-run): acquire the exclusive
  takeover mutex (a second atomic `mkdir`, `takeover.lock` — two
  concurrent takeovers cannot both hold it); verify the recorded **run
  incarnation and every registered CAO worker** are dead; increment
  `generation` and replace the owner record; release the mutex. Records
  from a superseded generation are recognizable and never acted on.
- **Stop semantics** (`ap-stop <run_id>`, maintainer-owned): all targets
  come **from the registry and live process-table observation, never from
  the runfile** — the runfile corroborates but cannot select a kill
  target (an agent-writable file must not aim a maintainer-privileged
  signal). Sequence: read the launch record's incarnation; confirm it in
  the process table (else record observed-terminal, release lease, exit);
  signal its process group (TERM, grace, KILL); `ap-agent-exec
  kill-session`; shut down this run's registered CAO workers via the CAO
  API (they are *not* in the pane tree, so the stop contract names them
  explicitly); release the lease last.
- **Terminal records — two kinds, never conflated**:
  - *agent-claimed exit*: the shim writes `{exit_code, end_time}` to the
    runfile on normal exit. The watcher copies it into the registry
    **marked as a claim**. A claim **never** releases the lease,
    suppresses an alert condition, or authorizes resume or cleanup.
  - *observed-terminal*: written by watcher or `ap-stop` from their own
    process checks (session gone, incarnation dead). SIGKILL/power loss
    produce no claim — only an observed-terminal record. The registry
    never represents disappearance as a trusted exit.

### 4.2 Registry (maintainer-owned, agent never writes)

`/usr/local/autopilot/registry/` — writers are `ap-launch`, `ap-stop`, and
the watcher only (all maintainer uid). Schema: versioned JSONL, one file
per month; record types (complete): `launch`, `observed_terminal`,
`claimed_exit` (marked), `alert`, `alert_failed`, `canary`,
`watcher_slow`, `usage_limit`, `expected_resume`, `continuation_attempt`,
`takeover` — each with `run_id`, `generation`, `schema`, monotonic `seq`,
and writer identity. Append serialization via `flock`; duplicate `seq` or
out-of-order records are a watcher alert, not silently repaired.
"Append-only" is enforced by uid ownership (agent has group read, no
write), not asserted.

### 4.3 Heartbeat and remote workflow

Heartbeat: agent touches `.heartbeat` per loop turn. **Staleness
threshold: 10 minutes** (matching the alert SLO): stale → stall alert.
Freshness is **non-authoritative**: a fresh heartbeat (or a
present-looking tmux session) means only "no stall condition" — it never
cancels, delays, or suppresses any condition raised by a control-plane
observation (dead incarnation, overdue `expected_resume`, registry
anomaly, broker-staleness), and it never establishes terminal state. A
wedged-but-touching run is caught by those other conditions and,
ultimately, by the human reading the morning report — the heartbeat is a
liveness tripwire, not a health certificate.

Remote workflow: ssh → `ap-agent-exec attach` wrapper → detach.
Double-launch refused by the lease; two operators attaching is safe
(read-only unless they type).

## 5. Supervision

### 5.1 Watcher (maintainer-owned)

Launchd job, `StartInterval` 300s, **single short-lived pass per
invocation** (no daemon to wedge): non-blocking `flock` (skip if a
previous pass is running); every external operation has a timeout —
process/tmux probes 10s, push delivery 15s, total pass budget 60s. If a
pass overruns, launchd simply fires the next one. Each successful pass
records its own timestamp; a pass that finds the previous success **older
than 2 intervals** appends a `watcher_slow` record covering the gap (the
realizable form of skipped-pass detection — a skipped pass can't record
anything itself, so the next successful one accounts for the silence).

Checks per tick (each independent, none authorizes repair): registered-run
incarnation liveness; tmux session existence (via `ap-agent-exec
list-sessions`); heartbeat freshness vs the 10-minute threshold;
broker-token freshness (§2.1's 50-minute deadline); terminal-record
consistency (runfile claim vs registry); alert-queue state.

**Alert contract (numeric):**

- Primary channel: one durable remote push (ntfy/Pushover). "Provider
  accepted" ≠ "you received" — acknowledged. Mitigations: a persisted
  retry queue in the registry dir (5 attempts, exponential backoff, then
  an `alert_failed` record), dedup key `run_id+condition`, and the daily
  canary as the end-to-end delivery test.
- **SLOs**: stall/kill alert within **10 minutes** while the host is awake
  (the mini is always-on; sleep-during-run is out of contract). Canary at
  **08:00 daily**; a missed canary means *you check the mini* — that
  human-side response is the documented missed-canary procedure, and it
  bounds watcher-silence at 24h. This is the accepted observability floor
  for Stage ≤4; Stage 5's gate exercises the real channel under a forced
  stall before any unattended overnight run.
- Boot check: registered run with no terminal record after reboot → boot
  notification.

**Hard prohibitions** (including the §4.2 exception stated
precisely): the watcher writes only observation records and alerts to the
registry; it never edits run state, clears anything, re-mints, kills, or
resumes. Interruption is a human running `ap-stop`.

### 5.2 Reboot

Stop-the-run event; boot notification; human-initiated `--resume` (§6).

### 5.3 Usage-limit handling (evidence-based; continuation Stage 5+ only)

- **Authoritative evidence**: a `claude` exit alone is not a rate-limit
  determination. On any orchestrator exit, the *watcher* queries usage
  itself — as maintainer, with the **observation credential** from the
  §2.3 inventory (same Max subscription; `claude-usage.sh` resolves the
  invoking user's credentials, so the maintainer side must hold one — its
  presence is a Stage-2 canary item). **Exhausted-window predicate**
  (exact): the usage query reports the session window at ≥100%
  utilization with a `reset_epoch` in the future. Exit + predicate true =
  `usage_limit` record with `reset_epoch`; exit + predicate false =
  ordinary termination alert. Usage query fails → fail closed: ordinary
  termination alert, no continuation.
- **Stages 1–4: stop-and-notify.** The alert carries `reset_epoch`.
- **Stage 5+: bounded continuation, control-plane-owned.** Not an
  agent-side sleeping parent — its sleep would be indistinguishable from a
  wedge. When the watcher records `usage_limit`, it also writes
  `expected_resume {run_id, generation, at: reset_epoch+jitter}` —
  maintainer-owned durable state, so the run is *known* to be
  intentionally paused (no false stall; and no agent heartbeat or session
  state can negate an `expected_resume` or an independently detected
  failure) and attempt counters survive crashes and relaunches. At/after
  `at`, a maintainer launchd job re-runs `ap-launch --resume <run_id>` —
  same lease (via the takeover path), fresh reserve check, resuming the
  **exact recorded Claude session UUID** from the launch record (§4.1) —
  writing a `continuation_attempt` record first. Bounds: **at most once
  per usage window, at most twice per run**, counted by
  `continuation_attempt` records in the registry (typed, durable,
  crash-proof). Clock changes, host sleep past `at`, reboot, or missing
  usage data → stop-and-alert, never a silent extra attempt.

## 6. Keep / port / delete

**Keep as-is**: `/deliver-task` lifecycle + adapters + co-review + freeze
rule; `task-scan.py`, `plan-graph.py`, `claim-scan.sh`, `validate.py`,
`probe-coders.sh`, `select-coder`/`orchestrate-coders`/`cao-coder.sh`;
`pr-fix-guard.sh`; the run-state file formats as the human-readable
ledger.

**Port small** (new, minimal implementations under this design):
- *Admission canary* (successor of `preflight.sh`, a rewrite, not a
  rename): executes the real path as the agent identity — clone, commit,
  push, PR open/close, Linear read/write, usage query, one worker
  dispatch — pass/fail on the actual load-bearing operations.
- *Read-only reconciliation* (successor of doctor's 7 invariants):
  reports divergence between run-state, git, and tracker; repairs
  nothing.
- *Resume*, invoked as `ap-launch --resume <run_id>`: lease revalidation
  and registry continuity from the control plane; the model-side
  reconciliation prose (git + tracker + run-state comparison) is the
  ported read-only part. No alarm-clearing, exit-state, or doctor-repair
  steps — those concepts no longer exist.
- *Run-loop prose in `SKILL.md`*: heartbeat touch + reserve gate + §5.3
  stop semantics replace every legacy harness invocation.
- *`claude-usage.sh`*: verified (fixed if needed) for both credential
  contexts — agent (reserve gate) and maintainer (watcher observation).

**Delete, do not port** (after Stage 5 + grep-clean dependency audit;
until then the old harness remains as an explicitly unsupported
fallback): `spawn-orchestrator.sh` + its test suite,
`orchestrator.sb.tmpl`, `orchestrator.plist.tmpl`,
`smoke-confinement.sh`, the supervisor pause ledger, exit
classification, in-jail alarm machinery, the verify broker, and
wrapper-specific doctor repairs. Verified coupling to audit: `SKILL.md`,
`resume.md`, `run-state.md`, `run-budget.md`, `launch-runtime.md`.

Linear reconciliation: ≈24 issues obsoleted with pointer; PRE-536 →
launcher prompt file; PRE-551 → §2.1/§5.3 protocols; PRE-619 closes with
the supervisor.

## 7. Migration stages and evidence gates

**Stage 1 — identity + broker.**
Build: App + rulesets (§2.2), broker, token file, `gh` shim, cred helper,
agent gitconfig (runnable before the agent user exists).
**Gate** (all executed): canary performs clone/commit/push/PR
open+comment+close/Linear read+write **including the GraphQL reads `gh`
actually issues**; **App key unreadable from every non-broker process
path**;
broker fixed-config verified (refuses
symlinked/wrong-owner paths; atomic rename observed under a concurrent
reader; no arguments/env accepted); mint fault drills (expired token,
missing key, revoked installation, clock skew) fail loudly; **denial
tests**: default-branch push, non-matching branch push, non-installed
repo, org-level operation — all denied, with the App absent from every
bypass list; **positive no-fallback evidence** via instrumented helpers
(§2.1).

**Stage 2 — agent user + sandbox + tmux + control plane.**
Build: user + `apagent` group, `/usr/local/autopilot/` tree, sudoers
(exact-command, maintainer→agent only), `ap-launch`/`ap-stop`/wrappers,
registry, watcher + push channel, CAO-as-agent unit.
**Gate — per execution path** (orchestrator Bash, excluded `gh`, each
worker backend incl. one real CAO worker) **× launch context**
(ssh-interactive and launchd/no-GUI): recorded **uid, groups, HOME, PATH,
TMPDIR, CWD, tool versions, env allowlist**;
sentinels in your home/Keychain/`~/.ssh`/`~/.aws`/personal gitconfig
unreadable; App key unreadable; Claude auth headless; `kill -9` of the
orchestrator → remote push (delivery-logged) within the 10-min SLO;
canary alert received on the real device; re-attach after disconnect;
double-launch refused; lease takeover drills (dead incarnation replaced;
**live incarnation refused** — proving the incarnation is the pane, not
the exited launcher; **two concurrent takeovers → exactly one wins** the
mutex); maintainer observation credential present and usage query
succeeds as maintainer; `ap-agent-exec` rejects an invalid `run_id` and
an unknown verb; CAO worker parentage recorded and `ap-stop` shown to
reach it.

**Stage 3 — run #4** (one small real task, non-auto-pilot plan, partially
attended, no fan-out, no auto-retry).
**Gate**: reviewable PR exists; **an independent verifier (CI or a
separate process) validates the exact pushed SHA**; forced **worker**
stall (orchestrator waiting) detected and alerted, in addition to the
orchestrator-kill case.

**Stage 4 — recovery drills** (3-task chain), each with a clean outcome:
orchestrator SIGKILL; crash between each durable transition (post-push /
post-tracker / post-run-state); expired token during a read; expired
token during a **write with unknown outcome** (reconcile, don't re-fire);
usage-limit exit (stop-and-notify path, watcher-corroborated); stale tmux
socket; boot-time notification after reboot; repeated + concurrent
`--resume` (lease holds); registry `seq` anomaly alerts.
**Gate**: reconciliation from git/tracker/registry only — zero legacy
supervisor commands, no duplicate PR/claim/comment, no destructive touch
of a live worker.

**Stage 5 — overnight.** Prerequisites: forced-stall alert through the
real remote channel; continuation failure-injection drills — counters
proven durable across a continuation-job crash, a stale `expected_resume`
(clock moved / host slept past `at`) producing stop-and-alert, and a
fresh agent heartbeat shown *not* to cancel an overdue
`expected_resume`. Then one genuinely unattended run: starts with no attached
terminal; spans **≥ 4 hours**; crosses ≥ 1 real boundary (worker
dispatch, broker token renewal mid-run, watcher-corroborated usage-window
stop or continuation, dependency transition); morning report tied to
actual git/tracker state; no alert gap > 10 min while awake; canary
received at 08:00.
**After this gate only**: dependency audit → delete old harness → Linear
reconciliation.

## 8. Risks

1. **Max runaway** (no server cap): reserve gate + parallelism cap +
   at-most-twice continuation, counters control-plane-durable. Accepted.
2. **`gh` outside the sandbox**: bounded by §2.2 (no-bypass rulesets,
   branch-pattern restriction, installation scope, 1h tokens, no key
   access) — each part denial-tested in Stage 1. Accepted.
3. **TCC under a headless user**: Stage-2 no-GUI context test; work stays
   out of TCC-protected locations.
4. **CAO under agent**: unproven until Stage-2 canary; fallback defined.
5. **Watcher/push blind spots**: bounded, not eliminated — numeric SLOs,
   retry queue, daily end-to-end canary, documented missed-canary
   procedure; sleep-during-run out of contract on the always-on mini.
6. **Control-plane bloat** (the #1 spiral risk): the
   one-way trust rule + anti-spiral rule are the tripwire; any
   interpretation/repair logic in launcher/broker/watcher/wrapper is a
   stop-and-reassess event.
