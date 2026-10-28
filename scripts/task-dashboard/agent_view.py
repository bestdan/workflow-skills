"""`dashboard.py view <name>`: a view's lanes and items, for an agent to read.

It reads the DB directly and needs no running server. Each lane comes from
the same builder its page renders (view_model.Lane), so the JSON and the page
cannot disagree about which items a lane holds or how many: the include
filter, ignores and snoozes apply exactly as there, and nothing is ranked or
filtered here.

`show_all` adds what the page hides, each item marked with why: `ignored`,
`snoozed`, `filtered` (its include group is off) or `limit` (past Now's
list length). A hidden item never counts toward its lane's `count`. Jira
lanes add only what the include filter hides; an ignored Jira issue is in the
document's `ignored` list, as on every page's Ignored footer.

Every field is plain text. An item's `action` is structured: `review` (what to
do by hand, a command in backticks), `command` (argv), and `url`, or null when
the page suggests none. A Needs you, Finished or Now item also carries
`bridge_session_id`: a hook session's Remote Control id, null for an ended
session and for anything else; when it is set, the action's `url` is that
session's page. It carries `branch` too, and for a hook session
`first_prompt` and `last_ask` (its transcript summary, needs.py) and `pr`,
the open PR on its branch as {number, url, repo}; each is null when there is
none. A Now item adds `tier` (its tier in the mode that ranked it:
fleet_now.TIERS, `the rest` or `blocked`, LITTLE_TIERS, or `deep` or
`tidy`) and `checklist`, a close-out's steps with evidence as [{step, done}],
empty for anything else.

A Now document's `notes` carry `mode`, the mode it was built in (the
`--mode` asked for, else the saved one, else ship); outside Ship,
`more_in_mode`; and in Little `closed_today` (null when the fleet step has no
count from today).
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

import fleet_now
import fleet_tabs
import fleet_views
import github_fleet
import include
import needs
import overlay
import score
import server
import view_model
import views
import workstreams

SCHEMA_VERSION = 1

# View name -> its nav path. Health is a panel of figures, not of items.
PATHS = {
    "now": "/now",
    "needs": "/needs",
    "prs": "/prs",
    "issues": "/issues",
    "workstreams": "/workstreams",
    "stale": "/stale",
    "board": "/board",
    "waiting-on-me": "/waiting-on-me",
    "waiting-on": "/waiting-on",
    "epics": "/epics",
}

Item = dict[str, Any]


class ViewError(ValueError):
    """A view this instance does not show."""


def available(cfg: dict[str, Any]) -> list[str]:
    """The views this instance's nav names, in PATHS order."""
    hidden = views.hidden_tabs(cfg)
    return [v for v, path in PATHS.items() if path not in hidden]


def _stamp(dt: "datetime | None") -> "str | None":
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _item(
    item: overlay.Item,
    kind: str,
    lane: str,
    title: str,
    *,
    ref: "str | None" = None,
    url: "str | None" = None,
    host: "str | None" = None,
    path: "str | None" = None,
    at: "datetime | None" = None,
    evidence: str = "",
    why: str = "",
    risks: "list[str] | None" = None,
    action: "view_model.Action | None" = None,
    **extra: Any,
) -> Item:
    return {
        "source": item[0],
        "key": item[1],
        "kind": kind,
        "lane": lane,
        "title": title or ref or item[1],
        "ref": ref,
        "url": url,
        "host": host,
        "path": path,
        "at": _stamp(at),
        "evidence": evidence,
        "why": why,
        "risks": list(risks or []),
        "action": action.as_dict() if action else None,
        "hidden": None,
        **extra,
    }


# --- item converters, one per builder's item type ----------------------------


def stale_item(i: fleet_views.StaleItem, lane: str) -> Item:
    return _item(
        i.item, i.kind, lane, i.title,
        ref=i.ref, url=i.url, host=i.host, path=i.path, at=i.at,
        evidence=i.evidence or i.why, why=i.why, risks=i.risks, action=i.action,
        meta=i.meta, branch=i.branch,
    )  # fmt: skip


def need_item(n: needs.Need, lane: str, now: datetime) -> Item:
    why = " · ".join([n.state] + ([n.where] if n.where else []))
    return _item(
        n.item, n.kind, lane, n.title,
        ref=n.ref, url=n.url, host=n.host, path=n.path, at=n.since,
        evidence=n.state, why=why, action=n.action,
        waited=needs.waited(n.since, now), brief=n.brief,
        bridge_session_id=n.bridge_session_id,
        first_prompt=n.first_prompt, last_ask=n.last_ask, branch=n.branch,
        pr=dict(n.pr) if n.pr else None,
    )  # fmt: skip


