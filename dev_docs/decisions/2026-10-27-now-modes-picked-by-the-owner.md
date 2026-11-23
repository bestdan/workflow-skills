---
created: 2026-10-27
status: accepted
convention: ../task-dashboard.md
---

# The owner picks Now's mode, and the pick is saved in the overlay

## Context

On 2026-10-26 Now ranked one list by one score, and that list was ten
`auto:human-review-needed` issues, oldest first: 24 issues tied at the score
ceiling (category 3 + `prio:1` 0.6 + age capped at 0.5), and the tie went to
the longest wait. The owner had sat down to ship, not to triage. Which item
comes next depends on the kind of session the owner is about to have, and no
single order serves every kind. The removed Focus feature (#1182) asked the
owner to hand-pick items and went unused, so a mode had to cost one click
and no curation.

## Decision

Now has modes, each answering one question with its own collector and its
own order (`2026-10-27-each-now-mode-has-its-own-collector.md`): Ship (the
default, "what is closest to done?"), Little, Deep, Tidy, and Plan, added
later by PR #1298 to show the live plan in its own order. The owner picks
one with a button in the page header that cycles `overlay.NOW_MODES`. The
tool never infers the mode from time of day or activity. The button POSTs
the next mode, which is saved as one overlay row, then redirects to `/now`,
so a reload, a phone visit, `export` and `import` all keep it. A GET
`?mode=` shows a mode once and saves nothing, keeping the rule that a page
load records no state. With nothing saved the mode is Ship. `view now` with no
`--mode` reads the saved mode, so an agent sees what the owner
sees. Modes apply on every instance, and each card's why line names the
active mode's terms. A "why not now" answer records the mode in its
snapshot.

Landed in PR [#1224](https://github.com/bestdan/workflow-skills/pull/1224)
(the button and its persistence, overlay export version 7) and
[#1236](https://github.com/bestdan/workflow-skills/pull/1236) (the button
moved into the header beside the sync icon). Plan mode landed in
[#1298](https://github.com/bestdan/workflow-skills/pull/1298). The button
reached `github.scope = "me"` instances with
[#1242](https://github.com/bestdan/workflow-skills/pull/1242).

## Consequences

- Good, because the owner gets the order that fits the session with one
  click, and nothing to curate.
- Good, because one saved mode follows the owner across devices and is what
  agents read.
- Bad, because the mode is global: the phone and the desktop cannot sit in
  different modes at once.
- Bad, because a mode left on from yesterday applies today until changed.

## Revisit when

- The owner wants a different mode per device, which would move the pick out
  of the shared overlay.
- There are enough "why not now" answers per mode to make per-mode `tune`
  worth building; today `tune` proposes one set of weights.

## Confirmation

`ModeTests` and `ModeRouteTests` in `scripts/test_task_dashboard_fleet_now.py`
cover the cycle, the POST, the one-off GET and the saved default;
`scripts/test_task_dashboard_overlay.py` covers export and import of the
mode.

## Alternatives

- **One score with per-mode weights:** rejected, because each mode asks a
  different question over a different set of items.
- **Infer the mode:** rejected, because a deep-work block is chosen
  deliberately (Newport, _Deep Work_).
- **Store the mode in the browser:** rejected, because a phone visit and an
  agent would then see a different Now from the owner's desk.
