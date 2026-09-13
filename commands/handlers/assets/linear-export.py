#!/usr/bin/env python3
"""Dump every issue in a Linear team — archived ones included — to one
date-named JSON file, as the provenance record for keys that outlive the
workspace.

Linear issue keys are baked into branch names (`bestdan/ss-earnings-record-task-6`)
and commit subjects (`[PRE-663] …`) across several repos, and the Linear
workspace is the only place a key resolves to a title, a body, or a discussion.
Once those issues are cancelled, archived or deleted, this file is the only
thing that can answer "what was PRE-663?". So the export is deliberately
TEAM-WIDE and unfiltered: selecting a subset is a downstream concern, and a
subset is not a provenance record.

Read-only. Never mutates anything.

TRUNCATION IS THE FAILURE MODE THIS GUARDS AGAINST. A partial export looks
exactly like a complete one — it is a well-formed JSON file with issues in it —
so every place this could silently drop rows fails loudly instead:

  - the top-level `issues` query paginates to exhaustion on `endCursor`;
  - every nested connection (comments, relations, labels, …) is fetched at
    `first: NESTED_PAGE` and RAISES if `pageInfo.hasNextPage` is still true,
    rather than writing the first page and calling it the whole set;
  - an existing file for today's date is refused without --force.

The export deliberately asserts no issue count. The workspace's total moves;
what the file records is what the paginated query returned, and the summary
reports that number rather than checking it against a literal.

The API key is resolved by commands/handlers/assets/_secret_resolve.py, which
walks two independent ladders: secret/pointer (`$LINEAR_API_KEY` ->
`$LINEAR_API_KEY_REF` -> unavailable) and resolver (`$LINEAR_API_KEY_RESOLVER`
-> default `op`), against an allow-list of resolver identifiers (`op`, `opx`).
A failed resolve never falls through to the next rung. See
dev_docs/auth_key_access.md for the full contract. This script reads no config.

SCHEMA NOTE — the `relations` / `inverseRelations` shape is inherited from
linear-relations.py, whose header records it as documented-but-unverified. This
repo runs keyless, so `scripts/test-linear-export-live.sh` is where drift in
those field names surfaces for the first time.

Usage:
  python3 linear-export.py --team PreThink --out ~/src/linear-export
  python3 linear-export.py --team PreThink --out ~/src/linear-export --json
  python3 linear-export.py --team <uuid> --out <dir> --force
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secret_resolve import SecretUnavailable, resolve_key  # noqa: E402
from _shape import ShapeError, expect  # noqa: E402

API = "https://api.linear.app/graphql"

# linear.team may be a team NAME or a UUID id (see linear-common.md / linear-config.md).
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# Top-level page size. 100 is Linear's documented ceiling for `issues`, and the
# per-issue payload here is the heaviest in this directory (description plus
# every comment body), so this is the page size that both fits and finishes.
PAGE = 100

# Nested-connection page size, and the cap that turns an overflow into a crash.
# 250 is far above anything observed (the busiest PreThink issue carries a
# couple of dozen comments), so an overflow means the assumption broke — which
# is exactly when a loud failure beats a quiet truncation.
NESTED_PAGE = 250

# Every nested connection selected below — the single list check_nested() walks.
# Adding a connection to the query means adding it here, or its overflow goes
# unchecked.
NESTED_FIELDS = (
    "children",
    "labels",
    "relations",
    "inverseRelations",
    "attachments",
    "comments",
)

TEAM_BY_ID = """
query($id: ID!) {
  teams(filter: { id: { eq: $id } }) { nodes { id key name } }
}"""

TEAM_BY_NAME = """
query($name: String!) {
  teams(filter: { name: { eq: $name } }) { nodes { id key name } }
}"""

# includeArchived: true is the whole point — 513 of the 781 issues at plan time
# were archived, and those are the ones whose keys are least recoverable by any
# other means.
ISSUES_QUERY = """
query($cursor: String, $first: Int!, $nested: Int!, $team: ID!) {
  issues(first: $first, after: $cursor, includeArchived: true, filter: {
    team: { id: { eq: $team } }
  }) {
    nodes {
      id identifier title description priority estimate
      createdAt updatedAt archivedAt completedAt canceledAt startedAt
      url branchName
      state { name type }
      project { id name }
      parent { identifier }
      assignee { name email }
      creator { name }
      children(first: $nested) { nodes { identifier } pageInfo { hasNextPage } }
      labels(first: $nested) { nodes { name } pageInfo { hasNextPage } }
      relations(first: $nested) {
        nodes { type relatedIssue { identifier } }
        pageInfo { hasNextPage }
      }
      inverseRelations(first: $nested) {
        nodes { type issue { identifier } }
        pageInfo { hasNextPage }
      }
      attachments(first: $nested) {
        nodes { title url }
        pageInfo { hasNextPage }
      }
      comments(first: $nested) {
        nodes { body createdAt user { name } }
        pageInfo { hasNextPage }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""


class ExportError(Exception):
    """A failure that must abort the export rather than shrink it.

    Raised (not sys.exit'd) so the nested-overflow guard is assertable from a
    hermetic test without the test having to catch SystemExit and re-read the
    message — main() turns it into the exit.
    """


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
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"GraphQL HTTP {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"GraphQL request failed: {e.reason}")
    # Guard the root BEFORE the membership test: `"errors" in None` and
    # `"errors" in 5` raise TypeError, so a scalar JSON body would reach neither
    # this check nor expect() below.
    if not isinstance(payload, dict):
        sys.exit(f"GraphQL response: expected an object, got {type(payload).__name__}")
    if "errors" in payload:
        sys.exit("GraphQL error: " + json.dumps(payload["errors"], indent=2))
    try:
        return expect(payload, "data", dict, "GraphQL response")
    except ShapeError as exc:
        sys.exit(str(exc))


def resolve_team(key, team):
    """Return the team's {id, key, name} — accepting a UUID or a name.

    The name is needed even when a UUID was passed: it names the output file,
    and a file called `<uuid>.json` is a worse provenance record than one
    called `prethink.json`.
    """
    if UUID_RE.match(team):
        data = gql(key, TEAM_BY_ID, {"id": team})
    else:
        data = gql(key, TEAM_BY_NAME, {"name": team})
    nodes = data["teams"]["nodes"]
    if not nodes:
        sys.exit(f"Team not found: {team}")
    return nodes[0]


def check_nested(node):
    """Raise if any nested connection on `node` had a page we did not fetch.

    This is the guard the whole script is organised around: a connection that
    reports hasNextPage at NESTED_PAGE means the export is missing data it
    claims to hold, and there is nothing downstream that could notice.
    """
    for field in NESTED_FIELDS:
        conn = node.get(field) or {}
        if (conn.get("pageInfo") or {}).get("hasNextPage"):
            raise ExportError(
                f"{node.get('identifier', '<unknown>')}: {field} has more than "
                f"{NESTED_PAGE} entries — refusing to write a truncated export. "
                f"Raise NESTED_PAGE or paginate {field}."
            )


def edges(node):
    """Flatten relations/inverseRelations to (type, the other identifier) pairs.

    Direction is preserved by which list a pair lands in, mirroring
    linear-relations.py: `relations` is what this issue points at,
    `inverseRelations` is what points at it.
    """
    out = []
    for entry in (node.get("relations") or {}).get("nodes") or []:
        other = entry.get("relatedIssue") or {}
        out.append({"type": entry.get("type"), "identifier": other.get("identifier")})
    inverse = []
    for entry in (node.get("inverseRelations") or {}).get("nodes") or []:
        other = entry.get("issue") or {}
        inverse.append(
            {"type": entry.get("type"), "identifier": other.get("identifier")}
        )
    return out, inverse


def shape(node):
    """One issue, flattened — connections become plain lists, scalars pass through."""
    check_nested(node)
    relations, inverse_relations = edges(node)
    return {
        "id": node.get("id"),
        "identifier": node.get("identifier"),
        "title": node.get("title"),
        "description": node.get("description"),
        "priority": node.get("priority"),
        "estimate": node.get("estimate"),
        "createdAt": node.get("createdAt"),
        "updatedAt": node.get("updatedAt"),
        "archivedAt": node.get("archivedAt"),
        "completedAt": node.get("completedAt"),
        "canceledAt": node.get("canceledAt"),
        "startedAt": node.get("startedAt"),
        "url": node.get("url"),
        "branchName": node.get("branchName"),
        "state": node.get("state"),
        "project": node.get("project"),
        "parent": (node.get("parent") or {}).get("identifier"),
        "children": [
            c.get("identifier") for c in (node.get("children") or {}).get("nodes") or []
        ],
        "assignee": node.get("assignee"),
        "creator": node.get("creator"),
        "labels": [
            label.get("name") for label in (node.get("labels") or {}).get("nodes") or []
        ],
        "relations": relations,
        "inverseRelations": inverse_relations,
        "attachments": list((node.get("attachments") or {}).get("nodes") or []),
        "comments": list((node.get("comments") or {}).get("nodes") or []),
    }


def fetch_issues(key, team_id):
    """Every issue in the team, paginated to exhaustion.

    The cursor comes from `endCursor` and the loop ends on `hasNextPage` —
    never on a page that came back short, which is how a cursor loop silently
    stops one page early.
    """
    out, cursor = [], None
    while True:
        page = gql(
            key,
            ISSUES_QUERY,
            {
                "cursor": cursor,
                "first": PAGE,
                "nested": NESTED_PAGE,
                "team": team_id,
            },
        )["issues"]
        for node in page["nodes"]:
            out.append(shape(node))
        info = page["pageInfo"]
        if not info["hasNextPage"]:
            return out
        cursor = info["endCursor"]


def out_path(out_dir, team_name, now):
    slug = re.sub(r"[^a-z0-9]+", "-", team_name.lower()).strip("-") or "team"
    return os.path.join(out_dir, f"{now.strftime('%Y-%m-%d')}-{slug}.json")


def summary(document, path):
    """Counts worth eyeballing before trusting the file, plus where it landed.

    `archived` is called out separately because the archived set is the half
    that cannot be recovered from Linear's UI, so "did the export actually
    include them" is the one question a reader most needs answered.
    """
    issues = document["issues"]
    return {
        "path": path,
        "team": document["team"],
        "issue_count": document["issue_count"],
        "archived": sum(1 for i in issues if i.get("archivedAt")),
        "with_comments": sum(1 for i in issues if i.get("comments")),
        "with_relations": sum(
            1 for i in issues if i.get("relations") or i.get("inverseRelations")
        ),
        "projects": len({(i.get("project") or {}).get("id") for i in issues} - {None}),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--team", required=True, help="Linear team name or UUID")
    ap.add_argument(
        "--out", required=True, help="destination directory (created if missing)"
    )
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    ap.add_argument(
        "--force", action="store_true", help="overwrite today's export if it exists"
    )
    args = ap.parse_args(argv)

    out_dir = os.path.expanduser(args.out)
    key = get_key()
    team = resolve_team(key, args.team)
    now = datetime.now(timezone.utc)
    path = out_path(out_dir, team["name"], now)

    # Checked BEFORE the (slow, rate-limited) fetch: refusing after ten minutes
    # of pagination wastes the run and teaches the caller to pass --force
    # reflexively.
    if os.path.exists(path) and not args.force:
        sys.exit(f"Refusing to overwrite {path} — pass --force to replace it.")

    try:
        issues = fetch_issues(key, team["id"])
    except ExportError as exc:
        sys.exit(str(exc))

    document = {
        "exported_at": now.isoformat().replace("+00:00", "Z"),
        "team": {"id": team["id"], "key": team.get("key"), "name": team["name"]},
        "issue_count": len(issues),
        "issues": issues,
    }

    os.makedirs(out_dir, exist_ok=True)
    # Write-then-rename: a crash mid-write must not leave a half-written file
    # sitting where a reader would take it for the export.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)

    info = summary(document, path)
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"Exported {info['issue_count']} issue(s) from {team['name']} -> {path}")
        print(
            f"  archived={info['archived']}  with_comments={info['with_comments']}  "
            f"with_relations={info['with_relations']}  projects={info['projects']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
