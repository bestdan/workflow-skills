#!/usr/bin/env python3
"""Audit landed PRs: the co-review gate, and the shadow lander's record.

Two modes, both read-only:

- `review` lists every PR merged since `--since` whose final head carries no
  `co-review` `success` commit status. Each one is named by how it got in,
  from the base branch's ruleset evaluations (`rulesets/rule-suites`, matched
  on the merge commit):
    bypass   a bypass actor pushed past the rules (the admin bypass)
    ungated  the rules passed, so `co-review` was not required at the time
    unknown  no evaluation found; GitHub keeps them for about a month
  The list should be empty apart from deliberate bypasses.

- `shadow` reads the `land-pr.sh --dry-run` comment on each PR merged since
  `--since` and reports the would-land count against the 30 graduation needs.
  For each would-land PR it lists the commits pushed after the head that
  comment judged, and every fix or revert PR merged within 7 days that cites
  it by number. Whether a later commit was a substantive owner change is the
  owner's call: the report lists, it does not judge.
  With `--policy <file>`, each shadowed PR is re-tiered by merge-tier.py under
  that candidate policy (with AGENT_LAND on) instead of trusting the posted
  verdict. It counts as would-land when the candidate tiers it `auto` and the
  posted comment had no hold besides the tier's.

Usage:
  python3 scripts/merge-audit.py --repo <owner/name> --since YYYY-MM-DD \
    --mode review|shadow [--policy <file>]

Ends with one summary line: `AUDIT: review merged=N lacking=M bypass=B
ungated=U unknown=K`, or `AUDIT: shadow shadowed=S would-land=W/30`.
Exit 0 for any finding, 1 when the merged-PR list cannot be read, 2 for a
usage error. A read error on one PR is reported against that PR, never a crash.

Run it weekly; this repo adds no scheduler of its own. From a session with
`gh` auth, for example:
  /loop 7d python3 scripts/merge-audit.py --repo bestdan/workflow-skills \
    --mode review --since <the date 7 days ago>
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import re
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec is not None and spec.loader is not None, f"cannot load {filename}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The lander owns the gh seam, the reads, and the comment format; reuse them so
# a test that replaces land.run_gh reaches every call made here.
land = _load("land_pr", "land-pr.py")
ReadError = land.ReadError

GRADUATION = 30
FOLLOW_UP_DAYS = 7
FOLLOW_UP_TYPES = ("fix", "revert")
# Posted hold reasons that come from the tier, which --policy recomputes.
TIER_HOLDS = (
    "tier human: ",
    "tier not computed: ",
    "cannot read AGENT_LAND",
    "AGENT_LAND is ",
)
VERDICT_RE = re.compile(r"would land: \*\*(yes|no)\*\*")
HEAD_RE = re.compile(r"dry run on `([0-9a-f]{7,40})`")
TYPE_RE = re.compile(r"^([a-z]+)(?:\([^)]*\))?!?: ")


def _when(stamp):
    return dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


# --- reads ------------------------------------------------------------------


def read_merged(repo, since):
    prs = land.gh_json(
        [
            "pr",
            "list",
            "-R",
            repo,
            "--state",
            "merged",
            "--search",
            f"merged:>={since}",
            "--limit",
            "1000",
            "--json",
            "number,title,body,url,mergedAt,headRefOid,baseRefName,mergeCommit,labels",
        ]
    )
    if not isinstance(prs, list):
        raise ReadError("pr list: expected a list")
    return sorted(
        (p for p in prs if isinstance(p, dict) and p.get("mergedAt")),
        key=lambda p: p["mergedAt"],
    )


def read_rule_results(repo, base):
    """{merge sha: 'pass'|'bypass'|...} from the base branch's rule evaluations."""
    ref = urllib.parse.quote(f"refs/heads/{base}", safe="")
    results: dict = {}
    for page in land._pages(
        [f"repos/{repo}/rulesets/rule-suites?ref={ref}&time_period=month&per_page=100"]
    ):
        if not isinstance(page, list):
            raise ReadError("rule suites: a page is not a list")
        for suite in page:
            if isinstance(suite, dict) and suite.get("after_sha"):
                results.setdefault(suite["after_sha"], suite.get("result"))
    return results


def read_lander_comment(repo, pr):
    """The oldest land-pr comment on the PR, or None."""
    found = []
    for page in land._pages([f"repos/{repo}/issues/{pr}/comments"]):
        if not isinstance(page, list):
            raise ReadError("issue comments: a page is not a list")
        for c in page:
            body = (c.get("body") or "") if isinstance(c, dict) else ""
            if body.lstrip().startswith(land.MARKER):
                found.append(c)
    return min(found, key=lambda c: c.get("id", 0)) if found else None


def read_commits(repo, pr):
    commits: list = []
    for page in land._pages([f"repos/{repo}/pulls/{pr}/commits"]):
        if not isinstance(page, list):
            raise ReadError("pull commits: a page is not a list")
        commits.extend(c for c in page if isinstance(c, dict) and c.get("sha"))
    return commits


# --- review -----------------------------------------------------------------


