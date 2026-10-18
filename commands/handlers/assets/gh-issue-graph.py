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
- `order` — present only with `--sort`: a topological ordering of the open,
  in-scope nodes, ranked within topo constraints by `prio_rank` (lower is more
  urgent, absent is last), then a node carrying `est:<n>` before one that
  doesn't (an unestimated node never wins on `est`, which is
  `gh-issue-reoptimize.md` Dimension 3's "omit the tie-break otherwise"),
  smaller `est:<n>` between two nodes that both carry it, then older
  `createdAt` first. This is Dimension 3's re-order step, code-backed instead
  of hand-walked. `--edges <file>` folds in a JSON list of
  `{"blocked", "blocker"}` proposed edges (Dimension 1-2's approved findings)
  before sorting; an edge naming a node outside the sortable set is ignored. A
  node inside a cycle never dequeues and is left out of `order` — `cycles`
  above already reports it for the human decision `find_cycles` exists to
  defer.

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
  python3 gh-issue-graph.py --repo owner/name --sort --edges approved.json --json
"""

import argparse
import functools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _body_refs import parse as parse_body_refs  # noqa: E402
from _labels import (  # noqa: E402
    DEFAULT_LABELS_FILE,
    load_vocabulary,
)

# The fields every finding below is derived from. `stateReason` is what separates
# a stale edge from a satisfied one, so a caller that drops it gets a graph that
# cannot tell "blocks forever" from "already done".
ISSUE_FIELDS = "number,title,body,state,stateReason,labels,milestone,createdAt"

# `_body_refs.parse()`'s `id_pattern` for this handler: a bare `#<n>` mention,
# never a repo-qualified `owner/repo#<n>` one — the negative lookbehind refuses
# to match when `#` is immediately preceded by a word character, `.`, `/` or
# `-`, which is exactly the shape a qualifier takes. A cross-repo number is not
# a local issue, so it is excluded from body_references entirely rather than
# resolved to the wrong local id.
BODY_REF_ID_PATTERN = re.compile(r"(?<![\w./-])#(?P<num>\d+)\b")

# `Blocked by: #12, #13` — the echo `/push-plan` §5 writes and `gh-issue.md`
# step 2 renders. `Blocked by task: <slug>` is deliberately NOT matched: a slug
# names a plan task that never became an issue, so there is no edge to migrate
# it to. Anchored to a line start so a sentence mentioning the phrase in prose
# is not mistaken for the footer.
# A leading `>` is NOT allowed: a generated footer is never quoted, so
# `> Blocked by: #42` is someone quoting a footer in prose — a plan doc, a
# review comment pasted into a body. Migrating that would propose a dependency
# from a quotation.
FOOTER_LINE = re.compile(
    r"^[ \t]*Blocked by:(?P<refs>.*)$", re.IGNORECASE | re.MULTILINE
)
# Qualified refs are matched so they can be REJECTED, not so they can be
# stripped: `other/repo#42` reduced to `#42` names a different, real issue in
# this repo, and `gh-issue-deps.py` refuses cross-repo edges precisely so that
# cannot happen. Matching only `#(\d+)` would smuggle one past it.
FOOTER_REF = re.compile(r"(?P<repo>[\w.-]+/[\w.-]+)?#(?P<number>\d+)")

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


def build_node(issue, vocabulary, in_scope, repo):
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
        # `auto` is carried for the same reason `body` is: the priority-inversion
        # repair writes through `gh-issue-state.py`, which takes the COMPLETE
        # managed set and refuses an open issue that is not carrying exactly one
        # `auto:` rung. Without this the caller cannot build a legal write.
        "auto": group_value(names, "auto", vocabulary),
        "created_at": issue.get("createdAt"),
        "in_scope": in_scope,
        "footer_blockers": footer_blockers(
            issue.get("body") or "", issue["number"], repo
        ),
        # Every dependency-phrase reference the body makes, direction- and
        # strength-classified by the shared `_body_refs` table — the fixed
        # phrase list `gh-issue-reoptimize.md` Dimensions 1-2 used to hand-walk.
        "body_references": parse_body_refs(
            issue.get("body") or "", str(issue["number"]), BODY_REF_ID_PATTERN
        ),
    }


def footer_blockers(body, self_number, repo):
    """Issue numbers named by `Blocked by: #<n>` footer lines in a body.

    Read in exactly one place — the `footer_only` migration input. A body
    restating its own number never yields a self-block, and a reference
    qualified with a DIFFERENT repo is dropped rather than localised: this path
    feeds edge creation, and `#42` in another repo is not `#42` in this one.
    A reference qualified with THIS repo is kept — that is the shape
    `/push-plan` writes when `gh-issue.repo` is configured.
    """
    found = []
    for match in FOOTER_LINE.finditer(body):
        for ref in FOOTER_REF.finditer(match.group("refs")):
            named = ref.group("repo")
            if named and named != repo:
                continue
            number = int(ref.group("number"))
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


def compare_nodes(a, b, vocabulary):
    """Ranking comparator for `--sort`'s tie-break, in rank order.

    `prio_rank` first (lower is more urgent). Then smaller `est:<n>` — but
    only between two nodes that BOTH carry the label; a node with no `est:`
    ranks after every node that has one, which is what `gh-issue-reoptimize.md`
    Dimension 3 means by "omit the tie-break otherwise" (an unestimated issue
    never wins ON est, so it falls out of that comparison rather than being
    treated as a value). Then older `createdAt` first (let aging issues bubble
    up, the same convention `gh-issue-claim.md`'s Rank step uses). Node number
    last, so the comparator is a total order and the sort is deterministic.

    The "only between two nodes that both carry it" rule is a fixed group
    split (has-`est` before lacks-`est`), not a per-pair skip straight to age —
    the earlier per-pair form was NOT transitive: three same-prio nodes A (no
    est, oldest), B (est:1, newest), C (est:2, mid-age) gave A<B and B<C by age
    (each pair has one side without est) but C<A by est (both sides have it) —
    a cycle `functools.cmp_to_key` cannot sort consistently. Splitting into two
    groups up front and only comparing est WITHIN the has-est group removes the
    cycle: every comparison is decided by the same fixed precedence
    (prio, group, est, age, number), which is what makes it transitive.
    """
    pa, pb = prio_rank(a["prio"], vocabulary), prio_rank(b["prio"], vocabulary)
    if pa != pb:
        return -1 if pa < pb else 1
    a_has_est, b_has_est = a["est"] is not None, b["est"] is not None
    if a_has_est != b_has_est:
        return -1 if a_has_est else 1
    if a_has_est:
        ea, eb = int(a["est"]), int(b["est"])
        if ea != eb:
            return -1 if ea < eb else 1
    ca, cb = a["created_at"] or "", b["created_at"] or ""
    if ca != cb:
        return -1 if ca < cb else 1
    return -1 if a["number"] < b["number"] else (1 if a["number"] > b["number"] else 0)


def topo_order(nodes, edges, extra_edges, vocabulary):
    """Kahn's algorithm over the OPEN, IN-SCOPE nodes, tie-broken by `compare_nodes`.

    `edges` is the full `blocked -> {blocker, ...}` map `analyse()` builds; only
    the edges whose blocked AND blocker are both in the sortable set gate the
    order — a closed or out-of-scope blocker cannot legitimately hold up a
    schedule (it is already satisfied/stale, or outside what this run may
    touch). `extra_edges` is `--edges`'s approved Dimension 1-2 findings, folded
    in the same way and subject to the same filter.

    A node inside a cycle never reaches indegree 0 and is left out of the
    returned order — `find_cycles` over the full graph already reports it.
    """
    sortable = {
        n for n, node in nodes.items() if node["in_scope"] and node["state"] == "open"
    }

    adjacency: dict[int, list[int]] = {n: [] for n in sortable}
    indegree = {n: 0 for n in sortable}

    def add_edge(blocked, blocker):
        if blocked not in sortable or blocker not in sortable or blocked == blocker:
            return
        adjacency[blocker].append(blocked)
        indegree[blocked] += 1

    for blocked, blockers in edges.items():
        for blocker in blockers:
            add_edge(blocked, blocker)
    for extra in extra_edges:
        add_edge(extra["blocked"], extra["blocker"])

    ranker = functools.cmp_to_key(
        lambda x, y: compare_nodes(nodes[x], nodes[y], vocabulary)
    )
    ready = [n for n in sortable if indegree[n] == 0]
    order = []
    while ready:
        ready.sort(key=ranker)
        current = ready.pop(0)
        order.append(current)
        for dependent in adjacency[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)

    return order


def analyse(
    repo,
    labels_file,
    milestone,
    scope_labels,
    limit,
    issue_numbers,
    sort=False,
    extra_edges=(),
):
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
        nodes[issue["number"]] = build_node(issue, vocabulary, in_scope=True, repo=repo)

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
                    view_issue(repo, blocker), vocabulary, in_scope=False, repo=repo
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

    # `body_references` with no matching native relation yet — Dimension 1-2's
    # "missing edges, from prose" and "hidden cross-milestone dependencies"
    # both read this instead of re-parsing bodies themselves. A `related`
    # reference has no native counterpart in gh-issue at all, so it is always
    # proposed; a `blocked_by`/`blocks` reference is proposed only when the
    # edge it implies (in the direction it implies) isn't already in `native`.
    proposed = []
    for number in sorted(n for n, node in nodes.items() if node["in_scope"]):
        for ref in nodes[number]["body_references"]:
            target = int(ref["target"])
            if ref["direction"] == "blocked_by":
                covered = (number, target) in native
            elif ref["direction"] == "blocks":
                covered = (target, number) in native
            else:
                covered = False
            if not covered:
                proposed.append({"from": number, **ref})

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
        "proposed": proposed,
        **(
            {"order": topo_order(nodes, edges, extra_edges, vocabulary)} if sort else {}
        ),
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
        # Members, not a path. `cycles` holds sorted strongly-connected-component
        # members, so rendering them with arrows would assert an ordering the
        # component does not carry — printing `#1 -> #2 -> #3` for a graph whose
        # edges are 1->3->2->1 claims two edges that do not exist, to a human who
        # is about to approve a repair.
        "Cycles — mutually blocking, in no particular order; human decision",
        result["cycles"],
        lambda c: "{" + ", ".join(f"#{n}" for n in c) + "}",
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
    section(
        "Proposed from body prose — no matching native relation yet",
        result["proposed"],
        lambda e: (
            f"#{e['from']} → #{e['target']} ({e['direction']}, {e['strength']}) "
            f"— “{e['phrase']}”"
        ),
    )
    if "order" in result:
        print(f"\nTopological order — {len(result['order'])} node(s), provisional:")
        print("  " + " -> ".join(f"#{n}" for n in result["order"]))


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
    parser.add_argument(
        "--sort",
        action="store_true",
        help=(
            "also compute a topological order (Dimension 3's re-order step) "
            "over the open, in-scope nodes"
        ),
    )
    parser.add_argument(
        "--edges",
        type=Path,
        metavar="FILE",
        help=(
            "JSON file of approved extra edges — a list of "
            '{"blocked": <n>, "blocker": <n>} — folded in before --sort orders '
            "the graph; ignored without --sort"
        ),
    )
    args = parser.parse_args(argv)

    extra_edges = []
    if args.edges:
        raw_edges = json.loads(args.edges.read_text())
        extra_edges = [
            {"blocked": e["blocked"], "blocker": e["blocker"]} for e in raw_edges
        ]

    result = analyse(
        args.repo,
        args.labels_file,
        args.milestone,
        args.scope_labels,
        args.limit,
        args.issue_numbers,
        sort=args.sort,
        extra_edges=extra_edges,
    )
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
