# External architecture review — Auto-pilot E-lite v4

## Verdict

**Do not approve the full control-plane design for Stage-1 implementation.**

The identity/broker portion remains separable and Stage-1-ready. v4 substantially improves all four Round-3 areas, but none is fully closed: the new lifecycle anchor is not atomically bound to the launched process, lease release/takeover remains incomplete, the watcher cannot meet its stated 10-minute SLO with its current timing, and Stage-5 continuation lacks a durable single-attempt admission transaction and proof that the maintainer’s usage view is the agent run’s usage window.

## Round-3 blocker audit

| # | Required blocker | Status | v4 evidence and finding |
| --- | --- | --- | --- |
| 1 | Control-plane-derived lifecycle facts | **Partially resolved** | §4.1 adds fixed wrapper verbs, absolute maintainer-owned paths, `env -i`, validated `run_id`, launcher-observed pane `{pid,starttime}`, and registry-selected stop targets. `.runfile` is correctly demoted to a claim. However, the launcher trusts a response from an agent-owned tmux server to identify the pane, with no atomic binding from `start-session` to the observed PID, no start/observe failure state, and no defined control-plane procedure to register CAO-worker identities. |
| 2 | Atomic generation lease tied to live incarnation; terminal release; concurrent-takeover denial | **Partially resolved** | §4.1 replaces the exited launcher PID with pane incarnation, adds generations and a takeover mutex, and §7 requires live-incarnation and concurrent-takeover drills. But v4 explicitly leaves a lease held after an observed terminal and requires a later takeover/reclamation; that does not satisfy the required observed-terminal release. Mutex-crash recovery, atomic/durable generation replacement, and incomplete-launch recovery are unspecified. |
| 3 | Complete watcher semantics | **Partially resolved** | §4.2 now enumerates the missing record types, §4.3 gives a 10-minute heartbeat threshold and makes heartbeat/tmux non-authoritative outside their own liveness signal, and §5.1 makes skipped-pass reporting realizable. But a 10-minute threshold checked every five minutes cannot deliver a stall alert within ten minutes of the last heartbeat; the worst case is roughly 15 minutes plus probe/delivery time. |
| 4 | Complete Stage-5 continuation contract | **Partially resolved** | §2.3 inventories a maintainer observation credential; §4.1 records a Claude session UUID; §§4.2 and 5.3 name durable continuation records, bounds, and failure drills. Missing are a proof that the maintainer credential observes the *same* Max session window as the agent credential, and an atomic per-window attempt reservation that prevents two continuation paths from both admitting an attempt. |

## Adversarial review of v4 additions

### Launcher-observed pane incarnation

§4.1 has a launch race:

1. `start-session` succeeds.
2. The pane exits, is replaced, or tmux dies before `pane-pid`.
3. The launcher either cannot write an incarnation or reads a different pane PID.

The proposal does not define whether this releases the prepared lease, records an aborted launch, kills a surviving session, or refuses a later takeover. A lease record whose incarnation is written only after observation has an unavoidable pre-incarnation state; it needs an explicit durable state machine and cleanup rule.

The tmux response is also not a control-plane attestation. The tmux server runs as `agent`; an agent process can create a matching session or make a session appear present. The launcher can record what it observed, but it has not established that the returned pane PID belongs to the `run-shim` invocation it just requested. `ps` validation confirms a process exists, not that it is the intended run.

The `setsid → exec claude` topology is underspecified. If the shim is replaced by `exec`, pane PID may indeed remain the Claude PID. But if `setsid` forks because the pane command is already a process-group leader, the pane can exit while a child continues elsewhere. The design must specify and test the actual macOS `setsid` behavior, resulting PID/PGID/session IDs, and what the pane PID represents after each transition.

Finally, `{pid, ps -o lstart}` is stronger than a bare PID but does not prove the stated “PID reuse cannot impersonate” guarantee. `lstart` is wall-clock, second-granularity text, not a kernel-issued process incarnation identifier.

### Generation lease and takeover mutex

The key contradiction is explicit: §4.1 says an observed-terminal lease remains held until a subsequent launch reclaims it, while the required Round-3 fix required control-plane observed-terminal release. This makes normal termination dependent on a later human takeover path and leaves unclear how ordinary `--resume` enters that path.

The mutex is only safe while its creator remains healthy. A crash after creating `takeover.lock` permanently blocks takeover under the stated protocol. Removing it on age alone would recreate the unsafe stale-owner problem. The mutex needs its own owner `{pid,starttime,generation}` and a crash-safe reclamation rule, or an OS-managed lock whose ownership is released on process death.

“Increment generation and replace the owner record” also lacks atomic-write, fsync, and recovery semantics. A crash can leave a new generation with an old owner record, a truncated record, or neither. The concurrent-takeover drill proves only the happy path, not these durability boundaries.

### `ap-agent-exec` verb interface

The narrow verb list is a real improvement, but the verbs are not yet registry-bound:

