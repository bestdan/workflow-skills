#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/agent_view.py and the CLI's
`view` and `overlay` commands.

Fixture DBs come from the other suites' seeds, written through the real
writers; pages are rendered in process, and the one server test runs with a
fake sync function, so no acli, gh, git or network runs. Needs Python 3.11:
run via scripts/test-task-dashboard-agent-view.sh.
"""

import contextlib
import copy
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import agent_view  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_now  # type: ignore[import-not-found]  # noqa: E402
import fleet_views  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import include  # type: ignore[import-not-found]  # noqa: E402
import lane_tabs  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402
import snapshot_adapter  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_fleet_now as now_fx  # noqa: E402
import test_task_dashboard_needs as needs_fx  # noqa: E402
import test_task_dashboard_fleet_views as stale_fx  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402
import views  # type: ignore[import-not-found]  # noqa: E402

TAG = re.compile(r"<[a-zA-Z/!]")
WT = stale_fx.WT
WORKTREE = ("machine:mini", f"worktree:{WT}/task-1135")

CONFIG_TOML = """
[jira]
site = "example.invalid"
board_ids = [77]
[jira.fields]
sprint = "customfield_10303"
flagged = "customfield_10007"
[github]
enabled = true
scope = "owner"
"""


def page(conn, cfg, view, now, inc=None):
    """The view's body as the server serves it: nav, lanes and tabs."""
    path = agent_view.PATHS[view]
    if view == "board":
        b = server.board_data(conn, cfg)
        return server.render_board(b, cfg["jira"]["site"], overlay.load(conn), now, inc)
    return views.render(path, conn, cfg, now, inc, {})


def sections(body):
    """Each `section.lane`'s id -> its inner HTML."""
    return {
        m.group(1): m.group(2)
        for m in re.finditer(
            r'<section class="lane[^"]*" data-section="([^"]+)"[^>]*>(.*?)</section>',
            body,
            re.S,
        )
    }


def card_keys(html_text):
    return re.findall(
        r'class="card[^"]*" data-source="[^"]*" data-key="([^"]*)"', html_text
    )


def titles(html_text):
    return [m.group(1) for m in re.finditer(r'class="title"[^>]*>([^<]+)<', html_text)]


