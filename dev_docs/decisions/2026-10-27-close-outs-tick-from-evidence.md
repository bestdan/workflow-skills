---
created: 2026-10-27
status: accepted
convention: ../task-dashboard.md
---

# Close-outs tick from evidence, within a 14-day window and a cap of 3

## Context

To the owner, closing out a finished session is a sequence: merge its PR,
close its task, leave and remove the worktree, end the session. Each is a
loose end that leaves attention residue, so Ship puts close-outs second. The
dashboard is read-only, so it cannot do the steps, and a click-to-tick
checklist is curation the owner would not keep up. `fleet_views`'
`finished_sessions` returns every idle session, so "the turn ended" is not
evidence that work is finished. The dashboard sees a merged PR for
`github_fleet.CLOSED_WINDOW_DAYS` (14).

## Decision

A close-out is a finished session or a workstream in the Complete lane. A
session enters only on evidence its work is finished: its PR merged, its
branch landed, or it ended. Its card is a checklist (PR merged, task closed,
worktree removed, session ended), each step ticked from mirrored evidence and
never from a click; a step with no evidence is left out, not counted open. A
complete workstream has one step, milestone closed, and an overlay key
(`workstreams`, its id), so the ordinary snooze and ignore apply to it. A
close-out leaves when every step it has is ticked or `close_out.WINDOW_DAYS`
(14) after its last activity, whichever comes first. Ship shows at most
`CLOSE_OUT_CAP` (3), oldest first, pins aside. The card's action resumes the
session with the remaining steps as its prompt, so the session finishes its
own close-out.

Landed in PR [#1221](https://github.com/bestdan/workflow-skills/pull/1221).

## Consequences

- Good, because nothing needs ticking by hand, and a card leaves on its own.
- Good, because the window matches how long the dashboard can see a merged
  PR, and the bound Needs you's Finished group uses with no visit recorded.
- Bad, because a session with little evidence (no PR, no snapshot) can leave
  with steps the owner never did.
- Bad, because past the cap, newer close-outs wait behind older ones.

## Revisit when

- Close-outs routinely age out of the window unfinished, which would argue
  for a longer window or a louder card.
- The cap hides close-outs the owner wanted to see, shown by "why not now"
  answers on Ship.

## Confirmation

`CloseOutTests` in `scripts/test_task_dashboard_fleet_now.py`.

## Alternatives

- **Tick steps by click:** rejected, because it makes the dashboard write
  state for work done elsewhere, and goes stale.
- **Enter on any idle session:** rejected, because an ended turn is not
  finished work.
