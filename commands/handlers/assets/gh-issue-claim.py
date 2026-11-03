#!/usr/bin/env python3
"""Deterministic primitives for the gh-issue claim lifecycle.

Two sessions can race to claim the same issue, and the pieces that decide who
wins — and how the loser finds out — have to be identical no matter which
session runs them, so they live here as code rather than as prose two
sessions could each interpret slightly differently:

- `branch-name` / `issue-number` compute and parse the deterministic
  claim-lock branch name. Both racers must derive the SAME name from the
  issue number alone (never the title, which can be edited or read at
  different times by each session), or the ref race below has nothing to
  collide on.
- `acquire` performs the create-only ref push that IS the election, and its
  exit code is a contract: 0 (won), 3 (lost the race), and 4 (indeterminate —
  neither won nor lost) mean different things to the caller, and the caller
  branches on the number, not on parsing prose. `acquire-ref` is the same
  election with the branch name passed in directly instead of derived from an
  issue number — a tracker-neutral entry point, so `claim-lock.md` and any
  caller with its own branch-naming rule share this one implementation instead
  of hand-walking the POST/422 dispatch a second time.
- `find-task-refs` is the cross-prefix read pre-flight cannot do with its own
  `<branch>`: it lists every claim-lock ref for an issue under ANY prefix, so a
  session whose `branch_prefix` disagrees with a sibling's can SEE the other
  ref. It is a detector, not an election — a read before a create cannot make
  two differently-named refs exclusive, so two sessions that both scan before
  either creates still both succeed. What prevents that is resolving the prefix
  from one revision (`skills/deliver-task/SKILL.md` step 1); this only catches
  the collision once a ref exists. Its exit code separates "none found" (1) from
  "the remote did not answer" (4), because `git ls-remote` prints nothing in
  both cases and reading the second as the first claims into that race.
- `wip` counts in-flight work with one server-side query and reports the
  remaining batch ceiling as `slack`, so a caller cannot under-count by
  missing a label spelling, over-cost by issuing two calls, or hand a batch
  dispatch a negative ceiling by doing the subtraction in prose.
- `release` deletes a lock ref this session created, on bail.
- `elect` decides the comment-token election (`claim-lock.md` "Fallback"), and
  `title-prs` is pre-flight's open-PR-by-title check. Both read text anyone can
  write on a public repo — comment bodies and PR titles — so they do the reading
  here and print only ids, numbers and URLs. A stranger's comment or title never
  reaches the model that runs the claim.

Usage:
  python3 gh-issue-claim.py branch-name --issue 142
  python3 gh-issue-claim.py branch-name --issue 142 --prefix bestdan/
  python3 gh-issue-claim.py issue-number --branch bestdan/task-142
  python3 gh-issue-claim.py find-task-refs --issue 142
  python3 gh-issue-claim.py find-task-refs --issue 142 --remote upstream
  python3 gh-issue-claim.py wip --repo owner/name --json
  python3 gh-issue-claim.py acquire --repo owner/name --issue 142 --base-sha <sha>
  python3 gh-issue-claim.py acquire-ref --repo owner/name --branch task/PLAT-142 --base-sha <sha>
  python3 gh-issue-claim.py release --repo owner/name --issue 142
  python3 gh-issue-claim.py elect --repo owner/name --issue 142 \\
      --since 2026-11-01T12:00:00Z --comment-id 123456789
  python3 gh-issue-claim.py title-prs --issue 142 [--repo owner/name]
  python3 gh-issue-claim.py stale-claims --repo owner/name [--prefix bestdan/] \\
      [--hours 24] [--apply] [--json]

`stale-claims` sweeps claims a dead session stranded (assigned, `status:3_started`,
no PR). Dry run by default; `--apply` releases each dead claim in the handler's Bail
order. Pushed work is never released. Exit code is the contract: 0 ran and nothing is
stuck (every claim live, or every dead one released under `--apply`), 1 dead claims
found on a dry run or `stranded-with-work` claims exist (reported, never touched),
4 a `gh` read or write failed. A failure is never read as dead: that candidate gets no
verdict, and 4 wins over 1.
"""

