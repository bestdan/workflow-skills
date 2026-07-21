---
title: Auto-pilot E-lite — design proposal (identity-first, no-VM substrate)
created: 2026-07-21
revised: 2026-07-21 — v2 after external design review
status: proposal v2 — review applied
context: v1 incorporated maintainer decisions of 2026-07-21 (Max subscription for everything; trial a dedicated macOS agent user; GitHub App identity; tmux-hosted orchestrator for remote attach). v2 applies the external design review (auto-pilot-e-lite-design-review-codex.md) — verdict "directionally sound; not yet safe to approve" — whose ranked changes are folded in below. §10 records what changed and what was cut.
related:
  - ./auto-pilot-architecture-review-2026-07-21.md
  - ./auto-pilot-architecture-review-2026-07-21-codex.md
  - ./auto-pilot-option-e-research-2026-07-21.md
  - ./auto-pilot-e-lite-design-review-codex.md
  - ./auto-pilot-problem-statement.md (Part I problem statement unchanged)
---

# Auto-pilot E-lite: design proposal (v2)

## 0. Summary

Replace the hand-built substrate (Seatbelt renderer + launchd wake cycle +
6.3k-line bash supervisor) with five small components:

1. **Identity**: a bot identity per provider — GitHub App with a
   **maintainer-owned token broker**, Linear bot key, agent-local gitconfig.
   Blast radius enforced server-side (installation scope + branch
   protections). Claude auth stays on the **Max subscription**.
2. **Containment**: a dedicated headless **`agent` macOS user** running
   Claude Code's **native sandbox** fail-closed; excluded binaries (`gh`)
   are treated as outside the wall and bounded server-side, not assumed
   contained.
3. **Session host**: one long-lived Claude session in **tmux under the agent
   user**, on a fixed private socket, launched by a dedicated entry script
   with an explicit **run contract** (§4.1).
4. **Supervision**: a maintainer-owned watcher with its own registry and
   append-only log, one durable remote push channel, and a self-health
   canary. It observes and notifies; it never repairs.
5. **Rate-limit continuation**: an explicit external wrapper (successor of
   `claude-auto-resume.sh`) — never prose executed by a process that the
   usage wall has already killed.

The delivery loop (`/deliver-task`, adapters, co-review, freeze rule) is
kept. The run-state/resume **references are replaced, not retained** — v1's
"kept unchanged" was wrong; they invoke the legacy supervisor (§6). The old
harness is deleted only **after Stage 5** and a dependency audit.

**The standing rule (anti-spiral guard):** the watcher, broker, and
continuation wrapper are forbidden to interpret, classify, repair, or
resume. Each has a one-sentence job. If any of them starts accumulating
special cases, that is the early-warning sign from the inversion doc firing
again — stop and reassess.

## 1. Locked decisions (2026-07-21)

