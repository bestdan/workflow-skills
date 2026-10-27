"""The Needs you, Now, Waiting on me, I'm waiting on, Stale and Epics views.

On a fleet instance Now is fleet_now.py's ranked queue (and the home page),
with the Jira Now below it when Jira is on; Needs you is needs.py's.
The fleet instance's Pull requests and Issues tabs are fleet_tabs.py's; they
replace Waiting on me and I'm waiting on in a fleet instance's nav.

Each view reads SQLite only: Jira rows through the `jira_issues` view, so a
non-Jira row never reaches them, plus `links`, `prs` and the overlay tables.
Every issue card carries its score broken into terms (score.py) and the PRs
joined to it, so the ranking can be argued with from any tab.

"Mine" is the issue's assignee matching config `jira.me`, the display name the
mirror stores people by. With `jira.me` unset, every issue a scope query
returned (`scope = 'search'`) counts as mine, and "someone else's" means any
issue at all; the page says so.

The overlay (pins, order, snoozes) is read here and written elsewhere. Now
puts pinned items first, by `order.position` then pin time, and hides an item
whose snooze `until` is still in the future. A date-only `until` ends at
local midnight at the start of that date (`score.local_day_start`).

Each view takes an include filter (include.py) and draws its switches under
the nav. A lane belongs to one group, every row here being a Jira `ticket` or
a `prs` row, so a lane whose group is off says so rather than listing nothing.
An issue card keeps its joined PR lines whatever the filter: they are part of
the issue's card, not PR rows of their own.
"""

import html
import json
import sqlite3
from datetime import datetime
from typing import Any

import fleet_now
import fleet_tabs
import fleet_views
import include
import lane_tabs
import needs
import overlay
import score
import sync
import view_model
import workstreams

_e = html.escape

# Now leads: on a fleet instance it is the home page, with Needs you, the
# full list it is drawn from, second.
TABS = (
    ("/now", "Now"),
    ("/needs", "Needs you"),
    ("/waiting-on-me", "Waiting on me"),
    ("/waiting-on", "I'm waiting on"),
    ("/prs", "Pull requests"),
    ("/issues", "Issues"),
    ("/workstreams", "Workstreams"),
    ("/board", "Board"),
    ("/stale", "Stale"),
    ("/epics", "Epics"),
    ("/health", "Health"),
)

# Tabs left out with `jira.enabled` false.
JIRA_ONLY = {"/epics", "/board", "/waiting-on-me", "/waiting-on"}
# Jira tabs a fleet instance leaves out: its Pull requests and Issues tabs
# (fleet_views.ONLY) replace them.
NOT_ON_FLEET = {"/waiting-on-me", "/waiting-on"}

CSS = """
nav { display:flex; flex-wrap:wrap; gap:4px; padding:8px 16px 0;
  border-bottom:1px solid var(--line); }
nav a { padding:4px 10px; border-radius:6px 6px 0 0; color:var(--muted);
  text-decoration:none; }
nav a[aria-current=page] { color:var(--fg); font-weight:600; background:var(--card);
  border:1px solid var(--line); border-bottom:none; }
.card .terms { list-style:none; margin:4px 0 0; padding:0; display:flex;
  flex-wrap:wrap; gap:2px 10px; font-size:12px; color:var(--muted); }
.card .terms b { color:var(--fg); }
.card .total { font-weight:600; color:var(--fg); }
.card .neg b { color:var(--bad); }
.card .prs { margin:4px 0 0; padding-left:16px; font-size:12px; }
.card .why { color:var(--muted); font-size:12px; }
.pin { font-size:12px; color:var(--accent); font-weight:600; }
.note { color:var(--muted); font-size:12px; }
.card .ref { color:var(--muted); font-size:12px; font-weight:400; }
.card .epic { color:var(--muted); font-size:12px; }
.icon { display:inline-block; width:1.1em; text-align:center; margin-right:4px;
  color:var(--muted); position:relative; cursor:help; }
.icon:hover::after { content:attr(data-tip); position:absolute; left:0; top:1.5em;
  z-index:10; white-space:nowrap; padding:2px 8px; border-radius:4px;
  font-size:12px; font-weight:400; background:var(--fg); color:var(--bg); }
.icon.s-indeterminate { color:var(--accent); }
.icon.s-done { color:#1a7f37; }
.icon.blocked { color:var(--bad); }
#filter { width:240px; max-width:100%; padding:4px 10px;
  font:inherit; color:var(--fg); background:var(--card);
  border:1px solid var(--line); border-radius:6px; }
.lane.filtered-empty .card, .lane.filtered-empty .empty { display:none; }
.card { position:relative; }
.others { margin-top:16px; }
.others summary { color:var(--muted); cursor:pointer; }
.ignored { margin-top:16px; color:var(--muted); }
.ignored ul { list-style:none; padding:0; }
.ignored li button { font:inherit; font-size:12px; margin-left:6px; }
"""

