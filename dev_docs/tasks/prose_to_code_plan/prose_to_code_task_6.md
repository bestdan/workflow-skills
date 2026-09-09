---
title: Emit body_references from the graph scripts instead of hand-parsing bodies
priority: high
size: 3
impact: 5
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - commands/handlers/gh-issue-reoptimize.md:136-145
  - commands/handlers/gh-issue-reoptimize.md:173-182
  - commands/handlers/linear-reoptimize.md:96-121
  - commands/handlers/assets/gh-issue-graph.py
  - commands/handlers/assets/linear-relations.py
is_blocked_by: [prose_to_code_task_4, prose_to_code_task_5]
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
---

Plan: [[prose_to_code_plan]] · Index row 3.

## Context

Both reoptimize handlers ask the agent to parse every issue body for id
mentions and a fixed phrase list (`unblocks`, `blocked on`, `blocked by`,
`relies on`, `depends on`, `requires`, `with X in place`, `re-scoped per`,
`part of … plan`), classify strength, and pick edge direction — with the
warning that `unblocks` reverses direction and "a mistake here writes a real
dependency backwards and nothing catches it". The phrase → direction/strength
table is fixed; only the shared-subsystem inference in
`gh-issue-reoptimize.md:178-182` is judgment. Blocked on tasks 4 and 5 so the
two scripts are not edited concurrently.

## Task

1. Add one shared module `commands/handlers/assets/_body_refs.py` with
   `parse(body, self_id, id_pattern) -> [{target, phrase, direction,
   strength}]`. Direction: `blocked_by` for blocked on/by, relies on, depends
   on, requires, with X in place; `blocks` for unblocks; `related` for
   re-scoped per, part of, and a bare mention. Strength follows the handlers'
   tables. Exclude self-mentions.
2. Call it from `gh-issue-graph.py` (id pattern `(\S*#)?\d+`) and
   `linear-relations.py` (`<issue id="…">` and `[A-Z]+-\d+`), adding a
   `body_references` field per node and a `proposed` list of references with
   no matching native relation.
3. Tests for the module: every phrase, the `unblocks` reversal, self-id
   exclusion, a mention inside a code span (decide and pin: count it or not).
4. Rewrite Dimensions 1–2 in both handlers to read `proposed` and keep only the
   judgment step.

## Acceptance Criteria

- Code-enforced: `just check` passes; the `unblocks` test fails if direction is
  not reversed.
- Code-enforced: neither handler's Dimension 1 lists the phrase table any
  more; it links to `_body_refs.py`.
- User-run: `/reoptimize-tasks` on the PreThink team proposes the PRE-210 →
  PRE-189 `unblocks` edge the prose cites as its example, or reports that the
  relation is now native.
