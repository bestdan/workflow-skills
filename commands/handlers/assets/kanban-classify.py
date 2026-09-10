#!/usr/bin/env python3
"""Shared kanban classification + ordering for the three `/list-tasks` list
sections: `linear-list.md` step 4, `gh-issue.md`'s `## List` step 3, and
`jira.md`'s `## List` step 3. One tested function replaces three hand-walked
copies of the same section table.

**Render order (fixed, all three trackers):**

  new -> needs_refinement -> ready -> in_progress -> blocked -> needs_review -> done

**Precedence (the canonical statement — the three handlers link here instead
of restating it):** when an issue could match more than one section's rule,
prefer the more actionable signal in this order:

  blocked > needs_review > in_progress > ready > needs_refinement

`classify()` applies the rules in exactly that order per tracker, so a
collision always resolves the same way regardless of which tracker produced
the row. `done` (and `new`) sit outside this precedence entirely: a closed
gh-issue or a `statusCategory: done` jira issue is `done` even if it still
carries a stale `blocked`/`needs-review` label (labels aren't cleared on
close), so `classify()` checks the terminal category **before** any
label-driven rule for those two trackers. (Linear's own state-type partition
makes only the blocked-vs-needs_review collision reachable in practice;
gh-issue and jira can also collide on the label-driven sections. The three
handler tables were reconciled when this helper replaced them: the per-tracker
field map below carries their known differences, and
`scripts/test_kanban_classify.py` pins each tracker's map.)

**Input** (stdin): a JSON array of rows, each:

  id            str            — the issue's display identifier
  category      str            — the tracker's native workflow bucket (below)
  labels        list[str]      — label names present on the issue
  has_open_pr   bool, optional — Linear-only best-effort needs_review signal
  priority      per-tracker    — used only for the within-section sort (below)
  sort_date     str, optional  — ISO-8601 tie-break date (below)

`category` domains, one per `--tracker`:

  linear    "backlog" | "unstarted" | "started" | "completed"     (state type)
  gh-issue  "0_untriaged" | "1_needs_refinement" | "2_ready" |
            "3_started" | "4_needs_review" | "closed" | "" (no status label)
  jira      "new" | "indeterminate" | "done"                      (statusCategory)

`priority` domains, one per `--tracker` (used only for sort — the None-last
rule lives per tracker below, so a value outside the domain, or missing, sorts
last):

  linear    int 0-4 (0 = none) — fed straight into `_linear_rank.rank_key()`
  gh-issue  int 0-3, or null
  jira      one of "urgent" | "high" | "medium" | "low", or null

`sort_date` is the tie-break: Linear's own rank rule sorts `updatedAt`
ascending (deliberately, to surface stale cards — see `_linear_rank.py`);
gh-issue and jira sort `createdAt`/`created` ascending. Each tracker keeps its
own field; that is a tracker fact, not drift.

Every other key on a row is passed through unchanged onto the matching output
row (mirroring `linear-rank.py`'s own contract) — so the caller can carry
`title`, `assignee`, `estimate` and any other card-rendering field straight
through with no reshaping; this module only reads the keys named above.

**Output** (stdout): one JSON object:

  {"sections": {"new": [...], ..., "done": [...]}, "unmatched": ["<id>", ...]}

All seven section keys are always present (possibly empty); the caller
decides whether to omit an empty section at render time. `unmatched` lists
the ids of rows whose category/labels matched none of the tracker's rules.

Usage:
  <fetch + shape rows in prose> | python3 kanban-classify.py --tracker gh-issue
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _linear_rank import rank_key as _linear_rank_key  # noqa: E402

SECTION_ORDER = [
    "new",
    "needs_refinement",
    "ready",
    "in_progress",
    "blocked",
    "needs_review",
    "done",
]

_JIRA_PRIORITY_RANK = {"urgent": 1, "high": 2, "medium": 3, "low": 4}


def classify(row, tracker):
    """Return the section name for one row, or None if no rule matches."""
    labels = set(row.get("labels") or [])
    category = row.get("category")

    if tracker == "linear":
        if category == "backlog":
            return "needs_refinement" if "human-approval-requested" in labels else "new"
        if category == "unstarted":
            return "ready"
        if category == "started":
            if "blocked" in labels:
                return "blocked"
            if row.get("has_open_pr"):
                return "needs_review"
            return "in_progress"
        if category == "completed":
            return "done"
        return None

    if tracker == "gh-issue":
        if category == "closed":
            return "done"
        if "blocked" in labels:
            return "blocked"
        if category == "4_needs_review":
            return "needs_review"
        if category == "3_started":
            return "in_progress"
        if category == "2_ready":
            return "ready"
        if category == "1_needs_refinement":
            return "needs_refinement"
        if category in ("0_untriaged", "", None):
            return "new"
        return None

    if tracker == "jira":
        if category == "done":
            return "done"
        if category == "indeterminate":
            if "blocked" in labels:
                return "blocked"
            if "needs-review" in labels:
                return "needs_review"
            return "in_progress"
        if category == "new":
            if "auto-eligible" in labels:
                return "ready"
            if "human-approval-requested" in labels:
                return "needs_refinement"
            return "new"
        return None

    raise ValueError(f"unknown tracker: {tracker}")


def _sort_key(row, tracker):
    priority = row.get("priority")
    date = row.get("sort_date") or ""

    if tracker == "linear":
        return _linear_rank_key({"priority": priority, "_updatedAt": date})

    if tracker == "gh-issue":
        rank = priority if isinstance(priority, int) else float("inf")
        return (rank, date)

    if tracker == "jira":
        rank = _JIRA_PRIORITY_RANK.get(priority, float("inf"))
        return (rank, date)

    raise ValueError(f"unknown tracker: {tracker}")


def main():
    ap = argparse.ArgumentParser(
        description="Classify and order kanban rows for /list-tasks."
    )
    ap.add_argument("--tracker", required=True, choices=["linear", "gh-issue", "jira"])
    args = ap.parse_args()

    rows = json.load(sys.stdin)

    sections: dict[str, list] = {name: [] for name in SECTION_ORDER}
    unmatched = []
    for row in rows:
        section = classify(row, args.tracker)
        if section is None:
            unmatched.append(row.get("id"))
            continue
        sections[section].append(row)

    for name in SECTION_ORDER:
        sections[name].sort(key=lambda r: _sort_key(r, args.tracker))

    counts = ", ".join(f"{name}={len(sections[name])}" for name in SECTION_ORDER)
    print(f"{counts}, unmatched={len(unmatched)}", file=sys.stderr)

    print(json.dumps({"sections": sections, "unmatched": unmatched}))


if __name__ == "__main__":
    main()
