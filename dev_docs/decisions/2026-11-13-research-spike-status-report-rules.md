---
created: 2026-11-13
status: accepted
convention: ../research_spike.md
---

# research-spike's `status` report: what it separates, warns about, and never fails on

## Context

`status` answers the question the `aiutopilot` maintainer was really asking on
day four: what still blocks building? A report like that can mislead in
several directions. "Ready" can be read as "done". Healthy project totals can
hide one sick track. A flag meant to be rare can be put on everything.
Retiring questions can make a project look converged. A broken record can make
a decision look readier than it is.

The design (`2026-08-01-research-spike-skill.md`, deleted on graduation) set
the report's rules. They landed in
[#326](https://github.com/bestdan/workflow-skills/pull/326) and
[#1047](https://github.com/bestdan/workflow-skills/pull/1047).

## Decision

`status [<project>]` prints one line per decision labelled `DECIDED`, `READY`,
`BLOCKED` or `PROPOSED`, with each blocked decision's live blockers and their
paths beneath it. Then the question and obligation pair for each track and
the total. The rules:

- The header counts decided, ready and blocked separately (and proposed when
  there are any). The project gate is all decisions decided, not ready.
- Totals never print without the per-track lines, even for one track.
- When more than a third of open obligations carry `blocking:`, it warns. The
  threshold is fixed and not configurable.
- Retired questions are counted apart from answered ones everywhere. A ready
  decision unblocked by a retirement says so on its line.
- `status` always exits `0`.

Shipped beyond the design: a completeness footer. It counts validation
findings, references to no decision, and decisions with a `state:` outside the
enum, and says the report may be incomplete. It reprints none of them, and it
does not fail the run. Without it, a typo in `blocks:` resolves to nothing and
the decision prints `READY`. The taint suffix and the `declared none` column
came later, under
[a separate record](2026-11-13-research-spike-taint-and-none-in-status-only.md).

## Consequences

- Good, because the divergence between converging questions and climbing
  obligations is visible per track, where it starts.
- Good, because a warning that pushed back on the operator caught a modelling
  error in the first adoption: 5 of 10 obligations marked blocking, 3 of them
  double-counting a gate already carried by `blocks:`.
- Bad, because a report that cannot fail is easy to ignore; the footer only
  points at `validate`.
- Bad, because the report is a snapshot and shows no trend.

## Revisit when

- The one-third threshold fires on a spike where a reviewer agrees most
  obligations really do gate decisions.
- A consumer needs the report as data rather than text
  ([deferred, E](2026-11-13-research-spike-lean-b-d-e-deferred.md)).

## Confirmation

`scripts/test-research-spike.sh` pins the report's layout, the scarcity
warning, the retirement note, the footer and the exit code.

## Alternatives

- **A configurable scarcity threshold:** a threshold with a knob gets turned
  up the first time it fires.
- **Fold retired into answered:** a project could converge by giving up.
- **Let `status` fail on a broken tree:** a report that can fail is one people
  stop running; `validate` is the gate.
