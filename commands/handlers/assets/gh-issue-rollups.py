#!/usr/bin/env python3
"""Report which of a repo's open issues are parent rollups (they have sub-issues).

`/promote-tasks`'s gh-issue path must not promote a parent rollup. A rollup is
an issue that was broken into sub-issues and now serves only as a shell;
promoting one moves an empty shell to `status:2_ready` + `auto:eligible`, where
`/do-tasks` claims it as ordinary work.

`gh issue list` does not expose sub-issue counts as a filterable field, so the
set comes from one bulk GraphQL query per page of open issues.

## Why this is a file and not a fenced block in the handler doc

It used to be 36 lines of bash inside `gh-issue-promote.md` step 3a. Handler
`.md` bodies are runtime prompt text; `scripts/lint-shell.sh` globs only
`*.sh`/`*.bash`/`*.bats` from `git ls-files`, so nothing in the gate ever
syntax-checked, shellchecked, or ran that block. Seven defects were found in it
across three review rounds on PR #432, every one by a human reviewer and none by
the gate. They shared a signature: **a wrong or empty result that reads as a
clean run.** Each guard below is named for the defect it closes, and
`scripts/test_gh_issue_rollups.py` covers each one.

## Output contract

The steps that consume this run as separate tool calls with no shared shell
state — a shell variable or a temp path does not survive the invocation — so
**stdout is the contract**:

    ROLLUP_OK=1
    <parent issue number>
    ...                       # sorted, unique, one per line; absent if none

    ROLLUP_OK=0
    ROLLUP_REASON=<reason>    # on any failure; exit status is also non-zero

A repo with genuinely no rollups prints `ROLLUP_OK=1` and nothing else. That
must stay distinguishable from a failed lookup, which prints `ROLLUP_OK=0` and
a reason — five of the seven defects violated exactly that property. A partial
set is never emitted: any page failure discards everything collected so far.

Usage:
  python3 gh-issue-rollups.py --repo owner/name
  python3 gh-issue-rollups.py --repo owner/name --json
"""

import argparse
import json
import os
import subprocess
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shape import ShapeError, expect  # noqa: E402

