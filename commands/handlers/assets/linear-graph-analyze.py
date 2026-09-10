#!/usr/bin/env python3
"""Analyze a Linear issue graph: cycles, topological order, priority inversions.

The analysis half of `/reoptimize-tasks` Dimension 3 (and the cycle step of
Dimension 1) on the linear handler. Before this script existed,
`linear-reoptimize.md` asked the agent to hand-walk cycle detection, a
topological sort with an urgency -> smaller-estimate -> age tie-break, and a
priority-inversion sweep over every edge — the same class of unchecked
hand-walk `gh-issue-graph.py`'s docstring warns about.

Input is the JSON `linear-relations.py` prints (`{meta, issues: [...]}`), on
stdin or `--file`. Each issue is keyed by its `identifier` (falling back to
`id` when `identifier` is absent), and `blockedBy` entries name blockers by
the same key — exactly what `linear-relations.py`'s `_ref()` emits. One field
the age tie-break wants, `createdAt`, is **not** in that script's GraphQL
selection today, so on real fast-path input every issue lacks it and the
tie-break degrades to "missing sorts last" (see `sort_key`); the tests supply
it by hand. Add `createdAt` to `linear-relations.py`'s query to make the age
rule live.

Cycle detection reuses `gh-issue-graph.py`'s `find_cycles()` (Tarjan's SCC,
iterative) verbatim rather than writing a second implementation — it is
generic over any hashable node id and gh-issue-graph.py has no third-party
imports, so it stays safe to load at the bare-`python3` asset tier. The
topological order below is a fresh Kahn's-algorithm walk: `scripts/
plan-graph.py` already has one, but it ties on alphabetical slug order for a
different domain (plan slugs) and needs Python 3.11 + pyyaml, so importing it
would either change its tie-break contract or drag a dependency into this
3.9-clean asset. Its shape (indegree map, a ready queue, decrement on
dequeue) is deliberately mirrored here rather than reinvented.

**Terminal nodes** (`state.type` in `completed` / `canceled` / `duplicate`,
matching `linear-archive.py`'s `TERMINAL_TYPES`) stay in the graph for cycle
detection — a cycle can run through a closed issue — but are dropped, along
with every edge touching one, before the topological sort: a closed blocker
no longer blocks anything, and Dimension 3 never orders terminal work.
Priority inversions are swept only over edges where both ends are still
open, for the same reason `gh-issue-graph.py`'s inversion check skips a
closed blocker: raising priority on closed work is a no-op no one asked for.

**Priority ordering:** Linear's `priority` is `1`=Urgent .. `4`=Low, `0`=None.
Rank order is `1 -> 2 -> 3 -> 4 -> 0` (None sorts last / least urgent).

**If a cycle exists, `order` covers only the acyclic remainder** — Kahn's
algorithm never dequeues a node stuck in (or downstream of) a cycle, so
`cycles` and `order` are computed independently and may disagree about which
nodes are "safe"; the caller reports both. This script never fails on a
cycle: it exits 0 and reports it, the same stance `gh-issue-graph.py` and
`linear-reoptimize.md` take (a cycle is a human decision). It exits non-zero
only when the input itself is malformed.

Usage:
  python3 linear-relations.py --team PreThink | python3 linear-graph-analyze.py
  python3 linear-graph-analyze.py --file graph.json
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shape import ShapeError, expect  # noqa: E402

_ASSETS_DIR = Path(__file__).resolve().parent
_GH_ISSUE_GRAPH_PATH = _ASSETS_DIR / "gh-issue-graph.py"
_spec = importlib.util.spec_from_file_location(
    "gh_issue_graph_asset", _GH_ISSUE_GRAPH_PATH
)
assert _spec is not None and _spec.loader is not None, (
    f"cannot load {_GH_ISSUE_GRAPH_PATH}"
)
_gh_issue_graph = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gh_issue_graph)
find_cycles = _gh_issue_graph.find_cycles

# `1 -> 2 -> 3 -> 4 -> 0`: None (0) is the least urgent, sorted last.
PRIORITY_ORDER = [1, 2, 3, 4, 0]

TERMINAL_TYPES = {"completed", "canceled", "duplicate"}


class MalformedInput(Exception):
    """The input JSON does not have the shape this script requires."""


def priority_rank(value):
    """Urgency as a sortable int — lower is more urgent.

    An out-of-vocabulary value (not 0-4) sorts after everything named, same
    fallback stance as `gh-issue-graph.py`'s `prio_rank()`.
    """
    return (
        PRIORITY_ORDER.index(value) if value in PRIORITY_ORDER else len(PRIORITY_ORDER)
    )


def node_key(issue, where):
    """The graph identity for one issue: `identifier`, falling back to `id`."""
    identifier = issue.get("identifier")
    if isinstance(identifier, str) and identifier:
        return identifier
    node_id = issue.get("id")
    if isinstance(node_id, str) and node_id:
        return node_id
    raise MalformedInput(f"{where}: issue has neither identifier nor id")


def load_nodes(payload):
    """Parse `{meta, issues: [...]}` into `{key: node}`, key order preserved."""
    issues = expect(payload, "issues", list, "input")
    nodes = {}
    order = []
    for i, issue in enumerate(issues):
        where = f"input.issues[{i}]"
        if not isinstance(issue, dict):
            raise MalformedInput(
                f"{where}: expected an object, got {type(issue).__name__}"
            )
        key = node_key(issue, where)
        state = issue.get("state")
        state_type = (state or {}).get("type") if isinstance(state, dict) else None
        blocked_by = issue.get("blockedBy")
        if blocked_by is None:
            blocked_by = []
        if not isinstance(blocked_by, list):
            raise MalformedInput(f"{where}.blockedBy: expected a list")
        priority = issue.get("priority")
        if priority is None:
            priority = 0
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise MalformedInput(f"{where}.priority: expected an int")
        estimate = issue.get("estimate")
        if estimate is not None and (
            not isinstance(estimate, (int, float)) or isinstance(estimate, bool)
        ):
            raise MalformedInput(f"{where}.estimate: expected a number or null")
        nodes[key] = {
            "key": key,
            "state_type": state_type,
            "terminal": state_type in TERMINAL_TYPES,
            "priority": priority,
            "estimate": estimate,
            "created_at": issue.get("createdAt"),
            "blocked_by": [b for b in blocked_by if isinstance(b, str)],
        }
        order.append(key)
    return nodes, order


def kahn_order(keys, edges):
    """Topological order over `keys` using `edges[blocked] = {blocker, ...}`.

    Mirrors `scripts/plan-graph.py`'s Kahn's-algorithm walk, but breaks ties by
    (priority rank, estimate, created_at) instead of alphabetical slug order —
    Dimension 3's "urgency -> smaller estimate -> age" ranking — via `sort_key`
    rather than a bare string compare.

    Returns the order; any key never dequeued is stuck in (or downstream of) a
    cycle and is simply absent — the caller does not treat that as failure.
    """

    def sort_key(key):
        node = keys[key]
        estimate = node["estimate"]
        created_at = node["created_at"]
        return (
            priority_rank(node["priority"]),
            (1, 0) if estimate is None else (0, estimate),
            (1, "") if not isinstance(created_at, str) else (0, created_at),
            key,
        )

    indegree = {k: 0 for k in keys}
    adj: dict = {k: [] for k in keys}
    for blocked, blockers in edges.items():
        for blocker in blockers:
            if blocker not in keys or blocked not in keys:
                continue
            adj[blocker].append(blocked)
            indegree[blocked] += 1

    ready = sorted((k for k in keys if indegree[k] == 0), key=sort_key)
    order = []
    while ready:
        ready.sort(key=sort_key)
        current = ready.pop(0)
        order.append(current)
        for nxt in adj[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    return order


def find_inversions(nodes, edges):
    """Every `blockedBy` edge where the blocker is less urgent than the
    dependent, restricted to edges where both ends are still open — raising
    priority on a closed issue is a no-op, same stance as `gh-issue-graph.py`.
    """
    inversions = []
    for blocked in sorted(edges):
        dependent = nodes[blocked]
        if dependent["terminal"]:
            continue
        for blocker_key in sorted(edges[blocked]):
            blocker = nodes.get(blocker_key)
            if blocker is None or blocker["terminal"]:
                continue
            dep_rank = priority_rank(dependent["priority"])
            blk_rank = priority_rank(blocker["priority"])
            if blk_rank > dep_rank:
                inversions.append(
                    {
                        "blocker": blocker_key,
                        "dependent": blocked,
                        "blocker_priority": blocker["priority"],
                        "dependent_priority": dependent["priority"],
                    }
                )
    return inversions


def analyze(payload):
    nodes, key_order = load_nodes(payload)

    # edges[blocked] = {blocker, ...} — same shape gh-issue-graph.py's
    # find_cycles() expects, and every blockedBy target outside the input set
    # is dropped rather than backfilled (unlike gh-issue-graph.py's analysis-only
    # backfill): a cross-scope Linear blocker has no node here to reason about.
    edges = {
        key: {b for b in nodes[key]["blocked_by"] if b in nodes} for key in key_order
    }

    cycles = find_cycles(key_order, edges)

    non_terminal = {k: n for k, n in nodes.items() if not n["terminal"]}
    ordering_edges = {
        k: {b for b in edges.get(k, ()) if b in non_terminal} for k in non_terminal
    }
    order = kahn_order(non_terminal, ordering_edges)

    inversions = find_inversions(nodes, edges)

    return {"cycles": cycles, "order": order, "inversions": inversions}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Read the linear-relations.py JSON from this file instead of stdin.",
    )
    args = parser.parse_args(argv)

    raw = args.file.read_text() if args.file else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"linear-graph-analyze: input is not valid JSON: {e}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print(
            f"linear-graph-analyze: expected a JSON object, got {type(payload).__name__}",
            file=sys.stderr,
        )
        return 1

    try:
        result = analyze(payload)
    except (MalformedInput, ShapeError) as e:
        print(f"linear-graph-analyze: {e}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
