#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/include.py, the per-view filters.

Fixture DBs are built through the real writers (the Needs you and fleet view
suites' seeds, and the server suite's Jira seed); pages are rendered directly
and through the in-process server, which runs with a fake sync function. No
acli, gh, git, claude or network runs. Needs Python 3.11: run via
scripts/test-task-dashboard-include.sh.
"""

import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import claude_remote  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_views  # type: ignore[import-not-found]  # noqa: E402
import include  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402
import sessions  # type: ignore[import-not-found]  # noqa: E402
import sync  # type: ignore[import-not-found]  # noqa: E402
import views  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_fleet_views as fleet_fx  # noqa: E402
import test_task_dashboard_needs as needs_fx  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

MINIMAL = """
[jira]
site = "x.atlassian.net"
board_ids = [1]
[jira.fields]
sprint = "customfield_1"
flagged = "customfield_2"
"""

VIEW_PATHS = (
    "/",
    "/needs",
    "/now",
    "/away",
    "/waiting-on-me",
    "/waiting-on",
    "/board",
    "/stale",
    "/epics",
    "/health",
)


def inc(*groups, view=""):
    return include.Include(view, frozenset(groups), "url")


class GroupTests(unittest.TestCase):
    def test_every_mirror_kind_has_its_group(self):
        expected = {
            sync.KIND: "tasks",
            "issue": "tasks",
            "pr": "prs",
            "closed_pr": "prs",
            sessions.KIND: "sessions",
            claude_remote.KIND: "sessions",
            "worktree": "other",
            "handoff": "other",
            "tmux": "other",
            "design": "other",
        }
        for kind, group in expected.items():
            self.assertEqual(include.group_of(kind), group, kind)
        self.assertEqual(include.group_of("something-new"), "other")

    def test_views_are_the_routes_plus_board_and_health(self):
        # Kept by hand (include.py imports nothing from the package), so a new
        # route must be added there too, or remembering its choice is a 400.
        routes = {p.lstrip("/") for p in views.ROUTES}
        self.assertEqual(set(include.VIEWS), routes | {"board", "health"})
        self.assertEqual(len(include.VIEWS), len(set(include.VIEWS)))

    def test_parse(self):
        self.assertEqual(include.parse(["tasks,prs"]), {"tasks", "prs"})
        self.assertEqual(include.parse(["tasks", " prs ,"]), {"tasks", "prs"})
        self.assertEqual(include.parse([""]), frozenset())
        with self.assertRaises(include.IncludeError) as cm:
            include.parse(["tasks,bugs"])
        self.assertIn("bugs", str(cm.exception))
        self.assertIn("tasks, prs, sessions, other", str(cm.exception))

    def test_an_absent_include_is_not_an_empty_one(self):
        self.assertIsNone(include.from_query({}))
        self.assertEqual(include.from_query({"include": [""]}), frozenset())

    def test_url_then_remembered_then_config_then_everything(self):
        cfg = {"views": {"now": {"include": ["prs"]}}}
        url = {"include": ["sessions"]}
        remembered = frozenset({"tasks"})
        r = include.resolve("now", url, remembered, cfg)
        self.assertEqual((r.groups, r.origin), ({"sessions"}, "url"))
        r = include.resolve("now", {}, remembered, cfg)
        self.assertEqual((r.groups, r.origin), ({"tasks"}, "remembered"))
        r = include.resolve("now", {}, None, cfg)
        self.assertEqual((r.groups, r.origin), ({"prs"}, "config"))
        r = include.resolve("epics", {}, None, cfg)
        self.assertEqual((r.groups, r.origin), (set(include.GROUPS), "default"))
        self.assertTrue(r.everything)


class ConfigTests(unittest.TestCase):
    def load(self, extra):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.toml"
            p.write_text(extra + MINIMAL)
            return config.load(p)

    def test_defaults_validate(self):
        cfg = self.load('[views.needs]\ninclude = ["prs", "sessions"]\n')
        self.assertEqual(cfg["views"]["needs"]["include"], ["prs", "sessions"])
        self.assertEqual(include.config_default(cfg, "needs"), {"prs", "sessions"})
        self.assertEqual(self.load("")["views"], {})
        # Read the way validation read it: whitespace and commas included.
        cfg = self.load('[views.now]\ninclude = [" prs ", "tasks,sessions"]\n')
        self.assertEqual(
            include.config_default(cfg, "now"), {"prs", "tasks", "sessions"}
        )
        self.assertTrue(include.resolve("now", {}, None, cfg).shows("pr"))

    def test_bad_values_are_config_errors(self):
        cases = {
            '[views.needs]\ninclude = ["bugs"]\n': "valid groups are",
            '[views.nope]\ninclude = ["prs"]\n': "valid views are",
            '[views.needs]\ninclude = "prs"\n': "list of group names",
            "[views.needs]\ninclude = [1]\n": "list of group names",
            '[views.needs]\ninclude = ["prs"]\nshow = 1\n': "only `include",
            'views = "x"\n': "must be a table",
        }
        for text, message in cases.items():
            with self.assertRaises(config.ConfigError, msg=text) as cm:
                self.load(text)
            self.assertIn(message, str(cm.exception), text)


def switch_attrs(row):
    """Each switch's attributes, keyed by its group (`all` for the All switch)."""
    out = {}
    for attrs in re.findall(r'<a role="switch"([^>]*)>', row):
        pairs = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        out[pairs.get("data-group", "all" if "data-all" in pairs else "?")] = pairs
    return out


