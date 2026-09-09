---
title: Add --sort to gh-issue-graph.py for reoptimize Dimension 3
priority: medium
size: 2
impact: 3
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - commands/handlers/gh-issue-reoptimize.md:184-193
  - commands/handlers/assets/gh-issue-graph.py
  - scripts/test_gh_issue_graph.py
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - gh-issue
---

Plan: [[prose_to_code_plan]] · Index row 4, gh-issue half.

## Context

`gh-issue-graph.py` computes cycles, stale, satisfied and inverted edges, but
`gh-issue-reoptimize.md` Dimension 3 still asks the agent to fold in the
proposed edges, topologically sort the non-terminal nodes, and rank within
constraints by `prio:` label, then smaller `est:`, then age. Same algorithm as
task 4; the node and edge shapes differ (labels, `owner/repo#n`).

## Task

1. Add `--sort` to `gh-issue-graph.py`: optional `--edges <file>` with
   approved extra edges, output `order: [n,...]` alongside the existing
   fields. Reuse `prio_rank` for the vocabulary.
2. Extend `scripts/test_gh_issue_graph.py` with the ordering cases (tie-break
   by `est:` then `createdAt`; an extra edge that changes the order).
3. Rewrite Dimension 3 in `gh-issue-reoptimize.md` to run the helper.

## Acceptance Criteria

- Code-enforced: `just check` passes; the new tests fail if `est:` and age are
  swapped in the tie-break.
- Code-enforced: `gh-issue-reoptimize.md` Dimension 3 names the helper's
  `order` field instead of describing a sort.
