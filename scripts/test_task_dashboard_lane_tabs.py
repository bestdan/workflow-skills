#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/lane_tabs.py, the lane tabs.

tabify() is tested on hand-written bodies, then every multi-lane page is
fetched from the in-process server over fixture DBs built through the real
writers (the fleet fixtures of the fleet-tabs, needs and fleet-now suites, the
Jira fixture of the server suite). No gh, acli, claude or network runs: the
server runs with a fake sync function. The page script is pinned by its
source, as the include switches' is. Needs Python 3.11: run via
scripts/test-task-dashboard-lane-tabs.sh.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import db  # type: ignore[import-not-found]  # noqa: E402
import lane_tabs  # type: ignore[import-not-found]  # noqa: E402
import server  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_fleet_now as now_fx  # noqa: E402
import test_task_dashboard_fleet_tabs as tabs_fx  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

NOW = tabs_fx.NOW

SWITCHES = '<nav>x</nav><div class="include" data-view="v"><a>All</a></div>'


def lane(name, title, n, *, off=False, tab=None):
    attrs = (' data-filtered="prs"' if off else "") + lane_tabs.tab_attr(tab)
    cards = "".join('<div class="card">c</div>' for _ in range(n))
    return (
        f'<section class="lane" data-section="{name}"{attrs}>'
        f"<h2>{title} ({n})</h2>{cards}</section>"
    )


def tabs(page):
    """[(lane, label, selected)] from the page's tab bar, in order."""
    bar = re.search(r'<div class="lane-tabs"[^>]*>(.*?)</div>', page, re.S)
    if bar is None:
        return []
    out = []
    for m in re.finditer(
        r'<a role="tab" aria-selected="(true|false)" data-lane="([^"]+)"[^>]*>'
        r'<span class="name">([^<]*)</span> <span class="count[^"]*">([^<]*)</span>',
        bar.group(1),
    ):
        out.append((m[2], f"{m[3]} {m[4]}", m[1] == "true"))
    return out


def visible(page):
    """The lanes not hidden."""
    return [
        m[1]
        for m in re.finditer(
            r'<section class="lane[^"]*" data-section="([^"]+)"([^>]*)>', page
        )
        if "hidden" not in m[2].split()
    ]


def card_count(page, name):
    m = re.search(
        rf'<section class="lane[^"]*" data-section="{name}"[^>]*>(.*?)</section>',
        page,
        re.S,
    )
    assert m, f"no lane {name}"
    return len(re.findall(r'<(?:div|details) class="card', m.group(1)))


