#!/usr/bin/env python3
"""GraphQL fast-path for `/reoptimize-tasks` (linear handler) "Load — build the
graph": resolve one Linear team's (or project's) full issue set, with each
issue's native relation edges, in one filtered GraphQL query per scope
(paginated to exhaustion) instead of `linear-reoptimize.md`'s per-issue
`get_issue includeRelations: true` loop — the biggest single token spend in
this codebase, since reoptimize reads every issue in scope including terminal
(`Done`/`Canceled`) ones.

Read-only. Never mutates anything. Reuses linear-archive.py's get_key()/gql()
helpers verbatim (same key resolution + the "No Linear API key…" guidance).

SCHEMA NOTE — verified-pending. This script derives the four logical edges
(`blockedBy`/`blocks`/`relatedTo`/`duplicateOf`) from Linear's `relations` and
`inverseRelations` connections, per the GraphQL API as documented:
  relations         { nodes { type relatedIssue { id identifier } } }
  inverseRelations  { nodes { type issue        { id identifier } } }
with `type` in {"blocks", "related", "duplicate"}. This repo runs keyless, so
the exact field names/shape are NOT verified against a live schema here —
`scripts/test-linear-relations-live.sh` is the opt-in smoke test that surfaces
drift (wrong field name, renamed enum value, etc.) the first time it runs with
a real key.

Derivation:
  blocks      = relations,        type == "blocks"   -> relatedIssue
  blockedBy   = inverseRelations, type == "blocks"    -> issue
  relatedTo   = either connection, type == "related"  -> relatedIssue / issue
  duplicateOf = either connection, type == "duplicate" -> relatedIssue / issue

`description` IS included in the per-issue payload here — unlike
linear-scan.py / linear-ready.py, which omit it to keep their frequent,
lightweight reads cheap. reoptimize legitimately reads `description` for
Dimension 1 prose->native reconciliation and Dimension 4 duplicate/overlap
judgment, and it runs as a rare, deliberate whole-backlog pass (not a
per-request hot path), so the bigger per-issue payload is a justified
tradeoff, not an oversight.

The API key is read, in order, from:
  1. $LINEAR_API_KEY, else
  2. `op read "$LINEAR_API_KEY_REF"` (a full op://vault/item/field reference).

IMPORTANT: under 1Password desktop-app integration, `op` only unlocks in an
authorized terminal — NOT in an agent's tool-spawned subshell. Run this from
your own terminal, or headless with $LINEAR_API_KEY set directly, or
$OP_SERVICE_ACCOUNT_TOKEN + $LINEAR_API_KEY_REF (so `op read` resolves the
key).

On any failure (missing key, GraphQL error, team not found) this exits
non-zero with the reason on stderr, so the caller can fall back to the MCP
floor. Stdout carries exactly one JSON object and nothing else.

Usage:
  python3 linear-relations.py --team PreThink
  python3 linear-relations.py --team PreThink --project <uuid> --project <uuid>
  python3 linear-relations.py --team PreThink --limit 100
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.linear.app/graphql"

# linear.team may be a team NAME or a UUID id (see linear-common.md / linear-config.md).
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# Resolve by id via the `teams(filter:)` form (not the root `team(id:)` arg) —
# mirrors linear-ready.py's PRELUDE_BY_ID/PRELUDE_BY_NAME so $id stays `ID!`.
PRELUDE_BY_ID = """
query($id: ID!) {
  viewer { id }
  teams(filter: { id: { eq: $id } }) {
    nodes { id name states { nodes { id name type } } }
  }
}"""

PRELUDE_BY_NAME = """
query($name: String!) {
  viewer { id }
  teams(filter: { name: { eq: $name } }) {
    nodes { id name states { nodes { id name type } } }
  }
}"""

# %s slots: project var decl, extra filter. Team is always the resolved UUID id
# (resolve_team returns team.id), so the filter is by id and $team is an ID.
# No `state: { type: … }` filter — reoptimize needs terminal issues too.
ISSUES_QUERY = """
query($cursor: String, $first: Int!, $team: ID!%s) {
  issues(first: $first, after: $cursor, filter: {
    team: { id: { eq: $team } }
    %s
  }) {
    nodes {
      id identifier title description priority estimate updatedAt
      state { type }
      project { id }
      labels { nodes { name } }
      relations { nodes { type relatedIssue { id identifier } } }
      inverseRelations { nodes { type issue { id identifier } } }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""


def get_key():
    key = os.environ.get("LINEAR_API_KEY")
    if key:
        return key.strip()
    ref = os.environ.get("LINEAR_API_KEY_REF")
    if not ref:
        sys.exit(
            "No Linear API key. Set $LINEAR_API_KEY, or $LINEAR_API_KEY_REF to a "
            "full op://vault/item/field reference. (op must run in an authorized "
            "shell — see this file's header.)"
        )
    out = subprocess.run(["op", "read", ref], capture_output=True, text=True)
    key = out.stdout.strip()
    if not key:
        sys.exit(f"Could not read key from 1Password ({ref}): {out.stderr.strip()}")
    return key


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
        # Linear returns query-validation errors as HTTP 400 with the detail in
        # the body — surface it cleanly instead of letting urlopen raise.
        sys.exit(f"GraphQL HTTP {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        # Network failure or the timeout above — exit non-zero (not a hang) so the
        # caller falls back to the MCP floor per this script's contract.
        sys.exit(f"GraphQL request failed: {e.reason}")
    if "errors" in payload:
        sys.exit("GraphQL error: " + json.dumps(payload["errors"], indent=2))
    return payload["data"]


def resolve_team(key, team):
    if UUID_RE.match(team):
        data = gql(key, PRELUDE_BY_ID, {"id": team})
    else:
        data = gql(key, PRELUDE_BY_NAME, {"name": team})
    nodes = data["teams"]["nodes"]  # both preludes return teams.nodes
    node = nodes[0] if nodes else None
    if not node:
        sys.exit(f"Team not found: {team}")
    return data["viewer"], node


def fetch_issues(key, team_id, project_id, page_size):
    var_decl = ", $project: ID" if project_id else ""
    extra = "project: { id: { eq: $project } }" if project_id else ""
    query = ISSUES_QUERY % (var_decl, extra)
    out, cursor = [], None
    while True:
        variables = {"cursor": cursor, "first": page_size, "team": team_id}
        if project_id:
            variables["project"] = project_id
        page = gql(key, query, variables)["issues"]
        out.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return out
        cursor = page["pageInfo"]["endCursor"]


def _ref(node):
    """A related/inverse-related node -> its identifier, falling back to id."""
    return node.get("identifier") or node.get("id")


def derive_edges(issue):
    """Fold `relations`/`inverseRelations` into the four logical edge lists."""
    blocks, blocked_by, related_to, duplicate_of = [], [], [], []

    for edge in issue.get("relations", {}).get("nodes", []):
        target = edge.get("relatedIssue")
        if not target:
            continue
        ref = _ref(target)
        if edge.get("type") == "blocks":
            blocks.append(ref)
        elif edge.get("type") == "related":
            related_to.append(ref)
        elif edge.get("type") == "duplicate":
            duplicate_of.append(ref)

    for edge in issue.get("inverseRelations", {}).get("nodes", []):
        target = edge.get("issue")
        if not target:
            continue
        ref = _ref(target)
        if edge.get("type") == "blocks":
            blocked_by.append(ref)
        elif edge.get("type") == "related":
            if ref not in related_to:
                related_to.append(ref)
        elif edge.get("type") == "duplicate":
            if ref not in duplicate_of:
                duplicate_of.append(ref)

    return blocks, blocked_by, related_to, duplicate_of


def main():
    ap = argparse.ArgumentParser(
        description="Resolve a Linear scope's full issue graph, with native "
        "relation edges, via a GraphQL fast-path."
    )
    ap.add_argument(
        "--team",
        default=os.environ.get("LINEAR_TEAM"),
        required=os.environ.get("LINEAR_TEAM") is None,
        help="Team name (e.g. PreThink) or UUID id, or $LINEAR_TEAM.",
    )
    ap.add_argument(
        "--project",
        action="append",
        default=[],
        help="Project UUID to scope to. Repeatable. Omit for whole-team scope.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=50,
        help="GraphQL page size only — the script paginates to exhaustion "
        "regardless, so this does not truncate results.",
    )
    args = ap.parse_args()

    key = get_key()
    viewer, team_node = resolve_team(key, args.team)

    project_ids = list(dict.fromkeys(args.project)) or [None]

    all_issues, seen_ids = [], set()
    for project_id in project_ids:
        for issue in fetch_issues(key, team_node["id"], project_id, args.limit):
            if issue["id"] in seen_ids:  # a project could be queried twice by mistake
                continue
            seen_ids.add(issue["id"])
            all_issues.append(issue)

    issues_out = []
    for issue in all_issues:
        blocks, blocked_by, related_to, duplicate_of = derive_edges(issue)
        issues_out.append(
            {
                "id": issue["id"],
                "identifier": issue["identifier"],
                "title": issue["title"],
                "description": issue.get("description"),
                "state": issue["state"],
                "priority": issue["priority"],
                "estimate": issue.get("estimate"),
                "updatedAt": issue["updatedAt"],
                "project": issue.get("project"),
                "labels": [n["name"] for n in issue["labels"]["nodes"]],
                "blockedBy": blocked_by,
                "blocks": blocks,
                "relatedTo": related_to,
                "duplicateOf": duplicate_of,
            }
        )

    print(
        f"Resolved team={team_node['name']} scopes={len(project_ids)} "
        f"issues={len(issues_out)}",
        file=sys.stderr,
    )

    result = {
        "meta": {
            "viewer": viewer,
            "team": {"id": team_node["id"], "name": team_node["name"]},
            "states": team_node["states"]["nodes"],
        },
        "issues": issues_out,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
