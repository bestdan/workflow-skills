"""`dashboard.py scopes` and `dashboard.py items`: the mirror as a query.

dev_docs/designs/2026-10-30-dashboard-as-agent-data-source.md, sections 1
and 2. `view` prints a whole tab shaped for its page; these print what an
agent asked for: the scopes a word can mean, and one compact record per item
in a scope. Each reads the DB directly and needs no running server.

Every field comes from the code the views already use, so the two cannot
disagree: fleet_tabs.pull_requests for PRs, fleet_now for the Now terms and
Little's size test, needs for whether a session waits on the owner,
fleet_views and views for Stale, workstreams for membership, score for Jira's
links. Nothing here ranks: items are listed newest activity first, which is
recency, not priority.

Items (`kind`, the dashboard's own word for each):

- `issue`: a mirrored GitHub issue (the mirror holds open ones only).
- `pr`: an open PR, mine, one asking for my review, or another's on my repos.
- `ticket`: a Jira issue, done ones in the done window included.
- `design`: a design doc at `status: proposed`.
- `session`: a Claude Code session the hooks report, not ended.
- `worktree`: a worktree a machine snapshot reported; a landed one is `done`.

A scope is an id (`repo:`, `ws:`, `jira:`, `epic:`, `label:`, `host:`) or a
bare word, which matches ids and titles case-insensitively. `all` is reserved:
it means no filter and is never matched as a word.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import close_out
import fleet_now
import fleet_tabs
import fleet_views
import gauges
import github_fleet
import needs
import overlay
import score
import sessions
import sync
import views
import workstreams

SCHEMA_VERSION = 1

ALL = "all"
KINDS = ("issue", "pr", "ticket", "design", "session", "worktree")
STATES = ("todo", "started", "done")
OPEN_STATES = ("todo", "started")
ONLY = ("blocked", "waiting", "stale")
SCOPE_KINDS = ("repo", "workstream", "jira", "epic", "label", "host")
_PREFIX = {
    "repo": "repo",
    "ws": "workstream",
    "jira": "jira",
    "epic": "epic",
    "label": "label",
    "host": "host",
}

IDENTITY = ("source", "key", "ref", "kind")
BRIEF = IDENTITY + (
    "title", "url", "state", "prio", "due", "age_d", "idle_d", "labels",
    "workstreams", "pr", "activity", "blocks", "blocked_by", "overlay", "now",
    "size",
)  # fmt: skip
FULL = BRIEF + (
    "repo", "branch", "host", "path", "parent", "created", "updated",
    "evidence", "risks", "action",
)  # fmt: skip
# The fields that cost a whole Now or Stale build; skipped when not asked for.
_NOW_FIELDS = {"now", "evidence", "action"}
_STALE_FIELDS = {"evidence", "risks", "action"}

_JIRA_STATE = {"new": "todo", "indeterminate": "started", "done": "done"}

Item = dict[str, Any]


class QueryError(ValueError):
    """A scope, kind, state, filter or field this instance cannot answer."""


# --- scopes ------------------------------------------------------------------


@dataclass
class Scope:
    id: str
    kind: str
    title: str
    match: "Callable[[Item], bool]"

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "title": self.title}


Match = Callable[[Item], bool]


def _in_repo(name: str) -> Match:
    """An item in repo `name` (`owner/name`, or a bare directory name): an
    item that knows its owner compares the full name; a worktree or session,
    which knows only its directory, compares the bare one."""
    bare = fleet_views._name(name)
    full = name if "/" in name else None

    def test(i: Item) -> bool:
        if full and i["repo"] and "/" in i["repo"]:
            return bool(i["repo"] == full)
        return bool(i["_repo_bare"] == bare)

    return test


def _in_ws(ws: str) -> Match:
    return lambda i: ws in i["workstreams"]


def _in_project(proj: str) -> Match:
    return lambda i: i["kind"] == "ticket" and i["key"].split("-", 1)[0] == proj


def _in_epic(epic: str) -> Match:
    return lambda i: i["parent"] == epic or i["key"] == epic


def _labelled(label: str) -> Match:
    return lambda i: label in (i["labels"] or [])


def _on_host(host: str) -> Match:
    return lambda i: i["host"] == host


def _scopes(conn: sqlite3.Connection, items: list[Item]) -> list[Scope]:
    """Every scope the items and the workstreams table can name, by kind then id."""
    out: dict[str, Scope] = {}

    def add(sid: str, kind: str, title: str, test: Match) -> None:
        out.setdefault(sid, Scope(sid, kind, title, test))

    full = {i["repo"] for i in items if i["repo"] and "/" in i["repo"]}
    for name in full:
        add(f"repo:{name}", "repo", name, _in_repo(name))
    # A repo only a worktree or session names, by its bare directory name.
    bare_full = {fleet_views._name(r) for r in full}
    for i in items:
        bare = i["_repo_bare"]
        if bare and bare not in bare_full:
            add(f"repo:{bare}", "repo", bare, _in_repo(bare))
    for ws_id, title in conn.execute("SELECT id, title FROM workstreams ORDER BY id"):
        add(f"ws:{ws_id}", "workstream", title or ws_id, _in_ws(ws_id))
    titles = {i["key"]: i["title"] for i in items if i["kind"] == "ticket"}
    for i in items:
        if i["kind"] == "ticket":
            proj = i["key"].split("-", 1)[0]
            add(f"jira:{proj}", "jira", proj, _in_project(proj))
            parent = i["parent"]
            if parent:
                add(f"epic:{parent}", "epic", titles.get(parent) or parent,
                    _in_epic(parent))  # fmt: skip
        for label in i["labels"] or []:
            add(f"label:{label}", "label", label, _labelled(label))
        if i["host"]:
            add(f"host:{i['host']}", "host", i["host"], _on_host(i["host"]))
    order = {k: n for n, k in enumerate(SCOPE_KINDS)}
    return sorted(out.values(), key=lambda s: (order[s.kind], s.id.lower()))


def _is_open(item: Item) -> bool:
    return item["state"]["category"] in OPEN_STATES


def _count(scope: Scope, items: list[Item]) -> int:
    return sum(1 for i in items if _is_open(i) and scope.match(i))


def match(scopes: list[Scope], word: str) -> list[Scope]:
    """The scopes `word` names: the one whose id it is, else every scope whose
    id or title contains it, case aside. `repo:<name>` with no owner also
    names each `repo:<owner>/<name>`, since worktree and session paths show
    only the bare name. Callers treat `all` as no filter before calling this
    (resolve(), scopes_doc()); match itself does not reserve it."""
    exact = [s for s in scopes if s.id == word]
    if exact:
        return exact
    w = word.lower()
    if w.startswith("repo:") and "/" not in w:
        bare = w[len("repo:") :]
        return [
            s
            for s in scopes
            if s.kind == "repo" and (fleet_views._name(s.title) or "").lower() == bare
        ]
    return [s for s in scopes if w in s.id.lower() or w in s.title.lower()]


def resolve(scopes: list[Scope], words: "list[str] | None") -> "list[Scope] | None":
    """The union of what each word matches; None for no filter (no words, or
    `all` among them). Raises QueryError for a word that matches nothing."""
    if not words or ALL in words:
        return None
    out: dict[str, Scope] = {}
    for word in words:
        found = match(scopes, word)
        if not found:
            prefix = word.split(":", 1)[0]
            hint = (
                f"no {_PREFIX[prefix]} scope {word!r}"
                if prefix in _PREFIX and ":" in word
                else f"no scope matches {word!r}"
            )
            raise QueryError(f"{hint}; `scopes` lists them")
        for s in found:
            out.setdefault(s.id, s)
    return list(out.values())


def related(
    conn: sqlite3.Connection, matched: list[Scope], items: list[Item]
) -> list[dict[str, Any]]:
    """Every workstream with a member in a matched repo scope, with how many
    of its member items are in that repo, most first. Never applied: the
    agent confirms each one and passes its `ws:` id."""
    repos = [s for s in matched if s.kind == "repo"]
    if not repos:
        return []
    taken = {s.id for s in matched}
    titles = dict(conn.execute("SELECT id, title FROM workstreams"))
    counts: dict[str, int] = {}
    for i in items:
        if any(s.match(i) for s in repos):
            for ws in i["workstreams"]:
                counts[ws] = counts.get(ws, 0) + 1
    out = [
        {"id": f"ws:{ws}", "kind": "workstream", "title": titles.get(ws) or ws,
         "members_in_scope": n}
        for ws, n in counts.items()
        if f"ws:{ws}" not in taken
    ]  # fmt: skip
    return sorted(out, key=lambda r: (-r["members_in_scope"], r["id"]))


# --- items -------------------------------------------------------------------


def _stamp(dt: "datetime | None") -> "str | None":
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _days(since: "str | None", now: datetime) -> "int | None":
    at = score.parse_ts(since)
    return None if at is None else max(0, int((now - at).total_seconds() // 86400))


def _item(source: str, key: str, kind: str, **fields: Any) -> Item:
    base: Item = {
        "source": source, "key": key, "ref": None, "kind": kind, "title": "",
        "url": None, "state": None, "prio": None, "due": None, "labels": [],
        "repo": None, "branch": None, "host": None, "path": None, "parent": None,
        "created": None, "updated": None, "blocks": None, "blocked_by": None,
        "_blocked": False, "_waiting": False, "_size": None, "_pr_row": None,
    }  # fmt: skip
    base.update(fields)
    base["_repo_bare"] = fleet_views._name(base["repo"])
    return base


def _issue_state(labels: list[str]) -> dict[str, Any]:
    status = next((lb for lb in labels if lb.startswith("status:")), None)
    started = status in workstreams.STARTED_LABELS
    return {"name": status, "category": "started" if started else "todo"}


def _issues(conn: sqlite3.Connection, cfg: dict[str, Any]) -> list[Item]:
    # The same gate as Now's issue sources: none before a fleet run succeeds.
    if gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) is None:
        return []
    out = []
    for key, created, updated, raw in conn.execute(
        "SELECT key, created, updated, raw_json FROM issues"
        " WHERE source = ? AND kind = 'issue' ORDER BY key",
        (github_fleet.SOURCE,),
    ):
        row = fleet_views._raw(raw)
        labels = list(row.get("labels") or [])
        est = fleet_now.est_of(labels)
        out.append(_item(
            github_fleet.SOURCE, str(row.get("key") or key), "issue",
            ref=f"{row.get('repo') or ''}#{row.get('number')}",
            title=row.get("title") or "", url=row.get("url"),
            state=_issue_state(labels), prio=score.prio_of(labels),
            labels=labels, repo=row.get("repo"),
            created=row.get("created") or created,
            updated=row.get("updated") or updated,
            blocks=row.get("blocks"), blocked_by=row.get("blocked_by"),
            _blocked=fleet_now.issue_blocked(row),
            _size=(f"est:{est}", f"label est:{est}") if est else None,
        ))  # fmt: skip
    return out


def _prs(conn: sqlite3.Connection, cfg: dict[str, Any]) -> list[Item]:
    if not cfg["github"]["enabled"]:
        return []
    mirrored = {
        url: (updated, head)
        for url, updated, head in conn.execute("SELECT url, updated, head_ref FROM prs")
    }
    out = []
    for p in fleet_tabs.pull_requests(conn, cfg):
        awaiting = p.get("awaiting") or {}
        updated, head = mirrored.get(p["url"], (None, None))
        out.append(_item(
            github_fleet.SOURCE, str(p["url"]), "pr",
            ref=f"{p.get('repo') or ''}#{p.get('number')}",
            title=p.get("title") or "", url=p.get("url"),
            state={"name": "draft" if p.get("is_draft") else "open",
                   "category": "started"},
            repo=p.get("repo"), branch=awaiting.get("head_ref") or head,
            created=p.get("created"), updated=awaiting.get("updated") or updated,
            _size=fleet_now.pr_bucket(p), _pr_row=p,
        ))  # fmt: skip
    return out


def _designs(conn: sqlite3.Connection, cfg: dict[str, Any]) -> list[Item]:
    if gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) is None:
        return []
    return [
        _item(
            github_fleet.SOURCE,
            str(d.get("key") or d.get("url")),
            "design",
            ref=d.get("path"),
            title=d.get("title") or "",
            url=d.get("url"),
            state={"name": d.get("status") or "proposed", "category": "todo"},
            repo=d.get("repo"),
            created=d.get("created"),
        )  # fmt: skip
        for d in workstreams.mirrored_designs(conn)
    ]


def _sessions(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[Item]:
    if not sessions.enabled(cfg):
        return []
    out = []
    for row in needs._rows(conn, sessions.SOURCE, sessions.KIND):
        if row.get("ended") or row.get("empty_spawn"):
            continue
        waiting = needs._waiting(row, now)
        state = "waiting" if waiting else row.get("status_bucket") or "unknown"
        out.append(_item(
            sessions.SOURCE, str(row.get("session_id") or row.get("key")), "session",
            ref=row.get("title"), title=row.get("first_prompt") or row.get("title") or "",
            state={"name": state, "category": "started"},
            repo=fleet_views.repo_of_path(row.get("cwd")), branch=row.get("branch"),
            host=row.get("host"), path=row.get("cwd"),
            created=row.get("created"), updated=row.get("updated"),
            _waiting=waiting,
            # As Needs you dates the wait (needs._session).
            _waiting_since=row.get("waiting_since") or row.get("updated"),
        ))  # fmt: skip
    return out


def _worktree_state(row: dict[str, Any]) -> dict[str, Any]:
    landed = (row.get("landed") or {}).get("state") == "landed"
    if landed:
        return {"name": "landed", "category": "done"}
    bits = (["dirty"] if row.get("dirty") else []) + (
        [f"{row['unpushed']} unpushed"] if row.get("unpushed") else []
    )
    return {"name": ", ".join(bits) or "clean", "category": "started"}


def _worktrees(conn: sqlite3.Connection) -> list[Item]:
    out = []
    for source, key, raw in conn.execute(
        "SELECT source, key, raw_json FROM issues"
        " WHERE source LIKE 'machine:%' AND kind = 'worktree' ORDER BY source, key"
    ):
        row = fleet_views._raw(raw)
        path = row.get("path")
        out.append(_item(
            source, key, "worktree", ref=row.get("branch"),
            title=path or key, state=_worktree_state(row),
            repo=row.get("repo") or fleet_views.repo_of_path(path),
            branch=row.get("branch"), host=source.split(":", 1)[1], path=path,
        ))  # fmt: skip
    return out


def _tickets(m: "views.Mirror | None") -> list[Item]:
    if m is None:
        return []
    out = []
    for key, issue in m.issues.items():
        links = m.links.get(key, [])
        labels = score.labels_of(issue)
        category = (issue.get("status_category") or "").lower()
        est = fleet_now.est_of(labels)
        out.append(_item(
            "jira", key, "ticket", ref=key, title=issue.get("summary") or "",
            url=f"https://{m.cfg['jira']['site']}/browse/{key}",
            state={"name": issue.get("status_name"),
                   "category": _JIRA_STATE.get(category, "todo")},
            prio=issue.get("priority"), due=issue.get("due_date"), labels=labels,
            parent=issue.get("parent_key"),
            created=issue.get("created"), updated=issue.get("updated"),
            blocks=score.open_blocked(links),
            blocked_by=score.unresolved_blockers(links),
            _blocked=score.is_blocked(issue, links, m.cfg)[0],
            _size=(f"est:{est}", f"label est:{est}") if est else None,
        ))  # fmt: skip
    return out


# --- joins -------------------------------------------------------------------


def _pr_brief(p: dict[str, Any]) -> dict[str, Any]:
    """A PR's readiness, as its card reads it."""
    awaiting = p.get("awaiting")
    misses = (awaiting or {}).get("queue_misses") or []
    return {
        "ref": f"{p.get('repo') or ''}#{p.get('number')}",
        "ci": p.get("ci_state"),
        "review": p.get("review_decision"),
        "draft": bool(p.get("is_draft")),
        "co_reviewed": None if awaiting is None else "co_review" not in misses,
    }


