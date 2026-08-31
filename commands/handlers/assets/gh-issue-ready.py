#!/usr/bin/env python3
"""Report which open, `status:2_ready` issues are actually dependency-ready.

An issue is ready iff it carries the vocabulary's ready status label AND no
issue in its `blocked_by` list is still open. `status:2_ready` alone is not
enough — a repo can label an issue ready while GitHub's native dependency
graph still has it waiting on an open blocker, and nothing else surfaces that
gap.

`gh` has no dependency subcommand — measured: `gh issue --help` lists nothing
for it. The dependency graph is reached through `gh api` instead, against
`repos/{owner}/{repo}/issues/{n}/dependencies/blocked_by` (and its `blocking`
sibling), both verified real: a bogus sibling path 404s, these return `[]`.
Both are GETs, so this script is read-only by construction — it never calls
`gh issue edit`, never mutates a label, and never passes `--method` other than
the implicit GET.

A cloud routine has no `gh`, so it cannot use this path; the unattended
equivalent is an open question, not this file's job.

Usage:
  python3 gh-issue-ready.py --repo owner/name
  python3 gh-issue-ready.py --repo owner/name --limit 100 --json
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import (  # noqa: E402
    DEFAULT_LABELS_FILE,
    VocabularyError,
    load_vocabulary,
)

READY_STATUS_VALUE = "2_ready"


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def ready_label(groups):
    """Derive `status:2_ready` from the vocabulary instead of hardcoding it.

    A rename in labels.yml (`2_ready` -> something else) must fail loudly here
    rather than silently matching zero issues.
    """
    if READY_STATUS_VALUE not in groups.get("status", []):
        raise VocabularyError(
            f"labels.yml: status group has no `{READY_STATUS_VALUE}` value"
        )
    return f"status:{READY_STATUS_VALUE}"


def list_ready_issues(repo, label, limit):
    code, out, err = run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--label",
            label,
            "--json",
            "number,title,labels",
            "--limit",
            str(limit),
        ]
    )
    if code != 0:
        raise SystemExit(
            f"gh issue list failed for {repo}: {err.strip() or out.strip()}"
        )
    return json.loads(out or "[]")


def open_blockers(repo, issue):
    """Numbers of this issue's `blocked_by` dependencies still open."""
    code, out, err = run_gh(
        ["api", f"repos/{repo}/issues/{issue}/dependencies/blocked_by"]
    )
    if code != 0:
        raise SystemExit(
            f"gh api blocked_by failed for {repo}#{issue}: {err.strip() or out.strip()}"
        )
    blockers = json.loads(out or "[]")
    return [b["number"] for b in blockers if b.get("state") == "open"]


def compute(repo, labels_file, limit):
    groups, _colors = load_vocabulary(labels_file)
    label = ready_label(groups)

    candidates = list_ready_issues(repo, label, limit)
    ready = []
    blocked = []
    for issue in candidates:
        number, title = issue["number"], issue["title"]
        blockers = open_blockers(repo, number)
        if blockers:
            blocked.append(
                {"number": number, "title": title, "open_blockers": blockers}
            )
        else:
            ready.append({"number": number, "title": title})

    return {
        "repo": repo,
        "checked": len(candidates),
        "ready": ready,
        "blocked": blocked,
    }


def report(result):
    print(f"{result['repo']}: {result['checked']} issue(s) carry status:2_ready")

    ready = result["ready"]
    print(f"\nReady ({len(ready)}):")
    for issue in ready:
        print(f"  #{issue['number']} {issue['title']}")

    blocked = result["blocked"]
    print(f"\nBlocked ({len(blocked)}):")
    for issue in blocked:
        blockers = ", ".join(f"#{n}" for n in issue["open_blockers"])
        print(f"  #{issue['number']} {issue['title']} — waiting on {blockers}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--limit", type=int, default=50, help="max issues to check")
    parser.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = compute(args.repo, args.labels_file, args.limit)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
