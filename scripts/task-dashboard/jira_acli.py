"""Jira reads through `acli`, Atlassian's CLI.

Every Jira fetch is two steps, because `acli jira workitem search` accepts only a
fixed field allowlist with no dates, links, parent or custom fields: search for
keys, then `view` each key for its fields. `view` takes one key per call, so the
views fan out over a thread pool, and the ~1% that fail transiently at 8-way
concurrency get one serial retry. The constraints are measured in
dev_docs/research/2026-10-23-task-dashboard-acli-spike.md.

Every call goes through `run_acli`, the one place a subprocess is started. Each
public function takes it as `runner=`, so tests pass a fake instead.

Output always goes to a pipe: `view --json` with stdout sent to /dev/null exits 1.
Responses wrap each issue in an envelope full of null keys; only `key` and
`fields` are read.
"""

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

Result = tuple[int, str, str]
Runner = Callable[[list[str], float], Result]

DEFAULT_TIMEOUT = 120.0

# Lower-cased substrings that mark an auth failure in acli's output. A GUESS:
# the real logged-out text has not been captured yet, so this is deliberately
# permissive. #1087 replaces it with the text a logged-out acli actually prints.
# It must never match the transient `failed to fetch work item details`, which
# the serial retry exists to absorb.
AUTH_ERROR_PATTERNS = (
    "unauthorized",
    "unauthenticated",
    "not logged in",
    "login",
    "token",
    "401",
)

# The fields every `view` requests, before the per-site custom fields.
VIEW_FIELDS = (
    "summary",
    "status",
    "assignee",
    "reporter",
    "priority",
    "duedate",
    "parent",
    "labels",
    "issuelinks",
    "created",
    "updated",
    "resolutiondate",
    "statuscategorychangedate",
)


class AcliError(RuntimeError):
    """An acli call failed, or printed something that is not the expected JSON."""

    def __init__(self, message: str, argv: "list[str] | None" = None) -> None:
        super().__init__(message)
        self.argv = argv or []


class AuthError(AcliError):
    """acli is not logged in; the fix is `acli jira auth login` in a terminal."""


def run_acli(argv: list[str], timeout: float = DEFAULT_TIMEOUT) -> Result:
    """Run one acli argv with stdout and stderr on pipes; never raises.

    A timeout returns rc 124 and a missing binary rc 127, so every failure
    reaches `classify` as data rather than as an exception from here.
    """
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"acli timed out after {timeout:g}s"
    except FileNotFoundError:
        return 127, "", f"acli not found: {argv[0]}"
    return proc.returncode, proc.stdout, proc.stderr


def is_auth_error(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in AUTH_ERROR_PATTERNS)


def classify(rc: int, stdout: str, stderr: str, argv: list[str]) -> AcliError:
    """The exception for a failed call: AuthError on an auth pattern, else AcliError.

    stdout is searched too, because where acli prints its errors is not yet
    measured.
    """
    detail = (stderr.strip() or stdout.strip() or f"exit {rc}")[:500]
    message = f"acli exited {rc}: {detail}"
    if is_auth_error(stderr) or is_auth_error(stdout):
        return AuthError(message, argv)
    return AcliError(message, argv)


def _call_json(argv: list[str], runner: Runner, timeout: float) -> Any:
    rc, out, err = runner(argv, timeout)
    if rc != 0:
        raise classify(rc, out, err, argv)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise classify(rc, out, err or "output is not JSON", argv) from None


