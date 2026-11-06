#!/usr/bin/env python3
"""Decide whether a pull request would land, and land it when it may.

With `--dry-run` (Phase 0, shadow) this script computes everything a landing
needs, posts one `would land: yes|no` comment, and never merges. Without it
(Phase 1) it reads the `AGENT_LAND` repo variable first and holds unless it is
`on`, so off or unset never reaches `gh pr merge`; then it runs every dry-run
check and, when they all pass, squash-merges with `--match-head-commit` pinned
to the head it judged. Run it as `scripts/land-pr.sh`.

A PR would land only when every one of these holds, and each one that does
not is a hold reason of its own:

1. The PR is open, not a draft, and based on the repo's default branch. A
   stacked PR into another branch is judged under that branch's policy,
   which an agent can edit, so it holds until its parent has landed.
2. `merge-tier.py`, loaded from this script's own directory (the installed
   plugin, never the PR's working tree), classifies it `auto`. Its inputs are
   the files from REST `pulls/<n>/files`, the title, body and labels, the
   `.github/merge-policy.yml` read from the base branch (never the head), and
   the `AGENT_LAND` repo variable, where unset means off.
3. The co-review evidence record for the exact head (co-review-evidence.py)
   exists and shows no unresolved finding, no open verification item, no
   consult-decided technical call below the policy's `consult_bar` (default
   85), no preference call, no stack-reaching call, and no recommendation of
   another round. merge-tier.py reads no evidence, so these demotions live here.
4. The `co-review` commit status on the head is `success`. A status
   co-review-carry.py carried from the reviewed head to the round's fix commit
   counts, and the verdict comment names the head it was carried from.
5. Every check on the head is green (success, neutral or skipped), at least
   one has reported, and every check the base branch's rulesets require has
   reported. `co-review` is left to step 4.

Every read error is a hold with the reason, never a crash.

The squash commit's body lists those reasons and the evidence pointers, and
ends with the trailers `Landed-by: land-pr.sh` and `Co-review-head: <sha>`. A
head that moved after the check makes GitHub refuse the merge, which is a hold.
Any other failed `gh pr merge` is re-read, with about 30 seconds of waiting
between re-reads, while the PR is open at the judged head, its merge commit is
not yet reported, or the read fails, since GitHub can finish the merge after
the call fails: it is a landing only when the PR is merged at the judged head
and the merge commit carries both trailers. Otherwise it is a hold with the
original reason and a second one saying what the last re-read found, ending
`check by hand whether it merged` when that read could not rule a merge out.
A landing recovered that way prints
`MERGE: recovered after error: <failure>` and repeats the failure in the PR
comment, since neither the merge commit nor GitHub records it.
After a landing, the `landed-by-agent` label and a PR comment repeating the
record are best effort: their failure is reported and does not fail the run.

Usage:
  python3 scripts/land-pr.py <pr> --repo <owner/name> [--dry-run]

`--dry-run` prints `COMMENT: <created|updated|unchanged> <url>` (or `COMMENT:
error reason=...`), then a `TIER:` line, then a final `LAND: would-land` or
`LAND: hold reasons=<r1; r2>` line; exit 0 for either verdict, 1 when the
comment could not be posted. Without it, a landing prints an optional
`MERGE: recovered after error: <failure>` line, then `LABEL: ...` and
`COMMENT: ...` lines, then `LAND: landed sha=<merge sha>`, or
`LAND: landed sha=unknown reason=<why>` when the merge commit cannot be read
after a merge call that succeeded; a hold prints only
`LAND: hold reasons=...`; exit 0 for either. A usage error exits 2.

`TIER: auto`, `TIER: human reasons=<r1; r2>` or `TIER: unknown reason=<why>` is
the tier with AGENT_LAND taken as on: whether the PR is one that lands itself
once landing is switched on, which the hand-off records in the tracker. While
AGENT_LAND is off, the LAND line holds every PR on that alone.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec is not None and spec.loader is not None, f"cannot load {filename}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tier_mod = _load("merge_tier", "merge-tier.py")
evidence_mod = _load("co_review_evidence", "co-review-evidence.py")

# Seconds between re-reads of a failed merge call; tests replace `sleep`.
RE_READ_DELAYS = (2, 4, 8, 16)
sleep = time.sleep

MARKER = "<!-- land-pr v1 -->"
POLICY_PATH = ".github/merge-policy.yml"
CO_REVIEW = "co-review"
GREEN_CONCLUSIONS = ("success", "neutral", "skipped")
LANDED_MARKER = "<!-- land-pr landed v1 -->"
LANDED_LABEL = "landed-by-agent"
# What GitHub says when --match-head-commit no longer names the PR's head. If
# the wording ever changes, the refusal goes through landed_anyway() instead,
# which finds the PR unmerged and holds; only the reason text gets worse.
HEAD_MOVED_RE = re.compile(r"head branch was modified", re.I)


class ReadError(Exception):
    def __init__(self, message, not_found=False):
        super().__init__(message)
        self.not_found = not_found


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


def _evidence_gh(args, stdin=None):
    # Looked up at call time, so a test that replaces run_gh here also reaches
    # the evidence reader's reads through co-review-stamp.py.
    return run_gh(args, stdin)


setattr(evidence_mod.stamp, "run_gh", _evidence_gh)


def _target(args):
    """The endpoint a call names, for error text; flags like --paginate say nothing."""
    return next((a for a in args if a.startswith("repos/")), " ".join(args[:3]))


def gh(args, stdin=None):
    code, out, err = run_gh(args, stdin)
    if code != 0:
        detail = (err or out).strip().splitlines()
        raise ReadError(
            f"gh {_target(args)} failed (rc {code})"
            + (f": {detail[-1]}" if detail else ""),
            not_found="HTTP 404" in (err or ""),
        )
    return out


def gh_json(args):
    out = gh(args)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise ReadError(f"gh {_target(args)}: unreadable JSON: {exc}") from exc


def _pages(args):
    """Every item of a paginated REST list, flattened from `--slurp` pages."""
    pages = gh_json(["api", "--paginate", "--slurp", *args])
    if not isinstance(pages, list):
        raise ReadError(f"{args[0]}: expected a list of pages")
    return pages


# --- reads ------------------------------------------------------------------


def read_pr(repo, pr):
    data = gh_json(
        [
            "pr",
            "view",
            str(pr),
            "-R",
            repo,
            "--json",
            "number,url,state,isDraft,headRefOid,baseRefName,title,body,labels",
        ]
    )
    if not isinstance(data, dict) or not evidence_mod.stamp.SHA_RE.match(
        str(data.get("headRefOid", ""))
    ):
        raise ReadError("gh pr view: no headRefOid")
    return data


def read_default_branch(repo):
    data = gh_json(["repo", "view", repo, "--json", "defaultBranchRef"])
    name = (
        ((data or {}).get("defaultBranchRef") or {}).get("name")
        if isinstance(data, dict)
        else None
    )
    if not name:
        raise ReadError("gh repo view: no defaultBranchRef")
    return name


def read_files(repo, pr):
    files = []
    for page in _pages([f"repos/{repo}/pulls/{pr}/files"]):
        if not isinstance(page, list):
            raise ReadError("pulls files: a page is not a list")
        for f in page:
            if not isinstance(f, dict):
                raise ReadError("pulls files: an entry is not an object")
            entry = {
                "path": f.get("filename"),
                "additions": f.get("additions"),
                "deletions": f.get("deletions"),
                "status": f.get("status"),
            }
            if f.get("previous_filename"):
                entry["previous_path"] = f["previous_filename"]
            files.append(entry)
    return files


def read_policy(repo, base):
    ref = urllib.parse.quote(base, safe="")
    try:
        data = gh_json(["api", f"repos/{repo}/contents/{POLICY_PATH}?ref={ref}"])
    except ReadError as exc:
        if exc.not_found:
            raise ReadError(f"no {POLICY_PATH} on base `{base}`") from exc
        raise
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        raise ReadError(f"{POLICY_PATH} on `{base}`: not a base64 file")
    try:
        return base64.b64decode(data.get("content") or "").decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ReadError(f"{POLICY_PATH} on `{base}`: cannot decode: {exc}") from exc


def read_agent_land(repo):
    """('on'|'off', reasons): unset is off, anything but on/off is off too."""
    try:
        value = gh(
            ["api", f"repos/{repo}/actions/variables/AGENT_LAND", "--jq", ".value"]
        )
    except ReadError as exc:
        if exc.not_found:
            return "off", []
        return "off", [f"cannot read AGENT_LAND: {exc}"]
    value = value.strip().lower()
    if value in ("on", "off"):
        return value, []
    return "off", [f"AGENT_LAND is `{value}`, neither on nor off"]


def read_statuses(repo, head):
    # Paginated: a status past the first page would otherwise go unseen, and a
    # red one unseen is a would-land that should have been a hold.
    found = {}
    for page in _pages([f"repos/{repo}/commits/{head}/status"]):
        statuses = page.get("statuses") if isinstance(page, dict) else None
        if not isinstance(statuses, list):
            raise ReadError("commit status: a page has no statuses list")
        for s in statuses:
            if isinstance(s, dict):
                found[s.get("context")] = s.get("state")
    return found


def read_check_runs(repo, head):
    runs: list = []
    for page in _pages([f"repos/{repo}/commits/{head}/check-runs"]):
        page_runs = page.get("check_runs") if isinstance(page, dict) else None
        if not isinstance(page_runs, list):
            raise ReadError("check runs: a page has no check_runs list")
        runs.extend(r for r in page_runs if isinstance(r, dict))
    return runs


def read_required_checks(repo, base):
    ref = urllib.parse.quote(base, safe="")
    contexts = set()
    for page in _pages([f"repos/{repo}/rules/branches/{ref}"]):
        if not isinstance(page, list):
            raise ReadError("branch rules: a page is not a list")
        for rule in page:
            if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
                params = rule.get("parameters") or {}
                for check in params.get("required_status_checks") or []:
                    if isinstance(check, dict) and check.get("context"):
                        contexts.add(check["context"])
    return contexts


# --- judgments --------------------------------------------------------------


def tier(pr_data, files, policy_text, agent_land):
    facts = {
        "files": files,
        "title": pr_data.get("title") or "",
        "body": pr_data.get("body") or "",
        "labels": [
            label.get("name", "")
            for label in pr_data.get("labels") or []
            if isinstance(label, dict)
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        pr_file = Path(tmp) / "pr.json"
        policy_file = Path(tmp) / "merge-policy.yml"
        pr_file.write_text(json.dumps(facts))
        policy_file.write_text(policy_text)
        return tier_mod.evaluate(str(pr_file), str(policy_file), agent_land)


def evidence_holds(record, consult_bar):
    holds = []
    if record["unresolved_findings"]:
        holds.append(f"evidence: {record['unresolved_findings']} unresolved finding(s)")
    if record["verification_open"]:
        holds.append(
            f"evidence: {record['verification_open']} verification item(s) open"
        )
    for call in record["consult_calls"]:
        if call["kind"] == "preference":
            holds.append("evidence: a preference call")
        elif call["kind"] == "stack":
            holds.append("evidence: a stack-reaching call awaits the owner")
        elif call["confidence"] < consult_bar:
            holds.append(
                f"evidence: a consult call at {call['confidence']}% is below the {consult_bar}% bar"
            )
    if record["next_round"] == "another round":
        holds.append("evidence: co-review recommends another round")
    return holds


def check_holds(statuses, runs, required):
    """Hold reasons for the head's checks, `co-review` excluded."""
    states: dict = {}

    def note(name, state):
        rank = {"pass": 0, "pending": 1}
        if name not in states or rank.get(state, 2) > rank.get(states[name], 2):
            states[name] = state

    for run in runs:
        name = run.get("name") or "?"
        if run.get("status") != "completed":
            note(name, "pending")
        elif run.get("conclusion") in GREEN_CONCLUSIONS:
            note(name, "pass")
        else:
            note(name, run.get("conclusion") or "failed")
    for context, state in statuses.items():
        if context == CO_REVIEW:
            continue
        note(context, {"success": "pass"}.get(state, state or "failed"))

    holds = []
    if not states:
        holds.append("no checks have reported on the head")
    for name in sorted(required - {CO_REVIEW}):
        if name not in states:
            holds.append(f"required check `{name}` has not reported")
    for name, state in sorted(states.items()):
        if state == "pending":
            holds.append(f"check `{name}` is pending")
        elif state != "pass":
            holds.append(f"check `{name}` is {state}")
    return holds


