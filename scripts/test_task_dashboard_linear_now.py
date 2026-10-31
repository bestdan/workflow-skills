#!/usr/bin/env python3
"""Linear issues as a source of the shared Now ranker: the read
(linear_api.assigned_issues), the sync step (linear_issues.py,
sync.run_linear_issues), and the candidates (fleet_now.linear_tickets,
link_linear).

Hermetic: a temp DB, a fake Linear runner and a fake key; nothing reaches the
network. Runs on a Jira-off, `github.scope = "me"` instance unless a test says
otherwise. Needs Python 3.11: run via scripts/test-task-dashboard-linear-now.sh.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["TZ"] = "America/New_York"
time.tzset()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task-dashboard"))

import agent_view  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_now  # type: ignore[import-not-found]  # noqa: E402
import freshness  # type: ignore[import-not-found]  # noqa: E402
import linear_api  # type: ignore[import-not-found]  # noqa: E402
import linear_issues  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import sync  # type: ignore[import-not-found]  # noqa: E402

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
CFG = config.deep_merge(
    config.DEFAULTS, {"jira": {"enabled": False}, "linear": {"enabled": True}}
)
OFF = config.deep_merge(CFG, {"linear": {"enabled": False}})
LIN = linear_issues.SOURCE


def ts(days):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def node(key, state_type="started", state="In Progress", **kw):
    """An assignedIssues node as Linear sends it."""
    out = {
        "identifier": key,
        "title": f"title {key}",
        "url": f"https://linear.app/acme/issue/{key.lower()}",
        "priority": 3,
        "priorityLabel": "Medium",
        "dueDate": None,
        "createdAt": ts(10),
        "updatedAt": ts(1),
        "startedAt": ts(2) if state_type == "started" else None,
        "state": {"name": state, "type": state_type},
        "team": {"key": key.split("-")[0]},
        "labels": {"nodes": []},
        "cycle": None,
        "project": None,
        "relations": {"nodes": []},
        "inverseRelations": {"nodes": []},
    }
    out.update(kw)
    return out


def blocks(key, state_type="started"):
    return {"type": "blocks", "issue": {"identifier": key,
            "state": {"type": state_type}}}  # fmt: skip


def blocking(key, state_type="unstarted"):
    return {"type": "blocks", "relatedIssue": {"identifier": key,
            "state": {"type": state_type}}}  # fmt: skip


class FakeLinear:
    """Answers the issue queries, `per_page` nodes a page, and the user lookup
    for the emails in `users`."""

    def __init__(self, nodes=(), fail=None, per_page=50, users=("me@example.com",)):
        self.nodes, self.fail, self.per_page = list(nodes), fail, per_page
        self.users = {u.lower() for u in users}
        self.calls = []

    def __call__(self, body, key, timeout):
        self.calls.append((body, key))
        if self.fail:
            raise self.fail
        if "users(" in body["query"]:
            hit = body["variables"]["email"].lower() in self.users
            return {"data": {"users": {"nodes": [{"id": "u1"}] if hit else []}}}
        start = int(body["variables"]["after"] or 0)
        end = start + self.per_page
        page = {"hasNextPage": end < len(self.nodes), "endCursor": str(end)}
        conn = {"pageInfo": page, "nodes": self.nodes[start:end]}
        if "email" in body["variables"]:
            assert "  issues(" in body["query"], body
            return {"data": {"issues": conn}}
        assert "assignedIssues(" in body["query"], body
        return {"data": {"viewer": {"assignedIssues": conn}}}


def no_key():
    raise linear_api.NoKey("unconfigured", "no Linear key is configured")


class Case(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.conn = db.connect(Path(self._td.name) / "d.db")
        self.addCleanup(self.conn.close)

    def mirror(self, *nodes):
        issues = linear_api.assigned_issues("k", FakeLinear(nodes))
        return linear_issues.write(self.conn, issues, NOW)

    def step(self, runner, resolve_key=lambda: "lin_test_not_a_key", cfg=CFG):
        return sync.run_linear_issues(
            cfg, self.conn, NOW, runner=runner, resolve_key=resolve_key,
            clock=lambda: NOW,
        )  # fmt: skip

    def keys(self):
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT key FROM issues WHERE source = ? ORDER BY key", (LIN,)
            )
        ]

    def pr(self, number, role="author", ci="success", decision=None,
           title=None, head_ref=None):  # fmt: skip
        url = f"https://github.com/o/r/pull/{number}"
        self.conn.execute(
            "INSERT INTO prs (url, repo, number, title, head_ref, is_draft, role,"
            " review_decision, ci_state, raw_json) VALUES (?, 'o/r', ?, ?, ?, 0,"
            " ?, ?, ?, ?)",
            (url, number, title or f"pr {number}", head_ref, role, decision, ci,
             json.dumps({"createdAt": ts(2)})),
        )  # fmt: skip
        self.conn.commit()
        return url

    def by_key(self, cfg=CFG):
        shown = fleet_now.build(self.conn, cfg, NOW, overlay.load(self.conn)).shown
        return {r.need.item[1]: r for r in shown}


class ReadTests(Case):
    def test_it_asks_for_my_open_states_and_follows_every_page(self):
        fake = FakeLinear([node(f"PRE-{n}") for n in range(1, 6)], per_page=2)
        issues = linear_api.assigned_issues("k", fake)
        self.assertEqual([i["key"] for i in issues], [f"PRE-{n}" for n in range(1, 6)])
        self.assertEqual(len(fake.calls), 3)
        body, key = fake.calls[0]
        self.assertEqual(key, "k")
        self.assertEqual(body["variables"]["types"], ["unstarted", "started"])

    def test_a_named_assignee_is_read_by_email_not_as_the_key_user(self):
        fake = FakeLinear([node("PRE-1")])
        (issue,) = linear_api.assigned_issues("k", fake, assignee="me@example.com")
        self.assertEqual(issue["key"], "PRE-1")
        body, _ = fake.calls[-1]
        self.assertEqual(body["variables"]["email"], "me@example.com")
        self.assertNotIn("viewer", body["query"])

    def test_linear_me_naming_no_user_fails_and_keeps_the_rows(self):
        self.mirror(node("PRE-1"))
        cfg = config.deep_merge(CFG, {"linear": {"me": "typo@example.com"}})
        run = self.step(FakeLinear([node("PRE-2")]), cfg=cfg)
        self.assertEqual(run["outcome"], "failed")
        self.assertIn("linear.me names no Linear user", run["error"])
        self.assertEqual(self.keys(), ["PRE-1"])

    def test_the_step_passes_linear_me(self):
        fake = FakeLinear([node("PRE-1")])
        cfg = config.deep_merge(CFG, {"linear": {"me": " me@example.com "}})
        self.assertEqual(self.step(fake, cfg=cfg)["outcome"], "ok")
        self.assertEqual(fake.calls[-1][0]["variables"]["email"], "me@example.com")
        self.step(fake)
        self.assertNotIn("email", fake.calls[-1][0]["variables"])

    def test_linear_me_must_be_a_string(self):
        path = Path(self._td.name) / "config.toml"
        jira_off = "[jira]\nenabled = false\n"
        path.write_text(jira_off + '[linear]\nenabled = true\nme = "me@example.com"\n')
        self.assertEqual(config.load(path)["linear"]["me"], "me@example.com")
        path.write_text(jira_off + "[linear]\nenabled = true\nme = 7\n")
        with self.assertRaisesRegex(config.ConfigError, "linear.me must be a string"):
            config.load(path)

    def test_a_list_cut_short_at_the_cap_is_counted_as_truncated(self):
        more = {"hasNextPage": True}
        cut = node("PRE-2", inverseRelations={"pageInfo": more, "nodes": []})
        counts = self.mirror(node("PRE-1"), cut)
        self.assertEqual(counts["truncated"], 1)
        rows = linear_api.assigned_issues("k", FakeLinear([node("PRE-1"), cut]))
        self.assertEqual([i["truncated"] for i in rows], [False, True])

    def test_only_open_issues_on_the_other_end_of_blocks_count(self):
        n = node(
            "PRE-1",
            inverseRelations={"nodes": [blocks("PRE-8"), blocks("PRE-9", "completed"),
                                        {"type": "related", "issue": {
                                            "identifier": "PRE-7",
                                            "state": {"type": "started"}}}]},
            relations={"nodes": [blocking("PRE-5"), blocking("PRE-6", "canceled")]},
        )  # fmt: skip
        (issue,) = linear_api.assigned_issues("k", FakeLinear([n]))
        self.assertEqual(issue["blocked_by"], ["PRE-8"])
        self.assertEqual(issue["blocks"], ["PRE-5"])


class WriteTests(Case):
    def test_a_row_fills_the_columns_the_jira_score_reads(self):
        cycle = {"name": "Cycle 12", "endsAt": ts(-3), "isActive": True}
        self.mirror(node("PRE-1", "unstarted", "Todo", cycle=cycle, dueDate="2026-11-04",
                         priority=1, priorityLabel="Urgent",
                         labels={"nodes": [{"name": "bug"}]}))  # fmt: skip
        row = self.conn.execute(
            "SELECT kind, status_category, status_category_changed, priority,"
            " due_date, labels, sprint_name, sprint_state, sprint_end, scope"
            " FROM issues WHERE source = 'linear'"
        ).fetchone()
        self.assertEqual(
            row,
            ("ticket", "new", ts(10), "Urgent", "2026-11-04", '["bug"]', "Cycle 12",
             "active", ts(-3), "assigned"),
        )  # fmt: skip

    def test_no_priority_is_null(self):
        self.mirror(node("PRE-1", priority=0, priorityLabel="No priority"))
        (prio,) = self.conn.execute("SELECT priority FROM issues").fetchone()
        self.assertIsNone(prio)

    def test_each_read_replaces_the_last_and_leaves_other_sources_alone(self):
        self.conn.execute(
            "INSERT INTO issues (source, kind, key) VALUES ('jira', 'ticket', 'PRE-1')"
        )
        self.conn.commit()
        self.mirror(node("PRE-1"), node("PRE-2"))
        counts = self.mirror(node("PRE-2"))
        self.assertEqual(self.keys(), ["PRE-2"])
        self.assertEqual(counts["dropped"], 1)
        self.mirror()
        self.assertEqual(self.keys(), [])
        self.assertEqual(
            self.conn.execute("SELECT source FROM issues").fetchall(), [("jira",)]
        )


class StepTests(Case):
    def test_a_good_run_writes_the_rows_and_counts_them(self):
        n = node("PRE-2", inverseRelations={"nodes": [blocks("PRE-9")]})
        run = self.step(FakeLinear([node("PRE-1"), n]))
        self.assertEqual(run["outcome"], "ok")
        self.assertEqual(run["kind"], linear_issues.RUN_KIND)
        counts = json.loads(run["counts"])
        self.assertEqual((counts["issues"], counts["blocked"]), (2, 1))
        self.assertEqual(self.keys(), ["PRE-1", "PRE-2"])

    def test_no_key_is_an_auth_failure_that_keeps_the_rows(self):
        self.mirror(node("PRE-1"))
        fake = FakeLinear()
        run = self.step(fake, resolve_key=no_key)
        self.assertEqual(run["outcome"], "auth_failed")
        self.assertIn("no Linear key (unconfigured)", run["error"])
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.keys(), ["PRE-1"])

    def test_a_refused_key_and_a_failed_call_keep_the_rows(self):
        self.mirror(node("PRE-1"))
        refused = self.step(FakeLinear(fail=linear_api.AuthError("Linear refused")))
        self.assertEqual(refused["outcome"], "auth_failed")
        failed = self.step(FakeLinear(fail=linear_api.LinearError("HTTP 502")))
        self.assertEqual(failed["outcome"], "failed")
        self.assertIn("HTTP 502", failed["error"])
        self.assertEqual(self.keys(), ["PRE-1"])

    def test_run_all_runs_it_only_when_linear_is_on_on_any_instance(self):
        # GitHub off too, so run_all builds no real gh adapter.
        no_gh = {"github": {"enabled": False}}
        on, off_cfg = config.deep_merge(CFG, no_gh), config.deep_merge(OFF, no_gh)
        fake = FakeLinear([node("PRE-1")])
        kinds = [
            r["kind"]
            for r in sync.run_all(
                "incremental", on, self.conn, NOW, linear_runner=fake,
                resolve_key=lambda: "k", clock=lambda: NOW,
            )
        ]  # fmt: skip
        self.assertIn(linear_issues.RUN_KIND, kinds)
        self.assertFalse(sync.fleet_enabled(on))
        off = sync.run_all(
            "incremental", off_cfg, self.conn, NOW, linear_runner=fake,
            resolve_key=lambda: "k", clock=lambda: NOW,
        )  # fmt: skip
        self.assertNotIn(linear_issues.RUN_KIND, [r["kind"] for r in off])

    def test_run_all_resolves_the_key_once_for_both_linear_steps(self):
        calls = []

        def resolve():
            calls.append(1)
            return "k"

        memo = sync.once(resolve)
        self.assertEqual((memo(), memo()), ("k", "k"))
        self.assertEqual(len(calls), 1)

        def missing():
            calls.append(1)
            no_key()

        memo = sync.once(missing)
        for _ in range(2):
            with self.assertRaises(linear_api.NoKey):
                memo()
        self.assertEqual(len(calls), 2)

    def test_the_freshness_panel_names_the_step(self):
        self.assertIn(
            (linear_issues.RUN_KIND, (linear_issues.RUN_KIND,), "Linear issues"),
            freshness.steps(CFG),
        )
        self.assertNotIn(linear_issues.RUN_KIND, [s[0] for s in freshness.steps(OFF)])


class NowTests(Case):
    def test_started_is_started_and_unstarted_is_the_rest(self):
        cycle = {"name": "Cycle 12", "endsAt": ts(-3), "isActive": True}
        self.mirror(node("PRE-1"), node("PRE-2", "unstarted", "Todo", cycle=cycle))
        shown = self.by_key()
        self.assertEqual(shown["PRE-1"].category, "started")
        self.assertEqual(shown["PRE-1"].tier[1], "started")
        self.assertEqual(shown["PRE-1"].need.kind, "ticket")
        self.assertEqual(shown["PRE-1"].need.url, "https://linear.app/acme/issue/pre-1")
        self.assertEqual(shown["PRE-2"].category, "ready")
        self.assertEqual(shown["PRE-2"].tier[1], fleet_now.REST)
        self.assertEqual(shown["PRE-2"].candidate.why,
                         "your Linear issue, Todo in Cycle 12")  # fmt: skip

    def test_it_brings_the_ticket_terms(self):
        self.mirror(node("PRE-1", priority=1, priorityLabel="Urgent",
                         relations={"nodes": [blocking("PRE-5")]}))  # fmt: skip
        terms = {t[0]: t for t in self.by_key()["PRE-1"].terms}
        self.assertEqual(terms["priority"][2], "Urgent (urgent)")
        self.assertGreater(terms["priority"][1], 0)
        self.assertEqual(terms["unblocks"][2], "blocks 1 open issue")

    def test_an_open_blocker_or_the_label_puts_it_in_the_blocked_tier(self):
        self.mirror(
            node("PRE-1", inverseRelations={"nodes": [blocks("PRE-9")]}),
            node("PRE-2", labels={"nodes": [{"name": "Blocked"}]}),
            node("PRE-3", inverseRelations={"nodes": [blocks("PRE-9", "completed")]}),
        )
        shown = self.by_key()
        self.assertEqual(shown["PRE-1"].tier[1], fleet_now.BLOCKED)
        self.assertIn("blocked: by PRE-9", shown["PRE-1"].candidate.why)
        self.assertEqual(shown["PRE-1"].candidate.features["blocked"], "link")
        self.assertEqual(shown["PRE-2"].tier[1], fleet_now.BLOCKED)
        self.assertEqual(shown["PRE-3"].tier[1], "started")

    def test_linear_off_ranks_none_of_the_mirrored_rows(self):
        self.mirror(node("PRE-1"))
        self.assertEqual(self.by_key(OFF), {})

    def test_the_agent_view_lists_it(self):
        self.mirror(node("PRE-1"))
        doc = agent_view.build(self.conn, CFG, "now", NOW)
        (item,) = doc["lanes"][0]["items"]
        self.assertEqual((item["key"], item["kind"], item["tier"]),
                         ("PRE-1", "ticket", "started"))  # fmt: skip

    def test_a_snoozed_issue_leaves_now(self):
        self.mirror(node("PRE-1"))
        overlay.snooze(self.conn, (LIN, "PRE-1"), "2026-11-03")
        self.assertEqual(self.by_key(), {})


class FoldTests(Case):
    """A PR naming a ranked Linear issue folds into it."""

    def test_a_lowercase_branch_folds_an_approved_pr_and_raises_the_tier(self):
        self.mirror(node("PRE-12", "unstarted", "Todo"))
        url = self.pr(4, decision="APPROVED", head_ref="bestdan/pre-12-export-csv")
        shown = self.by_key()
        self.assertNotIn(url, shown)
        r = shown["PRE-12"]
        self.assertEqual(r.tier[1], "ready to merge")
        self.assertEqual([f.need.item[1] for f in r.folded], [url])

    def test_a_key_in_the_title_folds_it(self):
        self.mirror(node("PRE-12"))
        url = self.pr(4, decision="APPROVED", title="feat: export csv [PRE-12]")
        self.assertEqual(
            [f.need.item[1] for f in self.by_key()["PRE-12"].folded], [url]
        )

    def test_a_pr_naming_an_issue_not_ranked_keeps_its_card(self):
        self.mirror(node("PRE-12"))
        url = self.pr(4, decision="APPROVED", head_ref="bestdan/pre-99-other")
        shown = self.by_key()
        self.assertIn(url, shown)
        self.assertEqual(shown["PRE-12"].folded, [])


if __name__ == "__main__":
    unittest.main()
