"""The Health panel: the work-in-progress reset design's figures, live.

Part of the fleet instance (dev_docs/task-dashboard.md, "Health"). The reset
design planned to
re-measure its figures by hand on a fixed date; this module computes them from
the mirror instead, for any window, so the `/health` route and
`dashboard.py health --json` read the same numbers from the same function,
`compute()`.

WINDOWED FIGURES are computed over ``[start, end)`` and compared with the
window of the same length just before it (the trend):

- **Approval-shaped prompts per 100** (bare "y"/"yes" included): history
  entries whose ``approval_shaped`` flag the collector set.
- **Switch rate:** among prompts less than ``SWITCH_GAP`` after the previous
  prompt (on any machine), the share whose session differs from it.
- **Prompts with 3+ sessions active:** a session is active at a prompt when it
  had a prompt in the ``SWITCH_GAP`` up to and including it. The share of
  prompts at which ``ACTIVE_MIN`` or more sessions were.
- **Permission prompts per 100 prompts:** hook ``Notification`` events of type
  ``permission_prompt`` over hook ``UserPromptSubmit`` events.
- **The two waits**, median and p90 in minutes, paired per session from hook
  events: the owner waits from a prompt to the session's next ``Stop``; an
  agent waits from a ``Stop`` to the session's next prompt. A wait belongs to
  the window its closing event falls in, so a re-measure of a past window
  gives the same answer later. Until ``BASELINE_DAYS`` of hook data exist
  (first event to the window's end) the wait rows say "baseline" and are not
  judged against a target. Nothing is ever estimated: no pairs, no value.

Entries with no session id count toward the per-100 figures but not toward
the switch rate or the active-session share.

CURRENT FIGURES have no window: open PRs awaiting the owner and the review
queue (``gauges.review_queue``, the one reader both the header and /status
use), designs at ``proposed`` (github_fleet's rows, unknown under the same
rule). Issues opened : closed has
no source yet, since github_fleet mirrors only issues needing a human, and
shows as such.

TARGETS come from config ``[health.targets]``: ``"observed"`` (shown, never
coloured) or a comparison such as ``"<= 5"``.
"""

import bisect
import html
import json
import math
import sqlite3
import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

import config
import gauges
import github_fleet
import include
import score

WINDOWS = (7, 14, 30)
DEFAULT_WINDOW = 7
SWITCH_GAP = timedelta(minutes=30)
ACTIVE_MIN = 3
BASELINE_DAYS = 14

# id, label, source, unit, kind ("window" | "wait" | "now")
FIGURES: tuple[tuple[str, str, str, str, str], ...] = (
    ("approval_per_100", 'Approval-shaped and bare "y"/"yes" prompts',
     "prompt history", "per 100 prompts", "window"),
    ("switch_rate", "Switch rate (prompts < 30 min apart)",
     "prompt history", "%", "window"),
    ("three_plus_active", "Prompts with 3+ sessions active",
     "prompt history", "%", "window"),
    ("permission_per_100", "Harness permission prompts",
     "session-state hook", "per 100 prompts", "window"),
    ("owner_wait_median", "Owner waiting on agents, median",
     "session-state hook", "min", "wait"),
    ("owner_wait_p90", "Owner waiting on agents, p90",
     "session-state hook", "min", "wait"),
    ("agent_wait_median", "Agents waiting on owner, median",
     "session-state hook", "min", "wait"),
    ("agent_wait_p90", "Agents waiting on owner, p90",
     "session-state hook", "min", "wait"),
    ("open_prs", "Open PRs awaiting the owner", "GitHub", "PRs", "now"),
    ("issues_opened_closed", "Issues opened : closed", "GitHub", "ratio", "now"),
    ("designs_proposed", "Designs at proposed", "GitHub (design front matter)",
     "designs", "now"),
    ("review_queue", "Human-review queue length", "GitHub", "PRs", "now"),
)  # fmt: skip
FIGURE_IDS = tuple(f[0] for f in FIGURES)

# The counted units, singular for a value of exactly 1. Render-time only: the
# result dict keeps the plural unit.
SINGULAR_UNITS = {"PRs": "PR", "designs": "design"}

# The mirror kind each figure is about, for the include filter (include.py
# maps the kind onto its group).
FIGURE_KINDS: dict[str, "str | None"] = {
    "approval_per_100": "session",
    "switch_rate": "session",
    "three_plus_active": "session",
    "permission_per_100": "session",
    "owner_wait_median": "session",
    "owner_wait_p90": "session",
    "agent_wait_median": "session",
    "agent_wait_p90": "session",
    "open_prs": "pr",
    "issues_opened_closed": "issue",
    "designs_proposed": "design",
    "review_queue": "pr",
}
assert set(FIGURE_KINDS) == set(FIGURE_IDS)

