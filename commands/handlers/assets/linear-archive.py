#!/usr/bin/env python3
"""Archive terminal-state Linear issues older than N days, to keep a workspace
under Linear's free-plan cap of 250 *active* issues (archived issues are
unlimited and excluded from the cap).

This is the standalone backstop for the `linear` handler's `/archive-tasks` flow
(see commands/handlers/linear-archive.md). The Linear MCP exposes no archive
mutation, so this talks to the GraphQL API directly with a personal API key.

Safety: DRY RUN by default — lists candidates and changes nothing. Pass --apply
to actually archive. Re-running is idempotent (archived issues are excluded from
the query by default).

The API key is read, in order, from:
  1. $LINEAR_API_KEY, else
  2. `op read "$LINEAR_API_KEY_REF"` (a full op://vault/item/field reference).

IMPORTANT: under 1Password desktop-app integration, `op` only unlocks in an
authorized terminal — NOT in an agent's tool-spawned subshell. Run this from your
own terminal, or headless with $OP_SERVICE_ACCOUNT_TOKEN / $LINEAR_API_KEY set.

Usage:
  python3 linear-archive.py --team PreThink --older-than 10
  python3 linear-archive.py --team PreThink --older-than 10 --apply
  python3 linear-archive.py --team PreThink --older-than 30 --project <uuid> --include-canceled --apply
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.linear.app/graphql"

# linear.team may be a team NAME or a UUID id (see linear-common.md / linear-config.md).
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# %s slots: project var decl, team field, ts field, extra filter, ts field (nodes).
# The $project variable is declared only when scoping to a project — GraphQL
# rejects (HTTP 400) an operation that declares a variable it never uses, which
# is exactly what the whole-team path (no --project) would otherwise do.
QUERY = """
query($cursor: String, $cutoff: DateTimeOrDuration!, $team: String!, $type: String!%s) {
  issues(first: 100, after: $cursor, filter: {
    team: { %s: { eq: $team } }
    state: { type: { eq: $type } }
    %s: { lt: $cutoff }
    %s
  }) {
    nodes { id identifier title %s }
    pageInfo { hasNextPage endCursor }
  }
}"""

ARCHIVE = "mutation($id: String!) { issueArchive(id: $id, trash: false) { success } }"


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
    with urllib.request.urlopen(req) as r:
        payload = json.loads(r.read())
    if "errors" in payload:
        sys.exit("GraphQL error: " + json.dumps(payload["errors"], indent=2))
    return payload["data"]


def find(key, team, project, state_type, ts_field, cutoff):
    team_field = "id" if UUID_RE.match(team) else "name"  # UUID team id, else name
    var_decl = ", $project: ID" if project else ""
    extra = "project: { id: { eq: $project } }" if project else ""
    query = QUERY % (var_decl, team_field, ts_field, extra, ts_field)
    out, cursor = [], None
    while True:
        variables = {"cursor": cursor, "cutoff": cutoff, "team": team, "type": state_type}
        if project:
            variables["project"] = project
        page = gql(key, query, variables)["issues"]
        out.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return out
        cursor = page["pageInfo"]["endCursor"]


def main():
    ap = argparse.ArgumentParser(
        description="Archive old terminal-state Linear issues."
    )
    ap.add_argument(
        "--team",
        default=os.environ.get("LINEAR_TEAM"),
        required=os.environ.get("LINEAR_TEAM") is None,
        help="Team name (e.g. PreThink) or $LINEAR_TEAM.",
    )
    ap.add_argument(
        "--older-than",
        type=int,
        default=int(os.environ.get("ARCHIVE_AFTER", "0")) or None,
        help="Age threshold in days (or $ARCHIVE_AFTER).",
    )
    ap.add_argument(
        "--project", default=None, help="Optional project UUID to scope to."
    )
    ap.add_argument(
        "--include-canceled",
        action="store_true",
        help="Also sweep Canceled issues (by canceledAt). Default: Done only.",
    )
    ap.add_argument(
        "--apply", action="store_true", help="Archive. Without it, DRY RUN."
    )
    args = ap.parse_args()

    if not args.older_than or args.older_than <= 0:
        sys.exit(
            "Refusing to run without an age threshold. Pass --older-than <N> (or set $ARCHIVE_AFTER)."
        )

    key = get_key()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.older_than)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    passes = [("completed", "completedAt")]
    if args.include_canceled:
        passes.append(("canceled", "canceledAt"))

    candidates = []
    for state_type, ts_field in passes:
        for issue in find(key, args.team, args.project, state_type, ts_field, cutoff):
            issue["_when"] = issue.get(ts_field, "")[:10]
            candidates.append(issue)

    scope = f"team={args.team}" + (f", project={args.project}" if args.project else "")
    types = "Done + Canceled" if args.include_canceled else "Done"
    print(f"Cutoff: {cutoff}  ({scope}, {types})\n")

    if not candidates:
        print("No terminal-state issues older than the cutoff. Nothing to archive.")
        return

    candidates.sort(key=lambda i: i["_when"])
    print(f"{len(candidates)} candidate(s):")
    for i in candidates:
        print(f"  {i['identifier']:<10} {i['_when']}  {i['title'][:70]}")

    if not args.apply:
        print(
            f"\nDRY RUN — nothing archived. Re-run with --apply to archive these {len(candidates)}."
        )
        return

    print(f"\nArchiving {len(candidates)}...")
    ok = fail = 0
    for i in candidates:
        try:
            if gql(key, ARCHIVE, {"id": i["id"]})["issueArchive"]["success"]:
                ok += 1
            else:
                fail += 1
                print(f"  FAILED {i['identifier']} (success=false)")
        except SystemExit as e:
            fail += 1
            print(f"  FAILED {i['identifier']}: {e}")
    print(
        f"\nArchived {ok}, failed {fail}. Archived issues no longer count toward the 250 cap."
    )


if __name__ == "__main__":
    main()
