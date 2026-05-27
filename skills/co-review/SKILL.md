---
name: co-review
description: Collaborative PR review — produce your own review, reconcile it against GitHub bot/human comments via an independent sub-agent, auto-fix high-confidence items, and ask the user about judgment calls. Use when the user runs /co-review or asks for a "co-review" of a PR.
---

# co-review — collaborative PR review

Combines a fresh review of the PR with whatever comments are already on GitHub, then splits the result into "auto-fix" and "ask the user."

## Independence note

The main agent writes the review _and_ applies fixes, but does **not** judge whether its own findings are correct. That job is delegated to a sub-agent (the reconciler) which sees the main agent's review and the GitHub comments side-by-side without knowing which came from "you."

This is not perfectly independent: the main agent still chooses what to flag in the first place, and writes the prose the reconciler reads. A reviewer with a strong prior can still bias the pool. The split exists to prevent the most obvious failure mode (the same agent grading its own homework) — not to guarantee neutrality.

## Steps

1. **Identify the PR.**
   - If the user passed a PR number, use it.
   - Otherwise: run `git branch --show-current` first, then `gh pr list --head <branch> --json number,url` with the literal branch value substituted in. Do **not** combine them with `$(...)` — command substitution inside a Bash tool call is rejected by the permission matcher even when both subcommands are allowlisted.
   - If none, stop and say so.

2. **Gather inputs in parallel:**
   - `gh pr view <n> --json title,body,reviews,comments,files`
   - `gh pr diff <n>`
   - `gh api repos/{owner}/{repo}/pulls/<n>/comments` for inline review comments (top-level `comments` from `gh pr view` does not include inline diff comments).

3. **Review the PR yourself.** Form an independent review focused on:
   - Correctness and obvious bugs
   - Project conventions (CLAUDE.md / AGENTS.md already in context)
   - Security and perf where relevant
   - Test coverage gaps that matter
     Skip nitpicks, formatting, and pre-existing issues. Produce a list of findings with `file:line`, the issue, and your suggested fix.

4. **Assess PR scope.** Before per-line review, judge whether the PR is too big and should be split. Only raise this if you have **high confidence** — don't flag every multi-file PR. Signals that justify a split call:
   - Multiple unrelated concerns in one diff (e.g., a refactor + a feature + a config change).
   - Distinct logical units that could land independently without breaking each other.
   - A reviewer realistically cannot hold the whole change in their head.

   Mere line count or file count alone is **not** sufficient — a large mechanical rename is fine as one PR. If you do call a split, name the proposed PRs concretely: for each, list which files/hunks belong to it and a one-line description. If the PR is appropriately sized, say nothing about splitting and move on.

5. **Spawn the reconciler sub-agent** (`general-purpose`). Give it:
   - The full diff
   - All GitHub inline comments (with author + path + line)
   - Your own review findings — labelled neutrally as "Reviewer A" alongside the GitHub authors, **not** as "the main agent's review." The reconciler should not know which list came from you.

   Ask the sub-agent to:
   - Decide for each finding whether it's correct, given the diff and the project context it can read from the repo.
   - Assign a confidence: **high** (clearly correct, low-risk fix), **medium** (probably correct but a judgment call), **low** (wrong, not applicable to this codebase, or over-engineering for a personal repo).
   - Return a JSON array, one object per finding: `{file, line, issue, source, confidence, recommended_fix, rationale}`.
   - Treat suggestions that are over-engineered for this codebase (e.g., enterprise hardening for a personal repo) or that don't apply to its actual setup (e.g., worktree handling on a directly-cloned repo) as **low** confidence and say why — the sub-agent won't see this skill's Rules section unless you pass it along.

6. **Reconcile and present** to the user:
   - **Split recommendation** (only if step 4 produced one) — lead with this, with the proposed PR breakdown. Ask the user whether to proceed with per-line review anyway or pause to split first.
   - Auto-fix list (high confidence) — state what you will change.
   - Ask list (medium) — one yes/no question per item.
   - Skip list (low) — name them so the user can override if they disagree.

7. **Apply high-confidence fixes** with Edit. Verify each:
   - Shell scripts: `bash -n`
   - Code: lint / type-check / tests if the project has them
   - Don't bundle in unrelated cleanups.

8. **Wait for the user's answers** on the medium items. Apply the ones they say yes to.

9. **Stop short of commit/push.** Summarize what changed; let the user trigger the next step.

## Rules

- Respect AGENTS.md / CLAUDE.md instructions already loaded.
- Don't re-litigate decisions the user made earlier in the conversation.
- If a bot comment is wrong for this codebase (e.g., over-engineering for a personal repo, worktree-handling on a directly-cloned repo), the reconciler should mark it low — say so explicitly so the user sees why it was skipped.
- Never auto-fix items the user has already declined in this session.