| # | Decision | Consequence |
| --- | --- | --- |
| 1 | **Claude Max subscription for everything.** | Budget control = usage windows. Reserve gate (`claude-usage.sh --reserve`) kept as the window arbiter — but it must be **re-verified under the agent user** (it reads Keychain / `~/.claude` of the invoking user). Rate-limit exits handled externally (§5.3). No server-side spend cap exists; parallelism and per-task bounds are the client-side substitutes. |
| 2 | **Trial a dedicated `agent` macOS user.** | Headless service account; workflow in §3.1. Adopt permanently only if Stage-2 friction is acceptable. |
| 3 | **GitHub App identity.** | With v2's broker split: the App's private key is **never readable by the agent user** (§2.1). |
| 4 | **tmux-hosted orchestrator** (remote attach on the mac mini). | Plain tmux. The CAO-hosted-orchestrator variant is **dropped** (review cut #1); CAO remains a worker backend only, and only once evidence-gated (§3.3). |

## 2. Identity layer

### 2.1 GitHub App + maintainer-owned token broker

**The review's top finding:** if the App private key lives in the agent
home, a compromised run can mint tokens forever — the 1-hour TTL bounds
nothing. So the key and the minting live with a **different principal**:

- **App setup** (one-time): create App `bestdan-autopilot`; permissions
  Contents RW, Pull requests RW, Issues RW, Checks Read — nothing else;
  install on target repos only. **Server-side write control does not stop
  at installation scope**: enable branch protection/rulesets on the
  default branch of every installed repo (no direct pushes; PRs only), so
  even a leaked token cannot write `main`.
- **Broker** (`scripts/gh-token-broker.sh`, maintainer-owned, ~60 lines):
  runs under **your** uid via a launchd job every 45 minutes and on demand.
  It signs the JWT with the key at `~danielegan/.autopilot/app.pem`
  (0600, your user), mints an installation token, and writes it to
  `/Users/agent/.autopilot/gh-token` (owned by you, mode 0640
  group-readable by `agent` — agent can read the token, never the key),
  plus an issuance record (time, installation, expiry) to the broker log.
- **Consumption**: the agent never holds `GH_TOKEN` in a long-lived
  environment (env inheritance is how tokens go stale mid-run). Instead:
  - git: `credential.helper` in the agent gitconfig is a 5-line script that
    reads the token file at each operation.
  - `gh`: a thin `gh` shim on the agent's PATH that exports
    `GH_TOKEN=$(cat token-file)` per invocation.
  Every operation therefore uses the freshest minted token; a broker
  refresh needs no coordination with running shells.
- **Failure protocol** (replaces v1's "re-mint on 401", cut by review): the
  broker refreshes **proactively before expiry**. On an authenticated
  failure the agent may trigger one on-demand mint (sudo-whitelisted,
  exact-command rule) and retry **idempotent reads only**. A **write with
  unknown outcome is never blindly retried**: the orchestrator reconciles
  observable GitHub/Linear state first (does the PR/comment/claim exist?)
  — the same discipline `pr-fix-guard.sh` already encodes. If the token
  file is stale/absent (broker dead), the run stops and notifies; it does
  not fall back to any other credential.

**Credential inventory** (the review's missing artifact — normative):

| Credential | Owner/uid | Location | Readable by agent? | Scope | TTL | Rotation | Revocation test |
| --- | --- | --- | --- | --- | --- | --- | --- |
| App private key | maintainer | `~danielegan/.autopilot/app.pem` | **No** (Stage-1 assertion) | mint-only | until rotated | regenerate in App settings | delete key → mint fails |
| Installation token | broker-minted | `/Users/agent/.autopilot/gh-token` | Yes | installed repos, PR/contents/issues | 1 h | broker, 45 min | uninstall App from repo |
| Linear bot key | agent | agent env file, 0600 | Yes | team-scoped, Read+Write | static | manual | revoke in Linear settings |
| Claude Max OAuth | agent | agent `~/.claude` (or `CLAUDE_CODE_OAUTH_TOKEN`) | Yes | full subscription | long-lived | re-auth | sign out of session in console |

The Claude OAuth credential and the Linear key remain agent-readable bearer
secrets — **explicitly in the threat model** (accepted under decision 1;
their blast radius is the subscription's usage window and one Linear team,
both tolerable and revocable).

### 2.2 Linear + Claude specifics

- **Linear**: bot member (or your account initially) issues a key scoped to
  the target team(s): Read + Write, no Admin. Authorization matrix, key
  location, and the revocation path live in the inventory above. Fast-path
  scripts already take the key via env.
- **Claude**: authenticate the agent user against Max once — interactive
  OAuth as the agent user (preferred; auto-refreshing), else
  `claude setup-token` → agent env file. Known packaging caveat: headless
  auth needs `~/.claude/.credentials.json` **and** `~/.claude.json`
  present, or the env-var path. Stage-2 canary covers it.

### 2.3 git identity (simplified — review cut #2)

No `includeIf` gymnastics: the agent user has exactly one `~/.gitconfig`:

```gitconfig
[user]
    name = autopilot-agent
    email = <app-slug>[bot]@users.noreply.github.com
[credential "https://github.com"]
    helper = !/Users/agent/.autopilot/git-cred-helper
[core]
    hooksPath = /dev/null
```

Run repos are **fresh clones** owned by `agent` (kills PRE-533/534 classes
at the root); per-task worktrees are ordinary `git worktree add` inside
them. The v1 claim that `gh auth git-credential` + `GH_TOKEN` "just works"
is downgraded to a Stage-1 test item: the gate exercises clone, fetch,
push, PR create, review comment, and the GraphQL reads `gh` actually
performs.

## 3. Containment layer

### 3.1 The `agent` user (workflow unchanged from v1)

You never log out or switch accounts. One-time scripted setup (~15 min):
`sysadminctl -addUser agent` (non-admin), `/Users/agent/work/`, env file,
sudoers entries (see below), per-user tool installs. Daily use:
`sudo` wrapper to launch; `tmux attach` to check in; detach freely.

**sudoers discipline** (review: broad sudo is itself a boundary): only
exact-command NOPASSWD rules, no general `sudo -u agent` shell —
e.g. your user may run the launch wrapper, the attach wrapper, and the
on-demand mint; nothing else. The launch path uses a **login context**
(`sudo -iu agent` inside the wrapper) so HOME/PATH/TMPDIR are the agent's
own — v1's bare `sudo -u agent tmux` inherits the caller's environment and
is exactly the class of quiet divergence the old harness died of.

**Agent-home hardening** (review addition): secrets 0600/0640 with audited
ownership; dedicated `~agent/tmp` and tool caches (Homebrew *binaries* are
world-readable, but caches/globals must be agent-writable — per-user
`npm`/`uv` prefixes; tool availability is a Stage-2 assertion, not an
assumption); no shared writable directories with your user; **no ACLs from
your home extended to agent**.

### 3.2 Native sandbox — and what is honestly outside it

Orchestrator and Claude workers run with sandboxed Bash,
`failIfUnavailable: true`. Network allowlist: `api.anthropic.com`,
`github.com`, `api.linear.app`, loopback.

**The `gh` exclusion is a named hole, not a footnote** (review finding #2):
an excluded binary runs with no sandbox policy — filesystem and network —
while bearing App authority. E-lite's position: the **credential wall is
the user boundary + server-side scope** (installation list, branch
protections, 1-hour tokens, no key access); the sandbox is defense-in-depth
for everything else. Stage 2 tests the *effective* policy per execution
path (plain Bash, excluded `gh`, each worker backend) rather than trusting
the sandbox's startup success. Docker is not in the run path and gets no
exclusion (review cut).

### 3.3 CAO: evidence-gated worker backend with a real fallback

Nothing in the repo proves CAO workers inherit the agent uid, HOME, env
allowlist, or sandbox settings (`cao-coder.sh` only validates inputs and
execs `cao-run`). So:

- CAO participates **only after** a Stage-2 canary records, from inside a
  real CAO worker: uid, HOME, CWD, env inventory, token-file readability,
  personal-home sentinel unreadability, and sandbox behavior.
- `cao-server` runs as `agent` via launchd with
  `CAO_ENABLE_WORKING_DIRECTORY=true` declared in the unit (retires the
  PRE-541 proxy-gate).
- **Fallback if CAO fails the canary**: the `less-claude` profile is
  disabled for auto-pilot runs and workers run as sandboxed Claude
  sessions under `agent`. v1's fallback ("leave CAO under your user") is
  **withdrawn** — it abandons the identity boundary the design exists for.

## 4. Session host and run loop

### 4.1 The run contract (new in v2 — review ranked-change #2)

A run is launched only by `scripts/run-entry.sh` (agent-owned), which
establishes, before the model starts:

- **Run identity**: `run_id` (timestamp+slug); tmux session named
  `ap-<run_id>` on the fixed private socket
  `tmux -S /Users/agent/.autopilot/tmux.sock` (one socket path for ssh,
  sudo, and launchd contexts — kills the socket-divergence failure mode).
- **Singleton lease**: an `mkdir`-style atomic lock at
  `/Users/agent/.autopilot/lock/<repo>`; a second launch or concurrent
  `--resume` fails loudly. Stale-lock takeover requires the recorded pgid
  to be dead.
- **Process group**: the entry script is the pgid leader and records it;
  stop = one signal to the group (no orphaned workers).
- **Durable launch record**: `run_id`, pgid, socket, work root, env-file
  path, command fingerprint, start time — appended to the **watcher
  registry** (§5.1) *before* the model starts, so observation never
  depends on discovering runs by globbing.
- **Heartbeat**: the orchestrator touches
  `/Users/agent/work/<run_id>/.heartbeat` at each loop boundary. Honest
  semantics: it proves the loop is *turning*, not that work is *good* —
  liveness signal only; progress is judged from git/tracker state, by
  humans or reconciliation, never by the watcher.
- **Terminal-state record**: on any exit path the entry script appends
  `{run_id, end_time, exit_code}` to the registry. "Session gone with no
  terminal record" is itself an alertable state.

The claim v1 made — that the wedge hazard "disappears structurally" — is
retracted per the review: a long-lived session can still hang forever. What
changes is that the *observer* is now a separate live process (§5) instead
of a wake slot the hung call has already consumed.

### 4.2 Remote workflow

ssh to the mini → attach wrapper (`sudo` exact-command) → watch or steer →
detach. Session survives disconnects by construction. Two-operator /
double-launch collisions are handled by the singleton lease, not etiquette.

## 5. Supervision

### 5.1 Watcher (maintainer-owned, observation only)

Runs under **your** uid (launchd, `StartInterval` 300s), with its own
state the agent cannot write:

- **Registry + log**: `~danielegan/.autopilot/registry.jsonl` (launch and
  terminal records, appended via the sudo-whitelisted entry hooks) and an
  append-only watcher log. The agent user has no write access to either.
- **Checks per tick** (each independent; none authorizes repair):
  registered-run pgid liveness, tmux session existence on the known
  socket, heartbeat freshness vs threshold, terminal-record presence, and
  last-alert-delivery result.
- **Alert contract**: primary channel is **one durable remote push**
  (ntfy or Pushover — chosen for the you're-not-at-the-mini case; macOS
  notification is a bonus, not the mechanism). Each alert has a dedup key
  (`run_id` + condition), a delivery timeout, a retry, and a logged
  delivery result. Undeliverable alerts are themselves logged as failures.
- **Self-health**: a daily canary alert ("watcher alive, N runs
  registered") so a dead watcher is noticed within a day, plus a boot-time
  message when the host restarts with a registered run not terminally
  recorded ("run X did not survive the reboot — resume when ready").
- **Hard prohibitions** (the anti-supervisor clause): the watcher never
  edits run state, clears anything, re-mints credentials, kills processes,
  or resumes runs. Stall response is: notify, with escalating dedup-keyed
  reminders. A **human** decides to interrupt (`stop-run <run_id>` sends
  one signal to the recorded pgid — a wrapper you run, not the watcher).

**Known accepted limits** (stated, per review): `StartInterval` is not a
strict timer across sleep — the notification SLO assumes an awake host
(the mini is always-on; sleep-during-run is out of contract and surfaces
via the boot/canary paths). Push delivery can fail during network loss —
mitigated by delivery logging + retries, not eliminated.

### 5.2 Reboot

A reboot is a stop-the-run event. Nothing auto-resurrects tmux sessions.
The watcher's boot check notifies; `--resume` (§6, rebuilt) is the human-
initiated recovery path.

### 5.3 Max usage-limit continuation (explicit, external — new in v2)

A process killed at the usage wall cannot execute pause prose. Decision:

- **Default (Stages 1–4): stop-and-notify.** The entry script detects the
  rate-limit exit of the `claude` process, records it terminally with
  reason `usage-limit`, and the watcher's notification tells you the reset
  time (from `claude-usage.sh`). You resume when you choose.
- **Overnight mode (Stage 5+): bounded continuation wrapper.** The
  successor of `claude-auto-resume.sh`, running as the entry script's
  parent: on a usage-limit exit it sleeps to `reset_epoch` + jitter and
  relaunches **at most once per window and at most twice per run**, then
  stops-and-notifies. It classifies nothing else; every other exit is
  terminal. This is the *only* automatic relaunch in the entire design.

## 6. Keep / port / delete (three-way split — replaces v1's kill/keep list)

The review verified v1's "kept unchanged" was false: `SKILL.md`,
`references/resume.md`, `run-state.md`, and `run-budget.md` all invoke
`spawn-orchestrator.sh` subcommands (heartbeat, alarms, exit-state, doctor,
supervisor gates). Retaining them unchanged leaves dead references.

**Keep as-is**: `/deliver-task` lifecycle + adapters + co-review + freeze
rule; `task-scan.py`, `plan-graph.py`, `claim-scan.sh`, `validate.py`,
`probe-coders.sh`, `select-coder`/`orchestrate-coders`/`cao-coder.sh`;
`pr-fix-guard.sh`; the run-state **file formats** as the human-readable
ledger.

**Port small** (new, minimal implementations under the run contract):
- *Admission canary* (successor of `preflight.sh`): executes the real path
  as the agent identity — clone, commit, push, PR open/close, Linear
  read/write, usage query, one worker dispatch — pass/fail on the actual
  load-bearing operations. Not a rename of `preflight.sh`; a rewrite
  against the new substrate.
- *Read-only reconciliation* (successor of doctor's 7 invariants): reports
  divergence between run-state, git, and tracker. Repairs nothing.
- *Rewritten `resume.md`*: reconcile from git + tracker + registry, take
  the singleton lease, continue. No alarm-clearing, no exit-state
  machinery, no doctor-repair steps — those concepts no longer exist.
- *Rewritten run-loop prose in `SKILL.md`*: heartbeat touch + reserve gate
  + the §5.3 stop semantics replace every `spawn-orchestrator.sh`
  invocation.
- *`claude-usage.sh`*: verified (and fixed if needed) to read the **agent
  user's** credential locations.

**Delete, do not port** (after Stage 5 + a `grep`-clean dependency audit —
deletion timing moved per review): `spawn-orchestrator.sh` + its test
suite, `orchestrator.sb.tmpl`, `orchestrator.plist.tmpl`,
`smoke-confinement.sh`, the supervisor pause ledger, exit classification,
in-jail alarm machinery, the verify broker, and wrapper-specific doctor
repairs. Until then the old harness stays as an explicitly unsupported
fallback.

**Linear reconciliation** (unchanged from v1): PRE-490–499, 533–536,
538/539, 542, 546–553 close as obsoleted-with-pointer; PRE-536's prompt
template survives as `run-entry.sh` + its prompt file; PRE-551 becomes the
§2.1 failure protocol; PRE-619 closes with the supervisor's deletion.

## 7. Migration stages and evidence gates (strengthened per review)

**Stage 1 — identity + broker.**
Build: App, broker, token file, `gh` shim, cred helper, agent gitconfig
(usable before the agent user exists — run against a scratch dir).
**Gate** (all executed, not reasoned): canary clones/commits/pushes/opens+
closes a PR/comments/reads Linear using only the agent identity; mint-path
fault drills (expired token, missing key, revoked installation, clock
skew) fail loudly and correctly; direct default-branch push **denied**;
op against a non-installed repo **denied**; a run of the canary with your
personal creds deliberately absent proves no Keychain/`gh auth` fallback
was silently used.

**Stage 2 — agent user + sandbox + tmux + watcher.**
Build: user, sudoers, entry/attach/stop wrappers, socket, registry,
watcher, push channel, CAO-as-agent unit.
**Gate — per execution path** (orchestrator Bash, excluded `gh`, each
worker backend including one real CAO worker), in **both** launch contexts
(ssh-interactive and launchd/no-GUI): recorded uid/HOME/PATH/TMPDIR/socket;
sentinel files in your home, Keychain, `~/.ssh`, `~/.aws`, personal
gitconfig all unreadable; **App private key unreadable by every path**;
Claude auth works headless; a `kill -9` of the orchestrator produces a
**remote push** (delivery-logged) within the SLO; watcher canary alert
received; re-attach works from a fresh ssh session after disconnect;
double-launch is refused by the lease.

**Stage 3 — run #4** (one small real task, non-auto-pilot plan, partially
attended, no fan-out, no auto-retry).
**Gate**: reviewable PR exists; **an independent verifier (CI, or a
separately-invoked check as a different process) validates the exact
pushed SHA** — "verify ran in the agent env" alone is insufficient; a
forced **worker** stall (not just orchestrator kill) while the orchestrator
waits is detected and alerted.

**Stage 4 — recovery drills** (3-task chain). Exercise, each with a clean
outcome: orchestrator `SIGKILL`; crash between each durable transition
(after push / after tracker update / after run-state write); expired token
during a read; expired token during a **write with unknown outcome**
(reconcile, don't re-fire); usage-limit exit (stop-and-notify path); stale
tmux socket; boot-time notification after a reboot; repeated and
concurrent `--resume` (lease holds).
**Gate**: reconciliation from git/tracker/registry only — zero legacy
supervisor commands invoked, no duplicate PR/claim/comment, no destructive
touch of a live worker.

**Stage 5 — overnight.** Prerequisite: a forced-stall alert drill through
the real remote channel. Then one genuinely unattended run that starts
with no attached terminal, spans a substantial interval, **crosses at
least one real boundary** (worker dispatch, token renewal by the broker
mid-run, usage-window stop or bounded continuation, dependency
transition), produces a morning report tied to actual git/tracker state,
and shows no alert gap beyond the documented SLO while the host was awake.
**After this gate only**: dependency audit → delete the old harness →
Linear reconciliation.

## 8. Risks (v2)

1. **Max runaway** (no server cap): reserve gate + parallelism cap +
   §5.3's at-most-twice relaunch bound. Accepted.
2. **`gh` outside the sandbox**: bounded server-side (installation scope,
   branch protections, 1h broker tokens, no key). Accepted and tested,
   not assumed.
3. **TCC under a headless user**: Stage-2's launchd/no-GUI context test is
   the canary; work stays out of TCC-protected locations.
4. **CAO under agent**: unproven until the Stage-2 worker canary; hard
   fallback defined (§3.3).
5. **Watcher/push blind spots**: self-health canary + delivery logging
   narrow them; sleep-during-run is explicitly out of contract on the
   always-on mini.
6. **Spiral regression**: the §0 standing rule + §5.1 prohibitions are the
   tripwire — any interpretation/repair logic appearing in watcher, broker,
   or wrapper is a stop-and-reassess event.

## 9. Cut in v2 (from the review)

CAO-hosted orchestrator option; `includeIf` git config; macOS notification
as a primary alert channel; "re-mint on 401" as a policy; Docker sandbox
exclusions; the claim that run-state/resume/budget prose survives
unchanged.

## 10. v1 → v2 changelog (review findings applied)

1. GitHub App key moved behind a maintainer-owned broker; key
   unreadability is a tested gate (review #1).
2. Run contract written before implementation: run identity, lease, pgid,
   registry, terminal records, socket pinning (review #2).
3. Keep-list corrected to keep/port/delete; run-state, resume, budget, and
   SKILL run-loop prose are ported/rewritten, not "kept" (review #3).
4. CAO made evidence-gated with a real fallback; "CAO stays under your
   user" withdrawn (review #4).
5. Stage-2 gate expanded to negative tests per execution path × launch
   context (review #5).
6. Usage-limit behavior made external and bounded (review #6).
7. Watcher given registry, append-only log, remote-first alerts with
   delivery logging, self-health canary, and hard prohibitions (review #7).
8. Old-harness deletion moved from post-Stage-4 to post-Stage-5 + audit
   (review #8).
9. Branch protections/rulesets added as server-side write control beyond
   installation scope (review #9).
10. Overclaims retracted: "wedge hazard disappears structurally,"
    "heartbeat proves liveness," "gh auth just works."
