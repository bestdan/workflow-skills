"""Score extra raters against the Opus panel with the scorer's own functions.

An artifact of dev_docs/research/2026-09-24-assess-task-comparison-controls/.
Offline, standard library only. Run by path, from the repository root:

    D=dev_docs/research/2026-09-24-assess-task-comparison-controls/references
    python3 $D/score-arms.py $D/measurement/opus-jevq.json \\
        $D/measurement/codex.json $D/measurement/crush.json

Each arm is one run, so it is scored as one extra panel member: A = mean agreement
with each of the three agents (mean_cross), gate (d) = the scorer's gate_d. The Jev
column is the committed 2026-09-22 run, all three passes.
"""

import importlib.util
import json
import sys

R = "dev_docs/research/2026-09-22-assess-task-comparison/references/"
spec = importlib.util.spec_from_file_location("cmp", R + "compare-assess-task.py")
cmp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmp)

run = json.load(open(R + "measurement/jev-run.json"))
base = [json.load(open(R + f"measurement/baseline/agent-{i}.json")) for i in (1, 2, 3)]
cards = run["cards"]
panel = cmp.panel_answers(base, cards)
jev = cmp.jev_answers(run, cards)
MEAS = ["complexity", "creativity", "verification_criticality"]

arms = {}
for path in sys.argv[1:]:
    doc = json.load(open(path))
    parsed = {c: cmp.parse_block(doc["cards"].get(c, {}).get("raw")) for c in cards}
    arms[doc["arm"]] = {d: [{c: parsed[c][d] for c in cards}] for d in cmp.DIMENSIONS}
    n_ok = sum(1 for c in cards if c in doc["cards"])
    miss = sum(1 for c in cards for d in cmp.DIMENSIONS if parsed[c][d] is None)
    lat = sorted(v["latency_s"] for v in doc["cards"].values())
    print(
        f"{doc['arm']}: {n_ok}/50 cards answered, {miss} missing/out-of-enum values, "
        f"median latency {lat[len(lat) // 2]:.1f}s"
        if lat
        else doc["arm"]
    )

print()
hdr = f"{'dimension':26s}{'A_panel':>8s}{'A_const':>8s}{'jev':>8s}" + "".join(
    f"{a:>12s}" for a in arms
)
print(hdr + "   (d: L:H per rater)")
for d in cmp.DIMENSIONS:
    ap = float(cmp.mean_pairwise(panel[d], cards))
    ac = float(cmp.a_const(panel[d], cards))
    row = f"{d:26s}{ap:8.3f}{ac:8.3f}{float(cmp.mean_cross(jev[d], panel[d], cards)):8.3f}"
    dirs = []
    for a, ans in arms.items():
        row += f"{float(cmp.mean_cross(ans[d], panel[d], cards)):12.3f}"
        g = cmp.gate_d(ans[d], panel[d], cards, cmp.DIMENSIONS[d])
        dirs.append(f"{a} {g['lower']}:{g['higher']}")
    print(row + "   " + ", ".join(dirs))

tp = cmp.tuple_answers(panel, MEAS, cards)
tj = cmp.tuple_answers(jev, MEAS, cards)
row = f"{'tuple (3 measurable)':26s}{float(cmp.mean_pairwise(tp, cards)):8.3f}{'':8s}{float(cmp.mean_cross(tj, tp, cards)):8.3f}"
for a, ans in arms.items():
    row += (
        f"{float(cmp.mean_cross(cmp.tuple_answers(ans, MEAS, cards), tp, cards)):12.3f}"
    )
print(row)

# Pairwise agreement among the extra raters and Jev's pass 1, on the measurable dims:
print("\nrater-vs-rater agreement (measurable dims, mean):")
names = ["jev"] + list(arms)
src = {"jev": {d: [jev[d][0]] for d in cmp.DIMENSIONS}, **arms}
for i, a in enumerate(names):
    for b in names[i + 1 :]:
        v = sum(float(cmp.mean_cross(src[a][d], src[b][d], cards)) for d in MEAS) / len(
            MEAS
        )
        print(f"  {a} ~ {b}: {v:.3f}")
