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
import include  # type: ignore[import-not-found]  # noqa: E402
import lane_tabs  # type: ignore[import-not-found]  # noqa: E402
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


TURN_CWD = "/h/src/notes"

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
        # Its own checkout, so no other session's process shares its cwd.
        ev("s-turn", "2026-10-24T05:00:00.000Z", "UserPromptSubmit", TURN_CWD),
        ev("s-turn", "2026-10-24T06:00:00.000Z", "Stop", TURN_CWD, asked=True),
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
    cfg["github"].update(enabled=True, scope="owner")
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
        self.assertRegex(main, r'title="waiting since [0-9-]+">3d</span>')

    def test_a_pr_without_a_brief_shows_its_header_once_and_no_brief(self):
        seed(self.conn)
        main = section(self.page(), "needs")
        first = main[: main.index("<details ")]
        self.assertNotIn('class="brief"', first)
        self.assertEqual(first.count("feat: thing 1201"), 1)
        self.assertEqual(first.count(">3d</span>"), 1)
        self.assertEqual(first.count('title="CI success">'), 1)
        # The title links to the PR, so, as on Now, there is no "open the PR";
        # the controls stay.
        self.assertNotIn("open the PR", first)
        self.assertIn("data-ov=", first)

    def test_a_folded_card_links_its_item_from_its_act(self):
        # A folded card's title opens the fold, so its act carries the link.
        seed(self.conn)
        main = section(self.page(), "needs")
        folded = main[main.index("<details ") :]
        self.assertIn(
            f'→ <a href="{issue_url(950)}" target="_blank" rel="noopener">open the issue</a>',
            folded,
        )

    def test_a_card_puts_its_title_first_and_its_ref_after(self):
        seed(self.conn)
        main = section(self.page(), "needs")
        self.assertIn(
            f'<a class="title" href="{pr_url(1201)}" target="_blank" rel="noopener">feat: thing 1201 [#1101]</a>'
            f' <span class="ref">{REPO}#1201</span>',
            main,
        )
        self.assertRegex(
            main,
            r'<summary><span class="lead"><span class="kind k-open"[^>]*><svg .*?</svg>'
            r'</span></span><span class="title">needs a human</span>'
            rf' <span class="ref">{re.escape(REPO)}#950</span>',
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
        cfg["github"]["scope"] = "me"
        page = self.page(cfg)
        self.assertIn('github.scope is not "owner"', page)
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


BRIDGE = "session_01TURNXYZ"
TMUX = "claude-rc:@56.%56"
REGISTRY = [
    # The asked session, idle: the owner's turn, with its Remote Control id.
    {"session_id": "s-turn", "bridge_session_id": BRIDGE, "name": "src-32",
     "status": "idle", "pid": 4100, "updated_at": "2026-10-24T11:58:00.000Z",
     "tmux": TMUX},
    # A permission prompt with no Remote Control id.
    {"session_id": "s-perm", "bridge_session_id": None, "name": None,
     "status": "busy", "pid": 4101, "updated_at": "2026-10-24T11:58:00.000Z",
     "tmux": None},
    # A finished turn, still live.
    {"session_id": "s-done", "bridge_session_id": "session_01DONE",
     "name": "done-thing", "status": "idle", "pid": 4102,
     "updated_at": "2026-10-24T11:58:00.000Z", "tmux": None},
    # Live, and nothing else known about it.
    {"session_id": "s-pr", "bridge_session_id": None, "name": None,
     "status": None, "pid": 4103, "updated_at": None, "tmux": None},
]  # fmt: skip


def seed_registry(conn):
    """A later schema 3 snapshot from the same host, carrying its registry."""
    doc = copy.deepcopy(with_branch(SNAPSHOT))
    doc.update(schema=3, taken_at="2026-10-24T11:59:30.000Z", scan_errors=[],
               registry=copy.deepcopy(REGISTRY), sessions=[], history=[])  # fmt: skip
    snapshot_adapter.ingest(conn, doc, NOW)
    sessions.write(conn, sessions.derive(conn, NOW), NOW)


def card(page, key):
    """From a card's key to the next card or the end of its lane."""
    start = page.find(f'data-key="{key}"')
    assert start >= 0, f"no card {key}"
    ends = [
        i
        for i in (page.find('class="card', start), page.find("</section>", start))
        if i >= 0
    ]
    return page[start : min(ends) if ends else len(page)]


class RegistryTests(NeedsCase):
    def setUp(self):
        super().setUp()
        seed(self.conn)
        seed_registry(self.conn)
        self.page_html = self.page()

    def test_a_session_with_a_bridge_id_links_to_its_remote_control_session(self):
        turn = card(self.page_html, "s-turn")
        self.assertIn(
            f'<a href="https://claude.ai/code/{BRIDGE}" target="_blank"'
            ' rel="noopener">open the session</a>',
            turn,
        )
        self.assertIn('<span class="title">src-32</span>', turn)
        self.assertIn("idle: your turn", turn)
        self.assertNotIn("asked (heuristic)", turn)
        self.assertIn(f"tmux: {TMUX}", turn)

    def test_a_session_without_one_links_to_the_bare_url_and_says_why(self):
        perm = card(self.page_html, "s-perm")
        self.assertIn(
            '<a href="https://claude.ai/code" target="_blank" rel="noopener">'
            "open claude.ai/code (no Remote Control link)</a>",
            perm,
        )
        self.assertIn("permission prompt · busy", perm)
        # A registry entry with nothing but its pid: the heuristic label, the
        # cwd title.
        pr_card = card(self.page_html, "s-pr")
        self.assertIn("asked (heuristic)", pr_card)
        self.assertIn("(no Remote Control link)", pr_card)
        self.assertIn(
            '<span class="title">task-1101 @ bestdan/task-1101</span>', pr_card
        )

    def test_a_malformed_bridge_id_gets_the_bare_url(self):
        for bad in ("session_01/../x", "session_01?x=1", "session_01#x",
                    "other_01", "session_", None, 5):  # fmt: skip
            self.assertEqual(needs.session_url(bad), needs.CODE_URL, bad)
            self.assertIsNone(needs.valid_bridge(bad), bad)
        good = "session_01Ab-c_9"
        self.assertEqual(needs.session_url(good), f"{needs.CODE_URL}/{good}")
        # A mirror row written before the check: the card says why it has no
        # session link, and the agent view carries no id.
        row = {"key": "s", "session_id": "s", "status_bucket": "waiting",
               "status_category": "need_input", "updated": "2026-10-24T11:00:00Z",
               "bridge_session_id": "session_01/../evil"}  # fmt: skip
        n = needs._session(row, NOW)
        assert n is not None
        self.assertEqual(n.url, needs.CODE_URL)
        self.assertIsNone(n.bridge_session_id)
        self.assertEqual(needs.open_label(n), needs.NO_RC_LABEL)

    def test_a_finished_session_takes_its_registry_name_and_link(self):
        done = card(section(self.page_html, "finished"), "s-done")
        self.assertIn('<span class="title">done-thing</span>', done)
        self.assertIn('href="https://claude.ai/code/session_01DONE"', done)


def post_registry(conn, taken_at, registry=(), *, host="mini", schema=3, events=()):
    """A snapshot from `host` at `taken_at` carrying only the hook `events`;
    a registry from schema 3."""
    doc = {"schema": schema, "host": host, "taken_at": taken_at, "worktrees": [],
           "handoffs": [], "tmux": [], "sessions": list(events),
           "history": []}  # fmt: skip
    if schema >= 2:
        doc["scan_errors"] = []
    if schema >= 3:
        doc["registry"] = copy.deepcopy(list(registry))
    snapshot_adapter.ingest(conn, doc, NOW)
    sessions.write(conn, sessions.derive(conn, NOW), NOW)


WITHOUT_TURN = REGISTRY[1:]  # every entry but s-turn's
WITHOUT_PR = REGISTRY[:3]  # every entry but s-pr's


def summary(sid, first=None, ask=None, branch=None, bridge=None):
    return {"session_id": sid, "first_prompt": first, "last_ask": ask,
            "branch": branch, "bridge_session_id": bridge}  # fmt: skip


def post_transcripts(
    conn, transcripts, taken_at="2026-10-24T11:59:40.000Z", registry=REGISTRY
):
    """A schema 5 snapshot: the registry, with transcript summaries."""
    registry = copy.deepcopy(list(registry))
    for entry in registry:
        entry["cwd"] = None
    doc = {"schema": 5, "host": "mini", "taken_at": taken_at, "worktrees": [],
           "handoffs": [], "tmux": [], "sessions": [], "history": [],
           "scan_errors": [], "registry": registry,
           "transcripts": list(transcripts)}  # fmt: skip
    snapshot_adapter.ingest(conn, doc, NOW)
    sessions.write(conn, sessions.derive(conn, NOW), NOW)


ASK = "I can't see it. Should I clone it? And what change do you want?"


class TranscriptTests(NeedsCase):
    def setUp(self):
        super().setUp()
        seed(self.conn)
        seed_registry(self.conn)

    def test_the_card_shows_the_first_prompt_the_ask_and_the_branch(self):
        before = self.page()
        post_transcripts(self.conn, [
            summary("s-turn", "can you see and edit the repo `debtmanager`?", ASK,
                    "bestdan/notes-fix", "cse_01NOTLINKED"),
        ])  # fmt: skip
        page = self.page()
        turn = card(section(page, "needs"), "s-turn")
        self.assertIn(
            '<span class="title">can you see and edit the repo `debtmanager`?</span>'
            ' <span class="stag">src-32</span>',
            turn,
        )
        self.assertIn(f'<div class="ask">› {needs._e(ASK)}</div>', turn)
        self.assertIn("notes: bestdan/notes-fix", turn)
        # The registry's own Remote Control id still decides the link.
        self.assertIn(f'href="https://claude.ai/code/{BRIDGE}"', turn)
        self.assertNotIn("cse_", page)
        # A session with no summary keeps today's card exactly.
        self.assertEqual(card(page, "s-perm"), card(before, "s-perm"))

    def test_a_cse_id_is_never_linked_and_a_session_one_is(self):
        post_transcripts(self.conn, [
            summary("s-perm", "approve the thing", bridge="cse_01ABC"),
            summary("s-pr", "fix task 1101", bridge="session_01FROMTRANSCRIPT"),
        ])  # fmt: skip
        page = self.page()
        perm = card(page, "s-perm")
        self.assertIn("(no Remote Control link)", perm)
        self.assertNotIn("cse_01ABC", page)
        pr_card = card(page, "s-pr")
        self.assertIn('href="https://claude.ai/code/session_01FROMTRANSCRIPT"', pr_card)
        # The transcript's id is carried in the mirror row all the same.
        rows = {
            r["key"]: r for r in needs._rows(self.conn, sessions.SOURCE, sessions.KIND)
        }
        self.assertEqual(rows["s-perm"]["transcript_bridge_session_id"], "cse_01ABC")

    def test_the_open_pr_on_the_sessions_branch_is_linked(self):
        pr_card = card(self.page(), "s-pr")
        self.assertIn(
            f'<a href="{pr_url(1201)}" target="_blank" rel="noopener">PR #1201</a>',
            pr_card,
        )
        # Another repo's PR on the same branch name is not this session's.
        self.assertIsNone(needs.session_pr(
            needs.open_prs(self.conn, fleet_cfg()), "dotfiles", "bestdan/task-1101"))  # fmt: skip
        # Nor is a branch with no open PR.
        self.assertNotIn(">PR #", card(self.page(), "s-turn"))

    def test_a_long_ask_is_shown_cut(self):
        long_ask = "y" * 400
        post_transcripts(self.conn, [summary("s-turn", "go", long_ask)])
        self.assertIn(f"› …{long_ask}</div>", card(self.page(), "s-turn"))

    def test_a_cut_ask_starts_at_its_next_word(self):
        cut = "lia backend for servicing " + "z" * 374
        self.assertEqual(len(cut), 400)
        post_transcripts(self.conn, [summary("s-turn", "go", cut)])
        self.assertIn(
            f"› …backend for servicing {'z' * 374}</div>", card(self.page(), "s-turn")
        )
        # The cut may fall on a word's start; a space there is dropped alone.
        self.assertEqual(needs.ask_excerpt(" " + cut[1:]), "…" + cut[1:].lstrip())
        # One short of the cap was not cut, so it is quoted whole.
        self.assertEqual(needs.ask_excerpt(cut[1:]), cut[1:])


RESUME = f"resume with `claude --resume s-turn` in {TURN_CWD} on mini"


class EndedTests(NeedsCase):
    """A hook session missing from its host's registry has ended."""

    def setUp(self):
        super().setUp()
        seed(self.conn)
        seed_registry(self.conn)

    def end_turn(self, taken_at="2026-10-24T11:59:40.000Z"):
        post_registry(self.conn, taken_at, WITHOUT_TURN)

    def test_an_ended_session_leaves_needs_and_is_finished_with_its_resume(self):
        self.assertIn("s-turn", keys(section(self.page(), "needs")))
        self.end_turn()
        page = self.page()
        self.assertNotIn("s-turn", keys(listed(page)))
        self.assertIn("s-turn", finished_keys(page))
        row = card(section(page, "finished"), "s-turn")
        self.assertIn(
            '<div class="act">→ ended · resume with <code>claude --resume s-turn'
            f"</code> in {TURN_CWD} on mini</div>",
            row,
        )
        self.assertRegex(row, r'title="last active [0-9-]+">6h</span> · on mini<')
        self.assertNotIn("<a ", row.split('<div class="ov">')[0])
        # The live sessions keep their places.
        self.assertIn("s-perm", keys(section(page, "approvals")))
        self.assertIn("s-done", finished_keys(page))

    def test_a_waiting_session_is_listed_ended_even_before_the_last_visit(self):
        overlay.set_last_visit(self.conn, NOW)
        self.end_turn()
        page = self.page()
        self.assertEqual(finished_keys(page), ["s-turn"])
        # With every process gone, each session that waited on me is listed,
        # latest first; a turn that ended, or one working, before the visit
        # stays out.
        post_registry(self.conn, "2026-10-24T11:59:50.000Z", [])
        self.assertEqual(finished_keys(self.page()), ["s-perm", "s-pr", "s-turn"])

    def test_a_host_without_a_registry_ends_nothing(self):
        # seed() alone is a schema 1 snapshot: no session there has ended.
        conn = db.connect(Path(self._td.name) / "plain.db")
        self.addCleanup(conn.close)
        seed(conn)
        post_registry(conn, "2026-10-24T11:59:10.000Z", schema=2)
        rows = needs._rows(conn, sessions.SOURCE, sessions.KIND)
        self.assertTrue(rows)
        self.assertFalse(any(r["ended"] for r in rows))
        self.assertIn(
            "s-turn", keys(section(needs.render(conn, fleet_cfg(), NOW), "needs"))
        )
        # A host whose latest snapshot is schema 2 again ends nothing either,
        # whatever an earlier schema 3 one said.
        self.end_turn()
        post_registry(self.conn, "2026-10-24T11:59:50.000Z", schema=2)
        self.assertIn("s-turn", keys(section(self.page(), "needs")))

    def test_a_live_process_in_the_same_cwd_under_another_id_ends_nothing(self):
        # The process may have moved from s-turn to another id (a `/clear`, a
        # resume) while its registry file kept the old one, so a live entry
        # in s-turn's cwd may be s-turn itself.
        other = {"session_id": "s-other", "bridge_session_id": None,
                 "name": None, "status": "idle", "pid": 4200,
                 "updated_at": None, "tmux": None}  # fmt: skip
        post_registry(
            self.conn, "2026-10-24T11:59:40.000Z", [*WITHOUT_TURN, other],
            events=[ev("s-other", "2026-10-21T09:00:00.000Z",
                         "UserPromptSubmit", TURN_CWD)],
        )  # fmt: skip
        self.assertIn("s-turn", keys(section(self.page(), "needs")))
        # Once that process is gone too, s-turn has ended.
        post_registry(self.conn, "2026-10-24T11:59:50.000Z", WITHOUT_TURN)
        self.assertIn("s-turn", finished_keys(self.page()))

    def test_another_hosts_registry_ends_nothing_here(self):
        post_registry(self.conn, "2026-10-24T11:59:40.000Z", host="lindev")
        self.assertIn("s-turn", keys(section(self.page(), "needs")))

    def test_a_session_back_in_the_registry_is_restored(self):
        self.end_turn()
        self.assertNotIn("s-turn", keys(listed(self.page())))
        post_registry(self.conn, "2026-10-24T11:59:50.000Z", REGISTRY)
        page = self.page()
        self.assertIn("s-turn", keys(section(page, "needs")))
        self.assertNotIn("s-turn", finished_keys(page))
        self.assertIn("idle: your turn", card(section(page, "needs"), "s-turn"))

    def test_the_resume_action_is_structured_and_a_strange_id_gets_none(self):
        self.end_turn()
        p = needs.build(self.conn, fleet_cfg(), NOW, overlay.load(self.conn))
        [turn] = [n for n in p.finished if n.item[1] == "s-turn"]
        self.assertEqual(
            turn.action.as_dict(),
            {"review": RESUME, "command": ["claude", "--resume", "s-turn"]},
        )
        row = {"key": "s; rm -rf ~", "ended": True, "host": "mini",
               "updated": "2026-10-24T11:00:00Z"}  # fmt: skip
        odd = needs.ended_need(row, NOW.replace(hour=0), NOW)
        assert odd is not None
        self.assertIsNone(odd.command)
        self.assertEqual(odd.hint, "")
        self.assertIn('<div class="act">→ ended</div>',
                      needs.finished_row(odd, NOW, overlay.load(self.conn)))  # fmt: skip

    def test_an_ended_session_shows_its_prompt_ask_and_pr_but_no_link(self):
        before = card(section(self.page(), "finished"), "s-done")
        post_transcripts(self.conn, [
            summary("s-pr", "fix task 1101", ASK, bridge="session_01ENDED"),
        ], registry=WITHOUT_PR)  # fmt: skip
        page = self.page()
        row = card(section(page, "finished"), "s-pr")
        self.assertIn(
            '<span class="title">fix task 1101</span>'
            ' <span class="stag">task-1101 @ bestdan/task-1101</span>',
            row,
        )
        self.assertIn(f'<div class="ask">› {needs._e(ASK)}</div>', row)
        self.assertIn(
            f'<div class="act">→ ended · <a href="{pr_url(1201)}" target="_blank"'
            ' rel="noopener">PR #1201</a> · resume with <code>claude --resume s-pr'
            f"</code> in {WT}/task-1101 on mini</div>",
            row,
        )
        # Its transcript's Remote Control id is not linked: the process is gone.
        self.assertNotIn("session_01ENDED", page)
        self.assertNotIn("claude.ai/code", row)
        p = needs.build(self.conn, fleet_cfg(), NOW, overlay.load(self.conn))
        [ended] = [n for n in p.finished if n.item[1] == "s-pr"]
        self.assertIsNone(ended.url)
        self.assertIsNone(ended.bridge_session_id)
        # A finished row with no summary is unchanged.
        self.assertEqual(card(section(page, "finished"), "s-done"), before)

    def test_an_ended_session_without_a_summary_renders_as_before(self):
        self.end_turn()
        row = card(section(self.page(), "finished"), "s-turn")
        self.assertRegex(
            row,
            r'<summary><span class="lead"><span class="kind k-session"[^>]*><svg .*?</svg>'
            r'</span></span><span class="title">notes</span> <span class="meta">'
            r'<span class="age" title="last active [0-9-]+">6h</span> · on mini'
            r'</span></summary><div class="act">→ ended · resume with <code>'
            rf"claude --resume s-turn</code> in {re.escape(TURN_CWD)} on mini</div>"
            r'<div class="ov">',
        )

    def test_a_finished_turn_links_the_pr_on_its_branch(self):
        cwd = f"{WT}/task-1101"
        events = [ev("s-fin", "2026-10-24T10:30:00.000Z", "UserPromptSubmit", cwd),
                  ev("s-fin", "2026-10-24T11:00:00.000Z", "Stop", cwd, asked=False)]  # fmt: skip
        for line in events:
            line["branch"] = "bestdan/task-1101"
        live = {"session_id": "s-fin", "bridge_session_id": None, "name": None,
                "status": "idle", "pid": 4104, "updated_at": None, "tmux": None}  # fmt: skip
        post_registry(self.conn, "2026-10-24T11:59:40.000Z", [*REGISTRY, live],
                      events=events)  # fmt: skip
        row = card(section(self.page(), "finished"), "s-fin")
        self.assertRegex(row, r'title="turn ended [0-9-]+">\d+m</span> · on mini<')
        self.assertIn(
            f'<div class="act">→ <a href="{pr_url(1201)}" target="_blank"'
            ' rel="noopener">PR #1201</a></div>',
            row,
        )


EXTERNAL = re.compile(r"<a\b([^>]*)>", re.S)


def links(page):
    """(href, attrs) for every `<a>` with an href."""
    out = []
    for m in EXTERNAL.finditer(page):
        href = re.search(r'\bhref="([^"]*)"', m.group(1))
        if href:
            out.append((href.group(1), m.group(1)))
    return out


class NewTabTests(server_harness.ServerCase):
    """Every page, on a fixture with Jira, PR, issue and session cards: each
    link that leaves the dashboard opens in a new tab, and none inside it does."""

    fleet = True

    def setUp(self):
        super().setUp()
        self.httpd.app.now = lambda: NOW
        self.httpd.app.cfg["ingest"]["machines"] = {"mini": "sha256:" + "0" * 64}
        conn = db.connect(self.db_path)
        server_harness.seed(conn)
        seed(conn)
        seed_registry(conn)
        conn.close()

    def test_external_links_open_in_a_new_tab_and_internal_ones_do_not(self):
        paths = ["/", *views.ROUTES, "/board", "/health"]
        seen = set()
        for path in paths:
            status, page = self.request("GET", path)
            self.assertEqual(status, 200, path)
            for href, attrs in links(page):
                external = href.startswith(("http://", "https://"))
                new_tab = 'target="_blank"' in attrs and 'rel="noopener"' in attrs
                self.assertEqual(new_tab, external, (path, href))
                if external:
                    seen.add(href.split("/")[2])
        # The fixture reaches every kind of external link the pages draw.
        self.assertLessEqual({"github.com", "example.invalid", "claude.ai"}, seen)


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
