#!/usr/bin/env python3
"""Audit a repo's issues against the label invariants in labels.yml.

This is an **audit**, not a load-bearing repair. Every status write goes through
gh-issue-state.py's validate-then-one-PATCH path, which replaces the whole label
set in a single request — so a double-`status:` issue cannot arise on the happy
path. The drift this catches comes from somewhere else entirely: a human editing
labels in GitHub's web UI, which is a supported way to work with this board.

Three rules, matching the invariants labels.yml states:

1. An open issue carrying two or more vocabulary `status:` labels — keep the
   HIGHEST rung, drop the rest. The ladder is numbered (`0_untriaged` ..
   `4_needs_review`) precisely so "highest" is a fact rather than a judgment,
   and keeping it is the same forward-only doctrine `/reconcile-tasks` applies
   to Linear: a wrong read can leave an issue ahead of where it belongs, never
   retire live work.
2. An open issue missing a `status:` or an `auto:` rung — FLAG, never assign.
   Which rung it should carry is a human's call; guessing `0_untriaged` would
   quietly demote started work, and guessing anything else would invent state.
3. An issue closed although it was NEVER labelled `status:4_needs_review` —
   FLAG. Under this schema a merge IS completion, so a stray or mistaken
   `Closes #<n>` in an unrelated PR body closes an issue that never passed
   review, and nothing else in the loop would notice.

Rule 1 is the only rule that can write, and only with `--apply`. Rules 2 and 3
never write, at any flag combination.

**Scope is load-bearing for rule 2.** Every issue in a repo that is not part of
the task loop — a bug report a user filed, a dependency bot's issue — is missing
both rungs and is a rule-2 hit. Pass `--label` for each of the repo's configured
`gh-issue.labels` (`follow-up` and friends) to narrow the audit to task issues;
repeated flags AND together. Unscoped, rule 2 reports the whole repo and the
report says so rather than letting the noise read as drift.

Rule 3 costs one API call per closed issue, so `--limit` bounds it. Mind what the
window actually holds: `gh issue list` orders by CREATION date descending, not by
close date (measured 2026-09-03), so it is the most recently created issues, and a
long-lived issue closed yesterday can sit outside a small `--limit` and never be
audited. There is no close-date ordering to reach for: GitHub search has no
`sort:closed`. `--search "sort:updated-desc"` is the nearest proxy and does
compose with `--label` (measured), but `updated` moves on a comment after the
close, so it is not the same question — this script does not use it.

Rule 3 asks whether the label was EVER applied, across the issue's whole event
history, not whether it was applied before the last close. An issue reopened and
re-closed has a `4_needs_review` event somewhere behind it either way, and
scoping to the final close would re-flag every one of them. The rule exists to
find issues that never reached review at all.

Read paths, both measured against the live API on 2026-09-03:

- `repos/{owner}/{repo}/issues/{n}/events` carries the `labeled` stream rule 3
  needs. The task spec named the `timeline` endpoint; that returns the same
  `labeled` events plus `cross-referenced` and comment entries this rule has no
  use for, so the leaner one is used.
- It is read with `--paginate --slurp`, like gh-issue-ready.py's blocker read
  and for the same reason: a bare call returns one page, and an issue whose
  `4_needs_review` event fell past it would read as never-reviewed. `--slurp`
  wraps the pages in one array, because bare `--paginate` concatenates arrays
  into something that is not valid JSON.

Usage:
  python3 gh-issue-reconcile.py --repo owner/name
  python3 gh-issue-reconcile.py --repo owner/name --label follow-up --json
  python3 gh-issue-reconcile.py --repo owner/name --label follow-up --apply
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import (  # noqa: E402
    DEFAULT_LABELS_FILE,
    VocabularyError,
    expected_labels,
    load_vocabulary,
)


def _load(filename, name):
    """Import a sibling asset whose filename is not a legal module name."""
    spec = importlib.util.spec_from_file_location(name, ASSETS / filename)
    assert spec is not None and spec.loader is not None, filename
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gh_issue_state = _load("gh-issue-state.py", "gh_issue_state")

REVIEW_STATUS_VALUE = "4_needs_review"


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def review_label(groups):
    """Derive `status:4_needs_review` from the vocabulary instead of hardcoding it.

    A rename in labels.yml must fail loudly here rather than silently making
    rule 3 flag every closed issue in the repo.
    """
    if REVIEW_STATUS_VALUE not in groups.get("status", []):
        raise VocabularyError(
            f"labels.yml: status group has no `{REVIEW_STATUS_VALUE}` value"
        )
    return f"status:{REVIEW_STATUS_VALUE}"


def rung_rank(value):
    """The ladder position encoded in a status value's numeric prefix.

    `max()` over these is what rule 1 keeps. Parsing the prefix rather than
    trusting labels.yml's line order means a reordered file still ranks
    correctly, and a status value that lost its number fails loudly instead of
    ranking arbitrarily — which would silently pick the wrong rung to keep.
    """
    prefix = value.split("_", 1)[0]
    if not prefix.isdigit():
        raise VocabularyError(
            f"labels.yml: status value `{value}` has no numeric ladder prefix"
        )
    return int(prefix)


def status_rungs(labels, vocabulary):
    """The issue's in-vocabulary `status:` labels, highest rung last.

    Out-of-vocabulary names (`status:blocked`, invented in the web UI) are
    excluded deliberately: they have no ladder position, so they cannot be
    ranked, and the full-set write drops them anyway — gh-issue-state.py reports
    that as `dropped`.
    """
    rungs = [
        label for label in labels if label.startswith("status:") and label in vocabulary
    ]
    return sorted(rungs, key=lambda label: rung_rank(label.split(":", 1)[1]))


def list_issues(repo, state, limit, scope_labels, fields):
    args = ["issue", "list", "--repo", repo, "--state", state]
    for scope in scope_labels:
        args += ["--label", scope]
    args += ["--json", fields, "--limit", str(limit)]
    code, out, err = run_gh(args)
    if code != 0:
        raise SystemExit(
            f"gh issue list --state {state} failed for {repo}: "
            f"{err.strip() or out.strip()}"
        )
    return json.loads(out or "[]")


def label_names(issue):
    return [entry["name"] for entry in issue.get("labels", [])]


def ever_labelled(repo, issue, label):
    """True if `label` was applied to the issue at any point in its history."""
    code, out, err = run_gh(
        ["api", "--paginate", "--slurp", f"repos/{repo}/issues/{issue}/events"]
    )
    if code != 0:
        raise SystemExit(
            f"gh api events failed for {repo}#{issue}: {err.strip() or out.strip()}"
        )
    pages = json.loads(out or "[]")
    return any(
        event.get("event") == "labeled"
        and (event.get("label") or {}).get("name") == label
        for page in pages
        for event in page
    )


def repair_set(current, keep, groups, vocabulary):
    """The complete label set that leaves `keep` as the issue's only status rung.

    Every other managed label the vocabulary defines rides through unchanged —
    the `auto:` rung the invariant needs, plus prio:/est: — and everything
    outside the four namespaces is preserved, because the write replaces the
    whole set and would otherwise delete `follow-up` and anything a human added.
    """
    managed = [keep] + [
        label
        for label in current
        if label != keep
        and not label.startswith("status:")
        and gh_issue_state.in_managed_namespace(label, set(groups))
        and label in vocabulary
    ]
    gh_issue_state.validate(managed, vocabulary)
    preserved = gh_issue_state.preserve_unmanaged(current, set(groups))
    return managed + [label for label in preserved if label not in managed]


def compute(repo, labels_file, limit, scope_labels=(), apply=False):
    groups, colors = load_vocabulary(labels_file)
    vocabulary = expected_labels(groups, colors)
    review = review_label(groups)

    double_status = []
    missing_rung = []
    open_issues = list_issues(repo, "open", limit, scope_labels, "number,title,labels")
    for issue in open_issues:
        current = label_names(issue)
        rungs = status_rungs(current, vocabulary)

        if len(rungs) > 1:
            keep = rungs[-1]
            finding = {
                "number": issue["number"],
                "title": issue["title"],
                "rungs": rungs,
                "keep": keep,
                "drop": [label for label in rungs if label != keep],
                "applied": False,
                "refused": None,
            }
            try:
                labels = repair_set(current, keep, groups, vocabulary)
            except gh_issue_state.InvalidLabelSet as exc:
                # Rule 2's drift on the same issue — a missing `auto:` rung,
                # say. Repairing rule 1 would still leave an illegal set, and
                # inventing the missing rung is exactly what rule 2 forbids.
                finding["refused"] = str(exc)
            else:
                finding["labels"] = labels
                if apply:
                    gh_issue_state.patch_issue(repo, issue["number"], labels)
                    finding["applied"] = True
            double_status.append(finding)

        # Reported independently of rule 1: an issue can break both invariants,
        # and rule 1's repair deliberately does not fix this one.
        #
        # Membership in the vocabulary, not the bare `status:` prefix, is what
        # counts as carrying a rung — the same definition gh-issue-state.py's
        # validate() enforces, where a name outside labels.yml is rejected
        # outright. A prefix test would read a hand-typed `status:blocked` as a
        # rung and leave the issue invisible to every rule: rule 1 already skips
        # it, having no ladder position to rank it by. That is the exact input
        # this audit exists for.
        missing = []
        unrecognized = []
        for group in ("status", "auto"):
            prefixed = [label for label in current if label.startswith(f"{group}:")]
            if any(label in vocabulary for label in prefixed):
                continue
            missing.append(group)
            unrecognized += [label for label in prefixed if label not in vocabulary]
        if missing:
            missing_rung.append(
                {
                    "number": issue["number"],
                    "title": issue["title"],
                    "missing": missing,
                    # Named so the report does not say "no status: label" about
                    # an issue that visibly carries one.
                    "unrecognized": unrecognized,
                }
            )

    closed_unreviewed = []
    closed_issues = list_issues(
        repo, "closed", limit, scope_labels, "number,title,stateReason"
    )
    for issue in closed_issues:
        if ever_labelled(repo, issue["number"], review):
            continue
        closed_unreviewed.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                # Never a filter, always context. `state_reason` is not written
                # by anything in this handler, so `not_planned` here means a
                # human deliberately abandoned the issue and the finding can be
                # dismissed on sight — which is worth saying, and not worth
                # deciding on the reader's behalf.
                "state_reason": (issue.get("stateReason") or "").lower() or None,
            }
        )

    return {
        "repo": repo,
        "scope_labels": list(scope_labels),
        "limit": limit,
        "checked": {"open": len(open_issues), "closed": len(closed_issues)},
        "double_status": double_status,
        "missing_rung": missing_rung,
        "closed_unreviewed": closed_unreviewed,
        "applied": apply,
    }


def report(result):
    scope = (
        ", ".join(result["scope_labels"])
        if result["scope_labels"]
        else "whole repo (rule 2 will flag every issue outside the task loop)"
    )
    checked = result["checked"]
    print(
        f"{result['repo']}: checked {checked['open']} open and "
        f"{checked['closed']} closed issue(s), limit {result['limit']} — "
        f"scope: {scope}"
    )

    findings = result["double_status"]
    print(f"\nRule 1 — two or more status: rungs, keep the highest ({len(findings)}):")
    for finding in findings:
        dropped = ", ".join(finding["drop"])
        if finding["refused"]:
            outcome = f"NOT repaired — {finding['refused']}"
        elif finding["applied"]:
            outcome = f"repaired — kept {finding['keep']}, dropped {dropped}"
        else:
            outcome = f"would keep {finding['keep']}, drop {dropped}"
        print(f"  #{finding['number']} {finding['title']} — {outcome}")

    findings = result["missing_rung"]
    print(f"\nRule 2 — open issue missing a rung, FLAG only ({len(findings)}):")
    for finding in findings:
        missing = ", ".join(f"{group}:" for group in finding["missing"])
        line = f"  #{finding['number']} {finding['title']} — no {missing} label"
        if finding["unrecognized"]:
            names = ", ".join(finding["unrecognized"])
            line += f" (carries {names}, not in labels.yml)"
        print(line)

    findings = result["closed_unreviewed"]
    print(
        f"\nRule 3 — closed, never labelled {REVIEW_STATUS_VALUE}, FLAG only "
        f"({len(findings)}):"
    )
    for finding in findings:
        reason = finding["state_reason"] or "unset"
        print(
            f"  #{finding['number']} {finding['title']} — "
            f"possible stray closing keyword (state_reason: {reason})"
        )

    if not result["applied"]:
        print("\nnothing changed (dry-run) — pass --apply to repair rule 1")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="max issues to read per state (newest by creation date)",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        dest="scope_labels",
        metavar="LABEL",
        help=(
            "narrow the audit to issues carrying this label; repeatable (AND). "
            "Pass the repo's configured gh-issue.labels — unscoped, rule 2 "
            "flags every issue that is not part of the task loop"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="repair rule 1. Rules 2 and 3 are flag-only and never write",
    )
    parser.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = compute(
        args.repo, args.labels_file, args.limit, args.scope_labels, args.apply
    )
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