class TabifyTests(unittest.TestCase):
    def body(self):
        return SWITCHES + lane("a", "Alpha", 0) + lane("b", "Beta &amp; co", 2)

    def test_one_tab_per_lane_with_its_count(self):
        page = lane_tabs.tabify(self.body(), "v", "/v", {})
        self.assertEqual(
            tabs(page), [("a", "Alpha (0)", False), ("b", "Beta &amp; co (2)", True)]
        )
        self.assertIn('data-count="2"', page)

    def test_a_short_label_names_the_tab_and_the_title_is_its_tooltip(self):
        body = (
            SWITCHES
            + lane("a", "Alpha: a long title", 1, tab="Alpha")
            + lane("b", "Beta", 2)
        )
        page = lane_tabs.tabify(body, "v", "/v", {})
        self.assertEqual(
            [label for _, label, _ in tabs(page)], ["Alpha (1)", "Beta (2)"]
        )
        self.assertIn('data-lane="a" data-count="1" title="Alpha: a long title"', page)
        self.assertIn('data-lane="b" data-count="2" title="Beta"', page)
        # The lane keeps its full heading.
        self.assertIn("<h2>Alpha: a long title (1)</h2>", page)

    def test_the_bar_goes_right_under_the_include_switches(self):
        page = lane_tabs.tabify(self.body(), "v", "/v", {})
        self.assertTrue(page.startswith(SWITCHES + '<div class="lane-tabs"'))

    def test_the_first_lane_with_rows_shows_and_the_rest_are_hidden(self):
        page = lane_tabs.tabify(self.body(), "v", "/v", {})
        self.assertEqual(visible(page), ["b"])
        self.assertIn('<section class="lane" data-section="a" hidden>', page)
        self.assertIn('data-origin="default"', page)

    def test_the_url_names_the_lane(self):
        page = lane_tabs.tabify(self.body(), "v", "/v", {"lane": ["a"]})
        self.assertEqual(visible(page), ["a"])
        self.assertIn('data-origin="url"', page)
        # A lane the page lacks falls back to the default.
        page = lane_tabs.tabify(self.body(), "v", "/v", {"lane": ["zzz"]})
        self.assertEqual(visible(page), ["b"])
        self.assertIn('data-origin="default"', page)

    def test_each_tab_links_to_its_lane_keeping_the_query(self):
        query = {"include": ["tasks,prs"], "lane": ["a"]}
        page = lane_tabs.tabify(self.body(), "v", "/v", query)
        self.assertIn('href="/v?include=tasks,prs&amp;lane=b"', page)
        self.assertNotIn("lane=a&amp;lane", page)

    def test_a_switched_off_lane_keeps_its_tab_marked_off(self):
        body = SWITCHES + lane("a", "Alpha", 0, off=True) + lane("b", "Beta", 0)
        page = lane_tabs.tabify(body, "v", "/v", {})
        self.assertEqual(
            tabs(page), [("a", "Alpha (off)", False), ("b", "Beta (0)", True)]
        )
        self.assertIn('<span class="count off">(off)</span>', page)
        self.assertIn('data-lane="a" data-count="0" data-off=""', page)

    def test_all_empty_shows_the_first_lane(self):
        body = SWITCHES + lane("a", "Alpha", 0) + lane("b", "Beta", 0)
        self.assertEqual(visible(lane_tabs.tabify(body, "v", "/v", {})), ["a"])

    def test_one_lane_is_left_alone(self):
        body = SWITCHES + lane("a", "Alpha", 3)
        self.assertEqual(lane_tabs.tabify(body, "v", "/v", {}), body)

    def test_no_switches_puts_the_bar_before_the_first_lane(self):
        body = "<h2>Board</h2>" + lane("a", "Alpha", 1) + lane("b", "Beta", 1)
        page = lane_tabs.tabify(body, "v", "/v", {})
        self.assertTrue(page.startswith('<h2>Board</h2><div class="lane-tabs"'))

    def test_the_script_is_pinned(self):
        js = lane_tabs.JS
        # Tab click: in place, URL updated, remembered per view.
        self.assertIn("history.replaceState(null, '', tab.getAttribute('href'))", js)
        self.assertIn("localStorage.setItem(key, tab.dataset.lane)", js)
        self.assertIn("var key = 'lane:' + bar.dataset.view;", js)
        # The include switches follow the lane shown, so a switch keeps it.
        self.assertIn("document.querySelectorAll('.include a[data-include]')", js)
        self.assertIn("u.searchParams.set('lane', tab.dataset.lane);", js)
        # A remembered lane applies only when the URL named none.
        self.assertIn("if (bar.dataset.origin !== 'url')", js)
        # ...and only while the include filter has not switched it off.
        self.assertIn("if (tab && tab.dataset.off === undefined) show(tab, {});", js)
        # The text filter's per-lane count drives the tabs.
        self.assertIn("+l.dataset.shown", js)
        self.assertIn("t.classList.toggle('dim', !!q && n === 0)", js)
        self.assertIn("if (q && cur && hits(cur) === 0)", js)
        # An off tab keeps its "(off)".
        self.assertIn("if (t.dataset.off !== undefined) return;", js)
        # The filter hands its recount over.
        self.assertIn("lane.dataset.shown = shown;", server.JS)
        self.assertIn("if (window.laneTabs) window.laneTabs.refresh(q);", server.JS)
        self.assertLess(
            server.render_page("t", "", "", "x", False).index(
                "window.laneTabs.refresh"
            ),
            server.render_page("t", "", "", "x", False).index("var bar = document"),
        )


