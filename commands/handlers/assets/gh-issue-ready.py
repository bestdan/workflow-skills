#!/usr/bin/env python3
"""Report which open, `status:2_ready` issues are actually dependency-ready.

An issue is ready iff it carries the vocabulary's ready status label AND no
issue in its `blocked_by` list is still open. `status:2_ready` alone is not
enough — a repo can label an issue ready while GitHub's native dependency
graph still has it waiting on an open blocker, and nothing else surfaces that
gap.

`gh` has no dependency subcommand — measured: `gh issue --help` lists nothing
for it. The dependency graph is reached through `gh api` instead, against
`repos/{owner}/{repo}/issues/{n}/dependencies/blocked_by` (and its `blocking`
sibling), both verified real: a bogus sibling path 404s, these return `[]`.
Both are GETs, so this script is read-only by construction — it never calls
`gh issue edit`, never mutates a label, and never passes `--method` other than
the implicit GET.

A cloud routine has no `gh`, so it cannot use this path; the unattended
equivalent is an open question, not this file's job.

With `--max-estimate N` it also gates on size, reporting an `oversized` bucket
beside `ready` and `blocked`. This is where gh-issue's `max_estimate` lives —
at claim, not at promote, matching `linear` and resolving the divergence
bestdan/workflow-skills#746 names. The bound is EXCLUSIVE (`est:3` fails
against `3`) and the reason string is byte-identical to `_linear_rank.py`'s, so
one board reads the same on both handlers.

**Omitting the flag means no size gate.** Two callers are entitled to omit it.
The first is a claim run whose human already granted the override through
`commands/handlers/attendedness.md`. The second is the promote hold
(`gh-issue-promote.md` step 3b), which asks only the dependency question: promotion
has no size gate, so there is no bound to apply. A `max-estimate=` run override is
NOT such a caller: it replaces the bound, so it is passed here as a different
`--max-estimate` value, never by dropping the flag. The claim flow otherwise always passes the
resolved bound and offers the override on the drop, because "is a human watching?"
is not observable and this script must not guess it (attendedness.md, "Do not
classify the run").

Size is a routing concern for automation, not a quality verdict on the card, so
an oversized issue stays `status:2_ready` and visible here rather than being
demoted at promote where only a human could retrieve it.

Usage:
  python3 gh-issue-ready.py --repo owner/name
  python3 gh-issue-ready.py --repo owner/name --limit 100 --json
  python3 gh-issue-ready.py --repo owner/name --label follow-up   # match a board's scope
  python3 gh-issue-ready.py --repo owner/name --issue 7 --issue 9 # candidate-scoped pass
  python3 gh-issue-ready.py --repo owner/name --max-estimate 3    # unattended: gate on size
  python3 gh-issue-ready.py --repo owner/name --issue 7:3 --issue 9:5 --max-estimate 3
                                        # caller supplies each estimate: no label reads

One or more --issue switches to candidate-scoped mode: the candidate set is
EXACTLY those numbers and the `gh issue list` query is skipped entirely.
--limit and --label are ignored in this mode. This is what the claim flow
needs: it has already selected and ranked its candidates through its own
board query, and re-deriving them here through a second bounded list query
could silently drop one — `--limit` is applied by the API before anything
local runs, so a missing verdict is indistinguishable from a ready one.
Passing the numbers removes that window. There is no title in this mode (no
list call was made), so each issue reports an empty title. Nor is the
`status:2_ready` label checked, which is what lets the promote hold ask about
un-scored issues: in this mode `ready` means only "no open blocker".
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import (  # noqa: E402
    DEFAULT_LABELS_FILE,
    VocabularyError,
    load_vocabulary,
)

READY_STATUS_VALUE = "2_ready"
ESTIMATE_PREFIX = "est:"


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def ready_label(groups):
    """Derive `status:2_ready` from the vocabulary instead of hardcoding it.

    A rename in labels.yml (`2_ready` -> something else) must fail loudly here
    rather than silently matching zero issues.
    """
    if READY_STATUS_VALUE not in groups.get("status", []):
        raise VocabularyError(
            f"labels.yml: status group has no `{READY_STATUS_VALUE}` value"
        )
    return f"status:{READY_STATUS_VALUE}"


def parse_issue_arg(raw):
    """`<n>` or `<n>:<est>` -> (number, estimate or None).

    The optional estimate is how a caller that ALREADY has the issue's labels
    avoids making this script re-read them. The claim flow's board query selects
    on `--json number,title,body,labels`, so it holds every candidate's `est:`
    before it calls here; without this form it would pay one `gh issue view` per
    candidate — up to 50 on a full window, before a single blocker read.

    A bare `<n>` still works and still triggers the read, because a caller that
    genuinely does not know the estimate must not have one invented for it.
    There is deliberately no spelling for "I know it has no estimate": the
    fallback read reaches the same verdict, and an extra sentinel would be one
    more thing to get wrong for one saved call in the rarest case.
    """
    text = str(raw)
    number, sep, estimate = text.partition(":")
    try:
        parsed_number = int(number)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--issue expects <n> or <n>:<est>, got {text!r}"
        ) from None
    if not sep:
        return parsed_number, None
    try:
        return parsed_number, int(estimate)
    except ValueError:
        # Fail loudly rather than degrading to a read: a caller that meant to
        # pass an estimate and typo'd it would otherwise get a silently
        # different (slower, but also possibly different-verdict) code path.
        raise argparse.ArgumentTypeError(
            f"--issue estimate must be an integer, got {estimate!r} in {text!r}"
        ) from None


def list_ready_issues(repo, label, limit, scope_labels=()):
    """Open issues carrying the ready label, narrowed to `scope_labels`.

    The scope matters for more than cost. `--limit` is applied by the API, so a
    window drawn over the whole repo can exclude an in-scope issue entirely —
    and a missing verdict is indistinguishable from a ready one, so the caller
    cannot detect the omission. Repeated `--label` flags AND together, matching
    how the board query narrows itself.
    """
    args = [
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--label",
        label,
    ]
    for scope in scope_labels:
        args += ["--label", scope]
    args += ["--json", "number,title,labels", "--limit", str(limit)]
    code, out, err = run_gh(args)
    if code != 0:
        raise SystemExit(
            f"gh issue list failed for {repo}: {err.strip() or out.strip()}"
        )
    return json.loads(out or "[]")


def label_names(labels):
    """Label names from either `gh`'s object form or a plain list of strings."""
    return [lab["name"] if isinstance(lab, dict) else lab for lab in labels or []]


