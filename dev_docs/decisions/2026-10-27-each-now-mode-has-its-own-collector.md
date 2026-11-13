---
created: 2026-10-27
status: accepted
convention: ../task-dashboard.md
---

# Each Now mode has its own collector and its own rule

## Context

The modes (`2026-10-27-now-modes-picked-by-the-owner.md`) each answer a
different question: what is closest to done, what can be knocked out fast,
which decision unblocks the most, what is rotting. Filtering Ship's
candidates cannot answer the last three, because Ship leaves out exactly the
items they want: unsized backlog, proposed designs, stale drafts. Each mode
borrows from one line of research: Ship from Kanban WIP limits and Leroy's
attention residue (2009, _OBHDP_ 109(2)); Little from Reinertsen's cost of
delay over duration; Deep from Newport's WIP of one.

## Decision

Ship ranks the shared ranker's candidates plus close-outs. Little, Deep and
Tidy each have their own collector over the mirror, ordered by rule with no
score:

- **Little** takes a PR that a bot opened, that is docs-only, or that
  changes at most `LITTLE_FILES` (3) files and `LITTLE_LINES` (50) lines; a
  draft, or a PR whose CI is anything but green or absent, is left out. It
  takes `est:1` and `est:2` issues not at `status:3_started`; an unsized
  issue is not small. Order: `LITTLE_TIERS`, then `prio`, then the longer
  wait.
- **Deep** takes proposed designs, issues that block open issues (GitHub's
  native `blocking` field), issues at `status:1_needs_refinement`, and
  `status:2_ready` issues at `est:8` or more. Order: most blocked, then
  `prio`, then the larger estimate. It shows `DEEP_LIMIT` (3): one to take up
  and two alternates.
- **Tidy** takes `auto:human-review-needed`, `status:0_untriaged` and
  `status:1_needs_refinement` issues, plus my own PRs open longer than
  `fleet_views.OPEN_ITEM_DAYS` (14), drafts and pending CI included.
  Strictly oldest first, at most 10, with "N more" below.

Pins, ignores, snoozes and the include filter apply in every mode. Pins show
ahead of the list, and Deep's limit counts only unpinned items.

Since the design, three sources joined. PR
[#1256](https://github.com/bestdan/workflow-skills/pull/1256) added my Jira
tickets to Deep (those blocking open tickets, and to-do ones in a status
`DEEP_JIRA_STATUSES` names) and to Tidy (not updated in `OPEN_ITEM_DAYS`),
because on a `github.scope = "me"` instance Deep ranked nothing. PR
[#1304](https://github.com/bestdan/workflow-skills/pull/1304) moved a PR that
writes a research, design or plan doc of more than `LITTLE_LINES` lines out
of Little and into Deep. Plan mode
([#1298](https://github.com/bestdan/workflow-skills/pull/1298)) shows the
live plan in its own order.

Landed in PR [#1224](https://github.com/bestdan/workflow-skills/pull/1224)
(Little) and [#1235](https://github.com/bestdan/workflow-skills/pull/1235)
(Deep and Tidy).

## Consequences

- Good, because each mode shows items Ship would never surface.
- Good, because a rule-ordered list is easy to argue with: its why line names
  the rule.
- Bad, because the thresholds are module constants in `fleet_now.py`, not
  config, so tuning them takes a code change.
- Bad, because `DEEP_JIRA_STATUSES` matches status names, so a Jira site with
  other names gets only Deep's blocker half.

## Revisit when

- Something marks a nightly review fix. Until then those PRs reach Little only
  through the size test.
- Deep proves to miss `auto:human-review-needed` items that read as design
  questions.
- The owner wants to tune Little's or Deep's thresholds, which would promote
  them to config.

## Confirmation

`LittleTests`, `DeepTests`, `DeepDocTests` and `TidyTests` in
`scripts/test_task_dashboard_fleet_now.py`, and `JiraModeTests` in
`scripts/test_task_dashboard_jira_now.py`.

## Alternatives

- **Filter Ship's candidates per mode:** rejected, because Ship's set lacks
  the backlog, designs and stale drafts the other modes exist for.
- **Count unsized issues as small:** rejected, because unknown is not little.
