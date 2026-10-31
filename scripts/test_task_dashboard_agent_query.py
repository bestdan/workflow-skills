#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/agent_query.py and the CLI's
`scopes` and `items` commands.

The fixture DB is written through the real writers (github_fleet.write, the
snapshot adapter, sessions.write, linear_issues.write), plus the few mirror rows no writer here
takes (a Jira ticket, a workstream), so no acli, gh, git or network runs.
Needs Python 3.11: run via scripts/test-task-dashboard-agent-query.sh.
"""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT / "scripts"))

import agent_query  # type: ignore[import-not-found]  # noqa: E402
import config  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import fleet_now  # type: ignore[import-not-found]  # noqa: E402
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import linear_issues  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import sessions  # type: ignore[import-not-found]  # noqa: E402
import snapshot_adapter  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_fleet_views as stale_fx  # noqa: E402
import test_task_dashboard_needs as needs_fx  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402

NOW = needs_fx.NOW
REPO = "bestdan/finplan"
OTHER = "bestdan/dotfiles"
WT = "/h/src/worktrees/finplan"
HUMAN = github_fleet.HUMAN_LABEL
TOKEN = "s3cret-server-token"

CONFIG_TOML = f"""
[jira]
site = "example.invalid"
board_ids = [77]
[jira.fields]
sprint = "customfield_10303"
flagged = "customfield_10007"
[github]
enabled = true
scope = "owner"
[linear]
enabled = true
[ingest.machines]
mini = "sha256:{"0" * 64}"
"""


def pr_url(n, repo=REPO):
    return f"https://github.com/{repo}/pull/{n}"


def issue_url(n, repo=REPO):
    return f"https://github.com/{repo}/issues/{n}"


def pr(n, branch, *, repo=REPO, ci="success", size=None, closes=(), bot=False,
       misses=("co_review",), role="author"):  # fmt: skip
    row = {
        "kind": "pr", "key": pr_url(n, repo), "url": pr_url(n, repo), "repo": repo,
        "number": n, "title": f"pr {n}", "role": role, "ci_state": ci,
        "in_queue": False, "queue_misses": list(misses), "review_decision": None,
        "head_ref": branch, "created": "2026-10-20T00:00:00Z",
        "updated": "2026-10-23T00:00:00Z",
        "closes": [{"repo": repo, "number": c, "milestone": None} for c in closes],
    }  # fmt: skip
    if bot:
        row.update(author="dependabot[bot]", author_is_bot=True)
    if size is not None:
        files, adds, dels, paths = size
        row["size"] = {"files": files, "additions": adds, "deletions": dels,
                       "paths": paths or [f"f{i}.py" for i in range(files)]}  # fmt: skip
    return row


def issue(n, *labels, repo=REPO, blocked_by=0, updated="2026-10-22T00:00:00Z"):
    return {
        "kind": "issue", "key": issue_url(n, repo), "url": issue_url(n, repo),
        "repo": repo, "number": n, "title": f"issue {n}", "labels": list(labels),
        "milestone": None, "created": "2026-10-01T00:00:00Z", "updated": updated,
        "blocks": 0, "blocked_by": blocked_by,
    }  # fmt: skip


DESIGN_URL = f"https://github.com/{REPO}/blob/main/dev_docs/designs/2026-10-20-x.md"

ROWS = [
    pr(10, "bestdan/fix-10", size=(2, 10, 4, None), closes=(1,)),  # small
    pr(11, "bestdan/docs-11", size=(1, 30, 0, ["README.md"])),  # docs-only
    pr(12, "bestdan/big-12", size=(9, 400, 20, None)),  # large
    pr(13, "dependabot/x", bot=True, role="others", misses=()),  # bot
    pr(14, "bestdan/unsized-14"),  # no size read yet
    pr(30, "bestdan/other-30", repo=OTHER, size=(1, 1, 1, None)),
    # A fork's PR from its `main`: never linked to the owner's `main` checkout.
    pr(50, "main", role="others", misses=()),
    issue(1, "est:1", "status:3_started", "prio:1"),
    issue(2, "est:2", "status:2_ready"),
    issue(3, "status:2_ready"),  # unsized
    issue(4, "est:5", "status:2_ready", "Topics", blocked_by=1),  # natively blocked
    issue(5, "blocked", "status:1_needs_refinement", HUMAN, "Topic"),
    issue(40, "est:1", repo=OTHER),
    issue(41, repo="acme/dotfiles"),  # another owner, the same bare name
    {
        "kind": "design",
        "key": DESIGN_URL,
        "url": DESIGN_URL,
        "repo": REPO,
        "path": "dev_docs/designs/2026-10-20-x.md",
        "title": "x design",
        "status": "proposed",
        "created": "2026-10-20",
    },  # fmt: skip
]

SNAPSHOT = {
    "schema": 1,
    "host": "mini",
    "taken_at": "2026-10-24T11:59:00.000Z",
    "worktrees": [
        {
            **stale_fx.worktree("fix-10", "bestdan/fix-10", "open", dirty=True),
            "repo": "finplan",
            "path": f"{WT}/fix-10",
        },
        {
            **stale_fx.worktree("old", "bestdan/old", "landed", "merged"),
            "repo": "finplan",
            "path": f"{WT}/old",
        },
        {
            **stale_fx.worktree("main", "main", "open"),
            "repo": "finplan",
            "path": "/h/src/finplan",
        },
    ],  # fmt: skip
    "handoffs": [],
    "tmux": [],
    "sessions": [
        # On PR 10's branch, asked a question an hour ago: waits on me.
        needs_fx.ev(
            "s-10", "2026-10-24T10:00:00.000Z", "UserPromptSubmit", f"{WT}/fix-10"
        ),
        needs_fx.ev(
            "s-10", "2026-10-24T11:00:00.000Z", "Stop", f"{WT}/fix-10", asked=True
        ),
        # Working, on no PR's branch.
        needs_fx.ev(
            "s-work", "2026-10-24T11:30:00.000Z", "UserPromptSubmit", "/h/src/dotfiles"
        ),
        # Working in a subdirectory of the `main` checkout.
        needs_fx.ev(
            "s-main",
            "2026-10-24T11:40:00.000Z",
            "UserPromptSubmit",
            "/h/src/finplan/scripts",
        ),
    ],  # fmt: skip
    "history": [],
}

# Another machine's `main` checkout of the same repo, at the same path, with a
# session waiting on me in it: none of it is the mini checkout's activity.
OTHER_HOST = {
    "schema": 1,
    "host": "lin",
    "taken_at": "2026-10-24T11:59:00.000Z",
    "worktrees": [
        {**stale_fx.worktree("main", "main", "open"), "repo": "finplan",
         "path": "/h/src/finplan"},
    ],
    "handoffs": [],
    "tmux": [],
    "sessions": [
        needs_fx.ev("s-lin", "2026-10-24T10:00:00.000Z", "UserPromptSubmit",
                    "/h/src/finplan"),
        needs_fx.ev("s-lin", "2026-10-24T11:00:00.000Z", "Stop", "/h/src/finplan",
                    asked=True),
    ],
    "history": [],
}  # fmt: skip

WS = "linear:p-1"
WS_OTHER = "linear:p-2"
WS_DONE = "linear:p-3"  # its only finplan member is done
WS_ALL = "design:bestdan/dotfiles/dev_docs/designs/allow.md"


def linear(key, state_type="started", state="In Progress", **kw):
    """An assigned_issues dict, as linear_issues.write takes it."""
    out = {
        "key": key, "title": f"title {key}",
        "url": f"https://linear.app/acme/issue/{key.lower()}", "state": state,
        "state_type": state_type, "priority": 2, "priority_label": "High",
        "due": None, "estimate": None, "created": "2026-10-05T00:00:00Z",
        "updated": "2026-10-22T12:00:00Z", "started": None, "team": "PRE",
        "labels": [], "cycle": None, "cycle_end": None, "cycle_active": False,
        "project": None, "project_id": None, "assignee": "Me",
        "assignee_email": "me@example.com", "assignee_is_me": True,
        "blocked_by": [], "blocks": [], "truncated": False,
    }  # fmt: skip
    out.update(kw)
    return out


LINEAR = [
    # Mine, in workstream WS's project, estimated, in a cycle, blocked.
    linear("PRE-1", estimate=3, cycle="Cycle 12", cycle_active=True,
           due="2026-11-04", project="FinPlan Tax Engine", project_id="p-1",
           blocked_by=["PRE-9"], blocks=["PRE-4"]),
    # Unassigned; its est: label wins over the estimate.
    linear("PRE-2", "unstarted", "Todo", estimate=2, labels=["est:1"],
           assignee=None, assignee_email=None, assignee_is_me=False),
    # Someone else's, in a project that is no open workstream.
    linear("PRE-4", "backlog", "Backlog", assignee="Ana",
           assignee_email="ana@example.com", assignee_is_me=False,
           project="Shipped Thing", project_id="p-9", estimate=0.5),
]  # fmt: skip
# Synced before the read carried the assignee and the project id.
LEGACY = {
    k: v for k, v in linear("PRE-3", project="FinPlan Tax Engine").items()
    if k not in ("assignee", "assignee_email", "assignee_is_me", "project_id")
}  # fmt: skip


def seed(conn):
    linear_issues.write(conn, [*LINEAR, LEGACY], NOW)
    snap = copy.deepcopy(SNAPSHOT)
    for line in snap["sessions"]:
        if line["session_id"] == "s-10":
            line["branch"] = "bestdan/fix-10"
        if line["session_id"] == "s-main":
            line["branch"] = "main"
    snapshot_adapter.ingest(conn, snap, NOW)
    other = copy.deepcopy(OTHER_HOST)
    for line in other["sessions"]:
        line["branch"] = "main"
    snapshot_adapter.ingest(conn, other, NOW)
    sessions.write(conn, sessions.derive(conn, NOW), NOW)
    github_fleet.write(conn, ROWS, NOW)
    server_harness.add_run(conn, "ok", NOW, kind=github_fleet.RUN_KIND)
    conn.executemany(
        "INSERT INTO workstreams (id, source, title) VALUES (?, ?, ?)",
        [(WS, "linear", "FinPlan Tax Engine"), (WS_OTHER, "linear", "Decumulation"),
         (WS_ALL, "design", "Allow-listed gh api writes")],
    )  # fmt: skip
    conn.executemany(
        "INSERT INTO workstream_members (ws_id, item_source, item_key, kind, via,"
        " is_open, started, waiting) VALUES (?, 'github', ?, ?, 'test', 1, 0, 0)",
        [(WS, issue_url(2), "issue"), (WS_OTHER, pr_url(12), "pr"),
         (WS_OTHER, issue_url(3), "issue"), (WS_ALL, issue_url(40, OTHER), "issue")],
    )  # fmt: skip
    conn.execute(
        "INSERT INTO workstreams (id, source, title) VALUES (?, 'linear', ?)",
        (WS_DONE, "Finished Thing"),
    )
    conn.execute(
        "INSERT INTO workstream_members (ws_id, item_source, item_key, kind, via,"
        " is_open, started, waiting) VALUES (?, 'machine:mini', ?, 'worktree',"
        " 'test', 0, 0, 0)",
        (WS_DONE, f"worktree:{WT}/old"),
    )
    # A Jira ticket under an epic, blocked by an open one, and a PR naming it.
    conn.executemany(
        "INSERT INTO issues (source, kind, key, summary, status_name,"
        " status_category, priority, parent_key, labels, created, updated, scope)"
        " VALUES ('jira', 'ticket', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'search')",
        [("PAY-7", "pay the thing", "In Progress", "indeterminate", "High",
          "PAY-1", '["est:2"]', "2026-10-10T00:00:00Z", "2026-10-21T00:00:00Z"),
         ("PAY-1", "payments epic", "To Do", "new", None, None, "[]",
          "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"),
         ("PAY-9", "shipped", "Done", "done", None, "PAY-1", "[]",
          "2026-10-01T00:00:00Z", "2026-10-20T00:00:00Z")],
    )  # fmt: skip
    conn.execute(
        "INSERT INTO links (issue_key, linked_key, link_type, direction,"
        " linked_status_category) VALUES ('PAY-7', 'PAY-1', 'Blocks', 'inward',"
        " 'new')"
    )
    conn.execute(
        "INSERT INTO prs (url, repo, number, title, head_ref, is_draft,"
        " review_decision, ci_state, role, issue_key, raw_json) VALUES"
        " (?, 'acme/pay', 77, 'PAY-7 pay', 'bestdan/PAY-7', 0, 'APPROVED',"
        " 'success', 'author', 'PAY-7', '{}')",
        ("https://github.com/acme/pay/pull/77",),
    )
    conn.execute("UPDATE prs SET updated = '2026-10-23T00:00:00Z' WHERE number = 77")
    # An older PR in another repo naming the same key, with a higher number.
    conn.execute(
        "INSERT INTO prs (url, repo, number, title, head_ref, is_draft,"
        " review_decision, ci_state, role, issue_key, updated, raw_json) VALUES"
        " (?, 'acme/old', 900, 'PAY-7 old', 'bestdan/PAY-7-old', 0, NULL,"
        " 'failure', 'author', 'PAY-7', '2026-09-01T00:00:00Z', '{}')",
        ("https://github.com/acme/old/pull/900",),
    )
    conn.execute("INSERT INTO meta (key, value) VALUES ('server_token', ?)", (TOKEN,))
    conn.commit()


def cfg():
    out = needs_fx.fleet_cfg()
    out["linear"]["enabled"] = True
    return out


class Case(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = Path(self._td.name) / "d.db"
        conn = db.connect(self.db_path)
        seed(conn)
        conn.close()
        self.conn = db.connect_read_only(self.db_path)
        self.addCleanup(self.conn.close)
        self.cfg = cfg()

    def items(self, **kw):
        kw.setdefault("fields", "full")
        return agent_query.items_doc(self.conn, self.cfg, NOW, **kw)

    def by_key(self, **kw):
        return {i["key"]: i for i in self.items(**kw)["items"]}


class ScopeTests(Case):
    def scopes(self, query=None):
        return agent_query.scopes_doc(self.conn, self.cfg, NOW, query)

    def test_a_word_matches_the_repo_and_workstream_titles(self):
        doc = self.scopes("finplan")
        ids = [s["id"] for s in doc["matched"]]
        self.assertEqual(ids, [f"repo:{REPO}", f"ws:{WS}"])
        repo = doc["matched"][0]
        # Open items of every kind: 6 PRs, 5 issues, the design, three open
        # worktrees (two hosts) and three sessions. The landed one is done.
        self.assertEqual(repo["open"], 18)

    def test_a_repo_scope_lists_related_workstreams_it_did_not_match(self):
        related = self.scopes("finplan")["related"]
        self.assertEqual(
            related,
            [{"id": f"ws:{WS_OTHER}", "kind": "workstream",
              "title": "Decumulation", "members_in_scope": 2}],
        )  # fmt: skip

    def test_related_leaves_out_a_workstream_with_only_done_members(self):
        ids = [r["id"] for r in self.scopes("finplan")["related"]]
        self.assertNotIn(f"ws:{WS_DONE}", ids)

    def test_no_query_lists_every_kind_and_no_related(self):
        doc = self.scopes()
        kinds = {s["kind"] for s in doc["matched"]}
        self.assertEqual(kinds, set(agent_query.SCOPE_KINDS))
        self.assertNotIn("related", doc)
        ids = {s["id"] for s in doc["matched"]}
        self.assertLessEqual(
            {"jira:PAY", "epic:PAY-1", "host:mini", "label:est:1", f"repo:{OTHER}"},
            ids,
        )

    def test_all_is_reserved_not_matched(self):
        # As a word, `all` would match this workstream's title.
        every = agent_query._scopes(
            self.conn, agent_query.collect(self.conn, self.cfg, NOW)
        )
        self.assertIn(f"ws:{WS_ALL}", [s.id for s in agent_query.match(every, "all")])
        doc = self.items(scope=["all"])
        self.assertEqual(doc["scope"], "all")
        self.assertEqual(doc["total"], self.items()["total"])
        self.assertEqual(self.items(scope=["finplan", "all"])["scope"], "all")
        self.assertEqual(
            len(self.scopes("all")["matched"]), len(self.scopes()["matched"])
        )

    def test_an_exact_id_wins_over_the_word_match(self):
        doc = self.items(scope=[f"repo:{REPO}"])
        self.assertEqual([s["id"] for s in doc["scope"]], [f"repo:{REPO}"])

    def test_scopes_union_and_name_what_they_resolved_to(self):
        doc = self.items(scope=[f"ws:{WS_OTHER}", "jira:PAY"])
        self.assertEqual(
            sorted(i["key"] for i in doc["items"]),
            ["PAY-1", "PAY-7", issue_url(3), pr_url(12)],
        )

    def test_an_unknown_scope_is_an_error_naming_it(self):
        with self.assertRaisesRegex(agent_query.QueryError, "no scope matches 'zzz'"):
            self.items(scope=["zzz"])
        with self.assertRaisesRegex(agent_query.QueryError, "no repo scope"):
            self.items(scope=["repo:acme/none"])

    def test_repo_with_a_bare_name_names_every_owner_of_it(self):
        doc = self.items(scope=["repo:finplan"])
        self.assertEqual([s["id"] for s in doc["scope"]], [f"repo:{REPO}"])
        both = sorted(s["id"] for s in self.items(scope=["repo:dotfiles"])["scope"])
        self.assertEqual(both, ["repo:acme/dotfiles", f"repo:{OTHER}"])
        with self.assertRaisesRegex(agent_query.QueryError, "no repo scope"):
            self.items(scope=["repo:fin"])  # whole names only, no substring

    def test_an_id_in_another_case_is_that_id_not_a_substring(self):
        doc = self.items(scope=["label:topic"])
        self.assertEqual([s["id"] for s in doc["scope"]], ["label:Topic"])

    def test_a_bare_named_session_counts_under_every_owner(self):
        for scope in (f"repo:{OTHER}", "repo:acme/dotfiles"):
            self.assertIn("s-work", self.by_key(scope=[scope]), scope)

    def test_a_full_repo_scope_keeps_out_another_owners_repo(self):
        keys = self.by_key(scope=[f"repo:{OTHER}"])
        self.assertIn(issue_url(40, OTHER), keys)
        self.assertNotIn(issue_url(41, "acme/dotfiles"), keys)
        # A session knows only its directory, so it matches by bare name.
        self.assertIn("s-work", keys)

    def test_epic_and_host_scopes(self):
        epic = self.by_key(scope=["epic:PAY-1"], state="todo,started,done")
        self.assertEqual(sorted(epic), ["PAY-1", "PAY-7", "PAY-9"])
        host = self.by_key(scope=["host:mini"])
        self.assertEqual({i["kind"] for i in host.values()}, {"session", "worktree"})


class LinearTests(Case):
    def test_a_linear_ticket_is_shaped_as_a_jira_one(self):
        pre = self.by_key(kind="ticket")["PRE-1"]
        self.assertEqual(
            {k: pre.get(k) for k in ("source", "ref", "title", "url", "state", "prio",
                                     "due", "cycle", "assignee", "mine", "blocked_by",
                                     "blocks", "size")},
            {"source": "linear", "ref": "PRE-1", "title": "title PRE-1",
             "url": "https://linear.app/acme/issue/pre-1",
             "state": {"name": "In Progress", "category": "started"},
             "prio": "High", "due": "2026-11-04", "cycle": "Cycle 12",
             "assignee": "Me", "mine": True, "blocked_by": ["PRE-9"],
             "blocks": ["PRE-4"],
             "size": {"bucket": "est:3", "est_min": None,
                      "from": "linear estimate 3"}},
        )  # fmt: skip
        self.assertIs(self.by_key(kind="ticket")["PAY-7"]["mine"], True)

    def test_listed_beside_jira_tickets(self):
        keys = sorted(self.by_key(kind="ticket"))
        self.assertEqual(keys, ["PAY-1", "PAY-7", "PRE-1", "PRE-2", "PRE-3", "PRE-4"])

    def test_state_and_size_of_the_rest(self):
        keys = self.by_key(kind="ticket")
        self.assertEqual(keys["PRE-2"]["state"], {"name": "Todo", "category": "todo"})
        self.assertEqual(keys["PRE-4"]["state"]["category"], "todo")
        # An est: label wins over the estimate, as it does on a Jira ticket.
        self.assertEqual(keys["PRE-2"]["size"]["from"], "label est:1")
        self.assertEqual(keys["PRE-4"]["size"]["bucket"], "est:0.5")

    def test_not_mine_is_marked_and_survives_brief(self):
        brief = {i["key"]: i for i in self.items(fields=None, kind="ticket")["items"]}
        self.assertIs(brief["PRE-2"]["mine"], False)  # unassigned
        self.assertIs(brief["PRE-4"]["mine"], False)  # someone else's
        self.assertIs(brief["PRE-1"]["mine"], True)
        # A row synced before the read carried its assignee cannot tell.
        self.assertNotIn("mine", brief["PRE-3"])
        self.assertNotIn("assignee", brief["PRE-1"])  # full only

    def test_linear_me_decides_mine_when_set(self):
        self.cfg["linear"]["me"] = "ANA@example.com"
        keys = self.by_key(kind="ticket")
        self.assertEqual((keys["PRE-1"]["mine"], keys["PRE-4"]["mine"]), (False, True))
        # A row synced before the read carried its assignee still cannot tell.
        self.assertNotIn("mine", keys["PRE-3"])

    def test_a_team_scope_lists_its_open_count_and_resolves(self):
        doc = agent_query.scopes_doc(self.conn, self.cfg, NOW, "linear:PRE")
        self.assertEqual(
            doc["matched"],
            [{"id": "linear:PRE", "kind": "linear", "title": "PRE", "open": 4}],
        )
        keys = self.by_key(scope=["linear:PRE"])
        self.assertEqual(sorted(keys), ["PRE-1", "PRE-2", "PRE-3", "PRE-4"])
        # A Linear key mints no jira: scope, and is in none.
        every = {s["id"] for s in agent_query.scopes_doc(
            self.conn, self.cfg, NOW)["matched"]}  # fmt: skip
        self.assertNotIn("jira:PRE", every)
        self.assertNotIn("PRE-1", self.by_key(scope=["jira:PAY"]))

    def test_a_linear_team_sharing_a_jira_project_key_stays_linear(self):
        rw = db.connect(self.db_path)
        linear_issues.write(rw, [*LINEAR, LEGACY, linear("PAY-100", team="PAY")], NOW)
        rw.close()
        self.assertEqual(sorted(self.by_key(scope=["linear:PAY"])), ["PAY-100"])
        self.assertNotIn("PAY-100", self.by_key(scope=["jira:PAY"]))

    def test_a_project_ticket_joins_its_workstream(self):
        keys = self.by_key(kind="ticket")
        self.assertEqual(keys["PRE-1"]["workstreams"], [WS])  # by project id
        self.assertEqual(keys["PRE-3"]["workstreams"], [WS])  # by project name
        self.assertNotIn("workstreams", keys["PRE-4"])  # a project no ws names
        ws = self.by_key(scope=[f"ws:{WS}"])
        self.assertLessEqual({"PRE-1", "PRE-3", issue_url(2)}, set(ws))

    def test_off_lists_none(self):
        self.cfg["linear"]["enabled"] = False
        self.assertFalse([k for k in self.by_key() if k.startswith("PRE-")])
        self.assertNotIn("linear:PRE", {s["id"] for s in agent_query.scopes_doc(
            self.conn, self.cfg, NOW)["matched"]})  # fmt: skip


class ItemTests(Case):
    def test_state_defaults_to_open_and_done_is_asked_for(self):
        keys = self.by_key()
        self.assertNotIn("PAY-9", keys)
        self.assertNotIn(f"worktree:{WT}/old", keys)
        done = self.by_key(state="done")
        self.assertEqual(sorted(done), ["PAY-9", f"worktree:{WT}/old"])
        self.assertEqual(done["PAY-9"]["state"], {"name": "Done", "category": "done"})

    def test_kind_filter(self):
        kinds = {i["kind"] for i in self.items(kind="session,worktree")["items"]}
        self.assertEqual(kinds, {"session", "worktree"})

    def test_a_github_issue_state_is_its_status_label(self):
        keys = self.by_key()
        self.assertEqual(
            keys[issue_url(1)]["state"],
            {"name": "status:3_started", "category": "started"},
        )
        self.assertEqual(keys[issue_url(2)]["state"]["category"], "todo")
        self.assertEqual(keys[issue_url(1)]["prio"], "prio:1")

    def test_size_buckets_and_minutes(self):
        keys = self.by_key()

        def size(k):
            return keys[k].get("size")

        self.assertEqual(
            size(pr_url(10)),
            {"bucket": "small_pr", "est_min": 15, "from": "PR, 2 files, +10/−4"},
        )
        self.assertEqual(size(pr_url(11))["bucket"], "docs_pr")
        self.assertEqual(size(pr_url(11))["est_min"], 10)
        self.assertEqual(size(pr_url(13))["bucket"], "bot_pr")
        self.assertEqual(size(pr_url(13))["est_min"], 5)
        # Sized but large: a bucket and no default minutes.
        self.assertEqual(size(pr_url(12))["bucket"], "large_pr")
        self.assertNotIn("est_min", {k for k, v in size(pr_url(12)).items() if v})
        self.assertEqual(
            size(issue_url(1)),
            {"bucket": "est:1", "est_min": 20, "from": "label est:1"},
        )
        self.assertEqual(size(issue_url(2))["est_min"], 45)
        self.assertEqual(size(issue_url(4)), {"bucket": "est:5", "est_min": None,
                                              "from": "label est:5"})  # fmt: skip
        self.assertEqual(size("PAY-7")["bucket"], "est:2")
        # Unsized is absent, never small.
        self.assertIsNone(size(pr_url(14)))
        self.assertIsNone(size(issue_url(3)))

    def test_size_minutes_come_from_config(self):
        self.cfg["size"]["minutes"]["est:5"] = 120
        self.cfg["size"]["minutes"]["small_pr"] = 7
        keys = self.by_key()
        self.assertEqual(keys[issue_url(4)]["size"]["est_min"], 120)
        self.assertEqual(keys[pr_url(10)]["size"]["est_min"], 7)

    def test_size_agrees_with_little(self):
        """Every PR Little calls small carries the same bucket here."""
        keys = self.by_key()
        for r in fleet_now.little_candidates(self.conn, self.cfg, NOW):
            if r.need.kind == "pr":
                self.assertEqual(keys[r.need.item[1]]["size"]["bucket"], r.category)

    def test_pr_joins(self):
        keys = self.by_key()
        # A PR carries its own readiness.
        self.assertEqual(
            keys[pr_url(10)]["pr"],
            {"ref": f"{REPO}#10", "ci": "success", "review": None, "draft": False,
             "co_reviewed": False},
        )  # fmt: skip
        # The issue PR 10 closes carries it; a Jira ticket carries the PR
        # naming its key, from the `prs` table alone.
        self.assertEqual(keys[issue_url(1)]["pr"]["ref"], f"{REPO}#10")
        self.assertEqual(keys["PAY-7"]["pr"]["review"], "APPROVED")
        self.assertIsNone(keys["PAY-7"]["pr"]["co_reviewed"])
        # A worktree and a session on PR 10's branch carry it too.
        self.assertEqual(keys[f"worktree:{WT}/fix-10"]["pr"]["ref"], f"{REPO}#10")
        self.assertEqual(keys["s-10"]["pr"]["ref"], f"{REPO}#10")
        self.assertNotIn("pr", keys[issue_url(2)])

    def test_a_design(self):
        d = self.by_key(kind="design")[DESIGN_URL]
        self.assertEqual(
            (d["ref"], d["state"], d["repo"]),
            ("dev_docs/designs/2026-10-20-x.md",
             {"name": "proposed", "category": "todo"}, REPO),
        )  # fmt: skip

    def test_a_main_checkout_lists_only_its_own_host_and_sessions(self):
        # Both hosts' checkouts share the key; the source tells them apart.
        main = {i["source"]: i for i in self.items()["items"]
                if i["key"] == "worktree:/h/src/finplan"}  # fmt: skip
        mini, lin = main["machine:mini"], main["machine:lin"]
        self.assertEqual(
            sorted((a["kind"], a["host"]) for a in mini["activity"]),
            [("session", "mini"), ("worktree", "mini")],
        )
        waiting = {(i["source"], i["key"]) for i in self.items(only="waiting")["items"]}
        self.assertNotIn(("machine:mini", "worktree:/h/src/finplan"), waiting)
        self.assertIn(("machine:lin", "worktree:/h/src/finplan"), waiting)
        self.assertEqual(
            sorted((a["kind"], a["state"]) for a in lin["activity"]),
            [("session", "waiting"), ("worktree", "clean")],
        )

    def test_a_fork_pr_on_main_is_not_linked_by_branch(self):
        keys = self.by_key()
        self.assertNotIn("pr", keys["worktree:/h/src/finplan"])
        self.assertNotIn("activity", keys[pr_url(50)])

    def test_the_newest_pr_wins_across_repos(self):
        # PR 900 has the higher number but is older, in another repo.
        self.assertEqual(self.by_key()["PAY-7"]["pr"]["ref"], "acme/pay#77")

    def test_dumps_with_items_only_is_valid_json(self):
        self.assertEqual(json.loads(agent_query.dumps({"items": []})), {"items": []})

    def test_activity_follows_the_pr_branch(self):
        keys = self.by_key()
        acts = keys[issue_url(1)]["activity"]
        self.assertEqual(
            sorted((a["kind"], a["state"]) for a in acts),
            [("session", "waiting"), ("worktree", "dirty")],
        )
        session = next(a for a in acts if a["kind"] == "session")
        self.assertEqual(session["host"], "mini")
        self.assertEqual(session["waiting_since"], "2026-10-24T11:00:00.000Z")
        self.assertNotIn("activity", keys[issue_url(2)])

    def test_only_waiting(self):
        keys = self.by_key(only="waiting")
        # s-lin waits in lin's `main` checkout, which waits with it.
        self.assertEqual(
            sorted(keys), sorted([issue_url(1), pr_url(10), "s-10",
                                  f"worktree:{WT}/fix-10", "s-lin",
                                  "worktree:/h/src/finplan"]),
        )  # fmt: skip

    def test_only_blocked_and_state_combine(self):
        keys = self.by_key(only="blocked")
        self.assertEqual(sorted(keys), ["PAY-7", "PRE-1", issue_url(4), issue_url(5)])
        self.assertEqual(sorted(self.by_key(only="blocked", state="started")),
                         ["PAY-7", "PRE-1"])  # fmt: skip
        pay = self.by_key()["PAY-7"]
        self.assertEqual((pay["blocked_by"], pay.get("blocks")), (["PAY-1"], None))
        self.assertEqual(self.by_key()[issue_url(4)]["blocked_by"], 1)

    def test_only_stale_is_the_stale_tab(self):
        # The landed worktree is Stale's, under Close; it is done, so asked for.
        keys = self.by_key(only="stale", state="todo,started,done")
        self.assertIn(f"worktree:{WT}/old", keys)
        old = keys[f"worktree:{WT}/old"]
        self.assertEqual(old["action"]["command"][0], "worktree-remove.sh")

    def test_now_terms_come_from_ship(self):
        keys = self.by_key()
        ranked = {
            r.need.item: r for r in fleet_now.candidates(self.conn, self.cfg, NOW)
        }
        r = ranked[(github_fleet.SOURCE, issue_url(5))]
        self.assertEqual(
            keys[issue_url(5)]["now"],
            {"tier": r.tier[1], "category": r.category, "score": r.total,
             "terms": [[n, v] for n, v, _ in r.terms if v]},
        )  # fmt: skip
        # PR 10 folds into the issue it closes.
        self.assertEqual(keys[pr_url(10)]["now"], {"folded_into": issue_url(1)})

    def test_overlay(self):
        conn = db.connect(self.db_path)
        it = (github_fleet.SOURCE, issue_url(2))
        overlay.pin(conn, it, NOW)
        overlay.snooze(conn, it, "2026-11-30")
        conn.close()
        self.assertEqual(
            self.by_key()[issue_url(2)]["overlay"],
            {"pinned": True, "snoozed_until": "2026-11-30"},
        )
        self.assertNotIn("overlay", self.by_key()[issue_url(3)])

    def test_workstreams(self):
        self.assertEqual(self.by_key()[issue_url(2)]["workstreams"], [WS])


class FieldTests(Case):
    def test_brief_is_sparse_and_drops_a_url_that_repeats_the_key(self):
        doc = self.items(fields=None)
        self.assertTrue(doc["sparse"])
        item = next(i for i in doc["items"] if i["key"] == issue_url(3))
        self.assertNotIn("url", item)
        self.assertNotIn("pr", item)
        self.assertLessEqual(set(item), set(agent_query.BRIEF))
        self.assertNotIn("evidence", item)

    def test_a_named_list_prints_exactly_those_plus_identity(self):
        doc = self.items(fields="size,pr")
        self.assertFalse(doc["sparse"])
        for item in doc["items"]:
            self.assertEqual(list(item), ["source", "key", "ref", "kind", "size", "pr"])

    def test_identity_named_in_the_list_is_not_repeated(self):
        doc = self.items(fields="key,title")
        self.assertEqual(
            list(doc["items"][0]), ["source", "key", "ref", "kind", "title"]
        )

    def test_bad_values_are_refused(self):
        for kw in ({"fields": "title,nope"}, {"kind": "epic"}, {"state": "open"},
                   {"only": "late"}, {"limit": -1}):  # fmt: skip
            with self.subTest(kw=kw):
                with self.assertRaises(agent_query.QueryError):
                    self.items(**kw)

    def test_limit_and_offset_report_total_and_returned(self):
        every = self.items()
        page = self.items(limit=3, offset=2)
        self.assertEqual((page["total"], page["returned"]), (every["total"], 3))
        self.assertEqual(page["items"], every["items"][2:5])

    def test_newest_activity_first(self):
        stamps = [i.get("updated") for i in self.items(fields="updated")["items"]]
        dated = [s for s in stamps if s]
        self.assertEqual(dated, sorted(dated, reverse=True))
        self.assertEqual(stamps[: len(dated)], dated)  # undated last


class CliTests(Case):
    def run_cli(self, *argv):
        cfg_path = Path(self._td.name) / "config.toml"
        cfg_path.write_text(CONFIG_TOML)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dashboard.main(
                [*argv, "--db", str(self.db_path), "--config", str(cfg_path)]
            )
        return code, out.getvalue(), err.getvalue()

    def test_items_prints_one_compact_line_per_item(self):
        code, out, _ = self.run_cli(
            "items", "--scope", "finplan", "--kind", "pr", "--limit", "2"
        )
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["returned"], 2)
        self.assertEqual(len(out.strip().splitlines()), 4)  # head, 2 items, ]}
        self.assertIn("−", out)  # not escaped

    def test_jsonl(self):
        code, out, _ = self.run_cli("items", "--jsonl", "--kind", "session")
        lines = [json.loads(x) for x in out.strip().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual(lines[0]["returned"], len(lines) - 1)
        self.assertNotIn("items", lines[0])

    def test_scopes(self):
        code, out, _ = self.run_cli("scopes", "finplan")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["query"], "finplan")

    def test_a_bad_scope_exits_2(self):
        code, out, err = self.run_cli("items", "--scope", "zzz")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no scope matches", err)

    def test_never_prints_the_server_token_or_writes(self):
        before = self.db_path.read_bytes()
        for argv in (("scopes",), ("items", "--fields", "full", "--state",
                                   "todo,started,done")):  # fmt: skip
            code, out, _ = self.run_cli(*argv)
            self.assertEqual(code, 0)
            self.assertNotIn(TOKEN, out)
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_a_missing_db_exits_1(self):
        cfg_path = Path(self._td.name) / "config.toml"
        cfg_path.write_text(CONFIG_TOML)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = dashboard.main(["items", "--db", str(Path(self._td.name) / "no.db"),
                                   "--config", str(cfg_path)])  # fmt: skip
        self.assertEqual(code, 1)
        self.assertIn("no dashboard DB", err.getvalue())


class ReadOnlyTests(unittest.TestCase):
    def test_refuses_writes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "d.db"
            db.connect(path).close()
            conn = db.connect_read_only(path)
            self.addCleanup(conn.close)
            with self.assertRaises(db.sqlite3.OperationalError):
                conn.execute("INSERT INTO meta (key, value) VALUES ('x', 'y')")

    def test_brings_a_db_that_is_behind_up_to_date_first(self):
        latest = max(v for v, _ in db.MIGRATIONS)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "d.db"
            raw = db.sqlite3.connect(path)
            saved = db.MIGRATIONS
            db.MIGRATIONS = [m for m in saved if m[0] < latest]
            try:
                db.migrate(raw)
            finally:
                db.MIGRATIONS = saved
            raw.close()
            conn = db.connect_read_only(path)
            self.addCleanup(conn.close)
            self.assertEqual(db._stored_version(conn), latest)

    def test_a_missing_db_is_not_created(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "none.db"
            with self.assertRaises(FileNotFoundError):
                db.connect_read_only(path)
            self.assertFalse(path.exists())


class ConfigTests(unittest.TestCase):
    def load(self, extra):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.toml"
            p.write_text(CONFIG_TOML + extra)
            return config.load(p)

    def test_defaults(self):
        mins = self.load("")["size"]["minutes"]
        self.assertEqual(
            mins,
            {"bot_pr": 5, "docs_pr": 10, "small_pr": 15, "est:1": 20, "est:2": 45},
        )

    def test_override_and_add(self):
        mins = self.load('\n[size.minutes]\n"est:3" = 90\nbot_pr = 2\n')["size"][
            "minutes"
        ]
        self.assertEqual((mins["est:3"], mins["bot_pr"], mins["est:1"]), (90, 2, 20))

    def test_refused(self):
        for extra in ('\n[size.minutes]\nhuge = 9\n', '\n[size.minutes]\n"est:0" = 9\n',
                      '\n[size.minutes]\nbot_pr = 0\n', '\n[size.minutes]\nbot_pr = "5"\n',
                      '\n[size]\nminutes = 5\n'):  # fmt: skip
            with self.subTest(extra=extra):
                with self.assertRaises(config.ConfigError):
                    self.load(extra)


if __name__ == "__main__":
    unittest.main()
