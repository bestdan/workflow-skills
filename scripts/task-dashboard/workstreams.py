"""The fleet instance's Workstreams tab (`/workstreams`): work grouped by theme.

A workstream is one of three things, each a row of the `workstreams` table:

- a GitHub MILESTONE: an open milestone in a repo of the `github.orgs`
  owners, with its open and closed issue counts, its `approved: yes` line
  (github_fleet.milestone_approved) and its due date;
- a LINEAR PROJECT whose status is not completed or canceled (linear_api.py),
  with its status, progress, target date and URL. Only with
  `[linear] enabled = true` and a key; otherwise the step records why in its
  counts (`linear`: `off`, `no key`, ...) and the tab shows the others;
- a DESIGN DOC at `status: proposed` in `github.design_repos`, the rows
  github_fleet's design fetch already mirrors.

MEMBERSHIP links items already in the mirror (source `github`: open issues,
open PRs, and my recently closed PRs) to workstreams, as rows of
`workstream_members`, with `via` saying why:

- an issue in a milestone (`milestone`);
- a PR whose closing issue is in a milestone (`closes`);
- a PR or issue whose title or branch carries a Linear key whose issue is in
  a project (`linear:<KEY>`), looked up in one batched query per sync;
- an issue or PR a design doc names (`design`), by URL or `#N`.

The `workstreams` sync step (run_step) builds both tables and replaces them in
one transaction, so a render reads rows and recomputes nothing. A failed
Linear read keeps the last good Linear rows; a failed GitHub read fails the
step and keeps everything.

LANES, first match wins, after which an In progress or Planned workstream
whose members (or, with none, the workstream itself) show no activity for
`workstreams.stalled_days` days (default 14) moves to Stalled:

- In progress: a started Linear project; a milestone with an open PR or a
  started issue (`status:3_started` or `status:4_needs_review`).
- Planned: a planned Linear project; an approved milestone.
- Design: a design doc; a backlog or triage Linear project; an unapproved
  milestone.
- Stalled: also a paused Linear project, whatever its activity.

A card links to Pull requests and Issues filtered to its members
(`?ws=<id>`, fleet_tabs.py), and `dashboard.py view workstreams --json`
(agent_view.py) lists the same lanes with the members.
"""

import html
import json
import sqlite3
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote

import fleet_views
import github_fleet
import github_gh
import include
import linear_api
import score
import view_model
import views  # card_title and _lane, read at call time: views imports this

_e = html.escape

RUN_KIND = "workstreams"
PARAM = "ws"
KIND = "workstream"
ITEM_SOURCE = github_fleet.SOURCE

LANES = (
    ("in-progress", "In progress", "No workstream is in progress."),
    ("planned", "Planned", "No workstream is planned."),
    ("design", "Design", "No workstream is at the design stage."),
    ("stalled", "Stalled", "No workstream has stalled."),
)

SOURCE_LABELS = {
    "milestone": "milestone",
    "linear": "Linear project",
    "design": "design doc",
}

# A Linear project status type -> its lane before the stall rule.
LINEAR_LANES = {
    "started": "in-progress",
    "planned": "planned",
    "backlog": "design",
    "triage": "design",
    "paused": "stalled",
}

STARTED_LABELS = frozenset({"status:3_started", "status:4_needs_review"})

_TS = "%Y-%m-%dT%H:%M:%SZ"


def ms_id(repo: str, number: int) -> str:
    return f"ms:{repo}#{number}"


def linear_id(project_id: str) -> str:
    return f"linear:{project_id}"


def design_id(repo: str, path: str) -> str:
    return f"design:{repo}/{path}"


def enabled(cfg: dict[str, Any]) -> bool:
    """The step runs with the fleet's GitHub step: it reads that step's rows."""
    gh = cfg["github"]
    return bool(gh["enabled"] and gh["fleet"])


# --- the mirror's items ------------------------------------------------------


def _loads(text: "str | None") -> dict[str, Any]:
    return fleet_views._raw(text)


