#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/whynot.py: the fleet Now's "why
not now" picker, the answers it records, the daily base rate, the Health
section, and `dashboard.py tune`.

The fixture is the fleet Now suite's (test_task_dashboard_fleet_now.py),
written through the real writers; the server runs in-process with a fake sync
function, and the CLI runs against a temp DB and config. No gh, git, claude
or network runs. Needs Python 3.11: run via scripts/test-task-dashboard-whynot.sh.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
DASHBOARD = PKG / "dashboard.py"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_now  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import views  # type: ignore[import-not-found]  # noqa: E402
import whynot  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_fleet_now as now_fx  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

NOW = now_fx.NOW
GH = github_fleet.SOURCE
pr_url, issue_url = now_fx.pr_url, now_fx.issue_url
WEIGHTS = config.DEFAULTS["score"]


def snap(repo="acme/app", top="quick_win", value=10.0, **features):
    """A snapshot whose largest term is `top`."""
    return {
        "kind": features.get("kind", "pr"),
        "title": f"item in {repo}",
        "score": value + 0.2,
        "terms": [[top, value, "why"], ["age", 0.2, "waiting 1d"]],
        "features": {"repo": repo, "kind": "pr", "own": "no", **features},
    }


def answer(conn, n, reason, s, text="", at=NOW):
    overlay.snooze(conn, (GH, f"k{n}"), "2026-11-01")
    overlay.why_not(conn, (GH, f"k{n}"), reason, text, s, at)


def shown(conn, n, repo, day="2026-10-20"):
    conn.execute(
        "INSERT INTO now_shown (day, source, item_key, features) VALUES (?, ?, ?, ?)",
        (day, GH, f"shown{n}", json.dumps({"repo": repo, "kind": "pr"})),
    )
    conn.commit()


class PickerTests(now_fx.Case):
    def test_a_now_card_carries_the_hidden_why_not_now_line(self):
        now_fx.seed(self.conn)
        page = views.render("/now", self.conn, self.cfg, NOW)
        card = page.split('<div class="card"', 2)[1]
        until = overlay.snooze_until(NOW)
        self.assertIn(f'data-ov="snooze" data-until="{until}" data-why="1"', card)
        line = re.search(r'<div class="whynot" hidden>(.*?)</div>', card)
        assert line is not None, card
        self.assertIn(
            f'Snoozed until <b class="whynot-until">{until}</b>', line.group(1)
        )
        self.assertEqual(
            re.findall(r'data-reason="([^"]+)"', line.group(1)),
            list(overlay.WHY_NOT_CODES),
        )
        # How long instead: 1d to 30d, each its own date, 7d (the snooze it
        # just made) pressed.
        lengths = re.findall(
            r'data-ov="snooze-for" data-until="([^"]+)" aria-pressed="(\w+)">(\d+)d<',
            line.group(1),
        )
        self.assertEqual(
            lengths,
            [
                (overlay.snooze_until(NOW, d), str(d == 7).lower(), str(d))
                for d in (1, 3, 7, 14, 30)
            ],
        )
        # No second plain snooze in the line: only Undo, which posts an empty date.
        self.assertNotIn('data-ov="snooze"', line.group(1))
        self.assertIn('data-ov="undo"', line.group(1))
        self.assertIn('maxlength="500"', line.group(1))

    def test_other_views_snooze_without_asking(self):
        html = overlay.card_controls((GH, "x"), overlay.Overlay(), NOW, reorder=False)
        self.assertNotIn("data-why", html)


