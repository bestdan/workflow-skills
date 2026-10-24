#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/jira_acli.py.

No acli is ever run: every call goes through a fake runner that replays the
fixtures in scripts/task-dashboard/fixtures/acli/. The fixture-driven tests
glob that folder by filename prefix, so redacted real captures added beside the
synthetic ones are covered without a code change.
Needs Python 3.11: run via scripts/test-task-dashboard-acli.sh.
"""

import importlib.util
import json
import re
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
FIXTURES = PKG / "fixtures" / "acli"

spec = importlib.util.spec_from_file_location("td_jira_acli", PKG / "jira_acli.py")
assert spec is not None and spec.loader is not None
acli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = acli  # dataclasses resolves annotations through it
spec.loader.exec_module(acli)

FIELD_IDS = {"sprint": "customfield_10303", "flagged": "customfield_10007"}
TRANSIENT = (FIXTURES / "stderr-transient-fetch.txt").read_text()


def fixtures(prefix):
    found = sorted(
        [*FIXTURES.glob(prefix + "*"), *FIXTURES.glob("real-" + prefix + "*")]
    )
    assert found, f"no fixture matches {prefix}*"
    return found


def load(name):
    return json.loads((FIXTURES / name).read_text())


def replay(stdout, rc=0, stderr=""):
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return rc, stdout, stderr

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


class FlakyRunner:
    """Fails each key in `fail_once` on its first view, and `fail_always` forever."""

    def __init__(self, fail_once=(), fail_always=(), stderr=TRANSIENT):
        self.fail_once = set(fail_once)
        self.fail_always = set(fail_always)
        self.stderr = stderr
        self.seen = {}
        self.lock = threading.Lock()

    def __call__(self, argv, timeout):
        key = argv[argv.index("view") + 1]
        with self.lock:
            self.seen[key] = self.seen.get(key, 0) + 1
            first = self.seen[key] == 1
        if key in self.fail_always or (key in self.fail_once and first):
            return 1, "", self.stderr
        return 0, json.dumps({"key": key, "fields": {"summary": key}}), ""


class RunnerSeamTests(unittest.TestCase):
    def test_captures_stdout_and_stderr(self):
        rc, out, err = acli.run_acli(
            [
                sys.executable,
                "-c",
                "import sys; print('o'); print('e', file=sys.stderr)",
            ]
        )
        self.assertEqual((rc, out.strip(), err.strip()), (0, "o", "e"))

    def test_missing_binary_is_data_not_exception(self):
        rc, _, err = acli.run_acli(["/nonexistent/acli-binary"])
        self.assertEqual(rc, 127)
        self.assertIn("not found", err)

    def test_timeout_is_data_not_exception(self):
        rc, _, err = acli.run_acli(
            [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2
        )
        self.assertEqual(rc, 124)
        self.assertIn("timed out", err)


class SearchTests(unittest.TestCase):
    def test_every_search_fixture_yields_keys(self):
        for path in fixtures("search-"):
            with self.subTest(path.name):
                keys = acli.search_keys("x", runner=replay(path.read_text()))
                self.assertTrue(keys)
                self.assertTrue(all(isinstance(k, str) and "-" in k for k in keys))
                self.assertEqual(len(keys), len(set(keys)))

    def test_argv_and_order(self):
        runner = replay((FIXTURES / "search-page.json").read_text())
        keys = acli.search_keys(
            "assignee = currentUser()", acli="/x/acli", runner=runner
        )
        self.assertEqual(keys, ["DEMO-101", "DEMO-102", "OTHR-7"])
        argv = runner.calls[0]
        self.assertEqual(argv[:4], ["/x/acli", "jira", "workitem", "search"])
        self.assertEqual(argv[argv.index("--jql") + 1], "assignee = currentUser()")
        self.assertIn("--paginate", argv)
        self.assertIn("--json", argv)
        self.assertEqual(argv[argv.index("--fields") + 1], "status")

    def test_wrapped_object_is_accepted(self):
        body = json.dumps({"issues": [{"key": "A-1"}, {"key": "A-1"}, {"key": "A-2"}]})
        self.assertEqual(acli.search_keys("x", runner=replay(body)), ["A-1", "A-2"])

    def test_failure_raises(self):
        with self.assertRaises(acli.AcliError):
            acli.search_keys("x", runner=replay("", rc=1, stderr="boom"))
        with self.assertRaises(acli.AcliError):
            acli.search_keys("x", runner=replay("Number of work items: 3"))


class ViewTests(unittest.TestCase):
    KEYS = [f"DEMO-{i}" for i in range(1, 21)]

    def test_transient_failures_are_retried(self):
        runner = FlakyRunner(fail_once={"DEMO-3", "DEMO-17"})
        res = acli.view_issues(self.KEYS, acli.VIEW_FIELDS, 8, runner=runner)
        self.assertEqual(list(res.issues), self.KEYS)
        self.assertEqual(res.failed, [])
        self.assertEqual(runner.seen["DEMO-3"], 2)
        self.assertEqual(runner.seen["DEMO-1"], 1)

    def test_persistent_failure_is_returned_not_raised(self):
        runner = FlakyRunner(fail_always={"DEMO-9"})
        res = acli.view_issues(self.KEYS, acli.VIEW_FIELDS, 8, runner=runner)
        self.assertEqual(len(res.issues), 19)
        self.assertNotIn("DEMO-9", res.issues)
        self.assertEqual(res.failed, ["DEMO-9"])
        self.assertIn("failed to fetch work item details", res.errors["DEMO-9"])
        self.assertEqual(runner.seen["DEMO-9"], 2)  # retried once, no more

    def test_auth_error_raises(self):
        runner = FlakyRunner(fail_always=set(self.KEYS), stderr="401 Unauthorized")
        with self.assertRaises(acli.AuthError):
            acli.view_issues(self.KEYS, acli.VIEW_FIELDS, 4, runner=runner)

    def test_view_argv(self):
        runner = replay((FIXTURES / "view-all-null.json").read_text())
        acli.view_issues(["DEMO-102"], ["summary", "status"], 1, runner=runner)
        self.assertEqual(
            runner.calls[0],
            ["acli", "jira", "workitem", "view", "DEMO-102",
             "--fields", "summary,status", "--json"],
        )  # fmt: skip

    def test_every_view_fixture_round_trips(self):
        for path in fixtures("view-"):
            with self.subTest(path.name):
                raw = json.loads(path.read_text())
                res = acli.view_issues([raw["key"]], acli.VIEW_FIELDS, 1,
                                       runner=replay(path.read_text()))  # fmt: skip
                self.assertEqual(res.failed, [])
                parsed = acli.parse_issue(res.issues[raw["key"]], FIELD_IDS)
                self.assertEqual(parsed["issue"]["key"], raw["key"])
                self.assertIsInstance(parsed["issue"]["flagged"], bool)
                for link in parsed["links"]:
                    self.assertIn(link["direction"], ("inward", "outward"))


class ParseTests(unittest.TestCase):
    def test_links_sprint_flag_parent(self):
        out = acli.parse_issue(load("view-links-sprints-flagged.json"), FIELD_IDS)
        issue = out["issue"]
        self.assertEqual(issue["sprint_id"], 503)
        self.assertEqual(issue["sprint_name"], "DEMO Sprint 42")
        self.assertEqual(issue["sprint_state"], "active")
        self.assertEqual(issue["sprint_end"], "2026-10-26T13:00:00Z")
        self.assertIs(issue["flagged"], True)
        self.assertEqual(issue["parent_key"], "DEMO-50")
        self.assertEqual(issue["status_category"], "indeterminate")
        self.assertEqual(issue["status_category_changed"], "2026-10-19T13:30:00Z")
        self.assertEqual(issue["updated"], "2026-10-22T20:54:43Z")
        self.assertEqual(issue["due_date"], "2026-11-06")
        self.assertEqual(json.loads(issue["labels"]), ["backend", "queue"])
        self.assertEqual(issue["assignee"], "Pat Example")
        self.assertEqual(
            sorted(out["links"], key=lambda r: r["linked_key"]),
            [
                {"issue_key": "DEMO-101", "linked_key": "DEMO-120", "link_type": "Blocks",
                 "direction": "outward", "linked_status_category": "new"},
                {"issue_key": "DEMO-101", "linked_key": "DEMO-90", "link_type": "Blocks",
                 "direction": "inward", "linked_status_category": "new"},
                {"issue_key": "DEMO-101", "linked_key": "OTHR-7", "link_type": "Relates",
                 "direction": "outward", "linked_status_category": "done"},
            ],
        )  # fmt: skip

    def test_all_nullable_fields_null(self):
        out = acli.parse_issue(load("view-all-null.json"), FIELD_IDS)
        issue = out["issue"]
        self.assertEqual(issue["key"], "DEMO-102")
        self.assertIs(issue["flagged"], False)
        self.assertEqual(issue["labels"], "[]")
        self.assertEqual(out["links"], [])
        for col in ("summary", "status_name", "status_category", "assignee",
                    "parent_key", "due_date", "created", "sprint_id", "sprint_end"):  # fmt: skip
            self.assertIsNone(issue[col], col)

    def test_columns_match_issues_table(self):
        db_spec = importlib.util.spec_from_file_location("td_db", PKG / "db.py")
        assert db_spec is not None and db_spec.loader is not None
        db = importlib.util.module_from_spec(db_spec)
        db_spec.loader.exec_module(db)
        conn = db.connect(":memory:")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(issues)")}
        link_cols = {r[1] for r in conn.execute("PRAGMA table_info(links)")}
        conn.close()
        out = acli.parse_issue(load("view-links-sprints-flagged.json"), FIELD_IDS)
        # The sync stamps these itself: the row's source/kind, which scope
        # found it, and when (sync.py).
        stamped = {"source", "kind", "scope", "synced_at"}
        self.assertEqual(set(out["issue"]), cols - stamped)
        self.assertEqual(set(out["links"][0]), link_cols)

    def test_no_active_sprint(self):
        raw = {
            "key": "A-1",
            "fields": {"customfield_10303": [{"id": 1, "state": "closed"}]},
        }
        self.assertIsNone(acli.parse_issue(raw, FIELD_IDS)["issue"]["sprint_id"])

    def test_missing_key_raises(self):
        with self.assertRaises(acli.AcliError):
            acli.parse_issue({"fields": {}}, FIELD_IDS)


class SprintTests(unittest.TestCase):
    def test_every_list_sprints_fixture(self):
        for path in fixtures("list-sprints-"):
            with self.subTest(path.name):
                sprints = acli.active_sprints(77, runner=replay(path.read_text()))
                self.assertTrue(sprints)
                for s in sprints:
                    self.assertEqual(set(s), {"id", "name", "startDate", "endDate"})
                    self.assertIsInstance(s["id"], int)

    def test_argv_and_values(self):
        runner = replay((FIXTURES / "list-sprints-active.json").read_text())
        sprints = acli.active_sprints(77, runner=runner)
        self.assertEqual(sprints[0]["id"], 503)
        self.assertEqual(sprints[0]["endDate"], "2026-10-26T13:00:00.000Z")
        self.assertEqual(
            runner.calls[0],
            ["acli", "jira", "board", "list-sprints", "--id", "77",
             "--state", "active", "--json"],
        )  # fmt: skip


class ClassifyTests(unittest.TestCase):
    def test_transient_fixtures_are_not_auth(self):
        for path in fixtures("stderr-transient-"):
            with self.subTest(path.name):
                err = acli.classify(1, "", path.read_text(), ["acli"])
                self.assertIs(type(err), acli.AcliError)

    def test_auth_fixtures_are_auth(self):
        for path in fixtures("stderr-auth-"):
            with self.subTest(path.name):
                self.assertIsInstance(
                    acli.classify(1, "", path.read_text(), ["acli"]), acli.AuthError
                )

    def test_synthetic_unauthorized(self):
        err = acli.classify(1, "", "Error: Unauthorized", ["acli", "jira"])
        self.assertIsInstance(err, acli.AuthError)
        self.assertIsInstance(err, acli.AcliError)
        self.assertEqual(err.argv, ["acli", "jira"])

    def test_auth_text_on_stdout(self):
        real = (FIXTURES / "real-stderr-auth-logged-out.txt").read_text()
        self.assertIsInstance(acli.classify(1, real, "", []), acli.AuthError)


# The only project keys a fixture may use. The real- captures are pseudonymised
# onto DEMO, ALPHA and BETA, so a capture committed without that pass carries
# some other prefix and fails here. An allowlist, not a list of the real keys:
# any function of those keys committed here, a hash included, would give them
# back. A new fixture's keys must use one of these.
PSEUDONYM_PROJECTS = {"DEMO", "ALPHA", "BETA", "OTHR"}
ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9]*)-\d+\b")


class FixtureHygieneTests(unittest.TestCase):
    """Keeps real captures safe: no email, account id, site domain or real key."""

    def test_no_identifying_text(self):
        files = [p for p in FIXTURES.rglob("*") if p.is_file()]
        self.assertTrue(files)
        for path in files:
            text = path.read_text()
            with self.subTest(path.name):
                self.assertNotIn("@", text)
                self.assertNotIn("atlassian.net", text.lower())
                self.assertNotIn("accountid", text.lower())
                unlisted = set(ISSUE_KEY.findall(text)) - PSEUDONYM_PROJECTS
                # The message names no prefix: an unlisted one may be real.
                self.assertTrue(not unlisted, "issue key outside PSEUDONYM_PROJECTS")
                self.assertNotIn("emailaddress", text.lower())


if __name__ == "__main__":
    unittest.main()
