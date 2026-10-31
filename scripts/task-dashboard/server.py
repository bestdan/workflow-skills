"""The dashboard's HTTP server and its sync thread: one process, stdlib only.

The server reads SQLite and nothing else, so a page never waits on acli or gh.
Syncs run on one background thread that the interval and the Sync now button
both wake. That keeps one sync path and one writer to the mirror tables. The
one exception is a card's Refresh button: `POST /refresh` re-reads that one
PR or issue (refresh.py) on its own request thread and rewrites only its rows.

Access control, from the Hosting and access section of
dev_docs/designs/2026-10-23-task-dashboard.md:

- The socket binds to 127.0.0.1 only.
- Every request's `Host` must be exactly `127.0.0.1:<port>`, `localhost:<port>`
  or an entry of `server.extra_allowed_hosts`, else 403. Loopback binding alone
  does not stop DNS rebinding: a site could point its own name at 127.0.0.1 and
  read pages full of work Jira data. The Host check is what stops that.
- Every POST also needs an `Origin` naming the same allowed host, and the
  per-install token from `meta`, which the page embeds. The token holds only
  because the Host check stops another origin from reading the page.
  `POST /refresh` takes exactly the checks `POST /overlay/*` does.
- `POST /ingest` (fleet view) exists only when `[ingest.machines]` is set. It
  passes the same Host check, then a per-machine bearer token instead of the
  Origin and page token; ingest.py has the rest.

`extra_allowed_hosts` entries match the `Host` header exactly (case aside), port
included. Tailscale Serve on port P proxies to 127.0.0.1:P; a refused `Host` is
logged to stderr, so the header Serve actually forwards can be read off the log
and copied into config rather than guessed.
"""

import hmac
import html
import importlib.util
import json
import secrets
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import db
import fleet_now
import fleet_tabs
import fleet_views
import freshness
import gauges
import github_gh
import health
import icons
import include
import ingest
import lane_tabs
import needs
import overlay
import refresh
import score
import sync
import view_model
import views
import whynot

BIND = "127.0.0.1"
TOKEN_KEY = "server_token"
STALE_INTERVALS = freshness.STALE_INTERVALS
MAX_BODY = 4096
SYNC_MODES = ("incremental", "reconcile")
# Pages folded into another, kept as 302s to `/` so old bookmarks still land:
# Since you were away is now Needs you's new badges and Finished group.
RETIRED_ROUTES = ("/away",)

# scripts/task-dashboard/server.py -> the plugin root, two directories up.
KANBAN_PATH = (
    Path(__file__).resolve().parents[2]
    / "commands"
    / "handlers"
    / "assets"
    / "kanban-classify.py"
)

SECTION_TITLES = {
    "new": "New",
    "needs_refinement": "Needs refinement",
    "ready": "Ready",
    "in_progress": "In progress",
    "blocked": "Blocked",
    "needs_review": "Needs review",
    "done": "Done",
}

# Jira priority names onto kanban-classify's jira domain; score.py owns it.
JIRA_PRIORITY = score.JIRA_PRIORITY


# --- sync thread -------------------------------------------------------------


