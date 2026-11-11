#!/usr/bin/env python3
"""Report scripts/check.sh's per-check wall times and warn when they grow.

check.sh times every check it runs and hands the times here once the run is
over. This prints the slowest checks, then warns, never fails, on either of two
signals:

- **Budget.** The whole gate took longer than its budget for this mode. The
  budget is a fixed number committed in check.sh, so it catches slow creep that
  a rolling comparison would absorb: a gate that grows 10% a week never looks
  unusual against its own last ten runs.
- **Jump.** One check, or the whole gate, took at least JUMP_RATIO times its
  median over this machine's recent green runs of the same mode, and at least
  JUMP_MIN_SECONDS more. This names the check that moved.

Every warning is advisory. Wall time depends on the machine and on whatever
else is running on it, including other gates, so a warning is a prompt to look,
not a verdict.

Usage:
  python3 scripts/check-timings.py --times <file> --total <secs> --mode <mode>
      [--budget <secs>] [--history <file> [--record]] [--github]

`--times` holds one `<secs>\\t<check>` line per check. `--history` is a TSV
this script owns (`<run>\\t<mode>\\t<check>\\t<secs>`); `--record` appends this
run to it, which check.sh asks for only after a green run. `--github` also
emits each warning as a GitHub Actions `::warning::` annotation.

Exits 0 whatever it finds, and 2 on unusable input.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

SLOWEST_SHOWN = 5
JUMP_RATIO = 1.5
JUMP_MIN_SECONDS = 10
MIN_SAMPLES = 3
RUNS_KEPT = 20
TOTAL = "(whole gate)"

Row = tuple[str, str, str, int]


def read_times(path: str) -> list[tuple[str, int]]:
    times: list[tuple[str, int]] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        secs, _, name = line.partition("\t")
        times.append((name, int(secs)))
    return times


def read_history(path: Path) -> list[Row]:
    rows: list[Row] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        run, mode, name, secs = parts
        try:
            rows.append((run, mode, name, int(secs)))
        except ValueError:
            continue
    return rows


def write_history(path: Path, rows: list[Row]) -> None:
    """Write rows back, keeping the last RUNS_KEPT runs of each mode.

    Per mode, so a burst of --fast runs cannot evict every full-mode sample.
    """
    runs_by_mode: dict[str, list[str]] = {}
    for run, mode, _, _ in rows:
        runs = runs_by_mode.setdefault(mode, [])
        if run not in runs:
            runs.append(run)
    keep = {run for runs in runs_by_mode.values() for run in runs[-RUNS_KEPT:]}
    text = "".join(f"{r}\t{m}\t{n}\t{s}\n" for r, m, n, s in rows if r in keep)
    tmp = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def jumps(
    times: list[tuple[str, int]], total: int, mode: str, history: list[Row]
) -> list[str]:
    """Warnings for each check that jumped against its recent median."""
    past: dict[str, list[int]] = {}
    for _, row_mode, name, secs in history:
        if row_mode == mode:
            past.setdefault(name, []).append(secs)
    found: list[str] = []
    for name, secs in [*times, (TOTAL, total)]:
        samples = past.get(name, [])[-RUNS_KEPT:]
        if len(samples) < MIN_SAMPLES:
            continue
        median = statistics.median(samples)
        if secs >= median * JUMP_RATIO and secs - median >= JUMP_MIN_SECONDS:
            # Whole-second timing records a sub-second check as 0, so the
            # median can be 0 and a ratio is undefined.
            ratio = f"{secs / median:.1f}x" if median else "up from"
            found.append(
                f"{name} took {secs}s, {ratio} its median of "
                f"{median:g}s over the last {len(samples)} green {mode} runs"
            )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--times", required=True)
    parser.add_argument("--total", type=int, required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--budget", type=int)
    parser.add_argument("--history")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--github", action="store_true")
    args = parser.parse_args(argv)

    try:
        times = read_times(args.times)
    except (OSError, ValueError) as err:
        print(f"check-timings: unreadable --times: {err}", file=sys.stderr)
        return 2

    slowest = sorted(times, key=lambda t: t[1], reverse=True)[:SLOWEST_SHOWN]
    print(f"→ timing ({args.mode}): {args.total}s total; slowest checks:")
    for name, secs in slowest:
        print(f"    {secs:>4}s  {name}")

    warnings: list[str] = []
    if args.budget is not None and args.total > args.budget:
        warnings.append(
            f"the {args.mode} gate took {args.total}s, over its {args.budget}s "
            "budget in scripts/check.sh"
        )
    history_path = Path(args.history) if args.history else None
    history = read_history(history_path) if history_path else []
    warnings += jumps(times, args.total, args.mode, history)

    for warning in warnings:
        print(f"  ⚠ slower: {warning}")
        if args.github:
            print(f"::warning title=check.sh timing::{warning}")
    if warnings:
        print(
            "    advisory only. Other load on the machine (another gate, a "
            "build) inflates times; see dev_docs/gate-performance.md"
        )

    if history_path and args.record:
        run = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = history + [(run, args.mode, n, s) for n, s in times]
        rows.append((run, args.mode, TOTAL, args.total))
        try:
            write_history(history_path, rows)
        except OSError as err:
            print(f"check-timings: history not saved: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
