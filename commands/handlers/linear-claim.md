# Linear handler — /do-tasks tracker flow

Invoked from `/do-tasks` (section 3, "Tracker path") when `handler: linear` is configured. This file holds the full tracker execute flow, run in the current session: **find candidates** (read-only), **pre-flight in-flight check** (read-only), **claim the issue** (mutating, before work starts — a token-comment lock), **judge feasibility** (read-only, run _while holding the claim_), **move to review on PR open** (mutating, after the PR is opened), and a **report** at the end. The **bail** phase has two distinct triggers: a _feasibility reject_ on an already-claimed card **releases the claim and continues** to the next candidate, while a _mid-execution_ failure **halts** the run. `/do-tasks` orchestrates the branch-and-execute and `gh pr create` between the judge and move-to-review phases.

> **Why claim before judging?** Every step up to the claim is read-only, so two sessions that start near each other both rank the same issue #1 and both spend _minutes_ judging feasibility while the issue sits unclaimed — they collide. Claiming immediately after the cheap pre-flight (and judging feasibility only once the claim is held) collapses that unclaimed window to the pre-flight plus the lock's writes. And because racing sessions are usually authenticated as the **same** Linear user, `assignee` cannot distinguish a winner; the claim is therefore a first-writer-wins election on an append-only **comment** log carrying a unique per-session token.

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

