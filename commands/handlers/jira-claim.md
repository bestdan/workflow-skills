---
description: Handler procedure read by its dispatching command; not run directly.
disable-model-invocation: true
user-invocable: false
---

# jira handler — /do-tasks execute flow

Invoked from `/do-tasks` (section 5, "jira path") when `handler: jira` is configured. This file holds the full jira claim/execute flow, run in the current session over the Atlassian MCP: **find candidates** (read-only), **pre-flight in-flight check** (read-only), **judge feasibility** (read-only), **claim the issue** (mutating, before work starts), **branch + execute**, **PR**, and **move to review on PR open** (mutating, after the PR is opened). A separate **bail** phase runs when work proves infeasible mid-execution. It mirrors the tracker flow in `commands/handlers/linear-claim.md`, over the Atlassian MCP instead of the Linear MCP — the same way `gh-issue-claim.md` mirrors it over the `gh` CLI.

**Shared reference:** the Atlassian MCP preflight is `commands/handlers/jira.md` step 1; the `ready_status` config key (the jira analogue of the Linear `Todo`/`unstarted` ready lane) is defined in `commands/handlers/jira-config.md`; `commands/handlers/linear-claim.md` is the structural template. The claim is decided by the Jira **assignee** field, not by a GitHub ref: the state of a Jira claim lives in Jira, so a `task/<KEY>` branch that outlived its work cannot hold the ticket. `commands/handlers/claim-lock.md` is the gh-issue handler's ref lock and is **not** used here — "Claim the issue" names the race that costs, and why it is accepted.

> **MCP namespace.** `<atlassian-mcp>__` is `mcp__claude_ai_Atlassian__` or `mcp__atlassian__` depending on the install (see `jira-config.md`) — substitute the prefix loaded in your session. Tool names after the prefix (`getAccessibleAtlassianResources`, `searchJiraIssuesUsingJql`, `getJiraIssue`, `editJiraIssue`, `atlassianUserInfo`, `getTransitionsForJiraIssue`, `transitionJiraIssue`, `addCommentToJiraIssue`) are identical across installs.

> **Scope.** This flow now carries the same `--claim-only`/`--no-claim` split and pre-claim WIP gate that `linear-claim.md` and `gh-issue-claim.md` do — see "Modes: atomic vs. claim/execute split" and "Pre-claim WIP gate" below. `/do-tasks` claims and executes atomically by default; the two flags split that into composable steps.

> **Hard rule for every phase below: never transition a jira issue to a `Done`/`completed`-category status, and never close it manually.** Merge is the only completion signal — Jira's GitHub integration (or a smart commit on merge) closes the issue automatically when the PR, whose title carries `[<KEY>]` and whose body names `<KEY>`, merges. If you are about to `transitionJiraIssue` to a `Done`-category status from this file, you have a bug — stop.

## Config

Read `dev_docs/tasks/.task-config.yml`. The jira claim flow reads:

