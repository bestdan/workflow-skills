# repo-pr handler — /do-tasks file-path execute flow

Invoked from `/do-tasks` when the handler is `repo-pr` (or absent). Scan for
dependency-ready task files and turn them into PRs — by default dispatching a
remote Claude session per task (each in its own isolated cloud VM), or in the
current session with `--local`.

This file holds the file-path mechanics (scan, ranking, multi-blocker readiness,
WIP cap, the remote dispatch prompt, local mode, and the report format) so
`/do-tasks` can reference it rather than restate it — symmetric with
`linear-claim.md` for the tracker path.

> **Legacy migration preflight.** `/do-tasks` runs this before invoking this file
> (if a legacy `dev_docs/todos/` directory exists, run the **Legacy migration**
> prompt from `skills/task/SKILL.md`), so assume it is already handled here.

## Argument mapping

`/do-tasks` passes its arguments straight through:

| `/do-tasks` invocation | Behavior here                                                                     |
| ---------------------- | --------------------------------------------------------------------------------- |
| `/do-tasks`            | default: select and process the single highest-ranked dependency-ready task       |
| `/do-tasks <slug>`     | process that specific task; if it is still blocked, stop and report every blocker |
| `/do-tasks --all`      | select all dependency-ready tasks, dispatch up to the WIP limit, hold the rest    |
| `/do-tasks -n N`       | like `--all`, but cap the **selected** batch at `N` before applying the WIP limit |
| `--remote` (default)   | remote dispatch (the "Dispatch remote agents" section)                            |
| `--local`              | the "Local mode" section (caps the batch at 1)                                    |

## Claim protocol (branch-name-independent)

The claim is the loop's distributed lock: it must let the first agent win and make
every later agent **observe that claim and bail**. The lock is an **open draft PR
labeled `task-claim` that names the slug** — _not_ the first push of `task/<slug>`.

This matters because the old lock (first `git push -u origin task/<slug>` wins) only
works when the agent can choose its branch name. **Branch-pinned environments**
(e.g. Claude Code on the web hands each session a fixed `claude/<session>` branch and
forbids pushing elsewhere) can never create `task/<slug>`, so two such sessions both
scan a fresh `main`, both still see the task as `ready`, and both proceed — the
`status: in_progress` flip is invisible because it lives on the loser's unmerged
branch. A claim marker that lives in the GitHub API (an open PR) is visible from a
fresh clone of `main` without inspecting any competitor's private branch, and does
not depend on the claimer controlling the branch name.

Every claim path below (remote dispatch, `--local`, and the size-gate **reserve**)
uses these exact steps:

1. **Pre-claim check.** Query open claim/loop/blocked PRs and bail if this slug is
   already claimed or parked as blocked. Pass `--limit 100` so an active repo's open
   PRs aren't truncated past the default 30 (a missed marker would let a second agent
   double-claim):

   ```bash
   gh pr list --state open --label task-claim   --limit 100 --json number,headRefName,body
   gh pr list --state open --label task-loop     --limit 100 --json number,headRefName,body
   gh pr list --state open --label task-blocked  --limit 100 --json number,headRefName,body
   ```

   A PR claims `<slug>` when one of its body lines is **exactly** `Claims-task: <slug>`
   **or** its `headRefName` is `task/<slug>`. Match the **whole line**, not a substring
   (`grep -Fxq` on trimmed body lines, or `^Claims-task: <slug>$`) — a substring test
   would let slug `task_1` falsely match a `Claims-task: task_13` line and skip a claim
   it shouldn't. If any open PR (any of the three labels) claims it, **STOP** — report
   `already claimed by PR #<n>` (or `blocked — see PR #<n>` for a `task-blocked` match)
   and move to the next candidate. A `task-blocked` match needs a human to resolve the
   block, so never auto-re-claim it.

   Also probe for an **in-flight branch with no PR yet** — a `task/<slug>` pushed by a
   session that has not yet opened its claim PR (or whose PR was closed leaving the
   branch behind). This is the cheapest cross-session signal and gives a clear early
   skip instead of a generic push rejection at Acquire:

   ```bash
   git ls-remote --heads origin "task/<slug>"
   ```

   A non-empty result → **STOP** and report `remote branch task/<slug> already exists`
   (move to the next candidate). In a branch-pinned environment that cannot create
   `task/<slug>`, this probe simply returns nothing and the PR-marker checks above carry
   the lock, as before.

