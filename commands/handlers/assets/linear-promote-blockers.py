#!/usr/bin/env python3
"""Decide which `/promote-tasks` (linear handler) Backlog candidates to hold
because a native "is blocked by" relation is still open.

`linear-promote.md` step 5 runs this over the candidates it has already
collected. The rule is the claim path's blocker gate, imported from
`_linear_rank.py` rather than restated, so the promote gate and the claim gate
cannot disagree: a blocker satisfies the dependency only in a
`completed`-type state or once it no longer exists, a `canceled` blocker still
holds, and a blocker whose state could not be read holds too.

Two ways to get the relations, one decision:

  --fetch      GraphQL fast path. Reads every candidate's `inverseRelations`,
               with each blocker's state, in aliased `issue(id:)` queries of
               up to 50 candidates each. Needs a Linear API key; on any failure
               it exits non-zero with the reason on stderr, so the caller falls
               back to the MCP floor.
  (no flag)    MCP floor. The caller attaches `blockedBy` to each candidate
               from `<linear-mcp>__get_issue` reads, exactly as
               `linear-claim.md` "Pre-flight" step 6 builds it.

Input (stdin): a JSON array, one object per candidate, each with at least

  id          str — the display identifier, e.g. "PRE-12"
  blockedBy   MCP floor only: [{"id": "PRE-9", "statusType": "completed"}, ...].
              Leave out a blocker whose read reported "not found"; keep one
              whose read failed any other way, with no statusType.

Stdout carries exactly one JSON object:

  {"held": {"PRE-12": "waiting on PRE-9"}, "clear": ["PRE-13", ...]}

`held` maps each held candidate to the reason step 9 reports it with — the
same `waiting on <id>[, <id>...]` string the claim gate emits. Read-only:
never mutates anything.

The API key is resolved by commands/handlers/assets/_secret_resolve.py; see
dev_docs/auth_key_access.md. A configured `linear.api_key*` reaches this script
only because the caller bridges it onto the environment (linear-common.md "Key
resolution"). This script reads no config.

--dependents reads the graph the other way: which issues the given ids block.
It is the candidate read for `/promote-tasks after=<ids>`
(`linear-promote.md` step 5, "`after=` candidates"), so the ids on stdin are
normally issues just completed. It decides only which dependents are the
promoter's to look at; every gate after it still runs on what it returns.

  stdin       [{"id": "PRE-9"}, ...] with --fetch. On the MCP floor, each id
              also carries `blocks`, built from `<linear-mcp>__get_issue`
              reads: [{"id": "PRE-12", "teamId": "<uuid>",
              "statusType": "backlog"}, ...]. Leave out a dependent whose read
              reported "not found"; keep one whose read failed any other way,
              with no statusType.
  --team-id   the configured team's id. A dependent in another team goes to
              `foreign`: the promoter's labels and states are this team's.

Stdout carries one JSON object:

  {"dependents_of": ["PRE-9"],
   "candidates": [{"id": "PRE-12", "via": ["PRE-9"]}],
   "foreign": [...], "unread": [...], "truncated": ["PRE-9"]}

A dependent reached through several ids appears once, with each under `via`.
Only `backlog`-type dependents are kept: one past Backlog is out of the
promoter's lane, and a deleted one is gone. `unread` is a dependent whose state
could not be read, and `truncated` names an id with more than 50 relations,
whose later dependents were not seen; neither is ever written to.

Usage:
  python3 linear-promote-blockers.py --fetch < candidates.json
  python3 linear-promote-blockers.py < candidates-with-blockedBy.json
  python3 linear-promote-blockers.py --dependents --team-id <uuid> --fetch < ids.json
  python3 linear-promote-blockers.py --dependents --team-id <uuid> < ids-with-blocks.json
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _linear_rank import blocked_by, unmet_blockers  # noqa: E402
from _secret_resolve import SecretUnavailable, resolve_key  # noqa: E402
from _shape import ShapeError, expect  # noqa: E402

API = "https://api.linear.app/graphql"

# Linear's own cap on one connection page, matching linear-ready.py's query.
CHUNK = 50

ISSUE_FIELDS = """
    identifier
    inverseRelations(first: 50) {
      nodes { type issue { identifier state { type } } }
      pageInfo { hasNextPage }
    }"""


def get_key():
    try:
        return resolve_key("LINEAR_API_KEY")
    except SecretUnavailable as e:
        sys.exit(str(e))


def gql(key, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": key,
            "Content-Type": "application/json",
        },  # personal key, no "Bearer"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"GraphQL HTTP {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"GraphQL request failed: {e.reason}")
    if not isinstance(payload, dict):
        sys.exit(f"GraphQL response: expected an object, got {type(payload).__name__}")
    if "errors" in payload:
        sys.exit("GraphQL error: " + json.dumps(payload["errors"], indent=2))
    try:
        return expect(payload, "data", dict, "GraphQL response")
    except ShapeError as exc:
        sys.exit(str(exc))


# A forward `blocks` relation names the issue this one blocks in its
# `relatedIssue` field: the mirror of `inverseRelations` above.
DEPENDENT_FIELDS = """
    identifier
    relations(first: 50) {
      nodes { type relatedIssue { identifier team { id } state { type } } }
      pageInfo { hasNextPage }
    }"""


def chunk_query(n, fields=ISSUE_FIELDS):
    """One aliased `issue(id:)` read per candidate: `issue` takes an
    identifier as well as a UUID, so the MCP floor's identifiers go in as-is."""
    decls = ", ".join(f"$i{k}: String!" for k in range(n))
    reads = "\n".join(f"  c{k}: issue(id: $i{k}) {{{fields}\n  }}" for k in range(n))
    return f"query({decls}) {{\n{reads}\n}}"


