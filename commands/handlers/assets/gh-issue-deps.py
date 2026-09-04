#!/usr/bin/env python3
"""Create native `blocked_by` dependency edges between issues that already exist.

This is the WRITE half of the dependency graph `gh-issue-ready.py` reads. Until
it existed, `/push-plan` recorded a plan's blockers as a `Blocked by: #<n>` body
footer only, so every issue the loop filed had an empty `blocked_by` list and
both dependency checks — `/list-tasks` (`gh-issue.md`) and `/do-tasks` (via
`gh-issue-ready.py`) — passed everything. They were correct code answering a
question nobody populated the data for.

Two measured facts shape this script, and getting either wrong fails silently:

- **The POST body carries `issue_id`, a database id — not an issue number.**
  `POST repos/{owner}/{repo}/issues/{n}/dependencies/blocked_by` takes
  `{"issue_id": <id>}`, where the id is the blocker's REST `id` field, a
  repo-independent integer that is nothing like its `#<number>`. Passing the
  number links a different issue, or none. So every blocker number is resolved
  through `GET repos/{repo}/issues/{n}` first, and `.id` is what travels.
- **`blocked_by` is a paginated REST list.** A bare read returns the first 30
  edges, so an existing edge past page one would read as absent and be created
  twice. `--paginate --slurp` is what makes the existing-edge check honest,
  same as `gh-issue-ready.py`'s read of the same endpoint.

Both endpoints of an edge must already exist when this runs — an edge needs the
blocker's number, and `/push-plan` v1 never updates an issue after creation. So
callers run this as a SECOND PASS, after every issue in the batch is filed. A
blocker that has no issue yet is caught rather than papered over: a bare slug is
skipped with a warning (that is `--ready-only` holding a blocker back), and a
number the repo does not have fails loudly on the GET.

Edge creation is create-missing-only, so a re-push adds no duplicates.

A cloud routine has no `gh`, and the GitHub MCP connector has no dependency tool
(measured — `dev_docs/decisions/2026-08-24-routine-claim-channel.md`), so this
path is LOCAL ONLY. There is no unattended equivalent to fall back to.

Note: the local `sandbox-network-guard` hook blocks non-GET `gh api`, so
`--apply` needs the sandbox escape to run locally.

Usage:
  # <blocked>:<blocker>, both issue numbers; repeatable
  python3 gh-issue-deps.py --repo owner/name --edge 12:11 --edge 12:10
  python3 gh-issue-deps.py --repo owner/name --edge 12:11 --apply --json

Without `--apply` nothing is written: the reads still run, so the report says
exactly which edges would be created, which already exist, and which were
skipped.
"""

import argparse
import json
import re
import subprocess
import sys

# `#142`, `142`, or `owner/repo#142` — the id shape /push-plan §5.3 uses for
# gh-issue. Anything else is a bare slug the caller could not resolve.
ISSUE_REF = re.compile(r"^(?:(?P<repo>[^\s#]+)#|#)?(?P<number>\d+)$")


class EdgeError(Exception):
    """A caller mistake that must stop the run before anything is written."""


def run_gh(args, stdin=None):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin)
    return proc.returncode, proc.stdout, proc.stderr


def parse_ref(ref, repo):
    """An issue number from a reference, or None when it is not one.

    An `owner/repo#<n>` prefix naming THIS repo is stripped — that is the shape
    `/push-plan` records in `tracker_id` when `gh-issue.repo` is configured, so
    refusing it would reject the handler's own output. A prefix naming a
    DIFFERENT repo returns None: a cross-repo edge is not something this path has
    measured, and inventing one would write a link nobody verified reads back.
    """
    match = ISSUE_REF.match(ref.strip())
    if not match:
        return None
    named = match.group("repo")
    if named and named != repo:
        return None
    return int(match.group("number"))


def parse_edges(specs, repo):
    """`<blocked>:<blocker>` pairs -> [(blocked_number, blocker_ref)].

    Only the blocked side must be a number here: it is an issue this run just
    created, so a caller that cannot name it has nothing to attach an edge to.
    The blocker side stays raw so an unresolved slug can be reported as skipped
    rather than crashing the whole pass.
    """
    edges = []
    for spec in specs:
        blocked_ref, sep, blocker_ref = spec.partition(":")
        if not sep or not blocker_ref.strip():
            raise EdgeError(f"malformed --edge {spec!r}: expected <blocked>:<blocker>")
        blocked = parse_ref(blocked_ref, repo)
        if blocked is None:
            raise EdgeError(
                f"--edge {spec!r}: {blocked_ref.strip()!r} is not an issue in {repo}"
            )
        blocker = parse_ref(blocker_ref, repo)
        if blocker is not None and blocker == blocked:
            raise EdgeError(
                f"--edge {spec!r}: #{blocked} cannot block itself — the plan graph is wrong"
            )
        edges.append((blocked, blocker_ref.strip()))
    return edges