2. **Acquire.** Switch to a work branch — prefer `git checkout -b task/<slug>`; in a
   branch-pinned environment where you cannot push a new branch, **stay on the current
   session branch** (the branch name no longer carries the lock). Flip the task file
   `status: ready → in_progress`, commit, and push to the branch you are on:

   ```bash
   git checkout -b task/<slug>   # or: stay on the pinned session branch
   # edit dev_docs/tasks/<path>/<slug>.md: status: ready -> status: in_progress
   git add <task file>
   git commit -m 'claim task: <slug>'
   git push -u origin <branch>
   ```

   If pushing `task/<slug>` is rejected because the ref already exists, another agent
   acquired it first — **STOP** (this remains a cheap early-out where branches _are_
   settable; it is no longer the only collision point).

3. **Open the claim marker.** Open a **draft** PR labeled `task-claim` carrying the
   slug marker. Use `task-claim` (not `task-loop`) so the in-flight claim does not show
   up in the `needs_review` column, which `/list-tasks` derives from open `task-loop`
   PRs:

   ```bash
   gh label create task-claim --description 'task-loop claim marker (in progress)' --color 'FBCA04' 2>/dev/null || true
   gh pr create --draft --label task-claim --title 'chore(task): <title> [claim]' --body 'Claiming task <slug> for execution. Will be filled in and marked ready for review when the work is done.

   Claims-task: <slug>'
   ```

4. **Reconcile (close the residual race window).** Two sessions on _different_ branches
   can both pass step 1 concurrently and both open a draft PR. Re-run the step-1 query
   and, if more than one open PR claims this slug, the **lowest PR number wins**
   deterministically. If yours is not the lowest, `gh pr close <your-pr>`, undo the
   status flip (change `status: in_progress` back to `ready`, commit, and push so no
   stale `in_progress` file is left on your branch), and **STOP**. (When both sessions
   share one fixed branch, the second
   `gh pr create` is rejected outright — GitHub allows one open PR per head→base pair —
   so the second bails here without a tie to break.)

A claim that succeeds leaves the task file at `status: in_progress` and one open draft
`task-claim` PR. **Finishing** the work (see the dispatch/local steps) converts that
same PR into the review PR: delete the task file, push, then relabel it
`task-loop`, replace the placeholder body with the real summary — **keeping the
`Claims-task: <slug>` marker line in the new body** — and `gh pr ready` it. Retaining
the marker matters because the task file still shows `status: ready` on `main` until
the PR merges; in a branch-pinned env the review PR's head branch is not `task/<slug>`,
so the marker is the only thing that lets the pre-claim check recognize the in-review
task and stops a second session from re-claiming and redoing it. Until merge, a
`--no-claim` resume finds the claim by its open `task-claim` PR and checks out that
PR's `headRefName`.

## 1. Scan for tasks

