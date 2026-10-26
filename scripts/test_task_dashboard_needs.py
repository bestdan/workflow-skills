#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/needs.py, the Needs you page.

A fixture DB is built through the real writers (snapshot_adapter.ingest then
sessions.write for the hook rows, claude_remote.write for a cloud row,
github_fleet.write for PRs and issues), then the page is rendered from it,
directly and through the in-process server. No gh, git, claude or network is
touched: the server runs with a fake sync function. Needs Python 3.11: run via
scripts/test-task-dashboard-needs.sh.
"""

import copy
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import claude_remote  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import gauges  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import lane_tabs  # type: ignore[import-not-found]  # noqa: E402
import include  # type: ignore[import-not-found]  # noqa: E402
import needs  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import sessions  # type: ignore[import-not-found]  # noqa: E402
import snapshot_adapter  # type: ignore[import-not-found]  # noqa: E402
import views  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
REPO = "bestdan/workflow-skills"
WT = "/h/src/worktrees/workflow-skills"


def pr_url(n):
    return f"https://github.com/{REPO}/pull/{n}"


def issue_url(n):
    return f"https://github.com/{REPO}/issues/{n}"


def pr(n, created, *, in_queue=True, brief=None, head=None, ci="success"):
    return {
        "kind": "pr",
        "key": pr_url(n),
        "url": pr_url(n),
        "repo": REPO,
        "number": n,
        "title": f"feat: thing {n} [#{n - 100}]",
        "head_ref": head or f"bestdan/task-{n - 100}",
        "review_decision": None,
        "role": "author",
        "brief": brief,
        "ci_state": ci,
        "in_queue": in_queue,
        "queue_misses": [] if in_queue else ["ci"],
        "created": created,
    }


GITHUB_ROWS = [
    # The longest wait, and no decision brief: no brief block.
    pr(1201, "2026-10-21T12:00:00Z"),
    pr(
        1202,
        "2026-10-23T12:00:00Z",
        brief="Adds X.\nDecide: ship behind the flag?\nA no drops the flag work.",
    ),
    pr(1203, "2026-10-01T00:00:00Z", in_queue=False),  # awaiting, not queued
    {
        "kind": "issue",
        "key": issue_url(950),
        "url": issue_url(950),
        "repo": REPO,
        "number": 950,
        "title": "needs a human",
        "labels": ["auto:human-review-needed"],
        "created": "2026-10-22T12:00:00Z",
    },
]


_V1 = object()


def ev(sid, ts, event, cwd="/h/src/dotfiles", notification=None, asked=_V1):
    """A hook line: format 1 unless `asked` is given, which makes it format 2."""
    line = {"v": 1, "ts": ts, "event": event, "session_id": sid, "cwd": cwd}
    if notification:
        line["notification"] = notification
    if asked is not _V1:
        line.update(v=2, asked=asked)
    return line


SNAPSHOT = {
    "schema": 1,
    "host": "mini",
    "taken_at": "2026-10-24T11:59:00.000Z",
    "worktrees": [],
    "handoffs": [],
    "tmux": [],
    "sessions": [
        # Asked a question 6h ago: a real decision, in the main list. The
        # idle_prompt the harness raises a minute later does not clear it.
        ev("s-turn", "2026-10-24T05:00:00.000Z", "UserPromptSubmit"),
        ev("s-turn", "2026-10-24T06:00:00.000Z", "Stop", asked=True),
        ev("s-turn", "2026-10-24T06:01:00.000Z", "Notification", None, "idle_prompt"),
        # Finished without a question, or the hook could not tell, or a
        # format-1 hook wrote the Stop: none of them waits on me.
        ev("s-done", "2026-10-24T05:00:00.000Z", "UserPromptSubmit"),
        ev("s-done", "2026-10-24T06:00:00.000Z", "Stop", asked=False),
        ev("s-null", "2026-10-24T05:00:00.000Z", "UserPromptSubmit"),
        ev("s-null", "2026-10-24T06:00:00.000Z", "Stop", asked=None),
        ev("s-v1", "2026-10-24T05:00:00.000Z", "UserPromptSubmit"),
        ev("s-v1", "2026-10-24T06:00:00.000Z", "Stop"),
        # Asked, then answered: working again.
        ev("s-answered", "2026-10-24T05:00:00.000Z", "UserPromptSubmit"),
        ev("s-answered", "2026-10-24T06:00:00.000Z", "Stop", asked=True),
        ev("s-answered", "2026-10-24T07:00:00.000Z", "UserPromptSubmit"),
        # Asked, answered, then finished: the later Stop decides.
        ev("s-later", "2026-10-24T05:00:00.000Z", "UserPromptSubmit"),
        ev("s-later", "2026-10-24T06:00:00.000Z", "Stop", asked=True),
        ev("s-later", "2026-10-24T07:00:00.000Z", "UserPromptSubmit"),
        ev("s-later", "2026-10-24T08:00:00.000Z", "Stop", asked=False),
        # A permission prompt 1h ago: a plain approval.
        ev("s-perm", "2026-10-24T10:00:00.000Z", "UserPromptSubmit"),
        ev(
            "s-perm",
            "2026-10-24T11:00:00.000Z",
            "Notification",
            notification="permission_prompt",
        ),
        # Working: not waiting on me.
        ev("s-work", "2026-10-24T11:30:00.000Z", "UserPromptSubmit"),
        # Waiting, but quiet for 10 days: Stale's, not this page's.
        ev("s-old", "2026-10-14T08:00:00.000Z", "UserPromptSubmit"),
        ev("s-old", "2026-10-14T09:00:00.000Z", "Stop"),
        # Never prompted: an empty spawn, Stale's too.
        ev("s-empty", "2026-10-24T09:00:00.000Z", "Notification", None, "idle_prompt"),
        # On PR 1201's head branch, turn ended 2h ago.
        ev(
            "s-pr",
            "2026-10-24T09:00:00.000Z",
            "UserPromptSubmit",
            f"{WT}/task-1101",
        ),
        ev("s-pr", "2026-10-24T10:00:00.000Z", "Stop", f"{WT}/task-1101", asked=True),
    ],
    "history": [],
}


def with_branch(snapshot):
    """The hook reads the branch from .git/HEAD; the fixture states it."""
    for line in snapshot["sessions"]:
        if line["session_id"] == "s-pr":
            line["branch"] = "bestdan/task-1101"
    return snapshot


REMOTE = {
    "id": "session_01ABC",
    "title": "cloud thing",
    "status": "requires_action",
    "environment_kind": "anthropic_cloud",
    "repository": {"owner": "bestdan", "name": "dotfiles"},
    "branch": "main",
    "updated_at": "2026-10-24T11:30:00Z",
}


def seed(conn, *, fleet_ran=True):
    snapshot_adapter.ingest(conn, with_branch(SNAPSHOT), NOW)
    sessions.write(conn, sessions.derive(conn, NOW), NOW)
    claude_remote.write(conn, [claude_remote.to_row(REMOTE)], NOW)
    github_fleet.write(conn, GITHUB_ROWS, NOW)
    if fleet_ran:
        server_harness.add_run(conn, "ok", NOW, kind=github_fleet.RUN_KIND)


def fleet_cfg(remote=False):
    cfg = config.deep_merge(config.DEFAULTS, server_harness.USER_CONFIG)
    cfg["github"].update(enabled=True, fleet=True)
    cfg["ingest"]["machines"] = {"mini": "sha256:" + "0" * 64}
    cfg["claude_remote"]["enabled"] = remote
    return cfg


def section(page, name):
    m = re.search(
        rf'<section class="lane[^"]*" data-section="{name}"[^>]*>(.*?)</section>',
        page,
        re.S,
    )
    assert m, f"no section {name}"
    return m.group(1)


def keys(text):
    """The keys of the needs listed; the Finished group's rows are not needs."""
    return re.findall(
        r'class="card need(?! done)[^"]*" data-source="[^"]*" data-key="([^"]*)"',
        text,
    )