def ranked_item(r: fleet_now.Ranked, lane: str, now: datetime) -> Item:
    out = need_item(r.need, lane, now)
    out.update(
        evidence=r.why,
        why=fleet_now.why_line(r),
        category=r.category,
        tier=r.tier[1],
        checklist=[{"step": s.name, "done": s.done} for s in r.checklist],
        score=r.total,
        terms=[[name, value, why] for name, value, why in r.terms if value],
    )
    return out


def pr_item(p: dict, why: str, lane: str, now: datetime) -> Item:
    return _item(
        (github_fleet.SOURCE, str(p["url"])), "pr", lane, p.get("title") or "",
        ref=f"{p.get('repo') or ''}#{p.get('number')}", url=p.get("url"),
        at=score.parse_ts(p.get("created")),
        evidence=why, why=" · ".join(fleet_tabs.pr_meta(p, why, now)),
    )  # fmt: skip


def issue_item(row: dict, lane: str, now: datetime) -> Item:
    return _item(
        (github_fleet.SOURCE, str(row.get("key") or row.get("url"))), "issue", lane,
        row.get("title") or "",
        ref=f"{row.get('repo') or ''}#{row.get('number')}", url=row.get("url"),
        at=score.parse_ts(row.get("created")),
        why=" · ".join(fleet_tabs.issue_meta(row, now)),
        labels=list(row.get("labels") or []),
    )  # fmt: skip


def workstream_item(c: workstreams.Card, lane: str, now: datetime) -> Item:
    return _item(
        (workstreams.KIND, c.id), workstreams.KIND, lane, c.ws.get("title") or "",
        ref=workstreams.ref(c.ws), url=c.ws.get("url"), at=c.last_activity,
        evidence=c.next, why=" · ".join(workstreams.meta_parts(c, now)),
        action=view_model.Action(review=c.next) if c.todo else None,
        workstream_source=c.ws["source"], status=c.ws.get("status"),
        progress={"done": c.done, "total": c.total, "percent": c.percent},
        open_prs=c.open_prs, waiting_on_you=c.waiting,
        filters={
            "prs": workstreams.filter_href("/prs", c.id),
            "issues": workstreams.filter_href("/issues", c.id),
        },
        designs=[
            {"name": d["name"], "url": d.get("url")} for d in c.ws.get("designs") or []
        ],
        members=[
            {"source": m["item_source"], "key": m["item_key"], "kind": m["kind"],
             "via": m["via"], "open": m["is_open"]}
            for m in c.members
        ],
    )  # fmt: skip


def _jira_url(cfg: dict[str, Any], key: str) -> str:
    return f"https://{cfg['jira']['site']}/browse/{key}"


def jira_item(m: views.Mirror, issue: dict, why: str, lane: str) -> Item:
    key = issue["key"]
    return _item(
        ("jira", key), "ticket", lane, issue.get("summary") or "",
        ref=key, url=_jira_url(m.cfg, key), evidence=why,
        why=" · ".join(views.issue_meta(issue) + ([why] if why else [])),
        score=m.scores[key][0], pinned=key in m.pins,
    )  # fmt: skip


def jira_pr_item(p: dict, why: str, lane: str) -> Item:
    return _item(
        ("github", str(p["url"])), "pr", lane, p.get("title") or "",
        ref=f"{p.get('repo') or ''}#{p.get('number')}", url=p.get("url"),
        evidence=why, why=why, issue_key=p.get("issue_key"),
    )  # fmt: skip


def epic_item(cfg: dict[str, Any], g: dict, lane: str) -> Item:
    blocked = f", {g['blocked']} blocked" if g["blocked"] else ""
    return _item(
        ("jira", g["key"]), "ticket", lane, g.get("summary") or "",
        ref=g["key"], url=_jira_url(cfg, g["key"]),
        evidence=f"{g['done']}/{g['total']} done{blocked}",
        done=g["done"], total=g["total"], blocked=g["blocked"],
    )  # fmt: skip


def board_item(cfg: dict[str, Any], row: dict, lane: str) -> Item:
    key = str(row["id"])
    meta = (row.get("status_name"), row.get("assignee"), row.get("priority_name"))
    return _item(
        ("jira", key), "ticket", lane, row.get("summary") or "",
        ref=key, url=_jira_url(cfg, key),
        why=" · ".join(str(v) for v in meta if v), epic=row.get("epic"),
    )  # fmt: skip


