---
description: Execute ready tasks — the unified, handler-dispatched verb for turning ready tasks into PRs
allowed-tools: Bash(git *), Bash(gh *), Bash(claude *), Bash(find *), Bash(grep *), Bash(cat *), Bash(python3 *), Glob, Grep, Read, Write, Edit, AskUserQuestion, Agent, mcp__linear, mcp__claude_ai_Linear, mcp__atlassian, mcp__claude_ai_Atlassian
argument-hint: "[slug | --all | -n N] [--remote|--local] [--claim-only|--no-claim] [--project X]"
---

# Do Tasks

The single verb for executing captured tasks. It resolves the **handler** from
`dev_docs/tasks/.task-config.yml` (absent → `repo-pr`) and dispatches the same
way `/add-task` and `/list-tasks` do, then turns dependency-ready tasks into PRs.

The per-handler mechanics live in handler reference files this command
**references rather than re-specifies**, so the two cannot drift:
`commands/handlers/repo-pr-execute.md` (file path) and
`commands/handlers/linear-claim.md` (tracker path).

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/`
> directory exists, run the **Legacy migration** prompt from
> `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

## Modes

- `/do-tasks` — execute the single highest-ranked dependency-ready task
- `/do-tasks <slug>` — execute a specific task
- `/do-tasks --all` — execute dependency-ready tasks up to the WIP limit, holding the overflow
- `/do-tasks -n N` — dispatch up to `N` tasks, **each to its own session (one task per session)**, bounded by the WIP limit
- `/do-tasks --remote` / `/do-tasks --local` — choose where execution runs (default: remote dispatch)
- `/do-tasks --claim-only` — run only the claim step (reserve the task); no execution, no PR
- `/do-tasks --no-claim` — skip the claim step and execute a task this caller already claimed
- `/do-tasks --project <name|id|unassigned|any>` — **tracker handler only**: pin which scope to claim from, skipping the scope prompt. `any` ranks across all projects (per-project caps); `unassigned` claims from the Unassigned bucket; a name/id picks one project (a live project not in config triggers an offer to add it). See section 3.

**Scope of `--all` / `-n N`.** Batch _execution_ is meaningful only for **remote**
dispatch (each task gets its own cloud VM). Foreground pairing is inherently
single, so `--local` caps the **batch** (`--all` / `-n N`) at **1** — it processes
the single highest-ranked task and reports the rest as held. `/do-tasks <slug>
--local` still runs the named slug. (Batch _claiming_ is the one exception —
`--claim-only` reserves without executing, so it batches regardless; see the
Claim / execute split below.)

For the **tracker** (`linear`) handler, execution is single and foreground
(it runs in the current session): `--remote`/`--local` do not apply, and `--all` /
`-n N` degrades to a single claim with a one-line note (except with `--claim-only`,
which is batchable — see below). See section 3.

### Claim / execute split (`--claim-only`, `--no-claim`)

`/do-tasks` is atomic by default — it **claims** a task (reserve it, move it to
in-progress) and then **executes** it (do the work, open a PR) in one step. These
two flags expose the claim and execute halves as composable steps, so a claim now
plus a `--no-claim` execute later (by a different actor, or after a resume) add up
to one normal run. The flags are **mutually exclusive** — passing both is an
error: stop and ask which one was meant.

