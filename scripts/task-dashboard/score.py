"""The prioritization score and the blocked test: pure functions, no I/O.

The score is a deterministic sum of named, config-weighted terms, so the
ranking is stable between refreshes and every card can show why it ranks where
it does. There is no LLM here on purpose. The spec is the Prioritization score
and Blocked paragraphs of dev_docs/designs/2026-10-23-task-dashboard.md.

`issue` is a `jira_issues` row as a dict. `links` are that issue's `links` rows,
each carrying the linked issue's *effective* status category: the mirror's own
row when the linked issue is mirrored, else the category Jira inlined in the
link. views.py does that join in SQL; this module only reads the result.
"""

import json
import math
from datetime import date, datetime, timezone
from typing import Any

# Jira priority names onto the urgent/high/medium/low domain kanban-classify
# uses. Both the current scheme (Highest..Lowest) and the older one
# (Blocker..Trivial) are mapped; an unknown name maps to None.
JIRA_PRIORITY = {
    "highest": "urgent",
    "blocker": "urgent",
    "critical": "urgent",
    "high": "high",
    "medium": "medium",
    "major": "medium",
    "low": "low",
    "lowest": "low",
    "minor": "low",
    "trivial": "low",
}

# Priority term = weight x level. Unknown or unset priority scores 0.
PRIORITY_LEVEL = {"urgent": 4, "high": 3, "medium": 2, "low": 1}

# Deadline pressure rises linearly from 0 at DEADLINE_HORIZON_DAYS out to 1 on
# the day, and keeps rising while overdue, capped at 2 (two horizons late).
DEADLINE_HORIZON_DAYS = 14.0
DEADLINE_CAP = 2.0

# Stuck term = weight x (days past threshold / threshold), capped here.
STUCK_CAP = 2.0

TERMS = ("priority", "deadline", "unblocks", "stuck", "review", "blocked")

Term = tuple[str, float, str]


def parse_ts(value: "str | None") -> "datetime | None":
    """A mirror timestamp (`...Z`, UTC) or a bare date (midnight UTC)."""
    if not value:
        return None
    try:
        if len(value) == 10:
            return datetime.combine(
                date.fromisoformat(value), datetime.min.time(), timezone.utc
            )
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_done(category: "str | None") -> bool:
    return (category or "").lower() == "done"


def _is_blocks(link: dict[str, Any]) -> bool:
    return (link.get("link_type") or "").lower() == "blocks"


def unresolved_blockers(links: list[dict[str, Any]]) -> list[str]:
    """Keys of the issues blocking this one (inward `Blocks`) that are not done."""
    return [
        link["linked_key"]
        for link in links
        if _is_blocks(link)
        and link.get("direction") == "inward"
        and not is_done(link.get("linked_status_category"))
    ]


def open_blocked(links: list[dict[str, Any]]) -> list[str]:
    """Keys of the open issues this one blocks (outward `Blocks`)."""
    return [
        link["linked_key"]
        for link in links
        if _is_blocks(link)
        and link.get("direction") == "outward"
        and not is_done(link.get("linked_status_category"))
    ]


def labels_of(issue: dict[str, Any]) -> list[str]:
    raw = issue.get("labels")
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