# The caps the fleet instance shows; every other target is the owner's to
# copy in from the reset design's table, and is "observed" until then.
DEFAULT_TARGETS: dict[str, str] = {fid: "observed" for fid in FIGURE_IDS}
DEFAULT_TARGETS["review_queue"] = f"<= {gauges.REVIEW_QUEUE_CAP}"

_OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
}


def unknown_targets(targets: dict[str, str]) -> list[str]:
    """Target ids in config that name no figure (config.load checks the values)."""
    return sorted(set(targets) - set(FIGURE_IDS))


def met(value: "float | None", target: str) -> "bool | None":
    """Whether ``value`` meets ``target``; None when observed or no value."""
    m = config.HEALTH_TARGET_RE.fullmatch(target)
    if value is None or m is None:
        return None
    return _OPS[m.group(1)](value, float(m.group(2)))


# --- reading the mirror -------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _rows(
    conn: sqlite3.Connection, sql: str, lo: datetime, hi: datetime
) -> list[tuple[datetime, tuple]]:
    """``(ts, rest)`` rows with lo <= ts < hi, sorted by ts.

    The string bounds are widened by a day so a timestamp written in a slightly
    different ISO form is never cut by the text comparison; parse_ts decides.
    """
    out = []
    margin = timedelta(days=1)
    for ts, *rest in conn.execute(sql, (_iso(lo - margin), _iso(hi + margin))):
        t = score.parse_ts(ts)
        if t is not None and lo <= t < hi:
            out.append((t, tuple(rest)))
    out.sort(key=lambda r: r[0])
    return out


_HISTORY = (
    "SELECT ts, session_id, host, approval_shaped FROM history_entries"
    " WHERE ts >= ? AND ts < ?"
)
_EVENTS = (
    "SELECT ts, host, session_id, event, notification FROM session_events"
    " WHERE ts >= ? AND ts < ?"
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _per_100(n: int, of: int) -> "float | None":
    return None if of == 0 else round(100 * n / of, 1)


def history_figures(
    conn: sqlite3.Connection, start: datetime, end: datetime
) -> dict[str, "float | None"]:
    rows = _rows(conn, _HISTORY, start - SWITCH_GAP, end)
    inside = [(t, r) for t, r in rows if t >= start]
    approvals = sum(1 for _, r in inside if r[2])
    sessioned = [(t, r[0]) for t, r in rows if r[0] is not None]
    close = switched = active3 = counted = 0
    recent: Counter = Counter()  # session -> prompts in the SWITCH_GAP up to t
    left = 0
    for i, (t, sid) in enumerate(sessioned):
        recent[sid] += 1
        while t - sessioned[left][0] >= SWITCH_GAP:
            recent[sessioned[left][1]] -= 1
            if not recent[sessioned[left][1]]:
                del recent[sessioned[left][1]]
            left += 1
        if t < start:
            continue
        counted += 1
        if i > 0 and t - sessioned[i - 1][0] < SWITCH_GAP:
            close += 1
            switched += sessioned[i - 1][1] != sid
        active3 += len(recent) >= ACTIVE_MIN
    return {
        "approval_per_100": _per_100(approvals, len(inside)),
        "switch_rate": _per_100(switched, close),
        "three_plus_active": _per_100(active3, counted),
    }


def _pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value with q of them at or below."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _minutes(values: list[float], q: "float | None") -> "float | None":
    if not values:
        return None
    v = statistics.median(values) if q is None else _pct(values, q)
    return round(v / 60, 1)


def waits(
    conn: sqlite3.Connection, edges: Sequence[datetime]
) -> list[tuple[list[float], list[float]]]:
    """One ``(owner waits, agent waits)`` pair, in seconds, per window.

    ``edges`` ascend; window ``i`` is ``[edges[i], edges[i + 1])`` and a wait
    belongs to the window its closing event falls in. Waits closing before
    ``edges[0]`` are dropped. One pass pairs per session from every event before
    ``edges[-1]``, so a wait that opened before a window but closed in it is
    counted whole.
    """
    windows: list[tuple[list[float], list[float]]] = [([], []) for _ in edges[1:]]

    def close(t: datetime, opened: datetime, which: int) -> None:
        i = bisect.bisect_right(edges, t) - 1
        if 0 <= i < len(windows):
            windows[i][which].append((t - opened).total_seconds())

    pending_prompt: dict[tuple, datetime] = {}
    pending_stop: dict[tuple, datetime] = {}
    for t, (host, sid, event, _) in _rows(conn, _EVENTS, _EPOCH, edges[-1]):
        if sid is None:
            continue
        key = (host, sid)
        if event == "UserPromptSubmit":
            opened = pending_stop.pop(key, None)
            if opened is not None:
                close(t, opened, 1)
            pending_prompt.setdefault(key, t)
        elif event == "Stop":
            opened = pending_prompt.pop(key, None)
            if opened is not None:
                close(t, opened, 0)
            pending_stop[key] = t
    return windows


def hook_figures(
    conn: sqlite3.Connection,
    start: datetime,
    end: datetime,
    owner: list[float],
    agent: list[float],
) -> dict[str, "float | None"]:
    events = _rows(conn, _EVENTS, start, end)
    prompts = sum(1 for _, r in events if r[2] == "UserPromptSubmit")
    permission = sum(
        1 for _, r in events if r[2] == "Notification" and r[3] == "permission_prompt"
    )
    return {
        "permission_per_100": _per_100(permission, prompts),
        "owner_wait_median": _minutes(owner, None),
        "owner_wait_p90": _minutes(owner, 0.9),
        "agent_wait_median": _minutes(agent, None),
        "agent_wait_p90": _minutes(agent, 0.9),
    }


def hook_days(conn: sqlite3.Connection, end: datetime) -> "float | None":
    """Days from the first hook event to ``end``; None with no hook data."""
    row = conn.execute(
        "SELECT MIN(ts) FROM session_events WHERE ts < ?", (_iso(end),)
    ).fetchone()
    first = score.parse_ts(row[0])
    return None if first is None else round((end - first).total_seconds() / 86400, 1)


def current_figures(
    conn: sqlite3.Connection, *, fleet: bool
) -> dict[str, "float | None"]:
    counts = gauges.review_queue(conn, fleet)
    designs = None
    if counts is not None:  # same "unknown" rule as the queue: fleet off or unsynced
        designs = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE source = ? AND kind = 'design'"
            " AND status_name = 'proposed'",
            (github_fleet.SOURCE,),
        ).fetchone()[0]
    return {
        # A PR someone else opened on the owner's repos awaits the owner too.
        "open_prs": None if counts is None else counts["awaiting"] + counts["others"],
        "issues_opened_closed": None,
        "designs_proposed": designs,
        "review_queue": None if counts is None else counts["queue"],
    }


