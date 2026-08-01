---
title: "research-spike: SKILL.md — the five procedures and the script/LLM boundary"
priority: high
size: 5
status: new
created: 2026-08-01
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - skills/research-spike/SKILL.md
  - skills/research-spike/references/record-grammar.md
  - skills/plan-with-docs/SKILL.md
  - scripts/validate.py
is_blocked_by: [research_spike_task_2, research_spike_task_7]
parent: research_spike
expires: 2026-08-31
tags: [research-spike, skill, docs]
---

[[research_spike_plan]]

## Context

The judgment half. Blocked until `init` (task 2) and the ledgers (task 7) exist
because the procedures are written **around** the script's actual behaviour —
documenting a verb surface before it runs is how the doc and the tool drift on
day one.

**Presenting the two layers as one flat verb list was reviewed as the most
likely way to implement the wrong half.** SKILL.md must state the boundary
before it lists anything: the script never edits prose; the LLM never computes
a status or a count. Every procedure ends by running `validate` — **the
validator, not the procedure, is what guarantees the result.**

Repo constraints: SKILL.md body ≤ 500 lines and `description` ≤ 1024 chars,
both enforced by `scripts/validate.py`; skill `name` must equal the directory
(`research-spike`). Detail goes in sibling reference files.

## Task

**`skills/research-spike/SKILL.md`** — new skill. Frontmatter `name:
research-spike` plus a `description` that triggers on research-spike
management, deferred/obligation tracking, and open-question convergence
without naming the skill (task 11 adds the eval that proves it).

Body, in this order:

1. **The boundary, first.** Script vs LLM, as above, with the one-line test:
   _if two runs over the same tree could disagree, it belongs to the script._
2. **The on-disk structure** and the id-scoping rule (`project/track/id`;
   `project/id` for decisions).
3. **The script surface** — the six subcommands, invoked as
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" …`. Note the
   `$CLAUDE_PLUGIN_ROOT`-unset fallback (Glob `**/scripts/research-spike.py`),
   matching `commands/handlers/linear-common.md`. Paths resolve against the
   **plugin**, not the target repo — this is the PRE-487 class of bug.
4. **The five procedures**, each a numbered walk-through:
   - `file` — add a question: prose, id, `blocks` (offer the existing decision
     list, or file a `proposed` decision in the agent's own track). Naming a
     nonexistent decision fails validation.
   - `answer` — walk a question to `answered`: record the `answer:` conclusion,
     then satisfy coverage — obligations registered or `none:` declared
     **before** the status flips.
   - `defer` — register an obligation next to the prose that creates it,
     creating a stub card when no destination exists and **saying so out
     loud**: the stub count is the diagnostic.
   - `backfill` — section-by-section interactive walk of an existing doc,
     importing free-form questions into the structured format. State plainly
     that **the backfill is the moment of truth** — in the reference repo it
     produced 13 records, 4 with no destination at all.
   - `promote-decision` — organizer-only: move a track's `proposed` block into
     `decisions.md`.
5. **Write-ownership convention.** Track agents touch `tracks/<theirs>/` only,
   proposed decisions included; `decisions.md` and `LEDGER.md` are
   organizer-owned. Nothing enforces this directly — every cross-track effect
   is caught by the organizer's project-wide `validate`. One agent at a time
   per track; a collision surfaces as an ordinary git merge conflict.
6. **The agent-context contract.** An agent told to "work track X" needs
   `tracks/X/` plus `decisions.md` and nothing else. State it explicitly so a
   future `/work-track` dispatcher or auto-pilot integration composes without
   redesign.
7. **The discipline the tool cannot supply** — register at deferral time not
   review time; count what you are actually spending (questions-answered is the
   flattering metric, obligations-open the predictive one — report both or
   neither); be honest about granularity (the reference ledger read _10 open_
   against a hand count nearer 23, because records are registered at the
   altitude the deferral was made — both true, measuring different things);
   a stub that never gets superseded is a new place for work to hide.
8. **What the skill must not do** — the five bullets from the design, each with
   its "or this becomes theatre" reason.
9. **Known limits, stated rather than hedged** — it cannot see an unregistered
   deferral; coverage reaches questions files and `contracts/` only; the ledger
   counts declared debt, not tasks; bridged work is not reconciled; the
   divergence signal is a snapshot.

**`skills/research-spike/references/record-grammar.md`** — the full field
reference for all four record types plus `none:`, the parser's rules (one
`key: value` per line, unknown keys are errors, **no inline comments**,
comma-separated lists), and a worked example of each. This is what keeps
SKILL.md under the line cap.

## Acceptance Criteria

**Code-enforced:**

- `uv run scripts/validate.py` passes: `name` matches the directory,
  `description` ≤ 1024 chars, body ≤ 500 lines.
- `claude plugin validate . --strict` passes.
- `bash scripts/check.sh` green (`dprint check` included).

**User-run:**

- Follow `defer` end to end in a scratch repo without consulting the design
  doc, and confirm you end with a passing `validate` and a stub card that says
  when it should be deleted.
- Confirm SKILL.md states the script/LLM boundary before it lists any verb — a
  reader skimming the first screen should not be able to mistake a procedure
  for a command.