Run the deterministic scanner — it is the single executable implementation of the scan → parse → classify → **readiness** → **rank** procedure (the canonical **Ranking** and multi-blocker readiness rules live in `skills/task/SKILL.md`), so this path no longer re-derives that arithmetic by hand:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/task-scan.py" "$(git rev-parse --show-toplevel)/dev_docs/tasks"
```

It emits one JSON document: `cards` grouped by status, each card carrying a computed `rank` (within its status group), `dependency_ready` + `unresolved_blockers`, and an `expired` flag, plus epic rollups. The script takes the task dir as an **argument** (not a hardcoded root), skips `type: epic` files (epic rollups, not task cards), skips `_archive/` (where `/archive-tasks` parks stale `done` files — see `commands/handlers/repo-pr-archive.md`) and files with no frontmatter, and **fails closed** (non-zero exit) on malformed frontmatter — treat a non-zero exit as a hard stop, not an empty scan.

Select from `cards.ready`: a card is eligible only when its `dependency_ready` is true **and** its `priority` is not `urgent` — the scanner ranks `urgent` first (for `/list-tasks` display), but urgent is human-only, so the auto-execute path must skip it explicitly. The script resolves every `is_blocked_by` slug — a single string or a list — against `dev_docs/tasks/**/*.md`, marking it satisfied when the target file is absent or `status: done`, and lists any still-active blockers in `unresolved_blockers`. Among eligible cards, the one with the lowest `rank` wins; the rank encodes priority tier (`urgent` > `high` > `medium` > `low`) → value/effort `impact/size` descending (no-`impact`/invalid-`size` cards last within tier, never dropped) → oldest `created` first.

If no ready, dependency-ready tasks exist, report that and stop. Hint the user to run `/promote-tasks` if there are cards sitting in `new` or `needs_refinement`. If the only remaining ready tasks are waiting on dependencies, name each one's `unresolved_blockers` (e.g. `waiting on b, c`).

## 2. Select tasks to process

- Default: pick the single highest-ranked dependency-ready task
- With `<slug>`: find that specific task; if it is waiting on `is_blocked_by`, stop and report every unresolved blocker instead of dispatching it
- With `--all`: select all dependency-ready tasks and skip any that are still waiting on another task. The WIP limit in step 4 then caps how many of these are actually dispatched.
- With `-n N`: like `--all`, but after ranking keep only the top `N` before the WIP limit applies. The effective batch is `min(N, wip_limit - current_wip)`. Report any selected task you did not dispatch, distinguishing `held (-n N ceiling)` from `held (WIP limit reached)`.

## 3. Check dispatch prerequisites

Before dispatching, verify GitHub access:

```bash
gh auth status 2>&1
```

If this fails (token invalid, TLS errors, network issues), **stop** and tell the user — both remote and local modes call `gh pr create`, so a broken `gh` blocks both. If the error mentions TLS/x509/certificate, note it's likely Claude Code's sandbox blocking keychain access and suggest re-running outside sandbox mode.

## 4. Dispatch remote agents

**WIP limit (batch only — `--all` / `-n N`).** Before dispatching a batch, bound it by the kanban WIP limit so you don't flood the human PR-review bottleneck (the `needs_review` column). Single-task mode (`/do-tasks` / `/do-tasks <slug>`) is **not** gated — skip this paragraph there.

1. Resolve `wip_limit` from `dev_docs/tasks/.task-config.yml` (the repo-pr handler config). Default to `3` if the key is absent.
2. Count current WIP = the number of **distinct in-flight tasks**, deduped by slug
   across three sources: open `task-claim` PRs (claimed, work underway), open
   `task-loop` PRs (finished, in review), and task files with `status: in_progress` in
   the current checkout:

   ```bash
   gh pr list --label task-claim --state open --limit 100 --json number,headRefName,body
   gh pr list --label task-loop  --state open --limit 100 --json number,headRefName,body
   ```

   **Dedupe by slug** — a task mid-finish can momentarily appear as both an
   `in_progress` file and a `task-claim`/`task-loop` PR; count each slug once.
   Counting open `task-claim` PRs matters because a claim's `in_progress` flip lives on
   an unmerged branch and is **invisible to a fresh-clone batch scan**, so the open
   `task-claim` PR — not the `in_progress` file — is the reliable in-flight signal for
   work claimed by other sessions. If the `gh pr list` queries fail (API error or rate
   limit — step 3 has already confirmed `gh` is installed and authenticated), count only
   the `in_progress` files and note in the report that the count may undercount open PRs
   (so the effective cap is looser than intended).
3. Dispatch only the top `wip_limit - current_wip` selected tasks, highest-ranked first (priority, then value/effort score, then age — as sorted in step 1). For `-n N` the batch is `min(N, wip_limit - current_wip)`. If that slack is `0` or negative, dispatch nothing and report `WIP limit <wip_limit> reached (<current_wip> in flight) — nothing dispatched`.
4. Report every task you did **not** dispatch as `held (WIP limit N reached)` or `held (-n N ceiling)`, listed under the dispatched ones in step 5.

**Size-gate auto-routing (batch only — `--all` / `-n N`).** The intended operating
model is **both**: drain small tasks autonomously and reserve bigger ones for a
human. After the WIP cap fixes the dispatch set above, split it by `size` so the
batch self-routes instead of being hand-sorted. Single-task mode (`/do-tasks` /
`/do-tasks <slug>`) is **not** size-gated — an explicit pick is an explicit
instruction to do it in full.

1. Resolve `auto_execute_max_size` from `dev_docs/tasks/.task-config.yml` (the
   repo-pr handler config). Default to `2` if the key is absent (auto-do size
   `1`–`2`; reserve `3`+).
2. For each task in the WIP-bounded dispatch set, route by its `size`:
   - `size <= auto_execute_max_size` → **execute**: dispatch normally (the claim +
     execute remote session below, or the in-session run under `--local`).
   - `size > auto_execute_max_size` → **reserve** (`--claim-only` semantics from
     `/do-tasks`): run the **Claim protocol** above (pre-claim check → acquire →
     open the draft `task-claim` PR → reconcile) and **stop there** — do **not**
     execute, delete the file, or convert the PR to a `task-loop` review PR. The open
     draft `task-claim` PR is the reservation marker; leave the file `in_progress` for
     a human to resume (e.g. with `/do-tasks <slug> --no-claim`, which finds the claim
     by its open `task-claim` PR).
3. A reserved task still consumed a WIP slot when it was selected, so it counts
   against the cap exactly as an executed one does — the gate routes the batch, it
   does not enlarge it.
4. **Explicit flags override the gate.** A `--claim-only` run reserves every
   selected task regardless of size; a `--no-claim` run executes the named
   already-claimed task regardless of size. The size split applies only to the
   default (atomic claim + execute) batch path.

Report the two groups distinctly in step 5: executed/dispatched vs.
`reserved for human (size N > auto_execute_max_size)`.

For each selected task routed to **execute**, read its full content (frontmatter + body), then dispatch a remote session.

The remote session prompt must be self-contained because the remote VM won't have this plugin installed. Include the task content and all processing instructions inline.

**Important:** Do NOT pass `--print` to `claude --remote` — it is not supported.

```bash
claude --remote "You are processing a task for the task plugin system.

