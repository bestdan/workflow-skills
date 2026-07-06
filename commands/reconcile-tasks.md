---
description: Bounded, schedulable reconciler that fixes issues sitting in the wrong state against a fixed, enumerated rule table (v1 rows 1-2 only, promote/complete-only, never demotes) — composes with /loop and /schedule
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Glob, Grep, Read, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__save_comment, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__linear__get_issue, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__linear__save_issue, mcp__linear__save_comment, mcp__linear__list_teams, mcp__linear__list_projects
argument-hint: "[--apply] [--all]"
---

# Reconcile Tasks

`/sweep-for-complete` catches one specific drift: a started-type issue whose
own linked PR merged. `/reconcile-tasks` is the **bounded reconciler** built on
top of it — it fixes issues sitting in the **wrong** state against a fixed,
enumerated rule table, not open-ended "fix whatever looks off" judgment. **v1
enforces exactly two rows**, both promote/complete-only:

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

- **`--apply`** — actually apply the two rules' corrections. Without it, the
  command only prints the combined candidate table and changes nothing
  (dry-run is the default posture, mirroring `/archive-tasks` and
  `/sweep-for-complete`).
- **`--all`** — widen scope to the whole team instead of the configured
  projects + Unassigned bucket. See the handler file's "Preflight + scope" for
  exactly what this changes.

## 1. Resolve the handler

Read `dev_docs/tasks/.task-config.yml`:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- `handler: linear` → read and follow **`commands/handlers/linear-reconcile.md`**
  (resolves scope, applies row 1 via the `/sweep-for-complete` flow, applies
  row 2 by finding open-PR issues stuck in a non-started column, and folds
  both into one combined report).
- File absent, or `handler: repo-pr` → **UNSUPPORTED.** Print: "unsupported for
  handler repo-pr — a merged PR **is** the done signal for repo-pr; there is
  no separate tracker state to reconcile. `/reconcile-tasks` has nothing to
  do."
- `handler: gh-issue` → **UNSUPPORTED.** Print: "unsupported for handler
  gh-issue — GitHub already closes the issue natively on merge via
  `Closes #<n>` in the PR body, and there is no separate review-column state
  to reconcile. `/reconcile-tasks` has nothing to add; if auto-close didn't
  fire, close the issue directly with `gh issue close <n>`."
- `handler: jira` → **UNSUPPORTED.** Print: "unsupported for handler jira —
  Jira's completion path is its GitHub integration or **smart commits**
  (`<issue-key> #done` / `#comment` in a commit/PR) transitioning the issue
  natively on merge. `/reconcile-tasks` has nothing to add here; configure the
  integration or smart commits if that transition isn't firing."
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

If the relative path doesn't resolve, find the handler file with **Glob**
(`**/commands/handlers/linear-reconcile.md`) and Read the result. Pass
`--apply` and `--all` through.

## 2. Report

The handler file owns its report format, but every outcome fits this
skeleton:

- **Unsupported handler** — the one-line pointer above, nothing mutated.
- **Dry-run (default)** — the combined candidate table grouped by rule (`→
  Done` rows, `→ In Review` rows), each with its driving PR, plus the
  left/skipped lines and an explicit "nothing changed (dry-run)".
- **Applied (`--apply`)** — the same table, now with each row's outcome, plus
  the per-rule counts the handler's "Report" step defines.
