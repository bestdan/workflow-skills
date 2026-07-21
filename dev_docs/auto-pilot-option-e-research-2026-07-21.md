---
title: Option E research — lightweight substrates for the auto-pilot runner
created: 2026-07-21
status: research synthesis — for discussion
context: follow-up to auto-pilot-architecture-review-2026-07-21{,-codex}.md; four parallel web/local research passes on open-source runner patterns, macOS isolation weight, scoped credentials, and cao-server fit. Constraint from the maintainer — "unless it's lightweight, spinning up VMs with a PAT for every worktree sounds very heavy."
related:
  - ./auto-pilot-architecture-review-2026-07-21.md
  - ./auto-pilot-architecture-review-2026-07-21-codex.md
---

# Option E, made lightweight: what the ecosystem actually does (2026-07)

## 0. Reframing the weight concern

Codex's Option E ("disposable runner") does **not** imply a VM per worktree.
The granularity everywhere in the ecosystem is **one boundary per run**;
worktrees stay cheap git-level isolation inside it. And the research shows the
two halves of E decompose independently:

- **Identity (blast-radius cap): needs no VM at all.** Scoped tokens + a
  per-directory git identity are pure configuration.
- **Containment: has a no-VM tier** (vendor-maintained OS sandbox + optional
  separate macOS user) with a microVM tier available later if wanted.

## 1. Identity layer (no VM, server-side enforcement)

| Provider  | Mechanism                                                            | Key facts                                                                                                                                                                                                                                                                                                                    |
| --------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GitHub    | **GitHub App installation token** (preferred)                        | 1-hour TTL, minted on demand by a small script (JWT → `POST /app/installations/{id}/access_tokens`); repo-scoped; not tied to a human user; a leaked token dies in ≤1h. Fewer `gh` CLI surprises than fine-grained PATs (FGPATs still hit GraphQL scope mismatches on `gh pr edit`, labels, reviewers; no Projects support). |
| Anthropic | **Workspace-scoped API key + spend limit**                           | Per-workspace spend caps enforced server-side (Spend Limits API). Caveat: `claude setup-token` OAuth draws on the subscription with **no hard spend cap** — unattended hard-capping requires the API-key path.                                                                                                               |
| Linear    | **Team-scoped API key under a bot account** (or OAuth w/ refresh)    | Personal keys support Read/Write/Create-issues scopes restricted to specific teams; new OAuth apps default to short-lived tokens since Oct 2025.                                                                                                                                                                             |
| git       | **`includeIf "gitdir:~/agent-worktrees/**"`** → `~/.gitconfig-agent` | Bot author identity, scoped credential helper, per-repo deploy key via `core.sshCommand` — the isolation boundary is just "which directory the repo is checked out into." Zero runtime cost.                                                                                                                                 |

The cross-provider pattern: **hand the agent an identity that mints
short-lived tokens, never a durable broad secret.** This layer alone
dissolves most of the open credential backlog (PRE-535, PRE-543,
PRE-546–553): the jailed run no longer needs to reach the personal Keychain
at all — the agent runs with its own env-provisioned, scoped, expiring creds.

## 2. Containment layer — three tiers by weight

**Tier 0 (no VM): Claude Code built-in sandbox (`srt`) + separate macOS user.**

- Anthropic's open-source `sandbox-runtime` is _the same architecture as the
  hand-built jail_ (Seatbelt profiles, proxy-based network allowlist,
  credential masking) but vendor-maintained, versioned, and already powering
  Claude Code's sandboxed Bash (`/sandbox`, `sandbox.*` settings,
  `failIfUnavailable` for unattended fail-closed). Known limits: sandboxes
  Bash only; hostname-level network filtering; `docker`/`gh`/watchman need
  `excludedCommands`.
- A **dedicated macOS user account** (SandVault / Agent Safehouse / macbox
  pattern, active in 2026): own home dir, own Keychain (personal secrets
  kernel-invisible), ACL-scoped access to the agent worktree dir, headless
  `sudo -u agent`. Composes with the built-in sandbox. Zero cold-start,
  low one-time setup.
- macOS `sandbox-exec` CLI is deprecated with no replacement — one more
  reason not to keep hand-building on it.

**Tier 1 (microVM per run, still no Docker Desktop):**

- **Docker Sandboxes (`sbx`, GA Jan 2026)** — one microVM per sandbox via
  Virtualization.framework on Apple Silicon; explicitly marketed for
  unattended Claude Code/Codex with credentials kept out of the agent's
  address space. Young but Docker-backed.