- **default** (neither flag) — atomic claim + execute, unchanged.
- **`--claim-only`** — run the claim step and **stop**: no execution, no review PR.
  - `repo-pr`: run the **Claim protocol** in
    `commands/handlers/repo-pr-execute.md` (pre-claim check → acquire the work branch
    and flip `status: ready → in_progress` → open the draft `task-claim` PR that names
    the slug → reconcile). The open draft `task-claim` PR is the reservation marker.
    Do **not** delete the file or convert it to a `task-loop` review PR. The claim lock
    is that PR, not the branch name, so this works in branch-pinned environments that
    cannot create `task/<slug>`.
  - `linear`: run "Pre-flight: is work already in flight?" then "Claim the issue"
    in `commands/handlers/linear-claim.md` (skip if a PR/branch already exists; else
    the token-comment lock — post a token-bearing claim comment first, set
    `started`-type state + `auto-claimed` + viewer assignee, then re-read the comments
    and elect the earliest state-backed claim as the winner), record the branch name,
    then stop before "Branch + execute" — `--claim-only` reserves the card **without**
    judging feasibility (the judge now runs after the claim, which `--claim-only` skips).
  - `gh-issue`: run through "Claim the issue" in
    `commands/handlers/gh-issue-claim.md` (pre-claim WIP gate → find candidates →
    pre-flight → judge → acquire the atomic `task/<n>` claim lock → assign `@me`, add
    `auto-claimed`, remove `auto-eligible`), then stop before "Branch + execute". The
    created `task/<n>` lock ref plus the assigned `auto-claimed` issue is the
    reservation marker — no PR.
  - `jira`: run through "Claim the issue" in `commands/handlers/jira-claim.md`
    (pre-claim WIP gate → find candidates → pre-flight → judge → acquire the atomic
    `task/<KEY>` claim lock → self-assign + transition to an In-Progress status), then
    stop before "Branch + execute". The created `task/<KEY>` lock ref plus the assigned,
    In-Progress issue is the reservation marker — no PR.

  On `gh-issue` and `jira` the lock is the **atomic creation of that ref** (GitHub's
  create-ref API, which returns 422 rather than updating an existing ref), not the
  assignee — so two sessions
  authenticated as the **same** account cannot both win (see
  `commands/handlers/claim-lock.md`, which also carries the comment-token election
  these handlers degrade to in a branch-pinned environment that cannot push
  `task/<KEY>`).
