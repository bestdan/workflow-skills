---
title: scripts/plan-graph.py — topological sort + cycle detection for /push-plan
priority: high
size: 3
status: ready
created: 2026-07-17
expires: 2026-08-16
source_branch: claude/sleepy-ride-8d4bjx
related_files:
  - scripts/plan-graph.py # new file
  - scripts/check.sh # wire the paired test
  - commands/push-plan.md # §4.3 (linear), §5.3 (gh-issue), §5b.3 (jira) topo sort; lines 21,164,333
  - commands/handlers/assets/linear-relations.py # the reoptimize graph-load precedent (read-only, don't duplicate)
parent: deterministic_scripts
impact: 3
tags: [scripts, push-plan, graph]
---

# Task 2 — `scripts/plan-graph.py` (push-plan topological ordering)

Part of [[deterministic_scripts_plan]]. The audit's Finding #2 (push side). No
dependency on the other tasks.

## Context

`/push-plan` must create tracker issues in dependency order. On every push the
agent currently hand-parses N task files' frontmatter, classifies each
`is_blocked_by` entry against an id-shape regex, hand-executes Kahn's algorithm,
detects cycles, and distinguishes "typo'd slug" from "external tracker id" — in
its head, over a graph that can run 15–30 nodes (`push-plan.md` line 21, and the
§4.3 / §5.3 / §5b.3 blocks at lines ~164 / ~333). A missed edge silently
produces a wrong creation order; a missed cycle produces a partial push the
prose explicitly forbids. The three handler blocks are **not** three full
re-explanations (§5.3/§5b.3 are ~8-line deltas noting "same algorithm, only the
id-shape regex differs"), so the win is **determinism on a high-stakes op**, not
prose deletion.

The reoptimize graph *load* was already extracted as
`commands/handlers/assets/linear-relations.py` — do **not** duplicate it; this
script is the **push-side** ordering only. `linear-relations.py` reads native
relations from Linear; `plan-graph.py` reads `is_blocked_by` from local plan
files (or JSON on stdin) before anything exists in the tracker.

The three id-shape regexes are already spelled out verbatim in the prose
(linear identifier, gh `#<n>`, jira `KEY-<n>`) — lift them, don't reinvent.

## Task

1. Create `scripts/plan-graph.py` (executable, uv shebang, pyyaml — matches
   `validate.py`). Input: a plan directory **or** JSON on stdin
   (`[{slug, is_blocked_by, tracker_id, status}]`). Flags:
   `--id-shape linear|gh|jira` (selects the edge-classification regex),
   optional `--rewrite <slug>=<id>` for the kept-dependent rewrite step.
2. Output one JSON doc: `order` (topologically sorted slugs), `edges` used,
   each `is_blocked_by` entry classified `in-plan | tracker-id | unknown-slug`
   (unknown-slug is a **warn**, not fatal), `cycles` (a non-empty list is a
   **fail** — exit non-zero), and the seeded `slug→tracker_id` map.
3. Fail-closed: a cycle exits non-zero with the involved slugs named; malformed
   frontmatter exits non-zero. The push prose must be able to trust a zero exit
   as "safe to create in `order`".
4. Add `scripts/test-plan-graph.sh` with fixtures: a clean DAG, a cycle
   (asserts non-zero + named slugs), a bare tracker-id edge (classified
   `tracker-id`, not warned), a typo'd slug (classified `unknown-slug`, warned
   not failed), and each `--id-shape`. Wire into `scripts/check.sh`.
5. **Wire `push-plan.md`** §4.3 / §5.3 / §5b.3 to call the script and cite it
   as the ordering authority; keep the surrounding create-loop prose. Keep
   `linear-reoptimize` Dimensions 1/2/4 (NL reconciliation, semantic inference,
   duplicate detection) as prose — only the topo sort moves.

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes including `test-plan-graph.sh`.
- The cycle fixture makes `plan-graph.py` exit non-zero and name the cycle
  slugs; the clean-DAG fixture emits a valid topological order.
- `rg -n "Kahn|topological" commands/push-plan.md` shows a reference to the
  script, not a hand-executed algorithm.

**User-run:**

- Run `plan-graph.py` over this very plan directory
  (`dev_docs/tasks/deterministic_scripts_plan`) and confirm the emitted order
  respects `is_blocked_by` (task 6 last; tasks 1–5 orderable before it).
