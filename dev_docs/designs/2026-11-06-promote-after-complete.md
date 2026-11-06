---
created: 2026-11-06
status: proposed
---

# Re-promote a completed item's dependents as part of completing it

**Recommendation: add a targeted mode, `/promote-tasks after=<id>[,<id>…]`,
that scores only the un-scored dependents of the items just completed. Every
verb that completes something runs it as a tail step, and so does the agent
merge path once the lander can merge. Do not chain a full `/promote-tasks` run
onto completion.** It pays off today on `repo-pr` and `gh-issue`. On `linear` it
does nothing until #1060 lands. On `jira` it does nothing at all.

## What completing a task changes on the board

The promoter holds a card in `new` while it has an unresolved blocker
(`commands/promote-tasks.md` → "Hold blocked cards"). The held card stays
un-scored, so it gets scored on a later run once its blockers resolve.
Completing a task resolves blockers. So after a completion, the held dependents
sit in `new` until somebody runs `/promote-tasks` again. That is the friction
this proposal removes.

The friction is uneven across handlers, because each one holds on a different
signal and only some of those signals change when a blocker completes:

| Handler    | What the promoter holds on                                                                           | Does completing the blocker release the hold?                                                   |
| ---------- | ---------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `repo-pr`  | `is_blocked_by` slug that is present and not `done`                                                  | **Yes.** Deleting the file or `done` satisfies it                                               |
| `gh-issue` | `blocked` label **or** an open native `blocked_by` edge (`gh-issue-promote.md` 3b)                   | **Yes** for the edge. The label is manual                                                       |
| `linear`   | `blocked` label only. Native relations are not read (`linear-promote.md` step 5, #1060)              | **No.** Relation-blocked issues get promoted anyway and are gated at claim; the label is manual |
| `jira`     | `Flagged` and `jira.blocked_statuses`; `is blocked by` links are not read (`jira-promote.md` step 3) | **No.** Both are set by hand                                                                    |

So "after a completion, re-run promote" does something today only for `repo-pr`
and `gh-issue`. On `linear` and `jira` a re-run after a completion scores only
whatever arrived in the backlog since the last run, unrelated to the
completion.

## Where completion happens, and whether an agent is there

| Completion path                                                                                                                                       | Handlers                                    | Agent in the loop?                                                                            |
| ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- | --------------------------------------------------------------------------------------------- |
| PR merges and closes the item natively (`Closes #n`, Linear's GitHub integration, Jira smart commits; `repo-pr`'s task-file delete lands with the PR) | all                                         | Today, no: the owner merges. Under graduated merge autonomy, yes: the agent runs `land-pr.sh` |
| `/complete-task <id>`                                                                                                                                 | all                                         | Yes, one item                                                                                 |
| `/sweep-for-complete --apply` (and `/reconcile-tasks`, `/sweep-for-archive`, which call it)                                                           | `linear` only; `gh-issue` refuses by design | Yes, a batch                                                                                  |
| Nightly gh-issue routine (`dev_docs/nightly-gh-issue-routine.md`)                                                                                     | `gh-issue`                                  | Already runs `/promote-tasks all` at step 2                                                   |
| `/deliver-task`, `/do-tasks`, `/auto-pilot`                                                                                                           | all                                         | Never complete: they hand off at `needs_review`                                               |

Today the common path has no agent in it. That changes with
[graduated merge autonomy](2026-10-03-graduated-merge-autonomy.md):
`/deliver-task` gains a final step that runs `scripts/land-pr.sh`, an explicit
merge command an agent runs. It runs in shadow today (`--dry-run` only;
`skills/deliver-task/references/shadow-landing.md`). Once it merges for real,
the agent that landed the PR is present at the completion and knows the task
id. Part 3 puts the re-check there.

## Design

### 1. `/promote-tasks after=<id>[,<id>…]`: score the dependents only

A new argument token, combinable with `dry-run`. It replaces the candidate set,
and nothing else changes:

- **Candidates** are the items that list one of the given ids as a blocker and
  are still un-scored. Each handler finds them in its own graph:
  - `repo-pr`: cards from `task-scan.py` whose `is_blocked_by` names one of the
    slugs. No new read.
  - `gh-issue`: `GET …/issues/<n>/dependencies/blocking` for each id. The
    endpoint is already verified real (`gh-issue-ready.py` docstring) but
    unused. Add a `--blocking-of <n>` mode to `gh-issue-ready.py` rather than a
    fourth graph reader. Keep the un-scored filter from step 3's search.
  - `linear`: `get_issue` with `includeRelations: true` on each id, then
    follow `blocks`. That is a no-op until #1060 makes the promoter hold on
    relations, because before then no dependent is being held on one. **Ship
    `linear` after #1060, not before.**
  - `jira`: unsupported. Refuse by name, as `backfill-only` does on handlers
    that lack it.
- **Every gate still runs**: the author trust gate, the parent-rollup skip, the
  hold check (a dependent with a second open blocker stays held), backfill,
  scoring, the tier forecast. The new mode only narrows the input.
- **No scope prompt.** The id set _is_ the scope, so step 2a's milestone,
  project or epic question is skipped. That removes the only `AskUserQuestion`
  on the promote path, which is what makes the mode safe to run inside an
  unattended caller.
- **Idempotent.** It only ever touches un-scored items, so a second run, or an
  overlap with the nightly full run, writes nothing new.

Why not chain a full `/promote-tasks`: it asks a scope question whenever several
milestones/projects/epics are open, it scores the whole backlog with model
judgment and possibly a consultant dispatch per card, and almost all of that
work has nothing to do with the completion. The id set is already in hand at
every completion site, so the narrow read is cheaper and more accurate.

### 2. A tail step on the verbs that complete

Each verb below gains a final "Re-check dependents" step. It follows the
promote handler's `after=` flow inline, the same way `/sweep-for-archive`
composes its three verbs (it reads the handler files rather than invoking a
skill):

- **`/complete-task <id>`**: runs on both `applied` and `already complete`.
  `already complete` usually means the merge closed the item natively a moment
  earlier, which releases dependents just as an explicit close does. The tail
  is idempotent, so running it on an item completed long ago writes nothing.
  Under `--dry-run` it runs as `dry-run`. The existing confirm-before-mutating
  prompt names the extra step ("then re-check dependents of #123"), so it is one
  prompt, not two.
- **`/sweep-for-complete --apply`**: one tail call with the full set of ids it
  completed. Without `--apply`, a dry-run tail reports what _would_ unblock.
  `/reconcile-tasks` and `/sweep-for-archive` get the tail for free, because
  both run this flow.
- **The nightly gh-issue routine** needs no change: its step 2 already scores
  everything.

The report gains one section: `dependents re-checked (N): promoted …, held …,
needs_refinement …`.

### 3. After the lander merges: complete, then re-check

When `land-pr.sh` exits having merged (not in shadow, not a hold),
`/deliver-task` runs the `/complete-task` flow on its own task id with
`assume_verified: true`, then that flow's tail. The lander has just verified the
merge against the exact head SHA, which is the verification `assume_verified`
exists to carry (`commands/complete-task.md` → trust boundary).

Running `/complete-task` rather than going straight to `after=` closes a race.
A native close (`Closes #n`, Linear's integration) can lag the merge by
seconds, and a dependents read that loses the race sees the blocker still open
and holds every dependent. `/complete-task` either finds the item already
complete or closes it explicitly, so the tail always reads a closed blocker. It
also covers a tracker whose native close is off, which is why `/complete-task`
exists.

This changes one rule. `/deliver-task` today says it never moves a task to
`done`, because completion must be merge-verified
(`skills/deliver-task/SKILL.md` step 7). The rule's reason still holds: this
step runs only after the lander has merged, so the completion is
merge-verified. The rule becomes "never `done` before a verified merge."

Two paths fall outside this. The owner merging by hand, in the GitHub UI or by
asking a session to run the owner's merge verb, has no plugin step after it;
the nightly routine and the next full `/promote-tasks` catch those. And while
the lander runs in shadow, part 3 is inert and parts 1 and 2 carry the feature.

## Frictions and permissions

- **`allowed-tools` must be the union, or the tail stops at a prompt.** In a
  `/loop` or `/schedule` run, a permission prompt is a hard stop. Today
  `/complete-task` lacks every promote-only tool: `list_issues`,
  `list_issue_labels`, `create_issue_label`, `list_comments`, `Skill`, and the
  python helpers. `/sweep-for-complete` lacks most of them. Each verb that
  gains the tail adds them to its frontmatter.
- **`/promote-tasks` already under-declares.** Its frontmatter allows only
  `Bash(git|find|grep|cat|gh *)`, but it runs `task-scan.py`,
  `gh-issue-trust.py`, `gh-issue-ready.py` and `tier-forecast.py`. Interactively
  that costs a prompt per helper; in a composed tail it is a stop. Fix it in
  the same PR as part 1, adding `Bash(python3 *)` and the direct
  `${CLAUDE_PLUGIN_ROOT}/scripts/*` form the steps use.
- **The intent confirmer dispatches a subagent.** A `human`-tier dependent
  needs a fresh-context `agent-guidance:consultant` to confirm its Intent before
  it can become `ready` (`skills/task/references/intent-confirmation.md`). The
  tail runs that confirmation as a full run does, and acts on the verdict:
  confirmed at or above `consult_bar` promotes, anything else lands in
  `needs_refinement` with the crux. That needs the `Agent` tool, which neither
  `/promote-tasks` nor the completing verbs declare today; add it with the
  other tools above. The cost is one consultant run per `human`-tier dependent,
  within the fan-out bound below.
- **The sandbox.** `gh` fails TLS under the Bash sandbox, so `gh-issue` reads
  must run unsandboxed. `/complete-task`'s `gh-issue` path already does, so the
  tail adds calls of the same kind, not a new escape. The Linear and Atlassian
  MCPs are unaffected.
- **A wrong completion now cascades.** `/complete-task` trusts its caller. With
  the tail, a mistaken completion also promotes that item's dependents to
  `status:2_ready` + `auto:eligible`, where the nightly `/deliver-task` can pick
  them up. The dependent still has to pass every confidence check, so the extra
  exposure is the ordering, not the quality bar. The tail writes its
  promotions rather than only reporting them: a report-only tail would leave the
  manual step this design removes. Demotion stays a human's call, as now.
- **Cost is bounded by fan-out.** One graph read per completed id, plus
  scoring per dependent, which is usually zero to three items.
- **The capability matrix gets a row** in `commands/task-config.md`
  (`↳ re-promotes dependents after completion`: `repo-pr` yes, `gh-issue` yes,
  `linear` after #1060, `jira` no), per the rule in `AGENTS.md`.

## Rollout

1. `/promote-tasks after=` for `repo-pr` and `gh-issue`, with
   `gh-issue-ready.py --blocking-of` and its test pair, plus the
   `allowed-tools` fix. `feat`.
2. Tail steps on `/complete-task` and `/sweep-for-complete`, with the
   frontmatter union. `feat`.
3. The post-land step in `/deliver-task`, shipped with or after the lander's
   switch from shadow to merging. `feat`.
4. `linear`, once #1060 merges.

`jira` waits on its own parity gaps (#725 and the unread `is blocked by` links).

## Not in scope

- **Follow-ups captured during the work.** `/add-task` during a
  `/deliver-task` leaves new un-scored items. They are unrelated to the
  completion's graph, so `after=` does not see them. If they matter, scoring at
  capture time is a separate change to `/add-task`. `jira` already lands a
  complete, unblocked task in `ready_status` this way.
- **Clearing a manual `blocked` label or a Jira flag** when the blocker closes.
  Those were set by a human for a reason the tracker does not record.
