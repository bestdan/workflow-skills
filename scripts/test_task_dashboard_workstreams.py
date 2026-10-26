#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/workstreams.py and linear_api.py.

The mirror is seeded through the real writers (github_fleet.write, the `prs`
table as the Jira sync's GitHub step writes it), then the `workstreams` sync
step runs with a fake `gh` runner (the open-milestones read) and a fake Linear
runner, which records every request. Nothing reaches GitHub or Linear, and
every owner, repo, team and project here is invented. Needs Python 3.11: run
via scripts/test-task-dashboard-workstreams.sh.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import agent_view  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_now  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import lane_tabs  # type: ignore[import-not-found]  # noqa: E402
import linear_api  # type: ignore[import-not-found]  # noqa: E402
import sync  # type: ignore[import-not-found]  # noqa: E402
import views  # type: ignore[import-not-found]  # noqa: E402
import whynot  # type: ignore[import-not-found]  # noqa: E402
import workstreams  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
REPO = "acme/widgets"


def ts(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pr_url(n):
    return f"https://github.com/{REPO}/pull/{n}"


def issue_url(n):
    return f"https://github.com/{REPO}/issues/{n}"


def issue(n, *labels, milestone=None, title=None, updated=1):
    return {
        "kind": "issue", "key": issue_url(n), "url": issue_url(n), "repo": REPO,
        "number": n, "title": title or f"issue {n}", "labels": list(labels),
        "milestone": {"number": milestone, "title": f"m{milestone}"} if milestone else None,
        "created": ts(30), "updated": ts(updated),
    }  # fmt: skip


def awaiting(n, title, *, closes=(), role="author", updated=1):
    return {
        "kind": "pr", "key": pr_url(n), "url": pr_url(n), "repo": REPO,
        "number": n, "title": title, "head_ref": f"feat/{n}", "role": role,
        "ci_state": "success", "in_queue": False, "queue_misses": ["co_review"],
        "review_decision": None, "created": ts(5), "updated": ts(updated),
        "closes": [{"repo": REPO, "number": i, "milestone": m} for i, m in closes],
    }  # fmt: skip


DESIGN = {
    "kind": "design",
    "key": f"https://github.com/{REPO}/blob/main/dev_docs/designs/2026-10-01-sync.md",
    "url": f"https://github.com/{REPO}/blob/main/dev_docs/designs/2026-10-01-sync.md",
    "repo": REPO,
    "path": "dev_docs/designs/2026-10-01-sync.md",
    "title": "Two-way sync",
    "status": "proposed",
    "created": "2026-10-01",
    "refs": [{"repo": REPO, "number": 13}],
}

ROWS = [
    issue(10, "status:3_started", milestone=3),
    issue(11, milestone=4),
    issue(12, "status:2_ready", milestone=5, updated=20),
    issue(13, title="Track WID-9 rollout"),
    issue(14, title="unrelated"),
    awaiting(20, "feat: export csv [WID-7]", closes=[(10, 3)]),
    awaiting(21, "fix: a thing WID-8"),
    awaiting(22, "chore: bump", role="others"),
    DESIGN,
]

MILESTONES = [
    # number, title, description, open, closed, updated days ago
    (3, "Export", "Ship CSV export.\napproved: yes", 2, 3, 2),
    (4, "Bulk import", "Not yet agreed.", 1, 0, 2),
    (5, "Cleanup", "approved: yes", 1, 1, 25),
]

PROJECTS = [
    {"id": "p-1", "name": "Widget export API", "status": "started", "progress": 0.4},
    {"id": "p-2", "name": "Import pipeline", "status": "planned", "progress": 0.0},
    {"id": "p-3", "name": "Finished", "status": "completed", "progress": 1.0},
    {"id": "p-4", "name": "Ideas", "status": "backlog", "progress": 0.0},
]

ISSUES = {"WID-7": "p-1", "WID-8": None, "WID-9": "p-2"}


class FakeGh:
    """Answers the open-milestones read; records each argv."""

    def __init__(self, milestones=MILESTONES):
        self.calls = []
        self.milestones = milestones

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        assert any("repositoryOwner" in a for a in argv), argv
        # A milestone is (number, title, description, open, closed,
        # updated days ago[, repo]); the repo defaults to REPO.
        by_repo: dict = {}
        for n, t, d, o, c, u, *rest in self.milestones:
            repo = rest[0] if rest else REPO
            by_repo.setdefault(repo, []).append(
                {
                    "number": n, "title": t, "description": d, "dueOn": None,
                    "url": f"https://github.com/{repo}/milestone/{n}",
                    "updatedAt": ts(u),
                    "open": {"totalCount": o}, "closed": {"totalCount": c},
                }
            )  # fmt: skip
        repos = {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {"nameWithOwner": r, "milestones": {"nodes": nodes}}
                for r, nodes in by_repo.items()
            ],
        }
        return 0, json.dumps({"data": {"repositoryOwner": {"repositories": repos}}}), ""


class FakeLinear:
    """Answers linear_api's two queries; records each request body and key."""

    def __init__(self, fail=None, projects=None):
        self.calls = []
        self.fail = fail
        self.projects: list = PROJECTS if projects is None else projects

    def __call__(self, body, key, timeout):
        self.calls.append((body, key))
        if self.fail:
            raise self.fail
        page = {"hasNextPage": False, "endCursor": None}
        if "projects(" in body["query"]:
            nodes = [
                {
                    "id": p["id"], "name": p["name"],
                    "url": f"https://linear.app/acme/project/{p['id']}",
                    "progress": p["progress"], "targetDate": None,
                    "updatedAt": ts(1),
                    "status": {"name": p["status"].title(), "type": p["status"]},
                    "teams": {"nodes": [{"key": "WID"}]},
                }
                for p in self.projects
            ]  # fmt: skip
            return {"data": {"projects": {"pageInfo": page, "nodes": nodes}}}
        assert "issues(" in body["query"], body
        nodes = [
            {"identifier": k, "project": {"id": p} if p else None}
            for k, p in ISSUES.items()
        ]
        return {"data": {"issues": {"pageInfo": page, "nodes": nodes}}}


def seed(conn, rows=ROWS):
    github_fleet.write(conn, rows, NOW)
    conn.execute(
        "INSERT INTO prs (url, repo, number, title, head_ref, is_draft,"
        " review_decision, ci_state, role, updated, raw_json)"
        " VALUES (?, ?, ?, ?, ?, 0, NULL, 'success', 'author', ?, ?)",
        (pr_url(20), REPO, 20, "feat: export csv [WID-7]", "feat/20", ts(1),
         json.dumps({"createdAt": ts(5)})),
    )  # fmt: skip
    conn.execute(
        "INSERT INTO sync_runs (kind, started_at, finished_at, outcome, counts)"
        " VALUES (?, ?, ?, 'ok', '{}')",
        (github_fleet.RUN_KIND, ts(0), ts(0)),
    )
    conn.commit()


def fleet_cfg(**linear):
    cfg = config.deep_merge(config.DEFAULTS, server_harness.USER_CONFIG)
    cfg["github"].update(enabled=True, fleet=True, orgs=["acme"])
    cfg["linear"].update(linear)
    return cfg


def section(page, name):
    m = re.search(
        rf'<section class="lane" data-section="{name}"[^>]*>(.*?)</section>', page, re.S
    )
    assert m, f"no section {name}"
    return m.group(1)


def keys(text):
    return re.findall(r'data-key="([^"]+)"', text)


MS3, MS4, MS5 = (workstreams.ms_id(REPO, n) for n in (3, 4, 5))
P1, P2, P4 = (workstreams.linear_id(p) for p in ("p-1", "p-2", "p-4"))
DOC = workstreams.design_id(REPO, DESIGN["path"])


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "d.db")
        seed(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def step(self, cfg, linear=None, key="lin_test_not_a_key", gh=None):
        self.gh = gh or FakeGh()
        self.linear = linear or FakeLinear()

        def resolve():
            if key is None:
                raise linear_api.NoKey("unconfigured", "no Linear key is configured")
            return key

        return sync.run_workstreams(
            cfg, self.conn, NOW, runner=self.gh, linear_runner=self.linear,
            resolve_key=resolve, clock=lambda: NOW,
        )  # fmt: skip

    def page(self, cfg, path="/workstreams", query=None):
        body = views.render(path, self.conn, cfg, NOW, None, query or {})
        return lane_tabs.tabify(body, path.lstrip("/"), path, query or {})

    def by_id(self, cfg):
        return {c.id: c for c in workstreams.cards(self.conn, cfg, NOW)}


class SourcesTests(Case):
    def test_each_source_lands_in_its_lane_with_progress_and_members(self):
        cfg = fleet_cfg(enabled=True)
        run = self.step(cfg)
        self.assertEqual((run["kind"], run["outcome"]), ("workstreams", "ok"))
        tab = workstreams.lanes(self.conn, cfg, NOW)
        lanes = {lane.id: sorted(c.id for c in lane.items) for lane in tab.lanes}
        self.assertEqual(
            lanes,
            {
                "in-progress": sorted([MS3, P1]),
                "planned": sorted([MS4, P2]),
                "design": sorted([P4, DOC]),
                "stalled": [MS5],
                "complete": [],
            },
        )
        cards = self.by_id(cfg)
        # A completed project is not a workstream.
        self.assertNotIn(workstreams.linear_id("p-3"), cards)
        ms3 = cards[MS3]
        self.assertEqual((ms3.done, ms3.total), (3, 5))
        self.assertEqual((ms3.prs, ms3.issues), (1, 1))
        self.assertEqual((ms3.open_prs, ms3.waiting), (1, 1))
        self.assertEqual(ms3.next, "1 PR waiting on you")
        self.assertEqual(
            {m["item_key"]: m["via"] for m in ms3.members},
            {issue_url(10): "milestone", pr_url(20): "closes #10"},
        )
        p1 = cards[P1]
        self.assertEqual((p1.percent, [m["item_key"] for m in p1.members]),
                         (40, [pr_url(20)]))  # fmt: skip
        doc = cards[DOC]
        self.assertEqual((doc.done, doc.total), (0, 1))
        self.assertEqual([m["item_key"] for m in doc.members], [issue_url(13)])
        self.assertEqual(doc.next, "decide the design")
        self.assertEqual(cards[MS4].next,
                         "approve the milestone: add `approved: yes` to its description")  # fmt: skip
        self.assertEqual(cards[P4].next, "plan the project")

    def test_a_linear_key_in_a_title_joins_its_project_in_one_batched_lookup(self):
        cfg = fleet_cfg(enabled=True)
        self.step(cfg)
        bodies = [b for b, _ in self.linear.calls]
        self.assertEqual(len(bodies), 2)  # the projects, then one key lookup
        lookups = [b for b in bodies if "issues(" in b["query"]]
        self.assertEqual(len(lookups), 1)
        self.assertEqual(
            lookups[0]["variables"]["filter"],
            {"or": [{"team": {"key": {"eq": "WID"}}, "number": {"in": [7, 8, 9]}}]},
        )
        cards = self.by_id(cfg)
        self.assertEqual([m["item_key"] for m in cards[P2].members], [issue_url(13)])
        # PR 21's key names an issue in no project, so the PR joins nothing.
        everywhere = {m["item_key"] for c in cards.values() for m in c.members}
        self.assertNotIn(pr_url(21), everywhere)
        self.assertEqual({k for _, k in self.linear.calls}, {"lin_test_not_a_key"})

    def test_linear_off_makes_no_call_and_the_tab_renders(self):
        cfg = fleet_cfg(enabled=False)
        run = self.step(cfg, linear=FakeLinear(fail=AssertionError("called")))
        self.assertEqual(run["outcome"], "ok")
        self.assertEqual(self.linear.calls, [])
        self.assertEqual(json.loads(run["counts"])["linear"], "off")
        page = self.page(cfg)
        self.assertIn(MS3, keys(page))
        self.assertFalse([k for k in keys(page) if k.startswith("linear:")])

    def test_no_key_makes_no_call_and_says_so(self):
        cfg = fleet_cfg(enabled=True)
        run = self.step(cfg, linear=FakeLinear(fail=AssertionError("called")), key=None)
        self.assertEqual(self.linear.calls, [])
        self.assertEqual(run["outcome"], "ok")
        self.assertEqual(json.loads(run["counts"])["linear"], "no key")
        page = self.page(cfg)
        self.assertIn('data-linear="no key"', page)
        self.assertIn(MS3, keys(page))

    def test_a_failed_linear_read_keeps_the_last_good_projects(self):
        cfg = fleet_cfg(enabled=True)
        self.step(cfg)
        run = self.step(cfg, linear=FakeLinear(fail=linear_api.LinearError("HTTP 502")))
        self.assertEqual(run["outcome"], "ok")
        self.assertEqual(json.loads(run["counts"])["linear"], "failed")
        self.assertIn("502", run["error"])
        cards = self.by_id(cfg)
        self.assertIn(P1, cards)
        self.assertEqual([m["item_key"] for m in cards[P1].members], [pr_url(20)])

    def test_a_refused_key_says_so_and_keeps_the_last_good_projects(self):
        cfg = fleet_cfg(enabled=True)
        self.step(cfg)
        refused = FakeLinear(fail=linear_api.AuthError("Linear refused the key"))
        run = self.step(cfg, linear=refused)
        self.assertEqual(run["outcome"], "ok")
        self.assertEqual(json.loads(run["counts"])["linear"], "auth failed")
        self.assertIn(P1, self.by_id(cfg))
        self.assertIn('data-linear="auth failed"', self.page(cfg))

    def test_a_resolver_that_will_not_load_skips_only_linear(self):
        cfg = fleet_cfg(enabled=True)
        missing = Path(self.tmp.name) / "no-such" / "_secret_resolve.py"
        env = {"LINEAR_API_KEY_REF": "op://TestVault/Item With Spaces/credential"}
        no_file = Path(self.tmp.name) / "no-key-file"
        with mock.patch.object(linear_api, "_SECRET_RESOLVE", missing):
            with self.assertRaises(linear_api.NoKey) as caught:
                linear_api.resolve_key(env, no_file)
            self.assertEqual(caught.exception.category, "missing")
            self.gh, self.linear = FakeGh(), FakeLinear(fail=AssertionError("called"))
            run = sync.run_workstreams(
                cfg, self.conn, NOW, runner=self.gh, linear_runner=self.linear,
                resolve_key=lambda: linear_api.resolve_key(env, no_file),
                clock=lambda: NOW,
            )  # fmt: skip
        self.assertEqual(run["outcome"], "ok")
        self.assertEqual(json.loads(run["counts"])["linear"], "no key (missing)")
        self.assertEqual(self.linear.calls, [])
        self.assertIn(MS3, self.by_id(cfg))

    def test_a_paused_project_is_stalled_whatever_its_activity(self):
        cfg = fleet_cfg(enabled=True)
        paused = [{"id": "p-9", "name": "On hold", "status": "paused", "progress": 0.5}]
        self.step(cfg, linear=FakeLinear(projects=paused))
        c = self.by_id(cfg)[workstreams.linear_id("p-9")]
        self.assertEqual(
            (c.lane, c.next, c.todo),
            ("stalled", "the project is paused: resume or close it", True),
        )

    def test_a_failed_milestone_read_fails_the_step_and_keeps_the_rows(self):
        cfg = fleet_cfg(enabled=False)
        self.step(cfg)
        before = sorted(self.by_id(cfg))

        def broken(argv, timeout):
            return 1, "", "HTTP 502: bad gateway"

        run = self.step(cfg, gh=broken)
        self.assertEqual(run["outcome"], "failed")
        self.assertEqual(sorted(self.by_id(cfg)), before)

    def test_fleet_off_runs_no_step_and_the_tab_says_so(self):
        cfg = fleet_cfg()
        cfg["github"]["fleet"] = False
        cfg["ingest"]["machines"] = {"box": "sha256:" + "0" * 64}
        rows = sync.run_all(
            "full", cfg, self.conn, NOW,
            jira=sync.NoJiraSource(), github=None, clock=lambda: NOW,
            fleet_runner=FakeGh(), linear_runner=FakeLinear(fail=AssertionError()),
        )  # fmt: skip
        self.assertNotIn("workstreams", [r["kind"] for r in rows])
        self.assertIn("github.fleet is off", workstreams.render(self.conn, cfg, NOW))


class StalledTests(Case):
    def test_no_member_activity_for_the_window_is_stalled(self):
        cfg = fleet_cfg()
        self.step(cfg)
        c = self.by_id(cfg)[MS5]
        self.assertEqual((c.lane, c.next), ("stalled", "no activity for 20d"))
        stalled = section(self.page(cfg), "stalled")
        self.assertIn("no activity for 20d", stalled)

    def test_the_window_is_configurable(self):
        cfg = fleet_cfg()
        cfg["workstreams"]["stalled_days"] = 30
        self.step(cfg)
        self.assertEqual(self.by_id(cfg)[MS5].lane, "planned")

    def test_a_workstream_with_no_members_uses_its_own_activity(self):
        cfg = fleet_cfg()
        self.step(cfg, gh=FakeGh([(9, "Empty", "approved: yes", 0, 0, 40)]))
        self.assertEqual(self.by_id(cfg)[workstreams.ms_id(REPO, 9)].lane, "stalled")


def design(repo, path, title, refs=()):
    url = f"https://github.com/{repo}/blob/main/{path}"
    return {
        "kind": "design", "key": url, "url": url, "repo": repo, "path": path,
        "title": title, "status": "proposed", "created": "2026-10-01",
        "refs": [{"repo": r, "number": n} for r, n in refs],
    }  # fmt: skip


class LaneRuleTests(Case):
    def test_a_milestone_with_every_issue_closed_is_complete(self):
        cfg = fleet_cfg()
        done = workstreams.ms_id(REPO, 6)
        self.step(cfg, gh=FakeGh(MILESTONES + [(6, "Done", "approved: yes", 0, 5, 30)]))
        c = self.by_id(cfg)[done]
        self.assertEqual(
            (c.lane, c.next, c.todo), ("complete", "close the milestone", True)
        )
        self.assertEqual(workstreams.progress_text(c), "all 5 issues closed")
        lane = section(self.page(cfg), "complete")
        self.assertEqual(keys(lane), [done])
        self.assertIn("all 5 issues closed", lane)
        self.assertIn("close the milestone", lane)
        doc = agent_view.build(self.conn, cfg, "workstreams", NOW)
        self.assertEqual([lane["id"] for lane in doc["lanes"]][-1], "complete")
        item = next(
            i for lane in doc["lanes"] for i in lane["items"] if i["key"] == done
        )
        self.assertEqual((item["lane"], item["action"]),
                         ("complete", {"review": "close the milestone"}))  # fmt: skip

    def test_a_closed_milestone_with_an_open_pr_is_still_in_progress(self):
        cfg = fleet_cfg()
        github_fleet.write(
            self.conn,
            ROWS + [awaiting(30, "fix: follow-up", closes=[(99, 6)], role="others")],
            NOW,
        )
        self.step(cfg, gh=FakeGh(MILESTONES + [(6, "Done", "approved: yes", 0, 5, 30)]))
        c = self.by_id(cfg)[workstreams.ms_id(REPO, 6)]
        self.assertEqual((c.lane, c.open_prs), ("in-progress", 1))
        self.assertEqual(c.next, "1 open PR, none waiting on you")

    def test_an_unapproved_milestone_with_open_issues_is_planned_or_stalled(self):
        cfg = fleet_cfg()
        idle, fresh, empty = (workstreams.ms_id(REPO, n) for n in (7, 8, 9))
        self.step(cfg, gh=FakeGh(MILESTONES + [
            (7, "Old backlog", "Not agreed.", 3, 2, 40),
            (8, "New backlog", "", 2, 0, 2),
            (9, "Idea", "", 0, 0, 1),
        ]))  # fmt: skip
        cards = self.by_id(cfg)
        approve = "approve the milestone: add `approved: yes` to its description"
        self.assertEqual((cards[idle].lane, cards[idle].next, cards[idle].todo),
                         ("stalled", approve + ", or close it", True))  # fmt: skip
        self.assertEqual((cards[fresh].lane, cards[fresh].next), ("planned", approve))
        self.assertIn("not approved", workstreams.meta_parts(cards[fresh], NOW))
        self.assertIn("not approved", section(self.page(cfg), "planned"))
        self.assertEqual(cards[empty].lane, "design")


class FoldTests(Case):
    def fold(self, *docs, milestones=MILESTONES, rows=ROWS):
        github_fleet.write(self.conn, rows + list(docs), NOW)
        cfg = fleet_cfg()
        self.step(cfg, gh=FakeGh(milestones))
        return cfg

    def designs_of(self, cfg, ws):
        return [d["id"] for d in self.by_id(cfg)[ws].ws.get("designs") or []]

    def test_a_doc_titled_as_a_milestone_folds_into_it(self):
        cfg = self.fold(
            design(REPO, "dev_docs/designs/2026-09-01-export.md", "Export!")
        )
        cards = self.by_id(cfg)
        folded = workstreams.design_id(REPO, "dev_docs/designs/2026-09-01-export.md")
        self.assertNotIn(folded, cards)
        self.assertEqual([d["name"] for d in cards[MS3].ws["designs"]],
                         ["2026-09-01-export.md"])  # fmt: skip
        card = section(self.page(cfg), "in-progress")
        self.assertIn(">design: 2026-09-01-export.md</a>", card)
        doc = agent_view.build(self.conn, cfg, "workstreams", NOW)
        ms3 = next(i for lane in doc["lanes"] for i in lane["items"] if i["key"] == MS3)
        self.assertEqual(ms3["designs"][0]["name"], "2026-09-01-export.md")
        self.assertNotIn(
            folded, [i["key"] for lane in doc["lanes"] for i in lane["items"]]
        )

    def test_a_doc_whose_refs_are_a_milestones_issues_folds_into_it(self):
        cfg = self.fold(
            design(REPO, "dev_docs/designs/bulk.md", "Importing in bulk", [(REPO, 11)])
        )
        cards = self.by_id(cfg)
        self.assertNotIn(workstreams.design_id(REPO, "dev_docs/designs/bulk.md"), cards)
        self.assertEqual([d["name"] for d in cards[MS4].ws["designs"]], ["bulk.md"])
        # The doc that names a milestoneless issue stays a design.
        self.assertEqual(cards[DOC].lane, "design")

    def test_a_filename_in_two_repos_is_listed_once(self):
        path = "dev_docs/designs/2026-09-02-export.md"
        cfg = self.fold(
            design("acme/tools", path, "Export"), design(REPO, path, "Export")
        )
        cards = self.by_id(cfg)
        self.assertFalse([c for c in cards if c.endswith(path)])
        self.assertEqual([d["id"] for d in cards[MS3].ws["designs"]],
                         [workstreams.design_id(REPO, path)])  # fmt: skip

    def test_an_unmatched_doc_in_two_repos_stays_in_design_once(self):
        path = "dev_docs/designs/2026-09-03-later.md"
        cfg = self.fold(
            design("acme/tools", path, "Later"), design(REPO, path, "Later")
        )
        design_lane = section(self.page(cfg), "design")
        self.assertEqual([k for k in keys(design_lane) if k.endswith(path)],
                         [workstreams.design_id("acme/tools", path)])  # fmt: skip
        self.assertIn("no members yet", design_lane)

    def test_the_milestone_with_the_most_refs_wins(self):
        path = "dev_docs/designs/split.md"
        cfg = self.fold(
            design(REPO, path, "Split", [(REPO, 10), (REPO, 11), (REPO, 15)]),
            rows=ROWS + [issue(15, milestone=4)],
        )
        self.assertEqual(self.designs_of(cfg, MS4), [workstreams.design_id(REPO, path)])
        self.assertEqual(self.designs_of(cfg, MS3), [])

    def test_a_tie_goes_to_the_docs_own_repo_then_the_lowest_id(self):
        shared = MILESTONES + [
            (1, "Shared", "approved: yes", 1, 0, 1, "acme/tools"),
            (7, "Shared", "approved: yes", 1, 0, 1),
        ]
        tools, widgets = workstreams.ms_id("acme/tools", 1), workstreams.ms_id(REPO, 7)
        own = design(REPO, "dev_docs/designs/own.md", "Shared")
        other = design("acme/other", "dev_docs/designs/other.md", "Shared")
        cfg = self.fold(own, other, milestones=shared)
        self.assertEqual(self.designs_of(cfg, widgets),
                         [workstreams.design_id(REPO, "dev_docs/designs/own.md")])  # fmt: skip
        self.assertEqual(self.designs_of(cfg, tools),
                         [workstreams.design_id("acme/other", "dev_docs/designs/other.md")])  # fmt: skip

    def test_the_first_copy_is_kept_when_the_milestones_repo_has_none(self):
        path = "dev_docs/designs/2026-09-04-shared.md"
        cfg = self.fold(
            design("acme/zzz", path, "Export"), design("acme/other", path, "Export")
        )
        self.assertEqual(self.designs_of(cfg, MS3),
                         [workstreams.design_id("acme/other", path)])  # fmt: skip

    def test_titles_normalize_across_scripts_and_an_empty_one_never_folds(self):
        self.assertEqual(workstreams.normalize_title("Café_Ünï — v2!"), "café ünï v2")
        cfg = self.fold(
            design(REPO, "dev_docs/designs/cafe.md", "CAFÉ"),
            design(REPO, "dev_docs/designs/marks.md", "???"),
            milestones=MILESTONES + [
                (6, "café", "approved: yes", 1, 0, 1),
                (7, "!!!", "approved: yes", 1, 0, 1),
            ],
        )  # fmt: skip
        self.assertEqual(self.designs_of(cfg, workstreams.ms_id(REPO, 6)),
                         [workstreams.design_id(REPO, "dev_docs/designs/cafe.md")])  # fmt: skip
        self.assertEqual(self.designs_of(cfg, workstreams.ms_id(REPO, 7)), [])
        marks = workstreams.design_id(REPO, "dev_docs/designs/marks.md")
        self.assertEqual(self.by_id(cfg)[marks].lane, "design")


class FilterTests(Case):
    def test_ws_filters_pull_requests_and_issues_to_the_members(self):
        cfg = fleet_cfg()
        self.step(cfg)
        q = {"ws": [MS3]}
        prs = self.page(cfg, "/prs", q)
        self.assertIn(pr_url(20), keys(prs))
        self.assertNotIn(pr_url(21), keys(prs))
        self.assertNotIn(pr_url(22), keys(prs))
        self.assertIn('data-ws="ms:acme/widgets#3"', prs)
        issues = self.page(cfg, "/issues", q)
        self.assertEqual(sorted(k for k in keys(issues) if "/issues/" in k),
                         [issue_url(10)])  # fmt: skip
        # A lane tab keeps the filter.
        self.assertIn("ws=ms%3Aacme%2Fwidgets%233", issues)
        # Unfiltered, the others are back.
        self.assertIn(pr_url(21), keys(self.page(cfg, "/prs")))

    def test_an_unknown_ws_filters_to_nothing_and_says_so(self):
        cfg = fleet_cfg()
        self.step(cfg)
        prs = self.page(cfg, "/prs", {"ws": ["ms:nope#1"]})
        self.assertIn("No workstream ms:nope#1", prs)
        self.assertEqual([k for k in keys(prs) if k.startswith("https://")], [])

    def test_cards_link_to_the_filtered_pages(self):
        cfg = fleet_cfg()
        self.step(cfg)
        card = section(self.page(cfg), "in-progress")
        self.assertIn('href="/prs?ws=ms%3Aacme%2Fwidgets%233">Pull requests (1)', card)
        self.assertIn('href="/issues?ws=ms%3Aacme%2Fwidgets%233">Issues (1)', card)


class AgentViewTests(Case):
    def test_view_workstreams_json_matches_the_page(self):
        cfg = fleet_cfg(enabled=True)
        self.step(cfg)
        self.assertIn("workstreams", agent_view.available(cfg))
        doc = agent_view.build(self.conn, cfg, "workstreams", NOW)
        page = self.page(cfg)
        tabs = {lane.id: lane.count for lane in lane_tabs.lanes(page)}
        self.assertEqual({lane["id"]: lane["count"] for lane in doc["lanes"]}, tabs)
        for lane in doc["lanes"]:
            self.assertEqual(
                [i["key"] for i in lane["items"]], keys(section(page, lane["id"]))
            )
        ms3 = next(i for lane in doc["lanes"] for i in lane["items"] if i["key"] == MS3)
        self.assertEqual(ms3["progress"], {"done": 3, "total": 5, "percent": None})
        self.assertEqual(
            sorted((m["key"], m["via"]) for m in ms3["members"]),
            [(issue_url(10), "milestone"), (pr_url(20), "closes #10")],
        )
        self.assertEqual(ms3["filters"]["prs"], "/prs?ws=ms%3Aacme%2Fwidgets%233")
        self.assertEqual(ms3["action"], {"review": "1 PR waiting on you"})
        # A status line is evidence, never an action.
        ms5 = next(i for lane in doc["lanes"] for i in lane["items"] if i["key"] == MS5)
        self.assertIsNone(ms5["action"])
        self.assertEqual(ms5["evidence"], "no activity for 20d")
        self.assertNotIn("→ no activity", section(page, "stalled"))
        self.assertEqual(doc["notes"]["linear"], "ok")

    def test_the_nav_names_workstreams_after_issues(self):
        cfg = fleet_cfg()
        nav = views.nav("/workstreams", cfg)
        names = re.findall(r'<a href="(/[a-z-]+)"', nav)
        self.assertEqual(names[names.index("/issues") + 1], "/workstreams")
        self.assertNotIn("/workstreams", views.nav("/board", config.DEFAULTS))


class ServerTests(server_harness.ServerCase):
    """The pages over HTTP, from the in-process server on a free port."""

    fleet = True

    def test_the_tab_and_the_filter_are_served(self):
        conn = db.connect(self.db_path)
        try:
            seed(conn)
            cfg = fleet_cfg()
            sync.run_workstreams(
                cfg, conn, NOW, runner=FakeGh(), clock=lambda: NOW,
                linear_runner=FakeLinear(fail=AssertionError("called")),
            )  # fmt: skip
        finally:
            conn.close()
        status, page = self.request("GET", "/workstreams")
        self.assertEqual(status, 200)
        self.assertIn('href="/workstreams" aria-current=page>Workstreams', page)
        self.assertIn('class="lane-tabs"', page)
        self.assertIn(MS3, keys(page))
        status, prs = self.request("GET", "/prs?ws=" + MS3.replace("#", "%23"))
        self.assertEqual(status, 200)
        self.assertIn(pr_url(20), keys(prs))
        self.assertNotIn(pr_url(21), keys(prs))


class TuneFeatureTests(Case):
    def test_now_records_an_items_workstream_as_an_unscored_feature(self):
        cfg = fleet_cfg()
        self.step(cfg)
        ranked = {r.need.item[1]: r for r in fleet_now.candidates(self.conn, cfg, NOW)}
        r = ranked[issue_url(10)]
        self.assertEqual(whynot.features(r, NOW)["workstream"], "Export")
        self.assertIn("workstream", whynot.FEATURES)
        self.assertNotIn("workstream", whynot.SCORED_BY)


class DesignRefTests(unittest.TestCase):
    def test_urls_long_and_bare_refs_and_nothing_else(self):
        text = (
            "Follows #12 and acme/tools#3, see"
            " https://github.com/acme/widgets/pull/99. Not issue#5, &#39;, ## h,"
            " or #123abc. #12 again."
        )
        self.assertEqual(
            github_fleet.design_refs(text, REPO),
            [
                {"repo": REPO, "number": 99},
                {"repo": REPO, "number": 12},
                {"repo": "acme/tools", "number": 3},
            ],
        )


class KeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "linear_api_key"

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_environment_wins(self):
        self.file.write_text("from-file\n")
        os.chmod(self.file, 0o600)
        env = {"LINEAR_API_KEY": "from-env"}
        self.assertEqual(linear_api.resolve_key(env, self.file), "from-env")

    def test_a_private_key_file_is_read(self):
        self.file.write_text("from-file\n")
        os.chmod(self.file, 0o600)
        self.assertEqual(linear_api.resolve_key({}, self.file), "from-file")

    def test_a_key_file_others_can_read_is_refused_without_echoing_it(self):
        self.file.write_text("secret-value\n")
        os.chmod(self.file, 0o644)
        with self.assertRaises(linear_api.NoKey) as caught:
            linear_api.resolve_key({}, self.file)
        self.assertEqual(caught.exception.category, "key-file-mode")
        self.assertNotIn("secret-value", str(caught.exception))
        self.assertIn("chmod 600", str(caught.exception))

    def test_nothing_configured_is_unconfigured(self):
        with self.assertRaises(linear_api.NoKey) as caught:
            linear_api.resolve_key({}, self.file)
        self.assertEqual(caught.exception.category, "unconfigured")

    def test_a_reference_goes_through_the_plugins_resolver(self):
        ref = "op://TestVault/Item With Spaces/credential"
        env = {"LINEAR_API_KEY_REF": ref, "LINEAR_API_KEY_RESOLVER": "nope"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("LINEAR_API_KEY", None)
            with self.assertRaises(linear_api.NoKey) as caught:
                linear_api.resolve_key(dict(os.environ), self.file)
        self.assertEqual(caught.exception.category, "unknown-resolver")
        self.assertNotIn("Item With Spaces", str(caught.exception))

    def test_config_refuses_a_key_in_the_linear_table(self):
        p = Path(self.tmp.name) / "config.toml"
        p.write_text('[jira]\nenabled = false\n[linear]\napi_key = "x"\n')
        with self.assertRaises(config.ConfigError) as caught:
            config.load(p)
        self.assertIn("never in config", str(caught.exception))
        p.write_text("[jira]\nenabled = false\n")
        cfg = config.load(p)
        self.assertEqual((cfg["linear"]["enabled"], cfg["workstreams"]["stalled_days"]),
                         (False, 14))  # fmt: skip


class LinearApiTests(unittest.TestCase):
    def test_graphql_errors_and_auth(self):
        def answer(payload):
            return lambda body, key, timeout: payload

        auth = {
            "errors": [
                {"message": "bad", "extensions": {"code": "AUTHENTICATION_ERROR"}}
            ]
        }
        with self.assertRaises(linear_api.AuthError):
            linear_api.projects("k", answer(auth))
        with self.assertRaises(linear_api.LinearError):
            linear_api.projects("k", answer({"errors": [{"message": "nope"}]}))
        with self.assertRaises(linear_api.LinearError):
            linear_api.projects("k", answer(["not", "an", "object"]))

    def test_no_keys_means_no_lookup(self):
        def never(body, key, timeout):
            raise AssertionError("called")

        self.assertEqual(linear_api.project_of_keys([], "k", never), {})

    def test_keys_are_taken_only_for_known_teams(self):
        self.assertEqual(
            linear_api.keys_in("WID-7 and OTHER-3 and WID-07, wid-8", {"WID"}),
            ["WID-7"],
        )


if __name__ == "__main__":
    unittest.main()
