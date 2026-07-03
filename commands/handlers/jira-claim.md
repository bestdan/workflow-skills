# jira handler — /do-tasks execute flow

Invoked from `/do-tasks` (section 5, "jira path") when `handler: jira` is configured. This file holds the full jira claim/execute flow, run in the current session over the Atlassian MCP: **find candidates** (read-only), **pre-flight in-flight check** (read-only), **judge feasibility** (read-only), **claim the issue** (mutating, before work starts), **branch + execute**, **PR**, and **move to review on PR open** (mutating, after the PR is opened). A separate **bail** phase runs when work proves infeasible mid-execution. It mirrors the tracker flow in `commands/handlers/linear-claim.md`, over the Atlassian MCP instead of the Linear MCP — the same way `gh-issue-claim.md` mirrors it over the `gh` CLI.

**Shared reference:** the Atlassian MCP preflight is `commands/handlers/jira.md` step 1; the `ready_status` config key (the jira analogue of the Linear `Todo`/`unstarted` ready lane) is defined in `commands/handlers/jira-config.md`; `commands/handlers/linear-claim.md` is the structural template.

> **MCP namespace.** `<atlassian-mcp>__` is `mcp__claude_ai_Atlassian__` or `mcp__atlassian__` depending on the install (see `jira-config.md`) — substitute the prefix loaded in your session. Tool names after the prefix (`getAccessibleAtlassianResources`, `searchJiraIssuesUsingJql`, `getJiraIssue`, `editJiraIssue`, `atlassianUserInfo`, `getTransitionsForJiraIssue`, `transitionJiraIssue`, `addCommentToJiraIssue`) are identical across installs.

> **Scope.** This flow now carries the same `--claim-only`/`--no-claim` split and pre-claim WIP gate that `linear-claim.md` and `gh-issue-claim.md` do — see "Modes: atomic vs. claim/execute split" and "Pre-claim WIP gate" below. `/do-tasks` claims and executes atomically by default; the two flags split that into composable steps.

> **Hard rule for every phase below: never transition a jira issue to a `Done`/`completed`-category status, and never close it manually.** Merge is the only completion signal — Jira's GitHub integration (or a smart commit on merge) closes the issue automatically when the PR, whose title carries `[<KEY>]` and whose body names `<KEY>`, merges. If you are about to `transitionJiraIssue` to a `Done`-category status from this file, you have a bug — stop.

## Config

Read `dev_docs/tasks/.task-config.yml`. The jira claim flow reads:

- `jira.site` — cloudId / site URL (as the create and promote flows use).
- `jira.project` — project key.
- `jira.ready_status` — **required here.** The status the ready lane lives in (the jira analogue of Linear's `Todo`). `/do-tasks` pulls candidates from this status. If it is unset/empty, **stop** with: "jira `/do-tasks` needs `jira.ready_status` set in dev_docs/tasks/.task-config.yml (the status promoted issues land in). Set it, or run `/task-config jira`." Do not guess a status name.
- `jira.base_branch` — optional; the branch `/do-tasks` branches from (default: the repo's default branch).

## Modes: atomic vs. claim/execute split

`/do-tasks` claims and executes atomically by default. Two **mutually exclusive**
flags (`--claim-only` / `--no-claim`, passed through from `/do-tasks`) split that
into composable steps so a claim now plus a `--no-claim` execute later add up to one
normal run — passing both is an error: stop and ask which was meant.

- **default** (neither flag) — run every phase below: pre-claim WIP gate → find
  candidates → pre-flight → judge → claim → branch + execute → PR → move to review.
- **`--claim-only`** — run through "Claim the issue" (pre-claim WIP gate, find
  candidates, pre-flight, judge, then self-assign and transition to an In-Progress
  status), then **stop**: no branch, no execution, no PR. The assigned, In-Progress
  issue is the reservation marker — do **not** transition it to In Review.
  `--claim-only` is the one execute-family action safe to batch, so `/do-tasks --all`
  / `-n N --claim-only` may reserve several issues at once, each bounded by the WIP
  gate.
- **`--no-claim <KEY>`** — skip the claim and resume an issue this caller has
  **already** claimed. **Requires an explicit issue key** — there is no default
  selection. Guard: proceed only when the issue's assignee is this caller (its
  `assignee.accountId` equals your `atlassianUserInfo` `account_id`) **and** its status
  is in the `indeterminate` (In Progress) category. Otherwise **stop and explain** —
  executing an unclaimed issue reopens the race the claim step closes. When the guard
  passes, **check out the existing `task/<KEY>` branch** rather than branching fresh
  from base — Jira publishes no branch, so the handler's deterministic `task/<KEY>` is
  the claim branch:

  ```bash
  git fetch origin && git switch "task/<KEY>"
  ```

  If that branch exists neither locally nor on the remote (the issue was reserved via
  `--claim-only`, which creates no branch), create it now —
  `git switch -c "task/<KEY>" "origin/<base>"` (`<base>` is `jira.base_branch` if set,
  else the repo's default branch — resolved as in "Branch + execute" below). Then run
  "Branch + execute" (skipping
  branch creation), "PR", and "Move to review" — without re-claiming. `--no-claim` is
  always single (`--all` / `-n N` do not apply).

## Pre-claim WIP gate

Mirrors the Linear pre-claim gate. It runs **before** judging feasibility or
claiming, on every claiming run — single mode included; only `--no-claim`, which
claims nothing, skips it.

1. Resolve `wip_limit` from the top-level `wip_limit` key in
   `dev_docs/tasks/.task-config.yml` (default `3` — the same key the repo-pr, linear,
   and gh-issue handlers use).
2. Count current in-flight work = issues in the configured project whose status sits in
   the In Progress / In Review category. A single JQL count covers both: Jira's
   `indeterminate` category (display name `In Progress`) spans every In-Progress _and_
   In-Review-type status, so `statusCategory = "In Progress"` catches the lot (the
   category name `In Progress`, its key `indeterminate`, and its id `4` are all accepted
   and equivalent — pick one):

   ```
   <atlassian-mcp>__searchJiraIssuesUsingJql
     cloudId: <jira.site>
     jql: project = "<project>" AND statusCategory = "In Progress" ORDER BY updated ASC
     fields: ["status"]
     maxResults: 100
   ```

   Count the returned issues — the length of `issues[]` (or `issues.nodes[]` on installs
   that nest it). The enhanced-search response carries **no** `total` field, so don't rely
   on one; the first page (`maxResults` 100) is far more than any `wip_limit`. The
   `indeterminate` category is the in-flight unit — an issue stays there
   from claim through PR review, so an open PR is already reflected by its issue's
   status; do **not** add open PRs separately, that double-counts. (This mirrors the
   linear gate, which counts both `In Progress` and `In Review`, and the gh-issue gate,
   which counts both `auto-claimed` and `needs-review`.)
3. If that count is **≥ `wip_limit`**, decline: report
   `WIP limit <wip_limit> reached (<count> in flight) — no issue claimed` and stop. Do
   not claim another issue.

For `--claim-only --all` / `-n N`, the gate bounds the batch instead of declining
outright: reserve at most `max(0, wip_limit - <count>)` issues (0 slack → reserve
nothing and report the WIP-limit decline).

## Find candidates

1. **Preflight.** Run the Atlassian MCP preflight exactly as `commands/handlers/jira.md` step 1: call `<atlassian-mcp>__getAccessibleAtlassianResources` (no args) and confirm a resource whose `url` matches `https://<jira.site>`. On either failure, **stop** with the same messages ("Jira handler needs the Atlassian MCP. Install/connect it in Claude Code settings, then re-run." / "Configured Jira site `<site>` is not in your accessible Atlassian resources."). Do not fall back to another handler. Also confirm `gh auth status`, a clean working tree, and fetch the base branch.

2. **Query.** Call `<atlassian-mcp>__searchJiraIssuesUsingJql` with:
   - `cloudId`: `<jira.site>`
   - `jql`: `project = "<project>" AND status = "<ready_status>" AND assignee IS EMPTY AND Flagged IS EMPTY AND (labels IS EMPTY OR labels NOT IN ("human-approval-requested", "blocked")) ORDER BY priority DESC, updated ASC`
   - `fields`: `["summary", "status", "priority", "labels", "description", "assignee"]`
   - `maxResults`: 50

   `assignee IS EMPTY` skips anything already claimed (the jira analogue of Linear's "pull only from `unstarted`" and gh-issue's `no:assignee`). `Flagged IS EMPTY` skips issues a human has flagged as an impediment — a native, site-level field, so the clause is safe on any board; it is the same blocked-issue guard `jira-promote.md` step 3 applies to its candidate query (the claim query is already pinned to one `status`, so the promoter's `blocked_statuses` exclusion isn't needed here). The `(labels IS EMPTY OR labels NOT IN ("human-approval-requested", "blocked"))` clause excludes issues already marked for human review or blocked — server-side; the `labels IS EMPTY` arm is required because JQL `NOT IN` does not match issues whose labels field is empty (an unlabeled issue would otherwise be dropped). The `ORDER BY` ranks by priority then age up front. Read the issues from whichever key the server returns (`issues[]` or `issues.nodes[]`; the create flow reads `issues.nodes[0]`). Limit 50 — if exactly 50 are returned the page may be truncated; note it in the report and do not paginate.

3. **Filter.** Drop any returned issue that carries a `human-approval-requested` or `blocked` label (defensive backstop against JQL index lag — the step-2 query already excludes these server-side). Jira has no native `estimate`/size field (story points, when present, are a custom field), so there is no estimate gate here — scope is judged later in "Judge feasibility", exactly as `jira-promote.md` folds size into the scope judgment.

Take the ranked candidates **one at a time**: for each candidate in ranked order, run **Pre-flight: is work already in flight?** and then, if it passes, **Judge feasibility** — on a pre-flight trip or a feasibility reject, advance to the next candidate and start it at pre-flight. Each entry needs `key`, `fields.summary`, `fields.description`, `fields.priority`, `fields.labels`, and the issue's `webUrl` (or build `https://<jira.site>/browse/<key>`). If no candidate remains, report that and stop.

If the `/do-tasks` argument was a specific issue key (e.g. `PLAT-142`), skip the query and call `<atlassian-mcp>__getJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`, the same `fields` plus `"Flagged"`) for that one issue. Apply the step-3 filter and the `assignee IS EMPTY` / `status = <ready_status>` / `Flagged IS EMPTY` gates to it; if it fails any gate, return the failure reason rather than the issue (a flagged issue reports as blocked). Do not auto-override the gates from a direct key — `/do-tasks` surfaces the reason and stops.

## Pre-flight: is work already in flight?

Runs on the candidate **before "Judge feasibility" and "Claim the issue"**, on every claiming path (single, direct `<KEY>`, and `--claim-only`). The same cheap, high-value guard as `linear-claim.md`'s pre-flight, keyed on the handler's deterministic `task/<KEY>` branch (Jira publishes no branch of its own): catch a sibling session that is already building this issue before spending the full issue-description read and feasibility judgment. If **any** check trips, **do not judge, do not claim, and do not build** — skip and report.

1. **Open PR by key.** The execute path titles PRs `[<KEY>] <summary>`, so an open PR carrying the key is an in-flight build:

   ```bash
   gh pr list --state open --search "[<KEY>] in:title" --json number,url,title
   ```

   GitHub search tokenizes on punctuation, so `[PROJ-45]` searches the tokens `PROJ` and `45` and can over-match. Before skipping, confirm a returned PR's `title` actually contains the literal `[<KEY>]` token — only then **skip** with `Skipped <KEY>: open PR already exists (<url>)`. (The `task/<KEY>` branch check in step 2 is exact and needs no post-filter.)

2. **Remote branch (no PR yet).** A pushed `task/<KEY>` with no PR signals another session mid-build:

   ```bash
   git ls-remote --heads origin "task/<KEY>"
   ```

   A non-empty result → `Skipped <KEY>: remote branch task/<KEY> already exists`.

In single/direct mode an in-flight result **stops**; in ranked mode it moves to the next candidate and re-runs pre-flight on it.

## Judge feasibility

Runs on the candidate that just passed pre-flight — one candidate at a time, in ranked order; stop at the first feasible one. Read the full issue description and decide whether this session can finish it without a human (a concrete outcome, identifiable files, a PR landable in ~1 hour, no product/design call or inaccessible infra needed).

If feasible: continue with this candidate (proceed to "Claim the issue"). If not: leave a one-line skip comment and move to the next candidate, starting it at pre-flight:

```
<atlassian-mcp>__addCommentToJiraIssue
  cloudId: <jira.site>
  issueIdOrKey: <KEY>
  commentBody: "Skipped by /do-tasks: <reason>"
```

**Do not claim it.** If every candidate is rejected, summarize the reasons and stop — do not lower the bar. Print the chosen issue's key, summary, and a one-sentence rationale, then proceed.

Keeping the order as pre-flight → judge → claim is acceptable here because the claim is a cheap read-then-write guard executed immediately after the judge, unlike Linear's slow token-comment election that forces the judge inside the claim.

## Claim the issue

Jira has no transactional claim, so use a **claim-then-verify** guard (read-then-write, then re-read — the analogue of `linear-claim.md`'s concurrency guard and `gh-issue-claim.md`'s assignee guard): self-assign and move the issue to an In-Progress status, then re-read to confirm no one raced in.

1. **Resolve your account id.** Call `<atlassian-mcp>__atlassianUserInfo` (no args) and capture the current user's `account_id`. (`@me` has no JQL/edit equivalent in Jira — assignment is by account id.)

2. **Re-read** the chosen issue — `<atlassian-mcp>__getJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`, `fields: ["assignee", "status"]`). If it now has an assignee, or its status is no longer `ready_status`, **another session beat you** — return `race`, fall back to the next candidate.

