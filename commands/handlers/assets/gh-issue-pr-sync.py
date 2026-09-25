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

A merged PR is a third case, and it is NOT a rung transition. `Closes #<n>` in
the PR body makes GitHub close the issue itself, and closure IS completion under
this schema — but GitHub knows nothing about this vocabulary, so it flips the
state and leaves every label in place. "Done" is the ABSENCE of the `status:`
and `auto:` rungs (labels.yml), so the auto-close alone leaves the issue
violating the invariant, carrying a stale `auto:eligible` that is a live
instruction to a scheduler. The merged branch therefore STRIPS both rungs
rather than writing one, keeping `prio:`/`est:`, and never touches issue state.

That distinction is the whole of it: a merged PR must never write a rung, and
this does not — writing one would park a finished issue in a live column. It
removes them, which is what completion means here.

The merged branch asks GitHub which issues the PR closed
(`closingIssuesReferences`) rather than deriving one from the branch name. The
two usually agree, and where they differ the reference is the right answer: it
names what actually closed, including a PR that closes several issues or one
whose branch is not `<prefix>task-<n>` at all. It also self-verifies — an issue
the merge did not close comes back open, and an open issue is skipped.

A merged PR that only MENTIONS an issue — `Refs #<n>`, a partial PR — closes
nothing, and GitHub leaves that issue exactly where it was. If it was on
`4_needs_review`, it now sits there with no open PR, still counted by the claim
WIP gate as work awaiting review. So the merged branch also returns each
such issue to `3_started`, the rung the closed-unmerged row writes, for the same
reason: the review this rung signalled has ended, and the work has not.

It finds them by the issue's STATE, not by any link to this PR. A candidate is
any `#<n>` in the PR's title or body, which over-matches (issues and PRs share a
number space), and that is safe only because of the gate each candidate must
pass: open, on `4_needs_review`, and cross-referenced by no open PR. An issue in
that state is wrong however it got there, so a loose mention can only ever
correct it. The PR's head branch is no help here: a PR that only mentions an
issue can merge from a branch that names none.

This closes the drift at its source; `gh-issue-reconcile.py`'s row 4 is the
sweep that catches what this cannot — an issue closed by hand in the web UI, a
repo where this workflow does not run, and anything that drifted before this
existed. Both compute the rung-free set with `gh-issue-state.py`'s
`done_label_set()`, so they cannot disagree about what "done" is.

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
      --event closed --merged --pr 612 --apply

Without --apply it decides and prints what it would write, performing the read
but no write. A no-op — any branch that is not a task branch, a closed issue, an
unexpected current rung, a merged PR that closed and stranded nothing — exits 0
and says why:
this runs on every PR in the repo, so "did nothing" is the common case, not a
failure.
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
gh_issue_graph = _load("gh-issue-graph.py", "gh_issue_graph")

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


def decide(event, branch):
    """(issue number, (expected rung, target rung)) — or (None, reason) to skip.

    Everything decidable from the PR event alone happens here, before any
    network call, so the overwhelmingly common case — a PR on a branch that is
    not a task branch — costs zero requests.

    This is the RUNG-TRANSITION path only. A merged PR never reaches here: it is
    not a transition, and main() routes it to strip_merged() first. That is why
    this takes no `merged` argument — one used to be passed and ignored, which
    would now read as if the merged case were still decided here.
    """
    issue = gh_issue_claim.parse_issue_number(branch)
    if issue is None:
        return None, f"not a task branch: {branch}"
    return issue, TRANSITIONS[event]


