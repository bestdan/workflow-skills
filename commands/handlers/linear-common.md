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

The Linear handler is selected by `handler: linear` in `dev_docs/tasks/.task-config.yml`. Commands read the **merged view** — `.task-config.yml` overlaid with the optional gitignored `.task-config.local.yml` (mappings merge recursively — a local `linear.api_key_ref` overlays just that leaf and keeps the committed `team`/`projects`; see `commands/task-config.md` → "Local override"). The config keys (top-level `wip_limit` plus the `linear:` block):

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
  remote_batch: true # optional, used by /do-tasks batch (--all / -n N). true/absent → dispatch
  # one remote session per dependency-ready issue (each self-checks for the Linear connector and
  # bails loudly if absent). Set false when the remote VMs are known NOT to carry the Linear
  # connector: --all then degrades to a single foreground claim. Default true.
  projects: # optional — the Linear projects this repo's task commands scope to. Replaces the
    # old scalar `default_project`. Absent or empty → whole-team scope (today's "no pin" behavior);
    # exactly one entry → equivalent to a single pin.
    - id: ebbc284b-a1c1-4cb3-96e0-914e210a79a2 # required — project id/UUID, not a name.
      name: Handler parity follow-ups # optional — for prompts/reports; resolved via
      # list_projects when absent.
      wip_limit: 5 # optional — per-project override (else inherits the top-level wip_limit).
      max_estimate: 5 # optional — per-project override (else inherits linear.max_estimate).
      repo: bestdan/workflow-skills # optional — GitHub owner/name whose merged PRs establish
    # ownership for /find-false-closures, and which repo /sweep-for-complete searches when it
    # falls back to title/branch PR discovery. A workspace can span repos, so each project may
    # name its own; absent → both fall back to the current repo's origin, which is wrong for
    # every project whose work lives in another repo. Set it on each project once the workspace
    # spans more than one.
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
  active_issue_quota: 250 # optional, used by /promote-tasks (linear-promote.md step 7) — the
  # workspace-wide cap on ACTIVE issues (states of type `unstarted` + `started`; Backlog/Done/
  # Canceled are excluded) that Linear's free plan enforces. A HIGH promotion moves an issue
  # Backlog -> Todo, which grows this count, so the promoter checks it before applying a batch
  # rather than discovering the cap mid-run. Must be an integer 0-250 (250 is Linear's actual
  # free-plan hard cap, not an arbitrary ceiling -- the check isn't safely computable past it
  # with this tool). Default 250. Set to 0 to disable the check entirely (e.g. on a paid plan
  # with no cap). Any other out-of-range/non-numeric value disables the check for that run with
  # a warning -- fail OPEN on the quota, not closed like an invalid orphan_claim_hours below;
  # step 8's live-race handling is the backstop that makes that acceptable.
  orphan_claim_hours: 24 # optional, used by /reconcile-tasks row 4 (linear-reconcile.md) —
  # idle-hours threshold before a started+auto-claimed issue with no resolvable PR and no
  # remote branch is treated as an orphaned claim and demoted back to Backlog. Default 24.
  api_key_ref: op://Private/Linear API/credential # optional, used by /archive-tasks —
