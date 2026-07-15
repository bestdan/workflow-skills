---
description: Detect completed Linear issues that no merged PR owns (the bare-id over-close bug) and optionally restore them — a safe, schedulable backstop
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(op *), Bash(python3 *), Glob, Grep, Read, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__linear__list_teams, mcp__linear__list_projects
argument-hint: "[--apply] [--project <uuid>] [--repo <owner/name>] [--since 48h] [--only PRE-1,PRE-2]"
---

# Find False Closures

Linear's GitHub integration treats a **bare** issue id (`PRE-123`) appearing
anywhere in a merged PR's title or body as a closing reference, so a PR that
merely name-drops a sibling issue sweeps that sibling to Done — no branch, no
PR, no code. `/reconcile-tasks` can't repair it (its rule table is
promote/complete-only and never demotes), and `/sweep-for-complete` is immune
to the bug but doesn't detect issues already falsely closed.

`/find-false-closures` is that backstop: it lists completed issues with **no
owning merged PR** and, with `--apply`, restores them to their team's
Todo/unstarted state. Like `/sweep-for-complete` it defaults to a **dry run**,
mutates nothing without `--apply`, and composes with `/loop`
(`/loop 1d /find-false-closures`) or `/schedule` — the cadence lives in
whichever of those wraps it.

## Arguments

- **`--apply`** — restore the flagged issues to Todo. Without it, the command
  prints the candidate table and changes nothing (dry-run is the default).
- **`--project <uuid>`** — scope to one Linear project UUID, overriding the
  configured `linear.projects`. Repeatable is not needed here — omit to sweep
  every configured project.
- **`--repo <owner/name>`** — the GitHub repo whose merged PRs establish
  ownership. Resolution order: this flag, else the project's own `repo:` in
  `linear.projects` (each project can name its repo — the workspace spans
  more than one), else the current repo's `origin` (`gh repo view`).
- **`--since <window>`** — only consider issues completed within the window:
  `48h` / `2d` shorthand, an ISO datetime, or a Linear duration (`-P2D`).
  Omit to scan the whole completed history. Ideal for a scheduled run that
  only needs to look at what closed since it last ran.
- **`--only PRE-1,PRE-2`** — with `--apply`, restore **exactly** these ids
  (which must be among the detected false closures) instead of the whole
  flagged set. Lets an apply match a specific approval rather than reopening
  everything a project-wide scan turns up.

## 1. Resolve the handler

Read `dev_docs/tasks/.task-config.yml`:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- `handler: linear` → read and follow
  **`commands/handlers/linear-false-closures.md`** (resolves scope + repo,
  runs the detection asset per project, and — with `--apply` — restores the
  flagged issues). If the relative path doesn't resolve, find it with **Glob**
  (`**/commands/handlers/linear-false-closures.md`) and Read the result. Pass
  `--apply`, `--project`, `--repo`, `--since`, and `--only` through.
- File absent, or `handler: repo-pr` → **UNSUPPORTED.** Print: "unsupported for
  handler repo-pr — there is no tracker state to falsely close; a merged PR
  **is** the record. `/find-false-closures` has nothing to do."
- `handler: gh-issue` → **UNSUPPORTED.** Print: "unsupported for handler
  gh-issue — GitHub closes an issue only on an explicit `Closes #<n>` in the PR
  body, never on a bare mention, so this over-close class doesn't occur."
- `handler: jira` → **UNSUPPORTED.** Print: "unsupported for handler jira —
  Jira transitions issues via its GitHub integration / smart commits on an
  explicit reference, not a bare mention, so this over-close class doesn't
  occur."
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

## 2. Report

The handler file owns its report format, but every outcome fits this skeleton:

- **Unsupported handler** — the one-line pointer above, nothing mutated.
- **Dry-run (default)** — per project, the `ok`/`skip` lines and the
  `FALSE CLOSURES` candidate table, plus an explicit "nothing restored
  (dry-run)".
- **Applied (`--apply`)** — the same table, now with each restore's outcome,
  plus the per-project restored/failed counts the asset prints.
