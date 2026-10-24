#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/sync.py and the fake acli.

The sync is driven through the real adapters (jira_acli, github_gh) with fake
runners in place of acli and gh, so the parsing under test is the adapters'
own. The end-to-end cases run dashboard.py against dev/fake-acli. Everything
lives under tempdirs. Needs Python 3.11: run via scripts/test-task-dashboard-sync.sh.
"""

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
FIXTURES = PKG / "fixtures" / "acli"
FAKE_ACLI = PKG / "dev" / "fake-acli"

# sync.py imports its siblings as top-level modules, as dashboard.py arranges.
sys.path.insert(0, str(PKG))

# mypy checks this file without scripts/task-dashboard on its path; the
# modules themselves are type-checked in their own group (scripts/typecheck.sh).
import config  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import jira_acli  # type: ignore[import-not-found]  # noqa: E402
import sync  # type: ignore[import-not-found]  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)

USER_CONFIG = {
    "jira": {
        "site": "example.invalid",
        "board_ids": [77],
        "fields": {"sprint": "customfield_10303", "flagged": "customfield_10007"},
    },
    "github": {"enabled": False, "orgs": ["acme"]},
}


def make_config(**github):
    cfg = config.deep_merge(config.DEFAULTS, USER_CONFIG)
    cfg["github"].update(github)
    return cfg


def view_payload(key, blocks=(), relates=(), summary=None):
    """A `view` response for `key`, built on the all-null fixture."""
    raw = json.loads((FIXTURES / "view-all-null.json").read_text())
    raw["key"] = key
    f = raw["fields"]
    f["summary"] = summary or f"Issue {key}"
    f["status"] = {"name": "To Do", "statusCategory": {"key": "new"}}
    links = []
    for kind, targets in (("Blocks", blocks), ("Relates", relates)):
        for target in targets:
            links.append(
                {
                    "type": {"name": kind},
                    "outwardIssue": {
                        "key": target,
                        "fields": {"status": {"statusCategory": {"key": "new"}}},
                    },
                }
            )
    f["issuelinks"] = links
    return raw


class FakeAcli:
    """A runner answering acli argv from in-memory state.

    `scope_keys` is what a scope search returns; a `key in (...)` search
    returns the listed keys that `changed` contains (all of them when `changed`
    is None). `views` maps key to payload; a key missing from it gets a plain
    synthesized payload. `fail_view` keys fail every attempt, `raise_on` names
    an argv word whose call fails, `auth` makes every call an auth failure.
    """

    def __init__(self, scope_keys, views=None):
        self.scope_keys = list(scope_keys)
        self.views = dict(views or {})
        self.changed = None
        self.fail_view = set()
        self.raise_on = None
        self.auth = False
        self.sprints = [{"id": 503, "name": "S42", "state": "active"}]
        self.calls = []

    def jqls(self):
        return [a[a.index("--jql") + 1] for a in self.calls if "--jql" in a]

    def viewed(self):
        return [a[4] for a in self.calls if a[2:4] == ["workitem", "view"]]

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if self.auth:
            return 1, "", "Error: unauthorized: please log in again"
        if self.raise_on and self.raise_on in argv:
            return 1, "", "Error: something broke"
        if argv[2:4] == ["workitem", "search"]:
            jql = argv[argv.index("--jql") + 1]
            m = re.search(r"key in \(([^)]*)\)", jql)
            if m:
                keys = [k.strip() for k in m.group(1).split(",")]
                if self.changed is not None:
                    keys = [k for k in keys if k in self.changed]
            else:
                keys = self.scope_keys
            return 0, json.dumps([{"key": k} for k in keys]), ""
        if argv[2:4] == ["workitem", "view"]:
            key = argv[4]
            if key in self.fail_view:
                return 1, "", "Error: failed to fetch work item details"
            return 0, json.dumps(self.views.get(key) or view_payload(key)), ""
        if argv[2:4] == ["board", "list-sprints"]:
            return 0, json.dumps({"sprints": self.sprints}), ""
        raise AssertionError(f"unexpected acli call: {argv}")


class FakeGh:
    """A runner answering the GitHub adapter's gh calls."""

    def __init__(self, authored=(), ci="SUCCESS"):
        self.authored = list(authored)
        self.ci = ci
        self.error = None
        self.ci_fail = set()
        self.ci_auth_fail = False
        self.ci_delay = 0.0
        self.in_flight = 0
        self.max_in_flight = 0
        self.lock = threading.Lock()

    def __call__(self, argv, timeout):
        if argv[:3] == ["gh", "pr", "view"]:
            with self.lock:
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                time.sleep(self.ci_delay)
                return self._answer(argv)
            finally:
                with self.lock:
                    self.in_flight -= 1
        return self._answer(argv)

    def _answer(self, argv):
        if self.error:
            return 1, "", self.error
        if argv[:3] == ["gh", "api", "graphql"]:
            q = next(a for a in argv if a.startswith("q="))
            nodes = self.authored if "author:@me" in q else []
            page = {
                "data": {
                    "search": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": nodes,
                    }
                }
            }
            return 0, json.dumps(page), ""
        if argv[:3] == ["gh", "pr", "view"]:
            if self.ci_auth_fail:
                return 1, "", "HTTP 401: Bad credentials"
            if any(a in self.ci_fail for a in argv):
                return 1, "", "HTTP 502: bad gateway"
            rollup = [
                {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": self.ci}
            ]
            return 0, json.dumps({"statusCheckRollup": rollup}), ""
        raise AssertionError(f"unexpected gh call: {argv}")


def pr_node(number, title, branch):
    return {
        "url": f"https://github.com/acme/app/pull/{number}",
        "title": title,
        "headRefName": branch,
        "isDraft": False,
        "reviewDecision": None,
        "updatedAt": "2026-10-22T10:00:00Z",
        "repository": {"nameWithOwner": "acme/app"},
    }


class SyncCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.conn = db.connect(self.tmp / "d.db")
        self.addCleanup(self.conn.close)
        self.cfg = make_config()

    def run_sync(self, mode, acli, now=NOW, gh=None, cfg=None):
        cfg = cfg or self.cfg
        jira = sync.JiraSource(cfg, runner=acli)
        github = sync.GithubSource(cfg, runner=gh) if gh else None
        return sync.run(
            mode, cfg, self.conn, now, jira=jira, github=github, clock=lambda: now
        )

    def keys(self, source="jira"):
        rows = self.conn.execute(
            "SELECT key FROM issues WHERE source = ? ORDER BY key", (source,)
        )
        return [r[0] for r in rows]

    def checksum(self, table):
        rows = self.conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3").fetchall()
        return hashlib.sha256(repr(rows).encode()).hexdigest()

    def seed_run(self, kind, started, outcome="ok", failed=()):
        self.conn.execute(
            "INSERT INTO sync_runs (kind, started_at, finished_at, outcome, failed_keys)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                kind,
                sync._ts(started),
                sync._ts(started),
                outcome,
                json.dumps(list(failed)),
            ),
        )
        self.conn.commit()