class FeatureTests(now_fx.Case):
    def by_item(self):
        return {
            r.need.item[1]: r for r in fleet_now.candidates(self.conn, self.cfg, NOW)
        }

    def test_a_snapshot_carries_score_terms_and_unscored_features(self):
        now_fx.seed(self.conn)
        r = self.by_item()[pr_url(21)]  # dependabot, CI pending
        s = whynot.snapshot(r, NOW)
        self.assertEqual(s["score"], r.total)
        self.assertEqual(s["terms"], [list(t) for t in r.terms])
        f = s["features"]
        self.assertEqual(f["repo"], now_fx.tabs_fx.REPO)
        self.assertEqual((f["kind"], f["category"]), ("pr", "others_pr"))
        self.assertEqual(
            (f["author"], f["bot"], f["ci"]), ("dependabot[bot]", "yes", "pending")
        )
        self.assertEqual(f["own"], "no")
        issue = whynot.features(self.by_item()[issue_url(12)], NOW)
        self.assertEqual(
            (issue["prio"], issue["status"], issue["own"], issue["bot"]),
            ("prio:2", "status:3_started", "yes", None),
        )
        self.assertEqual(issue["age"], "1-7d")
        self.assertEqual(issue["age_days"], 5.5)

    def test_own_is_authorship_for_a_pr(self):
        reviewer = now_fx.tabs_fx.awaiting(30, role="reviewer")  # in the queue
        now_fx.seed(self.conn, rows=now_fx.ROWS + [reviewer])
        by_item = self.by_item()
        requested = whynot.snapshot(by_item[pr_url(30)], NOW)["features"]
        self.assertEqual(
            (requested["category"], requested["own"]), ("review_queue", "no")
        )
        mine = whynot.features(by_item[pr_url(1)], NOW)  # authored, in the queue
        self.assertEqual((mine["category"], mine["own"]), ("review_queue", "yes"))
        self.assertEqual(whynot.features(by_item[pr_url(4)], NOW)["own"], "yes")
        self.assertEqual(whynot.features(by_item[issue_url(13)], NOW)["own"], "yes")
        self.assertEqual(whynot.features(by_item[issue_url(10)], NOW)["own"], "no")

    def test_dominant_is_the_largest_positive_term(self):
        self.assertEqual(
            whynot.dominant([["a", 1, ""], ["b", 3, ""], ["c", 0, ""]]), "b"
        )
        self.assertIsNone(whynot.dominant([["a", 0, ""], ["b", -1, ""]]))


class BaseRateTests(now_fx.Case):
    def count(self, day=None):
        sql = "SELECT COUNT(*) FROM now_shown" + (" WHERE day = ?" if day else "")
        return self.conn.execute(sql, (day,) if day else ()).fetchone()[0]

    def test_each_shown_item_is_noted_once_a_day(self):
        now_fx.seed(self.conn)
        for _ in range(3):
            views.render("/now", self.conn, self.cfg, NOW)
        self.assertEqual(self.count(), fleet_now.LIMIT)
        later = NOW + timedelta(days=1)
        views.render("/now", self.conn, self.cfg, later)
        self.assertEqual(self.count(whynot._day(later)), fleet_now.LIMIT)
        # Days past the retention window are pruned on the next new note.
        far = NOW + timedelta(days=whynot.RETAIN_DAYS + 2)
        views.render("/now", self.conn, self.cfg, far)
        self.assertEqual(self.count(), fleet_now.LIMIT)
        row = self.conn.execute("SELECT features FROM now_shown LIMIT 1").fetchone()
        self.assertIn("repo", json.loads(row[0]))