def listed(page):
    """The page without its Finished group."""
    return re.sub(r'<section class="lane finished".*?</section>', "", page, flags=re.S)


def finished_keys(page):
    if 'data-section="finished"' not in page:
        return []
    return re.findall(
        r'class="card need done" data-source="[^"]*" data-key="([^"]*)"',
        section(page, "finished"),
    )


class NeedsCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.conn = db.connect(Path(self._td.name) / "d.db")
        self.addCleanup(self.conn.close)

    def page(self, cfg=None):
        return needs.render(self.conn, cfg or fleet_cfg(), NOW)


class AskedTests(NeedsCase):
    def test_only_a_turn_that_asked_is_listed_and_labelled(self):
        seed(self.conn)
        page = self.page()
        main = section(page, "needs")
        self.assertIn("s-turn", keys(main))
        for sid in ("s-done", "s-null", "s-v1", "s-answered", "s-later"):
            self.assertNotIn(f'data-key="{sid}"', listed(page), sid)
        card = re.search(r'data-key="s-turn".*?</summary>', main, re.S)
        assert card is not None
        self.assertIn("asked (heuristic)", card.group(0))
        # A question is a decision, never batched with the plain approvals.
        self.assertNotIn("s-turn", keys(section(page, "approvals")))

    def test_the_include_filter_and_ignore_reach_an_asked_session(self):
        seed(self.conn)
        no_sessions = include.Include("needs", frozenset({"tasks", "prs"}), "url")
        page = needs.render(self.conn, fleet_cfg(), NOW, no_sessions)
        self.assertNotIn('data-key="s-turn"', page)
        everything = include.Include("needs", include.ALL, "url")
        page = needs.render(self.conn, fleet_cfg(), NOW, everything)
        self.assertIn("s-turn", keys(section(page, "needs")))
        overlay.ignore(self.conn, ("claude-hook", "s-turn"), NOW)
        page = needs.render(self.conn, fleet_cfg(), NOW, everything)
        self.assertNotIn('data-key="s-turn"', page)

    def test_a_permission_prompt_after_an_asked_stop_is_an_approval(self):
        # A Stop hook can continue the turn, so a permission prompt can follow
        # an asked Stop with no prompt between: the prompt is what blocks it.
        snap = copy.deepcopy(SNAPSHOT)
        lines: list = snap["sessions"]  # type: ignore[assignment]
        lines += [
            ev("s-ask-perm", "2026-10-24T05:00:00.000Z", "UserPromptSubmit"),
            ev("s-ask-perm", "2026-10-24T06:00:00.000Z", "Stop", asked=True),
            ev(
                "s-ask-perm",
                "2026-10-24T06:05:00.000Z",
                "Notification",
                notification="permission_prompt",
            ),
        ]
        snapshot_adapter.ingest(self.conn, with_branch(snap), NOW)
        sessions.write(self.conn, sessions.derive(self.conn, NOW), NOW)
        approvals = section(self.page(), "approvals")
        card = re.search(r'data-key="s-ask-perm".*?</summary>', approvals, re.S)
        assert card is not None
        self.assertIn("permission prompt", card.group(0))


