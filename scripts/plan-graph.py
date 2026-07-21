#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Dependency-order graph for `/push-plan` §4.3 / §5.3 / §5b.3.

Replaces the hand-executed "order the tasks topologically" step those sections
describe: given a plan's tasks and their `is_blocked_by` edges, classify each
edge, topologically sort (Kahn's algorithm), and detect cycles — deterministic
instead of an agent hand-walking a 15-30 node graph.

Input is either a plan directory (scanned recursively for `*.md`, skipping the
`type: epic` container file, same shape as `commands/push-plan.md` §2) or a
JSON array on stdin: `[{slug, is_blocked_by, tracker_id, status}, ...]`.

`is_blocked_by` entries are classified per push-plan.md §4.3/§5.3/§5b.3:
  - `in-plan`     — names another task in this input; becomes an ordering edge.
  - `tracker-id`  — already matches the handler's id shape (--id-shape); not an
                    ordering edge, passed through as-is.
  - `unknown-slug`— matches neither a task in this input nor an id shape —
                    almost always a typo of an in-plan slug (§4.3). Warned on
                    stderr, NOT a failure.

The three id-shape regexes are lifted verbatim from push-plan.md §4.5:
  linear: /^[A-Z]+-\\d+$/        gh: /^(\\S*#)?\\d+$/        jira: /^[A-Z][A-Z0-9]*-\\d+$/

Fail-closed: a cycle, or malformed frontmatter/JSON, exits non-zero. A cycle
still prints the JSON doc first (with a non-empty `cycles` list) so the caller
can see exactly which slugs are involved, then dies. Zero exit means `order`
is safe to create in.

Usage:
    scripts/plan-graph.py <plan_dir> --id-shape linear
    cat tasks.json | scripts/plan-graph.py --id-shape gh --rewrite foo=#12
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

# Verbatim from commands/push-plan.md §4.3 (linear) / §5.3 (gh) / §5b.3 (jira).
ID_SHAPES = {
    "linear": re.compile(r"^[A-Z]+-\d+$"),
    "gh": re.compile(r"^(\S*#)?\d+$"),
    "jira": re.compile(r"^[A-Z][A-Z0-9]*-\d+$"),
}


def die(msg: str) -> None:
    print(f"plan-graph: {msg}", file=sys.stderr)
    sys.exit(1)


def split_frontmatter(path: Path):
    """Return the frontmatter dict, or None if there is none (not a task
    card). Dies on malformed YAML — fail-closed, per task-scan.py's contract."""
    text = path.read_text()
    if not text.startswith("---"):
        return None
    m = re.search(r"\n---\s*\n", text)
    if not m:
        return None
    try:
        return yaml.safe_load(text[3 : m.start()]) or {}
    except yaml.YAMLError as e:
        die(f"malformed frontmatter in {path}: {e}")


def as_list(v) -> list[str]:
    """is_blocked_by may be a single string or a list of strings (§4.3)."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def load_plan_dir(plan_dir: Path) -> list[dict]:
    if not plan_dir.is_dir():
        die(f"plan dir not found: {plan_dir}")
    tasks = []
    for path in sorted(plan_dir.rglob("*.md")):
        data = split_frontmatter(path)
        if data is None:
            continue  # no frontmatter — not a task card
        if not isinstance(data, dict):
            die(f"unparseable frontmatter in {path}: expected a mapping, got {data!r}")
        if data.get("type") == "epic":
            continue  # the container, never an ordering node (§2)
        tasks.append(
            {
                "slug": path.stem,
                "is_blocked_by": as_list(data.get("is_blocked_by")),
                "tracker_id": data.get("tracker_id"),
                "status": data.get("status"),
            }
        )
    return tasks


def load_stdin() -> list[dict]:
    try:
        raw = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        die(f"stdin is not valid JSON: {e}")
    if not isinstance(raw, list):
        die("stdin JSON must be a list of task objects")
    tasks = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
            die(f"malformed task entry on stdin: {entry!r}")
        tasks.append(
            {
                "slug": entry["slug"],
                "is_blocked_by": as_list(entry.get("is_blocked_by")),
                "tracker_id": entry.get("tracker_id"),
                "status": entry.get("status"),
            }
        )
    return tasks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "plan_dir",
        nargs="?",
        default=None,
        help="Plan directory to scan (dev_docs/tasks/<name>_plan/). Omit to "
        "read a JSON task list from stdin instead.",
    )
    ap.add_argument(
        "--id-shape",
        choices=sorted(ID_SHAPES),
        required=True,
        help="Handler whose already-an-id regex classifies is_blocked_by "
        "entries as tracker-id vs unknown-slug (§4.3/§5.3/§5b.3).",
    )
    ap.add_argument(
        "--rewrite",
        action="append",
        default=[],
        metavar="slug=id",
        help="Seed or override the slug->tracker-id map (e.g. an id a create "
        "call just resolved). Repeatable.",
    )
    args = ap.parse_args()

    tasks = load_plan_dir(Path(args.plan_dir)) if args.plan_dir else load_stdin()
    id_re = ID_SHAPES[args.id_shape]

    # Slug is the graph's identity key (edges, tracker_map, classified all key on
    # it), so two files sharing a stem across phase dirs would silently collapse
    # to one node and drop the other's edges — fail-closed instead (§ contract).
    seen: set[str] = set()
    dups = sorted({s for t in tasks if (s := t["slug"]) in seen or seen.add(s)})
    if dups:
        die(f"duplicate task slug(s): {', '.join(dups)} — rename so each is unique")

    slugs = {t["slug"] for t in tasks}

    tracker_map: dict[str, str] = {}
    for t in tasks:
        if t["tracker_id"]:
            tracker_map[t["slug"]] = t["tracker_id"]
    for pair in args.rewrite:
        if "=" not in pair:
            die(f"--rewrite '{pair}' must be slug=id")
        slug, _, tid = pair.partition("=")
        tracker_map[slug] = tid

    warnings: list[str] = []
    edges: list[tuple[str, str]] = []  # (blocker, blocked) — ordering edges only
    classified: dict[str, list[dict]] = {}

    for t in tasks:
        slug = t["slug"]
        entries = []
        for blocker in t["is_blocked_by"]:
            if blocker in slugs:
                kind = "in-plan"
                edges.append((blocker, slug))
            elif id_re.match(blocker):
                kind = "tracker-id"
            else:
                kind = "unknown-slug"
                warnings.append(
                    f"{slug}: is_blocked_by '{blocker}' matches no task in this "
                    f"plan and isn't a {args.id_shape} id — likely a typo of an "
                    "in-plan slug"
                )
            entries.append({"blocker": blocker, "kind": kind})
        classified[slug] = entries

    # Kahn's algorithm. Ties break alphabetically so the order is stable
    # across runs (no dependency on dict/filesystem iteration order).
    indegree = {s: 0 for s in slugs}
    adj: dict[str, list[str]] = {s: [] for s in slugs}
    for blocker, blocked in edges:
        adj[blocker].append(blocked)
        indegree[blocked] += 1

    ready = sorted(s for s in slugs if indegree[s] == 0)
    order: list[str] = []
    while ready:
        ready.sort()
        n = ready.pop(0)
        order.append(n)
        for m in adj[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                ready.append(m)

    # Any slug Kahn's algorithm never dequeued is stuck in (or downstream of)
    # a cycle — never a partial order, so cycles and order are disjoint.
    cycles = sorted(s for s in slugs if s not in order)

    for w in warnings:
        print(f"  ⚠ {w}", file=sys.stderr)

    result = {
        "order": order,
        "edges": [{"blocker": b, "blocked": d} for b, d in edges],
        "is_blocked_by": classified,
        "cycles": cycles,
        "tracker_map": tracker_map,
    }
    print(json.dumps(result, indent=2))

    if cycles:
        die(f"cycle detected involving: {', '.join(cycles)}")


if __name__ == "__main__":
    main()
