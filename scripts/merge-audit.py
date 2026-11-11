#!/usr/bin/env python3
"""Audit landed PRs: the co-review gate, the shadow lander's record, and the
agent's real landings.

Three modes, all read-only:

- `review` lists every PR merged since `--since` whose final head carries no
  `co-review` `success` commit status. Each one is named by how it got in,
  from the base branch's ruleset evaluations (`rulesets/rule-suites`, matched
  on the merge commit):
    owner    the owner's login pushed past the rules (the admin bypass)
    bypass   any other actor pushed past the rules; an alarm, since only the
             owner is meant to hold the bypass
    ungated  the rules passed the merge, though its head shows no `co-review`
             success now; usually the check was not yet required
    unknown  no evaluation found; GitHub keeps them for about a month
  Every line names the actor: the rule suite's `actor_name` for a bypass,
  otherwise the PR's `mergedBy`. The owner is `--owner`, by default the
  repo's owner segment. Agents act as a non-admin bot, so `owner` means the
  owner's own credential was used: their browser session or a per-command
  approval. The list should be empty apart from `owner` lines.
  Reading rule suites needs Administration (read) on the repo. A run without
  it, such as one as a Write-role bot, cannot tell a bypass from an ungated
  merge, so every gap reads `unknown`; it still names who merged it.
  `--rule-suites <file>` reads a saved response instead, so an admin fetches
  the suites once and anyone runs the audit:
    gh api 'repos/<o>/<r>/rulesets/rule-suites?time_period=month&per_page=100' \
      --paginate --slurp > suites.json
  Where the admin's token sits behind a per-command approval, they run that
  one fetch themselves; this script never asks for the credential.

- `shadow` reads the `land-pr.sh --dry-run` comment on each PR merged since
  `--since` and reports the would-land count against the 30 graduation needs.
  For each would-land PR it lists the commits pushed after the head that
  comment judged, and every fix or revert PR merged within 7 days that cites
  it by number. A follow-up is found by its Conventional Commit title
  (`fix:` or `revert:`) and a bare `#N`, so a revert made with GitHub's Revert
  button needs retitling as `revert: ...` to be counted. Whether a later commit was a substantive owner change is the
  owner's call: the report lists, it does not judge.
  With `--policy <file>`, each shadowed PR is re-tiered by merge-tier.py under
  that candidate policy (with AGENT_LAND on) instead of trusting the posted
  verdict. It counts as would-land when the candidate tiers it `auto` and the
  posted comment had no hold besides the tier's.

- `landed` lists every PR merged since `--since` whose merge commit carries
  the `Landed-by: land-pr.sh` trailer, with each follow-up fix or revert found
  by the same rule as `shadow`'s. `/revert-landing` titles its revert so that
  rule finds it. It also names any such merge that lacks the `landed-by-agent`
  label. Whether a follow-up's defect was one a gate could have caught, and so
  shrinks the auto slice, is the owner's call. A landing less than 7 days old
  can still collect a follow-up; set `--since` 14 days back to audit a full
  window each week.

Usage:
  python3 scripts/merge-audit.py --repo <owner/name> --since YYYY-MM-DD \
    --mode review|shadow|landed [--policy <file>] [--owner <login>] \
    [--rule-suites <file>]

Ends with one summary line: `AUDIT: review merged=N lacking=M owner=O
bypass=B ungated=U unknown=K`, `AUDIT: shadow shadowed=S would-land=W/30`, or
`AUDIT: landed landed=L followed-up=F unlabeled=U unknown=K`.
Exit 0 for any finding, 1 when the merged-PR list cannot be read, 2 for a
usage error. A read error on one PR is reported against that PR, never a crash.

Run it weekly; this repo adds no scheduler of its own. From a session with
`gh` auth, for example:
  /loop 7d python3 scripts/merge-audit.py --repo bestdan/workflow-skills \
    --mode review \
    --since "$(python3 -c 'import datetime as d; print(d.date.today() - d.timedelta(7))')"
The `--since` is computed on each run, so every week audits the latest 7 days.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
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
PR_LIMIT = 1000
RULE_SUITE_DAYS = 30
FOLLOW_UP_DAYS = 7
FOLLOW_UP_TYPES = ("fix", "revert")
LANDED_TRAILER = "Landed-by: land-pr.sh"
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
            str(PR_LIMIT),
            "--json",
            "number,title,body,url,mergedAt,headRefOid,baseRefName,mergeCommit,labels,mergedBy",
        ]
    )
    if not isinstance(prs, list):
        raise ReadError("pr list: expected a list")
    if len(prs) >= PR_LIMIT:
        # GitHub search stops at 1000 results, so paging cannot get past it.
        raise ReadError(f"pr list: hit the {PR_LIMIT}-PR search limit; narrow --since")
    return sorted(
        (p for p in prs if isinstance(p, dict) and p.get("mergedAt")),
        key=lambda p: p["mergedAt"],
    )


def read_rule_results(repo, base):
    """{merge sha: (result, actor)} from the base branch's rule evaluations.

    `result` is 'pass', 'bypass' and so on; `actor` is the suite's
    `actor_name`, or None.
    """
    ref = urllib.parse.quote(f"refs/heads/{base}", safe="")
    return index_rule_suites(
        land._pages(
            [
                f"repos/{repo}/rulesets/rule-suites?ref={ref}&time_period=month&per_page=100"
            ]
        )
    )


def index_rule_suites(pages, repo=None):
    """{after_sha: (result, actor_name)} from pages of rule suites.

    With `repo`, a suite naming a different repository is an error, so a file
    saved from the wrong repo cannot pass as this one's.
    """
    results: dict = {}
    for page in pages:
        if not isinstance(page, list):
            raise ReadError("rule suites: a page is not a list")
        for suite in page:
            if not (isinstance(suite, dict) and suite.get("after_sha")):
                continue
            name = suite.get("repository_name")
            if repo and name and name != repo.split("/")[1]:
                raise ReadError(f"rule suites: a suite is for {name}, not {repo}")
            results.setdefault(
                suite["after_sha"], (suite.get("result"), suite.get("actor_name"))
            )
    return results


def load_rule_suites(path, repo):
    """Index a saved `gh api …/rule-suites` response: one page, or a
    `--paginate --slurp` list of pages."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ReadError(f"cannot read --rule-suites {path}: {exc}") from exc
    if not isinstance(data, list):
        raise ReadError(f"--rule-suites {path}: expected a JSON list")
    pages = data if data and all(isinstance(x, list) for x in data) else [data]
    return index_rule_suites(pages, repo)


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


