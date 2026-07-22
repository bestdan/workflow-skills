# Linear handler — shared reference

Shared definitions used by `linear-add.md`, `linear-list.md`, and `linear-claim.md`. This file has no commands of its own — it only defines the config schema, the preflight pattern, and the kanban mapping table that every Linear-handled command needs.

## Connection

Linear is accessed via the official MCP server connected from `https://mcp.linear.app/mcp`. All Linear-handled commands assume the MCP is reachable; each command's preflight stops with the same error message if it isn't.

> **Linear MCP tool namespace.** The same MCP can be installed two ways, producing two different tool prefixes:
>
> - Installed via `claude.ai` settings → tools are `mcp__claude_ai_Linear__list_teams`, etc.
> - Installed via `claude mcp add --transport http linear https://mcp.linear.app/mcp` (what `mcp-setup-offer.md` instructs) → tools are `mcp__linear__list_teams`, etc. (prefix is `mcp__<server-name>__`, server registered as `linear`).
>
> Use whichever prefix is loaded in the session. Tool names after the prefix (`list_teams`, `list_projects`, `list_workflow_states`, `create_issue`, `save_issue`, `get_issue`, `save_comment`, `list_issue_labels`, `create_issue_label`, `get_user`, `list_issues`) are identical across both installs. `linear-common.md` and the per-verb files write tool names as `<linear-mcp>__list_teams`, etc. — substitute the prefix loaded in your session.

## Config block

The Linear handler is selected by `handler: linear` in `dev_docs/tasks/.task-config.yml`. Commands read the **merged view** — `.task-config.yml` overlaid with the optional gitignored `.task-config.local.yml` (mappings merge recursively — a local `linear.api_key_ref` overlays just that leaf and keeps the committed `team`/`projects`; see `task-config.md` → "Local override"). The config keys (top-level `wip_limit` plus the `linear:` block):

```yaml
handler: linear
wip_limit: 3 # top-level — shared with the repo-pr & gh-issue handlers. For Linear it is the
# per-project default that each `linear.projects` entry inherits unless it sets its own.
linear:
  team: PreThink # required — team name (as shown in Linear) or team id/UUID.
  # The team key (e.g. PRE) is not accepted because `list_teams` does not return it.
  default_priority: 3 # optional — 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low (default 3).
  max_estimate: 3 # optional default for /do-tasks (tracker path) — exclusive upper bound on
  # `estimate` (Linear's `estimate` uses the same Fibonacci scale as our task `size` — see
  # "Task size" in skills/task/SKILL.md). Default 3. Inherited by each project unless overridden.
  base_branch: main # optional, used by /do-tasks (tracker path) — branch /do-tasks branches
  # from. Default main.
  projects: # optional — the Linear projects this repo's task commands scope to. Replaces the
    # old scalar `default_project`. Absent or empty → whole-team scope (today's "no pin" behavior);
    # exactly one entry → equivalent to a single pin.
    - id: ebbc284b-a1c1-4cb3-96e0-914e210a79a2 # required — project id/UUID, not a name.
      name: Handler parity follow-ups # optional — for prompts/reports; resolved via
      # list_projects when absent.
      wip_limit: 5 # optional — per-project override (else inherits the top-level wip_limit).
      max_estimate: 5 # optional — per-project override (else inherits linear.max_estimate).
      repo: bestdan/workflow-skills # optional — GitHub owner/name whose merged PRs establish
    # ownership for /find-false-closures. A workspace can span repos, so each project may name
    # its own; absent → /find-false-closures falls back to the current repo's origin.
    - id: 9f3a0b1c-0000-0000-0000-000000000000 # a second project, no overrides → inherits wip_limit 3, max_estimate 3.
  global_wip_limit: 6 # optional — absolute ceiling on TOTAL in-flight across ALL configured
  # projects, enforced on top of the per-project caps (absent → no global ceiling; the sum of
  # per-project caps applies). Lives under linear: because it is multi-project-specific, unlike
  # the cross-handler top-level wip_limit.
  unassigned_wip_limit: 3 # optional — WIP cap for the synthetic "Unassigned" bucket that
  # catches every in-flight issue with NO project OR in a project not listed under projects
  # above. Default = top-level wip_limit. Set to 0 to never ranked-claim unassigned work.
  # Only takes effect when 1+ projects are configured — with none, the whole-team scope already
  # covers everything, so there is no "outside".
  orphan_claim_hours: 24 # optional, used by /reconcile-tasks row 4 (linear-reconcile.md) —
  # idle-hours threshold before a started+auto-claimed issue with no resolvable PR and no
  # remote branch is treated as an orphaned claim and demoted back to Backlog. Default 24.
  api_key_ref: op://Private/Linear API/credential # optional, used by /archive-tasks —
# a 1Password op:// reference to a Linear PERSONAL API key. The MCP has no archive
# mutation, so the GraphQL issueArchive backstop needs a raw key. Never a literal key.
# See linear-config.md → "Archive key" and commands/handlers/linear-archive.md.
# labels support is deferred — the Linear MCP create_issue tool takes label UUIDs, not names,
# and resolving names → ids requires an extra tool call. Add a tag in the body for now.
```

