#!/usr/bin/env python3
"""Turn in-flight Jira issues into `linear-pr-resolve.py` input rows.

The Jira half of `jira-sweep-complete.md` step 3. Jira's GitHub integration is
the thing that would normally link a PR to an issue; this sweep exists for the
boards where it is off. What is left is the trail `/do-tasks` and
`/deliver-task` write on the issue itself, as comments:

  Claimed by /do-tasks. Working on branch `task/PROJ-45`; PR link will follow.
  PR opened: https://github.com/acme/widgets/pull/123

Those two comments are structural -- this plugin wrote them, at claim time and
at PR-open time, about this issue -- so they play the part a Linear `links`
attachment plays: the PR url becomes an `attachments` entry (source 1) and the
branch becomes `branchName` (source 3). `linear-pr-resolve.py` then runs its
title and branch fallbacks and its merge-state read unchanged.

WHY ONLY THOSE TWO SHAPES. A comment that merely *mentions* a PR url ("see
#123 for context") is citation, not linkage, and treating citation as linkage
is the over-close bug `/sweep-for-complete` was built not to reproduce. So a
url counts only when the comment *starts* with `PR opened: `, and a branch only
when the comment starts with `Claimed by /<command>.`.

Input (stdin): a JSON array of issues. Each needs `key`. Comments are read from
whichever shape the backend produced:

  fields.comment.comments[]   -- the Atlassian MCP's getJiraIssue
  comments[] / comments{comments[]}  -- `acli jira workitem comment list --json`

A comment `body` may be plain text (acli renders it) or an Atlassian Document
Format tree (the REST v3 shape); ADF inline code is re-wrapped in backticks so
the branch pattern matches either.

With `--acli`, an issue that carries no comments is filled in by running
`acli jira workitem comment list --key <KEY> --json --paginate`. A failed read
is recorded in that row's `source_errors`, which `linear-pr-resolve.py` folds
into its probe errors -- so an issue whose comments could not be read lands in
`unresolved`, never in "no PR".

Output (stdout): a JSON array, one row per input issue, in input order:

  {"id", "identifier", "attachments", "branchName", "source_errors"}

Read-only. Nothing here mutates Jira or GitHub.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _linear_pr import pr_identity  # noqa: E402

PR_OPENED = re.compile(r"^\s*PR opened:\s*(\S+)")
# The backticks are optional because acli renders an ADF code mark as bare
# spaces: "Working on branch  task/<KEY> ; PR link will follow." (observed
# 2026-11-06). The branch then ends at whitespace or the `;`. The MCP's
# markdown rendering escapes them instead: "branch \`task/<KEY>\`;"
# (observed 2026-11-08), so a backslash before either backtick is optional too.
# A stacked claim puts a comma straight after a bare branch ("branch task/<KEY>,
# stacked on ..."); git allows commas in a name, so extract() strips a trailing
# `,` or `.` rather than the regex refusing them.
CLAIMED_BRANCH = re.compile(
    r"^\s*Claimed by /[\w:-]+\.\s+Working on branch\s+\\?`?([^`\s;\\]+)"
)


def die(msg):
    sys.exit(f"jira-pr-sources: {msg}")


def run_acli(args):
    """Run `acli <args>` and return (returncode, stdout, stderr).

    The single subprocess seam; tests stub it rather than the network.
    """
    proc = subprocess.run(["acli", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# ------------------------------------------------------------- comment bodies


def adf_text(node):
    """Flatten an ADF node to text, keeping inline code in backticks."""
    if isinstance(node, list):
        return "".join(adf_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        text = node.get("text") or ""
        marks = node.get("marks") or []
        if any(isinstance(m, dict) and m.get("type") == "code" for m in marks):
            return f"`{text}`"
        return text
    if node.get("type") == "hardBreak":
        return "\n"
    if node.get("type") in ("inlineCard", "blockCard", "embedCard"):
        return (node.get("attrs") or {}).get("url") or ""
    inner = adf_text(node.get("content") or [])
    if node.get("type") in ("paragraph", "heading", "codeBlock"):
        return inner + "\n"
    return inner


MARKDOWN_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|~>])")


def body_text(body):
    """Comment text. A string body is unescaped first: the MCP renders
    comments as markdown, escaping backticks (observed 2026-11-08) and
    possibly `_`. Neither a git branch nor a url can hold a backslash, so
    unescaping never corrupts either."""
    if isinstance(body, str):
        return MARKDOWN_ESCAPE.sub(r"\1", body)
    return adf_text(body)


def comment_list(issue) -> Optional[list]:
    """The issue's comments in whichever shape it arrived, or None if absent."""
    fields = issue.get("fields")
    if isinstance(fields, dict):
        comment = fields.get("comment")
        if isinstance(comment, dict) and isinstance(comment.get("comments"), list):
            return comment["comments"]
    raw = issue.get("comments")
    if isinstance(raw, dict) and isinstance(raw.get("comments"), list):
        return raw["comments"]
    if isinstance(raw, list):
        return raw
    return None


