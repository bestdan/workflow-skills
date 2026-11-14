---
created: 2026-08-20
status: accepted
convention: ../local-review.md
---

# Preview builds the DOM from `marked`'s tokens through a closed set of sinks

## Context

`local-review`'s `preview` mode renders a wholly-added markdown file as a
document. The file can come from a fork PR, and the page holds the per-launch
token that authorizes `/submit`, the write the agent acts on. `marked` does not
sanitize: its renderer returns raw HTML verbatim, so "render, then allowlist the
output" was not buildable. Calling `createElement` and `textContent` is not safe
on its own either: `script` and `style` text is active, and `iframe.srcdoc`,
`object.data` and form actions need no HTML parse.

## Decision

Preview calls `marked.lexer()` only, never its parser or renderer, so no
attacker-derived string reaches an HTML parser. The safety comes from a closed
set of sinks rather than from the API names. The element type comes from a
hard-coded allowlist (`div`, `span`, `p`, `h1`–`h6`, `ul`, `ol`, `li`, the
table elements, `pre`, `code`, `blockquote`, `em`, `strong`, `del`, `a`, `hr`,
`br`), never from `token.type`. For ordinary markdown tokens, the only
attacker-influenced assignments are `textContent` and, on `a`, `href`. A
`mermaid` fence's drawn SVG is a separate sink, confined to a sandboxed frame;
see
[`2026-11-12-local-review-mermaid-in-a-sandboxed-frame.md`](2026-11-12-local-review-mermaid-in-a-sandboxed-frame.md).
An unknown token renders its `raw` as a
`span`'s text, and its fields are never walked. A link must carry `http:`,
`https:` or `mailto:` in its raw text before the platform `URL` parser sees it,
so relative and protocol-relative hrefs render as plain text with the URL
visible. A permitted link opens in a new tab with `rel="noopener noreferrer"`.
Images never load: they render as their alt text and URL.

Every response carries the `Referrer-Policy: no-referrer` header. It landed in
[#388](https://github.com/bestdan/workflow-skills/pull/388), when the token was
in the URL, and it still matters: after the cookie redirect the token URL
remains a bearer credential, and anyone holding it can post comments to the
review. The preview rules were hardened in
[#391](https://github.com/bestdan/workflow-skills/pull/391), and
[#481](https://github.com/bestdan/workflow-skills/pull/481) made relative hrefs
render as plain text.

## Consequences

- Good, because there is no sanitizer to bypass, and the property is
  structural: `describe()` is a pure function whose output a test can check
  for any tag or attribute outside the allowlist.
- Good, because a review never becomes a network beacon: the page fetches
  nothing off-host for a previewed file.
- Bad, because every new markdown construct needs a hand-written case, and
  until it gets one it renders as inert raw text.
- Bad, because the invariant closes HTML-parser injection only. Content
  spoofing and prompt injection into the agent are handled separately, in
  [`2026-08-20-local-review-preview-shows-what-the-file-hides.md`](2026-08-20-local-review-preview-shows-what-the-file-hides.md).

## Revisit when

- A markdown renderer ships that emits a DOM or a description tree under a
  stated sink allowlist, so the hand-written walk could go.
- The page gains a reason to load a previewed image, such as one served from
  the reviewed tree rather than off-host.

## Confirmation

`scripts/test-local-review.sh` runs a security corpus through `describe()`
under bare `node` (raw HTML, encoded and whitespace-padded `javascript:`,
relative and protocol-relative URLs, remote images, unknown token types,
`__proto__` frontmatter keys, deep nesting) and fails on any tag outside the
allowlist or any attribute outside `{href, class}`.

## Alternatives

- **Render with `marked` and sanitize the HTML:** `marked` has no sanitize
  option, and safety would rest on a sanitizer staying correct over content a
  fork PR chooses.
- **A prefix or regex check on the unresolved href:** relative,
  protocol-relative and entity-encoded forms pass it, and a relative href
  resolves under the token path.
