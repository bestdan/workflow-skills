# Verdict

**Proceed, but cut it down to Stages 1–3 plus the scenario harness. Do not approve Stages 4–5 or the one-task-per-process model yet.**

The authority inversion is directionally right. The document’s unifying diagnosis, progress predicate, sizing, and migration claims are not yet strong enough to justify the full rewrite.

The practical architecture should be:

- supervisor-owned lease and control state outside the worktree;
- supervisor-owned verification results tied to a tree SHA;
- stable `watch` and bounded `worker` executables;
- a worker allowed to process a bounded batch of up to N tasks while retaining context;
- the real scenario harness delivered before or atomically with the watcher split;
- the existing graph orchestration, doctor, and lifecycle instructions retained until measured evidence justifies moving them.

## 1. The diagnosis over-fits the evidence

There is no canonical ten-defect inventory in the draft, so I reconstructed the concrete instances from the review history and follow-up tasks. Only about four are actually caused by agent-authored control facts.

| Defect                                                                | Would inversion prevent it? | Actual cause                                                                                   |
| --------------------------------------------------------------------- | --------------------------: | ---------------------------------------------------------------------------------------------- |
| Doctor I5 removed a live worker worktree                              |   Yes, with a correct lease | Destructive reconciliation without authoritative ownership; also a write-window/fixture defect |
| Pause exemption trusted `status` + `paused_until`                     |                         Yes | Agent self-corroboration                                                                       |
| Alarm scan landed below the gate                                      |           Yes, with Stage 3 | Generated control-flow reachability                                                            |
| Heartbeat landed below the gate                                       |           Yes, with Stage 3 | Generated control-flow reachability                                                            |
| Doctor I3’s vanished-PR branch was unreachable                        |                      **No** | Fake `gh` had impossible exit semantics                                                        |
| Notification guard missed fixtures that replaced `PATH`               |                      **No** | Test-harness containment failure                                                               |
| Doctor I4 ignored failed `gh` writes and recorded “repaired”          |                      **No** | Discarded return codes                                                                         |
| `die` escaped supposedly best-effort `                                |                             | true` paths                                                                                    |
| `--park-limit` was parsed but not emitted                             |                      **No** | Configuration plumbing failure                                                                 |
| Periodic status reconciliation had no production `gh`/usage arguments |                      **No** | Production entry point failed to supply dependencies                                           |

The original defect review explicitly describes the dominant pattern as test/production divergence, not self-authored state: wrong fixture timing, impossible `gh` semantics, bypassing the real wrapper, and escaped side effects ([review feedback](/Users/danielegan/src/workflow-skills/dev_docs/auto-pilot-developer-review-feedback.md:13)). The pause defect is the clean authority-boundary example. Several others are ordinary—but serious—composition and test-fidelity failures.

Two current follow-ups weaken the grand-unification claim further:

- `supervisor_gate` does not verify that bootout succeeded ([task 28](/Users/danielegan/src/workflow-skills/dev_docs/tasks/autopilot_hardening_plan/autopilot_hardening_task_28.md:20)).
- The sentinel-aware teardown is followed by a sentinel-free retry, producing “gone but not done” ([task 29](/Users/danielegan/src/workflow-skills/dev_docs/tasks/autopilot_hardening_plan/autopilot_hardening_task_29.md:19); implementation at [spawn-orchestrator.sh](/Users/danielegan/src/workflow-skills/scripts/spawn-orchestrator.sh:1285)).

A Python controller can reproduce both defects perfectly. SQLite does not make launchd transactional.

The more defensible diagnosis is:

> The system has three interacting weaknesses: agent-authored control state, generated control-flow seams, and a harness that does not faithfully exercise production composition.

That still supports Stages 1–3. It does not prove the need for Stages 4–5.

## 2. Stage 1 captures most of the authority win; Stage 3 captures the seam win

The supervisor-owned lease is the strongest proposal in the document. It gives destructive cleanup, retry counting, pause budgets, and process ownership a fact the agent cannot invent. Moving the verify verdict outside the writable tree is similarly well justified: the current reference explicitly admits that the result is forgeable ([launch-runtime.md](/Users/danielegan/src/workflow-skills/skills/auto-pilot/references/launch-runtime.md:414)).

