# External architecture review — Auto-pilot E-lite

## Overall verdict

**Directionally sound; not yet safe to approve as the replacement substrate.**

E-lite correctly removes the main failure generator: a hand-authored Seatbelt profile, credential forwarding, and a wake-driven Bash supervisor trying to model arbitrary personal-host state. A dedicated non-admin macOS user, fresh clones, and server-scoped GitHub App credentials genuinely shrink the compatibility state space.

It does **not** yet establish a complete operational boundary. The proposal understates three new load-bearing components:

1. The GitHub App private key plus token-mint path is still a credential broker.
2. `tmux` is session persistence, not supervision, cancellation, or recovery.
3. The retained run-state/resume design is tightly coupled to the old supervisor and cannot be declared “kept unchanged” while deleting `spawn-orchestrator.sh`.

The design recreates the old failure pattern if the heartbeat watcher, token refresh logic, and resume semantics accrete special-case interpretation and repair behavior. It must remain a small external observer with explicit, tested contracts—not become `spawn-orchestrator.sh` v2.

Do not delete the old harness after Stage 4. Delete it only after the replacement has passed recovery and overnight evidence, its retained references no longer invoke legacy supervisor commands, and the fallback has been deliberately retired.

## Evidence from the current repository

The diagnosis is supported by the implementation:

- `scripts/spawn-orchestrator.sh` owns rendering, sandboxing, launchd lifecycle, alarms, heartbeat, exit classification, pauses, verification brokering, status reporting, doctor repairs, restacking, and resume support.
- The current generated launcher wraps a synchronous `claude -p` call in `sandbox-exec`; `StartInterval` cannot provide independent supervision while that process remains blocked.
- `skills/auto-pilot/SKILL.md` and its run-state references directly invoke legacy supervisor functions for heartbeat, exit reasons, alarms, doctor, and resume.
- `scripts/cao-coder.sh` only validates inputs and executes `cao-run`. It does not establish a sandbox, sanitize environment, or assert UID/HOME; CAO execution under the agent user is therefore an unproven assumption, not an inherited property.
- `scripts/claude-auto-resume.sh` already demonstrates that rate-limit recovery requires an external parent process when Claude exits. E-lite does not say whether that behavior survives.
- `scripts/claude-usage.sh` reads the current user’s Keychain or `~/.claude/.credentials.json`; it must be tested and, if necessary, redesigned for the agent-user credential model.

## 1. Identity layer

### Right

- A GitHub App installation token is materially better than personal Keychain-backed GitHub authentication. Repository installation scope and short token TTL are real blast-radius controls.
- Fresh agent-owned clones remove the shared-worktree/gitdir failure class.
- A separate git config with `core.hooksPath = /dev/null` removes accidental inheritance of personal hooks and credential helpers.
- A separate Linear bot identity is the correct direction.

### Wrong or underspecified

- The App private key is a durable, high-value credential. If the Claude process runs as `agent`, it can normally read every file readable by `agent`, including that key. A compromised or prompt-injected run can then mint replacement tokens indefinitely. A one-hour installation token does not bound compromise if its minting key is in the same principal.
- “Re-mint on 401/expiry” is not a design. A 401 can mean expiry, wrong installation, revoked authorization, an unsupported endpoint, or an operation whose outcome is unknown after a network failure. Blindly retrying writes risks duplicate comments, claims, or PR mutations.
- The proposed `gh auth git-credential` plus `GH_TOKEN` behavior is asserted, not demonstrated. Git credential-helper behavior and `gh` environment-token behavior must be validated through actual clone, fetch, push, PR, review, and GraphQL operations.
- `Contents: RW` on every installed repository is broader than “PR creation.” The proposal does not specify default-branch protections, allowed branch prefixes, whether the App bypasses rulesets, or how repository installations are separated by trust level.
- “Linear key scoped to relevant teams” needs a concrete authorization matrix. The bot’s workspace role, team membership, issue-write rights, and token rotation/revocation path are not specified.
- The durable Claude OAuth credential remains an agent-user bearer secret. That is acceptable under the locked subscription decision, but it must be explicitly in the threat model rather than treated as eliminated.

### Missing

- A small token broker with a distinct owner from the model process. The broker should hold the App private key, mint only for a configured installation, return short-lived tokens, log issuance metadata, and never expose the private key to `agent`.
- A bounded token-refresh protocol: refresh before expiry where practical; on an authenticated read failure, re-mint once and retry only an idempotent operation; for writes with unknown outcome, reconcile GitHub/Linear state before retrying.
- A credential inventory: owner, storage location, who can read it, scope, TTL, rotation method, and revocation test.
- Branch protection/ruleset policy that prevents direct default-branch writes even if the App token is exposed.

## 2. Containment layer

### Right