QUERY = """
query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    issues(states: [OPEN], first: 100, after: $cursor) {
      nodes { number subIssues(first: 0) { totalCount } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

# GitHub's sub-issues feature is not active on every repo/org. That one failure
# is expected rather than exceptional, so the handler's Fallback prose names it
# specifically; every other failure reports its own text.
SUBISSUES_UNAVAILABLE = "subIssues field unavailable"


class LookupFailed(Exception):
    """A page could not be trusted. Carries the reason for `ROLLUP_REASON=`."""


class GhResult(NamedTuple):
    """What `gh` returned. Named because two of the three fields are `str`.

    A plain tuple unpacks positionally, so swapping stdout and stderr
    type-checks and runs — it just reports the wrong text as the failure reason.
    Naming the fields does not make that swap impossible, and an earlier version
    of this docstring overclaimed that it did. What it does is make the swap
    visible where the value is BUILT: `run_gh` constructs with keywords, so a
    transposition there has to be written past the field names.

    The call sites still unpack positionally, deliberately — tests stub `run_gh`
    with a plain 3-tuple, and that affordance holds only while consumers unpack
    rather than reach for `.stdout`.
    """

    returncode: int
    stdout: str
    stderr: str


class Page(NamedTuple):
    """One validated page of the issues connection."""

    parents: list
    has_next: bool
    end_cursor: object


def run_gh(args):
    """Run `gh` and return a GhResult. The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return GhResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def fetch_page(owner, repo, cursor):
    """One GraphQL page, validated end to end, or raise LookupFailed.

    Defect (2): the original checked nothing, so a denied page produced an empty
    parent set indistinguishable from a repo with no rollups.
    Defect (4): it then piped the body through unchecked `jq`, so a well-formed
    HTTP 200 carrying an unexpected shape read as a genuine last page.
    """
    args = [
        "api",
        "graphql",
        "-f",
        f"query={QUERY}",
        "-F",
        f"owner={owner}",
        "-F",
        f"repo={repo}",
    ]
    if cursor is not None:
        args += ["-F", f"cursor={cursor}"]
    code, out, err = run_gh(args)

    text = (err or "") + (out or "")
    if "subIssues" in text and ("Field 'subIssues'" in text or "doesn't exist" in text):
        raise LookupFailed(SUBISSUES_UNAVAILABLE)
    if code != 0:
        raise LookupFailed(f"gh api graphql exited {code}: {_first_line(err or out)}")
    if not (out or "").strip():
        raise LookupFailed("gh api graphql returned an empty body")

    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise LookupFailed(f"response was not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LookupFailed("response was not a JSON object")

    # A GraphQL `errors` envelope arrives with HTTP 200 and exit 0, so the
    # return code above cannot see it.
    if payload.get("errors"):
        raise LookupFailed(
            f"GraphQL errors: {_first_line(json.dumps(payload['errors']))}"
        )

    try:
        return _validate_page(payload)
    except ShapeError as exc:
        raise LookupFailed(str(exc)) from exc


def _first_line(text):
    return (text or "").strip().splitlines()[0] if (text or "").strip() else "no detail"


def _validate_page(payload):
    """Return a validated Page, or raise ShapeError.

    Every read goes through expect(), so a malformed response produces one
    sentence naming the field rather than a KeyError traceback — and each
    result is narrowed to its type at the call site, which is what lets the
    comparisons below be checked rather than taken on faith.
    """
    issues = expect(
        expect(
            expect(payload, "data", dict, "response"),
            "repository",
            dict,
            "response.data",
        ),
        "issues",
        dict,
        "response.data.repository",
    )
    nodes = expect(issues, "nodes", list, "issues")

    parents = []
    for node in nodes:
        number = expect(node, "number", int, "issue node")
        sub = expect(node, "subIssues", dict, f"issue #{number}")
        # Defect (5): the shell block compared this in `jq`, which orders every
        # number before every string, so a string totalCount satisfied `> 0` and
        # a promotable issue was silently skipped as a rollup. expect() rejects
        # both a string and a bool here.
        if expect(sub, "totalCount", int, f"issue #{number}.subIssues") > 0:
            parents.append(number)

    page_info = expect(issues, "pageInfo", dict, "issues")
    has_next = expect(page_info, "hasNextPage", bool, "issues.pageInfo")

    end_cursor = page_info.get("endCursor")
    if has_next:
        # Defect (6): `hasNextPage: true` with a missing/null/empty `endCursor`
        # set the shell's CURSOR to the literal string "null" and re-fetched the
        # same page forever — a hang, not a failure. Read with .get() rather
        # than expect(): a null endCursor is legal on the LAST page, so absence
        # is only an error under has_next.
        if not isinstance(end_cursor, str) or not end_cursor:
            raise ShapeError(
                f"issues.pageInfo.endCursor: hasNextPage is true but endCursor "
                f"is {end_cursor!r}"
            )

    return Page(parents, has_next, end_cursor)


def collect_parents(repo):
    """Every open parent-rollup issue number, sorted and unique.

    Raises LookupFailed on any page failure, discarding the partial set — a
    partial answer here silently under-reports rollups, which is the failure
    mode this whole file exists to prevent.
    """
    if "/" not in repo:
        raise LookupFailed(f"--repo must be owner/name, got {repo!r}")
    owner, name = repo.split("/", 1)
    if not owner or not name:
        raise LookupFailed(f"--repo must be owner/name, got {repo!r}")

    parents = set()
    cursor = None
    seen_cursors = set()
    while True:
        page = fetch_page(owner, name, cursor)
        parents.update(page.parents)
        if not page.has_next:
            return sorted(parents)
        # Defect (7): a valid but *unchanging* cursor loops forever, and the
        # guard for defect (6) does not cover it — that cursor is a non-empty
        # string, so it passes every shape check.
        if page.end_cursor in seen_cursors:
            raise LookupFailed(f"pagination repeated cursor {page.end_cursor!r}")
        seen_cursors.add(page.end_cursor)
        cursor = page.end_cursor


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a JSON object instead of the ROLLUP_OK= line protocol",
    )
    args = parser.parse_args(argv)

    try:
        parents = collect_parents(args.repo)
    except LookupFailed as exc:
        reason = str(exc)
        if args.json:
            print(json.dumps({"ok": False, "reason": reason, "parents": []}))
        else:
            print("ROLLUP_OK=0")
            print(f"ROLLUP_REASON={reason}")
        return 1

    if args.json:
        print(json.dumps({"ok": True, "reason": None, "parents": parents}))
    else:
        print("ROLLUP_OK=1")
        for number in parents:
            print(number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
