#!/usr/bin/env python3
"""GraphQL fast-path for `/do-tasks` (tracker path) find-candidates: resolve one
Linear team's ready (`unstarted`-type) issues, apply the canonical ready-candidate
gates, and rank the survivors — with one filtered GraphQL query per scope
(paginated to exhaustion when a scope has more than one page) instead of the
~6-call MCP fan-out (list_teams -> list_workflow_states -> list_issues per scope ->
get_user -> lazy get_issue for branchName).

Read-only. Never mutates anything. This is the sibling of linear-archive.py; it
reuses that script's get_key()/gql() helpers verbatim.

Gate + rank rules mirror commands/handlers/linear-common.md's "Ready-candidate
selection" block exactly. Change them there first and update both consumers
(linear-claim.md and this file) in lockstep.

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

On any failure (missing key, GraphQL error, team not found) this exits non-zero
with the reason on stderr, so the caller can fall back to the MCP floor. Stdout
carries exactly one JSON object and nothing else.

Usage:
  python3 linear-ready.py --team PreThink
  python3 linear-ready.py --team PreThink --project <uuid> --project <uuid>:5
  python3 linear-ready.py --team PreThink --max-estimate 5 --limit 100
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

# Resolve by id via the `teams(filter:)` form (not the root `team(id:)` arg):
# it mirrors PRELUDE_BY_NAME and reuses the `id: { eq: <ID> }` comparator that
# ISSUES_QUERY already proves takes `ID` — so $id is `ID!`, sidestepping the
# String!-vs-ID mismatch that bit the issues filter.
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
ISSUES_QUERY = """
query($cursor: String, $first: Int!, $team: ID!%s) {
  issues(first: $first, after: $cursor, filter: {
    team: { id: { eq: $team } }
    state: { type: { eq: "unstarted" } }
    %s
  }) {
    nodes {
      id identifier title priority estimate updatedAt branchName url
      assignee { id isMe displayName }
      labels { nodes { name } }
      state { id type }
      project { id name }
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
    nodes = data["teams"]["nodes"]  # both preludes now return teams.nodes
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


def parse_project_arg(raw, default_max):
    # <uuid> or <uuid>:<max>
    if ":" in raw:
        uuid, _, max_str = raw.rpartition(":")
        return uuid, int(max_str)
    return raw, default_max


def gate(issue, max_estimate):
    """Return a drop reason string, or None if the issue survives the gates."""
    estimate = issue.get("estimate")
    if estimate is None:
        return "no estimate set"
    if estimate >= max_estimate:
        return f"estimate {estimate} >= {max_estimate}"
    label_names = {n["name"] for n in issue["labels"]["nodes"]}
    if "auto-claimed" in label_names:
        return "already auto-claimed"
    if "human-approval-requested" in label_names:
        return "human-approval-requested"
    if "blocked" in label_names:
        return "blocked"
    assignee = issue.get("assignee")
    if assignee and not assignee.get("isMe"):
        who = assignee.get("displayName") or assignee.get("id") or "unknown"
        return f"assigned to {who}"
    return None


def rank_key(candidate):
    priority = candidate["priority"] or 0
    return (priority if priority != 0 else float("inf"), candidate["_updatedAt"])


def main():
    ap = argparse.ArgumentParser(
        description="Resolve ready Linear candidates via a GraphQL fast-path."
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
        help="Project UUID to scope to, optionally with a per-project max "
        "estimate: <uuid>:<max>. Repeatable. Omit for whole-team scope.",
    )
    ap.add_argument(
        "--max-estimate",
        type=int,
        default=int(os.environ.get("LINEAR_MAX_ESTIMATE", "3")),
        help="Default max estimate (exclusive floor) for scopes without their "
        "own :<max> suffix. Also $LINEAR_MAX_ESTIMATE.",
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

    if args.project:
        scopes = []
        seen_project_ids = set()
        for raw in args.project:
            project_id, max_estimate = parse_project_arg(raw, args.max_estimate)
            if project_id in seen_project_ids:
                continue
            seen_project_ids.add(project_id)
            scopes.append(
                {"id": project_id, "name": None, "max_estimate": max_estimate}
            )
    else:
        scopes = [
            {"id": None, "name": team_node["name"], "max_estimate": args.max_estimate}
        ]

    all_issues = []
    for scope in scopes:
        issues = fetch_issues(key, team_node["id"], scope["id"], args.limit)
        if scope["name"] is None:
            if issues and issues[0].get("project"):
                scope["name"] = issues[0]["project"]["name"]
            else:
                scope["name"] = scope["id"]
        scope_tag = {
            "id": scope["id"],
            "name": scope["name"],
            "max_estimate": scope["max_estimate"],
        }
        for issue in issues:
            issue["project"] = scope_tag
            all_issues.append((issue, scope["max_estimate"]))

    # Unassigned bucket exclusion pass — mirrors linear-common.md's "The
    # Unassigned bucket" (claim variant): only runs when 1+ projects are
    # configured (bare --project args), since with none the whole-team scope
    # above already covers everything. One extra whole-team query, keeping
    # only issues whose project is null or outside the configured id set.
    # Unlike the MCP floor's single capped list_issues call, fetch_issues
    # paginates to exhaustion, so this pass has no truncation blind spot.
    if args.project:
        configured_ids = {scope["id"] for scope in scopes}
        unassigned_scope_tag = {
            "id": "__unassigned__",
            "name": "Unassigned",
            "max_estimate": args.max_estimate,
        }
        for issue in fetch_issues(key, team_node["id"], None, args.limit):
            project = issue.get("project")
            project_id = project["id"] if project else None
            if project_id is not None and project_id in configured_ids:
                continue
            issue["project"] = unassigned_scope_tag
            all_issues.append((issue, args.max_estimate))

    candidates, dropped = [], []
    for issue, max_estimate in all_issues:
        labels = [n["name"] for n in issue["labels"]["nodes"]]
        reason = gate(issue, max_estimate)
        if reason:
            dropped.append({"identifier": issue["identifier"], "reason": reason})
            continue
        candidates.append(
            {
                "id": issue["id"],
                "identifier": issue["identifier"],
                "title": issue["title"],
                "priority": issue["priority"],
                "estimate": issue["estimate"],
                "labels": labels,
                "url": issue["url"],
                "branchName": issue["branchName"],
                "state": issue["state"],
                "project": issue["project"],
                "_updatedAt": issue["updatedAt"],
            }
        )

    candidates.sort(key=rank_key)
    for c in candidates:
        del c["_updatedAt"]

    print(f"Resolved team={team_node['name']} scopes={len(scopes)}", file=sys.stderr)
    print(f"{len(candidates)} candidate(s), {len(dropped)} dropped", file=sys.stderr)

    result = {
        "meta": {
            "viewer": viewer,
            "team": {"id": team_node["id"], "name": team_node["name"]},
            "states": team_node["states"]["nodes"],
        },
        "candidates": candidates,
        "dropped": dropped,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