def mirror_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every github item a workstream can hold, one per URL.

    Each is {key, kind (`issue` | `pr`), repo, number, title, head_ref,
    labels, milestone, closes, is_open, started, waiting, updated}. `waiting`
    is a PR in github_fleet's awaiting set that is not someone else's: one
    that waits on the owner. An open PR wins over a `closed_pr` row for the
    same URL (an index lag).
    """
    items: dict[str, dict[str, Any]] = {}

    def put(key: str, **fields: Any) -> dict[str, Any]:
        item = items.setdefault(
            key,
            {
                "key": key, "kind": "pr", "repo": None, "number": None,
                "title": "", "head_ref": None, "labels": [], "milestone": None,
                "closes": [], "is_open": True, "started": False,
                "waiting": False, "updated": None,
            },
        )  # fmt: skip
        for name, value in fields.items():
            if value is not None:
                item[name] = value
        return item

    for (raw,) in conn.execute(
        "SELECT raw_json FROM issues WHERE source = ? AND kind = 'issue'",
        (ITEM_SOURCE,),
    ):
        r = _loads(raw)
        labels = [str(x) for x in r.get("labels") or []]
        ms = r.get("milestone") or {}
        put(
            str(r.get("url") or r.get("key")),
            kind="issue", repo=r.get("repo"), number=r.get("number"),
            title=r.get("title") or "", labels=labels,
            milestone=ms.get("number"), started=bool(STARTED_LABELS & set(labels)),
            updated=r.get("updated") or r.get("created"),
        )  # fmt: skip
    for url, repo, number, title, head_ref, updated in conn.execute(
        "SELECT url, repo, number, title, head_ref, updated FROM prs"
    ):
        put(
            url, repo=repo, number=number, title=title or "",
            head_ref=head_ref, updated=updated,
        )  # fmt: skip
    for (raw,) in conn.execute(
        "SELECT raw_json FROM issues WHERE source = ? AND kind = 'pr'",
        (ITEM_SOURCE,),
    ):
        r = _loads(raw)
        put(
            str(r.get("url") or r.get("key")),
            repo=r.get("repo"), number=r.get("number"), title=r.get("title"),
            head_ref=r.get("head_ref"), closes=r.get("closes") or [],
            waiting=r.get("role") in ("author", "reviewer"),
            updated=r.get("updated") or r.get("created"),
        )  # fmt: skip
    for (raw,) in conn.execute(
        "SELECT raw_json FROM issues WHERE source = ? AND kind = 'closed_pr'",
        (ITEM_SOURCE,),
    ):
        r = _loads(raw)
        url = str(r.get("url") or r.get("key"))
        if url in items:
            continue
        put(
            url, repo=r.get("repo"), number=r.get("number"), title=r.get("title"),
            head_ref=r.get("head_ref"), is_open=False,
            updated=r.get("closed_at") or r.get("updated"),
        )  # fmt: skip
    return list(items.values())


def mirrored_designs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        _loads(raw)
        for (raw,) in conn.execute(
            "SELECT raw_json FROM issues WHERE source = ? AND kind = 'design'"
            " ORDER BY key",
            (ITEM_SOURCE,),
        )
    ]


# --- build -------------------------------------------------------------------


def _member(ws: str, item: dict[str, Any], via: str) -> dict[str, Any]:
    return {
        "ws_id": ws,
        "item_source": ITEM_SOURCE,
        "item_key": item["key"],
        "kind": item["kind"],
        "via": via,
        "is_open": bool(item["is_open"]),
        "started": bool(item["started"]),
        "waiting": bool(item["waiting"]),
        "updated": item["updated"],
    }


def build(
    milestones: list[dict],
    designs: list[dict],
    projects: list[dict],
    linear_keys: dict[str, str],
    items: list[dict],
) -> "tuple[list[dict], list[dict]]":
    """(workstreams, members) from the three sources and the mirror's items.

    `linear_keys` maps a Linear key to the project its issue is in
    (linear_api.project_of_keys). Each workstream is a dict with `id`,
    `source`, `title`, `status`, `url`, `updated` and its source's facts.
    """
    out: list[dict] = []
    for m in milestones:
        total = int(m["open"]) + int(m["closed"])
        out.append(
            {
                "id": ms_id(m["repo"], m["number"]),
                "source": "milestone",
                "title": m["title"],
                "status": "approved" if m["approved"] else "not approved",
                "url": m.get("url"),
                "repo": m["repo"],
                "number": m["number"],
                "approved": bool(m["approved"]),
                "due": m.get("due"),
                "done": int(m["closed"]),
                "total": total,
                "updated": m.get("updated"),
            }
        )
    for p in projects:
        out.append(
            {
                "id": linear_id(p["id"]),
                "source": "linear",
                "title": p["name"],
                "status": p["status"],
                "status_type": p["status_type"],
                "url": p.get("url"),
                "progress": p["progress"],
                "due": p.get("target"),
                "teams": list(p.get("teams") or []),
                "updated": p.get("updated"),
            }
        )
    for d in designs:
        out.append(
            {
                "id": design_id(d["repo"], d.get("path") or d["key"]),
                "source": "design",
                "title": d.get("title") or d.get("path") or d["key"],
                "status": d.get("status") or "proposed",
                "url": d.get("url"),
                "repo": d["repo"],
                "path": d.get("path"),
                "refs": list(d.get("refs") or []),
                "updated": d.get("created"),
            }
        )

    members: dict[tuple[str, str], dict] = {}

    def add(ws: str, item: dict, via: str) -> None:
        members.setdefault((ws, item["key"]), _member(ws, item, via))

    ms_ids = {
        (m["repo"], m["number"]): ms_id(m["repo"], m["number"]) for m in milestones
    }
    project_ids = {p["id"] for p in projects}
    teams = {t for p in projects for t in p.get("teams") or []}
    by_ref = {(i["repo"], i["number"]): i for i in items if i["repo"]}
    for item in items:
        if item["kind"] == "issue" and item["milestone"] is not None:
            target = ms_ids.get((item["repo"], item["milestone"]))
            if target:
                add(target, item, "milestone")
        for c in item["closes"]:
            target = ms_ids.get((c.get("repo"), c.get("milestone")))
            if target:
                add(target, item, f"closes #{c.get('number')}")
        text = f"{item['title']} {item['head_ref'] or ''}"
        for key in linear_api.keys_in(text, teams):
            project = linear_keys.get(key)
            if project is not None and project in project_ids:
                add(linear_id(project), item, f"linear:{key}")
    for doc in out:
        if doc["source"] != "design":
            continue
        for ref in doc["refs"]:
            found = by_ref.get((ref.get("repo"), ref.get("number")))
            if found:
                add(doc["id"], found, "design")
    return out, list(members.values())


def candidate_keys(items: list[dict], projects: list[dict]) -> list[str]:
    """The Linear keys the items carry whose team owns an open project."""
    teams = {t for p in projects for t in p.get("teams") or []}
    keys: dict[str, None] = {}
    for item in items:
        for key in linear_api.keys_in(
            f"{item['title']} {item['head_ref'] or ''}", teams
        ):
            keys.setdefault(key, None)
    return list(keys)


# --- the sync step -----------------------------------------------------------


def _previous_linear(conn: sqlite3.Connection) -> "tuple[list[dict], list[dict]]":
    """The Linear workstreams and members the last good step wrote."""
    rows = [
        _loads(raw)
        for (raw,) in conn.execute(
            "SELECT raw_json FROM workstreams WHERE source = 'linear'"
        )
    ]
    ids = {r.get("id") for r in rows}
    cols = (
        "ws_id", "item_source", "item_key", "kind", "via", "is_open",
        "started", "waiting", "updated",
    )  # fmt: skip
    members = [
        dict(zip(cols, r))
        for r in conn.execute(f"SELECT {', '.join(cols)} FROM workstream_members")
        if r[0] in ids
    ]
    return rows, members


def fetch(
    cfg: dict[str, Any],
    conn: sqlite3.Connection,
    gh_runner: github_gh.Runner = github_gh.run,
    linear_runner: linear_api.Runner = linear_api.post,
    resolve_key: Callable[[], str] = linear_api.resolve_key,
) -> dict[str, Any]:
    """Everything write() needs. Reads GitHub, Linear and the mirror; writes
    nothing. A Linear failure is reported in the plan, not raised."""
    milestones = github_fleet.open_milestones(cfg["github"]["orgs"], gh_runner)
    designs = mirrored_designs(conn)
    items = mirror_items(conn)
    projects: list[dict] = []
    keys: dict[str, str] = {}
    calls = 0
    linear, error = "off", None
    carried: "tuple[list[dict], list[dict]]" = ([], [])
    if cfg["linear"]["enabled"]:
        try:
            key = resolve_key()
        except linear_api.NoKey as exc:
            linear = (
                "no key"
                if exc.category == "unconfigured"
                else f"no key ({exc.category})"
            )
            error = None if exc.category == "unconfigured" else str(exc)
        else:
            timeout = float(cfg["linear"]["timeout_seconds"])

            def counted(body: dict, k: str, t: float) -> Any:
                nonlocal calls
                calls += 1
                return linear_runner(body, k, t)

            try:
                projects = linear_api.projects(key, counted, timeout)
                keys = linear_api.project_of_keys(
                    candidate_keys(items, projects), key, counted, timeout
                )
                linear = "ok"
            except linear_api.LinearError as exc:
                linear = (
                    "auth failed" if isinstance(exc, linear_api.AuthError) else "failed"
                )
                error = str(exc)
                projects, keys = [], {}
                carried = _previous_linear(conn)
    rows, members = build(milestones, designs, projects, keys, items)
    rows += carried[0]
    members += carried[1]
    return {
        "rows": rows,
        "members": members,
        "linear": linear,
        "linear_error": error,
        "linear_calls": calls,
        "counts": {
            "workstreams": len(rows),
            "members": len(members),
            "milestones": len(milestones),
            "linear_projects": sum(1 for r in rows if r["source"] == "linear"),
            "designs": len(designs),
            "linear_calls": calls,
        },
    }


def write(
    conn: sqlite3.Connection, rows: list[dict], members: list[dict], now: datetime
) -> None:
    """Replace both tables with `rows` and `members`, in one transaction."""
    stamp = now.astimezone(timezone.utc).strftime(_TS)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM workstream_members")
        conn.execute("DELETE FROM workstreams")
        conn.executemany(
            "INSERT OR REPLACE INTO workstreams (id, source, title, status, url,"
            " updated, raw_json, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["id"], r["source"], r["title"], r["status"], r.get("url"),
                    r.get("updated"), json.dumps(r, sort_keys=True), stamp,
                )
                for r in rows
            ],
        )  # fmt: skip
        conn.executemany(
            "INSERT OR REPLACE INTO workstream_members (ws_id, item_source,"
            " item_key, kind, via, is_open, started, waiting, updated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    m["ws_id"], m["item_source"], m["item_key"], m["kind"],
                    m["via"], int(bool(m["is_open"])), int(bool(m["started"])),
                    int(bool(m["waiting"])), m.get("updated"),
                )
                for m in members
            ],
        )  # fmt: skip
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def run_step(
    cfg: dict[str, Any],
    conn: sqlite3.Connection,
    now: datetime,
    finish: Callable[..., None],
    start: Callable[[sqlite3.Connection, str, datetime], int],
    *,
    gh_runner: github_gh.Runner = github_gh.run,
    linear_runner: linear_api.Runner = linear_api.post,
    resolve_key: Callable[[], str] = linear_api.resolve_key,
    clock: Callable[[], datetime],
) -> int:
    """Fetch and write the workstreams, recording one `sync_runs` row (kind
    RUN_KIND) through sync's `start` and `finish`; returns its id.

    `counts.linear` says what happened to Linear: `off`, `no key`, `no key
    (<category>)`, `ok`, `failed` or `auth failed`; a Linear failure is the
    run's `error` and leaves the run ok. A GitHub AuthError is
    `auth_failed`; anything else that raises is `failed`, and either keeps
    the last good rows.
    """
    run_id = start(conn, RUN_KIND, now)
    counts: dict[str, Any] = {}
    outcome, error = "ok", None
    try:
        plan = fetch(cfg, conn, gh_runner, linear_runner, resolve_key)
        counts = {**plan["counts"], "linear": plan["linear"]}
        error = plan["linear_error"]
        write(conn, plan["rows"], plan["members"], now)
    except github_gh.AuthError as exc:
        outcome, error = "auth_failed", f"{exc} (fix: run `gh auth login`)"
    except Exception as exc:  # recorded, not raised: the next run retries
        outcome = "failed"
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    except BaseException as exc:
        if conn.in_transaction:
            conn.rollback()
        finish(
            conn, run_id, "failed", clock(), counts, [],
            f"interrupted ({type(exc).__name__})",
        )  # fmt: skip
        raise
    if conn.in_transaction:
        conn.rollback()
    finish(conn, run_id, outcome, clock(), counts, [], error)
    return run_id


# --- reading -----------------------------------------------------------------


def last_run(conn: sqlite3.Connection) -> "dict[str, Any] | None":
    """The newest finished `workstreams` run: {outcome, counts, error}."""
    row = conn.execute(
        "SELECT outcome, counts, error FROM sync_runs WHERE kind = ?"
        " AND outcome IS NOT NULL ORDER BY id DESC LIMIT 1",
        (RUN_KIND,),
    ).fetchone()
    if row is None:
        return None
    return {"outcome": row[0], "counts": _loads(row[1]), "error": row[2]}


def synced(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sync_runs WHERE kind = ? AND outcome = 'ok' LIMIT 1",
            (RUN_KIND,),
        ).fetchone()
        is not None
    )


def members_of(conn: sqlite3.Connection, ws: str) -> "set[tuple[str, str]] | None":
    """The (source, key) of each member of workstream `ws`; None when there is
    no such workstream."""
    if conn.execute("SELECT 1 FROM workstreams WHERE id = ?", (ws,)).fetchone() is None:
        return None
    return {
        (s, k)
        for s, k in conn.execute(
            "SELECT item_source, item_key FROM workstream_members WHERE ws_id = ?",
            (ws,),
        )
    }


def title_of(conn: sqlite3.Connection, ws: str) -> "str | None":
    row = conn.execute("SELECT title FROM workstreams WHERE id = ?", (ws,)).fetchone()
    return row[0] if row else None


def membership(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """(source, key) -> the title of one workstream the item is in (the first
    by id), for Now's features (whynot.features)."""
    out: dict[tuple[str, str], str] = {}
    for s, k, title in conn.execute(
        "SELECT m.item_source, m.item_key, w.title FROM workstream_members m"
        " JOIN workstreams w ON w.id = m.ws_id ORDER BY m.ws_id"
    ):
        out.setdefault((s, k), title or "")
    return out


