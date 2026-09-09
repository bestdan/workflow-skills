---
title: Add linear-pr-resolve.py for sweep-complete's PR discovery and merge-state
priority: high
size: 5
impact: 5
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - commands/handlers/linear-sweep-complete.md:187-353
  - commands/handlers/linear-sweep-complete.md:410-433
  - commands/handlers/linear-claim.md:113
  - commands/handlers/assets/linear-false-closures.py:202
  - commands/handlers/assets/linear-scan.py
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - linear
---

Plan: [[prose_to_code_plan]] · Index row 1.

## Context

For each in-flight issue `linear-sweep-complete.md` asks the agent to try three
PR sources in order (links attachment, `gh pr list --search` by identifier,
`gh pr list --head` by branch), resolve the repo per issue, distinguish "no PR"
from "probe failed" (fail closed to `left: unresolved`), then classify the
issue by merge-state precedence (any MERGED → done; else any OPEN; else any
unread → unresolved; else all closed-unmerged). The whole-token identifier
match is restated in `linear-claim.md`. The 2026-09-02 nightly run reported
PRE-73 as "genuinely no PR found yet" while open PR #1003 existed — the
bracket-only title match. `linear-false-closures.py` already has `merged_prs`
and `pr_identity`; share them.

## Task

1. Add `commands/handlers/assets/linear-pr-resolve.py`. Input: JSON on stdin
   — an array of issues with `id`, `identifier`, `attachments`, `project` —
   plus `--config` for `linear.projects[].repo` and a `--repo` fallback. It
   runs the three `gh` probes itself (they are CLI, not MCP), records
   `resolved_via`, and treats a non-zero probe exit as `unresolved: true`,
   never as "no PR".
2. Identifier match: `(?<![A-Za-z0-9])<ID>(?![A-Za-z0-9])` — a
   non-alphanumeric boundary on both sides, as `linear-sweep-complete.md:215-217`
   states. One function, with the PRE-73 title as a positive fixture and
   `PRE-73a` / `PRE-730` as negative ones.
3. `classify(prs)` → `merged | open | unresolved | closed_unmerged` in that
   precedence; output one JSON row per issue with `prs`, `state`, `resolved_via`.
4. Tests with the `gh` seam stubbed: each source in isolation, probe failure
   → unresolved, the precedence table, the PRE-73 regression.
5. Rewrite `linear-sweep-complete.md` steps 3.x and the classification step to
   call the helper; drop the duplicate identifier rule from `linear-claim.md`
   in favour of a pointer.

## Acceptance Criteria

- Code-enforced: `just check` passes; the PRE-73 fixture fails on a
  bracket-only match.
- Code-enforced: `linear-sweep-complete.md` no longer describes the three
  probes as agent steps.
- User-run: the nightly-linear-tidy routine's sweep step reports PRE-73 (or
  the current equivalent) with `resolved_via: title` instead of "no PR".
