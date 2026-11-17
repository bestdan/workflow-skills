#!/usr/bin/env python3
"""Tracker I/O helpers for the jira `/garden-backlog` handler.

`commands/handlers/jira-garden.md` is the prose half; this file owns every
part two runs must do identically, and every part a review round would
otherwise have to catch by reading markdown:

  jql      the candidate query and the `priority CHANGED` query for a scope
  fetch    page a JQL search to completion through twg, one file per page
  page     read one saved search page: its issue count and next-page token
  rows     turn saved pages into the core's List rows (symbolic values only)
  fields   turn one ticket's approved writes into an edit payload
  comment  render the provenance comment the next run's early exit reads

WHY THE PROVENANCE COMMENT IS PARSED HERE. Three judgments in `rows` depend on
it, and each fails silently in the direction of re-gardening or of trusting a
value nobody chose:

- `gardened_at` is the newest comment that starts with MARKER.
- `updated_at` excludes that comment: posting it bumps the issue's own
  `updated`, so a raw comparison would make every gardened ticket look edited
  since. An `updated` within UPDATE_SLACK of `gardened_at` is reported as
  `gardened_at`.
- A non-empty estimate or a non-default priority is human-set unless the
  newest comment that recorded a write to that field wrote the value the
  ticket holds now. Otherwise the garden's own write would make the field
  human-set, and the next run would refuse to touch its own work.

`comment` renders the body and `parse_comment` reads it, so the two cannot
drift apart; the tests round-trip one through the other.

Read-only, except that `fetch` writes page files into `--out-dir`. Nothing here
writes to Jira.

Exit codes: 0 success; 2 usage or bad input; 4 a twg call failed.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "jira_pr_sources", os.path.join(HERE, "jira-pr-sources.py")
)
assert _spec is not None and _spec.loader is not None, "cannot load jira-pr-sources.py"
pr_sources = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_sources)

MARKER = "garden-backlog:"
CLOSE_LABEL = "garden:close-candidate"
HUMAN_LABEL = "garden:needs-human"
IMPORTANCE_LEVELS = ("blocker", "critical", "major", "minor", "trivial")
UPDATE_SLACK = timedelta(seconds=5)
BASE_FILTER = (
    "statusCategory != Done AND issuetype != Epic"
    " AND (sprint IS EMPTY OR sprint NOT IN openSprints())"
)
PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
# The bullet is optional: a markdown body posted through the API comes back as
# ADF, whose list items flatten to bare lines.
WRITE_LINE = re.compile(
    r"^\s*(?:[-*]\s*)?(size|importance):\s*(.*?)\s*->\s*(.*?)\s*(?:;\s*reason:.*)?$"
)
META_KEYS = ("id", "key", "self", "expand", "url")


class InputError(Exception):
    """Bad arguments or a page in a shape this script does not know."""


class TwgFailed(Exception):
    """A twg call exited non-zero."""


class TwgResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def run_twg(args: Sequence[str]) -> TwgResult:
    """The single subprocess seam; tests stub it."""
    proc = subprocess.run(["twg", *args], capture_output=True, text=True)
    return TwgResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


# --------------------------------------------------------------------- jql


def scope_clause(scope: str) -> str:
    """A project key, an epic key, or a JQL fragment, as one JQL clause."""
    s = scope.strip()
    if not s:
        raise InputError("empty scope")
    if PROJECT_RE.match(s):
        return f'project = "{s}"'
    if ISSUE_KEY_RE.match(s):
        return f'parent = "{s}"'
    if re.search(r"\border\s+by\b", s, re.IGNORECASE):
        raise InputError(
            "a JQL scope must not carry ORDER BY; the handler sets the order"
        )
    return f"({s})"


def build_jql(scope: str) -> Dict[str, str]:
    where = f"{scope_clause(scope)} AND {BASE_FILTER}"
    return {
        "candidates": f"{where} ORDER BY key ASC",
        "priority_changed": f"{where} AND priority CHANGED ORDER BY key ASC",
    }


# ------------------------------------------------------------------- pages


def page_issues(payload: Any) -> List[Dict[str, Any]]:
    """The issues of one saved search page, from either backend's shape."""
    if not isinstance(payload, dict):
        raise InputError("page is not a JSON object")
    data = payload.get("data")
    holder = data if isinstance(data, dict) else payload
    issues = holder.get("issues")
    if isinstance(issues, dict):
        issues = issues.get("nodes")
    if not isinstance(issues, list):
        raise InputError("page has no `issues` list")
    return [i for i in issues if isinstance(i, dict)]


