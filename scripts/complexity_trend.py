#!/usr/bin/env python3
"""Complexity ledger for the auto-pilot controller inversion.

Tracks `SPECIAL-CASE(progress|reconciliation|adapter)` markers added to the
new controller surface (Stage 2's lease/verdict code, Stage 3's watch/worker)
against special cases shed from the old `spawn-orchestrator.sh` supervisor.
See dev_docs/auto-pilot-inversion-design.md, "Measuring it (the complexity
ledger)".

    uv run python scripts/complexity_trend.py check
    uv run python scripts/complexity_trend.py add --side new --delta 1 \\
        --category progress --note "..." [--pr 123]

The tripwire: new-side net >= 5 AND new-side net > old-side sheds -> TRIP.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "dev_docs" / "auto-pilot-complexity-ledger.jsonl"

# Stage 2/3 PRs must append their controller source paths (relative to repo
# root) here as they land -- empty until then.
NEW_CONTROLLER_PATHS: list[str] = []

# Informational only: the old supervisor is measured via ledger events, not
# by scanning this file for markers (it is not retro-annotated).
OLD_SUPERVISOR = "scripts/spawn-orchestrator.sh"

CATEGORIES = {"progress", "reconciliation", "adapter"}
SIDES = {"new", "old"}

MARKER_RE = re.compile(r"SPECIAL-CASE\((" + "|".join(sorted(CATEGORIES)) + r")\)")


class LedgerError(Exception):
    def __init__(self, lineno: int, reason: str):
        super().__init__(f"line {lineno}: {reason}")
        self.lineno = lineno
        self.reason = reason


class CensusError(Exception):
    """A NEW_CONTROLLER_PATHS entry is unreadable — not a ledger-line problem."""


def load_ledger(path: Path) -> tuple[dict, list[dict]]:
    """Parse and validate the ledger. Returns (meta, events)."""
    lines = path.read_text().splitlines()
    if not lines:
        raise LedgerError(1, "ledger is empty; expected a meta line")

    try:
        meta = json.loads(lines[0])
    except json.JSONDecodeError as e:
        raise LedgerError(1, f"invalid JSON: {e}") from e
    if (
        not isinstance(meta, dict)
        or meta.get("type") != "meta"
        or meta.get("schema") != 1
    ):
        raise LedgerError(1, 'first line must be {"type": "meta", "schema": 1, ...}')

    events: list[dict] = []
    for lineno, raw in enumerate(lines[1:], start=2):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LedgerError(lineno, f"invalid JSON: {e}") from e
        if not isinstance(obj, dict):
            raise LedgerError(lineno, "event must be a JSON object")
        if obj.get("type") != "event":
            raise LedgerError(lineno, 'type must be "event"')
        try:
            date.fromisoformat(obj["date"])
        except (KeyError, TypeError, ValueError) as e:
            raise LedgerError(lineno, "date must be a valid YYYY-MM-DD date") from e
        if obj.get("side") not in SIDES:
            raise LedgerError(lineno, f"side must be one of {sorted(SIDES)}")
        delta = obj.get("delta")
        if not isinstance(delta, int) or isinstance(delta, bool) or delta == 0:
            raise LedgerError(lineno, "delta must be a nonzero int")
        if obj.get("category") not in CATEGORIES:
            raise LedgerError(lineno, f"category must be one of {sorted(CATEGORIES)}")
        if not isinstance(obj.get("note"), str) or not obj["note"].strip():
            raise LedgerError(lineno, "note must be a nonempty string")
        pr = obj.get("pr")
        if pr is not None and (not isinstance(pr, int) or isinstance(pr, bool)):
            raise LedgerError(lineno, "pr must be an int or null")
        events.append(obj)

    return meta, events


def marker_census(paths: list[str]) -> dict[str, int]:
    """Count SPECIAL-CASE markers per category across the controller paths."""
    census = {c: 0 for c in sorted(CATEGORIES)}
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            raise CensusError(f"NEW_CONTROLLER_PATHS entry does not exist: {rel}")
        for category in MARKER_RE.findall(p.read_text()):
            census[category] += 1
    return census


def side_net(events: list[dict], side: str) -> int:
    return sum(e["delta"] for e in events if e["side"] == side)


def category_net(events: list[dict], side: str) -> dict[str, int]:
    out = {c: 0 for c in sorted(CATEGORIES)}
    for e in events:
        if e["side"] == side:
            out[e["category"]] += e["delta"]
    return out


def print_report(events: list[dict]) -> None:
    new_events = [e for e in events if e["side"] == "new"]
    old_events = [e for e in events if e["side"] == "old"]

    def adds_sheds(evs: list[dict]) -> tuple[int, int]:
        adds = sum(e["delta"] for e in evs if e["delta"] > 0)
        sheds = sum(-e["delta"] for e in evs if e["delta"] < 0)
        return adds, sheds

    new_adds, new_sheds = adds_sheds(new_events)
    old_adds, old_sheds = adds_sheds(old_events)

    print("=== Complexity ledger report ===")
    print(f"new: adds={new_adds} sheds={new_sheds} net={side_net(events, 'new')}")
    print(f"old: adds={old_adds} sheds={old_sheds} net={side_net(events, 'old')}")
    print()
    print("per-category net (new):")
    for cat, net in category_net(events, "new").items():
        print(f"  {cat}: {net}")
    print("per-category net (old):")
    for cat, net in category_net(events, "old").items():
        print(f"  {cat}: {net}")
    print()
    print("last 10 events:")
    for e in events[-10:]:
        pr = f"#{e['pr']}" if e.get("pr") is not None else "no-pr"
        print(
            f"  {e['date']} {e['side']:>3} {e['delta']:+d} {e['category']} "
            f"{pr} {e['note']}"
        )
    print()


def run_check(args: argparse.Namespace) -> int:
    try:
        _, events = load_ledger(LEDGER)
        census = marker_census(NEW_CONTROLLER_PATHS)
    except (LedgerError, CensusError) as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 2

    # Per-category, not just in aggregate: a "+1 progress" event must not be
    # satisfied by an adapter marker in the code.
    ledger_by_cat = category_net(events, "new")
    if ledger_by_cat != census:
        for cat in sorted(CATEGORIES):
            if ledger_by_cat[cat] != census[cat]:
                print(
                    f"INVALID: new-side {cat}: ledger net {ledger_by_cat[cat]} != "
                    f"live marker census {census[cat]} across NEW_CONTROLLER_PATHS",
                    file=sys.stderr,
                )
        return 2

    new_net = side_net(events, "new")
    old_sheds_net = sum(
        -e["delta"] for e in events if e["side"] == "old" and e["delta"] < 0
    )
    print_report(events)

    if new_net >= 5 and new_net > old_sheds_net:
        print(
            f"VERDICT: TRIP — new-side net {new_net} >= 5 and exceeds "
            f"old-side sheds {old_sheds_net}; stop and reassess"
        )
        return 1
    if new_net > old_sheds_net:
        print(
            f"VERDICT: WARN — new-side net {new_net} exceeds old-side sheds "
            f"{old_sheds_net}, but below the 5 floor"
        )
        return 0
    print(
        f"VERDICT: OK — new-side net {new_net} does not exceed old-side "
        f"sheds {old_sheds_net}"
    )
    return 0


def run_add(args: argparse.Namespace) -> int:
    if args.delta == 0:
        print("delta must be nonzero", file=sys.stderr)
        return 2
    if not args.note.strip():
        print("note must be nonempty", file=sys.stderr)
        return 2

    event = {
        "type": "event",
        "date": date.today().isoformat(),
        "pr": args.pr,
        "side": args.side,
        "delta": args.delta,
        "category": args.category,
        "note": args.note,
    }
    with LEDGER.open("a") as fh:
        fh.write(json.dumps(event) + "\n")

    try:
        _, events = load_ledger(LEDGER)
        census = marker_census(NEW_CONTROLLER_PATHS)
    except (LedgerError, CensusError) as e:
        print(f"INVALID after append: {e}", file=sys.stderr)
        return 2

    ledger_by_cat = category_net(events, "new")
    if ledger_by_cat != census:
        print(
            f"WARNING: appended, but the new-side ledger ({ledger_by_cat}) now "
            f"differs from the live marker census ({census}). Add or remove "
            f"SPECIAL-CASE markers in NEW_CONTROLLER_PATHS, or fix this "
            f"entry -- the next `check` will exit 2.",
            file=sys.stderr,
        )

    print(f"Appended: {json.dumps(event)}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="validate the ledger and report the trend")

    add_p = sub.add_parser("add", help="append an event to the ledger")
    add_p.add_argument("--side", choices=sorted(SIDES), required=True)
    add_p.add_argument("--delta", type=int, required=True)
    add_p.add_argument("--category", choices=sorted(CATEGORIES), required=True)
    add_p.add_argument("--note", required=True)
    add_p.add_argument("--pr", type=int, default=None)

    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "check"

    if args.command == "check":
        return run_check(args)
    return run_add(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