class Case(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = Path(self._td.name) / "d.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)

    def assert_parity(self, cfg, view, now, inc=None):
        """Every lane the page draws is in the document with the same count,
        and an off lane is off in both."""
        body = page(self.conn, cfg, view, now, inc)
        doc = agent_view.build(self.conn, cfg, view, now, inc)
        by_id = {lane["id"]: lane for lane in doc["lanes"]}
        found = lane_tabs.lanes(body)
        inner = sections(body)
        for lane in found:
            self.assertIn(lane.id, by_id, (view, lane.id))
            got = by_id[lane.id]
            self.assertEqual(got["off"], lane.off, (view, lane.id))
            if "<h2>" in inner[lane.id][:4]:
                self.assertEqual(got["count"], lane.count, (view, lane.id))
                self.assertEqual(got["title"], lane.title, (view, lane.id))
            else:  # a lane the tab names has no heading: count its cards
                n = len(re.findall(r'<div class="card', inner[lane.id]))
                self.assertEqual(got["count"], n, (view, lane.id))
        extra = set(by_id) - {lane.id for lane in found}
        # Epics' Other is a closed list, not a section; nothing else is extra.
        self.assertLessEqual(extra, {"other"}, view)
        return body, doc


class StaleParityTests(Case):
    def setUp(self):
        super().setUp()
        stale_fx.seed(self.conn)
        self.cfg = stale_fx.fleet_cfg()

    def test_the_same_items_lanes_and_counts_as_the_page(self):
        body, doc = self.assert_parity(self.cfg, "stale", stale_fx.NOW)
        lanes = sections(body)
        for lane in doc["lanes"]:
            self.assertEqual(
                [i["title"] for i in lane["items"]],
                titles(lanes[lane["id"]]),
                lane["id"],
            )
        self.assertGreater(sum(lane["count"] for lane in doc["lanes"]), 5)

    def test_items_carry_host_path_evidence_risks_and_a_structured_action(self):
        doc = agent_view.build(self.conn, self.cfg, "stale", stale_fx.NOW)
        items = {i["key"]: i for lane in doc["lanes"] for i in lane["items"]}
        landed = items[WORKTREE[1]]
        self.assertEqual(landed["lane"], "close")
        self.assertEqual((landed["source"], landed["host"]), ("machine:mini", "mini"))
        self.assertEqual(landed["path"], f"{WT}/task-1135")
        self.assertEqual(landed["evidence"], "closed 1135 (#1135) merged 18h ago")
        self.assertEqual(
            landed["action"], {"command": ["worktree-remove.sh", f"{WT}/task-1135"]}
        )
        dirty = items[f"worktree:{WT}/task-1100"]
        self.assertEqual(dirty["risks"], ["uncommitted changes"])
        self.assertEqual(dirty["action"]["review"], "look before removing")
        pr = items[stale_fx.pr_url(900)]
        self.assertEqual(
            pr["action"],
            {
                "review": "review it: merge, close, or say what it waits on",
                "url": stale_fx.pr_url(900),
            },
        )
        self.assertEqual(pr["ref"], f"{stale_fx.REPO}#900")

    def test_freshness_names_each_host(self):
        doc = agent_view.build(self.conn, self.cfg, "stale", stale_fx.NOW)
        self.assertEqual(doc["schema"], agent_view.SCHEMA_VERSION)
        self.assertEqual(
            doc["freshness"]["scanned"]["mini"]["snapshot_at"],
            stale_fx.SNAPSHOT["taken_at"],
        )

    def test_every_group_off_is_off_in_both(self):
        inc = include.Include("stale", frozenset(), "url")
        _, doc = self.assert_parity(self.cfg, "stale", stale_fx.NOW, inc)
        self.assertTrue(all(lane["off"] and not lane["items"] for lane in doc["lanes"]))
        every = agent_view.build(self.conn, self.cfg, "stale", stale_fx.NOW, inc, True)
        hidden = {i["hidden"] for lane in every["lanes"] for i in lane["items"]}
        self.assertEqual(hidden, {"filtered"})

    def test_all_marks_an_ignored_item_and_keeps_the_counts(self):
        overlay.ignore(self.conn, WORKTREE, stale_fx.NOW)
        doc = agent_view.build(self.conn, self.cfg, "stale", stale_fx.NOW)
        every = agent_view.build(
            self.conn, self.cfg, "stale", stale_fx.NOW, show_all=True
        )
        keys = [i["key"] for lane in doc["lanes"] for i in lane["items"]]
        self.assertNotIn(WORKTREE[1], keys)
        hidden = [i for lane in every["lanes"] for i in lane["items"] if i["hidden"]]
        self.assertEqual(
            [(i["key"], i["hidden"]) for i in hidden], [(WORKTREE[1], "ignored")]
        )
        self.assertEqual(
            [lane["count"] for lane in every["lanes"]],
            [lane["count"] for lane in doc["lanes"]],
        )
        self.assertIn(WORKTREE[1], [i["key"] for i in doc["ignored"]])

    def test_an_ignored_handoff_leaves_stale(self):
        item = ("machine:mini", f"handoff:{WT}/.handoffs/dated.md")
        self.assertIn(
            "aim dated", fleet_views.render_stale(self.conn, self.cfg, stale_fx.NOW)
        )
        overlay.ignore(self.conn, item, stale_fx.NOW)
        self.assertNotIn(
            "aim dated", fleet_views.render_stale(self.conn, self.cfg, stale_fx.NOW)
        )


class FleetParityTests(Case):
    """Pull requests, Needs you, Now and Issues on the Now suite's fixture."""

    def setUp(self):
        super().setUp()
        now_fx.seed(self.conn)
        self.cfg = now_fx.fleet_cfg()
        doc = agent_view.build(self.conn, self.cfg, "prs", now_fx.NOW)
        self.ignored, self.snoozed = [
            i["key"] for lane in doc["lanes"] for i in lane["items"]
        ][:2]
        overlay.ignore(self.conn, ("github", self.ignored), now_fx.NOW)
        overlay.snooze(self.conn, ("github", self.snoozed), "2999-01-01")

    def test_prs_needs_now_and_issues_match_their_pages(self):
        for view in ("prs", "needs", "now", "issues"):
            body, doc = self.assert_parity(self.cfg, view, now_fx.NOW)
            lanes = sections(body)
            for lane in doc["lanes"]:
                keys = card_keys(lanes[lane["id"]])
                self.assertEqual(
                    [i["key"] for i in lane["items"]], keys, (view, lane["id"])
                )

    def test_the_include_filter_applies_as_on_the_page(self):
        inc = include.Include("prs", frozenset({"tasks"}), "url")
        _, doc = self.assert_parity(self.cfg, "prs", now_fx.NOW, inc)
        self.assertTrue(all(lane["off"] for lane in doc["lanes"]))

    def test_all_marks_ignored_and_snoozed_prs(self):
        doc = agent_view.build(self.conn, self.cfg, "prs", now_fx.NOW, show_all=True)
        hidden = {
            i["key"]: i["hidden"]
            for lane in doc["lanes"]
            for i in lane["items"]
            if i["hidden"]
        }
        self.assertEqual(
            hidden,
            {self.ignored: "ignored", self.snoozed: "snoozed"},
        )
        self.assertEqual(doc["notes"]["snoozed"], 1)

    def test_no_html_in_any_field_and_every_action_is_structured(self):
        for cfg, view, now in (
            (self.cfg, "needs", now_fx.NOW),
            (self.cfg, "now", now_fx.NOW),
            (self.cfg, "prs", now_fx.NOW),
            (self.cfg, "issues", now_fx.NOW),
        ):
            doc = agent_view.build(self.conn, cfg, view, now, show_all=True)
            self.assertIsNone(TAG.search(json.dumps(doc)), view)
            for lane in doc["lanes"]:
                for i in lane["items"]:
                    act = i["action"]
                    if act is not None:
                        self.assertLessEqual(set(act), {"review", "command", "url"})
                        self.assertIsInstance(act.get("command", []), list)
        doc = agent_view.build(self.conn, self.cfg, "needs", now_fx.NOW)
        human = [
            i for lane in doc["lanes"] for i in lane["items"] if i["kind"] == "issue"
        ]
        self.assertEqual(
            human[0]["action"]["review"], "decide it, then `/promote-tasks`"
        )


class SessionLinkTests(Case):
    """Needs you and Now carry a hook session's Remote Control id and url."""

    def setUp(self):
        super().setUp()
        needs_fx.seed(self.conn)
        needs_fx.seed_registry(self.conn)
        self.cfg = needs_fx.fleet_cfg()

    def test_needs_and_now_carry_the_bridge_id_and_the_session_url(self):
        for view in ("needs", "now"):
            doc = agent_view.build(self.conn, self.cfg, view, needs_fx.NOW)
            items = {i["key"]: i for lane in doc["lanes"] for i in lane["items"]}
            turn = items["s-turn"]
            self.assertEqual(turn["bridge_session_id"], needs_fx.BRIDGE, view)
            url = f"https://claude.ai/code/{needs_fx.BRIDGE}"
            self.assertEqual(turn["url"], url, view)
            self.assertEqual(turn["action"]["url"], url, view)
            perm = items["s-perm"]
            self.assertIsNone(perm["bridge_session_id"], view)
            self.assertEqual(perm["action"]["url"], "https://claude.ai/code", view)

    def test_needs_and_now_carry_the_transcript_summary_and_the_pr(self):
        needs_fx.post_transcripts(self.conn, [
            needs_fx.summary("s-turn", "edit the notes", needs_fx.ASK, "bestdan/n"),
        ])  # fmt: skip
        for view in ("needs", "now"):
            doc = agent_view.build(self.conn, self.cfg, view, needs_fx.NOW)
            items = {i["key"]: i for lane in doc["lanes"] for i in lane["items"]}
            turn = items["s-turn"]
            self.assertEqual(
                (turn["title"], turn["first_prompt"], turn["last_ask"],
                 turn["branch"], turn["pr"]),
                ("edit the notes", "edit the notes", needs_fx.ASK, "bestdan/n", None),
                view,
            )  # fmt: skip
            pr_item = items["s-pr"]
            self.assertIsNone(pr_item["first_prompt"], view)
            self.assertEqual(pr_item["branch"], "bestdan/task-1101", view)
            self.assertEqual(
                pr_item["pr"],
                {"number": 1201, "url": needs_fx.pr_url(1201), "repo": needs_fx.REPO},
                view,
            )

    def test_an_ended_session_is_finished_with_its_resume_and_left_out_of_now(self):
        needs_fx.post_registry(
            self.conn, "2026-10-24T11:59:40.000Z", needs_fx.WITHOUT_TURN
        )
        doc = agent_view.build(self.conn, self.cfg, "needs", needs_fx.NOW)
        lanes = {lane["id"]: lane for lane in doc["lanes"]}
        self.assertNotIn("s-turn", [i["key"] for i in lanes["needs"]["items"]])
        [turn] = [i for i in lanes["finished"]["items"] if i["key"] == "s-turn"]
        self.assertEqual(turn["evidence"], "ended")
        self.assertEqual(
            turn["action"],
            {"review": needs_fx.RESUME, "command": ["claude", "--resume", "s-turn"]},
        )
        self.assertIsNone(turn["url"])
        now = agent_view.build(self.conn, self.cfg, "now", needs_fx.NOW)
        keys = [i["key"] for lane in now["lanes"] for i in lane["items"]]
        self.assertNotIn("s-turn", keys)
        self.assertIn("s-perm", keys)

    def test_an_ended_session_carries_its_transcript_summary_and_pr(self):
        needs_fx.post_transcripts(self.conn, [
            needs_fx.summary("s-pr", "fix task 1101", needs_fx.ASK,
                             bridge="session_01ENDED"),
        ], registry=needs_fx.WITHOUT_PR)  # fmt: skip
        doc = agent_view.build(self.conn, self.cfg, "needs", needs_fx.NOW)
        lanes = {lane["id"]: lane for lane in doc["lanes"]}
        [ended] = [i for i in lanes["finished"]["items"] if i["key"] == "s-pr"]
        self.assertEqual(
            (ended["evidence"], ended["title"], ended["first_prompt"],
             ended["last_ask"], ended["pr"]),
            ("ended", "fix task 1101", "fix task 1101", needs_fx.ASK,
             {"number": 1201, "url": needs_fx.pr_url(1201), "repo": needs_fx.REPO}),
        )  # fmt: skip
        self.assertIsNone(ended["url"])
        self.assertIsNone(ended["bridge_session_id"])
        self.assertEqual(ended["action"]["command"], ["claude", "--resume", "s-pr"])


class NowLimitTests(Case):
    def test_all_marks_what_the_list_length_cut_as_limit(self):
        now_fx.seed(self.conn)
        cfg = now_fx.fleet_cfg()
        self.assertGreater(len(now_fx.EXPECTED), fleet_now.LIMIT)
        doc = agent_view.build(self.conn, cfg, "now", now_fx.NOW, show_all=True)
        (lane,) = [lane for lane in doc["lanes"] if lane["id"] == "next"]
        shown = [i["key"] for i in lane["items"] if i["hidden"] is None]
        cut = {i["key"]: i["hidden"] for i in lane["items"] if i["hidden"]}
        self.assertEqual(shown, now_fx.EXPECTED[: fleet_now.LIMIT])
        self.assertEqual(cut, {k: "limit" for k in now_fx.EXPECTED[fleet_now.LIMIT :]})
        self.assertEqual(lane["count"], fleet_now.LIMIT)


class EscapeOnceTests(Case):
    """A session's host and branch are plain text in the JSON and escaped
    exactly once on the page."""

    def test_an_ampersand_in_a_host_and_a_branch(self):
        snap = copy.deepcopy(stale_fx.SNAPSHOT)
        snap["host"] = "r&d"
        for line in snap["sessions"]:
            if line["session_id"] == "s-idle":
                line["branch"] = "fix&go"
        github_fleet.write(self.conn, stale_fx.GITHUB_ROWS, stale_fx.NOW)
        snapshot_adapter.ingest(self.conn, snap, stale_fx.NOW)
        cfg = stale_fx.fleet_cfg()
        doc = agent_view.build(self.conn, cfg, "stale", stale_fx.NOW)
        (item,) = [
            i for lane in doc["lanes"] for i in lane["items"] if i["key"] == "s-idle"
        ]
        self.assertTrue(item["meta"].startswith("on r&d · dotfiles: fix&go · "))
        self.assertEqual(item["host"], "r&d")
        page = fleet_views.render_stale(self.conn, cfg, stale_fx.NOW)
        self.assertIn("on r&amp;d · dotfiles: fix&amp;go · last event", page)
        self.assertNotIn("&amp;amp;", page)


class StaleHtmlFreeTests(Case):
    def test_stale_has_no_html(self):
        stale_fx.seed(self.conn)
        doc = agent_view.build(
            self.conn, stale_fx.fleet_cfg(), "stale", stale_fx.NOW, show_all=True
        )
        self.assertIsNone(TAG.search(json.dumps(doc)))


class JiraParityTests(Case):
    def setUp(self):
        super().setUp()
        server_harness.seed(self.conn)
        self.cfg = server_harness.make_config()
        overlay.pin(self.conn, ("jira", "ABC-4"), stale_fx.NOW)

    def test_every_jira_view_matches_its_page(self):
        views_ = agent_view.available(self.cfg)
        self.assertEqual(
            views_, ["now", "stale", "board", "waiting-on-me", "waiting-on", "epics"]
        )
        for view in views_:
            self.assert_parity(self.cfg, view, stale_fx.NOW)
            off = include.Include(view, frozenset({"other"}), "url")
            self.assert_parity(self.cfg, view, stale_fx.NOW, off)

    def test_a_view_this_instance_does_not_show_is_refused(self):
        with self.assertRaises(agent_view.ViewError):
            agent_view.build(self.conn, self.cfg, "needs", stale_fx.NOW)


class CliTests(Case):
    def setUp(self):
        super().setUp()
        stale_fx.seed(self.conn)
        self.cfg_path = Path(self._td.name) / "config.toml"
        self.cfg_path.write_text(CONFIG_TOML)
        self._now = dashboard._now
        dashboard._now = lambda: stale_fx.NOW
        self.addCleanup(setattr, dashboard, "_now", self._now)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dashboard.main(
                [*argv, "--db", str(self.db_path), "--config", str(self.cfg_path)]
            )
        return code, out.getvalue(), err.getvalue()

    def stale_keys(self):
        code, out, _ = self.run_cli("view", "stale", "--json")
        self.assertEqual(code, 0)
        return [i["key"] for lane in json.loads(out)["lanes"] for i in lane["items"]]

    def test_overlay_ignore_hides_the_item_on_the_page_and_in_view(self):
        self.assertIn(WORKTREE[1], self.stale_keys())
        code, out, err = self.run_cli(
            "overlay", "ignore", "--source", WORKTREE[0], "--key", WORKTREE[1]
        )
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn(WORKTREE[1], self.stale_keys())
        cfg = stale_fx.fleet_cfg()
        body = fleet_views.render_stale(self.conn, cfg, stale_fx.NOW)
        self.assertNotIn(f"worktree {WT}/task-1135<", body)
        code, out, _ = self.run_cli("view", "stale", "--json", "--all")
        hidden = [
            i["hidden"]
            for lane in json.loads(out)["lanes"]
            for i in lane["items"]
            if i["key"] == WORKTREE[1]
        ]
        self.assertEqual(hidden, ["ignored"])

    def test_snooze_from_the_cli_goes_through_the_same_validation(self):
        code, _, err = self.run_cli(
            "overlay",
            "snooze",
            "--source",
            "github",
            "--key",
            "k",
            "--until",
            "2026-13-01",
        )
        self.assertEqual(code, 1)
        with self.assertRaises(overlay.OverlayError) as raised:
            overlay.apply(
                self.conn,
                "snooze",
                {"source": ["github"], "key": ["k"], "until": ["2026-13-01"]},
            )
        self.assertIn(str(raised.exception), err)

    def test_an_unknown_action_is_refused_and_writes_nothing(self):
        before = overlay.export(self.conn, stale_fx.NOW)
        code, _, err = self.run_cli("overlay", "delete", "--key", "ABC-1")
        self.assertEqual(code, 1)
        self.assertIn("unknown overlay action: 'delete'", err)
        self.assertEqual(overlay.export(self.conn, stale_fx.NOW), before)

    def test_text_mode_prints_one_line_per_item(self):
        code, out, _ = self.run_cli("view", "stale")
        self.assertEqual(code, 0)
        doc = agent_view.build(self.conn, stale_fx.fleet_cfg(), "stale", stale_fx.NOW)
        n = sum(len(lane["items"]) for lane in doc["lanes"])
        self.assertEqual(len([ln for ln in out.splitlines() if ln.startswith("  ")]), n)
        self.assertIn(
            f"worktree @mini worktree {WT}/task-1135 -> worktree-remove.sh", out
        )

    def test_an_unknown_view_or_group_exits_2(self):
        self.assertEqual(self.run_cli("view", "nope")[0], 2)
        self.assertEqual(self.run_cli("view", "stale", "--include", "bogus")[0], 2)


class ServerMessageTest(server_harness.ServerCase):
    """The CLI refuses a bad key with the message the server answers with."""

    def test_same_message(self):
        status, body = self.request(
            "POST",
            "/overlay/ignore",
            origin=f"http://127.0.0.1:{self.port}",
            body=[("token", self.token), ("source", "github"), ("key", " ")],
        )
        self.assertEqual(status, 400)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dashboard.main(
                ["overlay", "ignore", "--source", "github", "--key", " ",
                 "--db", str(self.db_path)]
            )  # fmt: skip
        self.assertEqual(code, 1)
        self.assertIn(body.strip(), err.getvalue())


if __name__ == "__main__":
    unittest.main()
