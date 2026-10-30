"""GitHub source adapter for the task dashboard, over the `gh` CLI.

The `gh` account is personal and reaches personal repos as well as the work
orgs, so every search is scoped to the owners in config `github.orgs`; unscoped,
`author:@me` returned 220 PRs in the spike, 141 of them personal.

PRs come from a paginated GraphQL `search`, not `gh search prs`, because the
latter has no `headRefName` and the branch is half of the Jira join. CI state
comes from the same search, as the head commit's rollup `state` alone
(rollup_state()). Asking for the full rollup, every check context, in a
100-node search made GitHub return an HTML error after 12s; the state alone
adds no connection to the query's cost. A sync used to read CI with one
`gh pr view` per PR, which spent most of the user's shared GraphQL budget
(#1252); ci_state() is that read, kept for the per-card Refresh.

Each search also asks for `rateLimit { remaining resetAt }`. That is the
user-wide budget every token and app acting as the user draws from, which
`gh api rate_limit` does not report: that endpoint counts the calling token
alone. search_nodes() hands it to a caller that asks (Budget).

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


# The head commit's CI rollup state, without its contexts. A PR query that
# needs CI state selects this instead of a per-PR `gh pr view`.
ROLLUP_STATE = "commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }"

SEARCH_QUERY = f"""
query($q: String!, $after: String) {{
  rateLimit {{ remaining resetAt }}
  search(query: $q, type: ISSUE, first: 50, after: $after) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      ... on PullRequest {{
        url title headRefName headRefOid isDraft reviewDecision createdAt updatedAt
        repository {{ nameWithOwner }}
        changedFiles additions deletions files(first: 20) {{ nodes {{ path }} }}
        {ROLLUP_STATE}
      }}
    }}
  }}
}}
"""

# How many changed paths a PR query reads (`files(first: N)` in SEARCH_QUERY,
# github_fleet.REVIEW_QUERY and refresh.py's query): enough for Little mode's
# docs-only test (fleet_now.py), which a longer PR never passes anyway.
SIZE_PATHS = 20


def pr_size(node: "dict | None") -> "dict | None":
    """A PR node's size, {files, additions, deletions, paths}, or None when
    the node has no size fields (a row synced before they were asked for).

    `paths` is None when the PR changes more files than SIZE_PATHS, since the
    list is then cut short.
    """
    node = node or {}
    counts = [node.get(k) for k in ("changedFiles", "additions", "deletions")]
    ints = [v for v in counts if isinstance(v, int) and not isinstance(v, bool)]
    if len(ints) != len(counts):
        return None
    files, adds, dels = ints
    nodes = (node.get("files") or {}).get("nodes") or []
    paths = [str(n["path"]) for n in nodes if n and n.get("path")]
    return {
        "files": files,
        "additions": adds,
        "deletions": dels,
        "paths": paths if files <= len(paths) else None,
    }


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


class Budget:
    """The user-wide GraphQL budget: the lowest `remaining` any response this
    run reported.

    The lowest, not the newest: measured on 2026-10-30, consecutive calls
    answered from two different counters (one 4,972 and the next 3,875, a
    minute apart), and the limit trips on whichever runs out first.
    `remaining` is None until a query that selects `rateLimit` has answered.
    """

    def __init__(self) -> None:
        self.remaining: "int | None" = None
        self.reset_at: "str | None" = None

    def note(self, data: Any) -> None:
        rate = (data or {}).get("rateLimit") if isinstance(data, dict) else None
        if not isinstance(rate, dict) or not isinstance(rate.get("remaining"), int):
            return
        if self.remaining is None or rate["remaining"] < self.remaining:
            self.remaining = rate["remaining"]
            self.reset_at = rate.get("resetAt")

    def below(self, floor: int) -> bool:
        """True when a response has reported fewer than `floor` points left."""
        return self.remaining is not None and self.remaining < floor


def search_prs(
    query: str,
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
    graphql: str = SEARCH_QUERY,
    budget: "Budget | None" = None,
) -> list[dict]:
    """Every node with a `url` for a search, following endCursor to the end.

    `graphql` swaps the node selection; it must keep the `search(...)` shape,
    `pageInfo`, and `url`. The fleet view passes one selecting issues, or PRs
    with their body. `budget`, when given, is updated from each page whose
    query selects `rateLimit`.
    """
    return search_nodes(query, runner, timeout, graphql, budget=budget)[0]


def search_nodes(
    query: str,
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
    graphql: str = SEARCH_QUERY,
    limit: "int | None" = None,
    budget: "Budget | None" = None,
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
        if budget is not None and isinstance(data, dict):
            budget.note(data.get("data"))
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
        "ci_state": rollup_state(node),
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


# GitHub's StatusState for a rollup, in reduce_rollup()'s terms. EXPECTED is a
# required status that has not reported yet, so it is pending.
_ROLLUP_STATES = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "ERROR": "failure",
    "PENDING": "pending",
    "EXPECTED": "pending",
}


def rollup_state(node: dict) -> "str | None":
    """`success | failure | pending | none` from a node selecting ROLLUP_STATE,
    or None when the node did not select it (a row synced before it was).

    GitHub reduces the contexts itself, so this can differ from
    reduce_rollup() at the edges: a state GitHub adds later reads as pending.
    """
    commits = node.get("commits")
    if not isinstance(commits, dict):
        return None
    nodes = commits.get("nodes") or []
    commit = (nodes[0] or {}).get("commit") if nodes else None
    rollup = (commit or {}).get("statusCheckRollup")
    if not rollup:
        return "none"
    return _ROLLUP_STATES.get(str(rollup.get("state") or "").upper(), "pending")


def ci_state(url: str, runner: Runner = run, timeout: float = DEFAULT_TIMEOUT) -> str:
    """One PR's CI state, from `gh pr view <url> --json statusCheckRollup`.

    One call per PR, so only the per-card Refresh uses it; a sync reads
    rollup_state() off its search instead.
    """
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
