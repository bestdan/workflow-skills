---
description: Execute ready tasks — the unified, handler-dispatched verb for turning ready tasks into PRs
allowed-tools: Bash(git *), Bash(gh *), Bash(claude *), Bash(find *), Bash(grep *), Bash(cat *), Bash(python3 *), Glob, Grep, Read, Write, Edit, AskUserQuestion, Agent, mcp__linear, mcp__claude_ai_Linear, mcp__atlassian, mcp__claude_ai_Atlassian
argument-hint: "[slug | --all | -n N] [--remote|--local] [--claim-only|--no-claim] [--project X] [--non-interactive]"
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
>
> Under `--non-interactive`, do **not** prompt and do **not** migrate: proceed as if the
> answer were **skip once**, and note the legacy directory in the report (point at
> `/doctor --fix`). Note this prompt is **prose**, not `AskUserQuestion` — so an
> unattended run does not stop at it, it _answers itself_ and starts moving files.
> Skipping is therefore the fail-safe branch here, and blocking every run over a
> directory rename would be the wrong one.

## Modes

- `/do-tasks` — execute the single highest-ranked dependency-ready task
- `/do-tasks <slug>` — execute a specific task
- `/do-tasks --all` — execute dependency-ready tasks up to the WIP limit, holding the overflow
- `/do-tasks -n N` — dispatch up to `N` tasks, **each to its own session (one task per session)**, bounded by the WIP limit
- `/do-tasks --remote` / `/do-tasks --local` — choose where execution runs (default: remote dispatch)
- `/do-tasks --claim-only` — run only the claim step (reserve the task); no execution, no PR
- `/do-tasks --no-claim` — skip the claim step and execute a task this caller already claimed
- `/do-tasks --project <name|id|unassigned|any>` — **`linear` only** (the other tracker handlers have no project dimension: `gh-issue` refuses the flag, see section 4; `jira` has no scope prompt): pin which scope to claim from, skipping the scope prompt. `any` ranks across all projects (per-project caps); `unassigned` claims from the Unassigned bucket; a name/id picks one project (a live project not in config triggers an offer to add it). See section 3.
- `/do-tasks --non-interactive` — declare that no human is present: **never prompt anywhere in this command**. Every decision that would otherwise ask takes a documented default — matching the same flag on `/co-review` and `/select-coder`, which is why the guarantee is global rather than a list of exceptions. All five prompt sites are covered: the scope prompt resolves to **Any** (section 3), the WIP gate declines instead of offering its override (`commands/handlers/attendedness.md`), the persist-unconfigured-project offer never fires (`linear-common.md`), the legacy-migration preflight skips with a note (above), and a `--claim-only`/`--no-claim` conflict is a hard error rather than a question. Pass it from any unattended caller — a cron, a wrapper script, or a dispatching session handing work to a remote worker.

**Scope of `--all` / `-n N`.** Batch _execution_ is meaningful only for **remote**
dispatch (each task gets its own cloud VM). Foreground pairing is inherently
single, so `--local` caps the **batch** (`--all` / `-n N`) at **1** — it processes
the single highest-ranked task and reports the rest as held. `/do-tasks <slug>
--local` still runs the named slug. (Batch _claiming_ is the one exception —
`--claim-only` reserves without executing, so it batches regardless; see the
Claim / execute split below.)

For the **tracker** handlers, the execution mode now splits by handler:

- **`linear`** supports **true batch execution**, and **`gh-issue`** supports it
  **on opt-in** (`gh-issue.remote_batch: true`; off by default, see section 4 —
  without it `--all` / `-n N` degrades to a single foreground claim). Where it
  runs, `--all` / `-n N` (without `--claim-only`) dispatches **one remote session
  per dependency-ready issue** (each its own cloud VM), bounded by WIP slack, via
  the **Tracker-batch subroutine** in section 3 — which `linear` runs directly and
  `gh-issue` instantiates in section 4. Bare `/do-tasks` stays single and
  foreground, and `--local` caps the batch at **1** (single highest-ranked issue,
  foreground).
- **`jira`** execution is still single and foreground (current session):
  `--remote`/`--local` do not apply, and `--all` / `-n N` degrades to a single
  claim with a one-line note. See section 5.

Across all three trackers, `--claim-only` batches regardless (it reserves without
executing — see the Claim / execute split below), and `/do-tasks <identifier>`
claims one specific issue.

### Claim / execute split (`--claim-only`, `--no-claim`)

`/do-tasks` is atomic by default — it **claims** a task (reserve it, move it to
in-progress) and then **executes** it (do the work, open a PR) in one step. These
two flags expose the claim and execute halves as composable steps, so a claim now
plus a `--no-claim` execute later (by a different actor, or after a resume) add up
to one normal run. The flags are **mutually exclusive** — passing both is an
error: stop and ask which one was meant (under `--non-interactive`, a hard error with
that message — never a question).

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
    pre-flight → judge → acquire the atomic `<branch_prefix>task-<n>` claim lock →
    assign `@me`, move the rung to `status:3_started`), then stop before "Branch +
    execute". The created lock ref plus the assigned, started issue is the
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
  `status:3_started` (`gh-issue`), or assigned to the caller in an `indeterminate`
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
  - `gh-issue`: check out the handler's deterministic claim branch
    `<branch_prefix>task-<n>`, which the claim created as its lock
    (`git fetch origin && git switch "<branch>"`); create it
    (`git switch -c "<branch>" origin/<base>`) only when the claim ran on the degraded
    comment-election path, which creates no ref. **Resolve `<branch>` with
    `python3 commands/handlers/assets/gh-issue-claim.py branch-name --issue <n> [--prefix "<branch_prefix>"]`,
    never by spelling it** — `gh-issue.branch_prefix` is per-repo, so a literal is right in one
    repo and wrong in the next. Then do the work, open the PR, and "Move to review on PR
    open" (per `gh-issue-claim.md`) — without re-claiming.
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

