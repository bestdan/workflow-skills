"""The header's review gauge: "Review: 2 ready · 9 waiting".

From the fleet view design ("Views", item 5), which caps the human-review
queue at 5. The cap is not enforced; the gauge only shows it.

- **Ready** is github_fleet's `queue` count over the mirrored
  `source = 'github'` PR rows that are not ignored. `review_queue()` counts
  `review_queue_rows()`, which Needs you lists, and `/status` reads
  `review_queue()`, so the gauge, `/status` and Needs you agree. It links to
  the Pull requests tab's Ready lane and turns red past the cap.
- **Waiting** is the count of that tab's Waiting on you lane
  (fleet_tabs.waiting_count): PRs awaiting the owner that miss a queue rule,
  each card saying which. It links to that lane. The queue's rules rarely all
  hold at once, so a ready count alone reads 0 beside a long list of waiting
  PRs, which looks broken.

Before the fleet sync step's first successful run, or while `github.scope` is not
"owner", both are unknown and show "—"; after one, a run that found nothing reads
0.
"""

import html
import json
import sqlite3
from dataclasses import dataclass

import github_fleet

REVIEW_QUEUE_CAP = 5

_e = html.escape

CSS = """
header .gauge { color:var(--muted); }
header .gauge b { color:var(--fg); }
header .gauge a { color:inherit; text-decoration:none; }
header .gauge a:hover { text-decoration:underline; }
header .gauge.over .ready b { color:var(--bad); }
"""


@dataclass
class Gauges:
    review_queue: "int | None"  # None: fleet off, or never synced successfully
    waiting: "int | None"  # the Waiting on you lane's count; None as above


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


def read(conn: sqlite3.Connection, waiting: "int | None", *, fleet: bool) -> Gauges:
    """The gauge's numbers; `waiting` is fleet_tabs.waiting_count()'s."""
    counts = review_queue(conn, fleet)
    if counts is None:
        return Gauges(None, None)
    return Gauges(counts["queue"], waiting)


def _part(lane: str, value: "int | None") -> str:
    shown = "—" if value is None else str(value)
    inner = f"<b>{shown}</b> {lane}"
    if value is None:
        return f'<span class="{lane}">{inner}</span>'
    return f'<a class="{lane}" href="/prs?lane={lane}">{inner}</a>'


def render(g: Gauges) -> str:
    queue = g.review_queue
    over = " over" if queue is not None and queue > REVIEW_QUEUE_CAP else ""
    title = (
        f"Ready: the review queue, cap {REVIEW_QUEUE_CAP}, shown not enforced."
        " Waiting: PRs awaiting you that miss a queue rule; each card says which."
    )
    if queue is None:
        title += " No data yet."
    return (
        f'<span class="gauge{over}" data-gauge="review-queue" title="{_e(title)}">'
        '<span class="lbl">Review: </span>'
        f"{_part('ready', queue)} · {_part('waiting', g.waiting)}</span>"
    )
