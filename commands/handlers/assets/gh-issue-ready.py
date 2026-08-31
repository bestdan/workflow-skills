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
  python3 gh-issue-ready.py --repo owner/name --label follow-up   # match a board's scope
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


def list_ready_issues(repo, label, limit, scope_labels=()):
    """Open issues carrying the ready label, narrowed to `scope_labels`.

    The scope matters for more than cost. `--limit` is applied by the API, so a
    window drawn over the whole repo can exclude an in-scope issue entirely —
    and a missing verdict is indistinguishable from a ready one, so the caller
    cannot detect the omission. Repeated `--label` flags AND together, matching
    how the board query narrows itself.
    """
    args = [
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--label",
        label,
    ]
    for scope in scope_labels:
        args += ["--label", scope]
    args += ["--json", "number,title,labels", "--limit", str(limit)]
    code, out, err = run_gh(args)
    if code != 0:
        raise SystemExit(
            f"gh issue list failed for {repo}: {err.strip() or out.strip()}"
        )
    return json.loads(out or "[]")


def open_blockers(repo, issue):
    """Numbers of this issue's `blocked_by` dependencies still open.

    `--paginate --slurp`, not a bare call: this is a REST list endpoint, so a
    plain read returns only the first 30 blockers and an open one past that page
    would make a blocked issue read as ready — a silent wrong answer, which is
    the one thing this script must not produce. `--slurp` is what makes it
    parseable: bare `--paginate` emits one JSON array per page, concatenated,
    which is not valid JSON; `--slurp` wraps the pages in a single array, so the
    result is a list of pages to flatten.
    """
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
            f"gh api blocked_by failed for {repo}#{issue}: {err.strip() or out.strip()}"
        )
    pages = json.loads(out or "[]")
    blockers = [b for page in pages for b in page]
    return [b["number"] for b in blockers if b.get("state") == "open"]


def compute(repo, labels_file, limit, scope_labels=()):
    groups, _colors = load_vocabulary(labels_file)
    label = ready_label(groups)

    candidates = list_ready_issues(repo, label, limit, scope_labels)
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
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        dest="scope_labels",
        metavar="LABEL",
        help="narrow to issues also carrying this label; repeatable (AND)",
    )
    parser.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = compute(args.repo, args.labels_file, args.limit, args.scope_labels)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
