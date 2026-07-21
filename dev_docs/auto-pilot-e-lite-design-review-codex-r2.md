# External architecture review — Auto-pilot E-lite v2

## Verdict

**Do not approve for Stage-1 implementation yet.**

v2 makes substantial, real design corrections. Its §10 claim that all nine review changes were applied is overstated: five are fully reflected at the design level, four are only partial, and the new broker/run/watcher/continuation contracts introduce unresolved trust and lifecycle failures.

“Applied” below means the proposal now states the requested design change; it does not mean it exists in the repository. None of the proposed broker, entry, watcher, or continuation files exists yet.

## 1. Audit of the first-round ranked changes

| # | Change                                                           | Status                | v2 evidence               | Audit                                                                                                                                                                                                                                                                                                                                                                           |
| - | ---------------------------------------------------------------- | --------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | Separate App private key; make unreadability a Stage-1 assertion | **Partially applied** | §2.1, §7 Stage 1, §10.1   | The key is correctly moved to the maintainer account and the token file is agent-readable rather than key-readable. But §2.1 and §10 claim a Stage-1 unreadability assertion, while §7’s Stage-1 gate does not test it; the negative unreadability test appears only in Stage 2. The privileged on-demand mint path is also unspecified and can defeat the intended narrowness. |
| 2 | Write a replacement run contract                                 | **Applied**           | §4.1, §4.2, §5.1          | v2 now defines run ID, lease, PGID, socket, registry, heartbeat, terminal record, and human stop path. The contract is not yet safe enough to implement; see §3 below.                                                                                                                                                                                                          |
| 3 | Replace, not retain, supervisor-coupled references               | **Applied**           | §0, §6, §9, §10.3         | The three-way keep/port/delete split is explicit and correctly identifies the legacy coupling. Repository verification supports this: `SKILL.md`, `resume.md`, `run-state.md`, `run-budget.md`, and `launch-runtime.md` still invoke `spawn-orchestrator.sh` heartbeat, alarm, doctor, exit-state, and supervisor commands.                                                     |
| 4 | Evidence-gate CAO and define an agent-user fallback              | **Applied**           | §3.3, §7 Stage 2, §10.4   | Correctly withdrawn the “run CAO as maintainer” fallback. This is consistent with the current `scripts/cao-coder.sh`, which only validates inputs then `exec`s `cao-run`; it proves neither UID, HOME, environment, nor sandbox inheritance.                                                                                                                                    |
| 5 | Strengthen Stage 2 with negative tests across execution paths    | **Partially applied** | §7 Stage 2, §10.5         | Path × launch-context testing, sentinels, key unreadability, remote alert, and lease testing are present. The gate omits the previously requested groups, CWD, environment allowlist, and tool-version assertions; those omissions matter for a launchd/headless service and CAO workers.                                                                                       |
| 6 | Define external Max-limit behavior                               | **Applied**           | §5.3, §10.6               | v2 correctly makes Stages 1–4 stop-and-notify and confines automatic continuation to a bounded external parent in Stage 5+. The continuation’s state and watcher interactions remain underspecified.                                                                                                                                                                            |
| 7 | Make watcher independently observable                            | **Applied**           | §5.1, §7 Stage 2, §10.7   | Registry, append-only log, remote-first alerting, deduplication, delivery logging, and a daily canary are stated. The registry authority and watcher failure model are not yet adequate.                                                                                                                                                                                        |
| 8 | Delay old-harness deletion until Stage 5 plus audit              | **Applied**           | §0, §6, §7 Stage 5, §10.8 | Correctly moved. The current dependency surface is large: `scripts/spawn-orchestrator.sh` is 6,323 lines and the referenced skill/runtime documents remain coupled to it.                                                                                                                                                                                                       |
| 9 | Enforce branch protections and minimal App permissions           | **Partially applied** | §2.1, §7 Stage 1, §10.9   | Default-branch protection and a limited permission set are now stated. The design still does not specify that the App has no ruleset bypass, which branches it may push, installation trust separation, or a denial test for forbidden organization-level operations.                                                                                                           |

### Audit of the “cut as unnecessary” list