class ScopeTests(unittest.TestCase):
    def test_every_scope_is_parenthesized_with_done_window(self):
        scopes = sync.build_scopes(make_config(), [503, 504])
        self.assertEqual(len(scopes), 3)
        for s in scopes:
            self.assertTrue(s.startswith("("), s)
            self.assertTrue(
                s.endswith(") AND (statusCategory != Done OR resolved >= -14d)"), s
            )
        self.assertIn("(sprint in (503, 504)) AND", scopes[1])

    def test_or_sits_inside_the_parentheses(self):
        reported = [s for s in sync.build_scopes(make_config(), []) if "watcher" in s]
        self.assertEqual(len(reported), 1)
        head = reported[0].split(") AND (statusCategory")[0] + ")"
        self.assertEqual(head, "(reporter = currentUser() OR watcher = currentUser())")

    def test_no_sprint_scope_without_active_sprints(self):
        scopes = sync.build_scopes(make_config(), [])
        self.assertEqual(len(scopes), 2)
        self.assertFalse(any("sprint in" in s for s in scopes))

    def test_done_window_follows_config(self):
        cfg = make_config()
        cfg["sync"]["done_window_days"] = 3
        self.assertIn("resolved >= -3d", sync.build_scopes(cfg, [])[0])


class CursorTests(SyncCase):
    def test_seventeen_minutes_is_minus_22m(self):
        self.assertEqual(sync.cursor_minutes(NOW - timedelta(minutes=17), NOW), 22)
        self.seed_run("full", NOW - timedelta(minutes=17))
        acli = FakeAcli(["DEMO-1"])
        row = self.run_sync("incremental", acli)
        self.assertEqual(row["outcome"], "ok")
        self.assertEqual(row["kind"], "incremental")
        scope_jqls = [j for j in acli.jqls() if "currentUser" in j or "sprint" in j]
        self.assertEqual(len(scope_jqls), 3)
        for jql in scope_jqls:
            self.assertTrue(jql.startswith("(("), jql)
            self.assertTrue(jql.endswith(') AND updated >= "-22m"'), jql)

    def test_failed_run_does_not_advance_the_cursor(self):
        self.seed_run("full", NOW - timedelta(minutes=17))
        broken = FakeAcli(["DEMO-1"])
        broken.raise_on = "search"
        self.assertEqual(self.run_sync("incremental", broken)["outcome"], "failed")
        later = NOW + timedelta(minutes=1)
        acli = FakeAcli(["DEMO-1"])
        self.run_sync("incremental", acli, now=later)
        self.assertTrue(all('"-23m"' in j for j in acli.jqls()), acli.jqls())

    def test_reconcile_does_not_advance_the_cursor(self):
        self.seed_run("full", NOW - timedelta(minutes=17))
        self.assertEqual(self.run_sync("reconcile", FakeAcli([]))["outcome"], "ok")
        acli = FakeAcli([])
        self.run_sync("incremental", acli, now=NOW + timedelta(minutes=1))
        self.assertTrue(all('"-23m"' in j for j in acli.jqls()), acli.jqls())

    def test_incremental_without_a_successful_run_is_full(self):
        self.seed_run("full", NOW - timedelta(minutes=5), outcome="failed")
        acli = FakeAcli(["DEMO-1", "DEMO-2"])
        row = self.run_sync("incremental", acli)
        self.assertEqual(row["kind"], "full")
        self.assertFalse(any("updated >=" in j for j in acli.jqls()))
        self.assertEqual(self.keys(), ["DEMO-1", "DEMO-2"])

    def test_incremental_views_only_returned_keys(self):
        self.run_sync("full", FakeAcli(["DEMO-1", "DEMO-2"]))
        acli = FakeAcli(["DEMO-2"])
        self.run_sync("incremental", acli, now=NOW + timedelta(minutes=5))
        self.assertEqual(acli.viewed(), ["DEMO-2"])
        self.assertEqual(self.keys(), ["DEMO-1", "DEMO-2"])  # incremental never drops

    def test_incremental_retries_keys_that_failed_last_time(self):
        self.run_sync("full", FakeAcli(["DEMO-1", "DEMO-2"]))
        flaky = FakeAcli(["DEMO-2"])
        flaky.fail_view = {"DEMO-2"}
        row = self.run_sync("incremental", flaky, now=NOW + timedelta(minutes=5))
        self.assertEqual(json.loads(row["failed_keys"]), ["DEMO-2"])
        acli = FakeAcli([])
        self.run_sync("incremental", acli, now=NOW + timedelta(minutes=10))
        self.assertEqual(acli.viewed(), ["DEMO-2"])

    def test_incremental_retries_a_new_key_that_failed_last_time(self):
        self.run_sync("full", FakeAcli(["DEMO-1"]))
        flaky = FakeAcli(["DEMO-1", "DEMO-9"])
        flaky.fail_view = {"DEMO-9"}
        row = self.run_sync("incremental", flaky, now=NOW + timedelta(minutes=5))
        self.assertEqual(json.loads(row["failed_keys"]), ["DEMO-9"])
        self.assertEqual(self.keys(), ["DEMO-1"])
        acli = FakeAcli([])
        self.run_sync("incremental", acli, now=NOW + timedelta(minutes=10))
        self.assertEqual(acli.viewed(), ["DEMO-9"])
        self.assertEqual(self.keys(), ["DEMO-1", "DEMO-9"])

    def test_incremental_does_not_view_failed_pr_urls(self):
        url = "https://github.com/acme/app/pull/7"
        self.seed_run("full", NOW - timedelta(minutes=5), failed=["DEMO-2", url])
        acli = FakeAcli([])
        self.run_sync("incremental", acli)
        self.assertEqual(acli.viewed(), ["DEMO-2"])