def judge(repo, pr):
    """(would_land, reasons, head, tier_line) for one PR.

    reasons explain either verdict; tier_line is the `TIER:` line, the tier
    with AGENT_LAND taken as on.
    """
    try:
        data = read_pr(repo, pr)
    except ReadError as exc:
        reason = f"cannot read the PR: {exc}"
        return False, [reason], None, tier_line("unknown", [reason])
    head, base = data["headRefOid"], data.get("baseRefName") or ""
    holds = []
    if data.get("state") != "OPEN":
        holds.append(f"PR is {str(data.get('state')).lower()}")
    if data.get("isDraft"):
        holds.append("PR is a draft")
    try:
        default = read_default_branch(repo)
    except ReadError as exc:
        holds.append(f"cannot read the default branch: {exc}")
    else:
        if base != default:
            holds.append(f"base `{base}` is not the default branch `{default}`")

    consult_bar = tier_mod.DEFAULT_CONSULT_BAR
    tier_reasons = []
    try:
        files = read_files(repo, pr)
        policy_text = read_policy(repo, base)
    except ReadError as exc:
        holds.append(f"tier not computed: {exc}")
        line = tier_line("unknown", [f"tier not computed: {exc}"])
    else:
        line = tier_line(*tier(data, files, policy_text, "on")[:2])
        agent_land, land_holds = read_agent_land(repo)
        holds.extend(land_holds)
        verdict, tier_reasons, policy = tier(data, files, policy_text, agent_land)
        if verdict != "auto":
            holds.extend(f"tier human: {r}" for r in tier_reasons)
        if policy is not None:
            consult_bar = policy["consult_bar"]

    record = None
    evidence_note = None
    try:
        record = evidence_mod.read_evidence(repo, pr, head)
    except (evidence_mod.stamp.StampError, ValueError) as exc:
        holds.append(f"evidence unreadable: {exc}")
    else:
        if record is None:
            holds.append(f"no co-review evidence for head {head[:7]}")
        else:
            holds.extend(evidence_holds(record, consult_bar))
            evidence_note = (
                f"co-review evidence for {head[:7]}: no unresolved finding, no open"
                f" verification item, {len(record['consult_calls'])} consult call(s),"
                f" none below the {consult_bar}% bar"
            )

    try:
        statuses = read_statuses(repo, head)
    except ReadError as exc:
        holds.append(f"cannot read commit statuses: {exc}")
        statuses = None
    if statuses is not None:
        state = statuses.get(CO_REVIEW)
        if state != "success":
            holds.append(
                f"`co-review` status on {head[:7]} is {state or 'missing'}, not success"
            )
        try:
            runs = read_check_runs(repo, head)
            required = read_required_checks(repo, base)
        except ReadError as exc:
            holds.append(f"cannot read checks: {exc}")
        else:
            holds.extend(check_holds(statuses, runs, required))

    if holds:
        return False, holds, head, line
    # A carried record names the head the round actually read, so the comment
    # can say the success on this head is the round's verdict moved one fix
    # commit forward rather than a review of it.
    origin = (record or {}).get(evidence_mod.stamp.CARRIED_FROM)
    carried = f" (carried from {origin[:7]})" if origin else ""
    return (
        True,
        [
            *tier_reasons,
            evidence_note,
            f"`co-review` success on {head[:7]}{carried}",
            "checks green",
        ],
        head,
        line,
    )