def is_blocked(
    issue: dict[str, Any], links: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[bool, list[str]]:
    """Blocked by any of four signals; returns the ones that fired, in order.

    `label` (the `blocked` label /list-tasks uses), `flagged` (Jira's Flagged
    field), `status` (a status named in `jira.blocked_statuses`, case aside)
    and `link` (an inward `Blocks` link whose issue is not done).
    """
    signals = []
    if any(label.lower() == "blocked" for label in labels_of(issue)):
        signals.append("label")
    if issue.get("flagged"):
        signals.append("flagged")
    statuses = {
        str(s).lower() for s in config.get("jira", {}).get("blocked_statuses", [])
    }
    if (issue.get("status_name") or "").lower() in statuses:
        signals.append("status")
    if unresolved_blockers(links):
        signals.append("link")
    return bool(signals), signals


def _days_to_deadline(
    issue: dict[str, Any], now: datetime
) -> "tuple[float, str] | None":
    due = issue.get("due_date")
    if due:
        try:
            d = date.fromisoformat(str(due)[:10])
        except ValueError:
            d = None
        if d is not None:
            return float((d - now.date()).days), f"due {d.isoformat()}"
    end = parse_ts(issue.get("sprint_end"))
    if end is not None:
        days = (end - now).total_seconds() / 86400
        return days, f"sprint ends {end.date().isoformat()}"
    return None


def _round(value: float) -> float:
    return round(value, 2) + 0.0  # + 0.0 turns -0.0 into 0.0


def score(
    issue: dict[str, Any], ctx: dict[str, Any], weights: dict[str, Any]
) -> tuple[float, list[Term]]:
    """`(total, [(term, value, why), ...])`, one entry per name in TERMS.

    `ctx` carries what is not on the issue row: `now` (aware datetime),
    `unblocks` (count of open issues this one blocks), `prs` (the joined `prs`
    rows), `blocked` (the `is_blocked` result) and optionally `stuck_days`
    (per-category thresholds). Each value is rounded to 2 places and the total
    is their sum, so the terms on a card add up to the total shown.
    """
    now: datetime = ctx["now"]
    terms: list[Term] = []

    def add(name: str, factor: float, why: str) -> None:
        terms.append((name, _round(float(weights.get(name, 0)) * factor), why))

    level = JIRA_PRIORITY.get((issue.get("priority") or "").lower())
    add(
        "priority",
        PRIORITY_LEVEL.get(level or "", 0),
        f"{issue.get('priority')} ({level})" if level else "no priority",
    )

    deadline = _days_to_deadline(issue, now)
    if deadline is None:
        add("deadline", 0, "no due date or sprint end")
    else:
        days, what = deadline
        pressure = max(0.0, min(DEADLINE_CAP, 1 - days / DEADLINE_HORIZON_DAYS))
        when = f"{-days:.0f}d overdue" if days < 0 else f"in {days:.0f}d"
        add("deadline", pressure, f"{what}, {when}")

    count = int(ctx.get("unblocks", 0))
    add("unblocks", count, f"blocks {count} open issue{'s' if count != 1 else ''}")

    add(*_stuck(issue, now, ctx.get("stuck_days") or {}))

    ready = [
        p for p in ctx.get("prs", [])
        if p.get("role") == "author"
        and (p.get("review_decision") == "APPROVED" or p.get("ci_state") == "success")
    ]  # fmt: skip
    if ready:
        p = ready[0]
        state = "approved" if p.get("review_decision") == "APPROVED" else "green"
        add("review", 1, f"PR #{p.get('number')} is {state}")
    else:
        add("review", 0, "no PR of mine approved or green")

    blocked, signals = ctx.get("blocked") or (False, [])
    add(
        "blocked", 1 if blocked else 0, ", ".join(signals) if blocked else "not blocked"
    )

    return _round(math.fsum(v for _, v, _ in terms)), terms


def _stuck(
    issue: dict[str, Any], now: datetime, thresholds: dict[str, Any]
) -> tuple[str, float, str]:
    category = (issue.get("status_category") or "").lower()
    threshold = thresholds.get(category)
    since = parse_ts(issue.get("status_category_changed"))
    if not threshold or since is None:
        return "stuck", 0, "no threshold for this category"
    days = (now - since).total_seconds() / 86400
    over = days - float(threshold)
    if over <= 0:
        return "stuck", 0, f"{days:.0f}d in category (threshold {threshold}d)"
    factor = min(STUCK_CAP, over / float(threshold))
    status = issue.get("status_name") or category
    return "stuck", factor, f"{status} {days:.0f}d in category (threshold {threshold}d)"
