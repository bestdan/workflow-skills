---
description: Find a todo small enough to finish in this session, claim it, branch, code, and open a PR — dispatches to the configured handler (Linear today; file-based defers to /process-todo --local)
allowed-tools: Bash(git *), Bash(gh *), Bash(find *), Bash(grep *), Bash(cat *), Glob, Grep, Read, Edit, Write, AskUserQuestion, Agent, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__list_issue_labels, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__create_issue_label, mcp__claude_ai_Linear__save_comment, mcp__linear__list_teams, mcp__linear__list_projects, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__linear__list_issue_labels, mcp__linear__get_issue, mcp__linear__save_issue, mcp__linear__create_issue_label, mcp__linear__save_comment
argument-hint: [issue identifier (e.g. ENG-123)] or empty to auto-pick
---

# Claim Todo

Pick one unclaimed, small-enough todo issue, decide whether this session can finish it without a human in the loop, claim it in the tracker, open a branch, do the work, and open a PR.

**How this differs from `/process-todo`:**

- `/process-todo` runs known-ready, file-based todos and dispatches **remote** cloud agents (one per todo). It is best for fire-and-forget batches.
- `/claim-todo` works against the configured tracker (Linear first; file-based defers to `/process-todo --local`), applies a model-judged feasibility filter, and runs the work **in the current session** so you can watch and intervene. It claims **at most one** todo per invocation.

If you want to drain the backlog headlessly, prefer `/process-todo --all`. If you want to pull one card and pair on it, use `/claim-todo`.

## Steps

### 1. Resolve the handler

Read `dev_docs/todos/.todo-config.yml`:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/todos/.todo-config.yml" 2>/dev/null
```

- File absent, or `handler: repo-pr` → **stop** with: "For the file-based (`repo-pr`) handler, use `/process-todo <slug> --local` instead. `/claim-todo`'s feasibility filter is tracker-aware (size, labels) and doesn't add value over file-based todos that are already promoted to `ready`."
- `handler: linear` → continue with the Linear path below (steps 2–8). Read `commands/handlers/linear-common.md` (shared config/preflight/kanban mapping) and `commands/handlers/linear-claim.md` (the `/claim-todo`-specific MCP calls) — follow them in parallel with these steps.
- `handler: gh-issue | jira` → **stop** with: "The `<handler>` handler does not yet support /claim-todo. Pull an issue manually, branch, and open a PR; or switch the handler to `linear` in `dev_docs/todos/.todo-config.yml`."
- Any other (unknown) value → **stop** with: "Unknown todo handler `<value>` in dev_docs/todos/.todo-config.yml. Run /todo-config to fix it."

If the relative paths don't resolve, find the handler files with **Glob** (`**/commands/handlers/linear-common.md` and `**/commands/handlers/linear-claim.md`) and Read the results.

### 2. Preflight

Run in parallel:

- `gh auth status 2>&1` — PR creation requires it. If it fails, stop with the same guidance as `/process-todo` step 3 (likely sandbox/keychain blocking).
- Verify the working tree is clean: `git status --porcelain`. If non-empty, **stop** and ask the user to commit/stash first — claiming a todo branches off `main`/the configured base, and dirty state would be lost.
- Resolve the base branch (default `main` unless `linear.base_branch` is set in config — see handler doc) and fetch it: `git fetch origin <base>`.

### 3. Find candidates (handler-specific)

Follow `commands/handlers/linear-claim.md` → "Find candidates". It returns a ranked list of unclaimed issues whose `estimate` is set and `< 3`, in priority order. If `$ARGUMENTS` is a specific issue identifier (e.g. `ENG-123`), skip discovery and fetch just that issue; if it does not satisfy the unclaimed/size gates, **report the reason and stop** rather than overriding the gates silently.

If no candidates remain, report "No claimable Linear issues (need `estimate` set and `< 3`, unclaimed, not `human-approval-requested`). Run `/list-todos ready` to see what is sitting in the backlog." and stop.

### 4. Judge feasibility

For each candidate (in ranked order, **one at a time** — stop at the first feasible one), fetch the full issue (title, description, labels, links) and decide whether this session can finish it without a human in the loop.

Apply judgment, not a checklist. Ask:

- Does the description describe a concrete outcome (not "investigate X")?
- Are the files or systems it touches identifiable from the description, or by a single grep in this repo?
- Would a reasonable engineer expect to land a PR in under ~1 hour of focused work, given the codebase?
- Is there anything that screams "needs a product/design call" or "depends on infra I don't have access to"?

If feasible: continue with this candidate. If not: leave a one-line Linear comment on the candidate (`Skipped by /claim-todo: <reason>`) and move to the next candidate. **Do not claim it.** If every candidate is rejected, summarize the reasons and stop — do not lower the bar.

Print the chosen issue's identifier, title, and a one-sentence rationale for why this one is feasible, then proceed.

### 5. Claim in Linear

Follow `commands/handlers/linear-claim.md` → "Claim the issue". This:

- Adds the `auto-claimed` label (creating it if absent — concurrency guard)
- Moves the issue to the team's `started`-type workflow state
- Posts a Linear comment recording the branch name and that `/claim-todo` is processing it

If the issue already has `auto-claimed` by the time you try to set it (race with another claim), **stop and pick a different candidate** by re-entering step 4 with the next candidate.

### 6. Branch and execute

**The branch name must be the exact string Linear publishes for the issue** — Linear's GitHub integration links PRs to issues by matching the branch name, so any deviation breaks auto-linking and the auto-transition to `completed` on merge.

Use Linear's published branch name verbatim. Issues returned by `<linear-mcp>__get_issue` and `list_issues` expose this as `branchName` (Linear's UI calls it "Copy git branch name"). The format Linear generates is `<username>/<identifier-lowercased>-<title-slug>`, e.g. `dan/pre-12-fix-broken-import`. Do **not** reconstruct the slug yourself — if `branchName` is present in the MCP payload, use it as-is. Only fall back to constructing it (same format) if the field is genuinely missing from the response.

```bash
git checkout <base>
git pull --ff-only
git checkout -b <branch>
```

Then do the work described in the issue. Read related files. Run the project's tests / lints / type checks if available (look for `justfile`, `Makefile`, `package.json` scripts) before committing. If you discover mid-execution that the work is **not** feasible after all, jump to step 8 (bail).

### 7. Commit, push, open PR

```bash
git add <only the files you changed>   # do NOT use git add -A
git commit -m "<conventional commit subject referencing the issue>

