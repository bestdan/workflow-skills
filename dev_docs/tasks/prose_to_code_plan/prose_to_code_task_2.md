---
title: Add diff-anchor-check.py for co-review's batched review POST
priority: high
size: 2
impact: 3
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - skills/co-review/SKILL.md:383-388
  - skills/co-review/references/permissions.md
  - scripts/local-review/server.py
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - co-review
---

Plan: [[prose_to_code_plan]] · Index row 10.

## Context

`co-review` step 12 posts one atomic review. Before the POST the prose asks the
agent to "anchor-check every comment: confirm its `line` is among the diff's
added/modified right-side lines, and fold any that don't anchor into `body`".
A single bad line rejects the whole review and posts nothing. Building the set
of valid right-side lines per file from hunk headers is deterministic and is
currently done by the agent reading the diff.

`scripts/local-review/server.py` already parses unified diffs (`parse_diff`);
reuse its hunk logic rather than writing a second parser. GitHub accepts a
review comment on any line present on the diff's right side — added and
context lines inside a hunk — and rejects deleted lines and lines outside
every hunk; encode that rule in the header comment.

## Task

1. Add `scripts/diff-anchor-check.py`. Inputs: `--diff <file>` (unified diff)
   and a JSON array of `{path, line, body}` on stdin. Output: JSON
   `{anchored: [...], unanchored: [...]}`; unanchored comments keep their
   fields so the caller folds them into `body`.
2. Add `scripts/test_diff_anchor_check.py` + `scripts/test-diff-anchor-check.sh`:
   a multi-hunk diff, a comment on a deleted line (unanchored), a comment on a
   context line (anchored), a renamed file, a file not in the diff.
3. Rewrite `skills/co-review/SKILL.md` step 12 to run the helper and use its
   split; keep the "retry once with the offending comment moved to body"
   fallback for the case the helper and GitHub disagree.
4. Check whether the invocation needs an allow-rule in
   `skills/co-review/references/permissions.md` before adding one; it is a
   read-only local script.

## Acceptance Criteria

- Code-enforced: `just check` passes; tests pin the five cases.
- Code-enforced: `skills/co-review/SKILL.md` step 12 names the helper and its
  stdout contract; `validate.py`'s fenced-block check stays green.
- User-run: `/co-review --post` on a PR with one comment aimed at a deleted
  line posts successfully with that comment folded into the body.
