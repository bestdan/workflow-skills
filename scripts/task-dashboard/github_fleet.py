"""GitHub reads for the fleet view: issues, design docs, the review queue.

The fleet view (dev_docs/designs/2026-10-23-fleet-view.md, "Sources") needs
more from the configured owners than github_gh.py's PR rows. This module adds
it on the same `gh` runner seam, and reuses that module's search pagination,
error mapping and CI reduction rather than copying them.

Rows land in the v2 generic `issues` table as `source = 'github'`, with
`kind = pr | issue | design | closed_pr`, keyed by the item's URL. A
`closed_pr` is one of my PRs closed in the last CLOSED_WINDOW_DAYS days, kept
apart from `pr` so nothing that counts open PRs sees it. The Jira sync only
touches `source = 'jira'` rows, and `write()` here only `source = 'github'`
ones, so the two never overwrite each other.

ISSUES are every open issue of the configured owners. The ones labelled
`auto:human-review-needed` (needs_human()) are fetched in full by a search of
their own, so Needs you and Stale, which read only those, see exactly that set
whatever the cap. The rest come from a second search, at most MAX_OPEN_ISSUES
per owner; an owner with more is reported in `issues_truncated`. The Issues
tab (fleet_tabs.py) reads them all.

THE REVIEW QUEUE, which the cap-of-5 gauge reads, holds a PR only when all
four rules hold:

1. Open, not a draft and not approved, and waiting on the owner: authored by
   me (agents open PRs as the owner's account) or requesting my review. The
   search asks GitHub for exactly that (`draft:false -review:approved`), and
   the draft and approved filters run again on the returned nodes so an index
   lag cannot let one through.
2. CI is `success`, by github_gh's rollup reduction. Pending, failed, no
   checks, and a failed lookup all miss.
3. Co-reviewed at its current head: a PR comment written by the viewer (the
   account `gh` is logged in as) carries
   `<!-- co-review-stamp v1 head=<40-hex sha> -->` and the sha equals the PR's
   `headRefOid`. Stamps in anyone else's comments are ignored, so another
   commenter cannot forge one. With several stamps the newest comment's wins,
   so a stamp for an older head is superseded rather than counted.
4. Milestone approved, when it can apply: if an issue the PR closes
   (`closingIssuesReferences`) is in a milestone, one of those milestones must
   have a description line reading `approved: yes` (case ignored, line
   trimmed). A PR that closes no issue in a milestone skips this rule, since
   most of the owner's repos use none. Descriptions are cached per repo for
   one run, and a failed lookup counts as a miss.

The PRs passing rule 1 alone are AWAITING. Every one is still a `kind = pr`
row, with `status_name` set to `queue` or `awaiting`, `queue_misses` naming
the rules it failed, and `queue_counts()` giving the totals. Rule 2 is checked
for every awaiting PR, and rule 4 only when a closing issue is in a milestone;
`milestone_approved` is None when rule 4 was skipped, so a skip is told apart
from a pass. Rule 3 costs one call per PR, so it runs only for those that
passed the others (`stamped` is None when skipped).

PRS FROM OTHERS are the open, non-draft PRs someone else opened on the owners'
repos without requesting my review: a third search, `-author:@me`, role
`others`, deduplicated by URL after the two above, so a review-requested PR
keeps its `reviewer` role. Bot authors are included: a dependency or version
bump waits on the owner too, and is the quickest merge. Every row records
`author` and `author_is_bot` (is_bot()). Rules 2-4 are for the owner's agent
PRs and are not applied: an `others` row has its CI state for display,
`in_queue` false, no `queue_misses`, and `status_name` `others`. The gauge
never counts one; `queue_counts()` gives them as `others`.

Run `python github_fleet.py review-queue <owner>...` (under uv, Python 3.11+)
to print the counts and the searches behind them, and
`python github_fleet.py issues <owner>...` to print how many open issues the
widened fetch mirrors and which owners the cap cut short.
"""

import base64
import binascii
import json
import re
import sqlite3
import sys
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

import github_gh
from github_gh import DEFAULT_TIMEOUT, GhError, Runner, run

