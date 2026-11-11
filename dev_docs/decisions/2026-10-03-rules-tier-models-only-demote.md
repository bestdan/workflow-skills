---
created: 2026-10-03
status: accepted
---

# A tested script sets the merge tier; models can only demote

## Context

An agent that judges its own PR safe to land is the author grading itself, and
every agent here posts as `bestdan`, so author and lander cannot be told apart
by identity. The field survey
([`../research/2026-10-03-agent-merge-autonomy-field.md`](../research/2026-10-03-agent-merge-autonomy-field.md))
found reviewer agreement weak evidence when reviewers share a model family
(Zietsman), and found that where a bot may approve or land a change (Meta's
RADAR, Intercom, Copilot's path-scoped approvals), rules clear the slice first
and model judgement works inside it, never widening it.
[The merge-autonomy design](../designs/2026-10-03-graduated-merge-autonomy.md) (PR #952)
made "rules tier; models only demote" its first invariant. This choice was not
put to `/consult`; it is the premise the rest of the design builds on.

## Decision

`scripts/merge-tier.py`, with its test pair, decides `auto` or `human` from the
PR's changed paths and line counts, title, body and labels, the repo's
`.github/merge-policy.yml` and the `AGENT_LAND` switch, and reports every rule
that fired. It reads no model output. The default is `human`: a path matching
no `auto` glob is human, an `always_human` glob beats any `auto` glob, and the
policy, the tier script, the lander and the evidence scripts are always human
(`SELF_PATHS`), whatever the policy says. The lander runs the installed
plugin's copy and reads the policy from the PR's base branch, never its head,
so a PR cannot widen its own tier. Built-in demotions: a `!` title or a
`BREAKING CHANGE` footer, a title type outside `allowed_types`, a diff over
`size_cap`, the `merge:hold` label, and `AGENT_LAND` off.

Review evidence can only push a PR further toward human. The lander holds a PR
whose co-review evidence record shows an unresolved finding, an open
verification item, a preference call, a consult-decided call below the policy's
`consult_bar`, a stack-reaching call, or a recommendation of another round. A
clean co-review is necessary for `auto`, never sufficient: the repo's checks
must also be green on the head.

A call whose answer later PRs in a stack build on (an interface, a name, a
schema, a convention) is never settled by `/consult`, whatever its confidence,
because a wrong answer costs the whole stack. Co-review step 9 leaves it out of
the consult batch and records it as a provisional stack call, `kind: stack`,
which holds the PR until the owner confirms.

## As shipped

- `merge-tier.py`, PR #1342 (issue #1309); this repo's policy, PR #1354 (issue
  #1313).
- The evidence record co-review publishes, PR #1343 (issue #1307). The
  demotions live in `land-pr.py`'s `evidence_holds`, from PR #1346 (issue
  #1315), not in `merge-tier.py`, so the tier script stays free of review
  input.
- The stack-reaching rule in co-review step 9, PR #1358 (issue #1308).
- The design made the "prompt paths need a releasing type" lint (the #593
  class) built in for workflow-skills. It shipped as a repo rule,
  `prompt-paths-need-releasing-type` in `REPO_RULES`, switched on by name in
  the policy's `repo_rules`.
- `SELF_PATHS` adds `co-review-evidence.py` and `co-review-stamp.py` to the
  design's list, since the lander demotes on what they produce.
- A forecast mode tiers a task from its expected paths before any PR exists,
  for `/promote-tasks`: `merge-tier.py --forecast`, PR #1386 (issue #1379),
  and `scripts/tier-forecast.py`, PR #1389 (issue #1381). The real PR is
  re-tiered at merge, so a forecast never decides a landing.

## Consequences

- Good, because the authoring agent cannot argue with the tier: its inputs are
  facts about the diff and a policy file it cannot edit in the same PR.
- Good, because every `human` verdict carries its reasons, which is the data
  shadow mode tunes the policy from.
- Bad, because paths are a coarse proxy. A risky change in an `auto` path lands
  if review is clean and checks pass; the gate is only as good as the tests.
- Bad, because an agent could split a change to stay under `size_cap`. The
  split still hits the same `always_human` globs.

## Revisit when

- A landed `auto` PR needs a follow-up fix that no gate could have caught
  (`scripts/merge-audit.py --mode landed`). Narrow the matching glob.
- A model-based classifier is shown, on shadow data, to tier better than the
  rules and is independent of the authoring model.
- Consult-decided calls kept `auto` at or above `consult_bar` are later
  overturned: raise the bar or stop letting consult keep a PR `auto`.

## Confirmation

`scripts/test_merge_tier.py` and `scripts/test_land_pr.py`, run by `just check`.
Any policy the parser cannot read gives `human` with `policy invalid: …`.

## Alternatives

- **Let co-review or the reconciler set the tier.** Rejected: it grades the
  author with a correlated model, and an agent can argue with a model.
- **Read the policy from the PR head.** Rejected: the PR could classify itself.