PR_COLUMNS = (
    "url",
    "repo",
    "number",
    "title",
    "is_draft",
    "review_decision",
    "ci_state",
    "role",
    "issue_key",
)


def hidden_tabs(cfg: dict[str, Any]) -> set[str]:
    """The TABS paths this instance leaves out of its nav (see nav())."""
    hidden = set(NOT_ON_FLEET if fleet_views.enabled(cfg) else fleet_views.ONLY)
    if not sync.jira_enabled(cfg):
        hidden.update(JIRA_ONLY)
    return hidden


def nav(
    active: str,
    cfg: "dict[str, Any] | None" = None,
    inc: "include.Include | None" = None,
    query: "dict[str, list[str]] | None" = None,
) -> str:
    """The tab bar, less the tabs this instance has no data for.

    A fleet-only tab is left out of an instance with no fleet, Waiting on me
    and I'm waiting on out of a fleet instance, and a Jira-only tab out of an
    instance with `jira.enabled` false. With `inc`, the view's
    include switches follow it (include.py); `query` is the request's own, so a
    switch keeps its other parameters.
    """
    hidden = hidden_tabs(cfg) if cfg is not None else set()
    links = "".join(
        f'<a href="{href}"{" aria-current=page" if href == active else ""}>{_e(name)}</a>'
        for href, name in TABS
        if href not in hidden
    )
    bar = "" if inc is None else include.switches(inc, active, query or {})
    return f"<nav>{links}</nav>{bar}"


# --- data --------------------------------------------------------------------


under_ignored = overlay.under_ignored


class Mirror:
    """One read of everything the views need, plus the derived per-issue facts."""

    def __init__(
        self, conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
    ) -> None:
        self.cfg = cfg
        self.now = now
        self.conn = conn
        # The request's query, which views.render sets: the `?ws=` filter.
        self.query: dict[str, list[str]] = {}
        self.me = str(cfg["jira"].get("me") or "").strip()
        self.ov = overlay.load(conn)
        conn.row_factory = sqlite3.Row
        self.issues: dict[str, dict[str, Any]] = {
            r["key"]: dict(r)
            for r in conn.execute("SELECT * FROM jira_issues ORDER BY key")
        }
        # The link signal is this join: a mirrored linked issue's own status
        # category wins over the one Jira inlined in the link at view time.
        self.links: dict[str, list[dict[str, Any]]] = {}
        for r in conn.execute(
            "SELECT l.issue_key, l.linked_key, l.link_type, l.direction,"
            " COALESCE(j.status_category, l.linked_status_category)"
            "   AS linked_status_category,"
            " j.assignee AS linked_assignee, j.status_name AS linked_status_name"
            " FROM links l LEFT JOIN jira_issues j ON j.key = l.linked_key"
            " ORDER BY l.issue_key, l.linked_key"
        ):
            self.links.setdefault(r["issue_key"], []).append(dict(r))
        self.ignored_prs = {k for s, k in self.ov.ignored if s == "github"}
        self.ignored = {k for s, k in self.ov.ignored if s == "jira"}
        prs = [
            dict(r)
            for r in conn.execute(
                f"SELECT {', '.join(PR_COLUMNS)} FROM prs ORDER BY repo, number"
            )
        ]
        self.prs = [p for p in prs if p["url"] not in self.ignored_prs]
        self.prs_by_issue: dict[str, list[dict[str, Any]]] = {}
        for p in self.prs:
            if p["issue_key"]:
                self.prs_by_issue.setdefault(p["issue_key"], []).append(p)
        self.pins = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                "SELECT p.item_key, o.position, p.pinned_at FROM pins p"
                ' LEFT JOIN "order" o ON o.source = p.source AND o.item_key = p.item_key'
                " WHERE p.source = 'jira'"
            )
        }
        self.snoozed = {
            r[0]
            for r in conn.execute(
                "SELECT item_key, until FROM snoozes WHERE source = 'jira'"
            )
            if _in_future(r[1], now)
        }
        conn.row_factory = None
        weights = cfg["score"]["weights"]
        stuck_days = cfg["score"].get("stuck_days") or {}
        self.blocked: dict[str, tuple[bool, list[str]]] = {}
        self.scores: dict[str, tuple[float, list[score.Term]]] = {}
        for key, issue in self.issues.items():
            links = self.links.get(key, [])
            self.blocked[key] = score.is_blocked(issue, links, cfg)
            ctx = {
                "now": now,
                "unblocks": len(score.open_blocked(links)),
                "prs": self.prs_by_issue.get(key, []),
                "blocked": self.blocked[key],
                "stuck_days": stuck_days,
            }
            self.scores[key] = score.score(issue, ctx, weights)

    def is_mine(self, issue: dict[str, Any]) -> bool:
        if not self.me:
            return issue.get("scope") == "search"
        return (issue.get("assignee") or "").casefold() == self.me.casefold()

    def is_mine_name(self, assignee: "str | None") -> bool:
        return bool(self.me) and (assignee or "").casefold() == self.me.casefold()

    def is_ignored(self, issue: dict[str, Any]) -> bool:
        """Ignored itself, or under an ignored ancestor (epic, story, ...)."""
        return under_ignored(
            issue["key"],
            issue.get("parent_key"),
            lambda k: (self.issues.get(k) or {}).get("parent_key"),
            self.ignored,
        )

    def open_mine(self) -> list[dict[str, Any]]:
        return [
            i
            for i in self.issues.values()
            if self.is_mine(i)
            and not score.is_done(i["status_category"])
            and not self.is_ignored(i)
        ]


