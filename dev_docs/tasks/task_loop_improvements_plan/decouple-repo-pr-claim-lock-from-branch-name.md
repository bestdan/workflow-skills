---
title: Decouple repo-pr task-loop claim lock from the git branch name
priority: medium
size: 5
status: ready
created: 2026-06-07
source_branch: claude/modest-brown-Q0BH4
source_pr: 28
related_files:
  - commands/process-tasks.md
  - commands/do-tasks.md
  - skills/task/SKILL.md
tags:
  - task-loop
  - coordination
  - web
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]].

## Context

The `repo-pr` task-loop's claim/coordination primitive is the **atomic first-push
of the branch `task/<slug>`** (`commands/process-tasks.md` CLAIM step): the first
agent to `git push -u origin task/<slug>` wins; any later agent's push to that
same new ref is rejected and the loser bails. The deterministic branch name _is_
the distributed lock. The `status: in_progress` flip is only a secondary signal
and is not even visible to a competitor, because it lives on the unmerged claim
branch — a fresh clone of `main` still shows the task as `ready`.

This breaks in **branch-pinned execution environments**. Claude Code on the web
hands each session a fixed branch (e.g. `claude/<session>`) and a policy against
pushing elsewhere, so the session cannot create `task/<slug>` and therefore never
acquires the lock. Two such sessions told to "claim the next task" both scan
`main`, both still see the task as `ready`, and both proceed — double-claiming the
same task with no collision to stop them.

Discovered while running `/process-tasks --local` from a Claude Code web session
(PR #28): the claim was pushed to the session branch, so the branch-name lock was
never engaged. The same failure shape applies to `/claim-task` against trackers
whose linking depends on a specific branch name, but this task is scoped to the
`repo-pr` claim lock.

## Task

1. Design a claim signal that does **not** depend on the agent's ability to choose
   the branch name. Candidate approaches to evaluate (pick one, document why):
   - **First-writer-wins on `main`:** commit the `ready → in_progress` flip
     directly to `main` and push; the non-fast-forward rejection on the losing
     push becomes the lock. Visible to every later scanner immediately. **Risk to
     evaluate:** repos commonly protect `main` (no direct pushes / required PRs),
     which would block this approach — confirm it degrades or is ruled out where
     `main` is protected.
   - **Labeled draft PR as the claim marker:** opening a `task-loop` draft PR that
     references the slug is the claim; a pre-claim check queries open `task-loop`
     PRs for that slug and bails if one exists.
   - Any equivalent atomic, branch-name-independent primitive.
2. Update `commands/process-tasks.md` (CLAIM step + `--local` mode) and
   `commands/do-tasks.md` (which subsumes `/process-tasks` for the `repo-pr`
   handler) to use the chosen signal, keeping the existing `task/<slug>` branch
   convention for environments that _can_ set it (it still aids the merge/done
   flow) but no longer relying on it for the lock. (`commands/claim-task.md` is
   the tracker path — its claim lock is the `auto-claimed` label, not a branch
   name — so it is out of scope here.)
3. Update `skills/task/SKILL.md` (the `/process-tasks` claim description and the
   kanban/coordination notes) to document the new claim semantics and explicitly
   note that the loop is now safe to run from branch-pinned environments.
4. Note the race-window characteristics of the chosen approach in the
   "Race conditions" section of `skills/task/SKILL.md`.

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run:** Two sessions that cannot both control the branch name (simulate by
  forcing both onto the same fixed branch) cannot both claim the same `ready`
  task — the second observes the first's claim and bails. The chosen signal is
  visible from a fresh clone of `main` (or via an API query) without inspecting a
  competitor's private branch. No claim path depends on the loser's
  `git push origin task/<slug>` being the only collision point.