Resolve the handler from the **merged view** — the committed config overlaid with the optional local override (see `commands/task-config.md` → "Resolving the handler"):

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
  `commands/handlers/gh-issue-claim.md` for the full claim/execute flow —
  foreground single in the current session by default, or batch remote dispatch
  for `--all` / `-n N` (section 4, "gh-issue batch").
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
bail mechanics, and the report format. In **single** mode `/do-tasks` runs these
phases in the **current session**; in **batch** mode it runs them inside a
dispatched **remote session per issue** (see the Tracker-batch subroutine below).
If the relative paths don't resolve, find them with **Glob**
(`**/commands/handlers/linear-claim.md`,
`**/commands/handlers/linear-common.md`).

**Execution modes.** Linear `/do-tasks` is no longer single-only:

- `/do-tasks` / `/do-tasks <identifier>` (e.g. `PRE-12`) / `--no-claim` — **single**,
  foreground, current session. Bare `/do-tasks` selects the single highest-ranked
  dependency-ready issue; `<identifier>` claims that one issue.
- `/do-tasks --all` / `-n N` (without `--claim-only`) — **true batch execution**:
  dispatch up to `min(N, wip_slack)` **remote** single-claim sessions (one per
  dependency-ready issue, each its own cloud VM), via the **Tracker-batch
  subroutine** below. `--remote` is the default and the only batch mode; `--local`
  caps the batch at **1** (the single highest-ranked issue, run foreground).
- `/do-tasks --all` / `-n N --claim-only` — **batch claim** (reserve several issues),
  bounded by the pre-claim WIP gate below, no execution. Unchanged.

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

