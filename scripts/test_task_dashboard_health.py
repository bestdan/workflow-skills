#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/health.py, /health and `health`.

A fixture of history entries and hook events with hand-computed answers checks
each windowed figure; the server and CLI cases check that the panel and
`dashboard.py health --json` print the same numbers for the same window. No
test reaches GitHub: the server runs with the server suite's fake sync, and the
GitHub figures read rows written straight into the mirror. Needs Python 3.11:
run via scripts/test-task-dashboard-health.sh.
"""

import contextlib
import io
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))

# mypy checks this file without scripts/task-dashboard on its path; the
# modules themselves are type-checked in their own group (scripts/typecheck.sh).
import config  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import health  # type: ignore[import-not-found]  # noqa: E402
import score  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

UTC = timezone.utc
START = datetime(2026, 10, 20, tzinfo=UTC)
END = datetime(2026, 10, 21, tzinfo=UTC)


def at(day, hh, mm):
    return datetime(2026, 10, day, hh, mm, tzinfo=UTC)


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# (time, session, approval_shaped). Hand-computed over [START, END):
# - approval: p1, p3, p8 of 8 prompts            -> 37.5 per 100
# - close pairs (< 30 min): p1-p2 A>B, p2-p3 B>C, p3-p4 C>C, p5-p6 A>B
#                                                 -> 3 of 4 switched, 75.0%
# - 3+ active: at p3 and p4 of 7 sessioned prompts -> 28.6%
# The previous day holds one approval-shaped prompt: 100.0, so a trend of −62.5.
HISTORY = [
    (at(19, 12, 0), "A", True),
    (at(20, 10, 0), "A", True),  # p1
    (at(20, 10, 5), "B", False),  # p2
    (at(20, 10, 10), "C", True),  # p3
    (at(20, 10, 20), "C", False),  # p4
    (at(20, 11, 30), "A", False),  # p5
    (at(20, 11, 40), "B", False),  # p6
    (at(20, 11, 45), None, False),  # p7: no session, per-100 figures only
    (at(20, 13, 0), "B", True),  # p8
]

P, S, N = "UserPromptSubmit", "Stop", "Notification"
# (time, session, event, notification). Hand-computed over [START, END):
# - owner waits 4, 10 (s1), 2, 10 (s2; the queued 10:31 prompt does not restart
#   it)                                      -> median 7.0 min, p90 10.0 min
# - agent waits 6 (s1), 28 (s2), 20 (s3, opened the day before) -> median 20.0,
#   p90 28.0
# - 1 permission prompt over 6 prompts       -> 16.7 per 100
EVENTS = [
    (at(19, 23, 50), "s3", S, None),
    (at(20, 0, 10), "s3", P, None),
    (at(20, 10, 0), "s1", P, None),
    (at(20, 10, 4), "s1", S, None),
    (at(20, 10, 10), "s1", P, None),
    (at(20, 10, 11), "s1", N, "permission_prompt"),
    (at(20, 10, 12), "s1", N, "idle_prompt"),
    (at(20, 10, 20), "s1", S, None),
    (at(20, 10, 0), "s2", P, None),
    (at(20, 10, 2), "s2", S, None),
    (at(20, 10, 30), "s2", P, None),
    (at(20, 10, 31), "s2", P, None),
    (at(20, 10, 40), "s2", S, None),
]


def seed(conn, *, first_event=None):
    conn.executemany(
        "INSERT INTO history_entries (host, ts, session_id, project,"
        " approval_shaped, received_at) VALUES ('mini', ?, ?, '/p', ?, 'r')",
        [(ts(t), s, int(a)) for t, s, a in HISTORY],
    )
    events = list(EVENTS)
    if first_event is not None:
        events.append((first_event, "old", S, None))
    conn.executemany(
        "INSERT INTO session_events (host, ts, event, session_id, notification,"
        " received_at) VALUES ('mini', ?, ?, ?, ?, 'r')",
        [(ts(t), e, s, n) for t, s, e, n in events],
    )
    conn.commit()


def two_pass_waits(conn, start, end):
    """The pre-#1145 reference: ``waits(conn, start, end)`` as it was, one full
    scan of session_events per window. The one-pass code must match it."""
    pending_prompt: dict[tuple, datetime] = {}
    pending_stop: dict[tuple, datetime] = {}
    owner: list[float] = []
    agent: list[float] = []
    for t, (host, sid, event, _) in health._rows(
        conn, health._EVENTS, health._EPOCH, end
    ):
        if sid is None:
            continue
        key = (host, sid)
        if event == P:
            opened = pending_stop.pop(key, None)
            if opened is not None and t >= start:
                agent.append((t - opened).total_seconds())
            pending_prompt.setdefault(key, t)
        elif event == S:
            opened = pending_prompt.pop(key, None)
            if opened is not None and t >= start:
                owner.append((t - opened).total_seconds())
            pending_stop[key] = t
    return owner, agent


# Waits that close in the previous day, [END - 2d, START): s4 opens before it
# and closes inside it, s5 opens inside it and closes in the current window.
PREVIOUS_EVENTS = [
    (at(18, 23, 50), "s4", P, None),
    (at(19, 0, 10), "s4", S, None),  # owner wait 20 min, opened before the window
    (at(19, 8, 0), "s4", P, None),  # agent wait 470 min
    (at(19, 23, 55), "s5", P, None),
    (at(20, 0, 5), "s5", S, None),  # owner wait 10 min, closes in the current window
    (at(19, 10, 0), "s6", S, None),
    (at(19, 10, 30), "s6", P, None),  # agent wait 30 min
    (at(19, 10, 45), "s6", S, None),  # owner wait 15 min
]


def seed_events(conn, events):
    conn.executemany(
        "INSERT INTO session_events (host, ts, event, session_id, notification,"
        " received_at) VALUES ('mini', ?, ?, ?, ?, 'r')",
        [(ts(t), e, s, n) for t, s, e, n in events],
    )
    conn.commit()


def seed_fleet(conn):
    rows = [
        {"kind": "pr", "key": f"https://g/pull/{n}", "title": "t", "in_queue": n < 2}
        for n in range(3)
    ]
    # A PR someone else opened: open and awaiting the owner, never queued.
    rows.append(
        {
            "kind": "pr",
            "key": "https://g/pull/9",
            "title": "t",
            "in_queue": False,
            "role": "others",
        }  # fmt: skip
    )
    rows += [
        {"kind": "design", "key": "https://g/d1", "title": "d", "status": "proposed"},
        {"kind": "design", "key": "https://g/d2", "title": "d", "status": "accepted"},
    ]
    github_fleet.write(conn, rows, END)
    server_harness.add_run(conn, "ok", END, kind=github_fleet.RUN_KIND)


def flow_row(taken_at=END, closed_7=9, previous_closed_7=8):
    """An issue_flow row as github_fleet.issue_flow writes it."""
    windows = {
        str(d): {"opened": 3 * d, "closed": 2 * d,
                 "previous_opened": d, "previous_closed": d}
        for d in health.WINDOWS
    }  # fmt: skip
    windows["7"] = {"opened": 12, "closed": closed_7,
                    "previous_opened": 10, "previous_closed": previous_closed_7}  # fmt: skip
    return {
        "kind": github_fleet.ISSUE_FLOW,
        "key": github_fleet.ISSUE_FLOW,
        "title": "issues opened and closed",
        "taken_at": taken_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "windows": windows,
    }


def seed_flow(conn, row):
    github_fleet.write(conn, [row], END)
    server_harness.add_run(conn, "ok", END, kind=github_fleet.RUN_KIND)


def by_id(result):
    return {f["id"]: f for f in result["figures"]}


class DbCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = Path(self._td.name) / "d.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)


class FigureTest(DbCase):
    def test_known_answers(self):
        seed(self.conn)
        f = by_id(health.compute(self.conn, {}, START, END, fleet=False))
        expected = {
            "approval_per_100": 37.5,
            "switch_rate": 75.0,
            "three_plus_active": 28.6,
            "permission_per_100": 16.7,
            "owner_wait_median": 7.0,
            "owner_wait_p90": 10.0,
            "agent_wait_median": 20.0,
            "agent_wait_p90": 28.0,
        }
        self.assertEqual({k: f[k]["value"] for k in expected}, expected)
        self.assertEqual(f["approval_per_100"]["previous"], 100.0)
        self.assertEqual(f["approval_per_100"]["trend"], -62.5)
        # Nothing to pair the day before: no value, so no trend, never a guess.
        self.assertIsNone(f["switch_rate"]["previous"])
        self.assertIsNone(f["switch_rate"]["trend"])
        self.assertIsNone(f["permission_per_100"]["previous"])

    def assert_waits_match_two_pass(self):
        prev = START - (END - START)
        one_pass = health.waits(self.conn, (prev, START, END))
        for got, (lo, hi) in zip(one_pass, [(prev, START), (START, END)]):
            self.assertEqual(
                tuple(map(sorted, got)),
                tuple(map(sorted, two_pass_waits(self.conn, lo, hi))),
            )
        return one_pass

    def test_one_pass_waits_match_two_pass_reference(self):
        seed(self.conn)
        (prev_owner, prev_agent), (owner, agent) = self.assert_waits_match_two_pass()
        self.assertEqual((prev_owner, prev_agent), ([], []))
        self.assertEqual(sorted(owner), [120.0, 240.0, 600.0, 600.0])
        self.assertEqual(sorted(agent), [360.0, 1200.0, 1680.0])
        self.assertEqual([health._minutes(owner, q) for q in (None, 0.9)], [7.0, 10.0])
        self.assertEqual([health._minutes(agent, q) for q in (None, 0.9)], [20.0, 28.0])

    def test_one_pass_waits_match_with_waits_closing_in_previous_window(self):
        seed(self.conn)
        seed_events(self.conn, PREVIOUS_EVENTS)
        (prev_owner, prev_agent), (owner, agent) = self.assert_waits_match_two_pass()
        self.assertEqual(sorted(prev_owner), [900.0, 1200.0])
        self.assertEqual(sorted(prev_agent), [1800.0, 28200.0])
        self.assertIn(600.0, owner)  # s5, opened in the previous window
        self.assertEqual(len(owner), 5)
        self.assertEqual(len(agent), 3)
        f = by_id(health.compute(self.conn, {}, START, END, fleet=False))
        self.assertEqual(f["owner_wait_median"]["previous"], 17.5)
        self.assertEqual(f["agent_wait_median"]["previous"], 250.0)
        self.assertEqual(f["owner_wait_median"]["value"], 10.0)

    def test_a_wait_closing_on_a_window_edge_belongs_to_the_later_window(self):
        prev = START - (END - START)
        seed_events(
            self.conn,
            [
                (at(18, 23, 0), "e1", P, None),
                (prev, "e1", S, None),  # closes exactly at the previous start
                (at(19, 23, 0), "e2", P, None),
                (START, "e2", S, None),  # closes exactly at the current start
            ],
        )
        (prev_owner, _), (owner, _) = self.assert_waits_match_two_pass()
        self.assertEqual((prev_owner, owner), ([3600.0], [3600.0]))

    def test_waits_scan_session_events_once_per_compute(self):
        seed(self.conn)
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        health.compute(self.conn, {}, START, END, fleet=False)
        self.conn.set_trace_callback(None)
        # The waits scan is the only epoch-bounded session_events read.
        scans = [
            q for q in statements if "FROM session_events" in q and "1969-12-31" in q
        ]
        self.assertEqual(len(scans), 1, scans)

    def test_waits_are_baseline_under_14_days_of_hook_data(self):
        seed(self.conn)
        result = health.compute(
            self.conn, {"owner_wait_median": "<= 5"}, START, END, fleet=False
        )
        f = by_id(result)
        self.assertEqual(result["hook_days"], 1.0)
        for fid in ("owner_wait_median", "owner_wait_p90", "agent_wait_median"):
            self.assertTrue(f[fid]["baseline"], fid)
            self.assertIn("baseline", f[fid]["note"])
        self.assertIsNone(f["owner_wait_median"]["met"])  # judged only past it
        self.assertFalse(f["permission_per_100"]["baseline"])
        page = health.render(result, 7)
        row = re.search(r'<tr[^>]*data-figure="owner_wait_median".*?</tr>', page)
        assert row is not None
        self.assertIn('class="baseline">baseline<', row.group(0))
        self.assertIn('class=""', row.group(0))  # not coloured

    def test_waits_are_judged_once_14_days_exist(self):
        seed(self.conn, first_event=END - timedelta(days=14))
        f = by_id(
            health.compute(
                self.conn,
                {"owner_wait_median": "<= 5", "agent_wait_median": "< 30"},
                START,
                END,
                fleet=False,
            )
        )
        self.assertFalse(f["owner_wait_median"]["baseline"])
        self.assertIs(f["owner_wait_median"]["met"], False)
        self.assertIs(f["agent_wait_median"]["met"], True)

    def test_no_hook_data_is_no_value_and_baseline(self):
        result = health.compute(self.conn, {}, START, END, fleet=False)
        f = by_id(result)
        self.assertIsNone(result["hook_days"])
        self.assertIsNone(f["owner_wait_median"]["value"])
        self.assertTrue(f["owner_wait_median"]["baseline"])
        self.assertIsNone(f["switch_rate"]["value"])

    def test_github_figures_are_unknown_until_a_fleet_run(self):
        seed_fleet(self.conn)
        off = by_id(health.compute(self.conn, {}, START, END, fleet=False))
        for fid in ("open_prs", "review_queue", "designs_proposed"):
            self.assertIsNone(off[fid]["value"], fid)
        on = by_id(health.compute(self.conn, {}, START, END, fleet=True))
        self.assertEqual(on["open_prs"]["value"], 4)
        self.assertEqual(on["review_queue"]["value"], 2)
        self.assertEqual(on["designs_proposed"]["value"], 1)
        self.assertNotIn("side_quests", on)
        self.assertIsNone(on["issues_opened_closed"]["value"])
        self.assertTrue(on["issues_opened_closed"]["note"].startswith("no source yet:"))

    def flow(self, start, end, fleet=True):
        f = by_id(health.compute(self.conn, {}, start, end, fleet=fleet))
        return f["issues_opened_closed"]

    def test_issue_flow_covers_each_window_ending_at_the_sync(self):
        seed_flow(self.conn, flow_row())
        f = self.flow(END - timedelta(days=7), END)
        self.assertEqual((f["value"], f["previous"], f["trend"]), (1.33, 1.25, 0.1))
        self.assertTrue(f["window"])
        self.assertEqual(
            f["note"],
            "12 opened : 9 closed; previous 7 days 10 : 8;"
            " counted 2026-10-21T00:00:00Z",
        )
        for d in (14, 30):
            f = self.flow(END - timedelta(days=d), END)
            self.assertEqual((f["value"], f["previous"], f["trend"]), (1.5, 1.0, 0.5))
            self.assertIn(
                f"{3 * d} opened : {2 * d} closed; previous {d} days", f["note"]
            )
        # Within the tolerance of the count: the panel's window ends at the request.
        late = END + health.ISSUE_FLOW_TOLERANCE
        self.assertEqual(self.flow(late - timedelta(days=7), late)["value"], 1.33)

    def test_issue_flow_outside_its_windows_has_no_source(self):
        seed_flow(self.conn, flow_row())
        late = END + health.ISSUE_FLOW_TOLERANCE + timedelta(seconds=1)
        for start, end, why in (
            (START, END, "this one is 1 day"),
            (END - timedelta(days=8), END, "this one is 8 days"),
            (late - timedelta(days=7), late, "more than 1 hour"),
            (START - timedelta(days=7), START, "more than 1 hour"),
        ):
            with self.subTest(why=why, end=end):
                f = self.flow(start, end)
                self.assertIsNone(f["value"])
                self.assertIsNone(f["trend"])
                self.assertTrue(f["note"].startswith("no source yet:"), f["note"])
                self.assertIn(why, f["note"])

    def test_issue_flow_with_nothing_closed_has_no_ratio(self):
        seed_flow(self.conn, flow_row(closed_7=0, previous_closed_7=0))
        f = self.flow(END - timedelta(days=7), END)
        self.assertEqual((f["value"], f["previous"], f["trend"]), (None, None, None))
        self.assertTrue(
            f["note"].startswith("12 opened : 0 closed; previous 7 days 10 : 0")
        )

    def test_issue_flow_needs_a_count_and_the_fleet_sync(self):
        window = (END - timedelta(days=7), END)
        f = self.flow(*window)
        self.assertIsNone(f["value"])
        self.assertIn("stored no issue counts", f["note"])
        seed_flow(self.conn, flow_row())
        f = self.flow(*window, fleet=False)
        self.assertIsNone(f["value"])
        self.assertTrue(f["note"].startswith("no source yet: the fleet sync is off"))

    def test_observed_target_is_never_coloured(self):
        seed(self.conn)
        f = by_id(health.compute(self.conn, {}, START, END, fleet=False))
        self.assertEqual(f["switch_rate"]["target"], "observed")
        self.assertIsNone(f["switch_rate"]["met"])

    def test_text_table(self):
        # `dashboard.py health` without --json. Compared whole, so a dropped or
        # reordered column (or row) fails; the values are the fixture's
        # hand-computed answers above.
        seed(self.conn)
        seed_fleet(self.conn)
        targets = {
            "approval_per_100": "<= 40",
            "switch_rate": "<= 50",
            "owner_wait_median": "<= 5",  # baseline, so never judged
        }
        result = health.compute(self.conn, targets, START, END, fleet=True)
        why = " — baseline: under 14 days of hook data"
        expected = [
            "health 2026-10-20T00:00:00.000Z -> 2026-10-21T00:00:00.000Z",
            '  Approval-shaped and bare "y"/"yes" prompts: 37.5 per 100 prompts'
            " (target <= 40, met; trend −62.5)",
            "  Switch rate (prompts < 30 min apart): 75 %"
            " (target <= 50, MISSED; trend —)",
            "  Prompts with 3+ sessions active: 28.6 % (target observed; trend +28.6)",
            "  Harness permission prompts: 16.7 per 100 prompts"
            " (target observed; trend —)",
            "  Owner waiting on agents, median [baseline]: 7 min"
            " (target <= 5; trend —)" + why,
            "  Owner waiting on agents, p90 [baseline]: 10 min"
            " (target observed; trend —)" + why,
            "  Agents waiting on owner, median [baseline]: 20 min"
            " (target observed; trend —)" + why,
            "  Agents waiting on owner, p90 [baseline]: 28 min"
            " (target observed; trend —)" + why,
            "  Open PRs awaiting the owner: 4 PRs (target observed; current)",
            "  Issues opened : closed: — (target observed; trend —)"
            " — no source yet: issues are counted only for windows of 7, 14, 30"
            " days ending at the last sync; this one is 1 day",
            "  Designs at proposed: 1 design (target observed; current)",
            "  Human-review queue length: 2 PRs"
            f" (target <= {health.gauges.REVIEW_QUEUE_CAP}, met; current)",
        ]
        self.assertEqual(health.text_table(result).split("\n"), expected + [""])

    def test_the_computed_result_keeps_the_plural_unit(self):
        # Singular is render-time only: compute(), which `health --json`
        # prints, still carries the plural unit when the value is 1.
        seed(self.conn)
        seed_fleet(self.conn)
        f = by_id(health.compute(self.conn, {}, START, END, fleet=True))
        self.assertEqual(f["designs_proposed"]["value"], 1)
        self.assertEqual(f["designs_proposed"]["unit"], "designs")
        self.assertEqual(f["open_prs"]["unit"], "PRs")

    def test_target_comparisons(self):
        self.assertIs(health.met(5, "<= 5"), True)
        self.assertIs(health.met(5, "< 5"), False)
        self.assertIs(health.met(0.5, ">= 0.25"), True)
        self.assertIsNone(health.met(None, "<= 5"))
        self.assertIsNone(health.met(4, "observed"))


def unit_figure(fid, value, unit):
    return {
        "id": fid, "label": fid, "note": "", "value": value, "unit": unit,
        "met": None, "baseline": False, "trend": None, "window": False,
        "target": "observed", "source": "GitHub",
    }  # fmt: skip


def unit_result(*figures):
    return {
        "since": "S", "until": "U", "hook_days": None, "unknown_targets": [],
        "figures": list(figures),
    }  # fmt: skip


class SingularUnitTest(unittest.TestCase):
    def test_a_value_of_one_takes_the_singular_unit_in_both_renderers(self):
        # (value, PR unit, design unit): singular at 1, plural otherwise.
        cases = ((1, "PR", "design"), (2, "PRs", "designs"))
        for value, pr, design in cases:
            result = unit_result(
                unit_figure("open_prs", value, "PRs"),
                unit_figure("designs_proposed", value, "designs"),
            )
            text = health.text_table(result)
            self.assertIn(f"  open_prs: {value} {pr} (", text)
            self.assertIn(f"  designs_proposed: {value} {design} (", text)
            html = health.render(result, 7)
            self.assertIn(f"<b>{value}</b> {pr}</td>", html)
            self.assertIn(f"<b>{value}</b> {design}</td>", html)


class ConfigTest(unittest.TestCase):
    def load(self, targets):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.toml"
            p.write_text(
                '[jira]\nsite = "x.invalid"\nboard_ids = []\n'
                '[jira.fields]\nsprint = "a"\nflagged = "b"\n'
                f"[health.targets]\n{targets}\n"
            )
            return config.load(p)

    def test_targets_are_read_and_validated(self):
        cfg = self.load('switch_rate = "<= 20"')
        self.assertEqual(cfg["health"]["targets"], {"switch_rate": "<= 20"})
        with self.assertRaisesRegex(config.ConfigError, "comparison"):
            self.load("switch_rate = 20")
        with self.assertRaisesRegex(config.ConfigError, "comparison"):
            self.load('switch_rate = "about 20"')

    def test_an_unknown_target_id_is_named(self):
        cfg = self.load('swich_rate = "<= 20"')  # a typo loads, and is reported
        with tempfile.TemporaryDirectory() as td:
            conn = db.connect(Path(td) / "d.db")
            try:
                result = health.compute(
                    conn, cfg["health"]["targets"], START, END, fleet=False
                )
            finally:
                conn.close()
        self.assertEqual(result["unknown_targets"], ["swich_rate"])
        self.assertIn("names no figure: swich_rate", health.render(result, 7))


class PanelTest(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        # Local midnight, so a 7-day panel window is exactly the CLI's dated one.
        self.end = score.local_day_start(date(2026, 10, 21))
        self.httpd.app.now = lambda: self.end
        conn = sqlite3.connect(self.db_path)
        seed(conn)
        seed_fleet(conn)
        conn.close()

    def panel_values(self, path="/health?days=7"):
        status, page = self.request("GET", path)
        self.assertEqual(status, 200)
        rows = re.findall(r'data-figure="([a-z_0-9]+)" data-value="([^"]*)"', page)
        return page, {fid: json.loads(v.replace("&quot;", '"')) for fid, v in rows}

    def test_health_json_matches_the_panel(self):
        _, panel = self.panel_values()
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "c.toml"
            cfg_path.write_text(
                '[jira]\nsite = "x.invalid"\nboard_ids = []\n'
                '[jira.fields]\nsprint = "a"\nflagged = "b"\n'
                '[github]\nenabled = true\nscope = "owner"\n'
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = dashboard.main(
                    [
                        "health", "--since", "2026-10-14", "--until", "2026-10-20",
                        "--json", "--db", str(self.db_path), "--config", str(cfg_path),
                    ]
                )  # fmt: skip
        self.assertEqual(code, 0)
        cli = {f["id"]: f["value"] for f in json.loads(out.getvalue())["figures"]}
        self.assertEqual(panel, cli)
        self.assertEqual(set(panel), set(health.FIGURE_IDS))
        self.assertEqual(panel["review_queue"], 2)
        self.assertIsNotNone(panel["approval_per_100"])

    def test_window_selector(self):
        page, _ = self.panel_values("/health?days=30")
        self.assertIn('href="/health?days=30" aria-current=page', page)
        page, _ = self.panel_values("/health?days=9")  # not offered: the default
        self.assertIn('href="/health?days=7" aria-current=page', page)
        self.assertIn('href="/health" aria-current=page>Health', page)
        self.assertIn('data-gauge="review-queue"', page)  # header gauges too

    def test_until_before_since_is_refused(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = dashboard.main(
                ["health", "--since", "2026-10-20", "--until", "2026-10-19",
                 "--db", str(self.db_path), "--config", "/nonexistent"]
            )  # fmt: skip
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
