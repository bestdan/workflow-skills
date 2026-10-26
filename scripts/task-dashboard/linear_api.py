"""Linear reads for the Workstreams tab: open projects, and which project each
Linear key the mirror mentions belongs to.

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
`completed` or `canceled`, filtered here rather than in the query. The key
lookup is ONE `issues` query per sync (paged at 250), filtered to the keys'
team and number, and only for keys whose team owns an open project.
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
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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
    nodes: list[dict] = []
    after = None
    while True:
        body = {"query": query, "variables": {**variables, "after": after}}
        conn: Any = _data(runner(body, key, timeout)).get(field)
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
