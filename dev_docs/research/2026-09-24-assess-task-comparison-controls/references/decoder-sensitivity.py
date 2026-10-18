"""Decoder sensitivity for the assess-task comparison: does a different, prior-aware
reading of Jev's stored distributions close the agreement gap? Offline, no API calls.

An artifact of dev_docs/research/2026-09-24-assess-task-comparison-controls/. Run by
path, from the repository root:

    python3 dev_docs/research/2026-09-24-assess-task-comparison-controls/references/decoder-sensitivity.py

Re-decodes the committed 2026-09-22 Jev run: a level is escalated to only when the
probability mass at or above it exceeds t. The first table sweeps t in-sample; the
second fits t on 49 cards and scores the held-out 50th, for every card, which is the
number worth quoting (typed-model-calls.md rule 4). Ties at exactly 0.5 resolve
differently from the committed decoder, so the argmax column can differ from the
scorer by a card on the binaries.
"""

import importlib.util
import json

R = "dev_docs/research/2026-09-22-assess-task-comparison/references/"
spec = importlib.util.spec_from_file_location("cmp", R + "compare-assess-task.py")
cmp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmp)

run = json.load(open(R + "measurement/jev-run.json"))
base = [json.load(open(R + f"measurement/baseline/agent-{i}.json")) for i in (1, 2, 3)]
cards = run["cards"]
panel = cmp.panel_answers(base, cards)
DIMS = cmp.DIMENSIONS


def dist(p, c, d):
    return p["cards"][c]["answers"][d]["distribution"]


def decode(dd, enum, t):
    """Escalate to a higher level only when the mass at-or-above it exceeds t.
    t=None -> argmax (the committed decoder). For a binary, t is the P(high) cut."""
    if t is None:
        return max(enum, key=lambda v: (dd[v], -enum.index(v)))
    val = enum[0]
    for i in range(1, len(enum)):
        if sum(dd[v] for v in enum[i:]) > t:
            val = enum[i]
    return val


def jev_vals(d, t, subset=None):
    enum = DIMS[d]
    return [
        {c: decode(dist(p, c, d), enum, t) for c in (subset or cards)}
        for p in run["passes"]
    ]


def a_jev(d, passes, subset):
    return float(cmp.mean_cross(passes, panel[d], subset))


TS = [None, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9]
print("dim                       A_panel A_const | A_jev by t (None=argmax) ...")
for d in DIMS:
    ap = float(cmp.mean_pairwise(panel[d], cards))
    ac = float(cmp.a_const(panel[d], cards))
    row = []
    for t in TS:
        pv = jev_vals(d, t)
        g = cmp.gate_d(pv, panel[d], cards, DIMS[d])
        row.append(
            f"{'argmax' if t is None else t}:{a_jev(d, pv, cards):.3f}({g['lower']}:{g['higher']})"
        )
    print(f"{d:25s} {ap:.3f}  {ac:.3f}  | " + "  ".join(row))

# Leave-one-card-out: fit t on 49 cards, apply to the held-out card.
print("\nleave-one-card-out fitted threshold (grid 0.5..0.95):")
grid = [x / 100 for x in range(50, 96, 5)]
for d in DIMS:
    hits = total = 0
    lo = hi = 0
    chosen = []
    for held in cards:
        train = [c for c in cards if c != held]
        best = max(grid, key=lambda t: (a_jev(d, jev_vals(d, t, train), train), -t))
        chosen.append(best)
        pv = jev_vals(d, best, [held])
        for p in pv:
            for a in panel[d]:
                total += 1
                hits += int(p[held] == a[held])
        m = cmp.majority(panel[d], held)
        rank = {v: i for i, v in enumerate(DIMS[d])}
        for p in pv:
            v = p[held]
            if m is not None and v != m:
                lo += rank[v] < rank[m]
                hi += rank[v] > rank[m]
    ap = float(cmp.mean_pairwise(panel[d], cards))
    print(
        f"{d:25s} LOO A_jev {hits / total:.3f} vs A_panel {ap:.3f} gap {100 * (hits / total - ap):+.1f}  misses L:H {lo}:{hi}  t chosen {min(chosen)}-{max(chosen)}"
    )