- A non-admin `agent` user is the strongest lightweight improvement in the proposal. It makes personal home-directory permissions, personal Keychain items, SSH material, browser data, and dotfiles unavailable by normal UID access rather than by a profile exception list.
- Keeping work under `/Users/agent/work` avoids many TCC-protected-location surprises.
- Native Claude sandboxing in fail-closed mode is a reasonable defense-in-depth layer when it is treated as such.

### Wrong or underspecified

- The agent user protects the maintainer’s credentials, not the agent’s own credentials. The latter include the Claude OAuth bearer token, Linear key, GitHub installation token, potentially the App private key, CAO state, and tmux sockets.
- An excluded `gh` command is not a minor accommodation. It is a credential-bearing binary outside the native sandbox wall. If exclusion removes filesystem and/or network policy for that process, it can read the agent home, use inherited tokens, access local services, and make arbitrary GitHub calls within App authority.
- “Every worker runs sandboxed” is not established. Current CAO dispatch uses `cao-run`; no repository code proves that CAO-created workers receive Claude sandbox settings, preserve the agent UID, use the intended HOME, or avoid inherited personal environment.
- A native sandbox startup success does not prove the effective policy. `failIfUnavailable` only helps if the command path, worker type, and excluded-command behavior are all part of the tested runtime.
- “Homebrew is world-readable, so no reinstalling” is insufficient. Readability does not establish executable availability, PATH, write access to caches, compatible Node/Python/tool versions, code-signing state, or safe updates under a non-admin service account.

### Missing

- A documented effective-access test matrix for the orchestrator and every worker type, including normal Bash, excluded `gh`, CAO workers, git hooks, interpreters, and verification commands.
- Explicit agent-home hardening: ownership and modes for secret files, dedicated temp/cache directories, no shared writable directories, no personal-home ACL leakage, and no App private key readable by the model process.
- An explicit network posture for excluded commands. If `gh` must be excluded, treat its network access as broad GitHub App authority and test that assumption rather than describing the sandbox as the network boundary.
- A defined fallback posture if CAO cannot run correctly as `agent`. “Leave CAO under the maintainer user” is not an implementation fallback; it abandons the core identity boundary.

## 3. Session host and run loop

### Right

- A long-lived tmux session removes the specific `StartInterval`-versus-long-model-call mismatch.
- tmux is appropriate for the stated SSH attach/detach workflow.
- Keeping the orchestrator plain tmux initially is preferable to making CAO both a worker backend and session host before its behavior is understood.

### Wrong or underspecified

- The claim that the no-timeout hazard “disappears structurally” is too strong. A long-lived Claude process can still wedge forever, ignore its own deadline, block on a worker, exhaust its usage window, or leave orphaned descendants. The failure changes from “next wake never occurs” to “human intervention depends entirely on watcher correctness.”
- tmux does not provide reboot recovery, a deadline, process-tree cancellation, rate-limit recovery, or an atomic singleton lease.
- A session name such as `autopilot` is not a run identity. It creates collision and stale-session ambiguity across launches and resumes.
- `sudo -u agent tmux …` is not a sufficient service-launch contract. It may inherit or resolve the wrong HOME/PATH/TMPDIR; the tmux socket location can differ between interactive SSH, sudo, and launchd contexts.
- The CAO launchd unit is underspecified: user-domain versus system-domain bootstrap, UID, HOME, PATH, working directory, environment-file loading, and restart behavior all affect whether CAO and tmux see the same state.
- The design retains Max-window pause semantics “as prose/config,” but a process killed at a usage limit cannot execute that prose. The existing `claude-auto-resume.sh` is an external process for precisely this reason.

### Missing

- A run-specific entry contract: run ID, tmux socket directory, session name, PID/process-group identity, command fingerprint, work root, environment-file path, and durable launch record.
- A small external deadline policy. It need not classify every failure, but it must define whether a stale run is merely notified, gracefully interrupted, or force-killed after a human-configurable grace period.
- A decision on Max-limit behavior: stop-and-notify, or a bounded external resume wrapper. Either is valid; an implicit model-driven resume is not.
- A real CAO process-tree smoke test that records UID, HOME, CWD, environment allowlist, worker PID, and effective sandbox behavior.

## 4. Supervision

### Right

- Reducing supervision to independent liveness observation and notification is the right architectural boundary.
- A reboot being a stop-and-human-resume event is a defensible operational choice.
- Refusing to rebuild exit-code classification and automatic repair is wise.

### Wrong or underspecified

