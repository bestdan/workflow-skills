#!/usr/bin/env python3
"""MCP-floor entry point for `linear-common.md`'s "Ready-candidate selection":
apply the canonical gates and rank to a set of issues the caller already
fetched via `<linear-mcp>__list_issues`, instead of hand-walking them in
prose. Sibling of `linear-ready.py` (the GraphQL fast path); both import the
same `gate()`/`rank_key()` from `_linear_rank.py` — see that module's header
for the six-gate table and the rank rule.

Read-only. Never mutates anything, never calls the network — the caller
already did the MCP read; this script only decides over the result.

Input (stdin): a JSON array of issue objects, one per `list_issues` result,
each with at least:

  id          str   — the issue's display identifier (e.g. "PRE-12")
  priority    int or {"value": int}  — 0=None, 1=Urgent .. 4=Low
  estimate    int, {"value": int}, or null
  updatedAt   str   — ISO-8601, used for the tie-break
  labels      list[str]  — label names
  assigneeId  str or null (omit when unassigned)

Every other key on an issue object is passed through unchanged onto the
matching output candidate, so the caller can feed `list_issues`' own fields
straight in with no reshaping. When an issue also carries `project.max_estimate`
(the per-project override the MCP-floor caller already tags each candidate
with — see `linear-claim.md` "Find candidates" floor step 4), that value wins
over `--max-estimate` for that one issue — this is how a single call can gate
a merged, multi-scope candidate set where each scope has its own max.

Usage:
  <linear-mcp>__list_issues ... | python3 linear-rank.py --max-estimate 3
  python3 linear-rank.py --max-estimate 3 --viewer-id <uuid> < issues.json

--viewer-id compares against each issue's `assigneeId` for the assignee gate
(the MCP floor's own viewer, from `<linear-mcp>__get_user` — see
`linear-common.md` "Ready-candidate selection", "The assignee gate is
viewer-relative"). Omit it to skip the assignee gate entirely (every
assigned issue then passes) — only correct when the caller has already
filtered by assignee itself.

Stdout carries exactly one JSON object:
  {"candidates": [...], "dropped": {"<id>": "<reason>", ...}}
"dropped" is a map, not a list, precisely so the caller can look up why one
specific identifier is missing without a linear scan.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _linear_rank import gate, rank_key  # noqa: E402


def _value(field):
    """Unwrap MCP's `{"value": N, "name": "..."}` shape to the bare number;
    pass a bare number (or None) through unchanged."""
    if isinstance(field, dict):
        return field.get("value")
    return field


def _to_gate_shape(issue, viewer_id):
    """Adapt one list_issues-shaped issue into the shape _linear_rank.gate()
    and rank_key() expect (the GraphQL fast path's nodes/isMe shape) —
    gate()/rank_key() stay unchanged; only this adapter is MCP-specific."""
    assignee_id = issue.get("assigneeId")
    assignee = None
    if assignee_id and viewer_id is not None:
        assignee = {"id": assignee_id, "isMe": assignee_id == viewer_id}
    return {
        "estimate": _value(issue.get("estimate")),
        "labels": {"nodes": [{"name": name} for name in issue.get("labels") or []]},
        "assignee": assignee,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Apply the ready-candidate gates and rank to MCP list_issues results."
    )
    ap.add_argument(
        "--max-estimate",
        type=int,
        default=int(os.environ.get("LINEAR_MAX_ESTIMATE", "3")),
        help="Max estimate (exclusive floor). Also $LINEAR_MAX_ESTIMATE.",
    )
    ap.add_argument(
        "--viewer-id",
        default=None,
        help="The current Linear user's id, for the assignee gate. Omit to "
        "skip that gate.",
    )
    args = ap.parse_args()

    issues = json.load(sys.stdin)

    candidates, dropped = [], {}
    for issue in issues:
        identifier = issue.get("id")
        project = issue.get("project")
        max_estimate = args.max_estimate
        if isinstance(project, dict) and project.get("max_estimate") is not None:
            max_estimate = project["max_estimate"]
        gate_issue = _to_gate_shape(issue, args.viewer_id)
        reason = gate(gate_issue, max_estimate)
        if reason:
            dropped[identifier] = reason
            continue
        candidates.append(
            (issue, _value(issue.get("priority")), issue.get("updatedAt"))
        )

    candidates.sort(key=lambda c: rank_key({"priority": c[1], "_updatedAt": c[2]}))

    print(f"{len(candidates)} candidate(s), {len(dropped)} dropped", file=sys.stderr)

    print(json.dumps({"candidates": [c[0] for c in candidates], "dropped": dropped}))


if __name__ == "__main__":
    main()