class Syncer:
    """The sync thread: interval loop, single flight, a queued reconcile.

    `sync_fn(mode)` runs one sync. A request while idle wakes the loop; a
    request while a run is in flight joins it, except a reconcile asked for
    during an incremental run, which is queued and runs right after it (joining
    would leave a reassigned issue on screen). Every finished run, manual or
    scheduled, restarts the interval, so a scheduled run never fires seconds
    after a manual one. A scheduled run is a reconcile once `reconcile_s` has
    passed since the last one, otherwise incremental.

    The state also says who started the run in flight (`trigger`: `manual`
    for a request, `scheduled` for the interval), which step of it is running
    (`step`, set through `step()` by the sync function) and since when, and
    when the next scheduled run is due. The page reads `trigger` so that only
    a sync someone asked for relabels the Sync button.
    """

    def __init__(
        self,
        sync_fn: Callable[[str], None],
        interval_s: float,
        reconcile_s: "float | None" = None,
        *,
        run_at_start: bool = True,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._sync_fn = sync_fn
        self.interval_s = interval_s
        self.reconcile_s = reconcile_s
        self._clock = clock
        self._wall = wall
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._run_lock = threading.Lock()  # one sync at a time
        self._state = threading.Lock()
        self._changed = threading.Condition(self._state)
        self._requested: "str | None" = None
        self._pending_reconcile = False
        self._running: "str | None" = None
        self._trigger: "str | None" = None
        self._started_at: "datetime | None" = None
        self._step: "str | None" = None
        self._step_started_at: "datetime | None" = None
        now = clock()
        self.next_due = now if run_at_start else now + interval_s
        self._last_reconcile = now
        self._thread = threading.Thread(target=self._loop, name="sync", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout)

    def request(self, mode: str) -> dict[str, Any]:
        """Ask for a run; returns the state after the request is recorded."""
        if mode not in SYNC_MODES:
            raise ValueError(f"unknown sync mode: {mode!r}")
        with self._state:
            if self._running is not None:
                if mode == "reconcile" and self._running != "reconcile":
                    self._pending_reconcile = True
            elif self._requested != "reconcile":
                self._requested = mode
            if self._running is None:
                self._wake.set()
            return self._snapshot()

    def state(self) -> dict[str, Any]:
        with self._state:
            return self._snapshot()

    def step(self, kind: str) -> None:
        """Record the step of the run in flight now starting (a `sync_runs`
        kind); the sync function calls it before each step."""
        with self._state:
            if self._running is not None:
                self._step = kind
                self._step_started_at = self._wall()

    def wait_idle(self, timeout: float) -> bool:
        """True once nothing is running, queued or pending (for tests and CLI).

        The timeout is real time, not the injected clock: a frozen test clock
        would otherwise never let it expire.
        """
        deadline = time.monotonic() + timeout
        with self._state:
            while self._busy():
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._changed.wait(left)
            return True

    def _busy(self) -> bool:
        return (
            self._running is not None
            or self._requested is not None
            or self._pending_reconcile
        )

    def _snapshot(self) -> dict[str, Any]:
        wall = self._wall()
        running = self._running is not None
        next_at = next_in = None
        if not running:
            next_in = max(0, int(self.next_due - self._clock()))
            next_at = _iso(wall + timedelta(seconds=next_in))
        return {
            "running": running,
            "mode": self._running,
            "queued": self._requested,
            "pending_reconcile": self._pending_reconcile,
            "busy": self._busy(),
            "trigger": self._trigger,
            "started_at": _iso(self._started_at),
            "step": self._step,
            "step_started_at": _iso(self._step_started_at),
            "step_elapsed_s": (
                None
                if self._step_started_at is None
                else max(0, int((wall - self._step_started_at).total_seconds()))
            ),
            "next_run_at": next_at,
            "next_run_in_s": next_in,
        }

    def _take(self) -> "str | None":
        with self._state:
            trigger = "manual"
            if self._pending_reconcile:
                self._pending_reconcile = False
                mode = "reconcile"
            elif self._requested is not None:
                mode, self._requested = self._requested, None
            elif self._clock() >= self.next_due:
                trigger = "scheduled"
                due = (
                    self.reconcile_s is not None
                    and self._clock() - self._last_reconcile >= self.reconcile_s
                )
                mode = "reconcile" if due else "incremental"
            else:
                return None
            self._running = mode
            self._trigger = trigger
            self._started_at = self._step_started_at = self._wall()
            self._step = mode
            self._changed.notify_all()
            return mode

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(max(0.0, self.next_due - self._clock()))
            self._wake.clear()
            if self._stop.is_set():
                break
            mode = self._take()
            if mode is not None:
                self._run(mode)

    def _run(self, mode: str) -> None:
        try:
            with self._run_lock:
                self._sync_fn(mode)
        except Exception:  # the thread must outlive one bad run
            traceback.print_exc(file=sys.stderr)
        finally:
            with self._state:
                if mode == "reconcile":
                    self._last_reconcile = self._clock()
                self._running = None
                self._trigger = self._started_at = None
                self._step = self._step_started_at = None
                self.next_due = self._clock() + self.interval_s
                if self._pending_reconcile:
                    self._wake.set()
                self._changed.notify_all()


def _iso(dt: "datetime | None") -> "str | None":
    return None if dt is None else dt.astimezone(timezone.utc).strftime(_TS)


_TS = "%Y-%m-%dT%H:%M:%SZ"


def make_sync_fn(
    cfg: dict[str, Any],
    db_path: Path,
    on_step: "Callable[[str], None] | None" = None,
) -> Callable[[str], None]:
    """The real sync: a fresh connection per run, on the sync thread.
    `on_step` hears each step's `sync_runs` kind as it starts."""

    def run(mode: str) -> None:
        conn = db.connect(db_path)
        try:
            rows = sync.run_all(
                mode, cfg, conn, datetime.now(timezone.utc), on_step=on_step
            )
        finally:
            conn.close()
        for row in rows:
            print(f"sync {row['kind']}: {row['outcome']}", file=sys.stderr)

    return run


# --- data --------------------------------------------------------------------


def ensure_token(conn: sqlite3.Connection) -> str:
    """The per-install POST token, generated once and kept in `meta`."""
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
        (TOKEN_KEY, secrets.token_urlsafe(32)),
    )
    conn.commit()
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (TOKEN_KEY,)).fetchone()
    return str(row[0])


