"""The header's one freshness indicator and its per-source panel.

The header says "Updated 7m ago": the age of the OLDEST source this instance
reads, so the page never looks fresher than its stalest part. Hovering it, or
clicking it, opens a panel with one row per source:

- each sync step the scheduled sync runs, from its `sync_runs` rows: the
  incremental/reconcile run (Jira, its GitHub PR step, or both, as configured),
  and when enabled the GitHub fleet step, the Workstreams step, the session
  rows and the cloud session list;
- each collector host in `machines`, from its last snapshot.

A row shows its age (since its last success), its last outcome, and its next
run. A sync step's next run is the Syncer's next scheduled run, since every
step runs in each cycle; a host's is its last snapshot plus the collector's
interval, which the server can only expect, not schedule.

A row is a WARNING when its last run failed, when a sync step's last success
is older than STALE_INTERVALS sync intervals, or when a host's last snapshot is
older than MACHINE_STALE_MINUTES. The Workstreams row also names what happened
to Linear when `[linear] enabled = true`, and warns when that read did not
succeed ("no key" included); with Linear off it says nothing about Linear and
never warns for it. Any warning row turns the indicator into a warning. The
failure alerts under the header (server._alert) still name the fix for a
failed run.

A host silent for longer than MACHINE_ASLEEP is ASLEEP: a laptop shut for the
weekend, say. It stays in the panel, greyed, as "asleep since <time>", but it
neither sets the headline age nor warns, so a sleeping machine never pins the
header to a warning.

`sources()` is the one reader: the header renders it and /status returns it.
"""

import html
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import claude_remote
import collector_install
import score
import sessions
import sync

# A sync step whose last success is this many intervals old is stale.
STALE_INTERVALS = 3
# The collector runs every INTERVAL_SECONDS, so a host this far behind has
# missed several runs: asleep, offline, or failing.
MACHINE_STALE_MINUTES = 30
# A host silent this long is asleep: shown, but out of the headline and warning.
MACHINE_ASLEEP = timedelta(hours=24)
COLLECTOR_INTERVAL = timedelta(seconds=collector_install.INTERVAL_SECONDS)

OUTCOME_TEXT = {
    "ok": "ok",
    "failed": "failed",
    "auth_failed": "login expired",
    sync.THROTTLED: "throttled: GitHub budget low, per-PR reads paused",
}

_e = html.escape


@dataclass(frozen=True)
class Source:
    id: str  # `sync`, a fleet step's sync_runs kind, or `machine:<host>`
    label: str
    age_s: "int | None"  # since the last success; None: never succeeded
    last_ok_at: "str | None"
    outcome: "str | None"  # the latest finished run's; None: never ran
    detail: str  # the outcome as text, with an error's first line
    next_text: str
    warn: bool
    asleep: bool = False  # a host past MACHINE_ASLEEP: panel only

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def main_label(cfg: dict[str, Any]) -> str:
    """What the incremental/reconcile run reads, as configured. No mode in
    the name: one label covers incremental, reconcile and full runs."""
    jira = sync.jira_enabled(cfg)
    github = bool(cfg["github"]["enabled"])
    if jira and github:
        return "Jira + GitHub sync"
    if jira or github:
        return "Jira sync" if jira else "GitHub sync"
    return "Sync"  # both off: the run still records a row, reading nothing


def steps(cfg: dict[str, Any]) -> list[tuple[str, tuple[str, ...], str]]:
    """(id, sync_runs kinds, label) of each step the scheduled sync runs, in
    the order it runs them."""
    out: list[tuple[str, tuple[str, ...], str]] = [
        ("sync", sync.MODES, main_label(cfg))
    ]
    if sync.fleet_enabled(cfg):
        out.append((sync.FLEET_KIND, (sync.FLEET_KIND,), "GitHub fleet"))
        out.append((sync.WORKSTREAMS_KIND, (sync.WORKSTREAMS_KIND,), "Workstreams"))
    if sessions.enabled(cfg):
        out.append((sessions.RUN_KIND, (sessions.RUN_KIND,), "Sessions"))
    if cfg["claude_remote"]["enabled"]:
        out.append(
            (claude_remote.RUN_KIND, (claude_remote.RUN_KIND,), "Cloud sessions")
        )
    return out


def step_label(cfg: dict[str, Any], kind: "str | None") -> "str | None":
    """The label of the step a `sync_runs` kind belongs to."""
    for _, kinds, label in steps(cfg):
        if kind in kinds:
            return label
    return kind


