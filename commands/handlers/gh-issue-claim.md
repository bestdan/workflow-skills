# gh-issue handler — /do-tasks execute flow

Invoked from `/do-tasks` (section 4, "gh-issue path") when `handler: gh-issue` is configured. This file holds the full gh-issue execute flow, run in the current session: **find candidates** (read-only), **pre-flight in-flight check** (read-only), **judge feasibility** (read-only), **claim the issue** (mutating, before work starts), **branch + execute**, **PR**, and **move to review on PR open** (mutating, after the PR is opened). A separate **bail** phase runs when work proves infeasible mid-execution. It mirrors the tracker flow in `commands/handlers/linear-claim.md`, over the `gh` CLI instead of the Linear MCP.

**Shared reference:** the status-label vocabulary is the same one `commands/handlers/gh-issue.md` (`## List`) and `gh-issue-promote.md` use; `commands/handlers/linear-claim.md` is the structural template. Reuse those labels — do **not** invent `task:*` labels.

> **Hard rule for every phase below: never close a gh issue manually, and never move it to a `completed`/`canceled` state.** Merge is the only completion signal — GitHub closes the issue automatically when the PR (with `Closes #<n>` in its body) merges. If you are about to `gh issue close` from this file, you have a bug — stop.

**Repo.** If `gh-issue.repo` is set in `dev_docs/tasks/.task-config.yml`, pass it as `--repo <repo>` on every `gh` call below. Otherwise omit `--repo` to act on the current repo, matching the create/list/promote flows.

## Modes: atomic vs. claim/execute split

`/do-tasks` claims and executes atomically by default. Two **mutually exclusive**
flags (`--claim-only` / `--no-claim`, passed through from `/do-tasks`) split that
into composable steps so a claim now plus a `--no-claim` execute later add up to one
normal run — passing both is an error: stop and ask which was meant.

- **default** (neither flag) — run every phase below: pre-claim WIP gate → find
  candidates → pre-flight → judge → claim → branch + execute → PR → move to review.
- **`--claim-only`** — run through "Claim the issue" (pre-claim WIP gate, find
  candidates, pre-flight, judge, then assign `@me`, add `auto-claimed`, remove
  `auto-eligible`), then **stop**: no branch, no execution, no PR. The assigned issue
  carrying `auto-claimed` is the reservation marker — do **not** swap to
  `needs-review`. `--claim-only` is the one execute-family action safe to batch, so
  `/do-tasks --all` / `-n N --claim-only` may reserve several issues at once, each
  bounded by the WIP gate.
- **`--no-claim <#n>`** — skip the claim and resume an issue this caller has
  **already** claimed. **Requires an explicit issue number** — there is no default
  selection. Guard: proceed only when the issue's assignee is this caller
  (`me=$(gh api user --jq .login)`; the issue's `assignees[].login` is exactly that
  one login) **and** it carries `auto-claimed`. Otherwise **stop and explain** —
  executing an unclaimed issue reopens the race the claim step closes. When the guard
  passes, **check out the claim branch** rather than branching fresh from `HEAD`:
  `gh issue develop <n> --list [--repo <repo>]` lists the issue's linked branch(es).
  If one exists (resuming after a crash mid-execution), `git fetch` and `git checkout`
  it — do **not** re-run `gh issue develop --checkout`, which would create a second
  branch. If none exists (the issue was reserved via `--claim-only`, which creates no
  branch), create it now with `gh issue develop <n> --checkout`. Then run "Branch +
  execute" (skipping branch creation), "PR", and "Move to review" — without re-claiming.
  `--no-claim` is always single (`--all` / `-n N` do not apply).

## Pre-claim WIP gate

Mirrors the Linear pre-claim gate. It runs **before** judging feasibility or
claiming, on every claiming run — single mode included; only `--no-claim`, which
claims nothing, skips it.

1. Resolve `wip_limit` from the top-level `wip_limit` key in
   `dev_docs/tasks/.task-config.yml` (default `3` — the same key the repo-pr and
   linear handlers use).
2. Count current in-flight work = open issues labeled `auto-claimed` (claimed, PR
   not yet open) **or** `needs-review` (PR open, in review). An in-flight issue holds
   exactly one of the two, so sum two counts:

   ```bash
   c1=$(gh issue list --state open --search "label:auto-claimed" --limit 100 --json number [--repo <repo>] --jq length)
   c2=$(gh issue list --state open --search "label:needs-review" --limit 100 --json number [--repo <repo>] --jq length)
   count=$((c1 + c2))
   ```

   Both labels count because the WIP limit exists to bound the human PR-review queue:
   "Move to review on PR open" swaps `auto-claimed → needs-review`, so a `needs-review`
   issue is an open PR still awaiting review — in-flight, not done. (This mirrors the
   linear gate, which counts both `In Progress` and `In Review`.)
3. If that count is **≥ `wip_limit`**, decline: report
   `WIP limit <wip_limit> reached (<count> in flight) — no issue claimed` and stop. Do
   not claim another issue.

For `--claim-only --all` / `-n N`, the gate bounds the batch instead of declining
outright: reserve at most `max(0, wip_limit - <count>)` issues (0 slack → reserve
nothing and report the WIP-limit decline).

## Find candidates

```bash
gh issue list --state open --search "label:auto-eligible no:assignee -label:auto-claimed -label:human-approval-requested -label:blocked" --limit 50 --json number,title,body,labels,assignees,createdAt [--repo <repo>]
```

