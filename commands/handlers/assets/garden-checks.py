#!/usr/bin/env python3
"""Deterministic checks for the `/garden-backlog` core, around the judges.

`commands/garden-backlog.md` §4 is the prose half. The judges are parallel
subagents, so anything two of them could read differently is decided here
instead, once, before or after they run:

  prejudge    before fan-out: add `checks.has_acceptance_criteria` to each
              listing row, from the description alone
  finalize    after the records are merged: reconcile quality with that check,
              derive the status target, and apply the write holds
  key-search  validity rung 2's search for a ticket key: PRs and commits that
              carry the key as a whole token

WHY `has_acceptance_criteria` IS A SCRIPT. In the #1522 calibration one judge
read the condition as "a literal Acceptance Criteria section" and failed 15 of
15 tickets; the other allowed `jira-promote.md`'s equivalent outcome and
passed 10 of 15. Status follows quality, so the chunk a ticket landed in
decided its status. `ac_forms` names the forms that satisfy the condition, and
its verdict is final both ways; the judge rules on the other conditions only.

WHY THE STATUS TARGET IS DERIVED HERE. It is a function of the quality verdict,
the row's status and its blockers, so a judge has nothing to add, and a judge
that forgets a blocker moves a blocked ticket to Ready. `finalize` overwrites
the judge's target and rebuilds the `status`, `human-queue` and `gardened`
writes from it. The holds:

- An unresolved "is blocked by" link holds Ready: the target is `unchanged`,
  and the ticket gets no `gardened` write either, so the next run re-judges it
  once the blocker clears. A row whose links were never read (`blocked_by`
  null) is held the same way: unread is not unblocked.
- Validity not settled (`unsure`, or a closure verdict below `high`) holds the
  `status` and `kind` writes. The record still shows the target.
- A judge that failed `has_acceptance_criteria` where the check passed did not
  rule on the conditions after it, so its quality verdict is unknown: the
  status write is held and the ticket queued for a human.

WHY KEY-SEARCH IS A SCRIPT. As prose, rung 2 said `gh search prs "<key>"` and
`git log --grep`. GitHub search tokenizes `IP-39` and matches the bare `IP`,
and a `\\b` in git's extended regex silently matches nothing, so both missed a
PR tagged with its key. Here the GitHub search is a quoted phrase, the git
search a fixed string, and both are post-filtered by `key_in_text`, so
`AB-12` never matches `AB-120`.

Exit codes: 0 success; 2 usage or bad input; 4 a `gh` or `git` call failed
(key-search still prints what it found, with the failures under `errors`).
"""

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

AC_NAMES = (
    "acceptance criteria",
    "acceptance criterion",
    "ac",
    "definition of done",
    "done when",
    "success criteria",
    "requirements",
)
EXPECTED_NAMES = (
    "expected",
    "expected behavior",
    "expected behaviour",
    "expected result",
    "expected results",
    "expected outcome",
)
ACTUAL_NAMES = (
    "actual",
    "actual behavior",
    "actual behaviour",
    "actual result",
    "actual results",
    "current behavior",
    "current behaviour",
    "observed",
    "observed behavior",
    "observed behaviour",
)
REPRO_NAMES = (
    "steps to reproduce",
    "steps to repro",
    "repro steps",
    "reproduction steps",
    "repro",
    "to reproduce",
    "how to reproduce",
)
KNOWN = {
    **{n: "ac" for n in AC_NAMES},
    **{n: "expected" for n in EXPECTED_NAMES},
    **{n: "actual" for n in ACTUAL_NAMES},
    **{n: "repro" for n in REPRO_NAMES},
}
MD_HEADING = re.compile(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*\s*:?\s*$)")
CLOSURE = ("fixed", "duplicate", "obsolete")
STARTED = ("started", "other")
AC_YIELDS = ("pass", "no_open_questions_or_tbd")
HUMAN_WRITE = {"group": "human-queue", "field": "labels", "value": "garden:needs-human"}
GARDENED_WRITE = {"group": "gardened", "field": "comment", "value": None}


class InputError(Exception):
    """Bad arguments or an input in a shape this script does not know."""


# ------------------------------------------------- has_acceptance_criteria


def heading_of(line: str) -> Tuple[Optional[str], str]:
    """`(section kind, same-line content)` when `line` opens a known section.

    A heading may be markdown (`## Expected`, which is also how jira-garden.py
    flattens an ADF heading), bold (`**Expected:**`), a bare line, or a label
    with its content after the colon (`Expected: the chart loads`).
    """
    s = line.strip()
    s = re.sub(r"^#{1,6}\s*", "", s)
    head, sep, rest = s.partition(":")
    for candidate, content in ((head, rest if sep else ""), (s, "")):
        name = re.sub(r"[*_`]", "", candidate).strip().rstrip(":").strip().casefold()
        if name in KNOWN:
            return KNOWN[name], re.sub(r"^[*_\s]+", "", content).strip()
    return None, ""


def md_level(line: str) -> Optional[int]:
    """The level of a `#` markdown heading, else None."""
    m = re.match(r"^\s*(#{1,6})\s", line)
    return len(m.group(1)) if m else None


