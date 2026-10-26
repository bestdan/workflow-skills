"""GitHub source adapter for the task dashboard, over the `gh` CLI.

The `gh` account is personal and reaches personal repos as well as the work
orgs, so every search is scoped to the owners in config `github.orgs`; unscoped,
`author:@me` returned 220 PRs in the spike, 141 of them personal.

PRs come from a paginated GraphQL `search`, not `gh search prs`, because the
latter has no `headRefName` and the branch is half of the Jira join. CI state is
fetched per PR: putting `statusCheckRollup` in a 100-node search made GitHub
return an HTML error after 12s.

All `gh` calls go through one runner seam, `run(argv, timeout)`, so tests pass a
fake instead of reaching the network.

Run `python github_gh.py my-prs <owner>...` (under uv, Python 3.11+) to print
the count and check every PR carries a branch name.
"""

import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from typing import Any

Runner = Callable[[list[str], float], tuple[int, str, str]]

DEFAULT_TIMEOUT = 60.0

# Substrings of gh's stderr that mean "not logged in / bad token", lower-cased.
AUTH_PATTERNS = ("gh auth login", "bad credentials", "http 401", "authentication")


class GhError(RuntimeError):
    """A gh call failed, timed out, or returned something unparseable."""


class AuthError(GhError):
    """gh is not logged in or its token is rejected."""


def run(argv: list[str], timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    """Run argv with piped stdout/stderr; a timeout comes back as rc 124."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:g}s: {' '.join(argv[:3])}"
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _call(argv: list[str], runner: Runner, timeout: float) -> Any:
    rc, out, err = runner(argv, timeout)
    if rc != 0:
        msg = f"{' '.join(argv[:3])} exited {rc}: {err.strip()}"
        if any(p in err.lower() for p in AUTH_PATTERNS):
            raise AuthError(msg)
        raise GhError(msg)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise GhError(f"{' '.join(argv[:3])} returned non-JSON: {exc}") from None


SEARCH_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        url title headRefName isDraft reviewDecision createdAt updatedAt
        repository { nameWithOwner }
      }
    }
  }
}
"""

_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def owner_qualifier(owner: str) -> str:
    """The search qualifier scoping a query to one owner.

    `org:` is used for users and orgs alike. Measured on 2026-10-23: `org:`,
    `user:` and `owner:` returned identical counts both for the user account
    `bestdan` and for the organization `cli`, so no account-type lookup is
    needed. The name is validated so it cannot smuggle extra qualifiers in.
    """
    if not _OWNER.match(owner):
        raise ValueError(f"not a GitHub owner name: {owner!r}")
    return f"org:{owner}"


def search_prs(
    query: str,
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
    graphql: str = SEARCH_QUERY,
) -> list[dict]:
    """Every node with a `url` for a search, following endCursor to the end.

    `graphql` swaps the node selection; it must keep the `search(...)` shape,
    `pageInfo`, and `url`. The fleet view passes one selecting issues, or PRs
    with their body.
    """
    return search_nodes(query, runner, timeout, graphql)[0]


def search_nodes(
    query: str,
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
    graphql: str = SEARCH_QUERY,
    limit: "int | None" = None,
) -> "tuple[list[dict], bool]":
    """search_prs, stopping at `limit` nodes: (nodes, whether more were left).

    With no `limit` it follows every page and the flag is False.
    """
    nodes: list[dict] = []
    after: str | None = None
    while True:
        argv = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={graphql}",
            "-f",
            f"q={query}",
        ]
        if after:
            argv += ["-f", f"after={after}"]
        data = _call(argv, runner, timeout)
        try:
            search = data["data"]["search"]
            page_info = search["pageInfo"]
        except (KeyError, TypeError):
            raise GhError(f"unexpected GraphQL response for {query!r}") from None
        # A node of a type the selection skips (an issue in an is:pr search is
        # impossible, but the union allows it) comes back as {}.
        nodes.extend(n for n in search["nodes"] if n and n.get("url"))
        if limit is not None and len(nodes) >= limit:
            more = len(nodes) > limit or bool(page_info["hasNextPage"])
            return nodes[:limit], more
        if not page_info["hasNextPage"]:
            return nodes, False
        after = page_info["endCursor"]


