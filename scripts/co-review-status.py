#!/usr/bin/env python3
"""Post the `co-review` commit status on the head a co-review round reviewed.

The status is what binds a co-review verdict to one exact commit. It is a
commit status with context `co-review`, posted on the head SHA the round read
(step 3's `headRefOid`), never on co-review's own fix commit. A later push
leaves the new head without the status, so a required `co-review` check goes
back to waiting until a run reviews that head too, unless the round passed
and co-review-carry.py carried its `success` to the fix commit. The merge
lander reads it as "review-complete on this SHA".

Usage:
  python3 scripts/co-review-status.py --repo <owner/name> --sha <40-hex> \
    --state pending|success|failure --description "<text>" [--target-url <url>]

On success prints exactly one line, `STATUS: <state> sha=<short> url=<url>`,
and exits 0. `<url>` is the status's `target_url` when one was given, else the
API URL of the status itself. A `gh` failure prints `STATUS: error sha=<short>
reason=<reason>` and exits 1. A usage error (including a SHA that is not 40
lowercase hex) exits 2 before any `gh` call. /co-review treats every non-zero
exit as a warning on its run summary, never as a failed run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

CONTEXT = "co-review"
STATES = ("pending", "success", "failure")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# GitHub rejects a status description longer than 140 characters.
DESCRIPTION_MAX = 140


class StatusError(Exception):
    pass


def run_gh(args, stdin=None):
    """The single subprocess seam; tests replace it."""
    try:
        proc = subprocess.run(
            ["gh", *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def clip(description):
    line = " ".join(description.split())
    if len(line) <= DESCRIPTION_MAX:
        return line
    return line[: DESCRIPTION_MAX - 1] + "…"


def post_status(repo, sha, state, description, target_url=None):
    payload = {"state": state, "context": CONTEXT, "description": clip(description)}
    if target_url:
        payload["target_url"] = target_url
    args = ["api", "--method", "POST", f"repos/{repo}/statuses/{sha}", "--input", "-"]
    code, out, err = run_gh(args, stdin=json.dumps(payload))
    if code != 0:
        detail = (err or out).strip().splitlines()
        raise StatusError(
            f"gh api failed (rc {code})" + (f": {detail[-1]}" if detail else "")
        )
    try:
        body = json.loads(out)
    except json.JSONDecodeError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return body.get("target_url") or body.get("url") or ""


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--sha", required=True, help="the 40-hex head the round reviewed")
    p.add_argument("--state", required=True, choices=STATES)
    p.add_argument("--description", required=True, help="one human-readable line")
    p.add_argument("--target-url", help="where the status links to, e.g. the PR")
    args = p.parse_args(argv)
    if not SHA_RE.match(args.sha):
        p.error("--sha must be a full 40-character lowercase hex sha")
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    if not args.description.strip():
        p.error("--description must not be empty")
    return args


def main(argv=None):
    args = parse_args(argv)
    short = args.sha[:7]
    try:
        url = post_status(
            args.repo, args.sha, args.state, args.description, args.target_url
        )
    except StatusError as exc:
        print(f"STATUS: error sha={short} reason={exc}")
        return 1
    print(f"STATUS: {args.state} sha={short} url={url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