def _link_prs(items: list[Item]) -> None:
    """Each item's `pr`: a PR its own readiness; a GitHub issue the newest
    open PR that closes it; a Jira ticket the newest that names its key; a
    session or worktree the newest on its repo and branch. Newest is the last
    updated (else created), the number breaking a tie: one Jira key can be
    named by PRs in several repos, whose numbers do not compare. A PR whose
    head is a default branch is never matched by branch: a fork's `main` would
    otherwise claim every session and checkout on the owner's `main`."""
    by_issue: dict[str, Item] = {}
    by_jira: dict[str, Item] = {}
    by_branch: dict[tuple, Item] = {}

    def recency(x: Item) -> tuple:
        at = score.parse_ts(x["updated"]) or score.parse_ts(x["created"])
        return (at is not None, at.timestamp() if at else 0.0,
                x["_pr_row"].get("number") or 0)  # fmt: skip

    def newer(index: dict, k: Any, pr: Item) -> None:
        if k not in index or recency(pr) > recency(index[k]):
            index[k] = pr

    for i in items:
        p = i["_pr_row"]
        if p is None:
            continue
        for c in (p.get("awaiting") or {}).get("closes") or []:
            if c.get("repo") and c.get("number") is not None:
                url = f"https://github.com/{c['repo']}/issues/{c['number']}"
                newer(by_issue, url, i)
        if p.get("issue_key"):
            newer(by_jira, p["issue_key"], i)
        if i["branch"] and i["branch"] not in close_out.DEFAULT_BRANCHES:
            newer(by_branch, (i["_repo_bare"], i["branch"]), i)
    for i in items:
        found: "Item | None"
        if i["_pr_row"] is not None:
            found = i
        elif i["kind"] == "issue":
            found = by_issue.get(i["key"])
        elif i["kind"] == "ticket":
            found = by_jira.get(i["key"])
        else:
            found = by_branch.get((i["_repo_bare"], i["branch"]))
        i["pr"] = _pr_brief(found["_pr_row"]) if found else None
        # Sessions and worktrees are found on the PR's branch, in its repo;
        # never on a PR's default-branch head, for the reason above.
        if found is None:
            i["_linked"] = (i["_repo_bare"], i["branch"])
        elif found["branch"] in close_out.DEFAULT_BRANCHES:
            i["_linked"] = (found["_repo_bare"], None)
        else:
            i["_linked"] = (found["_repo_bare"], found["branch"])