3. **Resolve project scope.** Call the **"Resolve configured projects"** helper from `linear-common.md` — it returns a list of scopes `{ id, name, wip_limit, max_estimate }` (inheritance already applied: per-project override else the global default). When `linear.projects` is absent/empty the list is a single synthetic **whole-team** scope (`id: null`). The query below runs **once per scope**; a `null` `id` means "omit `projectId`, search the whole team" (today's no-pin behavior).

4. **Query — once per resolved scope.** For each scope from step 3, call `<linear-mcp>__list_issues` with:
   - `teamId`: resolved team id
   - `projectId`: the scope's `id` (**omit** when `id` is `null` — the whole-team scope)
   - `stateId`: each `unstarted`-type state id from step 2 (loop or pass as list per the tool's accepted shape)
   - `includeArchived`: `false`
   - Limit: 50 **per scope**. If a scope truncates, note it; do not paginate (claiming one card per call doesn't need exhaustive enumeration).

   **Tag every returned issue with its source scope** — carry `project: { id, name, wip_limit, max_estimate }` on each candidate so the filter (step 5), the rank (step 6), and the per-project WIP gate in `do-tasks.md` read the **right per-project cap**. Union the candidates across scopes; an issue belongs to at most one project, so no dedup is needed (and a whole-team scope is a single list).

5. **Filter.** From the returned issues, drop any that fail any of these gates. Each gate has a fixed reason string so the caller can report consistently. (Linear's native `estimate` uses the same Fibonacci scale as our task `size` — see "Task size" in skills/task/SKILL.md — so these thresholds select tasks small enough to finish in one session.)
   - `estimate` is `null`/missing → `no estimate set`
   - `estimate >= <max>`, where `<max>` is the candidate's resolved per-project `max_estimate` (from step 3, default `3`) → `estimate <N> >= <max>`
   - Has label `auto-claimed` → `already auto-claimed`
   - Has label `human-approval-requested` → `human-approval-requested`
   - Has label `blocked` → `blocked`
   - `assignee` is set and is **not** the current Linear user (`<linear-mcp>__get_user` with no args returns the viewer) → `assigned to <name>`

6. **Rank.** Sort remaining issues by Linear `priority` (urgent=1 → low=4, then none=0 last), then by `updatedAt` ascending (oldest first — let aging cards bubble up).

7. **Return** the ranked list to the **Pre-flight** phase below. Each entry needs: `id`, `identifier`, `title`, `priority`, `estimate`, `description`, `labels`, `url`, **`branchName`** (Linear's published git branch name — used verbatim when `/do-tasks` branches), **`project`** (the `{ id, name, wip_limit, max_estimate }` scope it came from — `id: null` for whole-team — so the WIP gate checks the right per-project cap), and the resolved `id`s for: team, current `state`, target `started`-type `state` (see "Claim the issue" below). The execute path takes candidates in ranked order and, for each, runs **pre-flight → claim → judge feasibility** — the feasibility read now happens _after_ the claim, while the lock is held.

   If the MCP response does not include `branchName` on `list_issues` results, fetch it lazily via `<linear-mcp>__get_issue` for the top-ranked candidate the Pre-flight phase operates on (and for each next candidate as the loop advances). **Do not synthesize a branch name when the field is reachable** — using Linear's exact published string maximizes the chance of GitHub-integration auto-detection, even though the tracker path also adds an explicit `links` attachment as a fallback.

   If the `/do-tasks` argument was a specific identifier (e.g. `PRE-12`), skip the per-scope query in step 4 (keep step 2 — the cached state map is still needed by "Claim the issue" and "Move to review", and keep step 3 to resolve the configured scopes) and call `<linear-mcp>__get_issue` with that identifier. **Determine its `project` scope** by matching the issue's `projectId` against the resolved configured-projects list (step 3); if the issue isn't in any configured project, use the whole-team scope's caps. Apply the filters from step 5 to that one issue using **that scope's** `max_estimate`; if it fails any gate, return the failure reason rather than the issue. Do not auto-override the gates from a direct identifier — `/do-tasks` will surface the reason and stop.

## Pre-flight: is work already in flight?

Runs on the **top-ranked candidate** immediately **before "Claim the issue"** — no feasibility judgment yet (that now runs _after_ the claim, while holding it). On every path that **begins** work: the ranked-candidate pick, a `/do-tasks <identifier>` direct pick, and the `--claim-only` reserve. This is the cheapest, highest-value guard against duplicated work — it catches a sibling session that is already building this issue even when the token-comment lock below has not yet resolved (a remote branch or open PR is visible from a fresh clone the instant it is pushed, long before the loser's claim write lands). If **any** check trips, **do not claim and do not build**.

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

**Outcome by path.** In ranked-candidate mode, an in-flight result means move to the **next** candidate (re-run "Pre-flight" on it, then claim, then judge). On a direct `/do-tasks <identifier>` pick, surface the skip message and **stop** — do not fall through to another issue. On the `--claim-only` reserve, an in-flight result means the work is already reserved or being built — stop and report it. On the `--no-claim` resume (open-PR checks only, per the note above), an open PR means the work is already published — stop and report it; otherwise proceed with the resume.

## Claim the issue

The claim is a deterministic **first-writer-wins election on an append-only log** (Linear comments): two sessions that both pass the pre-flight must not both proceed, but `assignee` cannot decide a winner when both racers are authenticated as the **same** Linear user (both write — and re-read — the identical viewer id). So the lock is a **claim comment carrying a unique per-session token**, and the winner is whoever's comment is oldest. The `started`-type state, `auto-claimed` label, and viewer `assignee` stay on as the **human-visible** claim marker and as the orphan-GC guard the election reads against. Given the **top-ranked candidate** from "Pre-flight" and the branch name `/do-tasks` will create:

1. **Resolve the `auto-claimed` label id.** Call `<linear-mcp>__list_issue_labels` with `teamId`. If a label named `auto-claimed` exists, capture its id. If not, create it via `<linear-mcp>__create_issue_label` (`teamId`, `name: auto-claimed`, a recognizable color like `#5E6AD2`) and capture the new id.

2. **Resolve the target `started`-type state id.** From the cached state map (find-candidates step 2), pick the team's default `started`-type state. If multiple exist, prefer one named `In Progress`; otherwise take the first.

3. **Resolve the viewer.** Call `<linear-mcp>__get_user` with no args; capture the current viewer's `id`. The viewer as `assignee` — together with the `auto-claimed` label — is the human-visible claim marker (and the state-backing the election's orphan-GC checks for).

4. **Read-before-write guard** (cheap early-out). Call `<linear-mcp>__get_issue` with the candidate's `id` to re-read labels and assignee. If `auto-claimed` is now present, or `assignee` is now another user, **yield now**: return `race` so the tracker path falls back to the next candidate. (This catches an already-resolved claim before you spend a write; the comment election below closes the residual both-read-Todo window.) **Record the wall-clock time of this read as `T_unclaimed`** — the moment you last saw the card in Todo. The election in step 8 uses it to ignore stale claim comments from earlier attempts.

5. **Mint a unique session token.** Build `do-tasks-claim:<rand>` where `<rand>` is a long random hex string — `openssl rand -hex 16`, or `printf '%s%s' "$(date +%s%N)" "$RANDOM"` if `openssl` is absent. Wrap the whole token in an HTML comment so it is invisible in rendered Linear: `<!-- do-tasks-claim:<rand> -->`. **Do not** embed email, hostname, or pid in the token — the election needs only uniqueness and the ordering key, and this comment lands in a shared workspace; human attribution comes from the viewer `assignee`, not the token.

6. **Post the claim comment first — it is the lock.** Call `<linear-mcp>__save_comment` with `issueId` = candidate `id` and a body that carries the token **and** a human line:

   ```
   Claimed by /do-tasks. Working on branch `<branch>`; PR link will follow.
   <!-- do-tasks-claim:<rand> -->
   ```

   **Capture the comment id** `save_comment` returns (or recover it later by matching your unique `<rand>` token via `list_comments`) — the lost-race branch in step 8 and the Bail step both `delete_comment` _this session's_ claim comment by id, so it must be retrievable.

   **Then** — and only then — claim the issue body in **one** `<linear-mcp>__save_issue` call:
   - `id`: candidate `id`
   - `state`: the `started`-type state id from step 2 (the `save_issue` field is named `state`, not `stateId`; it accepts a state id, name, or type)
   - `labels`: the issue's existing label ids/names **plus** `auto-claimed` (the `save_issue` field is named `labels`, not `labelIds`; the call replaces the label set, so include the existing ones to avoid clobbering)
   - `assignee`: the viewer `id` from step 3

   Posting the comment before the issue write makes the comment the lock and the issue write the state-backing the election checks for.

7. **Jittered delay.** `sleep` a randomized interval with a concrete floor — start at ~2–3 s (the propagation budget; tune up from observed Linear read lag). This breaks symmetry between racers and lets Linear propagate the comment and issue writes before the verify read.

8. **Verify — elect the winner.** Call `<linear-mcp>__list_comments` on the candidate's `id`, filter to comments whose body contains the `do-tasks-claim:` marker, and among the **eligible** ones (both filters below) elect the winner = the claim comment with the **earliest `createdAt`** (tie-break: **lowest comment id**). Every reader computes the same winner deterministically.
   - **Eligibility filter (a) — live-window bound (the load-bearing fix).** Consider **only** `do-tasks-claim:` comments whose `createdAt` is **at or after `T_unclaimed`** (step 4 — the moment this session last saw the card in Todo). Any genuine competitor must have posted its claim _after_ that observation, so an older comment is a **stale orphan from a prior attempt** — ignore it **regardless of the issue's current state**. This is essential: the state-backed check below is a property of the _issue_, not the comment, so once _any_ live racer's `save_issue` flips the card to `started`+`auto-claimed`, that state would otherwise retroactively "back" a stale earliest orphan and the election would elect a dead token nobody owns — a permanent deadlock (the loser deletes its own comment and falls back, leaving the card `started`+`auto-claimed` with no owner). The `T_unclaimed` bound is what makes the backing attributable to the live race rather than to the issue as a whole.
   - **Eligibility filter (b) — state-backed.** Among the live-window comments, a comment counts only if the issue **currently** carries the `started`-type state + `auto-claimed` + an `assignee` — i.e. the claim's `save_issue` actually landed. A session that crashed after `save_comment` but before `save_issue` leaves a comment on a card still in Todo; ignore it (and `delete_comment` one if you posted it).
   - **Read-lag residual.** The election is only as good as what `list_comments` returns. Under Linear's eventual-consistency read lag a session may see **only its own** claim comment and wrongly conclude it won. If the re-read returns **only your own** marker, treat it as **inconclusive** — re-poll once or twice after a short delay before declaring a win. This narrows but cannot fully close the window (no server-side atomic CAS exists); the jitter floor in step 7 is the primary mitigation.
   - **If my comment is the winner** (eligible by both filters, earliest) → re-read the issue (`<linear-mcp>__get_issue`) to confirm the `started`-type state, `auto-claimed`, and viewer `assignee` **survived**. If they did → I hold the claim; proceed to **Judge feasibility**. **If the confirm read shows otherwise** — another claimer overwrote the assignee or moved the state after my write — treat it as a **lost race**: `delete_comment` my own claim comment, do **not** build, and return `race` to fall back to the next candidate (same as the not-the-winner branch). Do not proceed while not actually holding the claim.
   - **If my comment is not the winner** → I lost: `delete_comment` my own claim comment (keep the log clean), do **not** build, and return `race` so the tracker path falls back to the next candidate.

9. **Return** the issue identifier and url so `/do-tasks` can proceed to Judge feasibility (then branch + execute).

> **Implementation note:** the lock hinges on `list_comments` returning a usable `createdAt` and a stable comment `id` for the tie-break. Confirm both during implementation; if either is missing or non-monotonic, define the fallback ordering key before relying on the election.

> **Never** move the issue to a `completed`/`canceled` state from this path — merge is the only completion signal (the hard rule at the top of this file). The claim sets a `started`-type state only.

## Judge feasibility

Runs on the candidate this session **just claimed** (the token-comment lock is held). Read the full issue (title, description, labels, links) and decide whether this session can finish it without a human in the loop. (`--claim-only` stops _before_ this phase — it reserves the card without judging; see "Untouched invariants" in the design.)

Apply judgment, not a checklist. Ask:

- Does the description describe a concrete outcome (not "investigate X")?
- Are the files or systems it touches identifiable from the description, or by a single grep in this repo?
- Would a reasonable engineer expect to land a PR in under ~1 hour of focused work, given the codebase?
- Is there anything that screams "needs a product/design call" or "depends on infra I don't have access to"?

If **feasible**: print the issue's identifier, title, and a one-sentence rationale, then proceed to "Branch + execute". If **not feasible**: this is a **feasibility reject** on a card you already hold — run **"Bail — feasibility reject (release-and-continue)"** below: release the claim and move to the **next ranked candidate**, re-running Pre-flight → Claim → Judge on it. **Do not halt** (halting is reserved for a _mid-execution_ failure). If every candidate is rejected, summarize the reasons and stop — do not lower the bar.

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

## Bail

Bail releases a claim this session holds. There are **two triggers**, and they differ only in what happens _after_ the release — the release mechanics (steps 1–5) are identical:

- **Feasibility reject** — "Judge feasibility" rejected the card _before_ any building started. → **release-and-continue.**
- **Mid-execution failure** — work broke _while building_ (after "Branch + execute" began). → **halt.**

**Release the claim (both triggers):**

1. **Resolve `human-approval-requested` label id** (create if absent, same pattern as `auto-claimed`).

2. **Resolve the team's `backlog`-type state id** from the cached state map (prefer the default).

3. Call `save_issue` with the candidate's `id`:
   - `state`: backlog state id (field is `state`, not `stateId`)
   - `labels`: existing labels **minus** `auto-claimed` **plus** `human-approval-requested` (field is `labels`, not `labelIds`; call replaces the set)
   - `assignee`: `null` — release the claim's assignee so the issue returns to backlog unclaimed (the human-visible claim marker is assignee + `auto-claimed`; clear both on bail)

4. **Delete this session's claim comment** via `<linear-mcp>__delete_comment` (the token comment posted in "Claim the issue" step 6 is the lock — removing it fully releases the card so a later session can win the election cleanly). Then **comment** the bail reason via `save_comment`, including what was tried and what tripped the bail.

**Then, by trigger:**

5a. **Feasibility reject → release-and-continue.** Move to the **next ranked candidate** and re-run Pre-flight → Claim → Judge on it. This is the _only_ bail that auto-advances; it never built anything, so there is no half-done work for a human to inspect.

5b. **Mid-execution failure → halt.** Return the comment URL to `/do-tasks` and **stop** — do **not** silently pick a different candidate. Work was already in flight when it broke, so a human should look at what tripped the bail before more work is auto-claimed. This is the load-bearing distinction: a reject that never built continues; a build that broke halts.

## Report

`/do-tasks` prints the outcome of the tracker path:

- **On success:** the issue identifier, the PR URL, and a one-line summary of what changed.
- **On bail:** the issue identifier, why it bailed, and the Linear comment URL.
- **On the WIP gate declining:** the limit and the in-flight count (no issue claimed).
