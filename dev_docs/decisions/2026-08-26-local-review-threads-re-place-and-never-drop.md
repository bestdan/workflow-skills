---
created: 2026-08-26
status: accepted
convention: ../local-review.md
---

# A thread whose line moves is re-placed by its text and never dropped

## Context

A thread's anchor is the `{file, side, line, code}` it was created with. Under
`--git uncommitted` the diff is re-read on every check, and `/refresh` reloads
the page, so the row a thread named can move or vanish while the review is
open. A comment the user wrote has to stay visible until the user resolves it.

## Decision

The page re-places each thread with a pure `placeThreads(files, threads)` in
three rules, in order. The same row with the same text wins. Else exactly one
row in the file with the same text wins, marked "moved"; two or more matching
rows is not a signal and falls through rather than guessing. Else the thread
goes to that file's Outdated strip, showing its original anchor. A file is
matched by its old or new path, so a rename still finds its threads. The
Outdated strip is collapsed behind an always-visible "N outdated" toggle rather
than expanded. A file that leaves the diff entirely has no card to carry the
strip, so its threads are not shown until it returns, though they stay in the
store and in every round's payload. Landed in [#424](https://github.com/bestdan/workflow-skills/pull/424).

## Consequences

- Good, because no edit under review can remove a thread from the store or the
  payload.
- Bad, because a line whose text recurs in the file goes to Outdated even when
  only one copy is the one the user meant.
- Bad, because threads on a file that left the diff are shown nowhere in the
  page.

## Revisit when

- Threads on a file that left the diff are missed in real use; a page-level
  fallback strip is the deferred fix.

## Confirmation

`scripts/test-local-review.sh` runs a node corpus for the three rules and a
`--git` fixture case that edits the file, refreshes, and asserts the threads
survive.

## Alternatives

- **Drop a thread whose anchor is gone:** loses what the user wrote.
- **Fuzzy matching on near-identical text:** guesses, and a wrong guess puts a
  comment on a line it was not about.