# a 1Password op:// reference to a Linear PERSONAL API key. The MCP has no archive
# mutation, so the GraphQL issueArchive backstop needs a raw key. Never a literal key
# in THIS file — see api_key below for the local-only plaintext option.
# See linear-config.md → "Archive key" and commands/handlers/linear-archive.md.
# api_key: lin_api_…  # LOCAL-ONLY — a raw key, for operators who would rather not run a
# secret manager. Wins over api_key_ref (nothing to resolve, no approval raised). Like
# api_key_resolver it must NOT appear in this committed file, where it is refused.
# api_key_resolver: opx  # LOCAL-ONLY — shown here for the schema, but it must NOT appear
# in this committed file. It names which allow-listed program turns api_key_ref into the
# key (`op` default, or `opx`) — an IDENTIFIER, never a command line — so it is honored
# only from .task-config.local.yml or $LINEAR_API_KEY_RESOLVER. In the committed config it
# is refused, not ignored. See dev_docs/auth_key_access.md → "Provenance".
# labels support is deferred — the Linear MCP create_issue tool takes label UUIDs, not names,
# and resolving names → ids requires an extra tool call. Add a tag in the body for now.
```

**Schema rules:**

- `projects` **absent or empty** → whole-team scope with the single top-level `wip_limit` (preserves today's "no pin" behavior). **Exactly one** entry → equivalent to today's single pin.
- `wip_limit` stays **top-level** so the repo-pr and gh-issue handlers are untouched; per-project entries override it for Linear only. `max_estimate` stays under `linear:` as the inherited default.
- Per-project override keys are `wip_limit`, `max_estimate`, and `repo` (which consumers read it is stated once, on the `repo` bullet under "Resolve configured projects"). `team`, `base_branch`, `default_priority`, `api_key`, `api_key_ref`, and `api_key_resolver` remain global. `api_key_ref` points at a secret (a full-account bearer token) — its canonical home is the gitignored `.task-config.local.yml`, not the shared `.task-config.yml` (see `commands/task-config.md` → "Local override"). `api_key` (a raw key) and `api_key_resolver` (which names a program) are stricter still: both are honored **only** from `.task-config.local.yml` or the environment, and are refused outright in the committed config (see "Key resolution").
- Each entry's `id` is **required**; `name` is optional (used for prompts/reports; resolved via `list_projects` when absent).
- `remote_batch` is optional and lives under `linear:`. It is the deterministic opt-out for `/do-tasks` batch remote dispatch — `false` degrades `--all` / `-n N` to a single foreground claim; absent or `true` dispatches one remote session per issue (each self-checks for the connector). Default `true`. See `commands/do-tasks.md` §3 "Tracker-batch subroutine".
- `global_wip_limit` is optional and lives under `linear:` (it is Linear-multi-project-specific, unlike the cross-handler top-level `wip_limit`).
- `orphan_claim_hours` is optional and lives under `linear:`. It is read only by `/reconcile-tasks` row 4 (`linear-reconcile.md`) as the idle-hours threshold before an orphaned claim (started + `auto-claimed`, no resolvable PR, no remote branch) is demoted back to Backlog. Default `24`. **Must be a finite number > 0** — it is the sole guard for a fresh pre-branch claim, so row 4 treats a `0`, negative, or non-numeric value as invalid and fails closed (disables the row for that run) rather than mass-demoting.
- `active_issue_quota` is optional and lives under `linear:`. It is read only by `/promote-tasks` (`linear-promote.md` step 7) as the workspace-wide cap on active (`unstarted` + `started`) issues before a HIGH-scored batch is applied. **Must be an integer `0`–`250`** — `250` is Linear's actual free-plan hard cap, not an arbitrary ceiling, and the count is calibrated against it. Default `250`; `0` disables the check. Any other value (negative, non-integer, non-numeric, or `> 250`) is invalid and disables the check for that run, with a warning, rather than doing arithmetic against an uncheckable ceiling. Note this is the **opposite** direction from `orphan_claim_hours`: disabling that row withholds a write (fail closed), whereas disabling this check lets the batch promote unguarded (fail open). It is acceptable only because `linear-promote.md` step 8's live-race handling is the backstop — Linear enforces the real cap, and a rejected write becomes `held (quota)`.
- `unassigned_wip_limit` is optional and lives under `linear:`. It caps the synthetic **Unassigned** bucket (issues with no project or in an unconfigured project) and defaults to the top-level `wip_limit`; `0` means "never ranked-claim unassigned work". It is only meaningful when 1+ projects are configured (with none, the whole-team scope already spans everything).

### Resolve configured projects

Every Linear-handled command that scopes to projects **must call** this one resolution step instead of reading `default_project`/`projects` directly, so inheritance and the whole-team fallback live in exactly one place. It returns a **list** of resolved scopes, each `{ id, name, wip_limit, max_estimate, repo }` with inheritance already applied:

1. Read `linear.projects`. **If absent or empty**, return a single synthetic **whole-team** scope: `{ id: null, name: null, wip_limit: <top-level wip_limit, default 3>, max_estimate: <linear.max_estimate, default 3>, repo: null }`. `id: null` means "omit `projectId` — operate on the whole team", which is exactly today's no-pin behavior. Stop here.
2. **Otherwise**, map each entry to `{ id, name, wip_limit, max_estimate, repo }`:
   - `id` — the entry's required `id` (verbatim UUID).
   - `wip_limit` — the entry's `wip_limit` if set, **else** the top-level `wip_limit` (which itself defaults to `3` when unset).
   - `max_estimate` — the entry's `max_estimate` if set, **else** `linear.max_estimate` (which itself defaults to `3` when unset).
   - `name` — the entry's `name` if set; **else** resolve it **lazily** via `<linear-mcp>__list_projects` (match the `id`) only when a name is actually needed for a prompt or report — never eagerly.
   - `repo` — the entry's `repo` (`owner/name`) if set, **else** `null`. There is **no** inheritance and no default: a repo is a property of the project, not of the workspace, so an absent one must stay absent and let each consumer fall back to the current repo's `origin` at the point of use. Consumers that query GitHub per project read this field: `/find-false-closures` (`linear-false-closures.md` step 2), `/sweep-for-complete`'s PR-discovery fallbacks (`linear-sweep-complete.md` step 3), and `/reconcile-tasks` — which reads it both by delegating to that same sweep step (`linear-reconcile.md` step 2) and directly, to skip a row-4 branch check whose branch lives in another repo (`linear-reconcile.md` row 4). Any consumer that delegates to those steps inherits the field with them.

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

Inputs: the **claim scope set** = the resolved configured projects ("Resolve configured projects") **plus the Unassigned bucket** ("The Unassigned bucket", when 1+ projects are configured); a `--project <value>` flag if passed; and whether the session is **attended** — a human is present to answer an `AskUserQuestion` prompt right now; false for headless `/loop`/cron. `commands/handlers/attendedness.md` is the single home for that notion (this step and the pre-claim WIP gates share it). The steps below say "interactive" where they mean attended; the two are the same thing.

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

**The assignee gate is viewer-relative — and each path resolves "the current Linear user" from its own credential.** The GraphQL fast path reads `assignee { isMe }`, which the Linear API evaluates server-side against the **`$LINEAR_API_KEY`'s** owner; the MCP floor compares `assigneeId` against the **MCP connection's** viewer (`<linear-mcp>__get_user`). Those are two independent identities, so the gate only means the same thing on both paths when the personal API key and the MCP connection belong to the **same Linear user** — see `linear-claim.md` → "Find candidates" for the operator-facing statement of that requirement. When they diverge, the fast path gates against the key's owner and the floor against the MCP's, and the two paths can legitimately disagree about which candidates are "someone else's". Nothing detects this automatically; it is a configuration requirement, not an invariant the code enforces.

**Rank.** Sort remaining issues by Linear `priority`: urgent(1) → high(2) → medium(3) → low(4), then **none(0) last** (Linear stores "no priority" as `0`, so a naive numeric ascending sort would wrongly put it first), then by `updatedAt` ascending (oldest first — let aging cards bubble up).

This block is the single source of truth for `ready` selection. `linear-claim.md` (both the GraphQL fast-path and the MCP floor) and `commands/handlers/assets/linear-ready.py` all implement exactly these gates and this ordering — change them here and update both consumers in lockstep. **Every gate above is enforceable on both paths**, including the assignee gate: `<linear-mcp>__list_issues` returns `assignee`/`assigneeId` on each result, so the floor applies it client-side over data it has already fetched — no per-candidate `<linear-mcp>__get_issue`. The gates themselves are identical on both paths; where the two can still differ is _whose_ viewer the assignee gate is relative to (above) and how much of the backlog each path saw — the MCP floor caps its read at 50 per scope and does not paginate, where `linear-ready.py` paginates to exhaustion (`linear-claim.md` → floor step 4). Neither is a gate difference.

## Fast-path / MCP-floor gate (and the security boundary)

Three GraphQL reads run behind the **same** gate, defined once here: "Ready-candidate selection" (via `linear-ready.py`), "In-flight scan" below (via `linear-scan.py`), and `linear-reoptimize.md`'s relation-graph load (via `linear-relations.py`). Consumers reference this section and pass **which script** they use; everything about failure-handling and the security boundary is identical. Consumers keep their own **fast-path eligibility and fallback granularity** (see "What the gate does not own" below) — the gate owns only what follows.

**Key resolution — do this once before invoking any fast-path script.** The scripts read from the **environment only** and parse no YAML (they are env-in/JSON-out, stdlib-only). So a configured `linear.api_key_ref` reaches them only if this step bridges it — without the bridge the key is inert and every read verb floors, which looks exactly like a deliberately keyless host. The full contract is `dev_docs/auth_key_access.md`; what follows is how this handler applies it.

**Two independent ladders.** A pointer says _which_ secret; a resolver says _how this machine unlocks secrets_. They are bridged separately — never as a pair — so that an inherited `$LINEAR_API_KEY_REF` cannot silently suppress a configured resolver and fall back to plain `op`.

_Pointer:_

1. If `$LINEAR_API_KEY` or `$LINEAR_API_KEY_REF` is **already set** in the environment, bridge no pointer — an inherited value always wins (this is the headless `$OP_SERVICE_ACCOUNT_TOKEN` + `$LINEAR_API_KEY_REF` case, and the launching-terminal export).
2. Otherwise, if `linear.api_key` is set in the **raw `.task-config.local.yml` leaf** — a plaintext key, a supported choice for operators who don't run a secret manager (`linear-config.md` → "Archive key") — bridge it into `$LINEAR_API_KEY` and skip the ref entirely; nothing needs resolving. Read it from the local file in isolation, **not** the merged view: a raw secret in the committed `.task-config.yml` is refused, reported in one line, and never used. Treat the value like any resolved key — never quote it in a log line, a report, or a summary. It does, unavoidably, ride in the bridged command itself (step 5) and therefore in the session transcript; that exposure is inherent to choosing plaintext over a pointer and is documented where the choice is offered (`dev_docs/auth_key_access.md` → "Plaintext keys").
3. Otherwise read `linear.api_key_ref` from the **merged config** — `dev_docs/tasks/.task-config.yml` overlaid with the gitignored `dev_docs/tasks/.task-config.local.yml` (see `commands/task-config.md` → "Local override"). The agent already parses that merged view, so read the leaf directly; no YAML-scraping one-liner.

_Resolver:_

4. If `$LINEAR_API_KEY_RESOLVER` is already set, bridge no resolver. Otherwise read `linear.api_key_resolver` from the **raw `.task-config.local.yml` leaf in isolation** — _not_ the merged view. This is the one key in the repo with that exception, and it is deliberate: a resolver names a program to run, so an untrusted checkout must not be able to supply one. If `api_key_resolver` appears in the **committed** `.task-config.yml`, do not bridge it — say so in one line (`api_key_resolver belongs in .task-config.local.yml, not the committed config — ignoring it`) and carry on with the default. Silently dropping it would fail in the same shape as absence, which is the bug this whole step exists to remove.
5. Bridge whatever you have as a **one-shot environment prefix on the same command that runs the script**, in a **single Bash call** — `LINEAR_API_KEY` when step 2 supplied a plaintext key, otherwise the ref, plus the resolver when there is one:
   ```bash
   LINEAR_API_KEY_REF='<linear.api_key_ref from merged config>' \
     LINEAR_API_KEY_RESOLVER='<linear.api_key_resolver from .task-config.local.yml>' \
     python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/<script>.py" --team "<linear.team>" …
   ```
   Omit either assignment entirely when that ladder produced nothing — an empty value is not the same as an absent one. Two rules, both load-bearing:
   - **Same call.** Each Bash tool call is a **fresh shell**, so a standalone `export` in an earlier call is gone by the time the script runs — the bridge would be silently inert, which is the exact bug this step exists to fix. Never split the assignments and the invocation across two calls.
   - **Single quotes.** The values come from a config file; inside double quotes a ref containing `$(…)`, backticks, or `"` would be evaluated by the shell rather than passed literally. Single-quote them. A `op://` reference legitimately contains spaces (1Password item titles do), which is another reason the quoting is not optional. If a value itself contains a single quote, it is not a valid reference — treat it as a config error and floor rather than trying to escape it.