- `label:auto-eligible` selects promoted, ready issues; `no:assignee` skips anything already claimed; `-label:auto-claimed -label:human-approval-requested -label:blocked` excludes already-claimed, unrefined, or blocked issues. All filters ride in `--search` because `gh issue list` ignores a separate `--label` flag once `--search` is present.
- As a backstop to the query filter (e.g. label-index lag), drop any issue labeled `auto-claimed`, `human-approval-requested`, or `blocked` that still slips through — these receive no claim action.
- **Rank** by a `priority:<urgent|high|medium|low>` label if present (urgent → high → medium → low, none last), then by issue age (oldest `createdAt` first — let aging issues bubble up).
- Limit 50. If exactly 50 issues are returned the page may be truncated — note it in the report; do not paginate.

Take the ranked candidates **one at a time**: for each candidate in ranked order, run **Pre-flight: is work already in flight?** and then, if it passes, **Judge feasibility** — on a pre-flight trip or a feasibility reject, advance to the next candidate and start it at pre-flight. If no candidate remains, report that and stop.

## Pre-flight: is work already in flight?

Runs on the candidate **before "Judge feasibility" and "Claim the issue"**, on every claiming path (single, direct `<#n>`, and `--claim-only`). The same cheap, high-value guard as `linear-claim.md`'s pre-flight: catch a sibling session that is already building this issue before spending the full issue-body read and feasibility judgment. If **any** check trips, **do not judge, do not claim, and do not build** — skip and report.

1. **Linked branch + its PR.** GitHub links a `gh issue develop` branch to the issue; list it and check for an open PR on that head:

   ```bash
   gh issue develop <n> --list [--repo <repo>]                       # linked branch name(s), if any
   gh pr list --state open --head "<branch>" --json number,url,headRefName [--repo <repo>]
   ```

   If `gh issue develop --list` names a branch, treat the issue as in flight: a non-empty `gh pr list` → `Skipped #<n>: open PR already exists (<url>)`; otherwise (branch exists, no PR yet) → `Skipped #<n>: remote branch <branch> already exists`.

2. **Open PR by issue number.** The execute path titles PRs `[#<n>] <title>`, so also catch a PR opened from an unlinked branch:

   ```bash
   gh pr list --state open --search "[#<n>] in:title" --json number,url,title [--repo <repo>]
   ```

   GitHub search tokenizes on punctuation, so `[#12]` searches the bare token `12` and over-matches (`[#120]`, `[#212]`, …). Before skipping, confirm a returned PR's `title` actually contains the literal `[#<n>]` token — only then **skip** with `Skipped #<n>: open PR already exists (<url>)`. (The linked-branch check in step 1 is exact and needs no post-filter.)

In single/direct mode an in-flight result **stops**; in ranked mode it moves to the next candidate and re-runs pre-flight on it.

## Judge feasibility

Runs on the candidate that just passed pre-flight — one candidate at a time, in ranked order; stop at the first feasible one. Read the full issue body and decide whether this session can finish it without a human (a concrete outcome, identifiable files, a PR landable in ~1 hour, no product/design call or inaccessible infra needed).

If feasible: continue with this candidate (proceed to "Claim the issue"). If not: leave a one-line skip comment and move to the next candidate, starting it at pre-flight:

```bash
gh issue comment <n> --body "Skipped by /do-tasks: <reason>" [--repo <repo>]
```

**Do not claim it.** If every candidate is rejected, summarize the reasons and stop — do not lower the bar. Print the chosen issue's number, title, and a one-sentence rationale, then proceed.

Keeping the order as pre-flight → judge → claim is acceptable here because the claim is a cheap read-then-write guard executed immediately after the judge, unlike Linear's slow token-comment election that forces the judge inside the claim.

## Claim the issue

GitHub has no transactional claim, so use a **claim-then-verify** guard (read-then-write, then re-read — the analogue of `linear-claim.md`'s concurrency guard plus verify):

1. **Re-read** the chosen issue (`gh issue view <n> --json assignees,labels [--repo <repo>]`). If it now has an assignee, or carries `auto-claimed`, **another session beat you** — return `race`, fall back to the next candidate.
2. **Mutate** — assign yourself, flip the status label:

   ```bash
   gh issue edit <n> --add-assignee @me --add-label auto-claimed --remove-label auto-eligible [--repo <repo>]
   ```

3. **Confirm** — resolve your own login once (`me=$(gh api user --jq .login)`; `@me` is only valid as a `--add-assignee`/`--remove-assignee` argument, never a value you can match in the JSON), then re-read the issue's `assignees`. The claim holds **iff** `assignees[].login` is exactly that one login. If anyone else appears, a concurrent claimer raced in — unassign yourself (`gh issue edit <n> --remove-assignee @me --remove-label auto-claimed --add-label auto-eligible [--repo <repo>]`) and fall back to the next candidate.

(`gh issue edit` errors if a label doesn't exist; create it first with `gh label create "<label>" [--repo <repo>] 2>/dev/null`, mirroring the create flow.)

## Branch + execute

1. **Branch** — `gh issue develop <n> --checkout [--base <branch>] [--repo <repo>]` creates a branch linked to the issue and checks it out (base defaults to the repository's default branch; pass `--base` to override). Use the name `gh issue develop` prints — do not invent a branch name.
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