def _items(payload: Any, *keys: str) -> list[Any]:
    """A bare JSON array, or the array under the first of `keys` an object has."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            if isinstance(payload.get(k), list):
                return payload[k]
    raise AcliError(f"unexpected acli JSON shape: {type(payload).__name__}")


def search_keys(
    jql: str,
    *,
    acli: str = "acli",
    runner: Runner = run_acli,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[str]:
    """Every issue key the JQL matches, across all pages, in returned order."""
    argv = [acli, "jira", "workitem", "search", "--jql", jql]
    argv += ["--paginate", "--json", "--fields", "key"]
    keys: list[str] = []
    seen: set[str] = set()
    for item in _items(_call_json(argv, runner, timeout), "issues", "values"):
        key = item.get("key") if isinstance(item, dict) else None
        if isinstance(key, str) and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


@dataclass
class ViewResult:
    """Parsed `view` JSON per key, plus the keys that failed twice and why."""

    issues: dict[str, Any] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def _view_one(
    key: str, argv: list[str], runner: Runner, timeout: float
) -> "tuple[Any, AcliError | None]":
    try:
        payload = _call_json(argv, runner, timeout)
    except AuthError:
        raise
    except AcliError as exc:
        return None, exc
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), dict):
        return None, AcliError(f"view {key}: no fields object in response", argv)
    return payload, None


def view_issues(
    keys: list[str],
    fields: "list[str] | tuple[str, ...]",
    concurrency: int = 8,
    *,
    acli: str = "acli",
    runner: Runner = run_acli,
    timeout: float = DEFAULT_TIMEOUT,
) -> ViewResult:
    """`view` every key at `concurrency`, then retry the failures serially once.

    A key that fails both times lands in `failed`, with its message in `errors`;
    it never raises. An AuthError does raise, at once: when the login has
    expired every key fails the same way, and retrying them all serially would
    only delay the one message that names the fix.
    """
    field_arg = ",".join(fields)
    unique = list(dict.fromkeys(keys))

    def argv_for(key: str) -> list[str]:
        return [acli, "jira", "workitem", "view", key, "--fields", field_arg, "--json"]

    def attempt(key: str) -> "tuple[str, Any, AcliError | None]":
        payload, err = _view_one(key, argv_for(key), runner, timeout)
        return key, payload, err

    got: dict[str, Any] = {}
    retry: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(attempt, key) for key in unique]
        try:
            for fut in as_completed(futures):
                key, payload, err = fut.result()
                if err is None:
                    got[key] = payload
                else:
                    retry.append(key)
        except AuthError:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    result = ViewResult()
    for key in sorted(retry, key=unique.index):
        _, payload, err = attempt(key)
        if err is None:
            got[key] = payload
        else:
            result.failed.append(key)
            result.errors[key] = str(err)
    result.issues = {k: got[k] for k in unique if k in got}
    return result


def active_sprints(
    board_id: int,
    *,
    acli: str = "acli",
    runner: Runner = run_acli,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """The board's active sprints as `{id, name, startDate, endDate}`."""
    argv = [acli, "jira", "board", "list-sprints", "--id", str(board_id)]
    argv += ["--state", "active", "--json"]
    sprints = []
    for s in _items(_call_json(argv, runner, timeout), "sprints", "values"):
        if not isinstance(s, dict) or s.get("id") is None:
            continue
        if s.get("state") not in (None, "active"):
            continue
        sprints.append(
            {
                "id": int(s["id"]),
                "name": s.get("name"),
                "startDate": s.get("startDate"),
                "endDate": s.get("endDate"),
            }
        )
    return sprints


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _utc(value: Any) -> "str | None":
    """An ISO timestamp normalized to UTC (`...Z`) so SQL compares it correctly.

    Jira writes the offset without a colon (`-0400`), which 3.11's
    fromisoformat reads. Anything unparseable is kept verbatim, not dropped.
    """
    if not isinstance(value, str) or not value:
        return None
    if _DATE_ONLY.match(value):
        return value
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is None:
        return value
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(obj: Any, *path: str) -> Any:
    for part in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _active_sprint(value: Any) -> "dict[str, Any] | None":
    """The `active` element of the sprint field, which holds the whole history."""
    entries = value if isinstance(value, list) else [value]
    for s in entries:
        if isinstance(s, dict) and s.get("state") == "active":
            return s
    return None


def _links(key: str, value: Any) -> list[dict[str, Any]]:
    rows = []
    for link in value if isinstance(value, list) else []:
        if not isinstance(link, dict):
            continue
        for side, direction in (("outwardIssue", "outward"), ("inwardIssue", "inward")):
            other = link.get(side)
            if not isinstance(other, dict) or not other.get("key"):
                continue
            rows.append(
                {
                    "issue_key": key,
                    "linked_key": other["key"],
                    "link_type": _get(link, "type", "name") or "",
                    "direction": direction,
                    "linked_status_category": _get(
                        other, "fields", "status", "statusCategory", "key"
                    ),
                }
            )
    return rows


def parse_issue(raw: dict[str, Any], field_ids: dict[str, str]) -> dict[str, Any]:
    """One `view` response as `{"issue": <issues row>, "links": [<links rows>]}`.

    `field_ids` is the per-site `jira.fields` config (`sprint`, `flagged`). The
    issue row carries every `issues` column except `source`, `kind`, `scope`
    and `synced_at`, which the sync stamps. People are stored by display name, never account id or email.
    """
    key = raw.get("key")
    if not isinstance(key, str) or not key:
        raise AcliError("view response has no key")
    f = raw.get("fields")
    if not isinstance(f, dict):
        f = {}
    sprint = _active_sprint(f.get(field_ids.get("sprint", "")))
    labels = f.get("labels")
    issue = {
        "key": key,
        "summary": f.get("summary"),
        "status_name": _get(f, "status", "name"),
        "status_category": _get(f, "status", "statusCategory", "key"),
        "status_category_changed": _utc(f.get("statuscategorychangedate")),
        "assignee": _get(f, "assignee", "displayName"),
        "reporter": _get(f, "reporter", "displayName"),
        "priority": _get(f, "priority", "name"),
        "due_date": f.get("duedate"),
        "parent_key": _get(f, "parent", "key"),
        "labels": json.dumps(labels if isinstance(labels, list) else []),
        "created": _utc(f.get("created")),
        "updated": _utc(f.get("updated")),
        "resolution_date": _utc(f.get("resolutiondate")),
        "sprint_id": int(sprint["id"])
        if sprint and sprint.get("id") is not None
        else None,
        "sprint_name": sprint.get("name") if sprint else None,
        "sprint_state": sprint.get("state") if sprint else None,
        "sprint_end": _utc(sprint.get("endDate")) if sprint else None,
        "flagged": bool(f.get(field_ids.get("flagged", ""))),
        "raw_json": json.dumps({"key": key, "fields": f}, sort_keys=True),
    }
    return {"issue": issue, "links": _links(key, f.get("issuelinks"))}