class SwitchTests(unittest.TestCase):
    """What each switch's plain click and shift-click ask for."""

    def row(self, *groups, query=None):
        return switch_attrs(
            include.switches(inc(*groups, view="now"), "/now", query or {})
        )

    def test_a_plain_click_with_everything_shown_shows_that_group_alone(self):
        sw = self.row(*include.GROUPS)
        self.assertEqual(sw["prs"]["data-include"], "prs")
        self.assertEqual(sw["prs"]["href"], "/now?include=prs")
        self.assertEqual(sw["tasks"]["data-include"], "tasks")

    def test_clicking_the_only_group_shown_goes_back_to_every_group(self):
        sw = self.row("prs")
        self.assertEqual(sw["prs"]["data-include"], "tasks,prs,sessions,other")
        self.assertEqual(sw["prs"]["href"], "/now?include=tasks,prs,sessions,other")
        # Another group still goes solo.
        self.assertEqual(sw["tasks"]["data-include"], "tasks")
        # With two shown, clicking one of them goes solo, not back to all.
        self.assertEqual(self.row("tasks", "prs")["prs"]["data-include"], "prs")
        # With none shown, a click shows that group.
        self.assertEqual(self.row()["sessions"]["data-include"], "sessions")

    def test_all_shows_every_group_and_is_on_only_when_every_group_is(self):
        for groups, on in (
            ((), "false"),
            (("prs",), "false"),
            (include.GROUPS, "true"),
        ):
            sw = self.row(*groups)["all"]
            self.assertEqual(sw["aria-checked"], on, groups)
            self.assertEqual(sw["data-include"], "tasks,prs,sessions,other", groups)
            self.assertEqual(sw["href"], "/now?include=tasks,prs,sessions,other")
            # Nothing to add or remove: a shift-click on All is a plain click.
            self.assertNotIn("data-toggle", sw)
        # All comes first, ahead of the groups.
        self.assertEqual(list(self.row("prs")), ["all", *include.GROUPS])

    def test_a_shift_click_adds_or_removes_a_group(self):
        sw = self.row("prs")
        self.assertEqual(sw["tasks"]["data-toggle"], "tasks,prs")
        self.assertEqual(sw["tasks"]["data-toggle-href"], "/now?include=tasks,prs")
        self.assertEqual(sw["prs"]["data-toggle"], "")
        sw = self.row("tasks", "prs")
        self.assertEqual(sw["tasks"]["data-toggle"], "prs")
        # On/off still says whether a group is included.
        self.assertEqual(sw["tasks"]["aria-checked"], "true")
        self.assertEqual(sw["sessions"]["aria-checked"], "false")

    def test_other_parameters_ride_along_on_both_targets(self):
        sw = self.row("prs", query={"days": ["14"], "include": ["prs"]})
        self.assertEqual(sw["tasks"]["href"], "/now?days=14&amp;include=tasks")
        self.assertEqual(
            sw["tasks"]["data-toggle-href"], "/now?days=14&amp;include=tasks,prs"
        )

    def test_the_row_says_what_a_click_does(self):
        row = include.switches(inc("prs", view="now"), "/now", {})
        self.assertIn(include.HINT, row)
        self.assertIn("shift-click", include.HINT)

    def test_the_script_picks_the_shift_target_on_shift_click(self):
        # The suite has no JavaScript runtime, so this pins the one branch the
        # script makes; which set each target means is pinned above, in Python.
        js = include.JS
        self.assertIn("ev.shiftKey && a.dataset.toggle !== undefined", js)
        self.assertIn("shift ? a.dataset.toggle : a.dataset.include", js)
        self.assertIn("shift ? a.dataset.toggleHref : a.href", js)
        self.assertIn("encodeURIComponent(value)", js)
        self.assertIn("location.href = url", js)
        # Cmd/ctrl-click is left to the browser, which opens a new tab.
        self.assertIn("if (ev.metaKey || ev.ctrlKey) return;", js)


