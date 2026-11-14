---
created: 2026-08-24
status: proposed
---

# local-review — the agent claims a round by rename before reading it

Threaded replies and multi-round review have landed. The decisions they
carried are the `local-review-*` thread records under
`../decisions/`, and the protocol is in [`../local-review.md`](../local-review.md)
and `skills/local-review/references/threads.md`. One part of this design has
not landed: the agent's round loop in `skills/local-review/SKILL.md` reads
`--out` and then deletes it, where this design had it claim the file by rename
first. This document is trimmed to that part.

## What is true today

In threads mode the server stays alive and replaces `--out` atomically (temp
file plus `os.replace`) on every submit. The loop in
`skills/local-review/SKILL.md` step 4, written in #425, reads the round and
then removes the path:

```bash
payload=$(cat "$OUT"); rm -f "$OUT"
```

A submit that lands between the `cat` and the `rm` writes a new round to
`$OUT`, and the `rm` deletes it unread. The round's threads survive, because
the next round's `threads` array is the complete state. What is lost is that
round's `comments` list, the entries new in it, so the agent cannot tell from
the next payload which threads it never saw as new.

## Proposal

The loop claims the file by rename before reading it:

```bash
mv "$OUT" "$OUT.claimed"
payload=$(cat "$OUT.claimed"); rm -f "$OUT.claimed"
```

`rename(2)` is atomic, so a round the server writes after the `mv` lands at
`$OUT` and is read on the next iteration. The server needs no change, and the
poll contract (`[ -s "$OUT" ]`) is unchanged.

## Decisions

### Claim by rename, not read-then-delete

The agent renames `--out` to a claimed path, reads that, and deletes it.
Read-then-delete is rejected because it deletes a round the server writes in
the window between the two commands.

## Not decided here

- Whether the harness can drive the race deterministically, or the change
  rests on review.

## Graduation

- Decision records: one, "the agent claims a round by rename before reading
  it".
- Conventions: `skills/local-review/SKILL.md` step 4, and the round protocol in
  `../local-review.md`.
