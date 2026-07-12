---
title: "Auto-pilot: invert the authority boundary (scoped)"
status: draft v2 — revised after adversarial review
created: 2026-07-12
revised: 2026-07-12
related:
  - dev_docs/auto-pilot-developer-review-feedback.md
  - dev_docs/auto-pilot-design-review-codex.md
  - dev_docs/auto-pilot-inversion-design-review-codex.md
---

# Auto-pilot: invert the authority boundary (scoped)

> **v2.** The first draft claimed all ten known defects reduced to a single root cause and proposed a full controller rewrite. An adversarial review falsified the unifying claim and most of the sizing. This version keeps what survived and states plainly what did not. The rejected v1 claims are preserved in [§10](#10-what-v1-got-wrong) so we do not re-derive them.

## The diagnosis (corrected)

The system has **three interacting weaknesses**, not one:

1. **Agent-authored control state.** The agent writes the facts that decide whether it is healthy, paused, finished, or entitled to run again.
2. **Generated control-flow seams.** The launchd wrapper's above/below-the-gate topology is maintained by comments and print statements.
3. **A harness that does not exercise production composition.** Fixtures diverge from production precisely at the invariant boundary.

**The honest attribution.** Of the ten defects in our history, only four are caused by weakness (1):

| Defect                                             | Prevented by the inversion? | Actual cause                                    |
| -------------------------------------------------- | --------------------------- | ----------------------------------------------- |
| Doctor I5 force-removed a **live** worker worktree | **Yes** (with a real lease) | Destructive act without authoritative ownership |
| Pause exemption trusted `status` + `paused_until`  | **Yes**                     | Agent self-corroboration                        |
| Alarm scan landed below the gate                   | **Yes** (Stage 3)           | Generated control-flow reachability             |
| Heartbeat landed below the gate                    | **Yes** (Stage 3)           | Generated control-flow reachability             |
| Doctor I3's vanished-PR park unreachable           | No                          | Fake `gh` with impossible exit semantics        |
| Notifier guard blind to `PATH`-overriding fixtures | No                          | Test-harness containment failure                |
| Doctor I4 recorded "repaired" after a failed write | No                          | Discarded return codes                          |
| `die` escaped "best-effort" `\|\| true`            | No                          | Shell error-semantics mistake                   |
| `--park-limit` parsed but never emitted            | No                          | Configuration plumbing failure                  |
| Status report had no production `gh`/usage args    | No                          | Entry point failed to supply deps               |

**Six of ten would survive a rewrite untouched.** A Python controller reproduces every one of them. _SQLite does not make launchd transactional._ Tasks 28 and 29 — filed **after** this analysis began — are teardown/bootout bugs a new controller would reproduce exactly.

This still justifies a **scoped** inversion. It does **not** justify replacing the graph, the delivery lifecycle, or the doctor.

## The two structural hazards worth fixing

**1. The agent authors the verdicts that gate it.** `launch-runtime.md` concedes this outright:

> _"The sentinel dir lives in the run worktree, which the jailed orchestrator can write — so it could forge its own `<id>.result` (`code: 0`) and skip the broker entirely. This is deliberately out of scope."_

**2. The gate seam is a permanent bypass hazard.** An early `exit 0` in the middle of a generated program has produced three merge blockers, twice with **no conflict marker**.

The fix for #196 already invented the cure for (1) under pressure — a supervisor-owned ledger the jailed agent cannot write. Generalize it deliberately.

## Scope: what we are and are not doing

**In scope (approved for design):**

- **A scenario harness** around the **existing** production entry point. First, not last.
- **Supervisor-owned leases and control state**, outside the run worktree.
- **Verify verdicts outside agent write authority, bound to a tree SHA.**
- **Stable `watch` / `worker` executables** replacing the generated gated wrapper.
- A **context-retaining bounded worker**.

**Explicitly NOT in scope (revisit only with measurements):**

- Controller-owned task graph.
- One-task-per-cold-process.
- Deleting `doctor`, `status`, or `/deliver-task`'s lifecycle.
- Rewriting the supervisor in Python/SQLite.

## The bounded worker (this replaces v1's one-attempt model)

v1 proposed one task per cold process. That is **not required** for the authority win and costs real context: repository conventions learned on task 1, why a parent was built a certain way, what a reviewer meant, environment workarounds. Git and the tracker recover _facts_; they do not recover _reasoning_.

> **The controller leases a bounded worker session — at most N tasks, T minutes, or B tokens. The worker keeps its model context but cannot write controller state, grant itself retries, extend its lease, or declare the run terminal.**

The controller still chooses the work, writes the lease **before** dispatch, corroborates outcomes, enforces deadlines and retry limits, owns the verify verdict, and can refuse further dispatch. **That is the entire authority win.** Context reuse is orthogonal to it.

Start with a conservative N; **measure** cold-start cost, context-loss defects, and duplicate work before moving it. `N=1` is a tuning result, never an axiom.

**A contradiction v1 did not resolve:** `/deliver-task` owns the full per-task lifecycle (claim → implement → verify → PR → co-review → iterate → hand off). It does **not** "shrink dramatically." Only its auto-pilot-specific seams move to the controller: the verify broker, the question log, base-SHA tracking, and stale-review invalidation. The lifecycle stays with the model, because it _is_ model work.

## Progress: a bounded heuristic, not a unifying invariant

v1's rule — _"exit 0 without external effect is no progress"_ — is **gameable in both directions** and is demoted accordingly. It is a **retry/liveness heuristic**, useful and necessary, but it is not the theory of everything v1 claimed.

**It kills legitimate work.** An attempt may reproduce a bug before editing, read an unfamiliar subsystem, resolve a hard rebase, diagnose an auth failure, or correctly conclude a task is already satisfied. _"I learned why this cannot proceed"_ is a valid outcome: it consumes an attempt and eventually parks — it is **not** evidence of a wedge.

**And it is trivially forged.** v1 asserted a local commit is evidence the agent cannot author. **False — the agent owns the worktree, index, refs, and history.** Empty commits, formatting churn, alternating two tree states, amended history, or re-running a weak pinned verify all read as "progress."

The predicate must therefore be qualified:

- A **commit** counts only if it is a **descendant of the leased baseline**, has a **non-empty tree delta**, and has **not been credited before**. Even then it proves _activity_, not correctness.
- A **verify verdict** is **keyed to an exact tree SHA**, and only the **first** verdict for that SHA counts. Otherwise the agent gets a verdict, mutates the tree, and claims credit for the new state.
- **PR / tracker** changes count only as **allowed workflow transitions attributable to this attempt**.
- **Human-authored** changes during a lease trigger reconciliation; they never credit the worker.
- Churn gets a **bounded budget**.
- Raw activity is **diagnostic** — it may extend a liveness timeout; it is not progress.

## Sizing (corrected — and it is not a deletion)

`spawn-orchestrator.sh` is **4,503** lines; `test-spawn-orchestrator.sh` is **3,982**.

v1 tabulated ~1,600–1,800 lines as "deleted or subsumed" and estimated 1,200–1,400 replacement lines. **That was an aspiration, not a sizing argument.** The semantics do not vanish — they **move**:

| v1 said "deleted"  | Reality                                  |
| ------------------ | ---------------------------------------- |
| `doctor` (587)     | Becomes reconciliation — same edge cases |
| `supervisor_check` | Becomes policy                           |
| `status`           | Becomes a projection                     |
| `_supervisor_halt` | Becomes terminal transition + teardown   |
| `restack`          | Rewritten elsewhere, not removed         |

(Line counts differ by measurement: function-body spans vs definition-to-definition spans including the load-bearing comment blocks. Both are defensible; the _point_ is unaffected.)

The replacement estimate also **excluded**: migration/compat code, process-group and child cleanup, schema upgrades, adapter error taxonomies, persistence recovery, launchd integration, projection fidelity, foreground/detached parity, task-source normalization, and scenario fixtures + fault injection.

**Unproven prerequisite:** the repo has Python scripts, but `check.sh` runs them through `uv`. _"There is Python infra"_ does **not** establish that a stable Python runtime is reachable from a **detached launchd** context. That must be proven with a canary **before** any language decision — which is one more reason the language question is out of scope for now.

## Ordering (the harness comes first)

v1 claimed every stage was independently shippable and then admitted Stage 3 invalidates the wrapper tests before the scenario runner exists. **Both cannot be true.**

**Stage 0 — fix the known bugs. Do not freeze them.**
v1's freeze of tasks 17/22/28/29 was wrong: these are **adapter contracts**, not obsolete guards, and a new controller reproduces them. Fix now:

- **17** — TCC/consent under launchd attribution.
- **22** — `gh` NOT_FOUND vs transient (contract measured: rc=1, `Could not resolve to a PullRequest`).
- **28** — `supervisor_gate` tears down without verifying bootout.
- **29** — sentinel-free retry boots the job out anyway → "gone but not done."
- **NEW** — `.auto-pilot/supervisor-state` has **no ignore rule**. `git add -A` in the run worktree will **stage the supervisor ledger** (the Seatbelt deny blocks _writes_, not `git add`, which only reads). Once tracked, a later `checkout`/`reset` tries to write it, hits the deny, and **fails the git operation**. Ignore it _and_ move it out of the run root.

Task 21: **drop the line-survival audit** — not because a clean rebase "provably cannot" drop a line (too categorical: git applies commits, not theorems; custom merge drivers exist), but because it **checks the wrong invariant and manufactures false confidence**. Keep its real requirements: re-verify on the exact restacked SHA, invalidate and re-run co-review, and re-run the PR's acceptance criterion. Where that criterion needs **human or model judgment**, a deterministic controller cannot execute it — so it needs an explicit **`blocked_pending_acceptance`** state, not prose telling someone to re-run it.

Task 20's implementation may later become a projection, but **the user need — a positive periodic status — does not disappear.**

**Stage 1 — the scenario harness, against the current production entry point.**
Real Git repos and worktrees, an **injected clock**, a fake `claude`, and **explicit fake adapters** for GitHub/tracker/launchd/notifier — _not_ ambient `PATH` shadowing (the mechanism that could not observe its own bypass). This improves **either** architecture, so it is unconditionally worth building and carries no rewrite risk.

Scenarios: repeated `exit 0` with no external effect → halt+alarm; valid/missing/expired/far-future pause; crash after **every** transaction; `NOT_FOUND` vs transient; parent merge → verify + co-review actually re-run; teardown partial failure; and **one macOS canary** on real launchd + real Seatbelt with a fake model.

**Stage 2 — supervisor-owned lease + state outside the worktree; verdicts bound to a tree SHA.**
The authority win. Lands in the **current bash**. No rewrite.

**Stage 3 — replace the generated wrapper with stable `watch` / `worker`.**
Kills the seam as a category. **Not independently shippable**: its definition must include the watcher, the attempt launcher, the scenario runner, a **behavioral parity matrix**, and the launchd/Seatbelt canary. Ship the harness first, then switch entry points behind it.

**Decide before Stage 3:** foreground vs detached as the **default**. The original review recommended foreground default with `--detach` an explicit deployment mode; run #2 showed the _partially attended_ human is the common case. This materially changes how much launchd/TCC/jail surface we carry, so it is a **prerequisite**, not a footnote.

**Stages 4–5 (graph ownership, deleting doctor/status) — NOT APPROVED.** Revisit only after Stage 3 data on cold-start cost, recovery quality, duplicate work, context loss, and controller complexity.

## The steelman we must keep answering

Both detached runs did useful work. The defects are concrete, localized, and now carry strong acceptance criteria. The most damaging ones came from a harness that did not exercise production composition — which the harness fixes in **either** architecture. The current system encodes hundreds of lines of hard-won macOS, Git, GitHub, worktree, teardown, and recovery behavior that a rewrite must rediscover. **The suite's problem is not too few tests; it is that some tested the wrong boundary.**

That case is **stronger** than v1's Stages 4–5. It is **weaker** than Stages 1–3, because supervisor-owned leases, an unforgeable verdict path, and a stable watcher remove two _demonstrated_ structural hazards without touching the graph or the delivery lifecycle.

## The early-warning sign

> **If the new controller starts accumulating special cases for "progress," reconciliation, and adapter ambiguity faster than the old supervisor sheds them, we are moving the complexity rather than removing the authority problem.** Stop and reassess.

## 10. What v1 got wrong

Recorded so we do not re-derive it:

1. **"All ten defects are one root cause."** False — six are test-fidelity, plumbing, or shell-semantics bugs that survive any rewrite. A satisfying story fitted to ten points.
2. **"Exit 0 without external effect is no progress" as a unifying invariant.** It is a bounded heuristic; unqualified, it kills legitimate work and is forged by an empty commit.
3. **"A local commit is evidence the agent cannot author."** Flatly false. The agent owns the worktree and history.
4. **"One bounded task attempt per process."** Not required for the authority win; costs context; the bounded _session_ gets the same win for free.
5. **"`/deliver-task` shrinks dramatically."** It does not. The lifecycle is model work.
6. **"~1,600 lines deleted, 1,200 to replace them."** The semantics move rather than vanish, and the estimate omitted ten categories of real work.
7. **"Every stage is independently shippable."** Contradicted two paragraphs later by the doc itself.
8. **"Freeze tasks 17/22/28/29."** Unsafe — they are live production bugs a rewrite reproduces.
9. **"A clean rebase provably cannot drop a review line."** Over-generalized from an experiment on plain `rebase --onto` with default config.