# --- cards -------------------------------------------------------------------


@dataclass
class Card:
    """One workstream as its card and its JSON show it."""

    ws: dict[str, Any]
    lane: str
    members: list[dict[str, Any]] = field(default_factory=list)
    done: "int | None" = None
    total: "int | None" = None
    percent: "int | None" = None
    open_prs: int = 0
    waiting: int = 0
    last_activity: "datetime | None" = None
    next: str = ""

    @property
    def id(self) -> str:
        return str(self.ws["id"])

    @property
    def prs(self) -> int:
        return sum(1 for m in self.members if m["kind"] == "pr")

    @property
    def issues(self) -> int:
        return sum(1 for m in self.members if m["kind"] == "issue")


def _base_lane(ws: dict[str, Any], members: list[dict[str, Any]]) -> str:
    if ws["source"] == "linear":
        return LINEAR_LANES.get(str(ws.get("status_type") or ""), "design")
    if ws["source"] == "design":
        return "design"
    started = any(
        (m["kind"] == "pr" and m["is_open"]) or (m["kind"] == "issue" and m["started"])
        for m in members
    )
    if started:
        return "in-progress"
    return "planned" if ws.get("approved") else "design"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def card(
    ws: dict[str, Any], members: list[dict[str, Any]], now: datetime, stalled_days: int
) -> Card:
    """The workstream's lane, progress, counts and next line."""
    lane = _base_lane(ws, members)
    stamps = [score.parse_ts(m.get("updated")) for m in members]
    seen = [t for t in stamps if t is not None]
    last = max(seen) if seen else score.parse_ts(ws.get("updated"))
    idle = (now - last).days if last is not None else None
    if lane in ("in-progress", "planned") and last is not None:
        if now - last >= timedelta(days=stalled_days):
            lane = "stalled"
    c = Card(ws, lane, members, last_activity=last)
    c.open_prs = sum(1 for m in members if m["kind"] == "pr" and m["is_open"])
    c.waiting = sum(1 for m in members if m["waiting"] and m["is_open"])
    if ws["source"] == "milestone":
        c.done, c.total = ws.get("done"), ws.get("total")
    elif ws["source"] == "linear":
        c.percent = round(float(ws.get("progress") or 0) * 100)
    else:
        c.total = len(members)
        c.done = sum(1 for m in members if not m["is_open"])
    if c.waiting:
        c.next = f"{_plural(c.waiting, 'PR')} waiting on you"
    elif lane == "stalled" and ws.get("status_type") == "paused":
        c.next = "the project is paused: resume or close it"
    elif lane == "stalled":
        c.next = f"no activity for {idle}d"
    elif ws["source"] == "milestone" and not ws.get("approved"):
        c.next = "approve the milestone: add `approved: yes` to its description"
    elif ws["source"] == "design":
        c.next = "decide the design"
    elif ws["source"] == "linear" and lane == "design":
        c.next = "plan the project"
    elif lane == "planned":
        c.next = "start it, or leave it to `/do-tasks`"
    elif c.open_prs:
        c.next = f"{_plural(c.open_prs, 'open PR')}, none waiting on you"
    return c


