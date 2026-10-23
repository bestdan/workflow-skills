"""The sync engine: turns adapter output into mirror rows.

A run has two phases and they never interleave. The FETCH phase calls the
adapters and touches no table. The WRITE phase applies every mirror change in
one SQLite transaction. So a run that fails anywhere leaves the mirror exactly
as it was. The run's `sync_runs` row is written outside that transaction, at
start and at finish, so a failure is still recorded.

Modes:

- `incremental` searches each scope with a relative cursor appended and views
  only the keys that come back. With no successful incremental or full run
  recorded, it runs as `full`.
- `reconcile` searches each scope for keys only, views the keys the mirror
  lacks, and drops the Jira rows no scope returned. Overlay rows are kept.
- `full` views every key the scopes return, and drops the same way.

Every mode then runs the linked pass (one hop of `Blocks` links) and, when
enabled, the GitHub adapter, joining PRs to the Jira project keys in the mirror.

Only rows with `source = 'jira'` are the Jira sync's. Rows from any other
source (the fleet view's sessions, worktrees, ...) are never read, updated or
dropped here.

The spec is the Sync section of dev_docs/designs/2026-10-23-task-dashboard.md.
"""

import json
import math
import sqlite3
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

import github_gh
import jira_acli

MODES = ("incremental", "reconcile", "full")
SOURCE = "jira"
KIND = "ticket"

# Scope bodies, before the parentheses and the done window. The sprint scope is
# built from the configured boards' active sprints, never `openSprints()`, which
# covers every team's sprints in a shared project.
ASSIGNED = "assignee = currentUser()"
REPORTED_OR_WATCHED = "reporter = currentUser() OR watcher = currentUser()"

# Keys per `key in (...)` query in the linked pass, to keep each JQL short.
KEY_CHUNK = 50

# Minutes added to the cursor to cover index lag; upserts make the overlap free.
CURSOR_OVERLAP_MINUTES = 5

_TS = "%Y-%m-%dT%H:%M:%SZ"


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS)


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, _TS).replace(tzinfo=timezone.utc)


def done_window(days: int) -> str:
    return f"(statusCategory != Done OR resolved >= -{int(days)}d)"


def build_scopes(config: dict[str, Any], sprint_ids: "list[int]") -> list[str]:
    """Each scope as `(<scope>) AND <done window>`.

    The parentheses come first because JQL binds AND tighter than OR: the
    reported/watched scope contains an OR, and an unparenthesized clause
    appended to it would filter only its last branch. With no active sprint the
    sprint scope is left out rather than sent as an empty `in ()`.
    """
    window = done_window(config["sync"]["done_window_days"])
    bodies = [ASSIGNED]
    if sprint_ids:
        bodies.append(f"sprint in ({', '.join(str(int(i)) for i in sprint_ids)})")
    bodies.append(REPORTED_OR_WATCHED)
    return [f"({body}) AND {window}" for body in bodies]


def with_clause(scope: str, clause: str) -> str:
    """`scope` wrapped in parentheses again, then AND `clause`."""
    return f"({scope}) AND {clause}"


def cursor_clause(minutes: int) -> str:
    return f'updated >= "-{int(minutes)}m"'


def cursor_minutes(last_start: datetime, now: datetime) -> int:
    """Minutes since the last successful run started, rounded up, plus 5.

    Relative because Jira reads an absolute JQL date in the profile timezone,
    not UTC, and `search` cannot return `updated` to give a high-water mark.
    """
    elapsed = max(0.0, (now - last_start).total_seconds())
    return math.ceil(elapsed / 60) + CURSOR_OVERLAP_MINUTES