| Cut                                                  | Status      | v2 evidence    | Audit                                                                                                |
| ---------------------------------------------------- | ----------- | -------------- | ---------------------------------------------------------------------------------------------------- |
| CAO-hosted orchestrator option                       | **Applied** | §1.4, §3.3, §9 | Plain tmux is the only initial session host.                                                         |
| `includeIf` git complexity                           | **Applied** | §2.3, §9       | One agent-owned `.gitconfig` replaces it.                                                            |
| Local notification as primary alert                  | **Applied** | §5.1, §9       | A durable remote push channel is primary.                                                            |
| Automatic re-mint-on-401 rule                        | **Applied** | §2.1, §9       | Blind write retry is removed; reads get one bounded refresh/retry and writes require reconciliation. |
| Docker exclusions/discussion                         | **Applied** | §3.2, §9       | Docker is outside the initial run path.                                                              |
| Claim that run-state/resume/budget survive unchanged | **Applied** | §0, §6, §9     | v2 explicitly retracts it.                                                                           |

## 2. Repository verification

The proposal accurately describes several current constraints:

- `scripts/cao-coder.sh` does not establish containment or identity; it directly `exec`s `cao-run`.
- `scripts/claude-auto-resume.sh` confirms the essential premise of §5.3: an external parent is required after Claude exits at a usage wall. Its current resume behavior uses “most recent conversation in this directory,” which is not a sufficient run identity for the new design.
- `scripts/claude-usage.sh` resolves credentials from the invoking user’s Keychain or `~/.claude/.credentials.json` and writes reset observation under that user’s `~/.claude`; §6 correctly treats agent-user verification/fixing as required.
- The old runtime references are materially coupled to the old supervisor, not merely documented around it.

## 3. Fresh adversarial review of v2’s new components

### 3.1 Token broker (§2.1) — blocking

The key ownership split is necessary, but the broker is not yet a narrow authority boundary.

- “Agent may trigger one on-demand mint (sudo-whitelisted, exact-command rule)” is not enforceable merely by sudo syntax. An agent-controlled process can invoke that command whenever it likes unless the broker itself enforces a per-installation rate limit, a freshness check, and a durable request policy. It turns a one-hour token into effectively continuous App access.
- The broker contract does not say that its App ID, installation ID, token path, log path, executable path, and JWT key path are hard-coded and maintainer-owned. If any are derived from agent-controlled environment, PATH, working directory, arguments, config, or symlinks, the maintainer process becomes a confused deputy.
- Token publication needs an atomic protocol: create a maintainer-owned temporary file, set ownership/mode, fsync, then rename. Writing the live token file in place creates empty or partial-token failures during Git and `gh` operations.
- Parent-directory ownership and traversal permissions are omitted. `0640` on the file is insufficient if `/Users/agent/.autopilot` is agent-writable or if path substitution is possible.
- The Stage-1 admission canary must cover the `gh` GraphQL reads the workflow actually uses. §2.3 says this; §7 Stage 1 does not.
- “No personal credential fallback” needs positive evidence from the invoked credential helpers, not only removal of personal credentials. Git must receive exactly the intended helper response, and `gh` must resolve the intended binary and token source.

### 3.2 Run contract (§4.1) — blocking

The listed fields are useful, but several asserted properties do not follow from them.

- A tmux server and pane processes are not automatically in the entry script’s process group. If `run-entry.sh` creates a detached tmux session, it may exit or become separate from the pane’s process tree. A signal to the recorded PGID therefore does not establish “no orphaned workers,” particularly for CAO-created processes.
- The design must specify process topology and prove it: which process owns the lease, which PID/PGID/session is recorded, whether `setsid` is used, how PID reuse is prevented with start time or process identity, and which descendants are expected to survive or be killed.
- A stale-lock check based only on “recorded PGID is dead” races with concurrent takeover and PID reuse. The lock directory must be atomically created with an immutable owner record, canonical repository identity, process start time, and a takeover protocol that cannot admit two resumers.
- “Terminal-state record on any exit path” cannot hold for `SIGKILL`, power loss, or an entry-wrapper crash. The contract must distinguish a normal terminal record from watcher-observed disappearance; it must not represent the latter as a trusted agent-written terminal event.
- The registry is maintainer-owned, but launch and terminal records are supposedly appended through sudo-whitelisted hooks called from an agent-owned entry script. That is an un-designed privileged API. Either records are untrusted agent claims, or a maintainer-owned launcher creates them from its own process observations. The proposal currently claims both.
- A single fixed tmux socket solves context divergence only if the socket directory, ownership, stale-socket handling, and watcher access are defined. It otherwise creates a single denial-of-service point and an untested cross-UID access problem.

### 3.3 Watcher/registry/alert contract (§5.1) — blocking

The watcher is correctly constrained not to repair, but its observation channel is not yet trustworthy or reliably live.

