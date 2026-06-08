---
name: co-review
description: Collaborative PR review — produce your own review, optionally pull in other local agents (gemini, codex, …) as extra reviewers, reconcile everything against GitHub bot/human comments via an independent sub-agent, auto-fix high-confidence items, and ask the user about judgment calls. Supports a --local flag to review uncommitted changes with no PR, and a --post flag to review someone else's PR and post vetted findings back to GitHub. Use when the user runs /co-review or asks for a "co-review" of a PR.
---

# co-review — collaborative PR review

Combines a fresh review of the PR with whatever comments are already on GitHub — plus, optionally, reviews from other local agents you have installed — then splits the result into "auto-fix" and "ask the user."

## Independence note

The main agent writes the review _and_ applies fixes, but does **not** judge whether its own findings are correct. That job is delegated to a sub-agent (the reconciler) which sees the main agent's review, any local-agent reviews, and the GitHub comments side-by-side without knowing which came from "you."

This is not perfectly independent: the main agent still chooses what to flag in the first place, and writes the prose the reconciler reads. A reviewer with a strong prior can still bias the pool. The split exists to prevent the most obvious failure mode (the same agent grading its own homework) — not to guarantee neutrality. Pulling in other local agents widens the pool, which helps, but they are graded by the same reconciler.

## Modes

Three mode choices:

- **GitHub vs local** — by default co-review operates on a PR (fetches the diff and comments from GitHub). With `--local` it operates on your working tree instead: no PR required, no GitHub calls.
- **Which reviewers** — the main agent always reviews. Other local agents (gemini, codex, …) join the pool if configured. `--remote` forces them off for one run.
- **What happens to the findings** — by default co-review assumes the PR is **yours**: it auto-fixes high-confidence items in your working tree. With `--post` it assumes you're reviewing **someone else's** PR: it never touches the code and instead posts the vetted findings back to GitHub as a PR review.

### Flags

