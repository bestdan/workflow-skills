#!/usr/bin/env python3
"""Read the NATIVE `blocked_by` graph for a scope and report what is wrong with it.

The analysis half of `/reoptimize-tasks` on the gh-issue handler. Every finding
below is derived from the real dependency edges GitHub stores, not from the
`Blocked by: #<n>` body footer — which is the whole point of the script. Before
it existed, `gh-issue-reoptimize.md` parsed the footer as if it were the graph,
so a dependency carrying a real edge and no footer read as missing, and a footer
someone typed by hand read as a dependency that nothing enforces.

**The footer is an echo, never a source.** `/push-plan` §5 writes it as a
human-readable copy of an edge it just drew, and this script reads it in exactly
one place — `footer_only`, the migration input that says which hand-written
footers still have no edge behind them. Nothing else here consults it. The
inverse, `edge_only`, is the echo that went missing; repairing it is a body edit
that follows an edge, never a substitute for one.

Findings, all over the native graph:

- `cycles` — strongly connected components of size > 1, plus any self-edge.
  Members only: resolving a cycle is a human decision, same as Linear.
- `stale_edges` — the blocker is closed `not_planned`, so the edge blocks
  forever. Safe to remove once approved.
- `satisfied_edges` — the blocker is closed `completed`. Not a bug; GitHub
  already stops counting it. Reported for optional cleanup, never auto-removed.
- `inversions` — an open blocker less urgent than the open issue it blocks,
  ranked by `prio:`'s order in labels.yml (earlier is more urgent, absent is
  least). Sweeps every edge, not a sample.
- `concurrent` — a blocker and its dependent both at `status:3_started`. They
  cannot legitimately both be mid-build.

Reads only. Every call is a GET: `gh issue list`, `gh issue view`, and
`gh api .../dependencies/blocked_by`. Writes belong to `gh-issue-deps.py`
(edges) and `gh-issue-state.py` (labels); this script never calls either.

`blocked_by` is read with `--paginate --slurp` for the reason
`gh-issue-ready.py` gives: a bare read stops at 30 entries, so an edge past page
one would be invisible — and an invisible edge is a cycle this script would
report as absent.

**Blockers outside the scope are backfilled as analysis-only nodes.** A
milestone-scoped run whose issue is blocked by one in another milestone has no
`state` for that blocker otherwise, so the stale/satisfied check would silently
skip the edge. Backfilled nodes carry `"in_scope": false` and must never be
mutated — §Apply in `gh-issue-reoptimize.md` edits in-scope issues only.

**The backfill is transitive, deliberately: it closes the reachable graph.** A
backfilled node's own blockers are fetched too, because a cycle can leave the
scope and re-enter it — `1 -> 9 -> 5 -> 1` across three milestones is invisible
at depth 1, and reporting it as absent is the same silent-wrong-answer the
pagination rule above refuses. The cost is bounded by the reachable closure
(`pending` only grows for numbers not already held), whose worst case is the
whole repo — which is what the `team` scope fetches anyway. The consequence is
that `edges` and `cycles` span the closure while every **actionable** finding is
filtered back to in-scope dependents; see `analyse()`.

A cloud routine has no `gh`, so this path is LOCAL ONLY, same as every other
gh-issue asset.

Usage:
  python3 gh-issue-graph.py --repo owner/name
  python3 gh-issue-graph.py --repo owner/name --milestone "Phase 3" --json
  python3 gh-issue-graph.py --repo owner/name --issue 12 --issue 11 --json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import (  # noqa: E402
    DEFAULT_LABELS_FILE,
    load_vocabulary,
)

# The fields every finding below is derived from. `stateReason` is what separates
# a stale edge from a satisfied one, so a caller that drops it gets a graph that
# cannot tell "blocks forever" from "already done".
ISSUE_FIELDS = "number,title,body,state,stateReason,labels,milestone,createdAt"

# `Blocked by: #12, #13` — the echo `/push-plan` §5 writes and `gh-issue.md`
# step 2 renders. `Blocked by task: <slug>` is deliberately NOT matched: a slug
# names a plan task that never became an issue, so there is no edge to migrate
# it to. Anchored to a line start so a sentence mentioning the phrase in prose
# is not mistaken for the footer.
FOOTER_LINE = re.compile(
    r"^[ \t>]*Blocked by:(?P<refs>.*)$", re.IGNORECASE | re.MULTILINE
)
FOOTER_REF = re.compile(r"#(\d+)")

STARTED_STATUS_VALUE = "3_started"


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def list_issues(repo, milestone, scope_labels, limit):
    """Every issue in scope, open AND closed.

    Closed issues are not noise here: they carry the `stateReason` that decides
    whether an edge pointing at one is stale or satisfied. Dropping them would
    make every such edge unclassifiable.
    """
    args = ["issue", "list", "--repo", repo, "--state", "all"]
    if milestone:
        args += ["--milestone", milestone]
    for scope in scope_labels:
        args += ["--label", scope]
    args += ["--json", ISSUE_FIELDS, "--limit", str(limit)]
    code, out, err = run_gh(args)
    if code != 0:
        raise SystemExit(
            f"gh issue list failed for {repo}: {err.strip() or out.strip()}"
        )
    return json.loads(out or "[]")


def view_issue(repo, number):
    """One issue, for backfilling a blocker that fell outside the scope."""
    code, out, err = run_gh(
        ["issue", "view", str(number), "--repo", repo, "--json", ISSUE_FIELDS]
    )
    if code != 0:
        raise SystemExit(
            f"gh issue view {repo}#{number} failed: {err.strip() or out.strip()}"
        )
    return json.loads(out or "{}")


def blocked_by(repo, number):
    """Numbers on this issue's native `blocked_by` list.

    `--paginate --slurp` for `gh-issue-ready.py`'s reason: a bare read returns
    the first 30 edges, and an edge this script cannot see is a cycle it will
    report as absent.
    """
    code, out, err = run_gh(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/issues/{number}/dependencies/blocked_by",
        ]
    )
    if code != 0:
        raise SystemExit(
            f"gh api blocked_by failed for {repo}#{number}: "
            f"{err.strip() or out.strip()}"
        )
    pages = json.loads(out or "[]")
    return [entry["number"] for page in pages for entry in page]


def label_names(issue):
    return [label["name"] for label in issue.get("labels") or []]


def group_value(names, group, vocabulary):
    """The issue's value in a labels.yml group, or None.

    Reads the VOCABULARY, not the `<group>:` prefix. A hand-typed `prio:urgent`
    starts with `prio:` and is not a priority — the same trap task 7's rule 2
    fell into, recorded in `gh-issue-reconcile.md`.
    """
    allowed = vocabulary.get(group, [])
    for name in names:
        prefix, sep, value = name.partition(":")
        if sep and prefix == group and value in allowed:
            return value
    return None


def prio_rank(value, vocabulary):
    """Urgency as a sortable int — lower is more urgent, absent is last.

    The order comes from labels.yml's `prio:` list rather than from parsing the
    value as a number, so a repo that renames its priorities keeps working and a
    reorder there is honoured here without a second edit.
    """
    order = vocabulary.get("prio", [])
    return order.index(value) if value in order else len(order)


def build_node(issue, vocabulary, in_scope):
    names = label_names(issue)
    milestone = issue.get("milestone") or {}
    return {
        "number": issue["number"],
        "title": issue.get("title", ""),
        # Carried rather than dropped: Dimensions 1, 2 and 4 all parse bodies for
        # dependency prose, so the caller needs every one of these. Dropping the
        # field would not save context — it would buy a second download of what
        # this call already fetched.
        "body": issue.get("body") or "",
        "state": (issue.get("state") or "").lower(),
        "state_reason": (issue.get("stateReason") or "").lower() or None,
        "milestone": milestone.get("title"),
        "prio": group_value(names, "prio", vocabulary),
        "est": group_value(names, "est", vocabulary),
        "status": group_value(names, "status", vocabulary),
        "created_at": issue.get("createdAt"),
        "in_scope": in_scope,
        "footer_blockers": footer_blockers(issue.get("body") or "", issue["number"]),
    }


def footer_blockers(body, self_number):
    """Issue numbers named by `Blocked by: #<n>` footer lines in a body.

    Read in exactly one place — the `footer_only` migration input. A body
    restating its own number never yields a self-block.
    """
    found = []
    for match in FOOTER_LINE.finditer(body):
        for ref in FOOTER_REF.findall(match.group("refs")):
            number = int(ref)
            if number != self_number and number not in found:
                found.append(number)
    return found


def strongly_connected(numbers, edges):
    """Tarjan's SCC over the native graph, iterative so a deep chain cannot
    blow the recursion limit.

    `edges` maps a blocked issue to the set of issues blocking it, so a cycle
    here is a genuine deadlock: each member waits on the next.
    """
    index = {}
    low = {}
    on_stack = {}
    stack = []
    components = []
    counter = 0

    for root in numbers:
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True

        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack[child] = True
                    work.append((child, iter(sorted(edges.get(child, ())))))
                    advanced = True
                    break
                if on_stack.get(child):
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                components.append(sorted(component))
    return components


def find_cycles(numbers, edges):
    """SCCs of size > 1, plus any self-edge.

    Members only, never a resolution: breaking a cycle means deciding which
    dependency is wrong, which is a human call — the same stance
    `linear-reoptimize.md` takes.
    """
    cycles = [c for c in strongly_connected(numbers, edges) if len(c) > 1]
    cycles += [[n] for n in sorted(numbers) if n in edges.get(n, set())]
    return sorted(cycles)


def analyse(repo, labels_file, milestone, scope_labels, limit, issue_numbers):
    vocabulary, _colors = load_vocabulary(labels_file)

    if issue_numbers:
        # Issue-scoped: the caller already chose the set, so re-deriving it
        # through a bounded list query could silently drop one — the reason
        # `gh-issue-ready.py --issue` exists.
        raw = [view_issue(repo, n) for n in issue_numbers]
        truncated = False
    else:
        raw = list_issues(repo, milestone, scope_labels, limit)
        truncated = len(raw) == limit

    nodes = {}
    for issue in raw:
        nodes[issue["number"]] = build_node(issue, vocabulary, in_scope=True)

    edges = {}
    pending = list(nodes)
    while pending:
        number = pending.pop(0)
        blockers = set(blocked_by(repo, number))
        edges[number] = blockers
        for blocker in sorted(blockers):
            if blocker not in nodes:
                # Analysis-only. Without it a cross-milestone edge has no
                # `state` and its stale/satisfied verdict is unknowable.
                nodes[blocker] = build_node(
                    view_issue(repo, blocker), vocabulary, in_scope=False
                )
                pending.append(blocker)

    flat = [
        {"blocked": blocked, "blocker": blocker}
        for blocked in sorted(edges)
        for blocker in sorted(edges[blocked])
    ]

    stale, satisfied, inversions, concurrent = [], [], [], []
    for edge in flat:
        dependent = nodes[edge["blocked"]]
        blocker = nodes[edge["blocker"]]
        if not dependent["in_scope"]:
            # Closing the graph pulls in edges that live entirely outside the
            # scope. They are why a cross-scope cycle is visible, and they are
            # not this run's to repair — every fix below mutates the DEPENDENT,
            # which §Apply's hard rule forbids for an out-of-scope issue.
            continue
        if blocker["state"] == "closed":
            if blocker["state_reason"] == "not_planned":
                stale.append(edge)
            else:
                # `completed`, or a close with no reason recorded — GitHub
                # already treats the dependency as met either way.
                satisfied.append(edge)
            continue
        if dependent["state"] != "open":
            continue
        dep_rank = prio_rank(dependent["prio"], vocabulary)
        blk_rank = prio_rank(blocker["prio"], vocabulary)
        if blk_rank > dep_rank:
            inversions.append(
                {
                    **edge,
                    "blocked_prio": dependent["prio"],
                    "blocker_prio": blocker["prio"],
                    "raise_blocker_to": dependent["prio"],
                }
            )
        if (
            dependent["status"] == STARTED_STATUS_VALUE
            and blocker["status"] == STARTED_STATUS_VALUE
        ):
            concurrent.append(edge)

    native = {(e["blocked"], e["blocker"]) for e in flat}
    footer_only, edge_only = [], []
    for number in sorted(n for n, node in nodes.items() if node["in_scope"]):
        for blocker in nodes[number]["footer_blockers"]:
            if (number, blocker) not in native:
                footer_only.append({"blocked": number, "blocker": blocker})
    for edge in flat:
        if not nodes[edge["blocked"]]["in_scope"]:
            continue
        if edge["blocker"] not in nodes[edge["blocked"]]["footer_blockers"]:
            edge_only.append(edge)

    return {
        "repo": repo,
        "scope": {
            "milestone": milestone,
            "labels": list(scope_labels),
            "issues": list(issue_numbers),
        },
        "checked": sum(1 for node in nodes.values() if node["in_scope"]),
        "backfilled": sorted(n for n, node in nodes.items() if not node["in_scope"]),
        "truncated": truncated,
        "nodes": [nodes[n] for n in sorted(nodes)],
        "edges": flat,
        "cycles": find_cycles(sorted(nodes), edges),
        "stale_edges": stale,
        "satisfied_edges": satisfied,
        "inversions": inversions,
        "concurrent": concurrent,
        "footer_only": footer_only,
        "edge_only": edge_only,
    }


def report(result):
    print(
        f"{result['repo']}: {result['checked']} issue(s) in scope, "
        f"{len(result['edges'])} native blocked_by edge(s)"
    )
    if result["backfilled"]:
        refs = ", ".join(f"#{n}" for n in result["backfilled"])
        print(f"  backfilled out-of-scope blockers (analysis only): {refs}")
    if result["truncated"]:
        print(
            "  WARNING: the list came back exactly at --limit, so the scope may be "
            "truncated and an edge outside it would read as absent"
        )

    def section(title, rows, render):
        print(f"\n{title} ({len(rows)}):")
        for row in rows:
            print(f"  {render(row)}")

    section(
        "Cycles — human decision, never auto-resolved",
        result["cycles"],
        lambda c: " -> ".join(f"#{n}" for n in c) + " -> ...",
    )
    section(
        "Stale edges — blocker closed not_planned, blocks forever",
        result["stale_edges"],
        lambda e: f"#{e['blocked']} blocked_by #{e['blocker']}",
    )
    section(
        "Satisfied edges — blocker closed completed, optional cleanup",
        result["satisfied_edges"],
        lambda e: f"#{e['blocked']} blocked_by #{e['blocker']}",
    )
    section(
        "Priority inversions — blocker less urgent than what it blocks",
        result["inversions"],
        lambda e: (
            f"#{e['blocker']} (prio:{e['blocker_prio'] or 'none'}) blocks "
            f"#{e['blocked']} (prio:{e['blocked_prio'] or 'none'}) — "
            f"raise blocker to prio:{e['raise_blocker_to'] or 'none'}"
        ),
    )
    section(
        "Both in flight — blocker and dependent at status:3_started",
        result["concurrent"],
        lambda e: f"#{e['blocked']} and its blocker #{e['blocker']}",
    )
    section(
        "Footer without an edge — migrate to a native edge",
        result["footer_only"],
        lambda e: f"#{e['blocked']} says `Blocked by: #{e['blocker']}` with no edge",
    )
    section(
        "Edge without a footer — echo missing from the body",
        result["edge_only"],
        lambda e: f"#{e['blocked']} blocked_by #{e['blocker']}",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--milestone", help="narrow to this milestone (project scope)")
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        dest="scope_labels",
        metavar="LABEL",
        help="narrow to issues also carrying this label; repeatable (AND)",
    )
    parser.add_argument(
        "--issue",
        action="append",
        type=int,
        default=[],
        dest="issue_numbers",
        metavar="N",
        help=(
            "analyse exactly these issue numbers instead of listing a scope; "
            "repeatable; ignores --milestone/--label/--limit"
        ),
    )
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = analyse(
        args.repo,
        args.labels_file,
        args.milestone,
        args.scope_labels,
        args.limit,
        args.issue_numbers,
    )
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