class RouteCase(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: NOW
        self.httpd.app.cfg["ingest"]["machines"] = {"mini": "sha256:" + "0" * 64}
        self.httpd.app.cfg["jira"]["enabled"] = False
        conn = db.connect(self.db_path)
        now_fx.seed(conn)
        conn.close()

    def post(self, action, fields):
        body = [("token", self.token), ("source", GH), *fields]
        origin = f"http://127.0.0.1:{self.port}"
        return self.request("POST", f"/overlay/{action}", origin=origin, body=body)

    def rows(self):
        conn = self.conn()
        try:
            return conn.execute(
                "SELECT item_key, reason, text, score, terms, features FROM why_not"
            ).fetchall()
        finally:
            conn.close()

    def now_keys(self):
        page = self.request("GET", "/now")[1]
        return now_fx.item_keys(page.split('data-section="next"', 1)[1])

    def snooze(self, key=pr_url(22), until=None):
        fields = [
            ("key", key),
            ("until", overlay.snooze_until(NOW) if until is None else until),
        ]
        self.assertEqual(self.post("snooze", fields)[0], 200)

    def snoozes(self):
        conn = self.conn()
        try:
            return conn.execute("SELECT item_key, until FROM snoozes").fetchall()
        finally:
            conn.close()


class RouteTests(RouteCase):
    def test_a_reason_after_the_snooze_records_the_score_terms(self):
        conn = db.connect(self.db_path)
        expected = fleet_now.snapshot_of(
            conn, self.httpd.app.cfg, (GH, pr_url(22)), NOW
        )
        conn.close()
        assert expected is not None
        self.snooze()
        snoozed = self.snoozes()
        fields = [
            ("key", pr_url(22)),
            ("reason", "wrong_priority"),
            ("text", "  bots can wait  "),
        ]
        self.assertEqual(self.post("whynot", fields)[0], 200)
        self.assertEqual(self.snoozes(), snoozed)
        [(key, reason, text, total, terms, features)] = self.rows()
        self.assertEqual(
            (key, reason, text), (pr_url(22), "wrong_priority", "bots can wait")
        )
        self.assertEqual(total, expected["score"])
        self.assertEqual(json.loads(terms), expected["terms"])
        self.assertEqual(json.loads(terms)[0][0], "quick_win")
        self.assertEqual(json.loads(features), expected["features"])
        self.assertNotIn(pr_url(22), self.now_keys())

    def test_a_snooze_with_no_reason_records_nothing(self):
        self.snooze()
        self.assertEqual(self.rows(), [])
        self.assertNotIn(pr_url(22), self.now_keys())

    def test_an_item_now_no_longer_ranks_is_kept_with_what_is_known(self):
        self.snooze("gone", "2026-11-01")
        fields = [("key", "gone"), ("reason", "other")]
        self.assertEqual(self.post("whynot", fields)[0], 200)
        self.assertEqual(self.rows(), [("gone", "other", None, None, "[]", "{}")])

    def test_a_reason_for_an_item_with_no_snooze_is_refused(self):
        status, body = self.post("whynot", [("key", pr_url(22)), ("reason", "other")])
        self.assertEqual(status, 400)
        self.assertIn("snooze it first", body)
        self.assertEqual((self.rows(), self.snoozes()), ([], []))
        self.assertIn(pr_url(22), self.now_keys())

    def test_bad_answers_are_refused_and_nothing_is_written(self):
        self.snooze()
        snoozed = self.snoozes()
        for fields in (
            [("key", pr_url(22)), ("reason", "bored")],
            [("key", pr_url(22)), ("reason", "other"), ("text", "x" * 501)],
        ):
            self.assertEqual(self.post("whynot", fields)[0], 400, fields)
        self.assertEqual((self.rows(), self.snoozes()), ([], snoozed))

    def test_health_shows_the_why_not_now_section(self):
        self.snooze()
        self.post("whynot", [("key", pr_url(22)), ("reason", "not_urgent")])
        page = self.request("GET", "/health")[1]
        sec = re.search(r'data-section="why-not">(.*?)</section>', page, re.S)
        assert sec is not None
        self.assertIn("Why not now (1 in 30 days)", sec.group(1))
        self.assertIn('<table data-by="kind">', sec.group(1))
        self.assertIn('<table data-by="dominant">', sec.group(1))
        self.assertIn("<th>quick_win</th>", sec.group(1))
        self.assertIn('<li data-reason="not_urgent">', sec.group(1))


NODE = os.environ.get("TASK_DASHBOARD_NODE") or shutil.which("node") or ""
# A node_modules directory holding jsdom; the repo vendors no npm packages.
JSDOM = os.environ.get("TASK_DASHBOARD_JSDOM") or ""
DOM_JS = ROOT / "scripts" / "test_task_dashboard_whynot_dom.js"


@unittest.skipUnless(
    NODE and JSDOM and Path(JSDOM, "jsdom").is_dir(),
    "needs node and jsdom: set TASK_DASHBOARD_JSDOM to a node_modules holding it",
)
class PageScriptTests(RouteCase):
    """Now's Snooze, reason and Undo, run under jsdom over the served page.
    The script's POSTs are then replayed against the server, so each case
    pins both what the card does and what lands in the DB."""

    KEY = pr_url(22)

    def drive(self, steps, text="", status=200):
        path = Path(self.tmp.name) / "case.json"
        page = self.request("GET", "/now")[1]
        path.write_text(
            json.dumps(
                {"page": page, "key": self.KEY, "steps": steps, "text": text,
                 "status": status}
            )
        )  # fmt: skip
        out = subprocess.run(
            [NODE, str(DOM_JS), JSDOM, str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        state = json.loads(out.stdout)
        self.assertEqual(state["errors"], [])
        self.assertFalse(state["reloaded"])
        return state

    def replay(self, calls):
        """Each POST the script made, sent to the server; their statuses."""
        origin = f"http://127.0.0.1:{self.port}"
        return [
            self.request("POST", c["url"], origin=origin, body=parse_qsl(c["body"]))[0]
            for c in calls
        ]

    def test_one_click_snoozes_and_hides_the_card_in_place(self):
        state = self.drive(["snooze"])
        before, after = state["states"]
        self.assertTrue(before["title_shown"] and not before["line_shown"])
        self.assertFalse(after["title_shown"])
        self.assertTrue(after["connected"] and after["line_shown"])
        until = overlay.snooze_until(NOW)
        self.assertIn(f"Snoozed until {until} · for", after["line_text"])
        self.assertIn("why not now?", after["line_text"])
        self.assertEqual(after["pressed"], ["7d"])
        [call] = state["calls"]
        self.assertEqual(call["url"], "/overlay/snooze")
        self.assertEqual(self.replay(state["calls"]), [200])
        self.assertEqual(self.snoozes(), [(self.KEY, until)])
        self.assertEqual(self.rows(), [])
        self.assertNotIn(self.KEY, self.now_keys())

    def test_a_reason_afterwards_adds_one_answer_and_no_second_snooze(self):
        state = self.drive(["snooze", "reason:not_urgent"], text="after the release")
        self.assertFalse(state["states"][-1]["connected"])  # the line went away
        urls = [c["url"] for c in state["calls"]]
        self.assertEqual(urls, ["/overlay/snooze", "/overlay/whynot"])
        self.assertNotIn("until", dict(parse_qsl(state["calls"][1]["body"])))
        self.assertEqual(self.replay(state["calls"]), [200, 200])
        self.assertEqual(self.snoozes(), [(self.KEY, overlay.snooze_until(NOW))])
        [(key, reason, text, total, terms, _)] = self.rows()
        self.assertEqual(
            (key, reason, text), (self.KEY, "not_urgent", "after the release")
        )
        self.assertIsNotNone(total)
        self.assertEqual(json.loads(terms)[0][0], "quick_win")

    def test_a_length_re_snoozes_for_that_long_and_keeps_the_line(self):
        state = self.drive(["snooze", "for:30"])
        after = state["states"][-1]
        until = overlay.snooze_until(NOW, 30)
        self.assertTrue(after["connected"] and after["line_shown"])
        self.assertIn(f"Snoozed until {until} · for", after["line_text"])
        self.assertEqual(after["pressed"], ["30d"])
        self.assertEqual(
            [dict(parse_qsl(c["body"]))["until"] for c in state["calls"]],
            [overlay.snooze_until(NOW), until],
        )
        self.assertEqual(self.replay(state["calls"]), [200, 200])
        self.assertEqual(self.snoozes(), [(self.KEY, until)])
        self.assertEqual(self.rows(), [])

    def test_undo_removes_the_snooze_and_brings_the_card_back(self):
        state = self.drive(["snooze", "undo"])
        back = state["states"][-1]
        self.assertTrue(back["connected"] and back["title_shown"])
        self.assertFalse(back["line_shown"])
        self.assertEqual(self.replay(state["calls"]), [200, 200])
        self.assertEqual(self.snoozes(), [])
        self.assertEqual(self.rows(), [])
        self.assertIn(self.KEY, self.now_keys())

    def test_a_refused_snooze_leaves_the_card_and_says_so(self):
        state = self.drive(["snooze"], status=500)
        after = state["states"][-1]
        self.assertTrue(after["title_shown"] and not after["line_shown"])
        self.assertEqual(len(state["alerts"]), 1)
        self.assertIn("Not saved (500)", state["alerts"][0])


class JiraHealthTest(server_harness.ServerCase):
    def test_a_jira_instance_has_the_why_not_section_too(self):
        # Every instance's Now asks "why not now?", so its Health shows them.
        self.assertIn('data-section="why-not"', self.request("GET", "/health")[1])

    def test_snoozing_on_a_jira_instances_now_asks_why_not(self):
        conn = self.conn()
        conn.execute(
            "INSERT INTO issues (source, kind, key, summary, status_name,"
            " status_category, scope) VALUES ('jira', 'ticket', 'ABC-1', 's',"
            " 'In Progress', 'indeterminate', 'search')"
        )
        conn.commit()
        conn.close()
        page = self.request("GET", "/now")[1]
        self.assertIn('data-source="jira"', page)
        self.assertIn('data-ov="snooze"', page)
        self.assertEqual(page.count('data-ov="snooze"'), page.count('data-why="1"'))
        self.assertIn('class="whynot"', page)


class SectionTests(now_fx.Case):
    def test_latest_twenty_newest_first_and_the_window(self):
        for i in range(25):
            answer(self.conn, i, "other", snap(), f"t{i}", NOW - timedelta(hours=i))
        answer(self.conn, 99, "other", snap(), "old", NOW - timedelta(days=31))
        html = whynot.section(self.conn, NOW)
        self.assertIn("Why not now (25 in 30 days)", html)
        texts = re.findall(r"— (t\d+|old)", html)
        self.assertEqual(texts, [f"t{i}" for i in range(20)])

    def test_empty(self):
        self.assertIn("No answers in the last 30 days", whynot.section(self.conn, NOW))


class TuneTests(now_fx.Case):
    def tune(self):
        return whynot.tune(self.conn, WEIGHTS, NOW)

    def weights(self, doc):
        return {w["term"]: w for w in doc["weights"]}

    def test_a_term_dominating_wrong_priority_answers_is_suggested_lower(self):
        for i in range(3):
            answer(self.conn, i, "wrong_priority", snap(top="quick_win"))
        answer(self.conn, 3, "wrong_priority", snap(top="asked", value=8.0))
        answer(self.conn, 4, "not_urgent", snap(top="asked", value=8.0))
        doc = self.tune()
        w = self.weights(doc)
        self.assertEqual(
            (w["quick_win"]["wrong_priority_dominant"], w["quick_win"]["share"]),
            (3, 0.75),
        )
        self.assertEqual(
            w["quick_win"]["suggest"]["weight"],
            WEIGHTS["categories"]["quick_win"] * whynot.LOWER_BY,
        )
        self.assertIn("3 of 4", w["quick_win"]["suggest"]["why"])
        self.assertIsNone(
            w["asked"]["suggest"]
        )  # 1 of 4, and not_urgent is not counted
        self.assertEqual(
            set(w), set(now_fx.score.FLEET_CATEGORIES + now_fx.score.TERMS)
        )
        self.assertEqual(w["quick_win"]["table"], "categories")
        self.assertEqual(w["deadline"]["table"], "terms")
        self.assertEqual(doc["by_reason"], {"wrong_priority": 4, "not_urgent": 1})
        self.assertEqual(doc["rows"][0]["terms"], {"quick_win": 10.0, "age": 0.2})
        self.assertEqual(doc["rows"][0]["dominant"], "quick_win")

    def test_too_few_answers_suggest_nothing(self):
        for i in range(2):
            answer(self.conn, i, "wrong_priority", snap(top="quick_win"))
        self.assertTrue(all(w["suggest"] is None for w in self.tune()["weights"]))

    def test_answers_outside_the_window_are_left_out(self):
        for i in range(3):
            answer(self.conn, i, "wrong_priority", snap(), at=NOW - timedelta(days=40))
        doc = self.tune()
        self.assertEqual(doc["answers"], 0)
        self.assertIsNone(self.weights(doc)["quick_win"]["suggest"])

    def test_snoozes_concentrated_on_one_repo_make_repo_a_candidate(self):
        texts = [
            "finplan can wait",
            "",
            "waiting on Sam",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
        for i in range(11):
            repo = "bestdan/finplan" if i < 8 else "bestdan/other"
            answer(self.conn, i, "not_urgent", snap(repo=repo), texts[i])
        # Now showed 10 item-days: 3 of them finplan.
        for i in range(10):
            shown(self.conn, i, "bestdan/finplan" if i < 3 else "bestdan/other")
        shown(self.conn, 99, "bestdan/finplan", day="2026-09-01")  # outside the window
        doc = self.tune()
        self.assertEqual(doc["shown_item_days"], 10)
        [c] = [c for c in doc["candidates"] if c["feature"] == "repo"]
        self.assertEqual(c["value"], "bestdan/finplan")
        self.assertEqual(c["possible_term"], "repo:bestdan/finplan")
        self.assertIsNone(c["scored_by"])
        self.assertEqual((c["snoozes"], c["snooze_share"]), (8, round(8 / 11, 3)))
        self.assertEqual((c["shown"], c["shown_share"]), (3, 0.3))
        self.assertEqual(
            c["summary"],
            "73% of snoozes are repo bestdan/finplan, which is 30% of Now's items",
        )
        self.assertEqual(c["free_text"], ["finplan can wait", "waiting on Sam"])
        self.assertEqual(c["reasons"], {"not_urgent": 8})
        # The repo the snoozes do not lean towards is no candidate.
        self.assertFalse(
            [x for x in doc["candidates"] if x["value"] == "bestdan/other"]
        )

    def test_no_base_rate_means_no_candidates(self):
        for i in range(5):
            answer(self.conn, i, "not_urgent", snap(repo="bestdan/finplan"))
        doc = self.tune()
        self.assertEqual(doc["candidates"], [])
        text = whynot.text_report(doc)
        self.assertIn("no base rate recorded yet", text)
        self.assertNotIn("nothing to suggest", text)
        shown(self.conn, 1, "bestdan/finplan")
        self.assertNotIn("no base rate", whynot.text_report(self.tune()))


class CliTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.db_path = self.tmp / "d.db"
        self.cfg_path = self.tmp / "config.toml"
        self.cfg_path.write_text(
            "[score.categories]\nquick_win = 8\n[jira]\nenabled = false\n"
        )

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(DASHBOARD), "tune", *args, "--db", str(self.db_path),
             "--config", str(self.cfg_path)],
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip

    def test_tune_json_reads_the_configured_weights_and_writes_nothing(self):
        conn = db.connect(self.db_path)
        for i in range(3):
            answer(conn, i, "wrong_priority", snap(), at=datetime.now(timezone.utc))
        conn.close()
        before = self.cfg_path.read_bytes()
        out = self.cli("--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        doc = json.loads(out.stdout)
        w = {x["term"]: x for x in doc["weights"]}
        self.assertEqual(w["quick_win"]["weight"], 8.0)
        self.assertEqual(w["quick_win"]["suggest"]["weight"], 6.0)
        self.assertIn("Suggestions only", doc["note"])
        self.assertEqual(self.cfg_path.read_bytes(), before)
        text = self.cli()
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("weight score.categories.quick_win: 8 -> 6", text.stdout)

    def test_a_bad_window_is_refused(self):
        self.assertEqual(self.cli("--days", "0").returncode, 2)

    def test_tune_runs_on_a_jira_only_instance(self):
        self.cfg_path.write_text(
            '[jira]\nsite = "x.atlassian.net"\nboard_ids = [1]\n'
            '[jira.fields]\nsprint = "customfield_1"\nflagged = "customfield_2"\n'
            "[github]\nenabled = false\n"
        )
        conn = db.connect(self.db_path)
        answer(conn, 0, "wrong_priority", snap(), at=datetime.now(timezone.utc))
        conn.close()
        out = self.cli("--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["answers"], 1)

    def test_an_old_score_table_is_refused_naming_the_new_one(self):
        for old, new in (("fleet", "categories"), ("weights", "terms")):
            self.cfg_path.write_text(f"[score.{old}]\n[jira]\nenabled = false\n")
            out = self.cli("--json")
            self.assertEqual(out.returncode, 2)
            self.assertIn(f"use [score.{new}]", out.stderr)


if __name__ == "__main__":
    unittest.main()
