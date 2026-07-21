---
title: Auto-pilot E-lite — design proposal (identity-first, no-VM substrate)
created: 2026-07-21
revised: 2026-07-21 — v3 after second-round external review
status: proposal v3 — round-2 blockers addressed
context: v1 incorporated maintainer decisions of 2026-07-21 (Max subscription for everything; trial a dedicated macOS agent user; GitHub App identity; tmux-hosted orchestrator for remote attach). v2 applied the first external review. v3 addresses the second-round review's six Stage-1 blockers (auto-pilot-e-lite-design-review-codex-r2.md) — chiefly by making the entire privileged control plane (launcher, broker, registry, watcher) maintainer-owned, so trust flows one way. §10 records the v1→v2→v3 changes.
audience: reviewer, then implementer
related:
  - ./auto-pilot-architecture-review-2026-07-21.md
  - ./auto-pilot-architecture-review-2026-07-21-codex.md
  - ./auto-pilot-option-e-research-2026-07-21.md
  - ./auto-pilot-e-lite-design-review-codex.md (round 1, of v1)
  - ./auto-pilot-e-lite-design-review-codex-r2.md (round 2, of v2)
  - ./auto-pilot-problem-statement.md (Part I problem statement unchanged)
---

# Auto-pilot E-lite: design proposal (v3)

## 0. Summary and the one-way trust rule

Replace the hand-built substrate (Seatbelt renderer + launchd wake cycle +
6.3k-line bash supervisor) with a small **maintainer-owned control plane**
and an **agent-owned execution plane**:

| Plane | Owner (uid) | Components |
| --- | --- | --- |
| Control | maintainer | launcher (`ap-launch`), stopper (`ap-stop`), token broker, registry, watcher, continuation wrapper (Stage 5+) |
| Execution | `agent` | tmux server + run shim + Claude session + workers + clones/worktrees |

**The one-way trust rule (v3's organizing principle, from the round-2
review):** the control plane never executes agent-controlled code, never
reads agent-controlled *configuration*, and never accepts agent input as
authoritative. Everything the agent writes (heartbeat, exit files, run
notes) is a **claim**; only control-plane observations (its own process
checks, its own API queries) are **facts**. There is no privileged API the
agent can invoke — no sudo hooks, no on-demand mint. Trust flows one way.

**The standing anti-spiral rule (unchanged):** watcher, broker, launcher,
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
- **Broker contract** (round-2 blocker 1): a maintainer-owned script run by
  a maintainer launchd job every **45 minutes**. Its App ID, installation
  ID, key path, token path, and log path are **hard-coded constants in the
  script**; it takes no arguments, reads no environment beyond PATH-fixed
  absolute binary paths, follows no symlinks (it verifies each path's
  owner, mode, and non-symlink status before use, and refuses otherwise).
  There is **no agent-invokable mint** — v2's sudo-whitelisted on-demand
  mint is **withdrawn** (it made the 1-hour TTL meaningless, since the
  agent could re-invoke at will). With a 45-minute refresh of a 60-minute
  token, the published token always has ≥15 minutes of validity; a stale
  token file means the broker is dead, and the run's response is
  stop-and-notify — never a fallback credential.
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
- **Positive no-fallback evidence** (round-2 §3.1): the Stage-1 gate runs
  the canary with instrumented helpers — capturing which credential helper
  responded, which `gh` binary resolved, and which token was presented —
  not merely with personal credentials absent.

### 2.2 GitHub authorization policy (round-2 blocker 3)

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

Unchanged from v2: Linear bot key (team-scoped, Read+Write, no Admin) in
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
| Claude Max OAuth | agent | agent `~/.claude` | Yes | subscription | long-lived | re-auth | console sign-out |

## 3. Containment layer

### 3.1 The `agent` user

Unchanged from v2 in substance: non-admin headless account, work under
`/Users/agent/work/`, per-user tool caches (Homebrew binaries are shared;
caches/globals are agent-local — availability is a Stage-2 assertion),
secrets 0600/0640 with audited ownership, no shared writable directories,
no ACL leakage from your home.

**sudoers, minimized by v3's structure**: because launch/stop/registry are
maintainer-owned programs run *by you* (§4), the only remaining rule is
maintainer → `agent` for the fixed launch/attach/exec wrappers (exact
command paths, NOPASSWD). The agent user has **no sudo rules at all** —
nothing to invoke, nothing to abuse (round-2 blocker 4 dissolves rather
than being patched).

