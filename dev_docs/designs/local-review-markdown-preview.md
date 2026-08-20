# local-review — rendered markdown preview

Design for [#382](https://github.com/bestdan/workflow-skills/issues/382): a
third per-file view mode that renders a wholly-added markdown file as the
document it is, rather than as source.

Builds on the view-mode machinery merged in #380. Vocabulary and the
constraints that look arbitrary from outside are in
[`../decisions/local_review_view_modes.md`](../decisions/local_review_view_modes.md).

This revision incorporates an adversarial review by two independent reviewers.
Where the first draft was wrong, the wrongness is recorded rather than quietly
corrected — the mistakes are instructive.

## The problem

`single` fixed the wasted column for one-sided files. A markdown plan still
reads as source: tables are pipe soup, nested lists lose structure, emphasis is
asterisks. Preview is the half of the view-mode work that addresses the case
that motivated all of it.

## Scope

`preview` is offered only when `status == "added"` **and** the file is
markdown. For a modified file the diff carries only hunk fragments — a fence
opened outside the hunk never closes, a list item arrives without its parent —
so rendering it produces a document that is confidently wrong. Reading the full
file from disk was considered and rejected: it works in `--git` modes and
silently does not exist for `--diff-file` or `gh pr diff` of someone else's PR.

Preview must **refuse** rather than guess when its inputs are not what it
assumes. Concretely it falls back to `single` when the reconstructed row count
disagrees with the hunk's `@@` counts (a truncated diff), or when the document
exceeds the size bounds below.

## Threat model

`local-review.md` already states the governing principle: loopback is not a
trust boundary. Three assets are at risk, and the first draft of this document
only considered one of them.

1. **The per-launch token**, which appears in the page URL. Until #381 lands,
   `/submit` also carries a `gh` write capability.
2. **The reviewer's judgment.** The page is the evidence the human approves
   from. Content that is present in the file but invisible in the rendering
   corrupts that judgment.
3. **The agent.** `/submit` writes a file the agent reads and acts on, and the
   payload's `code` field carries attacker-authored text verbatim.

Diff content is attacker-influenceable: it can come from a fork PR.

## Decision: build the DOM from the token stream

**No attacker-derived string is ever passed to an HTML parser.**

That is the precise claim. The first draft said "never generate an HTML string
from untrusted input" and separately that this "closes the injection
question" — both too broad, for reasons recorded under _Corrections_ below.

Use `marked`'s **lexer** only — `marked.lexer(src)` — and walk the tokens.
`marked`'s parser and renderer, the half that emits HTML, are never called.

### The invariant

`createElement` plus `textContent` is **not** inherently safe, and saying so
was the first draft's worst error. `script` and `style` `textContent` is
active; `iframe.srcdoc`, `object.data`, and form actions need no `innerHTML`.
The safety comes from a closed set of sinks, not from the API names:

- **Element type comes from a hard-coded allowlist**, never derived from
  `token.type`. Permitted: `div`, `span`, `p`, `h1`–`h6`, `ul`, `ol`, `li`,
  `table`, `thead`, `tbody`, `tr`, `th`, `td`, `pre`, `code`, `blockquote`,
  `em`, `strong`, `del`, `a`, `hr`, `br`. Nothing else. No SVG or MathML
  namespaces, no custom elements.
- **The only attacker-influenced assignments are `textContent` and, on `a`,
  `href`.** Never `style`, `src`, `srcdoc`, `data`, `action`, `formAction`,
  `id`, `name`, or any `on*` property.
- **Unknown tokens hit a fallback that puts `String(token.raw ?? "")` into a
  `span`'s `textContent`.** It never derives an element from the token type and
  never walks arbitrary token fields.

### Links

The first draft said "scheme check against an allowlist", which is
underspecified to the point of being wrong. A prefix or regex test on an
unresolved string is defeated by relative URLs, protocol-relative URLs,
control characters, and entity forms — and a relative href resolves _under the
token path_, so `[click](../submit)` navigates inside the authorized tree.

- Require an allowed scheme in the raw text (`/^(https?|mailto):/i`, after
  trimming) **before** parsing. Resolving against the page base first would
  accept a relative href and quietly turn it into a same-origin URL; requiring
  the scheme up front rejects relative and protocol-relative forms by
  construction rather than by a follow-up origin comparison.
- Then parse with the platform `URL` parser — no base — and test the canonical
  `.protocol` against exactly `http:`, `https:`, `mailto:`. The parse is what
  catches forms the regex would wave through.
- A markdown file in a diff has no legitimate same-origin target here.
- Rejected links render as plain text with the URL visible, never as an `a`.
- Permitted links get `rel="noopener noreferrer"` and `target="_blank"`.
- The server sends `Referrer-Policy: no-referrer` on every response. The token
  is in the URL, so any outbound navigation is a token disclosure. This is a
  **header**, not a CSP directive — the first draft conflated the two.

### Images

`image` tokens never load. They render as a placeholder showing alt text and
the URL as text. The page fetches nothing off-host today and a preview that
loaded a remote image would turn a review into a network beacon.

### Fenced code

Fences render **`textContent`-only, unhighlighted**.

Highlighting would mean reusing the existing `hljs` path, which is
`innerHTML`-based (`server.py:770`, `server.py:903`). That path escapes via a
vendored library rather than structurally, and routing automatically-rendered
attacker markdown through it would reintroduce exactly the posture this design
exists to avoid. Syntax color in a plan document's short fences is not worth a
permanent carve-out at the one boundary under review.

This asymmetry is deliberate, not incoherent: new code need not repeat an
existing weakness. See _Security debt_ for the existing path.

**Superseded by #385.** The `innerHTML` path is gone: highlighting now reads
hljs's token tree and builds spans with `textContent`, so there is no weakness
left to keep fences away from. A **labelled** fence renders highlighted through
that shared path. An unlabelled one still renders plain, but for a different
reason than this section gave: not the `innerHTML` posture, which is gone, but
the cost of `highlightAuto` running every vendored grammar over a body a fork
PR chooses. See [`../local-review.md`](../local-review.md) for the current
posture and the measurements.

### Frontmatter

Recognized only as a `---` line at offset 0 with a matching closing `---`; an
unterminated opener is not frontmatter and the document renders normally. Not
YAML-parsed — split into key/value on the first `:` per line, kept as an
**array of records** (never a plain object, which would take a `__proto__` key
straight into prototype pollution), both halves rendered via `textContent`.
Rendered visibly, not hidden — see _What the reviewer must be able to see_.

### Bounding the lexer

The first draft claimed "mitigation is limited to pinning", which is false.
`marked`'s advisory history is dominated by ReDoS and includes a live OOM
(`GHSA-6v9c-7cg6-27q7`, high, CVSS 7.5, patched 18.0.2). Preview is the
_default_ for added markdown, so a hostile file parses on load.

- Refuse to lex above a byte cap — measured in **UTF-8 bytes**, since a
  `String.length` check counts UTF-16 code units and would let a CJK document
  through at roughly three times the stated size. Fall back to `single` with a
  visible notice.
- The **depth** cap bounds the token _walk_, not the lexer: it is applied while
  describing tokens marked has already parsed. Say so plainly rather than
  implying it bounds parsing. At the cutoff, remaining tokens render as inert
  visible text — never dropped, per the visibility commitment below.
- Blast radius is worse than the first draft's "a wedged tab, not a leaked
  token": draft comments live only in page memory, so a wedge destroys every
  unsent comment across every file.
- Lexing in a terminable Worker with a time budget is the stronger containment
  and is deliberately deferred — the caps are cheap and cover the known shape.

## What the reviewer must be able to see

Preview is a **rendering, not a completeness guarantee** — and this is where
preview differs in kind from `single`, because a rendering can omit.

Markdown has several places where text lives in the file, reaches the agent,
and is invisible in a normal rendering: link titles (`[x](url "text")`),
reference definitions (`[x]: url "text"`), HTML comments, and frontmatter. An
approval given in preview would otherwise bless content the reviewer never saw.

- `html` tokens render as escaped text in a muted style — visible as raw HTML,
  inert.
- Link titles, reference definitions, and frontmatter are rendered visibly.
- The preview surface is visually bounded and labelled as untrusted PR content,
  so attacker prose does not inherit the page's own authority. Rendered
  headings and task lists can otherwise imitate review instructions.

## Comment anchoring

Each leaf-granularity token maps to a source line range. Leaf granularity means
each **list item** and each **table row** is its own target; a fence is one
target. A plan is mostly lists and tables, and top-level-only granularity would
make a 40-item checklist a single target — functionally read-only.

The naive "accumulate a running offset from each token's `raw`" from the first
draft does not work. Explicit invariants, each with a test:

- **`marked` normalizes `\r\n` to `\n` before lexing.** Offsets must be
  computed against the normalized text, or every anchor after line 1 drifts
  silently on a CRLF file.
- **Frontmatter is stripped before lexing**, so its line count is added back.
- **Nested token `raw` overlaps its parent's** — only leaves contribute.
- **Table rows have no own `raw`**; row offsets are derived by line-counting
  within the table token's `raw`.
- **Tokens lacking `raw`** inherit their parent's range rather than guessing.

Payload gains `kind: "block"` and `endLine`, reusing the shape
`dismiss-comments` already uses. Without `endLine` the agent gets "line 12" for
a remark about a nine-line table and edits the header row.

Comments stay symmetric across all three modes.

## Testing

Splitting the renderer in two makes the security-critical half testable without
a DOM, which matters because `jsdom` is an npm dependency and this environment
cannot reach the registry:

- `describe(tokens)` → a plain tree of `{tag, text, attrs, children}`. Pure,
  no DOM, testable under plain `node` via the resolution helper from #383.
- `materialize(desc)` → DOM nodes. ~15 lines, no logic, eyeball-reviewable.

The security corpus asserts against `describe()` output: raw HTML tokens,
`javascript:` in encoded and whitespace-padded forms, protocol-relative and
relative URLs, remote image URLs, unknown token types, malformed tables,
`__proto__` frontmatter keys, and deep nesting. The assertions are that no
`tag` outside the allowlist appears, no attribute outside `{href, class}`
appears, and no node bearing a remote resource is produced.

### The DOM surface stays manually tested — decided, not overlooked

`materialize()` and the comment-chip lifecycle have **no automated coverage and
will not get any.** Testing them needs a DOM: `jsdom` is an npm dependency this
environment cannot fetch and a large one to vendor, and a hand-rolled shim is
its own liability — a fake DOM that disagrees with a real one produces
confident green on behaviour that is broken.

Know what that costs, because it is not hypothetical. Two DOM-timing defects
reached a human reviewer during this work, and both were invisible to the
suite:

- `renderBlockChip` ran inside `materialize()`, before the element had a
  parent. `insertAdjacentElement('afterend', …)` on a detached node does
  nothing and reports nothing, so restored comments silently never rendered
  while the submit count still counted them. Chips are now flushed after the
  tree is attached.
- The anchoring bugs the corpus _did_ catch — `newlines || 1` over-counting
  every block, and table rows landing one line late — were caught precisely
  because they live in `describe()`, on the pure side of the split.

That is the split working as intended: the security property is machine-checked,
the behaviour is not. Anyone extending preview should expect to smoke-test the
DOM by hand and should not read a green suite as covering it.

The block→line mapping gets its own tests, including a CRLF fixture. That
function is the one whose bug is silent and destructive.

## Vendoring

`marked.umd.js` pinned to **one exact version**, at or above `18.0.2` — the
first draft wrote `">= 18.0.2"` and called it an exact pin, which is a
contradiction in a single sentence. Committed under
`scripts/local-review/vendor/`, MIT appended to `vendor/LICENSE`, exact version
and SHA-256 in `NOTICE`, served through the existing `[\w.\-]+\.js` vendor
route.

The bytes cannot be fetched from this environment: `marked` publishes no
release assets, commits no build output, and neither `registry.npmjs.org` nor
the CDNs are on the network allowlist. The operator fetches it; the checksum is
recorded on the way in. Note a checksum recorded at vendoring time proves the
artifact has not changed since, not that it was authentic when fetched.

`legalModes()` drops `preview` when the file is absent, so stripping `vendor/`
costs the mode rather than breaking the page.

## Corrections to the first draft

Recorded because each was confidently stated and wrong:

| Claim                                                                                | Why it was wrong                                                                                                                                                                                                         |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| "Text content is set via `textContent` … no parse step for an attacker to influence" | True of `textContent` on inert elements only. `script`/`style` `textContent` is active, and `srcdoc`/`data`/form actions need no parser. Safety comes from the sink allowlist.                                           |
| "closes the injection question"                                                      | Closes HTML-parser injection. Does not close navigation, UI/content spoofing, or prompt injection into the agent.                                                                                                        |
| "scheme check against an allowlist"                                                  | Underspecified. Relative hrefs pass a naive check and resolve under the token path.                                                                                                                                      |
| "mitigation is limited to pinning"                                                   | Byte/nesting caps and a terminable Worker both bound it.                                                                                                                                                                 |
| "a wedged tab, not a leaked token"                                                   | Also destroys every unsent comment in the page.                                                                                                                                                                          |
| "yields the document verbatim"                                                       | The `\ No newline at end of file` marker is discarded, so a file without a trailing newline is not reconstructed exactly.                                                                                                |
| "pinned to an exact version `>= 18.0.2`"                                             | A range is not an exact pin.                                                                                                                                                                                             |
| CSP including `no-referrer`                                                          | `no-referrer` is a `Referrer-Policy` header. A CSP of `object-src`/`base-uri`/`form-action`/`frame-src` also does little against same-origin script POSTing to `/submit` — defense-in-depth, not a compensating control. |

## Security debt this design does not pay down

Both out of scope here; each wants its own issue.

- **The existing `hljs` + `innerHTML` path in the diff view** (`server.py:770`,
  `server.py:903`) is the page's weakest XSS boundary — attacker-controlled
  source influences generated HTML, and safety rests on a vendored escaper. The
  structural fix is a highlighter returning token ranges materialized as
  `span` + `textContent`, applied to both views.
- **The token lives in the URL**, so it is disclosed by any outbound
  navigation and sits in browser history. `Referrer-Policy: no-referrer` is a
  patch. The structural fix is exchanging it for an `HttpOnly`,
  `SameSite=Strict` cookie and redirecting to a token-free URL — a change to
  the documented agent contract, so not a side effect of this work.

## Acceptance

- An added `.md` file opens in preview by default, with a
  `Split | Single | Preview` control; a modified `.md` file never offers it.
- A comment on a table row arrives as `kind: "block"` with `line`–`endLine`.
- Switching modes preserves every comment and the submit count.
- A diff containing `<img onerror=…>`, `javascript:` in any encoded form, a
  protocol-relative URL, a relative URL, or a remote `<img src>` renders inert
  and fetches nothing off-host.
- `describe()` never emits a tag outside the allowlist or an attribute outside
  `{href, class}`, over the full security corpus.
- A CRLF document anchors comments to the correct lines.
- A document over the size cap falls back to `single` with a notice.
- Frontmatter, link titles, and reference definitions are visible in preview.
- `Referrer-Policy: no-referrer` is sent on every response.
- The mapping tests run under `node` and skip cleanly without it.
- `dev_docs/decisions/local_review_view_modes.md` stops describing preview in
  the present tense before it is true.
