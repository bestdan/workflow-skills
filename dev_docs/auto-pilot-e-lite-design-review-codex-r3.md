# External architecture review — Auto-pilot E-lite v3

## Verdict

**Do not approve for Stage-1 implementation yet.**

v3 fully resolves the first four prior blockers. The former process-tree and watcher blockers are substantially improved but remain **partial**: their specified control paths still let agent-controlled state select control-plane actions, and the lease/alert contracts are internally incomplete.

## 1. Prior blocker audit

| # | Prior blocker                                                           | Status                 | v3 evidence and finding                                                                                                                                                                                                                                                                                                                                                                                                      |
| - | ----------------------------------------------------------------------- | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | Fixed-configuration broker; atomic publication; no agent-invokable mint | **Resolved**           | §2.1 hard-codes authority/path constants, rejects arguments/environment/symlinks, uses same-directory temp + fsync + rename, and forbids on-demand mint. §7 Stage 1 tests these properties and no-fallback behavior.                                                                                                                                                                                                         |
| 2 | App-key unreadability in Stage 1                                        | **Resolved**           | §2.3 makes the key maintainer-owned and agent-unreadable; §7 Stage 1 explicitly requires unreadability from every Stage-1 process path. The gate should say “every non-broker path,” since the broker necessarily reads the key.                                                                                                                                                                                             |
| 3 | Explicit GitHub authorization policy and denial tests                   | **Resolved**           | §2.2 specifies an empty ruleset bypass list, branch restrictions, installation scope, and all required denials; §7 Stage 1 makes them gate evidence.                                                                                                                                                                                                                                                                         |
| 4 | Registry authority; no sudo append hook                                 | **Resolved**           | §4 moves launch/stop/registry writes to maintainer-owned components; §4.2 constrains writers to those components and gives serialization/schema rules. This removes the agent-invokable privileged append path.                                                                                                                                                                                                              |
| 5 | Concrete process-tree contract replacing the PGID assertion             | **Partially resolved** | §4.1 now defines tmux, `setsid`, PID+start-time identities, CAO’s out-of-tree workers, stop ordering, and terminal-record types. But `ap-stop` uses the agent-written `.runfile` to select the process group to signal (§4.1), making an agent claim authoritative for a destructive control-plane action. The lease owner is the short-lived launcher PID, so takeover will regard it as dead after a normal launch (§4.1). |
| 6 | Watcher liveness/alert semantics with timeouts and numeric SLOs         | **Partially resolved** | §5.1 adds bounded probe/push/pass timeouts, retries, a 10-minute awake-host SLO, and a daily canary. But heartbeat freshness has no numeric threshold, and a fresh agent-written heartbeat/session can suppress the stall condition. The documented registry schema also omits records the watcher is later required to write.                                                                                               |

## 2. Adversarial findings

### One-way trust rule is not yet enforced

The rule in §0 is correct, but §4–§5 violate it in actionable places.

- `ap-stop` reads the agent-owned `.runfile`, validates the claimed PID/start-time against a live process, then signals the claimed process group (§4.1). That validation proves only that _some_ agent process exists; it does not prove it is this run’s shim. A compromised run can point the file at the tmux server or another agent process and steer a maintainer stop command.

- The watcher treats both agent-owned tmux state and `.heartbeat` as liveness inputs (§4.3, §5.1). An agent can keep touching a heartbeat or create/retain a matching tmux session while useful work is wedged. These may be corroborating observations, but must not suppress a stall alert or establish a terminal state.

- `ap-launch` invokes `tmux` and `run-shim` through `sudo -iu agent` (§4.1), but does not specify maintainer-owned absolute executable paths, a scrubbed environment, a fixed working directory, or a validated `run_id`. Unlike the broker, the lifecycle command boundary remains dependent on agent login/PATH resolution. The sudo “exec wrapper” also needs a narrow, fixed verb/interface rather than a generic agent-tmux command runner.

- Marking `claimed_exit` as a claim is good (§4.1), but the design must state that it never releases a lease, suppresses an alert, or authorizes resume/cleanup. Only a control-plane-bound process observation may do so.

### Lease/takeover protocol is unsafe as written

The owner record stores `launcher pid+starttime` (§4.1). `ap-launch` normally exits immediately after creating the detached tmux session, so `--take-over` will see the recorded incarnation as dead while the run is live. The stated safety conclusion therefore does not follow.