**Schema rules:**

- `projects` **absent or empty** → whole-team scope with the single top-level `wip_limit` (preserves today's "no pin" behavior). **Exactly one** entry → equivalent to today's single pin.
- `wip_limit` stays **top-level** so the repo-pr and gh-issue handlers are untouched; per-project entries override it for Linear only. `max_estimate` stays under `linear:` as the inherited default.
- Per-project override keys are `wip_limit`, `max_estimate`, and `repo` (the last read only by `/find-false-closures`). `team`, `base_branch`, `default_priority`, and `api_key_ref` remain global. `api_key_ref` is a secret (a full-account bearer token) — its canonical home is the gitignored `.task-config.local.yml`, not the shared `.task-config.yml` (see `task-config.md` → "Local override").
- Each entry's `id` is **required**; `name` is optional (used for prompts/reports; resolved via `list_projects` when absent).
- `global_wip_limit` is optional and lives under `linear:` (it is Linear-multi-project-specific, unlike the cross-handler top-level `wip_limit`).
- `orphan_claim_hours` is optional and lives under `linear:`. It is read only by `/reconcile-tasks` row 4 (`linear-reconcile.md`) as the idle-hours threshold before an orphaned claim (started + `auto-claimed`, no resolvable PR, no remote branch) is demoted back to Backlog. Default `24`.
- `unassigned_wip_limit` is optional and lives under `linear:`. It caps the synthetic **Unassigned** bucket (issues with no project or in an unconfigured project) and defaults to the top-level `wip_limit`; `0` means "never ranked-claim unassigned work". It is only meaningful when 1+ projects are configured (with none, the whole-team scope already spans everything).

### Resolve configured projects

Every Linear-handled command that scopes to projects **must call** this one resolution step instead of reading `default_project`/`projects` directly, so inheritance and the whole-team fallback live in exactly one place. It returns a **list** of resolved scopes, each `{ id, name, wip_limit, max_estimate }` with inheritance already applied:

1. Read `linear.projects`. **If absent or empty**, return a single synthetic **whole-team** scope: `{ id: null, name: null, wip_limit: <top-level wip_limit, default 3>, max_estimate: <linear.max_estimate, default 3> }`. `id: null` means "omit `projectId` — operate on the whole team", which is exactly today's no-pin behavior. Stop here.
2. **Otherwise**, map each entry to `{ id, name, wip_limit, max_estimate }`:
   - `id` — the entry's required `id` (verbatim UUID).
   - `wip_limit` — the entry's `wip_limit` if set, **else** the top-level `wip_limit` (which itself defaults to `3` when unset).
   - `max_estimate` — the entry's `max_estimate` if set, **else** `linear.max_estimate` (which itself defaults to `3` when unset).
   - `name` — the entry's `name` if set; **else** resolve it **lazily** via `<linear-mcp>__list_projects` (match the `id`) only when a name is actually needed for a prompt or report — never eagerly.

Consumers that only select a project (`/add-task`, `/list-tasks`, `/promote-tasks`) use `id` + `name`; consumers that enforce WIP (`/do-tasks`, `/archive-tasks` scope) use `wip_limit` too. The optional **`linear.global_wip_limit`** is a separate scalar the `/do-tasks` WIP gate reads alongside this list (the ceiling across all returned scopes); it is not part of a per-project entry. Treat the returned list as read-only config — do not mutate it. This helper returns **only** the configured projects (or the single whole-team scope) — it does **not** include the Unassigned bucket below, so the select/query consumers can pass every returned `id` as a `projectId` safely.

### The Unassigned bucket

A synthetic scope that composes on top of the resolved configured projects. It exists **only when 1+ projects are configured** (with none, the whole-team scope already spans everything, so there is no "outside"). There are **two variants** with different membership rules — pick the one for the consumer at hand, never mix them:

- **Claim variant** — used by **`/do-tasks`'s claim path only** (its "Find candidates" and "Pre-claim WIP gate"); the select-only consumers (`/add-task`, `/list-tasks`, `/promote-tasks`) never see it. Membership is the catch-all for issues with **no project** or in a project **not** among the configured `id`s. For `/do-tasks` the Unassigned bucket is a **feature** — pick up team work not filed under a project — so the wide catch-all is intentional.
- **Sweep/reconcile variant** — used by **`/sweep-for-complete` and `/reconcile-tasks`'s default (no-`--all`) scope only**. Membership is **narrower**: `projectId == null` **only** (issues with genuinely no project — never "any project outside the configured set"). These two verbs are destructive-adjacent (they complete issues / move them to review), so the wide claim-variant catch-all would silently pull every unrelated, unconfigured project's in-flight work into a scheduled `--apply` run's blast radius — exactly the footgun this narrower rule exists to close. `--all` remains the explicit whole-team escape hatch for these two verbs when the wider sweep is actually wanted.

Shape (the sentinel and its client-side resolution are identical for both variants; the membership predicate and in-flight count follow the variant above):

- `{ id: "__unassigned__", name: "Unassigned", wip_limit: <linear.unassigned_wip_limit if set, else the top-level wip_limit (default 3)>, max_estimate: <linear.max_estimate, default 3>, unassigned: true }`.
- `id: "__unassigned__"` is a **sentinel**, not a real Linear project id and **distinct from `id: null`** (which means "whole team — omit `projectId`"). Because the Linear MCP `list_issues` `project` argument has no null-project value, this scope's membership is always resolved **client-side** (a filter on each issue's `projectId`), **never** by passing the sentinel as a `projectId`. The filter predicate and the in-flight count follow the variant: the **claim variant** keeps issues with no project **or** outside the configured set and counts in-flight **by subtraction** (whole-team − Σ configured); the **sweep/reconcile variant** keeps only `projectId == null` and counts those survivors of the whole-team query directly.
- When `unassigned_wip_limit` is `0`, the bucket still appears (so it can be reported) but its cap is `0` → it is never ranked-claimed (claim variant only — the sweep/reconcile variant doesn't consult `wip_limit`).

### Resolve claim scope

`/do-tasks` (tracker path) calls this to decide **which scope to claim from** — the claim-side mirror of `/add-task`'s project prompt (`linear-add.md` step 2). It has **two timings**:

- A **specific pin** — `--project <name|id|unassigned>`, or a project typed into the prompt's "Other" — is resolved **before** "Find candidates" and **scopes the candidate query** to that one scope. This is what lets a pinned project (even a live, **unconfigured** one) actually get queried — otherwise its issues would only surface via the Unassigned exclusion pass, tagged Unassigned, and narrowing by the project's real `id` would find nothing. See step 1.
- **Everything else** — no flag, or `--project any` — resolves **after** "Find candidates" from the ranked, scope-tagged candidates, and **narrows** that list. This path issues **no `list_issues` calls of its own** (the Linear MCP is token-expensive; "which projects have ready work" is read from the distinct `project` scopes present among the candidates). See steps 2–3.

Inputs: the **claim scope set** = the resolved configured projects ("Resolve configured projects") **plus the Unassigned bucket** ("The Unassigned bucket", when 1+ projects are configured); a `--project <value>` flag if passed; and whether the session is **interactive** (an `AskUserQuestion` prompt is possible — false for headless `/loop`/cron).

1. **Specific pin (`--project <name|id|unassigned>`) → resolve up front, no prompt, then scope candidate discovery to it:**
   - `unassigned` → the Unassigned bucket scope (error if 0 projects are configured, so no bucket exists: "no Unassigned bucket — configure projects first, or drop --project"). "Find candidates" runs only its Unassigned pass.
   - a project **name or id** → match against the resolved configured scopes (case-insensitive name, or exact id/UUID); if none matches, match against the team's live projects via `<linear-mcp>__list_projects` (an unconfigured project is valid — a one-run scope inheriting the global `wip_limit`/`max_estimate`, and it triggers the persist offer in step 5). On no match anywhere, push back ("`<value>` is not a project in team `<team>`") and stop. "Find candidates" queries **only this project's `id`** as `projectId` (so a live unconfigured pin gets its own candidates, not the Unassigned pass), and the WIP gate counts **only this project** — a direct per-project count, no whole-team subtraction unless `global_wip_limit` is set.
2. **`--project any` → Any:** the full scope set (rank across all; each candidate still gated by its own per-scope cap — today's cross-project behavior). Resolved after "Find candidates"; no prompt.
3. **No flag → resolve after "Find candidates"** from the ranked candidates. Let `scopes_with_work` = the distinct `project` scopes owning at least one ranked candidate (real projects and/or the Unassigned bucket).
   - **0** scopes with ready work → nothing to claim; report no issue claimed, no prompt.
   - **exactly 1** scope with ready work → use it (or the single whole-team scope when 0 projects are configured); no prompt.
   - **2+** scopes with ready work **and interactive** → prompt: `AskUserQuestion` (header "Claim from") listing at most **2** of `scopes_with_work` (most-recent candidate first; names resolved lazily) — including **Unassigned** when it has work — plus **Any** and the automatic **"Other"**, so the total stays within `AskUserQuestion`'s **4-option max** (2 scopes + Any + Other). "Other" lets the user type a project name/id, resolved as in step 1 (incl. a live/unconfigured project, which then scopes a fresh candidate query for that project per step 1). Return the chosen scope, the Unassigned bucket, or the full set for **Any**.
   - **2+** scopes with ready work **and non-interactive** → **Any**, no prompt.
4. **Narrow.** Filter the ranked candidate list to the chosen scope(s): a single project → only its candidates; Unassigned → only bucket candidates; **Any** → all. Hand the narrowed list and the chosen scope(s) to `do-tasks.md`'s "Pre-claim WIP gate".
5. **Offer to persist an unconfigured project** (interactive only). If the chosen scope is a **concrete live project whose id is not in `linear.projects`** (reached via `--project <name|id>` or the prompt's "Other"), ask once via `AskUserQuestion` whether to add it to the config. On **yes**, append a `{ id, name }` entry to `linear.projects` with a **targeted `Edit`** (append one entry under the existing `projects:` key — a text edit, not a full re-serialize, so comments and unrelated keys are untouched); it inherits the global `wip_limit`/`max_estimate` (the `linear-config.md` step 3b shape). On **no**, proceed for this run only (the project stays in the Unassigned bucket next time). **Never** offer for `any`, `unassigned`, an already-configured project, or non-interactively. This is the only place `/do-tasks` writes the config.

## Ready-candidate selection

The gate and rank rules `/do-tasks` (tracker path) apply when picking claimable issues out of a candidate set.

**State scope.** Only `unstarted`-type states (the `ready` kanban column) are eligible — `backlog` issues are unrefined and must go through `/promote-tasks` first, and `started` issues are by definition already claimed.

**Gates.** Drop a candidate if any of these fail. Each has a fixed reason string so the caller can report consistently:

| Gate                                                                               | Reason string              |
| ---------------------------------------------------------------------------------- | -------------------------- |
| `estimate` is `null`/missing                                                       | `no estimate set`          |
| `estimate >= <max>` (candidate's resolved per-project `max_estimate`, default `3`) | `estimate <N> >= <max>`    |
| Has label `auto-claimed`                                                           | `already auto-claimed`     |
| Has label `human-approval-requested`                                               | `human-approval-requested` |
| Has label `blocked`                                                                | `blocked`                  |
| `assignee` is set and is **not** the current Linear user                           | `assigned to <name>`       |

**Rank.** Sort remaining issues by Linear `priority`: urgent(1) → high(2) → medium(3) → low(4), then **none(0) last** (Linear stores "no priority" as `0`, so a naive numeric ascending sort would wrongly put it first), then by `updatedAt` ascending (oldest first — let aging cards bubble up).

This block is the single source of truth for `ready` selection. `linear-claim.md` (both the GraphQL fast-path and the MCP floor) and `commands/handlers/assets/linear-ready.py` all implement exactly these gates and this ordering — change them here and update both consumers in lockstep.

## In-flight scan

The read `/sweep-for-complete` (row 1's "merged → Done") and `/reconcile-tasks` row 2 ("open PR, wrong column") each run to find in-flight issues within a named state scope. Resolving and merge-checking each issue's PR is a **separate downstream step** — the scan itself does not pre-filter to PR-bearing issues (PR resolution has its own attachment → title → branch fallback; a state-scope match with no attachment is still a candidate).

**State scope (parameterized).** The caller names which state-type set applies:

- The merged→Done sweep scans every state id of type `started`.
- Reconcile row 2's "open PR, wrong column" scans every state id of type `backlog` **plus** every state id of type `unstarted`.

Either way, resolve state ids by **type only, never display name** (names are user-configurable — see "Kanban mapping" above).

**Skinny fields.** The scan needs only `id identifier title url state { id type }` and **explicitly not `description`**: it never reads issue body text, only state and (downstream) the linked PR. The PR attachment URL is fetched differently per backend: GraphQL folds `attachments { nodes { url } }` into the same scan query, while MCP `list_issues` does **not** return attachments — resolve them via a subsequent `get_issue` per issue (as `linear-sweep-complete.md` step 3 does).

**Scope resolution.** Identical to the other commands: with `--all`, a **single whole-team query**, no project resolution and no Unassigned pass; without `--all`, "Resolve configured projects" **plus** the Unassigned bucket's **sweep/reconcile variant** (`projectId == null` only — see "The Unassigned bucket"), **never** the wide claim variant. This scan's only two consumers are the destructive-adjacent verbs `/sweep-for-complete` and `/reconcile-tasks`, so it never uses the claim-path catch-all. The pass is still one extra whole-team query with `projectId` omitted, after which you keep only issues whose `projectId` is `null`.

**Per-scope query count.** MCP's `list_issues` `state` filter is single-valued, so MCP issues one `list_issues` call per resolved scope **per state id**, unioned per scope. GraphQL filters by state type in **one** query per scope (`state: { type: { in: [...] } }`, as `linear-ready.py` does) — never one query per state id.

This block is the single source of truth for the in-flight scan. `linear-sweep-complete.md`, `linear-reconcile.md`, and `commands/handlers/assets/linear-scan.py` (added by the task-2 follow-up) all implement exactly this — change it here and update all consumers in lockstep.

## Linear concepts → task concepts

- **Team** is required for every issue.
- **Project** is an optional grouping that can span teams.

## Preflight pattern

Every Linear-handled command starts the same way. Failure messages are identical across `/add-task`, `/list-tasks`, and `/do-tasks` so the user sees the same guidance regardless of which command surfaced the error.

1. Call `<linear-mcp>__list_teams` (no args).
   - If the tool errors or returns no teams, **stop** with: "Linear handler needs the Linear MCP. Connect it in Claude Code settings (`https://mcp.linear.app/mcp`), then re-run." Do not fall back to another handler.
   - Match `<linear.team>` against each returned team's `name` (case-insensitive) or `id`. `list_teams` does not return the team key (e.g. `PRE`), so a key value will not match — surface that in the error if a likely-key string (short, all-caps) is configured.
   - If no team matches, **stop** with: "Configured Linear team `<team>` is not in your accessible teams." (List the team names that were returned.)
   - Capture the resolved team `id` and pass it to the rest of the flow.

2. (Per-verb files do additional setup — e.g. `list_workflow_states` for `/list-tasks` and `/do-tasks`.) The shared part is just the team check.

## Kanban mapping

Linear tasks are **issues**. The seven kanban columns from `skills/task/SKILL.md` map onto Linear's team-level issue workflow states plus four labels. **No custom workflow states are required** — the team's default Linear setup (`Backlog → Todo → In Progress → Done → Canceled`) is enough, since `needs_refinement` and `needs_review` ride on labels and on the linked GitHub PR.

Resolve state ids at runtime by Linear's state **type** (not display name — display names are user-configurable):

| Kanban column      | Linear state type | Default name                                       | Linear label(s)                                                                                                                                                                       |
| ------------------ | ----------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `new`              | `backlog`         | `Backlog`                                          | —                                                                                                                                                                                     |
| `needs_refinement` | `backlog`         | `Backlog`                                          | `human-approval-requested`                                                                                                                                                            |
| `ready`            | `unstarted`       | `Todo`                                             | `auto-eligible` (set by promoter)                                                                                                                                                     |
| `in_progress`      | `started`         | `In Progress`                                      | `auto-claimed` + viewer as `assignee` (the human-visible claim marker; the actual lock is a token-bearing claim comment)                                                              |
| `blocked`          | `started`         | `In Progress`                                      | `blocked`                                                                                                                                                                             |
| `needs_review`     | `started`         | `In Progress` (or `In Review` if the team has one) | — (the open PR is the review signal via the explicit `links` attachment from the tracker execute path; Linear's GitHub integration, which used to also create this link, is disabled) |
| `done`             | `completed`       | `Done`                                             | —                                                                                                                                                                                     |

Transitions:

- `/add-task` (`linear-add.md`) creates the issue in the team's default `backlog`-type state.
- `/promote-tasks` (Linear flavor) scores cards in that backlog state: HIGH → move to the `unstarted`-type state (`Todo`) and add `auto-eligible`; LOW → leave it where it is and add `human-approval-requested`.
- `/do-tasks` (tracker path, `linear-claim.md`) picks issues from the `unstarted` state in ranked order and, for each, runs **pre-flight → claim → judge feasibility** (the feasibility read now happens _after_ the claim, while the lock is held — claiming before the slow judge collapses the unclaimed window two racing sessions would otherwise collide in). Pre-flight skips the issue if an open PR or remote branch already exists for it (another session is already building it). The claim is a **token-comment lock**: post a claim comment carrying a unique per-session token _first_, then move the issue to the `started` state, add `auto-claimed`, and set the viewer as `assignee` (the human-visible marker). After a jittered delay it re-reads the comments and elects the winner = the earliest state-backed claim comment; a session that did not win deletes its own comment and falls to the next candidate. (`assignee` alone cannot decide a winner when both racers authenticate as the same Linear user.) On PR open it optionally moves to an `In Review` state if one exists.
- The issue reaches `completed` via the reconciler verbs — `/sweep-for-complete` or `/reconcile-tasks`, both built on the `/complete-task` primitive — which verify that the issue's **own** `links`-attached PR merged before completing it. Linear's GitHub integration, which used to do this on `Closes <KEY>` merge, is **disabled**: it over-closed, treating a bare `<TEAM>-NNN` id anywhere in a PR's title or body as a closing link and sweeping unrelated sibling issues to `Done`. `/do-tasks` adds the explicit `links` attachment to the issue on PR open (not dependent on branch-name matching); that attachment is what makes the reconciler verbs' verification possible.
- Bail path: revert to the backlog state, add `human-approval-requested`, remove `auto-claimed`, clear `assignee`, delete the session's claim comment, and leave a reason comment. A **feasibility reject** (card rejected before building) releases the claim and **continues** to the next ranked candidate; a **mid-execution** failure releases the claim and **halts**.

> **Hard rule for the tracker execute path: it never moves a Linear issue to a `completed`- or `canceled`-type workflow state.** Completion is driven by the reconciler verbs (`/complete-task`, `/sweep-for-complete`, `/reconcile-tasks`), not by Linear's GitHub integration, which is disabled. If you are about to call `save_issue` with a `completed`-type `state` from `linear-claim.md`, you have a bug — stop.