3. **Assign yourself.** Call `<atlassian-mcp>__editJiraIssue` with:
   - `cloudId`: `<jira.site>`
   - `issueIdOrKey`: `<KEY>`
   - `fields`: `{ "assignee": { "accountId": "<account_id>" } }`

4. **Transition to In Progress.** `transitionJiraIssue` takes a transition **id**, not a status name, so resolve it per issue:

   ```
   <atlassian-mcp>__getTransitionsForJiraIssue
     cloudId: <jira.site>
     issueIdOrKey: <KEY>
   ```

   From the returned `transitions[]`, consider only entries whose target status is in the `indeterminate` (In Progress) category (`to.statusCategory.key == "indeterminate"`), then resolve the start-work status: if exactly one such transition exists, use it; if several exist, prefer one whose `to.name` is `In Progress` (case-insensitive), and if none is named `In Progress`, drop any whose `to.name` signals a non-start in-flight state (matches `hold`, `block`, `review`, `validation`, or `wait`) and use the single remaining candidate. Real workflows often name their start state `In Execution`, `Doing`, etc. — not literally `In Progress` — so don't assume the name. Capture its `id` and transition:

   ```
   <atlassian-mcp>__transitionJiraIssue
     cloudId: <jira.site>
     issueIdOrKey: <KEY>
     transition: { id: "<transition-id>" }
   ```

   If this leaves **no** candidate, or **more than one** after the filter, **do not guess** — surface the available transition names so the user can disambiguate (or fix the workflow / set a claim-target status in config), unassign yourself, and stop. Guessing among several `indeterminate` transitions risks parking a fresh claim in `On Hold/Blocked` or a review status — validated against a real workflow whose In-Progress category spans `In Execution`, `Validation`, and `On Hold/Blocked` with none named `In Progress`.