Closes <identifier>"
git push -u origin <branch>
```

Open the PR with `gh pr create`. **Title must be prefixed with the Linear identifier in brackets**, e.g. `[PRE-12] Fix broken import in utils.ts` — this gives reviewers and Linear's GitHub integration an unambiguous link back to the issue even before the body is read.

The PR↔issue link does **not** rely on branch-name auto-detection (which Linear matches unreliably). Three mechanisms stack here so at least one always works:

1. Identifier in PR title — surfaces to humans and to Linear's parser.
2. `Closes <identifier>` magic words **on their own line in the PR description** (not the title, not a comment — magic words in comments are ignored per Linear's docs). This drives Linear's GitHub-integration auto-transition to `completed` on merge.
3. Explicit `links` attachment via `save_issue` in step 7's "Move to review" sub-step (see Linear handler). This is the only mechanism that does not depend on the GitHub integration being installed/configured on the target repo.

Body shape:

```
## Summary

<one or two sentences from the issue's outcome>

Closes <identifier> — Linear's GitHub integration will move the issue to the
team's `completed` state on merge.

## What changed

- <bullets>

## Test plan

- [ ] <commands you ran, and what they showed>
```

Post one final Linear comment on the issue with the PR URL.

**Then move the issue to review.** Follow `commands/handlers/linear-claim.md` → "Move to review on PR open". This transitions to the team's `In Review` workflow state if one exists, otherwise leaves it in `In Progress`.

**Do NOT mark the issue Done, Completed, or Canceled.** The PR being open is not a completion signal — it's a review signal. Linear's GitHub integration moves the issue to the team's `completed` state automatically when the PR merges (via the `Closes <identifier>` line in the PR body). The issue must stay in a `started`-type state (`In Progress` or `In Review`) for the entire life of the open PR. If you find yourself reaching for `save_issue` with a `completed`-type state id after step 7, stop — that's the bug this paragraph exists to prevent.

### 8. Bail path (if execution proves infeasible)

If during step 6 the work turns out to need a human (scope creep, missing context, broken-in-an-unrelated-way, etc.):

- `git checkout <base>` and delete the local branch (`git branch -D <branch>`). Do not push.
- In Linear: remove the `auto-claimed` label, add `human-approval-requested`, move the issue back to the `backlog`-type state, and post a comment explaining what you found. Do not leave the issue in `started`.
- Report the outcome and stop. Do not silently pick a different candidate after a bail — the human should look at what tripped the bail before more work is auto-claimed.

### 9. Report

On success: print the issue identifier, the PR URL, and a one-line summary of what changed.

On bail: print the issue identifier, why it bailed, and the Linear comment URL.
