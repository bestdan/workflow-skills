"""The canonical ready-candidate gates and rank for Linear, shared by both
selection paths.

This is the **single source of truth** for `commands/handlers/linear-common.md`
→ "Ready-candidate selection". Change the rules here first, then update the
two callers: `linear-ready.py` (the GraphQL fast path, `issue["labels"]["nodes"]`
+ `issue["assignee"]["isMe"]` shape) and `linear-rank.py` (the MCP-floor entry
point, which adapts `list_issues`' flatter shape into this same one before
calling `gate()`/`rank_key()`).

**Gates.** Drop a candidate if any of these fail. Each has a fixed reason
string so a caller can report consistently:

| Gate                                                                               | Reason string              |
| ----------------------------------------------------------------------------------- | -------------------------- |
| `estimate` is `null`/missing                                                       | `no estimate set`          |
| `estimate >= <max>` (candidate's resolved per-project `max_estimate`, default `3`) | `estimate <N> >= <max>`    |
| Has label `auto-claimed`                                                           | `already auto-claimed`     |
| Has label `human-approval-requested`                                               | `human-approval-requested` |
| Has label `blocked`                                                                | `blocked`                  |
| `assignee` is set and is **not** the current Linear user                          | `assigned to <name>`       |

**The assignee gate is viewer-relative** — each path resolves "the current
Linear user" from its own credential (the GraphQL fast path's API key vs. the
MCP floor's connection). See `linear-common.md` "Ready-candidate selection" for
the full statement of that requirement; this module only applies the
comparison it is handed.

**Rank.** Sort remaining issues by Linear `priority`: urgent(1) -> high(2) ->
medium(3) -> low(4), then **none(0) last** (Linear stores "no priority" as
`0`, so a naive numeric ascending sort would wrongly put it first), then by
`updatedAt` ascending (oldest first — let aging cards bubble up).
"""


def gate(issue, max_estimate):
    """Return a drop reason string, or None if the issue survives the gates."""
    estimate = issue.get("estimate")
    if estimate is None:
        return "no estimate set"
    if estimate >= max_estimate:
        return f"estimate {estimate} >= {max_estimate}"
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


def rank_key(candidate):
    priority = candidate["priority"] or 0
    return (priority if priority != 0 else float("inf"), candidate["_updatedAt"])