def tier_line(verdict, reasons):
    flat = "; ".join(" ".join(r.split()) for r in reasons)
    if verdict == "auto":
        return "TIER: auto"
    if verdict == "human":
        return f"TIER: human reasons={flat}"
    return f"TIER: unknown reason={flat}"


# --- the comment ------------------------------------------------------------


def comment_body(would_land, reasons, head):
    at = f" on `{head[:7]}`" if head else ""
    lines = [
        MARKER,
        f"**land-pr** dry run{at}: would land: **{'yes' if would_land else 'no'}**",
        "",
    ]
    lines += [f"- {r}" for r in reasons]
    return "\n".join(lines)


def post_comment(repo, pr, body):
    """('created'|'updated'|'unchanged', url). Refreshes this account's own."""
    stamp = evidence_mod.stamp
    login = stamp.viewer_login()
    out = gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/issues/{pr}/comments",
            "--jq",
            ".[] | {id: .id, login: .user.login, body: .body, url: .html_url} | @json",
        ]
    )
    own = []
    for raw in out.splitlines():
        if not raw.strip():
            continue
        try:
            c = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ReadError(f"unreadable comment listing: {exc}") from exc
        if c.get("login") == login and (c.get("body") or "").lstrip().startswith(
            MARKER
        ):
            own.append(c)
    own.sort(key=lambda c: c["id"])
    if own and own[0].get("body") == body:
        return "unchanged", own[0].get("url") or ""
    if own:
        method, path, outcome = (
            "PATCH",
            f"repos/{repo}/issues/comments/{own[0]['id']}",
            "updated",
        )
    else:
        method, path, outcome = "POST", f"repos/{repo}/issues/{pr}/comments", "created"
    written = gh(
        ["api", "--method", method, path, "--input", "-"],
        stdin=json.dumps({"body": body}),
    )
    try:
        url = json.loads(written).get("html_url") or ""
    except (json.JSONDecodeError, AttributeError):
        url = ""
    return outcome, url