# --- lanes -------------------------------------------------------------------


def _lane(
    lane: view_model.Lane, inc: include.Include, convert: Callable[[Any, str], Item]
) -> dict[str, Any]:
    """A Lane as JSON. An off lane is empty, as its page shows it; its items
    are kept aside (`_off`) for show_all."""
    off = lane.off(inc)
    items = [convert(x, lane.id) for x in lane.items]
    return {
        "id": lane.id,
        "title": lane.title or lane.id.capitalize(),
        "count": 0 if off else len(items),
        "off": off,
        "items": [] if off else items,
        "_off": items if off else [],
    }


def _hidden(
    out: list[dict[str, Any]],
    candidates: list[Item],
    reason: Callable[[Item], str],
) -> None:
    """Add each candidate no lane lists to its lane, marked with `reason`."""
    shown = {
        (i["source"], i["key"]) for lane in out for i in lane["items"] + lane["_off"]
    }
    by_id = {lane["id"]: lane for lane in out}
    for item in candidates:
        if (item["source"], item["key"]) in shown:
            continue
        item["hidden"] = reason(item)
        lane = by_id.get(item["lane"])
        if lane is None:
            title = item["lane"].capitalize()
            lane = by_id[item["lane"]] = {
                "id": item["lane"], "title": title, "count": 0, "off": False,
                "items": [], "_off": [],
            }  # fmt: skip
            out.append(lane)
        lane["items"].append(item)


def _reason(ov: overlay.Overlay, inc: include.Include, now: datetime) -> Any:
    def why(item: Item) -> str:
        it = (item["source"], item["key"])
        if it in ov.ignored:
            return "ignored"
        if not inc.shows(item["kind"]):
            return "filtered"
        if ov.is_snoozed(it, now):
            return "snoozed"
        return "limit"

    return why


def _stale(ctx: "Ctx") -> list[dict[str, Any]]:
    out = []
    m = ctx.mirror()
    lane = views.jira_stale_lane(m)
    if lane is not None:
        out.append(_lane(lane, ctx.inc, lambda t, lid: jira_item(m, t[0], t[1], lid)))
    if not fleet_views.enabled(ctx.cfg):
        return out
    off = fleet_views.stale_off(ctx.inc)
    for lane in fleet_views.stale_lanes(ctx.conn, ctx.cfg, ctx.now, ctx.inc):
        out.append(_lane(lane, ctx.inc, stale_item))
        out[-1]["off"] = off
    if ctx.show_all:
        every = fleet_views.stale_items(fleet_views.load(ctx.conn, ctx.cfg), ctx.now)
        _hidden(out, [stale_item(i, i.lane) for i in every], ctx.reason)
    return out


def _needs(ctx: "Ctx") -> list[dict[str, Any]]:
    ov = ctx.ov
    page = needs.build(ctx.conn, ctx.cfg, ctx.now, ov, ctx.inc)
    out = [
        _lane(lane, ctx.inc, lambda n, lid: need_item(n, lid, ctx.now))
        for lane in needs.lanes(page, ctx.inc)
    ]
    ctx.notes["snoozed"] = page.snoozed
    if ctx.show_all:
        every = needs.collect(ctx.conn, ctx.cfg, ctx.now)
        done = needs.finished(
            ctx.conn, ctx.cfg, needs.since_looked(ov, ctx.now), ctx.now
        )
        cands = [
            need_item(n, "approvals" if n.approval else "needs", ctx.now) for n in every
        ]
        cands += [need_item(n, "finished", ctx.now) for n in done]
        _hidden(out, cands, ctx.reason)
    return out


def _now(ctx: "Ctx") -> list[dict[str, Any]]:
    mode = fleet_now.resolve_mode(ctx.mode, ctx.ov)
    page = fleet_now.build(ctx.conn, ctx.cfg, ctx.now, ctx.ov, ctx.inc, mode)
    out = [
        _lane(lane, ctx.inc, lambda r, lid: ranked_item(r, lid, ctx.now))
        for lane in fleet_now.lanes(page, ctx.inc)
    ]
    ctx.notes.update(mode=mode, snoozed=page.snoozed, more_on_needs=page.more_on_needs)
    if mode != fleet_now.SHIP:
        ctx.notes["more_in_mode"] = page.more_in_mode
    if mode == fleet_now.LITTLE:
        ctx.notes["closed_today"] = page.closed_today
    if ctx.show_all:
        every = fleet_now.mode_candidates(ctx.conn, ctx.cfg, ctx.now, mode)
        _hidden(out, [ranked_item(r, "next", ctx.now) for r in every], ctx.reason)
    return out


