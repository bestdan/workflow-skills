# gh-issue handler — /do-tasks execute flow

Invoked from `/do-tasks` (section 4, "gh-issue path") when `handler: gh-issue` is configured. This file holds the full gh-issue execute flow: **find candidates** (read-only), **pre-flight in-flight check** (read-only), **judge feasibility** (read-only), **claim the issue** (mutating, before work starts), **branch + execute**, **PR**, and **move to review on PR open** (mutating, after the PR is opened). A separate **bail** phase runs when work proves infeasible mid-execution. It mirrors the tracker flow in `commands/handlers/linear-claim.md`, over the `gh` CLI instead of the Linear MCP.

In the **current session** (`/do-tasks`, `/do-tasks <#n>`, `--no-claim`, `--claim-only`) this flow runs here as written — `--claim-only` included, which reserves several issues at once but reserves them all from this session. In **batch** mode (`/do-tasks --all` / `-n N`, without `--claim-only` or `--no-claim`) it runs once per selected issue inside a dispatched **remote** session — see `commands/do-tasks.md` §4 "gh-issue batch", which reuses this file's find/rank and dependency phases as the dispatcher's own selection and then hands each session a single pinned issue number. Pre-flight is not reused that way — it runs in the dispatched session, on plain `git` and `gh`. **Five things change in a dispatched session and nothing else does.** Two phases the dispatcher has already discharged are skipped: "Find candidates" (it ranked and selected) and the **pre-claim WIP gate** (its `slack` bounds the whole batch, provably). **Dependency readiness is not one of them** — the dispatcher's answer can go stale, so the session re-runs `gh-issue-ready.py` against its pinned issue immediately before claiming, and stops if it is blocked. A third change is that narrowing. The fourth is the claim: it runs `claim-lock.md`'s comment-token election instead of the ref acquire in "Claim the issue" step 2, because an unattended session that crashes after acquiring strands the lock ref permanently — **plus the two `git ls-remote` probes §4 step 6 adds to that election**, which are how it and a local ref-lock session detect each other at all. The fifth is mechanical and easy to miss when copying: every asset call in this file is spelled repo-relative and must be rewritten to `$CLAUDE_PLUGIN_ROOT` as it is inlined (see "Modes" below). §4 steps 5–7 own all five substitutions and the reasoning; do not re-derive them here.

**Shared reference:** the label vocabulary is `commands/handlers/assets/labels.yml`, read the same way by `commands/handlers/gh-issue.md` (`## List`) and `gh-issue-promote.md`; every label write on this path goes through `commands/handlers/assets/gh-issue-state.py`; the claim lock this file acquires is defined once in `commands/handlers/claim-lock.md` (shared with the jira handler); `commands/handlers/linear-claim.md` is the structural template. Reuse those labels — do **not** invent `task:*` labels.

**The deterministic parts are a script, not prose.** `commands/handlers/assets/gh-issue-claim.py` owns the four steps two racing sessions must perform **identically** — the branch name, parsing an issue number back out of a branch, the in-flight count, and the acquire/release of the lock ref. Its **exit codes are the contract** this file branches on; re-deriving any of it in prose reopens the race it closes.

**Branch name.** The work branch is `<branch_prefix>task-<n>`, and it is also the claim
lock ref (see `claim-lock.md`). `<branch_prefix>` is the `gh-issue.branch_prefix` key in
`dev_docs/tasks/.task-config.yml`, empty by default — so the name is `task-142` unless a
repo configures one, and `bestdan/task-142` where `branch_prefix: bestdan/`. Never build
the name by hand; ask for it:

```bash
branch=$(python3 commands/handlers/assets/gh-issue-claim.py branch-name --issue <n> [--prefix "<branch_prefix>"])
```