def sections_with_content(description: Any) -> Dict[str, bool]:
    """Which known sections the description opens, each with content.

    A markdown heading ends the open section only at the same level or
    higher; a deeper one is a subsection of it. A bold heading has no level,
    so it always ends the section.
    """
    found: Dict[str, bool] = {}
    if not isinstance(description, str):
        return found
    current: Optional[str] = None
    current_level: Optional[int] = None
    for line in description.splitlines():
        level = md_level(line)
        kind, inline = heading_of(line)
        if kind is not None:
            current, current_level = kind, level
            found.setdefault(kind, False)
            if inline:
                found[kind] = True
            continue
        if MD_HEADING.match(line):
            deeper = (
                level is not None
                and current_level is not None
                and level > current_level
            )
            if not deeper:
                current, current_level = None, None
            continue
        if current is not None and line.strip(" \t-*•"):
            found[current] = True
    return found


def ac_forms(description: Any) -> List[str]:
    """The acceptance-criteria forms the description carries, possibly none.

    `section`: an Acceptance Criteria section or an equivalent heading
    (Definition of done, Done when, Success criteria, Requirements) with
    content. `expected-actual`: a bug report with Expected, Actual and
    repro-steps sections, each with content.
    """
    found = sections_with_content(description)
    forms = []
    if found.get("ac"):
        forms.append("section")
    if found.get("expected") and found.get("actual") and found.get("repro"):
        forms.append("expected-actual")
    return forms


