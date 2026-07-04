---
title: Wire linear-claim.md — host-gated GraphQL fast-path + tightened MCP floor
priority: high
size: 3
status: new
created: 2026-07-03
source_branch: dpegan/linear-mcp-token-cost
related_files:
  - commands/handlers/linear-claim.md # Find candidates §1–7
  - commands/handlers/linear-config.md # Archive key → note read reuse
  - commands/handlers/assets/linear-ready.py # the script this calls (task 2)
  - commands/handlers/linear-common.md # Ready-candidate selection (task 1)
is_blocked_by: linear_mcp_token_cost_task_2
parent: linear_mcp_token_cost
tags: [linear, handler, docs]
---

# Task 3 — Wire `linear-claim.md` to the fast-path, with the MCP floor

Part of [[linear_mcp_token_cost_plan]]. Depends on [[linear_mcp_token_cost_task_2]] (the script) and transitively [[linear_mcp_token_cost_task_1]] (the spec both paths cite).

## Context

`linear-claim.md` §"Find candidates" is the hot path. This task makes it choose between two implementations at runtime and documents the auth story — the last piece of the [[findings]] "Decision".

**The gate is host capability, not connector namespace:** does a local shell resolve `$LINEAR_API_KEY`? ([[findings]] → "Decision" → "The gate is host capability".) This same gate is the **security boundary**: a Linear personal API key is a full-account bearer token (Linear has no read-only personal key; the MCP's OAuth token can't be extracted for raw GraphQL), so it must never be injected into a claude.ai/code cloud sandbox. Cloud simply never sets the key → the run falls to the floor. Local nothing-new is exposed (the key already lives in `op`).

**Auth mechanics** (the `op`-in-agent-shell gotcha, [[findings]] → "Auth, resolved"): resolve the key **once into the launching terminal's env** — `export LINEAR_API_KEY=$(op read "$LINEAR_API_KEY_REF")` before starting Claude Code — so every Bash tool call **inherits** it. The gotcha is about _invoking_ `op` inside the agent's subshell; inheriting an already-resolved env var is fine. Headless/CI sets `$LINEAR_API_KEY` (or `$OP_SERVICE_ACCOUNT_TOKEN` + `$LINEAR_API_KEY_REF`) in the job env. Reuses the existing `linear.api_key_ref` config — the key does double duty for archive and reads.

`linear-claim.md`'s command already uses Bash (`gh`, `git ls-remote`) in Pre-flight, so shelling out to `python3 …/linear-ready.py` needs no new `allowed-tools` grant — but confirm the command's `allowed-tools` in the `/do-tasks` frontmatter permits `Bash` for the assets path.

## Task

In `commands/handlers/linear-claim.md` §"Find candidates":

1. **Add the host-gate at the top of the candidate search** (after step 1 Preflight, which the fast-path still needs for the resolved team id — or have the script's own team resolution stand in; prefer keeping the shared preflight for the team-not-found error message, then branch):
   > **If `$LINEAR_API_KEY` is resolvable in this shell** (`[ -n "$LINEAR_API_KEY" ]`, or it can be resolved from `linear.api_key_ref` per the auth note), take the **GraphQL fast-path**; otherwise run the **MCP floor** (steps 2–6 below). The two select the _same_ candidates by construction (both implement `linear-common.md` → "Ready-candidate selection").
2. **Fast-path block:**
   - Resolve project scopes via `linear-common.md` → "Resolve configured projects" (same as the floor), pass them to the script as `--project <uuid>[:<max>]` args (+ `--team`, `--max-estimate` default).
   - Call `python3 commands/handlers/assets/linear-ready.py …` (Glob `**/handlers/assets/linear-ready.py` if the relative path doesn't resolve, mirroring how `linear-archive.md` references its script). Parse the JSON array from stdout into the ranked candidate list.
   - The parsed list **already has** `branchName` and per-project `project` scope, so it feeds directly into step 7's return shape — **skip the lazy `get_issue` for `branchName`** on this path.
   - On a **non-zero exit / parse failure**, log it and **fall through to the MCP floor** — the fast-path is an optimization, never a hard dependency.
3. **MCP floor (steps 2–6):** keep today's flow but **tighten** it and **cite the spec**:
   - Pass every server-side filter the `list_issues` tool supports: `team`, `state` (the `unstarted` type), `project` per scope, and a tight `limit`. Note inline that `estimate` and label-**exclusion** have no server-side filter, so those gates stay client-side — this is _why_ the floor stays fat and the fast-path exists (link [[findings]] → Alternatives → B).
   - Replace the inline gate/rank definitions with a reference to `linear-common.md` → "Ready-candidate selection" (task 1 already moved them; ensure the floor cites, not restates).
4. **Direct-identifier path** (`/do-tasks PRE-12`): leave on the MCP `get_issue` (single cheap call, no fan-out to optimize) — the fast-path targets the ranked search, not a known id. Say so explicitly so nobody wires the script into the single-id path.
5. **Auth/config docs:**
   - Add a short **"API key enables the read fast-path"** note near the top of §"Find candidates" (or a callout) pointing at the auth mechanics above: reuse `linear.api_key_ref`; export into the launching terminal; cloud stays on the floor by design.
   - In `commands/handlers/linear-config.md` → "Archive key", add one sentence: the same `api_key_ref` now **also** powers the `/do-tasks` read fast-path (`linear-claim.md`), so a user who sets it for archiving gets cheaper task-grabs for free — but it remains **optional** and is still **not** prompted for at `/task-config` time.

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes (`dprint fmt` the edited markdown first; `claude plugin validate . --strict`; `scripts/validate.py`).
- `rg -n "linear-ready.py" commands/handlers/linear-claim.md` shows the fast-path call; `rg -n "Ready-candidate selection" commands/handlers/linear-claim.md` shows both paths citing the shared spec (no re-stated gates).

**User-run:**

- **Fast-path:** with `$LINEAR_API_KEY` exported, run `/do-tasks` (or `--claim-only`) against a real team; confirm it finds and claims the same top candidate the pre-change MCP path would, using the script (verify via the transcript that `linear-ready.py` ran and the MCP fan-out did not).
- **Floor:** unset `$LINEAR_API_KEY`, re-run `/do-tasks`; confirm it silently uses the MCP path and picks the same candidate. No error, no mention of a missing key beyond a one-line debug note.
- **Fallback:** with a _bad_ `$LINEAR_API_KEY`, confirm the script errors and `/do-tasks` falls through to the MCP floor rather than aborting the run.
- **Claim flow intact:** confirm the token-comment election, `save_issue`, and move-to-review are unchanged — the script only replaced the read, the mutating claim is still MCP-native.

## Notes

- Do **not** move the claim election, `save_comment`, or `save_issue` onto GraphQL — out of scope ([[linear_mcp_token_cost_plan]] non-goals). This task ends where the ranked candidate list is handed to Pre-flight.
- If confirming `allowed-tools` reveals the `/do-tasks` command can't shell to `python3`, add the minimal grant in this task — it's a prerequisite for the fast-path to run at all.