Further, two concurrent takeovers can both observe the old owner as dead. A remove-and-recreate sequence around `mkdir` is not a compare-and-swap; one contender can remove the other’s newly created lock. The design also does not state who releases a lease after watcher-observed normal termination: the watcher is forbidden from clearing state (§5.1), while release is specified only in `ap-stop` (§4.1).

Use a maintainer-owned generation/epoch record, an exclusive takeover mutex, and a control-plane-derived run incarnation. Takeover must verify that incarnation and all registered CAO workers—not the exited launcher.

### Broker refresh leaves an unbounded staleness window

“No on-demand mint” is the right decision. However, “the published token always has ≥15 minutes” is false after a scheduled refresh fails: the old token can have less than 15 minutes left and eventually expire (§2.1).

Reading the token per operation avoids stale shell environments, but does not make a long push/reconnect safe near expiry. Define:

- a broker-health/last-success deadline checked independently by the watcher;
- when broker failure becomes an alert before token expiry;
- the minimum remaining-validity admission rule, if any; and
- reconciliation behavior for a long write that loses authentication or transport after starting.

The existing usage script’s user-scoped credential lookup confirms a related problem for §5.3: it reads the invoking user’s Keychain or `~/.claude/.credentials.json` ([`claude-usage.sh`](/Users/danielegan/src/workflow-skills/.worktrees/e-lite-review/scripts/claude-usage.sh:90)). v3 inventories Claude OAuth only under the agent account (§2.3), but requires the maintainer watcher to query the same subscription (§5.3). The watcher therefore needs a separately inventoried maintainer-readable observation credential, or the continuation path will always fail closed.

### Usage-limit continuation is not yet implementable

The evidence rule and Stage 1–4 stop-and-notify behavior are sound (§5.3). Stage 5 still needs:

- a specified, maintainer-owned mapping from `run_id` to an exact Claude conversation/session identifier. “Exact run identity” is asserted but not defined. The current implementation resumes the directory’s “most recent” conversation ([`claude-auto-resume.sh`](/Users/danielegan/src/workflow-skills/.worktrees/e-lite-review/scripts/claude-auto-resume.sh:129)), which v3 correctly rejects.
- an exact exhausted-window predicate and maintainer-side credential/access test;
- registry schema entries for `usage_limit`, `expected_resume`, continuation attempts, and counters;
- a rule that a fresh agent heartbeat/session cannot negate a control-plane `expected_resume` or an independently detected failure.

## 3. New consistency issues

- §4.2 lists only `launch`, `observed_terminal`, `claimed_exit`, `alert`, and `canary`, but §5 requires `watcher_slow`, `alert_failed`, `usage_limit`, and `expected_resume` records. The schema is incomplete.

- “Two consecutive skipped-by-flock passes append a `watcher_slow` record” (§5.1) is not operationally defined: a skipped pass lacks the lock. Specify an independent bounded status mechanism or state that the next successful pass records the missed intervals.

- The v3 related-document list references missing `dev_docs/auto-pilot-problem-statement.md`.

- §6 calls itself a “three-way split” but does not enumerate the keep/port/delete sets. For a standalone specification, retain the explicit lists rather than only naming the old dependency audit.

## 4. Required blockers

1. Make lifecycle facts control-plane-derived: fixed maintainer-owned launcher/shim/wrapper paths; scrubbed environment; registry-bound process/CAO identities; agent files and tmux responses may only corroborate, never select a kill target or suppress a condition.

2. Replace the lease record and takeover procedure with an atomic generation-based protocol tied to the live run incarnation, including normal observed-terminal lease release and concurrent-takeover denial tests.

3. Complete watcher semantics: numeric heartbeat-staleness threshold consistent with the 10-minute SLO; explicit treatment of fresh heartbeat/tmux data as non-authoritative; complete record schema; and a realizable skipped-pass health record.

4. Complete the Stage-5 continuation contract before it is implemented: maintainer-readable usage-query credential, exact conversation identity, durable typed attempt records/counters, and failure-injection evidence.

The identity/broker portion may be developed in isolation, but the v3 control-plane design should not be accepted as the Stage-1 foundation until these former process-tree and watcher blockers are fully closed.
