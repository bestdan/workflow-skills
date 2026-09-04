---
description: Bounded, schedulable reconciler that fixes issues sitting in the wrong state against a fixed, enumerated rule table (linear repairs PR-versus-column drift; gh-issue audits the label invariants) — composes with /loop and /schedule
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(python3 *), Glob, Grep, Read, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__save_comment, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__linear__get_issue, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__linear__save_issue, mcp__linear__save_comment, mcp__linear__list_teams, mcp__linear__list_projects
argument-hint: "[--apply] [--all] [--project <id|name>]"
---

# Reconcile Tasks

`/sweep-for-complete` catches one specific drift: a started-type issue whose
own linked PR merged. `/reconcile-tasks` is the **bounded reconciler** built on
top of it — it fixes issues sitting in the **wrong** state against a fixed,
enumerated rule table, not open-ended "fix whatever looks off" judgment.

**Each handler's table is its own**, because the two trackers drift in different
places. The rows below are the **`linear`** table; the `gh-issue` table is three
rows auditing the label invariants and lives in
`commands/handlers/gh-issue-reconcile.md`. What both tables share is the
doctrine: a closed rule set, and no rule that retires live work on an ambiguous
read.

For `linear`, **v1 enforces exactly two rows**, both promote/complete-only:

1. Linked PR **merged**, issue still in a **started**-type state → **Done**
   (delegated to the `/sweep-for-complete` flow, whose started-type scope this
   row inherits — a merged PR on a non-started issue is reported as an
   anomaly, not acted on).
2. **Open** PR, issue in a **non-started** column (Backlog/Todo) → **In
   Review**.

Both rules only ever move an issue **forward** — toward review or toward
done. Neither ever demotes an issue back to an earlier column. A mistaken read
under this doctrine can at worst leave an issue ahead of where it should be,
never retire live work it shouldn't have touched.

This command is deliberately **safe to schedule**. It defaults to a dry run
and composes with `/loop` (e.g. `/loop 15m /reconcile-tasks --apply`) or
`/schedule` without any new scheduling machinery of its own — the cadence
lives in whichever of those two wraps it.

## Arguments

All three flags pass through to whichever handler file runs. What each one
scopes is that handler's business — the descriptions below are `linear`'s, with
the `gh-issue` reading named alongside.

- **`--apply`** — actually apply the rules' corrections. Without it, the
  command only prints the candidate table and changes nothing (dry-run is the
  default posture, mirroring `/archive-tasks` and `/sweep-for-complete`). For
  `gh-issue` this repairs its **row 1 only**; its other two rows are flag-only
  and write nothing at any flag combination.
- **`--all`** — widen scope to the whole team instead of the default (the
  configured projects + project-less Unassigned issues). See the handler
  file's "Preflight + scope" for exactly what this changes. For `gh-issue`
  there is no team: `--all` drops the configured-label scope and audits every
  issue in the repo.
- **`--project <id|name>`** — narrow scope to exactly one project (configured
  or a live/unconfigured one). Mutually exclusive with `--all`. The **default**
  (neither flag) is bounded to your configured `linear.projects` plus issues
  with no project at all — it does **not** include every other project's
  in-flight work on the team; that requires `--all` or a specific `--project`.
  **`gh-issue` does not support this flag** — it has no project dimension yet.
  It says so rather than silently scoping by nothing, and **paired with
  `--apply` it stops**: a request to narrow must not come back as a write
  outside the narrowing.

## 1. Resolve the handler

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `commands/task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

- `handler: linear` → read and follow **`commands/handlers/linear-reconcile.md`**
  (resolves scope, applies row 1 via the `/sweep-for-complete` flow, applies
  row 2 by finding open-PR issues stuck in a non-started column, and folds
  both into one combined report).
- File absent, or `handler: repo-pr` → **UNSUPPORTED.** Print: "unsupported for
  handler repo-pr — a merged PR **is** the done signal for repo-pr; there is
  no separate tracker state to reconcile. `/reconcile-tasks` has nothing to
  do."
- `handler: gh-issue` → read and follow
  **`commands/handlers/gh-issue-reconcile.md`**. It reconciles something
  different from the Linear rows above, and deliberately: GitHub closes the
  issue natively on merge via `Closes #<n>` and the open PR **is** the review
  state, so the PR-versus-column drift rows 1–2 repair cannot occur. What can
  drift is the **label** state model, which the web UI can edit by hand — so
  that handler audits the `labels.yml` invariants instead, on its own closed
  three-row table (double `status:` rung → keep the highest; a missing
  `status:`/`auto:` rung → flag; closed without ever reaching
  `status:4_needs_review` → flag). Only the first row writes, and only under
  `--apply`.
- `handler: jira` → **UNSUPPORTED.** Print: "unsupported for handler jira —
  Jira's completion path is its GitHub integration or **smart commits**
  (`<issue-key> #done` / `#comment` in a commit/PR) transitioning the issue
  natively on merge. `/reconcile-tasks` has nothing to add here; configure the
  integration or smart commits if that transition isn't firing."
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

If the relative path doesn't resolve, find the handler file with **Glob**
(`**/commands/handlers/<handler>-reconcile.md`) and Read the result. Pass
`--apply`, `--all`, and `--project` through.

## 2. Report

The handler file owns its report format, but every outcome fits this
skeleton:

- **Unsupported handler** — the one-line pointer above, nothing mutated.
- **Dry-run (default)** — the scope line, the candidate table grouped by
  rule, plus the left/skipped lines and an explicit "nothing changed
  (dry-run)". `linear` groups by correction (`→ Done` rows, `→ In Review`
  rows), each with its driving PR; `gh-issue` groups by invariant, with no PR
  involved.
- **Applied (`--apply`)** — the same scope line and table, now with each
  row's outcome, plus the per-rule counts the handler's "Report" step
  defines.
