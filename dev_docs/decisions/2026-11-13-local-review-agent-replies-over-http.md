---
created: 2026-11-13
status: accepted
convention: ../local-review.md
---

# The agent replies over HTTP to the running server, and the page learns by polling

## Context

A threaded review needs a channel each way. The agent already holds the
per-launch token, parsed from `LOCAL_REVIEW_URL=`, and already polls `--out`.
The page already polls `GET /state` every six seconds and on focus or
visibility. The server is a `ThreadingHTTPServer`, which pins one thread per
open connection.

## Decision

The agent writes with `POST /reply` against the tokenized path; there is no
watched reply file. The page learns about replies from a `threads_rev` integer
on `/state`, bumped on every store mutation, and fetches `GET /threads` only
when it changes; there is no SSE or long-poll. Rounds reach the agent through
one `--out` path, replaced atomically on every submit, whose `threads` array is
the complete state, so overwriting the previous round loses no thread. The new
routes sit behind the same token, `Sec-Fetch-Site` and Origin gates as
`/submit`. Landed in [#419](https://github.com/bestdan/workflow-skills/pull/419), [#420](https://github.com/bestdan/workflow-skills/pull/420) and [#422](https://github.com/bestdan/workflow-skills/pull/422).

## Consequences

- Good, because one mechanism serves both directions, with no second channel's
  atomicity and ordering rules.
- Good, because shutdown has no parked long-lived handler threads to reap.
- Bad, because reply delivery lags by up to one poll interval.
- Bad, because a round's `comments` list, the entries new in that round, lives
  only in that round's file. Those entries are still in `threads`, but a round
  the agent never read is not marked as new anywhere else.

## Revisit when

- The poll lag becomes noticeable in use.
- More than one agent reads the same review.

## Confirmation

`scripts/test-local-review.sh` runs a threaded round trip (submit, ids in
`--out`, reply, `/threads` shows it, `threads_rev` bumped) and the Origin and
token rejections on `/reply`.

## Alternatives

- **A watched reply file:** doubles the channels and needs its own atomicity
  and ordering, while the server would poll the filesystem to serve a browser
  already polling it.
- **SSE or long-poll:** real push, but an always-open connection per page on a
  thread-per-connection server, for a latency win the poll makes small.
- **Per-round `--out` files:** changes the `[ -s "$OUT" ]` poll contract the
  skill and its tests encode.