import argparse
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import (  # noqa: E402
    DEFAULT_LABELS_FILE,
    VocabularyError,
    load_vocabulary,
)


def _load(filename, name):
    """Import a sibling asset whose filename is not a legal module name."""
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, filename
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gh_issue_trust = _load("gh-issue-trust.py", "gh_issue_trust")

STARTED_STATUS_VALUE = "3_started"
NEEDS_REVIEW_STATUS_VALUE = "4_needs_review"

BRANCH_SEGMENT_RE = re.compile(r"^task-(\d+)$")


def run_gh(args, stdin=None):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin)
    return proc.returncode, proc.stdout, proc.stderr


def run_git(args):
    """Run `git` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class ProbeFailed(Exception):
    """`git ls-remote` did not answer, so an empty result means nothing."""


def find_task_refs(issue, remote="origin"):
    """Branch names on `remote` that are claim-lock refs for `issue`, any prefix.

    Pre-flight probes its own `<branch>` before claiming, which by construction
    cannot see a sibling session working the same issue under a DIFFERENT
    `branch_prefix` — both sessions then acquire a ref the other cannot see and
    both conclude they won. This is the read that catches that, and it lives
    here for the same reason the rest of this module does: two racing sessions
    must perform it identically, and the shell pipeline it replaces shipped
    four separate defects while nothing in the gate could lint it.

    Two properties the caller depends on:

    - **An empty list is not a failed query.** `git ls-remote` writes nothing to
      stdout when it exits non-zero (unreachable remote, auth failure, DNS), so
      a caller reading only the output takes an outage for "no ref exists, the
      issue is free" and claims into the race. A failed probe raises instead.
    - **Membership is decided by `parse_issue_number`, not by a pattern.** The
      probe and the parser therefore agree by construction rather than by two
      maintainers keeping a regex in step with a function: a ref this flow
      cannot trace back to an issue is not one the probe reports, and
      `pre-task-42`, `task-420` and `team-task-42` can never be read as issue
      42's lock.

    Names keep their full prefix (`bestdan/task-42`), which is what the caller
    compares against its own `<branch>`. Only the leading `refs/heads/` is
    stripped, and by length rather than by a greedy match, so a branch whose own
    name contains `refs/heads/` is reported intact instead of collapsing onto a
    different issue's ref.
    """
    code, out, err = run_git(["ls-remote", "--heads", remote])
    if code != 0:
        raise ProbeFailed(
            err.strip() or out.strip() or f"git ls-remote {remote} failed"
        )
    prefix = "refs/heads/"
    names = []
    for line in out.splitlines():
        _sha, _tab, ref = line.partition("\t")
        if not ref.startswith(prefix):
            continue
        name = ref[len(prefix) :]
        if parse_issue_number(name) == issue:
            names.append(name)
    return names


def branch_name(issue, prefix=""):
    """The work branch IS the claim-lock ref: `<prefix>task-<issue>`.

    Derived from the issue number rather than the title so two racing
    sessions, which may read the title at different times, still compute the
    same name. `prefix` is used verbatim — a caller that wants a `/`
    separator includes one; nothing here appends or strips one, because a
    cloud routine's `claude/` prefix and a local session's configured prefix
    are both legal without this function editorializing about either.
    """
    return f"{prefix}task-{issue}"


def parse_issue_number(branch):
    """The issue number encoded in a claim-lock branch name, or None.

    Accepts ANY prefix — a cloud routine pushes `claude/task-142`, a local
    session pushes its configured `bestdan/task-142`, and a bare `task-142`
    is also legal — by taking only the segment after the last `/` and
    requiring it to match `task-<digits>` exactly. `task-142-fixup` and
    `mytask-142` are not task branches: the former has trailing text after
    the digits, the latter has no `/` boundary in front of `task-`.
    """
    segment = branch.rsplit("/", 1)[-1]
    match = BRANCH_SEGMENT_RE.match(segment)
    return int(match.group(1)) if match else None


def in_flight_labels(groups):
    """The two status rungs that count as in-flight, derived from labels.yml.

    Both rungs count because the WIP limit bounds the human review queue: an
    issue with an open PR awaiting review is still occupying that queue, not
    done with it. Raises VocabularyError if either value is missing, so a
    rename in labels.yml fails loudly instead of the search silently matching
    zero issues.
    """
    status_values = groups.get("status", [])
    missing = [
        value
        for value in (STARTED_STATUS_VALUE, NEEDS_REVIEW_STATUS_VALUE)
        if value not in status_values
    ]
    if missing:
        raise VocabularyError(
            "labels.yml: status group is missing value(s): " + ", ".join(missing)
        )
    return [f"status:{STARTED_STATUS_VALUE}", f"status:{NEEDS_REVIEW_STATUS_VALUE}"]


def count_wip(repo, labels_file, limit):
    """This caller's in-flight issues: one query, label OR'd via a quoted list.

    `label:"a","b"` is a verified union on the live API (both term orders,
    including when one term matches nothing) — the same technique
    gh-issue-ready.py's scope narrowing relies on. Each label is quoted
    because the label names contain a colon, which is also the search
    syntax's own separator.
    """
    groups, _colors = load_vocabulary(labels_file)
    labels = in_flight_labels(groups)
    quoted = ",".join(f'"{label}"' for label in labels)
    search = f"assignee:@me label:{quoted}"
    code, out, err = run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            search,
            "--limit",
            str(limit),
            "--json",
            "number,labels",
        ]
    )
    if code != 0:
        raise SystemExit(
            f"gh issue list failed for {repo}: {err.strip() or out.strip()}"
        )
    issues = json.loads(out or "[]")
    return [{"number": i["number"]} for i in issues]


def cmd_branch_name(args):
    print(branch_name(args.issue, args.prefix))
    return 0


def cmd_issue_number(args):
    number = parse_issue_number(args.branch)
    if number is None:
        print(f"not a task branch: {args.branch}", file=sys.stderr)
        return 1
    print(number)
    return 0


def cmd_find_task_refs(args):
    """Exit code is the contract: 0 found, 1 none, 4 the probe could not answer.

    1 and 4 are deliberately different numbers. Both write nothing to **stdout**
    — 4 does explain itself on stderr — so a caller branching on output alone
    cannot tell them apart, and collapsing them treats an unreachable remote as a
    free issue, which is the one reading this probe exists to prevent.
    """
    try:
        refs = find_task_refs(args.issue, args.remote)
    except ProbeFailed as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 4
    if not refs:
        return 1
    for ref in refs:
        print(ref)
    return 0


def cmd_wip(args):
    # Fetch at least `wip_limit` rows, whatever --limit says. The API
    # truncates before anything local runs, so a page capped below the limit
    # reads as a count under it: `at_limit` comes back false and `slack`
    # positive on a board that is already over, and the batch dispatches into
    # the overflow. Asking for wip_limit rows is sufficient rather than
    # generous -- if that many come back, count >= wip_limit and slack is 0
    # whatever the true total is; if fewer, the count is exact.
    issues = count_wip(args.repo, args.labels_file, max(args.limit, args.wip_limit))
    count = len(issues)
    at_limit = count >= args.wip_limit
    # Clamped, because an over-limit board (a human claimed by hand, or the
    # limit was lowered under running work) yields a negative difference, and
    # a batch caller reads this straight into `min(N, slack)` as its dispatch
    # ceiling. A negative there is not "dispatch nothing" in every prose
    # reading, so the clamp lives here rather than in the caller.
    slack = max(0, args.wip_limit - count)
    result = {
        "repo": args.repo,
        "count": count,
        "limit": args.wip_limit,
        "at_limit": at_limit,
        "slack": slack,
        "issues": issues,
    }
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{args.repo}: {count} in flight (limit {args.wip_limit}, slack {slack})")
        for issue in issues:
            print(f"  #{issue['number']}")
    return 0


def acquire_ref(repo, branch, base_sha):
    """Create the claim-lock ref. Exit code IS the election result.

    A plain `git push` cannot substitute for this: both racers cut the
    branch from the same base sha, so they push the identical sha and the
    loser's push reports "Everything up-to-date" and exits 0 — both sessions
    would conclude they won (measured — see commands/handlers/claim-lock.md).
    The create-only REST call has no such blind spot: a second POST for a ref
    that exists fails outright regardless of the sha it names.

    Never mutates the issue itself — the caller writes assignee/labels only
    after this returns 0, so a lost or indeterminate race leaves the issue
    untouched. Shared by `acquire` (which derives `branch` from an issue
    number) and `acquire-ref` (which takes it directly), so the POST/422
    dispatch has exactly one implementation. Exit 3 means the response said
    the ref already exists; any other failure, including a 422 for an invalid
    request, is exit 4.
    """
    body = json.dumps({"ref": f"refs/heads/{branch}", "sha": base_sha})
    code, out, err = run_gh(
        ["api", "--method", "POST", f"repos/{repo}/git/refs", "--input", "-"],
        stdin=body,
    )
    if code == 0:
        print(branch)
        return 0

    # Only the "already exists" message is a lost race. GitHub also answers 422
    # for a malformed request (an abbreviated or unknown sha), and reading that
    # as a loss makes the caller skip an issue nobody holds.
    if "reference already exists" in f"{out}\n{err}".lower():
        print(f"lost the race for {branch}: ref already exists", file=sys.stderr)
        return 3

    response = "\n".join(part for part in (out.strip(), err.strip()) if part)
    print(f"could not acquire {branch}: {response or 'gh api failed'}", file=sys.stderr)
    return 4


FULL_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


def full_sha(value):
    """argparse type for `--base-sha`: a full 40-character hex sha, or exit 2.

    The REST call rejects an abbreviated sha with a 422, which is not a lost
    race; refusing it here means the request is never sent at all.
    """
    if not FULL_SHA_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"a full 40-character sha is required, got {value!r}"
        )
    return value


def cmd_acquire(args):
    branch = branch_name(args.issue, args.prefix)
    return acquire_ref(args.repo, branch, args.base_sha)


def cmd_acquire_ref(args):
    return acquire_ref(args.repo, args.branch, args.base_sha)


def delete_ref(repo, branch):
    """Delete a claim-lock ref. Returns (ok, message)."""
    code, out, err = run_gh(
        ["api", "--method", "DELETE", f"repos/{repo}/git/refs/heads/{branch}"]
    )
    if code != 0:
        return False, f"could not release {branch}: {err.strip() or out.strip()}"
    return True, ""


def cmd_release(args):
    branch = branch_name(args.issue, args.prefix)
    ok, message = delete_ref(args.repo, branch)
    if not ok:
        print(message, file=sys.stderr)
        return 1
    return 0


CLAIM_TOKEN_RE = re.compile(r"<!--\s*do-tasks-claim:[0-9A-Za-z]+\s*-->")
STARTED_LABEL = f"status:{STARTED_STATUS_VALUE}"


def iso_time(value):
    """argparse type for `--since`: an ISO 8601 time with a zone, or exit 2.

    GitHub stamps comments in UTC with a `Z`. A naive time cannot be compared
    with that without guessing the caller's zone, and a wrong guess moves
    `T_unclaimed` by hours, which either elects a stale orphan or drops a live
    competitor.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 time: {value!r}")
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"--since needs a zone, e.g. Z: {value!r}")
    return parsed


