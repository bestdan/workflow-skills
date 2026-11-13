---
created: 2026-11-13
status: accepted
convention: ../local-review.md
---

# Preview refuses a document over a size cap rather than lexing it

## Context

`marked`'s advisory history is mostly ReDoS, plus one out-of-memory advisory
(`GHSA-6v9c-7cg6-27q7`, patched in 18.0.2). Preview is the default for a
wholly-added markdown file, so a hostile file is lexed on page load. Draft
comments live only in page memory, so a wedged tab loses every unsent comment
in every file.

## Decision

Preview refuses to lex a document over 512 KiB, measured in UTF-8 bytes rather
than `String.length`, which counts UTF-16 code units and would admit a CJK
document at about three times the cap. A refused file falls back to `single`
with a visible notice. A depth cap of 24 bounds the walk over the tokens
`marked` has already produced, not the lexer; past it the remaining tokens
render as inert visible text rather than being dropped. `marked` is vendored at
one exact version, recorded with its SHA-256 in `scripts/local-review/NOTICE`.
Lexing in a terminable Worker with a time budget is deferred. Landed in
[#388](https://github.com/bestdan/workflow-skills/pull/388).

## Consequences

- Good, because the caps are cheap and cover the known advisory shape.
- Bad, because the caps bound input size, not lexer time: a ReDoS pattern that
  fits under 512 KiB still blocks the tab.
- Bad, because a legitimate document over the cap loses preview.

## Revisit when

- A `marked` advisory lands that a sub-cap input triggers; that is the case for
  the Worker.
- Real documents under review approach the cap.

## Confirmation

`scripts/test-local-review.sh` asserts that an over-cap document, including a
multibyte one under the cap in code units, is refused, and that deep nesting
degrades to visible text.

## Alternatives

- **Pinning alone:** a pin does not bound a vulnerability the pinned version
  has.
- **A terminable Worker now:** stronger containment, deferred because the caps
  cover the known shape at a fraction of the cost.