class OrderTests(NeedsCase):
    def test_the_longest_wait_renders_first_and_expanded(self):
        seed(self.conn)
        main = section(self.page(), "needs")
        self.assertEqual(
            keys(main), [pr_url(1201), issue_url(950), pr_url(1202), "s-turn", "s-pr"]
        )
        first = re.search(r'<div class="card need first"[^>]*>', main)
        assert first is not None
        self.assertIn(f'data-key="{pr_url(1201)}"', first.group(0))
        self.assertEqual(main.count("need first"), 1)
        # Everything after the first is collapsed to one line.
        self.assertEqual(main.count("<details "), 4)
        self.assertIn("waiting 3d", main)

    def test_a_pr_without_a_brief_shows_its_header_once_and_no_brief(self):
        seed(self.conn)
        main = section(self.page(), "needs")
        first = main[: main.index("<details ")]
        self.assertNotIn('class="brief"', first)
        self.assertEqual(first.count("feat: thing 1201"), 1)
        self.assertEqual(first.count("waiting 3d"), 1)
        self.assertEqual(first.count("CI success"), 1)
        # The act line and the controls stay.
        self.assertIn(f'→ <a href="{pr_url(1201)}">open the PR</a>', first)
        self.assertIn("data-ov=", first)

    def test_a_card_puts_its_title_first_and_its_ref_after(self):
        seed(self.conn)
        main = section(self.page(), "needs")
        self.assertIn(
            f'<a class="title" href="{pr_url(1201)}">feat: thing 1201 [#1101]</a>'
            f' <span class="ref">({REPO}#1201)</span>',
            main,
        )
        self.assertIn(
            '<summary><span class="title">needs a human</span>'
            f' <span class="ref">({REPO}#950)</span>',
            main,
        )
        self.assertNotIn(f"{REPO}#1201 feat", main)

    def test_every_card_carries_a_title_the_filter_searches(self):
        seed(self.conn)
        page = self.page()
        for lane in ("needs", "approvals"):
            cards = re.split(r'<(?:div|details) class="card need', section(page, lane))
            self.assertGreater(len(cards), 1, lane)
            for card in cards[1:]:
                m = re.search(r'class="title"[^>]*>([^<]+)<', card)
                assert m is not None, card[:200]
                self.assertTrue(m.group(1).strip(), card[:200])

    def test_a_brief_is_shown_when_there_is_one(self):
        seed(self.conn)
        conn = self.conn
        overlay.pin(conn, ("github", pr_url(1202)), NOW)
        main = section(self.page(), "needs")
        first = main[: main.index("<details ")]
        self.assertIn(f'data-key="{pr_url(1202)}"', first)
        self.assertIn("Decide: ship behind the flag?", first)
        self.assertNotIn("data-fallback", first)

    def test_a_pin_goes_first_whatever_its_wait(self):
        seed(self.conn)
        overlay.pin(self.conn, ("claude-hook", "s-turn"), NOW)
        self.assertEqual(keys(section(self.page(), "needs"))[0], "s-turn")

    def test_left_out(self):
        seed(self.conn)
        page = self.page()
        for absent in ("s-work", "s-old", "s-empty", pr_url(1203), "session_01ABC"):
            self.assertNotIn(f'data-key="{absent}"', listed(page))

    def test_a_snoozed_item_is_hidden_and_counted(self):
        seed(self.conn)
        overlay.snooze(self.conn, ("github", pr_url(1201)), "2999-01-01")
        page = self.page()
        self.assertNotIn(pr_url(1201), keys(page))
        self.assertIn('data-snoozed="1"', page)

    def test_an_ignored_item_leaves_and_can_be_undone(self):
        seed(self.conn)
        item = ("claude-hook", "s-turn")
        overlay.ignore(self.conn, item, NOW)
        self.assertNotIn("s-turn", keys(self.page()))
        footer = views.render_ignored(self.conn)
        self.assertIn('data-source="claude-hook" data-key="s-turn"', footer)
        self.assertIn('data-ov="unignore"', footer)
        overlay.unignore(self.conn, item)
        self.assertIn("s-turn", keys(self.page()))
        self.assertEqual(views.render_ignored(self.conn), "")

    def test_an_ignored_pr_leaves_the_page_and_the_queue_gauge(self):
        seed(self.conn)
        before = gauges.review_queue(self.conn, True)
        assert before is not None
        overlay.ignore(self.conn, ("github", pr_url(1201)), NOW)
        self.assertNotIn(pr_url(1201), keys(self.page()))
        after = gauges.review_queue(self.conn, True)
        assert after is not None
        self.assertEqual(after["queue"], before["queue"] - 1)