def short_age(seconds: "float | None") -> str:
    if seconds is None:
        return "never"
    s = max(0.0, seconds)
    if s < 90:
        return f"{round(s)}s"
    if s < 90 * 60:
        return f"{round(s / 60)}m"
    if s < 36 * 3600:
        return f"{round(s / 3600)}h"
    return f"{round(s / 86400)}d"


def _until(at: "datetime | None", now: datetime, *, expected: bool) -> str:
    if at is None:
        return "—"
    s = (at - now).total_seconds()
    if s <= 0:
        return "overdue" if expected else "due now"
    return f"{'expected ' if expected else ''}in {short_age(s)}"


def _first_line(text: "str | None") -> str:
    lines = (text or "").strip().splitlines()
    return lines[0] if lines else ""


def _linear(cfg: dict[str, Any], counts: "str | None") -> "tuple[str, bool]":
    """The Workstreams run's Linear outcome (its `counts.linear`) as panel
    text, and whether it warns. Nothing, and no warning, with Linear off:
    then the step makes no Linear call, whatever an older run recorded."""
    if not cfg["linear"]["enabled"]:
        return "", False
    try:
        linear = (json.loads(counts or "{}") or {}).get("linear")
    except (ValueError, TypeError, AttributeError):
        linear = None
    if not isinstance(linear, str):
        return "", False  # a run that failed before reading Linear
    return f"Linear: {linear}", linear not in ("ok", "off")


def _step_source(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    sid: str,
    kinds: tuple[str, ...],
    label: str,
    now: datetime,
    interval_s: float,
    state: dict[str, Any],
) -> Source:
    marks = ", ".join("?" * len(kinds))
    ok_marks = ", ".join("?" * len(sync.SUCCESS_OUTCOMES))
    ok = conn.execute(
        f"SELECT finished_at FROM sync_runs WHERE outcome IN ({ok_marks})"
        f" AND kind IN ({marks}) ORDER BY finished_at DESC, id DESC LIMIT 1",
        (*sync.SUCCESS_OUTCOMES, *kinds),
    ).fetchone()
    last = conn.execute(
        f"SELECT outcome, error, counts FROM sync_runs WHERE finished_at IS NOT NULL"
        f" AND kind IN ({marks}) ORDER BY id DESC LIMIT 1",
        kinds,
    ).fetchone()
    ok_at = score.parse_ts(ok[0]) if ok else None
    age = None if ok_at is None else max(0, int((now - ok_at).total_seconds()))
    outcome = last[0] if last else None
    detail = OUTCOME_TEXT.get(outcome or "", outcome or "never run")
    if outcome not in (None, *sync.SUCCESS_OUTCOMES) and _first_line(last[1]):
        detail += f": {_first_line(last[1])}"
    linear, linear_warn = ("", False)
    if sid == sync.WORKSTREAMS_KIND and last:
        linear, linear_warn = _linear(cfg, last[2])
    if linear:
        detail += f"; {linear}"
        # A Linear failure leaves the run ok, with Linear's reason as its error.
        if linear_warn and outcome == "ok" and _first_line(last[1]):
            detail += f": {_first_line(last[1])}"
    if state.get("running"):
        nxt = "running now" if state.get("step") in kinds else "after this run"
    else:
        # Relative, from the Syncer's own clock: `now` may be a test's.
        due = state.get("next_run_in_s")
        nxt = (
            "—"
            if due is None
            else _until(now + timedelta(seconds=due), now, expected=False)
        )
    failed = outcome in ("failed", "auth_failed")
    stale = age is not None and age > STALE_INTERVALS * interval_s
    return Source(
        sid, label, age, ok[0] if ok else None, outcome, detail, nxt,
        failed or stale or linear_warn,
    )  # fmt: skip


def _machine_sources(conn: sqlite3.Connection, now: datetime) -> list[Source]:
    out = []
    for host, taken, counts in conn.execute(
        "SELECT host, last_snapshot_at, counts FROM machines ORDER BY host"
    ):
        at = score.parse_ts(taken)
        age = None if at is None else max(0, int((now - at).total_seconds()))
        # snapshot_adapter keeps how many repo scans failed, as a count.
        try:
            failed = int((json.loads(counts or "{}") or {}).get("scan_errors") or 0)
        except (ValueError, TypeError, AttributeError):
            failed = 0
        detail = "ok"
        if failed:
            detail = f"{failed} repo scan{'s' if failed > 1 else ''} failed"
        nxt = _until(
            None if at is None else at + COLLECTOR_INTERVAL, now, expected=True
        )
        asleep = at is not None and now - at > MACHINE_ASLEEP
        if at is not None and asleep:
            detail = f"asleep since {at.strftime('%Y-%m-%d %H:%M')} UTC"
            nxt = "—"
        stale = not asleep and (age is None or age > MACHINE_STALE_MINUTES * 60)
        out.append(
            Source(
                f"machine:{host}",
                str(host),
                age,
                taken,
                "ok",
                detail,
                nxt,
                stale,
                asleep,
            )
        )
    return out