class ReconcileTests(SyncCase):
    def test_adds_new_drops_old_keeps_overlay(self):
        old = view_payload("OLD-1", blocks=["OLD-9"])
        self.run_sync("full", FakeAcli(["OLD-1", "KEEP-1"], {"OLD-1": old}))
        self.assertEqual(self.keys(), ["KEEP-1", "OLD-1", "OLD-9"])
        self.conn.execute(
            "INSERT INTO notes (source, item_key, body) VALUES ('jira', 'OLD-1', 'mine')"
        )
        self.conn.execute(
            "INSERT INTO pins (source, item_key) VALUES ('jira', 'OLD-1')"
        )
        self.conn.commit()

        acli = FakeAcli(["KEEP-1", "NEW-1"])
        row = self.run_sync("reconcile", acli, now=NOW + timedelta(hours=1))
        self.assertEqual(row["outcome"], "ok")
        self.assertEqual(acli.viewed(), ["NEW-1"])  # only the key the mirror lacks
        self.assertEqual(self.keys(), ["KEEP-1", "NEW-1"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM links WHERE issue_key LIKE 'OLD-%'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT body FROM notes WHERE item_key = 'OLD-1'"
            ).fetchone(),
            ("mine",),
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM pins").fetchone()[0], 1
        )
        counts = json.loads(row["counts"])
        self.assertEqual(counts["dropped"], 2)

    def test_linked_keys_of_kept_issues_are_not_dropped(self):
        a = view_payload("A-1", blocks=["L-1"])
        self.run_sync("full", FakeAcli(["A-1"], {"A-1": a}))
        acli = FakeAcli(["A-1"])
        self.run_sync("reconcile", acli, now=NOW + timedelta(hours=1))
        self.assertEqual(self.keys(), ["A-1", "L-1"])
        self.assertEqual(acli.viewed(), [])

    def test_full_views_every_key_and_drops(self):
        self.run_sync("full", FakeAcli(["A-1", "B-1"]))
        acli = FakeAcli(["B-1"])
        self.run_sync("full", acli, now=NOW + timedelta(hours=1))
        self.assertEqual(acli.viewed(), ["B-1"])
        self.assertEqual(self.keys(), ["B-1"])


class LinkedPassTests(SyncCase):
    def test_blocks_links_one_hop_only(self):
        a = view_payload("A-1", blocks=["B-1", "A-2"], relates=["R-1"])
        b = view_payload("B-1", blocks=["C-1"])
        acli = FakeAcli(["A-1", "A-2"], {"A-1": a, "B-1": b})
        row = self.run_sync("full", acli)
        self.assertEqual(sorted(acli.viewed()), ["A-1", "A-2", "B-1"])
        self.assertEqual(self.keys(), ["A-1", "A-2", "B-1"])
        self.assertEqual(json.loads(row["counts"])["linked"], 1)
        links = self.conn.execute(
            "SELECT linked_key FROM links WHERE issue_key = 'A-1' ORDER BY 1"
        ).fetchall()
        self.assertEqual([r[0] for r in links], ["A-2", "B-1", "R-1"])

    def test_incremental_refreshes_known_linked_keys_by_cursor(self):
        a = view_payload("A-1", blocks=["B-1"])
        self.run_sync("full", FakeAcli(["A-1"], {"A-1": a}))
        acli = FakeAcli([])
        acli.changed = {"B-1"}
        self.run_sync("incremental", acli, now=NOW + timedelta(minutes=10))
        key_jqls = [j for j in acli.jqls() if j.startswith("(key in")]
        self.assertEqual(key_jqls, ['(key in (B-1)) AND updated >= "-15m"'])
        self.assertEqual(acli.viewed(), ["B-1"])

    def test_incremental_does_not_walk_a_linked_issues_links(self):
        a = view_payload("A-1", blocks=["B-1"])
        b = view_payload("B-1", blocks=["C-1"])
        self.run_sync("full", FakeAcli(["A-1"], {"A-1": a, "B-1": b}))
        scopes = dict(self.conn.execute("SELECT key, scope FROM issues"))
        self.assertEqual(scopes, {"A-1": "search", "B-1": "linked"})
        acli = FakeAcli([], {"B-1": b})
        acli.changed = {"B-1"}
        self.run_sync("incremental", acli, now=NOW + timedelta(minutes=10))
        self.assertEqual(acli.viewed(), ["B-1"])
        self.assertEqual(self.keys(), ["A-1", "B-1"])

    def test_fake_fixture_issue_links(self):
        raw = json.loads((FIXTURES / "view-links-sprints-flagged.json").read_text())
        acli = FakeAcli(["DEMO-101"], {"DEMO-101": raw})
        self.run_sync("full", acli)
        self.assertEqual(self.keys(), ["DEMO-101", "DEMO-120", "DEMO-90"])
        row = self.conn.execute(
            "SELECT sprint_id, flagged FROM issues WHERE key = 'DEMO-101'"
        ).fetchone()
        self.assertEqual(row, (503, 1))
        self.assertEqual(
            self.conn.execute("SELECT id, board_id FROM sprints").fetchall(),
            [(503, 77)],
        )


