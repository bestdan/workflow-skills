#!/usr/bin/env python3
"""Prove the landed GitHub board matches the Linear import, by disbelieving it.

`linear-import.py --apply` and `--link` each checked what they had just written,
using the same join, the same files and the same assumptions. That is the
weakness this script exists to cover: it reads the LIVE issues and compares them
against the plan and the export, and its value is entirely in disagreeing.

    python3 linear-verify.py --export <export.json> --plan-file <plan.json> \
        --mapping <mapping.json> --repo owner/name [--json]

Read-only: every `gh` call is a GET, so it runs sandboxed.

Six checks, each reporting the offending keys rather than a count:

1. `mapping`      every plan key has a mapping record at `phase: done` whose
                  issue is open.
2. `fields`       title, body, milestone and assignee equal the plan entry.
3. `labels`       the four managed namespaces equal the plan's `managed_labels`,
                  and the live issue carries the plan's `carried_labels`.
4. `vocabulary`   each open issue carries exactly one `status:` and one `auto:`
                  label — asked of `_labels.py`'s vocabulary, independently of
                  the plan, so a rung the plan never mentioned is still caught.
5. `edges`        the `blocked_by` pairs on the board equal the export's `blocks`
                  relations whose both ends are mapped. SETS, not counts: one
                  missing edge plus one wrong edge nets to zero.
6. `sub_issues`   the parent/child pairs on the board equal the export's.
7. `comments`     every export issue with comments carries exactly one comment
                  bearing the `linear-import: comments <key>` marker.

**The join is never re-derived.** Numbers come from the mapping and nothing else;
shapes come from the EXPORT, not from the plan's own `blocked_by` and `parent`
fields. A verifier that recomputed the plan's arithmetic could only confirm the
plan's arithmetic.

Three things the import did deliberately are reported as NOTES rather than
failures, because each one is a true difference from the plan that is not a
defect (see `dev_docs/gh-issue-migration/HANDOFF.md`):

- A mapped issue **closed since the import** and carrying no rung: work that has
  since been completed. Its `status:`/`auto:` rungs are then expected to be gone,
  so the label comparison drops them for that issue. A closed issue that still
  carries a rung is the opposite case and FAILS — it is one of the two label
  invariants with no reconciler rule.
- A **reopened** issue keeping an unmanaged label its GitHub original already had
  (`papercut` on #288). `gh-issue-state.py` carries forward everything outside
  the four managed namespaces, so that label is absent from the plan's
  `carried_labels` by design.
- `--link` rewrote 33 bodies to `#number` AFTER `--apply` wrote them, so the plan
  holds the pre-link text. The comparison therefore runs the plan body through
  `linear-import.rewrite_body()` first, which is idempotent — one fact, one home,
  rather than a second copy of the rewrite rules here.
"""

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ASSET_DIR))

from _labels import (  # noqa: E402
    AT_MOST_ONE,
    EXACTLY_ONE,
    VocabularyError,
    expected_labels,
    load_vocabulary,
)