> **When a cap is met, who decides.** The counts below always run. What happens when
> one is met depends on the action: a **batch** declines or is bounded as specified
> here, always; a **single** claim in an attended session offers the user a
> one-keystroke override first. This applies to the per-project caps **and** to
> `global_wip_limit` — a deliberate absolute ceiling is still a cap a present human
> may knowingly exceed. See `commands/handlers/attendedness.md`, which owns the rule.

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
   `started`-type state (e.g. `In Progress`, `In Review`) **assigned to the current
   viewer** via `<linear-mcp>__list_issues` — resolve by state **type**, not display name
   — passing the resolved `teamId`, the viewer's id as the `assignee` argument, and the
   scope's `id` as the `projectId` argument (omit when `id` is `null` — the whole-team
   scope). That scope's
   **slack = `wip_limit − in_flight`**. The started-type issue is the canonical in-flight
   unit — an open PR is already reflected by its issue sitting in a started state, so do
   **not** add open PRs separately (that double-counts).

   **The `assignee` filter is required.** The gate bounds **this operator's** concurrent
   work, not the team's. A shared Linear project carries other people's started issues, and
   an unscoped count on such a project exceeds any plausible `wip_limit` permanently — the
   gate never opens and no issue is ever claimed. "Claim the issue" self-assigns to the
   viewer, so the filter is exact. The argument is `assignee` — a **positive single-value
   match**, which is exactly what this gate wants. (That is why the gate can push it
   server-side where "Find candidates" cannot: its predicate is "unassigned **or** me",
   which a single-value match can't express, so that gate stays client-side — see
   `linear-claim.md` floor step 4. `assigneeId` is a field `list_issues` **returns**, not
   an argument it accepts.) Use the cached viewer id already resolved by
   `linear-claim.md` "Find candidates" (or `meta.viewer.id` on the fast path); do not spend
   a second call on it. This applies to **every** count in this step, the all-projects count
   below included — mixing a viewer-scoped per-project count with an unscoped all-projects
   count would make the Unassigned subtraction nonsense.

   **All-projects count (once) → Unassigned and/or global total.** This one omits
   `projectId` — and **only** `projectId`. It is still viewer-scoped, so it is _this
   caller's_ work across every project, never the team's; the name says "all projects"
   rather than "whole team" for exactly that reason (a **whole-team scope**, elsewhere in
   this file, is a _project_ scope — `id: null` — and is a different thing). Run **one**
   such `started` count only when it is actually needed — i.e. when the
   **chosen scope is the Unassigned bucket or Any** (needs the subtraction below) **or**
   `linear.global_wip_limit` is set (needs the total). A **single real-project** pick
   with no global ceiling needs neither, so **skip the all-projects count** — the Linear
   MCP is token-expensive. These are two independent triggers:
   - **Unassigned subtraction** (only for an Unassigned/Any pick, where step 2 counted
     **every** configured project): `unassigned_in_flight = max(0, all_projects_in_flight −
     Σ(configured in_flight))` — clamped so a mid-count state transition can't drive it
     negative and hand back phantom slack. The bucket's **slack = `unassigned_wip_limit −
     unassigned_in_flight`** (a cap of `0` → slack ≤ 0, never claimed). The Unassigned
     bucket is **never** counted with a `projectId` filter — the MCP has no null-project
     value; subtraction is the only correct count. (Do **not** attempt this subtraction on
     a single-project pick — the other projects weren't counted, so `Σ(configured)` is
     incomplete.)
   - **Global ceiling total** (whenever `global_wip_limit` is set, any config): the total
     in-flight **is that same all-projects count**. Do **not** sum the per-scope counts for
     the ceiling (that would double-count, and once the Unassigned bucket exists is simply
     wrong).
3. **Global ceiling.** If `linear.global_wip_limit` is set and **total in-flight** (the
   all-projects count from step 2) **≥ `global_wip_limit`**, **no** project can claim.
   Apply the at-limit procedure in `commands/handlers/attendedness.md`; when it resolves
   to decline, decline outright:
   `Global WIP limit <N> reached (<total> of your issues in flight across all projects) — no issue claimed`
   and stop. When it resolves to override, continue to step 4 — the ceiling is lifted for
   this run only, and each project's own cap still applies below. **That override is
   _the_ override for this run** (`attendedness.md`, "once means once per run"): if step 4
   later finds every candidate's project full, it is already granted — claim, do not ask
   again.
4. **Per-project gate (ranked path).** Otherwise the per-project cap is checked **per
   candidate** in the claim loop below: for the chosen candidate, if **its project's**
   slack is `≤ 0`, that project is full → **skip to the next ranked candidate** (which may
   live in another project with slack) rather than declining the whole run. The skip note
   is `WIP limit <wip_limit> reached (<count> in flight) in project <name> — skipping to the next candidate`
   (render `<name>` as `the whole team` when the scope's `name` is `null`; the same
   convention applies to the direct-mode decline message in step 5).
   If every remaining candidate's project is full, apply the at-limit procedure in
   `commands/handlers/attendedness.md` **once, here** — not per candidate. Walking the
   ranked list is not an at-limit outcome, because another project may still have slack;
   the run only meets the limit once nothing is left to try. On override, claim the
   top-ranked candidate whose project was full. On decline, report that no issue was
   claimed. The once-per-run bound is `attendedness.md`'s, not this step's — so an
   override or a Stop already given in step 3 settles this too, and no second question is
   asked however many candidates were skipped.
5. **Direct-identifier / single mode** (`/do-tasks <identifier>`): gate against **that
   issue's own resolved scope** cap — a configured project, or the **shared Unassigned
   bucket** when the issue is outside the configured projects (`linear-claim.md` step 7).
   The Unassigned bucket's in-flight is the subtraction from step 2 (already computed once
   the bucket exists); a configured project's is its per-project count. Also gate against
   the global ceiling. If either is at its limit, apply the at-limit procedure in
   `commands/handlers/attendedness.md` — this is a **single** action, so an attended run
   gets the override. On override, claim the named issue. On decline, **stop** — no
   fall-through — with the global message (step 3) or, for the per-project cap,
   `WIP limit <wip_limit> reached (<count> in flight) in project <name> — no issue claimed`
   (render `<name>` as `Unassigned` for the bucket, `the whole team` for a `null` scope).
   Both caps at their limit is still **one** question, not two.

**`--all --claim-only` batch.** Reserve up to each scope's own slack independently:
effective batch = `Σ max(0, slack_p)` across **all resolved scopes** (configured projects
**and** the Unassigned bucket), no scope over its own cap. Claim **candidates in rank
order**, decrementing **both** the chosen scope's remaining slack **and** — when
`global_wip_limit` is set — the remaining global slack as you go; skip a candidate whose
scope's **remaining** slack is `0` (so `unassigned_wip_limit: 0` reserves nothing for
unassigned work), and stop the batch once the global slack hits `0`. The held-overflow
report names each held task's project (`Unassigned` for the bucket).

### Claim and execute (single mode)

This is the **single**, foreground path (`/do-tasks`, `/do-tasks <identifier>`,
`--no-claim`). Batch runs the same `linear-claim.md` flow, but once per issue
inside a remote session — see the **Tracker-batch subroutine** below.

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
   loop.) **Also verify dependency-readiness here** — the shared "Find candidates"
   gates do **not** check native blockers, so this is where single mode enforces it,
   using the **same rule as the Tracker-batch subroutine (step 3)**: read `get_issue`
   with `includeRelations: true` on the candidate and confirm every `blockedBy` issue
   is in a `completed`-type state (`Done`) or no longer exists — a `canceled` blocker
   does **not** satisfy it (matching `linear-reoptimize.md` Dimension 1). If any blocker
   is unresolved the candidate is **not** dependency-ready: in ranked mode skip it
   (`waiting on <identifier>`, move to the next candidate); on a direct `<identifier>`
   pick **stop** and report the unresolved blocker rather than claiming an issue whose
   dependencies aren't met. This keeps bare `/do-tasks` and `/do-tasks --all` from ever
   disagreeing on whether an issue is ready.
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

### Tracker-batch subroutine (reusable across linear / gh-issue / jira)

This is the tracker analogue of the repo-pr remote fan-out
(`repo-pr-execute.md` §4 "Dispatch remote agents"). It is written **handler-neutral**
so section 4's gh-issue batch path already instantiates it, and the jira batch
path (section 5) can do the same once its batch task lands — only the find/rank
phase (step 1), the dependency-readiness check (step 3), and the per-issue
claim+execute flow (step 4) differ by handler. It runs **only** for
`--all` / `-n N` **without** `--claim-only` **and without** `--no-claim` (bare
`/do-tasks` stays single foreground; `--claim-only` keeps its batch-claim behavior;
`--no-claim` is always single — it resumes one already-claimed issue, so
`--all --no-claim` is a contradiction: reject it with
`--no-claim is single-only; drop --all/-n N`; `--local` caps the batch at 1 and runs
the single highest-ranked issue foreground via "Claim and execute" above).

**Connector availability: fail safe at the remote end, not by a pre-check.** Batch
dispatch hands each issue's claim, comment, and state transitions to a remote cloud
session, which can only run them if that session has the handler's MCP connector —
unlike the repo-pr remote fan-out, which needs only `git`/`gh`. A remote session **may**
inherit the Linear connector but does **not** always, and the launching session has **no
deterministic way to introspect what `claude --remote` will inherit** — the tools loaded
here say nothing about the VM's environment. So do **not** gate dispatch on an
un-actionable "confirm the VM has MCP" pre-check. Instead, put the check **where the
capability is actually visible — inside the remote session** — via two concrete rules:

- **Make the remote prompt's first step a self-check.** The inlined prompt (step 4) must
  begin: "If the Linear MCP connector is not available in this session, do **not** claim —
  stop immediately and report `remote Linear MCP unavailable`." A misconfigured remote then
  degrades **loudly** (a visible bail on that issue) rather than silently doing nothing or
  half-claiming. Because the claim is the session's first mutation, a bail here leaves no
  partial state.
- **Optional deterministic opt-out.** Hosts that already know their remote VMs lack the
  connector can set `linear.remote_batch: false` in `.task-config.yml` to skip remote
  dispatch entirely and degrade `--all` to a single foreground claim (note
  `remote batch disabled — claiming one issue`, mirroring the jira degrade). Absent or
  `true` → attempt remote dispatch with the self-check above. This is the only
  _deterministic_ signal available, so it is the config knob rather than a runtime probe.

1. **Rank unclaimed candidates.** Run the handler's find-candidates phase (Linear:
   `linear-claim.md` "Find candidates") restricted to the `unstarted`/ready state,
   dropping any already-claimed issue (Linear: carries `auto-claimed`) and any that
   fails the standard filters (estimate, labels, assignee). Sort the survivors by the
   handler's ranking (Linear: `priority`, then `updatedAt` ascending — oldest first).
   Rank on the metadata the list query already returns — do **not** fetch each issue's
   relations yet.
2. **Bound by WIP slack (per-project + optional global ceiling).** WIP is **not** a
   single team-wide scalar for Linear — resolve the chosen scope(s) and their caps
   exactly as **"Resolve claim scope"** and the **"Pre-claim WIP gate (per-project +
   optional global ceiling)"** above define them: each configured project (plus the
   Unassigned bucket) carries its own `wip_limit` (per-project override else the
   top-level `wip_limit`, default `3`; the bucket uses `unassigned_wip_limit`), and the
   optional `linear.global_wip_limit` caps the **total** in-flight across all of them.
   For each chosen scope, count `started`-type issues (per the gate) and set that
   scope's `slack = wip_limit − in_flight`; also track the remaining **global** slack.
   If every chosen scope has `slack ≤ 0` (or the global ceiling is already reached),
   dispatch nothing and report `WIP limit reached (<per-scope counts> in flight) —
   nothing dispatched`. **This bound is unconditional — never offer the attended
   override here**, however the batch was invoked: a batch is the one action
   `attendedness.md` gates regardless of who is present.
3. **Select dependency-ready candidates lazily, in ranked order, respecting each
   scope's slack.** Walk the ranked list and check dependency-readiness **on demand**,
   one candidate at a time: **dependency-ready** for a tracker means every native
   blocking relationship is resolved — for Linear, read `get_issue` with
   `includeRelations: true` and confirm each `blockedBy` issue is in a
   `completed`-type state (`Done`) or no longer exists. A **`canceled`** blocker does
   **not** satisfy the dependency (it blocks until a human removes the link — matching
   `linear-reoptimize.md` Dimension 1, where a canceled `blockedBy` blocks forever and
   only `Done` satisfies). Keep ready issues; skip the rest, recording each as
   `waiting on <identifier>`. As you accept a ready issue, **decrement its scope's
   slack and the global slack**, and **skip** a ready issue whose own scope is already
   full even if the global ceiling still has room. **Stop** once every chosen scope is
   full, the global ceiling is reached, you have `N` issues (for `-n N`), or you
   exhaust the list. Checking lazily in ranked order this way avoids up to ~50
   `get_issue` calls per run. Record any ranked candidate left unexamined past the
   bound as `held (WIP limit reached)` or `held (-n N ceiling)`.
4. **Dispatch one remote session per selected issue.** Each issue gets its **own**
   cloud VM running the handler's single-issue claim+execute flow against **that one
   issue's identifier** — never instruct a session to claim more than one. For
   Linear, the remote session runs `linear-claim.md` end to end (`Claim the issue` →
   branch with the verbatim `branchName` → execute → `gh pr create` with `[<id>]` +
   `Closes <id>` → `Move to review on PR open`). The remote prompt must be
   **self-contained** — the VM has no plugin **unless the repo declares one in a
   committed `.claude/settings.json`** (see §4's gate; unprobed), **and** a fresh
   clone has no local task config (`/task-config` gitignores `dev_docs/tasks/` by
   default) — so
   inline the issue identifier, the claim+execute instructions, **and** the
   already-resolved **non-secret** Linear config the single-issue flow needs (the
   resolved `team`, `base_branch`, the issue's project scope, and its applicable
   `wip_limit`/`max_estimate`), mirroring the inline-prompt pattern in
   `repo-pr-execute.md` §4. **Never** inline secrets — `api_key`, `api_key_ref`,
   `api_key_resolver`, or any raw key stays in the remote host's own environment,
   never in the prompt.

   **The prompt must declare the remote session unattended.** Inline
   `--non-interactive` semantics explicitly: "No human is present in this session —
   never prompt; if the WIP gate is met, decline and report it." Without that, the
   worker sees an ordinary user-role prompt, matches no hard negative in
   `attendedness.md`, concludes it is attended, and offers itself the override its
   dispatcher was gated by — turning one bounded batch into N unbounded claims. This is
   the sharpest failure mode in the whole design: **the dispatching session's
   attendedness never transfers to the sessions it dispatches.**

   Run the dispatch commands in sequence so the user sees each session id.
5. **The atomic claim is the only race guard.** Parallel sessions are safe **without**
   any branch- or file-based lock because each session's first mutating step is the
   handler's read-then-write claim (Linear: the token-comment election in
   `linear-claim.md` "Claim the issue" — first-writer-wins on the oldest claim
   comment, with `auto-claimed`/`assignee` as the human-visible marker). Each
   session is pinned to one identifier, so a
   session that finds its issue already claimed by another run simply **stops/bails** —
   it does not fall back to a different issue — which is exactly why no double-claim can
   occur even when two runs' candidate sets overlap. This is why the tracker batch needs
   no equivalent of repo-pr's draft `task-claim` PR marker.

   **The claim guards issue identity, not capacity.** The WIP slack read in step 2
   is not atomic, so two concurrent batch runs can each observe the same slack,
   dispatch to disjoint issues, and together overshoot `wip_limit`. Accepted at
   single-operator scale — don't run two batch dispatches concurrently.
6. **Report** the dispatched issues (identifier, title, "remote session started"),
   then, separately, those `held` by the WIP / `-n N` bound and those skipped as
   `waiting on <identifier>`. Point the user at `/tasks` to monitor.

## 4. gh-issue path (`gh-issue` handler)

Read and follow **`commands/handlers/gh-issue-claim.md`** end to end — it holds
the find-candidates query, the in-flight pre-flight, the feasibility judgment, the
atomic `<branch_prefix>task-<n>` claim lock (defined in
`commands/handlers/claim-lock.md`), the work branch, `gh pr create` with
`Closes #<n>`, the move to the `status:4_needs_review` rung, bail mechanics, and the
report format. If the relative path doesn't resolve, find it with **Glob**
(`**/commands/handlers/gh-issue-claim.md`).

