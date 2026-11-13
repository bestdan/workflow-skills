---
created: 2026-08-02
status: accepted
convention: ../research_spike.md
---

# research-spike: every question section must declare what it owes, even nothing

## Context

Records catch a malformed deferral. They cannot catch the deferral nobody
registered, which was the actual failure in `aiutopilot`. Grepping prose for
deferral vocabulary does not answer that question reliably (see
[the `suggest` record](2026-08-02-research-spike-suggest-cannot-fail.md)). In
that repo a precondition stated in a contract document was on no list at all,
which is a second class of hidden work.

The design (`2026-08-01-research-spike-skill.md`, deleted on graduation) chose
a structural rule. Question coverage landed in
[#323](https://github.com/bestdan/workflow-skills/pull/323); contracts
coverage with the decisions work in
[#325](https://github.com/bestdan/workflow-skills/pull/325).

## Decision

Every `### Q<n>.` section of a `questions.md` must contain at least one
`obligation` block: a real one, or a bare `none: <reason>` block. A `none:`
with no reason fails, and so does one mixed with other fields. Every file
under `tracks/*/contracts/` must likewise carry an obligation or a file-level
`none:`. Coverage reaches those two places and no others.

The design tied coverage to `answered` ("answered requires coverage"). As
shipped the rule is unconditional: a freshly filed `open` question with no
obligation block already fails `validate`. So the `file` procedure always ends
a new section with a `none: filing only, no work identified yet` block or a
real obligation.

## Consequences

- Good, because forgetting is no longer possible; only deliberate silence is,
  and a `none:` reason is something a reviewer can read and challenge.
- Good, because contract preconditions, the class that hid the worst case in
  the origin repo, are covered in a directory the skill owns.
- Bad, because a `none:` can be written to satisfy the rule mechanically. The
  `status` report counts them for that reason
  ([taint and `none:` count](2026-10-18-research-spike-taint-and-none-in-status-only.md)).
- Bad, because a deferral stated in prose anywhere else in the tree is still
  registered only voluntarily.

## Revisit when

- Deferrals are found created somewhere other than questions files and
  contracts, often enough to justify a third covered location.
- The `none:` count in real spikes shows most sections declaring nothing,
  which would mean the rule is being satisfied rather than followed.

## Confirmation

`validate` enforces both rules; `scripts/test-research-spike.sh` has fixtures
for a section with no declaration, a reasonless `none:`, a `none:` with other
fields, and a contracts file with neither.

## Alternatives

- **Coverage only once a question is answered:** what the design said; the
  unconditional rule is simpler and catches a filed question that already
  implies work.
- **Coverage over every markdown file:** noise, which trains people to
  satisfy the rule without thinking.