def issue_database_id(repo, issue, cache):
    """The blocker's REST `id`, which is what the POST body wants.

    A missing issue raises rather than returning None: reaching here with a
    number the repo does not have means the edge pass ran before its issues
    existed, and a skipped edge would hide that.
    """
    if issue in cache:
        return cache[issue]
    code, out, err = run_gh(["api", f"repos/{repo}/issues/{issue}"])
    if code != 0:
        raise SystemExit(
            f"resolving {repo}#{issue} failed: {err.strip() or out.strip()}"
        )
    payload = json.loads(out or "{}")
    if "id" not in payload:
        raise SystemExit(f"resolving {repo}#{issue}: response carried no `id`")
    cache[issue] = payload["id"]
    return cache[issue]


def existing_blockers(repo, issue, cache):
    """Numbers already on this issue's `blocked_by` list.

    `--paginate --slurp` for the reason `gh-issue-ready.py` gives: a bare read
    stops at 30 entries, and an existing edge past that page would read as
    absent and be created a second time.
    """
    if issue in cache:
        return cache[issue]
    code, out, err = run_gh(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/issues/{issue}/dependencies/blocked_by",
        ]
    )
    if code != 0:
        raise SystemExit(
            f"reading blocked_by for {repo}#{issue} failed: {err.strip() or out.strip()}"
        )
    pages = json.loads(out or "[]")
    cache[issue] = {entry["number"] for page in pages for entry in page}
    return cache[issue]


def create_edge(repo, blocked, blocker_id):
    """One POST. The body is `issue_id`, the blocker's DATABASE id."""
    code, out, err = run_gh(
        [
            "api",
            "--method",
            "POST",
            f"repos/{repo}/issues/{blocked}/dependencies/blocked_by",
            "--input",
            "-",
        ],
        stdin=json.dumps({"issue_id": blocker_id}),
    )
    if code != 0:
        raise SystemExit(
            f"POST blocked_by {repo}#{blocked} <- id {blocker_id} failed: "
            f"{err.strip() or out.strip()}"
        )
    return out


def apply_edges(repo, edges, apply=False):
    id_cache: dict = {}
    edge_cache: dict = {}
    created, existing, skipped = [], [], []

    for blocked, blocker_ref in edges:
        blocker = parse_ref(blocker_ref, repo)
        if blocker is None:
            # A bare slug (a blocker `--ready-only` held back) or another repo's
            # issue. Neither has an id to link to, so say so instead of guessing.
            skipped.append({"blocked": blocked, "blocker": blocker_ref})
            print(
                f"warning: #{blocked} blocked by {blocker_ref!r} — not an issue in "
                f"{repo}, so no native edge was created",
                file=sys.stderr,
            )
            continue
        if blocker in existing_blockers(repo, blocked, edge_cache):
            existing.append({"blocked": blocked, "blocker": blocker})
            continue
        blocker_id = issue_database_id(repo, blocker, id_cache)
        if apply:
            create_edge(repo, blocked, blocker_id)
        # Recorded whether or not it was sent, so a repeated --edge reports the
        # second copy as already linked instead of as a second creation.
        edge_cache[blocked].add(blocker)
        created.append(
            {"blocked": blocked, "blocker": blocker, "blocker_id": blocker_id}
        )

    return {
        "repo": repo,
        "applied": apply,
        "created": created,
        "existing": existing,
        "skipped": skipped,
    }


def report(result):
    verb = "created" if result["applied"] else "would create"
    print(f"{result['repo']}: {verb} {len(result['created'])} edge(s)")
    for edge in result["created"]:
        print(f"  #{edge['blocked']} blocked_by #{edge['blocker']}")
    if result["existing"]:
        print(f"\nAlready linked ({len(result['existing'])}):")
        for edge in result["existing"]:
            print(f"  #{edge['blocked']} blocked_by #{edge['blocker']}")
    if result["skipped"]:
        print(f"\nSkipped — no issue to link ({len(result['skipped'])}):")
        for edge in result["skipped"]:
            print(f"  #{edge['blocked']} blocked by {edge['blocker']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument(
        "--edge",
        action="append",
        default=[],
        dest="edges",
        metavar="BLOCKED:BLOCKER",
        help=(
            "issue BLOCKED is blocked by issue BLOCKER, both as `#<n>`, `<n>` or "
            "`owner/repo#<n>` naming --repo; repeatable"
        ),
    )
    parser.add_argument("--apply", action="store_true", help="send the POSTs")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        edges = parse_edges(args.edges, args.repo)
    except EdgeError as exc:
        print(f"refusing to write edges in {args.repo}: {exc}", file=sys.stderr)
        return 2

    result = apply_edges(args.repo, edges, apply=args.apply)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
