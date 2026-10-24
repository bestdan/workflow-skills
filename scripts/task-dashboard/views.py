"""The Now, Waiting on me, I'm waiting on, Stale and Epics views.

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
"""

import html
import sqlite3
from datetime import datetime
from typing import Any

import fleet_views
import score

_e = html.escape

TABS = (
    ("/now", "Now"),
    ("/away", "Since you were away"),
    ("/waiting-on-me", "Waiting on me"),
    ("/waiting-on", "I'm waiting on"),
    ("/board", "Board"),
    ("/stale", "Stale"),
    ("/epics", "Epics"),
)

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


def nav(active: str, cfg: "dict[str, Any] | None" = None) -> str:
    """The tab bar; a fleet-only tab is left out of an instance with no fleet."""
    hidden = set() if cfg is None or fleet_views.enabled(cfg) else fleet_views.ONLY
    links = "".join(
        f'<a href="{href}"{" aria-current=page" if href == active else ""}>{_e(name)}</a>'
        for href, name in TABS
        if href not in hidden
    )
    return f"<nav>{links}</nav>"


# --- data --------------------------------------------------------------------


class Mirror:
    """One read of everything the views need, plus the derived per-issue facts."""

    def __init__(
        self, conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
    ) -> None:
        self.cfg = cfg
        self.now = now
        self.conn = conn
        self.me = str(cfg["jira"].get("me") or "").strip()
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
        self.prs = [
            dict(r)
            for r in conn.execute(
                f"SELECT {', '.join(PR_COLUMNS)} FROM prs ORDER BY repo, number"
            )
        ]
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

    def open_mine(self) -> list[dict[str, Any]]:
        return [
            i
            for i in self.issues.values()
            if self.is_mine(i) and not score.is_done(i["status_category"])
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
        if k in m.issues and not score.is_done(m.issues[k]["status_category"])
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
        if issue.get("scope") != "search":
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
    """Per parent: total, done and blocked (open and blocked) child counts.

    Every mirrored child counts, including one mirrored only because it is
    linked to an in-scope issue. The mirror never fetches a whole epic, so the
    totals are of the children it holds, not of the epic's full child set.
    """
    groups: dict[str, dict[str, Any]] = {}
    for issue in m.issues.values():
        parent = issue.get("parent_key")
        if not parent:
            continue
        g = groups.setdefault(
            parent, {"key": parent, "total": 0, "done": 0, "blocked": 0}
        )
        g["total"] += 1
        if score.is_done(issue["status_category"]):
            g["done"] += 1
        elif m.blocked[issue["key"]][0]:
            g["blocked"] += 1
    for g in groups.values():
        parent = m.issues.get(g["key"])
        g["summary"] = parent["summary"] if parent else None
    return sorted(groups.values(), key=lambda g: g["key"])


# --- rendering ---------------------------------------------------------------


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
    return (
        f'<a href="{_e(p["url"])}">{_e(p["repo"] or "")}#{_e(str(p["number"]))}</a>'
        f" {_e(p['title'] or '')}{meta}"
    )


def issue_card(m: Mirror, issue: dict[str, Any], why: str = "") -> str:
    key = issue["key"]
    href = _e(f"https://{m.cfg['jira']['site']}/browse/{key}")
    meta = " · ".join(
        _e(str(v))
        for v in (
            issue.get("status_name"),
            issue.get("assignee"),
            issue.get("priority"),
        )
        if v
    )
    pin = '<span class="pin">pinned</span> ' if key in m.pins else ""
    why_html = f'<div class="why">{_e(why)}</div>' if why else ""
    prs = m.prs_by_issue.get(key, [])
    prs_html = (
        '<ul class="prs">' + "".join(f"<li>{_pr_line(p)}</li>" for p in prs) + "</ul>"
        if prs
        else ""
    )
    total, terms = m.scores[key]
    return (
        f'<div class="card" data-key="{_e(key)}">{pin}<a href="{href}">{_e(key)}</a>'
        f' {_e(issue.get("summary") or "")}<div class="meta">{meta}</div>'
        f"{why_html}{_terms(total, terms)}{prs_html}</div>"
    )


def pr_card(p: dict[str, Any], why: str = "") -> str:
    why_html = f'<div class="why">{_e(why)}</div>' if why else ""
    joined = f'<div class="meta">{_e(p["issue_key"])}</div>' if p["issue_key"] else ""
    return f'<div class="card" data-pr="{_e(p["url"])}">{_pr_line(p)}{joined}{why_html}</div>'


def _lane(name: str, title: str, cards: list[str], empty: str) -> str:
    body = "".join(cards) if cards else f'<p class="empty">{_e(empty)}</p>'
    return (
        f'<section class="lane" data-section="{name}">'
        f"<h2>{_e(title)} ({len(cards)})</h2>{body}</section>"
    )


def _me_note(m: Mirror) -> str:
    if m.me:
        return ""
    return (
        '<p class="note">jira.me is not set, so every in-scope issue counts as'
        " yours. Set it to your Jira display name in config.toml.</p>"
    )


def render_now(m: Mirror) -> str:
    cards = [issue_card(m, i) for i in now_items(m)]
    return _me_note(m) + _lane("now", "Now", cards, "Nothing open.")


def render_waiting_on_me(m: Mirror) -> str:
    issues, prs = waiting_on_me(m)
    return (
        _me_note(m)
        + _lane(
            "issues",
            "My issues blocking others",
            [issue_card(m, i, why) for i, why in issues],
            "None of your open issues blocks anyone.",
        )
        + _lane(
            "reviews",
            "Reviews requested of me",
            [pr_card(p) for p in prs],
            "No reviews requested.",
        )
    )


def render_waiting_on(m: Mirror) -> str:
    issues, prs = waiting_on(m)
    return (
        _me_note(m)
        + _lane(
            "issues",
            "My blocked issues",
            [issue_card(m, i, why) for i, why in issues],
            "None of your open issues has an unresolved blocker.",
        )
        + _lane(
            "prs",
            "My PRs waiting on review or CI",
            [pr_card(p, why) for p, why in prs],
            "No PR of yours is waiting.",
        )
    )


def render_stale(m: Mirror) -> str:
    """Jira's stale lane, then, on a fleet instance, the fleet's (fleet_views)."""
    cards = [issue_card(m, i, why) for i, why in stale(m)]
    jira = _lane("stale", "Stale", cards, "Nothing in progress has gone quiet.")
    if not fleet_views.enabled(m.cfg):
        return jira
    return (jira if m.issues else "") + fleet_views.render_stale(m.conn, m.cfg, m.now)


def render_away(m: Mirror) -> str:
    return fleet_views.render_away(m.conn, m.cfg, m.now)


def render_epics(m: Mirror) -> str:
    rows = []
    for g in epics(m):
        href = _e(f"https://{m.cfg['jira']['site']}/browse/{g['key']}")
        summary = f" {_e(g['summary'])}" if g["summary"] else ""
        blocked = f" · {g['blocked']} blocked" if g["blocked"] else ""
        rows.append(
            f'<div class="card" data-key="{_e(g["key"])}"><a href="{href}">'
            f'{_e(g["key"])}</a>{summary}<div class="meta">'
            f"{g['done']}/{g['total']} done{blocked}</div></div>"
        )
    return _lane("epics", "Epics", rows, "No issue in the mirror has a parent.")


ROUTES = {
    "/now": ("Now", render_now),
    "/waiting-on-me": ("Waiting on me", render_waiting_on_me),
    "/waiting-on": ("I'm waiting on", render_waiting_on),
    "/stale": ("Stale", render_stale),
    "/away": ("Since you were away", render_away),
    "/epics": ("Epics", render_epics),
}


def render(
    path: str, conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> str:
    """The body of the view at `path` (a key of ROUTES), tab bar included."""
    _, fn = ROUTES[path]
    return nav(path, cfg) + fn(Mirror(conn, cfg, now))