Three constraints meet here. `claim-lock.md` needs one deterministic name both racers
compute the same way — which is why it is derived from the issue number and not from the
title. The number must be **in** the name, so a branch or PR traces back to its issue.
And a repo may require a branch prefix of its own (this one requires `bestdan/`), which
a fixed `task/<n>` cannot satisfy. Reading the number back out is therefore
prefix-agnostic — cloud routines push to `claude/`-prefixed branches, so the parser takes
the segment after the last `/` and requires exactly `task-<digits>`:

```bash
n=$(python3 commands/handlers/assets/gh-issue-claim.py issue-number --branch "<branch>")   # exits 1 if not a task branch
```

This path deliberately does not use `gh issue develop`: its
generated branch name is not deterministic, so it cannot be probed before the claim,
and the create it performs yields no rejection this flow can read as a lost race. The
GitHub-native issue↔branch link is the cost; `Closes #<n>` in the PR body and the
`[#<n>]` title prefix carry the association instead.

**Every label write is read-modify-write.** `gh-issue-state.py` takes the **complete
managed set** and refuses a non-`--done` write that is missing exactly one `status:` and
exactly one `auto:` label. It carries unmanaged labels forward on its own (`follow-up`,
anything a human added), but not managed ones — so a transition reads the issue's current
labels, changes the one rung, and passes the whole set back:

```bash
labels=$(gh issue view <n> --json labels --jq '[.labels[].name]' [--repo <repo>])
# keep every prio:/est:/auto: label, replace the status: rung, then:
python3 commands/handlers/assets/gh-issue-state.py --repo <repo> --issue <n> \
  --labels "status:3_started,auto:eligible,prio:1,est:3" --apply
```

**Never** use `gh issue edit --add-label/--remove-label` on a transition path: it is not
atomic (8 measured requests), so a crash strands an issue carrying two `status:` rungs or
none.

> **Hard rule for every phase below: never close a gh issue manually, and never move it to a `completed`/`canceled` state.** Merge is the only completion signal — GitHub closes the issue automatically when the PR (with `Closes #<n>` in its body) merges. If you are about to `gh issue close` from this file, you have a bug — stop.

**Repo.** If `gh-issue.repo` is set in `dev_docs/tasks/.task-config.yml`, pass it as `--repo <repo>` on every `gh` call below. Otherwise omit `--repo` to act on the current repo, matching the create/list/promote flows. The asset scripts **require** `--repo`, so resolve it once when the key is unset: `repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)`.

## Modes: atomic vs. claim/execute split

`/do-tasks` claims and executes atomically by default. Two **mutually exclusive**
flags (`--claim-only` / `--no-claim`, passed through from `/do-tasks`) split that
into composable steps so a claim now plus a `--no-claim` execute later add up to one
normal run — passing both is an error: stop and ask which was meant.

- **default** (neither flag) — run every phase below: pre-claim WIP gate → find
  candidates → pre-flight → judge → claim → branch + execute → PR → move to review.
- **`--claim-only`** — run through "Claim the issue" (pre-claim WIP gate, find
  candidates, pre-flight, judge, then acquire the `<branch>` claim lock, assign `@me`,
  move the rung to `status:3_started`), then **stop**: no execution, no PR. The
  created `<branch>` lock ref plus the assigned, started issue is the reservation
  marker — do **not** move it to `status:4_needs_review`. `--claim-only` is the one
  execute-family action safe to batch, so
  `/do-tasks --all` / `-n N --claim-only` may reserve several issues at once, each
  bounded by the WIP gate.
- **`--no-claim <#n>`** — skip the claim and resume an issue this caller has
  **already** claimed. **Requires an explicit issue number** — there is no default
  selection. Guard: proceed only when the issue's assignee is this caller
  (`me=$(gh api user --jq .login)`; the issue's `assignees[].login` is exactly that
  one login) **and** it carries `status:3_started`. Otherwise **stop and explain** —
  executing an unclaimed issue reopens the race the claim step closes. When the guard
  passes, **check out the existing claim branch** rather than branching fresh from
  `HEAD` — the claim created the deterministic `<branch>`, so there is nothing
  to look up:

  ```bash
  git fetch origin && git switch "<branch>"
  ```

  If that branch exists neither locally nor on the remote — the claim ran on the
  degraded comment-election path, which creates no ref (see `claim-lock.md`) — create it
  now: `git switch -c "<branch>" "origin/<base>"` (`<base>` defaults to the repo's
  default branch). Then run "Branch + execute" (skipping branch creation), "PR", and
  "Move to review" — without re-claiming.
  `--no-claim` is always single (`--all` / `-n N` do not apply).