def _tab(ctx: "Ctx", tab: fleet_tabs.Tab, convert: Any, every: list[Item]) -> Any:
    out = [_lane(lane, ctx.inc, convert) for lane in tab.lanes]
    ctx.notes["snoozed"] = tab.snoozed
    ctx.notes["fleet_synced"] = tab.synced
    if ctx.show_all:
        _hidden(out, every, ctx.reason)
    return out


def _prs(ctx: "Ctx") -> list[dict[str, Any]]:
    tab = fleet_tabs.pr_lanes(ctx.conn, ctx.cfg, ctx.now)
    every = []
    if ctx.show_all:
        for p in fleet_tabs.pull_requests(ctx.conn, ctx.cfg):
            lane, why = fleet_tabs.pr_lane(p)
            every.append(pr_item(p, why, lane, ctx.now))
    return _tab(ctx, tab, lambda t, lid: pr_item(t[0], t[1], lid, ctx.now), every)


def _issues(ctx: "Ctx") -> list[dict[str, Any]]:
    tab = fleet_tabs.issue_lanes(ctx.conn, ctx.cfg, ctx.now)
    every = []
    if ctx.show_all and tab.synced:
        for row in needs._rows(ctx.conn, github_fleet.SOURCE, "issue"):
            lane = fleet_tabs.issue_lane(row.get("labels") or [])
            every.append(issue_item(row, lane, ctx.now))
    return _tab(ctx, tab, lambda row, lid: issue_item(row, lid, ctx.now), every)


def _workstreams(ctx: "Ctx") -> list[dict[str, Any]]:
    tab = workstreams.lanes(ctx.conn, ctx.cfg, ctx.now)
    ctx.notes["workstreams_synced"] = tab.synced
    ctx.notes["linear"] = tab.linear
    return [
        _lane(lane, ctx.inc, lambda c, lid: workstream_item(c, lid, ctx.now))
        for lane in tab.lanes
    ]


def _jira_lanes(ctx: "Ctx", lanes: list[view_model.Lane]) -> list[dict[str, Any]]:
    m = ctx.mirror()

    def convert(lane: view_model.Lane) -> Callable[[Any, str], Item]:
        if lane.group == views.PRS:
            return lambda t, lid: jira_pr_item(t[0], t[1], lid)
        return lambda t, lid: jira_item(m, t[0], t[1], lid)

    return [_lane(lane, ctx.inc, convert(lane)) for lane in lanes]


def _waiting_on_me(ctx: "Ctx") -> list[dict[str, Any]]:
    return _jira_lanes(ctx, views.waiting_on_me_lanes(ctx.mirror()))


def _waiting_on(ctx: "Ctx") -> list[dict[str, Any]]:
    return _jira_lanes(ctx, views.waiting_on_lanes(ctx.mirror()))


def _one_off_lane(lane_id: str, title: str, lanes: list[dict[str, Any]]) -> Any:
    """A page whose every row is a ticket draws one off lane, not one per
    lane, when tickets are switched off."""
    items = [i for lane in lanes for i in lane["items"] + lane["_off"]]
    off = {"id": lane_id, "title": title, "count": 0, "off": True}
    return [{**off, "items": [], "_off": items}]


def _epics(ctx: "Ctx") -> list[dict[str, Any]]:
    out = [
        _lane(lane, ctx.inc, lambda g, lid: epic_item(ctx.cfg, g, lid))
        for lane in views.epic_lanes(ctx.mirror())
    ]
    if not ctx.inc.has(views.TICKETS):
        return _one_off_lane("epics", "Epics", out)
    return out


def _board(ctx: "Ctx") -> list[dict[str, Any]]:
    board = server.board_data(ctx.conn, ctx.cfg)
    out = [
        _lane(lane, ctx.inc, lambda r, lid: board_item(ctx.cfg, r, lid))
        for lane in server.board_lanes(board, ctx.ov)
    ]
    if not ctx.inc.shows("ticket"):
        return _one_off_lane("board", "Issues", out)
    return out


BUILDERS: dict[str, Callable[["Ctx"], list[dict[str, Any]]]] = {
    "now": _now,
    "needs": _needs,
    "prs": _prs,
    "issues": _issues,
    "workstreams": _workstreams,
    "stale": _stale,
    "board": _board,
    "waiting-on-me": _waiting_on_me,
    "waiting-on": _waiting_on,
    "epics": _epics,
}