## The task file

The file is at dev_docs/tasks/<slug>.md with this content:

<paste full task file content>

## Instructions

1. CLAIM: The claim lock is an open draft PR labeled 'task-claim' that names the slug — NOT the branch push. Do all four sub-steps:

   a. PRE-CLAIM CHECK — bail if the slug is already claimed or parked as blocked. Pass --limit 100 so open PRs are not truncated past the default 30:
      gh pr list --state open --label task-claim   --limit 100 --json number,headRefName,body
      gh pr list --state open --label task-loop     --limit 100 --json number,headRefName,body
      gh pr list --state open --label task-blocked  --limit 100 --json number,headRefName,body
      A PR claims this slug if one of its body lines is EXACTLY 'Claims-task: <slug>' (match the whole line, e.g. grep -Fxq on trimmed lines or '^Claims-task: <slug>$' — NOT a substring, so task_1 does not match a 'Claims-task: task_13' line) OR its headRefName is 'task/<slug>'. If any open PR (any of the three labels) claims it, STOP — another agent has it, or it is blocked awaiting a human (never auto-re-claim a task-blocked match).

   b. ACQUIRE — switch to a work branch and flip the status. Prefer a dedicated branch; if this environment pins you to a fixed branch and forbids pushing a new one, stay on the current branch (the branch name does NOT carry the lock):
      git checkout -b task/<slug>   # or stay on the pinned branch
      # Edit dev_docs/tasks/<slug>.md: change status: ready -> status: in_progress
      git add dev_docs/tasks/<slug>.md
      git commit -m 'claim task: <slug>'
      git push -u origin <branch>
      If pushing task/<slug> is rejected because the ref exists, STOP — another agent acquired it.

   c. OPEN THE CLAIM MARKER — a draft PR labeled task-claim carrying the slug marker (task-claim, not task-loop, so the in-flight claim is not counted as needs_review):
      gh label create task-claim --description 'task-loop claim marker (in progress)' --color 'FBCA04' 2>/dev/null || true
      gh pr create --draft --label task-claim --title 'chore(task): <title> [claim]' --body 'Claiming task <slug> for execution. Filled in and marked ready when the work is done.

      Claims-task: <slug>'

   d. RECONCILE — re-run the step-(a) query. If more than one open PR claims this slug, the LOWEST PR number wins. If yours is not the lowest, run 'gh pr close <your-pr>', undo the status flip in the task file (change status: in_progress back to ready, commit, and push so no stale in_progress file is left on your branch), and STOP. (If two agents shared one fixed branch, the second gh pr create was already rejected — one open PR per head/base pair.)