def page_next(payload: Any) -> Optional[str]:
    """The next-page token, or None on the last page.

    MCP: `nextPageToken` with `isLast`. twg: `pageInfo.nextCursor` with
    `pageInfo.hasNextPage`.
    """
    if not isinstance(payload, dict):
        raise InputError("page is not a JSON object")
    info = payload.get("pageInfo")
    if isinstance(info, dict):
        if not info.get("hasNextPage"):
            return None
        cursor = info.get("nextCursor")
        if not isinstance(cursor, str) or not cursor:
            raise InputError("pageInfo.hasNextPage is true but nextCursor is empty")
        return cursor
    if payload.get("isLast") is True:
        return None
    token = payload.get("nextPageToken")
    if isinstance(token, str) and token:
        return token
    if payload.get("isLast") is False:
        raise InputError("isLast is false but nextPageToken is empty")
    return None


def load_page(path: str) -> Any:
    """A JSON file: a saved page, or a writes / labels / comment-spec input."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def fetch(
    site: str, jql: str, fields: str, out_dir: str, limit: Optional[int], page_size: int
) -> Dict[str, Any]:
    """Page a search to completion (or `limit`), saving each page to a file.

    Stops with an error when a cursor repeats, rather than re-reading the same
    page forever.
    """
    os.makedirs(out_dir, exist_ok=True)
    paths: List[str] = []
    seen: Set[str] = set()
    count = 0
    cursor: Optional[str] = None
    while True:
        size = page_size if limit is None else min(page_size, limit - count)
        args = [
            "--mode",
            "agent",
            "--site",
            site,
            "jira",
            "workitem",
            "query",
            "--jql",
            jql,
            "--fields",
            fields,
            "--limit",
            str(size),
        ]
        if cursor:
            args += ["--after", cursor]
        args += ["-o", "json", "--output-summary", "none"]
        res = run_twg(args)
        if res.returncode != 0:
            raise TwgFailed(
                f"twg query exited {res.returncode}: {(res.stdout or res.stderr).strip()[:500]}"
            )
        try:
            payload = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise TwgFailed(f"twg query printed invalid JSON: {exc}") from exc
        path = os.path.join(out_dir, f"page-{len(paths) + 1:03d}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        paths.append(path)
        count += len(page_issues(payload))
        cursor = page_next(payload)
        if cursor is None:
            return {"pages": paths, "count": count, "limit_cut": False}
        if limit is not None and count >= limit:
            return {"pages": paths, "count": count, "limit_cut": True}
        if cursor in seen:
            raise TwgFailed("twg returned a cursor it had already returned; stopping")
        seen.add(cursor)


# -------------------------------------------------------------- comments


def parse_time(value: Any) -> Optional[datetime]:
    """Jira's `2026-11-16T16:58:45.995-0500`, or ISO 8601, as aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def comment_text(comment: Dict[str, Any]) -> str:
    return str(pr_sources.body_text(comment.get("body")))


def parse_comment(text: str) -> Optional[Dict[str, Tuple[str, str]]]:
    """The field writes a provenance comment records, or None if it is not one.

    Only a comment that *starts* with MARKER counts, so a human quoting one
    does not mark the ticket gardened.
    """
    if not text.lstrip().startswith(MARKER):
        return None
    writes: Dict[str, Tuple[str, str]] = {}
    for line in text.splitlines():
        m = WRITE_LINE.match(line)
        if m:
            writes[m.group(1)] = (m.group(2), m.group(3))
    return writes


