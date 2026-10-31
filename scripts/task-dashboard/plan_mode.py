"""Now's Plan mode: the live plan (plans.py) in its own order, a reason each.

dev_docs/designs/2026-10-30-dashboard-as-agent-data-source.md, section 5. The
plan shown is plans.current's: the newest unexpired plan of any scope, a
session plan winning over a standing one while it lasts. Its entries keep the
plan's order and are not scored; pins still lead, as in every mode, and the
include filter, ignores and snoozes apply (fleet_now's shared behaviour).

EACH ENTRY is in one of three states, drawn from mirror rows only, so drawing
the page never calls GitHub:

- `done`, ticked, on a positive closed signal: the item is in the mirror in
  a done state (a Jira ticket in the done window, a landed worktree); a PR
  has a merged `closed_pr` row; or a GitHub issue is named, CLOSED, in a
  merged `closed_pr` row's `closes` (close_out.py's "task closed" evidence).
- `open`: in the mirror with no closed signal.
- `gone`: not in the mirror and no row says it closed. Leaving the mirror is
  not a tick: the issue mirror holds only open issues, and a row also leaves
  when a repo is archived, an issue is transferred, or a search returns less.
  A PR closed without merging is `gone` too, its evidence saying so. A gone
  entry keeps its place in the plan; its card is muted, flagged `gone`,
  titled by its closed PR's title when there is one, else by the entry's ref
  (`owner/repo#N` for a GitHub URL) or key, and says why it is not ticked.
  Until a sync refreshes planned items, a GitHub issue closed by hand, with
  no merged PR naming it, reads `gone`.
"""

import re
import sqlite3
from datetime import datetime
from typing import Any

import agent_query
import candidate
import fleet_views
import github_fleet
import needs
import plans
import score

MODE = "plan"
CATEGORY = "plan"
DONE, OPEN, GONE = "done", "open", "gone"
STATES = (DONE, OPEN, GONE)

_GITHUB = re.compile(r"https://github\.com/([^/]+/[^/]+)/(issues|pull)/(\d+)$")
_KIND = {"issues": "issue", "pull": "pr"}


def _closed_prs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        fleet_views._raw(raw)
        for (raw,) in conn.execute(
            "SELECT raw_json FROM issues WHERE source = ? AND kind = 'closed_pr'",
            (github_fleet.SOURCE,),
        )
    ]


def _closed_signals(
    closed: list[dict[str, Any]],
) -> "tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]":
    """(PR url -> its closed_pr row, issue url -> the merged PR closing it)."""
    prs: dict[str, dict[str, Any]] = {}
    issues: dict[str, dict[str, Any]] = {}
    for pr in closed:
        if pr.get("url"):
            prs[str(pr["url"])] = pr
        if not pr.get("merged"):
            continue
        for c in pr.get("closes") or []:
            if c.get("state") == "CLOSED" and c.get("repo") and c.get("number"):
                url = f"https://github.com/{c['repo']}/issues/{c['number']}"
                issues.setdefault(url, pr)
    return prs, issues


def _day(at: "str | None") -> str:
    return (at or "")[:10]


def _ref(pr: dict[str, Any]) -> str:
    return f"{pr.get('repo') or ''}#{pr.get('number')}"


def state_of(
    source: str,
    key: str,
    item: "agent_query.Item | None",
    prs: dict[str, dict[str, Any]],
    issues: dict[str, dict[str, Any]],
) -> "tuple[str, str]":
    """(state, evidence) for one plan entry."""
    pr = prs.get(key) if source == github_fleet.SOURCE else None
    if pr is not None and pr.get("merged"):
        return DONE, f"merged {_day(pr.get('merged_at') or pr.get('closed_at'))}"
    closer = issues.get(key) if source == github_fleet.SOURCE else None
    if closer is not None:
        return DONE, f"closed by {_ref(closer)}, merged {_day(closer.get('merged_at'))}"
    if item is not None:
        state = item.get("state") or {}
        if state.get("category") == "done":
            return DONE, str(state.get("name") or "done")
        return OPEN, str(state.get("name") or "open")
    if pr is not None:
        return GONE, f"closed without merging {_day(pr.get('closed_at'))}"
    return GONE, "not in the mirror, and nothing says it closed"


