#!/usr/bin/env python3
"""Print the co-review evidence record for one head of a pull request.

The record is what the merge lander demotes on: unresolved findings, open
verification items, consult-decided calls with their confidence and kind, and
the next-round recommendation. co-review writes it into its stamp comment with
`co-review-stamp.py --evidence`; this script reads it back. The format, and the
listing that finds the stamp, live in co-review-stamp.py, which this script
loads, so the writer and the reader cannot drift apart.

Only the authenticated account's own stamp is read, the same comment the
stamp script edits. Another account's comment carrying the markers is ignored.

Usage:
  python3 scripts/co-review-evidence.py --repo <owner/name> --pr <n> \
    --head <40-hex sha>

Exit 0 prints the record as one JSON line. Exit 3 means there is no record for
that head: no stamp, a stamp without a record, or a record for another head.
Exit 1 is a `gh` failure or a record that does not parse or validate; a lander
must treat it as no evidence too. A usage error exits 2.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "co_review_stamp", Path(__file__).resolve().parent / "co-review-stamp.py"
)
assert _SPEC is not None and _SPEC.loader is not None
stamp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stamp)

ABSENT = 3


def read_evidence(repo, pr, head):
    """The validated record for `head`, or None when there is none."""
    stamps = stamp.own_stamps(repo, pr, stamp.viewer_login())
    if not stamps:
        return None
    record = stamp.parse_evidence(stamps[0].get("body"))
    if record is None or not isinstance(record, dict) or record.get("head") != head:
        return None
    return stamp.validate_evidence(record, head)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--head", required=True, help="the 40-hex head to read evidence for")
    args = p.parse_args(argv)
    if not stamp.SHA_RE.match(args.head):
        p.error("--head must be a full 40-character lowercase hex sha")
    if "/" not in args.repo:
        p.error("--repo must be owner/name")

    try:
        record = read_evidence(args.repo, args.pr, args.head)
    except stamp.StampError as exc:
        print(f"co-review-evidence: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"co-review-evidence: unreadable evidence record: {exc}", file=sys.stderr)
        return 1
    if record is None:
        print(f"co-review-evidence: no record for {args.head[:7]}", file=sys.stderr)
        return ABSENT
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
