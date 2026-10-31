"""The prioritization score and the blocked test: pure functions, no I/O.

The score is a deterministic sum of named, config-weighted terms, so the
ranking is stable between refreshes and every card can show why it ranks where
it does. There is no LLM here on purpose. score() and is_blocked() below are
the spec; dev_docs/task-dashboard.md places them in the ranker.

fleet_score() is Now's score: what waits on the owner, and the owner's own
work, ranked by category (see the comment above FLEET_CATEGORIES). score()'s
terms add to a Jira ticket's there.

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
# the day, and keeps rising while overdue, capped at 2 (one horizon overdue).
DEADLINE_HORIZON_DAYS = 14.0
DEADLINE_CAP = 2.0

# Stuck term = weight x (days past threshold / threshold), capped here.
STUCK_CAP = 2.0

# The `[score.terms]` weights. score() reads the first four off a Jira issue;
# fleet_score() reads priority (as a prio: label) and age off any item.
TERMS = ("priority", "deadline", "unblocks", "stuck", "age")

Term = tuple[str, float, str]


def local_day_start(d: date) -> datetime:
    """Midnight at the start of `d` in the machine's local timezone, as an
    aware datetime.

    This is the one place a date-only value (a Jira due date, a date-only
    snooze `until`) is turned into an instant. Jira dates and snoozes are the
    user's calendar days, so "until tomorrow" means local midnight, not UTC.
    """
    return datetime(d.year, d.month, d.day).astimezone()


def parse_ts(value: "str | None") -> "datetime | None":
    """A mirror timestamp (`...Z`, UTC) or a bare date (local midnight)."""
    if not value:
        return None
    try:
        if len(value) == 10:
            return local_day_start(date.fromisoformat(value))
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_done(category: "str | None") -> bool:
    return (category or "").lower() == "done"


def _is_blocks(link: dict[str, Any]) -> bool:
    return (link.get("link_type") or "").lower() == "blocks"


def unresolved_blockers(links: list[dict[str, Any]]) -> list[str]:
    """Keys of the issues blocking this one (inward `Blocks`) that are not done.

    A blocker whose status category is unknown (not mirrored, and none inlined
    in the link) counts as not done, so the issue reads as blocked: an unknown
    blocker stays visible rather than being silently dropped.
    """
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
    and `link` (an inward `Blocks` link whose issue is not done, or whose
    status is unknown; see `unresolved_blockers`).
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
            # Calendar days, not elapsed hours over 24: a DST day is 23 or 25
            # hours long, and tomorrow is still one day away on it.
            days = float((d - now.astimezone().date()).days)
            return days, f"due {d.isoformat()}"
    end = parse_ts(issue.get("sprint_end"))
    if end is not None:
        days = (end - now).total_seconds() / 86400
        return days, f"sprint ends {end.astimezone().date().isoformat()}"
    return None


def _round(value: float) -> float:
    return round(value, 2) + 0.0  # + 0.0 turns -0.0 into 0.0


def score(
    issue: dict[str, Any], ctx: dict[str, Any], weights: dict[str, Any]
) -> tuple[float, list[Term]]:
    """`(total, [(term, value, why), ...])`: priority, deadline, unblocks and
    stuck, weighted by `[score.terms]`.

    `ctx` carries what is not on the issue row: `now` (aware datetime),
    `unblocks` (count of open issues this one blocks) and optionally
    `stuck_days` (per-category thresholds). Each value is rounded to 2 places
    and the total is their sum, so the terms on a card add up to the total
    shown.
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
    return _round(math.fsum(v for _, v, _ in terms)), terms


# --- the fleet score ---------------------------------------------------------
#
# Now (fleet_now.py) ranks what waits on the owner, and the owner's own work,
# by this score. Each item belongs to exactly one category, whose
# `[score.categories]` weight is its term; `priority` and `age` (`[score.terms]`)
# add to every item. Now's Ship order puts each category in a fixed tier first
# (fleet_now.TIERS), so this score orders items only within a tier. With the
# defaults (config.DEFAULTS["score"]) the categories sit in FLEET_CATEGORIES
# order, and the terms stay small enough that they reorder items within a
# category, or across two neighbouring ones.

FLEET_CATEGORIES = (
    "quick_win",  # a green-CI bot PR: one click to merge
    "own_approved",  # my PR, approved with CI green: merge it
    "asked",  # a session that asked, or waits on a permission prompt
    "close_out",  # finished work to close out (close_out.py)
    "review_queue",  # a PR in the review queue
    "started",  # my issue at status:3_started
    "others_pr",  # someone else's PR on my repos, a bot's with CI not green
    "human_issue",  # an issue at auto:human-review-needed
    "own_ci_failing",  # my PR, CI failing
    "ready",  # my issue at status:2_ready
    "own_pr",  # my PR out of the queue on something I can do
)
FLEET_TERMS = FLEET_CATEGORIES + ("priority", "age")

# The prio: label as a level, as the gh-issue handler ranks it (prio:0 first),
# on PRIORITY_LEVEL's scale, so prio:0 weighs as Jira's highest.
PRIO_LEVEL = {"prio:0": 4, "prio:1": 3, "prio:2": 2, "prio:3": 1}

# Age term = weight x weeks waiting, capped here, so an old item gains a little
# and never more than AGE_CAP_WEEKS weeks' worth.
AGE_CAP_WEEKS = 5.0


def prio_of(labels: "list[str] | None") -> "str | None":
    """The issue's `prio:` label, the most urgent when it carries several."""
    found = [lb for lb in labels or [] if lb in PRIO_LEVEL]
    return max(found, key=PRIO_LEVEL.__getitem__, default=None)


def fleet_score(
    item: dict[str, Any],
    now: datetime,
    categories: dict[str, Any],
    weights: dict[str, Any],
) -> tuple[float, list[Term]]:
    """`(total, [(term, value, why), ...])`, one entry per name in FLEET_TERMS.

    `item` carries `category` (one of FLEET_CATEGORIES), `why` (what put it in
    that category, shown beside the term), `prio` (a `prio:` label or None)
    and `since` (an aware datetime, or None when the wait's start is unknown).
    `categories` is `[score.categories]` and `weights` is `[score.terms]`.
    As in score(), each value is rounded to 2 places and the total is their
    sum, so the terms on a card add up to the total shown.
    """
    category = item["category"]
    if category not in FLEET_CATEGORIES:
        raise ValueError(f"unknown fleet category: {category!r}")
    terms: list[Term] = []

    def add(name: str, factor: float, why: str) -> None:
        table = categories if name in FLEET_CATEGORIES else weights
        terms.append((name, _round(float(table.get(name, 0)) * factor), why))

    for name in FLEET_CATEGORIES:
        add(name, 1 if name == category else 0, item["why"] if name == category else "")
    prio = item.get("prio")
    add("priority", PRIO_LEVEL.get(prio or "", 0), prio or "no prio: label")
    since: "datetime | None" = item.get("since")
    if since is None:
        add("age", 0, "waiting for an unknown time")
    else:
        days = max(0.0, (now - since).total_seconds() / 86400)
        add("age", min(AGE_CAP_WEEKS, days / 7), f"waiting {days:.0f}d")
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