**Execution modes.**

- `/do-tasks`, `/do-tasks <#n>`, and `--no-claim` — **single and foreground**, in the
  current session over the `gh` CLI. `/do-tasks <#n>` claims that one issue.
- `--claim-only` — reserves without executing, so it batches regardless of mode,
  bounded by the pre-claim WIP gate (`gh-issue-claim.md` "Pre-claim WIP gate").
  Unchanged by this section.
- `/do-tasks --all` / `-n N`, **without** `--claim-only` and **without**
  `--no-claim` — **true batch execution**: one dispatched remote session per
  selected issue, via **gh-issue batch** below. `--remote` is the default;
  `--local` caps the batch at **1** and runs the single highest-ranked issue
  foreground through `gh-issue-claim.md`'s default flow.

The claim/execute split (`--claim-only` / `--no-claim`) and the pre-claim WIP gate
are documented in `gh-issue-claim.md` ("Modes: atomic vs. claim/execute split" and
"Pre-claim WIP gate").

**`--project` is refused on this handler**, for the reason
`commands/handlers/gh-issue-reconcile.md` step 3 already gives and owns: the
gh-issue handler has no project dimension **yet**, milestones being the presumed
mapping. Read it there rather than here. The refusal follows that file's rule
exactly — a scope this handler cannot honour, plus a write, acts outside what the
user named, and a batch always writes. So stop with `--project is not supported by
the gh-issue handler` rather than ignoring the flag and running wider than was
asked for.

