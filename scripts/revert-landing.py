#!/usr/bin/env python3
"""Open a revert PR for a merged pull request's squash commit.

The undo for a bad landing is a revert PR and the next release. This opens it
through GitHub's `revertPullRequest` mutation, the one behind the Revert
button, so no local checkout is needed. The revert PR is titled
`fix: revert #<pr> (<original title>)`: the `fix` type makes it release, and
the bare `#<pr>` is what `merge-audit.py`'s follow-up rule matches.

Before opening it, the PR must be merged with a merge commit. A merge commit
without the `Landed-by: land-pr.sh` trailer is reverted all the same, with a
`NOTE:` saying it was not an agent landing. An open PR already titled
`fix: revert #<pr> (` is reported instead of opening a second.

After opening it, the revert PR's files are checked against the original's:
every path must appear in both, with additions and deletions swapped. GitHub
computes a new PR's files after the call returns, so the read is retried.

Usage:
  python3 scripts/revert-landing.py <pr> --repo <owner/name> [--reason <text>]

Prints an optional `NOTE:` line, then `REVERT: opened <url>`,
`REVERT: exists <url>` or `REVERT: refused reason=<why>`, then for an opened
or existing revert `CHECK: reverses <sha7>`, `CHECK: mismatch <detail>` or
`CHECK: unknown reason=<why>`. Exit 0 when the revert is open and its check
passed, 1 when no revert is open, 3 when one is open but its check did not
pass, 2 for a usage error.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec is not None and spec.loader is not None, f"cannot load {filename}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The lander owns the gh seam and the file read; a test that replaces
# land.run_gh and land.sleep reaches every call made here.
land = _load("land_pr", "land-pr.py")
ReadError = land.ReadError

TRAILER = "Landed-by: land-pr.sh"
CHECK_DELAYS = (2, 4, 8)
OPEN_LIMIT = 200
MUTATION = """
mutation($id: ID!, $title: String!, $body: String!) {
  revertPullRequest(input: {pullRequestId: $id, title: $title, body: $body}) {
    revertPullRequest { number url }
  }
}
"""


def revert_title(pr, title):
    return f"fix: revert #{pr} ({title})"


def read_merged_pr(repo, pr):
    data = land.gh_json(
        [
            "pr",
            "view",
            str(pr),
            "-R",
            repo,
            "--json",
            "id,number,title,state,mergeCommit,url",
        ]
    )
    if not isinstance(data, dict):
        raise ReadError("gh pr view: no PR data")
    return data


def find_open_revert(repo, pr):
    prs = land.gh_json(
        [
            "pr",
            "list",
            "-R",
            repo,
            "--state",
            "open",
            "--limit",
            str(OPEN_LIMIT),
            "--json",
            "number,title,url",
        ]
    )
    if not isinstance(prs, list):
        raise ReadError("pr list: expected a list")
    prefix = f"fix: revert #{pr} ("
    return next(
        (
            p
            for p in prs
            if isinstance(p, dict) and (p.get("title") or "").startswith(prefix)
        ),
        None,
    )


def open_revert(repo, node_id, title, body):
    data = land.gh_json(
        [
            "api",
            "graphql",
            "-f",
            f"query={MUTATION}",
            "-f",
            f"id={node_id}",
            "-f",
            f"title={title}",
            "-f",
            f"body={body}",
        ]
    )
    made = (
        (((data or {}).get("data") or {}).get("revertPullRequest") or {}).get(
            "revertPullRequest"
        )
        if isinstance(data, dict)
        else None
    )
    if not isinstance(made, dict) or not made.get("number"):
        errors = (data or {}).get("errors") if isinstance(data, dict) else None
        detail = (
            (errors or [{}])[0].get("message") if errors else "no revert PR returned"
        )
        raise ReadError(f"revertPullRequest: {detail}")
    return made


def _changes(files):
    """{path key: (additions, deletions)}; a rename is keyed by both its paths,
    so a→b and its revert b→a share a key."""
    out = {}
    for f in files:
        path, prev = f.get("path"), f.get("previous_path")
        key = tuple(sorted((path, prev))) if prev else (path,)
        out[key] = (f.get("additions") or 0, f.get("deletions") or 0)
    return out


def mismatch(original, revert):
    """None when `revert` undoes `original` file for file, else what differs."""
    want = {k: (d, a) for k, (a, d) in _changes(original).items()}
    got = _changes(revert)
    if not got:
        return "the revert PR shows no files yet"
    for key in sorted(set(want) | set(got)):
        name = " -> ".join(p for p in key if p)
        if key not in got:
            return f"{name} is not in the revert"
        if key not in want:
            return f"{name} is in the revert but not the original"
        if got[key] != want[key]:
            return (
                f"{name}: revert +{got[key][0]}/-{got[key][1]}, "
                f"expected +{want[key][0]}/-{want[key][1]}"
            )
    return None


def check(repo, pr, revert_number, merge_sha):
    """The CHECK line's text and whether it passed."""
    try:
        original = land.read_files(repo, pr)
    except ReadError as exc:
        return f"CHECK: unknown reason=cannot read #{pr}'s files: {exc}", False
    why, unreadable = None, False
    for delay in (0, *CHECK_DELAYS):
        if delay:
            land.sleep(delay)
        try:
            why = mismatch(original, land.read_files(repo, revert_number))
        except ReadError as exc:
            why, unreadable = f"cannot read #{revert_number}'s files: {exc}", True
            continue
        unreadable = False
        if why is None:
            return f"CHECK: reverses {merge_sha[:7]}", True
    if unreadable:
        return f"CHECK: unknown reason={why}", False
    return f"CHECK: mismatch {why}", False