6. If neither ladder produced anything, pass nothing and invoke the script anyway. It exits non-zero and the run floors — the correct behavior for a keyless host. That fallback still logs the gate's ordinary one-line debug message (see "The gate"); what it must **not** add is any further "your key didn't resolve" explanation, since there was no key to resolve.

The default resolver, `op read`, needs an authorized 1Password session. `op signin` **in the user's own terminal** establishes one that _is_ visible to the agent's tool-spawned subshell — `op` holds the session in its per-user cache daemon (`--cache`, on by default on UNIX), so it crosses the process boundary. Sessions lapse after roughly 30 minutes of inactivity, at which point the fast path floors until the next `op signin`. An approval-based resolver such as `opx` instead raises a dialog per read and invalidates the session afterwards — so a later plain `op read` in the same shell will fail until the next approval, which is that tool working as designed rather than a broken session.

**Make a configured-but-unresolvable key legible.** When step 3 **did** pass a ref and the script still exits non-zero, add **one** line beyond the gate's debug message, naming the cause and the fix — the usual cause is a lapsed `op` session, and the fix is one `op signin`.

**Do not copy the script's stderr verbatim.** The script now redacts the pointer itself — `linear-config.md` → "Keep `api_key_ref` out of the committed config" classifies it as sensitive, since it advertises which vault and item hold a full-account bearer token — and reports a **reason category** rather than the resolver's own output, which could leak the pointer or worse. The categories are `unconfigured`, `no-session`, `not-found`, `denied`, `no-binary`, `timeout`, `empty`, `malformed-ref`, and `unknown-resolver` (defined in `dev_docs/auth_key_access.md`). `unconfigured` is the keyless host — say nothing beyond the gate's own debug line, per step 5 above. Every other category means something _was_ configured and failed, so it gets the line below. Relay the category and the reduced pointer, and turn it into the one fix that matches:

```
Configured linear.api_key_ref (op://<vault>/…) did not resolve: <category>. If your op session has lapsed, run `op signin` in your own terminal.
```

Two categories deserve their own wording because the usual `op signin` advice is wrong for them: `malformed-ref` means the configured value is not a `<scheme>://…` reference at all (a command-prefixed value such as `opx op://…` is the common case — the resolver belongs in `api_key_resolver`, not in the ref), and `unknown-resolver` means `api_key_resolver` names something not on the allow-list.

This stays **non-fatal**: the run floors to MCP and still succeeds. Say it once per run, not per scope. When step 3 passed nothing (no `api_key_ref` anywhere), add nothing — that host simply has no key, and the gate's own debug line already covers it.

**The gate.** When `Bash` is available, attempt the GraphQL fast-path first. On **any** non-zero exit from the script, or stdout that doesn't parse as the expected JSON object, log one debug line (`Fast-path unavailable (<reason>) — falling back to MCP floor.`) and run the MCP floor instead. There is **no** separate `[ -n "$LINEAR_API_KEY" ]` pre-check — the script itself exits fast and non-zero when no key is resolvable, so the fallback **is** the gate. An explicit env pre-check would misgate the headless case where `$OP_SERVICE_ACCOUNT_TOKEN` + `$LINEAR_API_KEY_REF` are set but `$LINEAR_API_KEY` itself is not — that case must still attempt the fast path. A host with no `Bash` tool falls to the floor by construction (nothing to gate).