def review(repo, prs):
    lacking, rule_cache = [], {}
    for p in prs:
        head = p.get("headRefOid") or ""
        try:
            state = land.read_statuses(repo, head).get(land.CO_REVIEW)
        except ReadError as exc:
            lacking.append((p, "unknown", f"cannot read statuses: {exc}"))
            continue
        if state == "success":
            continue
        base = p.get("baseRefName") or ""
        if base not in rule_cache:
            try:
                rule_cache[base] = read_rule_results(repo, base)
            except ReadError as exc:
                rule_cache[base] = exc
        rules = rule_cache[base]
        merge_sha = (p.get("mergeCommit") or {}).get("oid") or ""
        status_note = f"`co-review` {state or 'missing'} on {head[:7]}"
        if isinstance(rules, ReadError):
            lacking.append(
                (p, "unknown", f"{status_note}; cannot read rule suites: {rules}")
            )
            continue
        result = rules.get(merge_sha)
        kind = {"bypass": "bypass", "pass": "ungated"}.get(result or "", "unknown")
        lacking.append((p, kind, status_note))
    for p, kind, note in lacking:
        print(f"#{p['number']} {kind:8} {note} — {p.get('title', '')}")
    counts = {
        k: sum(1 for _, kind, _ in lacking if kind == k)
        for k in ("bypass", "ungated", "unknown")
    }
    print(
        f"AUDIT: review merged={len(prs)} lacking={len(lacking)} "
        f"bypass={counts['bypass']} ungated={counts['ungated']} unknown={counts['unknown']}"
    )


# --- shadow -----------------------------------------------------------------


def parse_comment(body):
    """(verdict 'yes'|'no'|None, judged head prefix or None, reason lines)."""
    verdict = VERDICT_RE.search(body)
    head = HEAD_RE.search(body)
    reasons = [line[2:] for line in body.splitlines() if line.startswith("- ")]
    return (
        (verdict.group(1) if verdict else None),
        (head.group(1) if head else None),
        reasons,
    )


def retier(repo, p, policy_text):
    """merge-tier.py's verdict for the merged PR under a candidate policy."""
    facts = {"title": p.get("title"), "body": p.get("body"), "labels": p.get("labels")}
    files = land.read_files(repo, p["number"])
    verdict, reasons, _ = land.tier(facts, files, policy_text, "on")
    return verdict, reasons


def later_commits(commits, judged):
    if not judged:
        return None
    for i, c in enumerate(commits):
        if c["sha"].startswith(judged):
            return commits[i + 1 :]
    return None


def follow_ups(p, prs):
    merged = _when(p["mergedAt"])
    cite = re.compile(rf"(?<![\w/])#{p['number']}\b")
    out = []
    for q in prs:
        if q is p:
            continue
        gap = _when(q["mergedAt"]) - merged
        kind = TYPE_RE.match(q.get("title") or "")
        if (
            dt.timedelta(0) < gap <= dt.timedelta(days=FOLLOW_UP_DAYS)
            and kind
            and kind.group(1) in FOLLOW_UP_TYPES
            and cite.search(f"{q.get('title') or ''}\n{q.get('body') or ''}")
        ):
            out.append(q)
    return out


def shadow(repo, prs, policy_text):
    shadowed, landing = 0, []
    for p in prs:
        try:
            comment = read_lander_comment(repo, p["number"])
        except ReadError as exc:
            print(f"#{p['number']} unknown  cannot read comments: {exc}")
            continue
        if comment is None:
            continue
        shadowed += 1
        verdict, judged, reasons = parse_comment(comment.get("body") or "")
        if policy_text is None:
            would = verdict == "yes"
            why = "posted verdict yes" if would else None
        else:
            other_holds = (
                []
                if verdict == "yes"
                else [r for r in reasons if not r.startswith(TIER_HOLDS)]
            )
            try:
                tier, tier_reasons = retier(repo, p, policy_text)
            except ReadError as exc:
                print(f"#{p['number']} unknown  cannot re-tier: {exc}")
                continue
            would = tier == "auto" and not other_holds and verdict is not None
            why = f"candidate policy: {tier_reasons[0]}" if would else None
        if would:
            landing.append((p, judged, why))

    for p, judged, why in landing:
        print(f"#{p['number']} would-land ({why}) — {p.get('title', '')}")
        try:
            later = later_commits(read_commits(repo, p["number"]), judged)
        except ReadError as exc:
            print(f"    commits: unknown ({exc})")
        else:
            if later is None:
                print(
                    f"    commits: the judged head {judged or '(none)'} is not among the PR's commits"
                )
            for c in later or []:
                who = (c.get("author") or {}).get("login") or (
                    c.get("commit") or {}
                ).get("author", {}).get("name", "?")
                headline = ((c.get("commit") or {}).get("message") or "").splitlines()[
                    :1
                ]
                print(
                    f"    later commit {c['sha'][:7]} by {who}: {headline[0] if headline else ''}"
                )
        for q in follow_ups(p, prs):
            print(
                f"    follow-up #{q['number']} merged {q['mergedAt'][:10]}: {q.get('title', '')}"
            )
    print(f"AUDIT: shadow shadowed={shadowed} would-land={len(landing)}/{GRADUATION}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--since", required=True, help="YYYY-MM-DD, merged on or after")
    p.add_argument("--mode", required=True, choices=("review", "shadow"))
    p.add_argument("--policy", help="shadow only: a candidate merge-policy.yml")
    args = p.parse_args(argv)
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    try:
        dt.date.fromisoformat(args.since)
    except ValueError:
        p.error("--since must be YYYY-MM-DD")
    if args.policy and args.mode != "shadow":
        p.error("--policy applies to --mode shadow only")
    policy_text = None
    if args.policy:
        try:
            policy_text = Path(args.policy).read_text()
        except OSError as exc:
            p.error(f"cannot read --policy: {exc}")

    try:
        prs = read_merged(args.repo, args.since)
    except ReadError as exc:
        print(f"AUDIT: error reason={exc}")
        return 1
    if args.mode == "review":
        review(args.repo, prs)
    else:
        shadow(args.repo, prs, policy_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
