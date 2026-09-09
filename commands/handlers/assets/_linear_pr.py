#!/usr/bin/env python3
"""Shared GitHub-PR primitives for the Linear handler assets.

`linear-false-closures.py` (which asks "did a merged PR deliver this closed
issue?") and `linear-pr-resolve.py` (which asks "which PRs does this in-flight
issue have, and did any merge?") need the same two pieces: the `gh` subprocess
seam, and the rule for deciding when two GitHub PR urls name the same PR. One
home for both, so the two flows can never disagree about which PR is which.

Stdlib only, so the handler assets stay runnable as plain `python3 …` with no
dependency.
"""

import json
import re
import subprocess


class GhError(Exception):
    """A `gh` invocation this module owns exited non-zero.

    Raised rather than exited so each caller can apply its own failure policy:
    `linear-false-closures.py` dies (a truncated merged-PR list would reopen
    delivered work), while `linear-pr-resolve.py` records the failure and
    classifies the issue `unresolved` (a discovery failure is not a confirmed
    absence of a PR).
    """


def run_gh(args):
    """Run `gh <args>` and return (returncode, stdout, stderr).

    The single subprocess seam in this module and in `linear-pr-resolve.py`.
    Tests stub this one function rather than the network, so every probe,
    every failure exit and every post-filter is exercised without `gh`.
    """
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def merged_prs(repo):
    """All merged PRs in `repo`, from `gh` — the source of truth for what shipped.

    Paginates the full closed-PR history (no --limit cap): a silently truncated
    page would misclassify a real, delivered issue as a false closure, and
    --apply would then un-complete real work.

    Raises GhError when `gh` exits non-zero.
    """
    rc, out, err = run_gh(
        [
            "api",
            "--paginate",
            "--jq",
            ".[] | select(.merged_at != null) | {number: .number, headRefName: .head.ref, url: .html_url, title: .title, body: .body, mergedAt: .merged_at}",
            f"repos/{repo}/pulls?state=closed&per_page=100",
        ]
    )
    if rc != 0:
        raise GhError(f"gh api pulls failed: {err.strip()}")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


PR_IDENTITY = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", re.I)


def pr_identity(url):
    """Canonical `owner/repo/pull/<n>` for a GitHub PR url, else None.

    Linear stores whatever url was attached — routinely with a trailing slash,
    a `?src=linear` query, a fragment, or a `/files` tab — so an exact-string
    match against gh's canonical `html_url` misses real ownership links and
    would misclassify delivered work as a false closure. Compare parsed
    identities, not raw strings.
    """
    m = PR_IDENTITY.search(url or "")
    return f"{m.group(1).lower()}/pull/{m.group(2)}" if m else None
