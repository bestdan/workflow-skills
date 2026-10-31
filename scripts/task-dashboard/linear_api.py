"""Linear reads: for the Workstreams tab, open projects and which project each
Linear key the mirror mentions belongs to; for Now (linear_issues.py), my open
assigned issues: the key owner's, or `linear.me`'s when it is set.

Off by default (`[linear] enabled = false`). Every call goes through one
runner seam, `Runner(body, key, timeout) -> payload`, where `body` is the
GraphQL request ({"query", "variables"}) and `payload` the decoded reply, so
the tests pass a fake and nothing here reaches the network.

THE KEY is never in config or argv. resolve_key() follows
dev_docs/auth_key_access.md for `LINEAR_API_KEY`, first hit wins:

1. `$LINEAR_API_KEY`, the raw key;
2. the operator key file KEY_FILE (`~/.config/workflow-skills/linear_api_key`),
   which must be mode 600 or tighter: the shape that suits the dashboard's
   launchd agent, whose environment names no secret;
3. `$LINEAR_API_KEY_REF`, resolved by the plugin's
   commands/handlers/assets/_secret_resolve.py, whose resolver ladder and
   redaction this reuses rather than copies.

Nothing configured is NoKey with category `unconfigured`, which the sync
records as "linear: no key". Any other failure keeps its category (`no-session`,
`denied`, `key-file-mode`, ...), and no Linear call is made either way. Neither
the key nor a reference is ever put in a message.

Projects are every project the key can see whose status type is not
`completed` or `canceled`, filtered here rather than in the query. The
Workstreams key lookup is ONE `issues` query per sync (paged at 250), filtered
to the keys' team and number, and only for keys whose team owns an open
project. Now's read (assigned_issues) is its own paged query.
"""

import importlib.util
import json
import os
import re
import stat
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

API = "https://api.linear.app/graphql"
KEY_ENV = "LINEAR_API_KEY"
KEY_FILE = Path("~/.config/workflow-skills/linear_api_key")
DEFAULT_TIMEOUT = 30.0

# A project whose status type is one of these is finished, and not listed.
CLOSED_TYPES = ("completed", "canceled")

Runner = Callable[[dict, str, float], Any]

_SECRET_RESOLVE = (
    Path(__file__).resolve().parents[2]
    / "commands"
    / "handlers"
    / "assets"
    / "_secret_resolve.py"
)


class LinearError(RuntimeError):
    """A Linear call failed or returned something unexpected."""


class AuthError(LinearError):
    """Linear refused the key."""