def _in_future(value: "str | None", now: datetime) -> bool:
    until = score.parse_ts(value)
    return until is not None and until > now


# --- views -------------------------------------------------------------------


def now_items(m: Mirror) -> list[dict[str, Any]]:
    """Pinned first (overlay order, then pin time), then by score; no snoozed."""
    keys = {i["key"] for i in m.open_mine()}
    keys |= {
        k
        for k in m.pins
        if k in m.issues
        and not score.is_done(m.issues[k]["status_category"])
        and not m.is_ignored(m.issues[k])
    }
    keys -= m.snoozed
    pinned = sorted(
        (k for k in keys if k in m.pins),
        key=lambda k: (
            m.pins[k][0] is None,
            m.pins[k][0] or 0,
            m.pins[k][1] or "",
            k,
        ),
    )
    rest = sorted(
        (k for k in keys if k not in m.pins), key=lambda k: (-m.scores[k][0], k)
    )
    return [m.issues[k] for k in pinned + rest]


def waiting_on_me(m: Mirror) -> tuple[list[tuple[dict, str]], list[dict]]:
    """My open issues blocking someone else's open issue; PRs awaiting my review."""
    issues = []
    for issue in m.open_mine():
        theirs = [
            link
            for link in m.links.get(issue["key"], [])
            if link["linked_key"] in score.open_blocked([link])
            and not m.is_mine_name(link["linked_assignee"])
        ]
        if theirs:
            why = "blocks " + ", ".join(_who(link) for link in theirs)
            issues.append((issue, why))
    issues.sort(key=lambda pair: (-m.scores[pair[0]["key"]][0], pair[0]["key"]))
    return issues, [p for p in m.prs if p["role"] == "reviewer"]


def waiting_on(m: Mirror) -> tuple[list[tuple[dict, str]], list[tuple[dict, str]]]:
    """My open issues with an unresolved blocker; my PRs waiting on review or CI."""
    issues = []
    for issue in m.open_mine():
        links = m.links.get(issue["key"], [])
        blockers = [
            link
            for link in links
            if link["linked_key"] in score.unresolved_blockers([link])
        ]
        if blockers:
            issues.append((issue, "blocked by " + ", ".join(_who(b) for b in blockers)))
    issues.sort(key=lambda pair: (-m.scores[pair[0]["key"]][0], pair[0]["key"]))
    prs = []
    for p in m.prs:
        if p["role"] != "author":
            continue
        reasons = []
        # Null reviewDecision means the repo requires no review, not that one
        # is outstanding: counting it listed every PR on an unprotected repo.
        if not p["is_draft"] and p["review_decision"] == "REVIEW_REQUIRED":
            reasons.append("awaiting review")
        if p["ci_state"] in ("pending", "failure"):
            reasons.append(
                f"CI {'failed' if p['ci_state'] == 'failure' else 'pending'}"
            )
        if reasons:
            prs.append((p, ", ".join(reasons)))
    return issues, prs


