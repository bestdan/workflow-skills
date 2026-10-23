---
title: task-dashboard — prioritization score and the Now, Waiting, Stale, Epics views
priority: high
size: 5
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/designs/2026-10-23-task-dashboard.md
  - commands/handlers/jira.md
  - scripts/task-dashboard/server.py
is_blocked_by:
  - task_dashboard_task_5
  - task_dashboard_task_3
parent: task_dashboard
tags: [task-dashboard, prioritization, views]
---

Part of [[task_dashboard_plan]]. **Runs on: the Mac mini.** Tuning the
weights against real work happens in [[task_dashboard_task_11]].

## Context

This is the part that helps prioritize and coordinate. The design's **Views**,
**Prioritization score** and **Blocked** paragraphs are the spec.

- **Score:** a deterministic sum of named, config-weighted terms:
  - Priority
  - Deadline pressure (due date, else the active sprint's `endDate`)
  - Unblocks others (count of open issues this one blocks via outward
    `Blocks` links)
  - Stuck (time since `status_category_changed` beyond a per-category
    threshold; v1 has no per-status time, see the design's **Fields**)
  - Review waiting (a PR of mine is approved or green)
  - Blocked (a large negative term)

  No LLM. **Every card shows its terms**, so the ranking can be argued with.
- **Blocked:** any of four signals:
  - the `blocked` label
  - `Flagged`
  - a status in `jira.blocked_statuses`
  - an unresolved inward `Blocks` link (the other issue's status category is
    not `done`)

  The link signal is a SQL join over `links`.

Pins and snoozes come from the overlay (task 7). This task reads those tables
if rows exist, but doesn't need any UI to write them.

## Task

- Add `scripts/task-dashboard/score.py`:
  - `score(issue, ctx, weights) -> (total, [(term, value, why)])`. A pure
    function; `ctx` carries link counts, PR state and `now`.
  - `is_blocked(issue, links, config) -> (bool, [signals])`.
- Add these views to `server.py`, each with its own route and tab:
  1. **Now:** pinned first (in overlay order), then by score, with snoozed
     items hidden. Each card lists its score terms.
  2. **Waiting on me:** my issues that block someone else's open issue, plus
     PRs where my review is requested.
  3. **I'm waiting on:** my issues with an unresolved inward `Blocks` link,
     plus my PRs waiting on review or with CI pending or failed.
  4. **Stale:** `indeterminate`-category issues past the threshold with no
     update. The card shows the current status name.
  5. **Epics:** rollup per parent, with done/total and blocked counts.
- Show each issue's joined PRs on its card (task 3's join).
- Add `scripts/test_task_dashboard_score.py` and its `.sh` wrapper.

## Acceptance Criteria

**Code-enforced**

- `score`:
  - a blocked urgent issue ranks below an unblocked medium one with default
    weights
  - an issue due tomorrow outranks one due next month, all else equal
  - the returned terms sum to the total
- `is_blocked`:
  - each of the four signals alone yields `True` and names that signal
  - an inward `Blocks` link to a `done` issue does not
- On a fixture DB, Now puts a pinned item first regardless of score and hides
  an item snoozed until tomorrow.
- `just check` passes.

**User-run**

- On the Mac mini, against the fake `acli` and the personal GitHub account,
  check each view through the `ts-share.sh` link:
  - each view renders
  - every card's score terms are visible
  - Waiting on me lists your review-requested `bestdan/*` PRs, if any
- Checking the ranking against your real priorities, and tuning the weights,
  is in [[task_dashboard_task_11]].
