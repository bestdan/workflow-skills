#!/usr/bin/env python3
"""Archive terminal-state Linear issues — either every one older than N days, or
just the ones named with --issues — to keep a workspace under Linear's free-plan
cap of 250 *active* issues (archived issues are unlimited and excluded from the
cap).

The age threshold is the bulk-hygiene mode and is mandatory *for that mode*, so a
sweep can never run unbounded. --issues is the other mode: the caller has named
the issues, so age stops being the gate (issues closed minutes ago archive fine)
— but terminal state still is, and a named issue that is still open is skipped.

This is the standalone backstop for the `linear` handler's `/archive-tasks` flow
(see commands/handlers/linear-archive.md). The Linear MCP exposes no archive
mutation, so this talks to the GraphQL API directly with a personal API key.

Safety: DRY RUN by default — lists candidates and changes nothing. Pass --apply
to actually archive. Re-running --older-than is idempotent: the sweep query
excludes archived issues by default, so an already-archived issue is simply not
a candidate. --issues cannot lean on that — the caller named those issues, and
silence about them would read as failure — so it looks them up with
includeArchived and reports the already-archived ones as done, not missing.

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

Usage:
  python3 linear-archive.py --team PreThink --older-than 10
  python3 linear-archive.py --team PreThink --older-than 10 --apply
  python3 linear-archive.py --team PreThink --older-than 30 --project <uuid> --apply
  python3 linear-archive.py --team PreThink --issues PRE-12,PRE-13 --apply
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
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

# Explicit-issue lookup (--issues). No age filter — the caller named the issues,
# so the cutoff is not the gate; terminal state still is (checked client-side
# against the returned state.type). Two shapes because the identifier form
# (PRE-12) filters on the team-scoped issue `number`, while a pasted UUID filters
# on `id` — a global key, so its query cannot bind the team and the match is
# checked client-side against the returned `team` instead. Same
# declared-vars-must-be-used rule as QUERY: each shape declares only the
# variables it references.
#
# Both shapes pass includeArchived (the `issues` query drops archived rows
# otherwise) and select archivedAt, so a re-run of --issues can tell "already
# archived" apart from "no such issue" instead of reporting both as missing.
NODE_FIELDS = "id identifier title completedAt canceledAt archivedAt state { type } team { id name }"

# One page, no cursor loop: collect_named rejects a ref list longer than this, so
# a named lookup can never overflow it. Substituted into both queries rather than
# written twice, so the guard and the queries can't drift apart.
PAGE = 250

LOOKUP_BY_NUMBER = """
query($team: String!, $numbers: [Float!]) {
  issues(first: %d, includeArchived: true, filter: {
    team: { %s: { eq: $team } }
    number: { in: $numbers }
  }) {
    nodes { %s }
  }
}"""

LOOKUP_BY_ID = """
query($ids: [ID!]) {
  issues(first: %d, includeArchived: true, filter: { id: { in: $ids } }) {
    nodes { %s }
  }
}"""

TERMINAL_TYPES = {"completed", "canceled", "duplicate"}

IDENTIFIER_RE = re.compile(r"^([A-Z][A-Z0-9_]*)-(\d+)$", re.I)


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
    try:
        out = subprocess.run(
            ["op", "read", ref], capture_output=True, text=True, timeout=10
        )
    except subprocess.TimeoutExpired:
        sys.exit(
            f"Timed out reading key from 1Password ({ref}) after 10s "
            "— if the 1Password app is locked, unlock it or run `op signin` in your own terminal."
        )
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
        with urllib.request.urlopen(req) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Linear API error {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e.reason}")
    if "errors" in payload:
        sys.exit("GraphQL error: " + json.dumps(payload["errors"], indent=2))
    return payload["data"]


def terminal_passes():
    """(state_type, timestamp_field) pairs to sweep — every terminal state.

    Linear has three terminal state types and all of them mean "this issue is
    settled": `completed` (timestamped by completedAt), `canceled`, and
    `duplicate` (both timestamped by canceledAt). `duplicate` is its own type,
    not a flavour of `canceled`.

    All three are swept unconditionally. Anything left unswept can never be
    archived and consumes the workspace issue cap permanently — which is exactly
    what happened to duplicate-closed issues while this was opt-in.
    """
    return [
        ("completed", "completedAt"),
        ("canceled", "canceledAt"),
        ("duplicate", "canceledAt"),
    ]


def find(key, team, project, state_type, ts_field, cutoff):
    team_field = "id" if UUID_RE.match(team) else "name"  # UUID team id, else name
    var_decl = ", $project: ID" if project else ""
    extra = "project: { id: { eq: $project } }" if project else ""
    query = QUERY % (var_decl, team_field, ts_field, extra, ts_field)
    out, cursor = [], None
    while True:
        variables = {
            "cursor": cursor,
            "cutoff": cutoff,
            "team": team,
            "type": state_type,
        }
        if project:
            variables["project"] = project
        page = gql(key, query, variables)["issues"]
        out.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return out
        cursor = page["pageInfo"]["endCursor"]


def parse_issue_refs(raw):
    """Split --issues values into (identifiers, uuids), preserving caller order.

    Accepts repeated flags and comma/whitespace-separated lists of either
    identifiers (`PRE-12`, case-insensitive) or issue UUIDs. Anything else is a
    typo — reject it rather than silently dropping it from the archive set.

    Both forms are case-normalized to the case Linear returns (identifiers
    upper, UUIDs lower) so find_by_ref can match refs against returned nodes by
    plain set membership — an un-normalized ref matches nothing and gets
    reported "not found" even as the issue it names is archived.
    """
    identifiers, uuids, bad = [], [], []
    for chunk in raw:
        for ref in re.split(r"[,\s]+", chunk.strip()):
            if not ref:
                continue
            if UUID_RE.match(ref):
                uuids.append(ref.lower())
            elif IDENTIFIER_RE.match(ref):
                identifiers.append(ref.upper())
            else:
                bad.append(ref)
    if bad:
        sys.exit(
            "Not an issue identifier or UUID: "
            + ", ".join(bad)
            + " (expected e.g. PRE-12, or an issue UUID)"
        )
    return identifiers, uuids


def find_by_ref(key, team, identifiers, uuids):
    """Look up explicitly named issues. Returns (nodes, archived, missing_refs).

    Every ref is confined to `--team`, by two different mechanisms. The `number`
    filter is team-scoped server-side, so another team's `OTH-12` matches
    nothing. An issue `id` is a global key whose query cannot bind the team, so
    that half is checked client-side against the returned `team` instead —
    without it, a pasted UUID would archive an issue on a team the caller never
    named. Either way an out-of-team ref lands in `missing` rather than being
    archived, which is also why returned identifiers are re-checked against what
    was asked for. The team check runs before the archived split, so an archived
    out-of-team ref is still missing, not "already archived".

    Archived matches come back separately from live ones: the queries ask for
    them explicitly, so an already-archived issue is a no-op to report rather
    than a lookup failure. Only `nodes` are archive candidates.
    """
    nodes, archived, seen = [], [], set()
    if identifiers:
        team_field = "id" if UUID_RE.match(team) else "name"
        query = LOOKUP_BY_NUMBER % (PAGE, team_field, NODE_FIELDS)
        numbers = sorted({int(IDENTIFIER_RE.match(i).group(2)) for i in identifiers})
        wanted = set(identifiers)
        for node in gql(key, query, {"team": team, "numbers": numbers})["issues"][
            "nodes"
        ]:
            if node["identifier"].upper() in wanted and node["id"] not in seen:
                seen.add(node["id"])
                (archived if node.get("archivedAt") else nodes).append(node)
    if uuids:
        team_field = "id" if UUID_RE.match(team) else "name"
        query = LOOKUP_BY_ID % (PAGE, NODE_FIELDS)
        for node in gql(key, query, {"ids": sorted(set(uuids))})["issues"]["nodes"]:
            if node["team"][team_field] != team or node["id"] in seen:
                continue
            seen.add(node["id"])
            (archived if node.get("archivedAt") else nodes).append(node)

    # Both sides are already in Linear's case (parse_issue_refs normalizes the
    # refs), so plain membership is enough.
    matched = nodes + archived
    found = {n["identifier"].upper() for n in matched} | {
        n["id"].lower() for n in matched
    }
    return nodes, archived, [r for r in identifiers + uuids if r not in found]


def collect_aged(key, args):
    """Age-threshold sweep: every terminal state, older than the cutoff."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.older_than)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    candidates = []
    for state_type, ts_field in terminal_passes():
        for issue in find(key, args.team, args.project, state_type, ts_field, cutoff):
            issue["_when"] = (issue.get(ts_field) or "")[:10]
            candidates.append(issue)

    scope = f"team={args.team}" + (f", project={args.project}" if args.project else "")
    print(f"Cutoff: {cutoff}  ({scope}, Done + Canceled + Duplicate)\n")
    return candidates


