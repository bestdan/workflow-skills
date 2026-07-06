---
description: Safe, schedulable sweep that finds started-state issues whose linked PR merged and completes exactly those via the /complete-task primitive — composes with /loop and /schedule, no new scheduling infra
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Glob, Grep, Read, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__save_comment, mcp__claude_ai_Linear__list_teams, mcp__linear__get_issue, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__linear__save_issue, mcp__linear__save_comment, mcp__linear__list_teams
argument-hint: "[--apply] [--all]"
---

# Sweep for Complete

`/complete-task` is the purely mechanical "flip one issue to done" primitive —
it trusts whatever identifier it's given and does no merge verification of its
own. `/sweep-for-complete` is the layer above it: it finds the issues that are
**actually** done — sitting in a started-type state with their own linked PR
already merged — and calls `/complete-task` once per verified match. Nothing
here searches PR text for bare issue ids; it starts from the issues the
tracker is already holding open and asks, for each one individually, whether
**its own** structurally-linked PR merged.

This command is deliberately **safe to schedule**. It defaults to a dry run,
mutates only started-type issues with a merged PR, and composes with `/loop`
(e.g. `/loop 15m /sweep-for-complete --apply`) or `/schedule` without any new
scheduling machinery of its own — the cadence lives in whichever of those two
wraps it.

## Arguments

- **`--apply`** — actually complete the verified matches. Without it, the
  command only prints the candidate table and changes nothing (dry-run is the
  default posture, mirroring `/archive-tasks`).
- **`--all`** — widen scope to the whole team instead of the configured
  projects + Unassigned bucket. See the handler file's "Preflight + resolve
  scope" for exactly what this changes.

## 1. Resolve the handler

Read `dev_docs/tasks/.task-config.yml`:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- `handler: linear` → read and follow
  **`commands/handlers/linear-sweep-complete.md`** (resolves scope, finds
  started-type issues, resolves each one's own linked PR, checks its merge
  state, and calls the `/complete-task` linear phase per verified match).
- File absent, or `handler: repo-pr` → **UNSUPPORTED.** Print: "unsupported for
  handler repo-pr — a merged PR **is** the done signal for repo-pr; there is no
  separate tracker state to reconcile. `/sweep-for-complete` has nothing to
  do."
- `handler: gh-issue` → **UNSUPPORTED.** Print: "unsupported for handler
  gh-issue — GitHub already closes the issue natively on merge via
  `Closes #<n>` in the PR body. `/sweep-for-complete` has nothing to add; if
  that auto-close didn't fire, close the issue directly with
  `gh issue close <n>`."
- `handler: jira` → **UNSUPPORTED.** Print: "unsupported for handler jira —
  Jira's completion path is its GitHub integration or **smart commits**
  (`<issue-key> #done` / `#comment` in a commit/PR) transitioning the issue
  natively on merge. `/sweep-for-complete` has nothing to add here; configure
  the integration or smart commits if that transition isn't firing."
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

If the relative path doesn't resolve, find the handler file with **Glob**
(`**/commands/handlers/linear-sweep-complete.md`) and Read the result. Pass
`--apply` and `--all` through.

## 2. Report

The handler file owns its report format, but every outcome fits this
skeleton:

- **Unsupported handler** — the one-line pointer above, nothing mutated.
- **Dry-run (default)** — the candidate table (`IDENTIFIER — PR #n (merged
  <date>) → Done`), the left/skipped lines, and an explicit "nothing changed
  (dry-run)".
- **Applied (`--apply`)** — the same table, now with each row's completion
  outcome, plus the summary counts the handler's "Report" step defines.
