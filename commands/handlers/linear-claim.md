# Linear handler — /do-tasks tracker flow

Invoked from `/do-tasks` (section 3, "Tracker path") when `handler: linear` is configured. This file holds the full tracker execute flow, run in the current session: **find candidates** (read-only), **judge feasibility** (read-only), **claim the issue** (mutating, before work starts), **move to review on PR open** (mutating, after the PR is opened), and a **report** at the end. A separate **bail** phase runs when work proves infeasible mid-execution. `/do-tasks` orchestrates the branch-and-execute and `gh pr create` between the claim and move-to-review phases.

**Shared reference:** see `linear-common.md` for connection details, full config schema (including the `max_estimate` and `base_branch` keys used here), preflight pattern, and the kanban mapping table this file reads against.

> **Hard rule for every phase below: the tracker path never moves a Linear issue to a `completed`- or `canceled`-type workflow state.** Merge is the only completion signal, and Linear's GitHub integration handles that automatically when the PR (with `Closes <identifier>` in its body) merges. If you are about to call `save_issue` with a `completed`-type `state` from this file, you have a bug — stop.

## PR body magic words

Linear's GitHub integration scans the **whole** PR title and body for issue ids, not just the `Closes` line. On merge it moves every id referenced with a **closing** magic word to the team's `completed` state.

- **Closing** (auto-completes on merge): `close`, `closes`, `closed`, `closing`, `fix`, `fixes`, `fixed`, `fixing`, `resolve`, `resolves`, `resolved`, `resolving`, `complete`, `completes`, `completed`, `completing`, `implement`, `implements`, `implemented`, `implementing`.
- **Non-closing / contributing** (links only, **no** status change on merge): `ref`, `refs`, `references`, `part of`, `related to`, `contributes to`, `toward`, `towards`.

**Rule when composing the PR body:** close only the issues this PR actually finishes, and mark each one **explicitly** — `Closes <identifier>` on **its own line**, one per line. A PR may legitimately close more than one issue (`Closes <TEAM>-12` / `Closes <TEAM>-13` on separate lines); that is fine as long as each closing line is clear and each named issue was truly completed. The danger is never an explicit `Closes`; it is an id that gets **inferred** as a closing link. So: every Linear id that appears anywhere in the **title or body** must carry an explicit magic word — a closing one (`Closes …`) for an issue this PR completes, or a non-closing one (`related to …`, `part of …`) for a blocker / sibling / follow-up it merely references (these may also repeat, one clearly-marked reference per line). A bare `<TEAM>-NNN` token is the bug: in practice Linear treats it as a closing link and auto-completes that sibling on merge, even though the PR did none of its work. **Do not** rely on a bare Linear URL as the escape hatch either — the URL embeds the raw id (`…/issue/<TEAM>-NNN/…`), so it carries the same auto-close risk; only a non-closing magic word is verified to prevent it. This is exactly how an unrelated issue gets silently closed — guard against it every time an id appears that this PR did not finish.

## Find candidates

1. **Preflight.** Run the shared preflight from `linear-common.md` (call `list_teams`, match `<linear.team>`, capture team `id`). Same failure messages.

2. **Resolve workflow states.** Call `<linear-mcp>__list_workflow_states` with `teamId`. Cache the state-id → type map. Identify the state ids for type `unstarted` (= `ready` in the kanban mapping) — these are the only states the tracker path pulls from. Cards in `started` are by definition already claimed; cards in `backlog` are unrefined and must go through `/promote-tasks` first.

3. **Project filter.** If `linear.default_project` is set (non-empty), pass it as `projectId`. Otherwise omit — search across all of the team's active issues.

