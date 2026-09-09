# Judging a proposed fix

Detail behind SKILL.md step 8's reconciler contract.

## The rule

**Judge the proposed fix, not only the finding.** A finding and its
`recommended_fix` are two claims, and they fail independently: a reviewer can
correctly notice something and then propose a change that breaks the code. Run
the fix against the diff before rating it. When the fix would introduce a new
defect, mark the finding **low** whatever the finding's own merit, and say in
the rationale that the fix — not the observation — is what sank it.

A finding whose premise is checkable is not reconciled until it has been
checked. Read the regex, trace the control flow, evaluate the glob. A reviewer
describing code is evidence about the reviewer, not about the code.

## Three worked examples, all from one run

Each was rated `issue (blocking)` by at least one reviewer. All three are wrong,
and the first two would have shipped a bug if applied.

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
general and inapplicable here, which only reading the control flow shows.

**A fix that is a no-op.** A reviewer read an `applyTo` glob of
`skills/**,commands/**,agents/**` as missing handler files and proposed adding
`commands/handlers/**`. `**` spans path segments, so `commands/**` already
matched them. Applying it would have taught the next editor that `**` is
single-segment.

## What this costs when skipped

The reconciler's confidence tier drives auto-fix: a **high** rating applies the
change without asking. So an unchecked `recommended_fix` is not a bad
suggestion sitting in a list — it is an edit, made automatically, on the
strength of a reviewer's prose.
