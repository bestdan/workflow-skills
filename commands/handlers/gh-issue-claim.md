# gh-issue handler — /do-tasks execute flow

Invoked from `/do-tasks` (section 4, "gh-issue path") when `handler: gh-issue` is configured. This file holds the full gh-issue execute flow, run in the current session: **find candidates** (read-only), **pre-flight in-flight check** (read-only), **judge feasibility** (read-only), **claim the issue** (mutating, before work starts), **branch + execute**, **PR**, and **move to review on PR open** (mutating, after the PR is opened). A separate **bail** phase runs when work proves infeasible mid-execution. It mirrors the tracker flow in `commands/handlers/linear-claim.md`, over the `gh` CLI instead of the Linear MCP.

**Shared reference:** the status-label vocabulary is the same one `commands/handlers/gh-issue.md` (`## List`) and `gh-issue-promote.md` use; the claim lock this file acquires is defined once in `commands/handlers/claim-lock.md` (shared with the jira handler); `commands/handlers/linear-claim.md` is the structural template. Reuse those labels — do **not** invent `task:*` labels.

**Branch name.** The work branch is the handler's deterministic `task/<n>` — the same
name the jira handler uses, because it is also the claim lock (see
`claim-lock.md`). This path deliberately no longer uses `gh issue develop`: its
generated branch name is not deterministic, so it cannot be probed before the claim,
and the create it performs yields no rejection this flow can read as a lost race. The
GitHub-native issue↔branch link is the cost; `Closes #<n>` in the PR body and the
`[#<n>]` title prefix carry the association instead.

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
  candidates, pre-flight, judge, then acquire the `task/<n>` claim lock, assign `@me`,
  add `auto-claimed`, remove `auto-eligible`), then **stop**: no execution, no PR. The
  created `task/<n>` lock ref plus the assigned `auto-claimed` issue is the reservation
  marker — do **not** swap to
  `needs-review`. `--claim-only` is the one execute-family action safe to batch, so
  `/do-tasks --all` / `-n N --claim-only` may reserve several issues at once, each
  bounded by the WIP gate.
- **`--no-claim <#n>`** — skip the claim and resume an issue this caller has
  **already** claimed. **Requires an explicit issue number** — there is no default
  selection. Guard: proceed only when the issue's assignee is this caller
  (`me=$(gh api user --jq .login)`; the issue's `assignees[].login` is exactly that
  one login) **and** it carries `auto-claimed`. Otherwise **stop and explain** —
  executing an unclaimed issue reopens the race the claim step closes. When the guard
  passes, **check out the existing claim branch** rather than branching fresh from
  `HEAD` — the claim created the handler's deterministic `task/<n>`, so there is nothing
  to look up:

  ```bash
  git fetch origin && git switch "task/<n>"
  ```

  If that branch exists neither locally nor on the remote — the claim ran on the
  degraded comment-election path, which creates no ref (see `claim-lock.md`) — create it
  now: `git switch -c "task/<n>" "origin/<base>"` (`<base>` defaults to the repo's
  default branch). Then run "Branch + execute" (skipping branch creation), "PR", and
  "Move to review" — without re-claiming.
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

1. **Claim branch + its PR.** The claim pushes the deterministic `task/<n>` (see "Claim the issue"), so probing that one ref catches a sibling session mid-build:

   ```bash
   git ls-remote --heads origin "task/<n>"
   gh pr list --state open --head "task/<n>" --json number,url,headRefName [--repo <repo>]
   ```

   If `git ls-remote` returns the ref, treat the issue as in flight: a non-empty `gh pr list` → `Skipped #<n>: open PR already exists (<url>)`; otherwise (branch exists, no PR yet) → `Skipped #<n>: remote branch task/<n> already exists`.

   This is the cheap read in front of the same ref the claim locks on — a trip here saves the full issue-body read and feasibility judgment. It is a probe, not the lock: the lock is the push (see `commands/handlers/claim-lock.md`).

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