def _row(node: dict, role: str) -> dict:
    return {
        "url": node["url"],
        "repo": node["repository"]["nameWithOwner"],
        "number": int(node["url"].rstrip("/").rsplit("/", 1)[1]),
        "title": node["title"],
        "head_ref": node.get("headRefName"),
        "is_draft": bool(node.get("isDraft")),
        "review_decision": node.get("reviewDecision"),
        "role": role,
        "updated": node.get("updatedAt"),
        "raw_json": json.dumps(node, sort_keys=True),
    }


def _prs(
    qualifier: str, role: str, owners: Iterable[str], runner: Runner, timeout: float
) -> list[dict]:
    rows: dict[str, dict] = {}
    for owner in owners:
        # Archiving a repo leaves its PRs open; `archived:false` keeps them out.
        query = f"is:pr is:open archived:false {qualifier} {owner_qualifier(owner)}"
        for node in search_prs(query, runner, timeout):
            rows.setdefault(node["url"], _row(node, role))
    return list(rows.values())


def my_prs(
    owners: Iterable[str], runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> list[dict]:
    """My open PRs in the given owners, one search per owner, as `prs` rows."""
    return _prs("author:@me", "author", owners, runner, timeout)


def review_requested(
    owners: Iterable[str], runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> list[dict]:
    """Open PRs requesting my review in the given owners, as `prs` rows."""
    return _prs("review-requested:@me", "reviewer", owners, runner, timeout)


# CheckRun conclusions that count as a failed check. SUCCESS, NEUTRAL and
# SKIPPED pass; anything not listed and not passing (STALE, a value GitHub adds
# later) is treated as pending rather than silently reported green.
_FAILED_CONCLUSIONS = {
    "FAILURE",
    "CANCELLED",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
}
_PASSED_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_FAILED_STATES = {"FAILURE", "ERROR"}


def _check_verdict(entry: dict) -> str:
    kind = entry.get("__typename")
    if kind == "StatusContext" or (kind is None and "state" in entry):
        state = (entry.get("state") or "").upper()
        if state in _FAILED_STATES:
            return "failure"
        return "success" if state == "SUCCESS" else "pending"
    status = (entry.get("status") or "").upper()
    conclusion = (entry.get("conclusion") or "").upper()
    if conclusion in _FAILED_CONCLUSIONS:
        return "failure"
    if status != "COMPLETED":
        return "pending"
    return "success" if conclusion in _PASSED_CONCLUSIONS else "pending"


def reduce_rollup(entries: list[dict]) -> str:
    """`success | failure | pending | none` for a statusCheckRollup list."""
    if not entries:
        return "none"
    verdicts = {_check_verdict(e) for e in entries}
    if "failure" in verdicts:
        return "failure"
    if "pending" in verdicts:
        return "pending"
    return "success"


def ci_state(url: str, runner: Runner = run, timeout: float = DEFAULT_TIMEOUT) -> str:
    """One PR's CI state, from `gh pr view <url> --json statusCheckRollup`."""
    data = _call(
        ["gh", "pr", "view", url, "--json", "statusCheckRollup"], runner, timeout
    )
    if not isinstance(data, dict):
        raise GhError(f"unexpected statusCheckRollup response for {url}")
    return reduce_rollup(data.get("statusCheckRollup") or [])


# Titles carry upper-case keys, branches mostly lower-case, so the match is
# case-insensitive. The look-arounds stop a match starting or ending mid-token.
_KEY = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]+)-(\d+)(?!\d)")


def jira_keys(text: str, allowed_projects: Iterable[str]) -> list[str]:
    """Jira keys in `text` (title and/or branch), upper-cased, deduplicated in
    order, keeping only those whose project is in `allowed_projects`. The
    allowlist is what keeps strings like `v2-1` from counting."""
    allowed = {p.upper() for p in allowed_projects}
    keys: list[str] = []
    for m in _KEY.finditer(text or ""):
        project = m.group(1).upper()
        key = f"{project}-{m.group(2)}"
        if project in allowed and key not in keys:
            keys.append(key)
    return keys


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "my-prs":
        print("usage: github_gh.py my-prs <owner>...", file=sys.stderr)
        return 2
    rows = my_prs(argv[1:])
    missing = [r["url"] for r in rows if not r["head_ref"]]
    print(f"{len(rows)} open PRs; {len(missing)} without headRefName")
    for url in missing:
        print(f"  missing headRefName: {url}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
