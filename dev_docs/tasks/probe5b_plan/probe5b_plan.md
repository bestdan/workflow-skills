---
type: epic
title: Probe 5b — autonomous runaway containment
status: active
owner: Daniel Egan
created: 2026-07-27
---

# Probe 5b — autonomous runaway containment

§7a row 5b of [the E-lite design](../../auto-pilot-e-lite-design-2026-07-21.md).
Disposable baseline probe under §0a's spike contract; rule 4's never-promote
rule applies.

## Goal

Heartbeat detects _stalls_ (Probe 3). A **runaway** keeps the heartbeat fresh
while looping, burning the Max window, spawning workers, or attempting a merge.
Confirm the circuit breakers halt it and drive to safe-stop within a bounded
damage window, with **no human in the loop** and no irreversible action escaping
the gate (Decision #5; Risk #1).

## The finding that reshaped this probe

Step 1 of the probe was an inventory of what breakers exist in code versus what
the design only specifies. **Of the three breaker families row 5b names, none
has out-of-process enforcement today.** All three are agent-cooperative — prose
the orchestrator follows about itself — and a runaway is definitionally an agent
that has stopped honoring its own prose.

| Breaker (5b)               | Specified in                                       | Real enforcement in repo today                                                                                                                                                              |
| -------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Usage / reserve gate       | `run-budget.md` "Pre-invoke reserve", 15% floor    | `scripts/claude-usage.sh` **does** run out-of-process in the supervisor — `launch` resolves it by default as `--report-usage-bin` (`spawn-orchestrator.sh:4721`) and threads it into the rendered scan and report-tick lines (`:1440`, `:1489`), and `status_report` **executes** it at `:4412` (the `:4409` conditional guards that call). But it is **reporting only**: no supervisor decision consumes its output, and the reserve floor itself has no non-prose caller. (`scripts/claude-auto-resume.sh:86`, reading the query at `:83`, does gate on it, but is not on the auto-pilot supervisor path.) |
| Parallelism cap            | Risk #1; Decision #1                               | **Nothing out-of-process.** A numeric cap does exist — `wip_limit: 3` (`skills/task/SKILL.md:33`, semantics at `:40`) — but it is agent-side prose on the `/do-tasks` path, not a supervisor control. On the supervisor path there is no cap, semaphore, or live dispatch counter: the only `ps`/`pgrep`/`proc_pidinfo` call sites in `spawn-orchestrator.sh` are `:1584` (launch) and `:5178` (status), so **nothing on the `supervisor_scan` call graph (`:3019`–`:3133`) reads the process table at all**. The other named artifact, the paid-agent dispatch cap, is likewise agent-side prose and about cost not concurrency.  |
| At-most-twice continuation | Risk #1; §5.3                                      | **Nothing built.** §5.3 defers continuation to Stage 5+; §7a row 8 gives it its own later disposable kernel.                                                                                     |
| Review-gating              | Decision #5; `/deliver-task` hands off at `needs_review` | Agent-side prose. The enforcement Probe 4 confirmed was the server-side ruleset on the **disposable App, now deleted** (row 4). No live server-side merge block exists.                     |

What **is** real, out-of-process, and not agent-writable — all in
`scripts/spawn-orchestrator.sh`, pure shell in the un-jailed supervisor, with
`supervisor-state` write-denied to the jailed agent by the rendered profile:

- `supervisor-gate` (pause gate, no model call); `classify-exit` /
  `supervisor-check` (fatal-auth halt, no-progress guard)
- `supervisor-scan`, which runs on **every** wake above the gate: **blown
  `--until` → halt + teardown**, the **pause-exempt cumulative ledger**
  (`--pause-exempt-max`, 6h), and a park-storm branch (`--park-limit`) that is
  **report-only** — it calls `_alarm_safe` and never sets a halt condition
  (`spawn-orchestrator.sh:2452`; `run-budget.md:353` "no: reported, run
  continues")
- `alarm` + `ALARM` sentinel + `REPORT.md` prepend. Note the distinction these
  are easily read as satisfying but do not: they are **written by the
  supervisor**, but their artifacts live in the agent-writable run dir, so they
  are **not** agent-unwritable the way `supervisor-state` is.

That set is Probe 3's _stall_ breakers plus **two** runaway-relevant halting
breakers and one report-only tripwire. The pause-exempt ledger is the only
existing breaker built against a **lying** agent rather than a dead one, and it
was built from a reproduced forgery rather than a hypothesis — and it is the
only one whose input the agent cannot write. That is the seam this probe
attacks.

**So row 5b is FALSIFIED — by inventory, before any fixture ran** (recorded in
the design doc 2026-07-28). Two of its three breaker families have nothing to
inject against, so no fixture run can change that verdict; it can only refine
it. Under §7a rule 5 the consequences are taken **now**, not at the end of this
plan:

- Row 5b carries the falsification and the invalidated assumptions.
- The named redirect is taken: **a hard control-plane ceiling — wall-clock,
  action-count, or live worker count — is a prerequisite for any unattended
  operation.** It is specified in **§5.1 (a watcher check) + §4.2 (a
  `ceiling_trip` record)**, deliberately _not_ in `spawn-orchestrator.sh`, which
  §6 marks delete-do-not-port. Its inputs are pinned to the three sources the
  agent cannot forge: the control plane's own wall clock, the process table, and
  the provider-side usage query (§2.3's maintainer observation credential).
- Dependent work stops: **priority 6, the thin vertical run, is stopped at its
  start line.** It had not begun, so the durable state is "not started" and
  there is no external write to reconcile.
- Risk #1's mitigation and Decision #5's safety premise are corrected to say the
  breakers do not exist rather than implying they do.

**What the fixture is now for.** It is not a re-test of row 5b and cannot return
that row to `confirmed`. It is a **sizing measurement for the redirect**, under
its own narrower assumption:

> The two existing out-of-process halting breakers bound a runaway's damage, and
> the measurement sizes the ceiling the redirect must add.

Independent work may continue (rule 5), and this measurement is independent: the
ledger write-deny test (`EPERM` under the rendered profile, with a control write
succeeding), leg 1's forged-deadline falsifications, and leg 3's
halt-bounded worker and survivor counts are evidence the inventory cannot
produce, and the ceiling would otherwise be sized by guesswork.

## Scope / non-goals

In scope: a disposable fixture that drives the **real**
`scripts/spawn-orchestrator.sh` supervisor path through injected runaway legs,
with a fake `claude` and a surrogate runaway agent.

Not in scope:

- **No production code changes.** A falsified leg produces the redirect
  recorded in §7a row 5b, not a fix inside this probe.
- **No privileged execution.** Probe 5's teardown removed the helper tree and
  sudoers; 5b runs entirely unprivileged. No `sudo`, no launchd bootstrap into a
  real uid domain, no reaping outside the fixture's own process group.
- **No real model calls and no network.** §0a's Max allowance admits no `claude`
  launches here.
- **No re-minting of a GitHub App.** The token-boundary merge leg is recorded as
  blocked and re-earned by Stage 1's gate (see Open questions A).
- Continuation / parallelism cap **implementation** — those are the redirect,
  and belong to priorities 6–8 under their own stage gates.

## Approach

Kill sheet first, fixture second (§7a rule 1 and the delivery rule). Four
injection legs against one disposable driver:

1. **Deadline escape** — runaway loops past `--until` with healthy state.
2. **Forged-pause runaway** — agent rewrites `paused_until` every wake _and_
   spawns workers; the pause-exempt ledger must halt regardless.
3. **Worker-spawn storm** — designed to falsify; measures what actually bounds
   spawned workers and how many survive teardown.
4. **Irreversible-action attempt** — a merge attempt, tested at the sandbox/jail
   boundary only.

The tradeoff considered and rejected: building the missing breakers first and
_then_ probing them. That inverts the delivery rule (production component before
falsifier) and would spend the redirect's budget before the measurement that
sizes it. Measuring the escape first tells us which ceiling to build.

Time cap: **one day** (§7a rule 3) — originally half a day with no override
requested, **extended by the maintainer on 2026-07-28 after task 2**. Probe 5's
two-day override was earned by privileged uid-domain infrastructure this
measurement deliberately renounces, so parity would be unearned; that is why the
extension is half a day and why **no further extension is authorized**. The
justification is a finding rather than a shortfall: task 2's overrun bought a
construction-time defect only running it could catch — the parent-death channel
passed review and failed its own predicate, leaving the surrogate alive after a
`kill -9` of the driver. The kill sheet's "Time cap" section carries the full
record. The clock is otherwise pre-declared as before:

- **The clock covers fixture build + legs + classification** (tasks 2–6). The
  kill sheet is rule-1 pre-work and task 7 is scaffolding hygiene; both sit
  outside the probe proper.
- **Run order is by evidentiary value, and doubles as the degradation order:**
  1. **Leg 2** — the pause-exempt ledger is the only breaker whose input the
     agent cannot write, and `LedgerWrite`'s refusal (`EPERM` under the rendered
     profile) is the only positive enforcement evidence available here.
  2. **Leg 1** — the forged-deadline variants, the highest-value falsification.
  3. **Leg 3** — the storm sizing measurement the redirect needs.
  4. **Leg 4** — runs last; its marginal cost is low because task 2 renders the
     profile anyway, but SR-3 means a pass proves least.
- At the cap, unrun legs are recorded `inconclusive — not run at cap` and
  classification proceeds. This is safe **because the verdict no longer depends
  on the legs**: row 5b is already classified, so truncation costs measurement
  precision, not the classification.

"Nearly done" is not a fourth state.

## Tasks

1. [[probe5b_task_1]] — write the kill sheet and the breaker-gap inventory;
   **approval gate before any fixture code**.
2. [[probe5b_task_2]] — disposable fixture harness: runaway surrogate, fake
   `claude`, driver over the real supervisor path, with the containment rules
   that Probe 5's incident makes non-negotiable.
3. [[probe5b_task_3]] — legs 1 and 2 (deadline escape, forged-pause runaway).
4. [[probe5b_task_4]] — leg 3 (worker-spawn storm), the designed-to-falsify
   measurement.
5. [[probe5b_task_5]] — leg 4 (irreversible-action attempt) at the sandbox/jail
   boundary; token-boundary leg recorded blocked.
6. [[probe5b_task_6]] — `results.json`, classification, and the §7a row 5b
   outcome + redirect.
7. [[probe5b_task_7]] — graduate durable wisdom and delete this plan folder.

## Resolved decisions (2026-07-28)

Settled after a four-reviewer co-review, then a second opinion from two of them
on the open questions.

- **(A) Leg 4 — option 1.** Test the merge attempt at the sandbox/jail boundary
  only; record the token-boundary leg `blocked, re-earned by Stage 1's gate`.
  Task 2 renders the real profile regardless (leg 2's ledger test needs it), so
  leg 4's marginal cost collapses to a merge attempt against a local bare remote
  under an already-applied profile. That buys the only place in the repo where
  the jail's exec-deny on `gh`/`git` is **exercised** rather than analyzed —
  `nono-evaluation.md` is assessment, not evidence. SR-3 means a pass proves
  little, which is why leg 4 runs **last** and degrades for free.
- **(B) Leg 3 — keep.** The inventory established the *absence* of a cap; leg 3
  measures the *consequence*. The `halt-bounded` worker count and the
  survivors-after-teardown count exist nowhere else, and the latter is precisely
  the incident record's hazard class (processes created after the supervisor last
  looked). Without it the redirect's ceiling is sized by guesswork.
- **(C) Tracking — `git add -f` onto the branch.** Rule 4 requires the decision
  trail checked in, and this folder **is** the decision trail: it records why the
  probe's shape diverges from row 5b's literal text, which `results.json` and the
  row-5b outcome both cite. Linear is wrong here — `push-plan` migrates then
  deletes the local files, moving a day-scale disposable DAG into a tracker that
  outlives it. The `e7773b0` precedent is policy, not accident: **ordinary
  feature work goes to the configured handler; spike probes force-add to the
  branch, because they are evidence and belong in the same history as the fixture
  and the design-doc row they justify.** Task 7's later deletion is harmless —
  git history preserves the folder.
- **(D) Usage-window burn and continuation get rows, not legs.** A fake `claude`
  invocation counter would be *fixture-created* enforcement, not the real
  boundary rule 2 demands, and the outcome is knowable a priori — the inventory
  already proves there is no gate to consult. It also yields no number the
  redirect needs: invocations-until-halt is already captured by legs 1–2 as
  wake-index-at-halt, and the ceiling's size is already specified (the 15%
  reserve floor). `results.json` carries mandatory
  `falsified — no enforcement exists; not exercised` rows for both families,
  each citing the inventory's evidence.
