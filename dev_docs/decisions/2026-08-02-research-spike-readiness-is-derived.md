---
created: 2026-08-02
status: accepted
convention: ../research_spike.md
---

# research-spike decisions store only their human state; readiness is derived

## Context

A spike exists to unblock decisions, so convergence is measured per decision:
which ones could be taken now, and what still blocks the rest. Storing a
`ready` or `blocked` field next to the facts it summarises lets the two
disagree, and in review that was treated as a defect rather than a
convenience. Decision evidence has a second hazard: `/push-plan` deletes plan
directories after migrating them, so a decision pointing at a plan loses its
evidence on the first push.

The design (`dev_docs/designs/2026-08-01-research-spike-skill.md`, deleted on graduation) set
the record format. Decisions and referential integrity landed in
[#325](https://github.com/bestdan/workflow-skills/pull/325); derived readiness
in [#326](https://github.com/bestdan/workflow-skills/pull/326).

## Decision

A `decision` block stores `id`, `state` (`pending | decided`, or `proposed` inside a track's `questions.md` only), and
`decided_in:` / `reopened_because:` where required. There is no key for
`ready` or `blocked`, so typing one is an unknown-key error. A decision is
ready when every question that `blocks:` it is answered or retired and every
obligation `blocking:` it is discharged; the script derives that on every run.
`pending → decided` is a human act, and `decided_in:` must point at an
existing file that is not inside a plan directory (an error, unlike the
warning an obligation's destination gets). A live blocker against a `decided`
decision is an error unless the decision is reopened. A track that needs a new
decision files it as `state: proposed` in its own `questions.md`, and only the
organizer promotes it into `decisions.md`. A decision nothing references is a
warning.

One rule shipped that the design did not state: a reopened decision is
`state: pending` plus `reopened_because:` plus the **retained** `decided_in:`
of the decision being reopened. The validator reads one snapshot with no
history, so the retained pointer is the only evidence that there was a
decision to reopen. The design's "zero open blockers at decision time" shipped
as zero live blockers at any time, for the same reason.

## Consequences

- Good, because the number an adopter would most like to flatter is the one
  they cannot edit.
- Good, because filing new work against a taken decision forces an explicit,
  recorded reopen.
- Bad, because a `proposed` decision gets no readiness until it is promoted;
  `status` lists it only as awaiting promotion.
- Bad, because a reopen is three fields, which is easy to get wrong the first
  time.

## Revisit when

- Adopters need decision-to-decision dependencies. That would make cycles
  representable and need a cycle check, which the lean-concepts work
  explicitly left out.
- `/push-plan` stops deleting plan directories.

## Confirmation

`validate` enforces every rule above. `scripts/test-research-spike.sh` covers
a stored `ready:` failing as unknown, a blocker against a decided decision
without a reopen, the reopen shape, a plan-directory `decided_in:`, and a
proposed decision filed in `decisions.md`.

## Alternatives

- **Store the derived status too:** a stale copy can disagree with the truth.
- **Let track agents write `decisions.md` directly:** every track PR would
  touch an organizer-owned file, and nobody would own the decision list.
