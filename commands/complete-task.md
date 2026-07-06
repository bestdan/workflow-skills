---
description: Explicitly transition one identified work item to its tracker's completed state — a handler-dispatched, manually-invoked primitive that trusts the caller and does no PR/merge verification of its own
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Glob, Grep, Read, AskUserQuestion, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__save_comment, mcp__claude_ai_Linear__list_teams, mcp__linear__get_issue, mcp__linear__list_workflow_states, mcp__linear__save_issue, mcp__linear__save_comment, mcp__linear__list_teams
argument-hint: "<identifier> [--dry-run]"
---

# Complete Task

`/do-tasks`' tracker path never moves an issue to a `completed`-type state —
merge is meant to be the completion signal. But relying on that signal alone
breaks once a tracker's native auto-close integration is off (for example,
Linear's GitHub integration disabled or unreachable): the PR merges, the issue
never moves, and the tracker silently drifts out of sync with reality.
`/complete-task` is the explicit, mechanical replacement for that missing
auto-close: given one identifier, it transitions that single issue to its
handler's terminal `done`/`completed` state.

> **Trust boundary — read this before using it manually.** When invoked
> **manually** with an explicit identifier (`/complete-task PRE-123`), this verb
> **trusts the caller**. It does **not** check whether a PR merged, whether the
> work actually landed, or whether anything shipped — it takes the identifier at
> face value and completes it. The merge-verification step lives **one layer
> up**, in `/sweep-for-complete` (a future command that discovers merged PRs and
> then calls this primitive per verified issue, passing an already-verified
> signal — see the handler file's `--assume-verified` contract). `/complete-task`
> itself does no PR discovery and no verification: it is the purely mechanical
> "flip this one state" step, nothing more. Do not add merge-detection logic
> here — that belongs in the sweep, one layer up.

Like `/do-tasks` and `/archive-tasks`, this command is a **thin dispatcher**: it
resolves the **handler** from `dev_docs/tasks/.task-config.yml` (absent →
`repo-pr`) and then reads and follows the handler-specific completion procedure,
which owns the tracker-specific mechanics. The mechanics live in the handler
file this command **references rather than re-specifies**, so the two cannot
drift.

## Arguments

- **`<identifier>`** (required) — the tracker's issue identifier (e.g. `PRE-123`
  for Linear). No default; the command trusts exactly the id it is given.
- **`--dry-run`** — print the planned transition and stop. Change nothing.

## 1. Resolve the handler

Read `dev_docs/tasks/.task-config.yml`:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- `handler: linear` → read and follow **`commands/handlers/linear-complete.md`**
  (resolves the team's `completed`-type state, checks idempotence, confirms
  before mutating, applies the transition via `save_issue`, optionally posts a
  comment).
- File absent, or `handler: repo-pr` → **UNSUPPORTED.** Print: "unsupported for
  handler repo-pr — repo-pr has no separate completion step to drive. Its done
  signal **is** the merged PR (and/or the task file's `status: done`
  frontmatter, set by hand or by whatever closed the loop). There is nothing
  for `/complete-task` to transition." Do not silently no-op without this line.
- `handler: gh-issue` → **UNSUPPORTED.** Print: "unsupported for handler
  gh-issue — GitHub already closes the issue natively on merge via
  `Closes #<n>` in the PR body. `/complete-task` has nothing to add; if that
  auto-close didn't fire, close the issue directly with `gh issue close <n>`."
- `handler: jira` → **UNSUPPORTED.** Print: "unsupported for handler jira —
  Jira's completion path is its GitHub integration or **smart commits**
  (`<issue-key> #done` / `#comment` in a commit/PR) transitioning the issue
  natively on merge. `/complete-task` has nothing to add here; configure the
  integration or smart commits if that transition isn't firing."
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

If the relative path doesn't resolve, find the handler file with **Glob**
(`**/commands/handlers/linear-complete.md`) and Read the result. Pass the
identifier and the `--dry-run` flag through.

## 2. Report

The handler file owns its report format, but every outcome fits this skeleton:

- **Unsupported handler** — the one-line pointer above, nothing mutated.
- **Already complete** — the identifier and its current terminal state; no
  write made.
- **dry-run** — the planned `<current state> → <completed state>` transition,
  and an explicit "nothing changed (dry-run)".
- **applied** — the identifier, old state → new state, and whether a comment
  was posted.
