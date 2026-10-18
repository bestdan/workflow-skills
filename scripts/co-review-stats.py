#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Print a per-reviewer quality table from the /co-review findings ledger.

Read-only: this script never writes the ledger. It is the only reader of the
ledger that scripts/co-review-record.py writes, and the arithmetic lives here,
not in a model, so every number on stdout is computed.

Usage:
  uv run scripts/co-review-stats.py [--ledger <path>] [--since <ISO date>] [--repo <name>]

Definitions (a run is identified by the pair (host, run_id), never run_id alone):
  runs         runs where the reviewer's disposition was "ran".
  findings     finding rows listing the reviewer in sources.
  wrong/n/a/oos %  verdict counts / findings. "—" when the reviewer has no findings.
  Δ claude     PAIRED wrong-rate delta in percentage points: over the cohort of
               runs where both the reviewer and "claude" ran, the reviewer's
               wrong-rate minus claude's wrong-rate, each over its own findings
               in those runs. The cohort size is printed as (n=N). It is "—"
               for the claude row, for an empty cohort, and when either side
               has zero findings in the cohort (its rate is undefined).
  unique-high  verdict "high", sources == [reviewer] exactly, human_corroborated false.
  finds/run    findings / runs ("—" when runs is 0).
  p50 s, p90 s nearest-rank percentile (the ceil(p*n)-th smallest value) of
               duration_s over runs where the reviewer ran or failed. A
               timed-out run's duration is the timeout bound, so it is
               excluded; null or missing durations are skipped. "—" if none.
  fail %       (timed-out + failed) / (ran + failed + timed-out); skipped
               dispositions are not dispatches. "—" when never dispatched.

The denominator is reconciled runs: invocations that aborted before
reconciliation never reached the ledger, by design. Runs whose schema is not 1
are skipped (with their findings) and counted in the footer, as are
unparseable lines.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_LEDGER = Path.home() / ".claude" / "co-review" / "findings.jsonl"
SCHEMA = 1
BASE = "claude"
DASH = "—"
Key = Tuple[str, str]


def pct(num: int, den: int) -> Optional[float]:
    return 100.0 * num / den if den else None


def fmt_num(value: Optional[float]) -> str:
    return DASH if value is None else f"{value:.1f}"


def nearest_rank(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p * len(ordered))) - 1]


def iso_date(value: str) -> str:
    # ts is always UTC "...Z" (co-review's recording step writes it with date -u),
    # so a YYYY-MM-DD string compares correctly against it; anything else would
    # compare silently wrong.
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {value!r}")


def load(path: Path, since: Optional[str], repo: Optional[str]):
    """Return (runs, findings, stats). runs: key -> {disp, dur}; findings: key -> [rec]."""
    runs: Dict[Key, Dict[str, Any]] = {}
    findings: Dict[Key, List[Dict[str, Any]]] = {}
    skipped_schema = torn = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    raise ValueError("not an object")
                key = (str(rec["host"]), str(rec["run_id"]))
            except (ValueError, KeyError):
                torn += 1
                continue
            if rec.get("type") == "run":
                if rec.get("schema") != SCHEMA:
                    skipped_schema += 1
                    continue
                if key in runs:
                    continue
                if since is not None and not str(rec.get("ts", "")) >= since:
                    continue
                if repo is not None and rec.get("repo") != repo:
                    continue
                disp: Dict[str, Any] = {}
                dur: Dict[str, Optional[float]] = {}
                for r in rec.get("reviewers") or []:
                    if isinstance(r, dict) and r.get("name"):
                        disp[r["name"]] = r.get("disposition")
                        d = r.get("duration_s")
                        ok = isinstance(d, (int, float)) and not isinstance(d, bool)
                        dur[r["name"]] = d if ok else None
                runs[key] = {"disp": disp, "dur": dur}
            elif rec.get("type") == "finding":
                findings.setdefault(key, []).append(rec)
    findings = {k: v for k, v in findings.items() if k in runs}
    return runs, findings, {"schema": skipped_schema, "torn": torn}