> **This gate is also the security boundary.** A Linear personal API key (what `linear.api_key_ref` points at) is a full-account bearer token — anyone holding it can read and write everything the key's owner can in Linear. It must **never** be injected into a `claude.ai`/Claude Code **cloud** sandbox. Cloud sessions never set `$LINEAR_API_KEY`/`$LINEAR_API_KEY_REF`/`$LINEAR_API_KEY_RESOLVER`, so even where a cloud host is `Bash`-capable and attempts the script, it exits non-zero before any GraphQL request (no key resolvable) and the run falls to the MCP floor (OAuth-scoped, no raw key) by design — the guarantee is that the key is never _present_, not that the script is never _invoked_. **The "Key resolution" step above does not weaken this**: `api_key_ref` is only ever an `op://` **pointer**, never a key, and its canonical home is the gitignored `.task-config.local.yml`, which a cloud checkout does not have. Even if a checkout did name a ref, resolving it still requires an authorized `op` — which a cloud sandbox has no session for — so the script exits non-zero before any GraphQL request and the run floors. What the step does change is _who decides_: from "the launching environment exported a ref" to "the checkout's merged config names one." Be precise about what enforces this, though: it is **delivery**, not resolution. A cloud launch receives no `.local.yml`, no `$LINEAR_API_KEY_REF`, no `$LINEAR_API_KEY_RESOLVER`, and no raw key — no resolver design can protect an environment that is deliberately handed the key itself. Do not "fix" this by wiring the key into cloud config. (Account-key setup — the `api_key_ref`, the launching-terminal `export`, the headless `$OP_SERVICE_ACCOUNT_TOKEN` path — is in `linear-config.md` "Archive key".)