def one_line(value: Any) -> str:
    """A value on one line: free text with a newline in it could otherwise
    carry a line `parse_comment` reads as a garden write."""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def render_comment(spec: Dict[str, Any]) -> str:
    """The provenance comment body (markdown) for one ticket's applied writes.

    `spec`: {"date", "changes": [{"field", "old", "new", "reason"}],
    "labels": [...], "human_queue": [...]}. A `size` or `importance` change is
    written as `- <field>: <old> -> <new>; reason: <reason>`, the line `parse_comment`
    reads back; any other field (status) uses the same shape.
    """
    date = spec.get("date")
    if not isinstance(date, str) or not date:
        raise InputError("comment spec needs `date`")
    lines = [f"{MARKER} {date}", ""]
    for ch in spec.get("changes") or []:
        if not isinstance(ch, dict) or not ch.get("field"):
            raise InputError(f"bad change {ch!r}")
        old = ch.get("old")
        old_s = "none" if old in (None, "") else one_line(old)
        reason = one_line(ch.get("reason") or "")
        line = f"- {one_line(ch['field'])}: {old_s} -> {one_line(ch.get('new'))}"
        lines.append(f"{line}; reason: {reason}" if reason else line)
    for label in spec.get("labels") or []:
        lines.append(f"- label: {one_line(label)}")
    for reason in spec.get("human_queue") or []:
        lines.append(f"- needs a human: {one_line(reason)}")
    if len(lines) == 2:
        lines.append("- no field changed; reviewed and left as found")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- rows


def flat_fields(issue: Dict[str, Any]) -> Dict[str, Any]:
    """MCP nests fields under `fields`; twg flattens them onto the issue."""
    fields = issue.get("fields")
    if isinstance(fields, dict):
        return fields
    return {k: v for k, v in issue.items() if k not in META_KEYS}