- **`--no-claim`** — skip the claim step and execute a task this caller has
  **already** claimed. **Requires an explicit `<slug>`/`<identifier>`** — there is
  no default selection, since the target is a specific already-claimed task, not
  the highest-ranked ready one. Guard: proceed only when that task is already
  claimed by this caller — `status: in_progress` (`repo-pr`), assigned to the
  caller in a `started`-type state (`linear`), assigned to the caller with
  `auto-claimed` (`gh-issue`), or assigned to the caller in an `indeterminate`
  (In Progress) category status (`jira`). Otherwise **stop and explain** —
  executing an unclaimed task reopens the race the claim step closes. When the
  guard passes, **first check out the existing claim branch** — do **not** branch
  fresh from the current `HEAD`, which is usually the base branch:
  - `repo-pr`: find the open draft `task-claim` PR that names this slug and check out
    its `headRefName` (`gh pr list`/`gh pr view` → headRefName; `git fetch` it if not
    present locally — the claim may have been pushed to `task/<slug>` or, in a
    branch-pinned session, to that session's branch). Then do the work, validate,
    delete the file, commit, push, and **finish the PR** (relabel `task-claim` →
    `task-loop`, fill the body, `gh pr ready`) per `repo-pr-execute.md`.
  - `linear`: first run "Pre-flight: is work already in flight?" (per
    `linear-claim.md`) — if an open PR already exists for this issue, the work is
    already published, so stop and report it rather than re-executing. Otherwise
    check out Linear's verbatim `branchName`, do the work, open the PR, and "Move to
    review on PR open" — without re-claiming.
  - `gh-issue`: check out the handler's deterministic claim branch `task/<n>`
    (`git fetch origin && git switch task/<n>`), which the claim pushed as its lock;
    create it (`git switch -c task/<n> origin/<base>`) only when the claim ran on the
    degraded comment-election path, which pushes no ref. Then do the
    work, open the PR, and "Move to review on PR open" (per `gh-issue-claim.md`) —
    without re-claiming.
  - `jira`: check out the handler's deterministic claim branch `task/<KEY>`
    (`git fetch origin && git switch task/<KEY>`), which the claim pushed as its lock;
    create it (`git switch -c task/<KEY> origin/<base>`) only when the claim ran on the
    degraded comment-election path, which pushes no ref. Then do the work, open the PR,
    and "Move to review on PR open" (per `jira-claim.md`) — without re-claiming.

**Batching.** `--claim-only` is the one execute-family action safe to batch — it
runs no foreground execution — so `--all` / `-n N --claim-only` may reserve several
dependency-ready tasks at once, bounded by the WIP gate (the file path's WIP cap or
the tracker pre-claim gate). `--no-claim` is always **single**: it resumes one
already-claimed task, so `--all` / `-n N` do not apply to it.

## 1. Resolve the handler

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

Overlay the local override on the committed config — mappings merge recursively, local leaf values win — then resolve `handler:` from the merged view.

- File absent, or `handler: repo-pr` → **file path** (section 2 below). Read and
  follow `commands/handlers/repo-pr-execute.md`.
- `handler: linear` → **tracker path** (section 3 below). Follow
  `commands/handlers/linear-claim.md` (with `commands/handlers/linear-common.md`
  for config/preflight/kanban mapping) for the full claim flow.
- `handler: gh-issue` → **gh-issue path** (section 4 below). Follow
  `commands/handlers/gh-issue-claim.md` for the full claim/execute flow
  (foreground single, current session).
- `handler: jira` → **jira path** (section 5 below). Follow
  `commands/handlers/jira-claim.md` for the full claim/execute flow
  (foreground single, current session).
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

If the relative paths don't resolve, find the handler files with **Glob**
(`**/commands/handlers/repo-pr-execute.md`, `**/commands/handlers/linear-claim.md`,
`**/commands/handlers/linear-common.md`, `**/commands/handlers/gh-issue-claim.md`,
`**/commands/handlers/jira-claim.md`) and Read the results.

## 2. File path (`repo-pr` / absent handler)

Read and follow **`commands/handlers/repo-pr-execute.md`** end to end — it holds
the scan, ranking, multi-blocker readiness, WIP cap, remote dispatch prompt,
`--local` mechanics, and report format. The arguments map straight through:

| `/do-tasks` invocation | Behavior (per `repo-pr-execute.md`)                                         |
| ---------------------- | --------------------------------------------------------------------------- |
| `/do-tasks`            | select and process the single highest-ranked dependency-ready task          |
| `/do-tasks <slug>`     | process that specific task; if still blocked, stop and report every blocker |
| `/do-tasks --all`      | select all dependency-ready, dispatch up to the WIP limit, hold the rest    |
| `/do-tasks -n N`       | like `--all`, capped at the top `N` before the WIP limit applies            |
| `--remote` (default)   | remote dispatch                                                             |
| `--local`              | local mode (caps the batch at 1)                                            |

### `-n N`

`-n N` is `--all` with an explicit ceiling. After ranking the dependency-ready
tasks (priority → value/effort score → age), keep the top `N`, then apply the WIP limit
(`wip_limit - current_wip`). The effective batch is `min(N, wip_limit - current_wip)`.
Report any selected task you did not dispatch — distinguishing `held (-n N ceiling)`
from `held (WIP limit reached)` — so the user knows why each was left behind.

**One task per session.** `N` bounds the number of **separate** single-task
sessions launched, **not** how many tasks any one session takes. Each selected
task is dispatched to its own remote session that claims and executes exactly that
one task (its own branch and VM) — never instruct a single agent to claim or work
multiple tasks. So `-n 3` means up to three independent agents each doing one task,
capped further by the WIP ceiling; it is the **total in-flight** that the limit
protects, regardless of how the batch is split.

### WIP cap and multi-blocker semantics

Both are defined in `repo-pr-execute.md`:

- **WIP cap** — resolve `wip_limit` from `.task-config.yml` (default `3`), count
  current WIP via `scripts/claim-scan.sh` (distinct in-flight tasks, deduped by slug
  across open `task-claim` PRs, open `task-loop` PRs, and `in_progress` files — see
  repo-pr-execute.md step 4.2), and dispatch at most `wip_limit - current_wip`.
  Single-task mode (`/do-tasks` / `/do-tasks <slug>`) is not gated.

  **Check the WIP slack first (batch only).** Before scanning/ranking, confirm
  `gh` auth (the WIP count itself calls `gh`), then compute
  `wip_limit - current_wip`. If it is `≤ 0`, report
  `WIP limit <wip_limit> reached (<current_wip> in flight) — nothing dispatched` and stop,
  skipping ranking and dispatch entirely (a light frontmatter scan is still needed
  to count `in_progress`; you need not enumerate held tasks — point the user at
  `/list-tasks`). Only with positive slack do you rank and dispatch the top
  `min(N, slack)`. Single-task mode is not gated and skips this guard.
- **Multi-blocker readiness** — a task is dependency-ready only when **every**
  `is_blocked_by` entry is satisfied (target absent or `done`); `is_blocked_by`
  may be a single slug or a list (`[a, b]`).
- **Size-gate auto-routing** — after ranking and the WIP gate, the batch
  (`--all` / `-n N`) self-routes by `size`: tasks with
  `size <= auto_execute_max_size` (repo-pr config key, default `2`) are claimed and
  executed, while bigger ones are **reserved** (`--claim-only` semantics — claimed
  but not executed) for a human to resume. The two groups are reported separately.
  Single-task mode (`/do-tasks` / `/do-tasks <slug>`) is **not** gated, and
  explicit `--claim-only` / `--no-claim` override the gate.

## 3. Tracker path (`linear` handler)

Read and follow **`commands/handlers/linear-claim.md`** (with
`commands/handlers/linear-common.md` for config/preflight/kanban mapping) end to
end — it holds the find-candidates query, the in-flight pre-flight, the
token-comment claim lock, the feasibility judgment (now run _after_ the claim),
the branch-name-verbatim rule, PR↔issue linking, move-to-review, the two-trigger
bail mechanics, and the report format. `/do-tasks` runs these
phases in the **current session**. If the relative paths don't resolve, find them
with **Glob** (`**/commands/handlers/linear-claim.md`,
`**/commands/handlers/linear-common.md`).

**Single by nature.** Tracker _execution_ is foreground, so `--remote`/`--local`
do not apply and `--all` / `-n N` is **not supported for execution** — a batch flag
on a normal (claim+execute) or `--no-claim` run degrades to a single claim/resume
with a one-line note ("batch isn't supported for tracker handlers; claiming one
issue"). The exception is `--claim-only`: reserving an issue runs no foreground
work, so `--all` / `-n N --claim-only` may claim several issues at once, bounded by
the pre-claim WIP gate below. `/do-tasks <identifier>` (a specific Linear id, e.g.
`PRE-12`) claims that one issue.

> **Runtime order (the sections below are not in execution order).** The tracker
> path runs: **Find candidates** (`linear-claim.md` "Find candidates" — documented
> under "Claim and execute" step 1 below) → **Resolve claim scope** → **Pre-claim WIP
> gate** → claim/execute. The two gate sections are documented first for reference,
> but Find candidates' queries run before both. The one exception is a **specific
> pin** (`--project <name|id|unassigned>` or a project typed into the prompt's
> "Other"): it is resolved **before** Find candidates and scopes that query — see
> `linear-common.md` "Resolve claim scope" step 1.

### Resolve claim scope (which project to claim from)

Unless the user pins a scope, `/do-tasks` **asks which project to claim from** when
ready work spans more than one — the claim-side mirror of `/add-task`'s project
prompt. Run the **"Resolve claim scope"** step in `linear-common.md`; for the no-flag
and `--project any` paths it runs **after** `linear-claim.md` "Find candidates" has
ranked the scope-tagged candidates (reading those tags — **no** extra `list_issues`
calls) and **before** the WIP gate below:

- `--project <name|id|unassigned>` → **specific pin**, resolved up front; it scopes
  the candidate query and the WIP count to that one scope (a live unconfigured pin
  gets queried directly, not via the Unassigned pass). `--project any` → Any, no prompt.
- No flag, **2+ scopes have ready work, interactive** → prompt (≤2 projects with work
  incl. Unassigned + Any + Other, within the 4-option max). **Exactly 1 scope with
  work** → use it, no prompt. **0 scopes with work** → nothing to claim.
- No flag, **non-interactive** (headless `/loop`/cron) → **Any** (ranked across all,
  per-project caps) — never blocks on a prompt.

The step narrows the candidate list to the chosen scope(s) and may offer to persist a
newly-picked unconfigured project to `.task-config.yml` (interactive only). The WIP
gate then counts only the chosen scope(s). **Any** hands the full scope list to the
gate, so it never collapses to a whole-team aggregate — each candidate is checked
against its own project cap.

### Pre-claim WIP gate (per-project + optional global ceiling)

WIP is enforced **per configured project** (plus the Unassigned bucket), with an
optional **global ceiling** across all of them. Resolve the caps and counts once, then
check them per candidate. After the preflight resolves the team and workflow states and
the claim scope is resolved (above), **before** judging feasibility or claiming:

1. **Resolve caps.** Take the **chosen scope(s)** from "Resolve claim scope" above — one
   project, the Unassigned bucket, or (for **Any**/non-interactive) the full resolved
   list, each carrying its own `wip_limit` (per-project override else the top-level
   `wip_limit`, default `3`; the Unassigned bucket uses `unassigned_wip_limit`). Also read
   the optional `linear.global_wip_limit` (absent → no global ceiling). The global ceiling
   always applies regardless of the chosen scope.
2. **Count in-flight per project.** Count only what the chosen scope needs (the Linear MCP
   is token-expensive): a **single real project** pick needs just that project's count; the
   **Unassigned bucket** or **Any** needs every configured project's count (the subtraction
   below sums them). For each such **configured** scope (real project `id`, or the single
   whole-team scope when 0 projects are configured), count Linear issues in any
   `started`-type state (e.g. `In Progress`, `In Review`) via `<linear-mcp>__list_issues`
   — resolve by state **type**, not display name — passing the resolved `teamId` and the
   scope's `id` as the `projectId` argument (omit when `id` is `null` — the whole-team
   scope). That scope's
   **slack = `wip_limit − in_flight`**. The started-type issue is the canonical in-flight
   unit — an open PR is already reflected by its issue sitting in a started state, so do
   **not** add open PRs separately (that double-counts).

   **Whole-team count (once) → Unassigned and/or global total.** Run **one** whole-team
   `started` count (omit `projectId`) only when it is actually needed — i.e. when the
   **chosen scope is the Unassigned bucket or Any** (needs the subtraction below) **or**
   `linear.global_wip_limit` is set (needs the total). A **single real-project** pick
   with no global ceiling needs neither, so **skip the whole-team count** — the Linear
   MCP is token-expensive. These are two independent triggers:
   - **Unassigned subtraction** (only for an Unassigned/Any pick, where step 2 counted
     **every** configured project): `unassigned_in_flight = max(0, whole_team_in_flight −
     Σ(configured in_flight))` — clamped so a mid-count state transition can't drive it
     negative and hand back phantom slack. The bucket's **slack = `unassigned_wip_limit −
     unassigned_in_flight`** (a cap of `0` → slack ≤ 0, never claimed). The Unassigned
     bucket is **never** counted with a `projectId` filter — the MCP has no null-project
     value; subtraction is the only correct count. (Do **not** attempt this subtraction on
     a single-project pick — the other projects weren't counted, so `Σ(configured)` is
     incomplete.)
   - **Global ceiling total** (whenever `global_wip_limit` is set, any config): the total
     in-flight **is that same whole-team count**. Do **not** sum the per-scope counts for
     the ceiling (that would double-count, and once the Unassigned bucket exists is simply
     wrong).
3. **Global ceiling.** If `linear.global_wip_limit` is set and **total in-flight** (the
   whole-team count from step 2) **≥ `global_wip_limit`**, **no** project can claim —
   decline outright:
   `Global WIP limit <N> reached (<total> in flight across all projects) — no issue claimed`
   and stop.
4. **Per-project gate (ranked path).** Otherwise the per-project cap is checked **per
   candidate** in the claim loop below: for the chosen candidate, if **its project's**
   slack is `≤ 0`, that project is full → **skip to the next ranked candidate** (which may
   live in another project with slack) rather than declining the whole run. The skip note
   is `WIP limit <wip_limit> reached (<count> in flight) in project <name> — skipping to the next candidate`
   (render `<name>` as `the whole team` when the scope's `name` is `null`; the same
   convention applies to the direct-mode decline message in step 5).
   If every remaining candidate's project is full, report that no issue was claimed.
5. **Direct-identifier / single mode** (`/do-tasks <identifier>`): gate against **that
   issue's own resolved scope** cap — a configured project, or the **shared Unassigned
   bucket** when the issue is outside the configured projects (`linear-claim.md` step 7).
   The Unassigned bucket's in-flight is the subtraction from step 2 (already computed once
   the bucket exists); a configured project's is its per-project count. Also gate against
   the global ceiling. If either is at its limit, **stop** — no fall-through — declining
   with the global message (step 3) or, for the per-project cap,
   `WIP limit <wip_limit> reached (<count> in flight) in project <name> — no issue claimed`
   (render `<name>` as `Unassigned` for the bucket, `the whole team` for a `null` scope).

**`--all --claim-only` batch.** Reserve up to each scope's own slack independently:
effective batch = `Σ max(0, slack_p)` across **all resolved scopes** (configured projects
**and** the Unassigned bucket), no scope over its own cap. Claim **candidates in rank
order**, decrementing **both** the chosen scope's remaining slack **and** — when
`global_wip_limit` is set — the remaining global slack as you go; skip a candidate whose
scope's **remaining** slack is `0` (so `unassigned_wip_limit: 0` reserves nothing for
unassigned work), and stop the batch once the global slack hits `0`. The held-overflow
report names each held task's project (`Unassigned` for the bucket).

### Claim and execute

With positive WIP slack, run `commands/handlers/linear-claim.md` end to end:

1. **Preflight** — `linear-common.md` preflight (resolve team) + `linear-claim.md`
   "Find candidates" (resolve workflow states, query unstarted issues, filter by
   `estimate`/labels/assignee, rank). Also confirm `gh auth status`, a clean
   working tree, and fetch the base branch (`linear.base_branch`, default `main`).
2. **Pre-flight** — `linear-claim.md` "Pre-flight: is work already in flight?":
   on the **top-ranked candidate**, before claiming (no feasibility judgment yet —
   that runs after the claim), check for an existing open PR (by Linear's
   `branchName` and by `[<IDENTIFIER>]` title) and an existing remote branch, plus
   the `started`/`auto-claimed`/assigned-to-another gates. If in flight, skip with a
   clear message — ranked mode moves to the next candidate (re-run pre-flight on it);
   a direct `<identifier>` pick stops. This full gate runs on the paths that **begin**
   work (ranked, direct identifier, `--claim-only`); the `--no-claim` resume runs only
   the open-PR subset, since the issue's own branch/state/label are the caller's own
   claim markers (see that section's `--no-claim` note). **Also apply the per-project
   WIP gate here:** if the candidate's `project` has no **remaining** slack — its initial
   slack (per "Pre-claim WIP gate" above) minus any issues already claimed for that
   project in this run — treat it like an in-flight result: in ranked mode skip to the
   next candidate (whose project may have slack), on a direct pick stop. (The global
   ceiling, when set, is checked once up front and declines the whole run before the
   loop.)
3. **Claim** — `linear-claim.md` "Claim the issue": the **token-comment lock** —
   read-before-write guard, then post a token-bearing claim comment **first** (the
   lock), then set the `started`-type state + `auto-claimed` label (creating it if
   absent) + the viewer as `assignee` in one `save_issue`. After a jittered delay,
   re-read the comments and elect the winner = the earliest **eligible** claim
   comment. Eligibility has two filters: (a) a **live-window bound** — ignore any
   comment created before this session last saw the card unclaimed, so a stale
   orphan from a prior attempt can't win once a live racer's write flips the card to
   `started` (the load-bearing fix against deadlock); and (b) **state-backed** —
   ignore claims whose `save_issue` never landed. If you lost — or the post-election
   confirm read shows your markers were overwritten — delete your own claim comment
   and fall back to the next candidate.
4. **Judge feasibility** — `linear-claim.md` "Judge feasibility", run **while holding
   the claim**: read the full issue and decide whether this session can finish it
   without a human. If **feasible**, proceed to branch + execute. If **not**, this is
   a _feasibility reject_ on a card you already hold → run the **release-and-continue**
   bail (revert to backlog, swap `auto-claimed` → `human-approval-requested`, clear
   `assignee`, delete your claim comment, post the reason) and move to the **next
   ranked candidate**, re-running pre-flight → claim → judge. Do **not** halt — that
   is reserved for a mid-execution failure (step 8). (`--claim-only` stops before this
   step; it reserves without judging.)
5. **Branch + execute** — branch with Linear's **verbatim** `branchName` (never
   reconstruct it when the field is present), do the work, run the project's
   tests/lints (`just check` here).
6. **PR** — `gh pr create` with the Linear identifier in brackets in the title
   (`[PRE-12] …`) and `Closes <identifier>` on its own line in the body; post the
   PR URL as a Linear comment. **Close only issues this PR actually finishes, each
   as its own `Closes <identifier>` line** (more than one is fine when the PR
   genuinely completes several — one clearly-marked `Closes` per line). Any _other_
   Linear id that lands in the title or body — a blocker, a sibling phase task, a
   "follow-up" referenced in the task description prose — must be written so Linear
   will not sweep it to `Done` on merge: prefix it with a non-closing magic word
   (`related to`, `part of`, `towards`). A **bare** identifier — or a bare Linear
   URL, which embeds one — is treated as a closing link and the sibling issue gets
   auto-completed even though this PR did not do its work. See `linear-claim.md`
   "PR body magic words". With Linear's GitHub integration disabled, this magic
   word is inert for completion — completion is driven by the reconciler verbs
   (`/sweep-for-complete` / `/reconcile-tasks`), not by anything parsed from the
   PR body — but it stays because it documents which issue this PR finishes and
   re-enables cleanly if the integration is ever turned back on.
7. **Move to review** — `linear-claim.md` "Move to review on PR open": attach the
   PR via `links` and move to `In Review` if the team has one. **Never move the
   issue to a `completed`/`canceled` state** — completion belongs to the
   reconciler verbs (`/complete-task`, `/sweep-for-complete`, `/reconcile-tasks`),
   not to Linear's GitHub integration, which is disabled. This hard rule from
   `linear-claim.md` carries over unchanged.
8. **Bail (mid-execution → halt)** — if the work breaks _while building_ (after
   step 5 began), `linear-claim.md` "Bail": `git stash push -u` the WIP, remove
   `auto-claimed`, add `human-approval-requested`, revert the issue to the
   `backlog`-type state, clear `assignee`, delete your claim comment, and comment
   what tripped the bail. **Stop — do not auto-pick another candidate.** This is the
   load-bearing distinction from the step-4 feasibility reject: a reject that never
   built continues to the next candidate; a build that broke halts for a human.

## 4. gh-issue path (`gh-issue` handler)

Read and follow **`commands/handlers/gh-issue-claim.md`** end to end — it holds
the find-candidates query, the in-flight pre-flight, the feasibility judgment, the
atomic `task/<n>` claim lock (defined in `commands/handlers/claim-lock.md`), the work
branch, `gh pr create` with
`Closes #<n>`, the move-to-review label swap, bail mechanics, and the report format.
`/do-tasks` runs these phases in the **current session** over the `gh` CLI. If the
relative path doesn't resolve, find it with **Glob**
(`**/commands/handlers/gh-issue-claim.md`).