class FleetPageTests(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: NOW
        self.httpd.app.cfg["jira"]["enabled"] = False
        conn = db.connect(self.db_path)
        now_fx.seed(conn, rows=tabs_fx.AWAITING + now_fx.ROWS[8:], prs=tabs_fx.PRS)
        conn.close()

    def get(self, path):
        status, page = self.request("GET", path)
        self.assertEqual(status, 200, path)
        return page

    def assert_counts_match_lanes(self, page):
        for name, label, _ in tabs(page):
            if label.endswith("(off)"):
                continue
            counted = re.search(r"\((\d+)\)$", label)
            assert counted, label
            n = int(counted[1])
            self.assertEqual(n, card_count(page, name), name)

    def test_pull_requests(self):
        page = self.get("/prs")
        self.assertEqual(
            [label for _, label, _ in tabs(page)],
            [
                "Ready (1)",
                "Waiting (5)",
                "From others (3)",
                "Review requested (1)",
                "CI failing (1)",
                "Drafts (1)",
            ],
        )
        self.assert_counts_match_lanes(page)
        self.assertIn('title="Waiting on you"', page)
        self.assertIn("<h2>Waiting on you (5)</h2>", page)
        self.assertEqual(len(visible(page)), 1)
        self.assertEqual(visible(page), ["ready"])
        page = self.get("/prs?lane=drafts")
        self.assertEqual(visible(page), ["drafts"])
        self.assertEqual([n for n, _, sel in tabs(page) if sel], ["drafts"])

    def test_issues(self):
        page = self.get("/issues")
        self.assertEqual(
            [label.rsplit(" (", 1)[0] for _, label, _ in tabs(page)],
            ["Needs you", "Needs review", "Started", "Ready", "Untriaged"],
        )
        self.assert_counts_match_lanes(page)
        self.assertEqual(len(visible(page)), 1)

    def test_stale(self):
        page = self.get("/stale")
        names = [label.rsplit(" (", 1)[0] for _, label, _ in tabs(page)]
        self.assertEqual(names, ["Close", "Check"])
        self.assertIn('title="Check: no evidence, so not a reason to close"', page)
        self.assert_counts_match_lanes(page)

    def test_stale_with_jira_on_gives_the_jira_lane_a_titled_tab(self):
        self.httpd.app.cfg["jira"]["enabled"] = True
        conn = self.conn()
        server_harness.seed(conn)  # mirrored Jira issues, so the Jira lane draws
        conn.close()
        page = self.get("/stale")
        got = tabs(page)
        self.assertEqual([n for n, _, _ in got], ["stale", "close", "check"])
        self.assertRegex(got[0][1], r"^Jira \(\d+\)$")
        self.assert_counts_match_lanes(page)

    def test_needs_you(self):
        page = self.get("/needs")
        names = [n for n, _, _ in tabs(page)]
        self.assertEqual(names, visible(page) + names[1:])  # one shows: the first
        self.assertEqual((names[0], names[-1]), ("needs", "finished"))
        self.assertEqual(tabs(page)[-1][1].rsplit(" (", 1)[0], "Finished")
        self.assertIn('title="Finished since you looked"', page)
        self.assert_counts_match_lanes(page)
        self.assertNotIn('<details class="group"', page)
        # Mark as read stays in the Finished tab; Mark all as read by the heading.
        finished = page.split('data-section="finished"', 1)[1]
        self.assertIn(">Mark as read</button>", finished)
        needs_lane = page.split('data-section="needs"', 1)[1].split("</section>")[0]
        self.assertIn(">Mark all as read</button>", needs_lane)
        page = self.get("/needs?lane=finished")
        self.assertEqual(visible(page), ["finished"])

    def test_a_lane_switched_off_says_off(self):
        page = self.get("/prs?include=tasks")
        self.assertTrue(all(label.endswith("(off)") for _, label, _ in tabs(page)))
        self.assertEqual(len(tabs(page)), 6)
        self.assertEqual(tabs(page)[1][1], "Waiting (off)")  # off lanes keep the label
        # A lane named by the URL opens even when it is off.
        page = self.get("/prs?include=tasks&lane=drafts")
        self.assertEqual(visible(page), ["drafts"])
        self.assertIn('data-origin="url"', page)

    def test_now_is_one_lane_with_jira_or_without_it(self):
        self.assertEqual(tabs(self.get("/")), [])
        self.httpd.app.cfg["jira"]["enabled"] = True
        self.assertEqual(tabs(self.get("/")), [])

    def test_the_filter_and_the_tabs_share_the_page(self):
        page = self.get("/prs")
        self.assertEqual(page.count('id="filter"'), 1)
        self.assertLess(page.index('class="include"'), page.index('class="lane-tabs"'))


class JiraPageTests(server_harness.ServerCase):
    def setUp(self):
        super().setUp()
        conn = self.conn()
        server_harness.seed(conn)
        conn.close()

    def test_the_board(self):
        page = self.request("GET", "/board")[1]
        got = tabs(page)
        self.assertEqual(len(got), 7)
        self.assertEqual(got[0][1], "New (1)")
        self.assertEqual(len(visible(page)), 1)
        page = self.request("GET", "/board?lane=blocked")[1]
        self.assertEqual(visible(page), ["blocked"])

    def test_the_waiting_pages(self):
        for path in ("/waiting-on-me", "/waiting-on"):
            page = self.request("GET", path)[1]
            self.assertEqual(len(tabs(page)), 2, path)
            self.assertEqual(len(visible(page)), 1, path)
            self.assertEqual(tabs(page)[0][1].split(" (")[0], "Issues", path)

    def test_a_one_lane_page_has_no_tabs(self):
        page = self.request("GET", "/now")[1]
        self.assertEqual(tabs(page), [])
        self.assertNotRegex(page, r'<section class="lane"[^>]* hidden>')


if __name__ == "__main__":
    unittest.main()
