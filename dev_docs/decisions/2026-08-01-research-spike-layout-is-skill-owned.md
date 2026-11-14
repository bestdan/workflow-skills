---
created: 2026-08-01
status: accepted
convention: ../research_spike.md
---

# research-spike owns its directory layout instead of configuring it per repo

## Context

The obligation ledger was first built as a bespoke script inside `aiutopilot`.
That script hard-coded three choices that were right for one repo: a single
coverage file (`docs/open-questions.md`), a heading regex (`^### (Q\d+)\.`),
and a coverage rule that reached only that file. A reusable skill had to
either turn each into a per-repo setting or fix them by owning the layout.
The skill was also meant for long-running projects with several concurrent
lines of research, which one flat file does not hold.

The design (`dev_docs/designs/2026-08-01-research-spike-skill.md`, deleted on graduation) chose
the layout. It landed in
[#319](https://github.com/bestdan/workflow-skills/pull/319) (skeleton and
discovery) and [#320](https://github.com/bestdan/workflow-skills/pull/320)
(`init`).

## Decision

The skill fixes the tree at `dev_docs/research/<project>/`, with `PROJECT.md`,
`decisions.md`, a generated `LEDGER.md`, and `tracks/<track>/` holding
`questions.md`, an optional `contracts/` and `obligations/`. Questions are
`### Q<n>.` sections of `questions.md`. Several projects coexist and each is
self-contained. Ids are declared bare and qualified by the script as
`project/track/id` (`project/id` for decisions), and a destination that points
into another project's tree is an error. `init` scaffolds the layout; nothing
about it is configurable.

What shipped differs from the design in one respect: discovery does not
require the layout. `discover()` reads every `*.md` under a project
recursively, and a record's kind comes from its fence's info string, not from
the file it sits in. Only `obligations/` is checked for card shape (every file
must hold a `card` block); `questions.md` sections and `contracts/` files
carry the coverage rule instead
([coverage record](2026-08-02-research-spike-coverage-is-declared-per-section.md)).

## Consequences

- Good, because the coverage file, heading pattern and coverage scope are
  structural facts, so no repo can misconfigure them.
- Good, because a track is a self-contained context bundle: an agent told to
  work track X needs `tracks/X/` and `decisions.md`, and the SKILL.md states
  that contract so a later dispatcher can rely on it.
- Good, because two projects can each have an `account` track without id
  collisions.
- Bad, because a repo with an existing research layout has to move into this
  one rather than point the tool at what it has.
- Bad, because work an answer creates for another project cannot be pointed at
  directly. It is filed in that project and recorded locally with a receipt
  card, one more file per handoff.

## Revisit when

- An adopter cannot fit its research into `tracks/` without contortion, for
  example research organised by date rather than by line of inquiry.
- Cross-project dependencies become common enough that the receipt-card
  workaround is most of a project's obligations.
- A `/work-track` dispatcher is built and needs more than `tracks/X/` plus
  `decisions.md`.

## Confirmation

`scripts/test-research-spike.sh` covers `init`'s scaffold passing its own
`validate`, id scoping across two projects, and the cross-project destination
error. Nothing enforces the agent-context contract; it is stated in
`skills/research-spike/SKILL.md`.

## Alternatives

- **Per-repo config for the coverage files, heading regex and scope:** every
  setting is a way to be wrong, and the skill owns the procedures that write
  these files anyway.
- **One flat questions file per project:** parallel agents would contend on
  one file and one stored ledger.
