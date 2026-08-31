#!/usr/bin/env python3
"""Deterministic primitives for the gh-issue claim lifecycle.

Two sessions can race to claim the same issue, and the pieces that decide who
wins — and how the loser finds out — have to be identical no matter which
session runs them, so they live here as code rather than as prose two
sessions could each interpret slightly differently:

- `branch-name` / `issue-number` compute and parse the deterministic
  claim-lock branch name. Both racers must derive the SAME name from the
  issue number alone (never the title, which can be edited or read at
  different times by each session), or the ref race below has nothing to
  collide on.
- `acquire` performs the create-only ref push that IS the election, and its
  exit code is a contract: 0 (won), 3 (lost the race), and 4 (indeterminate —
  neither won nor lost) mean different things to the caller, and the caller
  branches on the number, not on parsing prose.
- `wip` counts in-flight work with one server-side query, so a caller cannot
  under-count by missing a label spelling or over-cost by issuing two calls.
- `release` deletes a lock ref this session created, on bail.

Usage:
  python3 gh-issue-claim.py branch-name --issue 142
  python3 gh-issue-claim.py branch-name --issue 142 --prefix bestdan/
  python3 gh-issue-claim.py issue-number --branch bestdan/task-142
  python3 gh-issue-claim.py wip --repo owner/name --json
  python3 gh-issue-claim.py acquire --repo owner/name --issue 142 --base-sha <sha>
  python3 gh-issue-claim.py release --repo owner/name --issue 142
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import (  # noqa: E402
    DEFAULT_LABELS_FILE,
    VocabularyError,
    load_vocabulary,
)

STARTED_STATUS_VALUE = "3_started"
NEEDS_REVIEW_STATUS_VALUE = "4_needs_review"

BRANCH_SEGMENT_RE = re.compile(r"^task-(\d+)$")


def run_gh(args, stdin=None):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin)
    return proc.returncode, proc.stdout, proc.stderr


def branch_name(issue, prefix=""):
    """The work branch IS the claim-lock ref: `<prefix>task-<issue>`.

    Derived from the issue number rather than the title so two racing
    sessions, which may read the title at different times, still compute the
    same name. `prefix` is used verbatim — a caller that wants a `/`
    separator includes one; nothing here appends or strips one, because a
    cloud routine's `claude/` prefix and a local session's configured prefix
    are both legal without this function editorializing about either.
    """
    return f"{prefix}task-{issue}"


def parse_issue_number(branch):
    """The issue number encoded in a claim-lock branch name, or None.

    Accepts ANY prefix — a cloud routine pushes `claude/task-142`, a local
    session pushes its configured `bestdan/task-142`, and a bare `task-142`
    is also legal — by taking only the segment after the last `/` and
    requiring it to match `task-<digits>` exactly. `task-142-fixup` and
    `mytask-142` are not task branches: the former has trailing text after
    the digits, the latter has no `/` boundary in front of `task-`.
    """
    segment = branch.rsplit("/", 1)[-1]
    match = BRANCH_SEGMENT_RE.match(segment)
    return int(match.group(1)) if match else None


def in_flight_labels(groups):
    """The two status rungs that count as in-flight, derived from labels.yml.

    Both rungs count because the WIP limit bounds the human review queue: an
    issue with an open PR awaiting review is still occupying that queue, not
    done with it. Raises VocabularyError if either value is missing, so a
    rename in labels.yml fails loudly instead of the search silently matching
    zero issues.
    """
    status_values = groups.get("status", [])
    missing = [
        value
        for value in (STARTED_STATUS_VALUE, NEEDS_REVIEW_STATUS_VALUE)
        if value not in status_values
    ]
    if missing:
        raise VocabularyError(
            "labels.yml: status group is missing value(s): " + ", ".join(missing)
        )
    return [f"status:{STARTED_STATUS_VALUE}", f"status:{NEEDS_REVIEW_STATUS_VALUE}"]


def count_wip(repo, labels_file, limit):
    """This caller's in-flight issues: one query, label OR'd via a quoted list.

    `label:"a","b"` is a verified union on the live API (both term orders,
    including when one term matches nothing) — the same technique
    gh-issue-ready.py's scope narrowing relies on. Each label is quoted
    because the label names contain a colon, which is also the search
    syntax's own separator.
    """
    groups, _colors = load_vocabulary(labels_file)
    labels = in_flight_labels(groups)
    quoted = ",".join(f'"{label}"' for label in labels)
    search = f"assignee:@me label:{quoted}"
    code, out, err = run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            search,
            "--limit",
            str(limit),
            "--json",
            "number,title,labels",
        ]
    )
    if code != 0:
        raise SystemExit(
            f"gh issue list failed for {repo}: {err.strip() or out.strip()}"
        )
    issues = json.loads(out or "[]")
    return [{"number": i["number"], "title": i["title"]} for i in issues]


def cmd_branch_name(args):
    print(branch_name(args.issue, args.prefix))
    return 0


def cmd_issue_number(args):
    number = parse_issue_number(args.branch)
    if number is None:
        print(f"not a task branch: {args.branch}", file=sys.stderr)
        return 1
    print(number)
    return 0


def cmd_wip(args):
    issues = count_wip(args.repo, args.labels_file, args.limit)
    count = len(issues)
    at_limit = count >= args.wip_limit
    result = {
        "repo": args.repo,
        "count": count,
        "limit": args.wip_limit,
        "at_limit": at_limit,
        "issues": issues,
    }
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{args.repo}: {count} in flight (limit {args.wip_limit})")
        for issue in issues:
            print(f"  #{issue['number']} {issue['title']}")
    return 0


def cmd_acquire(args):
    """Create the claim-lock ref. Exit code IS the election result.

    A plain `git push` cannot substitute for this: both racers cut the
    branch from the same base sha, so they push the identical sha and the
    loser's push reports "Everything up-to-date" and exits 0 — both sessions
    would conclude they won (measured — see commands/handlers/claim-lock.md).
    The create-only REST call has no such blind spot: a second POST for a ref
    that exists fails outright regardless of the sha it names.

    Never mutates the issue itself — the caller writes assignee/labels only
    after this returns 0, so a lost or indeterminate race leaves the issue
    untouched.
    """
    branch = branch_name(args.issue, args.prefix)
    body = json.dumps({"ref": f"refs/heads/{branch}", "sha": args.base_sha})
    code, out, err = run_gh(
        ["api", "--method", "POST", f"repos/{args.repo}/git/refs", "--input", "-"],
        stdin=body,
    )
    if code == 0:
        print(branch)
        return 0

    combined = f"{out}\n{err}".lower()
    if (
        "reference already exists" in combined
        or "http 422" in combined
        or "(422)" in combined
    ):
        print(f"lost the race for {branch}: ref already exists", file=sys.stderr)
        return 3

    print(f"could not acquire {branch}: {err.strip() or out.strip()}", file=sys.stderr)
    return 4


def cmd_release(args):
    branch = branch_name(args.issue, args.prefix)
    code, out, err = run_gh(
        ["api", "--method", "DELETE", f"repos/{args.repo}/git/refs/heads/{branch}"]
    )
    if code != 0:
        print(
            f"could not release {branch}: {err.strip() or out.strip()}", file=sys.stderr
        )
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("branch-name", help="print the claim-lock branch name")
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--prefix", default="")
    p.set_defaults(func=cmd_branch_name)

    p = subparsers.add_parser(
        "issue-number", help="parse the issue number out of a branch name"
    )
    p.add_argument("--branch", required=True)
    p.set_defaults(func=cmd_issue_number)

    p = subparsers.add_parser("wip", help="count this caller's in-flight issues")
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--wip-limit", type=int, default=3, dest="wip_limit")
    p.add_argument(
        "--limit", type=int, default=100, help="max issues the search returns"
    )
    p.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    p.add_argument("--json", action="store_true", dest="as_json")
    p.set_defaults(func=cmd_wip)

    p = subparsers.add_parser(
        "acquire", help="create the claim-lock ref (the election)"
    )
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--base-sha", required=True, dest="base_sha")
    p.add_argument("--prefix", default="")
    p.set_defaults(func=cmd_acquire)

    p = subparsers.add_parser(
        "release", help="delete a claim-lock ref this session created"
    )
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--prefix", default="")
    p.set_defaults(func=cmd_release)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
