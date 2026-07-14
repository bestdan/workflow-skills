#!/usr/bin/env python3
"""Detect Linear issues that were closed without any work behind them.

This workspace's Linear/GitHub integration treats a bare issue id (``PRE-123``)
appearing *anywhere* in a merged PR's title or body as a closing reference. A PR
that merely mentions a sibling issue therefore sweeps that sibling to Done, with
no branch, no PR, and no code. It has done so repeatedly, and it's why that
integration got disabled.

This script is the standalone backstop that detects those false closures and
optionally restores them. (See commands/handlers/linear-false-closures.md for
how it relates to the other Linear task commands.)

The test applied here:

    A completed issue must be OWNED by a merged PR.

Ownership means the PR is *about* the issue, established by any of: its head
branch embeds the issue's identifier (regex match, not equality on Linear's
suggested ``branchName``: the real branch is routinely a shortened form of it);
the issue carries a link attachment pointing at a merged PR; or the PR
title/body *closes* the issue with a keyword (``closes PRE-123``). A PR that
merely name-drops the id -- no branch, no attachment, no closing keyword --
owns nothing. A completed issue with no owning merged PR is a false closure.

The closing-keyword signal matters on cloud/hosted runs, where the PR head
branch often does not embed the Linear id, so the branch match alone would
miss delivered work.

Safety: READ-ONLY by default -- lists false closures and changes nothing. Pass
--apply to restore them to their own team's Todo/unstarted state (resolved per
issue, since a project can span teams).

Exit codes: in read-only mode, 1 if any false closure was found, 0 otherwise
(composes into CI). With --apply, 0 if every restore succeeded, non-zero only
if a restore failed.

Security boundary: the Linear API key here is a personal API key -- a
full-account bearer token -- and must never be pasted into, or fetched inside,
a claude.ai/Claude Code cloud sandbox. The key is read, in order, from:
  1. $LINEAR_API_KEY, else
  2. `op read "$LINEAR_API_KEY_REF"` (a full op://vault/item/field reference).

Under 1Password desktop-app integration, `op` only unlocks in an authorized
terminal, not in an agent's tool-spawned subshell -- see the "Gotcha" note in
commands/handlers/linear-archive.md. Run this from your own terminal, or
headless with $OP_SERVICE_ACCOUNT_TOKEN / $LINEAR_API_KEY set.

Usage:
  python3 linear-false-closures.py --project <uuid> --repo owner/name
  python3 linear-false-closures.py --project <uuid> --repo owner/name --apply
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

API = "https://api.linear.app/graphql"

COMPLETED_ISSUES = """
query($project: String!, $cursor: String) {
  project(id: $project) {
    name
    issues(filter: { state: { type: { eq: "completed" } } }, first: 100, after: $cursor) {
      nodes {
        id
        identifier
        title
        startedAt
        team { id }
        attachments(first: 250) { nodes { url } pageInfo { hasNextPage } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

UNSTARTED_STATES = """
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


def die(msg):
    sys.exit(f"linear-false-closures: {msg}")


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


def completed_issues(key, project):
    """All completed issues in the project, paginated -- no silent 100-issue cap."""
    out, cursor = [], None
    while True:
        data = gql(key, COMPLETED_ISSUES, {"project": project, "cursor": cursor})
        proj = data.get("project")
        if proj is None:
            die(f"no project {project}")
        page = proj["issues"]
        out.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return proj["name"], out
        cursor = page["pageInfo"]["endCursor"]


def merged_prs(repo):
    """All merged PRs, from `gh` -- the source of truth for what actually shipped.

    Paginates the full closed-PR history (no --limit cap): a silently truncated
    page would misclassify a real, delivered issue as a false closure, and
    --apply would then un-complete real work.
    """
    out = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            "--jq",
            ".[] | select(.merged_at != null) | {headRefName: .head.ref, url: .html_url, title: .title, body: .body}",
            f"repos/{repo}/pulls?state=closed&per_page=100",
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        die(f"gh api pulls failed: {out.stderr.strip()}")
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


PR_IDENTITY = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", re.I)


def pr_identity(url):
    """Canonical `owner/repo/pull/<n>` for a GitHub PR url, else None.

    Linear stores whatever url was attached -- routinely with a trailing slash,
    a `?src=linear` query, a fragment, or a `/files` tab -- so an exact-string
    match against gh's canonical `html_url` misses real ownership links and
    would misclassify delivered work as a false closure. Compare parsed
    identities, not raw strings.
    """
    m = PR_IDENTITY.search(url or "")
    return f"{m.group(1).lower()}/pull/{m.group(2)}" if m else None


def owning_pr(issue, prs, merged):
    """The merged PR that actually delivered this issue, or None.

    Match on the issue id embedded in the head branch (e.g. `.../pre-511-...`)
    rather than on Linear's suggested `branchName`: the branch actually used is
    routinely a shortened form of it, so an equality test would report real,
    delivered work as a false closure -- and --apply would then un-complete it.

    Three ownership signals, in order of authority: the branch it was built on,
    a link attachment the issue itself carries, or a PR whose title/body closes
    the issue with a keyword (``closes PRE-123``). A bare mention of the id --
    none of the three -- owns nothing; that is the over-close bug this catches.
    The keyword signal is what covers cloud runs whose branches lack the id.
    """
    ident = re.escape(issue["identifier"].lower())
    token = re.compile(rf"(?<!\d){ident}(?!\d)")
    closes = re.compile(
        rf"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#?{ident}(?!\d)", re.I
    )
    for pr in prs:
        if token.search((pr.get("headRefName") or "").lower()):
            return pr["url"]
    for att in issue["attachments"]["nodes"]:
        key = pr_identity(att["url"])
        if key and key in merged:
            return att["url"]
    for pr in prs:
        if closes.search(f"{pr.get('title') or ''}\n{pr.get('body') or ''}"):
            return pr["url"]
    return None


def resolve_todo_state(key, team_id, cache):
    """The team's preferred unstarted state, resolved (and cached) per team.

    A project can span teams, so this must be re-resolved per issue's own
    team -- resolving it once from the first false closure's team and reusing
    it for every issue would move issues into the wrong team's workflow.
    """
    if team_id in cache:
        return cache[team_id]
    states = gql(key, UNSTARTED_STATES, {"team": team_id})["team"]["states"]["nodes"]
    if not states:
        die(f"team {team_id} has no unstarted-type state")
    todo = next((s for s in states if s["name"].lower() == "todo"), states[0])
    cache[team_id] = todo
    return todo


def main():
    ap = argparse.ArgumentParser(
        description="Detect Linear issues closed with no work behind them."
    )
    ap.add_argument("--project", required=True, help="Linear project UUID.")
    ap.add_argument("--repo", required=True, help="owner/name of the GitHub repo.")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Restore false closures to Todo. Without it, DRY RUN.",
    )
    args = ap.parse_args()

    key = get_key()
    project_name, issues = completed_issues(key, args.project)

    prs = merged_prs(args.repo)
    merged = {k for k in (pr_identity(pr["url"]) for pr in prs) if k}

    false_closures, legit, truncated = [], [], []
    for issue in issues:
        owner = owning_pr(issue, prs, merged)
        if owner:
            legit.append((issue, owner))
        elif issue["attachments"]["pageInfo"]["hasNextPage"]:
            # >250 attachments: we can't prove none of them owns this issue, so
            # never call it a false closure (a wrong call would reopen real work).
            truncated.append(issue)
        else:
            false_closures.append((issue, owner))

    print(f"project: {project_name}  ({len(issues)} completed issues)")
    for issue, owner in legit:
        print(f"  ok    {issue['identifier']}  <- {owner}")
    for issue in truncated:
        print(
            f"  skip  {issue['identifier']}  (>250 attachments — not classified)"
        )

    if not false_closures:
        print("\nno false closures.")
        return 0

    print(
        f"\nFALSE CLOSURES ({len(false_closures)}) — completed, but no merged PR owns them:"
    )
    for issue, _ in false_closures:
        never = " (never started)" if not issue["startedAt"] else ""
        print(f"  BAD   {issue['identifier']}  {issue['title'][:60]}{never}")

    if not args.apply:
        print(
            f"\nDRY RUN — nothing restored. Re-run with --apply to restore these "
            f"{len(false_closures)} to Todo."
        )
        return 1

    print(f"\nRestoring {len(false_closures)}...")
    cache, ok, fail = {}, 0, 0
    for issue, _ in false_closures:
        try:
            todo = resolve_todo_state(key, issue["team"]["id"], cache)
            if gql(key, MOVE_ISSUE, {"id": issue["id"], "state": todo["id"]})[
                "issueUpdate"
            ]["success"]:
                ok += 1
                print(f"  restored {issue['identifier']} -> {todo['name']}")
            else:
                fail += 1
                print(f"  FAILED {issue['identifier']} (success=false)")
        except SystemExit as e:
            fail += 1
            print(f"  FAILED {issue['identifier']}: {e}")
    print(f"\nRestored {ok}, failed {fail}.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