class NoKey(Exception):
    """No usable key. `category` is _secret_resolve's reason, or
    `unconfigured` when nothing named a key."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


# --- the key -----------------------------------------------------------------


def _secret_resolve() -> Any:
    spec = importlib.util.spec_from_file_location("_secret_resolve", _SECRET_RESOLVE)
    if spec is None or spec.loader is None:
        raise NoKey("unconfigured", "the plugin's _secret_resolve.py is missing")
    # A missing file still gets a spec, so the load itself is what fails;
    # as NoKey it skips Linear alone rather than failing the whole step.
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (OSError, SyntaxError, ImportError) as exc:
        raise NoKey(
            "missing",
            f"cannot load the plugin's _secret_resolve.py: {type(exc).__name__}",
        ) from None
    return module


def resolve_key(
    env: "Mapping[str, str] | None" = None, key_file: "Path | None" = None
) -> str:
    """The Linear API key, by the ladder in the module docstring; NoKey when
    there is none."""
    env = os.environ if env is None else env
    raw = env.get(KEY_ENV)
    if raw:
        return raw
    path = (key_file or KEY_FILE).expanduser()
    try:
        st = path.stat()
    except FileNotFoundError:
        st = None
    except OSError as exc:
        raise NoKey(
            "denied", f"cannot read the Linear key file: {exc.strerror}"
        ) from None
    if st is not None:
        if st.st_mode & 0o077:
            raise NoKey(
                "key-file-mode",
                f"{path} is readable by others (mode"
                f" {stat.S_IMODE(st.st_mode):o}); run: chmod 600 {path}",
            )
        try:
            key = path.read_text().strip()
        except OSError as exc:
            raise NoKey("denied", f"cannot read {path}: {exc.strerror}") from None
        if not key:
            raise NoKey("empty", f"{path} is empty")
        return key
    if not env.get(f"{KEY_ENV}_REF"):
        raise NoKey("unconfigured", "no Linear key is configured")
    resolver = _secret_resolve()
    try:
        return resolver.resolve_key(KEY_ENV)
    except resolver.SecretUnavailable as exc:
        raise NoKey(exc.category, str(exc)) from None


# --- transport ---------------------------------------------------------------


def post(body: dict, key: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """POST one GraphQL request; the key goes in the Authorization header."""
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError(f"Linear refused the key (HTTP {exc.code})") from None
        raise LinearError(f"Linear answered HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LinearError(f"Linear request failed: {exc}") from None
    except ValueError:
        raise LinearError("Linear returned non-JSON") from None


def _data(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise LinearError("unexpected Linear response")
    errors = payload.get("errors")
    if errors:
        codes = {
            str(((e or {}).get("extensions") or {}).get("code") or "") for e in errors
        }
        first = str((errors[0] or {}).get("message") or "error")
        if "AUTHENTICATION_ERROR" in codes:
            raise AuthError(f"Linear refused the key: {first}")
        raise LinearError(f"Linear GraphQL error: {first}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LinearError("Linear response has no data")
    return data


def _pages(
    runner: Runner, key: str, timeout: float, query: str, variables: dict, field: str
) -> list[dict]:
    """Every node of the connection at `field`, a dotted path under `data`
    (`issues`, `viewer.assignedIssues`), following its cursor."""
    nodes: list[dict] = []
    after = None
    while True:
        body = {"query": query, "variables": {**variables, "after": after}}
        conn: Any = _data(runner(body, key, timeout))
        for part in field.split("."):
            conn = conn.get(part) if isinstance(conn, dict) else None
        try:
            nodes += conn["nodes"]
            page = conn["pageInfo"]
        except (KeyError, TypeError):
            raise LinearError(f"unexpected Linear {field} response") from None
        if not page.get("hasNextPage"):
            return nodes
        after = page.get("endCursor")


# --- projects ----------------------------------------------------------------

PROJECTS_QUERY = """
query($after: String) {
  projects(first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name url progress targetDate updatedAt
      status { name type }
      teams { nodes { key } }
    }
  }
}
"""


def projects(
    key: str, runner: Runner = post, timeout: float = DEFAULT_TIMEOUT
) -> list[dict]:
    """The open projects: {id, name, url, status, status_type, progress
    (0..1), target, updated, teams}."""
    out = []
    for node in _pages(runner, key, timeout, PROJECTS_QUERY, {}, "projects"):
        status = node.get("status") or {}
        kind = str(status.get("type") or "").lower()
        if kind in CLOSED_TYPES:
            continue
        out.append(
            {
                "id": node["id"],
                "name": node.get("name") or node["id"],
                "url": node.get("url"),
                "status": status.get("name") or kind,
                "status_type": kind,
                "progress": float(node.get("progress") or 0),
                "target": node.get("targetDate"),
                "updated": node.get("updatedAt"),
                "teams": sorted(
                    {
                        t["key"]
                        for t in (node.get("teams") or {}).get("nodes") or []
                        if t and t.get("key")
                    }
                ),
            }
        )
    return out


# --- keys --------------------------------------------------------------------

KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d{1,7})\b")

ISSUES_QUERY = """
query($filter: IssueFilter!, $after: String) {
  issues(first: 250, after: $after, filter: $filter) {
    pageInfo { hasNextPage endCursor }
    nodes { identifier project { id } }
  }
}
"""


def keys_in(text: str, teams: "set[str]") -> list[str]:
    """The Linear keys in `text` whose team is in `teams`, in order."""
    found: dict[str, None] = {}
    for m in KEY_RE.finditer(text or ""):
        if m[1] in teams:
            found.setdefault(f"{m[1]}-{int(m[2])}", None)
    return list(found)


def issue_filter(keys: Iterable[str]) -> dict:
    """An IssueFilter matching exactly `keys`: per team, its numbers."""
    by_team: dict[str, set[int]] = {}
    for k in keys:
        team, _, number = k.rpartition("-")
        by_team.setdefault(team, set()).add(int(number))
    return {
        "or": [
            {"team": {"key": {"eq": team}}, "number": {"in": sorted(nums)}}
            for team, nums in sorted(by_team.items())
        ]
    }


def project_of_keys(
    keys: Iterable[str],
    key: str,
    runner: Runner = post,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, str]:
    """{Linear key: project id} for the keys whose issue is in a project. One
    query (paged) for all of them; none when there are no keys."""
    keys = sorted(set(keys))
    if not keys:
        return {}
    variables = {"filter": issue_filter(keys)}
    out = {}
    for node in _pages(runner, key, timeout, ISSUES_QUERY, variables, "issues"):
        project = (node or {}).get("project") or {}
        if node.get("identifier") and project.get("id"):
            out[node["identifier"]] = project["id"]
    return out


# --- my issues ---------------------------------------------------------------

# The issue states Now ranks: Todo-like (`unstarted`) and In Progress-like
# (`started`). Triage, backlog, done and canceled are not the owner's to do now.
OPEN_STATE_TYPES = ("unstarted", "started")

# Linear refuses a query whose page size times its nested connections' sizes
# is "too complex": with the nested ones at their default of 50, a page of 50
# issues was refused (2026-10-31). An issue rarely carries more than 10
# labels or relations; one that does loses the rest, which can hide a
# `blocked` label or an open blocker. assigned_issues marks it `truncated`, and
# the sync step counts those, so a miss that stops being rare shows up.
_ISSUE_NODES = """
      pageInfo { hasNextPage endCursor }
      nodes {
        identifier title url priority priorityLabel dueDate estimate
        createdAt updatedAt startedAt
        state { name type }
        assignee { name email isMe }
        team { key }
        labels(first: 10) { pageInfo { hasNextPage } nodes { name } }
        cycle { name endsAt isActive }
        project { id name }
        relations(first: 10) {
          pageInfo { hasNextPage }
          nodes { type relatedIssue { identifier state { type } } }
        }
        inverseRelations(first: 10) {
          pageInfo { hasNextPage }
          nodes { type issue { identifier state { type } } }
        }
      }"""

# With no assignee named: the key's own user's issues.
ASSIGNED_QUERY = f"""
query($after: String, $types: [String!]) {{
  viewer {{
    assignedIssues(first: 50, after: $after,
                   filter: {{state: {{type: {{in: $types}}}}}}) {{{_ISSUE_NODES}
    }}
  }}
}}
"""

# With an assignee named by email (`linear.me`): for a key made on a different
# account from the one the issues are assigned to.
ASSIGNEE_QUERY = f"""
query($after: String, $types: [String!], $email: String!) {{
  issues(first: 50, after: $after,
         filter: {{state: {{type: {{in: $types}}}},
                  assignee: {{email: {{eqIgnoreCase: $email}}}}}}) {{{_ISSUE_NODES}
  }}
}}
"""

# Whether `linear.me` names a user, asked only when its issue read is empty:
# the filter matches nothing for an email no user has, which would otherwise
# read as "no open issues". A read that returns issues already proves it.
USER_QUERY = """
query($email: String!) {
  users(first: 1, filter: {email: {eqIgnoreCase: $email}}) { nodes { id } }
}
"""


def _open_blocks(relations: Any, side: str) -> list[str]:
    """The keys of the open issues on `side` of each `blocks` relation."""
    out = []
    for rel in (relations or {}).get("nodes") or []:
        rel = rel or {}
        other = rel.get(side) or {}
        state = str((other.get("state") or {}).get("type") or "").lower()
        if rel.get("type") == "blocks" and other.get("identifier"):
            if state not in CLOSED_TYPES:
                out.append(other["identifier"])
    return sorted(set(out))


def assigned_issues(
    key: str,
    runner: Runner = post,
    timeout: float = DEFAULT_TIMEOUT,
    assignee: str = "",
) -> list[dict]:
    """The issues in OPEN_STATE_TYPES assigned to `assignee` (an email), or
    with none named to the key's own user, each: {key, title,
    url, state, state_type, priority (0 none, 1 urgent .. 4 low),
    priority_label, due, estimate, created, updated, started, team, labels,
    cycle, cycle_end, cycle_active, project, project_id, assignee,
    assignee_email, assignee_is_me, blocked_by, blocks}. `estimate` is the
    issue's points, None when unset; `assignee` the assignee's name, None when
    unassigned. `blocked_by` and `blocks` are the open issues on the other end
    of a `blocks` relation.
    Raises LinearError when `assignee` names no Linear user."""
    variables: dict[str, Any] = {"types": list(OPEN_STATE_TYPES)}
    if assignee:
        query, field = ASSIGNEE_QUERY, "issues"
        variables["email"] = assignee
    else:
        query, field = ASSIGNED_QUERY, "viewer.assignedIssues"
    nodes = _pages(runner, key, timeout, query, variables, field)
    if assignee and not nodes:
        body = {"query": USER_QUERY, "variables": {"email": assignee}}
        found = _data(runner(body, key, timeout))
        if not ((found.get("users") or {}).get("nodes")):
            raise LinearError(f"linear.me names no Linear user: {assignee}")
    out = []
    for node in nodes:
        if not (node or {}).get("identifier"):
            continue
        state = node.get("state") or {}
        cycle = node.get("cycle") or {}
        project = node.get("project") or {}
        who = node.get("assignee") or {}
        out.append(
            {
                "key": node["identifier"],
                "title": node.get("title") or "",
                "url": node.get("url"),
                "state": state.get("name"),
                "state_type": str(state.get("type") or "").lower(),
                "priority": int(node.get("priority") or 0),
                "priority_label": node.get("priorityLabel"),
                "due": node.get("dueDate"),
                "estimate": node.get("estimate"),
                "created": node.get("createdAt"),
                "updated": node.get("updatedAt"),
                "started": node.get("startedAt"),
                "team": (node.get("team") or {}).get("key"),
                "labels": [
                    str(n["name"])
                    for n in (node.get("labels") or {}).get("nodes") or []
                    if n and n.get("name")
                ],
                "cycle": cycle.get("name"),
                "cycle_end": cycle.get("endsAt"),
                "cycle_active": bool(cycle.get("isActive")),
                "project": project.get("name"),
                "project_id": project.get("id"),
                "assignee": who.get("name"),
                "assignee_email": who.get("email"),
                "assignee_is_me": bool(who.get("isMe")),
                "blocked_by": _open_blocks(node.get("inverseRelations"), "issue"),
                "blocks": _open_blocks(node.get("relations"), "relatedIssue"),
                "truncated": any(
                    ((node.get(f) or {}).get("pageInfo") or {}).get("hasNextPage")
                    for f in ("labels", "relations", "inverseRelations")
                ),
            }
        )
    return out