class JiraSource:
    """The Jira adapter as the sync uses it: acli calls through one runner."""

    def __init__(
        self, config: dict[str, Any], runner: jira_acli.Runner = jira_acli.run_acli
    ) -> None:
        jira = config["jira"]
        self.acli = jira["acli_path"]
        self.board_ids = list(jira["board_ids"])
        self.field_ids = dict(jira["fields"])
        self.fields = list(jira_acli.VIEW_FIELDS) + [
            self.field_ids["sprint"],
            self.field_ids["flagged"],
        ]
        self.concurrency = int(config["sync"]["view_concurrency"])
        self.runner = runner

    def active_sprints(self) -> list[dict[str, Any]]:
        out = []
        for board in self.board_ids:
            for s in jira_acli.active_sprints(
                board, acli=self.acli, runner=self.runner
            ):
                out.append({**s, "board_id": board})
        return out

    def search(self, jql: str) -> list[str]:
        return jira_acli.search_keys(jql, acli=self.acli, runner=self.runner)

    def view(self, keys: list[str]) -> jira_acli.ViewResult:
        return jira_acli.view_issues(
            keys, self.fields, self.concurrency, acli=self.acli, runner=self.runner
        )

    def parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        return jira_acli.parse_issue(raw, self.field_ids)


class GithubSource:
    """The GitHub adapter as the sync uses it: `prs` rows with CI and the join."""

    def __init__(
        self, config: dict[str, Any], runner: github_gh.Runner = github_gh.run
    ) -> None:
        self.orgs = list(config["github"]["orgs"])
        self.runner = runner

    def prs(self, allowed_projects: "set[str]") -> "tuple[list[dict], list[str]]":
        """Rows for my PRs and reviews requested of me, and URLs whose CI failed.

        A PR whose CI lookup fails keeps a NULL `ci_state` and is reported, not
        fatal: one flaky `pr view` should not fail the whole run. An AuthError
        still raises.
        """
        rows: dict[str, dict] = {}
        for row in github_gh.my_prs(self.orgs, self.runner):
            rows.setdefault(row["url"], row)
        for row in github_gh.review_requested(self.orgs, self.runner):
            rows.setdefault(row["url"], row)
        failed: list[str] = []
        for url, row in rows.items():
            try:
                row["ci_state"] = github_gh.ci_state(url, self.runner)
            except github_gh.AuthError:
                raise
            except github_gh.GhError:
                row["ci_state"] = None
                failed.append(url)
            text = f"{row['title']} {row.get('head_ref') or ''}"
            keys = github_gh.jira_keys(text, allowed_projects)
            row["issue_key"] = keys[0] if keys else None
        return list(rows.values()), failed


# --- sync_runs ---------------------------------------------------------------


def last_cursor_run(conn: sqlite3.Connection) -> "sqlite3.Row | None":
    """The latest successful run that viewed every changed key.

    A reconcile views only the keys the mirror lacks, so it does not advance
    the incremental cursor; a failed run never does.
    """
    return conn.execute(
        "SELECT started_at, failed_keys FROM sync_runs"
        " WHERE outcome = 'ok' AND kind IN ('incremental', 'full')"
        " ORDER BY started_at DESC, id DESC LIMIT 1"
    ).fetchone()


def _start_run(conn: sqlite3.Connection, mode: str, now: datetime) -> int:
    cur = conn.execute(
        "INSERT INTO sync_runs (kind, started_at) VALUES (?, ?)", (mode, _ts(now))
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    outcome: str,
    finished: datetime,
    counts: dict[str, int],
    failed_keys: list[str],
    error: "str | None",
) -> None:
    conn.execute(
        "UPDATE sync_runs SET finished_at = ?, ok = ?, outcome = ?, error_kind = ?,"
        " error = ?, issues_seen = ?, counts = ?, failed_keys = ? WHERE id = ?",
        (
            _ts(finished),
            1 if outcome == "ok" else 0,
            outcome,
            None if outcome == "ok" else outcome,
            error,
            counts.get("searched", 0),
            json.dumps(counts, sort_keys=True),
            json.dumps(failed_keys),
            run_id,
        ),
    )
    conn.commit()


# --- fetch -------------------------------------------------------------------


