---
title: Auto-pilot E-lite — design proposal (identity-first, no-VM substrate)
created: 2026-07-21
status: proposal — v5
context: A maintainer-owned control plane paired with an agent-owned execution plane, so trust flows one way (Max subscription for everything; a dedicated macOS agent user; GitHub App identity; tmux-hosted orchestrator for remote attach).
audience: reviewer, then implementer
related:
  - ./auto-pilot-architecture-review-2026-07-21.md
  - ./auto-pilot-architecture-review-2026-07-21-codex.md
  - ./auto-pilot-option-e-research-2026-07-21.md
  - ./auto-pilot-e-lite-design-review-codex.md
  - ./auto-pilot-e-lite-design-review-codex-r2.md
  - ./auto-pilot-e-lite-design-review-codex-r3.md
  - ./auto-pilot-e-lite-design-review-codex-r4.md
  - ./auto-pilot-e-lite-design-review-codex-r5.md
  - ./auto-pilot-problem-statement.md
---

# Auto-pilot E-lite: design proposal

## 0. Summary and the one-way trust rule

Replace the hand-built substrate (Seatbelt renderer + launchd wake cycle +
6.3k-line bash supervisor) with a small **maintainer-owned control plane**
and an **agent-owned execution plane**:

| Plane     | Owner (uid) | Components                                                                                                    |
| --------- | ----------- | ------------------------------------------------------------------------------------------------------------- |
| Control   | maintainer  | launcher (`ap-launch`), stopper (`ap-stop`), token broker, registry, watcher, continuation wrapper (Stage 5+) |
| Execution | `agent`     | tmux server + run shim + Claude session + workers + clones/worktrees                                          |

**The one-way trust rule (the organizing principle):** the control plane
never executes agent-controlled code, never reads agent-controlled
_configuration_, and never accepts agent input as authoritative. Everything
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

## 0a. Implementation boundary — measure first, then specify

Five review rounds have settled the architectural direction (delete the
hand-built jail; identity-first containment; one-way trust) and cleared the
**minimal agent identity plus broker track** for Stages 0–1. They have _not_
cleared the production control plane. The remaining work separates into two
different categories which must not be collapsed into one implementation
spike:

- **Empirical substrate facts**: the real tmux pane/shim/Claude process
  topology; `setsid(2) → execve` PID/PGID/session continuity; launchd behavior
  across crash, sleep, and reboot; and whether the maintainer and agent
  credentials report the same Max window for one exact test session (a
  continuation-only prerequisite, not a Stage-2 blocker).
- **Design and policy choices**: the trusted run-manifest interface; atomic
  prepared/active/terminal launch states; lease-generation replacement;
  registration and teardown of the baseline in-tree worker topology; the
  clock-skew fail-closed policy; and the typed registry schema. Atomic
  continuation admission and CAO-worker registration are later, separately
  gated extensions. Experiments can inform these choices but cannot make them.

**Approved next step — a measurement spike, not a production prototype.** It
proceeds in **one-working-day tranches** (ordered by §7a): each tranche stops
at the day boundary, and every started probe closes as `confirmed`, `falsified`,
or `inconclusive` against its pre-written kill sheet. There is no `unfinished`
result and no automatic extension: another tranche may run a materially more
discriminating probe, take the named redirect, or defer the dependent feature.
No review round interposes between unchanged measurement tranches; only an
architectural redirect or the measured revision returns for review. Apart
from provisioning the dedicated non-admin agent identity, the spike runs
unprivileged from a disposable directory and dedicated test repository;
installs no production sudoers entry and writes nothing under
`/usr/local/autopilot`; and makes no Linear write. It uses the real Max account
in exactly two ways: establishing
the agent user's own Max OAuth and running the minimal invocations probe 1's
headless-auth check requires — one per launch context, interactive and
launchd, each consuming shared usage — and the
read-only cross-user coherence queries.
It may exercise real tmux, a per-user launchd test job, signals, clock changes
inside fixtures, and forced crashes. It must not mint or expose the production
App credential — probe 4 runs against a disposable test App (§7a).

The spike delivers a checked-in measurement table, reproducible fixture
commands/tests, a result for every started probe, and a result for every
baseline empirical question needed by Stage 2. A falsified or inconclusive
load-bearing result blocks only the dependent path; unrelated probes may
continue. The results are folded into a measured revision which completes the
baseline manifest, launch, lease, registry, and in-tree
worker-lifecycle contracts before Stage 2 implementation begins. Continuation
and CAO receive their own design and evidence gates before they are enabled;
they do not block the stop-and-notify baseline. Spike code is disposable and
is never promoted by renaming it into `/usr/local/autopilot/bin`.

This boundary applies the anti-spiral rule to the design itself: execute
claims about the substrate, but specify authority and transaction semantics
before privileged code exists. No Stage-1 work has begun.

## 1. Locked decisions (2026-07-21)

