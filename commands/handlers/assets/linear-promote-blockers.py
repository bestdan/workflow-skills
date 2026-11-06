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

Usage:
  python3 linear-promote-blockers.py --fetch < candidates.json
  python3 linear-promote-blockers.py < candidates-with-blockedBy.json
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


def chunk_query(n):
    """One aliased `issue(id:)` read per candidate: `issue` takes an
    identifier as well as a UUID, so the MCP floor's identifiers go in as-is."""
    decls = ", ".join(f"$i{k}: String!" for k in range(n))
    reads = "\n".join(
        f"  c{k}: issue(id: $i{k}) {{{ISSUE_FIELDS}\n  }}" for k in range(n)
    )
    return f"query({decls}) {{\n{reads}\n}}"


def fetch_blockers(key, identifiers):
    """Map each identifier to its `blockedBy` list, in the gate's shape."""
    out = {}
    for start in range(0, len(identifiers), CHUNK):
        chunk = identifiers[start : start + CHUNK]
        data = gql(
            key,
            chunk_query(len(chunk)),
            {f"i{k}": ident for k, ident in enumerate(chunk)},
        )
        for k, ident in enumerate(chunk):
            node = data.get(f"c{k}")
            if not isinstance(node, dict):
                sys.exit(f"GraphQL response: no issue returned for {ident}")
            out[ident] = blocked_by(node)
    return out


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
    args = ap.parse_args()

    candidates = json.load(sys.stdin)
    if not isinstance(candidates, list):
        sys.exit("stdin: expected a JSON array of candidates")

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