def _search_union(jira: JiraSource, queries: list[str]) -> list[str]:
    keys: dict[str, None] = {}
    for jql in queries:
        for k in jira.search(jql):
            keys.setdefault(k, None)
    return list(keys)


def _is_pr_url(key: str) -> bool:
    """`sync_runs.failed_keys` holds Jira keys and the PR URLs whose CI failed."""
    return key.startswith("https://")


def _key_queries(keys: "list[str]", clause: "str | None") -> list[str]:
    out = []
    for i in range(0, len(keys), KEY_CHUNK):
        jql = f"key in ({', '.join(keys[i : i + KEY_CHUNK])})"
        out.append(with_clause(jql, clause) if clause else jql)
    return out


def _view(jira: JiraSource, keys: list[str], fetched: dict, failed: dict) -> None:
    """View `keys` not yet fetched this run, parsing into `fetched`."""
    todo = [k for k in dict.fromkeys(keys) if k not in fetched]
    if not todo:
        return
    result = jira.view(todo)
    for key, raw in result.issues.items():
        fetched[key] = jira.parse(raw)
        failed.pop(key, None)
    for key in result.failed:
        failed[key] = result.errors.get(key, "view failed")


def _blocks_targets(links: list[dict[str, Any]]) -> list[str]:
    return [
        link["linked_key"]
        for link in links
        if (link.get("link_type") or "").lower() == "blocks"
    ]


def fetch(
    mode: str,
    config: dict[str, Any],
    conn: sqlite3.Connection,
    now: datetime,
    jira: JiraSource,
    github: "GithubSource | None",
) -> dict[str, Any]:
    """Everything the write phase needs. Reads the mirror; writes nothing."""
    mirror_scope: dict[str, "str | None"] = dict(
        conn.execute("SELECT key, scope FROM issues WHERE source = ?", (SOURCE,))
    )
    mirror_keys = set(mirror_scope)
    mirror_links: dict[str, list[dict[str, Any]]] = {}
    for issue_key, linked_key, link_type in conn.execute(
        "SELECT issue_key, linked_key, link_type FROM links"
    ):
        mirror_links.setdefault(issue_key, []).append(
            {"linked_key": linked_key, "link_type": link_type}
        )

    sprints = jira.active_sprints()
    scopes = build_scopes(config, sorted({s["id"] for s in sprints}))

    fetched: dict[str, dict[str, Any]] = {}
    failed: dict[str, str] = {}
    cursor: "str | None" = None

    if mode == "incremental":
        prev = last_cursor_run(conn)
        assert prev is not None, "incremental needs a prior cursor run"
        cursor = cursor_clause(cursor_minutes(_parse_ts(prev[0]), now))
        union = _search_union(jira, [with_clause(s, cursor) for s in scopes])
        # Keys whose view failed last time were not refreshed; retry them now,
        # including ones new to the mirror: the cursor has moved past them, so
        # nothing else would bring them back before a reconcile. A retried key
        # this run places in no scope is written with a NULL scope, which the
        # next reconcile sets. The list also holds PR URLs whose CI lookup
        # failed; those are not Jira keys.
        retry = [k for k in json.loads(prev[1] or "[]") if not _is_pr_url(k)]
        _view(jira, union + retry, fetched, failed)
        # The in-scope set is this run's hits plus what earlier runs found in
        # scope; incremental cannot see an issue leave scope (reconcile does).
        earlier = [k for k, sc in mirror_scope.items() if sc == "search"]
        in_scope = list(dict.fromkeys(union + earlier))
    else:
        union = _search_union(jira, scopes)
        if mode == "reconcile":
            _view(jira, [k for k in union if k not in mirror_keys], fetched, failed)
        else:
            _view(jira, union, fetched, failed)
        in_scope = union

    # Linked pass: blocker and blocked keys one hop from the in-scope set, and
    # no further: a linked issue's own links are stored but never walked.
    scope_set = set(in_scope)
    linked: dict[str, None] = {}
    for key in in_scope:
        links = fetched[key]["links"] if key in fetched else mirror_links.get(key, [])
        for target in _blocks_targets(links):
            if target not in scope_set:
                linked.setdefault(target, None)
    linked_keys = list(linked)
    if mode == "full":
        _view(jira, linked_keys, fetched, failed)
    else:
        _view(jira, [k for k in linked_keys if k not in mirror_keys], fetched, failed)
        if cursor is not None:
            # A linked issue matches no scope query, so in incremental its
            # changes reach the mirror only through this search over known ones.
            known = [k for k in linked_keys if k in mirror_keys]
            changed = _search_union(jira, _key_queries(known, cursor))
            _view(jira, changed, fetched, failed)

    scope_of = {k: "linked" for k in linked_keys}
    scope_of.update({k: "search" for k in in_scope})

    dropped: list[str] = []
    if mode in ("reconcile", "full"):
        dropped = sorted(mirror_keys - set(scope_of))

    prs: "list[dict] | None" = None
    pr_failed: list[str] = []
    if github is not None:
        final_keys = (mirror_keys - set(dropped)) | set(fetched)
        projects = {k.rsplit("-", 1)[0] for k in final_keys if "-" in k}
        prs, pr_failed = github.prs(projects)

    return {
        "mode": mode,
        "sprints": sprints,
        "union": union,
        "linked": linked_keys,
        "scope": scope_of,
        "fetched": fetched,
        "failed": failed,
        "dropped": dropped,
        "prs": prs,
        "pr_failed": pr_failed,
    }


