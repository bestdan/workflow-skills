#!/usr/bin/env python3
"""Backfill `prio:`/`est:` on gh-issue cards without touching their `status:` rung.

`commands/handlers/task-fill.md` owns the judgment — the static `medium`
priority default, the model-produced Fibonacci estimate, the over-ceiling rule,
the held-card exception. This file owns the mechanism for the `gh-issue`
adapter: candidate selection, the hold check, the symbolic->label encoding, the
complete-set composition, the write, and the one provenance record a batch run
emits.

It exists because backfill previously had no execution path of its own. It ran
only as an argument change to the promote flow's transition PATCH, and that flow
refuses by design to touch an already-scored issue. So an issue scored before
backfill existed carried no `prio:`/`est:` and had no supported way to get one:
closing that gap meant composing `--labels` values from prose, per issue, with
nothing pinning the one mistake the encoding invites.

That mistake is the priority inversion. GitHub's `prio:0` is HIGHEST; Linear's
priority `0` is _none_ (unset). A bare integer carried between the two trackers
silently inverts, turning an unprioritised card into the top of the board.
`encode` is the only place this handler writes that mapping down, and
`scripts/test_gh_issue_backfill.py` asserts it in both directions.

Three subcommands, because the promote flow and a standalone backfill need
different amounts of it:

- `encode` — symbolic priority/estimate -> the `prio:`/`est:` label names.
  This is the half `gh-issue-promote.md` needs: it is composing a full `--labels`
  value for its transition anyway, so its backfill must stay an argument change
  to that one PATCH rather than a second write. It takes the names from here
  instead of re-deriving the table. The create flow (`gh-issue.md` step 4) uses
  it too, with `--human-set`, to carry a drafted task's own priority and size
  onto the issue's initial stamp.
- `scan` — open issues MISSING `prio:` or `est:` at any `status:` rung, minus the
  held ones, with the body text the caller needs to estimate from.
- `apply` — write those two labels and nothing else.

`apply` is deliberately narrow. It is not a re-score: it refuses to move an
issue between `status:` rungs in either direction (demotion stays a human's
call), and it asserts the resulting `status:`/`auto:` labels are identical to
the ones it read. It fills only a MISSING field, so a human's value is never
overwritten, and an issue already carrying both receives no write at all.

Held cards get no write, per task-fill.md's "When it runs". Held means either
the `blocked` label or an open entry in GitHub's native `blocked_by` graph —
the definition `commands/handlers/gh-issue.md` gives and `gh-issue-ready.py`
implements. This reuses that implementation rather than writing a third.

Provenance. Per-issue provenance is the issue's own timeline: a `labeled` event
naming the actor and the time, which is where a human correcting the value
already is, and which costs no notification. The batch gets ONE record — the
report this prints, optionally posted as a single comment with
`--provenance-issue`. One comment per issue was right for a promote run touching
a handful of cards; at 33 it is notification noise.

Usage:
  python3 gh-issue-backfill.py encode --priority medium --estimate 8
  python3 gh-issue-backfill.py encode --human-set --priority urgent --estimate 2
  python3 gh-issue-backfill.py scan --repo owner/name --json
  python3 gh-issue-backfill.py apply --repo owner/name --plan plan.json --apply

The plan is a JSON list, one entry per issue, carrying the model's judgment:

  [{"number": 142, "priority": "medium", "estimate": 3}, ...]

`priority` is the symbolic vocabulary — never an integer, which is the inversion
above. `estimate` is an integer; one past the top of the ladder is recorded as
`over ladder` and left unset, exactly as task-fill.md's over-ceiling rule says,
while one merely off the ladder (7) is refused.
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
    EXACTLY_ONE,
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
gh_issue_ready = _load("gh-issue-ready.py", "gh_issue_ready")

BLOCKED_LABEL = "blocked"

# The symbolic priority vocabulary, HIGHEST FIRST. `none` is not in here: it is
# the absence of a `prio:` label, not a rung, so it has no position on the
# ladder. The order is what pairs with labels.yml's `prio:` values below.
PRIORITY_RANKS = ("urgent", "high", "medium", "low")
PRIORITY_NONE = "none"


class BackfillError(Exception):
    """The requested backfill is not writable, and the caller must be told why."""


def priority_encoding(groups):
    """symbolic -> `prio:` label, derived from labels.yml rather than hardcoded.

    Pairing the vocabulary's values with PRIORITY_RANKS in order is what makes a
    change to labels.yml fail loudly here. Hardcoding `medium -> prio:2` would
    survive a fifth `prio:` value being added and quietly encode the wrong rung.

    `prio:0` is highest. That is the inverse of Linear's priority `0`, which
    means _none_; see the module docstring.
    """
    values = groups.get("prio", [])
    if len(values) != len(PRIORITY_RANKS):
        raise VocabularyError(
            f"labels.yml: prio group has {len(values)} values, but the symbolic "
            f"vocabulary has {len(PRIORITY_RANKS)} ({', '.join(PRIORITY_RANKS)})"
        )
    ordered = sorted(values, key=int)
    return {name: f"prio:{value}" for name, value in zip(PRIORITY_RANKS, ordered)}


def estimate_ladder(groups):
    """The `est:` rungs labels.yml admits, ascending."""
    return sorted(int(value) for value in groups.get("est", []))


def encode(groups, priority=None, estimate=None, human_set=False):
    """The `prio:`/`est:` labels for one symbolic priority and one estimate.

    Returns (labels, notes). `notes` names what was deliberately NOT encoded, so
    a caller can report it: a `none` priority and an over-ladder estimate both
    mean "no label", and the two are not the same fact.

    `human_set` says the values came from a person rather than a backfill guess
    (a task card a human drafted or vetted). It lifts only the `urgent` refusal:
    that rule stops the promoter escalating on no signal, and a human's
    `urgent` is exactly the signal it lacks.
    """
    labels, notes = [], []

    if priority is not None:
        if priority == "urgent" and not human_set:
            # task-fill.md: escalation is a human's call and the promoter has no
            # signal for it. Refusing beats silently downgrading to `high`,
            # which would look like a judgment the caller made.
            raise BackfillError(
                "refusing to auto-set `urgent`: escalation is a human's call "
                "(commands/handlers/task-fill.md)"
            )
        if priority == PRIORITY_NONE:
            notes.append("priority none — no prio: label")
        else:
            table = priority_encoding(groups)
            if priority not in table:
                raise BackfillError(
                    f"unknown priority `{priority}`: expected one of "
                    f"{', '.join((*PRIORITY_RANKS, PRIORITY_NONE))} — never an "
                    "integer, which inverts between GitHub and Linear"
                )
            labels.append(table[priority])

    if estimate is not None:
        ladder = estimate_ladder(groups)
        if estimate in ladder:
            labels.append(f"est:{estimate}")
        elif estimate > ladder[-1]:
            # task-fill.md's over-ceiling rule: above the ladder's top the field
            # stays unset. Writing a bogus top rung would make an oversized card
            # look routable, and an off-ladder name fails the whole write.
            notes.append(f"estimate {estimate} over ladder top {ladder[-1]} — unset")
        else:
            raise BackfillError(
                f"estimate {estimate} is not on the ladder "
                f"({', '.join(str(rung) for rung in ladder)})"
            )

    return labels, notes


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def list_open_issues(repo, limit, milestone=None):
    args = ["issue", "list", "--repo", repo, "--state", "open"]
    if milestone:
        args += ["--milestone", milestone]
    args += ["--json", "number,title,body,labels", "--limit", str(limit)]
    code, out, err = run_gh(args)
    if code != 0:
        raise SystemExit(
            f"gh issue list failed for {repo}: {err.strip() or out.strip()}"
        )
    return json.loads(out or "[]")


def label_names(issue):
    return [entry["name"] for entry in issue.get("labels", [])]


def missing_fields(labels):
    """Which of `prio:`/`est:` this issue does not carry, in report order."""
    return [
        group
        for group in ("prio", "est")
        if not any(label.startswith(f"{group}:") for label in labels)
    ]


def hold_reason(repo, number, labels):
    """Why this issue is held, or None.

    Two sources, and they are not the same question. The `blocked` label is a
    human's override for a blocker with no issue to point at; an open entry in
    the native `blocked_by` graph is the definition `gh-issue.md` gives. Reading
    the graph reuses gh-issue-ready.py's paginating implementation — a blocker
    past page one would otherwise make a held issue read as writable.
    """
    if BLOCKED_LABEL in labels:
        return "blocked label"
    blockers = gh_issue_ready.open_blockers(repo, number)
    if blockers:
        return "blocked by " + ", ".join(f"#{n}" for n in blockers)
    return None


def scan(repo, labels_file, limit, milestone=None):
    """Open issues missing a `prio:` or `est:`, partitioned into writable and held.

    A result sitting exactly at `limit` may be truncated, and a sweep that says
    "whole backlog" while omitting later issues is the one wrong answer this is
    asked for — the scoring flow warns on the same condition
    (`gh-issue-promote.md` step 6), so this does too.
    """
    load_vocabulary(labels_file)
    candidates, held, complete = [], [], []
    issues = list_open_issues(repo, limit, milestone)
    for issue in issues:
        labels = label_names(issue)
        missing = missing_fields(labels)
        if not missing:
            complete.append(issue["number"])
            continue
        reason = hold_reason(repo, issue["number"], labels)
        entry = {
            "number": issue["number"],
            "title": issue["title"],
            "labels": labels,
            "missing": missing,
        }
        if reason:
            held.append({**entry, "reason": reason})
        else:
            candidates.append({**entry, "body": issue.get("body") or ""})
    return {
        "repo": repo,
        "checked": len(issues),
        "truncated": len(issues) >= limit,
        "milestone": milestone,
        "candidates": candidates,
        "held": held,
        "complete": complete,
    }


def load_plan(path):
    entries = json.loads(Path(path).read_text())
    if not isinstance(entries, list):
        raise SystemExit(f"{path}: expected a JSON list of plan entries")
    seen = set()
    for entry in entries:
        number = entry.get("number")
        if not isinstance(number, int):
            raise SystemExit(f"{path}: every entry needs an integer `number`")
        if number in seen:
            raise SystemExit(f"{path}: #{number} appears twice")
        # A quoted number is what a model emits when it is being careful with
        # JSON, and it would otherwise reach `encode`'s ladder comparison as a
        # str and raise mid-batch. Refusing the whole plan here is strictly
        # better: it happens before the first PATCH, so nothing is half-written.
        estimate = entry.get("estimate")
        if estimate is not None and not isinstance(estimate, int):
            raise SystemExit(
                f"{path}: #{number}: `estimate` must be an integer, got "
                f"{type(estimate).__name__}"
            )
        seen.add(number)
    return entries


def backfill_set(current, new_labels, groups, vocabulary):
    """The complete label set that adds `new_labels` and changes nothing else.

    The write replaces the issue's whole label set, so every managed label it
    already carries has to be echoed back, and the rungs have to come through
    byte-identical — this path is not a re-score and must never move an issue
    between `status:` rungs.
    """
    managed = [
        label
        for label in current
        if gh_issue_state.in_managed_namespace(label, set(groups))
        and label in vocabulary
    ]
    managed = managed + [label for label in new_labels if label not in managed]
    gh_issue_state.validate(managed, vocabulary)
    preserved = gh_issue_state.preserve_unmanaged(current, set(groups))
    return managed + [label for label in preserved if label not in managed]


def rungs_of(labels, vocabulary):
    return gh_issue_state.carried_rungs(labels, vocabulary)


def plan_one(repo, entry, groups, vocabulary):
    """Decide what one plan entry writes. Re-reads the issue; never writes.

    The re-read is not redundant with `scan`: a plan is composed by a model
    between the two calls, and an issue can be labelled, blocked or closed in
    that window. Deciding from the stale scan would write a value onto an issue
    that has since acquired a human's.
    """
    number = entry["number"]
    current, state = gh_issue_state.current_issue(repo, number)

    if state == "closed":
        return {"number": number, "action": "skipped", "reason": "closed"}

    missing = missing_fields(current)
    if not missing:
        return {
            "number": number,
            "action": "skipped",
            "reason": "already carries prio: and est:",
        }

    reason = hold_reason(repo, number, current)
    if reason:
        return {"number": number, "action": "held", "reason": reason}

    # An open issue carries exactly one `status:` and one `auto:` rung, and the
    # full-set write is validated against that invariant. An issue that breaks
    # it cannot be written at all — including a pre-migration issue with no
    # rungs, which is the promote flow's lane, not this one. Say so instead of
    # failing on a validator message that reads like a bug here.
    if len(rungs_of(current, vocabulary)) != len(EXACTLY_ONE):
        return {
            "number": number,
            "action": "skipped",
            "reason": "missing a status:/auto: rung — /promote-tasks or /reconcile-tasks owns that",
        }

    # Encode only the MISSING half. A plan may carry both values for an issue
    # that has since acquired one of them; writing that one would overwrite a
    # human's, which this path never does.
    #
    # An absent `priority` falls back to task-fill.md's static `medium` — that
    # default is deterministic, so it belongs here rather than in a caller's
    # prose. An absent `estimate` does not default: it is a model judgment, and
    # a blind constant misroutes work downstream.
    priority = entry.get("priority", "medium") if "prio" in missing else None
    estimate = entry.get("estimate") if "est" in missing else None
    new_labels, notes = encode(groups, priority, estimate)
    if "est" in missing and estimate is None:
        notes.append("no estimate supplied — est: left unset")
    if not new_labels:
        return {
            "number": number,
            "action": "skipped",
            "reason": "; ".join(notes) or "nothing to write",
            "notes": notes,
        }

    labels = backfill_set(current, new_labels, groups, vocabulary)
    # The full-set write purges a managed-namespace name the vocabulary does not
    # define (a hand-typed `status:custom`), and correctly so — but silently,
    # which would make this path's "writes prio:/est: and nothing else" claim
    # false in the reader's eyes. Report it, as every sibling full-set caller does.
    dropped = gh_issue_state.dropped_unrecognized(current, set(groups), vocabulary)
    before, after = rungs_of(current, vocabulary), rungs_of(labels, vocabulary)
    if before != after:
        # A backstop, not the first line of defence: composing a set that MOVED
        # a rung means composing one that also DUPLICATES it, which validate()
        # above already refuses. It is here because the two failures say
        # different things, and the contract this path is sold on — the rungs
        # come through untouched — deserves to be asserted rather than inferred
        # from a validator message about label counts.
        raise BackfillError(
            f"#{number}: backfill would change the status:/auto: rungs "
            f"({', '.join(before) or 'none'} -> {', '.join(after) or 'none'})"
        )

    return {
        "number": number,
        "action": "backfill",
        "added": new_labels,
        "labels": labels,
        "rungs": before,
        "dropped": dropped,
        "notes": notes,
    }


def apply_plan(repo, entries, labels_file, do_apply):
    groups, colors = load_vocabulary(labels_file)
    vocabulary = expected_labels(groups, colors)
    results, errors = [], []
    for entry in entries:
        # One entry's failure must not take the batch with it. Every gh
        # transport failure in this loop arrives as `SystemExit` — the helpers
        # raise it on a non-zero exit — so an expired token would otherwise
        # abort at issue 17 of 30, leaving 16 written and printing no report.
        # The sibling routines let it propagate because a later run heals the
        # gap and nobody reads their output; this is a one-shot command whose
        # printed report is the batch's only record. `patch_issue` is inside
        # the try for the same reason: a failed PATCH is one more entry's
        # failure, not the run's.
        try:
            result = plan_one(repo, entry, groups, vocabulary)
            if result["action"] == "backfill" and do_apply:
                gh_issue_state.patch_issue(repo, result["number"], result["labels"])
        except (BackfillError, gh_issue_state.InvalidLabelSet, SystemExit) as exc:
            errors.append({"number": entry["number"], "error": str(exc)})
            continue
        results.append(result)
    return {
        "repo": repo,
        "applied": do_apply,
        "results": results,
        "errors": errors,
        "backfilled": [r for r in results if r["action"] == "backfill"],
        "held": [r for r in results if r["action"] == "held"],
        "skipped": [r for r in results if r["action"] == "skipped"],
    }


def provenance_body(outcome):
    """The batch's ONE provenance record.

    Per-issue provenance is the `labeled` timeline event on each issue — actor,
    timestamp, correctable in place, no notification. This names the run that
    produced them so a human can find the whole batch from any one of them. It is
    only ever called on an applied run — a dry run writes nothing to post.
    """
    lines = [
        "/promote-tasks backfill-only: auto-set prio:/est: on "
        f"{len(outcome['backfilled'])} issue(s) — no status:/auto: rung moved."
    ]
    for result in outcome["backfilled"]:
        note = f" ({'; '.join(result['notes'])})" if result.get("notes") else ""
        lines.append(f"- #{result['number']}: {', '.join(result['added'])}{note}")
    if outcome["held"]:
        lines.append("")
        lines.append("Held (no write):")
        for result in outcome["held"]:
            lines.append(f"- #{result['number']}: {result['reason']}")
    return "\n".join(lines)


def post_provenance(repo, issue, body):
    """Post the batch's one comment. Returns an error string, or None on success.

    It returns rather than raises because the caller's next act is to print the
    report — and when this comment is what failed, that report is the batch's
    only remaining record. Exiting here would destroy it at precisely the moment
    it matters most.
    """
    code, out, err = run_gh(
        ["issue", "comment", str(issue), "--repo", repo, "--body", body]
    )
    if code != 0:
        return (
            f"posting provenance to {repo}#{issue} failed: {err.strip() or out.strip()}"
        )
    return None


def report_scan(result):
    scope = (
        f"milestone {result['milestone']}" if result["milestone"] else "whole backlog"
    )
    if result["truncated"]:
        # Leads the report, never trails it: a reader who stops early must still
        # learn the sweep was incomplete.
        print(
            f"⚠ query hit the {result['checked']}-issue cap — later open "
            "issues may not have been scanned. Re-run with a higher --limit."
        )
    print(f"{result['repo']}: checked {result['checked']} open issue(s), {scope}")
    print(f"\nMissing prio:/est: ({len(result['candidates'])}):")
    for issue in result["candidates"]:
        print(
            f"  #{issue['number']} {issue['title']} — missing {', '.join(issue['missing'])}"
        )
    print(f"\nHeld ({len(result['held'])}):")
    for issue in result["held"]:
        print(f"  #{issue['number']} {issue['title']} — {issue['reason']}")
    print(f"\nAlready complete: {len(result['complete'])}")


def report_apply(outcome):
    verb = "Backfilled" if outcome["applied"] else "Would backfill"
    print(f"{verb} {len(outcome['backfilled'])} issue(s):")
    for result in outcome["backfilled"]:
        note = f"  ({'; '.join(result['notes'])})" if result.get("notes") else ""
        print(f"  #{result['number']}  {', '.join(result['added'])}{note}")
        if result.get("dropped"):
            print(f"      dropped (not in labels.yml): {', '.join(result['dropped'])}")
    if outcome["held"]:
        print(f"\nHeld ({len(outcome['held'])}, no write):")
        for result in outcome["held"]:
            print(f"  #{result['number']}  {result['reason']}")
    if outcome["skipped"]:
        print(f"\nSkipped ({len(outcome['skipped'])}):")
        for result in outcome["skipped"]:
            print(f"  #{result['number']}  {result['reason']}")
    if outcome["errors"]:
        print(f"\nRefused ({len(outcome['errors'])}):")
        for failure in outcome["errors"]:
            print(f"  #{failure['number']}  {failure['error']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_encode = sub.add_parser("encode", help="symbolic priority/estimate -> labels")
    p_encode.add_argument(
        "--priority", help=f"one of {', '.join((*PRIORITY_RANKS, PRIORITY_NONE))}"
    )
    p_encode.add_argument("--estimate", type=int)
    p_encode.add_argument(
        "--human-set",
        action="store_true",
        help="the values are a human's (a drafted task card), so `urgent` is allowed",
    )
    p_encode.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    p_encode.add_argument("--json", action="store_true", dest="as_json")

    p_scan = sub.add_parser("scan", help="open issues missing prio:/est:")
    p_scan.add_argument("--repo", required=True, help="owner/name")
    p_scan.add_argument("--milestone", help="narrow to one milestone title")
    p_scan.add_argument("--limit", type=int, default=500)
    p_scan.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    p_scan.add_argument("--json", action="store_true", dest="as_json")

    p_apply = sub.add_parser("apply", help="write prio:/est: and nothing else")
    p_apply.add_argument("--repo", required=True, help="owner/name")
    p_apply.add_argument("--plan", required=True, help="JSON list of plan entries")
    p_apply.add_argument("--apply", action="store_true", help="send the PATCHes")
    p_apply.add_argument(
        "--provenance-issue",
        type=int,
        help="post the batch's ONE provenance comment on this issue",
    )
    p_apply.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    p_apply.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)

    if args.command == "encode":
        groups, _colors = load_vocabulary(args.labels_file)
        try:
            labels, notes = encode(
                groups, args.priority, args.estimate, human_set=args.human_set
            )
        except BackfillError as exc:
            print(f"refusing to encode: {exc}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps({"labels": labels, "notes": notes}, indent=2))
        else:
            print(",".join(labels))
            for note in notes:
                print(f"# {note}", file=sys.stderr)
        return 0

    if args.command == "scan":
        result = scan(args.repo, args.labels_file, args.limit, args.milestone)
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            report_scan(result)
        return 0

    entries = load_plan(args.plan)
    outcome = apply_plan(args.repo, entries, args.labels_file, args.apply)
    # A dry run posts nothing. The comment is a write like any other, and the
    # report below is what a dry run is for.
    # Seeded unconditionally: `--json` is an interface, and a key that appears
    # only on success makes the absent case indistinguishable from an old
    # version of this script.
    outcome["provenance_issue"] = None
    outcome["provenance_error"] = None
    if args.apply and args.provenance_issue and outcome["backfilled"]:
        outcome["provenance_issue"] = args.provenance_issue
        outcome["provenance_error"] = post_provenance(
            args.repo, args.provenance_issue, provenance_body(outcome)
        )
    if args.as_json:
        print(json.dumps(outcome, indent=2))
    else:
        report_apply(outcome)
        if outcome["provenance_error"]:
            print(f"\n{outcome['provenance_error']}", file=sys.stderr)
    return 1 if outcome["errors"] or outcome["provenance_error"] else 0


if __name__ == "__main__":
    sys.exit(main())
