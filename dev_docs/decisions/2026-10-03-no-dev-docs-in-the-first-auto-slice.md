---
created: 2026-10-03
status: accepted
---

# `dev_docs/` stays human-tier in every repo for the first slice

## Context

In workflow-skills, `docs:` PRs drew the largest share of the owner's
corrections, 21%, in the merge history
([`../research/2026-10-03-merge-oversight-history.md`](../research/2026-10-03-merge-oversight-history.md)).
They carry research verdicts and decision records, and the runbooks under
`dev_docs/` are instruction files that agents act on, so a wrong one changes
behaviour the way a skill does. A docs path looks low-risk to a path tier and
is not. [The merge-autonomy design](../designs/2026-10-03-graduated-merge-autonomy.md) (PR #952)
proposed keeping all of `dev_docs/` with the owner; a blind `/consult` agreed
at 75%, naming no crux.

## Decision

Every repo's `.github/merge-policy.yml` keeps `dev_docs/**` out of the `auto`
tier for the first slice. A `dev_docs` subset graduates only on shadow data:
would-land PRs under it that drew no substantive owner change.

## As shipped

- workflow-skills lists `dev_docs/**` under `always_human`, PR #1354 (issue
  #1313).
- finplan lists `dev_docs/**` and `**/dev_docs/**` under `always_human`, and
  agent-guidance lists `dev_docs/**` and `**/*.md`. dotfiles names no
  `dev_docs` glob under `auto`, which leaves it human by default. Checked in
  each repo's `main` on 2026-11-11.

## Consequences

- Good, because the class of PR the owner corrected most never lands without
  them.
- Bad, because research and decision PRs, a large share of the volume, stay in
  the owner's queue, and the human-tier review queue limit counts them.

## Revisit when

- Shadow data (`scripts/merge-audit.py --mode shadow --policy <candidate>`)
  shows a `dev_docs` subset, such as typo and link fixes in research records,
  whose would-land PRs drew no substantive owner change. Graduate that subset
  with narrower globs, not all of `dev_docs/`.

## Confirmation

The `always_human` entries in each policy, and `merge-tier.py`'s rule that an
`always_human` match beats any `auto` match. The policy file is itself always
human, so the entry cannot be removed by an agent-landed PR.

## Alternatives

- **Treat docs as `auto` like other low-risk text.** Rejected: `dev_docs`
  carries decisions and agent-facing runbooks, and drew the most corrections.