### 3.2 Native sandbox and the `gh` hole

Unchanged from v2: sandboxed Bash `failIfUnavailable`, network allowlist
(`api.anthropic.com`, `github.com`, `api.linear.app`, loopback). The `gh`
exclusion remains a named hole bounded server-side (§2.2's policy is the
compensating control, now with denial tests). Stage 2 tests effective
policy per execution path (plain Bash, excluded `gh`, each worker
backend), never trusting sandbox startup success.

### 3.3 CAO (evidence-gated; fallback unchanged)

As v2: `cao-server` as `agent` via launchd
(`CAO_ENABLE_WORKING_DIRECTORY=true` in the unit); participates only after
the Stage-2 in-worker canary (uid, HOME, CWD, env inventory, sentinel
unreadability, sandbox behavior — plus, per round-2, **worker parentage**:
record each worker's PID, PPID, and process tree so §4's stop contract
knows what tmux teardown does *not* kill). Fallback if it fails: disable
`less-claude` for auto-pilot; workers are sandboxed Claude sessions.

## 4. Run lifecycle (maintainer-owned launcher — restructured in v3)

v2 had an agent-owned entry script appending to a maintainer registry via
sudo hooks — the round-2 review correctly called this an un-designed
privileged API (blockers 4 and 5). v3 inverts it: **the launcher is
maintainer-owned and maintainer-run**, and derives every registry fact
from its own observations.

### 4.1 Process topology (explicit, replacing the PGID assertion)

```
you (maintainer shell, local or ssh)
└─ ap-launch <repo> <source> [flags]          maintainer uid
   ├─ acquires lease, writes launch record     (control plane)
   └─ sudo -iu agent tmux -S /usr/local/autopilot/tmux.sock \
        new-session -d -s ap-<run_id> run-shim <run_id>
      └─ tmux server                           agent uid (long-lived, all runs)
         └─ pane: run-shim                     agent uid
            ├─ setsid → records own {pid, pgid, starttime}
            │   to /Users/agent/work/<run_id>/.runfile   (a CLAIM)
            └─ exec claude ... (orchestrator session)
                └─ workers (subagents; CAO workers live OUTSIDE this tree)
```

- **Run identity**: `run_id` = timestamp+slug. Session `ap-<run_id>` on
  the single pinned socket `/usr/local/autopilot/tmux.sock` (parent dir
  maintainer-owned 0750 group `apagent`; the socket itself is created by
  the agent tmux server; the watcher checks it via the sudo'd exec
  wrapper). Stale-socket handling: if the socket exists but no agent tmux
  server holds it, `ap-launch` removes it before starting (an observation
  it makes itself, as maintainer).
- **Incarnation identity**: every recorded process is stored as
  `{pid, starttime}` pairs (from `ps -o lstart=`), never bare PIDs — PID
  reuse cannot impersonate a recorded process (round-2 blocker 5).
- **Lease** (maintainer-owned): atomic `mkdir
  /usr/local/autopilot/lock/<repo-key>` where `<repo-key>` is the
  canonical remote URL hash (not a local path). The lock's owner record
  `{run_id, launcher pid+starttime, created_at}` is written inside it
  before the tmux session starts. Takeover exists only as
  `ap-launch --take-over`, run by you, which verifies the recorded
  incarnation is dead before replacing the record — two concurrent
  resumers cannot both pass the atomic mkdir.
- **Stop semantics** (`ap-stop <run_id>`, maintainer-owned): read registry
  + runfile; verify the runfile's `{pid,starttime}` still matches a live
  process (else record observed-terminal and exit); signal the shim's
  recorded process group (TERM, grace, KILL); `tmux kill-session`; then
  shut down this run's CAO workers via the CAO API (they are *not* in the
  pane tree — the round-2 point — so the stop contract names them
  explicitly). Release the lease last.
- **Terminal records — two kinds, never conflated** (round-2 blocker 5):
  - *agent-claimed exit*: the shim writes `{exit_code, end_time}` to the
    runfile on normal exit. The watcher copies it into the registry
    **marked as a claim**.
  - *observed-terminal*: written by watcher or `ap-stop` from their own
    process checks (session gone, incarnation dead). SIGKILL/power loss
    produce no claim — only an observed-terminal record. The registry
    never represents disappearance as a trusted exit.

### 4.2 Registry (maintainer-owned, agent never writes)

