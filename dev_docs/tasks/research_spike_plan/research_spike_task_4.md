---
title: "research-spike: question records, the coverage rule, and contracts/ coverage"
priority: high
size: 5
status: done
created: 2026-08-01
completed: 2026-08-02
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - scripts/research-spike.py
  - scripts/test-research-spike.sh
is_blocked_by: research_spike_task_1
parent: research_spike
expires: 2026-08-31
tags: [research-spike, script, validation]
---

[[research_spike_plan]]

## Context

Records alone only catch _malformed_ deferrals. They cannot catch the deferral
nobody registered, which is the actual failure mode — and the naive fix, a grep
for deferral vocabulary, asks a question that is **unanswerable over English
prose**.

The coverage rule asks a different question. Every question section must
declare something, including declaring — with a reason — that it owes nothing:

````markdown
```obligation
none: option (A) adds no observation and owes no tooling
```
````

"Did you use a deferral word?" is a heuristic. "Is the field present?" is
mechanical. **Forgetting stops being available; only deliberate silence
remains** — and deliberate silence is a thing a reviewer can see and challenge.

`contracts/` gets the same rule for a specific measured reason: contract
documents state preconditions constantly ("this component must not be deployed
on a shared host"), and a contract document is not a backlog. That class hid
the worst offender in the origin repo — a precondition that was **on no list at
all**. Unlike arbitrary prose this is a bounded, opt-in directory the skill
owns, so the mechanical rule is available here; declining it would be building
a labeled hiding place.

## Task

In `scripts/research-spike.py`, validate `question` records and enforce
coverage.

**Question records** — one fenced `question` block per `### Q<n>.` section in a
track's `questions.md` (section discovery landed in task 1):

- `id` kebab-case, unique under `project/track/` qualification (reuse task 3's
  uniqueness machinery — one implementation, not two).
- `status` ∈ `open | answered | retired`.
- `blocks` — one or more decision ids (comma-separated), **or** the reserved
  sentinel, whose canonical one-line form is `blocks: none: <reason>` (task 1's
  sentinel rule: the reason is verbatim and is **not** comma-split). Bare
  `blocks: none` with nothing after it fails — a question that gates nothing is
  worth noticing. Referential resolution of the named decisions belongs to task
  5; accept and record the ids here.

  > **Do not unify this with the bare `none:` coverage block below.** They mean
  > different things: `blocks: none: …` says the question **gates no decision**,
  > while a bare `none: …` block says the question **owes no obligations**.
  > Collapsing them makes a bare `none:` block inside a question section
  > ambiguous between the two, and the coverage rule becomes undecidable. The
  > bare coverage block is fenced as `` ```obligation ``, matching the design's
  > example.
- `retired` requires `retired_because:` — questions leave the board without
  pretending to be answered.
- `answered` requires **both**:
  1. an `answer:` one-line field on the block (the evidence lives in the
     section prose, but the conclusion must be stated in the record —
     `answered` cannot be reached without saying what the answer is); and
  2. the coverage rule below satisfied. **Coverage cannot be satisfied by prose
     alone.**
- A `### Q<n>.` section with no `question` block is an error; a `question`
  block outside such a section is an error.

**The coverage rule:**

- Every question section must contain at least one `obligation` block **or** a
  bare `none: <reason>` block.
- A `none:` block carrying any other field fails — it is a declaration, not a
  partially-filled record.
- A `none:` with an empty or missing reason fails.
- Scope is **questions files and `contracts/` only**. Do not widen coverage to
  every markdown file: that turns the discipline into noise and trains people
  to satisfy it mechanically (design §"What the skill must **not** do").

**`contracts/` coverage:**

- Every file under `tracks/*/contracts/` must contain at least one `obligation`
  block or a **file-level** `none: <reason>` block.
- The error message should name the precondition class this rule exists to
  catch, so the fix is obvious to someone who has not read the design.

## Acceptance Criteria

**Code-enforced:**

- New fixtures in `scripts/test-research-spike.sh`:
  - a question section declaring nothing fails the coverage rule;
  - an explicit `none: <reason>` satisfies coverage;
  - a `none` **without** a reason fails;
  - a `none` carrying other fields fails;
  - `status: answered` without an `answer:` field fails;
  - `status: answered` with an `answer:` but no coverage fails;
  - `status: answered` with both passes;
  - `status: retired` without `retired_because` fails;
  - bare `blocks: none` with nothing after it fails; `blocks: none: <reason>`
    passes;
  - `blocks: none: <reason>` whose **reason contains a comma** parses as one
    reason and **zero** decision ids — the sentinel exempts it from the
    comma-list rule (task 1), and without this the tail becomes a dangling
    reference in task 5;
  - a question section carrying **both** `blocks: none: …` and a bare `none:`
    coverage block validates clean, with both meanings intact;
  - a `### Q3.` section with no `question` block fails;
  - a contracts file with neither an obligation nor a `none` fails; with either,
    passes;
  - a markdown file **outside** questions/`contracts/` with no declaration is
    clean (coverage is deliberately not universal).
- `bash scripts/check.sh` green.

**User-run:**

- Take a question section in a scratch tree from `open` to `answered` by hand
  and confirm the validator blocks the transition until both the `answer:` and
  the coverage declaration exist.