- `jira.site` — cloudId / site URL (as the create and promote flows use).
- `jira.project` — project key.
- `jira.ready_status` — **required here.** The status the ready lane lives in (the jira analogue of Linear's `Todo`). `/do-tasks` pulls candidates from this status. If it is unset/empty, **stop** with: "jira `/do-tasks` needs `jira.ready_status` set in dev_docs/tasks/.task-config.yml (the status promoted issues land in). Set it, or run `/task-config jira`." Do not guess a status name.
- `jira.base_branch` — optional; the branch `/do-tasks` branches from (default: the repo's default branch).
- `jira.blocked_statuses` — optional; status names that mean "blocked" on this board. The pre-claim WIP gate excludes them from its in-flight count, so a parked issue doesn't consume a slot. Defined in `commands/handlers/jira-config.md` → "Blocked statuses".

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
  executing an unclaimed issue builds work the board says nobody holds. When the guard
  passes, run pre-flight step 1 (open PR by key) — an open PR means the work is already
  published, so stop and report it rather than re-executing. Then run "Branch + execute"
  in its **resume** form, which reuses an existing `task/<KEY>` as this caller's own
  in-progress work and creates it only when there is none (the claim creates no branch,
  so a `--claim-only` reservation has none yet), then "PR" and "Move to review" —
  without re-claiming. `--no-claim` is always single (`--all` / `-n N` do not apply).

## Pre-claim WIP gate

Mirrors the Linear pre-claim gate. It runs **before** judging feasibility or
claiming, on every claiming run — single mode included; only `--no-claim`, which
claims nothing, skips it.

> **When the count is at the limit, who decides.** The count below always runs. What
> happens when it meets the limit depends on the action: a **batch** declines or is
> bounded as specified here, always; a **single** claim in an attended session offers
> the user a one-keystroke override first. See `commands/handlers/attendedness.md` —
> that file owns the rule, including why the override is a prompt rather than a
> printed question. Do not restate it here.

1. Resolve `wip_limit` from the top-level `wip_limit` key in
   `dev_docs/tasks/.task-config.yml` (default `3` — the same key the repo-pr, linear,
   and gh-issue handlers use).
2. Count current in-flight work = issues **assigned to this caller** whose status sits in
   the In Progress / In Review category. A single JQL count covers both: Jira's
   `indeterminate` category (display name `In Progress`) spans every In-Progress _and_
   In-Review-type status, so `statusCategory = "In Progress"` catches the lot (the
   category name `In Progress`, its key `indeterminate`, and its id `4` are all accepted
   and equivalent — pick one):

   ```
   <atlassian-mcp>__searchJiraIssuesUsingJql
     cloudId: <jira.site>
     jql: project = "<project>" AND assignee = currentUser() AND statusCategory = "In Progress" AND Flagged IS EMPTY [AND status NOT IN ("<blocked-status>", …)] ORDER BY updated ASC
     fields: ["status"]
     maxResults: 100
   ```

   **`assignee = currentUser()` is required.** The gate bounds **this operator's**
   concurrent work, not the project's. A shared team board carries other people's
   in-progress issues by the dozen — an unscoped count on such a board exceeds any
   plausible `wip_limit` permanently, so the gate never opens and `/do-tasks` can never
   claim. Scoping to the caller is what makes the limit mean something a caller can act on.

   **Parked work is not in flight.** `Flagged IS EMPTY` drops issues carrying Jira's
   native Impediment flag; the `status NOT IN (…)` clause is added **only when**
   `jira.blocked_statuses` is non-empty and drops the board's blocked-type statuses. Both
   clauses are the same ones `jira-promote.md` step 3 applies, for the same reason — spell
   them identically. A blocked status can sit in the `indeterminate` category, where it is
   indistinguishable from real work to the category filter, and the same status name sits
   in a different category on another board, so it cannot be hard-coded (see
   `jira-config.md` → "Blocked statuses"). An issue parked at `On Hold/Blocked` consumes no
   review capacity and must not consume a WIP slot.

   Count the returned issues — the length of `issues[]` (or `issues.nodes[]` on installs
   that nest it). The enhanced-search response carries **no** `total` field, so don't rely
   on one; the first page (`maxResults` 100) is far more than any `wip_limit`. The
   `indeterminate` category is the in-flight unit — an issue stays there
   from claim through PR review, so an open PR is already reflected by its issue's
   status; do **not** add open PRs separately, that double-counts. (This mirrors the
   linear gate, which counts both `In Progress` and `In Review`, and the gh-issue gate,
   which counts both `auto-claimed` and `needs-review`.)
3. If that count is **≥ `wip_limit`**, apply the at-limit procedure in
   `commands/handlers/attendedness.md`. **When it resolves to decline** — a batch, a hard
   negative, or the user chose Stop — report
   `WIP limit <wip_limit> reached (<count> in flight) — no issue claimed` and stop. Do
   not claim another issue. When it resolves to override, proceed with the claim and say
   so in the run report. This step owns the **message**; that file owns the **decision**.

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

If the `/do-tasks` argument was a specific issue key (e.g. `PLAT-142`), skip the query and call `<atlassian-mcp>__getJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`, the same `fields` plus `"Flagged"`) for that one issue. Apply the step-3 filter and the assignee (below) / `status = <ready_status>` / `Flagged IS EMPTY` gates to it; if it fails any gate, return the failure reason rather than the issue (a flagged issue reports as blocked) — with one exception. A status outside `<ready_status>`, or a `human-approval-requested` label present, is the hold a human placed on the issue, and the person naming it is who the hold is for: apply the held-issue override in `commands/handlers/attendedness.md` (that file owns the prompt and the hard negatives; on decline, surface the reason and stop as before). On **Claim anyway** proceed with the issue and keep `human-approval-requested` on it through the claim — the override claims the issue, it does not release it to the loop. `Flagged IS EMPTY` and `blocked` still refuse a direct key — `/do-tasks` surfaces the reason and stops.

The **assignee** gate on a direct key admits two values, not one: empty, or **this caller** (compare `fields.assignee.accountId` with the `account_id` from `atlassianUserInfo`, which "Claim the issue" step 1 resolves — resolve it here instead when the key is direct). Your own account on a ready issue is an earlier claim of yours that stopped between the self-assign and the transition; naming the key resumes it. Your own account on an issue already in the `indeterminate` (In Progress) category is a finished claim: report `<KEY> is already claimed by you — resume it with /do-tasks <KEY> --no-claim` and stop, rather than routing it through the held-status override. Any **other** assignee refuses the key — someone else holds it. The ranked query keeps `assignee IS EMPTY`: it selects new work, and resuming is what a named key is for.

## Pre-flight: is work already in flight?

Runs on the candidate **before "Judge feasibility" and "Claim the issue"**, on every claiming path (single, direct `<KEY>`, and `--claim-only`). The same cheap, high-value guard as `linear-claim.md`'s pre-flight: catch an issue whose work is already published, or that cannot be branched cleanly, before spending the full issue-description read and feasibility judgment. If **any** check trips, **do not judge, do not claim, and do not build** — skip and report.

1. **Open PR by key.** The execute path titles PRs `<type>(scope): <description> [<KEY>]`, so an open PR carrying the key is an in-flight build:

   ```bash
   gh pr list --state open --search "[<KEY>] in:title" --json number,url,title
   ```

   GitHub search tokenizes on punctuation, so `[PROJ-45]` searches the tokens `PROJ` and `45` and can over-match. Before skipping, confirm a returned PR's `title` actually contains the literal `[<KEY>]` token — only then **skip** with `Skipped <KEY>: open PR already exists (<url>)`. (The `task/<KEY>` branch check in step 2 is exact and needs no post-filter.)

2. **Stale work branch.** The work branch is the handler's deterministic `task/<KEY>` (Jira publishes no branch of its own), and "Branch + execute" creates it fresh from base. A branch of that name that already exists — locally or on origin — with no open PR (step 1 caught that case) is **not** a claim: the claim lives in the assignee field, and this issue was admitted as unassigned (or, on a direct key, as this caller's own). It is a leftover from an earlier attempt, typically one whose PR was closed unmerged, and it would make the branch step fail. Find it now rather than after claiming:

   ```bash
   git rev-parse --verify --quiet "refs/heads/task/<KEY>"
   git ls-remote --heads origin "task/<KEY>"
   gh pr list --head "task/<KEY>" --state all --json number,state,url
   ```

   If either of the first two finds the branch, report it with the fix rather than as an in-flight build — `Skipped <KEY>: stale branch task/<KEY> (<local|origin|both>; <its closed/merged PR url, or "no PR">)`, followed by the fix that fits:
   - **Its PR is closed or merged** — the branch is dead. Delete it: `git push origin --delete "task/<KEY>"` and/or `git branch -D "task/<KEY>"`.
   - **It has no PR** — it may carry unpublished work. Inspect each copy the report names (`<base>` is resolved as in "Branch + execute" step 1: `jira.base_branch`, else the repo's default branch), then either delete it as above or keep it under another name:
     - **On origin** — fetch it first, since pre-flight only listed it (`git fetch origin "task/<KEY>"`), inspect with `git log "origin/<base>..origin/task/<KEY>"`, and keep it with `git push origin "origin/task/<KEY>:refs/heads/task/<KEY>-old"` followed by `git push origin --delete "task/<KEY>"`.
     - **Local** — inspect with `git log "origin/<base>..task/<KEY>"`, and keep it with `git branch -m "task/<KEY>" "task/<KEY>-old"`.

   Do not delete or rename it yourself: the branch belongs to whoever pushed it, and the report is enough to unblock the next run.

In single/direct mode a tripped check **stops**; in ranked mode it moves to the next candidate and re-runs pre-flight on it.

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

Keeping the order as pre-flight → judge → claim is acceptable here because the claim below is a few fast Jira writes: two sessions may both judge the same issue, but the assignee field holds one account, and a session that finds **another** account there — at the claim's re-read, at its confirm, or at the re-read before `gh pr create` — stops instead of building or publishing a duplicate. Two sessions on the **same** account are not told apart; "Claim the issue" says why that is accepted. (Linear's slow token-comment election is what forces its judge _inside_ the claim.)