- `--local` — review local changes instead of a PR. Diff comes from `git diff <base>`: your working tree (committed **and** uncommitted changes) compared against `<base>`, **plus** any untracked files (`git ls-files --others --exclude-standard`), which `git diff` does not show — read those so brand-new files aren't silently skipped. No `gh` calls are made and no PR is required. Caveat: `git diff <base>` compares against `<base>`'s current tip, so if `<base>` has advanced since you branched it will also surface those upstream commits as reversed changes — diff against the merge-base instead (compute it in a separate call; don't use `$(...)`, per step 2).
- `--base <branch>` — base to diff against in `--local` mode. Defaults to `main`.
- `--remote` — skip local agents for this run: the main agent reviews and folds in GitHub comments as usual, but gemini/codex are not probed, asked about, or dispatched, and the config is left untouched. Useful for a quick "just the normal PR review" without spinning up extra agents. Mutually exclusive with `--local` (which drops GitHub entirely); if both are passed, stop and ask which the user meant.
- `--post` — review someone else's PR and post the vetted findings **back to the PR** instead of editing local files. The review and reconciliation are identical to the default flow, but the auto-fix step is replaced: nothing in the working tree is ever changed, and high/medium findings (after you vet them) are submitted as a single GitHub PR review with inline comments. Requires a PR — mutually exclusive with `--local`; if both are passed, stop and ask which the user meant. Composes with `--remote` (post a Claude-only review) and with local reviewers (post a reconciled multi-agent review).

## Local reviewers

Other local agents can act as extra reviewers. Resolution mirrors the task system's config pattern.

**Config file (repo-committed):** `dev_docs/co-review/.co-review.yml`

```yaml
local_reviewers:
  - gemini # known agent → built-in default invocation
  - codex
  - name: my-agent # custom agent → explicit invocation
    command: "my-agent review --stdin"
```

**Resolution:**

- **File absent** → not configured yet. Probe `PATH` (`command -v`) for the known default agents and **ask the user** which (if any) to use, then write their choice to the config so it isn't asked again.
- **`local_reviewers: []`** (explicit empty list) → the user chose "none." Run Claude-only and **do not re-ask**. (Absent ≠ empty — that distinction is what lets the skill remember and skip asking.)
- **File with entries** → run the **known built-in agents** (`gemini`, `codex`) silently and note which ran. Any **custom `command:`**, or any agent not on the built-in list, is untrusted (see the safety note below) — show the user the exact command and get explicit confirmation before running it.

**Detection (PATH probe + config override):** the known default list is `gemini` and `codex`. Probe each with `command -v`. The config may also name agents that aren't on the default list, supplying a `command:` for how to invoke them.

**Built-in invocations.** Everything that varies per PR — the rubric, any reviewer-specific requests, and the diff — is assembled into **one stdin stream**; the prompt argument is a short **fixed pointer**. Because nothing variable ends up in the command string, the command is invariant and can be approved once with an exact-match rule (see Permissions).

Assemble the input **and** pipe it to the agent in a **single shell invocation** — group the rubric, the reviewer-specific requests, and the diff inside one `{ …; }` pipeline so the bytes the agent reads are produced by the same shell that runs it. Do **not** write the assembled input to a temp file in one Bash call and read it back in another (see the sandbox note below for why that desyncs). The rubric is copied verbatim with `cat` (no LLM transcription); the requests ride on a here-doc so there is no intermediate file; the diff comes from `gh pr diff` / `git diff`. Keep the pointer **byte-for-byte** identical to the Permissions rules:

- `gemini`, GitHub mode →

  ```
  { cat "<this skill dir>/review_prompt.md"; cat <<'REQUESTS'
  <reviewer-specific requests, or omit this here-doc entirely if none>
  REQUESTS
  gh pr diff <n>; } | gemini -p "<POINTER>"
  ```

- `codex`, GitHub mode → same `{ …; }` group, piped to `codex exec --sandbox read-only "<POINTER>"` (use whatever read-only/sandbox flag your codex version supports).
- `--local` mode → swap `gh pr diff <n>` for `git diff <base>` inside the group (also append the contents of any untracked files you read).

Each `;`/`|`-separated segment is permission-matched on its own (`cat …` → `Bash(cat:*)`, `gh pr diff …` → `Bash(gh pr diff:*)`, the `gemini`/`codex` tail → its exact rule), so the grouped pipeline is covered by the same rules as before — only the diff and requests change, and both ride on stdin.

where `<POINTER>` is exactly:

> Review the rubric and diff on stdin. Output findings as file:line, the issue, and a suggested fix. Read only: do not modify files or run commands.

The diff and the requestor's asks ride on **stdin**, which the permission matcher never sees — so `review_prompt.md` and the per-PR requests can change freely without ever changing the approved command. A custom agent must supply its own `command:` (input is piped on stdin).

> **Why one shell, never a temp file.** An earlier version assembled the input into a temp file (`$TMPDIR/coreview-input.md`) in one Bash call and `cat`-ed it into the agent in another. That silently fed reviewers a **stale** diff from a prior session: input assembly runs in a **sandboxed** shell while the network-bound reviewer runs **unsandboxed**, and `$TMPDIR` (and even a sandbox-blocked `/tmp` write) resolves differently across that boundary — so the write landed in one place and the read came from a leftover file somewhere else. Building and consuming the bytes in a single `{ …; } | <agent>` pipeline removes the file entirely, so there is no path that can read a previous run's input. Do not reintroduce an intermediate input file (and never key one off `$TMPDIR`).

These agents must be constrained to **read-only**: they should emit a review and nothing else. Agentic CLIs like `codex exec` can edit files or run commands by default — the pointer says read-only and the `codex` invocation pins a sandbox flag, but never rely on the prompt alone: keep the sandbox flag in both the command and its allow-rule, especially in `--local` mode where edits are in flight.

> **Untrusted config — `.co-review.yml` is committed to the repo under review.** This skill runs in repos you don't control, so the config (and any custom `command:`) can be supplied by whoever wrote the repo. Treat a custom `command:`, or any agent not in the built-in list (`gemini`, `codex`), as untrusted code: **never run it silently.** Print the agent name and the exact command, and get explicit user confirmation before executing. Only the built-in agents invoked through their documented commands may run without a prompt.

## Permissions (approve once)

The reviewer command is **invariant**: everything that varies per PR (the diff and any reviewer-specific requests) travels on stdin, and the prompt argument is a fixed pointer. So you can approve each reviewer **once** with an **exact-match** rule — no broad wildcard. Merge the rules for the reviewers you use into the `permissions.allow` array in `~/.claude/settings.json` (user-wide) or the repo's `.claude/settings.json` — don't overwrite an existing settings file:

```json
{
  "permissions": {
    "allow": [
      "Bash(cat:*)",
      "Bash(gh pr diff:*)",
      "Bash(git diff:*)",
      "Bash(gemini -p \"Review the rubric and diff on stdin. Output findings as file:line, the issue, and a suggested fix. Read only: do not modify files or run commands.\")",
      "Bash(codex exec --sandbox read-only \"Review the rubric and diff on stdin. Output findings as file:line, the issue, and a suggested fix. Read only: do not modify files or run commands.\")"
    ]
  }
}
```

Why this is narrow:

- The `gemini` / `codex` rules are **exact** — they authorize only this one read-only review command with that exact prompt. They do **not** grant arbitrary `gemini -p` or `codex exec` runs, and the `codex` rule pins `--sandbox read-only` into the approved string. Edit the pointer and Claude Code re-prompts, so the approval can't silently come to mean something else.
- `Bash(cat:*)`, `Bash(gh pr diff:*)`, and `Bash(git diff:*)` cover assembling the input stream — they only **read** repo/PR data and emit it on stdout, which the `{ …; }` group pipes straight into the agent (no temp file is written, so there is nothing to allow-list for a write). Add only the diff source you use (`gh pr diff` for PRs, `git diff` for `--local`).
- The pointer string must match **byte-for-byte** between the command and the rule. Copy the invocation and the rules together; if you edit one, edit the other. If your `codex` version uses a different read-only flag, update both.
- These do **not** cover custom `command:` agents from `.co-review.yml` — those are untrusted by design (see above) and must stay prompt-on-every-run. (Plugins can't ship permission rules — only `agent`/`subagentStatusLine` settings — so this is a manual one-time step per user.)

## Steps

1. **Parse invocation.** Note any `--local`, `--remote`, `--post`, and `--base <branch>` flags and whether a PR number was passed. `--local` and `--remote` are mutually exclusive — if both are present, stop and ask which was meant. `--post` requires a PR and is **mutually exclusive with `--local`** — if both are present, stop and ask which was meant.

2. **Identify the PR** (skip entirely in `--local` mode).
   - If the user passed a PR number, use it.
   - Otherwise: run `git branch --show-current` first, then `gh pr list --head <branch> --json number,url` with the literal branch value substituted in. Do **not** combine them with `$(...)` — command substitution inside a Bash tool call is rejected by the permission matcher even when both subcommands are allowlisted.
   - If none, stop and say so (or suggest `--local` if the user just wants to review uncommitted work).

3. **Gather inputs.**
   - **GitHub mode** (in parallel):
     - `gh pr view <n> --json title,body,reviews,comments,files`
     - `gh pr diff <n>`
     - `gh api repos/{owner}/{repo}/pulls/<n>/comments` for inline review comments (top-level `comments` from `gh pr view` does not include inline diff comments).
   - **Local mode** (`--local`): `git diff <base>` (default `base = main`) for tracked changes, **plus** untracked files via `git ls-files --others --exclude-standard` so new files aren't missed (mind the merge-base caveat in the Flags section if `<base>` has advanced). No `gh` calls. There are no GitHub comments to reconcile.

4. **Resolve local reviewers.** If `--remote` was passed, skip this step entirely — no probe, no prompt, no config write — and continue with no local agents. Otherwise read `dev_docs/co-review/.co-review.yml`:
   - Absent → probe `PATH` for the known agents and ask the user which to use, then write the choice (including an empty list if they decline all) to the config.
   - Empty list → no local reviewers; continue Claude-only.
   - Entries present → built-in agents (`gemini`/`codex`) are used; for any custom `command:` or unknown agent, show it and get explicit confirmation first (see the untrusted-config note). Note which will run.

5. **Dispatch local-agent reviews** (if any) **in parallel.** Assemble the shared input stream (rubric + any reviewer-specific requests + diff) and pipe it to each agent with the fixed pointer prompt, exactly as described under **Local reviewers → Built-in invocations**; capture stdout. For any custom `command:` or non-built-in agent, show the command and get explicit user confirmation before the first run (untrusted config; see the note in Local reviewers). If an agent errors, times out, or isn't actually runnable, note it and continue — a missing reviewer is not fatal. Output is free-form prose; do not impose a JSON contract on external tools.

6. **Assess scope first.** Before any per-line review, judge whether the change is too big and should be split. Only raise this if you have **high confidence** — don't flag every multi-file change. Signals that justify a split call:
   - Multiple unrelated concerns in one diff (e.g., a refactor + a feature + a config change).
   - Distinct logical units that could land independently without breaking each other.
   - A reviewer realistically cannot hold the whole change in their head.

   Mere line count or file count alone is **not** sufficient — a large mechanical rename is fine as one unit. If you do call a split, name the proposed pieces concretely (files/hunks + one-line description each), and present the recommendation to the user as part of the review. If the change is appropriately sized, say so. (This runs in `--local` mode too — useful before a PR even exists.)

7. **Review the change yourself.** Form an independent review focused on:
   - Correctness and obvious bugs
   - Project conventions (CLAUDE.md / AGENTS.md already in context)
   - Security and perf where relevant
   - Test coverage gaps that matter
     Skip nitpicks, formatting, and pre-existing issues. Produce a list of findings with `file:line`, the issue, and your suggested fix.

8. **Spawn the reconciler sub-agent** (`general-purpose`). Give it:
   - The full diff
   - All GitHub inline comments (with author + path + line) — none in `--local` mode
   - Every review's findings — your own and each local agent's — labelled neutrally as "Reviewer A", "Reviewer B", … alongside the GitHub authors, **not** tagged with which agent produced them. The reconciler should not know which list came from "you" or which came from gemini/codex.

   Ask the sub-agent to:
   - Decide for each finding whether it's correct, given the diff and the project context it can read from the repo.
   - Assign a confidence: **high** (clearly correct, low-risk fix), **medium** (probably correct but a judgment call), **low** (wrong, not applicable to this codebase, or over-engineering for a personal repo).
   - Return a JSON array, one object per finding: `{file, line, issue, source, confidence, recommended_fix, rationale}`.
   - Treat suggestions that are over-engineered for this codebase (e.g., enterprise hardening for a personal repo) or that don't apply to its actual setup (e.g., worktree handling on a directly-cloned repo) as **low** confidence and say why — the sub-agent won't see this skill's Rules section unless you pass it along.
   - **In `--post` mode**, tell the reconciler the findings will be posted as comments on **someone else's** PR, so each `recommended_fix` should read as a concrete suggestion addressed to the author, not as an edit you're about to make.

9. **Reconcile and present** to the user. Always note which reviewers contributed (Claude + which local agents ran, or which were skipped and why). Then branch on disposition:
   - **Default (your PR):**
     - Auto-fix list (high confidence) — state what you will change.
     - Ask list (medium) — one yes/no question per item.
     - Skip list (low) — name them so the user can override if they disagree.
   - **`--post` mode (someone else's PR):**
     - Post-candidate list — **high + medium** findings, as a single numbered list. For each: `file:line`, the issue, the suggested fix, and its tier. These are what _may_ be posted, pending your vetting (step 10).
     - Skip list (low) — name them so the user can pull one back in if they disagree.

The remaining steps depend on disposition.

**Default disposition (your PR):**

10. **Apply high-confidence fixes** with Edit. Verify each:
    - Shell scripts: `bash -n`
    - Code: lint / type-check / tests if the project has them
    - Don't bundle in unrelated cleanups.

11. **Wait for the user's answers** on the medium items. Apply the ones they say yes to.

12. **Commit and push the changes.** Once the fixes are applied and verified, commit them and push to the current branch's upstream:
    - **Never commit/push to the default branch.** If the current branch is the repo's default branch (`main`/`master`), stop and tell the user to move the fixes onto a feature branch first — don't auto-commit or push review fixes straight to the default branch.
    - Stage only the files you changed as part of the review fixes — don't sweep in unrelated work that was already in the working tree.
    - Write a concise commit message describing the review fixes (e.g., `Apply co-review fixes`), summarizing the items addressed.
    - Push the current branch. If it already has an upstream (`git rev-parse --abbrev-ref --symbolic-full-name @{u}` succeeds), a plain `git push` is enough. Otherwise set one explicitly against the branch's intended remote — `git push -u <remote> HEAD`, where `<remote>` is the configured remote (default `origin`, but don't assume it: fall back to whatever `git remote` reports if `origin` isn't present).
    - If there are no committable changes (nothing was auto-fixed and the user approved nothing), skip this step.
    - Then summarize what changed and confirm the commit/push.

**`--post` disposition (someone else's PR):**

10. **Vet before posting.** Never touch the working tree — you don't own this code. Present the numbered post-candidate list and let the user deselect, edit the wording of, or pull a low finding into any candidate. Nothing is posted until the user explicitly approves the final set. If they approve none, stop and say so — post nothing.

11. **Choose the verdict.** Ask the user which review event to submit: `COMMENT` (neutral), `REQUEST_CHANGES`, or `APPROVE`. Ask this every run; don't assume.

12. **Post one batched PR review.** Submit a single review via `gh api repos/{owner}/{repo}/pulls/<n>/reviews` (use `--method POST` with `--input` reading a JSON file you write, so quoting and newlines survive):
    - `event` = the chosen verdict.
    - `body` = a short summary plus any findings that can't be anchored to a specific diff line (e.g., "missing test for X", whole-file concerns).
    - `comments` = an array of `{path, line, body}`, one per anchored candidate. `line` is the **actual file line number on the right/new side** of the diff (not a relative diff position) — a comment on an unchanged line is rejected by the API. Before submitting, anchor-check every comment: confirm its `line` is among the diff's added/modified right-side lines, and fold any that don't anchor into `body` instead. The review POST is **atomic** — a single bad line rejects the whole review and posts nothing, so validate up front rather than reacting to a rejection. If the POST still fails, retry once with the offending comment(s) moved to `body`.

13. **Report the result.** Print the review URL (`gh pr view <n> --json url` plus the review, or the API response's `html_url`). Don't commit or push anything — you changed no files.

## Rules

- Respect AGENTS.md / CLAUDE.md instructions already loaded.
- Don't re-litigate decisions the user made earlier in the conversation.
- If a bot or local-agent comment is wrong for this codebase (e.g., over-engineering for a personal repo, worktree-handling on a directly-cloned repo), the reconciler should mark it low — say so explicitly so the user sees why it was skipped.
- Never auto-fix items the user has already declined in this session.
- A local agent that fails to run is noted and skipped, never fatal.
- Don't re-ask the local-reviewer question once a config (including an explicit empty list) exists.
- Never silently run a custom `command:` or non-built-in agent from `.co-review.yml` — it's repo-controlled, untrusted code. Show it and confirm first.
- In `--post` mode, never edit the reviewed code — it isn't yours. The only output is the GitHub review.
- In `--post` mode, nothing is posted to GitHub until the user has vetted and explicitly approved the final comment set and chosen the verdict.