### gh-issue batch (`--all` / `-n N`, without `--claim-only` or `--no-claim`)

This is section 3's **Tracker-batch subroutine** with gh-issue's substitutions
filled in. **Read that subroutine first** — its `--no-claim` **rejection**
(`--all --no-claim` is a contradiction), its rule that a batch never offers the
attended override, and its warning that a dispatching session's attendedness never
transfers to the sessions it dispatches all carry over unchanged. Only what is
written below is gh-issue's.

`--local` is a **cap**, not a rejection, and it is the one clause §3 spells in
Linear's terms: it caps the batch at **1** and runs that single highest-ranked
issue foreground — through `gh-issue-claim.md`'s default flow here, **not** §3's
"Claim and execute".

**First, the gate — and it is off by default.** `gh-issue.remote_batch` defaults
to **`false`**, unlike `linear.remote_batch`, which defaults to `true`. Unless a
repo sets it to `true`, do **not** dispatch: degrade `--all` / `-n N` to a single
foreground claim through `gh-issue-claim.md`'s default flow and note `remote batch
disabled — claiming one issue`. Read this **before** step 1, so a host that has not
opted in never ranks, counts, or resolves dependencies first.

**Why the default differs from Linear's.** Linear's remote flow is MCP tool calls
and prose, both of which inline into a dispatch prompt. This handler's deterministic
steps are **scripts that ship in the plugin** — every label write goes through
`gh-issue-state.py` — so a dispatched session needs the plugin itself, not just the
prompt. A cloud session does get a plugin the repo declares in a **committed
`.claude/settings.json`** (`extraKnownMarketplaces` + `enabledPlugins`), which is
what makes `true` safe; user-level plugin settings do **not** travel. **That
mechanism is documented and has not been probed here**, and this repo's own record
says to probe routine behaviour rather than read it
(`dev_docs/decisions/2026-08-24-routine-claim-channel.md` — "wrong twice" that way).
An unprobed mechanism is not a basis for dispatching N sessions by default, so the
flag is opt-in until someone runs the probe and records it beside that file. Setting
`true` without the declaration is not silently broken — step 5's self-check stops
each session loudly on its own issue.

> **Every deterministic value below comes from a script whose exit code or JSON is
> the contract.** Do not re-derive a candidate query, an in-flight count, or a
> branch name in prose. The first attempt at this feature did exactly that against a
> vocabulary that had since moved, and would have reported "no candidate" forever
> while looking healthy.

1. **Rank unclaimed candidates.** Run `gh-issue-claim.md` "Find candidates"
   unchanged — the `status:2_ready` + `auto:eligible` + `no:assignee` +
   `-label:blocked` server-side search, ranked `prio:0` → `prio:1` → `prio:2` →
   `prio:3` (an issue carrying none sorts last), then oldest `createdAt` first. Keep
   both positive terms in `--search` for the reason that section gives. Do not read
   any issue's dependencies yet.
2. **Bound by WIP slack.** gh-issue has one top-level `wip_limit` (default `3`) —
   no per-project caps and no global ceiling, so section 3 step 2's per-scope
   arithmetic collapses to a single number. Read it; do not compute it:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-claim.py" wip \
     --repo <repo> --wip-limit <wip_limit> --json
   ```

   The dispatch ceiling is the JSON's **`slack`**, already clamped at `0`. Never
   subtract `count` from `limit` here — an over-limit board (a human claimed by
   hand, or the limit was lowered under running work) makes that difference
   negative, and `gh-issue-claim.py` owns the clamp so no caller has to remember it.
   If `slack` is `0`, dispatch nothing and report `WIP limit <wip_limit> reached
   (<count> in flight) — nothing dispatched`. This bound is **unconditional**: a
   batch never offers the attended override (`commands/handlers/attendedness.md`
   step 2).