def _gh_json(args, what):
    code, out, err = run_gh(args)
    if code != 0:
        raise ProbeFailed(f"{what}: {err.strip() or out.strip() or 'gh failed'}")
    return json.loads(out or "null")


def elect(repo, issue, since, own_id):
    """Decide `claim-lock.md`'s comment-token election for this session.

    Returns a dict of ids and booleans only. Comment bodies are read here to
    find the claim marker and are never returned, because anyone can comment
    on a public repo's issue and this runs inside an unattended session.

    A token counts only if its comment was created at or after `since`
    (`T_unclaimed`) and written by a trusted author, by the same per-owner-type
    rule `gh-issue-trust.py` applies to issues. The trust filter is what stops
    a stranger's hand-typed marker from winning an election and leaving the
    issue claimed by nobody. The issue must also carry the claim markers
    (an assignee and `status:3_started`); without them no token is
    state-backed, including this session's own.

    `own_id` is the id of the comment this session posted. It is counted even
    if the re-list does not show it yet: this session knows it posted it, and
    only a lower id can beat it.
    """
    try:
        allowed = gh_issue_trust.trusted_associations(repo)
    except SystemExit as exc:  # its failed-read exit, which must stay a 4 here
        raise ProbeFailed(str(exc)) from None
    state = _gh_json(["api", f"repos/{repo}/issues/{issue}"], "issue read")
    labels = {label.get("name") for label in state.get("labels") or []}
    state_backed = bool(state.get("assignees")) and STARTED_LABEL in labels

    pages = _gh_json(
        ["api", "--paginate", "--slurp", f"repos/{repo}/issues/{issue}/comments"],
        "comment list",
    )
    competing = []
    for page in pages or []:
        for comment in page:
            if comment.get("id") == own_id:
                continue
            if not CLAIM_TOKEN_RE.search(comment.get("body") or ""):
                continue
            if comment.get("author_association") not in allowed:
                continue
            created = datetime.fromisoformat(
                comment["created_at"].replace("Z", "+00:00")
            )
            if created < since:
                continue
            competing.append(comment["id"])
    competing.sort()
    winner = min([own_id, *competing])
    return {
        "state_backed": state_backed,
        "own_comment_id": own_id,
        "winner_comment_id": winner,
        "competing_comment_ids": competing,
        "won": state_backed and winner == own_id,
    }


