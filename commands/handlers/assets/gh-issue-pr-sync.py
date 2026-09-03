#!/usr/bin/env python3
"""Move an issue's `status:` rung to follow the lifecycle of its pull request.

On GitHub the open PR *is* the `needs_review` state — there is no separate
review field — so two transitions have to happen when a PR changes shape:

- a PR that becomes ready for review moves its issue `3_started` ->
  `4_needs_review`. Two events mean that: `ready_for_review` (a draft was
  marked ready) and `opened` on a PR that was NOT opened as a draft. They are
  one transition, so they share a row in TRANSITIONS.
- a PR closed WITHOUT merging moves it back `4_needs_review` -> `3_started`

Without the second one an issue sits in `needs_review` forever with no open PR
to review, which is indistinguishable from work waiting on a human.

**A draft PR is not `needs_review`, and this file cannot tell.** The `opened`
event fires for a draft too, and nothing in the arguments here says which it
was — the caller has that fact, so the caller carries the gate. The workflow
declines to invoke this script at all for a draft; see the `if:` in
.github/workflows/gh-issue-pr-sync.yml. Passing `--event opened` for a draft PR
would move it, and correctly so: the caller asserted it is not one.

A merged PR is deliberately NOT a transition here: `Closes #<n>` in the PR body
makes GitHub close the issue itself, and closure IS completion under this
schema (a closed issue carries no `status:`/`auto:` rung — see labels.yml). So
the merged case is a no-op, not a write.

This is the BACKSTOP channel. The agent that opens the PR is meant to set the
rung in the same step; this exists to catch PRs opened outside that loop, and
runs from .github/workflows/gh-issue-pr-sync.yml on a GitHub Actions runner —
a third credentialed channel alongside local `gh` and the cloud-routine MCP
connector. It needs `permissions: issues: write`, because the write is a PATCH
against the issues API rather than anything the default `contents: read` token
can reach.

Every decision it makes is gated on the issue's CURRENT rung, and a mismatch is
a no-op rather than a correction. That is the whole safety story: this runs
unattended on every PR in the repo, including PRs that have nothing to do with
the task loop, so it must never be the thing that invents a state. It writes
only when the issue is already sitting exactly where the transition expects it.

Reuse, not reimplementation, on both halves of the job:

- the branch parser is `gh-issue-claim.py`'s `parse_issue_number()`, which
  already accepts any prefix (`claude/task-142`, `bestdan/task-142`, bare
  `task-142`) and rejects near-misses. A second copy here would drift from the
  branch names the claim election actually creates.
- the write is `gh-issue-state.py`'s validate-then-one-PATCH path. `gh issue
  edit --add-label` is not atomic (8 measured requests) and a raw REST write
  auto-creates an unknown label instead of rejecting it, so nothing may write a
  label set except through that helper.

Both live in dashed filenames, which are not legal module names, so they are
loaded by path.

Usage:
  python3 gh-issue-pr-sync.py --repo owner/name --branch bestdan/task-142 \
      --event ready_for_review --apply

  python3 gh-issue-pr-sync.py --repo owner/name --branch claude/task-142 \
      --event closed --merged --apply

Without --apply it decides and prints what it would write, performing the read
but no write. A no-op — any branch that is not a task branch, a merged PR, a
closed issue, an unexpected current rung — exits 0 and says why: this runs on
every PR in the repo, so "did nothing" is the common case, not a failure.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent


def _load(filename, name):
    """Import a sibling asset whose filename is not a legal module name."""
    spec = importlib.util.spec_from_file_location(name, ASSETS / filename)
    assert spec is not None and spec.loader is not None, filename
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gh_issue_claim = _load("gh-issue-claim.py", "gh_issue_claim")
gh_issue_state = _load("gh-issue-state.py", "gh_issue_state")

STARTED = "status:3_started"
NEEDS_REVIEW = "status:4_needs_review"

# (expected current rung, rung to write). A PR becoming ready and a PR closing
# unmerged are exact inverses, which is why the table reads as one pair rather
# than two rules: whatever one does, the other undoes. `opened` and
# `ready_for_review` are the same transition reached by two events — a PR opened
# straight to non-draft never emits `ready_for_review`, and a PR opened as a
# draft emits it later — so both map to the same row.
TRANSITIONS = {
    "opened": (STARTED, NEEDS_REVIEW),
    "ready_for_review": (STARTED, NEEDS_REVIEW),
    "closed": (NEEDS_REVIEW, STARTED),
}


def status_rung(labels):
    """The issue's single `status:` label, or None if it does not carry exactly one.

    Two rungs is as unusable as none: the transition cannot know which one it is
    replacing, and the invariant it would have to restore is a human's call. So
    both cases return None, which the caller reads as "not where I expected" and
    skips.
    """
    rungs = [label for label in labels if label.startswith("status:")]
    return rungs[0] if len(rungs) == 1 else None


def decide(event, merged, branch):
    """(issue number, (expected rung, target rung)) — or (None, reason) to skip.

    Everything decidable from the PR event alone happens here, before any
    network call, so the overwhelmingly common case — a PR on a branch that is
    not a task branch — costs zero requests.
    """
    if event == "closed" and merged:
        return None, "PR merged — `Closes #<n>` closes the issue, which IS completion"
    issue = gh_issue_claim.parse_issue_number(branch)
    if issue is None:
        return None, f"not a task branch: {branch}"
    return issue, TRANSITIONS[event]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument(
        "--branch", required=True, help="the PR's head branch (head.ref)"
    )
    parser.add_argument(
        "--event",
        required=True,
        choices=sorted(TRANSITIONS),
        help="the pull_request action that fired",
    )
    parser.add_argument(
        "--merged",
        action="store_true",
        help="the PR was merged (only meaningful with --event closed)",
    )
    parser.add_argument("--apply", action="store_true", help="send the PATCH")
    parser.add_argument(
        "--labels-file", type=Path, default=gh_issue_state.DEFAULT_LABELS_FILE
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    def report(skipped, **fields):
        result = {
            "repo": args.repo,
            "branch": args.branch,
            "skipped": skipped,
            **fields,
        }
        if args.as_json:
            print(json.dumps(result, indent=2))
        elif skipped:
            print(f"no-op: {skipped}")
        else:
            verb = "Wrote" if args.apply else "Would write"
            print(
                f"{verb} {args.repo}#{fields['issue']}: {', '.join(fields['labels'])}"
            )
            if fields.get("dropped"):
                print(f"Dropped (not in labels.yml): {', '.join(fields['dropped'])}")
        return 0

    issue, outcome = decide(args.event, args.merged, args.branch)
    if issue is None:
        return report(outcome)
    expected, target = outcome

    current, state = gh_issue_state.current_issue(args.repo, issue)
    if state == "closed":
        return report(f"{args.repo}#{issue} is closed", issue=issue)

    rung = status_rung(current)
    if rung != expected:
        return report(
            f"{args.repo}#{issue} is on {rung or 'no single status: rung'}, "
            f"not {expected}",
            issue=issue,
        )

    groups, colors = gh_issue_state.load_vocabulary(args.labels_file)
    vocabulary = gh_issue_state.expected_labels(groups, colors)
    # Carry every OTHER managed label through unchanged — the `auto:` rung the
    # invariant requires, plus prio:/est:. Anything in a managed namespace that
    # the vocabulary does not define is dropped here rather than echoed back,
    # which is what gh-issue-state.py would do to it anyway.
    managed = [target] + [
        label
        for label in current
        if label != rung
        and gh_issue_state.in_managed_namespace(label, set(groups))
        and label in vocabulary
    ]
    # Report those deletions. The full-set write is right to purge a `prio:urgent`
    # a human invented, but the deletion is invisible unless it is named — and
    # unattended, an Actions log is the only place anyone could ever see it.
    dropped = gh_issue_state.dropped_unrecognized(current, set(groups), vocabulary)

    try:
        gh_issue_state.validate(managed, vocabulary)
    except gh_issue_state.InvalidLabelSet as exc:
        print(f"refusing to write {args.repo}#{issue}: {exc}", file=sys.stderr)
        return 2

    preserved = gh_issue_state.preserve_unmanaged(current, set(groups))
    labels = managed + [label for label in preserved if label not in managed]
    if args.apply:
        gh_issue_state.patch_issue(args.repo, issue, labels)
    return report(
        None,
        issue=issue,
        labels=labels,
        dropped=dropped,
        applied=args.apply,
        target=target,
    )


if __name__ == "__main__":
    sys.exit(main())
