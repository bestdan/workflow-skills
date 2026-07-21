# External architecture review — Auto-pilot

## Verdict

The delivery lifecycle is sound enough to preserve. The current substrate is not.

I would reject continued investment in the hand-built “Seatbelt jail + launchd interval wake + Bash supervisor” as the default way to run `bypassPermissions` unattended on arbitrary personal macOS hosts. This is not because Seatbelt, launchd, or Bash are individually invalid technologies. It is because this composition makes the product depend on an open-ended, host-specific compatibility contract while simultaneously claiming to be the security boundary. That is the failure generator.

The report is right that the last failures are substrate failures, not failures of task selection, implementation, co-review, freeze, or hand-off. It is also right that the blocked worker cannot supervise itself. The generated launcher runs a blocking `sandbox-exec … claude -p …` call with no model-call timeout; while it is blocked, launchd does not start another interval instance. The in-wake reporter improves visibility but does not make the supervisor able to recover or alert on a hung worker.

The report overstates three points:

- The evidence rejects this architecture as a general-purpose host harness with acceptable economics; it does not mathematically prove that a fixed, tightly standardized host could never be hardened.
- A VM/container does not make credentials, egress, or verification disappear. A repo-scoped token limits GitHub authority, not local-file access or arbitrary outbound exfiltration. The runner still needs an explicit threat model and a clean credential boundary.
- “Claude-native” scheduling, sandboxing, hooks, background tasks, and session persistence are asserted but not demonstrated here. They must be treated as capabilities to validate, not as a replacement architecture already proven sufficient.

## What I verified

The code supports the architectural diagnosis.

- `scripts/spawn-orchestrator.sh` is 6,323 lines and is a single multiplexer for rendering, launch, supervision, alarms, verification, status, doctor, and restacking. The non-test shell/Python scripts total 9,951 lines.
- `orchestrator.plist.tmpl` uses `StartInterval`; the generated launch wrapper invokes the model synchronously. It does not wrap the `claude -p` invocation in its existing bounded-call helper.
- The Seatbelt profile is default-deny but necessarily grants broad reads, outbound network class access, expanding executable paths, Claude runtime write exceptions, and host-specific path exceptions. That is evidence of an increasingly permissive compatibility layer, not a stable least-privilege boundary.
- The unjailed verify broker is real and deliberately exists because verification could not run reliably inside the jail. That is a pragmatic repair, but it confirms that the original confinement boundary is not composable for the product’s normal toolchain.
- `preflight.sh` authenticates `gh` and coder probes outside the generated detached wrapper, then mainly checks profile rendering, a simple jailed shell command, denied `$HOME` writes, and whether an egress allowlist JSON renders. It does not prove the exact detached wrapper can authenticate to GitHub, Linear, Claude usage, or the selected credential helper. The reported “green-but-dead-at-3am” failure is therefore consistent with the implementation.

I could not independently verify the historical run counts, Linear issue counts, PR counts, exact incidents, or the claimed availability and semantics of Claude-native scheduling/workflow primitives. Those claims need run logs, tracker access, and vendor documentation or canaries.

## Explicit answers to the six questions

### 1. Is the hand-built-jail premise falsified?

Yes, as the default architecture for this product and operating environment.

The relevant standard is not “can one more fix make one host work?” It is “can a maintainer add a new repo, linked worktree, hook, interpreter, credential helper, or coder without reopening the security and availability design?” The observed answer is no. The harness must discover and encode arbitrary host state, and each compatibility grant weakens or complicates the boundary it is supposed to enforce.

The focused hardening push is justified only as a short-lived compatibility fallback, not as the chosen path forward.

The discriminating evidence would be a representative canary matrix, run through the exact detached runtime:

1. Linked and ordinary worktrees.
2. A repository hook and a project-local interpreter.
3. The actual GitHub, tracker, model, and usage credentials.
4. Commit, push, PR creation, verification, and co-review.
5. A deliberate hung worker, expired credential, and restart.

Require repeated clean runs across that matrix without new per-host profile exceptions. Failure means stop hardening rather than file another compatibility task.

### 2. Ranking

1. **E — recommended:** identity-first disposable-runner hybrid.
2. **C — rented boundary.**
3. **D — identity-first hybrid, revised to make native scheduling optional rather than assumed.**
4. **B — Claude-native rewrite.**
5. **A — status quo.**

Option E is D with one important correction: use a disposable VM, container host, or managed runner for the first unattended run; do not first bet the product on undocumented agent-native scheduling. Use native sandboxing inside that runner as defense in depth, not as the sole boundary.

C is safer than D for the unattended target because it establishes a real workload boundary earlier. D remains a good foreground/partially-attended development mode. B is premature because it assumes vendor primitives provide durable scheduler, cancellation, observation, and recovery contracts that have not been established. A is last because it preserves the demonstrated failure-generating interface.

