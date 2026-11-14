#!/usr/bin/env python3
"""GraphQL fast path for the Linear handler's /complete-task phase (see
commands/handlers/linear-complete.md, the single source of truth for what this
transition does — change it there first and keep this file in lockstep).

It does what that file's MCP steps do, over the API with a personal key:
resolve the team, resolve the issue, pick the team's completed-type state by
TYPE (never by display name), refuse to write when the issue is already
terminal, move it, and post the caller's completion comment. It does no PR
discovery and no merge verification; that is the caller's job.

Exit status is the fallback contract, and it is narrower than "did it work":
a non-zero exit means NOTHING WAS WRITTEN, so the caller may fall to the MCP
floor without risking a double write. Once the state change has landed, the
script exits 0 even if the comment then fails, and reports the comment as
`failed` in its JSON so the caller can post it another way.

The API key is resolved by _secret_resolve.py (see dev_docs/auth_key_access.md);
this script reads no config. Stdout carries exactly one JSON object:

  {"identifier": "ABC-12", "from": {"name": "In Progress", "type": "started"},
   "to": {"name": "Done", "type": "completed"},
   "result": "completed" | "already-complete" | "canceled" | "planned",
   "comment": "posted" | "skipped" | "failed", "comment_error": "..."}

`to` is null when nothing was planned (already terminal). `comment_error`
appears only with `"comment": "failed"`.

Usage:
  python3 linear-complete.py --team PreThink --issue ABC-12 --dry-run
  python3 linear-complete.py --team PreThink --issue ABC-12 \
    --comment "Closed by merge of PR #7 (https://github.com/o/r/pull/7)"
  python3 linear-complete.py --team PreThink --issue ABC-12 --comment ""   # no comment
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secret_resolve import SecretUnavailable, resolve_key  # noqa: E402
from _shape import ShapeError, expect  # noqa: E402

API = "https://api.linear.app/graphql"

# The comment linear-complete.md's caller contract posts when the caller
# omits one. An explicit empty string means "post none".
DEFAULT_COMMENT = "Completed via /complete-task"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

TEAM_FIELDS = "nodes { id name states { nodes { id name type position } } }"
TEAM_BY_ID = (
    "query($id: ID!) { teams(filter: { id: { eq: $id } }) { %s } }" % TEAM_FIELDS
)
TEAM_BY_NAME = (
    "query($name: String!) { teams(filter: { name: { eq: $name } }) { %s } }"
    % TEAM_FIELDS
)

# `issue(id:)` takes the UUID or the shorthand team-key-and-number identifier.
ISSUE = """
query($id: String!) {
  issue(id: $id) { id identifier team { id } state { id name type } }
}"""

MOVE = """
mutation($id: String!, $state: String!) {
  issueUpdate(id: $id, input: { stateId: $state }) { success }
}"""

COMMENT = """
mutation($issue: String!, $body: String!) {
  commentCreate(input: { issueId: $issue, body: $body }) { success }
}"""


def die(msg):
    sys.exit(f"linear-complete: {msg}")


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


def resolve_team(key, team):
    if UUID_RE.match(team):
        data = gql(key, TEAM_BY_ID, {"id": team})
    else:
        data = gql(key, TEAM_BY_NAME, {"name": team})
    nodes = data["teams"]["nodes"]
    if not nodes:
        die(f"team not found: {team}")
    return nodes[0]


def completed_state(team_node):
    """The team's first completed-type state by board position.

    Linear marks no completed state as the default, so position — the order
    the board shows them in — is the tie-break linear-complete.md's "otherwise
    take the first" means. Matching on type keeps a renamed Done column working.
    """
    states = [s for s in team_node["states"]["nodes"] if s["type"] == "completed"]
    if not states:
        die(f"team {team_node['name']} has no completed-type workflow state")
    return min(states, key=lambda s: s.get("position") or 0)


def plan(team_node, issue):
    """Decide the outcome without writing. Returns (result, target_state)."""
    if issue["team"]["id"] != team_node["id"]:
        die(f"{issue['identifier']} is not on team {team_node['name']}")
    current = issue["state"]["type"]
    if current == "completed":
        return "already-complete", None
    if current == "canceled":
        return "canceled", None
    return "planned", completed_state(team_node)


def post_comment(key, issue_id, body):
    """Post the completion comment. Never exits: the state already moved."""
    try:
        ok = gql(key, COMMENT, {"issue": issue_id, "body": body})
    except SystemExit as e:
        return "failed", str(e.code)
    if not (ok.get("commentCreate") or {}).get("success"):
        return "failed", "commentCreate returned success=false"
    return "posted", None


def run(key, team, identifier, comment, dry_run):
    team_node = resolve_team(key, team)
    issue = gql(key, ISSUE, {"id": identifier}).get("issue")
    if not issue:
        die(f"no issue found for {identifier}")
    result, target = plan(team_node, issue)
    out = {
        "identifier": issue["identifier"],
        "from": {"name": issue["state"]["name"], "type": issue["state"]["type"]},
        "to": {"name": target["name"], "type": target["type"]} if target else None,
        "result": result,
        "comment": "skipped",
    }
    if result != "planned" or dry_run:
        return out

    moved = gql(key, MOVE, {"id": issue["id"], "state": target["id"]})
    if not (moved.get("issueUpdate") or {}).get("success"):
        die(f"issueUpdate returned success=false for {issue['identifier']}")
    out["result"] = "completed"

    if comment:
        out["comment"], err = post_comment(key, issue["id"], comment)
        if err:
            out["comment_error"] = err
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Move one Linear issue to its team's completed-type state."
    )
    ap.add_argument("--team", required=True, help="Team name or UUID id.")
    ap.add_argument("--issue", required=True, help="Identifier (ABC-12) or UUID.")
    ap.add_argument(
        "--comment",
        default=DEFAULT_COMMENT,
        help=f'Completion comment (default "{DEFAULT_COMMENT}"); "" posts none.',
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Print the plan; write nothing."
    )
    args = ap.parse_args()
    out = run(get_key(), args.team, args.issue, args.comment, args.dry_run)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
