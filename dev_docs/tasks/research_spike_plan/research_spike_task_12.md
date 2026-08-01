---
title: "research-spike: adoption playbook and a rehearsed backfill"
priority: medium
size: 3
status: new
created: 2026-08-01
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - skills/research-spike/SKILL.md
  - skills/research-spike/references/adoption.md
  - scripts/check.sh
is_blocked_by: [research_spike_task_10, research_spike_task_11]
parent: research_spike
expires: 2026-08-31
tags: [research-spike, adoption, docs]
---

[[research_spike_plan]]

## Context

The mechanism is worthless empty. The design's setup sequence exists because
the backfill — not the install — is where the finding lives: in the reference
repo it produced **13 records, and 4 of them had no destination at all**,
requiring stub cards to be created before they could be registered. **Needing
stubs to complete a backfill is the finding, not an inconvenience.**

The sequencing lesson applies to this very task. The instrument was first built
stacked behind the plan it was auditing, which had it describing only work
already done — the exact posture it exists to criticise. **Land the instrument
first, then let live work register its own deferrals.** Hence: adoption last,
and the first real project registers its own deferrals rather than being
annotated retroactively.

## Task

- **`skills/research-spike/references/adoption.md`** — the setup playbook,
  ordered as the design orders it:
  1. `init` the project and its first track;
  2. **backfill every deferral already made** — the payload;
  3. create stub cards for the ones with no destination, and **report that
     count out loud**;
  4. wire `validate` into the repo's check, and document it in the check
     contract **including the reasoning for what was left out** (`suggest`,
     and why a check whose false positives have nowhere to go must not be able
     to fail the build);
  5. note the advisory-tier generalization: a repo that _has_ one may run
     `suggest` in CI as a non-failing report.
- Include the stub-hygiene rule prominently: a stub carries
  `superseded_when:`, the condition of its own deletion; one stub in the
  reference implementation exists only until two real cards appear, and says
  so.
- **This repo does not gate on its own research tree** (plan decision 2). Leave
  `scripts/check.sh` running the fixture harness only, and **say why in the
  check contract**: there is no `dev_docs/research/` tree here yet, and a gate
  over a nonexistent tree measures nothing. The playbook states what to add
  (`python3 scripts/research-spike.py validate --strict`) and when — the moment
  a real project is initialized, in the same PR that initializes it.
- **No live project is initialized by this plan.** The first spike — the
  auto-pilot E-lite substrate questions in PR #243 are the obvious candidate —
  is adopted by whoever runs it, following this playbook. Do not backfill
  another branch's work from here: live work registers its own deferrals rather
  than being annotated retroactively by someone else's PR, which is the exact
  posture the instrument exists to criticise.

## Acceptance Criteria

**Code-enforced:**

- `bash scripts/check.sh` green, still running the fixture harness and **not**
  a repo-tree `validate` (there is no tree to validate).
- The playbook's **deterministic spine** is a fixture in
  `scripts/test-research-spike.sh`, not prose: `init` a scratch project and
  track under `mktemp -d`, **hand-write** an obligation with no resolvable
  destination plus the stub card that fixes it (exactly the records the playbook
  tells a human to produce), then `write-ledger` → `validate --strict` exits 0.
  Also assert the **negative**: the same tree _without_ the stub card **fails**
  `validate`, so the fixture proves the stub is load-bearing rather than
  decorative.

  > `backfill` itself is **not** code-enforceable and must not be written as a
  > shell assertion. It is a SKILL.md procedure — an interactive walk (task 9)
  > — and creating the stub card is the `defer` procedure's judgment act, which
  > the script is explicitly _forbidden_ to perform ("silently create a
  > destination to make a record resolve" is on the design's must-not list).
  > Putting that chain under Code-enforced would be this plan breaking the
  > script/LLM boundary the whole design rests on, inside an acceptance
  > criterion. The fixture above covers the deterministic half; the User-run
  > bullet below covers the LLM half.

**User-run:**

- Run the `backfill` procedure end to end against a scratch repo carrying at
  least one destination-less deferral, and confirm it produces the stub card the
  fixture above hand-writes. This is the LLM half of the playbook and is
  deliberately not code-enforced.
- Read the backfill output and confirm the stub count was stated plainly rather
  than buried — if the number of destination-less deferrals is not visible, the
  playbook has failed at the one thing it exists to surface.
- Confirm the check contract explains what was deliberately left out of the
  gate, not just what was put in.