class FailureTests(SyncCase):
    def populate(self):
        self.run_sync("full", FakeAcli(["A-1", "B-1"]))
        return self.checksum("issues"), self.checksum("links")

    def test_adapter_raising_mid_run_leaves_issues_identical(self):
        before = self.populate()
        cfg = make_config(enabled=True)
        gh = FakeGh(authored=[pr_node(1, "fix [A-1]", "a-1")])
        gh.error = "HTTP 502: bad gateway"
        acli = FakeAcli(["A-1", "B-1", "C-1"])
        row = self.run_sync("full", acli, now=NOW + timedelta(hours=1), gh=gh, cfg=cfg)
        self.assertEqual(row["outcome"], "failed")
        self.assertIn("502", row["error"])
        self.assertIn("C-1", acli.viewed())  # it got past the Jira fetch
        self.assertEqual((self.checksum("issues"), self.checksum("links")), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM prs").fetchone()[0], 0)

    def test_failed_search_is_recorded(self):
        before = self.populate()
        acli = FakeAcli(["A-1"])
        acli.raise_on = "search"
        row = self.run_sync("reconcile", acli, now=NOW + timedelta(hours=1))
        self.assertEqual(
            (row["outcome"], row["ok"], row["kind"]), ("failed", 0, "reconcile")
        )
        self.assertEqual((self.checksum("issues"), self.checksum("links")), before)
        self.assertIsNotNone(row["finished_at"])

    def test_auth_error_is_auth_failed(self):
        before = self.populate()
        acli = FakeAcli(["A-1"])
        acli.auth = True
        row = self.run_sync("full", acli, now=NOW + timedelta(hours=1))
        self.assertEqual(row["outcome"], "auth_failed")
        self.assertIn("acli jira auth login", row["error"])
        self.assertEqual(self.checksum("issues"), before[0])

    def test_gh_auth_error_is_auth_failed(self):
        gh = FakeGh()
        gh.error = "To get started with GitHub CLI, please run:  gh auth login"
        row = self.run_sync(
            "full", FakeAcli(["A-1"]), gh=gh, cfg=make_config(enabled=True)
        )
        self.assertEqual(row["outcome"], "auth_failed")
        self.assertEqual(self.keys(), [])

    def test_key_failing_twice_is_logged_not_fatal(self):
        acli = FakeAcli(["A-1", "B-1"])
        acli.fail_view = {"B-1"}
        row = self.run_sync("full", acli)
        self.assertEqual(row["outcome"], "ok")
        self.assertEqual(json.loads(row["failed_keys"]), ["B-1"])
        self.assertEqual(acli.viewed().count("B-1"), 2)
        self.assertEqual(self.keys(), ["A-1"])

    def test_interrupted_run_is_recorded_and_reraised(self):
        before = self.populate()
        acli = FakeAcli(["A-1", "C-1"])

        def interrupt(argv, timeout):
            if argv[2:4] == ["workitem", "view"]:
                raise KeyboardInterrupt
            return acli(argv, timeout)

        with self.assertRaises(KeyboardInterrupt):
            self.run_sync("full", interrupt, now=NOW + timedelta(hours=1))
        row = self.conn.execute(
            "SELECT outcome, ok, error, finished_at FROM sync_runs ORDER BY id DESC"
        ).fetchone()
        self.assertEqual(row[:3], ("failed", 0, "interrupted (KeyboardInterrupt)"))
        self.assertIsNotNone(row[3])
        self.assertEqual(self.checksum("issues"), before[0])
        self.assertFalse(self.conn.in_transaction)

    def test_write_failure_rolls_back(self):
        before = self.populate()
        saved = sync._UPSERT_ISSUE
        self.addCleanup(setattr, sync, "_UPSERT_ISSUE", saved)
        # A column that does not exist fails the first upsert, inside the write.
        sync._UPSERT_ISSUE = saved.replace("summary", "no_such_column", 1)
        row = self.run_sync(
            "full", FakeAcli(["A-1", "Z-1"]), now=NOW + timedelta(hours=1)
        )
        self.assertEqual(row["outcome"], "failed")
        self.assertIn("no_such_column", row["error"])
        self.assertEqual(self.checksum("issues"), before[0])
        self.assertFalse(self.conn.in_transaction)


class GithubTests(SyncCase):
    def test_prs_joined_to_jira_projects_only(self):
        self.conn.execute(
            "INSERT INTO issues (source, kind, key, summary) VALUES"
            " ('claude-remote', 'session', 'SESS-5', 'a session')"
        )
        self.conn.commit()
        gh = FakeGh(
            authored=[
                pr_node(1, "feat: thing [DEMO-1]", "bestdan/demo-1-thing"),
                pr_node(2, "chore: unrelated", "bestdan/sess-5"),
                pr_node(3, "fix: branch only", "demo-2-fix"),
            ]
        )
        row = self.run_sync(
            "full", FakeAcli(["DEMO-1", "DEMO-2"]), gh=gh, cfg=make_config(enabled=True)
        )
        self.assertEqual(row["outcome"], "ok")
        got = dict(self.conn.execute("SELECT number, issue_key FROM prs").fetchall())
        self.assertEqual(got, {1: "DEMO-1", 2: None, 3: "DEMO-2"})
        ci = {r[0] for r in self.conn.execute("SELECT ci_state FROM prs")}
        self.assertEqual(ci, {"success"})
        self.assertEqual(json.loads(row["counts"])["prs"], 3)

    def test_ci_lookup_failure_is_logged_not_fatal(self):
        gh = FakeGh(authored=[pr_node(1, "feat [DEMO-1]", "a"), pr_node(2, "b", "b")])
        bad = "https://github.com/acme/app/pull/2"
        gh.ci_fail = {bad}
        row = self.run_sync(
            "full", FakeAcli(["DEMO-1"]), gh=gh, cfg=make_config(enabled=True)
        )
        self.assertEqual(row["outcome"], "ok")
        ci = dict(self.conn.execute("SELECT url, ci_state FROM prs").fetchall())
        self.assertEqual(
            ci, {"https://github.com/acme/app/pull/1": "success", bad: None}
        )
        self.assertEqual(json.loads(row["failed_keys"]), [bad])
        self.assertEqual(json.loads(row["counts"])["ci_failed"], 1)

    def test_ci_lookups_run_concurrently(self):
        gh = FakeGh(authored=[pr_node(n, f"pr {n}", f"b{n}") for n in range(1, 9)])
        gh.ci_delay = 0.05
        cfg = make_config(enabled=True)
        cfg["sync"]["view_concurrency"] = 3
        row = self.run_sync("full", FakeAcli(["DEMO-1"]), gh=gh, cfg=cfg)
        self.assertEqual(row["outcome"], "ok")
        self.assertGreater(gh.max_in_flight, 1)
        self.assertLessEqual(gh.max_in_flight, 3)
        ci = {r[0] for r in self.conn.execute("SELECT ci_state FROM prs")}
        self.assertEqual(ci, {"success"})

    def test_ci_auth_error_fails_the_run(self):
        gh = FakeGh(authored=[pr_node(1, "a", "a"), pr_node(2, "b", "b")])
        gh.ci_auth_fail = True
        row = self.run_sync(
            "full", FakeAcli(["DEMO-1"]), gh=gh, cfg=make_config(enabled=True)
        )
        self.assertEqual(row["outcome"], "auth_failed")

    def test_disabled_github_leaves_prs_alone(self):
        self.conn.execute(
            "INSERT INTO prs (url, title) VALUES ('https://x/pull/1', 't')"
        )
        self.conn.commit()
        self.run_sync("full", FakeAcli(["A-1"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM prs").fetchone()[0], 1)


HEAD = "a" * 40


def fleet_pr(number, milestone=None):
    """A review-search node; with `milestone`, it closes an issue in that one."""
    refs = []
    if milestone is not None:
        refs.append(
            {
                "number": 900 + number,
                "repository": {"nameWithOwner": "acme/app"},
                "milestone": {"number": milestone},
            }
        )
    return {
        "url": f"https://github.com/acme/app/pull/{number}",
        "number": number,
        "title": f"feat: fleet {number}",
        "body": "",
        "headRefName": f"bestdan/f-{number}",
        "headRefOid": HEAD,
        "isDraft": False,
        "reviewDecision": None,
        "createdAt": "2026-10-20T10:00:00Z",
        "updatedAt": "2026-10-22T10:00:00Z",
        "repository": {"nameWithOwner": "acme/app"},
        "closingIssuesReferences": {"nodes": refs},
    }


class FleetGh:
    """A runner answering github_fleet's gh calls; records every argv.

    PR 1 passes all four review-queue rules (green CI, an approved milestone,
    the viewer's stamp at its head); PR 2 closes no issue, so it only awaits.
    `error` answers every call with that stderr.
    """

    def __init__(self):
        self.calls = []
        self.error = None
        self.ci_fail = set()

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if self.error:
            return 1, "", self.error
        q = next((a[2:] for a in argv if a.startswith("q=")), None)
        if q is not None:
            if "is:closed" in q:
                nodes = []
            elif "author:@me" in q:
                nodes = [fleet_pr(1, milestone=7), fleet_pr(2)]
            elif "is:issue" in q:
                nodes = [
                    {
                        "url": "https://github.com/acme/app/issues/5",
                        "number": 5,
                        "title": "needs a human",
                        "repository": {"nameWithOwner": "acme/app"},
                        "labels": {"nodes": [{"name": "auto:human-review-needed"}]},
                    }
                ]
            else:
                nodes = []
            page = {"pageInfo": {"hasNextPage": False, "endCursor": None}}
            return 0, json.dumps({"data": {"search": {**page, "nodes": nodes}}}), ""
        if argv[:3] == ["gh", "pr", "view"]:
            if any(a in self.ci_fail for a in argv):
                return 1, "", "HTTP 502: bad gateway"
            rollup = [
                {
                    "__typename": "CheckRun",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                }
            ]
            return 0, json.dumps({"statusCheckRollup": rollup}), ""
        if len(argv) > 2 and "/milestones/" in argv[2]:
            return 0, json.dumps({"description": "approved: yes"}), ""
        if any(a.startswith("owner=") for a in argv):
            stamp = f"<!-- co-review-stamp v1 head={HEAD} -->"
            comments = [
                {
                    "body": stamp,
                    "createdAt": "2026-10-23T00:00:00Z",
                    "author": {"login": "me"},
                }
            ]
            data = {
                "viewer": {"login": "me"},
                "repository": {"pullRequest": {"comments": {"nodes": comments}}},
            }
            return 0, json.dumps({"data": data}), ""
        raise AssertionError(f"unexpected gh call: {argv}")


class FleetTests(SyncCase):
    def run_all(self, cfg, gh, acli=None):
        acli = acli or FakeAcli(["DEMO-1"])
        # `github.enabled` is on, so the Jira run's PR step needs a fake too;
        # left to default it calls the real gh, which CI has not logged in.
        return sync.run_all(
            "full", cfg, self.conn, NOW,
            jira=sync.JiraSource(cfg, runner=acli),
            github=sync.GithubSource(cfg, runner=FakeGh()), fleet_runner=gh,
            clock=lambda: NOW,
        )  # fmt: skip

    def github_rows(self):
        return dict(
            self.conn.execute(
                "SELECT key, status_name FROM issues WHERE source = 'github'"
            ).fetchall()
        )

    def test_gate(self):
        self.assertFalse(sync.fleet_enabled(make_config()))
        self.assertFalse(sync.fleet_enabled(make_config(enabled=False, fleet=True)))
        self.assertFalse(sync.fleet_enabled(make_config(enabled=True)))
        self.assertTrue(sync.fleet_enabled(make_config(enabled=True, fleet=True)))

    def test_gate_off_makes_no_fleet_calls(self):
        gh = FleetGh()
        # `enabled` on with `fleet` at its default: the work-laptop shape.
        cfg = make_config(enabled=True)
        jira = sync.JiraSource(cfg, runner=FakeAcli(["DEMO-1"]))
        rows = sync.run_all(
            "full", cfg, self.conn, NOW, jira=jira, github=sync.GithubSource(cfg, FakeGh()),
            fleet_runner=gh, clock=lambda: NOW,
        )  # fmt: skip
        self.assertEqual(gh.calls, [])
        self.assertEqual([r["kind"] for r in rows], ["full"])
        self.assertEqual(self.github_rows(), {})

    def test_gate_on_writes_github_rows_and_records_a_run(self):
        rows = self.run_all(make_config(enabled=True, fleet=True), FleetGh())
        self.assertEqual(
            [(r["kind"], r["outcome"]) for r in rows],
            [("full", "ok"), ("github_fleet", "ok")],
        )
        self.assertEqual(
            self.github_rows(),
            {
                "https://github.com/acme/app/pull/1": "queue",
                "https://github.com/acme/app/pull/2": "awaiting",
                "https://github.com/acme/app/issues/5": None,
            },
        )
        counts = json.loads(rows[1]["counts"])
        self.assertEqual(
            (counts["queue"], counts["awaiting"], counts["rows"]), (1, 2, 3)
        )
        self.assertEqual(self.keys(), ["DEMO-1"])

    def test_failure_leaves_jira_rows_and_outcome(self):
        gh = FleetGh()
        gh.error = "HTTP 502: bad gateway"
        rows = self.run_all(make_config(enabled=True, fleet=True), gh)
        self.assertEqual([r["outcome"] for r in rows], ["ok", "failed"])
        self.assertIn("502", rows[1]["error"])
        self.assertEqual(self.keys(), ["DEMO-1"])

    def test_failure_keeps_the_last_good_github_rows(self):
        cfg = make_config(enabled=True, fleet=True)
        self.run_all(cfg, FleetGh())
        before = self.github_rows()
        gh = FleetGh()
        gh.error = "HTTP 502: bad gateway"
        self.run_all(cfg, gh)
        self.assertEqual(self.github_rows(), before)

    def test_auth_failure_carries_the_gh_hint(self):
        gh = FleetGh()
        gh.error = "To get started with GitHub CLI, please run:  gh auth login"
        rows = self.run_all(make_config(enabled=True, fleet=True), gh)
        self.assertEqual([r["outcome"] for r in rows], ["ok", "auth_failed"])
        self.assertIn("gh auth login", rows[1]["error"])
        self.assertEqual(self.keys(), ["DEMO-1"])

    def test_lookup_failure_is_listed_not_fatal(self):
        gh = FleetGh()
        bad = "https://github.com/acme/app/pull/1"
        gh.ci_fail = {bad}
        rows = self.run_all(make_config(enabled=True, fleet=True), gh)
        self.assertEqual(rows[1]["outcome"], "ok")
        self.assertEqual(json.loads(rows[1]["failed_keys"]), [bad])
        self.assertEqual(self.github_rows()[bad], "awaiting")

    def test_jira_failure_still_runs_the_fleet_step(self):
        acli = FakeAcli(["DEMO-1"])
        acli.auth = True
        rows = self.run_all(make_config(enabled=True, fleet=True), FleetGh(), acli)
        self.assertEqual([r["outcome"] for r in rows], ["auth_failed", "ok"])
        self.assertEqual(len(self.github_rows()), 3)

    def test_fleet_run_is_not_a_jira_cursor(self):
        # The fleet run starts after the Jira run, as it does in production, so
        # a cursor query that ignored kind would pick the fleet row.
        later = NOW + timedelta(minutes=1)
        ticks = iter([NOW, later, later])  # Jira finish, fleet start, fleet finish
        cfg = make_config(enabled=True, fleet=True)
        sync.run_all(
            "full", cfg, self.conn, NOW,
            jira=sync.JiraSource(cfg, runner=FakeAcli(["DEMO-1"])),
            github=sync.GithubSource(cfg, runner=FakeGh()),
            fleet_runner=FleetGh(), clock=lambda: next(ticks),
        )  # fmt: skip
        runs = self.conn.execute("SELECT kind, started_at FROM sync_runs").fetchall()
        self.assertEqual(
            runs, [("full", sync._ts(NOW)), ("github_fleet", sync._ts(later))]
        )
        self.assertEqual(sync.last_cursor_run(self.conn)[0], sync._ts(NOW))


class GenericItemTests(SyncCase):
    def test_session_row_beside_jira_rows_is_ignored(self):
        self.run_sync("full", FakeAcli(["DEMO-1"]))
        self.conn.execute(
            "INSERT INTO issues (source, kind, key, summary) VALUES"
            " ('claude-remote', 'session', 'DEMO-1', 'same key, other source')"
        )
        self.conn.execute(
            "INSERT INTO notes (source, item_key, body) VALUES"
            " ('claude-remote', 'DEMO-1', 'session note')"
        )
        self.conn.commit()
        for mode, acli in (
            ("reconcile", FakeAcli([])),
            ("full", FakeAcli(["DEMO-1"])),
            ("incremental", FakeAcli(["DEMO-1"])),
        ):
            self.run_sync(mode, acli, now=NOW + timedelta(hours=1))
        self.assertEqual(self.keys("claude-remote"), ["DEMO-1"])
        row = self.conn.execute(
            "SELECT kind, summary FROM issues WHERE source = 'claude-remote'"
        ).fetchone()
        self.assertEqual(row, ("session", "same key, other source"))
        # The view Jira-only readers (the Board) select from has no session row.
        kinds = {r[0] for r in self.conn.execute("SELECT kind FROM jira_issues")}
        self.assertEqual(kinds, {"ticket"})
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1
        )

    def test_jira_rows_carry_source_and_kind(self):
        self.run_sync("full", FakeAcli(["DEMO-1"]))
        self.assertEqual(
            self.conn.execute("SELECT source, kind FROM issues").fetchall(),
            [("jira", "ticket")],
        )


class FakeAcliTests(unittest.TestCase):
    """dashboard.py sync end to end, against dev/fake-acli."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        cfg = self.tmp / "config.toml"
        cfg.write_text(
            "[jira]\n"
            'site = "example.invalid"\n'
            "board_ids = [77]\n"
            f'acli_path = "{FAKE_ACLI}"\n'
            "[jira.fields]\n"
            'sprint = "customfield_10303"\n'
            'flagged = "customfield_10007"\n'
            "[github]\n"
            "enabled = false\n"
        )
        self.cfg = cfg
        self.db = self.tmp / "d.db"

    def sync(self, mode, **env):
        full_env = {**os.environ, "FAKE_ACLI_STATE": str(self.tmp / "state"), **env}
        full_env.pop("FAKE_ACLI_MODE", None)
        full_env.update(env)
        return subprocess.run(
            [sys.executable, str(PKG / "dashboard.py"), "sync", "--mode", mode,
             "--db", str(self.db), "--config", str(self.cfg)],
            capture_output=True, text=True, env=full_env, timeout=120,
        )  # fmt: skip

    def query(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_full_fills_issues_links_sprints(self):
        r = self.sync("full")
        self.assertEqual(r.returncode, 0, r.stderr)
        keys = [k for (k,) in self.query("SELECT key FROM issues ORDER BY key")]
        self.assertEqual(
            keys, ["DEMO-101", "DEMO-102", "DEMO-120", "DEMO-90", "OTHR-7"]
        )
        self.assertEqual(self.query("SELECT COUNT(*) FROM links")[0][0], 3)
        self.assertEqual(self.query("SELECT id FROM sprints"), [(503,)])
        self.assertEqual(
            self.query("SELECT kind, outcome FROM sync_runs"), [("full", "ok")]
        )

    def test_auth_fail_records_auth_failed(self):
        r = self.sync("full", FAKE_ACLI_MODE="auth_fail")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("acli jira auth login", r.stderr)
        self.assertEqual(
            self.query("SELECT outcome FROM sync_runs"), [("auth_failed",)]
        )
        self.assertEqual(self.query("SELECT COUNT(*) FROM issues")[0][0], 0)

    def test_flaky_views_are_absorbed_by_the_retry(self):
        r = self.sync("full", FAKE_ACLI_MODE="flaky")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.query("SELECT outcome, failed_keys FROM sync_runs"), [("ok", "[]")]
        )
        self.assertEqual(self.query("SELECT COUNT(*) FROM issues")[0][0], 5)

    def test_incremental_after_full(self):
        self.assertEqual(self.sync("full").returncode, 0)
        r = self.sync("incremental")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.query("SELECT kind, outcome FROM sync_runs ORDER BY id"),
            [("full", "ok"), ("incremental", "ok")],
        )

    def test_failed_run_exits_1(self):
        missing = self.tmp / "no-such-acli"
        self.cfg.write_text(self.cfg.read_text().replace(str(FAKE_ACLI), str(missing)))
        r = self.sync("full")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("acli not found", r.stderr)
        self.assertEqual(self.query("SELECT outcome FROM sync_runs"), [("failed",)])

    def test_bad_config_exits_2(self):
        self.cfg.write_text(
            self.cfg.read_text().replace('site = "example.invalid"', 'site = ""')
        )
        r = self.sync("full")
        self.assertEqual(r.returncode, 2)
        self.assertIn("jira.site", r.stderr)


def run_row(kind, outcome, error=None, failed=()):
    return {
        "kind": kind,
        "outcome": outcome,
        "started_at": "2026-10-23T12:00:00Z",
        "finished_at": "2026-10-23T12:00:01Z",
        "counts": "{}",
        "failed_keys": json.dumps(list(failed)),
        "error": error,
    }


class CmdSyncTests(unittest.TestCase):
    """dashboard.py sync's report and exit code over run_all's rows."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        tmp = Path(self._td.name)
        self.cfg = tmp / "config.toml"
        self.cfg.write_text(
            '[jira]\nsite = "example.invalid"\nboard_ids = [77]\n'
            '[jira.fields]\nsprint = "c1"\nflagged = "c2"\n'
            "[github]\nenabled = true\nfleet = true\n"
        )
        self.db = tmp / "d.db"

    def cmd_sync(self, rows, *extra):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sync, "run_all", return_value=rows):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = dashboard.main(
                    ["sync", "--db", str(self.db), "--config", str(self.cfg), *extra]
                )
        return code, out.getvalue(), err.getvalue()

    def test_both_rows_print_when_both_runs_fail(self):
        url = "https://github.com/acme/app/pull/1"
        code, out, err = self.cmd_sync(
            [
                run_row("incremental", "failed", "AcliError: boom"),
                run_row(
                    "github_fleet",
                    "auth_failed",
                    "gh: 401 (fix: run `gh auth login`)",
                    [url],
                ),
            ]
        )
        self.assertEqual(code, 1)  # the Jira run's code wins
        self.assertIn("sync incremental: failed", out)
        self.assertIn("sync github_fleet: auth_failed", out)
        self.assertIn("AcliError: boom", err)
        self.assertIn("gh auth login", err)
        self.assertIn(f"PR lookup failed, logged: {url}", err)

    def test_fleet_failure_alone_sets_the_exit_code(self):
        code, out, _ = self.cmd_sync(
            [run_row("incremental", "ok"), run_row("github_fleet", "auth_failed", "x")]
        )
        self.assertEqual(code, 3)
        self.assertIn("sync github_fleet: auth_failed", out)

    def test_fleet_warns_leaves_the_exit_code_to_jira(self):
        # install's warm-up sync passes --fleet-warns: a failed fleet step is a
        # warning there, not a reason to undo the install.
        code, out, err = self.cmd_sync(
            [run_row("full", "ok"), run_row("github_fleet", "auth_failed", "x")],
            "--fleet-warns",
        )
        self.assertEqual(code, 0)
        self.assertIn("sync github_fleet: auth_failed", out)
        self.assertIn("WARNING: the github.fleet step ended auth_failed", err)

    def test_fleet_warns_still_fails_on_a_failed_jira_run(self):
        code, _, _ = self.cmd_sync(
            [run_row("full", "failed", "boom"), run_row("github_fleet", "ok")],
            "--fleet-warns",
        )
        self.assertEqual(code, 1)

    def test_jira_row_alone(self):
        code, out, _ = self.cmd_sync([run_row("incremental", "ok")])
        self.assertEqual(code, 0)
        self.assertNotIn("github_fleet", out)


class ConfigTests(unittest.TestCase):
    def load(self, user):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.toml"
            lines = []
            for table, values in user.items():
                lines.append(f"[{table}]")
                lines += [f"{k} = {json.dumps(v)}" for k, v in values.items()]
            p.write_text("\n".join(lines) + "\n")
            return config.load(p)

    def base(self):
        return {
            "jira": {"site": "s", "board_ids": [1]},
            "jira.fields": {"sprint": "c1", "flagged": "c2"},
        }

    def test_empty_board_ids_is_valid(self):
        u = self.base()
        u["jira"]["board_ids"] = []
        self.assertEqual(self.load(u)["jira"]["board_ids"], [])

    def test_github_fleet_defaults_off_and_must_be_a_bool(self):
        self.assertIs(self.load(self.base())["github"]["fleet"], False)
        u = self.base()
        u["github"] = {"fleet": "yes"}
        with self.assertRaises(config.ConfigError) as cm:
            self.load(u)
        self.assertIn("github.fleet", str(cm.exception))

    def test_empty_site_is_an_error(self):
        u = self.base()
        u["jira"]["site"] = ""
        with self.assertRaises(config.ConfigError) as cm:
            self.load(u)
        self.assertIn("jira.site", str(cm.exception))

    def test_empty_field_id_is_an_error(self):
        u = self.base()
        u["jira.fields"]["flagged"] = " "
        with self.assertRaises(config.ConfigError):
            self.load(u)

    def test_non_integer_board_id_is_an_error(self):
        u = self.base()
        u["jira"]["board_ids"] = ["77"]
        with self.assertRaises(config.ConfigError):
            self.load(u)


class ModuleTests(unittest.TestCase):
    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            sync.run("sideways", make_config(), None, NOW)

    def test_view_fields_include_custom_fields(self):
        src = sync.JiraSource(make_config())
        self.assertEqual(
            src.fields[: len(jira_acli.VIEW_FIELDS)], list(jira_acli.VIEW_FIELDS)
        )
        self.assertEqual(src.fields[-2:], ["customfield_10303", "customfield_10007"])

    def test_acli_path_is_used(self):
        cfg = copy.deepcopy(make_config())
        cfg["jira"]["acli_path"] = "/opt/fake/acli"
        acli = FakeAcli([])
        sync.JiraSource(cfg, runner=acli).search("x")
        self.assertEqual(acli.calls[0][0], "/opt/fake/acli")


if __name__ == "__main__":
    unittest.main()
