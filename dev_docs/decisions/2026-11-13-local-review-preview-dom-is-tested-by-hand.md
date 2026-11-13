---
created: 2026-11-13
status: accepted
convention: ../local-review.md
---

# Preview's DOM half has no automated tests, by choice

## Context

Testing DOM code needs a DOM. `jsdom` is an npm dependency the development
environment could not fetch, and a large one to vendor. A hand-rolled DOM shim
that disagrees with a real browser produces passing tests over broken
behaviour.

## Decision

The preview renderer is split in two. `describe(tokens)` returns a plain tree
of `{tag, text, attrs, kids}` with no DOM; it carries every security-relevant
choice and the line mapping, and runs under bare `node`. `materialize(desc)`
turns that tree into elements, holds no logic, and is reviewed by eye.
`materialize()` and the comment-chip lifecycle have no automated coverage and
will not get any. Landed in [#388](https://github.com/bestdan/workflow-skills/pull/388).

## Consequences

- Good, because the security property and the anchoring are machine-checked.
- Bad, because DOM-timing defects pass the suite. One reached a human reviewer
  while this landed: chips were inserted before their element had a parent,
  `insertAdjacentElement` on a detached node did nothing, and restored comments
  never rendered while the submit count still counted them.
- Bad, because anyone extending preview has to smoke-test the DOM by hand and
  must not read a passing suite as covering it.

## Revisit when

- A DOM implementation becomes available to the test harness without a
  network fetch at test time.

## Confirmation

Nothing beyond the split itself; the DOM half rests on manual smoke tests.

## Alternatives

- **Vendor `jsdom`:** large, and a second runtime to keep current.
- **A hand-rolled DOM shim:** a fake that disagrees with a real DOM gives false
  confidence.