def prejudge(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        forms = ac_forms(row.get("description"))
        out.append(
            {
                **row,
                "checks": {
                    "has_acceptance_criteria": "pass" if forms else "fail",
                    "ac_forms": forms,
                },
            }
        )
    return out


# ---------------------------------------------------------------- finalize


def validity_settled(record: Dict[str, Any]) -> bool:
    v = record.get("validity") or {}
    verdict, confidence = v.get("verdict"), v.get("confidence")
    if verdict == "unsure" or verdict is None:
        return False
    if verdict in CLOSURE and confidence != "high":
        return False
    return True


def closure_stop(record: Dict[str, Any]) -> bool:
    v = record.get("validity") or {}
    return v.get("verdict") in CLOSURE and v.get("confidence") == "high"


def add_reason(record: Dict[str, Any], reason: str) -> None:
    queue = record.setdefault("human_queue", [])
    if reason not in queue:
        queue.append(reason)


def finalize_one(record: Dict[str, Any], row: Optional[Dict[str, Any]]) -> None:
    if row is None:
        add_reason(record, "no listing row for this record")
        record["proposed_writes"] = [
            w for w in record.get("proposed_writes") or [] if w.get("group") != "status"
        ]
        rebuild_queue_writes(record, held=True)
        return

    check = (row.get("checks") or {}).get("has_acceptance_criteria")
    if check is None:
        check = "pass" if ac_forms(row.get("description")) else "fail"
    quality = record.get("quality")
    quality_unknown = False
    # Only the conditions after has_acceptance_criteria give way to it. A
    # title failure comes first, and scope_fits may hold the slot by
    # task-fill.md's reason precedence.
    if check == "fail" and quality in AC_YIELDS:
        record["quality"] = "has_acceptance_criteria"
    elif check == "pass" and quality == "has_acceptance_criteria":
        quality_unknown = True
        add_reason(
            record,
            "quality: the judge failed has_acceptance_criteria but the description "
            "carries it; the later conditions were not judged",
        )
    quality = record.get("quality")

    current = row.get("status")
    blocked_by = row.get("blocked_by")
    blocked = blocked_by is None or bool(blocked_by)
    if current in STARTED:
        target, reason = "unchanged", f"{current} work is never moved"
    elif quality == "pass" and blocked:
        target = "unchanged"
        reason = (
            "blocked by " + ", ".join(blocked_by)
            if blocked_by
            else "blocker links were not read"
        )
    elif quality == "pass":
        target, reason = "ready", "quality pass"
    elif quality_unknown:
        target = "unchanged"
        reason = "quality unknown: the judge failed has_acceptance_criteria but the check passed"
    else:
        target, reason = "refinement", str(quality)
    record["status"] = {"target": target, "reason": reason}

    writes = [
        w
        for w in record.get("proposed_writes") or []
        if w.get("group") not in ("status", "human-queue", "gardened")
    ]
    settled = validity_settled(record)
    if not settled:
        writes = [w for w in writes if w.get("group") != "kind"]
    if target in ("ready", "refinement") and target != current:
        if settled and not quality_unknown:
            writes.append({"group": "status", "field": "status", "value": target})
    record["proposed_writes"] = writes
    rebuild_queue_writes(
        record, held=current not in STARTED and quality == "pass" and blocked
    )


def rebuild_queue_writes(record: Dict[str, Any], held: bool) -> None:
    writes = [
        w
        for w in record.get("proposed_writes") or []
        if w.get("group") not in ("human-queue", "gardened")
    ]
    if record.get("human_queue"):
        writes.append(dict(HUMAN_WRITE))
    elif not writes and not held:
        writes.append(dict(GARDENED_WRITE))
    record["proposed_writes"] = writes


def finalize(
    rows: List[Dict[str, Any]], records: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    by_key = {r.get("key"): r for r in rows if isinstance(r, dict)}
    out = []
    for rec in records:
        if not isinstance(rec, dict) or not isinstance(rec.get("key"), str):
            raise InputError(f"record without a key: {rec!r}")
        rec = json.loads(json.dumps(rec))
        if rec.get("early_exit") or closure_stop(rec):
            out.append(rec)
            continue
        finalize_one(rec, by_key.get(rec["key"]))
        out.append(rec)
    return out


# -------------------------------------------------------------- key-search


class Proc(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def run(argv: Sequence[str]) -> Proc:
    """The single subprocess seam; tests stub it."""
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True)
    except OSError as exc:
        return Proc(127, "", str(exc))
    return Proc(p.returncode, p.stdout, p.stderr)


def key_in_text(key: str, text: Any) -> bool:
    """Does `text` carry `key` as a whole token, in any case?"""
    if not key or not isinstance(text, str):
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(key) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def search_prs(key: str, repo: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    res = run(
        [
            "gh",
            "pr",
            "list",
            "-R",
            repo,
            "--state",
            "all",
            "--limit",
            "100",
            "--search",
            f'"{key}"',
            "--json",
            "number,url,title,body,state,mergedAt",
        ]
    )
    if res.returncode != 0:
        return [], f"gh pr search for {key} in {repo} failed: {res.stderr.strip()}"
    try:
        hits = json.loads(res.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [], f"gh pr search for {key} printed invalid JSON: {exc}"
    prs = []
    for h in hits if isinstance(hits, list) else []:
        where = (
            "title"
            if key_in_text(key, h.get("title"))
            else "body"
            if key_in_text(key, h.get("body"))
            else None
        )
        if where:
            prs.append(
                {
                    "number": h.get("number"),
                    "url": h.get("url"),
                    "title": h.get("title"),
                    "state": "MERGED" if h.get("mergedAt") else h.get("state"),
                    "merged_at": h.get("mergedAt"),
                    "match": where,
                }
            )
    return prs, None


def search_commits(
    key: str, checkout: str, ref: str
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    res = run(
        [
            "git",
            "-C",
            checkout,
            "log",
            ref,
            "-i",
            "-F",
            f"--grep={key}",
            "--format=%H%x1f%s%x1f%b%x1e",
        ]
    )
    if res.returncode != 0:
        return [], f"git log for {key} at {ref} failed: {res.stderr.strip()}"
    commits = []
    for entry in res.stdout.split("\x1e"):
        parts = entry.strip("\n").split("\x1f")
        if len(parts) < 2 or not parts[0].strip():
            continue
        sha, subject = parts[0].strip(), parts[1]
        body = parts[2] if len(parts) > 2 else ""
        if key_in_text(key, subject) or key_in_text(key, body):
            commits.append({"sha": sha, "subject": subject})
    return commits, None


def key_search(
    key: str, repos: List[str], checkouts: List[str], ref: str
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "key": key,
        "ref": ref,
        "prs": [],
        "commits": [],
        "errors": [],
    }
    for repo in repos:
        prs, err = search_prs(key, repo)
        out["prs"] += [{**p, "repo": repo} for p in prs]
        if err:
            out["errors"].append(err)
    for checkout in checkouts:
        commits, err = search_commits(key, checkout, ref)
        out["commits"] += [{**c, "checkout": checkout} for c in commits]
        if err:
            out["errors"].append(err)
    return out


# -------------------------------------------------------------------- main


def load_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def load_rows(path: str) -> List[Dict[str, Any]]:
    """A row list, bare or under `rows` (jira-garden.py rows' output)."""
    data = load_json(path)
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        data = data["rows"]
    if not isinstance(data, list):
        raise InputError(f"{path}: expected a list of rows")
    return [r for r in data if isinstance(r, dict)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("prejudge")
    s.add_argument("--rows", required=True)

    s = sub.add_parser("finalize")
    s.add_argument("--rows", required=True)
    s.add_argument("--records", required=True)

    s = sub.add_parser("key-search")
    s.add_argument("--key", required=True)
    s.add_argument("--repo", action="append", default=[])
    s.add_argument("--checkout", action="append", default=[])
    s.add_argument("--ref", default="origin/HEAD")

    args = p.parse_args(argv)
    try:
        if args.cmd == "prejudge":
            result: Any = prejudge(load_rows(args.rows))
        elif args.cmd == "finalize":
            records = load_json(args.records)
            if not isinstance(records, list):
                raise InputError("--records must be a JSON array")
            result = finalize(load_rows(args.rows), records)
        else:
            if not args.repo and not args.checkout:
                raise InputError("key-search needs at least one --repo or --checkout")
            result = key_search(args.key, args.repo, args.checkout, args.ref)
    except InputError as exc:
        print(f"garden-checks: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, indent=1)
    sys.stdout.write("\n")
    if args.cmd == "key-search" and result["errors"]:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