# --- the landing ------------------------------------------------------------


def audit_body(pr, reasons, head):
    """The squash commit's body: the record, then the two trailers last."""
    lines = [f"Landed by land-pr.sh: #{pr} judged on head {head}.", ""]
    lines += [f"- {r}" for r in reasons]
    lines += [
        "",
        f"Evidence: the co-review stamp comment on #{pr} and the `co-review`"
        f" commit status, both for {head}.",
        "",
        "Landed-by: land-pr.sh",
        f"Co-review-head: {head}",
    ]
    return "\n".join(lines) + "\n"


def merge(repo, pr, head, body):
    """Squash-merge pinned to `head`: (None, False) on success, else
    (hold reason, whether GitHub refused it because the head moved)."""
    with tempfile.TemporaryDirectory() as tmp:
        body_file = Path(tmp) / "audit.md"
        body_file.write_text(body)
        code, out, err = run_gh(
            [
                "pr",
                "merge",
                str(pr),
                "-R",
                repo,
                "--squash",
                "--match-head-commit",
                head,
                "--body-file",
                str(body_file),
            ]
        )
    if code == 0:
        return None, False
    detail = " ".join((err or out).split()) or f"rc {code}"
    if HEAD_MOVED_RE.search(detail):
        return f"head moved since the check on {head[:7]}: {detail}", True
    return f"gh pr merge failed: {detail}", False


