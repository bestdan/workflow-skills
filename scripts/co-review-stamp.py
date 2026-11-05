#!/usr/bin/env python3
"""Leave, or refresh, the one co-review stamp comment on a pull request.

The stamp is how a reader outside the session learns that a PR was
co-reviewed, and at which head. Its body opens with a fixed marker,

    <!-- co-review-stamp v1 head=<40-hex sha> -->

followed by one human-readable line. The fleet view's review-queue gauge
(scripts/task-dashboard/github_fleet.py) parses that marker and counts a PR as
co-reviewed only when the sha equals the PR's current head, so the format is a
contract: change it only together with that parser.

One stamp per PR per account, owned by the authenticated account. A run that
finds its own earlier stamp edits it in place; a run that finds none posts
one. Stamps the same account left more than once (two runs racing past the
read) are collapsed to the oldest, which is edited, and the rest deleted. A
duplicate that cannot be deleted is reported as a warning and retried on the
next run. A marker in another account's comment is left alone: it is not this
account's to edit.

With `--evidence <file>`, the stamp also carries the round's evidence record,
the facts the merge lander demotes on, as a hidden block after the line:

    <!-- co-review-evidence v1
    {"head": ..., "unresolved_findings": N, "verification_open": N,
     "consult_calls": [{"confidence": 0-100, "kind": ...}], "next_round": ...}
    -->

`kind` is `technical`, `preference` or `stack`; `next_round` is `another
round` or `no further round`. The record's `head` must equal `--head`, and an
invalid record is a usage error (exit 2) before any `gh` call. A record may
also carry `carried_from`, the 40-hex head the round actually reviewed, when
`head` is the round's own fix commit and co-review-carry.py carried the
verdict there; the stamp line then says so rather than claiming `head` was
reviewed. A stamp written
without `--evidence` carries no record, so a later round on the same head that
has not yet re-stamped reads as having none. scripts/co-review-evidence.py is
the reader.

Usage:
  python3 scripts/co-review-stamp.py --repo <owner/name> --pr <n> \
    --head <40-hex sha> --summary "<one line>" [--evidence <file>]

On success prints exactly one line, `STAMPED: <created|updated|unchanged>
<comment url>`, and exits 0. When the kept stamp was written but a duplicate
could not be deleted, that is still exit 0, with one
`co-review-stamp: warning: <reason>` line on stderr: the PR names the right
head, and a later run retries the delete. Any other failure prints one
`co-review-stamp: <reason>` line on stderr and exits 1; a usage error exits 2. /co-review treats every
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
EVIDENCE_OPEN = "<!-- co-review-evidence v1"
EVIDENCE_RE = re.compile(r"<!-- co-review-evidence v1\n(.*?)\n-->", re.DOTALL)
EVIDENCE_KEYS = (
    "head",
    "unresolved_findings",
    "verification_open",
    "consult_calls",
    "next_round",
)
# Present only on a record co-review-carry.py wrote for a fix commit.
CARRIED_FROM = "carried_from"
CALL_KINDS = ("technical", "preference", "stack")
NEXT_ROUNDS = ("another round", "no further round")


def validate_evidence(record, head):
    """Return the record if it is well formed for `head`, else raise ValueError."""
    if not isinstance(record, dict) or sorted(
        k for k in record if k != CARRIED_FROM
    ) != sorted(EVIDENCE_KEYS):
        raise ValueError(
            f"evidence must have exactly the keys {', '.join(EVIDENCE_KEYS)}"
            f" (and optionally {CARRIED_FROM})"
        )
    if record["head"] != head:
        raise ValueError("evidence head does not match --head")
    if CARRIED_FROM in record:
        origin = record[CARRIED_FROM]
        if not (isinstance(origin, str) and SHA_RE.match(origin)) or origin == head:
            raise ValueError(
                f"evidence {CARRIED_FROM} must be a 40-hex sha other than head"
            )
    for key in ("unresolved_findings", "verification_open"):
        value = record[key]
        if type(value) is not int or value < 0:
            raise ValueError(f"evidence {key} must be a non-negative integer")
    calls = record["consult_calls"]
    if not isinstance(calls, list):
        raise ValueError("evidence consult_calls must be a list")
    for call in calls:
        if not (
            isinstance(call, dict)
            and sorted(call) == ["confidence", "kind"]
            and type(call["confidence"]) is int
            and 0 <= call["confidence"] <= 100
            and call["kind"] in CALL_KINDS
        ):
            raise ValueError(
                "each consult call needs an integer confidence 0-100 and a kind of "
                + ", ".join(CALL_KINDS)
            )
    if record["next_round"] not in NEXT_ROUNDS:
        raise ValueError(
            f"evidence next_round must be one of: {', '.join(NEXT_ROUNDS)}"
        )
    return record


def parse_evidence(body):
    """The evidence record in a stamp body, or None when it carries none."""
    match = EVIDENCE_RE.search(body or "")
    if not match:
        return None
    return json.loads(match.group(1))


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


def stamp_body(head, summary, evidence=None):
    line = " ".join(summary.split())
    origin = (evidence or {}).get(CARRIED_FROM)
    if origin:
        what = f"reviewed `{origin[:7]}`; its pass was carried to `{head[:7]}`"
    else:
        what = f"reviewed `{head[:7]}`"
    body = f"{marker(head)}\n**co-review** {what} — {line}"
    if evidence is not None:
        # Every string in a valid record is an enum value or a hex sha, so the
        # JSON can never contain the `-->` that would end the comment early.
        body += f"\n{EVIDENCE_OPEN}\n{json.dumps(evidence, sort_keys=True)}\n-->"
    return body


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


def stamp(repo, pr, head, summary, evidence=None):
    body = stamp_body(head, summary, evidence)
    stamps = own_stamps(repo, pr, viewer_login())
    if not stamps:
        url = write("POST", f"repos/{repo}/issues/{pr}/comments", body)
        return "created", url, ""
    keep, extras = stamps[0], stamps[1:]
    if keep.get("body") == body:
        outcome, url = "unchanged", keep.get("url", "")
    else:
        path = f"repos/{repo}/issues/comments/{keep['id']}"
        outcome, url = "updated", write("PATCH", path, body) or keep.get("url", "")
    failed = []
    for extra in extras:
        try:
            gh(
                [
                    "api",
                    "--method",
                    "DELETE",
                    f"repos/{repo}/issues/comments/{extra['id']}",
                ]
            )
        except StampError as exc:
            failed.append((extra["id"], str(exc)))
    warning = ""
    if failed:
        ids = ", ".join(str(cid) for cid, _ in failed)
        warning = f"could not delete duplicate stamp(s) {ids}: {failed[0][1]}"
    return outcome, url, warning


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--head", required=True, help="the 40-hex head the run reviewed")
    p.add_argument("--summary", required=True, help="one human-readable line")
    p.add_argument("--evidence", help="a JSON file holding the round's evidence record")
    args = p.parse_args(argv)
    if not SHA_RE.match(args.head):
        p.error("--head must be a full 40-character lowercase hex sha")
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    if not args.summary.strip():
        p.error("--summary must not be empty")
    args.record = None
    if args.evidence:
        try:
            with open(args.evidence, encoding="utf-8") as fh:
                args.record = validate_evidence(json.load(fh), args.head)
        except (OSError, ValueError) as exc:
            p.error(f"--evidence: {exc}")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        outcome, url, warning = stamp(
            args.repo, args.pr, args.head, args.summary, args.record
        )
    except StampError as exc:
        print(f"co-review-stamp: {exc}", file=sys.stderr)
        return 1
    print(f"STAMPED: {outcome} {url}".rstrip())
    if warning:
        print(f"co-review-stamp: warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