## Claim the issue

The claim is decided by the issue's **assignee**: unassigned, or (on a named key) already assigned to this caller, means proceed; any other account means someone else holds it. The self-assign and the In-Progress transition below are both the claim and its human-visible marker — there is no separate lock, and no GitHub write happens at claim time.

**Accepted tradeoff: two sessions on the same account can both claim.** Jira exposes no compare-and-swap on issue fields, so two sessions authenticated as the **same** account can both read an empty assignee, both write their identical account id, both read it back, and both build the issue. That is why the gh-issue handler locks on ref creation instead (`commands/handlers/claim-lock.md` → "Why the assignee cannot be the lock"). Here the race needs two same-account sessions started by hand on the same ticket within the same few seconds: `/do-tasks` runs jira execution in the foreground, one ticket per session (§5), and no orchestrator dispatches jira tickets at all — `/auto-pilot` takes only linear and plan sources. Nothing stops two terminals, but nothing automated produces them. That is rare enough to accept in exchange for a claim whose whole state lives in Jira — a leftover `task/<KEY>` branch cannot hold a ticket, and a claim needs no GitHub API write, which some environments block. The pre-PR re-read in "PR" catches a **different** account that took the issue mid-build; it cannot catch a same-account twin.

1. **Resolve your account id.** Call `<atlassian-mcp>__atlassianUserInfo` (no args) and capture the current user's `account_id`. (`@me` has no JQL/edit equivalent in Jira — assignment is by account id.)