def read_merged_pr(repo, pr):
    """(issues this PR closes in `repo`, issues its title and body mention).

    GitHub resolves the closing keywords itself, so nothing here re-implements
    `Closes #<n>` matching — which would have to track every accepted keyword
    and the cross-repo `owner/name#n` form to stay correct.

    The repo filter is not defensive tidying: a PR may close an issue in ANOTHER
    repository, and `GITHUB_TOKEN` is scoped to this one. Writing there would
    fail; reading a same-numbered local issue instead would be worse. So those
    are dropped, and the caller reports the count.

    The mentions are the stranded-issue candidates, parsed by
    `gh-issue-graph.py`'s pattern, which already refuses a repo-qualified
    `owner/repo#<n>` and a mention inside code. The title is included because
    the execute path puts its `[#<n>]` token there.
    """
    code, out, err = gh_issue_state.run_gh(
        [
            "pr",
            "view",
            str(pr),
            "--repo",
            repo,
            "--json",
            "closingIssuesReferences,title,body",
        ]
    )
    if code != 0:
        raise SystemExit(
            f"reading closing issues for {repo}#{pr} failed: "
            f"{err.strip() or out.strip()}"
        )
    payload = json.loads(out or "{}")
    owner, name = repo.split("/", 1)
    closing = []
    for ref in payload.get("closingIssuesReferences") or []:
        ref_repo = ref.get("repository") or {}
        ref_owner = (ref_repo.get("owner") or {}).get("login")
        if ref_owner == owner and ref_repo.get("name") == name:
            closing.append(ref["number"])

    text = f"{payload.get('title') or ''}\n{payload.get('body') or ''}"
    mentioned = []
    for ref in gh_issue_graph.parse_body_refs(
        text, str(pr), gh_issue_graph.BODY_REF_ID_PATTERN
    ):
        number = int(ref["target"])
        if number not in closing and number not in mentioned:
            mentioned.append(number)
    return closing, mentioned


