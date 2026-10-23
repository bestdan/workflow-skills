"""GitHub reads for the fleet view: issues, design docs, the review queue.

The fleet view (dev_docs/designs/2026-10-23-fleet-view.md, "Sources") needs
more from the configured owners than github_gh.py's PR rows. This module adds
it on the same `gh` runner seam, and reuses that module's search pagination,
error mapping and CI reduction rather than copying them.

Rows land in the v2 generic `issues` table as `source = 'github'`, with
`kind = pr | issue | design`, keyed by the item's URL. The Jira sync only
touches `source = 'jira'` rows, and `write()` here only `source = 'github'`
ones, so the two never overwrite each other.

THE REVIEW QUEUE, which the cap-of-5 gauge reads, holds a PR only when all
four rules hold:

1. Open, not a draft and not approved, and waiting on the owner: authored by
   me (agents open PRs as the owner's account) or requesting my review. The
   search asks GitHub for exactly that (`draft:false -review:approved`), and
   the draft and approved filters run again on the returned nodes so an index
   lag cannot let one through.
2. CI is `success`, by github_gh's rollup reduction. Pending, failed, no
   checks, and a failed lookup all miss.
3. Co-reviewed at its current head: a PR comment carries
   `<!-- co-review-stamp v1 head=<40-hex sha> -->` and the sha equals the PR's
   `headRefOid`. With several stamps the newest comment's wins, so a stamp for
   an older head is superseded rather than counted.
4. Milestone approved: an issue the PR closes (`closingIssuesReferences`) is in
   a milestone whose description has a line reading `approved: yes` (case
   ignored, line trimmed). Descriptions are cached per repo for one run.

The PRs passing rule 1 alone are AWAITING. Every one is still a `kind = pr`
row, with `status_name` set to `queue` or `awaiting`, `queue_misses` naming
the rules it failed, and `queue_counts()` giving both totals. Rules 2 and 4
are checked for every awaiting PR. Rule 3 costs one call per PR, so it runs
only for those that passed the others (`stamped` is None when skipped).

Run `python github_fleet.py review-queue <owner>...` (under uv, Python 3.11+)
to print both counts and the searches behind the awaiting set.
"""

import base64
import binascii
import json
import re
import sqlite3
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import github_gh
from github_gh import DEFAULT_TIMEOUT, GhError, Runner, run

SOURCE = "github"

# The label /promote-tasks writes when automation must not take an issue; see
# commands/handlers/assets/labels.yml (`auto: [eligible, human-review-needed]`).
HUMAN_LABEL = "auto:human-review-needed"

DESIGNS_DIR = "dev_docs/designs"

# Design files read per repo. Dated names sort oldest first, so the newest
# MAX_DESIGN_FILES are read; a repo with more is truncated and reported.
MAX_DESIGN_FILES = 40

ISSUE_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        url number title createdAt updatedAt
        repository { nameWithOwner }
        labels(first: 20) { nodes { name } }
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
        repository { nameWithOwner }
        closingIssuesReferences(first: 10) {
          nodes { number repository { nameWithOwner } milestone { number } }
        }
      }
    }
  }
}
"""

COMMENTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      comments(last: 100) { nodes { body createdAt } }
    }
  }
}
"""

# What "waits on the owner" means; each is one search per owner.
REVIEW_ROLES = (("author:@me", "author"), ("review-requested:@me", "reviewer"))


def review_searches(owner: str) -> list[str]:
    """The GitHub searches whose union is one owner's awaiting set (rule 1)."""
    scope = github_gh.owner_qualifier(owner)
    return [
        f"is:pr is:open draft:false -review:approved {who} {scope}"
        for who, _ in REVIEW_ROLES
    ]


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


