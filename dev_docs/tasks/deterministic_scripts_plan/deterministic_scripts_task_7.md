---
title: task-scan.py --archive-candidates mode + wire repo-pr-archive
priority: medium
size: 2
status: ready
created: 2026-07-17
expires: 2026-08-16
source_branch: claude/sleepy-ride-8d4bjx
related_files:
  - scripts/task-scan.py # add the --archive-candidates mode
  - commands/handlers/repo-pr-archive.md # §2 candidate selection consumer
  - scripts/check.sh
is_blocked_by: deterministic_scripts_task_1
parent: deterministic_scripts
impact: 2
tags: [scripts, repo-pr, archive]
---

# Task 7 — `task-scan.py --archive-candidates` + wire repo-pr-archive

Part of [[deterministic_scripts_plan]]. Split out of
[[deterministic_scripts_task_1]] (plan open-question 1, resolved: keep task 1
within the one-PR budget). Depends on task 1 shipping the base scanner.

## Context

The audit's Findings #3/#6 noted that `repo-pr-archive.md` §2's candidate
selection — `status: done` cards whose completion date is older than N days —
is fiddly deterministic logic best folded into `task-scan.py` rather than given
its own script. Task 1 already emits the `status: done` grouping; this task adds
the date filter on top and wires the archive handler to it.

The completion-date resolution is a **three-way fallback** per
`repo-pr-archive.md` §2 (read the current prose for the exact precedence —
e.g. an explicit completion field, else last-modified, else `created`). Preserve
that precedence exactly; this is a relocation, not a redesign.

## Task

1. Add `--archive-candidates --older-than <N>` to `scripts/task-scan.py`: emit
   the `status: done` cards whose resolved completion date (three-way fallback
   per `repo-pr-archive.md` §2) is older than `N` days. Reuse the base scanner's
   frontmatter parse — don't re-read files a second way.
2. Extend `scripts/test-task-scan.sh` with archive fixtures: a done card older
   than N (selected), a done card newer than N (not selected), and each rung of
   the three-way date fallback (explicit field present / absent → next source).
3. **Wire `repo-pr-archive.md` §2** to call
   `task-scan.py --archive-candidates --older-than N` and cite it; keep the
   surrounding archive-execution prose (the actual retire/prune step stays as
   is — only candidate *selection* moves to the script).

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes including the new archive fixtures.
- `scripts/task-scan.py --archive-candidates --older-than 30 <dir>` emits only
  done cards older than 30 days; the newer-than-30 fixture is excluded.
- `rg -n "task-scan.py" commands/handlers/repo-pr-archive.md` shows §2 calling
  the script.

**User-run:**

- Confirm `--archive-candidates --older-than 30` selects the same done-cards the
  pre-change `repo-pr-archive.md` §2 prose would over a real task tree.