2. **Re-read** the chosen issue — `<atlassian-mcp>__getJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`, `fields: ["assignee", "status"]`) — and decide:
   - **Assignee empty** → proceed.
   - **Assignee is your own `account_id`, on a direct key** → proceed. This is this caller resuming its own claim — an earlier run that stopped after the self-assign — not a competitor. (It is also exactly what a same-account twin looks like, which is the accepted tradeoff above.)
   - **Assignee is your own `account_id`, on the ranked path** → **another session on your account claimed it** since "Find candidates" read it empty (the ranked query selects only `assignee IS EMPTY`, and this session has written nothing yet): return `race` and fall back to the next candidate. Leave the issue untouched.
   - **Any other assignee** → **another claimant beat you**: return `race` and fall back to the next candidate (on a direct key: stop and report `<KEY> is assigned to <other> — not claimed`). Leave the issue untouched.

   Independently, its status must still be the one you admitted in "Find candidates" (`ready_status` on the ranked path; the held status the direct-key override admitted). A different status means someone moved it since — return `race` as above. An overridden issue keeps its held status here; step 5 resolves the start transition from that status and stops with its existing message if none exists.

3. **Assign yourself** — skip when step 2 found your own account already there on a direct key. Call `<atlassian-mcp>__editJiraIssue` with:
   - `cloudId`: `<jira.site>`
   - `issueIdOrKey`: `<KEY>`
   - `fields`: `{ "assignee": { "accountId": "<account_id>" } }`