**What the gate does NOT own — kept per consumer.** _Fast-path eligibility_ (which searches attempt the fast path) and _fallback granularity_ (what unit falls to the floor) differ by caller and stay local to each:

- **Ready-candidate selection (`linear-claim.md` "Find candidates")** — attempts the fast path for the **ranked search only**; the direct-identifier path always stays on the MCP floor; and it falls the **whole search** to the floor when a scope the fast path can't serve is needed (a `--project unassigned` pin, or a specific `--project <name|id>` pin). The no-flag / `--project any` path's Unassigned bucket is served by the fast path itself (`linear-ready.py`'s own exclusion pass), not by falling back.
- **In-flight scan** — the two consumers differ, because `linear-scan.py` exits non-zero as a whole on any scope's failure (no per-scope isolation), so a **batched** call floors its whole batch: **`linear-reconcile.md` row 2** invokes the script **once per resolved scope**, so a failure floors **only** that scope; **`linear-sweep-complete.md`** **batches all configured projects into one call**, so any failure floors the **whole configured-project batch**. In both, the Unassigned scope is a **separate** pass that always floors on its own (the script has no null-project exclusion mode).
- **Relation-graph load (`linear-reoptimize.md`)** — loads the whole graph in one pass, so its fallback granularity is the **whole load** (any failure floors the entire load).

## In-flight scan

The read `/sweep-for-complete` (row 1's "merged → Done") and `/reconcile-tasks` row 2 ("open PR, wrong column") each run to find in-flight issues within a named state scope. Resolving and merge-checking each issue's PR is a **separate downstream step** — the scan itself does not pre-filter to PR-bearing issues (PR resolution has its own attachment → title → branch fallback; a state-scope match with no attachment is still a candidate).

**State scope (parameterized).** The caller names which state-type set applies:

- The merged→Done sweep scans every state id of type `started`.
- Reconcile row 2's "open PR, wrong column" scans every state id of type `backlog` **plus** every state id of type `unstarted`.

Either way, resolve state ids by **type only, never display name** (names are user-configurable — see "Kanban mapping" above).

**Skinny fields.** The scan needs only `id identifier title url state { id type } project { id name }` and **explicitly not `description`**: it never reads issue body text, only state, the issue's own project, and (downstream) the linked PR. The PR attachment URL is fetched differently per backend: GraphQL folds `attachments { nodes { url } }` into the same scan query, while MCP `list_issues` does **not** return attachments — resolve them via a subsequent `get_issue` per issue (as `linear-sweep-complete.md` step 3 does).

**`project` is the issue's own, and `scope` is where it came from — they are not interchangeable.** `project` is `{ id, name }`, or `null` for an issue with no project; consumers key per-project config off `project.id` (see the `repo` bullet under "Resolve configured projects"). `scope` is a separate string naming the query that returned the issue — a configured project id, or the **team** name for a whole-team query. Under `--all` there is exactly one whole-team scope, so `scope` is identical for every issue and carries no project information; only `project.id` answers "which project is this issue in" there. On the **MCP floor** the same distinction holds, but the source differs: `list_issues` results are tagged with their source scope, and the issue's own project comes from the per-issue `get_issue` that `linear-sweep-complete.md` step 3 already makes for attachments — take it from that call rather than adding a read.

**Scope resolution.** Identical to the other commands: with `--all`, a **single whole-team query**, no project resolution and no Unassigned pass; without `--all`, "Resolve configured projects" **plus** the Unassigned bucket's **sweep/reconcile variant** (`projectId == null` only — see "The Unassigned bucket"), **never** the wide claim variant. This scan's only two consumers are the destructive-adjacent verbs `/sweep-for-complete` and `/reconcile-tasks`, so it never uses the claim-path catch-all. The pass is still one extra whole-team query with `projectId` omitted, after which you keep only issues whose `projectId` is `null`.

**Per-scope query count.** MCP's `list_issues` `state` filter is single-valued, so MCP issues one `list_issues` call per resolved scope **per state id**, unioned per scope. GraphQL filters by state type in **one** query per scope (`state: { type: { in: [...] } }`, as `linear-ready.py` does) — never one query per state id.

**Fast-path invocation.** Behind the gate above, the GraphQL fast path is `linear-scan.py`, invoked with each resolved concrete scope's id as `--project` (repeatable — batch every scope in one call, or one call per scope; the consumer's choice, since the script unions either way) and a `--state-type` flag per state type in the caller's set (omit `--project` for the whole-team scope). **Never** pass the `"__unassigned__"` sentinel as `--project` — the script has no Unassigned-exclusion mode, so if the resolved scope set includes the Unassigned bucket, that scope floors (per-scope fallback):

```bash
LINEAR_API_KEY_REF='<from merged config — omit entirely if unset or already in env>' \
  python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-scan.py" --team "<linear.team>" \
  --project "<scope-id>" --state-type <type> [--state-type <type> …]
```

The `LINEAR_API_KEY_REF=` prefix is the "Key resolution" step above, and it must ride on **this same command** — a separate `export` in an earlier Bash call does not survive into this one. Consumers that show a bare `python3 …` invocation are abbreviating; the prefix still applies.

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/linear-scan.py`. Parse stdout as the `{ meta: { viewer, team, states }, issues: [...] }` object in the script's header; a parse failure is itself a fallback trigger (per the gate). `meta.states` (an array of `{ id, name, type }`) replaces the `list_workflow_states` state-id→type map — cache it the same way.

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