2. EXECUTE: Read the Context and Task sections. Read all files listed in related_files. If `is_blocked_by` is present, treat it as already satisfied before proceeding; if the blocking task file still exists in the checkout (and is not in status: done) for any reason, STOP rather than doing work out of order. Do the work described in the Task section.

3. VALIDATE: Look for test infrastructure (Makefile, justfile, package.json). Run tests if available. Check acceptance criteria.

4. DELETE the task file (the merged PR is the 'done' signal; no need to flip status: done in the file):
   git rm dev_docs/tasks/<slug>.md

5. COMMIT and PUSH:
   git add only the files you changed (do NOT use git add -A — it may pick up untracked artifacts)
   git commit -m 'chore(task): <title from frontmatter>

   Automated follow-up from task <slug>.md.
   Source branch: <source_branch>'
   git push

6. FINISH THE PR: Convert the draft claim PR opened in step 1 into the review PR — do NOT open a second PR. Relabel it from task-claim to task-loop (so /list-tasks shows it under needs_review), replace the placeholder body with the real summary (KEEP the 'Claims-task: <slug>' marker line in the new body — the task file still shows status: ready on main until this PR merges, and in a branch-pinned env the head branch is not task/<slug>, so this marker is the only thing that lets another session's pre-claim check see the task is already in review and not redo it), and mark it ready for review. <pr> is the claim PR number from step 1c:
   gh label create task-loop --description 'Auto-generated from task-loop' --color '0E8A16' 2>/dev/null || true
   gh pr edit <pr> --add-label task-loop --remove-label task-claim --title 'chore(task): <title>' --body '## Summary

   Automated follow-up from task <slug>.md, created during branch <source_branch>.

   <bulleted list of what you did>

   ## Original Context

   > <quote the Context section from the task>

   ## Test Plan

   - [ ] Tests pass
   - [ ] No new lint errors
   - [ ] Acceptance criteria met

   ---
   Source task: dev_docs/tasks/<slug>.md (deleted in this PR)

   Claims-task: <slug>'
   gh pr ready <pr>

## If you cannot complete the task

1. Edit the task: change status to 'blocked'
2. Add a '## Consumer Notes' section explaining what you tried and what went wrong
3. Commit and push
4. RELABEL the draft claim PR opened in step 1 from task-claim to task-blocked ('gh label create task-blocked --description "task-loop blocked, needs a human" --color "B60205" 2>/dev/null || true; gh pr edit <pr> --add-label task-blocked --remove-label task-claim') and leave it OPEN. Do NOT close it and do NOT convert it to a task-loop review PR.
   Why not close it: the 'status: blocked' flip lives only on this unmerged branch — main still shows the task as 'ready'. If you closed the PR, the next scanner would see 'ready' with no open claim and re-claim the same failing task in a loop. Keeping the PR open under task-blocked makes the block visible on GitHub, preserves the Consumer Notes, and the pre-claim check skips task-blocked PRs so nothing re-claims it. A human resolves the block (push a fix, or close the task-blocked PR to release it) before it runs again.

## After opening the PR

The PR is the 'needs_review' signal. The task file has been deleted in this PR, so it no longer appears in /list-tasks under in_progress. The merged PR is the implicit 'done' transition."
```

When dispatching multiple tasks (`--all` / `-n N`), run the `claude --remote` commands in sequence (not background) so the user can see each session ID. Each remote session runs independently in its own cloud VM, and claims and executes **exactly one** task — never instruct a single agent to claim or work multiple tasks.

## 5. Report

For each dispatched task, tell the user:

- The slug and title
- That a remote session has been started
- They can monitor all sessions with `/tasks`

Example output:

```
Dispatched 3 tasks to remote agents:
  - remove-stale-alias (low) — remote session started
  - fix-broken-import (medium) — remote session started
  - add-missing-parser-test (high) — remote session started

Monitor with /tasks. Each will open a PR when complete.
```

If any tasks were **reserved for a human** by the size gate (the Size-gate auto-routing block above), list them as their own group — e.g. `reserved for human (size 5 > auto_execute_max_size 2): <slug>` — so the user knows they were claimed but not executed and can resume them with `/do-tasks <slug> --no-claim`.

If any tasks were skipped because they are waiting on another task, list them separately with every unresolved blocker slug. If any dependency-ready tasks were **held by the WIP limit** or the `-n N` ceiling (step 4), list those too — e.g. `held (WIP limit 3 reached): <slug> (high)` — so the user knows they are eligible and will dispatch on the next `--all` once in-flight work clears.

## Local mode (`--local`)

When `--local` is specified or `claude --remote` is unavailable, process the task directly in the current session (`gh` is still required for the claim/review PRs). `--local` caps the batch at **1** — it processes the single highest-ranked task and reports the rest as held; `/do-tasks <slug> --local` still runs the named slug.

`--local` is the path most likely to run in a **branch-pinned environment** (Claude
Code on the web pins the session to a fixed `claude/<session>` branch and forbids
pushing elsewhere — this is exactly where the old branch-name lock failed). So the
claim here is the **Claim protocol** above, whose lock is the draft `task-claim` PR,
not the branch: it works whether or not you can create `task/<slug>`.

The size gate still applies to the batch path: `/do-tasks --all --local` with a highest-ranked task whose `size > auto_execute_max_size` **reserves** it (`--claim-only` semantics — claim only, no execution) and reports it as reserved rather than executing it. A named `/do-tasks <slug> --local` is single-task mode and is never size-gated.

1. **Claim** via the Claim protocol (pre-claim check → acquire → draft `task-claim` PR → reconcile). For acquire, prefer `git checkout -b task/<slug>` from the current HEAD; in a branch-pinned session that cannot push a new branch, stay on the pinned branch — the draft PR still carries the lock. If the pre-claim check or reconcile shows the slug already claimed, STOP and report it.
2. Route by the size gate (batch path only): if `size <= auto_execute_max_size`, execute, validate, delete the file, commit, push, and **finish the PR** (relabel `task-claim`→`task-loop`, fill the body **keeping the `Claims-task: <slug>` marker line**, `gh pr ready`); if `size > auto_execute_max_size`, **reserve** it (stop after the claim — leave the draft `task-claim` PR open). A named `/do-tasks <slug> --local` is single-task mode and always executes in full.
3. Return to the original branch with `git checkout -` (only if you created `task/<slug>`; in a branch-pinned session you stayed put).

There is no remote session in this mode — report the PR opened in-session instead.

This is useful for testing or when cloud sessions aren't available.