**`--all` / `-n N` without `--claim-only`** is **not** driven from this file. `commands/do-tasks.md` §4 "gh-issue batch" ranks and selects the dependency-ready candidates itself, then dispatches one remote session per selected issue, each running the flow above against its one issue number **with §4's five batch substitutions** — "Find candidates" and the pre-claim WIP gate skipped as already discharged; **dependency readiness narrowed rather than skipped**, the dispatcher using it to select and each session re-running `gh-issue-ready.py` against its pinned issue immediately before claiming; and the claim taken on `claim-lock.md`'s comment-token election rather than the ref acquire (the first four this file's opening paragraph names; the asset-path rewrite below is the fifth). Everything else runs as written, **except the asset paths**: every `python3 commands/handlers/assets/…` call in this file is repo-relative and resolves only inside this plugin's own repo, so the inlined prompt must rewrite each one to `python3 "$CLAUDE_PLUGIN_ROOT/commands/handlers/assets/…"`. §4 step 5 says which gates run where, and what discharging the WIP gate costs — `slack` becomes a one-instant, dispatcher-side bound rather than a guarantee. Everything the session runs before the claim is verification-only: a pre-flight trip, a feasibility reject, or a lost claim **stops and reports** rather than advancing to another candidate — advancing would put two dispatched sessions on one issue. `--all` / `-n N` with `--local` never dispatches: it caps the batch at 1 and runs the single highest-ranked issue through the default flow in the current session.

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
   `dev_docs/tasks/.task-config.yml` (default `3` — the same key the repo-pr and
   linear handlers use).
2. Count current in-flight work:

   ```bash
   python3 commands/handlers/assets/gh-issue-claim.py wip --repo <repo> --wip-limit <wip_limit> --json
   ```

   In flight = open issues **assigned to this caller** carrying `status:3_started`
   (claimed, PR not yet open) **or** `status:4_needs_review` (PR open, in review). An
   in-flight issue holds exactly one of the two, and GitHub's issue search takes a
   comma-separated label list as an OR, so that is **one** query rather than two counts
   summed. Read `count` and `at_limit` from the JSON.

   Both rungs count because the WIP limit exists to bound the human PR-review queue:
   "Move to review on PR open" moves `status:3_started → status:4_needs_review`, so a
   `status:4_needs_review` issue is an open PR still awaiting review — in-flight, not
   done. (This mirrors the linear gate, which counts both `In Progress` and `In Review`.)
   Both names are read from `labels.yml`, so a rename there fails loudly instead of
   silently counting zero.

   `assignee:@me` scopes the count to **this operator's** work, matching the jira and
   linear gates. "Claim the issue" self-assigns, so the filter is exact rather than a
   heuristic. Be clear about what this changes: it is a **loosening**. Without it, two
   operators running the loop against one repo shared a single budget; with it each holds
   `wip_limit` of its own. That is deliberate — the labels alone already made the count
   tool-scoped rather than a true bound on the repo's review queue, and a per-operator
   budget is the only denominator a caller can act on.
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

```bash
gh issue list --state open --search 'label:"status:2_ready" label:"auto:eligible" no:assignee -label:blocked' --limit 50 --json number,title,body,labels,assignees,createdAt [--repo <repo>]
```

- The two positive terms **AND** together and are both load-bearing. `status:2_ready` is where the work is; `auto:eligible` is whether automation may take it. `/promote-tasks` writes them as a pair precisely so a human can mark an issue ready and still withhold it from the loop — a `status:2_ready` issue carrying `auto:human-review-needed` is **not** a candidate here. Quote each value: the names contain a colon, which is also the search syntax's own separator.
- `no:assignee` skips anything already claimed; `-label:blocked` excludes the manual block override. All filters ride in `--search` because `gh issue list` ignores a separate `--label` flag once `--search` is present. No `status:` exclusions are needed — an open issue carries exactly one rung, so asking for `status:2_ready` already excludes the others.

  > **Both positive terms have to stay on the server.** Dropping one and filtering the
  > returned `labels` client-side looks equivalent and is not: `--limit 50` is applied
  > by the API **before** any local filter runs, so on a repo with more than 50 other
  > unassigned open issues the window can contain no ready issue at all and
  > `/do-tasks` reports "no candidate" — silently, and worst for exactly the oldest
  > issues the ranking below most wants, since the search returns newest first.

- As a backstop to the query filter (e.g. label-index lag), drop any issue whose returned `labels` do not carry **both** `status:2_ready` and `auto:eligible`, or that carries `blocked` — these receive no claim action.
- **Rank** by priority: `prio:0` → `prio:1` → `prio:2` → `prio:3`. An issue carrying none sorts last. Then by issue age (oldest `createdAt` first — let aging issues bubble up).
- **Then drop the dependency-blocked.** The query above catches only the manual `blocked` label; GitHub's native `blocked_by` graph is a separate fact, and without this pass `/list-tasks` shows an issue as blocked while `/do-tasks` claims it. Ask about **exactly** the ranked candidates:

  ```bash
  python3 commands/handlers/assets/gh-issue-ready.py --repo <repo> --issue <n1> --issue <n2> ... --json
  ```

  Keep the candidates in its `ready` array, in the ranked order above; drop those in `blocked`, reporting each with the open blockers it names. Pass the numbers rather than letting the script run its own board query: `--limit` is applied by the API before anything local runs, so a second bounded query could omit a candidate silently, and a missing verdict is indistinguishable from a ready one.

- Limit 50. If exactly 50 issues are returned the page may be truncated — note it in the report; do not paginate.

Take the ranked candidates **one at a time**: for each candidate in ranked order, run **Pre-flight: is work already in flight?** and then, if it passes, **Judge feasibility** — on a pre-flight trip or a feasibility reject, advance to the next candidate and start it at pre-flight. If no candidate remains, report that and stop.

## Pre-flight: is work already in flight?

Runs on the candidate **before "Judge feasibility" and "Claim the issue"**, on every claiming path (single, direct `<#n>`, and `--claim-only`). The same cheap, high-value guard as `linear-claim.md`'s pre-flight: catch a sibling session that is already building this issue before spending the full issue-body read and feasibility judgment. If **any** check trips, **do not judge, do not claim, and do not build** — skip and report.

1. **Claim branch + its PR.** The claim creates the deterministic `<branch>` (see "Claim the issue"), so probing that one ref catches a sibling session mid-build:

   ```bash
   git ls-remote --heads origin "<branch>"
   gh pr list --state open --head "<branch>" --json number,url,headRefName [--repo <repo>]
   ```

   If `git ls-remote` returns the ref, treat the issue as in flight: a non-empty `gh pr list` → `Skipped #<n>: open PR already exists (<url>)`; otherwise (branch exists, no PR yet) → `Skipped #<n>: remote branch <branch> already exists`.

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

The claim locks on an **atomic primitive** — creating the `<branch>` ref, a server-side compare-and-swap — because GitHub exposes no compare-and-swap on issue fields and a same-account racer reads back the identical `assignees`. Mechanics, the branch-pinned fallback, and the release rule live in **`commands/handlers/claim-lock.md`**; read it and follow it here rather than re-deriving them. The assignee and `status:3_started` rung below stay on as the **human-visible** claim marker — they no longer decide the race.

1. **Re-read** the chosen issue (`gh issue view <n> --json assignees,labels [--repo <repo>]`). If it now has an assignee, or its rung has moved off `status:2_ready`, or it no longer carries `auto:eligible`, **another session beat you** or a human withdrew it from automation — return `race`, fall back to the next candidate. This is a cheap early-out, not the lock; note the wall-clock time of this read as `T_unclaimed` (the fallback election in `claim-lock.md` needs it). Keep the label list — step 3 needs it.
2. **Acquire the lock** — `claim-lock.md` → "Primitive: create-only ref creation via the GitHub API", with `<base>` the repo's default branch (or `--base` when `/do-tasks` passed one), and `<repo>` = `gh-issue.repo` if set, else the current repo:

   ```bash
   git fetch origin
   base_sha=$(git rev-parse "origin/<base>")
   python3 commands/handlers/assets/gh-issue-claim.py acquire \
     --repo <repo> --issue <n> --base-sha "$base_sha" [--prefix "<branch_prefix>"]
   ```

   **Branch on the exit code — it is the election, and it is the only thing that decides it:**

   | exit | meaning                                                                      | do                                                                                                                  |
   | ---- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
   | `0`  | acquired (HTTP 201)                                                          | proceed to step 3                                                                                                   |
   | `3`  | lost — the ref already exists (HTTP 422)                                     | leave the issue's assignee and labels **untouched**, return `race`, fall back to the next candidate                 |
   | `4`  | neither — 403/404, protected-ref ruleset, branch-pinned environment, network | degrade to `claim-lock.md`'s comment-token election (using `T_unclaimed` from step 1) and report the degrade reason |

   Exit `4` is **not** a lost race and **not** a held claim; never report a claim you did not acquire atomically. On exit `0`, check the branch out before step 3: `git fetch origin "<branch>" && git switch -c "<branch>" FETCH_HEAD`. Do **not** substitute `git push origin <branch>` for this call: both racers branch from the same base sha, so the loser's push reports `Everything up-to-date` and exits 0 (measured — see the warning in `claim-lock.md`).

3. **Mark it on the board** — assign yourself, then move the rung to `status:3_started` through the writer, carrying the issue's other managed labels forward:

   ```bash
   gh issue edit <n> --add-assignee @me [--repo <repo>]
   python3 commands/handlers/assets/gh-issue-state.py --repo <repo> --issue <n> \
     --labels "status:3_started,<the issue's auto: rung>[,<its prio: label>][,<its est: label>]" --apply
   ```

   The `auto:` rung is carried through unchanged — it answers "may automation take this?", which claiming does not change. A malformed label set is refused locally, before the writer's network call. **If the writer fails after `--add-assignee` landed, stop and run Bail** — release the lock ref and clear the assignee. That half-written state is the one way this step can strand an issue: assigned and lock-held but still `status:2_ready`, which no other session will pick up (`no:assignee` excludes it) and no later phase will clean up. Reordering the two writes does not avoid it — `status:3_started` with no assignee is just as far outside the candidate query.

4. **Confirm the marker landed** (not the race — the acquire in step 2 already decided that). Resolve your own login once (`me=$(gh api user --jq .login)`; `@me` is only valid as a `--add-assignee`/`--remove-assignee` argument, never a value you can match in the JSON), then re-read the issue's `assignees`. If a **different** login appears, a same-second sibling wrote the marker even though you hold the lock: leave the assignee alone (stomping it would disrupt a human's deliberate reassignment) and report `#<n>: claim lock held, but assignee is <other> — the board marker disagrees with the lock`. Do **not** return `race` on this signal alone — you hold `<branch>` and no one else can create it, so the atomic winner is you.

