---
title: Add linear-graph-analyze.py for cycles, topo order, priority inversions
priority: high
size: 3
impact: 5
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - commands/handlers/linear-reoptimize.md:90-91
  - commands/handlers/linear-reoptimize.md:133-149
  - commands/handlers/assets/linear-relations.py
  - commands/handlers/assets/gh-issue-graph.py:306
  - scripts/plan-graph.py
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - linear
---

Plan: [[prose_to_code_plan]] · Index row 4.

## Context

`linear-relations.py` fetches issues and derives `blockedBy` edges; it has no
analysis functions (`rg '^def '` shows only fetch/derive). `linear-reoptimize.md`
then asks the agent to detect cycles, topologically sort the non-terminal
nodes with an urgency → smaller estimate → age tie-break, and sweep every edge
for a priority inversion (blocker less urgent than dependent, `0`=None least
urgent). The prose warns against `priority ÷ estimate`. `gh-issue-graph.py`
already has `find_cycles`; `scripts/plan-graph.py` already has a Kahn's
ordering — read both before writing a third.

## Task

1. Add `commands/handlers/assets/linear-graph-analyze.py`. Input: the JSON
   `linear-relations.py` prints, on stdin or `--file`. Output: JSON
   `{cycles: [[id,...]], order: [id,...], inversions: [{blocker, dependent,
   blocker_priority, dependent_priority}]}`. Priority ordering: 1 → 2 → 3 →
   4 → 0 (None last). Exit 0 even with cycles (the caller reports them and
   never auto-resolves); exit non-zero only on malformed input.
2. If a cycle exists, `order` covers the acyclic remainder and the header
   comment says so.
3. Tests: `scripts/test_linear_graph_analyze.py` + `.sh` — a two-node cycle,
   the None-last ordering, an inversion with a `0` blocker, tie-break by
   estimate then `createdAt`.
4. Rewrite the cycle step and Dimension 3 in `linear-reoptimize.md` to run the
   helper and read its JSON; keep the "propose, human gate applies" prose.

## Acceptance Criteria

- Code-enforced: `just check` passes; tests pin the four cases.
- Code-enforced: `linear-reoptimize.md` no longer asks the agent to "detect"
  or "sort" — it names the helper and the fields it reads.
- User-run: `/reoptimize-tasks` on the PreThink team reports the same cycle
  set as the last manual run and at least one inversion if any exists.