def compute(runs, findings) -> List[Dict[str, Any]]:
    names: Set[str] = set()
    for run in runs.values():
        names.update(run["disp"])
    for recs in findings.values():
        for f in recs:
            names.update(f.get("sources") or [])

    ran_by: Dict[str, Set[Key]] = {n: set() for n in names}
    for key, run in runs.items():
        for n, d in run["disp"].items():
            if d == "ran":
                ran_by[n].add(key)

    def of(name: str, keys: Optional[Set[Key]] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for key, recs in findings.items():
            if keys is None or key in keys:
                out.extend(f for f in recs if name in (f.get("sources") or []))
        return out

    def wrong_rate(fs: List[Dict[str, Any]]) -> Optional[float]:
        return pct(sum(1 for f in fs if f.get("verdict") == "wrong"), len(fs))

    rows = []
    for name in sorted(names, key=lambda n: (n != BASE, n)):
        fs = of(name)
        n_f = len(fs)
        verdicts = [f.get("verdict") for f in fs]
        n_runs = len(ran_by[name])
        durs: List[float] = []
        ran = failed = timed_out = 0
        for run in runs.values():
            d = run["disp"].get(name)
            if d == "ran":
                ran += 1
            elif d == "failed":
                failed += 1
            elif d == "timed-out":
                timed_out += 1
            secs = run["dur"].get(name)
            if d in ("ran", "failed") and secs is not None:
                durs.append(secs)
        delta = cohort = None
        if name != BASE:
            common = ran_by[name] & ran_by.get(BASE, set())
            cohort = len(common)
            mine, base = wrong_rate(of(name, common)), wrong_rate(of(BASE, common))
            if mine is not None and base is not None:
                delta = mine - base
        rows.append(
            {
                "name": name,
                "runs": n_runs,
                "findings": n_f,
                "wrong": pct(verdicts.count("wrong"), n_f),
                "delta": delta,
                "cohort": cohort,
                "na": pct(verdicts.count("not-applicable"), n_f),
                "oos": pct(verdicts.count("out-of-scope"), n_f),
                "high": verdicts.count("high"),
                "unique_high": sum(
                    1
                    for f in fs
                    if f.get("verdict") == "high"
                    and f.get("sources") == [name]
                    and not f.get("human_corroborated")
                ),
                "per_run": (n_f / n_runs) if n_runs else None,
                "p50": nearest_rank(durs, 0.5),
                "p90": nearest_rank(durs, 0.9),
                "fail": pct(failed + timed_out, ran + failed + timed_out),
            }
        )
    return rows


def delta_cell(row: Dict[str, Any]) -> str:
    if row["name"] == BASE or not row["cohort"]:
        return DASH
    if row["delta"] is None:
        return f"{DASH} (n={row['cohort']})"
    return f"{row['delta']:+.1f} (n={row['cohort']})"


def render(rows, n_runs: int, stats) -> str:
    header = [
        "reviewer", "runs", "findings", "wrong %", "Δ claude", "n/a %", "oos %",
        "high", "unique-high", "finds/run", "p50 s", "p90 s", "fail %",
    ]  # fmt: skip
    table = [header]
    for r in rows:
        per_run = DASH if r["per_run"] is None else f"{r['per_run']:.2f}"
        table.append([
            r["name"], str(r["runs"]), str(r["findings"]), fmt_num(r["wrong"]),
            delta_cell(r), fmt_num(r["na"]), fmt_num(r["oos"]), str(r["high"]),
            str(r["unique_high"]), per_run, fmt_num(r["p50"]), fmt_num(r["p90"]),
            fmt_num(r["fail"]),
        ])  # fmt: skip
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    lines = []
    for i, row in enumerate(table):
        cells = [row[0].ljust(widths[0])] + [
            c.rjust(w) for c, w in zip(row[1:], widths[1:])
        ]
        lines.append("  ".join(cells).rstrip())
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    lines.append("")
    lines.append(
        f"{n_runs} reconciled runs (invocations that aborted before reconciliation "
        "are absent by design)."
    )
    lines.append(
        f"Skipped: {stats['schema']} run(s) with unknown schema, "
        f"{stats['torn']} unparseable line(s)."
    )
    lines.append(
        "Δ claude: paired wrong-% delta in points over runs where both ran; "
        "p50/p90 exclude timed-out runs."
    )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument(
        "--since", type=iso_date, help="only runs on or after this date (YYYY-MM-DD)"
    )
    ap.add_argument("--repo", help="only runs for this repo")
    args = ap.parse_args(argv)
    path = Path(args.ledger).expanduser()
    if not path.is_file() or path.stat().st_size == 0:
        print(f"No co-review ledger data at {path}.")
        return 0
    runs, findings, stats = load(path, args.since, args.repo)
    if not runs:
        print(
            f"No reconciled runs match in {path} ({stats['schema']} unknown-schema "
            f"run(s), {stats['torn']} unparseable line(s))."
        )
        return 0
    print(render(compute(runs, findings), len(runs), stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