def _parse_ts(value: "str | None") -> "datetime | None":
    if not value:
        return None
    try:
        return datetime.strptime(value, _TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def sync_health(
    conn: sqlite3.Connection, interval_s: float, now: datetime
) -> dict[str, Any]:
    """Last successful sync, its age, staleness, and the latest finished run.

    All of it is the Jira sync's: no fleet step's run (GitHub, sessions) may
    make a failing Jira sync look fresh, so only the Jira modes count. The
    latest finished GitHub fleet run is reported beside it as `fleet_run`.
    """
    jira_kinds = f"kind IN ({', '.join('?' * len(sync.MODES))})"
    ok = conn.execute(
        f"SELECT finished_at FROM sync_runs WHERE outcome = 'ok' AND {jira_kinds}"
        " ORDER BY finished_at DESC, id DESC LIMIT 1",
        sync.MODES,
    ).fetchone()
    last = conn.execute(
        "SELECT kind, outcome, error, finished_at FROM sync_runs"
        f" WHERE finished_at IS NOT NULL AND {jira_kinds} ORDER BY id DESC LIMIT 1",
        sync.MODES,
    ).fetchone()
    fleet = conn.execute(
        "SELECT kind, outcome, error, finished_at FROM sync_runs"
        " WHERE finished_at IS NOT NULL AND kind = ? ORDER BY id DESC LIMIT 1",
        (sync.FLEET_KIND,),
    ).fetchone()
    # Any step's finish, so a page can tell that data newer than its own
    # landed (the header's "New data — reload").
    (finished,) = conn.execute("SELECT MAX(finished_at) FROM sync_runs").fetchone()
    ok_at = _parse_ts(ok[0]) if ok else None
    age = (now - ok_at).total_seconds() if ok_at else None
    return {
        "last_ok_at": ok[0] if ok else None,
        "age_seconds": None if age is None else max(0, int(age)),
        "stale": age is None or age > STALE_INTERVALS * interval_s,
        "last_run": _run_dict(last),
        "fleet_run": _run_dict(fleet),
        "last_finished_at": finished,
    }


def _run_dict(row: "tuple | None") -> "dict[str, Any] | None":
    if row is None:
        return None
    return {"kind": row[0], "outcome": row[1], "error": row[2], "finished_at": row[3]}


@lru_cache(maxsize=1)
def kanban() -> ModuleType:
    """kanban-classify.py, loaded by path so the Board matches /list-tasks."""
    spec = importlib.util.spec_from_file_location("kanban_classify", KANBAN_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {KANBAN_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def board_rows(conn: sqlite3.Connection, me: str = "") -> list[dict[str, Any]]:
    """Active-sprint Jira issues as kanban-classify `--tracker jira` rows.

    Every issue in a configured board's active sprint, plus, from any other
    team's active sprint, only the issues `me` is assigned to or reported.
    """
    cur = conn.execute(
        "SELECT key, summary, status_name, status_category, assignee, priority,"
        " labels, created, sprint_name, flagged, raw_json, parent_key FROM jira_issues"
        # The sprint field keeps the active sprint of any board; `sprints`
        # holds only the configured boards' active sprints.
        " WHERE sprint_state = 'active' AND (sprint_id IN (SELECT id FROM sprints)"
        "  OR (? != '' AND (lower(assignee) = lower(?) OR lower(reporter) = lower(?))))"
        " ORDER BY key",
        (me, me, me),
    )
    rows = []
    for (
        key, summary, status, category, assignee, priority, labels, created, sp,
        flagged, raw_json, parent_key,
    ) in cur:  # fmt: skip
        try:
            label_list = json.loads(labels or "[]")
        except ValueError:
            label_list = []
        rows.append(
            {
                "id": key,
                "category": category,
                "labels": label_list if isinstance(label_list, list) else [],
                "priority": JIRA_PRIORITY.get((priority or "").lower()),
                "sort_date": created,
                "summary": summary,
                "status_name": status,
                "assignee": assignee,
                "priority_name": priority,
                "sprint_name": sp,
                "flagged": bool(flagged),
                "epic": views.epic_name({"raw_json": raw_json}),
                "parent_key": parent_key,
            }
        )
    return rows


def classify_board(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The classifier's own `main()` loop, over rows rather than stdin."""
    k = kanban()
    sections: dict[str, list] = {name: [] for name in k.SECTION_ORDER}
    unmatched = []
    for row in rows:
        section = k.classify(row, "jira")
        if section is None:
            unmatched.append(row)
            continue
        sections[section].append(row)
    for name in k.SECTION_ORDER:
        sections[name].sort(key=lambda r: k._sort_key(r, "jira"))
    return {"sections": sections, "unmatched": unmatched}


# --- rendering ---------------------------------------------------------------

_e = html.escape

CSS = """
:root { --fg:#1f2328; --muted:#59636e; --bg:#fff; --card:#f6f8fa; --line:#d1d9e0;
  --bad:#cf222e; --badbg:#ffebe9; --accent:#0969da; }
@media (prefers-color-scheme: dark) { :root { --fg:#e6edf3; --muted:#9198a1;
  --bg:#0d1117; --card:#161b22; --line:#3d444d; --bad:#ff7b72; --badbg:#3c1618;
  --accent:#4493f8; } }
* { box-sizing: border-box; }
body { margin:0; font:14px/1.45 -apple-system,system-ui,sans-serif; color:var(--fg);
  background:var(--bg); }
header { padding:10px 16px; border-bottom:1px solid var(--line); }
header .bar { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; }
header .bar > * { white-space:nowrap; }
header .sync-note { margin-left:-10px; }
header h1 { font-size:16px; margin:0; }
header .alert { margin-top:8px; padding:6px 10px; border-radius:6px;
  background:var(--badbg); color:var(--bad); }
header #filter { margin-left:auto; }
@media (max-width:600px) {
  /* Two lines: what is fresh, then the filter with the buttons after it,
     so the mode and sync buttons never fall off the edge. */
  header .bar { gap:8px; }
  header .sync-note { margin-left:0; }
  header h1, header .lbl { display:none; }
  header #filter { flex:1 1 8em; min-width:0; margin-left:0; }
}
header button { font:inherit; padding:4px 12px; border-radius:6px;
  border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; }
header #sync { display:inline-flex; align-items:center; gap:6px; padding:5px 8px; }
header #mode { display:inline-flex; align-items:center; gap:6px; }
@keyframes spin { to { transform: rotate(360deg); } }
header #sync:disabled { cursor:default; }
header #sync .glyph { display:block; }
@media (prefers-reduced-motion: no-preference) {
  header #sync[aria-label^="Syncing"] .glyph { animation:spin 1.2s linear infinite; }
}
main { padding:12px 16px; }
.lane { margin-bottom:16px; }
.lane h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em;
  color:var(--muted); margin:0 0 6px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:6px;
  padding:6px 10px; margin-bottom:6px; }
.card a { color:var(--accent); font-weight:600; text-decoration:none; }
.card .meta { color:var(--muted); font-size:12px; }
.card .ci-bad { color:var(--bad); font-weight:600; }
.empty { color:var(--muted); }
"""

# The header's sync controls. The page polls /status (every BUSY_POLL_MS while
# a sync is in flight, IDLE_POLL_MS otherwise, and not while the tab is
# hidden). A sync in flight shows as a note beside the freshness indicator,
# naming the running step and its elapsed seconds. Only a sync someone asked
# for (trigger `manual`, this page's click or any other) relabels and
# disables the button; a scheduled one leaves it reading "Sync now". When any
# step finishes after the page was drawn, the page offers "New data — reload"
# and never reloads itself, so an open note editor or a typed filter is never
# thrown away.
JS = """
(function () {
  var BUSY_POLL_MS = 1500, IDLE_POLL_MS = 15000;
  var btn = document.getElementById('sync');
  var note = document.getElementById('sync-note');
  var fresh = document.getElementById('new-data');
  var token = document.querySelector('meta[name=dashboard-token]').content;
  var drawn = document.body.dataset.synced || '';
  var asked = false, timer = null;
  function esc(t) {
    return String(t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  var glyph = btn.querySelector('.glyph').outerHTML, drawn_html = btn.innerHTML;
  // The icon's name and tooltip; a failure also shows beside the icon. The
  // icon is redrawn only when that text changes, so a poll during a sync
  // does not restart its spin.
  function label(t, failed) {
    btn.setAttribute('aria-label', t);
    btn.title = t + ' (shift-click for a full reconcile)';
    var html = glyph + (failed ? ' ' + esc(t) : '');
    if (html !== drawn_html) { btn.innerHTML = html; drawn_html = html; }
  }
  function show(s) {
    if (s.busy && (asked || s.trigger === 'manual')) {
      label('Syncing\\u2026'); btn.disabled = true;
    } else {
      label('Sync now'); btn.disabled = false;
    }
    if (!s.busy) asked = false;
    if (s.running && s.step_label) {
      note.innerHTML = '<span class="lbl">syncing ' +
        esc(s.step_label.replace(/ sync$/, '')) + ' </span>' + (s.step_elapsed_s || 0) + 's';
      note.hidden = false;
    } else if (s.busy) {
      note.innerHTML = '<span class="lbl">sync </span>starting\\u2026';
      note.hidden = false;
    } else {
      note.hidden = true;
    }
    if (s.last_finished_at && s.last_finished_at !== drawn) fresh.hidden = false;
  }
  function poll() {
    clearTimeout(timer);
    if (document.hidden) { timer = setTimeout(poll, IDLE_POLL_MS); return; }
    fetch('/status').then(function (r) { return r.json(); }).then(function (s) {
      show(s);
      timer = setTimeout(poll, s.busy ? BUSY_POLL_MS : IDLE_POLL_MS);
    }).catch(function () { timer = setTimeout(poll, IDLE_POLL_MS); });
  }
  btn.addEventListener('click', function (ev) {
    var mode = ev.shiftKey ? 'reconcile' : 'incremental';
    asked = true;
    label('Syncing\\u2026'); btn.disabled = true;
    fetch('/sync', { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'mode=' + mode + '&token=' + encodeURIComponent(token)
    }).then(function (r) {
      if (r.status !== 202) { asked = false;
        label('Sync refused (' + r.status + ')', true);
        btn.disabled = false; return; }
      return r.json().then(function (s) { show(s); poll(); });
    }).catch(function () { asked = false; label('Sync failed', true);
      btn.disabled = false; });
  });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) poll();
  });
  timer = setTimeout(poll, document.body.dataset.busy === '1' ? BUSY_POLL_MS : IDLE_POLL_MS);
})();
(function () {
  var f = document.querySelector('header .fresh');
  if (!f) return;
  var b = f.querySelector('.fresh-btn');
  function set(on) {
    f.classList.toggle('open', on);
    b.setAttribute('aria-expanded', on ? 'true' : 'false');
  }
  b.addEventListener('click', function () { set(!f.classList.contains('open')); });
  document.addEventListener('click', function (ev) {
    if (!f.contains(ev.target)) set(false);
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') set(false);
  });
})();
(function () {
  var box = document.getElementById('filter');
  if (!box) return;
  function text(card) {
    return Array.prototype.map.call(
      card.querySelectorAll('.title, .ref, .epic, .meta'),
      function (el) { return el.textContent; }).join(' ').toLowerCase();
  }
  // A heading's title without its count; read again for a heading a card
  // refresh (refresh.py) put back.
  function base(h) {
    if (!h.dataset.base) h.dataset.base = h.textContent.replace(/\\s*\\(\\d+\\)$/, '');
    return h.dataset.base;
  }
  document.querySelectorAll('.lane > h2').forEach(base);
  // Every card action reloads the page; keep the filter across that, per tab.
  var saved = 'filter:' + location.pathname;
  function apply() {
    var q = box.value.trim().toLowerCase();
    document.querySelectorAll('.lane').forEach(function (lane) {
      var cards = lane.querySelectorAll('.card'), shown = 0;
      cards.forEach(function (c) {
        var hit = !q || text(c).indexOf(q) !== -1;
        c.hidden = !hit;
        if (hit) shown++;
      });
      var h = lane.querySelector('h2');
      if (h) h.textContent = base(h) + ' (' + shown + ')';
      lane.dataset.shown = shown;
      lane.classList.toggle('filtered-empty', !!q && shown === 0);
    });
    // The lane tabs (lane_tabs.py) recount from each lane's data-shown.
    if (window.laneTabs) window.laneTabs.refresh(q);
  }
  box.addEventListener('input', function () {
    try { sessionStorage.setItem(saved, box.value); } catch (e) {}
    apply();
  });
  try { box.value = sessionStorage.getItem(saved) || ''; } catch (e) {}
  window.cardFilter = { apply: apply };
  if (box.value) apply();
})();
"""


def _alert(last_run: "dict[str, Any] | None", what: str = "Last sync") -> str:
    """The header's failure line for the latest finished run, if it failed."""
    if not last_run or last_run["outcome"] in (None, *sync.SUCCESS_OUTCOMES):
        return ""
    error = last_run["error"] or ""
    if last_run["outcome"] == "auth_failed":
        if "gh auth login" in error:
            text = "GitHub login expired: run <code>gh auth login</code> in a terminal."
        else:
            text = (
                "Jira login expired: run <code>acli jira auth login</code>"
                " in a terminal, then Sync now."
            )
        return f'<div class="alert" role="alert">{text}</div>'
    first = error.strip().splitlines()[0] if error.strip() else "unknown error"
    return f'<div class="alert" role="alert">{what} failed: {_e(first)}</div>'


def sync_note(state: dict[str, Any]) -> str:
    """The note beside the freshness indicator while a sync is in flight;
    the page's script keeps it current."""
    hidden = "" if state.get("busy") else " hidden"
    text = ""
    if state.get("running") and state.get("step_label"):
        # "Jira + GitHub sync" names the main step; after "syncing" the
        # word is said once already.
        step = state["step_label"].removesuffix(" sync")
        text = (
            f'<span class="lbl">syncing {_e(step)} </span>'
            f"{state.get('step_elapsed_s') or 0}s"
        )
    elif state.get("busy"):
        text = '<span class="lbl">sync </span>starting…'
    return f'<span class="sync-note" id="sync-note"{hidden} aria-live="polite">{text}</span>'


# Two arrows chasing round a circle.
SYNC_ICON = icons.svg("refresh-cw")


def sync_button(label: str, disabled: bool) -> str:
    """The sync icon. `label` ("Sync now", "Syncing…") is its accessible name
    and tooltip; the header script keeps both current."""
    return (
        f'<button id="sync" aria-label="{label}"'
        f' title="{label} (shift-click for a full reconcile)"'
        f"{' disabled' if disabled else ''}>{SYNC_ICON}</button>"
    )


def render_header(
    health: dict[str, Any],
    state: dict[str, Any],
    fresh_html: str = "",
    gauge_html: str = "",
    mode_html: str = "",
) -> str:
    """The header bar, one line (two under 600px): the title, the sync icon,
    the freshness indicator, the sync note, the gauge, the text filter, then
    Now's mode button (`mode_html`, on Now only). `state` is
    App.sync_state(). The icon reads "Syncing…" and spins only for a sync
    someone asked for; a scheduled one leaves it alone. Failure alerts go
    under the line."""
    cls = ' class="stale"' if health["stale"] else ""
    manual = bool(state.get("busy") and state.get("trigger") == "manual")
    label = "Syncing…" if manual else "Sync now"
    return (
        f'<header{cls}><div class="bar"><h1>Task dashboard</h1>'
        f"{sync_button(label, manual)}{fresh_html}{sync_note(state)}"
        '<span class="new-data" id="new-data" hidden aria-live="polite">'
        'New data — <a href="">reload</a></span>'
        f"{gauge_html}{FILTER}{mode_html}"
        f"</div>{_alert(health['last_run'])}"
        f"{_alert(health.get('fleet_run'), 'GitHub fleet sync')}</header>"
    )


def _card(
    row: dict[str, Any],
    site: str,
    ov: "overlay.Overlay | None" = None,
    now: "datetime | None" = None,
    *,
    reorder: bool = False,
) -> str:
    key = str(row["id"])
    href = f"https://{site}/browse/{key}"
    icon = views.status_icon(
        row.get("category"), row.get("status_name"), bool(row.get("flagged"))
    )
    meta = " · ".join(
        _e(str(v))
        for v in (row.get("status_name"), row.get("assignee"), row.get("priority_name"))
        if v
    )
    attrs, controls = 'class="card"', ""
    if ov is not None and now is not None:
        item = ("jira", str(row["id"]))
        attrs = overlay.card_attrs(item, ov, now)
        controls = overlay.card_controls(item, ov, now, reorder=reorder)
    return (
        f"<div {attrs}>{icon}{views.card_title(href, row.get('summary'), key)}"
        f"{views.epic_line(row.get('epic'))}"
        f'<div class="meta">{meta}</div>{controls}</div>'
    )


def board_lanes(
    board: dict[str, Any], ov: "overlay.Overlay | None" = None
) -> list[view_model.Lane]:
    """The Board's lanes, each item a board_rows() row: with `ov`, Pinned
    first, then each classifier section and Unclassified that has rows.

    Pinned cards are repeated above the lanes, in their manual order; the
    lanes themselves stay exactly what /list-tasks would show.
    """
    lanes = []
    if ov is not None:
        rows = [r for lane in board["sections"].values() for r in lane]
        pinned = sorted(
            (r for r in rows + board["unmatched"] if ov.is_pinned(("jira", r["id"]))),
            key=lambda r: ov.pin_rank(("jira", r["id"])),
        )
        if pinned:
            lanes.append(view_model.Lane("pinned", "Pinned", pinned))
    for name in kanban().SECTION_ORDER:
        if board["sections"][name]:
            title = SECTION_TITLES.get(name, name)
            lanes.append(view_model.Lane(name, title, board["sections"][name]))
    if board["unmatched"]:
        lanes.append(view_model.Lane("unmatched", "Unclassified", board["unmatched"]))
    return lanes


def board_data(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    """The Board's classified rows, less the ignored and what is under them."""
    ignored = {k for s, k in overlay.load(conn).ignored if s == "jira"}
    parents = dict(
        conn.execute(
            "SELECT key, parent_key FROM jira_issues WHERE parent_key IS NOT NULL"
        )
    )
    me = str(cfg["jira"].get("me") or "").strip()
    return classify_board(
        [
            r
            for r in board_rows(conn, me)
            if not views.under_ignored(r["id"], r["parent_key"], parents.get, ignored)
        ]
    )


def render_board(
    board: dict[str, Any],
    site: str,
    ov: "overlay.Overlay | None" = None,
    now: "datetime | None" = None,
    inc: "include.Include | None" = None,
) -> str:
    if inc is not None and not inc.shows("ticket"):
        return (
            '<section class="lane" data-section="board" data-filtered="tasks">'
            f"<h2>Issues (0)</h2>{include.off_note('tasks')}</section>"
        )
    parts = []
    for lane in board_lanes(board, ov if now is not None else None):
        pinned = lane.id == "pinned"
        cards = "".join(_card(r, site, ov, now, reorder=pinned) for r in lane.items)
        attr = ' data-pinned="1"' if pinned else ""
        parts.append(
            f'<section class="lane" data-section="{lane.id}"{attr}>'
            f"<h2>{lane.title} ({len(lane.items)})</h2>{cards}</section>"
        )
    if not parts:
        return '<p class="empty">No issues in an active sprint.</p>'
    return "".join(parts)


FILTER = (
    '<input id="filter" type="search" placeholder="Filter this page"'
    ' aria-label="Filter cards" autocomplete="off">'
)


def render_page(
    title: str, header: str, body: str, token: str, busy: bool, synced: str = ""
) -> str:
    """`synced` is the latest step finish the page was drawn from
    (sync_health's `last_finished_at`); the script compares /status with it."""
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta name="dashboard-token" content="{_e(token)}">'
        f"<title>{_e(title)}</title>"
        f"<style>{CSS}{views.CSS}{fleet_views.CSS}{needs.CSS}{fleet_now.CSS}{overlay.CSS}{gauges.CSS}{freshness.CSS}{include.CSS}{lane_tabs.CSS}{whynot.CSS}{refresh.CSS}</style></head>"
        f'<body data-busy="{1 if busy else 0}" data-synced="{_e(synced)}">'
        f"{header}<main>{body}</main>"
        f"<script>{JS}{overlay.JS}{include.JS}{lane_tabs.JS}{refresh.JS}</script></body></html>"
    )


# --- HTTP --------------------------------------------------------------------


def write_overlay(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    action: str,
    form: dict[str, list[str]],
    now: datetime,
) -> None:
    """One overlay write, from a parsed form: what `POST /overlay/<action>`
    runs once its Host, Origin and token checks pass, and what
    `dashboard.py overlay` runs directly. Raises overlay.OverlayError."""

    # The mode the card was shown in (its data-mode), so the answer records
    # the item as that mode ranked it.
    mode = (form.get("mode") or [""])[0].strip() or None

    def snapshot(item: overlay.Item) -> "dict[str, Any] | None":
        return fleet_now.snapshot_of(conn, cfg, item, now, mode)

    overlay.apply(conn, action, form, now, snapshot)


class App:
    """What the request handler needs: config, DB path, token, the syncer."""

    def __init__(
        self,
        cfg: dict[str, Any],
        db_path: Path,
        token: str,
        syncer: Syncer,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        gh_runner: github_gh.Runner = github_gh.run,
    ) -> None:
        self.cfg = cfg
        self.gh_runner = gh_runner
        self.db_path = db_path
        self.token = token
        self.syncer = syncer
        self.now = now
        self.port = 0
        self.interval_s = float(cfg["sync"]["interval_minutes"]) * 60
        self.fleet = sync.fleet_enabled(cfg)
        machines = cfg.get("ingest", {}).get("machines", {})
        self.ingest = ingest.Ingest(machines, db_path, lambda: self.now())

    def allowed_hosts(self) -> set[str]:
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        hosts.update(str(h).lower() for h in self.cfg["server"]["extra_allowed_hosts"])
        return hosts

    def read(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def health(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """sync_health, without a fleet run once `github.scope` is not "owner".

        A failed fleet run would otherwise keep its header alert after the
        step that could clear it was switched off.
        """
        health = sync_health(conn, self.interval_s, self.now())
        if not self.fleet:
            health["fleet_run"] = None
        return health

    def sync_state(self) -> dict[str, Any]:
        """Syncer.state(), with the running step's label."""
        state = self.syncer.state()
        state["step_label"] = (
            freshness.step_label(self.cfg, state["step"]) if state["step"] else None
        )
        return state

    def sources(
        self, conn: sqlite3.Connection, state: dict[str, Any]
    ) -> list[freshness.Source]:
        return freshness.sources(conn, self.cfg, self.now(), self.interval_s, state)

    def header(
        self, conn: sqlite3.Connection, mode_html: str = ""
    ) -> "tuple[str, bool, str]":
        """(the header, whether a sync is in flight, the page's `synced`).

        The header reads the mirror, never a view's include filter, so it is
        identical whatever the filter is. `mode_html` is Now's mode button,
        which only the fleet Now passes.
        """
        state = self.sync_state()
        health = self.health(conn)
        fresh = freshness.render(self.sources(conn, state), bool(health["stale"]))
        waiting = (
            fleet_tabs.waiting_count(conn, self.cfg, self.now()) if self.fleet else None
        )
        gauge = gauges.render(gauges.read(conn, waiting, fleet=self.fleet))
        header = render_header(health, state, fresh, gauge, mode_html)
        return header, bool(state["busy"]), health["last_finished_at"] or ""

    def status(self) -> dict[str, Any]:
        state = self.sync_state()
        conn = self.read()
        try:
            health = self.health(conn)
            queue = gauges.review_queue(conn, self.fleet)
            fresh = freshness.as_json(self.sources(conn, state), bool(health["stale"]))
        finally:
            conn.close()
        return {**state, **health, "review_queue": queue, "freshness": fresh}

    def include_for(
        self, conn: sqlite3.Connection, view: str, query: dict[str, list[str]]
    ) -> include.Include:
        """The view's include filter: URL, else remembered, else config.

        Raises include.IncludeError on an unknown group in the URL.
        """
        remembered = overlay.view_includes(conn).get(view)
        return include.resolve(view, query, remembered, self.cfg)

    def view_page(self, path: str, query: "dict[str, list[str]] | None" = None) -> str:
        """One of the views.ROUTES pages: Now, Waiting on me, and the rest."""
        query = query or {}
        conn = self.read()
        try:
            inc = self.include_for(conn, path.lstrip("/"), query)
            body = views.render(path, conn, self.cfg, self.now(), inc, query)
            body = lane_tabs.tabify(body, path.lstrip("/"), path, query)
            mode_html = ""
            if path == "/now":
                # Every instance's Now has modes. The page was built, so a
                # bad ?mode= has already raised.
                asked = (query.get("mode") or [""])[0].strip() or None
                mode = fleet_now.resolve_mode(asked, overlay.load(conn))
                mode_html = fleet_now.mode_button(mode)
            header, busy, synced = self.header(conn, mode_html)
        finally:
            conn.close()
        title = f"{views.ROUTES[path][0]} · Task dashboard"
        return render_page(title, header, body, self.token, busy, synced)

    def health_page(
        self, days: int, query: "dict[str, list[str]] | None" = None
    ) -> str:
        """The Health panel (health.py) over the last `days` days."""
        query = query or {}
        conn = self.read()
        try:
            inc = self.include_for(conn, "health", query)
            header, busy, synced = self.header(conn)
            body = views.nav("/health", self.cfg, inc, query) + health.page(
                conn, self.cfg, self.now(), days, fleet=self.fleet, inc=inc
            )
            # Answers to the "why not now?" picker on every Now card.
            body += whynot.section(conn, self.now())
        finally:
            conn.close()
        return render_page(
            "Health · Task dashboard", header, body, self.token, busy, synced
        )

    def overlay_write(self, action: str, form: dict[str, list[str]]) -> None:
        conn = self.read()
        try:
            write_overlay(conn, self.cfg, action, form, self.now())
        finally:
            conn.close()

    def refresh(self, form: dict[str, list[str]]) -> refresh.Result:
        """`POST /refresh`: re-read the one item the form names (`key`, the
        card's URL, or `ref`, `owner/repo#N`). Raises refresh.RefreshError
        and github_gh.GhError."""
        ref = (form.get("key") or form.get("ref") or [""])[0]
        conn = self.read()
        try:
            return refresh.refresh(conn, self.cfg, ref, self.now(), self.gh_runner)
        finally:
            conn.close()

    def board_page(self, query: "dict[str, list[str]] | None" = None) -> str:
        query = query or {}
        conn = self.read()
        try:
            inc = self.include_for(conn, "board", query)
            ov = overlay.load(conn)
            board = board_data(conn, self.cfg)
            ignored_html = views.render_ignored(conn)
            header, busy, synced = self.header(conn)
            sprints = [
                r[0] for r in conn.execute("SELECT name FROM sprints ORDER BY id")
            ]
        finally:
            conn.close()
        title = "Board" + (f": {', '.join(sprints)}" if sprints else "")
        body = (
            views.nav("/board", self.cfg, inc, query)
            + f"<h2>{_e(title)}</h2>"
            + render_board(board, self.cfg["jira"]["site"], ov, self.now(), inc)
            + ignored_html
        )
        body = lane_tabs.tabify(body, "board", "/board", query)
        return render_page("Task dashboard", header, body, self.token, busy, synced)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: App, port: int) -> None:
        super().__init__((BIND, port), Handler)
        self.app = app
        app.port = self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "task-dashboard"

    def log_message(self, format: str, *args: Any) -> None:
        # Quiet by default: the page polls /status while a sync runs. Refusals
        # are logged on purpose, through _refuse.
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _text(self, code: int, text: str) -> None:
        self._send(code, text.encode(), "text/plain; charset=utf-8")

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _refuse(self, why: str) -> None:
        sys.stderr.write(f"task-dashboard: 403 {self.command} {self.path}: {why}\n")
        self._text(403, "forbidden\n")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if host in self.server.app.allowed_hosts():
            return True
        self._refuse(f"Host {host!r} not allowed")
        return False

    def _origin_ok(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip().lower()
        parts = urlsplit(origin)
        host = (self.headers.get("Host") or "").strip().lower()
        if (
            parts.scheme in ("http", "https")
            and parts.netloc == host
            and not (parts.path or parts.query)
        ):
            return True
        self._refuse(f"Origin {origin!r} does not match Host {host!r}")
        return False

    def do_GET(self) -> None:
        if not self._host_ok():
            return
        split = urlsplit(self.path)
        path = split.path
        # Blank values kept: `include=` (every switch off) is a choice.
        query = parse_qs(split.query, keep_blank_values=True)
        app = self.server.app
        html_type = "text/html; charset=utf-8"
        try:
            if path == "/" and fleet_views.fleet_pages(app.cfg):
                # A fleet instance's home page is Now, its ranked queue; the
                # Jira one's the Board.
                self._send(200, app.view_page("/now", query).encode(), html_type)
            elif path in RETIRED_ROUTES:
                # Since you were away folded into Needs you, a fleet-only tab.
                self._redirect("/needs" if fleet_views.fleet_pages(app.cfg) else "/")
            elif path in ("/", "/board"):
                self._send(200, app.board_page(query).encode(), html_type)
            elif path in views.ROUTES:
                self._send(200, app.view_page(path, query).encode(), html_type)
            elif path == "/health":
                days = health.parse_days(query)
                self._send(200, app.health_page(days, query).encode(), html_type)
            elif path == "/status":
                self._json(200, app.status())
            else:
                self._text(404, "not found\n")
        except (include.IncludeError, fleet_now.ModeError) as exc:
            self._text(400, f"{exc}\n")

    def do_POST(self) -> None:
        if not self._host_ok():
            return
        if urlsplit(self.path).path == "/ingest" and self.server.app.ingest.enabled:
            # A collector, not a browser: its own token check replaces the
            # Origin and page-token checks below. The Host check above stands.
            code, body, why = self.server.app.ingest.handle(self.headers, self.rfile)
            if why:
                sys.stderr.write(f"task-dashboard: {code} POST /ingest: {why}\n")
            self._json(code, body)
            return
        path = urlsplit(self.path).path
        action = path[len("/overlay/") :] if path.startswith("/overlay/") else None
        if path not in ("/sync", "/refresh") and action not in overlay.ACTIONS:
            self._text(404, "not found\n")
            return
        if not self._origin_ok():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > (MAX_BODY if action is None else overlay.MAX_BODY):
            self._text(400, "bad body\n")
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        token = (form.get("token") or [""])[0]
        app = self.server.app
        if not hmac.compare_digest(token.encode(), app.token.encode()):
            self._refuse("missing or wrong token")
            return
        if path == "/refresh":
            self._refresh(form)
            return
        if action is not None:
            try:
                app.overlay_write(action, form)
            except overlay.OverlayError as exc:
                self._text(400, f"{exc}\n")
                return
            except sqlite3.Error as exc:
                # Usually a lock a sync held past the connect timeout. Log the
                # detail here; the client gets a retryable status, no traceback.
                sys.stderr.write(f"task-dashboard: overlay {action} failed: {exc}\n")
                self._json(503, {"ok": False, "error": "database busy, try again"})
                return
            self._json(200, {"ok": True})
            return
        mode = (form.get("mode") or ["incremental"])[0]
        if mode not in SYNC_MODES:
            self._text(400, f"mode must be one of {', '.join(SYNC_MODES)}\n")
            return
        app.syncer.request(mode)
        self._json(202, app.status())

    def _refresh(self, form: dict[str, list[str]]) -> None:
        """Answer JSON: `ok` with the outcome, or `ok: false` with `error`.

        A ref this instance does not refresh is a 400; GitHub failing is a
        502, and the mirror is unchanged either way.
        """
        try:
            result = self.server.app.refresh(form)
        except refresh.RefreshError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        except github_gh.AuthError:
            error = "GitHub login expired: run gh auth login"
            self._json(502, {"ok": False, "error": error})
            return
        except github_gh.GhError as exc:
            sys.stderr.write(f"task-dashboard: refresh failed: {exc}\n")
            self._json(502, {"ok": False, "error": "GitHub did not answer; try again"})
            return
        except sqlite3.Error as exc:
            sys.stderr.write(f"task-dashboard: refresh failed: {exc}\n")
            self._json(503, {"ok": False, "error": "database busy, try again"})
            return
        self._json(200, {"ok": True, **result.as_dict()})


def build(
    cfg: dict[str, Any],
    db_path: Path,
    port: int,
    sync_fn: "Callable[[str], None] | None" = None,
    *,
    run_at_start: bool = True,
) -> "tuple[DashboardServer, Syncer]":
    """Migrate the DB, mint the token, and bind; nothing runs until started."""
    conn = db.connect(db_path)
    try:
        token = ensure_token(conn)
    finally:
        conn.close()
    # The lambda reads `syncer` when a step starts, after it is assigned.
    syncer = Syncer(
        sync_fn or make_sync_fn(cfg, db_path, lambda kind: syncer.step(kind)),
        float(cfg["sync"]["interval_minutes"]) * 60,
        float(cfg["sync"]["reconcile_minutes"]) * 60,
        run_at_start=run_at_start,
    )
    app = App(cfg, db_path, token, syncer)
    return DashboardServer(app, port), syncer


def serve(cfg: dict[str, Any], db_path: Path, port: int) -> None:
    httpd, syncer = build(cfg, db_path, port)
    syncer.start()
    print(f"task-dashboard: serving http://{BIND}:{httpd.app.port}/", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        syncer.stop()
