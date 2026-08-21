# WIP gate attendedness

Why the pre-claim WIP gate asks a present human instead of detecting one, and why
the "prefer work that needs a human" half of the same idea was rejected outright.

## The problem

`wip_limit` exists to bound **unattended** throughput: an autonomous run opens pull
requests faster than anyone merges them, so the cap protects the human review queue.
That rationale was written for the `repo-pr` batch path and then applied as a
universal precondition on every claiming path — including a single foreground claim in
a session where the human is sitting right there. In that session the bottleneck the
gate protects is staffed, and the gate is friction.

## What was rejected: detect attendedness, then skip the gate

The obvious design is to classify the run and skip the gate when it looks attended.
It was rejected because **"a human typed this" is not observable.** A scheduled
routine, a `claude -p` cron, and an SDK-driven harness all deliver an ordinary
user-role message carrying no marker that distinguishes it from a person at a
keyboard. A closed checklist of unattended signals cannot be closed.

The asymmetry is what kills it. Guessing "unattended" when someone is present costs
one decline they can immediately override. Guessing "attended" when nobody is present
removes the only bound on an autonomous loop, and the result is invisible until
morning. A design whose failure mode is unbounded and silent cannot rest on an
unverifiable predicate.

## What was chosen: ask, and let the answer decide

The gate always computes its count. At the limit it does not decide whether a human
is present — it **asks**, via `AskUserQuestion`. A human answers in one keystroke and
the claim proceeds; with nobody there no answer arrives and the gate holds.

This makes the classification fail closed by construction, which demotes the
hard-negative checklist from load-bearing to a cheap pre-check: a missing entry means
a pointless prompt in a run already known to be unattended, not an unbounded claim. It
is also why the override must be a prompt and not a printed "say the word and I'll
proceed" — prose that goes unanswered does not block, so an unattended run reads it
and continues. The prompt is the detector, not a courtesy.

Batches are excluded deliberately. `--all`, `-n N`, a `--claim-only` batch, and any
batch remote dispatch stay bounded however they were invoked, because presence at
dispatch says nothing about the pull requests that land later. The split is **batch vs single**,
never `--remote` vs `--local` — `repo-pr` dispatches single tasks remotely by default
and those stay ungated.

No config key was added. There is deliberately **no way to restore always-declining
behavior**, because with the ask-don't-detect shape the gate always fires and
attendedness only decides whether a human gets a choice. A key would only re-add the
escape hatch the design does not need.

## What was rejected: the selection-preference inversion

The same insight suggested a second change — attendedness should govern _which_ work
is selected, not only whether the gate fires. An unattended run prefers auto-eligible
tasks; an attended session should prefer the work that needs a human's judgment, since
that capacity exists only while someone is there. The concrete proposal was for an
attended `/do-tasks` to offer `human_approval_requested` work through
`AskUserQuestion` and claim it on approval, while reporting `needs_refinement` work as
report-only.

**It was cut, because those are the same population.** On every tracker handler
`human-approval-requested` _is_ the `needs_refinement` encoding — `linear-promote.md`
tags it and leaves the issue in backlog, `gh-issue-promote.md` adds the label, and
`jira-config.md` documents `refinement_status` as "the jira analogue of
`needs_refinement`/`human-approval-requested`". The proposal therefore specified two
contradictory treatments for one set of issues.

The semantics do not survive either. The marker means a human has not yet decided, and
it is set in two situations: the promoter judged the task underspecified, or a
mid-execution bail parked it with a comment saying a human should look before more
work is auto-claimed. A single click satisfies neither — it refines nothing, and it is
not a reading of the bail comment. Converting "a human must think" into "a human must
click" launders the gate rather than discharging it. And the marker is cleared by
_refinement_, which is `/promote-tasks`' lane, so `/do-tasks` is the wrong verb.

If this is revisited, it needs to show the human the actual reason (the promoter's
failed check, or the bail comment), record who approved and when as a comment rather
than a silent label removal, and live on the promote side. Note also that the original
proposal's "remove the marker inside the same claim mutation" is not achievable as
stated: the claim lock is a ref push or a comment election, and the label write is a
separate call regardless.
