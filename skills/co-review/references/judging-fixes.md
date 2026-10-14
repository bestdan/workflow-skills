# Judging a proposed fix

Worked examples behind SKILL.md step 8's reconciler contract. That step — and
its deliberate mirror in `agents/co-review-reconciler.md` — already states the
two rules these examples exercise, and they must not move here: **judge the
proposed fix, not only the finding**, and **check a checkable premise — including the
unstated one, reachability — before reconciling**. This file restates neither; it shows what each looks like when a
reviewer gets it wrong.

## Three worked examples, all from one run

Each was rated `issue (blocking)` by at least one reviewer. All three are wrong.
The first would have broken the build if applied; the second describes a bug the
control flow rules out; the third is a no-op.

**A fix that breaks the build.** Two reviewers independently read
`AUTOLINK.sub("", text)` as dropping an autolink's visible label, claiming
`"install mise <https://mise.jdx.dev/>"` loses the word `mise`, and both
proposed `AUTOLINK.sub(r"\1", text)`. The regex is
`<(https?://[^>]*)>` — it matches only the bracketed URL, so `mise` was never
inside it and never at risk. The proposed fix substitutes the URL back in as
bare text, which the next line's `BARE_URL` check then rejects, aborting the
generator on any autolink at all. Running the substitution once falsifies both
halves in seconds.

**A fix for an unreachable case.** A reviewer flagged
`with self.assertRaises(SystemExit)` as passing on success as well as failure.
The tests call `main()` directly rather than running the script, and `main()`
returns an int on every success path — it never raises. The concern is sound in
general and inapplicable here, which only reading the control flow shows. Here
the rating dropped because the premise failed, not the fix — the
checkable-premise rule, not the fix-judging one.

**A fix that is a no-op.** A reviewer read an `applyTo` glob of
`skills/**,commands/**,agents/**` as missing handler files and proposed adding
`commands/handlers/**`. `**` spans path segments, so `commands/**` already
matched them. Applying it would have taught the next editor that `**` is
single-segment.

## A fourth, where every stated premise was true

**A spec that exercises a path the entry point can't reach.** A `--post` run
posted an `issue (blocking)` against a PR that removed an early
`return unless purpose.personalized?` from a GraphQL resolver, claiming
non-personalized records now reached a transform that returns nil for
`null: false` fields. Every premise the finding stated held: the transform
returns nil, the fields are non-null, the client selects them. The finding was
still false. The resolver is mounted on one object type, reached only through
`resolve_type`, which maps a non-personalized record to a different type — so
the removed guard could never be false there. A resolver spec that builds a
non-personalized record and calls the resolver directly made the path look
live, though the schema can't produce it. Neither the reviewer nor the
reconciler opened `resolve_type`: each checked every premise it was given, and
nobody listed reachability. That is the case step 8's list-the-premises-yourself rule exists for —
a gap in which premises were checked, not in how carefully.

## What this costs when skipped

The reconciler's confidence tier drives auto-fix: a **high** rating applies the
change without asking. So an unchecked `recommended_fix` is not a bad
suggestion sitting in a list — it is an edit, made automatically, on the
strength of a reviewer's prose.
