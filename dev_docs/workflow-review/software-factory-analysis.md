# The Task Loop as a Software Factory

_Framing the workflow as a production line — Linear as the project-management
substrate — and asking: which stations exist, which are missing, and which could
run with the lights off?_

---

## 1. The factory framing

A "software factory" is a production line that converts **raw intent** (a bug, an
idea, a piece of feedback) into **shipped software**, with each station handing a
progressively more refined work item to the next. The discipline is to make each
station explicit, measure flow through it, and automate the stations that don't
need judgment.

Here is the canonical line, with where today's workflow sits:

```
① INTAKE → ② TRIAGE/REFINE → ③ PRIORITIZE → ④ DECOMPOSE → ⑤ SCHEDULE/DISPATCH
   → ⑥ BUILD → ⑦ REVIEW/QA → ⑧ MERGE → ⑨ DEPLOY/RELEASE → ⑩ MEASURE/LEARN
                                                                  │
                                             └──────── feedback ──┘
```

| # | Station               | Today's coverage                                                                   |  Maturity  |
| - | --------------------- | ---------------------------------------------------------------------------------- | :--------: |
| ① | **Intake**            | `plan-with-docs` (primary) + `/add-task` — but _only from inside a Claude session_ | 🟡 Partial |
| ② | **Triage/Refine**     | `/promote-tasks` confidence check; `break-down-task` for oversized                 | 🟢 Strong  |
| ③ | **Prioritize**        | `priority` + value/effort (`impact ÷ size`) ranking                                | 🟡 Partial |
| ④ | **Decompose**         | `plan-with-docs`, `break-down-task` → PR-sized cards, `is_blocked_by` DAG          | 🟢 Strong  |
| ⑤ | **Schedule/Dispatch** | `/do-tasks` with WIP cap + size-gated auto-routing                                 | 🟡 Partial |
| ⑥ | **Build**             | Remote VMs (repo-pr) / foreground (linear); claim-lock concurrency                 | 🟢 Strong  |
| ⑦ | **Review/QA**         | `/co-review`, `review-facts`, `verify` — but _not wired into the loop_             | 🟡 Partial |
| ⑧ | **Merge**             | Human merges; "done" = merge                                                       | 🔴 Manual  |
| ⑨ | **Deploy/Release**    | —                                                                                  | 🔴 Absent  |
| ⑩ | **Measure/Learn**     | — (explicitly a non-goal; file-deletion-on-PR even _destroys_ history)             | 🔴 Absent  |

**The headline:** the _middle_ of the line (refine → decompose → build) is
genuinely strong and already semi-autonomous. The **two ends are open** — intake
is single-channel and the back end (merge → deploy → measure → learn) barely
exists — and **nothing schedules the line itself**. A human still types every
`/promote-tasks` and `/do-tasks`. It's a superb set of _stations_; it is not yet
a _running line_.

---

## 2. The three biggest gaps

### Gap A — There is no conveyor belt (no autonomous trigger)

Every stage is a verb a human invokes. `capture → promote → do` is three manual
commands that never chain. A real factory has a **clock**: work arrives, gets
groomed on a cadence, and ready work is pulled into build automatically.

Today the loop only moves when someone is sitting at a terminal typing slash
commands. That is the single largest thing standing between "a great set of
tools" and "a factory."

### Gap B — The Linear path can't run autonomously

This is the sharp edge given that Linear is the chosen PM substrate. On Linear,
`/do-tasks` is **foreground, single, one-at-a-time** — the exact opposite of the
repo-pr path, which dispatches N parallel cloud VMs. So the moment you adopt
Linear as your source of truth, you **lose** the headless batch runner that makes
the file path feel like a factory. The capability matrix says it plainly:

| Verb                | repo-pr | linear |
| ------------------- | :-----: | :----: |
| do — single         |   ✅    |   ✅   |
| **process — batch** |   ✅    |   ❌   |