(The writer validates every name against `labels.yml` before it writes, so an undefined label is refused locally rather than created by the raw REST call. Provision a repo's vocabulary once with `python3 commands/handlers/assets/gh-label-sync.py --repo <repo> --apply`.)

## Branch + execute

1. **Branch** — already done. "Claim the issue" step 2 created `<branch>` from `<base>` (the repo's default branch unless `/do-tasks` passed `--base`) as the claim lock, so this session is already on it. Confirm with `git branch --show-current` and do **not** re-create it. Only on the degraded comment-election path (no ref was created) create it now: `git switch -c "<branch>" "origin/<base>"`.
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

Move the rung from `status:3_started` to `status:4_needs_review` (the issue stays open — review is signalled by the rung and the linked PR). Read the current labels, replace the one rung, pass the complete managed set:

```bash
python3 commands/handlers/assets/gh-issue-state.py --repo <repo> --issue <n> \
  --labels "status:4_needs_review,<the issue's auto: rung>[,<its prio: label>][,<its est: label>]" --apply
```

Never `gh issue close` here, and never pass `--done`, regardless of how done the work feels — merge handles closure via `Closes #<n>`.

**A backstop also runs, and its reverse has no other owner.** `.github/workflows/gh-issue-pr-sync.yml` calls `commands/handlers/assets/gh-issue-pr-sync.py`, which performs the same transition from the PR's `opened` and `ready_for_review` events — so a PR opened outside this loop still moves its issue, whether it was opened as a draft and later marked ready or opened non-draft in one step. A draft PR is filtered by the workflow's own `if:`, never by the script. The backstop writes only when the issue is already on `status:3_started`, so doing the write here first makes the Action a no-op rather than a conflict.

The reverse transition is the backstop's alone: a PR **closed without merging** moves the issue back to `status:3_started`, because nothing else is watching at that point and the issue would otherwise sit in `needs_review` forever with no open PR. Both directions need the workflow's `issues: write` permission, and the workflow ships only in this repo. A consumer repo adopting the `gh-issue` handler must vendor **the workflow and every asset it runs** — `gh-issue-pr-sync.py`, plus the `gh-issue-claim.py`, `gh-issue-state.py`, `_labels.py` and `labels.yml` it loads — at the paths the workflow invokes. An Actions runner sees only the checkout, never the plugin cache, so copying the workflow alone yields a run that dies on a missing script.

The backstop also assumes **the tracker is the same repo as the code**. It acts on `github.repository`, so a repo whose `gh-issue.repo` points at a different tracker must not run it — there it would read its own same-numbered issue while the real tracker goes unwritten. `GITHUB_TOKEN` is repo-scoped, so this is a limitation to respect rather than one to credential around.

## Bail (when execution proves infeasible mid-flight)

```bash
git stash push -u
git switch -
python3 commands/handlers/assets/gh-issue-claim.py release --repo <repo> --issue <n> [--prefix "<branch_prefix>"]
python3 commands/handlers/assets/gh-issue-state.py --repo <repo> --issue <n> \
  --labels "status:1_needs_refinement,auto:human-review-needed[,<its prio: label>][,<its est: label>]" --apply
gh issue edit <n> --remove-assignee @me [--repo <repo>]
gh issue comment <n> --body "Bailed by /do-tasks: <what was tried, what tripped the bail>" [--repo <repo>]
```

The bail writes **both** rungs, and they say different things: `status:1_needs_refinement`
puts the work back in front of a human, and `auto:human-review-needed` withholds it from
the loop — without the second, the next `/do-tasks` run would re-select the issue the
moment a human promoted it back, and bail again for the same reason.

Release the lock **first** (`claim-lock.md` → "Release the lock") so the issue never
returns to the ready lane while a stale `<branch>` still blocks the next session's
acquire. On the degraded election path there is no ref to delete — delete this session's
token comment instead (`gh api --method DELETE repos/<repo>/issues/comments/<id>`).

Stop — do not auto-pick another candidate after a bail; a human should look before more work is auto-claimed.

## Report

`/do-tasks` prints the outcome:

- **On success:** the issue number, the PR URL, and a one-line summary of what changed.
- **On bail:** the issue number, why it bailed, and the issue-comment URL.
- **On no feasible candidate:** say so (with the skip reasons recorded on each rejected issue).
