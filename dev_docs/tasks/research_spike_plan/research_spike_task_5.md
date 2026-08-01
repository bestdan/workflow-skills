---
title: "research-spike: decisions and cross-record referential integrity"
priority: high
size: 5
status: new
created: 2026-08-01
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - scripts/research-spike.py
  - scripts/test-research-spike.sh
is_blocked_by: [research_spike_task_3, research_spike_task_4]
parent: research_spike
expires: 2026-08-31
tags: [research-spike, script, validation]
---

[[research_spike_plan]]

## Context

Decisions are the convergence hook: questions and obligations name the decision
they block, so "what still blocks building?" becomes a derived fact instead of
a feeling. Blocked on tasks 3 and 4 because the references it checks
(`blocking:` on obligations, `blocks:` on questions) are parsed there.

**Decision records store only the human lifecycle state; everything else is
derived.** There is no stored `blocked`/`ready`: storing both was reviewed as a
defect, not a convenience, because a stale stored status can disagree with the
derived one. Enforce that by making any such key an unknown-key error — the
record format itself leaves nothing to hand-edit wrongly.

The `decided_in:` pointer must be **durable**: an ADR, a permanent design doc,
or a receipt card. **Never a plan directory** — `/push-plan` deletes plan
directories after tracker migration, so that pointer would rot on first push.
(This plan's own folder is exactly such a directory; see task 13.)

## Task

In `scripts/research-spike.py`, validate `decision` records and resolve
references across record types.

**Decision records** (`decisions.md`, project-level, organizer-owned):

- `id` kebab-case, unique under `project/` qualification.
- `state` ∈ `pending | decided`. Any other state key or value — notably a
  stored `ready` or `blocked` — is an error.
- `decided` requires `decided_in:`. Validate durability structurally: the value
  must resolve to an existing file that is **not** under a `*_plan/` directory,
  and the error must explain the `/push-plan` deletion hazard.
- A reopened decision is `state: pending` plus `reopened_because:`;
  `reopened_because` on a never-decided decision is an error.

**Proposed decisions** — filed by track agents as `state: proposed` blocks
inside their own `questions.md`, next to the question that needs them:

- valid only inside a track's `questions.md`, never in `decisions.md`;
- a `blocks:` reference to a proposed decision is **valid**;
- promoting one into `decisions.md` is an organizer act (the SKILL.md
  `promote-decision` procedure, task 9) — the script never promotes.

**Referential integrity:**

- Every `blocks:` (questions) and `blocking:` (obligations) id must name an
  existing decision — `decided`, `pending`, or `proposed` — resolved **within
  the enclosing project**. A dangling reference is an error: this is the "track
  that did not exist" bug, prevented for decisions too.
- A decision that nothing references is a **warning** (dead decision), not an
  error.
- Filing a **new blocker against a `decided` decision** is an error unless the
  decision is explicitly reopened. "New" means the referencing question is
  `open` or the referencing obligation is `open` — a historical, already-closed
  reference to a decided decision must stay clean.
- `state: decided` requires **zero open blockers at decision time**: no open
  question `blocks:` it and no open obligation is `blocking:` it. Error, not
  warning — this is what makes hand-editing the files safe to allow.
- Cross-project references are impossible by construction (task 3 forbids
  cross-project destinations); assert that a `blocks:` naming a decision that
  exists only in a **sibling project** fails as dangling.

## Acceptance Criteria

**Code-enforced:**

- New fixtures in `scripts/test-research-spike.sh`:
  - `blocks:` naming a nonexistent decision fails;
  - `blocks:` naming a `proposed` decision in the same track passes;
  - a `proposed` block inside `decisions.md` fails;
  - a stored `ready:`/`blocked:` key on a decision fails as an unknown key;
  - `decided` without `decided_in:` fails;
  - `decided_in:` pointing into a `*_plan/` directory fails, and the message
    names the `/push-plan` hazard;
  - `decided_in:` pointing at an ADR or receipt card passes;
  - `decided` with an open question blocking it fails;
  - `decided` with an open **obligation** `blocking:` it fails;
  - a new open blocker against a `decided` decision fails; the same with
    `state: pending` + `reopened_because:` passes;
  - `reopened_because` on a decision that was never decided fails;
  - a decision nothing references produces a **warning** and still exits `0`;
  - `blocks:` naming a decision that exists only in a sibling project fails.
- `bash scripts/check.sh` green.

**User-run:**

- Reopen a decided decision in a scratch tree and confirm the validator goes
  from rejecting the new blocker to accepting it, with no other edits.
