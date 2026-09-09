---
title: Split gate() and rank_key() out of linear-ready.py for the MCP floor
priority: medium
size: 3
impact: 3
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - commands/handlers/linear-common.md:157-172
  - commands/handlers/linear-claim.md
  - commands/handlers/linear-list.md:50
  - commands/handlers/linear-promote.md
  - commands/handlers/assets/linear-ready.py:180-215
  - scripts/test_linear_ready.py
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - linear
---

Plan: [[prose_to_code_plan]] · Index row 17.

## Context

`linear-ready.py` implements the six ready gates and the priority sort
(urgent → high → medium → low, then None=0 last, then `updatedAt` ascending)
for the GraphQL fast path. The MCP floor — `linear-common.md`'s
"Ready-candidate selection" and its restatements in claim, list, promote and
reoptimize — asks the agent to "mirror that block exactly" by hand. The
"do NOT sort numerically ascending" warning marks a past bug.

## Task

1. Move `gate()` and `rank_key()` into `commands/handlers/assets/_linear_rank.py`;
   `linear-ready.py` imports them (no behaviour change; existing tests must
   pass untouched).
2. Add a floor entry point `linear-rank.py`: MCP `list_issues` JSON on stdin,
   `--max-estimate`, prints the gated, ranked candidates as JSON with a
   `dropped: {id: reason}` map so the prose can report why.
3. Tests for the entry point: the None-last rule, an over-estimate drop, a
   blocked-by drop, the `updatedAt` tie-break.
4. Rewrite the floor passages in the four handlers to one call and a pointer
   to the module for the gate list.

## Acceptance Criteria

- Code-enforced: `just check` passes; `scripts/test_linear_ready.py` is
  unchanged and green; the None-last test fails on numeric-ascending.
- Code-enforced: the six-gate table lives in one place (`_linear_rank.py`'s
  header) and `linear-common.md` links to it.