3. **Drop the dependency-blocked.** One call, over **exactly** the ranked
   candidates from step 1:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-ready.py" \
     --repo <repo> --issue <n1> --issue <n2> ... --json
   ```

   Keep the numbers in its `ready` array, **in step 1's ranked order**; record each
   entry in `blocked` as `waiting on #<b>` naming the open blockers it reports.

   **There is no body-footer path on this handler.** A `Blocked by: #<n>` line in an
   issue body is a human-readable **echo** of a native `blocked_by` edge, never the
   edge itself — nothing here may read it to decide blocked-ness. `gh-issue-ready.py`
   reads the edge.

   This asks about every ranked candidate in one call rather than lazily,
   **deviating from section 3 step 3**. Two reasons: it is the same call the single
   path already makes, so both paths answer dependency-readiness identically; and
   passing the numbers explicitly is what closes the window in which a second
   bounded board query silently omits a candidate, making a missing verdict
   indistinguishable from a ready one. The cost is one `blocked_by` read per
   candidate — at most 50 per batch run, since step 1 is limited to 50.
4. **Take the first `min(N, slack)`** ready candidates in ranked order (`--all`
   takes `slack` of them). Record every ranked candidate left over as `held (WIP
   limit reached)` or `held (-n N ceiling)`, and keep step 3's `waiting on #<b>`
   entries separate from both — a blocked issue and a held one need different
   answers from the reader.
