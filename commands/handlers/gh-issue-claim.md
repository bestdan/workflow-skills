# gh-issue handler — /do-tasks execute flow

Invoked from `/do-tasks` (section 1, "gh-issue path") when `handler: gh-issue` is configured. This file holds the full gh-issue execute flow, run in the current session: **find candidates** (read-only), **judge feasibility** (read-only), **claim the issue** (mutating, before work starts), **branch + execute**, **PR**, and **move to review on PR open** (mutating, after the PR is opened). A separate **bail** phase runs when work proves infeasible mid-execution. It mirrors the tracker flow in `commands/handlers/linear-claim.md`, over the `gh` CLI instead of the Linear MCP.

**Shared reference:** the status-label vocabulary is the same one `commands/handlers/gh-issue.md` (`## List`) and `gh-issue-promote.md` use; `commands/handlers/linear-claim.md` is the structural template. Reuse those labels — do **not** invent `task:*` labels.

> **Hard rule for every phase below: never close a gh issue manually, and never move it to a `completed`/`canceled` state.** Merge is the only completion signal — GitHub closes the issue automatically when the PR (with `Closes #<n>` in its body) merges. If you are about to `gh issue close` from this file, you have a bug — stop.

**Repo.** If `gh-issue.repo` is set in `dev_docs/tasks/.task-config.yml`, pass it as `--repo <repo>` on every `gh` call below. Otherwise omit `--repo` to act on the current repo, matching the create/list/promote flows.

> **Scope note.** This is the **core** single-execute path. The claim/execute split (`--claim-only` / `--no-claim`) and a pre-claim WIP gate are **not** implemented here yet — `/do-tasks` runs the atomic claim+execute below. Those compose on top in a follow-up.

## Find candidates

```bash
gh issue list --state open --search "label:auto-eligible no:assignee" --limit 50 --json number,title,body,labels,assignees [--repo <repo>]
```

- `label:auto-eligible` selects promoted, ready issues; `no:assignee` skips anything already claimed. (Both ride in `--search` because `gh issue list` ignores a separate `--label` flag once `--search` is present.)
- From the result, drop any issue labeled `auto-claimed`, `human-approval-requested`, or `blocked` (a defensive filter — `no:assignee` already excludes most claimed work).
- **Rank** by a `priority:<urgent|high|medium|low>` label if present (urgent → high → medium → low, none last), then by issue age (oldest `createdAt` first — let aging issues bubble up).
- Limit 50. If exactly 50 issues are returned the page may be truncated — note it in the report; do not paginate.

Return the ranked list to **Judge feasibility**. If no candidate remains, report that and stop.

## Judge feasibility

Take candidates in ranked order, **one at a time** — stop at the first feasible one. For each, read the full issue body and decide whether this session can finish it without a human (a concrete outcome, identifiable files, a PR landable in ~1 hour, no product/design call or inaccessible infra needed).

If feasible: continue with this candidate (proceed to "Claim the issue"). If not: leave a one-line skip comment and move to the next:

```bash
gh issue comment <n> --body "Skipped by /do-tasks: <reason>" [--repo <repo>]
```

**Do not claim it.** If every candidate is rejected, summarize the reasons and stop — do not lower the bar. Print the chosen issue's number, title, and a one-sentence rationale, then proceed.

## Claim the issue

GitHub has no transactional claim, so use a **read-then-write guard** (the analogue of `linear-claim.md`'s concurrency guard):

1. **Re-read** the chosen issue (`gh issue view <n> --json assignees,labels [--repo <repo>]`). If it now has an assignee, or carries `auto-claimed`, **another session beat you** — return `race`, fall back to the next candidate.
2. **Mutate** — assign yourself, flip the status label:

   ```bash
   gh issue edit <n> --add-assignee @me --add-label auto-claimed --remove-label auto-eligible [--repo <repo>]
   ```

3. **Confirm** — re-read once more. If `assignees` is anyone other than you alone, a concurrent claimer raced in; unassign yourself (`gh issue edit <n> --remove-assignee @me --remove-label auto-claimed --add-label auto-eligible`) and fall back to the next candidate. Otherwise the claim holds.

(`gh issue edit` errors if a label doesn't exist; create it first with `gh label create "<label>" [--repo <repo>] 2>/dev/null`, mirroring the create flow.)

## Branch + execute

1. **Branch** — `gh issue develop <n> --base <base> [--repo <repo>]` creates a branch linked to the issue (base defaults to the repository's default branch; pass `--base` to override). **Check it out verbatim** (`gh issue develop` prints the branch name, or `--checkout` it) — do not invent a branch name.
2. **Execute** — do the work, then run the project's quality gate (`just check` here). Keep the diff scoped to this one issue.

## PR

```bash
gh pr create --title "[#<n>] <title>" --body "Closes #<n>

<summary>" [--repo <repo>]
```

`Closes #<n>` on its own line is the completion signal — GitHub closes the issue on merge. Then post the PR URL back to the issue:

```bash
gh issue comment <n> --body "PR opened: <PR URL>" [--repo <repo>]
```

## Move to review on PR open

Swap the in-progress label for the review label (the issue stays open — review is signalled by the label and the linked PR):

```bash
gh issue edit <n> --remove-label auto-claimed --add-label needs-review [--repo <repo>]
```

Never `gh issue close` here, regardless of how done the work feels — merge handles closure via `Closes #<n>`.

## Bail (when execution proves infeasible mid-flight)

```bash
git stash push -u
gh issue edit <n> --remove-label auto-claimed --add-label human-approval-requested --remove-assignee @me [--repo <repo>]
gh issue comment <n> --body "Bailed by /do-tasks: <what was tried, what tripped the bail>" [--repo <repo>]
```

Stop — do not auto-pick another candidate after a bail; a human should look before more work is auto-claimed.

## Report

`/do-tasks` prints the outcome:

- **On success:** the issue number, the PR URL, and a one-line summary of what changed.
- **On bail:** the issue number, why it bailed, and the issue-comment URL.
- **On no feasible candidate:** say so (with the skip reasons recorded on each rejected issue).