def fetch_labels(repo, issue):
    """This issue's label names, for candidate-scoped mode.

    The list query carries labels; the candidate-scoped path skips that query by
    design, so the estimate gate has to ask per issue. Only called when a
    `--max-estimate` was actually passed, so a caller that does not gate on size
    pays nothing.
    """
    code, out, err = run_gh(
        ["issue", "view", str(issue), "--repo", repo, "--json", "labels"]
    )
    if code != 0:
        raise SystemExit(
            f"gh issue view failed for {repo}#{issue}: {err.strip() or out.strip()}"
        )
    return label_names(json.loads(out or "{}").get("labels", []))


def estimate_of(names):
    """The issue's `est:` value as an int, or None when it carries none.

    A missing estimate is NOT a drop here, which is where this gate parts company
    with Linear's (`_linear_rank.py` returns `no estimate set`). On gh-issue the
    promoter backfills `est:` for every issue it scores, so an issue with no
    `est:` label has never been scored — and the `status:`/`auto:` rungs the
    candidate query already filters on express that far more precisely than an
    absent number does. Dropping on absence here would make every pre-backfill
    issue permanently unclaimable, which is a regression, not a gate.
    """
    for name in names:
        if name.startswith(ESTIMATE_PREFIX):
            try:
                return int(name[len(ESTIMATE_PREFIX) :])
            except ValueError:
                return None
    return None


def open_blockers(repo, issue):
    """Numbers of this issue's `blocked_by` dependencies still open.

    `--paginate --slurp`, not a bare call: this is a REST list endpoint, so a
    plain read returns only the first 30 blockers and an open one past that page
    would make a blocked issue read as ready — a silent wrong answer, which is
    the one thing this script must not produce. `--slurp` is what makes it
    parseable: bare `--paginate` emits one JSON array per page, concatenated,
    which is not valid JSON; `--slurp` wraps the pages in a single array, so the
    result is a list of pages to flatten.
    """
    code, out, err = run_gh(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/issues/{issue}/dependencies/blocked_by",
        ]
    )
    if code != 0:
        raise SystemExit(
            f"gh api blocked_by failed for {repo}#{issue}: {err.strip() or out.strip()}"
        )
    pages = json.loads(out or "[]")
    blockers = [b for page in pages for b in page]
    return [b["number"] for b in blockers if b.get("state") == "open"]


