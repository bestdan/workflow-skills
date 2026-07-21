# External architecture review — Auto-pilot E-lite v4 decision addendum

## Verdict

**Approve a narrow, disposable measurement spike; do not approve the proposed
control-plane implementation spike as written.**

The new §0a correctly recognizes that another prose-only review round will not
answer the remaining macOS questions. It nevertheless groups empirical facts,
policy choices, and missing protocol components together as though all can be
resolved by implementing `ap-launch`. They cannot. The identity/broker layer
can proceed after its Stage-1 identity dependency is corrected. Production
control-plane implementation remains blocked on a trusted launch-input path,
crash-safe state transitions, worker registration, and atomic continuation
admission.

## Findings

### 1. The spike scope conflates experiments with design work

§0a says no component is missing and describes all remaining work as
crash-safe concurrency semantics. That is inaccurate:

- `ap-launch <repo> <source> [flags]` generates a Claude session UUID, while
  `ap-agent-exec start-session <run_id>` has no defined trusted channel for the
  repository, source, flags, UUID, generation, or launch nonce. A
  maintainer-owned run manifest is a missing interface, not an empirical fact.
- CAO-worker registration is named as open but no authority-safe registration
  mechanism exists.
- The registry lists record types without defining the typed fields and
  transaction boundaries required for launch, takeover, and continuation.
- Clock-skew behavior is a policy decision. A spike can measure clock and
  launchd behavior, but it cannot choose the accepted fail-closed threshold.
- A `mkdir` mutex does not reclaim itself when its owner dies. That behavior is
  known; the design must choose an OS-owned lock or define reclamation.

The safe sequence is: measure substrate behavior in an unprivileged fixture,
fold the results into a complete protocol revision, review that revision, then
implement the privileged control plane.

### 2. The proposed spike has no safety boundary or exit contract

“Time-boxed” has no duration, fixture boundary, credential policy, cleanup
rule, or stop condition. Running a prototype against production sudoers,
`/usr/local/autopilot`, real repositories, and write-capable credentials would
turn the spike into the unreviewed implementation.

The spike should be limited to one working day, use a disposable directory and
test repository, install no production sudoers entries, make no Linear writes,
and use the real Max account only for the read-only cross-user coherence test.
Its deliverables should be measurements and fault-injection results, not a
production launcher. Any inconclusive load-bearing result blocks the next
stage.

### 3. Stage 1 is not separable from the agent identity

The token is published as `maintainer:apagent 0640`, the key-readability gate
must be exercised from the agent's real process path, and the admission canary
is defined as running under the agent identity. However, Stage 2 creates the
`agent` user and `apagent` group, while Stage 1 says its git configuration is
runnable before that user exists.

Move minimal agent-user/group provisioning into Stage 1, or redefine Stage 1
as broker-only and defer all agent-path evidence. The former keeps the claimed
identity-layer separability while testing the real boundary.

### 4. The runtime permissions and macOS primitives are inconsistent

The agent-owned tmux server cannot create `/usr/local/autopilot/tmux.sock`
inside a `maintainer:apagent 0750` directory. A separate agent-owned runtime
directory is required; tmux responses from it remain claims, never
control-plane attestations.

The design also relies on `flock` and `setsid`, neither of which is available
on the reviewed macOS host, while macOS provides `/usr/bin/lockf`. Use an
OS-owned lock whose ownership is released on process death for watcher,
registry, and takeover serialization. Either implement `setsid(2) -> execve`
inside a small pinned run shim or name and gate a pinned dependency; do not
assume the GNU utility exists.

### 5. Pane identity is still not a control-plane fact

The launcher obtains the pane PID from an agent-owned tmux server through a
wrapper running as the agent, then labels the result a fact after confirming
that the PID exists. This proves existence, not that the process is the shim
started for this launch. The start/observe race and shim-to-Claude identity
continuity therefore remain unresolved.

The measurement spike should establish the actual process topology. The
follow-up protocol must then bind a maintainer-owned manifest and prepared
lease to the launched process, define the pre-incarnation state, recheck
liveness before publication, and specify rollback after every injected crash.

### 6. The watcher cannot meet its alert SLO

A 10-minute heartbeat threshold checked every five minutes has a roughly
15-minute worst-case detection delay, before the 60-second pass and 15-second
delivery budgets. Tighten the interval and threshold so the complete
detect-plus-deliver bound is below ten minutes. For example, a two-minute
interval and six-minute threshold leave 45 seconds of margin after the stated
budgets.

### 7. Continuation admission remains non-atomic

Writing `continuation_attempt` before reacquiring the lease allows two jobs to
admit attempts concurrently. The control plane needs one durable reservation
transaction keyed by `{run_id, generation, reset_epoch}` under an OS-owned
lock, followed by an outcome record linked to that reservation. Continuation
must remain disabled until the spike proves that the maintainer and agent
credentials observe the same Max window for the exact test session.

## Required changes before production control-plane implementation

1. Publish a bounded, unprivileged measurement-spike contract and record its
   results.
2. Provision the minimal agent identity no later than Stage 1 and run broker
   tests through that identity.
3. Define the maintainer-owned run manifest and atomic prepared/active/terminal
   launch transaction, including every rollback boundary.
4. Replace crash-persistent mutexes with OS-owned locking and define atomic,
   durable generation replacement.
5. Keep CAO disabled until a registry-bound worker registration and teardown
   protocol has its own evidence gate.
6. Reconcile watcher timing with the ten-minute SLO.
7. Add atomic continuation reservation and prove cross-user usage-window
   coherence before enabling Stage 5 continuation.

The architectural direction remains sound. The next useful input should be
measured substrate behavior, but the measurements must inform the privileged
protocol rather than silently becoming it.
