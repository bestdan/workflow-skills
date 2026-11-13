---
created: 2026-09-14
status: accepted
convention: ../local-review.md
---

# A round's summary gets a thread of its own

## Context

The submit dialog takes an optional overall comment, the round's `summary`.
Without a thread, the agent could answer it only in chat, and the summary lived
only in that round's `--out` file, so two submits inside one agent poll
interval lost the first one's summary.

## Decision

A non-empty `summary` mints a thread on the same submit: the shape of a
per-line thread with `file`, `side`, `line`, `code` and `endLine` all `null`,
and `kind: "summary"`. It takes `/reply` and the user's Resolve like any other
thread. It skips anchor re-placement and renders in its own strip above the file
cards, in round order, dimmed in place when resolved. An empty summary mints
nothing. Landed in [#585](https://github.com/bestdan/workflow-skills/pull/585); the threaded-replies design had deferred carrying
summaries in the store until real use showed the need.

## Consequences

- Good, because the agent answers the overall comment where the user wrote it.
- Good, because the summary is now in the full-state `threads` array, so a
  second submit no longer loses it.
- Bad, because the thread model gains a second kind that every consumer of
  `threads` has to handle.

## Revisit when

- A third anchorless thread kind is proposed; the model then wants a general
  notion of an unanchored thread.

## Confirmation

`scripts/test-local-review.sh` covers summary threads in the round payload and
`GET /threads`.