def progress_text(c: Card) -> str:
    if c.percent is not None:
        return f"{c.percent}% done"
    if c.total:
        return f"{c.done}/{c.total} done"
    return "no members yet" if c.ws["source"] == "design" else "0/0 done"


def meta_parts(c: Card, now: datetime) -> list[str]:
    """The card's meta line, as plain-text parts."""
    bits = [c.ws.get("status") or "", progress_text(c)]
    if c.open_prs:
        bits.append(f"{_plural(c.open_prs, 'open PR')} ({c.waiting} waiting on you)")
    if c.ws.get("due"):
        bits.append(f"due {str(c.ws['due'])[:10]}")
    bits.append(f"last activity {fleet_views.ago(c.last_activity, now)}")
    return [b for b in bits if b]


def ref(ws: dict[str, Any]) -> str:
    where = ws.get("repo") or ", ".join(ws.get("teams") or [])
    label = SOURCE_LABELS.get(ws["source"], ws["source"])
    return f"{label} · {where}" if where else label


def filter_href(path: str, ws: str) -> str:
    return f"{path}?{PARAM}={quote(ws, safe='')}"


def _card_html(c: Card, now: datetime) -> str:
    head = views.card_title(str(c.ws.get("url") or ""), c.ws.get("title"), ref(c.ws))
    meta = " · ".join(_e(b) for b in meta_parts(c, now))
    links = (
        f'<a href="{_e(filter_href("/prs", c.id))}">Pull requests ({c.prs})</a> · '
        f'<a href="{_e(filter_href("/issues", c.id))}">Issues ({c.issues})</a>'
    )
    act = f'<div class="act">→ {view_model.code(c.next)}</div>' if c.next else ""
    return (
        f'<div class="card" data-source="{KIND}" data-key="{_e(c.id)}"'
        f' data-ws-source="{_e(c.ws["source"])}">{head}'
        f'<div class="meta">{meta}</div>{act}'
        f'<div class="meta members">{links}</div></div>'
    )


