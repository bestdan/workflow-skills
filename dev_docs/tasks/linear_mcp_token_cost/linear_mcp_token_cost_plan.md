---
type: epic
title: Linear MCP token-cost fix — GraphQL fast-path for find-candidates
status: active
owner: Daniel Egan
created: 2026-07-03
---

# Linear MCP token-cost fix

Implements the decision recorded in [[findings]] (`dev_docs/tasks/linear_mcp_token_cost/findings.md`). Read that first — it holds the motivation, the alternatives, and the resolved design. This file is the executable slice of it.

## Goal

Cut the token cost of the task loop's single most frequent operation — "grab the next task" (`/do-tasks` → `linear-claim.md` §"Find candidates") — by replacing the ~6-call MCP fan-out with **one filtered GraphQL request** on hosts where a local shell can resolve `$LINEAR_API_KEY`. Everywhere a script can't run or a raw key must not go (claude.ai/code cloud agents, web-without-shell, mobile), a **tightened MCP path** stays the universal floor. The switch is automatic and host-gated; nothing breaks when the key is absent.

## Approach

**A + B, layered and host-gated** (see [[findings]] → "Decision"):

- **Fast-path (local):** a single-purpose `linear-ready.py` (mirrors the shipped `linear-archive.py`) runs one GraphQL query, applies the ready-candidate gates + ranking, and prints ranked candidate JSON. `linear-claim.md` shells out to it.
- **Floor (everywhere):** the existing MCP fan-out, tightened with every server-side filter the `list_issues` tool actually supports. It stays fat by necessity — the tool has **no `estimate` filter** and can't _exclude_ labels, which is exactly why the fast-path exists.
- **Gate switch:** whether `$LINEAR_API_KEY` resolves in a runnable shell — **not** how the MCP was installed. Cloud never sets the key, so it falls to the floor; that same gate is the security boundary for a full-account bearer token.

The main tradeoff: a hand-written query is a _second_ implementation of "what is ready." We neutralize the divergence risk by making the gate/rank rules a **single canonical spec** (task 1) that both the script (task 2) and the MCP floor (task 3) cite verbatim rather than each re-deriving.

## Scope / non-goals

- **In scope:** the hot `find-candidates` read path only — the script, the `linear-claim.md` wiring, and the shared-gate single-source-of-truth block.
- **Out of scope:**
  - `/list-tasks` (`linear-list.md`) — occasional, and it needs `backlog`/`started`/`done` sections the ready-candidates query doesn't serve. Stays on the MCP.
  - The **claim election itself** — the token-comment lock, `save_issue`, `save_comment`, `get_user`, and the whole mutating flow in `linear-claim.md` stay MCP-native. The script only shortcuts step 4's read fan-out and feeds ranked candidates into an otherwise-untouched claim flow.
  - Mutations (`status`, `comment`) — not touched.
  - Building an OAuth app or any auth mechanism beyond reusing `linear.api_key_ref` + `$LINEAR_API_KEY`.

## Tasks

1. [[linear_mcp_token_cost_task_1]] — **Canonical ready-candidate selection spec.** Extract the gates + ranking from `linear-claim.md` into a citeable block in `linear-common.md`; leave `linear-claim.md` referencing it. The single source of truth both later tasks point at.
2. [[linear_mcp_token_cost_task_2]] — **`linear-ready.py` GraphQL fast-path script.** One filtered query, skinny fields (incl. `branchName`), gates + rank per task 1, ranked JSON to stdout. Mirrors `linear-archive.py`.
3. [[linear_mcp_token_cost_task_3]] — **Wire `linear-claim.md`.** Host-gate on `$LINEAR_API_KEY`: fast-path calls task 2's script; floor runs the tightened MCP path citing task 1's spec. Auth/env docs (reuse `api_key_ref`, the `op`-in-agent-shell gotcha, the cloud security boundary).

## Open questions

- **How strict a parity guard between the script and the floor?** Task 1 gives them a shared prose spec, but nothing _automatically_ asserts the Python gates match the prose. Options: (a) prose-only + a review checklist (cheapest, what the tasks assume today); (b) the script emits its gate/reason strings and a `validate.py` check diffs them against the spec block; (c) a golden-fixture test feeding one issue set through both paths. Currently planned as (a); escalate if the two drift in practice. See task 1 acceptance criteria.
- **Should `linear.api_key_ref` being read-capable be surfaced at `/task-config` time?** Today the key is presented as archive-only and not prompted for. Making reads faster is a new reason a user might want to set it. Task 3 documents it in the handler files but does _not_ add a `/task-config` prompt — confirm that's the right restraint.