SOURCE = "github"

# The `sync_runs.kind` of the sync step that runs fetch() and write()
# (sync.run_fleet). gauges.review_queue reads it to tell "never synced" from
# "synced, nothing waiting".
RUN_KIND = "github_fleet"

# The label /promote-tasks writes when automation must not take an issue; see
# commands/handlers/assets/labels.yml (`auto: [eligible, human-review-needed]`).
HUMAN_LABEL = "auto:human-review-needed"

DESIGNS_DIR = "dev_docs/designs"

# Design files read per repo. Dated names sort oldest first, so the newest
# MAX_DESIGN_FILES are read; a repo with more is truncated and reported.
MAX_DESIGN_FILES = 40

# Open issues mirrored per owner beyond the human-review set. GitHub's search
# stops at 1000 results, so a cap under it is always seen when it bites.
MAX_OPEN_ISSUES = 900

ISSUE_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        url number title createdAt updatedAt
        repository { nameWithOwner }
        labels(first: 20) { nodes { name } }
        milestone { number title }
      }
    }
  }
}
"""

REVIEW_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        url number title body headRefName headRefOid isDraft reviewDecision
        createdAt updatedAt
        author { login __typename }
        repository { nameWithOwner }
        closingIssuesReferences(first: 10) {
          nodes { number repository { nameWithOwner } milestone { number } }
        }
      }
    }
  }
}
"""

CLOSED_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        url number title state headRefName createdAt closedAt mergedAt
        repository { nameWithOwner }
        closingIssuesReferences(first: 10) {
          nodes { number state repository { nameWithOwner } }
        }
      }
    }
  }
}
"""

# How far back the closed-PR search reaches, which bounds what Stale can find
# merged. Needs you's Finished group also covers this many days when no visit
# is recorded.
CLOSED_WINDOW_DAYS = 14

COMMENTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      comments(last: 100) { nodes { body createdAt author { login } } }
    }
  }
}
"""

# What "waits on the owner" means; each is one search per owner. Every search
# for open items carries `archived:false`: archiving a repo leaves its PRs and
# issues open, so without it they would keep showing as work.
REVIEW_ROLES = (("author:@me", "author"), ("review-requested:@me", "reviewer"))


def review_searches(owner: str) -> list[str]:
    """The GitHub searches whose union is one owner's awaiting set (rule 1)."""
    scope = github_gh.owner_qualifier(owner)
    return [
        f"is:pr is:open archived:false draft:false -review:approved {who} {scope}"
        for who, _ in REVIEW_ROLES
    ]


def others_search(owner: str) -> str:
    """The search for PRs others opened on one owner's repos."""
    return (
        "is:pr is:open archived:false draft:false -author:@me"
        f" {github_gh.owner_qualifier(owner)}"
    )


# Bot accounts that open PRs here, named in case a node does not mark them.
BOT_LOGINS = {"github-actions", "dependabot", "copilot-swe-agent"}


def is_bot(author: "dict | None") -> bool:
    """A bot author: a `Bot` node, an `app/` or `[bot]` login, or BOT_LOGINS."""
    author = author or {}
    login = str(author.get("login") or "")
    return (
        author.get("__typename") == "Bot"
        or login.startswith("app/")
        or login.endswith("[bot]")
        or login in BOT_LOGINS
    )


# --- decision brief ----------------------------------------------------------

_BRIEF_HEADING = re.compile(r"^##\s+decision brief\s*#*\s*$", re.IGNORECASE)
_ANY_HEADING = re.compile(r"^#{1,6}\s")
BRIEF_MAX_LINES = 3


def decision_brief(pr_body: "str | None") -> "str | None":
    """The `## Decision brief` section of a PR body, or None.

    The section runs to the next heading. Blank lines are dropped and at most
    three lines are kept: what changed, the one thing to decide, and what a
    "no" would invalidate. A heading with nothing under it is None, so the card
    falls back to title, age and CI state as it does with no section at all.
    """
    if not pr_body:
        return None
    lines = pr_body.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if _BRIEF_HEADING.match(line.strip()):
            break
    else:
        return None
    kept: list[str] = []
    for line in lines[i + 1 :]:
        if _ANY_HEADING.match(line):
            break
        if line.strip():
            kept.append(line.rstrip())
        if len(kept) == BRIEF_MAX_LINES:
            break
    return "\n".join(kept) or None