class Ctx:
    def __init__(
        self,
        conn: sqlite3.Connection,
        cfg: dict[str, Any],
        now: datetime,
        inc: include.Include,
        show_all: bool,
        mode: "str | None" = None,
    ) -> None:
        self.conn, self.cfg, self.now, self.inc = conn, cfg, now, inc
        self.show_all = show_all
        self.mode = mode
        self.ov = overlay.load(conn)
        self.reason = _reason(self.ov, inc, now)
        self.notes: dict[str, Any] = {}
        self._mirror: "views.Mirror | None" = None

    def mirror(self) -> views.Mirror:
        if self._mirror is None:
            self._mirror = views.Mirror(self.conn, self.cfg, self.now)
        return self._mirror


# --- the document ------------------------------------------------------------


def freshness(conn: sqlite3.Connection) -> dict[str, Any]:
    """When each sync kind last succeeded, and each host's last snapshot."""
    synced = {
        kind: at
        for kind, at in conn.execute(
            "SELECT kind, MAX(finished_at) FROM sync_runs WHERE outcome = 'ok'"
            " GROUP BY kind ORDER BY kind"
        )
    }
    scanned = {}
    for host, taken, received, counts in conn.execute(
        "SELECT host, last_snapshot_at, received_at, counts FROM machines ORDER BY host"
    ):
        scanned[host] = {
            "snapshot_at": taken,
            "received_at": received,
            "scan_errors": fleet_views._raw(counts).get("scan_errors") or 0,
        }
    return {"synced": synced, "scanned": scanned}


def ignored_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every ignored item, as each page's Ignored footer lists them."""
    return [
        {"source": s, "key": k, "name": views._ignored_name(conn, s, k) or k}
        for s, k in conn.execute(
            "SELECT source, item_key FROM ignores ORDER BY source, item_key"
        )
    ]


def resolve_include(
    conn: sqlite3.Connection, cfg: dict[str, Any], view: str, groups: "str | None"
) -> include.Include:
    """The view's include filter, as its page resolves one: `groups` stands in
    for the URL's `?include=`. Raises include.IncludeError."""
    query = {} if groups is None else {include.PARAM: [groups]}
    remembered = overlay.view_includes(conn).get(view)
    return include.resolve(view, query, remembered, cfg)


def build(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    view: str,
    now: datetime,
    inc: "include.Include | None" = None,
    show_all: bool = False,
    mode: "str | None" = None,
) -> dict[str, Any]:
    """The view's document. Raises ViewError for a view the nav leaves out,
    and fleet_now.ModeError for an unknown `mode`, which only Now reads."""
    if view not in available(cfg):
        raise ViewError(
            f"no view {view!r} on this instance; views: {', '.join(available(cfg))}"
        )
    inc = inc or include.everything(view)
    ctx = Ctx(conn, cfg, now, inc, show_all, mode)
    lanes = BUILDERS[view](ctx)
    for lane in lanes:
        off = lane.pop("_off")
        if show_all:
            for item in off:
                item["hidden"] = "filtered"
            lane["items"] = off + lane["items"]
    return {
        "schema": SCHEMA_VERSION,
        "view": view,
        "generated_at": _stamp(now),
        "include": list(include.canonical(inc.groups)),
        "include_origin": inc.origin,
        "all": show_all,
        "freshness": freshness(conn),
        "lanes": lanes,
        "notes": ctx.notes,
        "ignored": ignored_list(conn),
    }


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2) + "\n"


def text(doc: dict[str, Any]) -> str:
    """One line per item under each lane's heading."""
    lines = [
        f"{doc['view']} at {doc['generated_at']} (include: {', '.join(doc['include']) or 'nothing'})"
    ]
    for lane in doc["lanes"]:
        off = " (off)" if lane["off"] else ""
        lines.append(f"\n{lane['title']} ({lane['count']}){off}")
        for i in lane["items"]:
            act = i["action"] or {}
            cmd = " ".join(act.get("command") or [])
            what = "; then ".join(x for x in (act.get("review"), cmd) if x)
            hidden = f"[{i['hidden']}] " if i["hidden"] else ""
            where = f" @{i['host']}" if i["host"] else ""
            tail = f" -> {what}" if what else ""
            lines.append(f"  {hidden}{i['kind']}{where} {i['title']}{tail}")
    return "\n".join(lines) + "\n"
