#!/usr/bin/env python3
"""Collect the open human-tier PRs into one morning digest.

Each open, non-draft PR is tiered the way the lander tiers it (land-pr.py's
reads and `tier`, with AGENT_LAND taken as on). Every PR whose tier is not
`auto` is waiting on the owner: `human`, or `unknown` when a read or the policy
failed, shown with the reason. For each one the digest carries the PR body's
`## Decision brief` section, which `/deliver-task` writes
(skills/deliver-task/references/decision-brief.md). A PR without the section,
or missing any of its three lines, is still listed, as `brief missing`.

Stacks: a PR whose base branch is another open PR's head branch sits on top of
it. Chains are built over every open PR, drafts included, so a stack reads
whole: a member that is not waiting appears as one line, `auto tier, lands
itself` or `draft`. A stack is shown only when one of its members is waiting.
Members are ordered bottom first, and stacks and single PRs by their lowest PR
number.

Usage:
  python3 scripts/morning-digest.py --repo <owner/name> [--post]

Prints the digest markdown on stdout, and nothing else there. With `--post`
it also writes the digest to the one open issue whose body carries the digest
marker, editing it in place, or creates and pins that issue when there is none;
a failed pin is a warning. It then prints a final `DIGEST: <created|updated|
unchanged> <issue url>` line, or `DIGEST: error reason=...`, on stderr. Exit 0
on success, 1 when the PR list cannot be read or the post failed (the digest is
still printed), 2 for a usage error. A read error on one PR is that PR's
`unknown` tier, never a crash.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec is not None and spec.loader is not None, f"cannot load {filename}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The lander owns the gh seam and the tier reads; reuse them so a test that
# replaces land.run_gh reaches every call made here.
land = _load("land_pr", "land-pr.py")
ReadError = land.ReadError

MARKER = "<!-- morning-digest v1 -->"
ISSUE_TITLE = "Morning digest"
PR_LIMIT = 100
BRIEF_HEADING = "## Decision brief"
FIELDS = ("Changed", "Judge", "A no invalidates")
FIELD_RE = re.compile(r"^\s*[-*]\s+\*\*(" + "|".join(FIELDS) + r"):\*\*\s*(.*)$")
HEADING_RE = re.compile(r"^#{1,2}\s")


# --- the brief --------------------------------------------------------------


def parse_brief(body):
    """(fields, missing): the brief's labelled lines, and the labels absent."""
    lines = (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == BRIEF_HEADING)
    except StopIteration:
        return {}, list(FIELDS)
    fields: dict = {}
    current = None
    for line in lines[start + 1 :]:
        if HEADING_RE.match(line):
            break
        m = FIELD_RE.match(line)
        if m:
            current = m.group(1)
            fields[current] = m.group(2).strip()
        elif current and line[:1] in (" ", "\t") and line.strip():
            fields[current] = (fields[current] + " " + line.strip()).strip()
        else:
            current = None
    fields = {k: v for k, v in fields.items() if v}
    return fields, [f for f in FIELDS if f not in fields]


# --- reads ------------------------------------------------------------------


def list_open_prs(repo):
    prs = land.gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--json",
            "number,title,url,body,baseRefName,headRefName,isDraft",
            "--limit",
            str(PR_LIMIT),
        ]
    )
    if not isinstance(prs, list):
        raise ReadError("gh pr list: expected a list")
    return [p for p in prs if isinstance(p, dict)]


def tier_of(repo, pr, policies):
    """(verdict, reasons) with AGENT_LAND on; `unknown` when a read fails."""
    try:
        data = land.read_pr(repo, pr["number"])
        files = land.read_files(repo, pr["number"])
        base = data.get("baseRefName") or ""
        if base not in policies:
            try:
                policies[base] = land.read_policy(repo, base)
            except ReadError as exc:
                policies[base] = exc
        policy = policies[base]
        if isinstance(policy, ReadError):
            raise policy
    except ReadError as exc:
        return "unknown", [f"tier not computed: {exc}"]
    verdict, reasons, _ = land.tier(data, files, policy, "on")
    return verdict, reasons


# --- stacks -----------------------------------------------------------------


def stacks(prs):
    """Groups of PRs chained by base onto head, bottom first, by lowest number."""
    by_head = {p.get("headRefName"): p for p in prs}

    def parent(p):
        up = by_head.get(p.get("baseRefName"))
        return up if up is not None and up is not p else None

    def root(p):
        seen = {p["number"]}
        while (up := parent(p)) is not None and up["number"] not in seen:
            seen.add(up["number"])
            p = up
        return p

    groups: dict = {}
    for p in prs:
        groups.setdefault(root(p)["number"], []).append(p)

    def depth(p):
        d, seen = 0, {p["number"]}
        while (up := parent(p)) is not None and up["number"] not in seen:
            seen.add(up["number"])
            d, p = d + 1, up
        return d

    out = [sorted(g, key=lambda p: (depth(p), p["number"])) for g in groups.values()]
    return sorted(out, key=lambda g: min(p["number"] for p in g))


