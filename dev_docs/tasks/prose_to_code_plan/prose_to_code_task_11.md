---
title: Add --from-mcp-json to linear-false-closures.py to build the PR file
priority: medium
size: 2
impact: 3
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - commands/handlers/linear-false-closures.md:100-133
  - commands/handlers/assets/linear-false-closures.py:226
  - scripts/test_linear_false_closures.py
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - linear
---

Plan: [[prose_to_code_plan]] · Index row 9, this repo's half.

## Context

When the cloud routine cannot run `gh`, the agent fetches merged PRs over MCP
and hand-builds the `--prs-file`: join `search_pull_requests` (bodies) with
`list_pull_requests` (head branches) on number, rename `merged_at` →
`mergedAt`, `head.ref` → `headRefName`, `html_url` → `url`, coerce null
title/body to `""` because `load_prs_file` dies on null. The pagination stop
rule (`updated_at`, never `merged_at`) is documented at length in
`dotfiles/agents/routines/nightly-linear-tidy.md`. The fetch stays with the
agent; the transform is code.

## Task

1. Add `--from-mcp-json <search.json> <list.json>` to
   `linear-false-closures.py`: join on number, die with the missing numbers on
   a failed join, rename, coerce nulls, and write the file `--prs-file`
   expects (or print it when `--prs-file -`).
2. Extend `scripts/test_linear_false_closures.py`: a clean join, a missing
   number, null body, the field renames.
3. Rewrite `linear-false-closures.md:100-133` to the one call; leave the
   pagination rule as the prose's remaining responsibility and say so.

## Acceptance Criteria

- Code-enforced: `just check` passes; the missing-number case exits non-zero
  and names it.
- User-run: run the flag on two saved MCP payloads from a real
  `search_pull_requests` / `list_pull_requests` call; `find-false-closures
  --prs-file` accepts the result.
- Follow-up, out of this repo's scope: rewrite
  `dotfiles/agents/routines/nightly-linear-tidy.md:261-321` to call the flag
  (file in `bestdan/dotfiles`).