# One read per candidate: its state, labels, and every PR that references it.
# `issueOrPullRequest` rather than `issue`, because a `#<n>` mention can name a
# pull request, and `issue(number:)` answers that with an error rather than a
# null. `last:` because an open PR is a recent reference, and `hasPreviousPage`
# says when older ones went unread.
CANDIDATE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issueOrPullRequest(number: $number) {
      ... on Issue {
        state
        labels(first: 100) { nodes { name } }
        timelineItems(last: 100, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT]) {
          pageInfo { hasPreviousPage }
          nodes {
            ... on CrossReferencedEvent { source { ... on PullRequest { number state } } }
            ... on ConnectedEvent { subject { ... on PullRequest { number state } } }
          }
        }
      }
    }
  }
}
"""


def _not_found(out):
    """Whether a failed GraphQL read failed only because the number names nothing.

    `gh` exits non-zero on any GraphQL error but still prints the response body,
    so the error `type` is readable. Every other failure stays fatal.
    """
    try:
        errors = json.loads(out or "{}").get("errors") or []
    except ValueError:
        return False
    return bool(errors) and all(e.get("type") == "NOT_FOUND" for e in errors)


def read_candidate(repo, number):
    """(labels, state, open PR numbers, complete?) — or None if not an issue."""
    owner, name = repo.split("/", 1)
    code, out, err = gh_issue_state.run_gh(
        [
            "api",
            "graphql",
            "-f",
            f"query={CANDIDATE_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ]
    )
    if code != 0:
        if _not_found(out):
            # A mention that names nothing — a typo, a deleted issue. It is free
            # text in someone's PR body, so it must not fail the run.
            return None
        raise SystemExit(
            f"reading {repo}#{number} failed: {err.strip() or out.strip()}"
        )
    node = ((json.loads(out or "{}").get("data") or {}).get("repository") or {}).get(
        "issueOrPullRequest"
    ) or {}
    if "state" not in node:
        # A pull request, or a number that resolves to nothing.
        return None
    labels = [label["name"] for label in node["labels"]["nodes"]]
    timeline = node["timelineItems"]
    open_prs = []
    for event in timeline["nodes"]:
        ref = event.get("source") or event.get("subject") or {}
        if ref.get("state") == "OPEN" and ref["number"] not in open_prs:
            open_prs.append(ref["number"])
    complete = not timeline["pageInfo"]["hasPreviousPage"]
    return labels, node["state"].lower(), open_prs, complete


def rung_write(current, rung, target, groups, vocabulary):
    """(label set, dropped labels) that replaces `rung` with `target`.

    Carries every OTHER managed label through unchanged — the `auto:` rung the
    invariant requires, plus prio:/est:. Anything in a managed namespace that
    the vocabulary does not define is dropped rather than echoed back, which is
    what gh-issue-state.py would do to it anyway, and is returned so the caller
    can name it: the full-set write is right to purge a `prio:urgent` a human
    invented, but the deletion is invisible unless it is named — and
    unattended, an Actions log is the only place anyone could ever see it.

    Raises InvalidLabelSet rather than return a set that is still illegal.
    """
    managed = [target] + [
        label
        for label in current
        if label != rung
        and gh_issue_state.in_managed_namespace(label, set(groups))
        and label in vocabulary
    ]
    dropped = gh_issue_state.dropped_unrecognized(current, set(groups), vocabulary)
    gh_issue_state.validate(managed, vocabulary)
    preserved = gh_issue_state.preserve_unmanaged(current, set(groups))
    return managed + [label for label in preserved if label not in managed], dropped


def return_stranded(repo, mentioned, labels_file, apply):
    """Move each mentioned issue this merge stranded in review back to started.

    Returns a list of per-issue outcome dicts, shaped like strip_merged()'s.
    Every candidate is a no-op unless it is open, on `4_needs_review`, and
    referenced by no open PR — see the module docstring for why that gate, and
    not the mention, is what makes the write safe.
    """
    groups, colors = gh_issue_state.load_vocabulary(labels_file)
    vocabulary = gh_issue_state.expected_labels(groups, colors)

    outcomes = []
    for issue in mentioned:
        read = read_candidate(repo, issue)
        if read is None:
            continue
        current, state, open_prs, complete = read
        if state != "open":
            outcomes.append({"issue": issue, "skipped": f"{repo}#{issue} is closed"})
            continue
        rung = status_rung(current)
        if rung != NEEDS_REVIEW:
            outcomes.append(
                {
                    "issue": issue,
                    "skipped": f"{repo}#{issue} is on "
                    f"{rung or 'no single status: rung'}, not {NEEDS_REVIEW}",
                }
            )
            continue
        if open_prs:
            listed = ", ".join(f"#{n}" for n in open_prs)
            outcomes.append(
                {
                    "issue": issue,
                    "skipped": f"{repo}#{issue} still has open PR {listed}",
                }
            )
            continue
        if not complete:
            # Over 100 references, and an open PR could be among the unread
            # ones. Leaving the rung is the recoverable mistake.
            outcomes.append(
                {
                    "issue": issue,
                    "skipped": f"{repo}#{issue} has more references than one read covers",
                }
            )
            continue

        try:
            labels, dropped = rung_write(current, rung, STARTED, groups, vocabulary)
        except gh_issue_state.InvalidLabelSet as exc:
            outcomes.append({"issue": issue, "refused": str(exc)})
            continue
        if apply:
            gh_issue_state.patch_issue(repo, issue, labels)
        outcomes.append(
            {"issue": issue, "labels": labels, "dropped": dropped, "applied": apply}
        )
    return outcomes


def strip_merged(repo, closing, labels_file, apply):
    """Strip the rungs from every issue this merged PR closed.

    Returns a list of per-issue outcome dicts. Each issue is decided on its own
    CURRENT state, the same safety posture the rung transitions take: an issue
    that is not closed, or carries no rung, is left alone. So a PR whose closing
    reference was already reconciled, or whose issue a human reopened, is a
    no-op rather than a correction.
    """
    groups, colors = gh_issue_state.load_vocabulary(labels_file)
    vocabulary = gh_issue_state.expected_labels(groups, colors)

    outcomes = []
    for issue in closing:
        current, state = gh_issue_state.current_issue(repo, issue)
        if state != "closed":
            # The merge did not close it — a human reopened it, or the reference
            # resolved without a close. Stripping the rungs off a live issue
            # would retire work that is still moving.
            outcomes.append({"issue": issue, "skipped": f"{repo}#{issue} is open"})
            continue

        rungs = gh_issue_state.carried_rungs(current, vocabulary)
        if not rungs:
            outcomes.append(
                {"issue": issue, "skipped": f"{repo}#{issue} carries no rung"}
            )
            continue

        try:
            labels = gh_issue_state.done_label_set(current, groups, vocabulary)
        except gh_issue_state.InvalidLabelSet as exc:
            # Two `prio:` labels, say. Report and leave it for row 4's sweep and
            # a human, rather than writing a set that is still illegal.
            outcomes.append({"issue": issue, "refused": str(exc), "rungs": rungs})
            continue

        dropped = gh_issue_state.dropped_unrecognized(current, set(groups), vocabulary)
        if apply:
            # No `state` in the payload — the issue stays closed. This strips
            # labels; reopening or closing is never this script's to do.
            gh_issue_state.patch_issue(repo, issue, labels)
        outcomes.append(
            {
                "issue": issue,
                "rungs": rungs,
                "labels": labels,
                "dropped": dropped,
                "applied": apply,
            }
        )
    return outcomes


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
    parser.add_argument(
        "--pr",
        type=int,
        help=(
            "the PR number. Required with --merged, which asks GitHub which "
            "issues the PR closed rather than deriving one from the branch"
        ),
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

    if args.event == "closed" and args.merged:
        if args.pr is None:
            parser.error("--merged requires --pr")
        closing, mentioned = read_merged_pr(args.repo, args.pr)
        outcomes = strip_merged(args.repo, closing, args.labels_file, args.apply)
        stranded = return_stranded(args.repo, mentioned, args.labels_file, args.apply)
        if args.as_json:
            print(
                json.dumps(
                    {
                        "repo": args.repo,
                        "pr": args.pr,
                        "merged": outcomes,
                        "stranded": stranded,
                    },
                    indent=2,
                )
            )
            return 0
        for outcome in stranded:
            number = outcome["issue"]
            if outcome.get("skipped"):
                print(f"no-op: {outcome['skipped']}")
            elif outcome.get("refused"):
                print(
                    f"refusing to write {args.repo}#{number}: {outcome['refused']}",
                    file=sys.stderr,
                )
            else:
                verb = "Returned" if args.apply else "Would return"
                print(f"{verb} {args.repo}#{number} to {STARTED}: no open PR left")
                if outcome["dropped"]:
                    names = ", ".join(outcome["dropped"])
                    print(f"Dropped (not in labels.yml): {names}")
        if not outcomes and not stranded:
            print(
                f"no-op: {args.repo}#{args.pr} closed and stranded no issue in this repo"
            )
        else:
            for outcome in outcomes:
                number = outcome["issue"]
                if outcome.get("skipped"):
                    print(f"no-op: {outcome['skipped']}")
                elif outcome.get("refused"):
                    print(
                        f"refusing to write {args.repo}#{number}: {outcome['refused']}",
                        file=sys.stderr,
                    )
                else:
                    verb = "Stripped" if args.apply else "Would strip"
                    rungs = ", ".join(outcome["rungs"])
                    print(f"{verb} {args.repo}#{number}: {rungs}")
                    if outcome["dropped"]:
                        names = ", ".join(outcome["dropped"])
                        print(f"Dropped (not in labels.yml): {names}")
        # A refusal is reported, never fatal: the rest of the PR's issues are
        # still worth stripping, and row 4's sweep will re-report this one.
        return 0

    issue, outcome = decide(args.event, args.branch)
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
    try:
        labels, dropped = rung_write(current, rung, target, groups, vocabulary)
    except gh_issue_state.InvalidLabelSet as exc:
        print(f"refusing to write {args.repo}#{issue}: {exc}", file=sys.stderr)
        return 2

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