def compute(
    repo,
    labels_file,
    limit,
    scope_labels=(),
    issue_numbers=(),
    max_estimate=None,
):
    groups, _colors = load_vocabulary(labels_file)
    label = ready_label(groups)

    if issue_numbers:
        # Candidate-scoped: skip the list query entirely. Re-deriving the set
        # through a second bounded query risks silently dropping a candidate
        # the caller already selected — see the module docstring.
        candidates = []
        for entry in issue_numbers:
            number, estimate = entry if isinstance(entry, tuple) else (entry, None)
            candidate = {"number": number, "title": ""}
            if estimate is not None:
                # Seeding `labels` is what makes the gate below skip its read —
                # the same branch a list-mode candidate takes, so the caller-
                # supplied estimate and a fetched one go through one code path.
                candidate["labels"] = [f"est:{estimate}"]
            candidates.append(candidate)
    else:
        candidates = list_ready_issues(repo, label, limit, scope_labels)
    ready = []
    blocked = []
    oversized = []
    for issue in candidates:
        number, title = issue["number"], issue["title"]
        # Size before dependencies: the estimate is already in hand (or one cheap
        # read away) while each blocker check is a paginated API call, so gating
        # first saves the call on an issue that was never claimable anyway.
        if max_estimate is not None:
            names = (
                label_names(issue["labels"])
                if "labels" in issue
                else fetch_labels(repo, number)
            )
            estimate = estimate_of(names)
            if estimate is not None and estimate >= max_estimate:
                oversized.append(
                    {
                        "number": number,
                        "title": title,
                        "estimate": estimate,
                        # Verbatim the string `_linear_rank.py` emits, so a board
                        # reads identically across the two handlers — the parity
                        # bestdan/workflow-skills#746 asks for.
                        "reason": f"estimate {estimate} >= {max_estimate}",
                    }
                )
                continue
        blockers = open_blockers(repo, number)
        if blockers:
            blocked.append(
                {"number": number, "title": title, "open_blockers": blockers}
            )
        else:
            ready.append({"number": number, "title": title})

    return {
        "repo": repo,
        "checked": len(candidates),
        "scoped": bool(issue_numbers),
        "max_estimate": max_estimate,
        "ready": ready,
        "blocked": blocked,
        "oversized": oversized,
    }


def label_for(issue):
    """`#<n> <title>`, or bare `#<n>` when no title was fetched (candidate-scoped mode)."""
    return (
        f"#{issue['number']} {issue['title']}"
        if issue["title"]
        else f"#{issue['number']}"
    )


def report(result):
    # Candidate-scoped mode never asked about the ready label — the caller's own
    # query already did — so the header says what was actually checked.
    scope = "candidate(s)" if result["scoped"] else "issue(s) carrying status:2_ready"
    print(f"{result['repo']}: checked {result['checked']} {scope}")

    ready = result["ready"]
    print(f"\nReady ({len(ready)}):")
    for issue in ready:
        print(f"  {label_for(issue)}")

    blocked = result["blocked"]
    print(f"\nBlocked ({len(blocked)}):")
    for issue in blocked:
        blockers = ", ".join(f"#{n}" for n in issue["open_blockers"])
        print(f"  {label_for(issue)} — waiting on {blockers}")

    # Printed only when a gate was asked for, so a caller that passes no
    # --max-estimate sees exactly the two sections it saw before.
    if result.get("max_estimate") is not None:
        oversized = result["oversized"]
        print(f"\nOversized ({len(oversized)}):")
        for issue in oversized:
            print(f"  {label_for(issue)} — {issue['reason']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="max issues to check (ignored with --issue)",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        dest="scope_labels",
        metavar="LABEL",
        help="narrow to issues also carrying this label; repeatable (AND); ignored with --issue",
    )
    parser.add_argument(
        "--issue",
        action="append",
        type=parse_issue_arg,
        default=[],
        dest="issue_numbers",
        metavar="N[:EST]",
        help=(
            "check exactly this issue number instead of querying for candidates; "
            "repeatable; skips the `gh issue list` call entirely and ignores "
            "--limit/--label. Append `:<est>` when you already hold the issue's "
            "estimate, to skip the per-issue label read the size gate would "
            "otherwise make"
        ),
    )
    parser.add_argument(
        "--max-estimate",
        type=int,
        default=None,
        metavar="N",
        help=(
            "drop candidates whose `est:` label is N or higher (EXCLUSIVE bound, "
            "matching linear-rank.py). Omitted means no size gate at all — only "
            "for a claim whose human granted the override (gh-issue-claim.md) or "
            "the promote dependency hold (gh-issue-promote.md step 3b)"
        ),
    )
    parser.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = compute(
        args.repo,
        args.labels_file,
        args.limit,
        args.scope_labels,
        args.issue_numbers,
        args.max_estimate,
    )
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
