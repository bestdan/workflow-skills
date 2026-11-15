---
created: 2026-08-20
status: accepted
convention: ../local-review.md
---

# Preview comments anchor to leaf blocks and carry their line span

## Context

A plan document is mostly lists and tables. If only top-level blocks were
comment targets, a 40-item checklist would be one target, and the agent would
get "line 12" for a remark about a nine-line table and edit the header row.

## Decision

Each leaf-granularity token is a comment target: a list item, a table row, a
heading, a paragraph, and a fence as a whole. A preview comment carries
`kind: "block"` with `line` and `endLine`, the shape `dismiss-comments` already
used. Offsets are computed on the text after `\r\n` is normalized to `\n`,
since `marked` normalizes before lexing. The stripped frontmatter's line count
is added back. Only leaves contribute, because nested `raw` overlaps its
parent's, and table rows, which have no `raw` of their own, are line-counted
within the table's. Comments are symmetric across `split`, `single` and
`preview`. Landed in [#388](https://github.com/bestdan/workflow-skills/pull/388).

## Consequences

- Good, because a comment on one row of a table reaches the agent as that row's
  extent.
- Bad, because a mistake in the mapping is silent: the comment lands on the
  wrong lines and nothing errors.

## Revisit when

- `marked` starts reporting source positions itself, which would replace the
  hand-computed offsets.

## Confirmation

The block-to-line mapping has its own tests in `scripts/test-local-review.sh`,
including a CRLF fixture, run under `node` and skipped cleanly without it.

## Alternatives

- **Top-level blocks only:** a long checklist becomes effectively read-only.
- **A running offset from each token's `raw`:** drifts on CRLF files and
  double-counts nested tokens.