def _gone_need(source: str, key: str, pr: "dict[str, Any] | None") -> needs.Need:
    m = _GITHUB.match(key) if source == github_fleet.SOURCE else None
    kind = _KIND[m.group(2)] if m else ("ticket" if source == "jira" else "item")
    ref = f"{m.group(1)}#{m.group(3)}" if m else None
    return needs.Need(
        item=(source, key), kind=kind,
        title=(pr or {}).get("title") or ref or key, since=None,
        url=key if key.startswith("https://") else None, state="", ref=ref,
    )  # fmt: skip


def _need(i: agent_query.Item) -> needs.Need:
    return needs.Need(
        item=(i["source"], i["key"]), kind=i["kind"],
        title=i["title"] or i["ref"] or i["key"],
        since=score.parse_ts(i["updated"]), url=i["url"], state="",
        ref=i["ref"] if i["kind"] in ("issue", "pr", "ticket") else None,
        repo=i["_repo_bare"], branch=i["branch"], host=i["host"], path=i["path"],
    )  # fmt: skip


def state_line(entry: dict[str, Any], state: str, evidence: str) -> str:
    """The card's meta line: the entry's action and estimate, then its state."""
    bits = [entry["action"]] if entry.get("action") else []
    if entry.get("est_min") is not None:
        bits.append(f"~{entry['est_min']} min")
    bits.append(evidence if state == OPEN else f"{state}: {evidence}")
    return " · ".join(bits)


def collect(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> "tuple[dict[str, Any] | None, dict[str, Any] | None, list[candidate.Candidate]]":
    """(the live plan, the lapsed one when none is live, its entries as
    candidates in plan order). Each candidate's `plan` is {position, action,
    reason, est_min, state, evidence}."""
    plan, lapsed = plans.current(conn, now)
    if plan is None:
        return None, lapsed, []
    by_item = {(i["source"], i["key"]): i for i in agent_query.collect(conn, cfg, now)}
    closed = _closed_prs(conn)
    prs, issues = _closed_signals(closed)
    out = []
    for pos, e in enumerate(plan["entries"], 1):
        source, key = e["source"], e["key"]
        item = by_item.get((source, key))
        state, evidence = state_of(source, key, item, prs, issues)
        if item is not None:
            need = _need(item)
        else:
            need = _gone_need(source, key, prs.get(key))
        need.state = state_line(e, state, evidence)
        out.append(
            candidate.Candidate(
                need, CATEGORY, e["reason"], mode=MODE,
                plan={
                    "position": pos, "action": e.get("action"),
                    "reason": e["reason"], "est_min": e.get("est_min"),
                    "state": state, "evidence": evidence,
                },
            )
        )  # fmt: skip
    return plan, None, out


def summary(plan: dict[str, Any], states: list[str]) -> dict[str, Any]:
    """The plan as the agent view's `notes.plan` carries it: everything but
    its entries, with how many entries are in each state."""
    out = {k: v for k, v in plan.items() if k != "entries"}
    out["counts"] = {s: states.count(s) for s in STATES}
    return out


def head(plan: dict[str, Any], done: int, total: int) -> str:
    """The line above the list: whose plan for what, and how far along."""
    scope = "every scope" if plan["scope"] == plans.ALL else plan["scope"]
    bits = [f"{plan['kind'].capitalize()} plan for {scope} by {plan['author']}"]
    bits.append(f"made {_day(plan['created_at'])}")
    if plan["expires_at"]:
        local = score.parse_ts(plan["expires_at"])
        when = local.astimezone().strftime("%H:%M") if local else plan["expires_at"]
        bits.append(f"expires {when}")
    if plan["minutes"] is not None:
        bits.append(f"time box {plan['minutes']} min")
    if plan["est_min_sum"] is not None:
        bits.append(f"estimated {plan['est_min_sum']} min")
    bits.append(f"{done}/{total} done")
    return " · ".join(bits)


def empty(lapsed: "dict[str, Any] | None") -> str:
    """Plan mode's empty line when no plan is live: why, and how to make one."""
    how = (
        "No plan is live. An agent makes one with `dashboard.py plan set --scope"
        " <scope id|all> --by <name> --file plan.json` (the task-dashboard"
        " skill's `plan`), an ordered list with a reason for each item"
    )
    if lapsed is None:
        return how
    return f"The {lapsed['kind']} plan for {lapsed['scope']} has expired. {how}"