Stage 3 is also justified. The generated wrapper really does have a load-bearing topology encoded in comments and print statements:

- scan and heartbeat above the gate;
- gate;
- early exit;
- model;
- post-model classification.

See [spawn-orchestrator.sh](/Users/danielegan/src/workflow-skills/scripts/spawn-orchestrator.sh:1093). A stable watcher removes that category of landing error.

That is the real cutoff.

Stage 4—controller-owned graph plus one-task workers—is a different proposition. It is not required to gain either authoritative leases or a stable watcher. Stage 5’s deletion of doctor/state machinery depends on Stage 4 reproducing a large set of recovery semantics successfully.

So the document should stop claiming that the complete graph rewrite follows naturally from the lease. It does not.

## 3. The progress predicate is unsafe in both directions

The definition at [inversion design](/Users/danielegan/src/workflow-skills/dev_docs/auto-pilot-inversion-design.md:81) conflates three things:

- liveness;
- externally observable activity;
- meaningful movement toward a task’s terminal condition.

They are not interchangeable.

### Legitimate attempts that produce no stated “progress”

These would be classified as no-progress despite doing useful work:

- investigating and reproducing a defect before editing;
- reading an unfamiliar subsystem and forming an implementation plan;
- resolving a difficult rebase or conflict before committing;
- running a long end-to-end exercise when the broker records only the eventual formal verify command;
- determining that a task is already satisfied and should be closed as a no-op;
- reviewing an existing implementation and finding no changes necessary;
- diagnosing an environment/auth/tooling failure;
- producing uncommitted changes during a long implementation;
- waiting for a bounded reviewer or external adapter;
- cleaning or recovering an interrupted worker worktree.

A bounded attempt may legitimately end with “I learned why this cannot proceed.” That should consume an attempt and eventually park, but it is not evidence that the worker was wedged.

### Wedged or adversarial attempts that remain “healthy”

The rule is much easier to game than the draft acknowledges:

- create an empty local commit;
- alternate two harmless tree states;
- amend or rewrite local history;
- commit formatting churn;
- repeatedly run a pinned but irrelevant or weak verify command;
- obtain a new broker verdict for the same unchanged tree every attempt;
- flip a tracker state forward and backward;
- update a PR body or metadata;
- receive a human-created PR/review change during the lease and get credit for it;
- rely on GitHub’s changing mergeability result.

A local commit is plainly agent-authored. The agent owns the worktree, index, refs, commit messages, and local history. It is no more “unforgeable” than `RUN.md`; it is merely easier for the controller to observe independently.

A broker verdict is also not progress unless it is bound to an immutable tree SHA. Otherwise the agent can request a verdict, change the tree, and claim the verdict as evidence for the new state. Repeating a verdict for the same SHA must not reset the attempt counter.

A safer model is:

- A lease establishes the attempt baseline.
- Activity is diagnostic and may extend a short liveness timeout.
- Progress is a previously unseen, attributable, monotonic transition toward a task-specific terminal predicate.
- A commit counts only if it is a descendant of the leased baseline, has a nonempty tree delta, and has not previously been credited. Even then it proves activity, not correctness.
- A verify verdict is keyed to the exact tree SHA and only the first verdict for that SHA counts.
- PR and tracker changes count only if they are allowed workflow transitions attributable to this attempt.
- Human changes trigger reconciliation; they do not give the worker credit.
- Repeated churn gets a bounded budget.

Without those qualifications, “exit 0 without external effect” is a useful retry heuristic, not the unifying invariant the document claims.

## 4. One task per cold process is not required for the authority win

The current model intentionally reuses context across tasks. The run loop invokes `/deliver-task` repeatedly in one orchestrator session ([auto-pilot skill](/Users/danielegan/src/workflow-skills/skills/auto-pilot/SKILL.md:313)). That context can carry:

- repository conventions learned during the first task;
- why a parent was implemented a particular way;
- review feedback relevant to stacked children;
- environmental workarounds;
- cross-cutting findings and earlier reversible decisions.

The draft admits the token/latency cost but offers no data about it ([inversion design](/Users/danielegan/src/workflow-skills/dev_docs/auto-pilot-inversion-design.md:174)). It also does not define how a new attempt recovers the reasoning context of an interrupted task. Git and tracker state recover facts; they do not recover why a design choice was made, what failed during exploration, or what a reviewer meant.

The missed middle design is better:

> A controller leases a bounded worker session for at most N tasks, T minutes, or B tokens. The worker may retain model context but cannot write controller state, grant itself retries, extend its lease, or declare the run terminal.

The controller can still:

- choose the task or bounded batch;
- write the lease before dispatch;
- corroborate outcomes;
- enforce deadlines and retry limits;
- own verification verdicts;
- refuse further dispatch.

Set `N=1` only if measurements show that it improves reliability enough to offset context loss and cold-start cost. It should not be embedded as an architectural axiom.

There is another internal mismatch: `/deliver-task` already owns the full per-task lifecycle—claim, implementation, verify, PR, co-review, iteration, hand-off ([deliver-task skill](/Users/danielegan/src/workflow-skills/skills/deliver-task/SKILL.md:112)). If “one task attempt” still runs that entire lifecycle, `/deliver-task` does **not** shrink dramatically. If the controller owns those phases, then the model is receiving phase attempts, not one task attempt. The design needs to choose.

## 5. Sizing and migration are optimistic

The headline file sizes are correct: 4,503 production lines and 3,982 test lines. The component table is much less reliable.

Measured as source spans from one top-level definition to the next:

- `doctor`: about 589 lines, close to 587.
- `supervisor_check`: about 251, not 224.
- `write_launch`: about 179, close to 174.
- `status`: about 183, not 164.
- `_supervisor_halt`: about 165, not 133.
- `assert_run_head`: about 137, not 74.
- exit-reason plus clearing: about 94, not ~70.
- gate plus scan: about 126, not 91.
- alarm scan: about 63, not 55.
- heartbeat plus alarm request: about 81, not ~40.
- `restack`: about 284, close to 277.

The listed production spans total roughly 1,868 lines. But most are not deleted semantically:

- doctor invariants become reconciliation;
- supervisor check becomes policy;
- status becomes a projection;
- halt becomes terminal-transition and teardown logic;
- graph parsing becomes a dependency resolver;
- alarm scanning becomes controller alarms;
- restack is rewritten elsewhere.

Calling those lines “deleted or subsumed” and then estimating only 1,200–1,400 replacement lines is not a sizing argument. It is an aspiration. The replacement estimate excludes or underweights:

- compatibility and migration code;
- process-group and child cleanup;
- schema upgrades;
- adapter error taxonomies;
- SQLite recovery and corruption behavior;
- launchd integration;
- projection fidelity;
- foreground/detached parity;
- task-source normalization;
- scenario fixtures and fault injection;
- packaging Python/`uv` into a detached launch environment.

The repository has Python scripts, but its check path runs them through `uv` ([check.sh](/Users/danielegan/src/workflow-skills/scripts/check.sh:35)). “There is Python infra” does not establish that a stable Python runtime is available to a detached launchd controller.

The test deletion estimate is particularly weak. There are direct generated-wrapper sections around [test-spawn-orchestrator.sh](/Users/danielegan/src/workflow-skills/scripts/test-spawn-orchestrator.sh:508), [test-spawn-orchestrator.sh](/Users/danielegan/src/workflow-skills/scripts/test-spawn-orchestrator.sh:1120), and the pause scenarios around [test-spawn-orchestrator.sh](/Users/danielegan/src/workflow-skills/scripts/test-spawn-orchestrator.sh:2747). That is hundreds of lines, not obviously 1,500–2,000. Doctor, restack, teardown, and reconciliation tests still need equivalents; relabeling them “scenarios” does not delete their behavioral surface.

### “Independently shippable” is contradicted by the document itself