class VerifyError(Exception):
    """Something the verifier cannot read. Distinct from a failed CHECK: a
    failed check is a finding about the board, this is the verifier being
    unable to form an opinion at all, and the two must never print alike."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_import_module():
    """`linear-import.py` as a module, for `rewrite_body` and `COMMENT_MARKER`.

    Imported rather than restated. The body rewrite has two rules that were
    derived from measurements of the real export (a URL-bearing reference only,
    and an unmapped key left alone), and a second copy here would drift from the
    one the board was actually written with — which is the one failure mode a
    verifier must not have.
    """
    path = ASSET_DIR / "linear-import.py"
    spec = importlib.util.spec_from_file_location("linear_import", path)
    if spec is None or spec.loader is None:
        raise VerifyError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise VerifyError(f"cannot read the {what} at {path}: {exc}")
    except json.JSONDecodeError as exc:
        raise VerifyError(f"the {what} at {path} is not valid JSON: {exc}")


def export_index(export):
    """{identifier: issue} for the whole export, archived entries included."""
    issues = export.get("issues")
    if not isinstance(issues, list):
        raise VerifyError("the export carries no `issues` list")
    return {issue["identifier"]: issue for issue in issues if issue.get("identifier")}


# ---------------------------------------------------------------------------
# GitHub reads. Every call is a GET.
# ---------------------------------------------------------------------------


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def gh_json(args, what, default=None):
    code, out, err = run_gh(args)
    if code != 0:
        raise VerifyError(f"{what} failed: {(err or out).strip()}")
    if not out.strip():
        return default
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise VerifyError(f"{what} returned unreadable JSON: {exc}")


ISSUE_FIELDS = "number,title,body,labels,milestone,assignees,state,stateReason,comments"


def read_issue(repo, number):
    payload = gh_json(
        ["issue", "view", str(number), "--repo", repo, "--json", ISSUE_FIELDS],
        f"reading {repo}#{number}",
        default={},
    )
    return payload or {}


def read_blockers(repo, number):
    """The numbers on this issue's `blocked_by` list.

    `--paginate --slurp` for gh-issue-deps.py's reason: a bare read stops at 30,
    and an edge past that page reads as absent — which here would be reported as
    a MISSING edge that is in fact present, the most expensive kind of wrong
    answer a verifier can give.
    """
    pages = gh_json(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/issues/{number}/dependencies/blocked_by",
        ],
        f"reading blocked_by for {repo}#{number}",
        default=[],
    )
    return {entry["number"] for page in pages or [] for entry in page}


def read_sub_issues(repo, number):
    """The child numbers attached to this issue, paginated for the same reason."""
    pages = gh_json(
        ["api", "--paginate", "--slurp", f"repos/{repo}/issues/{number}/sub_issues"],
        f"reading the sub-issues of {repo}#{number}",
        default=[],
    )
    return {entry["number"] for page in pages or [] for entry in page}


def viewer_login():
    payload = gh_json(["api", "user"], "resolving the authenticated user", default={})
    login = (payload or {}).get("login")
    if not login:
        raise VerifyError("resolving the authenticated user returned no `login`")
    return login


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


def mapped_numbers(plan, mapping):
    """(key -> number, [findings]) for the plan's keys.

    The mapping is the ONLY source of numbers. `--link` established that a
    recorded number is stable whatever the record's phase — it is written once,
    at phase `created`, by an `update` that never clears it — so the phase is
    checked as its own finding rather than used to gate the join. A key with a
    number but a phase short of `done` is still read from the board, because
    what the board says about it is exactly what the reader wants to know.
    """
    records = mapping.get("entries") or {}
    numbers, findings = {}, []
    for entry in plan["entries"]:
        key = entry["key"]
        record = records.get(key)
        if record is None:
            findings.append({"key": key, "problem": "no mapping record"})
            continue
        number = record.get("number")
        if number is None:
            findings.append({"key": key, "problem": "mapping record has no number"})
            continue
        numbers[key] = number
        phase = record.get("phase")
        if phase != "done":
            findings.append(
                {"key": key, "number": number, "problem": f"phase is {phase!r}"}
            )
    return numbers, findings


def export_edges(index, numbers):
    """Expected (blocked, blocker) NUMBER pairs, from the export's relations.

    Both directions are read and unioned. `--plan` reads `inverseRelations`
    alone, correctly — reading both there would double every edge — but a
    verifier that reads the same half can only agree with it. Linear mirrors the
    two, so the union is the same set when the export is well-formed and a
    superset when it is not, which is the direction a verifier wants to err in.
    """
    pairs = set()
    for key, number in numbers.items():
        issue = index.get(key)
        if issue is None:
            continue
        for relation in issue.get("inverseRelations") or []:
            if relation.get("type") != "blocks":
                continue
            blocker = numbers.get(relation.get("identifier"))
            if blocker is not None:
                pairs.add((number, blocker))
        for relation in issue.get("relations") or []:
            if relation.get("type") != "blocks":
                continue
            blocked = numbers.get(relation.get("identifier"))
            if blocked is not None:
                pairs.add((blocked, number))
    return pairs


def export_sub_issues(index, numbers):
    """Expected (parent, child) NUMBER pairs, from the export's parent/children.

    Both fields, for the reason above. They are not redundant here: the export's
    `children` list is populated for only some parents, while `parent` is set on
    every child, so the union is strictly larger than either field alone.
    """
    pairs = set()
    for key, number in numbers.items():
        issue = index.get(key)
        if issue is None:
            continue
        parent = numbers.get(issue.get("parent"))
        if parent is not None:
            pairs.add((parent, number))
        for child_key in issue.get("children") or []:
            child = numbers.get(child_key)
            if child is not None:
                pairs.add((number, child))
    return pairs


def collect(repo, numbers):
    """Read every mapped issue once: fields, blockers, children."""
    live = {}
    for key, number in numbers.items():
        live[key] = {
            "number": number,
            "issue": read_issue(repo, number),
            "blockers": read_blockers(repo, number),
            "children": read_sub_issues(repo, number),
        }
    return live


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check(name, summary, failures, notes=None):
    return {
        "name": name,
        "ok": not failures,
        "summary": summary,
        "failures": failures,
        "notes": notes or [],
    }


def is_closed(record):
    return (record["issue"].get("state") or "").upper() == "CLOSED"


def label_names(record):
    return {label["name"] for label in record["issue"].get("labels") or []}


def rung_labels(vocabulary):
    """The `status:`/`auto:` labels labels.yml defines — the rungs a closed issue
    must not carry. Read from the vocabulary rather than by prefix, for the
    reason `check_vocabulary` states."""
    return {
        label
        for group in EXACTLY_ONE
        for label in vocabulary
        if label.startswith(group + ":")
    }


def retired_keys(live, vocabulary):
    """Mapped keys that are closed AND carry no `status:`/`auto:` rung.

    Work completed since the import, not an import defect — the import wrote an
    open issue with rungs and `gh-issue-state.py --done` has since closed it and
    stripped them. Separating this set from "closed with rungs still on it" is
    the whole point: the latter is a real invariant break with no reconciler
    rule, and collapsing the two would hide it behind the benign case.
    """
    rungs = rung_labels(vocabulary)
    return {
        key
        for key, record in live.items()
        if is_closed(record) and not (label_names(record) & rungs)
    }


def check_mapping(plan, join_findings, live, retired):
    failures = list(join_findings)
    notes = []
    for key in sorted(live):
        record = live[key]
        if not is_closed(record):
            continue
        detail = {"key": key, "number": record["number"]}
        if key in retired:
            detail["state_reason"] = record["issue"].get("stateReason")
            notes.append(detail)
        else:
            detail["problem"] = "closed while still carrying a status:/auto: rung"
            detail["labels"] = sorted(label_names(record))
            failures.append(detail)
    return check(
        "mapping",
        f"{len(live)} of {len(plan['entries'])} plan entries mapped and read; "
        f"{len(notes)} closed since the import",
        failures,
        notes,
    )


def check_fields(plan, live, numbers, rewrite_body, assignee_login):
    failures = []
    for entry in plan["entries"]:
        key = entry["key"]
        record = live.get(key)
        if record is None:
            continue
        issue = record["issue"]
        wrong: dict = {}
        if issue.get("title") != entry["title"]:
            wrong["title"] = {"want": entry["title"], "got": issue.get("title")}
        want_body = rewrite_body(entry["body"], numbers)
        if (issue.get("body") or "") != want_body:
            wrong["body"] = "differs from the plan body as --link would rewrite it"
        milestone = (issue.get("milestone") or {}).get("title")
        if milestone != entry["milestone"]:
            wrong["milestone"] = {"want": entry["milestone"], "got": milestone}
        want_assignee = entry.get("assignee")
        if want_assignee == "@me":
            want_assignee = assignee_login()
        logins = {who["login"] for who in issue.get("assignees") or []}
        if want_assignee and want_assignee not in logins:
            wrong["assignee"] = {"want": want_assignee, "got": sorted(logins)}
        elif not want_assignee and logins:
            wrong["assignee"] = {"want": None, "got": sorted(logins)}
        if wrong:
            failures.append({"key": key, "number": record["number"], "wrong": wrong})
    return check(
        "fields",
        f"title, body, milestone and assignee compared on {len(live)} issues",
        failures,
    )


def check_labels(plan, live, vocabulary, retired):
    """The plan's label contract, namespace by namespace.

    Managed labels are compared as an equality because the import owns that set
    outright. Carried labels are compared as a SUBSET, because they are not the
    whole story: `gh-issue-state.py` carries forward every label outside the four
    managed namespaces, so a reopened issue keeps what its GitHub original had.
    """
    rungs = rung_labels(vocabulary)
    failures, notes = [], []
    for entry in plan["entries"]:
        key = entry["key"]
        record = live.get(key)
        if record is None:
            continue
        names = label_names(record)
        live_managed = names & set(vocabulary)
        live_other = names - set(vocabulary)
        want_managed = set(entry["managed_labels"])
        if key in retired:
            want_managed -= rungs
        want_carried = set(entry["carried_labels"])
        wrong: dict = {}
        if live_managed != want_managed:
            wrong["managed"] = {
                "missing": sorted(want_managed - live_managed),
                "unexpected": sorted(live_managed - want_managed),
            }
        missing_carried = sorted(want_carried - live_other)
        if missing_carried:
            wrong["carried_missing"] = missing_carried
        extra = sorted(live_other - want_carried)
        if extra:
            detail = {"key": key, "number": record["number"], "labels": extra}
            if entry["action"] == "reopen":
                notes.append(detail)
            else:
                wrong["carried_unexpected"] = extra
        if wrong:
            failures.append({"key": key, "number": record["number"], "wrong": wrong})
    return check(
        "labels",
        f"managed sets and carried labels compared on {len(live)} issues; "
        f"{len(notes)} reopened issues kept a label their GitHub original had",
        failures,
        notes,
    )


def check_vocabulary(live, groups):
    """The cardinality rule, asked of the vocabulary and not of the plan.

    The prefix reading is the trap this avoids: a hand-typed `status:blocked`
    starts with `status:` and would satisfy a prefix test, leaving the issue
    reading as healthy while being in a state nothing can act on. Membership in
    `labels.yml` is the question.
    """
    members = {
        group: {f"{group}:{value}" for value in values}
        for group, values in groups.items()
    }
    failures = []
    for key in sorted(live):
        record = live[key]
        names = label_names(record)
        closed = is_closed(record)
        wrong: dict = {}
        for group in EXACTLY_ONE:
            held = sorted(names & members[group])
            # A closed issue carries neither rung — the same invariant read from
            # its other end, which is what makes `--done` and this check agree.
            if len(held) != (0 if closed else 1):
                wrong[group] = held
        for group in AT_MOST_ONE:
            held = sorted(names & members[group])
            if len(held) > 1:
                wrong[group] = held
        if wrong:
            failures.append(
                {
                    "key": key,
                    "number": record["number"],
                    "state": record["issue"].get("state"),
                    "wrong": wrong,
                }
            )
    return check(
        "vocabulary",
        f"label cardinality checked against labels.yml on {len(live)} issues",
        failures,
    )


def check_edges(index, live, numbers):
    expected = export_edges(index, numbers)
    actual = {
        (record["number"], blocker)
        for record in live.values()
        for blocker in record["blockers"]
    }
    failures = [
        {"blocked": blocked, "blocker": blocker, "problem": "missing on the board"}
        for blocked, blocker in sorted(expected - actual)
    ] + [
        {"blocked": blocked, "blocker": blocker, "problem": "not in the export"}
        for blocked, blocker in sorted(actual - expected)
    ]
    return check(
        "edges",
        f"{len(expected)} expected blocked_by pairs, {len(actual)} on the board",
        failures,
    )


def check_sub_issues(index, live, numbers):
    expected = export_sub_issues(index, numbers)
    actual = {
        (record["number"], child)
        for record in live.values()
        for child in record["children"]
    }
    failures = [
        {"parent": parent, "child": child, "problem": "missing on the board"}
        for parent, child in sorted(expected - actual)
    ] + [
        {"parent": parent, "child": child, "problem": "not in the export"}
        for parent, child in sorted(actual - expected)
    ]
    return check(
        "sub_issues",
        f"{len(expected)} expected parent/child pairs, {len(actual)} on the board",
        failures,
    )


def check_comments(index, live, marker_template):
    """One transcript per commented issue — no more, and no fewer.

    The upper bound is the one worth stating: `--apply` recognises its own marker
    and skips, so a SECOND transcript means something posted outside that path,
    and a reader comparing counts would see the transcript present and stop.
    """
    failures = []
    expected = 0
    for key in sorted(live):
        record = live[key]
        issue = index.get(key) or {}
        want = 1 if (issue.get("comments") or []) else 0
        expected += want
        marker = marker_template.format(key=key)
        found = sum(
            1
            for comment in record["issue"].get("comments") or []
            if marker in (comment.get("body") or "")
        )
        if found != want:
            failures.append(
                {
                    "key": key,
                    "number": record["number"],
                    "want": want,
                    "got": found,
                }
            )
    return check(
        "comments",
        f"{expected} issues carry comments in the export",
        failures,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def verify(export, plan, mapping, repo, importer, groups, vocabulary):
    index = export_index(export)
    numbers, join_findings = mapped_numbers(plan, mapping)
    live = collect(repo, numbers)
    retired = retired_keys(live, vocabulary)

    cache = {}

    def assignee_login():
        if "login" not in cache:
            cache["login"] = viewer_login()
        return cache["login"]

    return [
        check_mapping(plan, join_findings, live, retired),
        check_fields(plan, live, numbers, importer.rewrite_body, assignee_login),
        check_labels(plan, live, vocabulary, retired),
        check_vocabulary(live, groups),
        check_edges(index, live, numbers),
        check_sub_issues(index, live, numbers),
        check_comments(index, live, importer.COMMENT_MARKER),
    ]


def render(checks, repo):
    print(f"linear-verify: {repo}")
    for result in checks:
        print(f"\n{'PASS' if result['ok'] else 'FAIL'}  {result['name']}")
        print(f"      {result['summary']}")
        for note in result["notes"]:
            print(f"      note: {json.dumps(note, sort_keys=True)}")
        for failure in result["failures"]:
            print(f"      {json.dumps(failure, sort_keys=True)}")
    failed = [result["name"] for result in checks if not result["ok"]]
    print()
    if failed:
        print(f"{len(failed)} check(s) failed: {', '.join(failed)}")
    else:
        print(f"all {len(checks)} checks passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export", required=True, help="the linear-export.py file")
    parser.add_argument(
        "--plan-file", required=True, dest="plan", help="the --plan import plan"
    )
    parser.add_argument(
        "--mapping", required=True, help="the mapping file --apply wrote"
    )
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        groups, colors = load_vocabulary()
        vocabulary = expected_labels(groups, colors)
        export = load_json(args.export, "export")
        plan = load_json(args.plan, "import plan")
        mapping = load_json(args.mapping, "mapping file")
        if not isinstance(plan.get("entries"), list):
            raise VerifyError(f"the import plan at {args.plan} carries no entries")
        checks = verify(
            export,
            plan,
            mapping,
            args.repo,
            load_import_module(),
            groups,
            vocabulary,
        )
    except (VerifyError, VocabularyError) as exc:
        print(f"linear-verify: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps({"repo": args.repo, "checks": checks}, indent=2))
    else:
        render(checks, args.repo)
    return 0 if all(result["ok"] for result in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