5. **Dispatch one remote session per selected issue.** Each selected issue gets its
   **own** session running `gh-issue-claim.md`'s flow — **inlined into the prompt**,
   since the VM cannot read that file — against **that one issue number**, as a
   direct `<#n>` pick, and never instruct a session to claim more than one issue.

   **Which gates the session runs, and which this dispatcher already discharged.**
   The split follows what a VM without the plugin can actually execute:

   - **Discharged here, not re-run there** — "Find candidates" (step 1 ranked and
     selected), the pre-claim WIP gate (step 2's `slack` bounds the whole batch:
     dispatching at most `slack` sessions leaves each observing a count strictly
     under `wip_limit`, however the **siblings** interleave — the count starts at
     `count` and the last to claim sees `count + slack - 1`, so none of them could
     have declined; that holds against this batch's own dispatches only, per the
     caveat below), and dependency readiness (step 3, moments earlier). Each is already answered, and each would otherwise cost the
     session a plugin asset it may not have.

     **Say what this costs: `slack` becomes the only WIP bound, measured once.**
     The session-side gate was redundant against _this_ batch's own dispatches —
     that is the arithmetic above — but not against anything else claiming in the
     meantime: a local `/do-tasks`, a human assigning by hand, another batch run.
     Re-running it in the session would have caught those late, and now nothing
     does. The bound is therefore a **dispatcher-side best effort taken at one
     instant**, not a guarantee the repo stays under `wip_limit`. Accepted at
     single-operator scale, and the same acceptance section 3 already makes for
     concurrent batch runs; a repo that needs a hard bound should be claiming from
     one session.
   - **Run in the session** — pre-flight (plain `git ls-remote` and `gh pr list`),
     the claim election (plain `gh` comment calls), execute, `gh pr create`, and
     the two label writes.

   Whatever the session does run before the claim is **verification-only**: a
   pre-flight trip, a feasibility reject, or a lost claim **stops that session and
   reports it**. It must never advance to another candidate, which would put two
   dispatched sessions on one issue.

   The prompt must be **self-contained** — a fresh clone has no local task config
   (`/task-config` gitignores `dev_docs/tasks/`), and the handler's **prose** files
   are plugin files the prompt should carry rather than cite. Inline the issue
   number, the claim+execute instructions themselves (**not** a pointer to
   `gh-issue-claim.md` or `claim-lock.md`), and the already-resolved **non-secret**
   gh-issue config the single-issue flow needs: `gh-issue.repo` if set, the base
   branch, and `branch_prefix`. **Not `wip_limit`** — the dispatcher discharged
   that gate, so a session handed a limit is being invited to run a check it
   should not, and a decline there would strand a dispatched issue with nothing
   done. The **scripts** are a different case —
   the gate above means the plugin is installed, so have the session call them at
   **`$CLAUDE_PLUGIN_ROOT`**, the spelling `CONTRIBUTING.md` mandates and the one
   used above. `gh-issue-claim.md` still writes its asset calls repo-relative,
   which resolves only when the cwd is the plugin's own repo — so those are the
   calls that need rewriting as you inline.

   **Rewrite the paths as you inline, and check the inlined text before you
   dispatch.** `gh-issue-claim.md` spells every asset call
   `python3 commands/handlers/assets/…`, so copying its steps verbatim carries that
   spelling into the prompt and the session's first script call fails — after the
   self-check has passed, since the self-check probes `$CLAUDE_PLUGIN_ROOT` and the
   copied call does not. Each becomes
   `python3 "$CLAUDE_PLUGIN_ROOT/commands/handlers/assets/…"`. The check is on the
   **unprefixed** spelling: no `python3 commands/handlers/assets/` may survive in
   the dispatched prompt. Every asset call must carry the `$CLAUDE_PLUGIN_ROOT/`
   prefix — which of course still contains `commands/handlers/assets/`, so do not
   grep for that substring alone. **Never** inline a token or any other secret —
   the dispatched session authenticates through its own `gh`. That is also what
   step 2's bound assumes: `wip` counts `assignee:@me`, so the dispatched sessions
   must authenticate as the **dispatching** account or their claims never enter the
   next run's `slack`.

   **Resolve every value you can here, so the session needs fewer of the plugin's
   assets.** The branch name is the clearest case: run `branch-name` **in this
   session** and inline the **literal** it prints, rather than telling the VM to run
   a script it does not have.

   ```bash
   branch=$(python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-claim.py" branch-name \
     --issue <n> [--prefix "<branch_prefix>"])
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and a path above does not resolve, Glob
   `**/handlers/assets/<name>.py` — the fallback `CONTRIBUTING.md` documents for
   every asset call.

   The result is `<branch_prefix>task-<n>` (`gh-issue-claim.md` "Branch name"). A
   literal `task/<n>` is **wrong** anywhere on this path: it probes and creates a
   ref that is not the real lock, so every racer concludes it won. Deriving it once
   here also means both sides of a race compute it the same way, which is the
   property `claim-lock.md` depends on.

   **Self-check first, on two things.** The prompt's first step must be: "If
   `gh auth status` fails, or
   `$CLAUDE_PLUGIN_ROOT/commands/handlers/assets/gh-issue-state.py` is not present,
   do **not** claim — stop immediately and report `remote gh CLI unavailable` or
   `remote handler assets unavailable`." Both are necessary and `gh` alone is not
   sufficient: every phase of this handler shells out to `gh`, **and** the label
   writes go through `gh-issue-state.py`, which validates against `labels.yml`
   before any network call. A session that claims an issue and then cannot write
   its rung strands exactly the half-written state `gh-issue-claim.md` "Claim the
   issue" step 3 warns about — assigned and lock-held but still `status:2_ready`,
   which nothing picks up and nothing cleans.

   **This is the backstop for the gate, and it is why an unprobed gate is still
   safe to offer.** The gate reads a declaration; the self-check reads the VM. If
   the declaration is there but the install did not happen — a marketplace the
   session's network could not reach, say — every dispatched session stops on its
   own issue and says so, rather than claiming work it cannot write back. Keeping
   the check inside the session is section 3's rule for the same reason: what the
   VM actually inherits is visible there and nowhere else.

   **Refuse remote dispatch when `gh-issue.repo` is not the session's own repo.** A
   cloud session's `gh` reaches only the repositories attached to it, so a batch
   whose tracker is a different repo from the code would have every session fail at
   its first write. Check it here and degrade to the foreground claim with
   `remote batch needs gh-issue.repo to be this repo — claiming one issue`.

   **Declare the session unattended.** Inline `--non-interactive` semantics: "No
   human is present in this session — never prompt; if any gate you run declines,
   report it and stop." Do **not** copy section 3's wording verbatim here: it names
   the WIP gate, which this session does not run (the dispatcher discharged it), so
   it would instruct the session about a decision it never makes. Section 3's
   _reason_ still applies in full and is the sharpest failure mode in the design —
   without the declaration, each dispatched session concludes it is attended and
   offers itself the override its dispatcher was gated by.

6. **Dispatched sessions claim on the comment election, not the ref lock.** This is
   the one place a dispatched session's flow differs from the single path, and — as
   with everything else in step 5 — the dispatcher **inlines the election's steps
   into the prompt** rather than naming `claim-lock.md`, which the VM cannot read.
   Copy them from `claim-lock.md` → "Fallback: comment-token election": record
   `T_unclaimed`, mint a token, post the claim comment, write the board markers,
   sleep a jittered ~2–3 s, re-list and elect on the lowest comment id among
   markers that are both at-or-after `T_unclaimed` and state-backed, and retract
   your own comment if it loses. Carry **step 7** across too — if the re-list
   returns only your own marker, treat it as inconclusive and re-poll once or
   twice before declaring a win. No server-side CAS backs this path, and a batch
   runs it N times unattended, so the one step that guards read lag is the last
   one to drop. The prompt must **omit** `gh-issue-claim.py
   acquire` entirely — there is no ref to create on this path.

   **Two additions to those steps, and they are what make the mixed-path race
   detectable.** The election cannot see a ref, so the session has to look for one
   itself — `git ls-remote --heads origin "<branch>"`, pre-flight's probe, run
   **twice**:

   - **Before writing the board markers**, right after posting the claim comment.
     A ref here means a local session acquired after this session's pre-flight:
     retract the comment, write **nothing** to the board, stop.
   - **After the jittered sleep, with the re-list.** This is the one that matters,
     because the first probe leaves the election's own ~2–3 s sleep wide open —
     and a local session needs only a `git fetch` and one POST to acquire inside
     it. A ref here is a lost claim **regardless of comment ordering**: retract
     your comment and stop. The markers are already written by then; **leave
     them**, exactly as fallback step 6 says — they carry the same account values
     the local winner writes, so stomping them helps nobody.

   Either way report `Skipped #<n>: claim lost — <branch> already exists on
   origin`, `claim-lock.md`'s own wording for this observation. Do **not** write
   "acquired by another session": a bare `ls-remote` hit cannot tell a live claim
   from a ref an earlier crash stranded, and this section is where that
   distinction is load-bearing.

   The session then creates its work branch itself,
   `git switch -c "<branch>" "origin/<base>"` (the case `gh-issue-claim.md`
   "Branch + execute" step 1 already covers for a claim that created no ref), using
   the literal `<branch>` step 5 inlined. On bail it deletes its own token comment
   rather than a ref, and runs **no** `release` call. Have it report
   `claim: comment election (batch dispatch)`, **not** `claim-lock.md`'s degrade
   string: that string names an API error, and no API error happened here.

   Why: the ref lock has no release an unattended session can be relied on to reach.
   A dispatched session that crashes or times out after acquiring strands `<branch>`
   on origin; every later session then reads that ref as a live claim and skips the
   issue **forever**, and this repo has **no stale-ref sweep** (`claim-lock.md` →
   "Why it is not the default"). The comment election's orphans are self-healing by
   contrast — its `T_unclaimed` filter and state-backed check discard them. A batch
   fans out N unattended sessions at once, so it multiplies exactly the failure the
   election is immune to. This is **not** a claim that a dispatched session lacks
   the credential to release: one that passed the `gh auth status` self-check could
   delete the ref. It is that a crash is precisely the case where it never gets to.

   What the election removes is the **window**, not the ref: the session pushes
   `<branch>` to open its PR, so the ref exists again from that moment. The
   difference is what a strand then means — an hour of unattended execution can no
   longer end in an empty ref that makes the issue skipped forever, and a ref left
   after the push is accompanied by finished work and an open PR that pre-flight
   reports rather than a silent forever-skip.

   **What neither lock fixes: the board markers.** A session that claims and then
   dies before opening a PR leaves the issue assigned and on `status:3_started`
   with nothing to show, and the candidate query excludes it on **both** counts
   (`no:assignee` and `status:2_ready`), so no later run picks it up. That is the
   handler's own failure mode, identical on the ref path and the election path — a
   crashed **local** claim strands an issue exactly the same way — so it argues for
   neither lock and is **not** repaired here. Do not read "the election's orphans
   are self-healing" as covering it: `claim-lock.md`'s self-healing is about stale
   claim **comments** losing later elections, not about the board. Batch multiplies
   the exposure by dispatching N unattended sessions, which is worth knowing before
   turning `remote_batch` on; recovering such an issue is a human `gh issue edit`
   today, and a sweep for it has no owner.
7. **What guards the race — and what does not.** Two dispatched sessions never
   contend: step 5 pins each to a distinct issue number and none falls back to
   another issue, which is why the batch needs no equivalent of repo-pr's draft
   `task-claim` PR marker. What **can** contend is a dispatched session and a
   **local** `/do-tasks` on the same issue. Step 6 puts them on different
   elections, so **neither primitive rejects the other** — the ref acquire cannot
   see a claim comment and the comment election cannot see a ref. That is a real
   asymmetry and it is not closed by a lock; it is closed by each side reading for
   the other's marker, and each direction has one:

   - **Local acquires the ref first** → one of the dispatched session's two ref
     probes in step 6 sees it — the first before any board write, the second
     after the election's sleep, which is the interval the first cannot cover —
     and it retracts and stops.
   - **The dispatched session writes its markers first** → the local session's
     "Claim the issue" step 1 re-read sees the assignee and the moved rung and
     returns `race` before its own acquire.

   What is left is genuine simultaneity: the two reads interleaving inside the
   window between the dispatched session's **second** probe and the local
   session's acquire, which is milliseconds rather than the seconds the sleep
   would otherwise have contributed. Nothing here makes that impossible, and the
   honest bound is that it is narrow rather than closed —
   `claim-lock.md`'s existing mixed-path window, which batch makes ordinary rather
   than exceptional. Taking the ref lock in the dispatched session would close it
   and reopen the permanent strand step 6 exists to avoid; that trade is the
   subject of step 6, not a gap here. **Do not run a batch against a repo a local
   `/do-tasks` is working at the same time.**

   The `slack` read in step 2 is not atomic either, so two concurrent batch runs can
   each observe the same slack, dispatch to disjoint issues, and together overshoot
   `wip_limit`. Accepted at single-operator scale, as in section 3 — don't run two
   batch dispatches concurrently.
8. **Report** the dispatched issues (number, title, "remote session started"), then
   separately those `held` by the WIP / `-n N` bound and those `waiting on #<b>`.
   Note a truncated candidate page if step 1 returned exactly 50. Point the user at
   `/tasks` to monitor.

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

