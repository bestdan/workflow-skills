---
created: 2026-11-12
status: accepted
convention: ../local-review.md
---

# Preview draws mermaid fences inside a sandboxed iframe

## Context

Preview builds every element itself from `marked`'s tokens and never hands an
attacker-derived string to an HTML parser, per
[`2026-08-20-local-review-preview-builds-dom-from-tokens.md`](2026-08-20-local-review-preview-builds-dom-from-tokens.md).
A diagram renderer returns an SVG string, which has to be parsed to be drawn.
A drawn diagram also hides text its source carries: `%%` comments and
`accTitle`/`accDescr`.

## Decision

A `mermaid` fence is drawn by the vendored Pele, and Pele's SVG string goes only
into the `srcdoc` of an `<iframe sandbox="">`: no scripts, an opaque origin, no
forms, popups or top navigation, and a CSP in the frame document of
`default-src 'none'` with inline styles allowed. The page itself never parses
the SVG. The invariant is about the page that holds the token, and a frame that
can neither run code nor read that page is outside it. Output carrying a link,
refresh or base element is refused, and a document draws at most 50 diagrams or
200 KiB of diagram source. The fence stays one comment target. Its source is a
toggle away and opens by default when it holds `%%` comments or
`accTitle`/`accDescr`, and a missing or failing Pele leaves the fence as plain
code. Landed in [#1511](https://github.com/bestdan/workflow-skills/pull/1511).

## Consequences

- Good, because diagrams in plans render as diagrams without weakening the
  page's invariant.
- Bad, because the safety now also rests on the browser enforcing the iframe
  sandbox and the frame's CSP.

## Revisit when

- A browser weakens `sandbox=""` or `srcdoc` CSP enforcement.
- Pele, or a replacement, can emit a description tree the page could
  materialize itself.

## Confirmation

`scripts/test-local-review.sh` asserts that the sandbox frame is the only place
a diagram's SVG is parsed, that the frame document carries a fetch-blocking CSP
and adds no script of its own, that SVG carrying a link, refresh or base
element is refused, that the per-document count and character budgets stop
drawing at their caps, that `%%` comments and accessibility text open the
source by default, and that the fallback still shows the whole fence body.

## Alternatives

- **Render mermaid fences as highlighted code only:** safe, but a plan's
  diagrams stay unreadable.