- The heartbeat file is agent-writable. It can prove recent file modification, not useful progress or healthy child execution. A blocked orchestrator can fail to beat it; a confused or compromised one can keep touching it indefinitely.
- A watcher that discovers runs by globbing `/Users/agent/work/<run>` has no trustworthy run registry, completion signal, or stale-run ownership model.
- The maintainer-user watcher may not be able to inspect the agent user’s tmux socket without an exact, noninteractive `sudo` rule. Conversely, a broad sudo rule becomes another privilege boundary to review.
- A macOS notification is not a reliable remote alert from a headless/SSH context. Pushover/ntfy delivery can also fail during network loss, sleep, DNS failure, or provider outage.
- Launchd `StartInterval` is not a strict ten-minute timer across sleep and reboot. “Notification within 10 minutes” needs an awake-host assumption or a wider, explicit SLO.
- The proposal has no watcher self-health signal. A dead watcher and dead orchestrator are silent together.

### Missing

- A watcher-owned immutable run registry and append-only watcher log outside agent write access.
- Independent checks of: process-group liveness, tmux session existence, heartbeat freshness, run terminal state, and watcher delivery result. None alone should authorize recovery.
- An explicit alert contract: recipient, transport, retry policy, deduplication key, delivery timeout, local audit record, and what counts as failed delivery.
- A watcher self-test and recurring canary alert.
- A rule that the watcher never edits `RUN.md`, re-mints credentials, clears alarms, or resumes a run. Those are the boundaries preventing a new supervisor state machine.

## 5. Keep/delete list

### Right

- Delete the hand-rendered Seatbelt profiles, egress renderer, exec grants, generated launcher, and unjailed verify broker once replacement evidence exists.
- Keep delivery lifecycle semantics, deterministic graph/scanning tools, validation, and reconciliation concepts.

### Wrong or underspecified

“Kept unchanged” is inaccurate. The current retained materials depend on the deleted harness:

- `SKILL.md` invokes `spawn-orchestrator.sh` for heartbeat, exit reasons, alarms, and doctor.
- `references/resume.md` invokes legacy alarm clearing, exit-state clearing, and doctor repair.
- `references/run-state.md` defines supervisor-owned pause, no-progress, exit, and alarm semantics.
- `references/run-budget.md` assumes a relaunching external supervisor.
- `preflight.sh` is designed around the old wrapper and cannot simply become a canary by renaming it.

Keeping these unchanged either leaves dead references or forces a feature-for-feature reimplementation of the old supervisor.

### Missing

Split retained material into three groups:

1. **Keep as-is:** task graph, delivery lifecycle, adapters, validation, claim scanning, plan graphing.
2. **Port as a small independent library:** read-only reconciliation against Git/tracker, run identity/locking, actual-path admission canary, and a minimal durable run report.
3. **Delete, do not port:** supervisor pause ledger, automatic relaunch classification, in-jail alarm requests, verify broker, wrapper-specific doctor repair, and launchd-specific terminal-state machinery.

## 6. Migration stages and gates

The staged approach is correct, but the gates are not yet sufficient.

### Stage 1 — identity

The proposed canary is necessary but too weak.

Add proof that:

- the minting key is not readable by the model process;
- token issuance works with clock skew, expired token, revoked installation, and missing key conditions;
- the exact git credential path performs clone, fetch, push, PR create, review/comment, and required GraphQL reads;
- a direct default-branch push is denied;
- an uninstalled repository and a forbidden organization operation are denied;
- no personal `gh` login, git credential helper, or Keychain fallback was used.

### Stage 2 — agent user, sandbox, tmux

The security assertion is far too narrow. “Cannot read my Keychain and `~/.ssh`” proves only two paths for one process type.

Require the canary to run with the exact production launch path—both interactive SSH and launchd/no-GUI contexts—and assert:

- UID, groups, HOME, PATH, TMPDIR, CWD, tmux socket location, and tool versions;
- inability to read sentinels in the maintainer home, personal Keychain, SSH directory, cloud credentials, and personal git configuration;
- inability of the model process to read the App private key;
- effective network/filesystem behavior for normal Bash, excluded `gh`, and each CAO worker backend;
- CAO server and worker processes actually run as `agent`;
- remote attach works after SSH disconnect and from a fresh SSH session;
- a watcher notification is received through the remote transport, not merely locally emitted.

### Stage 3 — first real task

Add an independent verification requirement: CI or a separately invoked verifier must validate the exact pushed SHA. “Normal agent environment, no verify broker” is not itself evidence that verification worked.

Also test a forced worker stall while the orchestrator waits on it, not merely an orchestrator process kill.

### Stage 4 — recovery drill

Killing one worker is inadequate. Exercise:

- `SIGKILL` of the orchestrator;
- crash between each durable transition: push, tracker update, run-state update;
- expired GitHub token during a read and during a write with unknown outcome;
- Max usage-limit exit;
- stale tmux session/socket;
- reboot or at least boot-time recovery notification;
- repeated `--resume` attempts and concurrent resume attempts.