The draft says every stage is independently shippable at line 154, then admits Stage 3 invalidates most wrapper tests and requires the scenario runner to arrive simultaneously at line 186 ([design](/Users/danielegan/src/workflow-skills/dev_docs/auto-pilot-inversion-design.md:152), [migration warning](/Users/danielegan/src/workflow-skills/dev_docs/auto-pilot-inversion-design.md:186)).

Stage 3 is not independently shippable unless its definition includes:

- the stable watcher;
- attempt launcher;
- scenario runner;
- behavioral parity matrix;
- at least one real launchd/Seatbelt canary.

The harness should arrive first where possible, then Stage 3 should switch entry points behind it.

## 6. The blast radius is larger—and differently shaped—than stated

The real affected documentation surface is approximately:

- 454 lines in `skills/auto-pilot/SKILL.md`;
- 1,912 lines across its five references;
- 245 lines in `skills/deliver-task/SKILL.md`;
- the `/auto-pilot`, `/deliver-task`, and `/add-task` command contracts.

Most of the auto-pilot run phase from [SKILL.md](/Users/danielegan/src/workflow-skills/skills/auto-pilot/SKILL.md:302) onward changes. So do launch, resume, crash reconciliation, run-budget semantics, status, verification, and projections.

But the claim that `/deliver-task` “shrinks dramatically” is wrong. Its lifecycle remains necessary unless the controller also takes ownership of model-mediated implementation, co-review interpretation, iterative fixes, and hand-off evidence. Only its auto-pilot-specific broker, question-log, base, and stale-review seams obviously move.

The command files are thin routers, so their line-count blast radius is small. Their semantic contracts still change substantially: foreground versus detached mode, resume selection, run-state location, and attempt/batch controls all need new user-facing arguments.

## 7. The `supervisor-state` hazard is real but overstated

The file currently lives at `.auto-pilot/supervisor-state` ([spawn-orchestrator.sh](/Users/danielegan/src/workflow-skills/scripts/spawn-orchestrator.sh:365)) and is protected by a later Seatbelt deny ([spawn-orchestrator.sh](/Users/danielegan/src/workflow-skills/scripts/spawn-orchestrator.sh:592), [path construction](/Users/danielegan/src/workflow-skills/scripts/spawn-orchestrator.sh:690)).

But the draft’s phrase “inside git-tracked `.auto-pilot/`” is imprecise:

- the directory contains tracked state;
- `supervisor-state` itself is explicitly uncommitted;
- current recovery code deliberately tolerates untracked `.auto-pilot` files;
- it explicitly avoids `git clean` and notes that checkout/reset do not discard untracked files ([spawn-orchestrator.sh](/Users/danielegan/src/workflow-skills/scripts/spawn-orchestrator.sh:3695), [doctor handling](/Users/danielegan/src/workflow-skills/scripts/spawn-orchestrator.sh:3939)).

The more immediate hazard is different: the deny prevents modifying the file, but it does not prevent reading and staging it. An accidental `git add -A` could commit the ledger because no ignore rule protects it. Once tracked, later checkout/reset behavior becomes uglier.

Moving it outside the run root is still correct. Calling the current location an active checkout/reset failure in normal documented operation is stronger than the code supports.

## 8. Restack: delete the audit, but delete it for the right reason

Deleting the proposed line-survival audit is safe and preferable. The implementation on the task-21 branch added 335 production lines and 397 test lines for this feature alone. Its content-based audit would still be weak:

- duplicate lines can hide a deletion;
- moved lines pass;
- blank lines are ignored;
- semantic negation passes;
- custom merge drivers or repository configuration complicate the claimed proof.

The draft’s assertion that a clean three-way rebase “provably cannot” lose a parent-added line is too categorical. Git applies commits, not abstract line-preservation theorems. The current restack uses a normal `git rebase --onto` ([spawn-orchestrator.sh](/Users/danielegan/src/workflow-skills/scripts/spawn-orchestrator.sh:3032)); clean completion is good evidence of textual mergeability, not behavioral preservation.

The right reason to remove the audit is that it produces false confidence while checking the wrong invariant. Keep the requirements that matter:

