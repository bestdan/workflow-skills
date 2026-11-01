"""`dashboard.py brief`: one call behind "help me prioritize X".

dev_docs/designs/2026-10-30-dashboard-as-agent-data-source.md, section 4. A
fixed-shape pack of a scope's state, in sections, within a token budget:

- `freshness`: each sync step's last success and each host's last snapshot
  (freshness.sources, the header's own read).
- `in_flight`: open PRs by readiness (PR_READINESS), sessions waiting on the
  owner, and worktrees with no PR.
- `blocked`: what is blocked, with what blocks it, and what blocks others.
- `candidates`: what the Now mode ranks, in its order, over the scope and
  not capped at the page's list length. With `--minutes`, the sized ones up
  to twice the time box, then the unsized ones apart. Deep inlines the top
  item's `item` dossier.
- `signals`: the owner's pins, active snoozes, notes and recent why-not
  answers in scope.
- `gaps`: the sources this instance does not mirror (GAPS).

Nothing here judges: every list is a projection of the mirror, through the
code `items` and Now already use (agent_query.collect, fleet_now's mode
collectors and orders). The judgment is the agent's.

**The budget.** About BUDGET_TOKENS tokens, measured as the printed JSON's
UTF-8 bytes over BYTES_PER_TOKEN, the measure the design's table of `view`
sizes used; a brief record is about 550 bytes, so about 50 records fill it.
Freshness, gaps, the Deep dossier and each list's header are always printed,
so they alone can take it past the budget, which `budget.over` then says.
The lists then fill in two passes, each in FILL order: first up to FLOOR
items each, so every section shows something, then the rest of each while it
fits (a list stops at its first item that does not fit, and a later list's
smaller item may still fit). So the end of FILL truncates first: the
unsized candidates, then the
`other` and draft PRs, blocking and blocked, worktrees with no PR, the
candidates, the owner's signals, and last the PRs and sessions the owner can
act on now. A list cut short says how many it left out (`omitted`), and a
`more` command whose output holds them: for a PR readiness bucket,
`blocking` and the signals that is the whole kind they were drawn from, a
superset to filter by the same rule, since `items` has no filter for them.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Any

import agent_query
import close_out
import fleet_now
import freshness
import linear_issues
import overlay
import score
import sessions
import sync

SCHEMA_VERSION = 1

BUDGET_TOKENS = 8000
BYTES_PER_TOKEN = 4
FLOOR = 3
# Bytes held back from the fill for the budget block; each list also holds
# back its own `omitted` count (up to OMITTED_DIGITS) and `more` command.
RESERVE = 130
OMITTED_DIGITS = 5
# A why-not answer this recent is one of the owner's signals.
SIGNAL_DAYS = 14

SECTIONS = ("freshness", "in_flight", "blocked", "candidates", "signals", "gaps")

# An open PR's readiness, the first rule that holds, from the fields
# fleet_tabs.pull_requests has:
# - draft: `is_draft`;
# - ci_failing: `ci_state` is failure;
# - ready_to_merge: `ci_state` success and approved (`review_decision`), or a
#   bot's PR (`awaiting.author_is_bot`), as Ship's quick_win and own_approved;
# - review_queue: in the review queue (`awaiting.in_queue`: CI green,
#   co-reviewed, its milestone approved), waiting on the owner's review;
# - other: anything else, such as CI pending or out of the queue for a
#   missing co-review.
PR_READINESS = ("ready_to_merge", "review_queue", "ci_failing", "draft", "other")

# The lists, in the order they fill; the last truncates first.
# In-flight work the owner can act on now leads, then the owner's own signals
# and the mode's candidates, which answer the ask; drafts and `other` PRs,
# which wait on something else, come last but the unsized candidates.
FILL = (
    ("in_flight", "ready_to_merge"), ("in_flight", "review_queue"),
    ("in_flight", "waiting"), ("in_flight", "ci_failing"), ("signals", "items"),
    ("candidates", "items"), ("in_flight", "worktrees"), ("blocked", "blocked"),
    ("blocked", "blocking"), ("in_flight", "draft"), ("in_flight", "other"),
    ("candidates", "unsized"),
)  # fmt: skip

_RECORD = tuple(f for f in agent_query.BRIEF if f != "now")
_CMD = "dashboard.py"

Item = dict[str, Any]


def pr_readiness(p: dict[str, Any]) -> str:
    """One of PR_READINESS for a fleet_tabs.pull_requests() row."""
    awaiting = p.get("awaiting") or {}
    ci = p.get("ci_state")
    if p.get("is_draft"):
        return "draft"
    if ci == "failure":
        return "ci_failing"
    if ci == "success" and (
        p.get("review_decision") == "APPROVED" or awaiting.get("author_is_bot")
    ):
        return "ready_to_merge"
    if awaiting.get("in_queue"):
        return "review_queue"
    return "other"


def gaps(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """What this instance does not mirror, so the agent knows what it cannot
    see. repo-pr task files are not mirrored by any instance yet."""
    out = []
    if not sync.jira_enabled(cfg):
        out.append({"source": "jira", "what": "Jira tickets",
                    "why": "jira.enabled is false"})  # fmt: skip
    if not linear_issues.enabled(cfg):
        out.append({"source": "linear", "what": "Linear issues",
                    "why": "linear.enabled is false"})  # fmt: skip
    if not cfg["github"]["enabled"]:
        out.append({"source": "github", "what": "GitHub PRs and issues",
                    "why": "github.enabled is false"})  # fmt: skip
    elif not sync.fleet_enabled(cfg):
        out.append({"source": "github", "what": "GitHub issues and design docs",
                    "why": 'github.scope is not "owner"'})  # fmt: skip
    if not sessions.enabled(cfg):
        out.append({"source": "sessions", "what": "Claude Code sessions",
                    "why": "sessions are off"})  # fmt: skip
    out.append({"source": "repo-pr", "what": "repo-pr task files (dev_docs/tasks/)",
                "why": "not mirrored by any instance yet"})  # fmt: skip
    return out


def _freshness(conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> dict:
    interval_s = float(cfg["sync"]["interval_minutes"]) * 60
    found = freshness.sources(conn, cfg, now, interval_s, {})
    s = freshness.summary(found)
    return {
        "text": s.text, "warn": s.warn,
        "sources": [
            {k: v for k, v in (
                ("id", x.id), ("label", x.label), ("age", freshness.short_age(x.age_s)),
                ("detail", x.detail), ("warn", x.warn), ("asleep", x.asleep or None),
            ) if v is not None}
            for x in found
        ],
    }  # fmt: skip


def _record(item: Item) -> Item:
    return agent_query._record(item, list(_RECORD), {}, {}, sparse=True)


def _placing(r: fleet_now.Ranked) -> dict[str, Any]:
    """Where the mode put it: its tier, category and why; Ship adds its
    score and the terms that make it up."""
    out: dict[str, Any] = {"tier": r.tier[1], "category": r.category, "why": r.why}
    if r.mode == fleet_now.SHIP:
        out["score"] = r.total
        out["terms"] = [[name, value] for name, value, _ in r.terms if value]
    return out


def _ranked(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime, mode: str,
    ov: overlay.Overlay,
) -> list[fleet_now.Ranked]:  # fmt: skip
    """What `mode` ranks, in its order, as `view now --mode` builds it but
    uncapped: pins lead (a pinned item another mode does not rank comes from
    Ship), the ignored and the snoozed are left out."""
    own = fleet_now.mode_candidates(conn, cfg, now, mode)
    if mode != fleet_now.SHIP and ov.pinned_at:
        seen = {r.need.item for r in own}
        own += [
            r for r in fleet_now.candidates(conn, cfg, now)
            if ov.is_pinned(r.need.item) and r.need.item not in seen
        ]  # fmt: skip
    ranked, _ = fleet_now._visible(own, ov, now, None)
    if mode == fleet_now.SHIP:
        return fleet_now.order(ranked, ov)
    return fleet_now.MODE_ORDER[mode](ranked, ov)


def _scope_args(resolved: "list[agent_query.Scope] | None") -> str:
    if resolved is None:
        return "--scope all"
    return " ".join(f"--scope {s.id}" for s in resolved)


def _list(items: list[Item], more: str) -> dict[str, Any]:
    """A list section: every record, cut to the budget later (_fit)."""
    return {"total": len(items), "items": items, "_more": more}


def _records(items: list[Item], more: str) -> dict[str, Any]:
    return _list([_record(i) for i in items], more)


def _signals(items: list[Item], ov: overlay.Overlay, now: datetime) -> list[Item]:
    """Pinned first, in the owner's order; then the rest with a snooze, a
    note or a recent why-not, newest first."""
    since = now - timedelta(days=SIGNAL_DAYS)
    out = []
    for i in items:
        mark = dict(i["overlay"] or {})
        mark.pop("ignored", None)
        why = mark.get("why_not")
        at = score.parse_ts(why["at"]) if why else None
        if why and (at is None or at < since):
            del mark["why_not"]
        if mark:
            out.append((i, mark))
    # Newest first, then (a stable sort) the pins ahead in the owner's order.
    out.sort(key=lambda x: x[0]["updated"] or "", reverse=True)
    out.sort(key=lambda x: (
        not x[1].get("pinned"),
        ov.pin_rank((x[0]["source"], x[0]["key"])) if x[1].get("pinned") else (),
    ))  # fmt: skip
    return [
        {**{k: i[k] for k in agent_query.IDENTITY}, "title": i["title"], **mark}
        for i, mark in out
    ]


def brief_doc(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    *,
    scope: "list[str] | None" = None,
    mode: "str | None" = None,
    minutes: "int | None" = None,
    sections: "str | None" = None,
) -> dict[str, Any]:
    """The `brief` document. Raises agent_query.QueryError for a bad scope,
    mode, time box or section."""
    names = agent_query._csv(sections, SECTIONS, "--sections") or list(SECTIONS)
    mode = mode or fleet_now.SHIP
    if mode not in fleet_now.MODES:
        raise agent_query.QueryError(
            f"--mode takes one of {', '.join(fleet_now.MODES)}; not {mode!r}"
        )
    if minutes is not None and minutes <= 0:
        raise agent_query.QueryError("--minutes must be a positive number of minutes")
    m = agent_query.mirror(conn, cfg, now)
    every = agent_query.collect(conn, cfg, now, m)
    resolved = agent_query.resolve(agent_query._scopes(conn, every), scope)
    where = _scope_args(resolved)
    in_scope = [
        i for i in every
        if agent_query._is_open(i)
        and (resolved is None or any(s.match(i) for s in resolved))
    ]  # fmt: skip
    ov = overlay.load(conn)
    doc: dict[str, Any] = {
        "schema": SCHEMA_VERSION, "generated_at": agent_query._stamp(now),
        "scope": agent_query.ALL if resolved is None
        else [s.as_dict() for s in resolved],
        "mode": mode, "sections": [s for s in SECTIONS if s in names],
    }  # fmt: skip
    printed: set[tuple[str, str]] = set()

    if "freshness" in names:
        doc["freshness"] = _freshness(conn, cfg, now)

    if "in_flight" in names:
        by: dict[str, list[Item]] = {r: [] for r in PR_READINESS}
        for i in in_scope:
            if i["kind"] == "pr":
                by[pr_readiness(i["_pr_row"])].append(i)
        waiting = [i for i in in_scope if i["kind"] == "session" and i["_waiting"]]
        # A default-branch checkout is no work in flight unless it holds some.
        trees = [
            i for i in in_scope
            if i["kind"] == "worktree" and i["pr"] is None
            and (i["branch"] not in close_out.DEFAULT_BRANCHES
                 or i["state"]["name"] != "clean")
        ]  # fmt: skip
        doc["in_flight"] = {
            **{r: _records(by[r], f"items {where} --kind pr") for r in PR_READINESS},
            "waiting": _records(
                waiting, f"items {where} --kind session --only waiting"
            ),
            "worktrees": _records(trees, f"items {where} --kind worktree"),
        }
        for x in (*by.values(), waiting, trees):
            printed |= {(i["source"], i["key"]) for i in x}

    if "blocked" in names:
        doc["blocked"] = {
            "blocked": _records([i for i in in_scope if i["_blocked"]],
                                f"items {where} --only blocked"),
            "blocking": _records([i for i in in_scope if i["blocks"]],
                                 f"items {where} --fields blocks"),
        }  # fmt: skip

    if "candidates" in names:
        doc["candidates"] = _candidates(
            conn, cfg, now, mode, minutes, ov, every, resolved, printed, where
        )

    if "signals" in names:
        doc["signals"] = {
            "items": _list(_signals(in_scope, ov, now),
                           f"items {where} --fields overlay")
        }  # fmt: skip

    if "gaps" in names:
        doc["gaps"] = gaps(cfg)

    return _fit(doc)


def _candidates(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime, mode: str,
    minutes: "int | None", ov: overlay.Overlay, every: list[Item],
    resolved: "list[agent_query.Scope] | None", printed: set, where: str,
) -> dict[str, Any]:  # fmt: skip
    """The mode's ranked items in scope, each its brief record with the
    mode's placing as `now`. One in flight is only a stub: identity, title,
    size, placing and `in_flight: true`, which says it is in flight, not
    that its record printed: when the budget cut it from in_flight, that
    list's `more`, or `item <source:key>`, gives it. With `--minutes` (not Deep):
    the sized ones, in order, each kept while the sum of `est_min` stays
    within twice the box; then the unsized, apart."""
    by_key = {(i["source"], i["key"]): i for i in every}
    rows = []
    for r in _ranked(conn, cfg, now, mode, ov):
        item = by_key.get(r.need.item)
        if item is None or not agent_query._is_open(item):
            continue
        if resolved is not None and not any(s.match(item) for s in resolved):
            continue
        it = (item["source"], item["key"])
        if it in printed:
            rec = {k: item[k] for k in (*agent_query.IDENTITY, "title")}
            rec.update(
                in_flight=True, **({"size": item["size"]} if item["size"] else {})
            )
        else:
            rec = _record(item)
        rec["now"] = _placing(r)
        rows.append((item, rec))
    out: dict[str, Any] = {
        "order": "Now's " + mode + " order: a starting point to argue with,"
        " not the answer",
    }
    more = f"brief {where} --mode {mode} --sections candidates"
    if mode == fleet_now.DEEP or minutes is None:
        if minutes is not None:
            out["minutes"] = {"box": minutes, "ignored": "Deep keeps a WIP of one"}
        out["items"] = _list([rec for _, rec in rows], more)
    else:
        cap, total, sized, unsized, over = 2 * minutes, 0, [], [], 0
        for item, rec in rows:
            est = (item["size"] or {}).get("est_min")
            if est is None:
                unsized.append(rec)
            elif total + est <= cap:
                total += est
                sized.append(rec)
            else:
                over += 1
        out["minutes"] = {"box": minutes, "cap": cap, "sum": total, "over_cap": over}
        if over:
            # They are cut by the box, not the budget: list them without it.
            out["minutes"]["over_cap_more"] = f"{_CMD} {more}"
        out["items"] = _list(sized, more + f" --minutes {minutes}")
        out["unsized"] = _list(unsized, more + f" --minutes {minutes}")
    if mode == fleet_now.DEEP and rows:
        top = rows[0][0]
        out["top"] = agent_query.item_doc(
            conn, cfg, now, f"{top['source']}:{top['key']}"
        )
    return out


def _size(value: Any) -> int:
    return len(agent_query.dumps(value).encode()) - 1  # less the newline


def _fit(doc: dict[str, Any]) -> dict[str, Any]:
    """Cut each list to the budget, FILL order deciding who gives way, and
    note in each cut list what it left out and how to list it."""
    lists = [doc[s][k] for s, k in FILL if s in doc and k in doc[s]]
    # Room for what is added after the fill: the budget block, and each
    # list's `omitted` and `more` should the budget cut it.
    reserve = RESERVE + sum(
        len(f',"omitted":,"more":"{_CMD} {lst["_more"]}"'.encode()) + OMITTED_DIGITS
        for lst in lists
    )
    budget = BUDGET_TOKENS * BYTES_PER_TOKEN - reserve
    full = [lst["items"] for lst in lists]
    for lst in lists:
        lst["items"] = []
    used = _size({k: v for k, v in doc.items()})
    kept = [0] * len(lists)
    for floor in (FLOOR, None):
        for n, items in enumerate(full):
            stop = len(items) if floor is None else min(len(items), floor)
            while kept[n] < stop:
                cost = _size(items[kept[n]]) + 1
                if used + cost > budget:
                    break
                used += cost
                kept[n] += 1
    for n, lst in enumerate(lists):
        lst["items"] = full[n][: kept[n]]
        more = lst.pop("_more")
        left = len(full[n]) - kept[n]
        if left:
            lst["omitted"] = left
            lst["more"] = f"{_CMD} {more}"
    doc["budget"] = {
        "tokens": BUDGET_TOKENS,
        "measure": f"UTF-8 bytes of the printed JSON / {BYTES_PER_TOKEN}",
        "truncated": any(lst.get("omitted") for lst in lists),
    }
    # `used` counts its own digits too, so settle it in a few rounds. `over`
    # (only the parts never cut can make it true: freshness, gaps, Deep's
    # dossier) is set in each round, as its own length counts.
    b = doc["budget"]
    b["used"], b["over"] = 0, False
    for _ in range(4):
        b["used"] = -(-_size(doc) // BYTES_PER_TOKEN)
        b["over"] = b["used"] > BUDGET_TOKENS
    return doc