def fetch_nodes(key, identifiers, fields):
    """Map each identifier to its GraphQL issue node, 50 per query."""
    out = {}
    for start in range(0, len(identifiers), CHUNK):
        chunk = identifiers[start : start + CHUNK]
        data = gql(
            key,
            chunk_query(len(chunk), fields),
            {f"i{k}": ident for k, ident in enumerate(chunk)},
        )
        for k, ident in enumerate(chunk):
            node = data.get(f"c{k}")
            if not isinstance(node, dict):
                sys.exit(f"GraphQL response: no issue returned for {ident}")
            out[ident] = node
    return out


def fetch_blockers(key, identifiers):
    """Map each identifier to its `blockedBy` list, in the gate's shape."""
    nodes = fetch_nodes(key, identifiers, ISSUE_FIELDS)
    return {ident: blocked_by(node) for ident, node in nodes.items()}


def fetched_dependents(node):
    """A GraphQL node's forward `blocks` edges, in the floor's `blocks` shape,
    and whether the relation page was truncated."""
    conn = node.get("relations") or {}
    out = []
    for rel in conn.get("nodes") or []:
        dep = rel.get("relatedIssue")
        if rel.get("type") != "blocks" or not dep:
            continue  # another relation type, or a dependent that no longer exists
        out.append(
            {
                "id": dep.get("identifier"),
                "teamId": (dep.get("team") or {}).get("id"),
                "statusType": (dep.get("state") or {}).get("type"),
            }
        )
    return out, bool((conn.get("pageInfo") or {}).get("hasNextPage"))


def sort_dependents(blocks_of, truncated, team_id):
    """Sort each id's dependents into the `after=` buckets."""
    rows: dict = {}
    for ident, blocks in blocks_of.items():
        for dep in blocks:
            dep_id = dep.get("id") or "unknown"
            state = dep.get("statusType")
            if state is None:
                bucket = "unread"
            elif state != "backlog":
                continue  # past Backlog: out of the promoter's lane
            elif dep.get("teamId") != team_id:
                bucket = "foreign"
            else:
                bucket = "candidates"
            rows.setdefault(dep_id, {"bucket": bucket, "via": []})["via"].append(ident)

    result: dict = {
        "dependents_of": list(blocks_of),
        "candidates": [],
        "foreign": [],
        "unread": [],
        "truncated": [i for i in blocks_of if i in truncated],
    }
    for dep_id in sorted(rows):
        row = rows[dep_id]
        result[row["bucket"]].append({"id": dep_id, "via": row["via"]})
    return result


def floor_blockers(candidate):
    """The MCP floor's `{id, statusType}` entries, in the gate's shape."""
    return [
        {"identifier": b.get("id"), "stateType": b.get("statusType")}
        for b in candidate.get("blockedBy") or []
    ]


def decide(candidates, blockers):
    held, clear = {}, []
    for c in candidates:
        ident = c["id"]
        waiting = unmet_blockers(blockers[ident])
        if waiting:
            held[ident] = "waiting on " + ", ".join(waiting)
        else:
            clear.append(ident)
    return {"held": held, "clear": clear}


def main():
    ap = argparse.ArgumentParser(
        description="Hold promote candidates whose native blockers are still open."
    )
    ap.add_argument(
        "--fetch",
        action="store_true",
        help="Read the relations via GraphQL (fast path) instead of taking "
        "each candidate's blockedBy from stdin (MCP floor).",
    )
    ap.add_argument(
        "--dependents",
        action="store_true",
        help="Report the issues the given ids block (the `after=` candidate "
        "read) instead of deciding holds.",
    )
    ap.add_argument(
        "--team-id",
        help="With --dependents: the configured team's id; dependents in any "
        "other team are reported as foreign.",
    )
    args = ap.parse_args()
    if args.dependents and not args.team_id:
        ap.error("--dependents needs --team-id")
    if args.team_id and not args.dependents:
        ap.error("--team-id is only read with --dependents")

    candidates = json.load(sys.stdin)
    if not isinstance(candidates, list):
        sys.exit("stdin: expected a JSON array of candidates")

    if args.dependents:
        ids = [c["id"] for c in candidates]
        if args.fetch:
            nodes = fetch_nodes(get_key(), ids, DEPENDENT_FIELDS)
            pairs = {i: fetched_dependents(nodes[i]) for i in ids}
            blocks_of = {i: pairs[i][0] for i in ids}
            truncated = {i for i in ids if pairs[i][1]}
        else:
            blocks_of = {c["id"]: c.get("blocks") or [] for c in candidates}
            truncated = set()
        result = sort_dependents(blocks_of, truncated, args.team_id)
        print(
            f"{len(result['candidates'])} candidates, {len(result['foreign'])} "
            f"foreign, {len(result['unread'])} unread",
            file=sys.stderr,
        )
        print(json.dumps(result))
        return

    if args.fetch:
        blockers = fetch_blockers(get_key(), [c["id"] for c in candidates])
    else:
        blockers = {c["id"]: floor_blockers(c) for c in candidates}

    result = decide(candidates, blockers)
    print(
        f"{len(result['held'])} held, {len(result['clear'])} clear",
        file=sys.stderr,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
