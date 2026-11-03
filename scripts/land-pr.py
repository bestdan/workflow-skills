#!/usr/bin/env python3
"""Decide whether a pull request would land, and post the verdict on it.

Phase 0 of graduated merge autonomy runs the lander in shadow: this script
computes everything a landing needs, posts one `would land: yes|no` comment,
and never merges. `--dry-run` is required; without it the script exits 2
before any `gh` call. Run it as `scripts/land-pr.sh`.

A PR would land only when every one of these holds, and each one that does
not is a hold reason of its own:

1. The PR is open and not a draft.
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
4. The `co-review` commit status on the head is `success`.
5. Every check on the head is green (success, neutral or skipped), at least
   one has reported, and every check the base branch's rulesets require has
   reported. `co-review` is left to step 4.

Every read error is a hold with the reason, never a crash.

Usage:
  python3 scripts/land-pr.py <pr> --repo <owner/name> --dry-run

Prints `COMMENT: <created|updated|unchanged> <url>` (or `COMMENT: error
reason=...`), then a final `LAND: would-land` or `LAND: hold reasons=<r1; r2>`
line. Exit 0 for either verdict, 1 when the comment could not be posted, and 2
for a usage error or a run without `--dry-run`.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import importlib.util
import json
import subprocess
import sys
import tempfile
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

MARKER = "<!-- land-pr v1 -->"
POLICY_PATH = ".github/merge-policy.yml"
CO_REVIEW = "co-review"
GREEN_CONCLUSIONS = ("success", "neutral", "skipped")


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
    """(would_land, reasons, head) for one PR; reasons explain either verdict."""
    try:
        data = read_pr(repo, pr)
    except ReadError as exc:
        return False, [f"cannot read the PR: {exc}"], None
    head, base = data["headRefOid"], data.get("baseRefName") or ""
    holds = []
    if data.get("state") != "OPEN":
        holds.append(f"PR is {str(data.get('state')).lower()}")
    if data.get("isDraft"):
        holds.append("PR is a draft")

    consult_bar = tier_mod.DEFAULT_CONSULT_BAR
    tier_reasons = []
    try:
        files = read_files(repo, pr)
        policy_text = read_policy(repo, base)
    except ReadError as exc:
        holds.append(f"tier not computed: {exc}")
    else:
        agent_land, land_holds = read_agent_land(repo)
        holds.extend(land_holds)
        verdict, tier_reasons, policy = tier(data, files, policy_text, agent_land)
        if verdict != "auto":
            holds.extend(f"tier human: {r}" for r in tier_reasons)
        if policy is not None:
            consult_bar = policy["consult_bar"]

    try:
        record = evidence_mod.read_evidence(repo, pr, head)
    except (evidence_mod.stamp.StampError, ValueError) as exc:
        holds.append(f"evidence unreadable: {exc}")
    else:
        if record is None:
            holds.append(f"no co-review evidence for head {head[:7]}")
        else:
            holds.extend(evidence_holds(record, consult_bar))

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
        return False, holds, head
    return (
        True,
        [*tier_reasons, f"`co-review` success on {head[:7]}", "checks green"],
        head,
    )


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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("pr", type=int)
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--dry-run", action="store_true", help="required in Phase 0")
    args = p.parse_args(argv)
    if "/" not in args.repo:
        p.error("--repo must be owner/name")
    if not args.dry_run:
        print("land-pr: landing not enabled; pass --dry-run", file=sys.stderr)
        return 2

    would_land, reasons, head = judge(args.repo, args.pr)
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
    if would_land:
        print("LAND: would-land")
    else:
        print("LAND: hold reasons=" + "; ".join(" ".join(r.split()) for r in reasons))
    return code


if __name__ == "__main__":
    sys.exit(main())