def landed_anyway(repo, pr, head):
    """The merge sha when a failed `gh pr merge` merged on GitHub all the same.

    A client timeout or a dropped connection can fail the call after the server
    merged. It counts as this lander's landing only when the PR is MERGED, its
    head is the judged head, and the merge commit's message carries both of
    this run's trailers; a hand merge at the same head has neither. Returns
    (sha, None) for a landing, else (None, why not), where the why says when
    the PR may have merged after all and needs checking by hand.

    GitHub can finish the merge after the call failed, so a PR still open at
    the judged head, a merged PR whose merge commit is not yet reported, or a
    read error is read again after each of RE_READ_DELAYS (30 s of waiting in
    all). Any other PR, open at another head, merged or closed, is settled and
    not read again.
    """
    for delay in (0, *RE_READ_DELAYS):
        if delay:
            sleep(delay)
        sha, why, settled = _read_landing(repo, pr, head)
        if settled:
            break
    return sha, why


CHECK_BY_HAND = "check by hand whether it merged"


def _read_landing(repo, pr, head):
    """(sha or None, why not or None, settled): one read of whether this run's
    merge landed."""
    try:
        data = gh_json(
            [
                "pr",
                "view",
                str(pr),
                "-R",
                repo,
                "--json",
                "state,headRefOid,mergeCommit",
            ]
        )
        if not isinstance(data, dict):
            return None, f"re-read returned no PR data, so {CHECK_BY_HAND}", False
        state = data.get("state")
        at = str(data.get("headRefOid") or "")[:7] or "an unknown head"
        sha = (data.get("mergeCommit") or {}).get("oid")
        if state == "OPEN" and data.get("headRefOid") != head:
            # The merge was pinned to `head`, so a PR open at another head
            # cannot become this run's landing.
            return None, f"re-read: open at {at}, not the judged head", True
        if state == "OPEN":
            return None, "re-read: still open after the re-reads", False
        if state == "MERGED" and data.get("headRefOid") != head:
            # Pinned to `head` too, so another head rules this run out even
            # before GitHub reports the merge commit.
            return None, f"re-read: merged at {at}, not the judged head", True
        if state == "MERGED" and not sha:
            why = f"re-read: merged, but no merge commit reported, so {CHECK_BY_HAND}"
            return None, why, False
        if state != "MERGED":
            return None, f"re-read: {str(state).lower()} without merging", True
        message = gh(["api", f"repos/{repo}/commits/{sha}", "--jq", ".commit.message"])
    except ReadError as exc:
        return None, f"last re-read failed: {exc}, so {CHECK_BY_HAND}", False
    lines = [line.strip() for line in message.splitlines()]
    if "Landed-by: land-pr.sh" in lines and f"Co-review-head: {head}" in lines:
        return sha, None, True
    return None, f"re-read: merged as {str(sha)[:7]} without this run's trailers", True