The asymmetry is understandable (the repo-pr claim-lock was built for fan-out;
Linear execution runs in-session), but it means _Linear + autonomy_ is precisely
the combination the system is weakest at.

### Gap C — The loop is open (no feedback, no memory)

Metrics, cycle-time, and an archive ledger were explicitly descoped, and the
repo-pr handler **deletes the card when the PR opens**. So the factory has no
idea how long things take, how accurate its size estimates are, how often
auto-executed PRs get reverted, or which tasks keep bouncing to
`needs_refinement`. Without measurement there is no learning loop — the factory
can't get better at estimating, prioritizing, or deciding what to auto-execute.

---

## 3. Station-by-station: what's missing and what could be automated

### ① Intake — _open up the front door_

Today work enters only from inside a Claude session — primarily `plan-with-docs`
(designing and slicing a feature), with `/add-task` for incidental follow-ups. A
factory pulls raw intent from everywhere:

- **Linear Triage** as the universal inbox. Linear already ingests from Slack,
  email, Zendesk/Intercom, and Sentry into a Triage queue. Wire `/promote-tasks`
  (Linear flavor) to run against Triage so non-engineers — and _machines_ — can
  file work that the loop then grooms.
- **Error/observability intake.** A Sentry issue or a failing alert → an
  auto-drafted Linear issue with the stack trace as Context. This is the
  highest-value autonomous intake: production _tells_ the factory what to build.