Keeping the order as pre-flight → judge → claim is acceptable here because the claim below is atomic: two sessions may both judge the same issue, but only one can acquire the lock, and the loser detects the loss from the acquire result and advances to the next candidate instead of building a duplicate. (Linear's slow token-comment election is what forces its judge _inside_ the claim.)

## Claim the issue

The claim locks on an **atomic primitive** — pushing the `task/<n>` ref, a server-side compare-and-swap — because GitHub exposes no compare-and-swap on issue fields and a same-account racer reads back the identical `assignees`. Mechanics, the branch-pinned fallback, and the release rule live in **`commands/handlers/claim-lock.md`**; read it and follow it here rather than re-deriving them. The assignee and `auto-claimed` label below stay on as the **human-visible** claim marker — they no longer decide the race.

1. **Re-read** the chosen issue (`gh issue view <n> --json assignees,labels [--repo <repo>]`). If it now has an assignee, or carries `auto-claimed`, **another session beat you** — return `race`, fall back to the next candidate. This is a cheap early-out, not the lock; note the wall-clock time of this read as `T_unclaimed` (the fallback election in `claim-lock.md` needs it).
2. **Acquire the lock** — `claim-lock.md` → "Primitive: create-only ref creation via the GitHub API", with `<base>` the repo's default branch (or `--base` when `/do-tasks` passed one), and `<repo>` = `gh-issue.repo` if set, else the current repo:

   ```bash
   git fetch origin
   base_sha=$(git rev-parse "origin/<base>")
   gh api --method POST "repos/<repo>/git/refs" -f "ref=refs/heads/task/<n>" -f "sha=$base_sha"
   ```

   **HTTP 422 `Reference already exists`** → **you lost**: leave the issue's assignee and labels untouched, return `race`, and fall back to the next candidate. **Any other failure** (403/404, protected-ref ruleset, branch-pinned environment) → degrade to `claim-lock.md`'s comment-token election (using `T_unclaimed` from step 1) and report the degrade reason. Only **HTTP 201** proceeds to step 3 — check the branch out first (`git fetch origin "task/<n>" && git switch -c "task/<n>" FETCH_HEAD`). Do **not** substitute `git push origin task/<n>` for this call: both racers branch from the same base sha, so the loser's push reports `Everything up-to-date` and exits 0 (measured — see the warning in `claim-lock.md`).
3. **Mark it on the board** — assign yourself, flip the status label:

   ```bash
   gh issue edit <n> --add-assignee @me --add-label auto-claimed --remove-label auto-eligible [--repo <repo>]
   ```

4. **Confirm the marker landed** (not the race — the push in step 2 already decided that). Resolve your own login once (`me=$(gh api user --jq .login)`; `@me` is only valid as a `--add-assignee`/`--remove-assignee` argument, never a value you can match in the JSON), then re-read the issue's `assignees`. If a **different** login appears, a same-second sibling wrote the marker even though you hold the lock: leave the assignee alone (stomping it would disrupt a human's deliberate reassignment) and report `#<n>: claim lock held, but assignee is <other> — the board marker disagrees with the lock`. Do **not** return `race` on this signal alone — you hold `task/<n>` and no one else can push it, so the atomic winner is you.

(`gh issue edit` errors if a label doesn't exist; create it first with `gh label create "<label>" [--repo <repo>] 2>/dev/null`, mirroring the create flow.)

## Branch + execute

1. **Branch** — already done. "Claim the issue" step 2 created `task/<n>` from `<base>` (the repo's default branch unless `/do-tasks` passed `--base`) and pushed it as the claim lock, so this session is already on it. Confirm with `git branch --show-current` and do **not** re-create it. Only on the degraded comment-election path (no ref was pushed) create it now: `git switch -c "task/<n>" "origin/<base>"`.
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
git switch - && git push origin --delete "task/<n>"   # release the claim lock
gh issue edit <n> --remove-label auto-claimed --add-label human-approval-requested --remove-assignee @me [--repo <repo>]
gh issue comment <n> --body "Bailed by /do-tasks: <what was tried, what tripped the bail>" [--repo <repo>]
```

Release the lock **first** (`claim-lock.md` → "Release the lock") so the issue never
returns to the ready lane while a stale `task/<n>` still blocks the next session's
acquire. On the degraded election path there is no ref to delete — delete this session's
token comment instead.

Stop — do not auto-pick another candidate after a bail; a human should look before more work is auto-claimed.

## Report

`/do-tasks` prints the outcome:

- **On success:** the issue number, the PR URL, and a one-line summary of what changed.
- **On bail:** the issue number, why it bailed, and the issue-comment URL.
- **On no feasible candidate:** say so (with the skip reasons recorded on each rejected issue).
