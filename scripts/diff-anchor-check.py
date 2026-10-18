#!/usr/bin/env python3
"""Anchor-check pending review comments against a diff's right-side hunk lines.

Usage:
  python3 diff-anchor-check.py --diff <unified-diff-file> < comments.json

Reads a JSON array of `{"path": str, "line": int, "body": str}` on stdin (the
candidate comments for a batched PR review) and prints
`{"anchored": [...], "unanchored": [...]}` on stdout. Every input object is
passed through unchanged into whichever list it lands in, so the caller can
fold an unanchored comment's `body` into the review's top-level `body` with no
further lookup.

GitHub's anchor rule for a single-review comment: a `line` anchors only if it
sits on the diff's right (new) side **inside a hunk** — an added line or a
context line. It never anchors on a deleted line (left side only, no `new`
line number) or on a line that no hunk of that file covers at all.

Reuses `parse_diff()` from scripts/local-review/server.py instead of writing
a second unified-diff parser. Loaded via `importlib.util.spec_from_file_location`
against server.py's own path (not `sys.path` + a plain import), so this script
does not depend on scripts/local-review being importable as a package. That
load does execute server.py's module body, which was checked to be
side-effect-free: everything at module level is either an `import`, a `def`/
`class`, a constant (including the `WORDLIST` tuple and the `PAGE` HTML
string), or the one `assert` validating `WORDLIST`'s length/uniqueness — no
I/O, no argument parsing, and no server startup (those all live under
`if __name__ == "__main__":`).
"""

import argparse
import importlib.util
import json
import os
import sys


def _load_parse_diff():
    server_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "local-review", "server.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_diff_anchor_check_server", server_path
    )
    assert spec is not None and spec.loader is not None, f"cannot load {server_path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_diff


def right_side_anchors(files):
    """{path: set(line numbers)} of every line anchorable on that file's right side.

    Keyed only by `new` — GitHub's Reviews API takes `path` in the file's
    *current* (new) tree, so a renamed file's old path never anchors, even
    though parse_diff's rows carry the same right-side line numbers under
    either name. A deleted file's `new` already equals its (only) real path,
    so no fallback to `old` is needed there.
    """
    anchors: dict = {}
    for f in files:
        lines = set()
        for hunk in f["hunks"]:
            for row in hunk["rows"]:
                r = row["r"]
                if r["t"] in ("add", "ctx"):
                    lines.add(r["n"])
        path = f["new"]
        if path and path != "/dev/null":
            anchors.setdefault(path, set()).update(lines)
    return anchors


def split_comments(comments, anchors):
    anchored = []
    unanchored = []
    for comment in comments:
        valid = anchors.get(comment["path"], set())
        if comment["line"] in valid:
            anchored.append(comment)
        else:
            unanchored.append(comment)
    return anchored, unanchored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", required=True, help="path to a unified diff file")
    args = parser.parse_args()

    with open(args.diff) as fh:
        diff_text = fh.read()

    parse_diff = _load_parse_diff()
    files = parse_diff(diff_text)
    anchors = right_side_anchors(files)

    comments = json.load(sys.stdin)
    anchored, unanchored = split_comments(comments, anchors)

    json.dump({"anchored": anchored, "unanchored": unanchored}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