- **Recurring/scheduled tasks** (dependency upgrades, cert rotation, "audit dead
  flags monthly") — raised in the original review and deferred. A cron that
  injects a templated card is trivial and high-leverage.
- **PR-review-comment → task.** A reviewer says "follow-up: extract this helper" →
  auto-`/add-task` from the thread. Closes the loop the plugin already half-has
  via `subscribe_pr_activity`.

### ② Triage/Refine — _enrich, don't just gate_

`/promote-tasks` is a strong _gate_, but refinement in a real factory also
_enriches_:

- **Auto-dedup.** Before promoting, semantically compare against open cards /
  Linear issues and flag likely duplicates (deferred in the original review;
  Linear's own dedup is weak). Prevents the backlog from silting up.
- **Auto-context enrichment.** On promote, have an agent grep the repo and attach
  the actual `related_files` and a one-paragraph "here's where this lives" — so
  the builder agent starts warm. Cheap, and it directly raises the auto-execute
  success rate.
- **Estimation calibration.** Once metrics exist (⑩), feed historical actuals
  back so the size estimate self-corrects instead of being a one-shot guess.

### ③ Prioritize — _make value objective_

`impact` is a hand-set Fibonacci number, and value/effort ranking is repo-pr-only
(Linear has no native impact field, so it falls back to `priority + estimate`).

- **A real scoring model** — RICE or WSJF — computed from fields the tracker
  already has (reach, confidence, effort) rather than a single subjective
  `impact`. Store it in a Linear custom field so ranking is portable.
- **Aging/SLA escalation.** A `low` card that's sat 60 days, or a `high` past its
  SLA, should auto-escalate. Linear has native SLA + automations for exactly
  this; the file path has `expires` (prune) but no _escalate_.
- **Connect impact to reality.** The strongest version: derive `impact` from
  business signal (how many users hit the error, revenue of the affected flow)
  rather than a guess.

### ④ Decompose — _already strong_

`plan-with-docs` + `break-down-task` + the `is_blocked_by` DAG are the most mature
part of the line. The one autonomy opportunity: let `/promote-tasks`, on a
HIGH-confidence "too big" verdict, **auto-invoke `break-down-task`** instead of
parking the card for a human to manually trigger the split.

### ⑤ Schedule/Dispatch — _add the clock_

This is where "a factory" is won or lost.

- **A scheduler.** A scheduled job (GitHub Action cron, or the plugin's own
  `send_later`/`loop` primitives) that runs `promote → do --all` on a cadence,
  draining ready work up to the WIP limit with zero human keystrokes. This single
  addition is what turns the toolbox into a line.
- **Linear-webhook-driven dispatch.** When an issue moves to `Todo` (`ready`),
  fire a remote agent to claim and execute it — instead of waiting for someone to
  run `/do-tasks`. This _also closes Gap B_: it gives Linear the headless,
  event-driven runner the file path has, without making Linear execution
  foreground.
- **Capacity-aware WIP.** WIP is a flat default of 3. Tie it to actual review
  throughput (open `needs_review` count, reviewer availability) so the line
  doesn't manufacture a 20-PR pile-up no human can review — the classic
  "infinite WIP" failure mode of autonomous coding agents.

### ⑥ Build — _strong; extend to Linear_

Solid: isolated VMs, deterministic claim-lock, size-gated auto-routing. The gap
is **parallel/headless build for Linear** (Gap B). Port the draft-PR claim-lock
concept to the Linear path so Linear issues can fan out across VMs like files do.

### ⑦ Review/QA — _wire it into the loop_

`/co-review`, `review-facts`, and `verify` exist but are **manual, out-of-band**
skills. In a factory, review is a _station the work flows through_, not a tool a
human remembers to pick up. This station — auto-review feeding auto-merge — is
where the highest-leverage improvement sits, so it gets its own deep dive below.

- **Acceptance-criteria-driven verification.** Each card _has_ explicit
  Acceptance Criteria (often split into "code-enforced" vs "user-run"). Have the
  builder agent turn the code-enforced ones into actual test assertions and gate
  the PR on them — making "done" mean "criteria demonstrably met," not "an agent
  said so."
- **Risk-tiered autonomy.** `auto_execute_max_size` gates on _size_. Add a gate on
  _risk_ (touches auth / migrations / payments / public API → always human) so
  autonomy scales with blast radius, not just diff size. This same tier is the
  hard gate on auto-merge below.

#### Deep dive: auto-review → auto-merge for agent-authored PRs

The goal: **the moment a `task-loop` PR opens, three reviewers (Claude + Codex +
Gemini) review it; if nothing needs a human and CI is green, it merges itself.**
The good news is that `/co-review` already has almost every primitive — the work
is mostly _wiring it to run headless and giving it a merge decision_.

**The key insight — co-review already classifies "needs a human."** The skill's
reconciler sub-agent (a separate agent, so the author isn't grading its own
homework) sorts every finding — from all reviewers and any existing GitHub
comments — into three confidence tiers:

| Tier       | co-review's default action | Maps to                       |
| ---------- | -------------------------- | ----------------------------- |
| **high**   | auto-fix, verify, push     | machine-resolvable            |
| **medium** | _ask the user_ (judgment)  | **exactly "human-requiring"** |
| **low**    | skip (wrong / over-eng.)   | noise                         |

So "the review shows no human-requiring comments" has a precise definition
already: **the reconciler returned zero `medium` findings.** That is the
auto-merge trigger — no new heuristic needed.

And the three-reviewer pool the user wants is co-review's existing model: the
**Claude** main agent always reviews; **Gemini** and **Codex** are built-in local
reviewers that join the pool when configured (`dev_docs/co-review/.co-review.yml:
local_reviewers: [gemini, codex]`). Widening the pool is the whole reason to run
all three before trusting an auto-merge — Codex and Gemini catch what Claude alone
misses, and vice versa.

**What to build (the gaps):**

1. **A trigger.** A GitHub Action `on: pull_request: [opened, synchronize]`,
   filtered to the `task-loop` label (agent-authored PRs), that runs the Claude
   CLI headless (`claude -p "/co-review --auto"`). (The plugin's
   `subscribe_pr_activity` can drive this from a live session too, but a CI
   workflow is the durable, always-on factory trigger.)

2. **A third co-review disposition: `--auto` (headless, your PR, may merge).**
   Today co-review has two dispositions — interactive "your PR" (asks about every
   medium item) and `--post` (someone else's PR). Neither is autonomous: the
   default _stops to ask_ about medium findings. `--auto` replaces those prompts
   with a policy:
   - **high** → auto-fix, verify (lint/test), commit + push — co-review _already_
     does this.
   - **medium ≥ 1** → this _is_ the human hand-off: post the medium items as a
     `REQUEST_CHANGES` review, label `needs-human`, assign a human, and **stop
     without merging**.
   - **low** → skip, noted in the review body.
   - **zero medium** → proceed to the merge gate.
   - Pre-seed `.co-review.yml` with `[gemini, codex]` so the first-run
     reviewer prompt never fires.

3. **The merge gate.** Enable auto-merge (`enable_pr_auto_merge` /
   `gh pr merge --auto --squash`) only when **all** hold:
   1. reconciler returned **zero medium** findings;
   2. all high-confidence fixes applied and **re-verified green**;
   3. **CI passing** on the final commit;
   4. **risk tier = low** (not auth / migrations / payments / public API / infra —
      the same gate as the bullet above) — high-risk PRs always wait for a human
      regardless of how clean the review is;
   5. no human has already left `REQUEST_CHANGES`.

   Anything short of all five routes to a human instead of merging.

**Honest caveats — what makes this safe (or not):**

- **"Zero medium" is a model judgment, not a proof.** Three reviewers plus a
  reconciler can still _miss_ a real issue (a false "all clear"). The backstops
  are CI, the risk-tier gate, and — critically — the **measurement loop (⑩)**:
  track the revert / hotfix rate of auto-merged PRs and feed it back to tighten
  the gate (lower the size ceiling, widen the risk list) if reverts climb. This is
  the dependency worth naming out loud: **trustworthy auto-merge _depends on_
  closing Gap C.** Turn it on conservatively (size-1, lowest-risk tiers only) and
  loosen as the revert data earns it.
- **Reconciler bias remains.** One model grades all three pooled reviews, so a
  strong shared prior can still slip through. Widening the pool helps but doesn't
  eliminate it.
- **Running Codex + Gemini in CI is real setup.** Install the CLIs, supply
  `GEMINI_API_KEY` and the Codex/OpenAI key as Actions secrets, and make
  co-review's exact-match permission allow-rules available headless (commit
  `.claude/settings.json` or pass `--allowedTools`). One-time, but not free.
- **Bounded rounds.** Auto-fixing high-confidence items mutates the diff, and the
  `synchronize` trigger re-runs the action on the fix commit. Cap the
  review→fix→re-review cycle (e.g. 2 rounds) and have the action skip its own
  bot-authored commits, or it can loop on itself.

**Net:** this single loop closes both station ⑦ (review becomes a station work
flows _through_) and station ⑧ (merge automates itself), leaving humans only the
`medium`-judgment and high-risk PRs — exactly the bounded review bottleneck the
WIP cap and capacity-aware scheduling (⑤) are there to protect.

### ⑧ Merge — _let green merge itself_

"Done" is a human merge. The high-value automation is the **auto-merge gate**
detailed in ⑦'s deep dive: a clean three-reviewer co-review (zero `medium`
findings) + green CI + low risk tier → `enable_pr_auto_merge`, no human click
required. Humans are pulled in only when a reviewer flags a judgment call, CI
fails, or the change touches a high-risk surface. Start with size-1, lowest-risk
PRs and widen as the revert data (⑩) earns trust.

### ⑨ Deploy/Release — _the missing back half_

The line currently ends at `merge`. A factory ships. Even a thin station helps:
record what merged, group it into a release, and (where infra allows) trigger the
deploy and watch it. At minimum, **close the card against the deploy, not the
merge**, so `done` means "in users' hands."

### ⑩ Measure/Learn — _close the loop_

The deliberate non-goal that most limits the system. Without it the factory is
blind. Minimum viable telemetry (and Linear computes most of it natively —
cycle time, throughput, scope-change):

- **Flow metrics:** lead time (capture→merge), cycle time (claim→merge),
  throughput, WIP aging, `needs_refinement` bounce rate.
- **Quality metrics:** % of auto-executed PRs reverted or heavily edited in
  review — the single most important number for _tuning how much to trust the
  autonomous runner_.
- **Estimation accuracy:** predicted `size` vs actual diff/time, fed back to ③.
- **An archive ledger.** The repo-pr handler deleting the card on PR-open means
  history evaporates. A lightweight append-only ledger (or just _keeping_ the
  card and flipping it to `done`) restores a learnable record.

---

## 4. If Linear is the substrate: native leverage being left on the table

Linear ships a lot of factory machinery the loop currently ignores:

| Linear feature           | Could power…                                                            |
| ------------------------ | ----------------------------------------------------------------------- |
| **Triage inbox**         | Multi-channel intake (Slack/email/Sentry/Zendesk) → station ①           |
| **Webhooks**             | Event-driven autonomous dispatch (issue → Todo fires a build) → Gap B/⑤ |
| **Cycles (sprints)**     | A scheduling horizon: "drain this cycle's committed issues" → ⑤         |
| **SLA + Automations**    | Aging/priority escalation → ③                                           |
| **Projects/Initiatives** | Already used as epics — extend to roadmap rollup                        |
| **Custom fields**        | Store RICE/WSJF score, risk tier, revert-flag → ③/⑦/⑩                   |
| **Native analytics**     | Cycle time / throughput dashboards for free → ⑩                         |
| **Sub-issues**           | `break-down-task` output as native children (partly done)               |

The throughline: **Linear can be the clock and the dashboard**, not just the card
store. Webhooks for dispatch and the analytics API for measurement together close
Gaps A, B, and C at once.

---

## 5. Recommendations, prioritized

### Quick wins (days, high leverage)

1. **A scheduler that chains `promote → do --all`** on a cron. The fastest path
   from "toolbox" to "line." (Gap A)
2. **Auto-review → auto-merge loop** (the highest-leverage item — see ⑦'s deep
   dive): a `pull_request`-triggered Action runs `/co-review` headless with the
   Claude + Codex + Gemini pool; a new `--auto` disposition auto-fixes
   high-confidence items and, on **zero `medium` (human-requiring) findings** +
   green CI + low risk tier, calls `enable_pr_auto_merge`. Medium findings route
   to a human instead. (Stations ⑦ + ⑧)
3. **Recurring/scheduled task injection** (deps, audits) — a cron that templates a
   card. (Station ①)

### Strategic (weeks, structural)

4. **Linear-webhook-driven headless dispatch** — closes the Linear autonomy gap
   _and_ adds the event clock in one move. (Gaps A + B)
5. **A measurement/ledger station** — stop deleting cards on PR-open; emit flow +
   revert metrics; surface them in `/list-tasks` or a Linear dashboard. (Gap C).
   Also the trust signal that lets the auto-merge gate (#2) safely widen.
6. **Risk-tiered autonomy** alongside the existing size gate. (Station ⑦) The
   prerequisite for trusting more auto-merge.
7. **Objective prioritization (RICE/WSJF) in a Linear custom field**, replacing
   hand-set `impact`. (Station ③)

### Foundational (the enabling theme)

8. **Capacity-aware WIP** so autonomous fan-out can't outrun human review — the
   guardrail that makes all the above _safe_ to switch on.
9. **Multi-channel Triage intake** (Sentry/Slack) — let production and users file
   the factory's work, closing the outer feedback loop.

---

## 6. One-paragraph summary

The workflow is an excellent **set of stations** with a genuinely strong middle
(refine → decompose → build) that already runs semi-autonomously and handles
concurrency cleanly. To become a **software factory** it needs three things:
a **clock** (a scheduler / Linear webhooks so the line moves without a human at
the keyboard), a **closed back half** (review wired into the loop, auto-merge on
green, and a deploy/measure stage), and a **memory** (stop discarding history;
measure flow and revert rate so the line learns and self-tunes). If Linear is the
substrate, its Triage, webhooks, cycles, SLA, and analytics are exactly the
missing conveyor and dashboard — currently used only as a card store, when they
could run the line.