4. **Query.** Call `<linear-mcp>__list_issues` with:
   - `teamId`: resolved team id
   - `projectId`: from step 3 (omit if not set)
   - `stateId`: each `unstarted`-type state id from step 2 (loop or pass as list per the tool's accepted shape)
   - `includeArchived`: `false`
   - Limit: 50. If more exist, note the truncation; do not paginate (claiming one card per call doesn't need exhaustive enumeration).

5. **Filter.** From the returned issues, drop any that fail any of these gates. Each gate has a fixed reason string so the caller can report consistently. (Linear's native `estimate` uses the same Fibonacci scale as our task `size` — see "Task size" in skills/task/SKILL.md — so these thresholds select tasks small enough to finish in one session.)
   - `estimate` is `null`/missing → `no estimate set`
   - `estimate >= <linear.max_estimate>` (default `3`) → `estimate <N> >= <max>`
   - Has label `auto-claimed` → `already auto-claimed`
   - Has label `human-approval-requested` → `human-approval-requested`
   - Has label `blocked` → `blocked`
   - `assignee` is set and is **not** the current Linear user (`<linear-mcp>__get_user` with no args returns the viewer) → `assigned to <name>`

6. **Rank.** Sort remaining issues by Linear `priority` (urgent=1 → low=4, then none=0 last), then by `updatedAt` ascending (oldest first — let aging cards bubble up).

7. **Return** the ranked list to the **Judge feasibility** phase below. Each entry needs: `id`, `identifier`, `title`, `priority`, `estimate`, `description`, `labels`, `url`, **`branchName`** (Linear's published git branch name — used verbatim when `/do-tasks` branches), and the resolved `id`s for: team, current `state`, target `started`-type `state` (see "Claim the issue" below). The Judge feasibility phase will read the description and decide feasibility.

   If the MCP response does not include `branchName` on `list_issues` results, fetch it lazily via `<linear-mcp>__get_issue` for whichever candidate the Judge feasibility phase selects. **Do not synthesize a branch name when the field is reachable** — using Linear's exact published string maximizes the chance of GitHub-integration auto-detection, even though the tracker path also adds an explicit `links` attachment as a fallback.

   If the `/do-tasks` argument was a specific identifier (e.g. `PRE-12`), skip steps 3–6 (keep step 2 — the cached state map is still needed by "Claim the issue" and "Move to review") and call `<linear-mcp>__get_issue` with that identifier. Apply the filters from step 5 to that one issue; if it fails any gate, return the failure reason rather than the issue. Do not auto-override the gates from a direct identifier — `/do-tasks` will surface the reason and stop.

## Judge feasibility

Take candidates in ranked order, **one at a time** — stop at the first feasible one. For each candidate, read the full issue (title, description, labels, links) and decide whether this session can finish it without a human in the loop.

Apply judgment, not a checklist. Ask:

- Does the description describe a concrete outcome (not "investigate X")?
- Are the files or systems it touches identifiable from the description, or by a single grep in this repo?
- Would a reasonable engineer expect to land a PR in under ~1 hour of focused work, given the codebase?
- Is there anything that screams "needs a product/design call" or "depends on infra I don't have access to"?

If feasible: continue with this candidate (proceed to "Claim the issue"). If not: leave a one-line Linear comment on the candidate (`Skipped by /do-tasks: <reason>`) and move to the next candidate. **Do not claim it.** If every candidate is rejected, summarize the reasons and stop — do not lower the bar.

Print the chosen issue's identifier, title, and a one-sentence rationale for why this one is feasible, then proceed.

## Pre-flight: is work already in flight?

Runs on the chosen candidate **after "Judge feasibility" and before "Claim the issue"**, on every path that **begins** work: the ranked-candidate pick, a `/do-tasks <identifier>` direct pick, and the `--claim-only` reserve. This is the cheapest, highest-value guard against duplicated work — it catches a sibling session that is already building this issue even when the `auto-claimed` label race below has not yet resolved (a remote branch or open PR is visible from a fresh clone the instant it is pushed, long before the loser's claim write lands). If **any** check trips, **do not claim and do not build**.

> **`--no-claim` runs a reduced form.** A `--no-claim` resume targets a task **this caller already claimed** — it is _expected_ to be in a `started`-type state, carry `auto-claimed`, be assigned to the caller, and possibly already have its branch pushed. So a resume runs **only the open-PR checks (steps 2–3)**: an open PR means the work is already published (stop and report it), but the issue's own remote branch (step 4) and started-state/`auto-claimed`/self-assignment (step 5) are the caller's _own_ claim markers — **skip steps 4–5 on the `--no-claim` path** so they don't abort a legitimate resume.

1. **Resolve the branch name.** Use the candidate's Linear `branchName` (already fetched in "Find candidates"; fetch it lazily via `<linear-mcp>__get_issue` if absent). **Do not synthesize it** — Linear publishes it deterministically per issue, and the exact string is what the checks below key on.

2. **Open PR by branch.** Linear's `branchName` is the head ref the execute path branches from, so an open PR on that head is an in-flight build:

   ```bash
   gh pr list --state open --head "<branchName>" --json number,url,headRefName
   ```

   If any PR is returned, **skip** — report `Skipped <IDENTIFIER>: open PR already exists (<url>)`.

3. **Open PR by identifier.** The execute path puts `[<IDENTIFIER>]` in every PR title (see "PR" in `/do-tasks` section 3), so also catch a PR opened from a non-standard branch:

   ```bash
   gh pr list --state open --search "<IDENTIFIER> in:title" --json number,url,title
   ```

   GitHub search tokenizes on punctuation, so this is a **coarse** pre-filter (`PRE-12` matches the tokens `PRE` and `12`, which can also hit unrelated titles). Before skipping, confirm a returned PR's `title` actually contains the literal `[<IDENTIFIER>]` bracket token — only then **skip** with the same message. (The branch check in step 2 is exact and needs no post-filter.)

4. **Remote branch (no PR yet).** A pushed branch with no PR is a strong signal another session is mid-build:

   ```bash
   git ls-remote --heads origin "<branchName>"
   ```

   A non-empty result → **skip**, naming the branch: `Skipped <IDENTIFIER>: remote branch <branchName> already exists`.

5. **Tracker-state signals.** Treat the issue as in flight (skip) if it is already in a `started`-type state, already carries `auto-claimed`, or its `assignee` is set to **another** user — not the current viewer (`<linear-mcp>__get_user` with no args returns the viewer). The ranked path's "Find candidates" filters (step 5) already exclude these, but the **direct-identifier path** must enforce the same gates **here** instead of overriding them.

**Outcome by path.** In ranked-candidate mode, an in-flight result means move to the **next** candidate (re-run "Judge feasibility" on it). On a direct `/do-tasks <identifier>` pick, surface the skip message and **stop** — do not fall through to another issue. On the `--claim-only` reserve, an in-flight result means the work is already reserved or being built — stop and report it. On the `--no-claim` resume (open-PR checks only, per the note above), an open PR means the work is already published — stop and report it; otherwise proceed with the resume.

## Claim the issue

The claim must be **claim-then-verify**: two agents that both pass the pre-flight above must not both proceed, so we write the claim and then re-read to confirm we hold it. Given the chosen candidate from "Judge feasibility" and the branch name `/do-tasks` will create:

1. **Resolve the `auto-claimed` label id.** Call `<linear-mcp>__list_issue_labels` with `teamId`. If a label named `auto-claimed` exists, capture its id. If not, create it via `<linear-mcp>__create_issue_label` (`teamId`, `name: auto-claimed`, a recognizable color like `#5E6AD2`) and capture the new id.

2. **Resolve the target `started`-type state id.** From the cached state map (find-candidates step 2), pick the team's default `started`-type state. If multiple exist, prefer one named `In Progress`; otherwise take the first.

3. **Resolve the viewer.** Call `<linear-mcp>__get_user` with no args; capture the current viewer's `id`. The viewer as `assignee` — together with the `auto-claimed` label — is the claim marker that the verify step (step 6) checks.

4. **Concurrency guard — read before write.** Call `<linear-mcp>__get_issue` with the candidate's `id` to re-read its labels and assignee. If `auto-claimed` is now present, or `assignee` is now another user, **stop and report the race**: return `race` so the tracker path can fall back to the next candidate. (This is the cheap early-out; the post-write verify in step 6 closes the residual both-read-Todo window.)

5. **Claim (write).** In **one** `<linear-mcp>__save_issue` call set state, label, and assignee together:
   - `id`: candidate `id`
   - `state`: the `started`-type state id from step 2 (the `save_issue` field is named `state`, not `stateId`; it accepts a state id, name, or type)
   - `labels`: the issue's existing label ids/names **plus** `auto-claimed` (the `save_issue` field is named `labels`, not `labelIds`; the call replaces the label set, so include the existing ones to avoid clobbering)
   - `assignee`: the viewer `id` from step 3

6. **Verify (re-read).** Immediately call `<linear-mcp>__get_issue` again on the candidate's `id`. The claim holds **iff** the re-read shows `assignee` equal to the viewer `id` from step 3 **and** `auto-claimed` present **and** the state still the `started`-type state you set. If `assignee` is now a different user (a concurrent claimer's write landed after yours), or the state was moved off your `started` state by someone else — **you lost the race**: do **not** build; return `race` so the tracker path falls back to the next candidate. Only proceed to "Branch + execute" when the re-read confirms **you** hold the claim.

7. **Comment.** Call `<linear-mcp>__save_comment` with `issueId` = candidate `id` and a body like:

   ```
   Claimed by /do-tasks. Working on branch `<branch>`; PR link will follow.
   ```

8. **Return** the issue identifier and url so `/do-tasks` can proceed to branch + execute.

> **Never** move the issue to a `completed`/`canceled` state from this path — merge is the only completion signal (the hard rule at the top of this file). The claim sets a `started`-type state only.

## Move to review on PR open

Called from `/do-tasks` immediately after `gh pr create` succeeds.

This step does two things in **one** `save_issue` call: it explicitly attaches the PR URL to the Linear issue (so the link is not dependent on branch-name auto-detection — Linear's branch-name matching is unreliable in practice and the user has reported it failing), and it transitions the issue to a review state if the team has one.

1. **Resolve the target state.** From the cached state map (find-candidates step 2), look for a `started`-type state whose name (case-insensitive) is `In Review`. If found, capture its id. If not, leave the state field unset in step 3 — the issue stays in its current `started` state (`In Progress`). Never move the issue to a `completed`-type state here, regardless of how done the work feels.

2. **Compose the link.** The PR URL from `gh pr create`, with a human title like `PR #<n>: <PR title>` (the `<n>` comes from `gh pr view --json number`, or parse it from the URL's trailing `/pull/<n>` segment).

3. **Mutate.** Call `<linear-mcp>__save_issue` with:
   - `id`: the candidate's UUID (`candidate.id`, the same value the claim step's `get_issue`/`save_issue` calls use — the human identifier like `ENG-123` is display-only)
   - `links`: `[{ "url": "<PR URL>", "title": "PR #<n>: <PR title>" }]` — this is the explicit PR↔issue attachment. **This is append-only**, so the call is safe even if other PRs are already attached.
   - `state`: the resolved `In Review` state id from step 1. **Omit this field entirely** if no `In Review` state exists — do not pass an empty string or null.
   - Do not touch `labels` here — `auto-claimed` stays on through review.

4. **No additional comment.** The PR-URL comment already posted by `/do-tasks` right after `gh pr create` is the user-facing signal that review has started; the `links` attachment is the structural one.

> Linear's GitHub integration may also create its own PR↔issue link if the team's repo is connected, but that depends on branch-name matching or magic words in the PR body — neither is reliable. The explicit `links` attachment above does not depend on the GitHub integration at all. If the integration also fires, you end up with one link (Linear de-duplicates by URL).

## Bail (when execution proves infeasible mid-flight)

1. **Resolve `human-approval-requested` label id** (create if absent, same pattern as `auto-claimed`).

2. **Resolve the team's `backlog`-type state id** from the cached state map (prefer the default).

3. Call `save_issue` with the candidate's `id`:
   - `state`: backlog state id (field is `state`, not `stateId`)
   - `labels`: existing labels **minus** `auto-claimed` **plus** `human-approval-requested` (field is `labels`, not `labelIds`; call replaces the set)
   - `assignee`: `null` — release the claim's assignee so the issue returns to backlog unclaimed (the claim marker is assignee + `auto-claimed`; clear both on bail)

4. **Comment** the bail reason via `save_comment`. Include what was tried and what tripped the bail.

5. Return the comment URL to `/do-tasks`. Do not silently pick a different candidate after a bail — the human should look at what tripped the bail before more work is auto-claimed.

## Report

`/do-tasks` prints the outcome of the tracker path:

- **On success:** the issue identifier, the PR URL, and a one-line summary of what changed.
- **On bail:** the issue identifier, why it bailed, and the Linear comment URL.
- **On the WIP gate declining:** the limit and the in-flight count (no issue claimed).
