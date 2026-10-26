"""The header gauges every page shows: side quests and the review queue.

From the fleet view design ("Views", item 5). The two caps limit different
things and both stand: side quests in flight against 3 (the work-in-progress
reset design), PRs waiting on the owner against 5 (the human-review queue's
own cap). Neither is enforced; the gauges only show them.

- **Side quests in flight** are overlay items tagged `side-quest` whose item
  is still in the mirror and not done. A tag on an item that left the mirror
  (a merged PR, a removed worktree) is kept, as every overlay row is, but no
  longer counts, and neither does an ignored one (overlay.hidden).
- **Review queue** is github_fleet's `queue` count over the mirrored
  `source = 'github'` PR rows that are not ignored. `review_queue()` counts
  `review_queue_rows()`, which Needs you lists, and `/status` reads
  `review_queue()`, so the gauge, `/status` and Needs you agree. Before the
  fleet sync step's first successful run, or while `github.fleet` is off, it
  is unknown and shows "—"; after one, a run that found nothing reads 0.
"""

import html
import json
import sqlite3
from dataclasses import dataclass

import github_fleet
import overlay
import score

SIDE_QUEST_CAP = 3
REVIEW_QUEUE_CAP = 5

_e = html.escape

CSS = """
header .gauge { color:var(--muted); }
header .gauge b { color:var(--fg); }
header .gauge.over b { color:var(--bad); }
"""


@dataclass
class Gauges:
    side_quests: int
    review_queue: "int | None"  # None: fleet off, or never synced successfully


def review_queue_rows(conn: sqlite3.Connection, fleet: bool) -> "list[dict] | None":
    """Every mirrored awaiting PR row, PRs from others included, or None when
    unknown.

    Unknown means `fleet` (sync.fleet_enabled) is off, so leftover rows are not
    reported as current, or no fleet sync run has succeeded yet. A row is in
    the queue proper when its `in_queue` is true. review_queue() counts these
    rows and Needs you (needs.py) lists them, so the two always agree.
    """
    if not fleet:
        return None
    if (
        conn.execute(
            "SELECT 1 FROM sync_runs WHERE kind = ? AND outcome = 'ok' LIMIT 1",
            (github_fleet.RUN_KIND,),
        ).fetchone()
        is None
    ):
        return None
    # An ignored PR is out of the queue: Needs leaves it out, so the count does.
    ignored = {
        r[0]
        for r in conn.execute(
            "SELECT item_key FROM ignores WHERE source = ?", (github_fleet.SOURCE,)
        )
    }
    rows = [
        json.loads(r[0])
        for r in conn.execute(
            "SELECT raw_json FROM issues WHERE source = ? AND kind = 'pr'"
            " AND raw_json IS NOT NULL",
            (github_fleet.SOURCE,),
        )
    ]
    return [r for r in rows if str(r.get("key") or r.get("url")) not in ignored]


def review_queue(conn: sqlite3.Connection, fleet: bool) -> "dict[str, int] | None":
    """github_fleet's `queue`, `awaiting` and `others` counts, or None when
    unknown."""
    rows = review_queue_rows(conn, fleet)
    return None if rows is None else github_fleet.queue_counts(rows)


def read(
    conn: sqlite3.Connection, ov: "overlay.Overlay | None" = None, *, fleet: bool
) -> Gauges:
    ov = ov if ov is not None else overlay.load(conn)
    tagged = [item for item, t in ov.kind_tags.items() if t == "side-quest"]
    side_quests = 0
    for source, key in tagged:
        if overlay.hidden(conn, ov.ignored, (source, key)):
            continue
        row = conn.execute(
            "SELECT status_category FROM issues WHERE source = ? AND key = ?",
            (source, key),
        ).fetchone()
        if row is not None and not score.is_done(row[0]):
            side_quests += 1
    counts = review_queue(conn, fleet)
    queue = None if counts is None else counts["queue"]
    return Gauges(side_quests, queue)


def _gauge(name: str, label: str, value: "int | None", cap: int) -> str:
    shown = "—" if value is None else str(value)
    over = " over" if value is not None and value > cap else ""
    title = f"{label}: cap {cap}, shown not enforced"
    if value is None:
        title += "; no data yet"
    return (
        f'<span class="gauge{over}" data-gauge="{name}" title="{_e(title)}">'
        f"{_e(label)} <b>{shown}/{cap}</b></span>"
    )


def render(g: Gauges) -> str:
    return _gauge("side-quests", "Side quests", g.side_quests, SIDE_QUEST_CAP) + _gauge(
        "review-queue", "Review queue", g.review_queue, REVIEW_QUEUE_CAP
    )