def stale(m: Mirror) -> list[tuple[dict, str]]:
    """In-scope `indeterminate` issues past the threshold with no update since."""
    threshold = (m.cfg["score"].get("stuck_days") or {}).get("indeterminate")
    if not threshold:
        return []
    out = []
    for issue in m.issues.values():
        if issue.get("scope") != "search" or m.is_ignored(issue):
            continue
        if (issue["status_category"] or "").lower() != "indeterminate":
            continue
        since = score.parse_ts(issue["status_category_changed"])
        updated = score.parse_ts(issue["updated"]) or since
        if since is None or updated is None:
            continue
        in_cat = (m.now - since).total_seconds() / 86400
        quiet = (m.now - updated).total_seconds() / 86400
        if in_cat > threshold and quiet > threshold:
            out.append(
                (
                    issue,
                    f"{issue['status_name'] or 'in progress'}: {in_cat:.0f}d in"
                    f" category, no update for {quiet:.0f}d",
                    since,
                )
            )
    out.sort(key=lambda t: (t[2], t[0]["key"]))
    return [(i, why) for i, why, _ in out]


def epics(m: Mirror) -> list[dict[str, Any]]:
    """Per parent: child counts and how the user relates to the epic.

    Every mirrored child that is not ignored counts, including one mirrored
    only because it is linked to an in-scope issue. The mirror never fetches a
    whole epic, so the totals are of the children it holds, not of the epic's
    full child set.

    `role` is `mine` (the epic is assigned to the user), `involved` (the user
    reported it, or is assigned to or reported one of its children), `pinned`,
    or `other`. An epic counts as an epic only once a child of it is mirrored:
    the mirror does not record issue types.
    """
    groups: dict[str, dict[str, Any]] = {}
    for issue in m.issues.values():
        parent = issue.get("parent_key")
        if not parent or m.is_ignored(issue):
            continue
        g = groups.setdefault(
            parent,
            {
                "key": parent,
                "total": 0,
                "done": 0,
                "blocked": 0,
                "child_says": None,
                "touched": False,
            },
        )
        g["child_says"] = g["child_says"] or epic_name(issue)
        g["touched"] = g["touched"] or _touches(m, issue)
        g["total"] += 1
        if score.is_done(issue["status_category"]):
            g["done"] += 1
        elif m.blocked[issue["key"]][0]:
            g["blocked"] += 1
    for g in groups.values():
        parent = m.issues.get(g["key"])
        child_says = g.pop("child_says")
        g["summary"] = (parent["summary"] if parent else None) or child_says
        touched = g.pop("touched")
        if parent and m.is_mine_name(parent.get("assignee")):
            g["role"] = "mine"
        elif touched or (parent and m.is_mine_name(parent.get("reporter"))):
            g["role"] = "involved"
        elif g["key"] in m.pins:
            g["role"] = "pinned"
        else:
            g["role"] = "other"
    return sorted(groups.values(), key=lambda g: g["key"])


def _touches(m: Mirror, issue: dict[str, Any]) -> bool:
    """Assigned to or reported by the user; with no `jira.me`, in search scope."""
    if not m.me:
        return issue.get("scope") == "search"
    return m.is_mine_name(issue.get("assignee")) or m.is_mine_name(
        issue.get("reporter")
    )


# --- rendering ---------------------------------------------------------------


# Icons go by status category, not status name: a site has a dozen names but
# only these three categories. The hover text carries the exact name.
STATUS_ICONS = {
    "new": ("○", "To do"),
    "indeterminate": ("◐", "In progress"),
    "done": ("●", "Done"),
}
BLOCKED_ICON = ("⊘", "Blocked")