- A JSONL file is not an authority model. Define record schema, writer identity, append serialization, duplicate/reordered record handling, run incarnation IDs, and whether the watcher may create an observed-abrupt-terminal record. “Append-only” must be implemented, not asserted.
- The agent-owned entry script cannot safely be trusted to invoke a maintainer privilege hook with arbitrary run metadata. Make the launcher and registry writer maintainer-owned, with no agent-controlled executable/configuration paths.
- `StartInterval 300` alone does not protect against a watcher process stuck in DNS, push delivery, or a filesystem call. Every external operation needs a timeout; the launchd job needs an explicit restart policy; and the self-health signal needs a documented missed-canary response.
- A daily canary detects a dead watcher only eventually. That may be acceptable, but it is not “independent observability” for an unattended run unless the SLO explicitly permits up to 24 hours of watcher silence.
- “Delivery succeeded” normally means a push provider accepted a request, not that the maintainer device received it. State that distinction and define retry persistence, retry bound, and alert escalation after provider/network failure.
- The watcher will see an intentionally sleeping Stage-5 continuation parent with a stale heartbeat. Without an explicit, maintainer-owned expected-wait deadline, it will either emit a false stall or must interpret agent-controlled state. The design currently permits neither.

### 3.4 Bounded usage-limit continuation (§5.3) — blocking before Stage 5; fixable after Stage 1

The Stage 1–4 stop-and-notify default is sound. Do not implement overnight continuation until these are designed.

- “Detects the rate-limit exit” is not a reliable interface. The current wrapper checks the independent usage endpoint after any Claude exit; v2 must specify the authoritative evidence, fail-closed behavior when that query fails, and handling for an ordinary model/tool failure that occurs while usage happens to be exhausted.
- The “once per window, twice per run” bounds require maintainer- or wrapper-owned durable state. A parent crash, tmux restart, or manual relaunch must not reset the counter.
- Each relaunch must acquire/revalidate the same run lease, reserve capacity again, use an exact session/run identifier, and write an attempt record. “Continue the most recent conversation” is ambiguous; the existing `claude-auto-resume.sh` uses that ambiguous mechanism.
- Reset-time clock changes, host sleep, reboot, and missing usage data must stop-and-alert rather than silently create extra attempts.
- The wrapper and watcher need an explicit expected-wait contract; otherwise the wrapper’s planned sleep is indistinguishable from a wedged run.

## 4. Remaining required changes

### Blocking before Stage 1

1. Define the broker as a maintainer-owned, fixed-configuration service: no agent-controlled paths or arguments; atomic token publication; ownership/parent-directory requirements; durable issuance/rate-limit state; fixed installation allowlist; and explicit revocation/error alerts.

2. Move the App-key unreadability assertion into the actual Stage-1 gate. Test it from every Stage-1 process path, not only later in Stage 2.

3. Specify and test the GitHub authorization policy: App has no ruleset bypass; direct default-branch push is denied; non-installed repositories and forbidden organization operations are denied; the required GraphQL calls succeed only with the intended App identity.

4. Resolve registry authority. A maintainer-owned launcher/registry writer must create trusted launch records and derive metadata itself. Do not expose a general sudo append hook to an agent-owned script.

5. Replace the PGID assertion with a concrete process-tree contract, including tmux, CAO workers, PID-reuse protection, lease takeover, normal terminal exit, abrupt disappearance, and stop semantics.

6. Define watcher liveness and alert semantics: bounded probe/push timeouts, restart behavior, persisted retries, missed-canary SLO, and the precise meaning of provider acceptance versus delivery.

### Fix during implementation, before the relevant stage gate

- Add groups, CWD, environment allowlist, and tool-version assertions to Stage 2.
- Implement canonical repository lock identities and atomic lease metadata.
- Make broker token refresh and credential-helper behavior part of the real admission canary, including GraphQL.
- Define registry schema/versioning and duplicate/abrupt-exit handling.
- Keep Stage 5 continuation disabled until it has durable counters, exact session identity, fresh reserve checks, expected-wait watcher semantics, and failure-injection tests.
- Make the Stage-5 “substantial interval,” alert SLO, retry count, and canary-miss response numeric rather than qualitative.

## 5. Final decision

**Do not approve for Stage 1.**

Approval can change to **approve with conditions** once the six Stage-1 blockers above are resolved in the design and the revised Stage-1 gate explicitly proves: private-key unreadability, fixed-configuration broker behavior, atomic token publication, no personal credential fallback, required Git/GitHub/GraphQL operations, and server-side denials including a no-bypass default-branch rule.

The delivery loop, dedicated agent user, GitHub App identity, tmux attachment model, and stop-and-notify behavior are sound foundations. The remaining issue is not whether to return to the old harness; it is that the new privileged control plane must be made smaller and more explicit before it is trusted.