# --- lanes -------------------------------------------------------------------


@dataclass
class Tab:
    lanes: list[view_model.Lane]
    synced: bool
    linear: "str | None"  # the last run's counts.linear
    linear_error: "str | None"


def cards(conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> list[Card]:
    by_ws: dict[str, list[dict[str, Any]]] = {}
    cols = ("ws_id", "item_source", "item_key", "kind", "via", "is_open",
            "started", "waiting", "updated")  # fmt: skip
    for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM workstream_members ORDER BY ws_id, item_key"
    ):
        m = dict(zip(cols, r))
        for flag in ("is_open", "started", "waiting"):
            m[flag] = bool(m[flag])
        by_ws.setdefault(m["ws_id"], []).append(m)
    days = int(cfg["workstreams"]["stalled_days"])
    return [
        card(_loads(raw), by_ws.get(ws_id, []), now, days)
        for ws_id, raw in conn.execute(
            "SELECT id, raw_json FROM workstreams ORDER BY id"
        )
    ]


def _order(c: Card) -> tuple:
    """Most recently active first; in Stalled, the longest idle first."""
    t = c.last_activity.timestamp() if c.last_activity else 0.0
    return (t if c.lane == "stalled" else -t, c.id)


def lanes(conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> Tab:
    """The Workstreams lanes, each item a Card."""
    ok = enabled(cfg) and synced(conn)
    found = cards(conn, cfg, now) if ok else []
    group = include.group_of(KIND)
    by_lane: dict[str, list[Card]] = {name: [] for name, _, _ in LANES}
    for c in found:
        by_lane[c.lane].append(c)
    out = [
        view_model.Lane(name, title, sorted(by_lane[name], key=_order), empty, group)
        for name, title, empty in LANES
    ]
    run = last_run(conn) if ok else None
    counts = (run or {}).get("counts") or {}
    linear = counts.get("linear")
    return Tab(out, ok, linear, (run or {}).get("error") if linear else None)


def _notes(cfg: dict[str, Any], tab: Tab) -> str:
    if not enabled(cfg):
        return '<p class="note">github.fleet is off, so nothing is shown here.</p>'
    if not tab.synced:
        return '<p class="note">No workstreams sync has succeeded yet.</p>'
    linear = tab.linear or ""
    if linear in ("", "ok", "off"):
        return ""
    why = {
        "no key": "no Linear key is set up, so Linear projects are not shown",
        "failed": "the last Linear read failed; its projects are as last read",
        "auth failed": "Linear refused the key; its projects are as last read",
    }.get(linear, f"Linear: {linear}; Linear projects are not shown")
    return f'<p class="note" data-linear="{_e(linear)}">{_e(why)}.</p>'


def render(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
) -> str:
    inc = inc or include.everything("workstreams")
    tab = lanes(conn, cfg, now)
    parts = [_notes(cfg, tab)]
    for lane in tab.lanes:
        parts.append(
            views._lane(
                lane.id, lane.title, [_card_html(c, now) for c in lane.items],
                lane.empty, lane.group, inc,
            )
        )  # fmt: skip
    return f'<div class="fleet workstreams">{"".join(parts)}</div>'


# --- the ?ws= filter ---------------------------------------------------------


@dataclass(frozen=True)
class Filter:
    """A `?ws=` filter: the workstream and its members' (source, key)."""

    id: str
    title: "str | None"
    members: "frozenset[tuple[str, str]]"
    known: bool

    def keeps(self, item: tuple[str, str]) -> bool:
        return item in self.members


def from_query(
    conn: sqlite3.Connection, query: "dict[str, list[str]] | None"
) -> "Filter | None":
    """The request's `?ws=` filter, or None when it names none. An unknown id
    filters to nothing, and says so."""
    wanted = ((query or {}).get(PARAM) or [""])[-1].strip()
    if not wanted:
        return None
    found = members_of(conn, wanted)
    return Filter(
        wanted, title_of(conn, wanted), frozenset(found or ()), found is not None
    )


def filter_note(f: "Filter | None", path: str) -> str:
    """The line above a filtered Pull requests or Issues page."""
    if f is None:
        return ""
    clear = f'<a href="{_e(path)}">show all</a>'
    if not f.known:
        return (
            f'<p class="note" data-ws="{_e(f.id)}">No workstream {_e(f.id)};'
            f" nothing matches it. {clear}</p>"
        )
    return (
        f'<p class="note" data-ws="{_e(f.id)}">Only the members of'
        f' <a href="/workstreams">{_e(f.title or f.id)}</a>. {clear}</p>'
    )