def status_icon(
    category: "str | None", name: "str | None", blocked: bool = False
) -> str:
    icon, label = (
        BLOCKED_ICON
        if blocked
        else STATUS_ICONS.get((category or "").lower(), ("·", "Unknown status"))
    )
    title = f"{label}: {name}" if name else label
    cls = "icon blocked" if blocked else f"icon s-{_e((category or 'unknown').lower())}"
    return f'<span class="{cls}" role="img" aria-label="{_e(title)}" data-tip="{_e(title)}">{icon}</span>'


def card_title(href: str, title: "str | None", ref: str) -> str:
    """The title as the link, with the key or PR number as a muted suffix.

    With no title the ref is the link text, so the suffix is left off.
    """
    link = view_model.link(href, title or ref, "title")
    return link + fleet_views.ref_suffix(ref) if title else link


def epic_name(issue: dict[str, Any]) -> "str | None":
    """The parent's summary, which every `view` response carries.

    Read from raw_json, not the mirror: the mirror holds the parent issue
    itself for only some children.
    """
    try:
        raw = json.loads(issue.get("raw_json") or "{}")
    except ValueError:
        return None
    parent = (raw.get("fields") or {}).get("parent") or {}
    name = (parent.get("fields") or {}).get("summary")
    return name if isinstance(name, str) and name else None


def epic_line(name: "str | None") -> str:
    return f'<div class="epic">◆ {_e(name)}</div>' if name else ""


def _who(link: dict[str, Any]) -> str:
    bits = [link["linked_key"]]
    extra = [
        x for x in (link.get("linked_status_name"), link.get("linked_assignee")) if x
    ]
    if extra:
        bits.append(f"({', '.join(extra)})")
    return " ".join(bits)


def _fmt(value: float) -> str:
    text = f"{value:+.2f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


def _terms(total: float, terms: list[score.Term]) -> str:
    items = [f'<li class="total">score {_fmt(total)}</li>']
    for name, value, why in terms:
        if value == 0:
            continue
        cls = ' class="neg"' if value < 0 else ""
        items.append(
            f'<li{cls} title="{_e(why)}">{_e(name)} <b>{_fmt(value)}</b>'
            f" <span>{_e(why)}</span></li>"
        )
    return f'<ul class="terms" data-total="{total}">{"".join(items)}</ul>'


def _pr_line(p: dict[str, Any]) -> str:
    bits = []
    if p["is_draft"]:
        bits.append("draft")
    if p["review_decision"]:
        bits.append(str(p["review_decision"]).lower().replace("_", " "))
    if p["ci_state"]:
        bits.append(f"CI {p['ci_state']}")
    meta = f" · {_e(' · '.join(bits))}" if bits else ""
    ref = f"{p['repo'] or ''}#{p['number']}"
    return card_title(p["url"], p["title"], ref) + meta


def issue_meta(issue: dict[str, Any]) -> list[str]:
    """A Jira issue card's meta line, as its plain-text parts."""
    fields = (issue.get("status_name"), issue.get("assignee"), issue.get("priority"))
    return [str(v) for v in fields if v]


def issue_card(m: Mirror, issue: dict[str, Any], why: str = "") -> str:
    key = issue["key"]
    href = f"https://{m.cfg['jira']['site']}/browse/{key}"
    meta = " · ".join(_e(v) for v in issue_meta(issue))
    pin = '<span class="pin">pinned</span> ' if key in m.pins else ""
    why_html = f'<div class="why">{_e(why)}</div>' if why else ""
    prs = m.prs_by_issue.get(key, [])
    prs_html = (
        '<ul class="prs">' + "".join(f"<li>{_pr_line(p)}</li>" for p in prs) + "</ul>"
        if prs
        else ""
    )
    total, terms = m.scores[key]
    icon = status_icon(
        issue.get("status_category"),
        issue.get("status_name"),
        bool(m.blocked[key][0]) or bool(issue.get("flagged")),
    )
    item = ("jira", key)
    return (
        f"<div {overlay.card_attrs(item, m.ov, m.now)}>{pin}{icon}"
        f"{card_title(href, issue.get('summary'), key)}"
        f'{epic_line(epic_name(issue))}<div class="meta">{meta}</div>'
        f"{why_html}{_terms(total, terms)}{prs_html}"
        f"{overlay.card_controls(item, m.ov, m.now, reorder=False)}</div>"
    )