def run(repo, pr, reason):
    try:
        data = read_merged_pr(repo, pr)
    except ReadError as exc:
        print(f"REVERT: refused reason=cannot read #{pr}: {exc}")
        return 1
    sha = (data.get("mergeCommit") or {}).get("oid")
    if data.get("state") != "MERGED" or not sha:
        state = str(data.get("state") or "unknown").lower()
        print(
            f"REVERT: refused reason=#{pr} is {state}, not merged with a merge commit"
        )
        return 1
    try:
        message = land.gh(
            ["api", f"repos/{repo}/commits/{sha}", "--jq", ".commit.message"]
        )
        if TRAILER not in [line.strip() for line in message.splitlines()]:
            print(
                f"NOTE: {sha[:7]} carries no `{TRAILER}` trailer, so it was not an agent landing"
            )
    except ReadError as exc:
        print(
            f"NOTE: cannot read {sha[:7]}'s message, so its trailer is unchecked: {exc}"
        )

    try:
        existing = find_open_revert(repo, pr)
    except ReadError as exc:
        print(
            f"REVERT: refused reason=cannot list open PRs to rule out a duplicate: {exc}"
        )
        return 1
    if existing:
        print(f"REVERT: exists {existing.get('url', '')}")
        number = existing["number"]
    else:
        lines = [f"Reverts #{pr}, squash commit {sha}."]
        if reason:
            lines += ["", f"Why: {reason}"]
        lines += ["", "Opened by revert-landing.py."]
        try:
            made = open_revert(
                repo,
                data.get("id"),
                revert_title(pr, data.get("title", "")),
                "\n".join(lines),
            )
        except ReadError as exc:
            print(f"REVERT: refused reason={exc}")
            return 1
        print(f"REVERT: opened {made.get('url', '')}")
        number = made["number"]
    line, ok = check(repo, pr, number, sha)
    print(line)
    return 0 if ok else 3


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("pr", type=int, help="the merged PR to revert")
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--reason", help="a line for the revert PR's body")
    args = p.parse_args(argv)
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    return run(args.repo, args.pr, args.reason)


if __name__ == "__main__":
    sys.exit(main())