# --- issues ------------------------------------------------------------------


def needs_human(row: dict) -> bool:
    """An `issue` row labelled auto:human-review-needed: Needs you's set."""
    return HUMAN_LABEL in (row.get("labels") or [])


def _issue_row(node: dict) -> dict:
    labels = [n["name"] for n in (node.get("labels") or {}).get("nodes", [])]
    return {
        "kind": "issue",
        "key": node["url"],
        "url": node["url"],
        "repo": node["repository"]["nameWithOwner"],
        "number": node["number"],
        "title": node["title"],
        "labels": labels,
        "milestone": _milestone(node.get("milestone")),
        "created": node.get("createdAt"),
        "updated": node.get("updatedAt"),
    }


def _milestone(node: "dict | None") -> "dict | None":
    """An issue's milestone as {number, title}, or None."""
    if not node or node.get("number") is None:
        return None
    return {"number": node["number"], "title": node.get("title")}


def issues_needing_human(
    owners: Iterable[str], runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> list[dict]:
    """Open issues labelled auto:human-review-needed, one search per owner."""
    rows: dict[str, dict] = {}
    for owner in owners:
        query = (
            f'is:issue is:open archived:false label:"{HUMAN_LABEL}"'
            f" {github_gh.owner_qualifier(owner)}"
        )
        for node in github_gh.search_prs(query, runner, timeout, ISSUE_QUERY):
            rows.setdefault(node["url"], _issue_row(node))
    return list(rows.values())


def open_issues(
    owners: Iterable[str],
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
    cap: int = MAX_OPEN_ISSUES,
) -> "tuple[list[dict], list[str]]":
    """Every open issue of the owners, and the owners the cap cut short.

    The human-review set comes first and in full (issues_needing_human); then
    one search per owner adds its other open issues, at most `cap` of them.
    """
    owners = list(owners)
    rows = {r["url"]: r for r in issues_needing_human(owners, runner, timeout)}
    truncated: list[str] = []
    for owner in owners:
        query = (
            f'is:issue is:open archived:false -label:"{HUMAN_LABEL}"'
            f" {github_gh.owner_qualifier(owner)}"
        )
        nodes, more = github_gh.search_nodes(
            query, runner, timeout, ISSUE_QUERY, limit=cap
        )
        if more:
            truncated.append(owner)
        for node in nodes:
            rows.setdefault(node["url"], _issue_row(node))
    return list(rows.values()), truncated


# --- recently closed PRs -----------------------------------------------------


def recently_closed(
    owners: Iterable[str],
    now: datetime,
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
    days: int = CLOSED_WINDOW_DAYS,
) -> list[dict]:
    """My PRs closed (merged or not) in the last `days` days, one search per owner.

    These are `kind = closed_pr` rows, not `pr`, so the review queue and every
    count of open PRs never sees them. Only Stale reads them: it matches
    `head_ref` to a landed worktree, and a handoff's "when #N merges" to a
    merged PR.
    """
    since = (now - timedelta(days=days)).astimezone(timezone.utc).date().isoformat()
    rows: dict[str, dict] = {}
    for owner in owners:
        # No `archived:false` here: a PR that merged before its repo was
        # archived is still evidence that its worktree can go.
        query = (
            f"is:pr is:closed author:@me closed:>={since}"
            f" {github_gh.owner_qualifier(owner)}"
        )
        for node in github_gh.search_prs(query, runner, timeout, CLOSED_QUERY):
            if not node.get("closedAt"):
                continue
            rows.setdefault(node["url"], closed_row(node))
    return list(rows.values())


def closed_row(node: dict) -> dict:
    """A `closed_pr` row from a closed PR node (CLOSED_QUERY's fields)."""
    return {
        "kind": "closed_pr",
        "key": node["url"],
        "url": node["url"],
        "repo": node["repository"]["nameWithOwner"],
        "number": node["number"],
        "title": node["title"],
        "head_ref": node.get("headRefName"),
        "merged": bool(node.get("mergedAt")),
        "status": "merged" if node.get("mergedAt") else "closed",
        "closed_at": node.get("closedAt"),
        "merged_at": node.get("mergedAt"),
        "created": node.get("createdAt"),
        "updated": node.get("closedAt"),
        "closes": closing_issues(node),
    }


def closing_issues(node: dict) -> list[dict]:
    """The issues a PR closes, each {repo, number, state}: what Now's
    close-out checklist ticks "task closed" from (close_out.py)."""
    out = []
    for issue in (node.get("closingIssuesReferences") or {}).get("nodes") or []:
        if not isinstance(issue, dict) or type(issue.get("number")) is not int:
            continue
        out.append(
            {
                "repo": (issue.get("repository") or {}).get("nameWithOwner"),
                "number": issue["number"],
                "state": issue.get("state"),
            }
        )
    return out


# --- review queue ------------------------------------------------------------

_STAMP = re.compile(r"<!--\s*co-review-stamp v1 head=([0-9a-fA-F]{40})\s*-->")
_APPROVED = re.compile(r"^approved:\s*yes$", re.IGNORECASE)


def in_review_queue(node: dict) -> bool:
    """Not a draft and not approved: the client-side half of rule 1."""
    return not node.get("isDraft") and node.get("reviewDecision") != "APPROVED"


def stamp_head(comments: Iterable[dict], author: str) -> "str | None":
    """The head sha of the newest co-review stamp `author` wrote, lower-cased.

    A comment by anyone else is ignored, so a stamp cannot be forged by
    another commenter. Comments are ordered by `createdAt`; within one body
    the last marker wins.
    """
    head = None
    for comment in sorted(comments, key=lambda c: c.get("createdAt") or ""):
        if ((comment.get("author") or {}).get("login") or "") != author:
            continue
        found = _STAMP.findall(comment.get("body") or "")
        if found:
            head = found[-1].lower()
    return head


def milestone_approved(description: "str | None") -> bool:
    """True when a line of the description, trimmed, is `approved: yes`."""
    lines = (description or "").replace("\r\n", "\n").split("\n")
    return any(_APPROVED.match(line.strip()) for line in lines)


class MilestoneCache:
    """Milestone descriptions, fetched once per (repo, number) for one run.

    A non-auth failure is remembered too and raised again without calling
    `gh`, so a 502 costs one call per milestone, not one per PR in it. An
    AuthError is never cached.
    """

    def __init__(self, runner: Runner, timeout: float) -> None:
        self.runner = runner
        self.timeout = timeout
        self.by_repo: dict[str, dict[int, str]] = {}
        self.failed: dict[tuple[str, int], GhError] = {}

    def description(self, repo: str, number: int) -> str:
        cache = self.by_repo.setdefault(repo, {})
        if (repo, number) in self.failed:
            raise self.failed[(repo, number)]
        if number not in cache:
            try:
                data = github_gh._call(
                    ["gh", "api", f"repos/{repo}/milestones/{int(number)}"],
                    self.runner,
                    self.timeout,
                )
            except github_gh.AuthError:
                raise
            except GhError as exc:
                self.failed[(repo, number)] = exc
                raise
            cache[number] = (data or {}).get("description") or ""
        return cache[number]


def _closes_approved(node: dict, milestones: MilestoneCache) -> "bool | None":
    """Rule 4: whether a milestone of an issue the PR closes is approved.

    None when no closing issue is in a milestone, so the rule does not apply.
    """
    refs = (node.get("closingIssuesReferences") or {}).get("nodes") or []
    milestoned = [i for i in refs if (i or {}).get("milestone")]
    if not milestoned:
        return None
    for issue in milestoned:
        repo = issue["repository"]["nameWithOwner"]
        number = issue["milestone"]["number"]
        if milestone_approved(milestones.description(repo, number)):
            return True
    return False


def closes(node: dict) -> list[dict]:
    """The issues a PR closes, each {repo, number, milestone}: the milestone's
    number, or None. Workstreams (workstreams.py) reads it."""
    out = []
    for issue in (node.get("closingIssuesReferences") or {}).get("nodes") or []:
        if not issue or issue.get("number") is None:
            continue
        ms = issue.get("milestone") or {}
        out.append(
            {
                "repo": issue["repository"]["nameWithOwner"],
                "number": issue["number"],
                "milestone": ms.get("number"),
            }
        )
    return out


def pr_comments(
    repo: str, number: int, runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> "tuple[str, list[dict]]":
    """The viewer's login and a PR's newest 100 conversation comments.

    Each comment carries `body`, `createdAt` and `author.login`.
    """
    owner, name = repo.split("/", 1)
    data = github_gh._call(
        [
            "gh", "api", "graphql",
            "-f", f"query={COMMENTS_QUERY}",
            "-f", f"owner={owner}",
            "-f", f"name={name}",
            "-F", f"number={int(number)}",
        ],
        runner,
        timeout,
    )  # fmt: skip
    try:
        viewer = data["data"]["viewer"]["login"]
        nodes = data["data"]["repository"]["pullRequest"]["comments"]["nodes"]
        return viewer, nodes
    except (KeyError, TypeError):
        raise GhError(f"unexpected comments response for {repo}#{number}") from None


def queue_row(
    node: dict,
    role: str,
    ci: "str | None",
    milestones: Any,
    comments_of: Callable[[str, int], "tuple[str, list[dict]]"],
) -> "tuple[dict, bool]":
    """One awaiting PR's `kind = pr` row, with rules 2-4 applied to it.

    `node` has REVIEW_QUERY's fields and `ci` is its rollup state (None when
    that lookup failed). `milestones` answers description(repo, number) and
    `comments_of(repo, number)` gives the viewer and the PR's comments, so a
    caller that already holds them (the per-item refresh) makes no more calls.
    Also returns whether a milestone or comments lookup failed; that counts
    as a miss of its rule. An AuthError raises.
    """
    url = node["url"]
    repo = node["repository"]["nameWithOwner"]
    lookup_failed = False
    misses = []
    approved: "bool | None" = None
    stamped = None
    if role != "others" and ci != "success":
        misses.append("ci")
    if role != "others":
        try:
            approved = _closes_approved(node, milestones)
        except github_gh.AuthError:
            raise
        except GhError:
            approved = False
            lookup_failed = True
    if approved is False:
        misses.append("milestone")
    if role != "others" and not misses:
        try:
            viewer, comments = comments_of(repo, node["number"])
            head = stamp_head(comments, viewer)
            current = (node.get("headRefOid") or "").lower()
            stamped = head is not None and head == current
        except github_gh.AuthError:
            raise
        except GhError:
            stamped = False
            lookup_failed = True
        if not stamped:
            misses.append("co_review")
    row = {
        "kind": "pr",
        "key": url,
        "url": url,
        "repo": repo,
        "number": node["number"],
        "title": node["title"],
        "head_ref": node.get("headRefName"),
        "head_oid": node.get("headRefOid"),
        "review_decision": node.get("reviewDecision"),
        "role": role,
        "author": (node.get("author") or {}).get("login"),
        "author_is_bot": is_bot(node.get("author")),
        "brief": decision_brief(node.get("body")),
        "ci_state": ci,
        "milestone_approved": approved,
        "stamped": stamped,
        "in_queue": role != "others" and not misses,
        "queue_misses": misses,
        "closes": closes(node),
        "created": node.get("createdAt"),
        "updated": node.get("updatedAt"),
    }
    return row, lookup_failed


def review_queue(
    owners: Iterable[str], runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> "tuple[list[dict], list[str]]":
    """Every awaiting PR (rule 1), each marked `in_queue` by rules 2-4, then
    the PRs from others (role `others`), which those rules do not apply to.

    Rule 4 is skipped, with `milestone_approved` None, for a PR that closes no
    issue in a milestone.

    Also returns the URLs where a CI, milestone or comments lookup failed.
    A failed lookup counts as a miss of its rule (a None `ci_state`, a
    `milestone` miss, a `co_review` miss), so one flaky call does not fail
    the run. An AuthError still raises.
    """
    owners = list(owners)
    nodes: dict[str, tuple[dict, str]] = {}
    for owner in owners:
        for (_, role), query in zip(REVIEW_ROLES, review_searches(owner)):
            for node in github_gh.search_prs(query, runner, timeout, REVIEW_QUERY):
                if in_review_queue(node):
                    nodes.setdefault(node["url"], (node, role))
    for owner in owners:
        query = others_search(owner)
        for node in github_gh.search_prs(query, runner, timeout, REVIEW_QUERY):
            if not node.get("isDraft"):
                nodes.setdefault(node["url"], (node, "others"))
    milestones = MilestoneCache(runner, timeout)
    rows: list[dict] = []
    failed: list[str] = []

    def comments_of(repo: str, number: int) -> "tuple[str, list[dict]]":
        return pr_comments(repo, number, runner, timeout)

    for url, (node, role) in nodes.items():
        ci: "str | None"
        try:
            ci = github_gh.ci_state(url, runner, timeout)
        except github_gh.AuthError:
            raise
        except GhError:
            ci = None
            failed.append(url)
        row, lookup_failed = queue_row(node, role, ci, milestones, comments_of)
        if lookup_failed and url not in failed:
            failed.append(url)
        rows.append(row)
    return rows, failed


def queue_counts(rows: Iterable[dict]) -> dict[str, int]:
    """`queue` (the gauge: all four rules), `awaiting` (rule 1 only) and
    `others` (PRs from others)."""
    prs = [r for r in rows if r["kind"] == "pr"]
    others = sum(1 for r in prs if r.get("role") == "others")
    return {
        "queue": sum(1 for r in prs if r["in_queue"]),
        "awaiting": len(prs) - others,
        "others": others,
    }


# --- design docs -------------------------------------------------------------


def front_matter(text: str) -> "dict[str, str] | None":
    """Flat `key: value` pairs between a leading `---` and the next `---`.

    Only the front matter is parsed; a `status:` line in the body (a design
    that quotes one) is never read. None when the file has no front matter.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t", "-", "#")):
            out[key.strip()] = value.strip().strip("'\"")
    return None


_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")


def _contents(repo: str, path: str, runner: Runner, timeout: float) -> "Any | None":
    """The contents API on the default branch; None on a 404."""
    argv = ["gh", "api", f"repos/{repo}/contents/{path}"]
    rc, out, err = runner(argv, timeout)
    if rc != 0 and "HTTP 404" in err:
        return None
    # Any other outcome gets github_gh's error mapping and JSON parse.
    return github_gh._call(argv, lambda _argv, _timeout: (rc, out, err), timeout)


_REF_URL = re.compile(
    r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+)"
    r"/(?:issues|pull)/(\d+)"
)
_REF_SHORT = re.compile(
    r"(?<![\w/&#])(?:([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+))?#(\d+)\b"
)


def design_refs(text: str, repo: str) -> list[dict]:
    """The issues and PRs a design names, each {repo, number}: the URLs
    first, then the `owner/name#N` and bare `#N` refs, each in text order,
    without duplicates.

    A GitHub issue or PR URL, an `owner/name#N`, and a bare `#N`, which
    means `repo`. GitHub numbers issues and PRs in one sequence, so a ref
    names whichever of the two the number is.
    """
    found: dict[tuple[str, int], None] = {}
    for m in _REF_URL.finditer(text):
        found.setdefault((m[1], int(m[2])), None)
    for m in _REF_SHORT.finditer(_REF_URL.sub(" ", text)):
        found.setdefault((m[1] or repo, int(m[2])), None)
    return [{"repo": r, "number": n} for r, n in found]


def proposed_designs(
    repos: Iterable[str],
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
    max_files: int = MAX_DESIGN_FILES,
) -> "tuple[list[dict], list[str], list[str]]":
    """Proposed design docs, the repos whose list was capped, and the missing.

    Reads `dev_docs/designs/*.md` on each repo's default branch through the
    contents API, at most `max_files` files per repo. A repo whose listing is
    a 404 contributes nothing and is reported in `missing`: GitHub answers a
    repo with no designs directory and a mistyped repo name the same way.
    Age is the front matter's `created`, else the date in a dated file name,
    else unknown.
    """
    rows: list[dict] = []
    truncated: list[str] = []
    missing: list[str] = []
    for repo in repos:
        if not _REPO.match(repo):
            raise ValueError(f"not an owner/name repo: {repo!r}")
        listing = _contents(repo, DESIGNS_DIR, runner, timeout)
        if listing is None:
            missing.append(repo)
            continue
        if not isinstance(listing, list):
            raise GhError(f"{repo}/{DESIGNS_DIR} is not a directory")
        files = sorted(
            (
                e
                for e in listing
                if e.get("type") == "file" and e["name"].endswith(".md")
            ),
            key=lambda e: e["name"],
        )
        if len(files) > max_files:
            truncated.append(repo)
            files = files[-max_files:]
        for entry in files:
            blob = _contents(repo, entry["path"], runner, timeout)
            if not isinstance(blob, dict) or blob.get("encoding") != "base64":
                continue
            try:
                text = base64.b64decode(blob.get("content") or "").decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                continue
            meta = front_matter(text)
            if not meta or meta.get("status", "").lower() != "proposed":
                continue
            dated = _DATED.match(entry["name"])
            title = next(
                (ln[2:].strip() for ln in text.split("\n") if ln.startswith("# ")),
                entry["name"],
            )
            rows.append(
                {
                    "kind": "design",
                    "key": entry["html_url"],
                    "url": entry["html_url"],
                    "repo": repo,
                    "path": entry["path"],
                    "title": title,
                    "status": "proposed",
                    "created": meta.get("created")
                    or (dated.group(1) if dated else None),
                    "refs": design_refs(text, repo),
                }
            )
    return rows, truncated, missing


# --- milestones --------------------------------------------------------------

# One page of an owner's unarchived repos with their open milestones. A repo
# with more than 25 open milestones shows its first 25 only.
MILESTONES_QUERY = """
query($owner: String!, $after: String) {
  repositoryOwner(login: $owner) {
    repositories(first: 50, after: $after, isArchived: false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner
        milestones(first: 25, states: OPEN) {
          nodes {
            number title description dueOn url updatedAt
            open: issues(states: OPEN) { totalCount }
            closed: issues(states: CLOSED) { totalCount }
          }
        }
      }
    }
  }
}
"""


def milestone_row(repo: str, ms: dict) -> dict:
    """One open milestone (MILESTONES_QUERY's fields) as the Workstreams
    source reads it."""
    return {
        "repo": repo,
        "number": ms["number"],
        "title": ms.get("title") or f"milestone {ms['number']}",
        "url": ms.get("url"),
        "due": ms.get("dueOn"),
        "approved": milestone_approved(ms.get("description")),
        "open": (ms.get("open") or {}).get("totalCount") or 0,
        "closed": (ms.get("closed") or {}).get("totalCount") or 0,
        "updated": ms.get("updatedAt"),
    }


def open_milestones(
    owners: Iterable[str], runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> list[dict]:
    """The open milestones in the owners' unarchived repos (milestone_row),
    one paged query per owner."""
    out = []
    for owner in owners:
        github_gh.owner_qualifier(owner)  # validates the name
        after: "str | None" = None
        while True:
            argv = [
                "gh", "api", "graphql",
                "-f", f"query={MILESTONES_QUERY}",
                "-f", f"owner={owner}",
            ]  # fmt: skip
            if after:
                argv += ["-f", f"after={after}"]
            data = github_gh._call(argv, runner, timeout)
            try:
                repos = data["data"]["repositoryOwner"]["repositories"]
                page, nodes = repos["pageInfo"], repos["nodes"]
            except (KeyError, TypeError):
                raise GhError(f"unexpected milestones response for {owner}") from None
            for repo in nodes:
                for ms in (repo.get("milestones") or {}).get("nodes") or []:
                    out.append(milestone_row(repo["nameWithOwner"], ms))
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
    return out


# --- sync --------------------------------------------------------------------


def fetch(
    config: dict[str, Any],
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
    now: "datetime | None" = None,
) -> dict[str, Any]:
    """Every github row for the fleet view. Reads GitHub; writes nothing."""
    now = now or datetime.now(timezone.utc)
    owners = list(config["github"]["orgs"])
    queue, failed = review_queue(owners, runner, timeout)
    designs, truncated, missing = proposed_designs(
        config["github"].get("design_repos", []), runner, timeout
    )
    open_urls = {r["url"] for r in queue}
    closed = [
        r
        for r in recently_closed(owners, now, runner, timeout)
        if r["url"] not in open_urls  # an index lag; the open row wins
    ]
    issues, issues_truncated = open_issues(owners, runner, timeout)
    rows = queue + issues + designs + closed
    return {
        "rows": rows,
        "failed": failed,
        "truncated": truncated,
        "issues_truncated": issues_truncated,
        "missing": missing,
        "counts": queue_counts(rows),
    }


def _status(row: dict) -> "str | None":
    if row["kind"] == "pr":
        if row.get("role") == "others":
            return "others"
        return "queue" if row["in_queue"] else "awaiting"
    return row.get("status")


def insert(conn: sqlite3.Connection, rows: list[dict], stamp: str) -> None:
    """INSERT `rows` as `source = 'github'` rows, inside the caller's transaction."""
    conn.executemany(
        "INSERT INTO issues (source, kind, key, summary, status_name, labels,"
        " created, updated, raw_json, synced_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                SOURCE,
                row["kind"],
                row["key"],
                row["title"],
                _status(row),
                json.dumps(row["labels"]) if "labels" in row else None,
                row.get("created"),
                row.get("updated"),
                json.dumps(row, sort_keys=True),
                stamp,
            )
            for row in rows
        ],
    )


def write(conn: sqlite3.Connection, rows: list[dict], now: datetime) -> None:
    """Replace every `source = 'github'` row with `rows`, in one transaction.

    The fields with a generic column go there (`summary` the title, `labels`
    as JSON, `status_name` a PR's `queue` or `awaiting` or a design's status);
    the whole row is kept in `raw_json`, where the URL, CI state, brief and
    queue rule results live.
    """
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM issues WHERE source = ?", (SOURCE,))
        insert(conn, rows, stamp)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("review-queue", "issues"):
        print("usage: github_fleet.py review-queue|issues <owner>...", file=sys.stderr)
        return 2
    if argv[0] == "issues":
        issues, truncated = open_issues(argv[1:])
        human = sum(1 for r in issues if needs_human(r))
        print(f"open issues: {len(issues)}; {HUMAN_LABEL}: {human}")
        print(f"cap: {MAX_OPEN_ISSUES} per owner beyond {HUMAN_LABEL}")
        for owner in truncated:
            print(f"  truncated: {owner}")
        return 0
    rows, failed = review_queue(argv[1:])
    counts = queue_counts(rows)
    print(
        f"review queue: {counts['queue']}; awaiting: {counts['awaiting']};"
        f" from others: {counts['others']}"
    )
    print("awaiting is GitHub's own count for the union of:")
    for owner in argv[1:]:
        for query in review_searches(owner):
            print(f"  {query}")
    print("from others is, less the PRs already above:")
    for owner in argv[1:]:
        print(f"  {others_search(owner)}")
    for row in rows:
        if row["role"] == "others":
            bot = " (bot)" if row.get("author_is_bot") else ""
            mark = f"from {row.get('author')}{bot}"
        elif row["in_queue"]:
            mark = "queue"
        else:
            mark = "miss " + ",".join(row["queue_misses"])
        print(f"  {row['url']}  [{mark}]")
    for url in failed:
        print(f"  lookup failed: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