def collect_named(key, args):
    """Explicit `--issues` set: no cutoff, but still terminal-state only.

    Age is the caller's business here — they named the issues. Terminal state is
    not: archiving an issue that is still open hides live work, so a non-terminal
    ref is reported and skipped rather than archived.
    """
    for flag in (
        "--project" if args.project else "",
        "--older-than" if args.older_than else "",
    ):
        if flag:
            print(f"Note: {flag} is ignored when --issues names specific issues.")
    identifiers, uuids = parse_issue_refs(args.issues)

    # Both lookups ask for one page. Rejecting an oversized list beats paging it:
    # the overflow would otherwise come back as "not found" and quietly go
    # unarchived, which reads identical to a clean run.
    if len(identifiers) + len(uuids) > PAGE:
        sys.exit(
            f"--issues takes at most {PAGE} refs at once "
            f"(got {len(identifiers) + len(uuids)}); split the list, or use "
            "--older-than for a bulk sweep."
        )
    nodes, archived, missing = find_by_ref(key, args.team, identifiers, uuids)

    candidates, open_refs = [], []
    for node in nodes:
        if node["state"]["type"] not in TERMINAL_TYPES:
            open_refs.append(f"{node['identifier']} ({node['state']['type']})")
            continue
        node["_when"] = (node.get("completedAt") or node.get("canceledAt") or "")[:10]
        candidates.append(node)

    print(f"Named issues (team={args.team}, no age threshold)\n")
    if archived:
        print(
            "Already archived (nothing to do): "
            + ", ".join(n["identifier"] for n in archived)
        )
    if missing:
        print(f"Not found on this team: {', '.join(missing)}")
    if open_refs:
        print(f"Skipped — not in a terminal state: {', '.join(open_refs)}")
    if archived or missing or open_refs:
        print()
    return candidates


def main():
    ap = argparse.ArgumentParser(
        description="Archive terminal-state Linear issues — by age, or by name."
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
        "--issues",
        action="append",
        default=[],
        metavar="REFS",
        help="Archive these issues by identifier (PRE-12) or UUID, regardless of "
        "age. Comma-separated and/or repeated. Ignores --older-than/--project.",
    )
    ap.add_argument(
        "--project", default=None, help="Optional project UUID to scope to."
    )
    ap.add_argument(
        "--apply", action="store_true", help="Archive. Without it, DRY RUN."
    )
    args = ap.parse_args()

    if not args.issues and (not args.older_than or args.older_than <= 0):
        sys.exit(
            "Refusing to run without an age threshold. Pass --older-than <N> (or set "
            "$ARCHIVE_AFTER), or name the issues to archive with --issues <refs>."
        )

    key = get_key()
    candidates = collect_named(key, args) if args.issues else collect_aged(key, args)

    if not candidates:
        print("Nothing to archive.")
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