def pr_card(m: Mirror, p: dict[str, Any], why: str = "") -> str:
    why_html = f'<div class="why">{_e(why)}</div>' if why else ""
    joined = f'<div class="meta">{_e(p["issue_key"])}</div>' if p["issue_key"] else ""
    item = ("github", p["url"])
    return (
        f"<div {overlay.card_attrs(item, m.ov, m.now)}>"
        f"{_pr_line(p)}{joined}{why_html}"
        f"{overlay.card_controls(item, m.ov, m.now, reorder=False, pin=False)}</div>"
    )


def _lane(
    name: str,
    title: "str | None",
    cards: list[str],
    empty: str,
    group: "str | None" = None,
    inc: "include.Include | None" = None,
    tab: "str | None" = None,
) -> str:
    """A lane of cards; `title` is None where the tab already names it.

    A lane whose `group` the include filter switched off says so instead.
    `tab` is a short label for its tab (lane_tabs.py), where the title is long.
    """
    tab_attr = lane_tabs.tab_attr(tab)
    if group is not None and inc is not None and not inc.has(group):
        head = f"<h2>{_e(title)} (0)</h2>" if title else ""
        return (
            f'<section class="lane" data-section="{name}" data-filtered="{group}"'
            f"{tab_attr}>{head}{include.off_note(group)}</section>"
        )
    body = "".join(cards) if cards else f'<p class="empty">{_e(empty)}</p>'
    head = f"<h2>{_e(title)} ({len(cards)})</h2>" if title else ""
    return (
        f'<section class="lane" data-section="{name}"{tab_attr}>{head}{body}</section>'
    )


def _me_note(m: Mirror) -> str:
    if m.me or not sync.jira_enabled(m.cfg):
        return ""
    return (
        '<p class="note">jira.me is not set, so every in-scope issue counts as'
        " yours. Set it to your Jira display name in config.toml.</p>"
    )


# Every Jira row these views list is a `ticket`; the PRs are the `prs` table's.
TICKETS = include.group_of("ticket")
PRS = include.group_of("pr")


def _ic(m: Mirror) -> Any:
    return lambda t: issue_card(m, t[0], t[1])


def _pc(m: Mirror) -> Any:
    return lambda t: pr_card(m, t[0], t[1])


def _render(lane: view_model.Lane, card: Any, inc: include.Include) -> str:
    """A Lane of jira_lanes() as HTML, each item drawn by `card`."""
    cards = [card(x) for x in lane.items]
    return _lane(lane.id, lane.title, cards, lane.empty, lane.group, inc, lane.tab)


def jira_now_lane(m: Mirror, title: "str | None") -> view_model.Lane:
    """Jira's Now: (issue, why) pairs, `why` always empty."""
    items = [(i, "") for i in now_items(m)]
    return view_model.Lane("now", title, items, "Nothing open.", TICKETS)


def render_now(m: Mirror, inc: include.Include) -> str:
    """A fleet instance's ranked queue (fleet_now), then, with Jira on, the
    Jira Now; a Jira instance's Now alone."""
    if not fleet_views.enabled(m.cfg):
        return _me_note(m) + _render(jira_now_lane(m, None), _ic(m), inc)
    asked = (m.query.get("mode") or [""])[0].strip() or None
    fleet = fleet_now.render(m.conn, m.cfg, m.now, inc, asked)
    if not sync.jira_enabled(m.cfg):
        return fleet
    return fleet + _me_note(m) + _render(jira_now_lane(m, "Jira"), _ic(m), inc)


def waiting_on_me_lanes(m: Mirror) -> list[view_model.Lane]:
    """Issues as (issue, why), then review requests as (PR, why)."""
    issues, prs = waiting_on_me(m)
    return [
        view_model.Lane(
            "issues", "Issues", issues, "None of your open issues blocks anyone.", TICKETS
        ),
        view_model.Lane(
            "reviews", "Pull requests", [(p, "") for p in prs], "No reviews requested.", PRS
        ),
    ]  # fmt: skip


def render_waiting_on_me(m: Mirror, inc: include.Include) -> str:
    issues, prs = waiting_on_me_lanes(m)
    return _me_note(m) + _render(issues, _ic(m), inc) + _render(prs, _pc(m), inc)