def name_of(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        n = value.get("name")
        return n if isinstance(n, str) else None
    return None


def symbolic_status(fields: Dict[str, Any], cfg: argparse.Namespace) -> str:
    status = fields.get("status")
    name = name_of(status)
    category = None
    if isinstance(status, dict) and isinstance(status.get("statusCategory"), dict):
        category = status["statusCategory"].get("key")
    if name in (cfg.ready_status, cfg.refinement_status):
        if category != "new":
            # Moving a ticket here would move it into started work, which the
            # core forbids; the config is wrong, not the ticket.
            raise InputError(
                f"{name!r} is configured as a garden target but is in the "
                f"{category!r} status category, not To Do"
            )
        return "ready" if name == cfg.ready_status else "refinement"
    if name in (cfg.blocked_status or []) or category == "indeterminate":
        return "started"
    if category == "new":
        return "initial"
    return "other"


def estimate_value(raw: Any) -> Optional[Any]:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    return raw if isinstance(raw, (int, float)) else None


def same_value(written: str, current: Any) -> bool:
    if current is None:
        return False
    try:
        return float(written) == float(current)
    except (TypeError, ValueError):
        return written.strip() == str(current).strip()


def build_row(
    issue: Dict[str, Any],
    changed: Set[str],
    cfg: argparse.Namespace,
    pmap: Dict[str, str],
) -> Dict[str, Any]:
    key = issue.get("key")
    if not isinstance(key, str) or not key:
        raise InputError(f"issue {issue.get('id')!r} has no key")
    fields = flat_fields(issue)

    comments = pr_sources.comment_list({"fields": fields}) or []
    gardened_at: Optional[datetime] = None
    last_write: Dict[str, Tuple[datetime, str]] = {}
    for c in comments:
        if not isinstance(c, dict):
            continue
        writes = parse_comment(comment_text(c))
        if writes is None:
            continue
        times = [
            t for t in (parse_time(c.get("created")), parse_time(c.get("updated"))) if t
        ]
        if not times:
            continue
        when = max(times)
        if gardened_at is None or when > gardened_at:
            gardened_at = when
        for fld, (_old, new) in writes.items():
            if fld not in last_write or when > last_write[fld][0]:
                last_write[fld] = (when, new)

    updated = parse_time(fields.get("updated"))
    updated_at = updated
    if updated and gardened_at and updated - gardened_at <= UPDATE_SLACK:
        updated_at = gardened_at

    size = (
        estimate_value(fields.get(cfg.estimate_field)) if cfg.estimate_field else None
    )
    size_by_garden = "size" in last_write and same_value(last_write["size"][1], size)
    priority = name_of(fields.get("priority"))
    reverse = {v: k for k, v in pmap.items() if k in IMPORTANCE_LEVELS}
    importance_by_garden = (
        "importance" in last_write
        and priority is not None
        and last_write["importance"][1].strip() == priority
    )
    importance_marked = priority is not None and (
        priority != cfg.default_priority or key in changed
    )

    parent = fields.get("parent")
    url = issue.get("url")
    if not isinstance(url, str) and cfg.site and "." in cfg.site:
        url = f"https://{cfg.site}/browse/{key}"
    description = fields.get("description")
    if isinstance(description, (dict, list)):
        description = pr_sources.adf_text(description).strip()
    pr_row = pr_sources.to_row(
        {"key": key, "id": issue.get("id"), "fields": fields}, False
    )

    return {
        "key": key,
        "url": url if isinstance(url, str) else None,
        "title": fields.get("summary"),
        "description": description,
        "parent": parent.get("key") if isinstance(parent, dict) else None,
        "status": symbolic_status(fields, cfg),
        "status_name": name_of(fields.get("status")),
        "issuetype": name_of(fields.get("issuetype")),
        "labels": [x for x in fields.get("labels") or [] if isinstance(x, str)],
        "updated_at": updated_at.isoformat() if updated_at else None,
        "gardened_at": gardened_at.isoformat() if gardened_at else None,
        "size": size,
        "size_human_set": size is not None and not size_by_garden,
        "priority": priority,
        "importance": reverse.get(priority) if priority else None,
        "importance_human_set": importance_marked and not importance_by_garden,
        # The whole jira-pr-sources row: linear-pr-resolve.py needs its
        # `identifier` as well as the trail.
        "linked_prs": pr_row,
    }


def priority_map(raw: Optional[str]) -> Dict[str, str]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(f"--priority-map-json: {exc}") from exc
    if not isinstance(val, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in val.items()
    ):
        raise InputError("--priority-map-json must map names to names")
    return val


def build_rows(cfg: argparse.Namespace) -> Dict[str, Any]:
    if not cfg.default_priority:
        raise InputError(
            "--default-priority is required: human-set priority depends on it"
        )
    pmap = priority_map(cfg.priority_map_json)
    changed: Set[str] = set()
    for path in cfg.changed_pages or []:
        for issue in page_issues(load_page(path)):
            if isinstance(issue.get("key"), str):
                changed.add(issue["key"])
    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for path in cfg.pages:
        for issue in page_issues(load_page(path)):
            row = build_row(issue, changed, cfg, pmap)
            if row["key"] in seen:
                continue
            seen.add(row["key"])
            rows.append(row)
    limit_cut = cfg.limit is not None and len(rows) > cfg.limit
    if cfg.limit is not None:
        rows = rows[: cfg.limit]
    return {
        "count": len(rows),
        "limit_cut": limit_cut or bool(cfg.limit_cut),
        "rows": rows,
    }


# ----------------------------------------------------------------- fields


def build_fields(cfg: argparse.Namespace) -> Dict[str, Any]:
    """One ticket's approved writes as an edit payload.

    `--writes`: a file holding the record's approved `proposed_writes`.
    `--labels`: a file holding the ticket's labels from a fresh Read, since a
    label edit replaces the whole set.
    A write whose config key is unset lands in `report_only`, never guessed.
    """
    writes = load_page(cfg.writes)
    labels = load_page(cfg.labels)
    if not isinstance(writes, list) or not isinstance(labels, list):
        raise InputError("--writes and --labels must be JSON arrays")
    pmap = priority_map(cfg.priority_map_json)
    out: Dict[str, Any] = {}
    report_only: List[str] = []
    transition: Optional[str] = None
    new_labels = [x for x in labels if isinstance(x, str)]
    for w in writes:
        if not isinstance(w, dict):
            raise InputError(f"bad write {w!r}")
        group, value = w.get("group"), w.get("value")
        if group == "size":
            if cfg.estimate_field:
                out[cfg.estimate_field] = value
            else:
                report_only.append(f"size {value}: jira.estimate_field is unset")
        elif group == "importance":
            name = pmap.get(str(value)) if value is not None else None
            if name:
                out["priority"] = {"name": name}
            else:
                report_only.append(
                    f"importance {value}: jira.priority_map has no entry for it"
                )
        elif group in ("closure", "human-queue"):
            label = CLOSE_LABEL if group == "closure" else HUMAN_LABEL
            if label not in new_labels:
                new_labels.append(label)
        elif group == "status":
            target = {
                "ready": cfg.ready_status,
                "refinement": cfg.refinement_status,
            }.get(str(value))
            if not target:
                raise InputError(f"status target {value!r} has no configured status")
            transition = target
        elif group != "gardened":
            raise InputError(f"unknown write group {group!r}")
    if new_labels != [x for x in labels if isinstance(x, str)]:
        out["labels"] = new_labels
    return {"fields": out, "transition_to": transition, "report_only": report_only}


# ------------------------------------------------------------------- main


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("jql")
    s.add_argument("--scope", required=True)

    s = sub.add_parser("fetch")
    s.add_argument("--site", required=True)
    s.add_argument("--jql", required=True)
    s.add_argument("--fields", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--limit", type=int)
    s.add_argument("--page-size", type=int, default=100)

    s = sub.add_parser("page")
    s.add_argument("path")

    def status_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--ready-status", required=True)
        sp.add_argument("--refinement-status", required=True)
        sp.add_argument("--priority-map-json")

    s = sub.add_parser("rows")
    s.add_argument("--pages", nargs="+", required=True)
    s.add_argument("--changed-pages", nargs="*")
    s.add_argument("--default-priority")
    s.add_argument("--estimate-field")
    s.add_argument("--blocked-status", action="append")
    s.add_argument("--site")
    s.add_argument("--limit", type=int)
    s.add_argument("--limit-cut", action="store_true")
    status_args(s)

    s = sub.add_parser("fields")
    s.add_argument("--writes", required=True)
    s.add_argument("--labels", required=True)
    s.add_argument("--estimate-field")
    status_args(s)

    s = sub.add_parser("comment")
    s.add_argument("--spec", required=True)

    args = p.parse_args(argv)
    if getattr(args, "limit", None) is not None and args.limit < 1:
        print("jira-garden: --limit must be at least 1", file=sys.stderr)
        return 2
    try:
        if args.cmd == "jql":
            result: Any = build_jql(args.scope)
        elif args.cmd == "fetch":
            result = fetch(
                args.site,
                args.jql,
                args.fields,
                args.out_dir,
                args.limit,
                args.page_size,
            )
        elif args.cmd == "page":
            payload = load_page(args.path)
            result = {"count": len(page_issues(payload)), "next": page_next(payload)}
        elif args.cmd == "rows":
            result = build_rows(args)
        elif args.cmd == "fields":
            result = build_fields(args)
        else:
            spec = load_page(args.spec)
            if not isinstance(spec, dict):
                raise InputError("--spec must be a JSON object")
            sys.stdout.write(render_comment(spec))
            return 0
    except InputError as exc:
        print(f"jira-garden: {exc}", file=sys.stderr)
        return 2
    except TwgFailed as exc:
        print(f"jira-garden: {exc}", file=sys.stderr)
        return 4
    json.dump(result, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