def _activity(items: list[Item]) -> None:
    """Each item's live sessions and open worktrees, matched by repo and
    branch: on its own branch, or on its linked PR's. A session or worktree
    lists itself."""
    on: dict[tuple, list[dict[str, Any]]] = {}
    for i in items:
        if i["kind"] in ("session", "worktree") and _is_open(i) and i["branch"]:
            on.setdefault((i["_repo_bare"], i["branch"]), []).append({
                "kind": i["kind"], "host": i["host"], "state": i["state"]["name"],
                "waiting_since": i.get("_waiting_since") if i["_waiting"] else None,
            })  # fmt: skip
    for i in items:
        i["activity"] = list(on.get(i["_linked"], [])) if i["_linked"][1] else []
        if i["kind"] != "session":
            i["_waiting"] = any(a["state"] == "waiting" for a in i["activity"])


def _overlay(ov: overlay.Overlay, last_why: dict, item: Item, now: datetime) -> Any:
    it = (item["source"], item["key"])
    out: dict[str, Any] = {}
    if ov.is_pinned(it):
        out["pinned"] = True
    if ov.is_snoozed(it, now):
        out["snoozed_until"] = ov.snoozed_until[it]
    if it in ov.ignored:
        out["ignored"] = True
    if ov.notes.get(it):
        out["note"] = ov.notes[it]
    if it in last_why:
        out["why_not"] = last_why[it]
    return out or None