- **microsandbox** — local microVMs on macOS via Hypervisor.framework,
  ~320ms-class cold start; placeholder-credential design (real secret never
  enters the guest, substituted at TLS egress). Younger project.
- One **warm Lima/Colima VM** reused across the run is the traditional
  well-trodden equivalent (boot once, `docker run --rm` per task inside).

**Tier 2 (full VM / cloud):** Tart snapshot-clones, e2b/Modal/Daytona cloud —
all rejected for this use case (heavy or off-box).

Orchestration-layer alternative noted: **dagger/container-use** ships the
per-agent-container + per-agent-branch pattern with MCP wiring, but requires
Docker locally and has no credential story — workflow ergonomics at exactly
the weight the constraint excludes.

## 3. cao-server: session host, not supervisor

CAO (`awslabs/cli-agent-orchestrator`) is confirmed as the upstream. Findings:

- What it is: tmux-session-per-agent + local daemon (REST/MCP/SSE on :9889)
  - cron-style Flows. Today the repo uses it purely as a **coder dispatch
    backend** for the `less-claude` profile (`cao-coder.sh` → `cao-run`).
- What it is not: a supervisor. **No crash-restart, no reboot survival (no
  launchd/systemd unit; a reboot silently kills daemon + all sessions), no
  push alerting (status is poll-only), no credential scoping or sandboxing**
  (tmux-pane isolation, full env inheritance; its `container` config is a
  bring-your-own `docker exec` passthrough).
- The entire local-orchestrator niche (claude-squad, Baton, Emdash, …) punts
  on process supervision the same way.

Verdict: CAO can **host** the orchestrator session (free live `tmux attach`
visibility, uniform poll API already used for workers), but a small external
watcher must still exist above it — and that watcher is the _one_ piece the
Jul-13 analysis called inherent to unattended operation anyway: heartbeat
age → notification, plus keep-alive of the daemon itself (a ~dozen-line
launchd `KeepAlive` job, not a 6,000-line state machine).

## 4. The proposed stack (E-lite)

| Layer                 | Today (failing)                                                    | Proposed                                                                                                                               | Weight      |
| --------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| Blast radius          | Seatbelt egress rules (never actually on) + personal creds in-jail | GitHub App installation tokens, Anthropic workspace key + spend cap, Linear bot key, `includeIf` git identity on `~/agent-worktrees/`  | config only |
| Containment           | Hand-rendered Seatbelt profile (6.3k-line renderer)                | Claude Code native sandbox (`failIfUnavailable`) + dedicated `agent` macOS user                                                        | no VM       |
| Scheduling/run loop   | launchd `StartInterval` wake + generated bash wrapper              | Long-lived orchestrator session (optionally hosted in CAO); native scheduling primitives validated by canary before overnight reliance | none        |
| Supervision           | supervisor/doctor/alarm subcommands of spawn-orchestrator.sh       | External watcher: launchd `KeepAlive` for the daemon + heartbeat-staleness → notification (~50 lines)                                  | tiny        |
| Per-task isolation    | jail + hand-rolled worktrees                                       | git worktrees under the agent dir (unchanged); CAO workers as today                                                                    | none        |
| Hard-boundary upgrade | —                                                                  | Docker Sandboxes (`sbx`) microVM per **run**, only if Tier 0 proves insufficient                                                       | deferred    |

What this deletes or obsoletes: Seatbelt/plist rendering and the exec-grant
machinery, the egress renderer, the credential-forwarding plan
(PRE-546–553), the four Jul-15 launch blockers' fix stream (fresh clone
under the agent user + neutralized `hooksPath` in the agent gitconfig kills
them at the root), and most of the supervisor state machine. What survives:
delivery-loop prose, run-state ledger, deterministic scan scripts, doctor
invariants recast as reconciliation, preflight recast as an admission canary
that executes the real path (commit, push, PR, tracker write) as the agent
identity.

## 5. Open decisions

1. **Billing mode for unattended runs**: subscription OAuth (no hard spend
   cap; cheap if already on Max) vs workspace API key (server-enforced cap).
   Hybrid possible: subscription for attended, API-capped for overnight.
2. **Separate macOS user**: adopt now (real Keychain/filesystem boundary,
   small one-time cost) or start with native sandbox only?
3. **GitHub App vs fine-grained PAT**: App is strictly better but needs
   one-time app creation + a token-mint script; FGPAT is a 10-minute stopgap.
4. **Orchestrator session host**: plain detached `claude` session vs
   CAO-hosted (visibility for free, one more daemon in the loop).
5. **Whether to canary `sbx`** now as the Tier-1 option or defer until Tier 0
   shows a gap.
