---
title: Canonical ready-candidate selection spec (single source of truth)
priority: high
size: 2
status: new
created: 2026-07-03
source_branch: dpegan/linear-mcp-token-cost
related_files:
  - commands/handlers/linear-common.md
  - commands/handlers/linear-claim.md:37 # §5 Filter (the gates)
  - commands/handlers/linear-claim.md:45 # §6 Rank
parent: linear_mcp_token_cost
tags: [linear, refactor, docs]
---

# Task 1 — Canonical ready-candidate selection spec

Part of [[linear_mcp_token_cost_plan]]. This is the foundation both later tasks cite; do it first.

## Context

Today the ready-candidate **gates** and **ranking** live only inside `linear-claim.md` §"Find candidates" (step 5 = filter, step 6 = rank). The plan adds a _second_ consumer of the same rules — `linear-ready.py` (task 2) — plus a tightened MCP floor (task 3). If each re-derives the gates, they drift, and the fast-path silently surfaces a different "next" than the floor. The findings doc calls this out as the divergence risk ([[findings]] → Alternatives → A, and → "Decision" → resolved question 2).

The fix is to make the gate/rank rules a **single canonical block** in `linear-common.md` (the shared reference every Linear command already reads for the config schema, preflight, and kanban mapping), then have `linear-claim.md` **cite** it rather than restate it. Task 2's script and task 3's floor then both point at the same block.

The exact current rules (from `linear-claim.md`, do not change the semantics — only relocate and canonicalize):

- **Gates** (drop if any fail; each has a fixed reason string):
  - `estimate` null/missing → `no estimate set`
  - `estimate >= <max>` (per-project `max_estimate`, default 3) → `estimate <N> >= <max>`
  - label `auto-claimed` → `already auto-claimed`
  - label `human-approval-requested` → `human-approval-requested`
  - label `blocked` → `blocked`
  - `assignee` set and not the viewer → `assigned to <name>`
- **Rank:** Linear `priority` urgent(1) → high(2) → med(3) → low(4), then **none(0) last** (note: Linear stores none as `0`, so a naive numeric ascending sort is wrong), then `updatedAt` ascending (oldest first).
- **State scope:** only `unstarted`-type states (the kanban `ready` column). Not `backlog`, not `started`.

## Task

1. In `commands/handlers/linear-common.md`, add a new section — **`## Ready-candidate selection`** — placed after "Resolve configured projects" (it depends on the per-project `max_estimate` that step resolves). Its body is the canonical spec:
   - The **state scope** (`unstarted`-type only) with a one-line why.
   - The **gate table**: each gate, its condition, and its **fixed reason string** verbatim (the strings above — callers depend on them for consistent reporting).
   - The **rank rule**, including the explicit "none = `0` sorts last, not first" warning.
   - A one-line note: _"This block is the single source of truth for `ready` selection. `linear-claim.md` (both the GraphQL fast-path and the MCP floor) and `commands/handlers/assets/linear-ready.py` all implement exactly these gates and this ordering — change them here and update both consumers in lockstep."_
2. In `commands/handlers/linear-claim.md`, replace the inline gate list (step 5) and rank text (step 6) with a **reference** to the new block: e.g. "Apply the gates from `linear-common.md` → 'Ready-candidate selection' (the `<max>` is the candidate's resolved per-project `max_estimate`)." Keep step 5/6's surrounding prose (scope tagging, the union-across-scopes note, what step 7 returns) — only the gate/rank _definitions_ move. The claim flow's behavior is unchanged.
3. Leave `linear-list.md` as-is — its grouping is a different (kanban-wide) classification, not ready-candidate selection; don't force it onto this block.

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes (`dprint check` formatting, `claude plugin validate . --strict`, `scripts/validate.py`). Run `dprint fmt` on the two edited files before checking.
- `rg -n "no estimate set|already auto-claimed|assigned to" commands/handlers/linear-claim.md` returns **no** gate _definitions_ — only a reference to the common block (the reason strings now live once, in `linear-common.md`).

**User-run:**

- Read the diff and confirm the gates + rank in the new `linear-common.md` block are semantically identical to the pre-change `linear-claim.md` §5–6 (no threshold, reason-string, or ordering change slipped in).
- Confirm `linear-claim.md` still reads coherently end-to-end for someone following the claim flow — the reference doesn't leave a dangling "apply the gates" with no gates in sight.

## Notes

- This task is pure relocation + citation; it ships value on its own (one source of truth for the existing MCP path) even before tasks 2–3 land.
- Re: the plan's open question on parity strictness — this task implements option (a) (prose SoT + review). If we later want option (b), the block's fixed reason strings are what a `validate.py` check would diff against the script's emitted strings, so name them exactly.