**Single by nature.** Unlike the Linear tracker path (which batches via the
Tracker-batch subroutine in section 3) and the gh-issue path (which instantiates
that subroutine in section 4), jira execution is foreground: `--remote`/`--local` do not apply, and `--all` / `-n N` degrades to a
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

For the **tracker path** in **single** mode, report per `linear-claim.md` "Report":
on success print the issue identifier, the PR URL, and a one-line summary; on bail
print the identifier, why it bailed, and the Linear comment URL; on the WIP gate
declining, print the limit and the in-flight count. In **batch** mode (`--all` /
`-n N`), report per the Tracker-batch subroutine (section 3) step 6: the dispatched
issues (identifier, title, "remote session started"), then separately those held by
the WIP / `-n N` bound and those skipped as waiting on a blocker; point the user at
`/tasks` to monitor.

For the **gh-issue path** in **single** mode, report per `gh-issue-claim.md`
"Report": on success print the issue number, the PR URL, and a one-line summary; on
bail print the issue number, why it bailed, and the issue-comment URL. In **batch**
mode (`--all` / `-n N`), report per section 4 "gh-issue batch" step 8: the
dispatched issues (number, title, "remote session started"), then separately those
held by the WIP / `-n N` bound and those waiting on a blocker; point the user at
`/tasks` to monitor.

For the **jira path**, report per `jira-claim.md` "Report": on success print the
issue key, the PR URL, and a one-line summary; on bail print the issue key and
why it bailed (the bail comment is posted on the issue).
