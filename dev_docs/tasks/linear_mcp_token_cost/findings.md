# Findings: Linear MCP token cost in the task-loop handlers

**Date:** 2026-07-03
**Status:** Decision committed (2026-07-03) — see [Decision](#decision). **A + B, layered and host-gated:** a GraphQL fast-path locally, a tightened MCP floor everywhere else.
**Scope:** The `linear` handler's read paths — `linear-claim.md` (find candidates for `/do-tasks`), `linear-list.md` (`/list-tasks`), and the preflight in `linear-common.md`. Mutations and other handlers (gh-issue/jira/repo-pr) are out of scope for now.

## Motivation

The Linear MCP is the dominant token sink when driving the task loop, and the worst offender is the single most frequent operation: **"grab the next task."** The user reports reaching for it constantly, and each invocation costs far more than the ~5 lines of signal it returns. This doc records where the cost comes from and what the realistic alternatives are, so we can design a fix rather than pick one blind.

## Where the tokens actually go

Two independent costs, with different fixes:

### 1. Tool-schema bloat (context-resident)

The Linear connector exposes **~65 tools**. Loaded eagerly, that is tens of thousands of tokens of JSON schema resident in the system prompt of _every_ turn, whether or not Linear is touched.

- Mitigated **in some harnesses** by deferred/on-demand tool loading (tools load via a search step only when needed). Where that is active, #1 is mostly handled.
- **Not** mitigated in a plain agent session or clients without deferral — there the full schema is always resident.
- We don't control the connector's schema (it's a remote MCP at `https://mcp.linear.app/mcp`), so the only levers are: disconnect when unused, or rely on the harness's deferral.

### 2. Response verbosity + call fan-out (per-operation)

This is the one that hurts on every "next task," and the one we _can_ fix in the handlers.

The `/do-tasks` "find candidates" flow (`linear-claim.md` §"Find candidates") is not one call — it is a **chain**:

1. `list_teams` — resolve `<linear.team>` → team id
2. `list_workflow_states` (teamId) — resolve the state-id → type map, to find `unstarted`-type states
3. `list_projects` (per "Resolve configured projects") — resolve configured project scopes
4. `list_issues` — **once per configured scope**, limit 50 each, returning full issue objects
5. `get_user` — resolve the viewer to apply the assignee gate
6. `get_issue` — lazily, to fetch `branchName` for the top candidate(s)

Each `list_issues` node is a fat object (title, description, nested state/team/project/labels/assignee, estimate, timestamps, urls). Pulling up to 50 per scope to then **discard almost all** of them via client-side gates (estimate set and `< max_estimate`, none of `blocked`/`auto-claimed`/`human-approval-requested`, assignee unset-or-me, ranked by priority then oldest) means we pay for a multi-KB payload to surface a handful of ranked identifiers. `/list-tasks` (`linear-list.md`) has the same shape — a per-scope `list_issues` at limit 20, plus `list_workflow_states`.

**The "ready" selection is fully specified and deterministic** (see the exact gates in `linear-claim.md` §5–6), which is what makes a cheaper equivalent feasible: everything the fan-out computes could come from one scoped query.

## What "next" means (so any alternative stays faithful)

From `linear-claim.md`, a `/do-tasks` candidate is:

- **State:** type `unstarted` (the kanban "ready" column). Not `backlog` (unrefined → `/promote-tasks` first), not `started` (already claimed).
- **Gates (drop if any fail):** `estimate` null → drop; `estimate >= max_estimate` (default 3) → drop; label `blocked` / `auto-claimed` / `human-approval-requested` → drop; `assignee` set and not the viewer → drop.
- **Rank:** Linear priority urgent(1)→high(2)→med(3)→low(4), then none(0) **last** (note: Linear stores none as `0`, so a naive numeric ascending sort is wrong), then `updatedAt` ascending (oldest first).
- **Scope:** team `<linear.team>` across the configured `linear.projects` (whole-team when none configured), per-project `max_estimate`/`wip_limit` inheritance applied.

Any token-frugal replacement must reproduce these gates and this ordering, or it will surface a different "next" than `/do-tasks` claims — the two must not diverge.

## Alternatives considered

### A. Direct GraphQL query (bypass the MCP for the read)

**Chosen — the local fast-path.** Linear's GraphQL API (`https://api.linear.app/graphql`) supports server-side filtering and a scoped field selection, collapsing the whole find-candidates chain into **one request** returning only the fields the gates and ranking need. A prototype query filters `team.name.eq` + `state.type.eq: "unstarted"` and selects `identifier/title/priority/estimate/updatedAt/assignee.isMe/labels/state/project` — the gates and ordering then run in `jq`. Estimated cost: **~100 tokens vs several KB across ~6 calls.**

Open questions / risks:

- **Auth:** needs a Linear **personal API key**, which the MCP's OAuth session cannot provide. There is already a convention for exactly this — `linear.api_key_ref` (a full `op://vault/item/field` reference) used by `/archive-tasks`'s GraphQL backstop (`linear-config.md` §"Archive key"). Reusing it means the key does double duty.
- **1Password reachability:** with 1Password **desktop-app integration**, `op read`/`op item get` only unlocks in the user's _authorized terminal_, **not** in an agent's tool-spawned subshell (errors `account is not signed in`). So an agent calling the helper needs `$LINEAR_API_KEY` exported or an `OP_SERVICE_ACCOUNT_TOKEN`. This is the same constraint that already shapes the archive backstop ("happiest as a standalone script … scheduled on a cron").
- **Server-side vs client-side gates:** some gates (labels, `isMe`) can move into the GraphQL filter to shrink the payload further; worth deciding which stay in `jq` for clarity vs which push server-side for cost.
- **Divergence risk:** a hand-written query is a _second_ source of truth for "what is ready." If the handler's gate logic changes, the query must change in lockstep. Needs a single-source strategy (shared snippet, generated query, or a test that asserts parity).

### B. Constrain the MCP calls (stay on the MCP, shrink the payload)

**Chosen — the universal floor.** Keep the MCP but always pass tight `limit`s, filter server-side by team/state/assignee, and prefer `get_issue`(known id) over `list_issues`. Cheaper than today, but still multi-call and still returns fat objects — a partial win that keeps the fan-out.

**Its ceiling is low, and that's the whole reason A exists.** Checked against the connector's `list_issues` schema, the server-side filters are `team`, `project`, `state`, `assignee` (a single value — `me` or `null`), `label` (a single **include**), `priority` (single value), and `limit` (≤250). There is **no `estimate` filter**, and labels can only be _included_, not _excluded_. But the candidate gates (`linear-claim.md` §5) are dominated by an **estimate threshold** and three **label exclusions** (`blocked` / `auto-claimed` / `human-approval-requested`) — none of which can move server-side. So the MCP floor must always fetch fat objects for cards it then discards client-side. B genuinely tops out at "modest"; only a hand-written query can filter on `estimate` and select skinny fields.

### C. Selective connection (orthogonal, free)

**Adopted as the free baseline.** Disconnect the Linear connector in sessions that aren't doing Linear work (kills #1 entirely). Doesn't help the per-operation cost when Linear _is_ in use, but it's the zero-effort baseline and composes with A or B.

### Non-starter: RTK

RTK filters shell-command output, not MCP tool responses — it can't touch either cost.

## Where a fix would live

- A shared read helper referenced by both `linear-claim.md` (find candidates) and `linear-list.md` (snapshot), so the two never diverge on "ready."
- Auth/config: reuse `linear.api_key_ref` from `linear-config.md`; document the `$LINEAR_API_KEY` / service-account escape hatch for agent subshells, mirroring the archive backstop's guidance.
- Whatever we choose must preserve the untouched invariants the claim flow depends on (the tracker path never sets a terminal state; the claim/preflight semantics are unchanged — this is only about _reading_ candidates cheaply).

## Decision

**A + B, layered and host-gated.** The find-candidates read runs one of two ways, chosen at runtime by whether a local shell can resolve `$LINEAR_API_KEY`:

- **Fast-path (local): a single-purpose GraphQL script.** Mirror `linear-archive.py` with a `linear-ready.py` (or equivalent) that owns the gates + ranking and prints ranked candidate JSON. One filtered request, skinny field selection — **~100 tokens vs several KB across ~6 calls** (~40×). `linear-claim.md` §"Find candidates" shells out to it; the script is the **single source of truth for the gates**, so the handler never re-implements them.
- **Floor (everywhere): the tightened MCP path (B).** When `$LINEAR_API_KEY` is absent, use the MCP with tight limits and every server-side filter it does support. This is the only path that works where a script can't run or a raw key must not go.

### The gate is host capability, not connector namespace

The switch is purely "**is `$LINEAR_API_KEY` resolvable in a runnable shell?**" — _not_ how the MCP was installed. The script hits `api.linear.app/graphql` directly, independent of the connector, so the `claude_ai_Linear` vs `linear` install mode is irrelevant. (This very session proves the two are orthogonal: a `claude_ai_Linear` connector on a fully-local host.) The gate degrades correctly everywhere: local CLI with the key exported → fast-path; cloud/CI with an injected env var → fast-path; **claude.ai/code cloud agents, web-without-shell, mobile → silently the MCP floor.**

### Security boundary = the same gate

Cloud agents get the token win **nowhere**, by design. A Linear personal API key is a **full-account bearer token** — Linear has no read-only personal key, and the MCP's OAuth token can't be extracted for raw GraphQL — so it must never be injected into a cloud sandbox (claude.ai/code). The `$LINEAR_API_KEY` gate enforces this for free: you simply never set it in cloud, so the run falls to the MCP floor. The mechanism that degrades gracefully **is** the security boundary. Locally nothing new is exposed — the key already lives on the machine in `op`.

### Auth, resolved (the `op`-in-agent-shell gotcha)

Locally, resolve the key **once into the launching terminal's env** — `export LINEAR_API_KEY=$(op read "$LINEAR_API_KEY_REF")` — before starting Claude Code. Every Bash tool call then **inherits** it; no per-call `op`, no key in the transcript. This sidesteps the archive handler's gotcha, which is about _invoking_ `op` inside the agent's tool-spawned subshell — inheriting an already-resolved env var is fine. Headless/CI sets `$LINEAR_API_KEY` (or `$OP_SERVICE_ACCOUNT_TOKEN` + `$LINEAR_API_KEY_REF`) in the job env. Reuse the existing `linear.api_key_ref` config (`linear-config.md` → "Archive key") — the key does double duty for archive and reads.

### Resolved open questions

1. **A, B, or A+C?** → **All three, layered.** A locally, B as the universal floor, C as the free orthogonal baseline.
2. **Single source of truth for the gates?** → The **script owns them**; `linear-claim.md` shells out. No parity test or generated query needed because there is only one implementation. (Trade-off: the MCP-floor path in B still applies the gates client-side, so that path and the script must agree — keep the floor's gate list a verbatim reference to the script's, not a re-derivation.)
3. **Auth story?** → Local env-inheritance from the launching terminal; cloud deliberately _without_ a key → MCP floor. See "Auth, resolved" above.
4. **Replace or supplement?** → **Supplement.** The MCP path is the always-present floor; the GraphQL read is the auto-detected fast lane on top.
5. **Scope?** → **Reads only, and only the hot `find-candidates` path.** `/list-tasks` stays on the MCP for now (occasional, and it needs the `backlog`/`started`/`done` sections the ready-candidates query doesn't serve). Mutations (`status`, `comment`) are out of scope — the claim election in `linear-claim.md` deliberately stays MCP-native.
