---
created: 2026-11-13
status: accepted
convention: ../local-review.md
---

# Preview renders the text a normal markdown rendering hides

## Context

A reviewer approves from what the page shows, and the agent reads the whole
file. Markdown keeps text that a normal rendering never displays: HTML comments
and other raw HTML, link titles (`[x](url "text")`), reference definitions and
frontmatter. A rendered heading or task list can also imitate the review page's
own instructions.

## Decision

Preview is a rendering, not a completeness guarantee, so it shows what a normal
rendering would hide. `html` tokens render as escaped text in a muted style.
Link titles and reference definitions render visibly. Frontmatter is
recognized only as a `---` line at offset 0 with a matching close. It is split
on the first `:` per line rather than YAML-parsed, kept as an array of records
so a `__proto__` key cannot pollute a prototype, and rendered as a visible
key/value header. The preview sits inside a bounded surface labelled as
untrusted content. Landed in [#388](https://github.com/bestdan/workflow-skills/pull/388).

## Consequences

- Good, because nothing reaches the agent that the reviewer could not have
  seen in the page.
- Good, because attacker prose does not inherit the page's authority.
- Bad, because a document with heavy HTML or comments reads noisier than it
  would on GitHub.

## Revisit when

- Preview gains a mode that hides markup on purpose; it then needs another way
  to show the reviewer what was hidden.

## Confirmation

The security corpus in `scripts/test-local-review.sh` covers raw HTML and
`__proto__` frontmatter keys. That hidden text stays visible is otherwise held
by review.

## Alternatives

- **Render like GitHub and drop the hidden text:** an approval given in preview
  would then cover content the reviewer never saw.