- `kill-session <run_id>` should require the current registry generation and verify that the target is the registered session/incarnation, rather than derive the session name solely from attacker-influenceable `run_id`.
- `list-sessions` and `pane-pid` return agent-tmux state. They may corroborate, but cannot establish a run, clear a terminal condition, or decide that a lease holder is live.
- `start-session <run_id>` does not explain how the wrapper receives the maintainer-selected repository, worktree, prompt/source, Claude UUID, or flags. A fixed working directory conflicts with `ap-launch <repo> <source> [flags]` unless there is a maintainer-owned run manifest keyed by `run_id`; none is defined.

`attach` gives a maintainer interactive access to an agent process, which is acceptable under the locked operating model. It must not be exposed to the agent user, and it is not a supervision primitive.

### Minimum-validity admission

§2.1 says agent-side helpers refuse a write with less than five minutes remaining, but does not identify the enforcing executable or make that executable unavoidable. An agent can invoke an absolute `git`, `gh`, or HTTP client instead of a PATH shim. This rule is therefore a correctness convention, not a control-plane guarantee, unless all supported write paths are routed through fixed shims and the Stage-1 canary proves that routing.

Both the five-minute calculation and the 50-minute broker-staleness check depend on wall-clock time. The design needs an accepted clock source, a clock-skew policy, and a test covering backward/forward adjustment. A token expiry from the broker log and an mtime from the filesystem are not independently trustworthy clocks.

### Observation credential and Max usage window

A maintainer-readable credential fixes the previous guaranteed fail-closed path, but §2.3 merely asserts “same Max subscription.” The existing usage helper resolves the invoking user’s Keychain or that user’s `~/.claude` credential file, so two macOS users can also hold two distinct OAuth sessions or accounts. [claude-usage.sh](/Users/danielegan/src/workflow-skills/.worktrees/e-lite-review/scripts/claude-usage.sh:89)

The Stage-2 test “maintainer observation credential present and usage query succeeds” proves only that the maintainer can query *a* window. It does not prove that a rate-limit exit in the agent’s exact Claude session yields the same `percent` and `reset_epoch` in the maintainer query. That coherence must be demonstrated before continuation relies on it.

## New internal inconsistencies

- **Socket permissions conflict.** §2.1 gives `/usr/local/autopilot/` directories mode `0750`, `maintainer:apagent`, expressly so the agent can traverse but cannot write. §4.1 then says the agent tmux server creates `/usr/local/autopilot/tmux.sock`. Group mode `r-x` cannot create that socket. A distinct agent-writable socket directory is needed, with its reduced trust explicitly acknowledged.

- **CAO worker lifecycle is undefined.** §4.1 requires registered CAO worker IDs in the lease and says stop targets are registry-bound, but does not define the control-plane registration mechanism. An agent-owned CAO API response cannot be allowed to select kill targets. Worse, `ap-stop` releases the lease immediately if the pane incarnation is gone, without first verifying or stopping registered CAO workers; a live out-of-tree worker can then overlap a new run.

- **The stated registry is a type inventory, not a complete schema.** §4.2 gives every record `run_id`, `generation`, `schema`, `seq`, and writer identity, but omits required typed fields: repository key, lease incarnation, session UUID, CAO-worker incarnations, `reset_epoch`, attempt ordinal, scheduled/actual time, outcome, and linkage to the prior record. “Typed, durable, crash-proof” counters do not follow from record names alone.

- **Continuation admission is ordered incorrectly/ambiguously.** §5.3 says a continuation attempt record is written first and then `ap-launch --resume` reacquires through takeover. Two jobs can write attempts before either has acquired the lease. At-most-once-per-window requires one atomic, durable reservation keyed by `{run_id, generation, reset_epoch}`, made before launching and held through outcome recording.

- **The Stage-2 gates omit the new dangerous transitions.** They test live/dead and concurrent takeover, but not pane death before observation, shim-to-Claude exec/setsid identity continuity, incomplete lease creation, mutex-owner crash, or pane-dead/CAO-worker-live behavior.

The current repository contains no `ap-launch`, `ap-stop`, or `ap-agent-exec` implementation to validate these contracts. It does confirm why v4’s exact-session change is necessary: [claude-auto-resume.sh](/Users/danielegan/src/workflow-skills/.worktrees/e-lite-review/scripts/claude-auto-resume.sh:129) still resumes the directory’s most recent conversation with `--continue`.

## Required blockers before approval

1. Define an atomic launch protocol: prepared lease, fixed-wrapper start, control-plane-bound process identity, post-start liveness recheck, durable launch publication, and rollback/observed-abort behavior. Define CAO-worker registration and require it to be registry-bound before any stop action.

2. Make observed terminal handling release the current-generation lease only after the pane and all registered workers are observed dead/stopped. Define crash-safe mutex ownership/reclamation and atomic, durable owner-generation replacement.

3. Reconcile watcher timing with its SLO: specify the measurement point, lower the heartbeat threshold or tighten cadence so worst-case detection plus alert delivery is within ten minutes, and define alert-state behavior when heartbeat/tmux data changes. Make the five-minute token rule enforced by fixed supported write shims with a clock-skew policy.

4. Before Stage 5, prove cross-user usage-window coherence for the same Max account and add an atomic `{run, generation, reset_epoch}` continuation-admission record with complete typed fields, exact-session verification, and crash/concurrency injection tests.

Until these are addressed, the full control plane is not sound enough to build as the replacement substrate.