def review(repo, prs, since, owner, suites=None):
    if dt.date.today() - dt.date.fromisoformat(since) > dt.timedelta(
        days=RULE_SUITE_DAYS
    ):
        print(
            f"note: rule suites cover about the last {RULE_SUITE_DAYS} days; "
            "an older PR lacking co-review reads as unknown"
        )
    lacking, rule_cache, unreadable = [], {}, set()
    for p in prs:
        head = p.get("headRefOid") or ""
        merged_by = (p.get("mergedBy") or {}).get("login") or "unknown"
        try:
            state = land.read_statuses(repo, head).get(land.CO_REVIEW)
        except ReadError as exc:
            lacking.append(
                (p, "unknown", f"cannot read statuses: {exc}; merged by {merged_by}")
            )
            continue
        if state == "success":
            continue
        base = p.get("baseRefName") or ""
        if suites is not None:
            # A saved file is keyed by merge sha, so it serves every base.
            rule_cache[base] = suites
        elif base not in rule_cache:
            try:
                rule_cache[base] = read_rule_results(repo, base)
            except ReadError as exc:
                rule_cache[base] = exc
        rules = rule_cache[base]
        merge_sha = (p.get("mergeCommit") or {}).get("oid") or ""
        status_note = f"`co-review` {state or 'missing'} on {head[:7]}"
        if isinstance(rules, ReadError):
            if base not in unreadable:
                unreadable.add(base)
                hint = (
                    " (needs Administration read)"
                    if getattr(rules, "not_found", False)
                    else ""
                )
                print(
                    f"note: cannot read rule suites for {base or '?'}{hint}, "
                    f"so its gaps read unknown: {rules}"
                )
            lacking.append(
                (
                    p,
                    "unknown",
                    f"{status_note}; merged by {merged_by}; rule suites unreadable",
                )
            )
            continue
        result, actor = rules.get(merge_sha, (None, None))
        if result == "bypass":
            # No actor on the suite: name mergedBy, and say it was inferred.
            who = actor or f"{merged_by} (from mergedBy)"
            actor = actor or merged_by
            kind = "owner" if actor.lower() == owner.lower() else "bypass"
            lacking.append((p, kind, f"{status_note}; bypassed by {who}"))
            continue
        kind = "ungated" if result == "pass" else "unknown"
        lacking.append((p, kind, f"{status_note}; merged by {merged_by}"))
    for p, kind, note in lacking:
        print(f"#{p['number']} {kind:8} {note} — {p.get('title', '')}")
    counts = {
        k: sum(1 for _, kind, _ in lacking if kind == k)
        for k in ("owner", "bypass", "ungated", "unknown")
    }
    print(
        f"AUDIT: review merged={len(prs)} lacking={len(lacking)} "
        f"owner={counts['owner']} bypass={counts['bypass']} "
        f"ungated={counts['ungated']} unknown={counts['unknown']}"
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


# --- landed -----------------------------------------------------------------


def read_message(repo, sha):
    return land.gh(["api", f"repos/{repo}/commits/{sha}", "--jq", ".commit.message"])


def landed(repo, prs):
    count, followed, unlabeled, unknown = 0, 0, 0, 0
    for p in prs:
        sha = (p.get("mergeCommit") or {}).get("oid") or ""
        if not sha:
            continue
        try:
            message = read_message(repo, sha)
        except ReadError as exc:
            unknown += 1
            print(f"#{p['number']} unknown  cannot read {sha[:7]}: {exc}")
            continue
        if LANDED_TRAILER not in [line.strip() for line in message.splitlines()]:
            continue
        count += 1
        print(f"#{p['number']} landed {p['mergedAt'][:10]} — {p.get('title', '')}")
        labels = {(lb or {}).get("name") for lb in p.get("labels") or []}
        if land.LANDED_LABEL not in labels:
            unlabeled += 1
            print(f"    lacks the `{land.LANDED_LABEL}` label")
        fixes = follow_ups(p, prs)
        followed += bool(fixes)
        for q in fixes:
            print(
                f"    follow-up #{q['number']} merged {q['mergedAt'][:10]}: {q.get('title', '')}"
            )
    print(
        f"AUDIT: landed landed={count} followed-up={followed} "
        f"unlabeled={unlabeled} unknown={unknown}"
    )


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--since", required=True, help="YYYY-MM-DD, merged on or after")
    p.add_argument("--mode", required=True, choices=("review", "shadow", "landed"))
    p.add_argument("--policy", help="shadow only: a candidate merge-policy.yml")
    p.add_argument(
        "--owner",
        help="review only: the login whose bypass is expected (default: the repo owner)",
    )
    p.add_argument(
        "--rule-suites",
        help="review only: a saved rule-suites response to read instead of the API",
    )
    args = p.parse_args(argv)
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    try:
        dt.date.fromisoformat(args.since)
    except ValueError:
        p.error("--since must be YYYY-MM-DD")
    if args.policy and args.mode != "shadow":
        p.error("--policy applies to --mode shadow only")
    if args.owner and args.mode != "review":
        p.error("--owner applies to --mode review only")
    if args.rule_suites and args.mode != "review":
        p.error("--rule-suites applies to --mode review only")
    suites = None
    if args.rule_suites:
        try:
            suites = load_rule_suites(args.rule_suites, args.repo)
        except ReadError as exc:
            p.error(str(exc))
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
        review(
            args.repo, prs, args.since, args.owner or args.repo.split("/")[0], suites
        )
    elif args.mode == "landed":
        landed(args.repo, prs)
    else:
        shadow(args.repo, prs, policy_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