def cmd_elect(args):
    """Exit code is the contract: 0 won, 3 lost, 5 inconclusive, 4 no verdict.

    5 is `claim-lock.md` step 7's read-lag residual: no other trusted token is
    visible, which is what a win looks like and also what a racer's
    not-yet-listed comment looks like. The caller re-polls; a 5 that survives
    the re-polls is a win. 4 covers a failed read and an issue that does not
    carry the claim markers, since then no token is state-backed.
    """
    try:
        result = elect(args.repo, args.issue, args.since, args.comment_id)
    except ProbeFailed as exc:
        print(f"election could not read the issue: {exc}", file=sys.stderr)
        return 4
    print(json.dumps(result))
    if not result["state_backed"]:
        print(
            f"#{args.issue} carries no claim markers (assignee and {STARTED_LABEL})",
            file=sys.stderr,
        )
        return 4
    if not result["won"]:
        return 3
    if not result["competing_comment_ids"]:
        return 5
    return 0


def title_prs(issue, repo=None):
    """Open PRs whose title carries the literal `[#<issue>]` token.

    GitHub search tokenizes on punctuation, so `[#12]` also returns `[#120]`;
    the literal check is the post-filter. Titles are matched here and never
    returned: anyone can open a PR on a public repo, with any title.
    """
    args = [
        "pr",
        "list",
        "--state",
        "open",
        "--search",
        f"[#{issue}] in:title",
        "--json",
        "number,url,title",
        "--limit",
        "100",
    ]
    if repo:
        args += ["--repo", repo]
    prs = _gh_json(args, "gh pr list") or []
    token = f"[#{issue}]"
    return [
        {"number": pr["number"], "url": pr["url"]}
        for pr in prs
        if token in (pr.get("title") or "")
    ]


