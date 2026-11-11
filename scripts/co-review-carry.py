#!/usr/bin/env python3
"""Carry a passing co-review round's `success` to the fix commit it pushed.

The `co-review` status binds a verdict to the head a round reviewed, and the
round's own fix commit is unreviewed by construction. Left at that, a round
that passes but pushes wording fixes leaves the new head without a status, and
the PR waits for another round that tends to find more wording to fix. This
script lets such a round reach the head that merges. The caller asserts the
first two conditions; the script checks the other three itself, from the
evidence file and from GitHub:

1. The round's verdict was `pass`: no findings remained once the auto-fixes
   were applied (asserted by `--verdict`).
2. None of the fixes it applied was `(blocking)` (asserted by
   `--applied-blocking`).
3. Its evidence record recommends `no further round` and counts no
   unresolved finding, which also catches a `pass` the record contradicts.
4. The reviewed head's own `co-review` status is `success`, so at least two
   reviewers ran and none failed.
5. The fix commit is exactly one commit on top of the reviewed head (GitHub's
   compare reads `ahead_by` 1, `behind_by` 0), so nothing but that one commit
   rides on the carried status.

When every condition holds, it re-stamps the PR with the record moved to the
fix commit (`head` set to it, `carried_from` naming the reviewed head), and
then posts `success` on the fix commit, described as carried. The stamp goes
first, so a status never exists without the evidence that explains it. The
lander can tell a carried head from a reviewed one by `carried_from`.

Usage:
  python3 scripts/co-review-carry.py --repo <owner/name> --pr <n> \
    --reviewed <40-hex> --head <40-hex> --verdict <pass|pass with suggestions|blocking> \
    --applied-blocking <n> --evidence <file> --summary "<one line>" \
    [--target-url <url>]

`--evidence` is the round's own record, for the reviewed head: the file
step 12 already passed to co-review-stamp.py.

Exit 0 prints `CARRY: carried from=<short> to=<short> stamp=<outcome>
status=<url>`, plus a `co-review-carry: warning:` line on stderr when the
stamp could not delete a duplicate. Exit 3 prints `CARRY: refused reason=<why>` and writes
nothing; that is the normal answer for a round that did not pass, and for a
repo co-review-status.py's owner list leaves out. Exit 1
prints `CARRY: error reason=<why>` for a `gh` failure (a stamp written before
a failed status leaves the fix commit without `success`, which the lander
holds on). A usage error exits 2 before any `gh` call. /co-review treats
every non-zero exit as a line on its run summary, never as a failed run.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec is not None and spec.loader is not None, f"cannot load {filename}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stamp_mod = _load("co_review_stamp", "co-review-stamp.py")
status_mod = _load("co_review_status", "co-review-status.py")

VERDICTS = ("pass", "pass with suggestions", "blocking")
REFUSED = 3


def run_gh(args, stdin=None):
    """The single subprocess seam; tests replace it."""
    try:
        proc = subprocess.run(
            ["gh", *args], input=stdin, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _seam(args, stdin=None):
    # Looked up at call time, so a test that replaces run_gh here reaches the
    # stamp and status modules' calls too.
    return run_gh(args, stdin)


setattr(stamp_mod, "run_gh", _seam)
setattr(status_mod, "run_gh", _seam)


class Refused(Exception):
    pass


def reviewed_status(repo, sha):
    """The latest `co-review` state on `sha`, or None when there is none."""
    out = stamp_mod.gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/commits/{sha}/statuses",
            "--jq",
            f'[.[] | select(.context == "{status_mod.CONTEXT}")][0].state // empty',
        ]
    )
    # The list is newest first, and --paginate applies the filter per page, so
    # the first non-empty line is the latest status.
    for line in out.splitlines():
        if line.strip():
            return line.strip()
    return None


def compare(repo, base, head):
    out = stamp_mod.gh(
        [
            "api",
            f"repos/{repo}/compare/{base}...{head}",
            "--jq",
            "{status, ahead_by, behind_by}",
        ]
    )
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise stamp_mod.StampError(f"unreadable compare response: {exc}") from exc
    if not isinstance(data, dict):
        raise stamp_mod.StampError("unreadable compare response")
    return data


def check(args, record):
    """Raise Refused with the first condition that does not hold."""
    skip = status_mod.owner_skip_reason(args.repo)
    if skip:
        raise Refused(skip)
    if args.verdict != "pass":
        raise Refused(f"verdict is {args.verdict}, not pass")
    if args.applied_blocking:
        raise Refused(f"{args.applied_blocking} applied fix(es) were blocking")
    if record["next_round"] != "no further round":
        raise Refused(f"the round recommends {record['next_round']}")
    if record["unresolved_findings"]:
        raise Refused(f"{record['unresolved_findings']} finding(s) unresolved")
    state = reviewed_status(args.repo, args.reviewed)
    if state != "success":
        raise Refused(
            f"`co-review` on {args.reviewed[:7]} is {state or 'missing'}, not success"
        )
    diff = compare(args.repo, args.reviewed, args.head)
    if (diff.get("ahead_by"), diff.get("behind_by")) != (1, 0):
        raise Refused(
            f"{args.head[:7]} is not one commit on top of {args.reviewed[:7]}"
            f" (ahead {diff.get('ahead_by')}, behind {diff.get('behind_by')})"
        )


def carry(args, record):
    check(args, record)
    carried = {**record, "head": args.head, stamp_mod.CARRIED_FROM: args.reviewed}
    stamp_mod.validate_evidence(carried, args.head)
    outcome, _url, warning = stamp_mod.stamp(
        args.repo, args.pr, args.head, args.summary, carried
    )
    try:
        url = status_mod.post_status(
            args.repo,
            args.head,
            "success",
            f"carried from {args.reviewed[:7]} — {args.summary}",
            args.target_url,
        )
    except status_mod.StatusError as exc:
        raise stamp_mod.StampError(
            f"stamp {outcome}, status not posted: {exc}"
        ) from exc
    return outcome, url, warning


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--reviewed", required=True, help="the 40-hex head the round read")
    p.add_argument("--head", required=True, help="the 40-hex fix commit it pushed")
    p.add_argument("--verdict", required=True, choices=VERDICTS)
    p.add_argument("--applied-blocking", required=True, type=int)
    p.add_argument("--evidence", required=True, help="the round's evidence record")
    p.add_argument("--summary", required=True, help="the stamp's one line")
    p.add_argument("--target-url", help="where the status links to, e.g. the PR")
    args = p.parse_args(argv)
    for name in ("reviewed", "head"):
        if not stamp_mod.SHA_RE.match(getattr(args, name)):
            p.error(f"--{name} must be a full 40-character lowercase hex sha")
    if args.reviewed == args.head:
        p.error("--head must be the fix commit, not the reviewed head")
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    if args.applied_blocking < 0:
        p.error("--applied-blocking must not be negative")
    if not args.summary.strip():
        p.error("--summary must not be empty")
    try:
        with open(args.evidence, encoding="utf-8") as fh:
            args.record = stamp_mod.validate_evidence(json.load(fh), args.reviewed)
    except (OSError, ValueError) as exc:
        p.error(f"--evidence: {exc}")
    if stamp_mod.CARRIED_FROM in args.record:
        p.error("--evidence: a carried record cannot be carried again")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        outcome, url, warning = carry(args, args.record)
    except Refused as exc:
        print(f"CARRY: refused reason={exc}")
        return REFUSED
    except stamp_mod.StampError as exc:
        print(f"CARRY: error reason={exc}")
        return 1
    print(
        f"CARRY: carried from={args.reviewed[:7]} to={args.head[:7]}"
        f" stamp={outcome} status={url}"
    )
    if warning:
        print(f"co-review-carry: warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