def header(page):
    m = re.search(r"<header.*?</header>", page, re.S)
    assert m is not None
    return m.group(0)


def switch_row(page):
    m = re.search(r'<div class="include"[^>]*>.*?</div>', page, re.S)
    assert m is not None, "no include switches"
    return m.group(0)


class FleetRouteTests(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: needs_fx.NOW
        self.httpd.app.cfg["ingest"]["machines"] = {"mini": "sha256:" + "0" * 64}
        conn = db.connect(self.db_path)
        needs_fx.seed(conn)
        server_harness.seed(conn)
        overlay.tag(conn, ("github", needs_fx.pr_url(1201)), "side-quest")
        overlay.set_focus(conn, ("jira", "ABC-4"))
        conn.close()

    def get(self, path):
        status, page = self.request("GET", path)
        self.assertEqual(status, 200, (path, page[:200]))
        return page

    def remember(self, view, groups):
        status, _ = self.request(
            "POST",
            "/overlay/include",
            origin=f"http://127.0.0.1:{self.port}",
            body={"token": self.token, "view": view, "include": groups},
        )
        self.assertEqual(status, 200)

    def restart(self):
        """A new server process over the same DB: only the DB carries over."""
        self.fake.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.syncer.stop()
        self.fake = server_harness.FakeSync()
        self.httpd, self.syncer = server.build(
            self.cfg, self.db_path, 0, self.fake, run_at_start=False
        )
        self.httpd.app.now = lambda: needs_fx.NOW
        self.port = self.httpd.server_address[1]
        self.token = self.httpd.app.token
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.syncer.start()

    def test_every_view_honours_include_and_draws_its_switches(self):
        for path in VIEW_PATHS:
            page = self.get(f"{path}?include=prs")
            row = switch_row(page)
            self.assertIn('data-include="prs"', row, path)
            self.assertIn('data-origin="url"', row, path)
            # PRs alone: clicking PRs again goes back to every group.
            self.assertIn(
                'data-group="prs" data-include="tasks,prs,sessions,other"', row, path
            )
            self.assertLess(page.index("</nav>"), page.index('class="include"'), path)
            on = re.findall(r'aria-checked="true" data-group="(\w+)"', row)
            self.assertEqual(on, ["prs"], path)
            # No include: everything, as before filters existed.
            row = switch_row(self.get(path))
            self.assertIn('data-include="tasks,prs,sessions,other"', row, path)

    def test_an_unknown_group_is_400_naming_the_valid_ones(self):
        for path in VIEW_PATHS:
            status, body = self.request("GET", f"{path}?include=prs,bugs")
            self.assertEqual(status, 400, path)
            self.assertIn("unknown include group: bugs", body, path)
            self.assertIn("tasks, prs, sessions, other", body, path)

    def test_header_gauges_are_identical_with_and_without_a_filter(self):
        for path in VIEW_PATHS:
            bare = header(self.get(path))
            self.assertIn("Side quests <b>1/3</b>", bare, path)
            self.assertIn('data-gauge="focus"', bare, path)
            for groups in ("prs", "sessions", "tasks", "other", ""):
                filtered = header(self.get(f"{path}?include={groups}"))
                self.assertEqual(filtered, bare, (path, groups))

    def test_an_ignored_item_stays_hidden_whatever_the_filter(self):
        conn = db.connect(self.db_path)
        overlay.clear_focus(conn, ("jira", "ABC-4"))
        overlay.ignore(conn, ("github", needs_fx.pr_url(1201)))
        overlay.ignore(conn, ("jira", "ABC-1"))
        conn.close()
        for query in ("", "?include=prs", "?include=tasks,prs,sessions,other"):
            self.assertNotIn(
                needs_fx.pr_url(1201), needs_fx.keys(self.get(f"/{query}")), query
            )
        self.assertIn(needs_fx.pr_url(1202), needs_fx.keys(self.get("/?include=prs")))
        for query in ("", "?include=tasks"):
            board = self.get(f"/board{query}").split("</header>", 1)[1]
            lanes = board.split('class="ignored"', 1)[0]
            self.assertNotIn('data-key="ABC-1"', lanes, query)
            self.assertIn('data-key="ABC-4"', lanes, query)
        # The gauges keep #1158's ignore subtraction and ignore the filter.
        bare = header(self.get("/"))
        for groups in ("prs", "", "tasks,prs,sessions,other"):
            self.assertEqual(header(self.get(f"/?include={groups}")), bare, groups)

    def test_the_switches_sit_beside_the_header_text_filter(self):
        page = self.get("/now?include=tasks")
        self.assertIn('<input id="filter"', header(page))
        self.assertIn('class="include"', page)
        # A switched-off lane keeps its note while the text filter hides empties.
        page = self.get("/now?include=prs")
        self.assertIn('class="empty switched-off"', page)
        self.assertIn(".lane.filtered-empty .empty.switched-off", page)

    def test_needs_you_lists_only_the_included_groups(self):
        overlay_clear = db.connect(self.db_path)
        overlay.clear_focus(overlay_clear, ("jira", "ABC-4"))
        overlay_clear.close()
        everything = needs_fx.keys(self.get("/"))
        self.assertIn("s-turn", everything)
        self.assertIn(needs_fx.pr_url(1201), everything)
        prs = needs_fx.keys(self.get("/?include=prs"))
        self.assertEqual(prs, [needs_fx.pr_url(1201), needs_fx.pr_url(1202)])
        self.assertEqual(
            needs_fx.keys(self.get("/needs?include=tasks")), [needs_fx.issue_url(950)]
        )
        # Sessions: the main list and the batched permission prompt.
        self.assertEqual(
            needs_fx.keys(self.get("/?include=sessions")), ["s-turn", "s-pr", "s-perm"]
        )
        page = self.get("/?include=other")
        self.assertEqual(needs_fx.keys(page), [])
        self.assertIn("Nothing included by the filter needs you.", page)

    def test_a_remembered_choice_survives_a_restart_and_a_url_overrides_it(self):
        self.remember("needs", "sessions")
        self.restart()
        row = switch_row(self.get("/"))
        self.assertIn('data-include="sessions"', row)
        self.assertIn('data-origin="remembered"', row)
        # An explicit URL wins, and reading it records nothing.
        row = switch_row(self.get("/?include=prs"))
        self.assertIn('data-origin="url"', row)
        row = switch_row(self.get("/needs"))
        self.assertIn('data-include="sessions"', row)
        # Per view: Now has its own (none), so it shows everything.
        self.assertIn('data-origin="default"', switch_row(self.get("/now")))
        # Every switch off is remembered as such.
        self.remember("now", "")
        self.assertIn('data-include=""', switch_row(self.get("/now")))

    def test_a_click_is_remembered_as_what_it_asked_for(self):
        # What the page's script posts on each click: the switch's data-include
        # for a plain click, its data-toggle for a shift-click.
        prs = switch_attrs(switch_row(self.get("/now")))["prs"]
        self.assertEqual(prs["href"], "/now?include=prs")
        self.remember("now", prs["data-include"])
        row = switch_row(self.get("/now"))
        self.assertIn('data-include="prs" data-origin="remembered"', row)
        tasks = switch_attrs(row)["tasks"]
        self.remember("now", tasks["data-toggle"])
        row = switch_row(self.get("/now"))
        self.assertIn('data-include="tasks,prs" data-origin="remembered"', row)
        self.remember("now", switch_attrs(row)["all"]["data-include"])
        sw = switch_attrs(switch_row(self.get("/now")))
        self.assertEqual(sw["all"]["aria-checked"], "true")

    def test_a_config_default_applies_until_a_choice_is_remembered(self):
        self.httpd.app.cfg["views"] = {"now": {"include": ["prs"]}}
        page = self.get("/now")
        self.assertIn('data-origin="config"', switch_row(page))
        self.assertIn('data-filtered="tasks"', page)
        self.remember("now", "tasks")
        page = self.get("/now")
        self.assertIn('data-origin="remembered"', switch_row(page))
        self.assertNotIn('data-filtered="', page)

    def test_the_board_and_the_jira_lanes_follow_their_group(self):
        board = self.get("/board?include=prs")
        self.assertIn('data-filtered="tasks"', board)
        self.assertNotIn("browse/ABC-1", board.split("</header>", 1)[1])
        self.assertIn('data-key="ABC-1"', self.get("/board?include=tasks"))
        waiting = self.get("/waiting-on?include=tasks")
        self.assertIn('data-section="prs" data-filtered="prs"', waiting)
        self.assertIn('<section class="lane" data-section="issues">', waiting)
        waiting = self.get("/waiting-on-me?include=prs")
        self.assertIn('data-section="issues" data-filtered="tasks"', waiting)
        self.assertIn('<section class="lane" data-section="reviews">', waiting)
        epics = self.get("/epics?include=sessions")
        self.assertIn('data-section="epics" data-filtered="tasks"', epics)

    def test_health_leaves_out_the_figures_of_a_group_switched_off(self):
        page = self.get("/health?days=14&include=prs")
        figures = re.findall(r'data-figure="(\w+)"', page)
        self.assertEqual(figures, ["open_prs", "side_quests", "review_queue"])
        # The window links carry the URL's filter; the switches keep the window.
        self.assertIn('href="/health?days=7&amp;include=prs"', page)
        row = switch_row(page)
        self.assertIn(' href="/health?days=14&amp;include=tasks"', row)
        self.assertIn('data-toggle-href="/health?days=14&amp;include=tasks,prs"', row)
        self.assertIn('data-toggle-href="/health?days=14&amp;include="', row)
        everything = re.findall(r'data-figure="(\w+)"', self.get("/health"))
        self.assertEqual(len(everything), 13)


class FleetViewTests(unittest.TestCase):
    """Since you were away and fleet Stale, rendered directly."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.conn = db.connect(Path(self._td.name) / "d.db")
        self.addCleanup(self.conn.close)
        fleet_fx.seed(self.conn)
        self.cfg = fleet_fx.fleet_cfg()

    def away(self, *groups):
        return fleet_views.render_away(self.conn, self.cfg, fleet_fx.NOW, inc(*groups))

    def stale(self, *groups):
        return fleet_views.render_stale(self.conn, self.cfg, fleet_fx.NOW, inc(*groups))

    def test_away_keeps_only_the_included_groups(self):
        issue = f"https://github.com/{fleet_fx.REPO}/issues/950"
        every = fleet_fx.keys(
            fleet_views.render_away(self.conn, self.cfg, fleet_fx.NOW)
        )
        for key in (fleet_fx.pr_url(1140), "s-done", issue, fleet_fx.DESIGN_URL):
            self.assertIn(key, every)
        self.assertNotIn(fleet_fx.pr_url(1135), every)  # closed PRs are not listed
        prs = fleet_fx.keys(self.away("prs"))
        self.assertEqual(prs, [fleet_fx.pr_url(1140)])
        self.assertNotIn("s-done", prs)
        self.assertNotIn(issue, prs)
        self.assertEqual(
            sorted(fleet_fx.keys(self.away("sessions"))),
            sorted(["s-asked", "s-askperm", "s-done", "s-wait"]),
        )
        self.assertEqual(fleet_fx.keys(self.away("tasks")), [issue])
        self.assertEqual(fleet_fx.keys(self.away("other")), [fleet_fx.DESIGN_URL])

    def test_the_focused_design_card_shows_only_with_other(self):
        overlay.set_focus(self.conn, ("github", fleet_fx.DESIGN_URL), fleet_fx.NOW)
        for groups, shown in ((("prs",), False), (("other",), True), ((), False)):
            focus = fleet_fx.section(self.away(*groups), "focus")
            self.assertEqual(fleet_fx.DESIGN_URL in fleet_fx.keys(focus), shown, groups)
        # The section stays, with its repo's included items.
        focus = fleet_fx.section(self.away("prs"), "focus")
        self.assertIn(fleet_fx.pr_url(1140), fleet_fx.keys(focus))
        unfiltered = fleet_views.render_away(self.conn, self.cfg, fleet_fx.NOW)
        self.assertIn(
            fleet_fx.DESIGN_URL, fleet_fx.keys(fleet_fx.section(unfiltered, "focus"))
        )

    def test_stale_says_switched_off_when_its_groups_are_off(self):
        page = self.stale()
        self.assertEqual(page.count('data-filtered="all"'), 2)
        self.assertIn("Tasks, PRs, Sessions and Other are switched off.", page)
        self.assertNotIn("Nothing has evidence to close.", page)
        self.assertNotIn("Nothing old is waiting on a check.", page)
        self.assertNotIn("switched off", self.stale("prs"))

    def test_stale_keeps_only_the_included_groups(self):
        def heads(page):
            return set(
                re.findall(r'<div class="card">(worktree|handoff|session) ', page)
            )

        every = fleet_views.render_stale(self.conn, self.cfg, fleet_fx.NOW)
        self.assertEqual(heads(every), {"worktree", "handoff", "session"})
        self.assertIn(fleet_fx.pr_url(900), every)
        other = self.stale("other")
        self.assertEqual(heads(other), {"worktree", "handoff"})
        self.assertNotIn(fleet_fx.pr_url(900), other)
        prs = self.stale("prs")
        self.assertEqual(heads(prs), set())
        self.assertIn(fleet_fx.pr_url(900), prs)
        self.assertEqual(heads(self.stale("sessions")), {"session"})


class JiraHealthNavTest(server_harness.ServerCase):
    def test_a_jira_instance_health_tab_hides_the_fleet_only_tabs(self):
        status, page = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertIn("aria-current=page>Health", page)
        for tab in fleet_views.ONLY:
            self.assertNotIn(f'href="{tab}"', page, tab)


class FleetHealthNavTest(server_harness.ServerCase):
    fleet = True

    def test_a_fleet_instance_health_tab_keeps_them(self):
        page = self.request("GET", "/health")[1]
        for tab in fleet_views.ONLY:
            self.assertIn(f'href="{tab}"', page, tab)


if __name__ == "__main__":
    unittest.main()