# --- write -------------------------------------------------------------------

_ISSUE_FIELDS = (
    "summary", "status_name", "status_category", "status_category_changed",
    "assignee", "reporter", "priority", "due_date", "parent_key", "labels",
    "created", "updated", "resolution_date", "sprint_id", "sprint_name",
    "sprint_state", "sprint_end", "flagged", "raw_json",
)  # fmt: skip

_UPSERT_ISSUE = (
    "INSERT INTO issues (source, kind, key, "
    + ", ".join(_ISSUE_FIELDS)
    + ", scope, synced_at) VALUES ("
    + ", ".join("?" * (len(_ISSUE_FIELDS) + 5))
    + ") ON CONFLICT(source, key) DO UPDATE SET kind = excluded.kind, "
    + ", ".join(f"{c} = excluded.{c}" for c in _ISSUE_FIELDS)
    + ", scope = COALESCE(excluded.scope, issues.scope)"
    + ", synced_at = excluded.synced_at"
)

_PR_FIELDS = (
    "url", "repo", "number", "title", "head_ref", "is_draft", "review_decision",
    "ci_state", "role", "issue_key", "updated", "raw_json",
)  # fmt: skip


def write(conn: sqlite3.Connection, plan: dict[str, Any], now: datetime) -> None:
    """Apply one run's mirror changes in a single transaction."""
    stamp = _ts(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for parsed in plan["fetched"].values():
            issue = parsed["issue"]
            conn.execute(
                _UPSERT_ISSUE,
                (SOURCE, KIND, issue["key"])
                + tuple(issue[c] for c in _ISSUE_FIELDS)
                + (plan["scope"].get(issue["key"]), stamp),
            )
            conn.execute("DELETE FROM links WHERE issue_key = ?", (issue["key"],))
            conn.executemany(
                "INSERT OR REPLACE INTO links (issue_key, linked_key, link_type,"
                " direction, linked_status_category) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        link["issue_key"],
                        link["linked_key"],
                        link["link_type"],
                        link["direction"],
                        link["linked_status_category"],
                    )
                    for link in parsed["links"]
                ],
            )
        # A key can move between `search` and `linked` without being viewed
        # again; reconcile and full saw every scope, so record where each is.
        if plan["mode"] in ("reconcile", "full"):
            conn.executemany(
                "UPDATE issues SET scope = ? WHERE source = ? AND key = ?",
                [(sc, SOURCE, k) for k, sc in plan["scope"].items()],
            )
        for key in plan["dropped"]:
            conn.execute(
                "DELETE FROM issues WHERE source = ? AND key = ?", (SOURCE, key)
            )
            conn.execute("DELETE FROM links WHERE issue_key = ?", (key,))
        conn.execute("DELETE FROM sprints")
        conn.executemany(
            "INSERT OR REPLACE INTO sprints (id, board_id, name, state, start_date,"
            " end_date) VALUES (?, ?, ?, 'active', ?, ?)",
            [
                (s["id"], s["board_id"], s["name"], s["startDate"], s["endDate"])
                for s in plan["sprints"]
            ],
        )
        if plan["prs"] is not None:
            conn.execute("DELETE FROM prs")
            conn.executemany(
                f"INSERT INTO prs ({', '.join(_PR_FIELDS)}) VALUES"
                f" ({', '.join('?' * len(_PR_FIELDS))})",
                [tuple(row.get(c) for c in _PR_FIELDS) for row in plan["prs"]],
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


# --- run ---------------------------------------------------------------------


def run(
    mode: str,
    config: dict[str, Any],
    db: sqlite3.Connection,
    now: datetime,
    *,
    jira: "JiraSource | None" = None,
    github: "GithubSource | None" = None,
    clock: "Callable[[], datetime] | None" = None,
) -> dict[str, Any]:
    """Run one sync and return its `sync_runs` row as a dict.

    `jira` and `github` default to the real adapters; tests pass ones built on
    fake runners. `github` is used only when `github.enabled` is true. `clock`
    gives the finish time (default: wall clock).
    """
    if mode not in MODES:
        raise ValueError(f"unknown sync mode: {mode!r}")
    if mode == "incremental" and last_cursor_run(db) is None:
        mode = "full"
    jira = jira or JiraSource(config)
    if config["github"]["enabled"]:
        github = github or GithubSource(config)
    else:
        github = None
    clock = clock or (lambda: datetime.now(timezone.utc))

    run_id = _start_run(db, mode, now)
    counts: dict[str, int] = {}
    failed_keys: list[str] = []
    outcome, error = "ok", None
    try:
        plan = fetch(mode, config, db, now, jira, github)
        failed_keys = sorted(plan["failed"]) + plan["pr_failed"]
        counts = {
            "searched": len(plan["union"]),
            "linked": len(plan["linked"]),
            "viewed": len(plan["fetched"]),
            "view_failed": len(plan["failed"]),
            "dropped": len(plan["dropped"]),
            "sprints": len(plan["sprints"]),
        }
        if plan["prs"] is not None:
            counts["prs"] = len(plan["prs"])
            counts["ci_failed"] = len(plan["pr_failed"])
        write(db, plan, now)
    except jira_acli.AuthError as exc:
        outcome, error = "auth_failed", f"{exc} (fix: run `acli jira auth login`)"
    except github_gh.AuthError as exc:
        outcome, error = "auth_failed", f"{exc} (fix: run `gh auth login`)"
    except Exception as exc:  # recorded, not raised: the next run retries
        outcome = "failed"
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    except BaseException as exc:
        # Ctrl-C or SystemExit: record the run as failed so its row does not
        # read as still running (NULL outcome), then let the interrupt through.
        if db.in_transaction:
            db.rollback()
        reason = type(exc).__name__
        _finish_run(
            db,
            run_id,
            "failed",
            clock(),
            counts,
            failed_keys,
            f"interrupted ({reason})",
        )
        raise
    if db.in_transaction:
        db.rollback()
    _finish_run(db, run_id, outcome, clock(), counts, failed_keys, error)
    cur = db.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,))
    names = [d[0] for d in cur.description]
    return dict(zip(names, cur.fetchone()))