# --- the digest -------------------------------------------------------------


def _flat(text):
    return " ".join(str(text).split())


def pr_lines(pr):
    """The body of one waiting PR's entry, below its heading."""
    verdict, reasons = pr["_tier"]
    lines = [pr.get("url") or "", ""]
    shown = "; ".join(_flat(r) for r in reasons[:2])
    lines.append(f"Tier: {verdict}" + (f" — {shown}" if shown else ""))
    lines.append("")
    fields, missing = parse_brief(pr.get("body"))
    for name in FIELDS:
        if name in fields:
            lines.append(f"- **{name}:** {fields[name]}")
    if missing:
        lines.append("- brief missing: " + ", ".join(missing))
    return lines


def waiting(pr):
    return not pr.get("isDraft") and pr["_tier"][0] != "auto"


def render(prs, today):
    lines = [MARKER, f"# Morning digest — {today.isoformat()}", ""]
    groups = [g for g in stacks(prs) if any(waiting(p) for p in g)]
    count = sum(1 for g in groups for p in g if waiting(p))
    if not count:
        lines.append("No human-tier PRs are waiting.")
        return "\n".join(lines) + "\n"
    lines.append(f"{count} PR{' waits' if count == 1 else 's wait'} on the owner.")
    for group in groups:
        lines.append("")
        if len(group) == 1:
            pr = group[0]
            lines += [f"## #{pr['number']} {_flat(pr.get('title'))}", ""]
            lines += pr_lines(pr)
            continue
        chain = " → ".join(f"#{p['number']}" for p in group)
        lines.append(f"## Stack: {chain}")
        for pr in group:
            lines += ["", f"### #{pr['number']} {_flat(pr.get('title'))}", ""]
            if waiting(pr):
                lines += pr_lines(pr)
            else:
                why = "draft" if pr.get("isDraft") else "auto tier, lands itself"
                lines.append(f"{pr.get('url') or ''} — {why}")
    return "\n".join(lines).rstrip("\n") + "\n"


def build(repo, today):
    prs = list_open_prs(repo)
    policies: dict = {}
    for pr in prs:
        pr["_tier"] = (
            ("draft", []) if pr.get("isDraft") else tier_of(repo, pr, policies)
        )
    return render(prs, today)


# --- posting ----------------------------------------------------------------


def find_issue(repo):
    issues = land.gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            "morning-digest v1 in:body",
            "--json",
            "number,body,url",
        ]
    )
    if not isinstance(issues, list):
        raise ReadError("gh issue list: expected a list")
    found = [
        i for i in issues if isinstance(i, dict) and MARKER in (i.get("body") or "")
    ]
    return min(found, key=lambda i: i["number"]) if found else None


def post(repo, body):
    """('created'|'updated'|'unchanged', url)."""
    issue = find_issue(repo)
    if issue and (issue.get("body") or "").strip() == body.strip():
        return "unchanged", issue.get("url") or ""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "digest.md"
        path.write_text(body)
        if issue:
            n = str(issue["number"])
            land.gh(["issue", "edit", n, "--repo", repo, "--body-file", str(path)])
            return "updated", issue.get("url") or ""
        out = land.gh(
            [
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                ISSUE_TITLE,
                "--body-file",
                str(path),
            ]
        )
    url = out.strip().splitlines()[-1] if out.strip() else ""
    number = url.rstrip("/").rsplit("/", 1)[-1]
    try:
        land.gh(["issue", "pin", number, "--repo", repo])
    except ReadError as exc:
        print(f"warning: could not pin {url}: {exc}", file=sys.stderr)
    return "created", url


def main(argv=None, today=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--post", action="store_true", help="write the digest issue")
    args = p.parse_args(argv)
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    try:
        digest = build(args.repo, today or dt.date.today())
    except ReadError as exc:
        print(f"morning-digest: cannot list open PRs: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(digest)
    if not args.post:
        return 0
    try:
        outcome, url = post(args.repo, digest)
    except ReadError as exc:
        print(f"DIGEST: error reason={_flat(exc)}", file=sys.stderr)
        return 1
    print(f"DIGEST: {outcome} {url}".rstrip(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
