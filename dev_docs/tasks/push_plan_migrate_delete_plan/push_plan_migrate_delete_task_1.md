---
title: "push-plan: write overview into the tracker container description"
priority: high
size: 2
status: new
created: 2026-06-29
source_branch: main
related_files:
  - commands/push-plan.md:109   # §4.2 Linear — resolve/create project container
  - commands/push-plan.md:198   # §5.2 gh-issue — resolve/create milestone container
  - commands/push-plan.md:307   # §5b.2 Jira — already writes the Epic description
parent: push_plan_migrate_delete
expires: 2026-09-29
tags:
  - scope: docs
---

> Plan: [[push_plan_migrate_delete_plan]]

## Context

`commands/push-plan.md` creates a tracker **container** from the plan's overview
epic file: a Linear **project** (§4.2), a gh-issue **milestone** (§5.2), or a
Jira **Epic** (§5b.2). Today the container is created with only a name/title —
the overview body (Goal / Scope / Approach / Tasks / Open questions) has **no home
in the tracker** and is the reason the local epic file currently can't be safely
deleted.

This task gives the overview a home so later tasks can delete the local file. It
is a precondition for task 3's epic deletion.

Two important constraints:

- **Only write the description when the container is _created_, never when it is
  _reused_.** Reuse paths — a recorded `tracker_id` on the epic, `linear.default_project`,
  or a found-by-title match — point at a possibly shared/pre-existing container;
  overwriting its description would be destructive. The existing "Cases 1–2 write
  nothing new" rule (§4.2) already establishes this boundary.
- **Jira already complies:** §5b.2's `createJiraIssue` call passes
  `description:` the epic body with `contentFormat: "markdown"`. Leave it as-is;
  just confirm it and keep it consistent with the wording added for the other two.

## Task

In `commands/push-plan.md`:

1. **§4.2 (Linear), create branch (case 3 — `save_project`):** add the overview
   body as the project description. The overview body = the epic file's markdown
   below its frontmatter. Add `description: <overview body>` to the
   `<linear-mcp>__save_project` create call, and a one-line note that the
   description is set **only on create** (cases 1–2 still write nothing new).
2. **§5.2 (gh-issue), create branch:** add `-f description="<overview body>"` to
   the `gh api repos/<repo>/milestones` create call. Do **not** add a description
   on the reuse-by-title lookup branch, and do **not** add one to the `plan:<name>`
   label fallback (a label has no description field — task 3 handles that case by
   keeping the epic file).
3. **§5b.2 (Jira):** add a one-line confirmation that the Epic description already
   carries the overview body (no behavior change), so the three paths read
   consistently.

Keep the edits to prose/pseudo-commands in the skill file — there is no executable
code here.

## Acceptance Criteria

- §4.2 instructs setting the Linear project `description` to the overview body on
  **create only**; reuse paths leave the description untouched.
- §5.2 instructs setting the milestone `description` to the overview body on
  **create only**; the label fallback is explicitly excluded.
- §5b.2 notes the Jira Epic already carries the overview body.
- **Code-enforced:** `just check` passes (dprint formatting + `claude plugin
  validate . --strict` + `scripts/validate.py`).