MERGE_SHA_TRIES = 3
MERGE_SHA_WAIT = 2.0


def merge_sha(repo, pr):
    """(sha, None), or ("unknown", why) when it cannot be read: the merge
    itself succeeded, so the label and comment still follow."""
    why = "GitHub reported no merge commit"
    for attempt in range(MERGE_SHA_TRIES):
        if attempt:
            sleep(MERGE_SHA_WAIT)
        try:
            data = gh_json(["pr", "view", str(pr), "-R", repo, "--json", "mergeCommit"])
        except ReadError as exc:
            why = f"cannot read the merge commit: {exc}"
            continue
        if isinstance(data, dict) and (
            oid := (data.get("mergeCommit") or {}).get("oid")
        ):
            return oid, None
        why = "GitHub reported no merge commit"
    return "unknown", why


def add_label(repo, pr):
    """Best effort. `gh pr edit --add-label` refuses a label the repo lacks, so
    a missing one is created, without --force so an existing one is untouched,
    and the add is tried once more."""
    edit = ["pr", "edit", str(pr), "-R", repo, "--add-label", LANDED_LABEL]
    try:
        gh(edit)
    except ReadError as exc:
        if "not found" not in str(exc):
            return f"LABEL: error reason={exc}"
        # A create that fails because a concurrent run made it is fine: the
        # retry below is what decides. The create's own error is kept so a
        # retry that still fails names the cause, a missing permission say.
        code, out, err = run_gh(
            [
                "label",
                "create",
                LANDED_LABEL,
                "-R",
                repo,
                "--description",
                "Merged by land-pr.sh without the owner",
            ]
        )
        try:
            gh(edit)
        except ReadError as retry_exc:
            reason = str(retry_exc)
            detail = (err or out).strip().splitlines()
            if code != 0 and detail:
                reason += f"; label create: {detail[-1]}"
            return f"LABEL: error reason={reason}"
        if code != 0:
            return f"LABEL: added {LANDED_LABEL}"
        return f"LABEL: created and added {LANDED_LABEL}"
    return f"LABEL: added {LANDED_LABEL}"


