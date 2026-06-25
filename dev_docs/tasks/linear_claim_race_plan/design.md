# Design: Linear claim race — timeliness + fidelity

**Date:** 2026-06-25
**Status:** Approved, pre-implementation
**Scope:** `commands/handlers/linear-claim.md`, `commands/handlers/linear-common.md`, `commands/do-tasks.md` §3. **Linear handler only** — gh-issue/jira/repo-pr are out of scope.

## Problem

Two independent `/do-tasks` sessions very often claim and fully build the **same** Linear issue (duplicated ~400 LOC + tests, one PR thrown away). The prior fix (`race_condition_prompt.md`, merged in `f17a685`/`e2400f1`) added a pre-flight PR/branch check and a claim-then-verify step, yet duplicates persist. Two structural flaws remain.

### Root cause 1 — the claim happens after the slowest step (timeliness)

The Linear flow runs `find candidates → judge feasibility → pre-flight → claim`. Every step before the claim is **read-only**. Two sessions starting near each other both query Todo, both rank the same issue #1, and both spend *minutes* in "Judge feasibility" (reading the full description, grepping the repo). During that entire window the issue sits in Todo, unclaimed; the pre-flight passes for both (neither has pushed a branch or claimed). The unclaimed window ≈ the whole feasibility-judgment time. That is the "claim-lag."

### Root cause 2 — the verify is last-write-wins on a non-distinguishing field (fidelity)

The claim marker is `assignee`, a reference to a Linear **user**. The racing sessions are typically authenticated as the **same** Linear account, so `get_user` returns the same viewer id for both. Both write `assignee = <same user>`, both re-read `assignee = <same user>`, both conclude "I hold it." Last-write-wins on a field whose value is identical for all racers cannot distinguish a winner. A unique per-session token cannot be smuggled into `assignee` (it is a user ref, not free text).

## Design

### Change 1 — Reorder: claim before the expensive judge (timeliness)

Phase order changes from `find → judge → pre-flight → claim` to:

1. **Find candidates** — unchanged. The existing `estimate`/labels/`assignee` filters in find-candidates step 5 *are* the cheap pre-screen and stay before the claim.
2. **Pre-flight in-flight check** — unchanged checks (open PR by branch, open PR by `[<IDENTIFIER>]` title, remote branch, started/`auto-claimed`/assigned-to-other), now run on the top-ranked candidate immediately before the claim.
3. **Claim** — claim-then-verify (see Change 2).
4. **Judge feasibility** — the expensive step (full description read + repo grep) now runs **while holding the claim**.

This collapses the unclaimed window from "minutes of feasibility analysis" to "pre-flight + the claim's two writes."

### Change 2 — Token-comment lock (fidelity)

The claim becomes a deterministic **first-writer-wins on an append-only log** (Linear comments), which `assignee` cannot provide:

1. **Read-before-write guard** (existing cheap early-out): `get_issue`; if `auto-claimed` is already present or `assignee` is another user, yield now and fall back to the next candidate.
2. **Mint a unique session token:** an **opaque random** identifier — `do-tasks-claim:<rand>` where `<rand>` is a long random hex string (e.g. `openssl rand -hex 16`, or `date +%s%N` + `$RANDOM` if `openssl` is absent) — wrapped in an HTML comment (`<!-- ... -->`) so it is invisible in rendered Linear. **Do not** embed email, hostname, or pid in the token: the election only needs uniqueness + the ordering key, and this comment lands in a shared workspace. Human attribution comes from the viewer `assignee` on the issue, not the token.
3. **Post the claim comment first** (it is the lock), via `save_comment`. *Then* `save_issue` with the `started`-type state + `auto-claimed` label + viewer `assignee` — assignee + label remain the **human-visible** claim marker.
4. **Jittered delay:** `sleep` a randomized interval with a concrete floor (the propagation budget — start at ~2–3 s, tune from observed Linear read lag) to break symmetry between racers and let Linear propagate the writes before the verify read.
5. **Verify:** `list_comments` on the issue, filter to comments containing the `do-tasks-claim:` marker, and compute the winner = the comment with the **earliest `createdAt`** (tie-break: **lowest comment id**). Every reader computes the same winner deterministically.
   - **Orphan garbage-collection (avoid deadlock):** a claim comment counts as a valid lock **only if the issue currently carries the `started`-type state + `auto-claimed` + an `assignee`** — i.e. the claim writes actually landed. A comment left by a session that crashed after `save_comment` but before/around `save_issue` sits on a card still in Todo with no `auto-claimed`; **ignore** such orphaned comments when electing the winner (and delete one if you posted it), so a stale earliest comment can never permanently block the card. Elect among the remaining state-backed claims only.
   - **Read-lag residual:** the election is only as good as what `list_comments` returns. Under Linear's eventual-consistency read lag a session may see **only its own** claim comment and wrongly conclude it won. If the verify re-read returns only your own marker, treat it as **inconclusive** — re-poll once or twice after a short delay before declaring a win. This narrows but cannot fully close the window (no server-side atomic CAS exists); the jitter floor in step 4 is the primary mitigation.
   - If **my** comment is the winner (state-backed, earliest) → I hold the claim. Also re-read the issue (`get_issue`) to confirm the `started`-type state, `auto-claimed`, and viewer `assignee` survived. Proceed to Judge feasibility.
   - If my comment is **not** the winner → I lost: `delete_comment` my own claim comment (keep the log clean), do **not** build, and fall back to the next candidate.