class ApprovalTests(NeedsCase):
    def test_a_permission_prompt_lands_in_approvals(self):
        seed(self.conn)
        page = self.page()
        self.assertEqual(keys(section(page, "approvals")), ["s-perm"])
        self.assertNotIn("s-perm", keys(section(page, "needs")))
        # A lane of its own, so a tab of its own, not a collapsed group.
        self.assertIn("<h2>Approvals (1)</h2>", section(page, "approvals"))
        self.assertNotIn('<details class="group"', page)

    def test_approvals_is_the_default_tab_when_nothing_else_waits(self):
        snapshot_adapter.ingest(self.conn, with_branch(SNAPSHOT), NOW)
        rows = [r for r in sessions.derive(self.conn, NOW) if r["key"] == "s-perm"]
        sessions.write(self.conn, rows, NOW)
        page = self.page()
        needs_lane = section(page, "needs")
        self.assertNotIn("Nothing needs you.", needs_lane)
        self.assertIn("only the plain approvals in Approvals", needs_lane)
        # Needs you is empty, so the first lane with rows opens: Approvals.
        tabbed = lane_tabs.tabify(page, "needs", "/needs", {})
        self.assertIn('aria-selected="true" data-lane="approvals"', tabbed)
        self.assertIn('data-section="needs" hidden>', tabbed)

    def test_an_approval_shaped_brief(self):
        for text in (
            "Tests pass.\nOpen the PR?",
            "Done.\nCommit and push?",
            "Shall I merge it?",
            "Ready. Proceed (y/n)",
            "ok to push?",
        ):
            self.assertTrue(needs.approval_brief(text), text)
        for not_one in (
            None,
            "",
            "Should I use the cache or the API?",
            "Which of the three designs do you prefer?",
            "Open the PR?\nBut first: what should the retry budget be?",
            "Done.",
            "Answered the yes/no form.",
            "Merge conflicts in auth.py, how should I resolve?",
            "Commit now, or what do you want first?",
            "Which branch should I push (y/n)?",
        ):
            self.assertFalse(needs.approval_brief(not_one), not_one)

    def test_a_cloud_session_with_an_approval_brief_is_batched(self):
        cfg = fleet_cfg(remote=True)
        seed(self.conn)
        page = self.page(cfg)
        self.assertIn("session_01ABC", keys(section(page, "needs")))
        self.assertIn('href="https://claude.ai/code/session_01ABC"', page)
        self.conn.execute(
            "UPDATE issues SET raw_json = json_set(raw_json, '$.brief', ?)"
            " WHERE source = 'claude-remote'",
            ("Pushed the branch.\nOpen the PR?",),
        )
        self.conn.commit()
        page = self.page(cfg)
        self.assertIn("session_01ABC", keys(section(page, "approvals")))