4. **Confirm the assignee landed.** Re-read the issue's `assignee` (`getJiraIssue`, `fields: ["assignee"]`). If it is your `account_id`, the claim is yours — continue. If a **different** `accountId` appears, another claimant's write landed after yours, and the field now names them: leave the assignee and status alone (they are the winner's, or a human's deliberate reassignment), report `<KEY>: assignee is now <other> — claim not taken, no work started`, and fall back to the next candidate (on a direct key: stop). If it reads back **empty**, someone cleared it after your write: leave the issue alone, report `<KEY>: assignee was cleared after the self-assign — claim not taken, no work started`, and fall back the same way. This runs **before** the transition so a losing session never moves the issue's status. A competing write that lands after this read is caught by the re-read in "PR", before anything is published.

5. **Transition to In Progress.** `transitionJiraIssue` takes a transition **id**, not a status name. Fetch the candidate's transitions, write the response to a file, and let the helper resolve the start-work one:

   ```
   <atlassian-mcp>__getTransitionsForJiraIssue
     cloudId: <jira.site>
     issueIdOrKey: <KEY>
   ```

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-resolve-transition.py" \
     --category indeterminate --exclude 'hold|block|review|validation|wait' --prefer 'in progress' \
     < <transitions-response.json>
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/jira-resolve-transition.py`. Real workflows often name their start state `In Execution`, `Doing`, etc. — not literally `In Progress` — which is why the helper prefers that name rather than assuming it. On success it prints `<id>\t<to.name>` — capture the id and transition:

   ```
   <atlassian-mcp>__transitionJiraIssue
     cloudId: <jira.site>
     issueIdOrKey: <KEY>
     transition: { id: "<transition-id>" }
   ```

   On `AMBIGUOUS`/`NONE` (exit 2), **do not guess** — surface the printed candidate names so the user can disambiguate (or fix the workflow / set a claim-target status in config), unassign yourself (`editJiraIssue`, `fields: { "assignee": null }`) so the issue does not sit claimed in its ready status, and stop. Guessing among several `indeterminate` transitions risks parking a fresh claim in `On Hold/Blocked` or a review status — validated against a real workflow whose In-Progress category spans `In Execution`, `Validation`, and `On Hold/Blocked` with none named `In Progress`.

6. **Comment** the branch name — `addCommentToJiraIssue` with `commentBody: "Claimed by /do-tasks. Working on branch \`task/<KEY>\`; PR link will follow."` The branch does not exist yet; "Branch + execute" creates it under that deterministic name.

Return the issue key and url so `/do-tasks` can execute.

## Branch + execute

1. **Branch.** Create the work branch now, when the work starts — the claim created none. `<base>` is `jira.base_branch` if set, otherwise the repo's default branch (resolve it, e.g. `git symbolic-ref --short refs/remotes/origin/HEAD` → strip the `origin/` prefix; do **not** assume `main`). First look for an existing branch of that name, locally and on origin:

   ```bash
   git fetch origin
   git rev-parse --verify --quiet "refs/heads/task/<KEY>"
   git ls-remote --heads origin "task/<KEY>"
   ```

   - **Neither exists** (the normal case) → create it from base:

     ```bash
     git switch -c "task/<KEY>" "origin/<base>"
     ```

   - **Either exists, on a fresh claim** (default mode) → **stop**. Pre-flight step 2 checked for this, so the branch appeared since, or pre-flight ran in another checkout. Report it as a stale branch, **not** a lost claim — the Jira claim is yours and stays in place: `<KEY> claimed, but branch task/<KEY> already exists (<local|origin|both>)`, the fix from pre-flight step 2 (delete it if its PR is closed or merged; otherwise inspect it, then delete it or rename it to `task/<KEY>-old`), and `then resume with /do-tasks <KEY> --no-claim`. Do not delete or rename the branch yourself.

   - **Either exists, on the `--no-claim` resume path** → the guard proved this caller holds the claim, and the claim creates no branch, so an existing `task/<KEY>` is normally this caller's own in-progress work. Reuse it — unless it heads a closed or merged PR (`gh pr list --head "task/<KEY>" --state all --json number,state,url`), which marks it as a leftover from an earlier attempt rather than this claim's work: then stop and report it with pre-flight step 2's fix. To reuse, `git switch "task/<KEY>"` — with only a remote copy, that creates a local branch tracking `origin/task/<KEY>`.

2. **Execute** — do the work, then run the project's quality gate (`just check` here). Keep the diff scoped to this one issue.

## PR

**Re-read the claim first.** Immediately before `gh pr create`, re-read the issue (`getJiraIssue`, `fields: ["assignee", "status"]`). If the assignee is now a **different** account, or the status has left the `indeterminate` (In Progress) category, someone took or moved the issue during the build: do **not** open the PR, leave the issue as you found it, and stop with `<KEY>: assignee/status changed during the build (<what you read>) — work is on task/<KEY>, no PR opened`. The work stays on the branch for a human to place. This catches a different claimant or a human reassignment; it cannot catch a twin session on your own account, which reads back your own id (see "Claim the issue").

```bash
gh pr create --title "<type>(scope): <description> [<KEY>]" --body "<KEY>: <jira issue URL>