def issues_needing_human(
    owners: Iterable[str], runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> list[dict]:
    """Open issues labelled auto:human-review-needed, one search per owner."""
    rows: dict[str, dict] = {}
    for owner in owners:
        query = (
            f'is:issue is:open label:"{HUMAN_LABEL}" {github_gh.owner_qualifier(owner)}'
        )
        for node in github_gh.search_prs(query, runner, timeout, ISSUE_QUERY):
            labels = [n["name"] for n in (node.get("labels") or {}).get("nodes", [])]
            rows.setdefault(
                node["url"],
                {
                    "kind": "issue",
                    "key": node["url"],
                    "url": node["url"],
                    "repo": node["repository"]["nameWithOwner"],
                    "number": node["number"],
                    "title": node["title"],
                    "labels": labels,
                    "created": node.get("createdAt"),
                    "updated": node.get("updatedAt"),
                },
            )
    return list(rows.values())


# --- review queue ------------------------------------------------------------

_STAMP = re.compile(r"<!--\s*co-review-stamp v1 head=([0-9a-fA-F]{40})\s*-->")
_APPROVED = re.compile(r"^approved:\s*yes$", re.IGNORECASE)


def in_review_queue(node: dict) -> bool:
    """Not a draft and not approved: the client-side half of rule 1."""
    return not node.get("isDraft") and node.get("reviewDecision") != "APPROVED"


def stamp_head(comments: Iterable[dict]) -> "str | None":
    """The head sha of the newest co-review stamp in `comments`, lower-cased.

    Comments are ordered by `createdAt`; within one body the last marker wins.
    """
    head = None
    for comment in sorted(comments, key=lambda c: c.get("createdAt") or ""):
        found = _STAMP.findall(comment.get("body") or "")
        if found:
            head = found[-1].lower()
    return head


def milestone_approved(description: "str | None") -> bool:
    """True when a line of the description, trimmed, is `approved: yes`."""
    lines = (description or "").replace("\r\n", "\n").split("\n")
    return any(_APPROVED.match(line.strip()) for line in lines)


class MilestoneCache:
    """Milestone descriptions, fetched once per (repo, number) for one run."""

    def __init__(self, runner: Runner, timeout: float) -> None:
        self.runner = runner
        self.timeout = timeout
        self.by_repo: dict[str, dict[int, str]] = {}

    def description(self, repo: str, number: int) -> str:
        cache = self.by_repo.setdefault(repo, {})
        if number not in cache:
            data = github_gh._call(
                ["gh", "api", f"repos/{repo}/milestones/{int(number)}"],
                self.runner,
                self.timeout,
            )
            cache[number] = (data or {}).get("description") or ""
        return cache[number]


def _closes_approved(node: dict, milestones: MilestoneCache) -> bool:
    refs = (node.get("closingIssuesReferences") or {}).get("nodes") or []
    for issue in refs:
        milestone = (issue or {}).get("milestone")
        if not milestone:
            continue
        repo = issue["repository"]["nameWithOwner"]
        if milestone_approved(milestones.description(repo, milestone["number"])):
            return True
    return False


def pr_comments(
    repo: str, number: int, runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> list[dict]:
    """A PR's newest 100 conversation comments, `body` and `createdAt`."""
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
        return data["data"]["repository"]["pullRequest"]["comments"]["nodes"]
    except (KeyError, TypeError):
        raise GhError(f"unexpected comments response for {repo}#{number}") from None


def review_queue(
    owners: Iterable[str], runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> "tuple[list[dict], list[str]]":
    """Every awaiting PR (rule 1), each marked `in_queue` by rules 2-4.

    Also returns the URLs whose CI lookup failed. Such a PR keeps a None
    `ci_state` and misses rule 2; one flaky `pr view` does not fail the run.
    """
    nodes: dict[str, tuple[dict, str]] = {}
    for owner in owners:
        for (_, role), query in zip(REVIEW_ROLES, review_searches(owner)):
            for node in github_gh.search_prs(query, runner, timeout, REVIEW_QUERY):
                if in_review_queue(node):
                    nodes.setdefault(node["url"], (node, role))
    milestones = MilestoneCache(runner, timeout)
    rows: list[dict] = []
    failed: list[str] = []
    for url, (node, role) in nodes.items():
        repo = node["repository"]["nameWithOwner"]
        ci: "str | None"
        try:
            ci = github_gh.ci_state(url, runner, timeout)
        except github_gh.AuthError:
            raise
        except GhError:
            ci = None
            failed.append(url)
        misses = []
        if ci != "success":
            misses.append("ci")
        approved = _closes_approved(node, milestones)
        if not approved:
            misses.append("milestone")
        stamped = None
        if not misses:
            head = stamp_head(pr_comments(repo, node["number"], runner, timeout))
            current = (node.get("headRefOid") or "").lower()
            stamped = head is not None and head == current
            if not stamped:
                misses.append("co_review")
        rows.append(
            {
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
                "brief": decision_brief(node.get("body")),
                "ci_state": ci,
                "milestone_approved": approved,
                "stamped": stamped,
                "in_queue": not misses,
                "queue_misses": misses,
                "created": node.get("createdAt"),
                "updated": node.get("updatedAt"),
            }
        )
    return rows, failed


def queue_counts(rows: Iterable[dict]) -> dict[str, int]:
    """`queue` (the gauge: all four rules) and `awaiting` (rule 1 only)."""
    prs = [r for r in rows if r["kind"] == "pr"]
    return {"queue": sum(1 for r in prs if r["in_queue"]), "awaiting": len(prs)}


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


def proposed_designs(
    repos: Iterable[str],
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
    max_files: int = MAX_DESIGN_FILES,
) -> "tuple[list[dict], list[str]]":
    """Design docs at `status: proposed`, and the repos whose list was capped.

    Reads `dev_docs/designs/*.md` on each repo's default branch through the
    contents API, at most `max_files` files per repo. A repo without the
    directory contributes nothing. Age is the front matter's `created`, else
    the date in a dated file name, else unknown.
    """
    rows: list[dict] = []
    truncated: list[str] = []
    for repo in repos:
        if not _REPO.match(repo):
            raise ValueError(f"not an owner/name repo: {repo!r}")
        listing = _contents(repo, DESIGNS_DIR, runner, timeout)
        if listing is None:
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
                }
            )
    return rows, truncated


# --- sync --------------------------------------------------------------------


def fetch(
    config: dict[str, Any], runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    """Every github row for the fleet view. Reads GitHub; writes nothing."""
    owners = list(config["github"]["orgs"])
    queue, failed = review_queue(owners, runner, timeout)
    designs, truncated = proposed_designs(
        config["github"].get("design_repos", []), runner, timeout
    )
    rows = queue + issues_needing_human(owners, runner, timeout) + designs
    return {
        "rows": rows,
        "failed": failed,
        "truncated": truncated,
        "counts": queue_counts(rows),
    }


def _status(row: dict) -> "str | None":
    if row["kind"] == "pr":
        return "queue" if row["in_queue"] else "awaiting"
    return row.get("status")


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
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "review-queue":
        print("usage: github_fleet.py review-queue <owner>...", file=sys.stderr)
        return 2
    rows, failed = review_queue(argv[1:])
    counts = queue_counts(rows)
    print(f"review queue: {counts['queue']}; awaiting: {counts['awaiting']}")
    print("awaiting is GitHub's own count for the union of:")
    for owner in argv[1:]:
        for query in review_searches(owner):
            print(f"  {query}")
    for row in rows:
        mark = "queue" if row["in_queue"] else "miss " + ",".join(row["queue_misses"])
        print(f"  {row['url']}  [{mark}]")
    for url in failed:
        print(f"  CI lookup failed: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