VISIT = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)


class NewTests(NeedsCase):
    """A card whose wait began after the last visit is marked new."""

    def new_keys(self, page):
        return re.findall(
            r'data-key="([^"]*)"[^>]*>(?:(?!</summary>|<div class="act">).)*'
            r'<span class="new">new</span>',
            page,
            re.S,
        )

    def test_only_a_wait_since_the_last_visit_is_new(self):
        seed(self.conn)
        overlay.set_last_visit(self.conn, VISIT)
        page = self.page()
        # PR 1202, s-turn, s-pr and the s-perm approval arrived after it;
        # PR 1201 and issue 950 before it.
        self.assertEqual(
            sorted(self.new_keys(page)),
            sorted([pr_url(1202), "s-turn", "s-pr", "s-perm"]),
        )
        # The order is unchanged by the marker.
        self.assertEqual(
            keys(section(page, "needs")),
            [pr_url(1201), issue_url(950), pr_url(1202), "s-turn", "s-pr"],
        )

    def test_the_expanded_first_card_can_be_new(self):
        seed(self.conn)
        overlay.set_last_visit(self.conn, datetime(2026, 10, 1, tzinfo=timezone.utc))
        main = section(self.page(), "needs")
        first = main[: main.index("<details ")]
        self.assertIn('<span class="new">new</span>', first)

    def test_with_no_visit_nothing_is_new(self):
        seed(self.conn)
        self.assertNotIn('class="new"', self.page())

    def test_mark_all_as_read_sits_under_the_heading_at_render_time(self):
        seed(self.conn)
        main = section(self.page(), "needs")
        # Not inside the h2: the page's text filter rewrites the heading.
        self.assertIn(
            '<h2>Needs you (5)</h2><div class="visit"><button type="button"'
            ' data-ov="visit" data-at="2026-10-24T12:00:00Z"',
            main,
        )
        self.assertIn("Mark all as read", main)

    def test_finished_sessions_are_grouped_closed_at_the_bottom(self):
        seed(self.conn)
        overlay.set_last_visit(self.conn, VISIT)
        page = self.page()
        self.assertGreater(
            page.index('data-section="finished"'),
            page.index('data-section="approvals"'),
        )
        self.assertEqual(
            sorted(finished_keys(page)), sorted(["s-done", "s-null", "s-v1", "s-later"])
        )
        # A turn that asked is in the main list, never here.
        self.assertNotIn("s-turn", finished_keys(page))
        self.assertIn("Finished since you looked (4)", page)
        overlay.set_last_visit(self.conn, NOW)
        self.assertNotIn('data-section="finished"', self.page())


