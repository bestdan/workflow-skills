#!/usr/bin/env python3
"""Detect Linear issues that were closed without any work behind them.

This workspace's Linear/GitHub integration treats a bare issue id (``PRE-123``)
appearing *anywhere* in a merged PR's title or body as a closing reference. A PR
that merely mentions a sibling issue therefore sweeps that sibling to Done, with
no branch, no PR, and no code. The culprit is a repo-local merge workflow that
scrapes ids out of PR text, not Linear's own GitHub integration -- see
commands/handlers/linear-claim.md, "Why the integration is inert".

This script is the standalone backstop that detects those false closures and
optionally restores them. (See commands/handlers/linear-false-closures.md for
how it relates to the other Linear task commands.)

The test applied here:

    A completed issue must be OWNED by delivered work.

Ownership is established by any of: its head branch embeds the issue's
identifier (regex match, not equality on Linear's suggested ``branchName``: the
real branch is routinely a shortened form of it); the issue carries a link
attachment pointing at a merged PR; the PR title/body *closes* the issue with a
keyword (``closes PRE-123``); or the issue is a parent whose sub-issues are
themselves completed (a rollup shell carries no PR of its own -- its children
did the work). A completed issue that matches none of these -- a PR that merely
name-drops the id, and no completed children -- is a false closure.

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
a claude.ai/Claude Code cloud sandbox. The key is resolved by
commands/handlers/assets/_secret_resolve.py, which walks two independent
ladders: secret/pointer (`$LINEAR_API_KEY` -> `$LINEAR_API_KEY_REF` ->
unavailable) and resolver (`$LINEAR_API_KEY_RESOLVER` -> default `op`),
against an allow-list of resolver identifiers (`op`, `opx`). A failed resolve
never falls through to the next rung. See dev_docs/auth_key_access.md for the
full contract.

A configured `linear.api_key` / `linear.api_key_ref` / `linear.api_key_resolver`
reaches this
script only because the caller bridges them onto the environment -- see
linear-common.md's "Key resolution" step. This script reads no config.

Usage:
  python3 linear-false-closures.py --project <uuid> --repo owner/name
  python3 linear-false-closures.py --project <uuid> --repo owner/name --apply
  # only issues closed recently; restore just the ones you name:
  python3 linear-false-closures.py --project <uuid> --repo owner/name --since 48h
  python3 linear-false-closures.py --project <uuid> --repo owner/name --apply --only PRE-1,PRE-2
  # cloud routines, where `gh` cannot reach the GitHub API -- see load_prs_file():
  python3 linear-false-closures.py --project <uuid> --prs-file merged_prs.json

Each false closure is reported with the merged PR that most likely tripped it
(the one bare-mentioning the id, merged just before the completion instant), so
the flag is actionable without hand-tracing the PR history.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import NoReturn
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secret_resolve import SecretUnavailable, resolve_key  # noqa: E402
from _shape import ShapeError, expect  # noqa: E402

API = "https://api.linear.app/graphql"

# gte this and every completed issue matches -- the default when no --since is
# given, so the one query serves both "the whole history" and a recent window.
EPOCH = "1970-01-01T00:00:00.000Z"

COMPLETED_ISSUES = """
query($project: String!, $cursor: String, $since: DateTimeOrDuration!) {
  project(id: $project) {
    name
    issues(filter: { state: { type: { eq: "completed" } }, completedAt: { gte: $since } }, first: 100, after: $cursor) {
      nodes {
        id
        identifier
        title
        createdAt
        startedAt
        completedAt
        team { id }
        attachments(first: 250) { nodes { url } pageInfo { hasNextPage } }
        children(first: 50, includeArchived: true) { nodes { identifier state { type } } pageInfo { hasNextPage } }
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


def die(msg) -> NoReturn:
    sys.exit(f"linear-false-closures: {msg}")


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
    with urllib.request.urlopen(req) as r:
        payload = json.loads(r.read())
    # Guard the root BEFORE the membership test: `"errors" in None` and
    # `"errors" in 5` raise TypeError, so a scalar JSON body would reach neither
    # this check nor expect() below and would surface as the traceback this
    # whole seam exists to remove. gh-issue-rollups.py guards in the same order.
    if not isinstance(payload, dict):
        sys.exit(f"GraphQL response: expected an object, got {type(payload).__name__}")
    if "errors" in payload:
        sys.exit("GraphQL error: " + json.dumps(payload["errors"], indent=2))
    # A malformed response used to surface as a KeyError traceback here, and
    # then as a chain of KeyErrors at every caller that indexed into the result.
    # expect() makes it one sentence naming the field.
    try:
        return expect(payload, "data", dict, "GraphQL response")
    except ShapeError as exc:
        sys.exit(str(exc))


def to_since(s):
    """Normalize --since to a Linear DateTimeOrDuration.

    Accepts a friendly ``48h`` / ``2d`` shorthand (the common "closed recently"
    case), any ISO-8601 datetime, or a Linear duration (``-P2D``) passed through
    verbatim. None -> EPOCH, i.e. the whole completed history.
    """
    if not s:
        return EPOCH
    m = re.fullmatch(r"(\d+)\s*([hd])", s.strip(), re.I)
    if m:
        n, unit = m.group(1), m.group(2).lower()
        return f"-PT{n}H" if unit == "h" else f"-P{n}D"
    return s.strip()


def completed_issues(key, project, since):
    """Completed issues in the project (since `since`), paginated -- no 100 cap."""
    out, cursor = [], None
    while True:
        data = gql(
            key,
            COMPLETED_ISSUES,
            {"project": project, "cursor": cursor, "since": since},
        )
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
            ".[] | select(.merged_at != null) | {number: .number, headRefName: .head.ref, url: .html_url, title: .title, body: .body, mergedAt: .merged_at}",
            f"repos/{repo}/pulls?state=closed&per_page=100",
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        die(f"gh api pulls failed: {out.stderr.strip()}")
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def load_prs_file(path):
    """The --prs-file alternative to merged_prs(): a caller-supplied window.

    Where `gh` cannot reach the GitHub API (a Claude Code cloud routine, whose
    session proxy refuses every repo-scoped REST/GraphQL call), the agent
    fetches merged PRs itself via mcp__github__list_pull_requests and hands
    them to this script as JSON instead. The file is an object, not a bare
    list, because the list alone can't prove it's complete: `complete_since`
    is the caller's assertion that it contains *every* PR merged in this repo
    at or after that instant. main() uses it to refuse to classify any issue
    whose life started before the window opened -- see the coverage guard
    there. Entries with no mergedAt are dropped: only merged PRs establish
    ownership.

    Returns (complete_since, prs).
    """
    try:
        with open(path) as f:
            raw = f.read()
    except OSError as e:
        die(f"--prs-file: {e}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        die(f"--prs-file: invalid JSON: {e}")
    if not isinstance(payload, dict):
        die(f"--prs-file: expected an object, got {type(payload).__name__}")
    try:
        since = expect(payload, "complete_since", str, "--prs-file")
        prs_raw = expect(payload, "pull_requests", list, "--prs-file")
    except ShapeError as exc:
        die(str(exc))
    if not since.strip():
        die("--prs-file: complete_since: blank")
    since_parsed = _ts(since)
    if since_parsed is None:
        die(f"--prs-file: complete_since: unparseable timestamp {since!r}")
    if since_parsed.tzinfo is None or since_parsed.utcoffset() is None:
        die(
            f"--prs-file: complete_since: no timezone offset ({since!r}) -- "
            f"add one, e.g. {since!r} -> {since + 'Z'!r}"
        )
    # A non-dict entry must be fatal rather than dropped: silently shrinking
    # the ownership evidence would turn delivered work into a "false closure"
    # that --apply reopens.
    for i, pr in enumerate(prs_raw):
        if not isinstance(pr, dict):
            die(
                f"--prs-file: pull_requests[{i}]: expected an object, got {type(pr).__name__}"
            )
    # Only merged PRs establish ownership -- this is the one intentional
    # silent exclusion.
    prs = [(i, pr) for i, pr in enumerate(prs_raw) if pr.get("mergedAt")]
    # Every kept entry is consumed downstream (number is indexed unguarded;
    # url/headRefName/title/body establish ownership signals), so a malformed
    # one must be fatal rather than dropped: silently shrinking the ownership
    # evidence would turn delivered work into a "false closure" that --apply
    # reopens.
    for i, pr in prs:
        where = f"--prs-file: pull_requests[{i}]"
        try:
            expect(pr, "number", int, where)
            expect(pr, "url", str, where)
            expect(pr, "headRefName", str, where)
            expect(pr, "title", str, where)
            expect(pr, "body", str, where)
            expect(pr, "mergedAt", str, where)
        except ShapeError as exc:
            die(str(exc))
    return since, [pr for _, pr in prs]


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

    Four ownership signals: the branch it was built on, a link attachment the
    issue itself carries, a PR whose title/body closes the issue with a keyword
    (``closes PRE-123``), or -- for a parent rollup shell that carries no PR of
    its own -- sub-issues that are themselves completed. A bare mention of the
    id, with none of these, owns nothing; that is the over-close bug this
    catches. The keyword signal covers cloud runs whose branches lack the id;
    the sub-issue signal covers parents closed once their children delivered.
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
    kids = issue.get("children") or {}
    nodes = kids.get("nodes", [])
    if nodes and not kids.get("pageInfo", {}).get("hasNextPage"):
        # Only credit a parent whose children have all reached a terminal state
        # (completed/canceled) with at least one actually completed. If the child
        # list is truncated we can't prove that, so fall through and flag it --
        # re-closing a real rollup is cheap; clearing a bad one is not.
        types = {k["state"]["type"] for k in nodes}
        if types <= {"completed", "canceled"} and "completed" in types:
            done = ", ".join(
                k["identifier"] for k in nodes if k["state"]["type"] == "completed"
            )
            return f"sub-issues {done} (completed)"
    return None


def _ts(s):
    """Parse an ISO-8601 timestamp (with trailing Z) to a datetime, else None."""
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def closing_mention_pr(issue, prs):
    """The merged PR that most likely tripped this false closure, or None.

    The over-closing automation fires on a bare id mention in a merged PR and
    closes the issue seconds later. So among the merged PRs that bare-mention
    the id, the culprit is the one merged at or just before the completion
    instant. Reported only, to make the flag actionable -- never acted on.
    """
    ident = re.escape(issue["identifier"].lower())
    token = re.compile(rf"(?<!\d){ident}(?!\d)")
    hits = [
        pr
        for pr in prs
        if token.search(f"{pr.get('title') or ''}\n{pr.get('body') or ''}".lower())
    ]
    if not hits:
        return None
    done = _ts(issue.get("completedAt"))

    def rank(pr):
        merged = _ts(pr.get("mergedAt"))
        if done and merged:
            # merged-before-completion first, then nearest in time to it.
            return (0 if merged <= done else 1, abs((done - merged).total_seconds()))
        return (2, 0)

    return min(hits, key=rank)


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
    ap.add_argument(
        "--repo",
        help="owner/name of the GitHub repo whose merged PRs establish "
        "ownership. Exactly one of --repo / --prs-file is required.",
    )
    ap.add_argument(
        "--prs-file",
        help="Path to a JSON file with a pre-fetched merged-PR list, for "
        "hosts where `gh` cannot reach the GitHub API. Exactly one of "
        "--repo / --prs-file is required.",
    )
    ap.add_argument(
        "--since",
        help="Only issues completed since this: 48h / 2d shorthand, an ISO "
        "datetime, or a Linear duration (-P2D). Default: the whole history.",
    )
    ap.add_argument(
        "--only",
        help="Comma-separated issue ids to restore (must be among the detected "
        "false closures). Scopes --apply to exactly these; ignored in dry run.",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Restore false closures to Todo. Without it, DRY RUN.",
    )
    args = ap.parse_args()
    if bool(args.repo) == bool(args.prs_file):
        die("exactly one of --repo or --prs-file is required")

    key = get_key()
    project_name, issues = completed_issues(key, args.project, to_since(args.since))

    complete_since = None
    if args.repo:
        prs = merged_prs(args.repo)
    else:
        complete_since, prs = load_prs_file(args.prs_file)
    merged = {k for k in (pr_identity(pr["url"]) for pr in prs) if k}

    # --prs-file coverage guard: --repo paginates the whole closed-PR history
    # (see merged_prs()'s docstring), but a --prs-file list is inherently a
    # window. An issue can only be classified if that window provably covers
    # its whole life -- otherwise an owning PR could have merged before the
    # window opened, and we cannot prove it did not. The anchor is the issue's
    # createdAt: a PR cannot own an issue that predates it, so an owning PR is
    # only guaranteed captured if the window opened at or before the issue was
    # created. startedAt/completedAt would pass this guard vacuously for a
    # never-started issue (completedAt is inside the window by construction),
    # which is exactly the population this tool targets. Never applies to
    # --repo. An anchor that cannot be parsed is treated as predating the
    # window, since we cannot prove otherwise.
    since_ts = _ts(complete_since) if complete_since else None

    false_closures, legit, truncated, windowed = [], [], [], []
    for issue in issues:
        if since_ts is not None:
            anchor_ts = _ts(issue["createdAt"])
            if anchor_ts is None or anchor_ts < since_ts:
                windowed.append(issue)
                continue
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
        print(f"  skip  {issue['identifier']}  (>250 attachments — not classified)")
    for issue in windowed:
        print(
            f"  skip  {issue['identifier']}  (merged-PR window starts "
            f"{complete_since} — not classified)"
        )

    if not false_closures:
        print("\nno false closures.")
        return 0

    print(
        f"\nFALSE CLOSURES ({len(false_closures)}) — completed, but no merged PR owns them:"
    )
    for issue, _ in false_closures:
        never = " (never started)" if not issue["startedAt"] else ""
        culprit = closing_mention_pr(issue, prs)
        tag = ""
        if culprit:
            tag = (
                f"  — likely closed by #{culprit['number']} "
                f"(merged {culprit.get('mergedAt') or '?'}, mentions id)"
            )
        print(f"  BAD   {issue['identifier']}  {issue['title'][:60]}{never}{tag}")

    to_restore = false_closures
    if args.only:
        wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}
        detected = {i["identifier"].upper() for i, _ in false_closures}
        missing = wanted - detected
        if missing:
            die(f"--only ids not among detected false closures: {sorted(missing)}")
        to_restore = [
            (i, o) for i, o in false_closures if i["identifier"].upper() in wanted
        ]

    if not args.apply:
        scope = (
            f"the {len(to_restore)} named"
            if args.only
            else f"these {len(false_closures)}"
        )
        print(
            f"\nDRY RUN — nothing restored. Re-run with --apply to restore {scope} to Todo."
        )
        return 1

    print(f"\nRestoring {len(to_restore)}...")
    cache: dict = {}
    ok = fail = 0
    for issue, _ in to_restore:
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