<summary of the change>"
```

The title grammar is the `agent-guidance` plugin's `portable.md` (`Git:` bullet), not this file.

The `[<KEY>]` in the title and the `<KEY>` / issue URL in the body are the links Jira's GitHub integration (and smart commits) match on to associate and close the issue on merge — the jira analogue of `Closes #<n>` (gh-issue) and `Closes <identifier>` (Linear). Atlassian documents the requirement as _including_ the key in the PR title, and names capitalization as the only constraint on its form, with no positional one ([Reference work items in your development spaces](https://support.atlassian.com/jira-software-cloud/docs/reference-issues-in-your-development-work/), read 2026-09-13); the page's own examples happen to lead with the key. The end position is therefore documented-compatible rather than separately exercised: if a merge stops closing its issue, check position first. Then post the PR URL back to the issue:

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
git switch -
```

There is no lock to release — the claim is the assignee, which step 1 below clears. If
this session pushed `task/<KEY>` to origin during the work and no PR was opened from it,
delete that remote branch (`git push origin --delete "task/<KEY>"`) so the next claim's
pre-flight does not have to report it as stale. The local branch blocks the next claim
from this checkout the same way, because pre-flight step 2 checks `refs/heads/task/<KEY>`
too — so move it aside rather than leave it: `git branch -m "task/<KEY>" "task/<KEY>-bailed-<timestamp>"`,
and name the new branch in the bail comment so a human can find the work. **Rename it,
never `git branch -D` it**: the stash holds only uncommitted changes, so a delete loses
any committed work, and once the remote copy is gone the reflog is the only one left.
Touch either branch **only** if this session created it; a branch that existed before
this run is someone else's to clear. Then, over the Atlassian MCP — first re-reading the
issue (`getJiraIssue`, `fields: ["assignee", "status"]`). If the assignee is no longer your
`account_id`, or the status has left the `indeterminate` (In Progress) category, someone
took or moved the issue during the build: skip steps 1 and 2, which would clobber their
claim, and say so in the step-3 comment.

1. **Unassign** — `editJiraIssue` (`cloudId`, `issueIdOrKey: <KEY>`, `fields: { "assignee": null }`).
2. **Transition back** to `ready_status` (resolve its transition id via `getTransitionsForJiraIssue`, matching `to.name == <ready_status>`) so the issue returns to the lane it came from. If a `human-approval-requested` label is configured on the board, re-read the issue's current labels (`getJiraIssue`, `fields: ["labels"]`), append `human-approval-requested` to that list, and write the combined list back via `editJiraIssue` (`fields: { "labels": [<existing…>, "human-approval-requested"] }`) so a human knows to look — `editJiraIssue` **replaces** the whole label set, so omitting the existing labels would drop them. Otherwise the comment below carries that signal.
3. **Comment** the bail reason via `addCommentToJiraIssue` (`commentBody: "Bailed by /do-tasks: <what was tried, what tripped the bail>"`).

Stop — do not auto-pick another candidate after a bail; a human should look before more work is auto-claimed.

## Report

`/do-tasks` prints the outcome:

- **On success:** the issue key, the PR URL, and a one-line summary of what changed.
- **On bail:** the issue key, why it bailed, and a note that the bail comment was posted.
- **On no feasible candidate:** say so (with the skip reasons recorded on each rejected issue).
