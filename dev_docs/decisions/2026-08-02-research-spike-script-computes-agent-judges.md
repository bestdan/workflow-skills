---
created: 2026-08-02
status: accepted
convention: ../research_spike.md
---

# research-spike: the script computes every status and count, the agent only judges

## Context

A research spike needs two kinds of work. Some is mechanical: parsing records,
checking that a destination exists, counting answered questions. Some is
judgment: writing a question, choosing an id, deciding whether a sentence
defers work at all. An agent asked to report progress will happily produce a
count itself, and a count it produces can disagree with the tree. The design
(`dev_docs/designs/2026-08-01-research-spike-skill.md`, deleted on graduation) noted that
presenting the two halves as one flat list of verbs was the likeliest way to
build the wrong half.

The script landed across
[#319](https://github.com/bestdan/workflow-skills/pull/319)–[#327](https://github.com/bestdan/workflow-skills/pull/327);
the SKILL.md procedures in
[#329](https://github.com/bestdan/workflow-skills/pull/329).

## Decision

If two runs over the same tree could disagree, the work belongs to
`scripts/research-spike.py`: parsing, every validation rule, decision
readiness, the `status` report, ledger derivation and freshness, and
`suggest`. The script is stdlib-only, offline, and never edits prose (`init`,
which scaffolds files, and `write-ledger`, which rewrites only the generated
blocks between markers, are the exceptions). The agent writes the prose and
the fenced blocks through five SKILL.md procedures (`file`, `answer`, `defer`,
`backfill`, `promote-decision`), and every procedure ends by running
`validate`. CI only ever runs the script.

As shipped, the script has six subcommands (`init`, `validate`, `ledger`,
`write-ledger`, `status`, `suggest`) and two global options that must precede
the subcommand (`--root`, `-v`). The design's `status <project>` shipped as
`status [<project>]`, reporting every project when none is named.

## Consequences

- Good, because the validator, not the procedure, guarantees a result, so an
  agent's mistake in a procedure is caught the same run.
- Good, because the numbers in a report can be reproduced by anyone who runs
  the script.
- Bad, because the agent has to write fenced blocks by hand and fix them on a
  validation failure; there is no `research-spike add-question` to do it.
- Bad, because the boundary is a convention for the agent half. Nothing stops
  an agent typing "3 open" into prose.

## Revisit when

- Agents repeatedly write malformed blocks that a scripted writer would have
  produced correctly, enough that the cost of hand-writing exceeds the cost of
  a script that edits prose.
- A consumer needs the derived state without re-parsing the markdown (see
  [the deferred `status --json`](2026-08-14-research-spike-lean-b-d-e-deferred.md)).

## Confirmation

`scripts/test-research-spike.sh` runs only the script against fixture trees.
The SKILL.md's "What the skill must not do" list forbids the agent asserting
any status or count. Nothing checks that.

## Alternatives

- **Script verbs that write each record kind:** the script would then edit
  prose, and the agent would still need judgment for ids and `none:` reasons.
- **Let the agent compute counts and readiness:** two runs could disagree,
  and a hand-typed status is the stale copy the record format exists to
  prevent.
