---
created: 2026-08-25
status: accepted
convention: ../local-review.md
---

# The server owns thread state: it mints ids at submit and keeps threads in memory

## Context

Before submit, a comment is a draft in the browser that can be edited or
deleted. After submit, it becomes a thread that the agent and the page both
address by key. The conversation has the lifetime of the server's token, and
the agent's durable record is the chat transcript plus the round payloads it
read.

## Decision

The server mints each new comment's id at submit time (`t1`, `t2`, … a
per-launch counter) and returns it in the submit response and `--out`. Threads,
replies and the resolved bit live in server memory on `Handler` class
attributes, behind their own lock. They survive a page reload through
`GET /threads` and die with the server on purpose. Landed in [#419](https://github.com/bestdan/workflow-skills/pull/419).

## Consequences

- Good, because drafts need no identity, and the server never trusts the client
  for uniqueness of keys into its own store.
- Good, because no second copy of state exists for the transcript to disagree
  with.
- Bad, because a server crash loses the thread view; the rounds the agent
  already read are all that remain.

## Revisit when

- Reviews need to survive a server restart, which would also need a token that
  outlives the process.

## Confirmation

`scripts/test-local-review.sh` asserts minted ids in `--out` and that threads
survive a `/refresh` in `--git uncommitted` mode.

## Alternatives

- **Browser-minted UUIDs at draft time:** ids die with discarded drafts, and
  the server would trust client uniqueness.
- **Persist threads to disk:** a restart loses the token anyway, and the file
  would copy state the transcript owns.
