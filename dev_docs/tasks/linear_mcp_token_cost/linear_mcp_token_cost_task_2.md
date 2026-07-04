---
title: linear-ready.py — GraphQL fast-path script for find-candidates
priority: high
size: 3
status: new
created: 2026-07-03
source_branch: dpegan/linear-mcp-token-cost
related_files:
  - commands/handlers/assets/linear-ready.py # new file
  - commands/handlers/assets/linear-archive.py # mirror its structure
  - commands/handlers/linear-common.md # Ready-candidate selection (task 1) + Resolve configured projects
is_blocked_by: linear_mcp_token_cost_task_1
parent: linear_mcp_token_cost
tags: [linear, script, graphql]
---

# Task 2 — `linear-ready.py` GraphQL fast-path script

Part of [[linear_mcp_token_cost_plan]]. Depends on [[linear_mcp_token_cost_task_1]] (the canonical gate/rank spec this script implements).

## Context

The find-candidates MCP fan-out (`list_teams` → `list_workflow_states` → `list_issues` per scope → `get_user` → lazy `get_issue` for `branchName`) returns multi-KB fat objects to surface a handful of ranked identifiers ([[findings]] → "Where the tokens actually go" §2). One filtered GraphQL request with skinny field selection replaces all of it at **~100 tokens vs several KB across ~6 calls** ([[findings]] → Alternatives → A).

**Mirror the shipped `commands/handlers/assets/linear-archive.py`** — it's the validated precedent for this exact shape (GraphQL against `https://api.linear.app/graphql`, personal API key, `op read` fallback, UUID-vs-name team detection, cursor pagination, dry-run-by-default safety on the mutating path). This script is the **read** sibling: no mutation, so no `--apply` gate — it only ever prints.

Key resolution is identical to `linear-archive.py`'s `get_key()`: `$LINEAR_API_KEY`, else `op read "$LINEAR_API_KEY_REF"`. Reuse that function's logic verbatim. The `op`-in-agent-shell gotcha (only an authorized terminal unlocks `op`) is handled at the **caller** layer in task 3 by exporting the key into the launching terminal's env — this script just reads `$LINEAR_API_KEY` and doesn't care how it got there.

GraphQL fields available on Linear's `Issue` that the gates + rank + downstream claim need: `id identifier title priority estimate updatedAt branchName`, `assignee { id isMe displayName }`, `labels { nodes { name } }`, `state { id type }`, `project { id name }`. Selecting `branchName` here is a bonus win — it eliminates the lazy `get_issue` the MCP path needs (`linear-claim.md` step 7 / Pre-flight step 1).

Server-side filter surface (from the connector's schema / Linear GraphQL): `team` (id or name — the script auto-detects UUID like archive does), `state: { type: { eq: "unstarted" } }`, and `project: { id: { eq } }` per scope. `estimate` and label-**exclusion** have **no** server-side filter, so those gates run client-side in Python — same as the MCP floor must. Do them in the script per task 1's spec.

## Task

Create `commands/handlers/assets/linear-ready.py`, executable (`chmod +x`), mirroring `linear-archive.py`:

1. **CLI args:**
   - `--team` (required; also `$LINEAR_TEAM`) — name or UUID, auto-detected via the same `UUID_RE` as archive.
   - `--project <uuid>` **repeatable** (`action="append"`), each optionally carrying a per-project `max_estimate`. Simplest shape: `--project <uuid>` plus a parallel `--max-estimate <N>` default, and accept per-project overrides as `--project <uuid>:<max>` parsed by the script (document whichever you pick). No `--project` → whole-team scope (omit the `project` filter), using the default `--max-estimate`.
   - `--max-estimate` (default `3`; also `$LINEAR_MAX_ESTIMATE`) — the exclusive upper bound, inherited when a project has no override. Matches `linear.max_estimate`.
   - `--limit` (default `50`) — per-scope page size, matching `linear-claim.md` step 4.
2. **Query** — one GraphQL call per resolved scope (loop projects like archive loops terminal types), paginating on `pageInfo.hasNextPage`:
   ```graphql
   query($cursor: String, $team: String!, $project: ID) {
     issues(first: <limit>, after: $cursor, filter: {
       team: { %s: { eq: $team } },      # id if UUID-shaped, else name
       state: { type: { eq: "unstarted" } },
       %s                                 # project: { id: { eq: $project } } when scoped
     }) {
       nodes {
         id identifier title priority estimate updatedAt branchName
         assignee { id isMe displayName }
         labels { nodes { name } }
         state { id type }
         project { id name }
       }
       pageInfo { hasNextPage endCursor }
     }
   }
   ```
   Tag each returned issue with its source scope's `{ id, name, max_estimate }` (so the gate uses the right per-project `max_estimate` and the caller's WIP gate reads the right cap). Union across scopes; no dedup needed (an issue belongs to one project).
3. **Gates + rank — implement task 1's canonical spec exactly**, client-side:
   - Drop: `estimate` null; `estimate >= that scope's max_estimate`; label in `{auto-claimed, human-approval-requested, blocked}`; `assignee` set and `assignee.isMe` false.
   - Rank: priority urgent(1)→high(2)→med(3)→low(4) then none(0) **last** (map `0`→`+inf` rank key, do **not** sort numeric ascending), then `updatedAt` ascending. Add a comment on the sort key citing `linear-common.md` → "Ready-candidate selection".
4. **Output:** a JSON array to stdout, ranked, each element carrying the fields `linear-claim.md` step 7 expects: `id, identifier, title, priority, estimate, description?, labels, url, branchName, project: { id, name, max_estimate }`, plus `state { id type }`. (`description` and `url` are optional selections — add `url description` to the query if cheap; the claim flow can also fetch description lazily. Prefer including `url` since it's tiny and the report uses it.) Stdout is **only** the JSON (send any human/log lines to stderr) so the caller can pipe straight into a parser.
5. **No mutation, no `--apply`.** Reuse archive's `get_key()` and `gql()` helpers (same `Authorization: <key>` header, no `Bearer`). On GraphQL error, exit non-zero with the error on stderr so the caller's host-gate can fall back to the MCP floor.

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes (the new `.py` must satisfy `dprint`/plugin-validate; if `validate.py` lints asset scripts, satisfy it). `python3 -c "import ast; ast.parse(open('commands/handlers/assets/linear-ready.py').read())"` succeeds (syntax).
- `python3 commands/handlers/assets/linear-ready.py --help` prints usage and exits 0 (argparse wired).
- With no key available, the script exits non-zero with the same "No Linear API key…" guidance as `linear-archive.py` (copy the message).

**User-run (needs a real Linear workspace + `$LINEAR_API_KEY`):**

- `LINEAR_API_KEY=… python3 commands/handlers/assets/linear-ready.py --team <team> --max-estimate 3` prints a JSON array of ready candidates, ranked, each with `identifier`, `estimate < 3`, no excluded labels, `branchName` populated.
- Spot-check the ranking against the Linear board: urgent/high/med/low order, none last, oldest-first within a priority.
- Cross-check the returned set against the current MCP path's step-5 output for the same team/scope — the two must select the **same** issues (parity with task 1's spec). Note any divergence as a bug in either this script or the spec.
- Confirm stdout is pure JSON (`… | python3 -m json.tool` succeeds); logs go to stderr.

## Notes

- Keep it single-purpose — this is not a CLI with subcommands. Same weight as `linear-archive.py` (~200 lines).
- Don't build `--project`-narrowing UX beyond what task 3's caller needs; the caller passes the already-resolved project scopes from `linear-common.md` → "Resolve configured projects".