def post_landed_comment(repo, pr, body):
    try:
        written = gh(
            [
                "api",
                "--method",
                "POST",
                f"repos/{repo}/issues/{pr}/comments",
                "--input",
                "-",
            ],
            stdin=json.dumps({"body": f"{LANDED_MARKER}\n```\n{body}```"}),
        )
    except ReadError as exc:
        return f"COMMENT: error reason={exc}"
    try:
        url = json.loads(written).get("html_url") or ""
    except (json.JSONDecodeError, AttributeError):
        url = ""
    return f"COMMENT: created {url}".rstrip()


def hold_line(reasons):
    return "LAND: hold reasons=" + "; ".join(" ".join(r.split()) for r in reasons)


def land_pr(repo, pr):
    """The real path. Exit 0 for landed or hold; label and comment are best effort."""
    # The switch is read first and on its own, so off or unset never reaches
    # `gh pr merge` whatever judge() would conclude.
    agent_land, land_holds = read_agent_land(repo)
    if agent_land != "on":
        print(hold_line(land_holds or ["AGENT_LAND is off or unset"]))
        return 0
    would_land, reasons, head, _ = judge(repo, pr)
    if not would_land or head is None:
        print(hold_line(reasons))
        return 0
    body = audit_body(pr, reasons, head)
    failure, head_moved = merge(repo, pr, head, body)
    record = ""
    sha_reason = None
    if failure:
        sha, why_not = (None, None) if head_moved else landed_anyway(repo, pr, head)
        if sha is None:
            print(hold_line([failure, why_not] if why_not else [failure]))
            return 0
        # The merge commit cannot carry the failure, so the comment records it.
        reason = " ".join(failure.split())
        record = f"Recovered after error: {reason}\n"
        print(f"MERGE: recovered after error: {reason}")
    else:
        sha, sha_reason = merge_sha(repo, pr)
    sha_note = f" ({' '.join(sha_reason.split())})" if sha_reason else ""
    print(add_label(repo, pr))
    print(
        post_landed_comment(
            repo, pr, f"Merge commit: {sha}{sha_note}\n{record}\n{body}"
        )
    )
    if sha_reason:
        print(f"LAND: landed sha={sha} reason={' '.join(sha_reason.split())}")
    else:
        print(f"LAND: landed sha={sha}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("pr", type=int)
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument(
        "--dry-run", action="store_true", help="judge and comment; never merge"
    )
    args = p.parse_args(argv)
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    if not args.dry_run:
        return land_pr(args.repo, args.pr)

    would_land, reasons, head, line = judge(args.repo, args.pr)
    code = 0
    try:
        outcome, url = post_comment(
            args.repo, args.pr, comment_body(would_land, reasons, head)
        )
    except (ReadError, evidence_mod.stamp.StampError) as exc:
        print(f"COMMENT: error reason={exc}")
        code = 1
    else:
        print(f"COMMENT: {outcome} {url}".rstrip())
    print(line)
    if would_land:
        print("LAND: would-land")
    else:
        print(hold_line(reasons))
    return code


if __name__ == "__main__":
    sys.exit(main())