`/usr/local/autopilot/registry/` — writers are `ap-launch`, `ap-stop`, and
the watcher only (all maintainer uid). Schema: versioned JSONL, one file
per month; record types `launch`, `observed_terminal`, `claimed_exit`
(marked), `alert`, `canary`, each with `run_id`, `schema`, monotonic
`seq`, and writer identity. Append serialization via `flock`; duplicate
`seq` or out-of-order records are a watcher alert, not silently repaired.
"Append-only" is enforced by uid ownership (agent has group read, no
write), not asserted.

### 4.3 Heartbeat and remote workflow

Heartbeat unchanged (agent touches `.heartbeat` per loop turn; liveness
claim only). Remote workflow: ssh → sudo'd attach wrapper → detach.
Double-launch refused by the lease; two operators attaching is safe
(read-only unless they type).

## 5. Supervision

### 5.1 Watcher (maintainer-owned; liveness semantics per round-2 blocker 6)

Launchd job, `StartInterval` 300s, **single short-lived pass per
invocation** (no daemon to wedge): non-blocking `flock` (skip if a
previous pass is running); every external operation has a timeout —
process/tmux probes 10s, push delivery 15s, total pass budget 60s. If a
pass overruns, launchd simply fires the next one; two consecutive
skipped-by-flock passes append a `watcher_slow` record.

Checks per tick (each independent, none authorizes repair): registered-run
incarnation liveness; tmux session existence (via the sudo'd exec
wrapper); heartbeat freshness vs threshold; terminal-record consistency
(runfile claim vs registry); alert-queue state.

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

**Hard prohibitions** (unchanged, now including the §4.2 exception stated
precisely): the watcher writes only observation records and alerts to the
registry; it never edits run state, clears anything, re-mints, kills, or
resumes. Interruption is a human running `ap-stop`.

### 5.2 Reboot

Stop-the-run event; boot notification; human-initiated `--resume` (§6).

### 5.3 Usage-limit handling (evidence-based; continuation Stage 5+ only)

- **Authoritative evidence** (round-2 §3.4): a `claude` exit alone is not
  a rate-limit determination. On any orchestrator exit, the *watcher*
  (maintainer side, same Max subscription) queries usage itself
  (`claude-usage.sh --session-status` as maintainer). Exit + independently
  observed exhausted-window = `usage_limit` observed-terminal record with
  `reset_epoch`; exit + healthy window = ordinary termination alert. If
  the usage query itself fails: fail closed — ordinary termination alert,
  no continuation.
- **Stages 1–4: stop-and-notify.** The alert carries `reset_epoch`.
- **Stage 5+: bounded continuation, control-plane-owned.** Not an
  agent-side sleeping parent (v2's design — its sleep was
  indistinguishable from a wedge). Instead, when the watcher records
  `usage_limit`, it writes an `expected_resume {run_id, at: reset_epoch+jitter}`
  record — maintainer-owned durable state, so the run is *known* to be
  intentionally paused (no false stall) and the attempt counter survives
  crashes and relaunches (round-2: durable counters). At/after `at`, the
  continuation step — a maintainer launchd job — re-runs `ap-launch
  --resume <run_id>` (same lease, fresh reserve check, exact run
  identity — never "most recent conversation", the ambiguity the review
  flagged in `claude-auto-resume.sh`): **at most once per usage window,
  at most twice per run**, counters read from the registry. Clock changes,
  host sleep past `at`, reboot, or missing usage data → stop-and-alert,
  never a silent extra attempt.

## 6. Keep / port / delete

Unchanged from v2 (three-way split), with one addition: the ported
*resume* is invoked as `ap-launch --resume <run_id>` — lease revalidation
and registry continuity come from the control plane; the model-side
reconciliation prose (git + tracker + run-state comparison) is the ported
read-only part. Deletion of the old harness: after Stage 5 + grep-clean
dependency audit (the round-1 verified coupling list: `SKILL.md`,
`resume.md`, `run-state.md`, `run-budget.md`, `launch-runtime.md`).

Linear reconciliation as v2 (≈24 issues obsoleted with pointer; PRE-536 →
launcher prompt file; PRE-551 → §2.1/§5.3 protocols; PRE-619 closes with
the supervisor).

## 7. Migration stages and evidence gates (v3 — round-2 gate fixes applied)