# --- the one computation both outputs read ------------------------------------


def _note(fid: str, kind: str, baseline: bool) -> "str | None":
    if fid == "issues_opened_closed":
        return "no source yet: the GitHub sync mirrors only issues needing a human"
    if kind == "wait" and baseline:
        return f"baseline: under {BASELINE_DAYS} days of hook data"
    return None


def compute(
    conn: sqlite3.Connection,
    targets: dict[str, str],
    start: datetime,
    end: datetime,
    *,
    fleet: bool,
) -> dict[str, Any]:
    """Every figure for ``[start, end)``, its trend, target and verdict."""
    prev = start - (end - start)
    now_vals = current_figures(conn, fleet=fleet)
    (old_owner, old_agent), (owner, agent) = waits(conn, (prev, start, end))
    cur = {
        **history_figures(conn, start, end),
        **hook_figures(conn, start, end, owner, agent),
    }
    old = {
        **history_figures(conn, prev, start),
        **hook_figures(conn, prev, start, old_owner, old_agent),
    }
    days = hook_days(conn, end)
    baseline = days is None or days < BASELINE_DAYS
    rows = []
    for fid, label, source, unit, kind in FIGURES:
        target = targets.get(fid, DEFAULT_TARGETS[fid])
        value = now_vals[fid] if kind == "now" else cur[fid]
        previous = None if kind == "now" else old[fid]
        trend = (
            None if value is None or previous is None else round(value - previous, 1)
        )
        is_baseline = kind == "wait" and baseline
        rows.append(
            {
                "id": fid,
                "label": label,
                "source": source,
                "unit": unit,
                "window": kind != "now",
                "value": value,
                "previous": previous,
                "trend": trend,
                "target": target,
                "met": None if is_baseline else met(value, target),
                "baseline": is_baseline,
                "note": _note(fid, kind, baseline),
            }
        )
    return {
        "since": _iso(start),
        "until": _iso(end),
        "previous_since": _iso(prev),
        "hook_days": days,
        "unknown_targets": unknown_targets(targets),
        "figures": rows,
    }


def date_window(since: date, until: date) -> tuple[datetime, datetime]:
    """Local midnight at the start of ``since`` to the end of ``until``."""
    return score.local_day_start(since), score.local_day_start(
        until + timedelta(days=1)
    )


# --- rendering ---------------------------------------------------------------

_e = html.escape

CSS = """
.health table { border-collapse:collapse; width:100%; }
.health th, .health td { text-align:left; padding:4px 8px;
  border-bottom:1px solid var(--line); vertical-align:top; }
.health th { font-size:12px; color:var(--muted); font-weight:600; }
.health td.num { white-space:nowrap; }
.health tr.miss td.num b { color:var(--bad); }
.health tr.met td.num b { color:#1a7f37; }
@media (prefers-color-scheme: dark) { .health tr.met td.num b { color:#3fb950; } }
.health .src, .health .why { color:var(--muted); font-size:12px; }
.health .windows a { margin-right:8px; color:var(--accent); }
.health .windows a[aria-current=page] { color:var(--fg); font-weight:600; }
.health .baseline { font-size:11px; text-transform:uppercase; color:var(--muted);
  border:1px solid var(--line); border-radius:4px; padding:0 4px; }
@media (max-width:600px) { .health .src-col { display:none; } }
"""


