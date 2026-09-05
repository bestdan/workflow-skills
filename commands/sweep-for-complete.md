---
description: Safe, schedulable sweep that finds started-state issues whose linked PR merged and completes exactly those via the /complete-task primitive — composes with /loop and /schedule, no new scheduling infra
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Glob, Grep, Read, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__save_comment, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__linear__get_issue, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__linear__save_issue, mcp__linear__save_comment, mcp__linear__list_teams, mcp__linear__list_projects, mcp__github
argument-hint: "[--apply] [--all] [--project <id|name>]"
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
- **`--all`** — widen scope to the whole team instead of the default (the
  configured projects + project-less Unassigned issues). See the handler
  file's "Preflight + resolve scope" for exactly what this changes.
- **`--project <id|name>`** — narrow scope to exactly one project (configured
  or a live/unconfigured one). Mutually exclusive with `--all`. The **default**
  (neither flag) is bounded to your configured `linear.projects` plus issues
  with no project at all — it does **not** include every other project's
  in-flight work on the team; that requires `--all` or a specific `--project`.
  The default run does not silently ignore that gap: it warns when
  started-type issues exist in team projects outside the configured scope —
  see the handler file's "Report" section for the exact line, and its
  "Preflight + resolve scope" for how the warning is computed at zero extra
  API cost.

Scope is **Linear-side**, never repo-side: one run covers every configured
project, whatever repo each project's work lives in. The title/branch
fallbacks — used for PRs opened outside `/do-tasks`, which carry no `links`
attachment — query a single repo, taken from **the issue's own project**
`repo:` key, so they work under `--all` too (see the handler file's "Resolve
each issue's PR"). Give each project a `repo:` before scheduling one sweep
across a multi-repo workspace.

Those fallbacks and the merge-check need GitHub. On `local-full` that is `gh`;
in a **cloud routine there is no `gh`**, so both run over the `mcp__github__*`
tools. That surface comes from the **GitHub App installed for claude.ai/code**,
not from a claude.ai connector — so it will not appear in a routine's connector
list, and there is nothing to "attach" there. `dev_docs/decisions/2026-08-24-routine-claim-channel.md`
records the surface enumerated in full (58 tools) from inside a routine.

**If those tools are absent, the run completes nothing.** Resolving a PR from a
`links` attachment is discovery, not verification — it proves a PR is linked,
never that it merged — and the merge-check that would prove it is the very read
that cannot run. So every in-flight issue lands in `left: unresolved`, never in
"no PR".

## 1. Resolve the handler

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `commands/task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

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
`--apply`, `--all`, and `--project` through.

## 2. Report

The handler file owns its report format, but every outcome fits this
skeleton:

- **Unsupported handler** — the one-line pointer above, nothing mutated.
- **Dry-run (default)** — the scope line, the candidate table (`IDENTIFIER —
  PR #n (merged <date>) → Done`), the left/skipped lines, and an explicit
  "nothing changed (dry-run)".
- **Applied (`--apply`)** — the same scope line and table, now with each
  row's completion outcome, plus the summary counts the handler's "Report"
  step defines.

Both, on the default (no-flag) scope only, also carry the out-of-scope
warning line the handler's "Report" step prints when a non-empty bucket was
computed — started-type issues sitting in team projects not covered by this
run — or when truncation prevented proving the bucket empty. The warning
changes nothing on its own; it only reports that `--all` or `--project
<name>` would reach more.
