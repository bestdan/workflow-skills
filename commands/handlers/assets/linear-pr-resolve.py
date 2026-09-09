#!/usr/bin/env python3
"""Resolve each in-flight Linear issue's PRs and classify its merge state.

The decision half of `linear-sweep-complete.md` steps 3 and 4, which the prose
used to ask the agent to hand-walk per issue: try three PR sources in order,
post-filter a coarse title search, distinguish "no PR" from "a probe failed",
then classify the issue by merge-state precedence.

WHY THIS IS A SCRIPT. The 2026-09-02 nightly tidy run reported PRE-73 as
"genuinely no PR found yet" while open PR #1003, titled
`Scaffold packages/rest-server FastAPI package (PRE-73)`, existed: the
hand-walked post-filter had narrowed to the `[<IDENTIFIER>]` bracket form only
the tracker execute path writes. The title match is now one function with that
title as a fixture (see `identifier_in_title`).

WHAT STAYS IN PROSE. The Linear MCP reads that produce this script's input, and
the completion writes that consume its output, are agent work -- MCP tools run
only from the agent. This script owns the decision over already-fetched JSON,
plus the `gh` probes, which are CLI. In a `claude-web` environment `gh` is not
available at all; there the prose's `mcp__github__*` path runs instead of this
script (see `linear-sweep-complete.md`, "Steps 2-3 in a `claude-web`
environment").

Input (stdin): a JSON array of issue objects, each with at least:

  id           str  -- the Linear issue id (uuid), echoed back on the row
  identifier   str  -- the display id, e.g. "PRE-73" (required)
  attachments  list -- the issue's attachments; each an object with a `url`,
                       or a bare url string. `{"nodes": [...]}` is accepted
                       too, since that is how GraphQL returns them.
  branchName   str  -- Linear's published branch name (optional; without it
                       the branch probe is skipped, not failed)
  project      obj  -- {"id": ..., "name": ...}, or a bare project id string,
                       or null. Used only to pick the repo.

Usage:
  python3 linear-pr-resolve.py --config dev_docs/tasks/.task-config.yml \\
      --repo "$(gh repo view --json nameWithOwner --jq .nameWithOwner)" < issues.json

Output (stdout): a JSON array, one row per input issue, in input order:

  {"id", "identifier", "repo", "prs", "resolved_via", "state", "unresolved",
   "probe_errors"}

`state` is one of:

  merged           -- some PR of this issue merged; a completion candidate
  open             -- no merged PR, but some PR is open
  unresolved       -- a probe or a merge-state read failed, or a resolved PR's
                      state could not be read. Fail-closed: never completed,
                      never demoted.
  closed_unmerged  -- every resolved PR read cleanly and is closed unmerged
  no_pr            -- every probe succeeded and found nothing ("no-PR skipped")

`no_pr` is the fifth value the four-way precedence needs to stay honest: the
prose's "no-PR skipped" bucket is GC'd by `/reconcile-tasks` row 4 while
`closed_unmerged` is *demoted* by row 3, so folding one into the other would
demote issues that simply have no PR yet.

Read-only. Nothing here mutates Linear or GitHub.
"""

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _linear_pr import pr_identity, run_gh  # noqa: E402

MERGED = "MERGED"
OPEN = "OPEN"


def die(msg):
    sys.exit(f"linear-pr-resolve: {msg}")


# ---------------------------------------------------------------- identifier


def identifier_in_title(identifier, title):
    """Does `title` carry `identifier` as a whole token?

    GitHub's search tokenizes on punctuation, so `--search "<ID> in:title"` is a
    coarse pre-filter that also matches a title merely containing `PRE` and `73`
    separately. This is the post-filter: the identifier bounded by a
    non-alphanumeric character or the string edge on each side.

    Accepts `[PRE-73]`, `(PRE-73)`, `PRE-73:` and a trailing `... (PRE-73)`.
    Rejects `PRE-730` and `PRE-73a`.

    DO NOT narrow this to the `[<IDENTIFIER>]` bracket form. Only the tracker
    execute path writes brackets; a hand-opened PR routinely puts the id in
    parentheses or at the end of the title, and hand-opened PRs are exactly the
    population these fallbacks exist for -- anything `/do-tasks` opened already
    resolved from its `links` attachment. That narrowing is the 2026-09-02
    PRE-73 defect this file's header names.
    """
    if not identifier or not title:
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(identifier) + r"(?![A-Za-z0-9])"
    return re.search(pattern, title) is not None


# -------------------------------------------------------------------- config


