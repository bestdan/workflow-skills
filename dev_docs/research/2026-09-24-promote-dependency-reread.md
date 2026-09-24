---
created: 2026-09-24
---

# How long step 3b's dependency answer ages before step 5 writes

**Measured 2026-09-24** for issue #868, which added step 4b to
`commands/handlers/gh-issue-promote.md`: a re-read of the `blocked_by` graph for
the scored set, just before the write batch. This is a dated snapshot and is
allowed to go stale.

## The window

The window is from the candidate read to the first `gh-issue-state.py --apply`
write. It was taken from the local Claude Code transcripts of past `/promote-tasks`
runs, all attended, between 2026-09-09 and 2026-09-13:

| repo              | writes | window |
| ----------------- | ------ | ------ |
| `dotfiles`        | 5      | 33 s   |
| `agent-guidance`  | 10     | 38 s   |
| `workflow-skills` | 16     | 48 s   |
| `workflow-skills` | 1      | 165 s  |

None of these runs had step 3b yet, which landed in PR #867. So the start point is
the step-3 candidate query, not the step-3b graph read. The difference is one
helper call.

**Not measured:** the unattended nightly run (PR #819's routine). Its transcripts
live in the cloud session, not on this machine.

## The trade-off accepted

- **Cost.** `gh-issue-ready.py --issue` makes one paginated `blocked_by` read per
  issue, even in one invocation. Step 4b therefore roughly doubles a run's graph
  traffic, since every candidate step 3b passes as ready gets scored.
- **Benefit.** It closes a window of tens of seconds to minutes in which a new
  blocker goes unseen. `/do-tasks` re-checks `blocked_by` at claim, so a miss is
  never claimed. What a miss costs is an issue sitting in the ready lane that
  should not be there.

We accepted the cost because the nightly run is unwatched and the call is cheap.
