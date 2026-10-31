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

The fleet view's GitHub rows (github_fleet.py, `source = 'github'`) are a
separate step, `run_fleet()`, which the scheduled sync and `dashboard.py sync`
run after this one when `github.scope` is "owner". It records its own `sync_runs`
row (kind `github_fleet`), so its failure never reaches the Jira rows or the
Jira run's outcome.

After it, `run_workstreams()` (kind `workstreams`, workstreams.py) rebuilds
the Workstreams tables from GitHub milestones, the fleet rows and, when
`linear.enabled` is on, Linear projects.

With `linear.enabled` on, on any instance, `run_linear_issues()` (kind
`linear_issues`, linear_issues.py) mirrors my open assigned Linear issues for
Now under `source = 'linear'`.

The fleet view's session rows are two more steps of the same shape, each with
its own `sync_runs` row: `run_sessions()` (kind `sessions`) folds the ingested
session-state hook events into rows whenever the instance ingests snapshots,
and `run_claude_remote()` (kind `claude_remote`) reads the optional cloud
session list when `claude_remote.enabled` is on.

dev_docs/task-dashboard.md ("Sync modes and the cursor rule") says why.
"""

import json
import math
import sqlite3
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

import claude_remote
import github_fleet
import github_gh
import jira_acli
import linear_api
import linear_issues
import sessions

MODES = ("incremental", "reconcile", "full")

# A fleet run whose searches found the user's GraphQL budget under
# `github.min_remaining`: it wrote its rows but skipped the per-PR comments
# reads, so the dashboard gives way to interactive sessions. Its rows are
# written, so it counts as a success wherever SUCCESS_OUTCOMES is read.
THROTTLED = "throttled"
SUCCESS_OUTCOMES = ("ok", THROTTLED)

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


class NoJiraSource:
    """The Jira adapter for `jira.enabled = false`: every scope is empty.

    It makes no acli call. The run still records its Jira-mode `sync_runs`
    row, and only the GitHub step can make that row's outcome other than ok.
    Rows mirrored while Jira was on are dropped by the next reconcile or full
    run, as intended; overlay rows are kept.
    """

    def active_sprints(self) -> list[dict[str, Any]]:
        return []

    def search(self, jql: str) -> list[str]:
        return []

    def view(self, keys: list[str]) -> jira_acli.ViewResult:
        return jira_acli.ViewResult()

    def parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("NoJiraSource views nothing, so it parses nothing")


def jira_enabled(config: dict[str, Any]) -> bool:
    """`jira.enabled`, which defaults to true."""
    return bool(config["jira"].get("enabled", True))


class GithubSource:
    """The GitHub adapter as the sync uses it: `prs` rows with CI and the join."""

    def __init__(
        self, config: dict[str, Any], runner: github_gh.Runner = github_gh.run
    ) -> None:
        self.orgs = list(config["github"]["orgs"])
        self.runner = runner

    def prs(self, allowed_projects: "set[str]") -> "tuple[list[dict], list[str]]":
        """Rows for my PRs and reviews requested of me, and an always-empty
        list of failed lookups.

        CI state comes with the searches (github_gh.rollup_state), so this
        makes no call per PR, and no CI lookup can fail on its own: the list
        is always empty. It stays in the return for the `failed_keys` shape.
        """
        rows: dict[str, dict] = {}
        for row in github_gh.my_prs(self.orgs, self.runner):
            rows.setdefault(row["url"], row)
        for row in github_gh.review_requested(self.orgs, self.runner):
            rows.setdefault(row["url"], row)
        failed: list[str] = []
        for row in rows.values():
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
    counts: dict[str, Any],
    failed_keys: list[str],
    error: "str | None",
) -> None:
    conn.execute(
        "UPDATE sync_runs SET finished_at = ?, ok = ?, outcome = ?, error_kind = ?,"
        " error = ?, issues_seen = ?, counts = ?, failed_keys = ? WHERE id = ?",
        (
            _ts(finished),
            1 if outcome in SUCCESS_OUTCOMES else 0,
            outcome,
            None if outcome in SUCCESS_OUTCOMES else outcome,
            error,
            counts.get("searched", 0),
            json.dumps(counts, sort_keys=True),
            json.dumps(failed_keys),
            run_id,
        ),
    )
    conn.commit()


# --- fetch -------------------------------------------------------------------


def _search_union(jira: "JiraSource | NoJiraSource", queries: list[str]) -> list[str]:
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


def _view(
    jira: "JiraSource | NoJiraSource", keys: list[str], fetched: dict, failed: dict
) -> None:
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
    jira: "JiraSource | NoJiraSource",
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
    jira: "JiraSource | NoJiraSource | None" = None,
    github: "GithubSource | None" = None,
    clock: "Callable[[], datetime] | None" = None,
) -> dict[str, Any]:
    """Run one sync and return its `sync_runs` row as a dict.

    `jira` and `github` default to the real adapters; tests pass ones built on
    fake runners. With `jira.enabled` false the default is NoJiraSource,
    which makes no acli call. `github` is used only when `github.enabled` is
    true. `clock` gives the finish time (default: wall clock).
    """
    if mode not in MODES:
        raise ValueError(f"unknown sync mode: {mode!r}")
    if mode == "incremental" and last_cursor_run(db) is None:
        mode = "full"
    if jira is None:
        jira = JiraSource(config) if jira_enabled(config) else NoJiraSource()
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
    return _run_row(db, run_id)


def _run_row(db: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    cur = db.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,))
    names = [d[0] for d in cur.description]
    return dict(zip(names, cur.fetchone()))


# --- the fleet view's GitHub step --------------------------------------------

FLEET_KIND = github_fleet.RUN_KIND
# The `sync_runs.kind` of run_workstreams(); workstreams.RUN_KIND is this.
WORKSTREAMS_KIND = "workstreams"


def fleet_enabled(config: dict[str, Any]) -> bool:
    """`github.scope` is "owner" and GitHub is not switched off as a whole."""
    gh = config["github"]
    return bool(gh["enabled"] and gh["scope"] == "owner")


def issue_owners(owners: "list[str]") -> str:
    """The owner set a full issue read covered, as recorded in its counts."""
    return ",".join(sorted(owners))


def fleet_issue_cache(
    conn: sqlite3.Connection, before: int, owners: "list[str]"
) -> "dict | None":
    """github_fleet.fetch's `issue_cache`: the mirror's issue rows, the start
    of the last good fleet run before run `before`, and the start of the
    last good one that read the issues in full with none cut short by the
    cap. None when there is no such run, or when that full read covered
    other owners than `owners` (an edit to `github.orgs`, which takes effect
    on a restart that keeps the mirror), so the issues are read in full."""
    marks = ", ".join("?" * len(SUCCESS_OUTCOMES))
    base = (
        f"SELECT started_at, counts FROM sync_runs WHERE kind = ? AND id < ?"
        f" AND outcome IN ({marks})"
    )
    args = (FLEET_KIND, before, *SUCCESS_OUTCOMES)
    last = conn.execute(f"{base} ORDER BY id DESC LIMIT 1", args).fetchone()
    full = conn.execute(
        f"{base} AND COALESCE(json_extract(counts, '$.issues_reused'), 0) = 0"
        " AND COALESCE(json_extract(counts, '$.issues_truncated'), 0) = 0"
        " ORDER BY id DESC LIMIT 1",
        args,
    ).fetchone()
    if last is None or full is None:
        return None
    try:
        covered = (json.loads(full[1] or "{}") or {}).get("issue_owners")
    except ValueError:
        covered = None
    if covered != issue_owners(owners):
        return None
    rows = []
    for (raw,) in conn.execute(
        "SELECT raw_json FROM issues WHERE source = ? AND kind = 'issue'",
        (github_fleet.SOURCE,),
    ):
        try:
            rows.append(json.loads(raw))
        except (TypeError, ValueError):
            return None  # a row it cannot carry forward: read them all
    return {"rows": rows, "since": _parse_ts(last[0]), "read_at": _parse_ts(full[0])}


def run_fleet(
    config: dict[str, Any],
    db: sqlite3.Connection,
    now: datetime,
    *,
    runner: github_gh.Runner = github_gh.run,
    clock: "Callable[[], datetime] | None" = None,
) -> dict[str, Any]:
    """Fetch and write the fleet view's GitHub rows; return its `sync_runs` row.

    Failures are treated as the Jira sync's GitHub step treats them: an
    AuthError is `auth_failed` with the `gh auth login` hint, a milestone or
    comments lookup that failed for one PR is listed in `failed_keys`, and
    anything else is `failed`. A run that skipped the comments reads for a
    low budget is THROTTLED, not failed. No Exception is raised (an interrupt is recorded
    as a failed run, then re-raised), and none reaches the Jira rows:
    `github_fleet.write` replaces only `source = 'github'` rows, in its own
    transaction, so a failed run leaves the last good GitHub rows in place. The
    caller checks `fleet_enabled()` first.
    """
    clock = clock or (lambda: datetime.now(timezone.utc))
    run_id = _start_run(db, FLEET_KIND, now)
    # Mostly counts, plus `issue_owners`, the owner set a full issue read
    # covered (fleet_issue_cache).
    counts: dict[str, Any] = {}
    failed_keys: list[str] = []
    outcome, error = "ok", None
    try:
        previous = github_fleet.previous_prs(db)
        plan = github_fleet.fetch(
            config, runner, now=now, previous=previous,
            issue_cache=fleet_issue_cache(db, run_id, config["github"]["orgs"]),
        )  # fmt: skip
        failed_keys = list(plan["failed"])
        counts = {
            **plan["counts"],
            "rows": len(plan["rows"]),
            "lookup_failed": len(plan["failed"]),
            "designs_missing": len(plan["missing"]),
            "designs_truncated": len(plan["truncated"]),
            "issues_truncated": len(plan["issues_truncated"]),
        }
        counts["issues_reused"] = int(plan["issues_reused"])
        if not plan["issues_reused"]:
            counts["issue_owners"] = issue_owners(config["github"]["orgs"])
        if plan["remaining"] is not None:
            counts["graphql_remaining"] = plan["remaining"]
        if plan["throttled"]:
            outcome = THROTTLED
        github_fleet.write(db, plan["rows"], now)
    except github_gh.AuthError as exc:
        outcome, error = "auth_failed", f"{exc} (fix: run `gh auth login`)"
    except Exception as exc:  # recorded, not raised: the next run retries
        outcome = "failed"
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    except BaseException as exc:
        if db.in_transaction:
            db.rollback()
        reason = type(exc).__name__
        _finish_run(
            db, run_id, "failed", clock(), counts, failed_keys,
            f"interrupted ({reason})",
        )  # fmt: skip
        raise
    if db.in_transaction:
        db.rollback()
    _finish_run(db, run_id, outcome, clock(), counts, failed_keys, error)
    return _run_row(db, run_id)


# --- the fleet view's session steps ------------------------------------------


def _wall() -> datetime:
    return datetime.now(timezone.utc)


def _run_step(
    db: sqlite3.Connection,
    kind: str,
    now: datetime,
    clock: Callable[[], datetime],
    body: Callable[[], "tuple[dict[str, int], str | None]"],
) -> dict[str, Any]:
    """Record one step as a `sync_runs` row around `body`, as run_fleet does.

    `body` returns (counts, None) on success, or (counts, error) for an auth
    failure it classified itself; any other Exception is `failed`.
    """
    run_id = _start_run(db, kind, now)
    counts: dict[str, int] = {}
    outcome, error = "ok", None
    try:
        counts, auth_error = body()
        if auth_error is not None:
            outcome, error = "auth_failed", auth_error
    except Exception as exc:  # recorded, not raised: the next run retries
        outcome = "failed"
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    except BaseException as exc:
        if db.in_transaction:
            db.rollback()
        _finish_run(
            db, run_id, "failed", clock(), counts, [],
            f"interrupted ({type(exc).__name__})",
        )  # fmt: skip
        raise
    if db.in_transaction:
        db.rollback()
    _finish_run(db, run_id, outcome, clock(), counts, [], error)
    return _run_row(db, run_id)


def run_sessions(
    db: sqlite3.Connection,
    now: datetime,
    *,
    clock: "Callable[[], datetime] | None" = None,
) -> dict[str, Any]:
    """Rebuild the `claude-hook` session rows from `session_events`."""

    def body() -> "tuple[dict[str, int], str | None]":
        rows = sessions.derive(db, now)
        sessions.write(db, rows, now)
        return {
            "sessions": len(rows),
            "waiting": sum(1 for r in rows if r["status_bucket"] == "waiting"),
            "need_input": sum(1 for r in rows if r["needs_action"]),
            "empty_spawns": sum(1 for r in rows if r["empty_spawn"]),
        }, None

    return _run_step(db, sessions.RUN_KIND, now, clock or _wall, body)


def run_linear_issues(
    config: dict[str, Any],
    db: sqlite3.Connection,
    now: datetime,
    *,
    runner: linear_api.Runner = linear_api.post,
    resolve_key: "Callable[[], str]" = linear_api.resolve_key,
    clock: "Callable[[], datetime] | None" = None,
) -> dict[str, Any]:
    """Mirror my open assigned Linear issues (linear_issues.py) for Now. The
    caller checks `linear.enabled` first."""

    def body() -> "tuple[dict[str, Any], str | None]":
        return linear_issues.step_body(config, db, now, runner, resolve_key)

    return _run_step(db, linear_issues.RUN_KIND, now, clock or _wall, body)


def run_claude_remote(
    config: dict[str, Any],
    db: sqlite3.Connection,
    now: datetime,
    *,
    runner: claude_remote.Runner = claude_remote.run,
    clock: "Callable[[], datetime] | None" = None,
) -> dict[str, Any]:
    """Read the cloud session list and reconcile the `claude-remote` rows.

    An unavailable read (a refusal, a timeout, a reply it cannot parse) is a
    failed run that leaves the last good rows in place; it never reads as "no
    sessions". The caller checks `claude_remote.enabled` first.
    """

    def body() -> "tuple[dict[str, int], str | None]":
        try:
            plan = claude_remote.fetch(config, runner)
        except claude_remote.AuthError as exc:
            return {}, f"{exc} (fix: run `claude auth login`)"
        dropped = claude_remote.write(db, plan["rows"], now)
        return {**plan["counts"], **dropped}, None

    return _run_step(db, claude_remote.RUN_KIND, now, clock or _wall, body)


def run_workstreams(
    config: dict[str, Any],
    db: sqlite3.Connection,
    now: datetime,
    *,
    runner: github_gh.Runner = github_gh.run,
    linear_runner: linear_api.Runner = linear_api.post,
    resolve_key: "Callable[[], str]" = linear_api.resolve_key,
    clock: "Callable[[], datetime] | None" = None,
) -> dict[str, Any]:
    """Rebuild the Workstreams tables (workstreams.py); return its `sync_runs`
    row. It reads the fleet step's rows, so it runs after run_fleet, and
    only when fleet_enabled(). With `linear.enabled` off or no key it makes
    no Linear call."""
    # Imported here: workstreams draws its cards through views, which imports
    # fleet_now and needs, both of which import this module.
    import workstreams

    run_id = workstreams.run_step(
        config, db, now, _finish_run, _start_run,
        gh_runner=runner, linear_runner=linear_runner, resolve_key=resolve_key,
        clock=clock or _wall,
    )  # fmt: skip
    return _run_row(db, run_id)


def once(resolve_key: "Callable[[], str]") -> "Callable[[], str]":
    """`resolve_key`, called at most once: later calls return its key, or
    raise the linear_api.NoKey it raised. run_all hands one to both Linear
    steps, so a sync whose key comes from `op read` reads it once."""
    memo: "list[Any]" = []

    def call() -> str:
        if not memo:
            try:
                memo.append(resolve_key())
            except linear_api.NoKey as exc:
                memo.append(exc)
        if isinstance(memo[0], linear_api.NoKey):
            raise memo[0]
        return memo[0]

    return call


def run_all(
    mode: str,
    config: dict[str, Any],
    db: sqlite3.Connection,
    now: datetime,
    *,
    jira: "JiraSource | NoJiraSource | None" = None,
    github: "GithubSource | None" = None,
    fleet_runner: github_gh.Runner = github_gh.run,
    claude_runner: claude_remote.Runner = claude_remote.run,
    linear_runner: linear_api.Runner = linear_api.post,
    resolve_key: "Callable[[], str]" = linear_api.resolve_key,
    clock: "Callable[[], datetime] | None" = None,
    on_step: "Callable[[str], None] | None" = None,
) -> list[dict[str, Any]]:
    """One scheduled sync: `run()`, then each fleet step that is enabled.

    Returns the `sync_runs` row of each run made. The fleet steps run whatever
    the Jira run's outcome, since every step writes disjoint rows. `on_step`,
    when given, is called with each step's `sync_runs` kind as it starts (the
    server's /status reports it).
    """
    clock = clock or _wall
    step = on_step or (lambda _kind: None)
    resolve_key = once(resolve_key)
    step(mode)
    rows = [run(mode, config, db, now, jira=jira, github=github, clock=clock)]
    if fleet_enabled(config):
        step(FLEET_KIND)
        rows.append(run_fleet(config, db, clock(), runner=fleet_runner, clock=clock))
        step(WORKSTREAMS_KIND)
        rows.append(
            run_workstreams(
                config, db, clock(), runner=fleet_runner,
                linear_runner=linear_runner, resolve_key=resolve_key, clock=clock,
            )
        )  # fmt: skip
    if linear_issues.enabled(config):
        step(linear_issues.RUN_KIND)
        rows.append(
            run_linear_issues(
                config, db, clock(), runner=linear_runner,
                resolve_key=resolve_key, clock=clock,
            )
        )  # fmt: skip
    if sessions.enabled(config):
        step(sessions.RUN_KIND)
        rows.append(run_sessions(db, clock(), clock=clock))
    if config["claude_remote"]["enabled"]:
        step(claude_remote.RUN_KIND)
        rows.append(
            run_claude_remote(config, db, clock(), runner=claude_runner, clock=clock)
        )
    return rows