def _last_why_not(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for s, k, reason, text, at in conn.execute(
        "SELECT source, item_key, reason, text, recorded_at FROM why_not"
        " ORDER BY recorded_at, id"
    ):
        out[(s, k)] = {"reason": reason, "text": text or None, "at": at}
    return out


def _size(item: Item, minutes: dict[str, Any]) -> "dict[str, Any] | None":
    found = item["_size"]
    if found is None:
        return None
    bucket, why = found
    return {"bucket": bucket, "est_min": minutes.get(bucket), "from": why}


def _now_terms(conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> dict:
    """(source, key) -> Ship's placing of it: tier, category, score and terms,
    or, for a PR folded into its issue or ticket, the item it folds into."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in fleet_now.candidates(conn, cfg, now):
        out[r.need.item] = {
            "tier": r.tier[1], "category": r.category, "score": r.total,
            "terms": [[name, value] for name, value, _ in r.terms if value],
            "_why": r.why, "_action": r.need.action.as_dict(),
        }  # fmt: skip
        for f in r.folded:
            out.setdefault(f.need.item, {"folded_into": r.need.item[1]})
    return out


def _stale(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime,
    m: "views.Mirror | None",
) -> dict[tuple[str, str], dict[str, Any]]:  # fmt: skip
    """(source, key) -> the Stale tab's evidence, risks and action for it."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if m is not None:
        for issue, why in views.stale(m):
            out[("jira", issue["key"])] = {"evidence": why, "risks": [], "action": None}
    if fleet_views.fleet_pages(cfg):
        for s in fleet_views.stale_items(fleet_views.load(conn, cfg), now):
            out[s.item] = {
                "evidence": s.evidence or s.why, "risks": list(s.risks),
                "action": s.action.as_dict(),
            }  # fmt: skip
    return out


def mirror(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> "views.Mirror | None":
    """The Jira views' read, or None on an instance with no Jira."""
    return views.Mirror(conn, cfg, now) if cfg["jira"]["enabled"] else None


def collect(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    m: "views.Mirror | None" = None,
) -> list[Item]:
    """Every item the mirror holds, open or done, joined; no Now or Stale.
    `m` is mirror()'s, passed in when the caller needs it too."""
    m = m if m is not None else mirror(conn, cfg, now)
    items = (
        _issues(conn, cfg) + _prs(conn, cfg) + _tickets(m) + _designs(conn, cfg)
        + _sessions(conn, cfg, now) + _worktrees(conn)
    )  # fmt: skip
    ws = workstreams.memberships(conn)
    for i in items:
        i["workstreams"] = ws.get((i["source"], i["key"]), [])
    _link_prs(items)
    _activity(items)
    ov = overlay.load(conn)
    last_why = _last_why_not(conn)
    minutes = cfg["size"]["minutes"]
    for i in items:
        i["age_d"] = _days(i["created"], now)
        i["idle_d"] = _days(i["updated"], now)
        i["overlay"] = _overlay(ov, last_why, i, now)
        i["size"] = _size(i, minutes)
    items.sort(
        key=lambda i: (
            score.parse_ts(i["updated"]) is None,
            -(score.parse_ts(i["updated"]) or now).timestamp(),
            i["source"], i["key"],
        )
    )  # fmt: skip
    return items


# --- the documents -----------------------------------------------------------


def _csv(value: "str | None", allowed: Iterable[str], what: str) -> "list[str] | None":
    if value is None:
        return None
    names = [v.strip() for v in value.split(",") if v.strip()]
    allowed = tuple(allowed)
    bad = [n for n in names if n not in allowed]
    if bad or not names:
        raise QueryError(
            f"{what} takes a comma list of {', '.join(allowed)};"
            f" not {', '.join(bad) or repr(value)}"
        )
    return names


def fields_of(value: "str | None") -> "tuple[list[str], bool]":
    """`--fields`: brief (the default), full, or a comma list of exact field
    names; identity is always included. Also whether the record is sparse:
    brief and full leave out a field that is null or empty, and a `url` that
    repeats the `key`, since an agent pays for every one; a named list prints
    every field it names."""
    if value in (None, "brief"):
        return list(BRIEF), True
    if value == "full":
        return list(FULL), True
    names = _csv(value, FULL, "--fields") or []
    return list(IDENTITY) + [n for n in names if n not in IDENTITY], False


def scopes_doc(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime,
    query: "str | None" = None,
) -> dict[str, Any]:  # fmt: skip
    items = collect(conn, cfg, now)
    every = _scopes(conn, items)
    doc: dict[str, Any] = {"schema": SCHEMA_VERSION, "query": query}
    if query is None or query == ALL:
        found = every
    else:
        found = match(every, query)
    doc["matched"] = [{**s.as_dict(), "open": _count(s, items)} for s in found]
    if query is not None and query != ALL:
        doc["related"] = related(conn, found, items)
    return doc


def items_doc(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    *,
    scope: "list[str] | None" = None,
    kind: "str | None" = None,
    state: "str | None" = None,
    only: "str | None" = None,
    fields: "str | None" = None,
    limit: "int | None" = None,
    offset: int = 0,
) -> dict[str, Any]:
    """The `items` document. Raises QueryError for a bad flag value."""
    kinds = _csv(kind, KINDS, "--kind")
    states = _csv(state, STATES, "--state") or list(OPEN_STATES)
    onlys = _csv(only, ONLY, "--only") or []
    names, sparse = fields_of(fields)
    if limit is not None and limit < 0 or offset < 0:
        raise QueryError("--limit and --offset must be 0 or more")
    m = mirror(conn, cfg, now)
    items = collect(conn, cfg, now, m)
    resolved = resolve(_scopes(conn, items), scope)
    picked = [
        i for i in items
        if i["state"]["category"] in states
        and (kinds is None or i["kind"] in kinds)
        and (resolved is None or any(s.match(i) for s in resolved))
    ]  # fmt: skip
    want_stale = "stale" in onlys or bool(_STALE_FIELDS & set(names))
    stale = _stale(conn, cfg, now, m) if want_stale else {}
    if "blocked" in onlys:
        picked = [i for i in picked if i["_blocked"]]
    if "waiting" in onlys:
        picked = [i for i in picked if i["_waiting"]]
    if "stale" in onlys:
        picked = [i for i in picked if (i["source"], i["key"]) in stale]
    total = len(picked)
    page = picked[offset:] if limit is None else picked[offset : offset + limit]
    terms = _now_terms(conn, cfg, now) if _NOW_FIELDS & set(names) else {}
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": _stamp(now),
        "scope": ALL if resolved is None else [s.as_dict() for s in resolved],
        "filters": {"kind": kinds, "state": states, "only": onlys},
        "total": total,
        "offset": offset,
        "returned": len(page),
        "sparse": sparse,
        "items": [_record(i, names, terms, stale, sparse) for i in page],
    }


def _record(
    item: Item, names: list[str], terms: dict, stale: dict, sparse: bool
) -> Item:
    it = (item["source"], item["key"])
    placed = terms.get(it)
    st = stale.get(it)
    derived = {
        "now": {k: v for k, v in placed.items() if not k.startswith("_")}
        if placed else None,
        "evidence": (st or {}).get("evidence") or (placed or {}).get("_why"),
        "risks": (st or {}).get("risks") or [],
        "action": (st or {}).get("action") or (placed or {}).get("_action"),
    }  # fmt: skip
    out = {n: derived[n] if n in derived else item.get(n) for n in names}
    if not sparse:
        return out
    if out.get("url") == item["key"]:
        del out["url"]
    return {
        k: v for k, v in out.items() if k in IDENTITY or v not in (None, [], {}, "")
    }


def dumps(doc: dict[str, Any]) -> str:
    """One compact line per item: an agent pays for every space."""
    items = doc.get("items")
    if items is None:
        return json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"
    head = {k: v for k, v in doc.items() if k != "items"}
    body = ",\n".join(
        json.dumps(i, separators=(",", ":"), ensure_ascii=False) for i in items
    )
    text = json.dumps(head, separators=(",", ":"), ensure_ascii=False)[:-1]
    text += "," if head else ""
    return text + '"items":[\n' + body + ("\n" if body else "") + "]}\n"


def jsonl(doc: dict[str, Any]) -> str:
    """The header (everything but the items) on line one, then one item a line."""
    head = {k: v for k, v in doc.items() if k != "items"}
    lines = [json.dumps(head, separators=(",", ":"), ensure_ascii=False)]
    lines += [
        json.dumps(i, separators=(",", ":"), ensure_ascii=False) for i in doc["items"]
    ]
    return "\n".join(lines) + "\n"