def waiting_on_lanes(m: Mirror) -> list[view_model.Lane]:
    """Blocked issues as (issue, why), then my waiting PRs as (PR, why)."""
    issues, prs = waiting_on(m)
    return [
        view_model.Lane(
            "issues", "Issues", issues,
            "None of your open issues has an unresolved blocker.", TICKETS,
        ),
        view_model.Lane("prs", "Pull requests", prs, "No PR of yours is waiting.", PRS),
    ]  # fmt: skip


def render_waiting_on(m: Mirror, inc: include.Include) -> str:
    issues, prs = waiting_on_lanes(m)
    return _me_note(m) + _render(issues, _ic(m), inc) + _render(prs, _pc(m), inc)


def jira_stale_lane(m: Mirror) -> "view_model.Lane | None":
    """Jira's stale lane as (issue, why) pairs; None on a fleet instance whose
    mirror holds no Jira issue, where the page leaves it out."""
    on_fleet = fleet_views.enabled(m.cfg)
    if on_fleet and not m.issues:
        return None
    # Beside the fleet's lanes it is a tab of its own, so it needs a title.
    return view_model.Lane(
        "stale",
        "Jira" if on_fleet else None,
        stale(m),
        "Nothing in progress has gone quiet.",
        TICKETS,
    )


def render_stale(m: Mirror, inc: include.Include) -> str:
    """Jira's stale lane, then, on a fleet instance, the fleet's (fleet_views)."""
    lane = jira_stale_lane(m)
    jira = _render(lane, _ic(m), inc) if lane is not None else ""
    if not fleet_views.enabled(m.cfg):
        return jira
    return jira + fleet_views.render_stale(m.conn, m.cfg, m.now, inc)


def render_needs(m: Mirror, inc: include.Include) -> str:
    return needs.render(m.conn, m.cfg, m.now, inc)


def render_prs(m: Mirror, inc: include.Include) -> str:
    ws = workstreams.from_query(m.conn, m.query)
    return fleet_tabs.render_prs(m.conn, m.cfg, m.now, inc, ws)


def render_issues(m: Mirror, inc: include.Include) -> str:
    ws = workstreams.from_query(m.conn, m.query)
    return fleet_tabs.render_issues(m.conn, m.cfg, m.now, inc, ws)


def render_workstreams(m: Mirror, inc: include.Include) -> str:
    return workstreams.render(m.conn, m.cfg, m.now, inc)


EPIC_LANES = (
    ("mine", "Mine", "No epic is assigned to you."),
    ("involved", "Involved", "You have no issue under anyone else's epic."),
    ("pinned", "Pinned", "No other epic is pinned."),
)


def _epic_card(m: Mirror, g: dict[str, Any]) -> str:
    href = f"https://{m.cfg['jira']['site']}/browse/{g['key']}"
    blocked = f" · {g['blocked']} blocked" if g["blocked"] else ""
    pinned = g["key"] in m.pins
    # Now lists a pin only when the mirror holds its issue (now_items).
    pin_tip = (
        "Keep it on Epics; while it is open it also shows on Now"
        if g["key"] in m.issues
        else "Keep it on Epics"
    )
    return (
        f'<div class="card" data-source="jira" data-key="{_e(g["key"])}">'
        f'{card_title(href, g["summary"], g["key"])}<div class="meta">'
        f"{g['done']}/{g['total']} done{blocked}</div>"
        f'<div class="ov"><button type="button" data-ov="{"unpin" if pinned else "pin"}"'
        f' title="{pin_tip}">'
        f"{'Unpin' if pinned else 'Pin'}</button>"
        '<button type="button" data-ov="ignore"'
        ' title="Hide this epic and everything under it">Ignore epic</button>'
        "</div></div>"
    )


def epic_lanes(m: Mirror) -> list[view_model.Lane]:
    """Mine, Involved and Pinned, then Other (a closed list on the page, and
    left out when empty); each item an epics() group."""
    groups = epics(m)
    out = [
        view_model.Lane(
            role, title, [g for g in groups if g["role"] == role], empty, TICKETS
        )
        for role, title, empty in EPIC_LANES
    ]
    others = [g for g in groups if g["role"] == "other"]
    if others:
        out.append(view_model.Lane("other", "Other epics", others, "", TICKETS))
    return out