### 3. What survives?

Keep:

- The `/deliver-task` lifecycle, adapters, co-review, freeze rule, and task-graph semantics.
- Deterministic task scanning, plan-graph handling, validation, claim/reconciliation helpers, and run-state format as a human-readable ledger.
- The useful doctor invariants, recast as reconciliation checks against Git and the tracker.
- Preflight as an **environment-admission canary** that executes the real runner path.
- Budget intent: spend caps, time limits, retry bounds, and a human-visible report.

Do not carry forward as production architecture:

- Seatbelt profile rendering, path/exec exception machinery, host egress rendering, and the unjailed verify-broker escape hatch.
- Generated launch wrappers, launchd relaunch policy, Bash exit classification, pause ledger, gate, alarm state machine, and wrapper-specific doctor repair paths.
- Any agent-writable fact that authorizes another attempt, retry, pause exemption, or terminal result.

Keep the old harness only as an explicitly unsupported fallback until replacement runs meet evidence gates; then delete it as a unit rather than maintaining two production supervisors.

### 4. What belongs in code versus model prose?

Use deterministic code for facts and enforcement:

- Credential scope, spend/time caps, runner timeout, and cancellation.
- Immutable attempt identity and externally observed Git/tracker transitions.
- Running pinned verification against an identified tree/base pair and recording its output.
- Heartbeat age and stale-run notification.
- Reconciliation that observes Git, CI, and tracker state before it changes local reporting.

Use model-executed prose for judgment:

- Understanding the task, implementing it, choosing among legitimate designs, interpreting review feedback, and composing the delivery lifecycle.

The rule is: the agent may propose and report progress, but it must not be the sole author of the evidence that grants it more authority. This does not require a controller-owned task graph or SQLite. It requires that the minimal external supervisor own only enforcement and observation, not model judgment.

The supervisor should therefore live outside the worker process and preferably in the runner/platform boundary. It should be small: enforce a deadline, observe a heartbeat, cancel or notify. It should not become another Bash controller that infers business state from logs.

### 5. Minimal path to a real run #4

Build the following, in order.

1. **Write the security and operating contract.**  
   Create a dedicated runner identity, a fine-grained GitHub token limited to the target repository and required PR operations, a spend cap, and no mounted personal credential stores or host worktree.  
   **Gate:** prove the token cannot read or write a second repository; prove the runner cannot read a sentinel secret on the host.

2. **Build one runner canary, not a new supervisor.**  
   Fresh-clone the target repository in a disposable runner; execute the actual model session under its supported sandbox; perform the exact required git, interpreter, test, push, PR, and API operations. Use one platform job timeout and one external stale-heartbeat notification.  
   **Gate:** all operations succeed in the actual runner, and killing the worker produces a notification within the stated bound.

3. **Run #4 as one small real task on a non-auto-pilot plan.**  
   Use the existing delivery prose and run-state branch. No fan-out, no automatic relaunch, no custom retry policy. On failure, stop and require an explicit human resume.  
   **Gate:** a reviewable PR exists; CI or a clean independent runner verifies the pinned revision; the human receives both normal completion and forced-stall evidence.

4. **Prove recovery before scale.**  
   Repeat with a three-task dependency chain and deliberately kill one worker between lifecycle steps. Reconcile from Git/tracker state and resume once.  
   **Gate:** no duplicate PR or claim, no unbounded retry, and no unsupported manual state repair.

5. **Only then decide overnight scheduling and fan-out.**  
   Prefer a platform scheduler/job primitive with documented timeout and cancellation semantics.  
   **Gate:** several clean unattended runs without runner-specific exceptions or new supervision state machines.

### 6. Strongest arguments against B/D

The strongest case against a Claude-native solution is substantial:

- Native sandboxes may be less inspectable, less configurable, or insufficiently strong against a `bypassPermissions` agent. Moving to vendor primitives can exchange visible complexity for opaque risk.
- Agent-driven scheduling may not survive process loss, machine sleep, quota limits, session expiration, or vendor behavior changes as reliably as a platform scheduler.
- The current exit contract, reconciliation behavior, and error taxonomy encode real operational learning. A rewrite can silently lose it.
- Native worktrees/subagents may not preserve reasoning context or produce the same reviewable, resumable semantics as the current bounded-worker model.
- Vendor coupling increases: permission semantics, sandbox behavior, workflow APIs, and session persistence can change outside this repository’s control.
- A disposable runner adds cost, image maintenance, secret provisioning, and another operational system.
- For a single standardized Mac and a fixed set of repositories, the existing harness might be cheaper to finish than migration.

Those arguments defeat a wholesale B rewrite. They do not justify A. The recommended E path preserves the proven delivery layer, keeps the useful deterministic artifacts, and replaces only the bespoke substrate after measured runner canaries demonstrate that the new boundary is real.
