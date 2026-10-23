"""GitHub reads for the fleet view: issues, design docs, the review queue.

The fleet view (dev_docs/designs/2026-10-23-fleet-view.md, "Sources") needs
more from the configured owners than github_gh.py's PR rows. This module adds
it on the same `gh` runner seam, and reuses that module's search pagination,
error mapping and CI reduction rather than copying them.

Rows land in the v2 generic `issues` table as `source = 'github'`, with
`kind = pr | issue | design`, keyed by the item's URL. The Jira sync only
touches `source = 'jira'` rows, and `write()` here only `source = 'github'`
ones, so the two never overwrite each other.

THE REVIEW QUEUE is the open, non-draft PRs in the configured owners that are
not approved and wait on the owner: ones I authored (agents open PRs as the
owner's account) or ones requesting my review. The search asks GitHub for
exactly that (`draft:false -review:approved`), so its count can be checked
against GitHub's own PR search, and the same two filters run again on the
returned nodes so an index lag cannot let a draft or an approved PR through.

Run `python github_fleet.py review-queue <owner>...` (under uv, Python 3.11+)
to print the queue's length and the searches that produced it.
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
        url number title body headRefName isDraft reviewDecision
        createdAt updatedAt
        repository { nameWithOwner }
      }
    }
  }
}
"""

# What "waits on the owner" means; each is one search per owner.
REVIEW_ROLES = (("author:@me", "author"), ("review-requested:@me", "reviewer"))


def review_searches(owner: str) -> list[str]:
    """The GitHub searches whose union is one owner's review queue."""
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


def in_review_queue(node: dict) -> bool:
    """Not a draft and not approved: the client-side half of the queue filter."""
    return not node.get("isDraft") and node.get("reviewDecision") != "APPROVED"


def review_queue(
    owners: Iterable[str], runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> list[dict]:
    """The open PRs waiting on the owner, deduplicated by URL.

    `ci_state` is not filled here; `fetch()` adds it, one `gh pr view` per PR.
    """
    rows: dict[str, dict] = {}
    for owner in owners:
        for (_, role), query in zip(REVIEW_ROLES, review_searches(owner)):
            for node in github_gh.search_prs(query, runner, timeout, REVIEW_QUERY):
                if not in_review_queue(node) or node["url"] in rows:
                    continue
                rows[node["url"]] = {
                    "kind": "pr",
                    "key": node["url"],
                    "url": node["url"],
                    "repo": node["repository"]["nameWithOwner"],
                    "number": node["number"],
                    "title": node["title"],
                    "head_ref": node.get("headRefName"),
                    "review_decision": node.get("reviewDecision"),
                    "role": role,
                    "brief": decision_brief(node.get("body")),
                    "created": node.get("createdAt"),
                    "updated": node.get("updatedAt"),
                }
    return list(rows.values())


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
    """Every github row for the fleet view. Reads GitHub; writes nothing.

    A PR whose CI lookup fails keeps a None `ci_state` and is listed in
    `failed`, as in sync.py: one flaky `pr view` does not fail the run.
    """
    owners = list(config["github"]["orgs"])
    queue = review_queue(owners, runner, timeout)
    failed: list[str] = []
    for row in queue:
        try:
            row["ci_state"] = github_gh.ci_state(row["url"], runner, timeout)
        except github_gh.AuthError:
            raise
        except GhError:
            row["ci_state"] = None
            failed.append(row["url"])
    designs, truncated = proposed_designs(
        config["github"].get("design_repos", []), runner, timeout
    )
    rows = queue + issues_needing_human(owners, runner, timeout) + designs
    return {"rows": rows, "failed": failed, "truncated": truncated}


_STATUS = {"pr": "review_decision", "design": "status"}


def write(conn: sqlite3.Connection, rows: list[dict], now: datetime) -> None:
    """Replace every `source = 'github'` row with `rows`, in one transaction.

    The fields with a generic column go there (`summary` the title, `labels`
    as JSON, `status_name` the review decision or design status); the whole
    row is kept in `raw_json`, which is where the URL, CI state and brief live.
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
                    row.get(_STATUS.get(row["kind"], ""), None),
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
    rows = review_queue(argv[1:])
    print(f"{len(rows)} PRs in the review queue; GitHub's own count is the union of:")
    for owner in argv[1:]:
        for query in review_searches(owner):
            print(f"  {query}")
    for row in rows:
        print(f"  {row['url']}  {row['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