def cmd_title_prs(args):
    """Exit code is the contract: 0 found (one `<number> <url>` per line), 1 none, 4 no answer."""
    try:
        prs = title_prs(args.issue, args.repo)
    except ProbeFailed as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 4
    if not prs:
        return 1
    for pr in prs:
        print(f"{pr['number']} {pr['url']}")
    return 0


STATE_SCRIPT = Path(__file__).resolve().parent / "gh-issue-state.py"
READY_LABEL = "status:2_ready"
KEPT_RUNG_PREFIXES = ("auto:", "prio:", "est:")


def run_state(args):
    """Run gh-issue-state.py; (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(
        [sys.executable, str(STATE_SCRIPT), *args], capture_output=True, text=True
    )
    return proc.returncode, proc.stdout, proc.stderr


def positive_hours(value):
    """argparse type for `--hours`: a finite number above zero, or exit 2.

    Zero or less would judge a minutes-old claim dead and `--apply` would release
    it; nan and inf make `timedelta` raise outside the 0/1/4 exit contract.
    """
    try:
        hours = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from None
    if not math.isfinite(hours) or hours <= 0:
        raise argparse.ArgumentTypeError(f"must be a finite number above 0: {value}")
    return hours


def now():
    """The clock `stale-claims` ages claims against. Tests patch this."""
    return datetime.now(timezone.utc)


def _parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _pages(args, what):
    """Flatten a `--paginate --slurp` read into one list of items."""
    pages = _gh_json(["api", "--paginate", "--slurp", *args], what) or []
    return [item for page in pages for item in page]


def open_pr_by_head(repo, branch):
    prs = _gh_json(
        ["pr", "list", "--repo", repo, "--state", "open", "--head", branch]
        + ["--json", "number", "--limit", "100"],
        "gh pr list --head",
    )
    return [pr["number"] for pr in prs or []]


def claim_started_at(repo, issue):
    """When `status:3_started` was most recently applied, or None if no event shows it."""
    events = _pages([f"repos/{repo}/issues/{issue}/timeline?per_page=100"], "timeline")
    times = [
        _parse_time(e["created_at"])
        for e in events
        if e.get("event") == "labeled"
        and (e.get("label") or {}).get("name") == STARTED_LABEL
    ]
    return max(times) if times else None


def commits_ahead(repo, branch):
    """Commits the lock ref has beyond the default branch; None when the ref is absent."""
    code, out, err = run_gh(["api", f"repos/{repo}/git/ref/heads/{branch}"])
    if code != 0:
        # Only gh's own 404 marker means "no ref". A looser match would send a ref
        # holding commits down the no-ref release path, past the stranded-with-work
        # guard.
        if "HTTP 404" in err:
            return None
        raise ProbeFailed(f"ref read: {err.strip() or out.strip() or 'gh failed'}")
    default = _gh_json(["api", f"repos/{repo}"], "repo read")["default_branch"]
    compare = _gh_json(
        ["api", f"repos/{repo}/compare/{default}...{branch}"], "compare read"
    )
    return int(compare["ahead_by"])


def token_comment_ids(repo, issue):
    """Ids of trusted-author claim-token comments (the comment-election path's lock)."""
    try:
        allowed = gh_issue_trust.trusted_associations(repo)
    except SystemExit as exc:
        raise ProbeFailed(str(exc)) from None
    comments = _pages(
        [f"repos/{repo}/issues/{issue}/comments?per_page=100"], "comments"
    )
    return [
        c["id"]
        for c in comments
        if CLAIM_TOKEN_RE.search(c.get("body") or "")
        and c.get("author_association") in allowed
    ]


def judge_claim(repo, issue, prefix, hours, current):
    """Verdict dict for one candidate. Raises ProbeFailed: a failed read is no verdict."""
    n = issue["number"]
    branch = branch_name(n, prefix)
    base = {"issue": n, "branch": branch}
    prs = open_pr_by_head(repo, branch)
    if not prs:
        prs = [pr["number"] for pr in title_prs(n, repo)]
    if prs:
        return {**base, "verdict": "live", "reason": f"open PR #{prs[0]}"}
    started = claim_started_at(repo, n)
    if started is None:
        return {**base, "verdict": "live", "reason": "no labeled event; age unknown"}
    age = current - started
    if age < timedelta(hours=hours):
        age_h = age.total_seconds() / 3600
        reason = f"claimed {age_h:.1f}h ago, inside the {hours:g}h threshold"
        return {**base, "verdict": "live", "reason": reason}
    age_h = int(age.total_seconds() // 3600)
    ahead = commits_ahead(repo, branch)
    if ahead:
        reason = f"{branch} has {ahead} commit(s) ahead, no PR; claimed {age_h}h ago"
        return {**base, "verdict": "stranded-with-work", "reason": reason}
    where = "no lock ref (comment election)" if ahead is None else "no commits ahead"
    return {
        **base,
        "verdict": "dead",
        "reason": f"claimed {age_h}h ago, no open PR, {where}",
        "age_hours": age_h,
        "has_ref": ahead is not None,
    }


def _check(result, what):
    code, out, err = result
    if code != 0:
        raise ProbeFailed(f"{what}: {err.strip() or out.strip() or 'failed'}")


def release_claim(repo, issue, verdict, hours):
    """Release one dead claim; returns the steps taken. Raises ProbeFailed.

    The assignees go before the label, so a failure part-way leaves the issue on
    `status:3_started` — still a candidate — and the next sweep finishes the job.
    The reverse order would strand it ready-but-assigned, outside both this sweep
    and the `/do-tasks` candidate query. The error names the steps that landed.
    """
    steps: list[str] = []
    try:
        _release_steps(repo, issue, verdict, hours, steps)
    except ProbeFailed as exc:
        landed = ", ".join(steps) or "nothing"
        raise ProbeFailed(f"{exc} (landed before it: {landed})") from None
    return steps


def _release_steps(repo, issue, verdict, hours, steps):
    n = issue["number"]
    if verdict["has_ref"]:
        ok, message = delete_ref(repo, verdict["branch"])
        if not ok:
            raise ProbeFailed(message)
        steps.append(f"deleted {verdict['branch']}")
    else:
        for cid in token_comment_ids(repo, n):
            _check(
                run_gh(
                    ["api", "--method", "DELETE", f"repos/{repo}/issues/comments/{cid}"]
                ),
                f"delete token comment {cid}",
            )
            steps.append(f"deleted token comment {cid}")
    for assignee in issue.get("assignees") or []:
        edit = ["issue", "edit", str(n), "--repo", repo]
        _check(
            run_gh(edit + ["--remove-assignee", assignee["login"]]),
            "remove assignee",
        )
        steps.append(f"unassigned {assignee['login']}")
    kept = [
        label["name"]
        for label in issue.get("labels") or []
        if label["name"].startswith(KEPT_RUNG_PREFIXES)
    ]
    labels = ",".join([READY_LABEL, *kept])
    _check(
        run_state(["--repo", repo, "--issue", str(n), "--labels", labels, "--apply"]),
        "gh-issue-state",
    )
    steps.append(f"returned to {READY_LABEL}")
    body = (
        f"Released by stale-claims: claimed {verdict['age_hours']}h ago "
        f"(threshold {hours:g}h), no open PR, and no commits on the lock ref. "
        f"Returned to `{READY_LABEL}` so another session can take it."
    )
    _check(
        run_gh(["issue", "comment", str(n), "--repo", repo, "--body", body]),
        "comment",
    )
    steps.append("commented")


def cmd_stale_claims(args):
    """Exit code is the contract: 0 nothing stuck, 1 dead (dry run) or stranded, 4 gh failed."""
    path = f"repos/{args.repo}/issues?state=open&labels=status%3A{STARTED_STATUS_VALUE}"
    try:
        issues = _pages([path + "&per_page=100"], "issue list")
    except ProbeFailed as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 4
    # Unassigned `status:3_started` issues are candidates too: that is what a
    # release that failed after clearing the assignees leaves behind.
    candidates = [i for i in issues if "pull_request" not in i]
    current = now()
    results, failed, stuck = [], False, False
    for issue in candidates:
        try:
            verdict = judge_claim(args.repo, issue, args.prefix, args.hours, current)
            if verdict["verdict"] == "dead" and args.apply:
                verdict["released"] = release_claim(
                    args.repo, issue, verdict, args.hours
                )
        except ProbeFailed as exc:
            failed = True
            print(f"probe failed for #{issue['number']}: {exc}", file=sys.stderr)
            continue
        if verdict["verdict"] == "stranded-with-work" or (
            verdict["verdict"] == "dead" and not args.apply
        ):
            stuck = True
        results.append(verdict)
    if args.as_json:
        print(json.dumps(results, indent=2))
    else:
        for v in results:
            line = f"{v['verdict']} #{v['issue']} {v['reason']}"
            if v.get("released"):
                line += "; released: " + ", ".join(v["released"])
            print(line)
    if failed:
        return 4
    return 1 if stuck else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("branch-name", help="print the claim-lock branch name")
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--prefix", default="")
    p.set_defaults(func=cmd_branch_name)

    p = subparsers.add_parser(
        "issue-number", help="parse the issue number out of a branch name"
    )
    p.add_argument("--branch", required=True)
    p.set_defaults(func=cmd_issue_number)

    p = subparsers.add_parser(
        "find-task-refs",
        help="claim-lock refs for an issue under ANY prefix (0 found / 1 none / 4 no answer)",
    )
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--remote", default="origin")
    p.set_defaults(func=cmd_find_task_refs)

    p = subparsers.add_parser("wip", help="count this caller's in-flight issues")
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--wip-limit", type=int, default=3, dest="wip_limit")
    p.add_argument(
        "--limit", type=int, default=100, help="max issues the search returns"
    )
    p.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    p.add_argument("--json", action="store_true", dest="as_json")
    p.set_defaults(func=cmd_wip)

    p = subparsers.add_parser(
        "acquire", help="create the claim-lock ref (the election)"
    )
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--base-sha", required=True, dest="base_sha", type=full_sha)
    p.add_argument("--prefix", default="")
    p.set_defaults(func=cmd_acquire)

    p = subparsers.add_parser(
        "acquire-ref",
        help="create-only ref push for a tracker-neutral branch name (the election)",
    )
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--branch", required=True)
    p.add_argument("--base-sha", required=True, dest="base_sha", type=full_sha)
    p.set_defaults(func=cmd_acquire_ref)

    p = subparsers.add_parser(
        "release", help="delete a claim-lock ref this session created"
    )
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--prefix", default="")
    p.set_defaults(func=cmd_release)

    p = subparsers.add_parser(
        "elect",
        help="decide the comment-token election (0 won / 3 lost / 5 inconclusive / 4 no verdict)",
    )
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--since", required=True, type=iso_time, help="T_unclaimed")
    p.add_argument(
        "--comment-id",
        required=True,
        type=int,
        dest="comment_id",
        help="id of this session's claim comment",
    )
    p.set_defaults(func=cmd_elect)

    p = subparsers.add_parser(
        "title-prs",
        help="open PRs titled with [#<n>] (0 found / 1 none / 4 no answer)",
    )
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--repo", default=None, help="owner/name")
    p.set_defaults(func=cmd_title_prs)

    p = subparsers.add_parser(
        "stale-claims",
        help="find (--apply: release) claims a dead session stranded (0 clean / 1 stuck / 4 gh failed)",
    )
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--prefix", default="")
    p.add_argument("--hours", type=positive_hours, default=24)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.set_defaults(func=cmd_stale_claims)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