| # | Decision                                                          | Consequence                                                                                                                                                                                                                                                                                                                                                                                                            |
| - | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | **Claude Max subscription for everything.**                       | Budget control = usage windows. Reserve gate (`claude-usage.sh`) kept, re-verified under the agent user. Rate-limit exits handled by the control plane (§5.3). No server-side spend cap; parallelism + per-task bounds are client-side substitutes. The Max OAuth credential is an agent-readable bearer secret, explicitly in the threat model (blast radius: the subscription's usage window; revocable in console). |
| 2 | **Trial a dedicated `agent` macOS user.**                         | Headless service account; workflow §3.1. Permanent only if Stage-2 friction is acceptable.                                                                                                                                                                                                                                                                                                                             |
| 3 | **GitHub App identity.**                                          | Key never readable by agent (§2.1); authorization policy incl. no-bypass rulesets (§2.2).                                                                                                                                                                                                                                                                                                                              |
| 4 | **tmux-hosted orchestrator** (remote attach, always-on mac mini). | Plain tmux on a pinned socket; launched by the maintainer-owned launcher (§4).                                                                                                                                                                                                                                                                                                                                         |

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
  the broker log and refuse to _start_ a push/PR operation with < 5
  minutes of validity remaining (failing closed to stop-and-notify). A
  long write that loses authentication mid-flight falls under the
  unknown-outcome reconciliation rule below. A stale token file means the
  broker is dead; the response is stop-and-notify — never a fallback
  credential.
- **Clock policy**: token expiry and broker freshness use wall time because
  GitHub supplies wall-clock epochs. The broker and watcher also record a
  monotonic timestamp. If wall time moves by more than 60 seconds relative to
  monotonic elapsed time, admission fails closed and the watcher alerts until
  one successful fresh mint re-establishes the baseline. Forward and backward
  jumps are Stage-1 fault drills; age alone never authorizes reclamation or a
  retry.
- **Publication protocol**: all control-plane state lives under a neutral
  maintainer-owned root, `/usr/local/autopilot/` (`maintainer:apagent`,
  dirs 0750 — the agent traverses but cannot write or substitute paths).
  The broker writes the token to a temp file in the same directory, sets
  `maintainer:apagent 0640`, fsyncs, then **renames** onto
  `/usr/local/autopilot/gh-token` — readers never observe a partial or
  empty token. Issuance metadata (time, installation, expiry, serial) is
  appended to the broker log (0640, agent-readable, maintainer-writable).
- **Consumption** (agent side): the supported workflow reaches git writes and
  `gh` through fixed, maintainer-owned helpers which read the token file and
  correlated broker metadata per operation. There is no long-lived `GH_TOKEN`
  in any environment, so refresh needs no coordination with running shells.
  The helpers enforce the five-minute admission rule. Because the agent can
  read the bearer token and invoke another HTTP client, this is a workflow
  correctness invariant, not a security boundary; the server-side App policy
  remains the security boundary.
- **Failure protocol**: broker refreshes proactively; the agent never
  triggers minting. On an authenticated failure the orchestrator may retry
  an **idempotent read** once (the file may have just rotated); a **write
  with unknown outcome is never re-fired** — reconcile observable
  GitHub/Linear state first (the `pr-fix-guard.sh` discipline). Stale
  token + dead broker → stop-and-notify.
- **Positive no-fallback evidence**: the Stage-1 gate runs
  the canary with instrumented helpers — capturing which credential helper
  responded, which `gh` binary resolved, and the installation/token serial or
  one-way digest that was presented — never the raw bearer token — not merely
  with personal credentials absent.

### 2.2 GitHub authorization policy

Server-side write control, stated and tested:

- Default branch of every installed repo protected by a **ruleset with an
  empty bypass list** — the App is _not_ a bypass actor; PRs only.
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

| Credential                     | Owner      | Location                              | Agent-readable        | Scope                                   | TTL                 | Rotation      | Revocation test                     |
| ------------------------------ | ---------- | ------------------------------------- | --------------------- | --------------------------------------- | ------------------- | ------------- | ----------------------------------- |
| App private key                | maintainer | `~danielegan/.autopilot/app.pem` 0600 | **No** (Stage-1 gate) | mint-only                               | until rotated       | App settings  | delete key → next broker run alerts |
| Installation token             | broker     | `/usr/local/autopilot/gh-token` 0640  | Yes                   | installed repos, PR/contents/issues     | 1 h (≥15 min fresh) | broker 45 min | uninstall App                       |
| Linear bot key                 | agent      | agent env file 0600                   | Yes                   | team-scoped RW                          | static              | manual        | revoke in Linear                    |
| Claude Max OAuth (run)         | agent      | agent `~/.claude`                     | Yes                   | subscription                            | long-lived          | re-auth       | console sign-out                    |
| Claude Max OAuth (observation) | maintainer | maintainer Keychain / `~/.claude`     | No                    | subscription, usage queries only (§5.3) | long-lived          | re-auth       | console sign-out                    |

The observation credential is your existing login — inventoried because
the watcher's usage queries (§5.3) run as maintainer and would otherwise
have no credential path (`claude-usage.sh` reads the _invoking_ user's
Keychain), which would make continuation permanently fail closed.

## 3. Containment layer

### 3.1 The `agent` user

Non-admin headless account, work under
`/Users/agent/work/`, per-user tool caches (Homebrew binaries are shared;
caches/globals are agent-local — availability is a Stage-2 assertion),
secrets 0600/0640 with audited ownership, no shared writable directories,
no ACL leakage from your home.

**sudoers**: because launch/stop/registry are
maintainer-owned programs run _by you_ (§4), the only rule is
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

### 3.3 CAO (deferred, evidence-gated extension)

CAO is **disabled in the baseline Stage-2 control plane**. Only worker backends
whose processes remain in the registered run topology may be enabled. This
removes an out-of-tree lifecycle from the first production protocol.

CAO may be enabled later as `agent` via launchd
(`CAO_ENABLE_WORKING_DIRECTORY=true` in the unit) only after a separate design
defines how the control plane binds a CAO worker to `{run_id, generation}`
without accepting an agent-selected kill target. Its evidence gate records
each worker's PID, PPID, start identity, and process tree; injects pane-dead /
worker-live failures; and proves that `ap-stop` reaches only the registered
worker incarnation. Failure or absence of that gate keeps `less-claude`
disabled for auto-pilot.

## 4. Run lifecycle (maintainer-owned launcher)

An agent-owned entry script appending to a maintainer registry via sudo
hooks would be an un-designed privileged API. Instead, **the launcher is
maintainer-owned and maintainer-run**, and derives every registry fact
from its own observations.

### 4.1 Process topology

```
you (maintainer shell, local or ssh)
└─ ap-launch <repo> <source> [flags]          maintainer uid
   ├─ acquires a prepared lease generation     (control plane)
   ├─ writes an immutable run manifest + digest (control plane)
   ├─ starts the session via ap-agent-exec (see below)
   ├─ receives a candidate pane PID, then independently validates the
   │   measured parent/PID/PGID/session topology against the manifest
   └─ atomically publishes the active lease + launch record only after
       the post-start liveness recheck succeeds             (facts)
      session started as:
      tmux server                              agent uid (long-lived, all runs)
      └─ pane: run-shim                        agent uid, maintainer-owned binary
         ├─ setsid(2) → records own {pid, pgid, starttime}
         │   to /Users/agent/work/<run_id>/.runfile   (a CLAIM — corroboration only)
         └─ execve claude --session-id <uuid> …  (orchestrator session)
             └─ workers (subagents; a future CAO extension is outside this tree)
```

**Trusted run manifest.** Before it crosses the uid boundary, `ap-launch`
writes `/usr/local/autopilot/manifests/<run_id>.json` by same-directory temp,
fsync, rename, and directory fsync. The maintainer-owned, agent-readable file
contains the canonical repo key and checkout path, allowlisted source/flags,
Claude session UUID, generation, launch nonce, and expected executable paths;
the launch record stores its digest. The agent cannot replace or edit it.
`ap-agent-exec start-session <run_id> <generation>` reads only this manifest;
it does not accept paths, commands, prompts, or flags from argv or the
environment.

**The agent-side command boundary** is a single narrow wrapper,
`ap-agent-exec`, with a **fixed verb interface** containing only
`start-session <run_id> <generation>`, `pane-pid <run_id> <generation>`,
`list-sessions`, `attach <run_id>`, and
`kill-session <run_id> <generation>`. It is maintainer-owned,
invoked as `sudo -u agent env -i /usr/local/autopilot/bin/ap-agent-exec
…` (scrubbed environment; absolute maintainer-owned paths for `tmux` and
`run-shim` hard-coded inside it; fixed working directory; `run_id`
validated against `^[a-z0-9-]+$` before use). The generic `sudo -iu
agent <anything>` form exists nowhere in sudoers — the lifecycle boundary
gets the same fixed-configuration treatment as the broker.

The run shim either calls `setsid(2)` and `execve(2)` itself or uses a pinned,
maintainer-owned absolute helper selected by the measurement spike. Stage 2
cannot begin while that choice or its observed PID/PGID/session invariants are
unresolved. A candidate PID returned by agent-owned tmux is never sufficient:
the measured revision must define a unique control-plane binding and rollback
for start failure, pane death before observation, identity mismatch, and
launcher crash before active publication.

- **Run identity**: `run_id` = timestamp+slug. Session `ap-<run_id>` on
  the single pinned socket `/Users/agent/.autopilot-runtime/tmux.sock`
  (runtime directory agent-owned 0700; the control plane never reads or edits
  it directly and reaches tmux only through the fixed wrapper). `start-session`
  removes a stale socket only after its agent-side process probe finds no tmux
  server. Socket/session results are claims used to operate tmux; they never
  establish lease ownership, process identity, or terminal state.
- **Incarnation identity**: every recorded process is stored as
  a measured incarnation rather than a bare PID. `{pid, ps lstart, pgid, sid,
  executable}` is the initial candidate, but second-granularity wall-clock
  `lstart` is not by itself a kernel attestation. Stage 0 must establish the
  strongest stable identity available on the target macOS version, and the
  measured revision must fail closed if it cannot distinguish reuse or
  shim-to-Claude transition. The run's incarnation is never the short-lived
  launcher's PID or the runfile's claim.
- **Session identity**: `ap-launch` generates the Claude session UUID,
  passes it via `--session-id`, and records it in the launch record. Any
  future resume targets exactly that session — never "the most recent
  conversation in this directory."
- **Lease** (maintainer-owned, generation-based): the lock at
  `/usr/local/autopilot/lock/<repo-key>` (`<repo-key>` = canonical remote
  URL hash, not a local path) has `prepared`, `active`, and `terminal` states.
  Its owner record contains `{generation, run_id, manifest_digest, run
  incarnation, registered worker incarnations, created_at}`. Every transition
  occurs while holding `/usr/bin/lockf -t 0` on the repo's maintainer-owned
  lock file and publishes a same-directory temp using fsync, rename, and
  directory fsync. OS lock ownership ends on process death; there is no
  crash-persistent `mkdir` mutex. A writer that loses the non-blocking
  acquisition (e.g. to a running watcher pass) fails closed: it aborts the
  transition, publishes nothing, and reports the contention — it never blocks,
  spins, or retries within the same invocation. The measured revision must enumerate and
  fault-inject rollback at every prepared-to-active boundary before Stage 2.
  **Release** occurs only after an `observed_terminal` record is durable and
  the current-generation pane plus every registered worker are observed dead:
  by `ap-stop`, or by the watcher for a naturally terminated run. Generation
  compare under the lock prevents either writer from releasing a successor.
  **Takeover** (`ap-launch --take-over`, human-run) uses the same OS-owned lock,
  verifies the recorded incarnation and registered workers are dead, records
  the abandoned/prepared generation, and atomically publishes the incremented
  generation. Records from a superseded generation are recognizable and never
  acted on.
- **Stop semantics** (`ap-stop <run_id>`, maintainer-owned): all targets
  come **from the registry and live process-table observation, never from
  the runfile** — the runfile corroborates but cannot select a kill
  target (an agent-writable file must not aim a maintainer-privileged
  signal). Sequence: read the launch record's incarnation; confirm it in
  the process table. If the pane is already dead, continue to the registered
  worker check rather than releasing immediately. Otherwise signal its
  registered process group (TERM, grace, KILL), then invoke `ap-agent-exec
  kill-session <run_id> <generation>`. CAO is absent in the baseline; when its
  extension is enabled, stop only the registry-bound worker incarnations.
  Write `observed_terminal`, fsync it, and release the matching lease last.
- **Terminal records — two kinds, never conflated**:
  - _agent-claimed exit_: the shim writes `{exit_code, end_time}` to the
    runfile on normal exit. The watcher copies it into the registry
    **marked as a claim**. A claim **never** releases the lease,
    suppresses an alert condition, or authorizes resume or cleanup.
  - _observed-terminal_: written by watcher or `ap-stop` from their own
    process checks (session gone, incarnation dead). SIGKILL/power loss
    produce no claim — only an observed-terminal record. The registry
    never represents disappearance as a trusted exit.

### 4.2 Registry (maintainer-owned, agent never writes)

`/usr/local/autopilot/registry/` — writers are `ap-launch`, `ap-stop`, the
watcher, and (Stage 5+) the fixed continuation wrapper only (all maintainer
uid). Schema: versioned JSONL, one file
per month. Baseline record types (complete for Stages 2–4):
`launch_prepared`, `launch`, `launch_aborted`, `observed_terminal`,
`lease_release`, `claimed_exit` (marked), `alert`, `alert_failed`, `canary`,
`watcher_slow`, `clock_anomaly`, `usage_limit`, and `takeover`. Before bounded
continuation is enabled, a versioned Stage-5 schema extension adds
`expected_resume`, `continuation_reserved`, and `continuation_attempt`. Every
record has `schema`, monotonic `seq`, wall and monotonic timestamps, a
boot-session identifier, writer identity, and a link to the causal prior `seq`.
Run-scoped records also require
`run_id`, `repo_key`, and `generation`; host-scoped `canary`, `watcher_slow`,
and unassociated `clock_anomaly` records declare `scope: host` instead. Typed
records additionally carry the data their decision requires: manifest digest
and session UUID; process and worker incarnations; terminal evidence; alert
dedup key and delivery status; usage percent and `reset_epoch`; or continuation
reservation key, ordinal, scheduled/actual time, and outcome. The measured
revision must publish the baseline JSON schema before Stage 2 code begins; the
continuation extension must be published and fault-injected before Stage 5.

Append serialization uses `/usr/bin/lockf -t 0` on a maintainer-owned lock
file. The writer derives the next `seq` from the highest existing `seq` across
all monthly files — `seq` is global and never resets at file rollover — then
appends, fsyncs, and releases the lock;
duplicate or out-of-order records are a watcher alert, not silently repaired.
"Append-only" is enforced by uid ownership (agent has group read, no
write), not asserted.

### 4.3 Heartbeat and remote workflow

Heartbeat: agent touches `.heartbeat` per loop turn. **Staleness
threshold: 6 minutes**: stale → stall alert. With the two-minute watcher
cadence and bounded pass/delivery times, worst-case notification remains below
the ten-minute SLO.
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

Launchd job, `StartInterval` 120s, **single short-lived pass per
invocation** (no daemon to wedge): non-blocking `/usr/bin/lockf -t 0` (skip if a
previous pass is running); every external operation has a timeout —
process/tmux probes 10s, push delivery 15s, total pass budget 60s. If a
pass overruns, launchd simply fires the next one. Each successful pass
records its own timestamp; a pass that finds the previous success **older
than 2 intervals** appends a `watcher_slow` record covering the gap (the
realizable form of skipped-pass detection — a skipped pass can't record
anything itself, so the next successful one accounts for the silence).

The pass holds `/usr/bin/lockf -t 0` on its own maintainer-owned lock file;
process death releases ownership. Checks per tick (each independent):
registered-run incarnation and worker liveness; tmux session existence (via
`ap-agent-exec list-sessions`, as a claim); heartbeat freshness vs the
six-minute threshold; broker-token freshness (§2.1's 50-minute deadline);
wall/monotonic clock coherence; terminal-record consistency (runfile claim vs
registry); registry sequence/schema validity; overdue `expected_resume`
records (Stage 5+); and alert-queue state.

**Alert contract (numeric):**

- Primary channel: one durable remote push (ntfy/Pushover). "Provider
  accepted" ≠ "you received" — acknowledged. Mitigations: a persisted
  retry queue in the registry dir (5 attempts, exponential backoff, then
  an `alert_failed` record), dedup key `run_id+condition`, and the daily
  canary as the end-to-end delivery test.
- **SLOs**: stall/kill alert within **10 minutes** while the host is awake
  (the mini is always-on; sleep-during-run is out of contract). Canary at
  **08:00 daily**; a missed canary means _you check the mini_ — that
  human-side response is the documented missed-canary procedure, and it
  bounds watcher-silence at 24h. This is the accepted observability floor
  for Stage ≤4; Stage 5's gate exercises the real channel under a forced
  stall before any unattended overnight run.
- Boot check: registered run with no terminal record after reboot → boot
  notification.

**Hard prohibitions**: the watcher never edits agent run state, re-mints,
kills, resumes, classifies an agent claim as fact, or repairs registry data.
Its sole lease transition is generation-checked release after it
has durably recorded `observed_terminal` and independently observed the pane
and all registered workers dead; authoring registry records (`usage_limit`,
`expected_resume`, `watcher_slow`, alerts) is appending evidence, not a
transition. Interruption remains a human running
`ap-stop`.

### 5.2 Reboot

Stop-the-run event; boot notification; human-initiated `--resume` (§6).

### 5.3 Usage-limit handling (evidence-based; continuation Stage 5+ only)

- **Authoritative evidence**: a `claude` exit alone is not a rate-limit
  determination. On any orchestrator exit, the _watcher_ queries usage
  itself — as maintainer, with the **observation credential** from the
  §2.3 inventory (same Max subscription; `claude-usage.sh` resolves the
  invoking user's credentials, so the maintainer side must hold one — its
  presence is a Stage-2 canary item). **Exhausted-window predicate**
  (exact): the usage query reports the session window at ≥100%
  utilization with a `reset_epoch` in the future. Exit + predicate true =
  `usage_limit` record with `reset_epoch`; exit + predicate false =
  ordinary termination alert. Usage query fails → fail closed: ordinary
  termination alert, no continuation.
- **Coherence prerequisite**: continuation is disabled unless the measurement
  spike demonstrates that the maintainer and agent credentials return the
  same session-window utilization and `reset_epoch` for one exact Claude test
  session. Merely proving that both queries succeed is insufficient.
- **Stages 1–4: stop-and-notify.** The alert carries `reset_epoch`.
- **Stage 5+: bounded continuation, control-plane-owned.** Not an
  agent-side sleeping parent — its sleep would be indistinguishable from a
  wedge. When the watcher records `usage_limit`, it also writes
  `expected_resume {run_id, generation, at: reset_epoch+jitter}` —
  maintainer-owned durable state, so the run is _known_ to be
  intentionally paused (no false stall; and no agent heartbeat or session
  state can negate an `expected_resume` or an independently detected
  failure) and reservation state survives crashes and relaunches. At/after
  `at`, a maintainer launchd job acquires the run's OS-owned continuation
  lock, revalidates clock coherence, the durable `expected_resume`, current
  terminal state, reserve, and attempt bounds, then checks for a reservation
  keyed by `{run_id, generation, reset_epoch}`. If none exists it appends and
  fsyncs exactly one `continuation_reserved` record before releasing the lock.
  A duplicate job observes that reservation and exits without launching.

  The reservation owner invokes `ap-launch --resume <run_id>` to acquire a new
  lease generation and resume the **exact recorded Claude session UUID** from
  the prior launch record (§4.1). It writes a linked `continuation_attempt`
  outcome after launch succeeds or fails. Bounds are **at most once per usage
  window and at most twice per run**, computed from durable reservation
  records rather than best-effort attempt logs. A reservation stranded before
  launch is not retried automatically; it alerts for human reconciliation.
  Clock changes, host sleep past `at`, reboot, missing usage data, or session
  identity mismatch likewise stop-and-alert, never admit a silent extra
  attempt.

## 6. Keep / port / delete

**Keep as-is**: `/deliver-task` lifecycle + adapters + co-review + freeze
rule; `task-scan.py`, `plan-graph.py`, `claim-scan.sh`, `validate.py`,
`probe-coders.sh`, `select-coder`/`orchestrate-coders`/`cao-coder.sh`;
`pr-fix-guard.sh`; the run-state file formats as the human-readable
ledger.

**Port small** (new, minimal implementations under this design):

- _Admission canary_ (successor of `preflight.sh`, a rewrite, not a
  rename): executes the real path as the agent identity — clone, commit,
  push, PR open/close, Linear read/write, usage query, one worker
  dispatch — pass/fail on the actual load-bearing operations.
- _Read-only reconciliation_ (successor of doctor's 7 invariants):
  reports divergence between run-state, git, and tracker; repairs
  nothing.
- _Resume_, invoked as `ap-launch --resume <run_id>`: lease revalidation
  and registry continuity from the control plane; the model-side
  reconciliation prose (git + tracker + run-state comparison) is the
  ported read-only part. No alarm-clearing, exit-state, or doctor-repair
  steps — those concepts no longer exist.
- _Run-loop prose in `SKILL.md`_: heartbeat touch + reserve gate + §5.3
  stop semantics replace every legacy harness invocation.
- _`claude-usage.sh`_: verified (fixed if needed) for both credential
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

**Stage 0 — minimal agent identity + bounded measurement spike.**
Provision the non-admin `agent` user and `apagent` group, but no sudoers entry
or production control-plane path. Then run §7a's first, one-working-day
falsification tranche from unprivileged fixtures: write the kill sheet, then
run as many of the highest-priority probes as fit. Stop after one working day
instead of squeezing every question into the tranche. Close every started
probe with a classified result; an inconclusive load-bearing probe remains a
dependency blocker and may be rerun only with a changed kill sheet that names
the new evidence expected to discriminate it (§0a). The fixtures may measure
tmux/process identity, `setsid(2) → execve` continuity, per-user launchd
crash/sleep/reboot behavior, real alert delivery, and exact-session cross-user
usage-window coherence without the GitHub App key, Linear key, production
sudoers, or production installation paths; the Max coherence probe is
read-only. Stage 0 and Stage 1 may interleave under §7a. Their combined
baseline evidence, including the baseline crash-transaction kernel, must exist
before Stage 2; continuation-reservation and CAO evidence are not Stage-2
prerequisites.
**Gate**: checked-in measurements and reproducible fixtures classify every
baseline load-bearing question as confirmed, falsified, or inconclusive. A
falsified or inconclusive result blocks its dependent Stage-2 path. A measured
design revision specifies the trusted manifest, complete baseline registry
schema, launch/lease state machine, and rollback table before control-plane
implementation.

**Stage 1 — broker + server-side authorization.**
Build: the maintainer-owned identity/token directory skeleton; App + rulesets
(§2.2), broker, token file, fixed `gh`/git helpers, and the agent's gitconfig.
Run manifests and the registry wait for the measured protocol revision; this
stage does not build or launch the orchestrator control plane.
**Gate** (all executed): canary performs clone/commit/push/PR
open+comment+close/Linear read+write as the actual `agent` uid, **including the
GraphQL reads `gh` actually issues**; **App key unreadable from every
non-broker process path, including the agent identity**;
broker fixed-config verified (refuses
symlinked/wrong-owner paths; atomic rename observed under a concurrent
reader; no arguments/env accepted); mint fault drills (expired token,
missing key, revoked installation, clock skew) fail loudly; **denial
tests**: default-branch push, non-matching branch push, non-installed
repo, org-level operation — all denied, with the App absent from every
bypass list; **positive no-fallback evidence** via instrumented helpers
(§2.1). Forward/backward clock jumps exercise the 60-second fail-closed policy.

**Stage 2 — sandbox + tmux + control plane (CAO disabled).**
Prerequisite: Stage 0's measured protocol revision is approved. Build: agent
execution directories, sudoers (exact-command, maintainer→agent only),
maintainer-owned run shim and manifest writer, `ap-launch`/`ap-stop`/wrappers,
registry, watcher, and push channel. CAO remains disabled under §3.3.
**Gate — per execution path** (orchestrator Bash, excluded `gh`, each
enabled worker backend) **× launch context**
(ssh-interactive and launchd/no-GUI): recorded **uid, groups, HOME, PATH,
TMPDIR, CWD, tool versions, env allowlist**;
sentinels in your home/Keychain/`~/.ssh`/`~/.aws`/personal gitconfig
unreadable; App key unreadable; Claude auth headless; `kill -9` of the
orchestrator → remote push (delivery-logged) within the 10-min SLO;
canary alert received on the real device; re-attach after disconnect;
double-launch refused; lease takeover drills (dead incarnation replaced;
**live incarnation refused** — proving the incarnation is the pane, not
the exited launcher; **two concurrent takeovers → exactly one wins** the
OS-owned lock); pane death before observation, launcher death in every
prepared-to-active transition, run-shim-to-Claude identity continuity,
lock-owner death, and corrupt/truncated publication all produce the specified
rollback without touching a successor generation; `ap-agent-exec` rejects an
invalid `run_id`, generation mismatch, manifest digest mismatch, and unknown
verb.

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
real remote channel; the maintainer and agent usage queries return the same
window for the exact test session; the continuation schema extension is
published; continuation failure-injection drills prove reservations durable
across concurrent jobs and a continuation-job crash; a stale
`expected_resume` (clock moved / host slept past `at`) producing
stop-and-alert, and a
fresh agent heartbeat shown _not_ to cancel an overdue
`expected_resume`; duplicate jobs produce one `continuation_reserved` record
and at most one launch. Then one genuinely unattended run: starts with no
attached terminal; spans **≥ 4 hours**; crosses ≥ 1 real boundary (worker
dispatch, broker token renewal mid-run, watcher-corroborated usage-window
stop or continuation, dependency transition); morning report tied to
actual git/tracker state; no alert gap > 10 min while awake; canary
received at 08:00.
**After this gate only**: dependency audit → delete old harness → Linear
reconciliation.

## 7a. Tactical validation delivery order

Section 7 remains the durable migration plan: its gates define what must be
true before production authority or unattended scope increases. This section
is the tactical overlay: it decides what disposable experiment to run next so
the largest, least-certain assumptions are falsified before their dependent
components are built. Tactical priority is deliberately not the same as stage
number. A probe may run ahead of the production stage it informs, but it never
satisfies that stage's gate by itself.

Prioritize each probe by:

> **assumption blast radius × uncertainty × ease of falsification ÷ experiment
> cost**

The ordering rules are:

1. Before writing the fixture, write the falsifier, pass threshold,
   `inconclusive` condition, time cap, dependent work, and redirect. A result is
   useful only if it changes what gets built next.
2. Exercise one load-bearing assumption through the real boundary, with the
   smallest disposable fixture that can disprove it. Do not build the
   production-shaped component first.
3. Time-box each probe to at most half a day unless this section states
   otherwise. At the cap, stop and classify the probe as `confirmed`,
   `falsified`, or `inconclusive`; "nearly done" is not a fourth state and does
   not extend the cap.
4. Check in the fixture command/test, sanitized raw evidence, non-secret
   environment metadata, result, and decision. Never persist bearer tokens,
   credential files, secret-bearing headers, or secret environment values.
   Spike code is never promoted into production by renaming it.
5. On `falsified` or load-bearing `inconclusive`, stop dependent work at its
   next safe checkpoint. Reconcile any external write, record the exact durable
   state and invalidated assumptions, then take the named redirect or defer the
   feature. Independent work may continue. Never repair a probe until it
   resembles the desired answer.
6. Repeating an inconclusive probe requires a changed kill sheet naming the new
   evidence or method expected to distinguish pass from fail. Otherwise take
   the redirect or defer the dependent feature; do not carry the same probe
   across tranches as open work.

The dependency order is explicit: after the kill sheet, the dedicated-user
canary and real-alert skeleton may run in parallel. The process-binding and
GitHub-authority probes wait for the agent identity; the baseline
crash-transaction kernel waits for the measured process model and a draft
baseline state machine. The thin vertical run waits for those baseline probes,
the Stage-1 gate, and approval of the measured revision. This is a dependency
graph, not a utilization target: independent probes may run concurrently only
when they have separate evidence and enough attention to stop cleanly. Probe 3
is never deferred behind probe 2 merely because its priority number is larger.

| Priority | Work item                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Key assumption                                                                                                                                  | Falsification redirect                                                                                                                                |
| -------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
|        0 | **Kill sheet.** For every item below, record the falsifier, pass threshold, inconclusive condition, evidence required, time cap, dependent work, and redirect before running it.                                                                                                                                                                                                                                                                                                                                                                                                                              | The team shares binary-enough decisions; evidence will end debate rather than create another review round.                                      | Tighten the decision rule before any implementation work.                                                                                             |
|        1 | **Dedicated-user viability canary.** From both an interactive shell and a per-user launchd job, exercise headless Claude auth, native sandbox startup, one enabled worker, tool/cache access, and sentinel unreadability.                                                                                                                                                                                                                                                                                                                                                                                     | The no-VM `agent` identity is a usable execution substrate with a real filesystem/Keychain boundary.                                            | Stop the no-VM E-lite substrate; test a microVM-per-run boundary or restrict the product to attended operation.                                       |
|        2 | **tmux/process-binding spike.** Start a trivial shim and then a harmless exec surrogate through the fixed-wrapper shape (the real shim→Claude transition is captured once, during probe 1's authorized invocations — §0a's Max allowance admits no `claude` launches beyond probe 1's); measure PID/PPID/PGID/SID/executable continuity; inject pane death before observation, launcher death, replacement panes, and stop races.                                                                                                                                                                             | The maintainer can uniquely bind a requested launch to one live incarnation and stop only that incarnation despite the agent-owned tmux server. | Abandon pane identity as authority; evaluate a directly held maintainer guardian/process handle or a different session host before designing leases.  |
|        3 | **Real alert walking skeleton.** A trivial launchd-hosted heartbeat process is killed and wedged; the actual watcher cadence and real push channel must notify the device within ten minutes.                                                                                                                                                                                                                                                                                                                                                                                                                 | The inherent unattended promise—external detection and notification—works without the full registry or launcher.                                | Stop unattended work; change launch context, watcher primitive, cadence, or delivery provider and rerun this probe.                                   |
|        4 | **GitHub authority canary.** Under the actual agent uid, perform every required git/`gh`/GraphQL operation and all denial tests with instrumented credential resolution — against a **disposable test App** installed only on the test repository; the production App never enters the spike. Stage 1's gate reruns the identical tests against the real App.                                                                                                                                                                                                                                                 | The App identity is both sufficient for the delivery loop and constrained by server-side policy.                                                | Change App permissions/helpers or replace unsupported `gh` paths with fixed API calls; do not build broker hardening around a false permission model. |
|        5 | **Baseline crash-transaction kernel.** Input: the draft baseline launch/lease/registry state machine from the measured revision — this probe falsifies that draft, preserving §0a's empirical/design boundary; it runs only after the draft exists. In a disposable directory, implement only `prepared → active → terminal`, generation replacement, and registry append using OS-owned locks and atomic publication; kill the writer at every durable boundary and run concurrent contenders. Time cap: two working days (an explicit override of rule 3). Continuation reservation is deliberately absent. | The baseline control-plane state model has one recoverable outcome under crash and concurrency before it is coupled to tmux, GitHub, or Claude. | Simplify the protocol or evaluate a transactional store such as SQLite before writing `ap-launch`, `ap-stop`, or the watcher around it.               |
|        6 | **Thin vertical run.** Prerequisites: approved measured revision and Stage-2 sudoers/production paths. Build only enough production-shaped code to launch one agent session in a test repo, emit heartbeat, dispatch one worker, make one tiny commit/PR, independently verify the pushed SHA, and exercise stop/alert/reconciliation. Before each externally visible seam, record its expected outcome and reconciliation action. On failure, reconcile any unknown outcome and durably terminalize or explicitly pause the run before stopping.                                                             | The surviving identity, lifecycle, authority, supervision, and transaction assumptions compose across their real seams.                         | Stop at the first failed seam; leave a reconciled durable state, then return to its owning probe instead of completing adjacent components.           |
|        7 | **Production hardening.** Only after the thin run survives: complete broker renewal, typed registry coverage, reboot handling, recovery drills, and the Stage-2/4 matrices.                                                                                                                                                                                                                                                                                                                                                                                                                                   | Hardening is now protecting a demonstrated path rather than elaborating a speculative one.                                                      | Any architectural failure returns to the relevant earlier probe; ordinary implementation defects stay within this phase.                              |
|        8 | **Optional complexity.** Specify and fault-inject continuation reservation in its own disposable kernel, then add bounded continuation and overnight scheduling/fan-out. The separately designed and gated CAO extension remains last.                                                                                                                                                                                                                                                                                                                                                                        | Optional capability can be layered on without changing the proven baseline authority model.                                                     | Delete or defer the optional feature; retain the stop-and-notify, no-CAO baseline.                                                                    |

Rows 0–5 are disposable baseline probes under §0a's spike contract, and rule
4's never-promote rule applies to them. Rows 6–7 are the baseline production
path: their code is kept and written under §7's stage prerequisites and gates,
not under the spike contract, and only those stage gates advance production
authority. Row 8 is a later optional branch: its continuation kernel is again
disposable, while the production continuation, scheduling, and CAO changes are
kept only after their own Stage-5 or §3.3 gates.

Two cheap probes run opportunistically as soon as the agent identity exists:

- **Max-window coherence**: query the exact same test session as agent and
  maintainer. Failure immediately deletes automatic continuation from the
  build order but does not block Stages 1–4's stop-and-notify baseline.
- **`setsid(2) → execve` topology**: capture the transition before the broader
  tmux race matrix. Its result selects the run-shim implementation used by
  priority 2; it does not by itself establish control-plane identity.

The delivery rule is therefore **falsifier first, fixture second, production
component last**. Passing a probe is permission to test the next dependent
assumption, not evidence that the eventual production component is complete.

## 8. Risks

1. **Max runaway** (no server cap): reserve gate + parallelism cap +
   at-most-twice continuation, counters control-plane-durable. Accepted.
2. **`gh` outside the sandbox**: bounded by §2.2 (no-bypass rulesets,
   branch-pattern restriction, installation scope, 1h tokens, no key
   access) — each part denial-tested in Stage 1. Accepted.
3. **TCC under a headless user**: Stage-2 no-GUI context test; work stays
   out of TCC-protected locations.
4. **CAO under agent**: disabled in the baseline; enabled only after §3.3's
   separate registration/teardown design and evidence gate.
5. **Watcher/push blind spots**: bounded, not eliminated — numeric SLOs,
   retry queue, daily end-to-end canary, documented missed-canary
   procedure; sleep-during-run out of contract on the always-on mini.
6. **Control-plane bloat** (the #1 spiral risk): the
   one-way trust rule + anti-spiral rule are the tripwire; any
   interpretation/repair logic in launcher/broker/watcher/wrapper is a
   stop-and-reassess event.