**Stage 1 — identity + broker.**
Build: App + rulesets (§2.2), broker, token file, `gh` shim, cred helper,
agent gitconfig (runnable before the agent user exists).
**Gate** (all executed): canary performs clone/commit/push/PR
open+comment+close/Linear read+write **including the GraphQL reads `gh`
actually issues**; **App key unreadable from every Stage-1 process path**
(moved here per round-2 blocker 2); broker fixed-config verified (refuses
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
TMPDIR, CWD, tool versions, env allowlist** (round-2 additions);
sentinels in your home/Keychain/`~/.ssh`/`~/.aws`/personal gitconfig
unreadable; App key unreadable; Claude auth headless; `kill -9` of the
orchestrator → remote push (delivery-logged) within the 10-min SLO;
canary alert received on the real device; re-attach after disconnect;
double-launch refused; lease takeover drill (dead incarnation replaced,
live one refused); CAO worker parentage recorded and `ap-stop` shown to
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
real remote channel; continuation counters proven durable across a wrapper
crash. Then one genuinely unattended run: starts with no attached
terminal; spans **≥ 4 hours**; crosses ≥ 1 real boundary (worker
dispatch, broker token renewal mid-run, watcher-corroborated usage-window
stop or continuation, dependency transition); morning report tied to
actual git/tracker state; no alert gap > 10 min while awake; canary
received at 08:00.
**After this gate only**: dependency audit → delete old harness → Linear
reconciliation.

## 8. Risks (v3)

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
6. **Control-plane bloat** (the new #1 spiral risk per round 2): the
   one-way trust rule + anti-spiral rule are the tripwire; any
   interpretation/repair logic in launcher/broker/watcher/wrapper is a
   stop-and-reassess event.

## 9. Cut

v2 cuts (CAO-hosted orchestrator; `includeIf`; macOS-notification-primary;
re-mint-on-401; Docker exclusions; "kept unchanged" claim) — plus, in v3:
the **agent-invokable on-demand mint** (§2.1) and the **agent-side
continuation parent** (§5.3), both replaced by control-plane equivalents.

## 10. Changelog

**v1 → v2** (round-1 review): broker split; run contract; keep/port/delete
correction; CAO evidence-gating + fallback; Stage-2 negative tests;
external usage-limit handling; watcher registry/alerts/canary; deletion
moved post-Stage-5; branch protections; overclaims retracted.

**v2 → v3** (round-2 review, six blockers):

1. **Broker fixed-configuration** (blocker 1): hard-coded constants, no
   args/env/symlinks with owner/mode verification, atomic
   temp+fsync+rename publication, maintainer-owned `/usr/local/autopilot/`
   tree with non-agent-writable parents — and the agent-invokable
   on-demand mint **removed entirely** (45-min refresh makes it
   unnecessary; its existence made the TTL meaningless).
2. **Key unreadability moved into the Stage-1 gate** (blocker 2), tested
   from every Stage-1 process path.
3. **GitHub authorization policy** (blocker 3): new §2.2 — no-bypass
   rulesets, `bestdan/ap/**` branch restriction, installation trust
   separation, four denial tests in the Stage-1 gate.
4. **Registry authority resolved** (blocker 4): launcher/stopper/watcher
   are maintainer-owned and derive records from their own observations;
   the agent writes only claims to its runfile; **no sudo append hooks
   exist**. Agent-claimed exits are stored marked as claims, distinct
   from observed-terminal records.
5. **Process-tree contract** (blocker 5): explicit topology (§4.1),
   `{pid,starttime}` incarnation identity everywhere, lease keyed to
   canonical remote URL with atomic takeover, stop semantics that name
   CAO workers as outside the pane tree, SIGKILL/power-loss handled as
   observed disappearance, stale-socket procedure.
6. **Watcher liveness/alert semantics** (blocker 6): short-lived
   single-pass design with per-operation timeouts, flock skip +
   `watcher_slow` signal, persisted bounded retry queue,
   accepted-vs-delivered distinction with the daily canary as the
   end-to-end test, numeric SLOs (10-min awake alert; 08:00 canary;
   24h watcher-silence floor with a documented human response).
7. **Continuation redesigned** (round-2 §3.4): watcher-corroborated
   usage evidence (fail-closed), `expected_resume` as maintainer-owned
   durable state (no false stalls, crash-proof counters), resume by exact
   `run_id` via `ap-launch --resume`, never "most recent conversation."
8. Stage-2 gate additions (groups, CWD, env allowlist, tool versions,
   lease-takeover and CAO-parentage drills); Stage-5 made numeric.
