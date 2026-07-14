#!/usr/bin/env python3
"""Detect Linear issues that were closed without any work.

This workspace's Linear/GitHub integration treats a bare issue id (``PRE-123``)
appearing *anywhere* in a merged PR's title or body as a closing reference. A PR
that merely mentions a sibling issue therefore sweeps that sibling to Done, with
no branch, no PR, and no code. It has done so repeatedly.

``/reconcile-tasks`` cannot repair this: its rule table is promote/complete-only
and never demotes, so a falsely-completed issue is invisible to it.

The test applied here:

    A completed issue must be OWNED by a merged PR.

Ownership means the PR is *about* the issue -- its head branch is the issue's
branch, or the issue carries a link attachment pointing at it. A PR that only
name-drops the id in its body owns nothing. A completed issue with no owning
merged PR is a false closure.

Read-only by default; ``--fix`` restores false closures to the team's Todo state.

Auth: a Linear personal API key in ``LINEAR_API_KEY``, or ``--op-ref`` to read
one from 1Password (e.g. ``op://Private/Linear API/credential``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import NoReturn

API = "https://api.linear.app/graphql"


def die(msg: str) -> NoReturn:
    sys.exit(f"linear-false-closures: {msg}")


def api_key(op_ref: str | None) -> str:
    if op_ref:
        out = subprocess.run(
            ["op", "read", op_ref], capture_output=True, text=True, check=False
        )
        if out.returncode != 0:
            die(f"op read {op_ref} failed: {out.stderr.strip()}")
        return out.stdout.strip()
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        die("no LINEAR_API_KEY in env; pass --op-ref to read one from 1Password")
    return key


def query(key: str, gql: str, variables: dict) -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": gql, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        die(f"Linear API {e.code}: {e.read().decode()[:200]}")
    if "errors" in body:
        die(f"Linear API error: {body['errors']}")
    return body["data"]


COMPLETED_ISSUES = """
query($project: String!) {
  project(id: $project) {
    name
    issues(filter: { state: { type: { eq: "completed" } } }, first: 100) {
      nodes {
        identifier
        title
        branchName
        startedAt
        team { id }
        attachments(first: 20) { nodes { url } }
      }
    }
  }
}
"""

TODO_STATE = """
query($team: String!) {
  team(id: $team) {
    states(filter: { type: { eq: "unstarted" } }, first: 10) {
      nodes { id name }
    }
  }
}
"""

MOVE_ISSUE = """
mutation($id: String!, $state: String!) {
  issueUpdate(id: $id, input: { stateId: $state }) { success }
}
"""


def merged_prs(repo: str) -> list[dict]:
    """Merged PRs, from `gh` -- the source of truth for what actually shipped."""
    out = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--limit",
            "200",
            "--json",
            "number,headRefName,url",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        die(f"gh pr list failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def owning_pr(issue: dict, prs: list[dict], merged: set[str]) -> str | None:
    """The merged PR that actually delivered this issue, or None.

    Match on the issue id embedded in the head branch (``…/pre-511-…``) rather
    than on Linear's suggested ``branchName``: the branch actually used is
    routinely a shortened form of it, so an equality test would report real,
    delivered work as a false closure -- and ``--fix`` would then un-complete it.

    A PR that merely mentions the id in its body owns nothing; only the branch
    it was built on, or a link attachment the issue itself carries, counts.
    """
    ident = re.escape(issue["identifier"].lower())
    token = re.compile(rf"(?<!\d){ident}(?!\d)")
    for pr in prs:
        if token.search(pr["headRefName"].lower()):
            return pr["url"]
    for att in issue["attachments"]["nodes"]:
        if att["url"] in merged:
            return att["url"]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, help="Linear project UUID")
    ap.add_argument("--repo", required=True, help="owner/name of the GitHub repo")
    ap.add_argument("--op-ref", help="1Password op:// reference to a Linear API key")
    ap.add_argument(
        "--fix",
        action="store_true",
        help="restore false closures to Todo (default: report only)",
    )
    args = ap.parse_args()

    key = api_key(args.op_ref)
    data = query(key, COMPLETED_ISSUES, {"project": args.project})
    project = data.get("project") or die(f"no project {args.project}")
    issues = project["issues"]["nodes"]

    prs = merged_prs(args.repo)
    merged = {pr["url"] for pr in prs}

    false_closures, legit = [], []
    for issue in issues:
        owner = owning_pr(issue, prs, merged)
        (legit if owner else false_closures).append((issue, owner))

    print(f"project: {project['name']}  ({len(issues)} completed issues)")
    for issue, owner in legit:
        print(f"  ok    {issue['identifier']}  <- {owner}")
    if not false_closures:
        print("\nno false closures.")
        return 0

    print(
        f"\nFALSE CLOSURES ({len(false_closures)}) — completed, but no merged PR owns them:"
    )
    for issue, _ in false_closures:
        never = " (never started)" if not issue["startedAt"] else ""
        print(f"  BAD   {issue['identifier']}  {issue['title'][:60]}{never}")

    if not args.fix:
        print("\nre-run with --fix to restore these to Todo.")
        return 1

    team = false_closures[0][0]["team"]["id"]
    states = query(key, TODO_STATE, {"team": team})["team"]["states"]["nodes"]
    todo = next((s for s in states if s["name"] == "Todo"), states[0])
    for issue, _ in false_closures:
        query(key, MOVE_ISSUE, {"id": issue["identifier"], "state": todo["id"]})
        print(f"  restored {issue['identifier']} -> {todo['name']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