def render_epics(m: Mirror, inc: include.Include) -> str:
    if not inc.has(TICKETS):
        # Every epic and child is a ticket: one note, not one per lane.
        return _lane("epics", None, [], "", TICKETS, inc)
    out = ""
    for lane in epic_lanes(m):
        cards = [_epic_card(m, g) for g in lane.items]
        if lane.id != "other":
            out += _lane(lane.id, lane.title, cards, lane.empty, lane.group, inc)
            continue
        out += (
            f'<details class="lane others" data-section="other">'
            f"<summary>Other epics ({len(cards)}): you have no issue in them."
            " Pin one to keep it above.</summary>"
            f"{''.join(cards)}</details>"
        )
    return out


ROUTES = {
    "/needs": ("Needs you", render_needs),
    "/now": ("Now", render_now),
    "/waiting-on-me": ("Waiting on me", render_waiting_on_me),
    "/waiting-on": ("I'm waiting on", render_waiting_on),
    "/stale": ("Stale", render_stale),
    "/epics": ("Epics", render_epics),
    "/prs": ("Pull requests", render_prs),
    "/issues": ("Issues", render_issues),
    "/workstreams": ("Workstreams", render_workstreams),
}


def render(
    path: str,
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
    query: "dict[str, list[str]] | None" = None,
) -> str:
    """The body of the view at `path` (a key of ROUTES), tab bar included.

    `inc` filters it (include.py); without one it shows everything and draws
    no switches.
    """
    _, fn = ROUTES[path]
    shown = inc if inc is not None else include.everything(path.lstrip("/"))
    m = Mirror(conn, cfg, now)
    m.query = query or {}
    return nav(path, cfg, inc, query) + fn(m, shown) + render_ignored(conn)


# Every Jira issue under `key`, at any depth. UNION, not UNION ALL, drops
# repeats, so a parent cycle in the data ends the recursion; a cycle back to
# `key` itself is not counted as something it hides.
_DESCENDANTS = """
WITH RECURSIVE d(key) AS (
  SELECT key FROM issues WHERE source = 'jira' AND parent_key = :key
  UNION
  SELECT i.key FROM issues i JOIN d ON i.parent_key = d.key WHERE i.source = 'jira'
)
SELECT count(*) FROM d WHERE key != :key
"""


def _ignored_name(conn: sqlite3.Connection, source: str, key: str) -> "str | None":
    """What to call an ignored item: its mirrored summary, else a fallback."""
    row = conn.execute(
        "SELECT summary FROM issues WHERE source = ? AND key = ?", (source, key)
    ).fetchone()
    if row and row[0]:
        return row[0]
    if source == "jira":
        # An epic the mirror does not hold is named by its children's copy.
        for (raw,) in conn.execute(
            "SELECT raw_json FROM issues WHERE source = 'jira' AND parent_key = ?"
            " ORDER BY key",
            (key,),
        ):
            name = epic_name({"raw_json": raw})
            if name:
                return name
        return None
    row = conn.execute(
        "SELECT title, repo, number FROM prs WHERE url = ?", (key,)
    ).fetchone()
    if row:
        return f"{row[0] or key} ({row[1] or ''}#{row[2]})"
    return None


def render_ignored(conn: sqlite3.Connection) -> str:
    """A closed list of every ignored item, of any source, each with Unignore.

    Empty when nothing is ignored. It reads only the rows it names, so the
    Board can show it without building a Mirror.
    """
    items = conn.execute(
        "SELECT source, item_key FROM ignores ORDER BY source, item_key"
    ).fetchall()
    if not items:
        return ""
    rows = []
    for source, key in items:
        name = _ignored_name(conn, source, key)
        ref = hides = ""
        if source == "jira":
            ref = f' <span class="ref">({_e(key)})</span>'
            (n,) = conn.execute(_DESCENDANTS, {"key": key}).fetchone()
            if n:
                hides = f" · hides {n} child issue{'s' if n != 1 else ''}"
        rows.append(
            f'<li class="card" data-source="{_e(source)}" data-key="{_e(key)}">'
            f"{_e(name or key)}{ref}{hides}"
            ' <button type="button" data-ov="unignore">Unignore</button></li>'
        )
    return (
        f'<details class="ignored"><summary>Ignored ({len(rows)})</summary>'
        f"<ul>{''.join(rows)}</ul></details>"
    )
