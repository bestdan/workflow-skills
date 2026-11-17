#!/usr/bin/env python3
"""Tracker I/O helpers for the jira `/garden-backlog` handler.

`commands/handlers/jira-garden.md` is the prose half; this file owns every
part two runs must do identically, and every part a review round would
otherwise have to catch by reading markdown:

  jql      the candidate query and the `priority CHANGED` query for a scope
           (an epic unless --wide) or for one ticket (--key)
  fetch    page a JQL search to completion through twg, one file per page
  page     read one saved search page: its issue count and next-page token
  rows     turn saved pages into the core's List rows (symbolic values only)
  own-prs  run validity rung 1 over the rows: resolve each row's own-PR trail
           and title through `linear-pr-resolve.py`, and put the verdict on
           the row as `own_pr`
  fields   turn one ticket's approved writes into an edit payload, and a kind
           write into the issue-type id it names
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
- Kind is the issue type: `Spike` is `spike`, any other type is `fix`. The
  type is human-set when it is not the project's creation type
  (`jira.issue_type`), unless the garden wrote it. Once the garden has written
  a kind, the type is human-set exactly when it no longer matches that write,
  so a human who changed it back is not overruled. No changelog is read, so a
  type a human reset to the creation type before the garden ever wrote one
  reads as not human-set; the gated apply is what stands in front of that.

WHY `own-prs` WRITES ITS VERDICT ONTO THE ROW. A row's `linked_prs` is the
resolver's *input*: the plugin's own comment trail, which is empty on every
ticket the plugin did not deliver. In the #1522 calibration the resolver ran,
and its title probe found the one ticket whose merged PR carried its key, but
its output was a separate file the judges never got. They read the empty
trail as "no PR" for all 30 tickets. With the verdict on the row, the judge
reads the resolver's answer, not its input.

WHY `blocked_by` IS ON THE ROW. "Quality pass -> ready" moved tickets still
blocked by an earlier slice. `blocked_by` lists the keys of the ticket's
unresolved "is blocked by" links; `garden-checks.py finalize` holds Ready
while it is non-empty, or null because the `issuelinks` field was not read.

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

_rspec = importlib.util.spec_from_file_location(
    "linear_pr_resolve", os.path.join(HERE, "linear-pr-resolve.py")
)
assert _rspec is not None and _rspec.loader is not None, (
    "cannot load linear-pr-resolve.py"
)
pr_resolve = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(pr_resolve)

MARKER = "garden-backlog:"
CLOSE_LABEL = "garden:close-candidate"
HUMAN_LABEL = "garden:needs-human"
SPIKE_TYPE = "Spike"
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
    r"^\s*(?:[-*]\s*)?(size|importance|kind):\s*(.*?)\s*->\s*(.*?)\s*(?:;\s*reason:.*)?$"
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
    try:
        proc = subprocess.run(["twg", *args], capture_output=True, text=True)
    except OSError as exc:
        raise TwgFailed(f"cannot run twg: {exc}") from exc
    return TwgResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


# --------------------------------------------------------------------- jql


def scope_clause(scope: str, wide: bool = False) -> str:
    """An epic key, or with `wide` a project key or JQL fragment, as one clause.

    Wide scopes are refused by default on cost: judging one epic of 30 tickets
    took about 440k tokens (#1561), so a 418-ticket project would take about
    6M. The refusal names the flag so the caller can pass it on purpose.
    """
    s = scope.strip()
    if not s:
        raise InputError("empty scope")
    # Jira keys are uppercase, so `ip` means the project, not a JQL fragment.
    key = s.upper()
    if ISSUE_KEY_RE.match(key):
        return f'parent = "{key}"'
    if PROJECT_RE.match(key):
        s = key
        clause, what = f'project = "{s}"', "a project"
    elif re.search(r"\border\s+by\b", s, re.IGNORECASE):
        raise InputError(
            "a JQL scope must not carry ORDER BY; the handler sets the order"
        )
    else:
        clause, what = f"({s})", "a JQL query"
    if not wide:
        raise InputError(
            f"scope {s!r} is {what}, wider than one epic; pass --wide to garden"
            " it anyway (one epic of 30 tickets cost about 440k tokens)"
        )
    return clause


def queries(where: str) -> Dict[str, str]:
    where = f"{where} AND {BASE_FILTER}"
    return {
        "candidates": f"{where} ORDER BY key ASC",
        "priority_changed": f"{where} AND priority CHANGED ORDER BY key ASC",
    }


def build_jql(scope: str, wide: bool = False) -> Dict[str, str]:
    return queries(scope_clause(scope, wide))


def build_key_jql(key: str) -> Dict[str, str]:
    """One ticket, for Read: never a scope, so never subject to the epic gate."""
    k = key.strip().upper()
    if not ISSUE_KEY_RE.match(k):
        raise InputError(f"--key {key!r} is not an issue key")
    return queries(f'key = "{k}"')


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
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise InputError(f"--out-dir {out_dir}: {exc}") from exc
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
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except OSError as exc:
            raise InputError(f"--out-dir {out_dir}: {exc}") from exc
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
    "labels": [...], "human_queue": [...]}. A `size`, `importance` or `kind`
    change (kind as issue-type names) is
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


def same_name(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def issue_type_list(payload: Any) -> List[Dict[str, Any]]:
    """The issue types of a saved project-metadata read, in any shape the MCP
    or a REST call returns: a bare list, or one under `issueTypes`, `values`
    or `data`."""
    if isinstance(payload, dict):
        for k in ("issueTypes", "values", "data"):
            if isinstance(payload.get(k), (list, dict)):
                return issue_type_list(payload[k])
        raise InputError("issue-type metadata has no issue-type list")
    if not isinstance(payload, list):
        raise InputError("issue-type metadata is not a JSON list or object")
    return [t for t in payload if isinstance(t, dict)]


def resolve_issue_type(types: List[Dict[str, Any]], name: str) -> Optional[str]:
    """The id of the non-subtask issue type called `name`, or None."""
    for t in types:
        n, tid = t.get("name"), t.get("id")
        if t.get("subtask") is True or not isinstance(n, str):
            continue
        if same_name(n, name) and isinstance(tid, (str, int)):
            return str(tid)
    return None


def blockers(fields: Dict[str, Any]) -> Optional[List[str]]:
    """Keys of the unresolved issues this one "is blocked by", or None when the
    `issuelinks` field was not read.

    On the issue's own link, the description that applies is the one on the
    side the *other* issue sits: `inwardIssue` reads as `type.inward` ("is
    blocked by"), `outwardIssue` as `type.outward`. A linked issue whose status
    is missing counts as unresolved, so an unread status holds Ready.
    """
    links = fields.get("issuelinks")
    if links is None:
        return None
    if not isinstance(links, list):
        raise InputError("issuelinks is not a list")
    keys: List[str] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        raw_type = link.get("type")
        ltype: Dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
        for side, desc in (("inwardIssue", "inward"), ("outwardIssue", "outward")):
            other = link.get(side)
            text = ltype.get(desc)
            if not isinstance(other, dict) or not isinstance(text, str):
                continue
            if "blocked by" not in text.casefold():
                continue
            ofields = (
                other.get("fields") if isinstance(other.get("fields"), dict) else other
            )
            status = ofields.get("status") if isinstance(ofields, dict) else None
            category = None
            if isinstance(status, dict) and isinstance(
                status.get("statusCategory"), dict
            ):
                category = status["statusCategory"].get("key")
            key = other.get("key")
            if category != "done" and isinstance(key, str) and key not in keys:
                keys.append(key)
    return keys


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

    issuetype = name_of(fields.get("issuetype"))
    kind = None
    if issuetype is not None:
        kind = "spike" if same_name(issuetype, SPIKE_TYPE) else "fix"
    if "kind" in last_write:
        kind_human_set = not (
            issuetype is not None and same_name(last_write["kind"][1], issuetype)
        )
    else:
        kind_human_set = not (
            issuetype is not None
            and bool(cfg.issue_type)
            and same_name(issuetype, cfg.issue_type)
        )

    parent = fields.get("parent")
    url = issue.get("url")
    if not isinstance(url, str) and cfg.site and "." in cfg.site:
        url = f"https://{cfg.site}/browse/{key}"
    description = fields.get("description")
    if isinstance(description, (dict, list)):
        description = pr_sources.adf_text(description, headings=True).strip()
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
        "issuetype": issuetype,
        "kind": kind,
        "kind_human_set": kind is not None and kind_human_set,
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
        "blocked_by": blockers(fields),
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
    issues = [i for path in cfg.pages for i in page_issues(load_page(path))]
    source = "config"
    if not cfg.refinement_status:
        cfg.refinement_status = inferred_refinement(issues, cfg)
        source = "inferred"
    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for issue in issues:
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
        "refinement_status": cfg.refinement_status,
        "refinement_status_source": source,
        "rows": rows,
    }


def inferred_refinement(issues: List[Dict[str, Any]], cfg: argparse.Namespace) -> str:
    """The refinement target when `jira.refinement_status` is unset.

    It is the one To Do status the in-scope tickets hold other than
    `ready_status` and the blocked statuses. That need not be the workflow's
    initial status, and it does not have to be: the garden's query does not
    filter on status and a target equal to the current status is no write, so a
    ticket that fails quality stays beside its unready peers, and the pick is
    always To Do, so nothing is started. Anything but exactly one is refused,
    not guessed.
    """
    skip = {cfg.ready_status, *(cfg.blocked_status or [])}
    names: Set[str] = set()
    for issue in issues:
        status = flat_fields(issue).get("status")
        name = name_of(status)
        cat = status.get("statusCategory") if isinstance(status, dict) else None
        if name and name not in skip and isinstance(cat, dict):
            if cat.get("key") == "new":
                names.add(name)
    if len(names) != 1:
        found = ", ".join(sorted(names)) or "none"
        raise InputError(
            "jira.refinement_status is unset and cannot be inferred: "
            f"the candidates hold {len(names)} To Do statuses besides "
            f"ready_status and the blocked statuses ({found}). Set jira.refinement_status in "
            "dev_docs/tasks/.task-config.yml."
        )
    return names.pop()


# ---------------------------------------------------------------- own-prs

STATE_RANK = ("merged", "open", "unresolved", "closed_unmerged", "no_pr")


def own_prs(rows: List[Dict[str, Any]], repos: List[str]) -> List[Dict[str, Any]]:
    """Each row with `own_pr`: rung 1's verdict across every repo searched.

    `linear-pr-resolve.py` takes one repo per issue, and an epic can span
    several, so each repo is resolved in turn and the strongest verdict kept,
    in its own precedence: positive evidence from one repo stands over a probe
    failure in another, and a failure anywhere outranks a clean "no PR".
    """
    if not repos:
        raise InputError("own-prs needs at least one --repo")
    out = []
    for row in rows:
        src = row.get("linked_prs")
        if not isinstance(src, dict) or not src.get("identifier"):
            src = {"identifier": row.get("key"), "attachments": [], "branchName": None}
        results = [pr_resolve.resolve_issue(src, {}, repo) for repo in repos]
        best = min(results, key=lambda r: STATE_RANK.index(r["state"]))
        errors = [e for r in results for e in r["probe_errors"]]
        prs = [p for r in results for p in r["prs"]]
        out.append(
            {
                **row,
                "own_pr": {
                    "state": best["state"],
                    "repo": best["repo"],
                    "resolved_via": best["resolved_via"],
                    "prs": prs,
                    "probe_errors": errors,
                },
            }
        )
    return out


# ----------------------------------------------------------------- fields


def build_fields(cfg: argparse.Namespace) -> Dict[str, Any]:
    """One ticket's approved writes as an edit payload.

    `--writes`: a file holding the record's approved `proposed_writes`.
    `--labels`: a file holding the ticket's labels from a fresh Read, since a
    label edit replaces the whole set.
    `--issue-types`: the project's issue-type metadata, read only for a kind
    write. The kind write comes back as `issuetype`, apart from `fields`, so the
    caller sends it as its own edit: Jira refuses a type change between
    workflows, and that refusal must not take the other fields with it.
    A write whose config key is unset, or whose target issue type the project
    lacks, lands in `report_only`, never guessed.
    """
    writes = load_page(cfg.writes)
    labels = load_page(cfg.labels)
    if not isinstance(writes, list) or not isinstance(labels, list):
        raise InputError("--writes and --labels must be JSON arrays")
    pmap = priority_map(cfg.priority_map_json)
    out: Dict[str, Any] = {}
    report_only: List[str] = []
    transition: Optional[str] = None
    issuetype: Optional[Dict[str, str]] = None
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
        elif group == "kind":
            issuetype, line = kind_target(str(value), cfg)
            if line:
                report_only.append(line)
        elif group != "gardened":
            raise InputError(f"unknown write group {group!r}")
    if new_labels != [x for x in labels if isinstance(x, str)]:
        out["labels"] = new_labels
    return {
        "fields": out,
        "issuetype": issuetype,
        "transition_to": transition,
        "report_only": report_only,
    }


def kind_target(
    kind: str, cfg: argparse.Namespace
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """A kind write as `({"id": ...}, None)`, or `(None, <report line>)`."""
    name: Optional[str]
    if kind == "spike":
        name = SPIKE_TYPE
    elif kind == "fix":
        name = cfg.issue_type
        if not name:
            return None, "kind fix: jira.issue_type is unset"
    else:
        raise InputError(f"kind {kind!r} is not fix or spike")
    if not cfg.issue_types:
        return None, f"kind {kind}: no project issue-type metadata was read"
    tid = resolve_issue_type(issue_type_list(load_page(cfg.issue_types)), name)
    if tid is None:
        return None, f"kind {kind}: the project has no `{name}` issue type"
    return {"id": tid}, None


# ------------------------------------------------------------------- main


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("jql")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--scope")
    g.add_argument("--key")
    s.add_argument("--wide", action="store_true")

    s = sub.add_parser("fetch")
    s.add_argument("--site", required=True)
    s.add_argument("--jql", required=True)
    s.add_argument("--fields", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--limit", type=int)
    s.add_argument("--page-size", type=int, default=100)

    s = sub.add_parser("page")
    s.add_argument("path")

    def status_args(sp: argparse.ArgumentParser, refinement_required: bool) -> None:
        sp.add_argument("--ready-status", required=True)
        sp.add_argument("--refinement-status", required=refinement_required)
        sp.add_argument("--priority-map-json")
        sp.add_argument("--issue-type")

    s = sub.add_parser("rows")
    s.add_argument("--pages", nargs="+", required=True)
    s.add_argument("--changed-pages", nargs="*")
    s.add_argument("--default-priority")
    s.add_argument("--estimate-field")
    s.add_argument("--blocked-status", action="append")
    s.add_argument("--site")
    s.add_argument("--limit", type=int)
    s.add_argument("--limit-cut", action="store_true")
    status_args(s, refinement_required=False)

    s = sub.add_parser("own-prs")
    s.add_argument("--rows", required=True)
    s.add_argument("--repo", action="append", default=[])

    s = sub.add_parser("fields")
    s.add_argument("--writes", required=True)
    s.add_argument("--labels", required=True)
    s.add_argument("--estimate-field")
    s.add_argument("--issue-types")
    status_args(s, refinement_required=True)

    s = sub.add_parser("comment")
    s.add_argument("--spec", required=True)

    args = p.parse_args(argv)
    for flag in ("limit", "page_size"):
        if getattr(args, flag, None) is not None and getattr(args, flag) < 1:
            name = flag.replace("_", "-")
            print(f"jira-garden: --{name} must be at least 1", file=sys.stderr)
            return 2
    try:
        if args.cmd == "jql":
            if args.key is not None:
                result: Any = build_key_jql(args.key)
            else:
                result = build_jql(args.scope, args.wide)
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
        elif args.cmd == "own-prs":
            data = load_page(args.rows)
            if isinstance(data, dict):
                data = data.get("rows")
            if not isinstance(data, list):
                raise InputError("--rows must be a row list or `rows` output")
            result = own_prs([r for r in data if isinstance(r, dict)], args.repo)
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