The gate should require reconciliation from Git/tracker evidence without legacy supervisor commands, no duplicate claims/PRs/comments, and no destructive cleanup of a live worker.

### Stage 5 — overnight

The current gate is too weak. “A mid-run failure that produced a push notification” can pass after five minutes and proves neither overnight liveness nor progress.

Require one unattended run that:

- starts without an attached terminal;
- remains observable for a substantial expected run interval;
- crosses at least one meaningful boundary: worker dispatch, token renewal, usage-window pause/stop, or dependency transition;
- produces a morning report tied to actual Git/tracker state;
- has no alert gap longer than the documented threshold while the host is awake;
- receives a forced-stall alert through the real remote channel.

A failure-alert-only drill should be a prerequisite, not an alternative pass condition.

## Specific unhandled failure modes

- The mint script fails halfway through a run because of clock skew, bad JWT, revoked installation, changed installation ID, unreadable key, or network failure; a generic 401 retry either loops or repeats a write with unknown outcome.
- `GH_TOKEN` is exported only into the original shell. Re-minting in a child does not update already-running parent or sibling environments.
- The GitHub App private key is accessible to the same agent that it is intended to constrain.
- `gh` is excluded from native sandboxing and becomes a credential-bearing escape from the claimed containment boundary.
- CAO workers inherit an unexpected HOME, PATH, keychain access, or UID, or do not receive native sandbox settings at all.
- `sudo -u agent` launches a non-login context; `sudo -iu agent` or a dedicated launch wrapper may be required to set target HOME and a deterministic environment.
- tmux uses a different socket directory in launchd versus SSH; attach and watcher checks then report false absence or create parallel servers.
- A system-domain launchd service running as `agent` is not equivalent to an agent GUI/user-domain service for TCC, Keychain, notifications, or bootstrap environment.
- Homebrew binaries are readable but agent-side caches, global package paths, updates, compilers, or transitive tools are unwritable or absent.
- TCC produces a prompt no headless user can answer; the process blocks or fails without a useful notification.
- The watcher accepts a fresh heartbeat while an orphaned worker is still looping, or emits a false stall while a legitimate worker is doing a long operation without an orchestrator heartbeat.
- The watcher itself dies, the host sleeps, or remote notification delivery fails; there is no independent indication that observation is still operating.
- A Claude rate-limit exit ends the tmux command. Without an external wrapper or explicit human intervention contract, “pause and resume” is only aspirational.
- `--resume` uses legacy doctor/alarm/exit-state machinery that has been deleted, or a replacement quietly grows the same repair and state-classification complexity.
- Two SSH operators attach or one launches a second tmux session while a stale PID check races; the run-state branch and worktrees are mutated concurrently.

## Ranked changes

1. **Separate the GitHub App private key from the model process.** Build a narrow, distinct-owner token broker and make private-key unreadability a Stage-1 assertion.
2. **Write the replacement run contract before implementation.** Define run identity, singleton lock, process group, heartbeat semantics, terminal-state record, watcher registry, alert transport, and manual resume procedure.
3. **Replace—not retain—the supervisor-coupled run-state and resume references.** Port only read-only reconciliation and explicitly remove legacy alarm, pause, relaunch, and doctor-repair semantics.
4. **Make CAO an evidence-gated worker backend.** Do not claim agent-user or native-sandbox coverage until an end-to-end CAO worker canary proves UID, HOME, credentials, CWD, and sandbox behavior.
5. **Strengthen Stage 2 with negative security tests for every execution path.** Test orchestrator, excluded commands, and workers; test no-GUI launchd and SSH attach separately.
6. **Define Max-rate-limit behavior externally.** Either stop-and-alert or use a bounded external continuation wrapper; do not rely on a killed Claude session to resume itself.
7. **Make the watcher independently observable.** Use a watcher-owned registry/log, remote alert delivery evidence, deduplication, and a self-health canary.
8. **Move old-harness deletion after Stage 5 and a clean dependency audit.** Preserve it only as an explicitly unsupported fallback until then.
9. **Enforce GitHub branch protections and minimal App permissions.** Installation scope alone is not sufficient write control.

## Cut as unnecessary

- The “CAO-hosted orchestrator versus plain tmux” choice. Start with plain tmux; revisit only after a demonstrated operational benefit.
- `includeIf` complexity for the agent account. A dedicated agent home can use one minimal `.gitconfig`.
- macOS local notification as a primary alert channel. Keep one durable remote push channel with delivery logging; local notification is optional.
- Any automatic “re-mint on 401” rule. Replace it with bounded refresh plus reconciliation.
- Docker exclusions and related discussion in the first implementation unless Docker is actually part of the run path.
- The claim that the retained run-state, resume prose, and budget machinery survive unchanged. They do not; state the smaller replacement explicitly.