def load_project_repos(path):
    """Parse `linear.projects[].repo` out of a `.task-config.yml`.

    Returns {project_id: repo}. Projects with no `repo:` are omitted -- they
    fall back to `--repo`, exactly as `linear-sweep-complete.md` specifies.

    Stdlib only, so this asset stays runnable as plain `python3 ...` with no
    dependency, which is why only the small subset of YAML the config's
    `linear.projects` block actually uses is accepted. Anything else inside
    that block raises loudly rather than being silently skipped: a dropped
    project maps its issues to the wrong repo, and every PR probe for them then
    searches a repo that never held the work -- filed as "no PR" (the same
    class of silent miss as the PRE-73 defect). Keys outside `linear.projects`
    are ignored, not parsed.
    """
    try:
        with open(path) as fh:
            raw = fh.read()
    except OSError as exc:
        die(f"--config: {exc}")

    repos: Dict[str, str] = {}
    in_linear = False
    projects_indent = None
    item_indent = None
    entry: Optional[Dict[str, str]] = None
    entries: List[Dict[str, str]] = []

    def close_entry():
        if entry is not None:
            entries.append(entry)

    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip())
        body = stripped.strip()

        if indent == 0:
            close_entry()
            entry = None
            projects_indent = None
            item_indent = None
            in_linear = body == "linear:"
            continue
        if not in_linear:
            continue

        if projects_indent is None:
            if body == "projects:":
                projects_indent = indent
            continue

        if indent <= projects_indent:
            close_entry()
            entry = None
            projects_indent = None
            item_indent = None
            if body == "projects:":
                projects_indent = indent
            continue

        if body.startswith("- "):
            close_entry()
            entry = {}
            item_indent = indent
            body = body[2:].strip()
        elif entry is None or item_indent is None or indent <= item_indent:
            raise ValueError(
                f"{path}:{lineno}: expected a `- key: value` list item under linear.projects"
            )

        key, sep, value = body.partition(":")
        if not sep:
            raise ValueError(
                f"{path}:{lineno}: expected `key: value` under linear.projects, got {body!r}"
            )
        entry[key.strip()] = value.strip().strip("'\"")

    close_entry()

    for i, item in enumerate(entries):
        if not item.get("id"):
            raise ValueError(f"{path}: linear.projects[{i}] has no `id:`")
        if item.get("repo"):
            repos[item["id"]] = item["repo"]
    return repos


# ------------------------------------------------------------- issue reading


def issue_project_id(issue):
    """The issue's own project id, however the caller's fetch shaped it."""
    project = issue.get("project")
    if isinstance(project, dict):
        return project.get("id")
    if isinstance(project, str):
        return project
    return issue.get("projectId")


def attachment_urls(issue):
    """Every url on the issue's attachments, across the shapes callers pass.

    The fast path (`linear-scan.py`) and the MCP floor (`get_issue`) hand over
    a list of objects; GraphQL wraps the same list in `{"nodes": [...]}`.
    """
    raw = issue.get("attachments") or []
    if isinstance(raw, dict):
        raw = raw.get("nodes") or []
    urls = []
    for att in raw:
        if isinstance(att, str):
            urls.append(att)
        elif isinstance(att, dict) and isinstance(att.get("url"), str):
            urls.append(att["url"])
    return urls


# -------------------------------------------------------------- the 3 probes


def probe_attachment(issue):
    """Source 1: the issue's own `links` attachments.

    The authoritative source -- a structural link `/do-tasks` and
    `/deliver-task` write at PR-open time, not an inferred one. It needs no
    repo: the url carries `owner/name` itself. It also cannot fail: there is no
    network call to exit non-zero.
    """
    prs = []
    for url in attachment_urls(issue):
        if pr_identity(url):
            prs.append({"url": url})
    return prs


def probe_title(identifier, repo):
    """Source 2: `gh pr list --search "<ID> in:title"`, post-filtered.

    Returns (prs, error). A non-zero exit is an error, never an empty result:
    the same command prints nothing on a network or auth failure as it does on
    a genuine no-match, and misfiling a failure as "no PR" is what demotes an
    issue whose PR is alive.
    """
    rc, out, err = run_gh(
        [
            "pr",
            "list",
            "-R",
            repo,
            "--state",
            "all",
            "--search",
            f"{identifier} in:title",
            "--json",
            "number,url,title,state",
        ]
    )
    if rc != 0:
        return [], f"title probe failed for {identifier} in {repo}: {err.strip()}"
    try:
        hits = json.loads(out or "[]")
    except json.JSONDecodeError as exc:
        return [], f"title probe returned invalid JSON for {identifier}: {exc}"
    return [h for h in hits if identifier_in_title(identifier, h.get("title"))], None


def probe_branch(branch_name, repo):
    """Source 3: `gh pr list --head <branchName>`. Returns (prs, error)."""
    rc, out, err = run_gh(
        [
            "pr",
            "list",
            "-R",
            repo,
            "--state",
            "all",
            "--head",
            branch_name,
            "--json",
            "number,url,state",
        ]
    )
    if rc != 0:
        return [], f"branch probe failed for {branch_name} in {repo}: {err.strip()}"
    try:
        return json.loads(out or "[]"), None
    except json.JSONDecodeError as exc:
        return [], f"branch probe returned invalid JSON for {branch_name}: {exc}"


