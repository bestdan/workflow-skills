#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Append one /co-review run's blind-graded findings to the reviewer ledger.

The reconciler grades findings labelled "Reviewer A/B/C" without knowing who
wrote them; the main agent holds the label -> identity mapping. This script
performs the join, so no agent ever holds both the grades and the scores.

It is a script rather than a sub-agent for one reason: a sub-agent returns text
into the caller's context, and that would leak scores back into the review
path. So on success it prints exactly one receipt line and never echoes ledger
contents — that silence is the firewall.

Usage:
  uv run scripts/co-review-record.py --run <meta.json> --findings <recon.json> \
    --mapping <map.json> [--ledger <path>]

Exit 0 on a write or a duplicate run_id; exit 1 on invalid input, having
written nothing. The caller treats failure as non-fatal, so a partial write is
worse than no write.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
from pathlib import Path

VERDICTS = {"high", "medium", "wrong", "not-applicable", "out-of-scope"}
KINDS = {"agent", "human"}
DEFAULT_LEDGER = Path.home() / ".claude" / "co-review" / "findings.jsonl"


class InputError(Exception):
    pass


def load_json(path: str, what: str):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{what}: cannot read {path}: {exc}") from exc


def validate_mapping(mapping) -> dict:
    if not isinstance(mapping, dict):
        raise InputError("mapping: expected an object of label -> identity")
    for label, ident in mapping.items():
        if not isinstance(ident, dict) or ident.get("kind") not in KINDS:
            raise InputError(f"mapping: {label!r} needs kind 'agent' or 'human'")
        if ident["kind"] == "agent" and not ident.get("name"):
            raise InputError(f"mapping: agent {label!r} has no name")
    return mapping


def validate_run(run) -> dict:
    if not isinstance(run, dict):
        raise InputError("run: expected an object")
    if not isinstance(run.get("run_id"), str) or not run["run_id"]:
        raise InputError("run: run_id is required")
    if not isinstance(run.get("reviewers", []), list):
        raise InputError("run: reviewers must be a list")
    return run


def join_findings(findings, mapping: dict, run_id: str) -> list[dict]:
    """Unblind each finding's sources; drop findings only humans raised."""
    if not isinstance(findings, list):
        raise InputError("findings: expected an array")
    records = []
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise InputError(f"findings[{i}]: expected an object")
        verdict = finding.get("verdict")
        if verdict not in VERDICTS:
            raise InputError(f"findings[{i}]: unknown verdict {verdict!r}")
        sources = finding.get("sources")
        if not isinstance(sources, list):
            raise InputError(f"findings[{i}]: sources must be a list")
        agents, human = [], False
        for label in sources:
            if label not in mapping:
                raise InputError(
                    f"findings[{i}]: source {label!r} is not in the mapping"
                )
            ident = mapping[label]
            if ident["kind"] == "human":
                human = True
            elif ident["name"] not in agents:
                agents.append(ident["name"])
        if not agents:
            continue
        records.append(
            {
                "type": "finding",
                "run_id": run_id,
                **{k: v for k, v in finding.items() if k != "sources"},
                "sources": agents,
                "human_corroborated": human,
            }
        )
    return records


def append(ledger: Path, run: dict, findings: list[dict]) -> bool:
    """Write the run and its findings under one lock. False on a duplicate."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "run" and rec.get("run_id") == run["run_id"]:
                return False
        lines = [{"type": "run", "schema": 1, **run}, *findings]
        fh.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in lines))
        fh.flush()
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--mapping", required=True)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = ap.parse_args(argv)
    try:
        run = validate_run(load_json(args.run, "run"))
        mapping = validate_mapping(load_json(args.mapping, "mapping"))
        records = join_findings(
            load_json(args.findings, "findings"), mapping, run["run_id"]
        )
    except InputError as exc:
        print(f"co-review-record: {exc}", file=sys.stderr)
        return 1
    if not append(args.ledger, run, records):
        print("RECORDED: duplicate, skipped")
        return 0
    print(
        f"RECORDED: {len(records)} findings, {len(run.get('reviewers', []))} reviewers"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