> **Implementation note:** the lock hinges on `list_comments` returning a usable `createdAt` and a stable comment `id` for the tie-break. Confirm both during implementation; if either is missing or non-monotonic, define the fallback ordering key before relying on the election.

### Change 3 — Release-and-continue on feasibility reject

When the deep "Judge feasibility" rejects a card that this session has **already claimed**:

- **Bail the card** with bail semantics: revert to the `backlog`-type state, add `human-approval-requested`, remove `auto-claimed`, clear `assignee`, delete the session's claim comment, and post a comment naming the reason.
- **Then move to the next ranked candidate** and re-run pre-flight → claim → judge.

A true **mid-execution** bail (work broke *while building*) still **halts** the run per the existing hard rule ("do not silently pick a different candidate after a bail"). The two bail triggers are now distinguished: feasibility-reject is release-and-continue; mid-execution failure is halt.

## Untouched invariants

- The tracker path **never** sets a `completed`/`canceled`-type workflow state — merge is the only completion signal.
- `--claim-only` = pre-flight + claim (now the token-comment lock), then stop before Judge feasibility.
- `--no-claim` resume runs the reduced pre-flight (open-PR subset only) and skips the claim.
- Linear's `branchName` is used **verbatim**; never synthesized.

## Testing

The repo's `evals/` are prompt-based skill evals (`evals/prompts/*.txt` + `manifest.tsv`). Add eval prompts asserting:

1. A card with an existing open PR is **skipped pre-claim** with the existing-PR message.
2. A lost token-comment race **yields** (deletes its claim comment, falls to next candidate) rather than building.
3. A card claimed and then **rejected at Judge feasibility** is released (backlog state, `auto-claimed` removed, `human-approval-requested` added, `assignee` cleared, claim comment deleted, reason comment posted) **and** the session proceeds to the **next ranked candidate** — exercising Change 3's release-and-continue.
4. A true **mid-execution bail** still **halts** the run and does **not** auto-pick another candidate — the load-bearing distinction Change 3 introduces.

Items 1, 3, and 4 are expressible in the single-agent prompt-eval harness (no concurrency needed). Item 2's full two-agent race may not be — if so, cover the single-agent claim mechanics (token mint, comment-then-claim ordering, orphan-GC) and state the concurrent-race fidelity guarantee is verified by inspection in the PR description rather than inventing a concurrency harness.

## Files changed

- `commands/handlers/linear-claim.md` — reorder phases; rewrite "Claim the issue" as the token-comment lock; split feasibility-reject (release-and-continue) from mid-execution bail (halt).
- `commands/handlers/linear-common.md` — update the kanban-mapping claim note to describe the token-comment lock and claim-before-judge ordering.
- `commands/do-tasks.md` §3 — reorder the "Claim and execute" step list (pre-flight + claim before judge) and the release-and-continue loop.
- `evals/` — add the skip-on-open-PR eval prompt (+ manifest entry).
