---
title: Auto-pilot — architecture validity review, current status and problem statement
created: 2026-07-21
status: draft — for external (Codex) review
context: written one week after auto-pilot-problem-statement.md (2026-07-13), after three consecutive failed real-launch attempts, to decide whether the foundational design of the harness is valid before any further hardening
audience: an external reviewer with no prior context, then the maintainer
related:
  - ./auto-pilot-problem-statement.md (the Jul-13 anchor; Part I's problem statement still holds)
  - ./auto-pilot.md (original design, locked decisions)
  - ./auto-pilot-inversion-design.md (the scoped v2 pivot, unbuilt)
---

# Auto-pilot: is the foundational design valid?

## 0. The question under review

Auto-pilot's product: **hand a vetted task graph to an agent, get back verified,
reviewable PRs unattended** — nothing merged, human reviews in the morning.

The question: the harness that enables this (a hand-built macOS Seatbelt jail +
launchd supervision + a 6,323-line bash orchestrator/supervisor) keeps
producing permission, credential, and resource-unavailability failures on real
launches, and each fix spawns further fixes. Is the foundational design sound
and merely under-hardened — or is the architecture itself the generator of the
failure stream? A specific alternative is on the table: replace the
script-built harness with **Claude Code–native primitives** (the harness's own
sandbox and permission system, background agents/workflows, scheduled wakeups,
hooks, session resume) rather than continuing to build the substrate by hand.

## 1. Current status (2026-07-21)

### What exists

| Layer                                                                    | Artifact                                                                                                               | Size            |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- | --------------- |
| Skill prose (the "program" the agent runs)                               | `skills/auto-pilot/**`, `skills/deliver-task/SKILL.md`, command routers                                                | ~2,987 lines md |
| Harness code (jail, spawn, supervisor, doctor, alarm, preflight, budget) | `scripts/*.sh` + `*.py`, non-test                                                                                      | ~9,951 lines    |
| — of which the monolith                                                  | `scripts/spawn-orchestrator.sh` (spawn + Seatbelt render + launchd + supervisor + doctor + alarm + restack, pure bash) | 6,323 lines     |
| Harness tests                                                            | mostly `test-spawn-orchestrator.sh`                                                                                    | ~7,244 lines    |

The supervisor is not a separate program: `classify-exit`, `supervisor-scan`,
`supervisor-check`, `supervisor-gate`, `doctor`, and the alarm system are all
subcommands of `spawn-orchestrator.sh`, invoked around each launchd wake.

### Run history — the load-bearing fact

- **Runs #1–#3 (Jul 9–12): the delivery loop worked.** ~20 PRs delivered
  across three detached runs; co-review caught real bugs; the freeze rule held.
- **No successful run since Jul 12.** Three real launch attempts since:
  - **Jul 15** (dotfiles host): green preflight, then four launch blockers —
    linked-worktree git internals under the read-only mount (`index.lock:
    Operation not permitted` on every commit), host `core.hooksPath` binary
    exec-denied, git push credential helper unreachable in-jail, no shipped
    orchestrator prompt template (PRE-533–536, PR #219).
  - **Jul 15** (FinPlan "State Tax Coverage"): jail exec-denies the project's
    Python interpreter; `render-profile` doesn't grant git-write to the shared
    worktree gitdir — run halts systemic on wake 1, zero tasks dispatched
    (PRE-538, PRE-539).
  - **Jul 17** (FinPlan "Scenarios"): `preflight.sh` — built specifically to
    catch the above classes — returned verdict **"go"** while Linear, GitHub
    keychain, and Claude-usage credentials could not authenticate inside the
    jail ("green-but-dead-at-3am", PRE-543, spawning an 8-task fix plan
    PRE-546–553).
- **The week since Jul 13** produced ~25 merged PRs of hardening (reserve
  gate, pause/halt/alarm, CAO backend + gate, preflight smokes, deterministic
  scan extraction) — and zero runs.

### Backlog trajectory

Across the two Linear projects (50 issues), closure rate by creation cohort:

| Cohort             | What it was                         | Closed |
| ------------------ | ----------------------------------- | ------ |
| Jul 9 (20 issues)  | initial build                       | 65%    |
| Jul 10 (20 issues) | dry-run fixes + jail hardening plan | 25%    |
| Jul 15 (6 issues)  | real-launch blockers                | **0%** |
| Jul 17 (9 issues)  | credential-forwarding fix plan      | **0%** |

The `autopilot-harness` project (14 issues, all jail/credential hardening) has
**zero issues done**. Every issue filed after Jul 13 is open. The backlog is
accumulating strictly faster than it drains, and each round's issues are
failures of the previous round's fixes.

### Decisions pending since Jul 13 — none made

- The **containment decision** (A: own egress proxy / B: harness sandbox /
  C: VM-container) — the Jul-13 doc called it "the trunk decision." No ADR.
- The **Python port** (PRs #205/#209 stack) — stalled on branches; the bash
  monolith grew ~1,800 lines instead.
- The **inversion design Stages 1–3** (scenario harness, supervisor-owned
  leases, separate watch/worker programs) — approved on paper Jul 12, nothing
  built.
- **Foreground-vs-detached default** — flagged as a prerequisite; undecided.

## 2. The higher-order pattern in the failures

### 2a. Where failures cluster

Taxonomy across PRs #206–#240 and both Linear projects:

1. **Sandbox/Seatbelt vs. real host state — the dominant cluster (~14
   distinct instances).** The jail's model of the world (exec allowlist, RO/RW
   mount split, egress rules) collides with unbounded host variance: linked
   worktrees keep git state outside the RW mount; `core.hooksPath` points at
   arbitrary binaries; project interpreters aren't on the allowlist; CAO
   binaries aren't fingerprinted; earlier, nested Seatbelt could not compose
   at all (in-jail verify returned noise) and egress enforcement was silently
   never on.
2. **Credentials/auth (~8 instances).** Keychain-bound helpers unreachable by
   design; `~/.claude` subscription creds can't be mounted safely; forwarded
   tokens not passed into the detached job; 401s indistinguishable from
   transient failures (the 4h14m/52-relaunch silent loop).
3. **Observability — "failure looks like success" (~6 instances).** Denied
   commits read as clean `exit 0`; CAO empty-diff hand-offs with every signal
   green; preflight verdict "go" with dead credentials; `--until` documented
   as a hard watchdog that doesn't exist.

Categories 1 and 2 are **substrate failures**, not delivery-loop failures. No
recorded failure since Jul 12 is a failure of claim/implement/review/hand-off.

### 2b. The generator

Two mechanisms explain why fixes spawn fixes:

**Mechanism 1 — the jail fights an unbounded state space.** The jail is a
hand-built boundary (Seatbelt profile + exec allowlist + mount split +
launchd) that must correctly anticipate every interaction between claude,
git, gh, coder CLIs, credential helpers, hooks, interpreters, and _whatever
the host happens to have configured_. Each real host/project reveals a new
interaction; each is patched individually; the state space does not shrink.
Smoke tests pass because they test the jail's model of the world, and the next
real launch fails because the world differs from the model. Five consecutive
rounds followed this exact shape.

**Mechanism 2 — every fix adds a claim-making component that itself needs
corroboration.** Preflight was added to verify launches → preflight itself
returned a false "go" → an 8-task plan now exists to verify preflight. The
CAO gate that verifies worker config is explicitly "a proxy, not proof," with
the real smoke deferred. The supervisor verifies the agent; PRE-619 records
that the supervisor's own documented behavior (backoff, pause limits) doesn't
match its code. The Jul-13 doc named this — _"the machinery for trusting the
machine has been growing faster than the machine"_ — and the week since
confirmed it empirically.

Both mechanisms trace to one root: **the harness hand-builds four things it
does not own — an OS boundary, a process supervisor, a credential broker, and
a scheduler/heartbeat — in bash, on a live personal macOS host.** All four
exist only to service the locked "Sandboxed yolo" decision (bypassPermissions
bounded by a self-built jail rather than a human). None of the four is the
product.

### 2c. What the evidence says about the design's three premises

| Premise                                                                                                    | Verdict from evidence                                                                                                                                                                                                                                                                           |
| ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The delivery loop (deliver-task; compose-never-duplicate; hand-off ≠ done; freeze rule; adapters)          | **Validated.** 3 runs, ~20 PRs, zero failures in this layer since Jul 12. Keep.                                                                                                                                                                                                                 |
| "Sandboxed yolo": a hand-built Seatbelt+launchd jail on the host is what safely bounds `bypassPermissions` | **Falsified in practice.** Nested Seatbelt can't compose; egress was never actually enforced; the jail blocks the product (interpreters, git, push) more reliably than it blocks threats; 3/3 recent launches dead at this layer; its verification layer produces false greens.                 |
| Supervision by bash inside the launchd wake cycle                                                          | **Structurally unsound and known to be** (inversion doc, Jul 12): 300s wake vs 2700s task ceiling means ~9/10 intervals are missed by design; no timeout on the claude call → a hung call means no heartbeat, no alarm, forever. The prescribed fix (separate watcher program) was never built. |

The Jul-13 problem statement already reached the corresponding prescription —
_(a) blast radius is an identity problem before it is a syscall problem
(repo-scoped PAT, server-side, immune to every measured failure mode); rent
the boundary rather than build a fifth generation of it_ — and the week since
did neither. This review's finding is that the prescription was correct and
un-executed, and the cost of non-execution is now measured: 25 PRs, 15 new
issues, zero runs.

## 3. Problem statement (updated, superseding none of Part I)

> The product problem statement is unchanged: _hand a vetted task graph to an
> agent; get back verified, reviewable PRs; blast radius capped by
> credentials; a heartbeat that reaches me when stuck; "done" independently
> verified._
>
> The engineering problem is now: **the substrate strategy — hand-building
> containment, supervision, credential plumbing, and scheduling in bash on a
> personal macOS host — is the primary generator of the failure stream, and
> continued hardening of it has negative expected yield** (measured: one week,
> ~25 PRs of hardening, three failed launches, 0% closure on all post-Jul-13
> issues). The decision to make is which substrate replaces it, not which
> patch comes next.

## 4. Options for the reviewer to weigh

**A. Status quo:** keep hardening the bash jail/supervisor. (Includes the
8-task credential plan, the four Jul-15 blockers, PRE-542, the inversion
Stages 1–3, and the Python port — the currently-implied path.)

**B. Claude-native harness:** delete the hand-built substrate; rebuild the
orchestrator on Claude Code's own primitives, which now cover most of what
the 6,323-line script does:

- _Containment_: Claude Code's native sandbox (sandboxed bash with
  filesystem/network policy, `allowWrite`/`denyWithinAllow`, loopback rules)
  plus permission rules/hooks — i.e., containment option B from the
  jail-containment plan; maintained by Anthropic, composes with the tool
  shell by construction, no nested-Seatbelt problem.
- _Run loop / scheduling_: a long-lived session using scheduled wakeups /
  cron / background tasks instead of launchd wake cycles; the loop is the
  agent's loop, not a plist.
- _Fan-out / per-task isolation_: native subagents/workflows with worktree
  isolation instead of hand-rolled spawn.
- _Supervision_: hooks + a minimal external watcher whose only job is
  heartbeat-staleness → notification (the one inherent requirement per the
  Jul-13 analysis).
- _Identity_: repo-scoped fine-grained PAT + spend cap (server-side blast
  radius) instead of egress rules that were never on.
- _Resume_: native session persistence instead of RUN.md crash-reconciliation
  (RUN.md may remain as the durable human-readable ledger).

**C. Rented boundary:** run the orchestrator in a VM / devcontainer / cloud
runner (containment option C) with scoped credentials; the jail problem
dissolves into "the token can only reach this repo," and tests run normally
because there is no second sandbox.

**D. Identity-first hybrid (B+C-lite, staged):** (1) repo-scoped PAT + spend
cap now; (2) orchestrator as a normal Claude Code session under the native
sandbox, partially attended (the empirically realized mode — run #2 did 8/9
tasks while the human was at their desk); (3) a ~50-line external heartbeat
watcher; (4) detached/overnight mode only if partially-attended runs prove
insufficient, and then via C, not via more Seatbelt.

## 5. Questions for the reviewer

1. Do you agree the evidence falsifies the "Sandboxed yolo on a hand-built
   jail" premise, or is this under-hardening that one more focused push (the
   8-task credential plan + 4 blockers) would plausibly close? What evidence
   would distinguish those two worlds?
2. Rank options A–D (or propose E). What does your ranking assume about
   Claude Code's native sandbox/scheduling being sufficient for unattended
   operation?
3. What of the existing 10k lines survives in your recommended world? (The
   candidates: preflight probes, doctor invariants, run-state schema, budget
   gate, deterministic scan scripts, delivery-loop skill prose.)
4. The delivery loop is prose (skill markdown) executed by the model, and it
   works; the substrate is code, and it fails. Is there a general lesson
   about which layer should be deterministic code vs. model-executed prose in
   this system — and does it change where the supervisor should live?
5. What is the minimal migration path that gets a **run #4 on a real,
   non-auto-pilot plan** — the trunk's next commit per the Jul-13 doc — with
   the least new machinery?
6. What are the strongest arguments _against_ the Claude-native option (B/D)?
   E.g., sandbox escape surface of `bypassPermissions` under the native
   sandbox, scheduling reliability of an agent-driven loop vs launchd,
   loss of the hard exit-code contract, vendor coupling.