5. **Confirm.** Re-read the issue's `assignee` (`getJiraIssue`, `fields: ["assignee"]`). The claim holds **iff** `assignee.accountId` equals your `account_id` from step 1. If a different `accountId` appears, a concurrent claimer raced in **and won** — leave the issue untouched (do **not** clear the assignee or revert the status; both now belong to the winner, and stomping them would disrupt their active claim), return `race`, and fall back to the next candidate.

6. **Comment** the branch name via `addCommentToJiraIssue` (`commentBody: "Claimed by /do-tasks. Working on branch \`task/<KEY>\`; PR link will follow."`).

Return the issue key and url so `/do-tasks` can branch and execute.

## Branch + execute

1. **Branch** — `task/<KEY>` (Jira has no native branch primitive, so the handler publishes this deterministic name). Branch from `<base>` — `jira.base_branch` if set, otherwise the repo's default branch (resolve it, e.g. `git symbolic-ref --short refs/remotes/origin/HEAD` → strip the `origin/` prefix; do **not** assume `main`):

   ```bash
   git fetch origin && git switch -c "task/<KEY>" "origin/<base>"
   ```

2. **Execute** — do the work, then run the project's quality gate (`just check` here). Keep the diff scoped to this one issue.

## PR

```bash
gh pr create --title "[<KEY>] <summary>" --body "<KEY>: <jira issue URL>

<summary of the change>"
```

