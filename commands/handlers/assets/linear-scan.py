#!/usr/bin/env python3
"""GraphQL fast-path for the Linear handler's "In-flight scan" (see
commands/handlers/linear-common.md's "## In-flight scan" section — the single
source of truth for this read; change it there first and update all
consumers, including this file, in lockstep).

`/sweep-for-complete` (row 1: merged PR -> Done) and `/reconcile-tasks` row 2
("open PR, wrong column") both need every in-flight issue's linked PR
attachment before they can resolve and merge-check it. This replaces the
per-issue `get_issue` attachment-fetch loop with one paginated GraphQL query
per scope (project, or whole-team when no --project is given).

Read-only. Never mutates anything. This is a sibling of linear-ready.py and
linear-archive.py; it reuses linear-archive.py's get_key()/gql() verbatim.

The API key is read, in order, from:
  1. $LINEAR_API_KEY, else
  2. `op read "$LINEAR_API_KEY_REF"` (a full op://vault/item/field reference).

`op read` needs an authorized 1Password session. Running `op signin` in your own
terminal establishes one that IS visible to an agent's tool-spawned subshell — op
holds the session in a per-user cache daemon — and it lapses after roughly 30
minutes of inactivity. Headless runs instead set $LINEAR_API_KEY directly, or
$OP_SERVICE_ACCOUNT_TOKEN + $LINEAR_API_KEY_REF (so `op read` resolves the key).

A configured `linear.api_key_ref` reaches step 2 only because the caller exports
it — see linear-common.md's "Key resolution" step. This script reads no config.

On any failure (missing key, GraphQL error, team not found) this exits
non-zero with the reason on stderr, so the caller can fall back to the MCP
floor. Stdout carries exactly one JSON object and nothing else.

Usage:
  python3 linear-scan.py --team PreThink
  python3 linear-scan.py --team PreThink --state-type started --state-type unstarted
  python3 linear-scan.py --team PreThink --project <uuid> --project <uuid>
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

VALID_STATE_TYPES = {"started", "unstarted", "backlog"}

# Resolve by id via the `teams(filter:)` form (not the root `team(id:)` arg), same
# as linear-ready.py — the `id: { eq: <ID> }` comparator is proven to take `ID!`.
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

# %s slot: project var decl + extra filter. Skinny fields per linear-common.md's
# "In-flight scan": id identifier title url state { id type } plus attachments —
# explicitly no `description`.
ISSUES_QUERY = """
query($cursor: String, $first: Int!, $team: ID!, $types: [String!]%s) {
  issues(first: $first, after: $cursor, filter: {
    team: { id: { eq: $team } }
    state: { type: { in: $types } }
    %s
  }) {
    nodes {
      id identifier title url
      state { id type }
      attachments { nodes { url } }
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
            "full op://vault/item/field reference. (A configured linear.api_key_ref "
            "is exported by the caller — see this file's header.)"
        )
    out = subprocess.run(["op", "read", ref], capture_output=True, text=True)
    key = out.stdout.strip()
    if not key:
        sys.exit(
            f"Could not read key from 1Password ({ref}): {out.stderr.strip()} "
            "— if the op session has lapsed, run `op signin` in your own terminal."
        )
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


def fetch_issues(key, team_id, state_types, project_id, page_size):
    var_decl = ", $project: ID" if project_id else ""
    extra = "project: { id: { eq: $project } }" if project_id else ""
    query = ISSUES_QUERY % (var_decl, extra)
    out, cursor = [], None
    while True:
        variables = {
            "cursor": cursor,
            "first": page_size,
            "team": team_id,
            "types": state_types,
        }
        if project_id:
            variables["project"] = project_id
        page = gql(key, query, variables)["issues"]
        out.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return out
        cursor = page["pageInfo"]["endCursor"]


def parse_project_arg(raw):
    # Colon-joined `a:b` is also accepted as a way to pass multiple project ids
    # in one --project flag.
    return raw.split(":") if ":" in raw else [raw]


def main():
    ap = argparse.ArgumentParser(
        description="Scan in-flight Linear issues (state + PR attachment) via a "
        "GraphQL fast-path."
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
        help="Project UUID to scope to. Repeatable; also accepts colon-joined "
        "`a:b` for multiple ids in one flag. Omit for whole-team scope.",
    )
    ap.add_argument(
        "--state-type",
        action="append",
        default=[],
        choices=sorted(VALID_STATE_TYPES),
        help="Workflow state type to scan (repeatable): started, unstarted, or "
        "backlog. Default: started.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=50,
        help="GraphQL page size only — the script paginates to exhaustion "
        "regardless, so this does not truncate results.",
    )
    args = ap.parse_args()

    state_types = args.state_type or ["started"]

    key = get_key()
    viewer, team_node = resolve_team(key, args.team)

    project_ids = []
    for raw in args.project:
        for pid in parse_project_arg(raw):
            if pid not in project_ids:
                project_ids.append(pid)

    scopes = (
        [{"id": pid, "name": pid} for pid in project_ids]
        if project_ids
        else [{"id": None, "name": team_node["name"]}]
    )

    all_issues = []
    for scope in scopes:
        issues = fetch_issues(
            key, team_node["id"], state_types, scope["id"], args.limit
        )
        for issue in issues:
            issue["project"] = scope["name"]
            issue["attachments"] = [n["url"] for n in issue["attachments"]["nodes"]]
            all_issues.append(issue)

    print(
        f"Resolved team={team_node['name']} scopes={len(scopes)} "
        f"state_types={state_types}",
        file=sys.stderr,
    )
    print(f"{len(all_issues)} in-flight issue(s) scanned", file=sys.stderr)

    result = {
        "meta": {
            "viewer": viewer,
            "team": {"id": team_node["id"], "name": team_node["name"]},
            "states": team_node["states"]["nodes"],
        },
        "issues": all_issues,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
