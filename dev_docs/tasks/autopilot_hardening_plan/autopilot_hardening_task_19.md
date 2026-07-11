---
title: "framing: de-anchor from 'overnight' — the property is UNATTENDED, not nocturnal"
priority: 2
size: 2
status: new
created: 2026-07-11
source_branch: main
related_files:
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/launch-runtime.md
  - skills/auto-pilot/references/run-state.md
  - skills/auto-pilot/references/run-budget.md
  - dev_docs/auto-pilot.md
is_blocked_by: autopilot_hardening_task_6
parent: autopilot_hardening
tags: [auto-pilot, docs, framing, p2]
---

[[autopilot_hardening_plan]]

## Context

Finding **#27** — raised by the human after detached run #2: *"you keep talking about
'overnight'… In fact, most of the work was done this morning."*

They are right, and it is **not a cosmetic complaint**. The docs are anchored on a
nocturnal story — and that anchor **caused real design errors**.

**The anchor, ~21 occurrences (8 in `SKILL.md` alone):**

- "pick up this Project and grind on it **overnight**" (the skill's description line)
- "Launch runs interactively, **tonight**, while the human can still fix failures"
- "a run that would otherwise pass green but **die at 3am**" (×4, SKILL + launch-runtime)
- "the human … closes the laptop and **goes to bed**"
- "the rolling human-facing report **the user wakes to**" / "the **morning** summary"
- "the run would cheerfully burn **the whole night**"

**What run #2 actually did:** launched 23:00, stalled 01:37–05:51 on an auth outage,
and then delivered **8 of its 9 tasks between 06:00 and 11:35** — in the morning, with
the human **at their desk**, reviewing and merging PRs in real time. The sleeping human
was a fiction for most of the run's life.

**The errors the anchor produced — each one bit us:**

1. **No alarm channel** (task 16). If the premise is "the human is asleep," alerting is
   pointless, so nothing alerts. That is *why* the 401 loop burned 4h14m in silence. Yet
   the human was awake at 05:55 and caught it in seconds — the alarm would have worked.
2. **`REPORT.md` as a batch digest, not a live surface.** "The report the user wakes to"
   is why there is no heartbeat and no live `status` (task 15), and why the human had to
   ask for a hand-rolled 15-minute polling loop to tell *working* from *wedged*.
3. **"Nothing prompts at 3am"** (SKILL step 3, launch-runtime §3). The real invariant is
   **"no human is attached to this process"** — true at 3am *and* at 10am when the human
   is in a meeting. Stating it as a fact about the *hour* is part of why the TCC consent
   gate (#24) was never actually probed.
4. **A mode the docs don't model at all: partially attended.** A human dipping in every
   20 minutes — exactly what happened — is neither "attended" nor "asleep." It is the
   *common* case, and it is the one where an alarm and a live status are most valuable.

**The correction.** The load-bearing property is **unattended** — *no human is currently
attached to this process* — which is about **attention, not the hour**. Detachment
outliving the launching session is a fact about **process lifetime**, not about
nightfall.

## Task

- **Sweep the auto-pilot surface** (`SKILL.md`, `references/*.md`, `dev_docs/auto-pilot.md`,
  `commands/auto-pilot.md`) and replace time-of-day framing with the property it was
  standing in for:
  | Instead of | Say |
  | ---------- | ---- |
  | "grind on it overnight" | "advance it unattended" |
  | "die at 3am" | "fail with no human attached" |
  | "nothing prompts at 3am" | "nothing prompts when no human can answer" |
  | "the human closes the laptop and goes to bed" | "the launching session ends; the run must outlive it" |
  | "the report the user wakes to" | "the run's live status surface + final report" |
  | "burn the whole night" | "burn the whole run" |
- **Keep the concrete, useful bits.** "Machine stays awake" is a *real* constraint
  (clamshell sleep kills a detached run) — keep it, but state it as a **power/sleep**
  requirement, not a bedtime story. Same for `--until`: a wall-clock bound is real; it
  is just not inherently nocturnal.
- **Name the three attendance modes explicitly**, and say which guarantees each needs:
  - **attended** — human in the loop; prompts are fine.
  - **partially attended** — human checks in periodically (**the common case**); needs a
    live status surface + alarms, and must never *block* on a human.
  - **unattended** — nobody watching; needs everything above *plus* fail-closed
    pre-flight (no consent gates, no interactive auth) and self-halt on the unrecoverable.
- **Fix the second-order consequences in the docs, not just the words**: `run-state.md`
  should describe `REPORT.md` as a **rolling, live** surface (not a morning digest), and
  `launch-runtime.md`'s credential/consent rules should key on *"no human can answer"*
  rather than *"3am"*.

Blocked by task 6 (it owns the reference/SKILL corrections pass) so the two doc edits do
not collide.

## Acceptance Criteria

**Code-enforced:**
- `rg -i '\bovernight\b|\btonight\b|\b3am\b|goes to bed|user wakes to|whole night'` over
  `skills/auto-pilot/` and `dev_docs/auto-pilot.md` returns **no hits** (a grep guard in
  the test suite, so the anchor cannot creep back).
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- The SKILL's description and opening line describe an **unattended** run with no
  reference to the hour, and the three attendance modes are stated with the guarantees
  each requires. A reader who runs auto-pilot at 10am sees their case described.