class GateTests(NeedsCase):
    def test_fleet_off_shows_no_github_rows(self):
        seed(self.conn)
        cfg = fleet_cfg()
        cfg["github"]["fleet"] = False
        page = self.page(cfg)
        self.assertIn("github.fleet is off", page)
        self.assertNotIn("https://github.com/", page)
        self.assertIn("s-turn", keys(page))

    def test_no_successful_fleet_run_means_no_prs_or_issues(self):
        seed(self.conn, fleet_ran=False)
        page = self.page()
        self.assertNotIn(pr_url(1201), keys(page))
        self.assertNotIn(issue_url(950), keys(page))
        self.assertIn("s-turn", keys(page))
        server_harness.add_run(self.conn, "ok", NOW, kind=github_fleet.RUN_KIND)
        self.assertIn(issue_url(950), keys(self.page()))


class RouteTests(server_harness.ServerCase):
    fleet = True

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: NOW
        self.httpd.app.cfg["ingest"]["machines"] = {"mini": "sha256:" + "0" * 64}
        conn = db.connect(self.db_path)
        seed(conn)
        conn.close()

    def test_now_is_the_fleet_home_page_and_needs_you_its_second_tab(self):
        status, page = self.request("GET", "/needs")
        self.assertEqual(status, 200)
        self.assertIn("aria-current=page>Needs you", page)
        self.assertIn(pr_url(1201), keys(section(page, "needs")))
        status, home = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("aria-current=page>Now", home)
        self.assertNotIn('data-section="needs"', home)
        self.assertIn('data-section="next"', home)
        nav = home.split("<nav>", 1)[1].split("</nav>", 1)[0]
        tabs = re.findall(r'<a href="([^"]+)"', nav)
        self.assertEqual(tabs[:2], ["/now", "/needs"])
        status, page = self.request("GET", "/board")
        self.assertEqual(status, 200)
        self.assertNotIn('data-section="needs"', page)
        # The Board itself: its tab is current and its heading rendered.
        self.assertIn("aria-current=page>Board", page)
        self.assertIn("<h2>Board", page)

    def test_the_tab_is_on_a_fleet_instance_only(self):
        page = self.request("GET", "/now")[1]
        self.assertIn('href="/needs"', page)


class JiraHomeTest(server_harness.ServerCase):
    def test_a_jira_instance_keeps_the_board_at_root(self):
        page = self.request("GET", "/")[1]
        self.assertNotIn('data-section="needs"', page)
        self.assertNotIn('href="/needs"', page)


if __name__ == "__main__":
    unittest.main()
