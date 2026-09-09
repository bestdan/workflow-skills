---
type: epic
title: Move hand-walked prose logic into tested scripts (2026-09 index)
status: active
owner: dan
created: 2026-09-09
---

# Prose-to-code extraction, round 2

Source: [`dev_docs/2026-09-09-prose-to-code-index.md`](../../2026-09-09-prose-to-code-index.md) (PR #522).
Row numbers below refer to that index.

## Goal

Ship the Tier 1 findings and the two cross-handler collapses as one PR each, so
the procedures the prose currently asks the agent to hand-walk — Jira transition
resolution, graph analysis, PR discovery, the reserve gate — become tested code
with stdout as the contract, per `CONTRIBUTING.md` → "Logic goes in a typed file".

## Scope / non-goals

- **In:** index rows 1, 2, 3, 4, 5, 6, 7, 9 (this repo's half), 10, 11, 12, 16, 17.
- **Out:** rows 8, 25, 26, 39 (they live in `bestdan/dotfiles` or the papercuts
  plugin — file there); Tier 2 rows 13–15, 18–24, 27–31 and all of Tier 3 (a
  later round, after this one shows which shapes hold up); anything that moves
  an MCP fetch or write into a script (the standing constraint: scripts own the
  decision over fetched JSON, never the fetch or the write).

## Approach

Every task follows the repo mold: a runtime helper in
`commands/handlers/assets/<name>.py` (or a `scripts/` entrypoint when it is
gate-side), a `scripts/test_<name>.py` + `scripts/test-<name>.sh` pair, the
prose rewritten to call it and describe what it prints. Where a script already
owns half the job (`gh-issue-graph.py`, `gh-issue-claim.py`,
`linear-false-closures.py`, `spawn-orchestrator.sh`, `preflight.sh`) extend it
rather than adding a sibling — two sources of truth for one decision is the
defect class the index exists to remove. The trade-off accepted: Jira and
Linear MCP-floor paths still fetch by hand and pass JSON to the helper, so the
prose stays longer than a pure-script path would; that is the constraint, not a
gap.

## Tasks

1. [[prose_to_code_task_1]] — `jira-resolve-transition.py`, two modes, five call sites (row 2).
2. [[prose_to_code_task_2]] — `diff-anchor-check.py` for co-review's batched review POST (row 10).
3. [[prose_to_code_task_3]] — `tutorial-root-guard.sh` before the research-spike tutorial's `rm -rf` (row 11).
4. [[prose_to_code_task_4]] — `linear-graph-analyze.py`: cycles, topological order, priority inversions (row 4).
5. [[prose_to_code_task_5]] — `gh-issue-graph.py --sort` for reoptimize Dimension 3 (row 4, gh-issue half).
6. [[prose_to_code_task_6]] — `body_references` extraction in both graph scripts (row 3).
7. [[prose_to_code_task_7]] — `linear-pr-resolve.py`: three-source PR discovery and merge-state classification (row 1).
8. [[prose_to_code_task_8]] — `spawn-orchestrator.sh reserve-gate`: `usage_delta` bookkeeping and go/no-go (row 5).
9. [[prose_to_code_task_9]] — generalise `gh-issue-claim.py acquire` for the shared claim-lock prose and Jira (row 6).
10. [[prose_to_code_task_10]] — `preflight.sh --scout-run-md`: task backend ∈ environment fingerprint (row 7).
11. [[prose_to_code_task_11]] — `linear-false-closures.py --from-mcp-json`: build the merged-PR file (row 9).
12. [[prose_to_code_task_12]] — `check-reproducibility.sh` for the fact-reviewer's re-run check (row 12).
13. [[prose_to_code_task_13]] — `kanban-classify.py` shared by the three list handlers (row 16).
14. [[prose_to_code_task_14]] — `_linear_rank.py`: one `gate()`/`rank_key()` for fast path and MCP floor (row 17).
15. [[prose_to_code_task_15]] — graduate the durable decisions into `dev_docs/deterministic-code-opportunity.md` and delete this folder.

## Open questions

- Row 16: the three kanban tables are close, not identical. Task 13 starts by
  diffing them; if they differ on purpose (Linear's `needs_review` needs linked
  PR data the others lack), the helper takes a per-tracker field map and the
  difference stays in the map, not in prose.
- Task 8's locking: `RUN.md` is written by one orchestrator process at a time
  by design; the helper does a read-modify-write on that assumption. If the
  four `/deliver-task` boundaries can overlap in a run, the task grows a lock.