**Single by nature.** Like the tracker path, gh-issue execution is foreground:
`--remote`/`--local` do not apply, and `--all` / `-n N` degrades to a single claim
with a one-line note ("batch isn't supported for gh-issue execution; claiming one
issue") until the gh-issue batch task lands. The exception is `--claim-only`:
reserving an issue runs no foreground work, so `--all` / `-n N --claim-only` may
reserve several issues at once, bounded by the pre-claim WIP gate. `/do-tasks <#n>`
(a specific issue number) claims that one issue. The claim/execute split
(`--claim-only` / `--no-claim`) and the pre-claim WIP gate are now wired for
gh-issue — both are documented in `gh-issue-claim.md` ("Modes: atomic vs.
claim/execute split" and "Pre-claim WIP gate").

## 5. jira path (`jira` handler)

Read and follow **`commands/handlers/jira-claim.md`** end to end — it holds the
config read (`ready_status` is required here), the find-candidates JQL, the
in-flight pre-flight, the feasibility judgment, the atomic `task/<KEY>` claim lock
(defined in `commands/handlers/claim-lock.md`) plus the self-assign + transition board
marker, `gh pr create` with the
`[<KEY>]` title prefix, the move-to-review transition, bail mechanics, and the
report format. `/do-tasks` runs these phases in the **current session** over the
Atlassian MCP. If the relative path doesn't resolve, find it with **Glob**
(`**/commands/handlers/jira-claim.md`).

**Single by nature.** Like the tracker and gh-issue paths, jira execution is
foreground: `--remote`/`--local` do not apply, and `--all` / `-n N` degrades to a
single claim with a one-line note ("batch isn't supported for jira execution;
claiming one issue"). The exception is `--claim-only`: reserving an issue runs no
foreground work, so `--all` / `-n N --claim-only` may reserve several issues at once,
bounded by the pre-claim WIP gate. The claim/execute split (`--claim-only` /
`--no-claim`) and the pre-claim WIP gate are now wired for jira — both are documented
in `jira-claim.md` ("Modes: atomic vs. claim/execute split" and "Pre-claim WIP gate").
`/do-tasks <KEY>` (a specific issue key, e.g. `PLAT-142`) claims that one issue.

## 6. Report

For the **file path**, report per `repo-pr-execute.md` "Report":

- **remote** dispatch — list each dispatched task (slug, title, that a remote
  session started) and point the user at `/tasks` to monitor.
- **`--local`** — there is no remote session; report the PR opened in-session
  instead.

In both file-path modes, list separately any tasks skipped because they are
waiting on another task (with every unresolved blocker) or **held** by the `-n N`
ceiling or the WIP limit.

For the **tracker path**, report per `linear-claim.md` "Report": on success print
the issue identifier, the PR URL, and a one-line summary; on bail print the
identifier, why it bailed, and the Linear comment URL; on the WIP gate declining,
print the limit and the in-flight count.

For the **gh-issue path**, report per `gh-issue-claim.md` "Report": on success
print the issue number, the PR URL, and a one-line summary; on bail print the
issue number, why it bailed, and the issue-comment URL.

For the **jira path**, report per `jira-claim.md` "Report": on success print the
issue key, the PR URL, and a one-line summary; on bail print the issue key and
why it bailed (the bail comment is posted on the issue).
