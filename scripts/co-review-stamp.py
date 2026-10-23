#!/usr/bin/env python3
"""Leave, or refresh, the one co-review stamp comment on a pull request.

The stamp is how a reader outside the session learns that a PR was
co-reviewed, and at which head. Its body opens with a fixed marker,

    <!-- co-review-stamp v1 head=<40-hex sha> -->

followed by one human-readable line. The fleet view's review-queue gauge
(scripts/task-dashboard/github_fleet.py) parses that marker and counts a PR as
co-reviewed only when the sha equals the PR's current head, so the format is a
contract: change it only together with that parser.

One stamp per PR, owned by the authenticated account. A run that finds its
own earlier stamp edits it in place; a run that finds none posts one. Stamps
the same account left more than once (two runs racing past the read) are
collapsed to the oldest, which is edited, and the rest deleted, so the PR never
carries two heads that disagree. A marker in another account's comment is left
alone: it is not this account's to edit.

Usage:
  python3 scripts/co-review-stamp.py --repo <owner/name> --pr <n> \
    --head <40-hex sha> --summary "<one line>"

On success prints exactly one line, `STAMPED: <created|updated|unchanged>
<comment url>`, and exits 0. Any failure prints one `co-review-stamp: <reason>`
line on stderr and exits 1; a usage error exits 2. /co-review treats every
non-zero exit as a warning on its run summary, never as a failed run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

MARKER_VERSION = "v1"
# Matches any version of the marker, so a future v2 writer replaces a v1 stamp
# rather than leaving a second one beside it.
MARKER_PREFIX = "<!-- co-review-stamp "
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class StampError(Exception):
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


def gh(args, stdin=None):
    code, out, err = run_gh(args, stdin)
    if code != 0:
        detail = (err or out).strip().splitlines()
        raise StampError(
            f"gh {' '.join(args[:3])} failed (rc {code})"
            + (f": {detail[-1]}" if detail else "")
        )
    return out


def marker(head):
    return f"<!-- co-review-stamp {MARKER_VERSION} head={head} -->"


def stamp_body(head, summary):
    line = " ".join(summary.split())
    return f"{marker(head)}\n**co-review** reviewed `{head[:7]}` — {line}"


def viewer_login():
    login = gh(["api", "user", "--jq", ".login"]).strip()
    if not login:
        raise StampError("could not resolve the authenticated login")
    return login


def own_stamps(repo, pr, login):
    """This account's stamp comments on the PR, oldest first."""
    out = gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/issues/{pr}/comments",
            "--jq",
            ".[] | {id: .id, login: .user.login, body: .body, url: .html_url} | @json",
        ]
    )
    stamps = []
    for raw in out.splitlines():
        if not raw.strip():
            continue
        try:
            c = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StampError(f"unreadable comment listing: {exc}") from exc
        body = c.get("body") or ""
        if c.get("login") == login and body.lstrip().startswith(MARKER_PREFIX):
            stamps.append(c)
    stamps.sort(key=lambda c: c["id"])
    return stamps


def write(method, path, body):
    out = gh(
        ["api", "--method", method, path, "--input", "-"],
        stdin=json.dumps({"body": body}),
    )
    try:
        return json.loads(out).get("html_url", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def stamp(repo, pr, head, summary):
    body = stamp_body(head, summary)
    stamps = own_stamps(repo, pr, viewer_login())
    if not stamps:
        url = write("POST", f"repos/{repo}/issues/{pr}/comments", body)
        return "created", url
    keep, extras = stamps[0], stamps[1:]
    if keep.get("body") == body:
        outcome, url = "unchanged", keep.get("url", "")
    else:
        path = f"repos/{repo}/issues/comments/{keep['id']}"
        outcome, url = "updated", write("PATCH", path, body) or keep.get("url", "")
    for extra in extras:
        gh(
            [
                "api",
                "--method",
                "DELETE",
                f"repos/{repo}/issues/comments/{extra['id']}",
            ]
        )
    return outcome, url


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--head", required=True, help="the 40-hex head the run reviewed")
    p.add_argument("--summary", required=True, help="one human-readable line")
    args = p.parse_args(argv)
    if not SHA_RE.match(args.head):
        p.error("--head must be a full 40-character lowercase hex sha")
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    if not args.summary.strip():
        p.error("--summary must not be empty")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        outcome, url = stamp(args.repo, args.pr, args.head, args.summary)
    except StampError as exc:
        print(f"co-review-stamp: {exc}", file=sys.stderr)
        return 1
    print(f"STAMPED: {outcome} {url}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