def sources(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    interval_s: float,
    state: dict[str, Any],
) -> list[Source]:
    """Every source the indicator covers; `state` is Syncer.state()."""
    out = [
        _step_source(conn, cfg, sid, kinds, label, now, interval_s, state)
        for sid, kinds, label in steps(cfg)
    ]
    return out + _machine_sources(conn, now)


@dataclass(frozen=True)
class Summary:
    text: str  # "Updated 7m ago", or "never synced"
    age_s: "int | None"
    warn: bool


def summary(found: list[Source]) -> Summary:
    """The oldest age, and whether any source warns. A source that has never
    succeeded is the oldest of all, so the page reads "never synced". An
    asleep host counts for neither."""
    found = [s for s in found if not s.asleep]
    warn = any(s.warn for s in found)
    if not found or any(s.age_s is None for s in found):
        return Summary("never synced", None, warn)
    oldest = max(s.age_s for s in found if s.age_s is not None)
    return Summary(f"Updated {short_age(oldest)} ago", oldest, warn)


def as_json(found: list[Source], stale: bool = False) -> dict[str, Any]:
    """The indicator for /status; `stale` warns as it does in render()."""
    s = summary(found)
    return {
        "text": s.text,
        "age_seconds": s.age_s,
        "warn": s.warn or stale,
        "sources": [x.as_dict() for x in found],
    }


CSS = """
header .fresh { position:relative; }
header .fresh-btn { border:0; background:none; padding:0; color:var(--muted);
  font:inherit; cursor:pointer; }
header .fresh.warn .fresh-btn, header.stale .fresh-btn { color:var(--bad);
  font-weight:600; }
header .fresh-panel { display:none; position:absolute; top:100%; left:0; z-index:10;
  margin-top:4px; padding:6px 8px; background:var(--bg); color:var(--fg);
  border:1px solid var(--line); border-radius:6px; box-shadow:0 4px 12px #0003;
  font-size:12px; font-weight:normal; width:max-content; max-width:calc(100vw - 32px);
  overflow-x:auto; }
@media (hover:hover) { header .fresh:hover .fresh-panel { display:block; } }
header .fresh.open .fresh-panel { display:block; }
header .fresh-panel table { border-collapse:collapse; }
header .fresh-panel th, header .fresh-panel td { text-align:left; padding:2px 6px;
  white-space:nowrap; }
header .fresh-panel th { color:var(--muted); font-weight:600; }
header .fresh-panel tr.warn td { color:var(--bad); }
header .fresh-panel tr.asleep td { color:var(--muted); opacity:.7; }
header .sync-note, header .new-data { font-size:12px; color:var(--muted); }
header .new-data a { color:var(--accent); }
"""


def render(found: list[Source], stale: bool = False) -> str:
    """The indicator and its panel. `stale` (server.sync_health's) also warns."""
    s = summary(found)
    warn = s.warn or stale
    mark = "⚠ " if warn else ""
    rows = []
    for x in found:
        cls = ' class="warn"' if x.warn else ' class="asleep"' if x.asleep else ""
        rows.append(
            f'<tr data-source="{_e(x.id)}" data-outcome="{_e(x.outcome or "")}"{cls}>'
            f"<td>{_e(x.label)}</td>"
            f'<td title="{_e(x.last_ok_at or "")}">{_e(short_age(x.age_s))}</td>'
            f"<td>{_e(x.detail)}</td><td>{_e(x.next_text)}</td></tr>"
        )
    age = "" if s.age_s is None else s.age_s
    # The button says "7m ago"; its tooltip keeps the whole "Updated 7m ago".
    text = _e(s.text.removeprefix("Updated "))
    return (
        f'<span class="fresh{" warn" if warn else ""}" data-age="{age}">'
        '<button type="button" class="fresh-btn" aria-expanded="false"'
        f' aria-controls="fresh-panel" title="{_e(s.text)}: where each part of this'
        f' page comes from">{mark}{text}</button>'
        '<div class="fresh-panel" id="fresh-panel" role="dialog"'
        ' aria-label="Sources"><table><thead><tr><th>Source</th><th>Age</th>'
        f"<th>Last run</th><th>Next run</th></tr></thead><tbody>{''.join(rows)}</tbody>"
        "</table></div></span>"
    )
