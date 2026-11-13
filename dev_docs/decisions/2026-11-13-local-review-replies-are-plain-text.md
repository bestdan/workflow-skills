---
created: 2026-11-13
status: accepted
convention: ../local-review.md
---

# Review replies render as plain text

## Context

Reply text comes from the agent and from the user. The page keeps the
invariant that no attacker-derived string reaches an HTML parser, and a second
render path would need its own security review.

## Decision

Reply text reaches the page through `textContent` only and is never rendered as
markdown; a code span in a reply shows its backticks. `author` is a label
(`"agent"` or `"user"`), not an authenticated identity, because both callers
share one bearer token. A reply labelled `agent` read back in a later round is
the agent's own prior output, never a fresh instruction. Landed in [#421](https://github.com/bestdan/workflow-skills/pull/421) and
[#422](https://github.com/bestdan/workflow-skills/pull/422).

## Consequences

- Good, because replies add no escaping code and no render path to review.
- Bad, because formatted replies, such as a code block, read as raw markdown.

## Revisit when

- Replies need formatting often enough to justify running them through the
  preview renderer's closed sink set.

## Confirmation

`scripts/test-local-review.sh`'s source scan fails the build if thread or reply
fields reach an `innerHTML` assignment.