def _num(value: "float | None") -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def _trend(value: "float | None") -> str:
    if value is None:
        return "—"
    if value == 0:
        return "±0"
    return ("+" if value > 0 else "−") + f"{abs(value):g}"


def _unit(f: dict[str, Any]) -> str:
    """The figure's unit, singular when its value is exactly 1."""
    if f["value"] is None:
        return ""
    unit = SINGULAR_UNITS.get(f["unit"], f["unit"]) if f["value"] == 1 else f["unit"]
    return " " + unit


def render(
    result: dict[str, Any], days: int, inc: "include.Include | None" = None
) -> str:
    """The panel; `inc` leaves out the figures about a group it switched off,
    and the window links carry a filter the URL named, so it survives a
    change of window (a remembered or configured one applies again anyway)."""
    suffix = ""
    if inc is not None and inc.origin == "url":
        suffix = f"&amp;include={_e(','.join(include.canonical(inc.groups)))}"
    windows = "".join(
        f'<a href="/health?days={d}{suffix}"{" aria-current=page" if d == days else ""}>'
        f"{d} days</a>"
        for d in WINDOWS
    )
    body = []
    for f in result["figures"]:
        kind = FIGURE_KINDS[f["id"]]
        if inc is not None and kind is not None and not inc.shows(kind):
            continue
        cls = {True: "met", False: "miss", None: ""}[f["met"]]
        tag = ' <span class="baseline">baseline</span>' if f["baseline"] else ""
        note = f'<div class="why">{_e(f["note"])}</div>' if f["note"] else ""
        unit = _e(_unit(f))
        body.append(
            f'<tr class="{cls}" data-figure="{_e(f["id"])}"'
            f' data-value="{_e(json.dumps(f["value"]))}"'
            f' data-trend="{_e(json.dumps(f["trend"]))}">'
            f"<td>{_e(f['label'])}{tag}{note}</td>"
            f'<td class="num"><b>{_num(f["value"])}</b>{unit}</td>'
            f"<td>{_e(f['target'])}</td>"
            f"<td>{_trend(f['trend']) if f['window'] else 'now'}</td>"
            f'<td class="src-col"><span class="src">{_e(f["source"])}</span></td></tr>'
        )
    hook = result["hook_days"]
    hook_text = "no hook data yet" if hook is None else f"{hook:g} days of hook data"
    unknown = result["unknown_targets"]
    warn = (
        f'<p class="note">Config [health.targets] names no figure:'
        f" {_e(', '.join(unknown))}.</p>"
        if unknown
        else ""
    )
    return (
        f'<style>{CSS}</style><section class="lane health" data-section="health">'
        f"<h2>Health</h2>{warn}"
        f'<p class="windows">Window: {windows}</p>'
        f'<p class="note">{_e(result["since"])} to {_e(result["until"])}, trend'
        f" against the {days} days before. {_e(hook_text)}.</p>"
        "<table><thead><tr><th>Figure</th><th>Value</th><th>Target</th>"
        '<th>Trend</th><th class="src-col">Source</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></section>"
    )


def parse_days(query: dict[str, list[str]]) -> int:
    """The ``days`` query value when it is one of WINDOWS, else the default."""
    raw = (query.get("days") or [""])[0]
    return int(raw) if raw in {str(d) for d in WINDOWS} else DEFAULT_WINDOW


def page(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    days: int,
    *,
    fleet: bool,
    inc: "include.Include | None" = None,
) -> str:
    targets = cfg.get("health", {}).get("targets", {})
    result = compute(conn, targets, now - timedelta(days=days), now, fleet=fleet)
    return render(result, days, inc)


def text_table(result: dict[str, Any]) -> str:
    """The figures as plain text, for ``dashboard.py health`` without --json."""
    lines = [f"health {result['since']} -> {result['until']}"]
    for f in result["figures"]:
        verdict = {True: "met", False: "MISSED", None: ""}[f["met"]]
        extra = " [baseline]" if f["baseline"] else ""
        trend = f"trend {_trend(f['trend'])}" if f["window"] else "current"
        unit = _unit(f)
        note = f" — {f['note']}" if f["note"] else ""
        lines.append(
            f"  {f['label']}{extra}: {_num(f['value'])}{unit}"
            f" (target {f['target']}{', ' + verdict if verdict else ''};"
            f" {trend}){note}"
        )
    return "\n".join(lines) + "\n"