def resolve_prs(issue, repo):
    """Walk the three sources in order; stop at the first that resolves a PR.

    Returns (prs, resolved_via, errors). A source that *fails* resolves
    nothing, so the walk continues to the next one -- positive evidence from a
    later source is still evidence, and the recorded error keeps the run
    fail-closed if none arrives.
    """
    errors: List[str] = []

    prs = probe_attachment(issue)
    if prs:
        return prs, "attachment", errors

    identifier = issue.get("identifier")
    if repo:
        prs, err = probe_title(identifier, repo)
        if err:
            errors.append(err)
        elif prs:
            return prs, "title", errors

        branch_name = issue.get("branchName") or issue.get("branch_name")
        if branch_name:
            prs, err = probe_branch(branch_name, repo)
            if err:
                errors.append(err)
            elif prs:
                return prs, "branch", errors
    else:
        errors.append(
            f"no repo for {identifier}: its project names none and no --repo was given"
        )

    return [], None, errors


# ------------------------------------------------------------- merge state


def read_merge_state(pr):
    """Fill in a resolved PR's merge state. Returns (pr, error).

    Pass the URL, never the number. A PR number is repository-local and a PR
    can resolve in another repo, so a number would read the merge state of
    whatever same-numbered PR exists in the current checkout -- and a false
    MERGED completes an issue whose real PR never merged.

    A read that fails leaves `state` None, which precedence rule 3 treats as an
    unread PR: the issue lands in `unresolved` rather than falling through to
    `closed_unmerged`.
    """
    rc, out, err = run_gh(
        ["pr", "view", pr["url"], "--json", "number,url,state,mergedAt"]
    )
    if rc != 0:
        return {
            **pr,
            "state": None,
        }, f"merge-state read failed for {pr['url']}: {err.strip()}"
    try:
        got = json.loads(out or "{}")
    except json.JSONDecodeError as exc:
        return {
            **pr,
            "state": None,
        }, f"merge-state read returned invalid JSON for {pr['url']}: {exc}"
    state = got.get("state")
    if got.get("mergedAt"):
        state = MERGED
    return {
        "number": got.get("number"),
        "url": got.get("url") or pr["url"],
        "state": state,
        "mergedAt": got.get("mergedAt"),
    }, None


def classify(prs):
    """The issue's state, by merge-state precedence, checked in order.

    1. Any PR MERGED  -> "merged": a completion candidate, whatever the others say.
    2. Else any OPEN   -> "open": /reconcile-tasks row 2's concern, not this flow's.
    3. Else any unread -> "unresolved". An unread PR does NOT fall through to
       closed_unmerged: a missing read is not a confirmed closed-unmerged read,
       and row 3 demotes only closed_unmerged, so this keeps the demote path
       fail-closed.
    4. Else            -> "closed_unmerged": every PR read cleanly and none merged.

    An empty list is "no_pr" -- the caller decides whether that is a true
    "no-PR skipped" or a masked probe failure.
    """
    if not prs:
        return "no_pr"
    states = [pr.get("state") for pr in prs]
    if any(s == MERGED for s in states):
        return "merged"
    if any(s == OPEN for s in states):
        return "open"
    if any(s is None for s in states):
        return "unresolved"
    return "closed_unmerged"


def resolve_issue(issue, repos, fallback_repo):
    """One input issue -> one output row."""
    identifier = issue.get("identifier")
    if not isinstance(identifier, str) or not identifier.strip():
        die(f"issue {issue.get('id')!r}: missing `identifier`")

    repo = repos.get(issue_project_id(issue)) or fallback_repo
    prs, resolved_via, errors = resolve_prs(issue, repo)

    read = []
    for pr in prs:
        got, err = read_merge_state(pr)
        if err:
            errors.append(err)
        read.append(got)

    state = classify(read)
    # A failed probe is not a confirmed absence. Promote only the two verdicts a
    # missed PR would make destructive: "no_pr" is GC'd by /reconcile-tasks row
    # 4 and "closed_unmerged" is demoted by row 3. "open" and "merged" already
    # rest on positive evidence.
    if errors and state in ("no_pr", "closed_unmerged"):
        state = "unresolved"

    return {
        "id": issue.get("id"),
        "identifier": identifier,
        "repo": repo,
        "prs": read,
        "resolved_via": resolved_via,
        "state": state,
        "unresolved": state == "unresolved",
        "probe_errors": errors,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Resolve each in-flight Linear issue's PRs and classify its merge state."
    )
    ap.add_argument(
        "--config",
        help="path to .task-config.yml, read for linear.projects[].repo",
    )
    ap.add_argument(
        "--repo",
        help="owner/name used for any issue whose project names no repo",
    )
    args = ap.parse_args(argv)

    repos: Dict[str, str] = {}
    if args.config:
        try:
            repos = load_project_repos(args.config)
        except ValueError as exc:
            die(str(exc))

    try:
        issues = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        die(f"stdin: invalid JSON: {exc}")
    if not isinstance(issues, list):
        die(f"stdin: expected a JSON array, got {type(issues).__name__}")

    rows = [resolve_issue(issue, repos, args.repo) for issue in issues]
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