The `[<KEY>]` title prefix and the `<KEY>` / issue URL in the body are the links Jira's GitHub integration (and smart commits) match on to associate and close the issue on merge — the jira analogue of `Closes #<n>` (gh-issue) and `Closes <identifier>` (Linear). Then post the PR URL back to the issue:

```
<atlassian-mcp>__addCommentToJiraIssue
  cloudId: <jira.site>
  issueIdOrKey: <KEY>
  commentBody: "PR opened: <PR URL>"
```

## Move to review on PR open

Called from `/do-tasks` immediately after `gh pr create` succeeds. Transition the issue to an In-Review status if the workflow has one; otherwise leave it In Progress.

1. **Resolve the transition.** Call `getTransitionsForJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`). Look for a transition whose target status is still in the `indeterminate` category and whose `to.name` (case-insensitive) is `In Review` (or `Review`). If found, capture its id; if none exists, **skip the transition** — the issue stays In Progress, and the open PR plus the PR-URL comment are the review signal. Never pick a `done`-category transition here, regardless of how done the work feels.

2. **Transition** (only if step 1 found one):

   ```
   <atlassian-mcp>__transitionJiraIssue
     cloudId: <jira.site>
     issueIdOrKey: <KEY>
     transition: { id: "<transition-id>" }
   ```

The PR-URL comment posted right after `gh pr create` is the user-facing signal that review has started; the status transition is the structural one.

## Bail (when execution proves infeasible mid-flight)

```bash
git stash push -u
```

Then, over the Atlassian MCP:

1. **Unassign** — `editJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`, `fields: { "assignee": null }`).
2. **Transition back** to `ready_status` (resolve its transition id via `getTransitionsForJiraIssue`, matching `to.name == <ready_status>`) so the issue returns to the lane it came from. If a `human-approval-requested` label is configured on the board, re-read the issue's current labels (`getJiraIssue`, `fields: ["labels"]`), append `human-approval-requested` to that list, and write the combined list back via `editJiraIssue` (`fields: { "labels": [<existing…>, "human-approval-requested"] }`) so a human knows to look — `editJiraIssue` **replaces** the whole label set, so omitting the existing labels would drop them. Otherwise the comment below carries that signal.
3. **Comment** the bail reason via `addCommentToJiraIssue` (`commentBody: "Bailed by /do-tasks: <what was tried, what tripped the bail>"`).

Stop — do not auto-pick another candidate after a bail; a human should look before more work is auto-claimed.

## Report

`/do-tasks` prints the outcome:

- **On success:** the issue key, the PR URL, and a one-line summary of what changed.
- **On bail:** the issue key, why it bailed, and a note that the bail comment was posted.
- **On no feasible candidate:** say so (with the skip reasons recorded on each rejected issue).