def truncated_comments(issue, comments) -> Optional[Tuple[int, int]]:
    """(returned, total) when `fields.comment` says it holds only a page.

    The issue-level comment field is paginated (`total`, `maxResults`), so a
    long thread can arrive cut short. The missing comments may hold the `PR
    opened:` line, so the row fails closed rather than reading as "no PR".
    The Atlassian MCP's markdown response carried no `total` (observed
    2026-11-08), so on that backend this guard is best-effort.
    """
    fields = issue.get("fields")
    comment = fields.get("comment") if isinstance(fields, dict) else None
    if not isinstance(comment, dict) or comments is None:
        return None
    total = comment.get("total")
    if isinstance(total, int) and total > len(comments):
        return len(comments), total
    return None


# ----------------------------------------------------------------- extraction


def extract(comments) -> Tuple[List[str], Optional[str]]:
    """(PR urls, branch) from the plugin-written comments, in comment order.

    Every `PR opened:` url is kept: an issue can carry a stale closed-unmerged
    PR and a newer merged one, and `linear-pr-resolve.py` classifies across
    all of them. The branch is the *last* claim comment's, since a re-claim
    after a bail names the branch the current attempt is on.
    """
    urls: List[str] = []
    branch = None
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        text = body_text(comment.get("body"))
        m = PR_OPENED.match(text)
        if m:
            url = m.group(1).rstrip(".,;)>")
            if pr_identity(url) and url not in urls:
                urls.append(url)
            continue
        m = CLAIMED_BRANCH.match(text)
        if m:
            branch = m.group(1).rstrip(".,")
    return urls, branch


def fetch_comments_acli(key) -> Tuple[Optional[list], Optional[str]]:
    rc, out, err = run_acli(
        ["jira", "workitem", "comment", "list", "--key", key, "--json", "--paginate"]
    )
    if rc != 0:
        return None, f"acli comment list failed for {key}: {(err or out).strip()}"
    try:
        pages = json_documents(out)
    except json.JSONDecodeError as exc:
        return None, f"acli comment list returned invalid JSON for {key}: {exc}"
    comments: list = []
    for page in pages:
        got = comment_list({"comments": page})
        if got is None:
            return None, f"acli comment list for {key} had no `comments` array"
        comments.extend(got)
    if not pages:
        return None, f"acli comment list for {key} printed nothing"
    return comments, None


def json_documents(text):
    """Every JSON document in `text`. `--paginate` prints one per page,
    back to back, rather than one merged array."""
    decoder = json.JSONDecoder()
    docs: list = []
    i = 0
    text = text or ""
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return docs
        doc, i = decoder.raw_decode(text, i)
        docs.append(doc)


def to_row(issue, use_acli):
    key = issue.get("key")
    if not isinstance(key, str) or not key.strip():
        die(f"issue {issue.get('id')!r}: missing `key`")

    errors: List[str] = []
    comments = comment_list(issue)
    shown = truncated_comments(issue, comments)
    if shown:
        errors.append(f"only {shown[0]} of {shown[1]} comments returned for {key}")
    if comments is None and use_acli:
        comments, err = fetch_comments_acli(key)
        if err:
            errors.append(err)
    if comments is None and not errors:
        errors.append(
            f"no comments supplied for {key} (pass --acli, or include fields.comment)"
        )

    urls, branch = extract(comments or [])
    return {
        "id": issue.get("id"),
        "identifier": key,
        "attachments": urls,
        "branchName": branch,
        "source_errors": errors,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--acli",
        action="store_true",
        help="fetch comments with acli for any issue that carries none",
    )
    args = ap.parse_args(argv)

    try:
        issues = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        die(f"stdin: invalid JSON: {exc}")
    if not isinstance(issues, list):
        die(f"stdin: expected a JSON array, got {type(issues).__name__}")

    rows = [to_row(issue, args.acli) for issue in issues if issue is not None]
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
