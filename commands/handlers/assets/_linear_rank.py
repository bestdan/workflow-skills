"""The canonical ready-candidate gates and rank for Linear, shared by both
selection paths.

This is the **single source of truth** for `commands/handlers/linear-common.md`
→ "Ready-candidate selection". Change the rules here first, then update the
two callers: `linear-ready.py` (the GraphQL fast path, `issue["labels"]["nodes"]`
+ `issue["assignee"]["isMe"]` shape) and `linear-rank.py` (the MCP-floor entry
point, which adapts `list_issues`' flatter shape into this same one before
calling `gate()`/`rank_key()`).

**Gates.** Drop a candidate if any of these fail, checked in table order,
so the reason reported is the first that fails. Each has a fixed reason
string so a caller can report consistently:

| Gate                                                                               | Reason string              |
| ----------------------------------------------------------------------------------- | -------------------------- |
| `estimate` is `null`/missing                                                       | `no estimate set`          |
| `estimate >= <max>` (candidate's resolved per-project `max_estimate`, default `3`) | `estimate <N> >= <max>`    |
| A native `blockedBy` issue is not in a `completed`-type state                      | `waiting on <id>[, <id>…]` |
| Has label `auto-claimed`                                                           | `already auto-claimed`     |
| Has label `human-approval-requested`                                               | `human-approval-requested` |
| Has label `blocked`                                                                | `blocked`                  |
| `assignee` is set and is **not** the current Linear user                          | `assigned to <name>`       |

**The assignee gate is viewer-relative** — each path resolves "the current
Linear user" from its own credential (the GraphQL fast path's API key vs. the
MCP floor's connection). See `linear-common.md` "Ready-candidate selection" for
the full statement of that requirement; this module only applies the
comparison it is handed.

**The blocker gate runs only when the caller supplies `blockedBy`** — a list of
`{"identifier", "stateType"}`, one per native "is blocked by" relation. Only
`completed` satisfies a blocker: a `canceled` one blocks until a human removes
the link (`linear-reoptimize.md` Dimension 1), and an entry with no known
`stateType` blocks too, so a relation the caller could not resolve never reads
as met. A deleted blocker has no relation left, so it never appears. The
GraphQL fast path always supplies the list; the MCP floor's `list_issues`
returns no relations, so it supplies them per candidate at pre-flight
(`linear-claim.md` → "Pre-flight", step 6) rather than for the whole set.

**One caller exempts blockers inside the set.** `/auto-pilot`'s Linear
`list_ready` stacks a dependent's branch on its in-run parent instead of
waiting for the parent to complete, so for it a blocker that is itself a
surviving candidate is a stack edge, not a hold. `stack_in_set()` computes
which blockers qualify; the `--stack-in-set` flag of `linear-ready.py` (fast
path) and `linear-rank.py` (MCP floor) applies it. Every
other caller leaves it off.

**Rank.** Sort remaining issues by Linear `priority`: urgent(1) -> high(2) ->
medium(3) -> low(4), then **none(0) last** (Linear stores "no priority" as
`0`, so a naive numeric ascending sort would wrongly put it first), then by
`updatedAt` ascending (oldest first — let aging cards bubble up).
"""


def gate(issue, max_estimate, satisfied=frozenset()):
    """Return a drop reason string, or None if the issue survives the gates.

    `satisfied` names blockers to count as met whatever their state — the
    in-set exemption `stack_in_set()` computes.
    """
    estimate = issue.get("estimate")
    if estimate is None:
        return "no estimate set"
    if estimate >= max_estimate:
        return f"estimate {estimate} >= {max_estimate}"
    # Ahead of `human-approval-requested`: that is the one reason a direct
    # pick can override, so it must not mask a blocker, which it cannot.
    waiting = [b for b in unmet_blockers(issue.get("blockedBy")) if b not in satisfied]
    if waiting:
        return "waiting on " + ", ".join(waiting)
    label_names = {n["name"] for n in issue["labels"]["nodes"]}
    if "auto-claimed" in label_names:
        return "already auto-claimed"
    if "human-approval-requested" in label_names:
        return "human-approval-requested"
    if "blocked" in label_names:
        return "blocked"
    assignee = issue.get("assignee")
    if assignee and not assignee.get("isMe"):
        who = assignee.get("displayName") or assignee.get("id") or "unknown"
        return f"assigned to {who}"
    return None


def unmet_blockers(blocked_by):
    """Identifiers of the blockers that are not in a `completed`-type state."""
    return [
        b.get("identifier") or "unknown"
        for b in blocked_by or []
        if b.get("stateType") != "completed"
    ]


def rank_key(candidate):
    priority = candidate["priority"] or 0
    return (priority if priority != 0 else float("inf"), candidate["_updatedAt"])


def stack_in_set(pairs):
    """Identifiers that survive every gate when a blocker inside the set counts
    as met. `pairs` is `[(issue, max_estimate), ...]`, each issue carrying
    `identifier` and `blockedBy`.

    Built up from nothing: each round admits the issues whose every open
    blocker is already admitted, until a round admits nothing new. So an issue
    is admitted only when a chain of admitted blockers leads back to an issue
    that is ready on its own. A chain whose root waits on something outside
    the set drops whole, and so does a cycle of blockers, which has no such
    root — starting from everything and removing would keep a cycle, each
    member exempting the other.
    """
    ids: set[str] = set()
    while True:
        admitted = {
            issue["identifier"]
            for issue, max_estimate in pairs
            if gate(issue, max_estimate, ids) is None
        }
        if admitted == ids:
            return ids
        ids = admitted