- re-run verification on the exact restacked SHA;
- invalidate and re-run co-review when relevant;
- re-run or explicitly block on the PR’s acceptance criterion;
- fail closed on actual rebase conflicts.

The design also misses that a user-run acceptance criterion may require human judgment or a model. A deterministic controller cannot always execute it. That needs an explicit `blocked_pending_acceptance` state, not prose saying to re-run it.

## 9. The original review is being stretched beyond what it said

The draft faithfully carries over the recommended authority inversion and watcher/attempt split. It overstates the review in two ways:

1. The original review separately identified authority, gate topology, test fidelity, restack, and deployment mode. It did not establish that every defect was a special case of self-authored truth.
2. It recommended foreground as the default and detached launchd as an explicit deployment mode ([original review](/Users/danielegan/src/workflow-skills/dev_docs/auto-pilot-design-review-codex.md:109)). The draft downgrades that to something merely “worth deciding” before Stage 3 ([inversion design](/Users/danielegan/src/workflow-skills/dev_docs/auto-pilot-inversion-design.md:188)).

That decision should be made before designing the new controller. It materially changes the required launchd, TCC, notification, and crash-recovery surface.

## 10. Stage 0 is unsafe

“Freeze tasks 17/20/21/22/28/29” is not justified ([design](/Users/danielegan/src/workflow-skills/dev_docs/auto-pilot-inversion-design.md:156)).

Several of those fix defects the proposed controller can reproduce:

- Task 17: detached TCC/consent behavior.
- Task 22: correct `gh` NOT_FOUND classification.
- Task 28: verify launchd bootout.
- Task 29: preserve the terminal-record/bootout invariant.

These are adapter contracts, not obsolete guards. Fix them now or carry their exact acceptance tests into the new implementation. Do not leave known production bugs open while waiting for an unapproved Stage 4 rewrite.

Task 21’s line audit should be dropped, but its re-verification and stale-review requirements should survive. Task 20’s current implementation may be replaced by projections, but the user need—positive periodic status—does not disappear.

## Steelman: keep the architecture and stop

The strongest case for no rewrite is substantial:

- Both detached runs completed useful work.
- The known defects are now concrete, localized, and equipped with strong acceptance criteria.
- The most damaging failures arose because the harness did not execute the production composition faithfully.
- A real scenario runner would improve either architecture.
- The current system contains hundreds of lines of hard-won macOS, Git, GitHub, worktree, teardown, and recovery behavior.
- The rewrite must rediscover those edge cases while simultaneously changing persistence, scheduling, model granularity, documentation, and deployment.
- There is no measurement showing that one-task cold starts improve quality or that Python/SQLite reduces total defect rate.
- The current suite has 715 assertions. Its problem is not lack of tests; it is that some tests exercised the wrong boundary.

That case is stronger than Stages 4–5 as currently justified.

It is weaker than the limited inversion because supervisor-owned leases, an external verdict path, and a stable watcher directly remove two demonstrated structural hazards without replacing the graph or delivery lifecycle.

# Recommendation

**Proceed, but cut it down to:**

1. Fix the known adapter/teardown defects now; do not freeze tasks 17, 22, 28, or 29.
2. Build the scenario harness around the existing production entry point.
3. Add supervisor-owned leases and state outside the worktree.
4. Move verify results outside agent write authority and bind every verdict to a tree SHA.
5. Replace the generated gated wrapper with stable `watch` and `worker` programs.
6. Let a worker retain context for a bounded batch of N tasks/time/tokens; start with a conservative N and measure it.
7. Keep the existing graph, `/deliver-task`, doctor, and projections until a parity matrix and real-run data show which pieces are genuinely redundant.
8. Revisit Stages 4–5 only after measuring cold-start cost, recovery quality, duplicate work, context-loss defects, and controller complexity.

The one early warning sign to watch is this: **the new controller begins accumulating special cases for “progress,” reconciliation, and adapter ambiguity faster than the old supervisor is losing them.** If that happens, the rewrite is moving the complexity rather than removing the authority problem.
