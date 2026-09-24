#!/usr/bin/env python3
"""Run the incumbent `assess-task` subagent over the corpus, and write a baseline file.

An artifact of `dev_docs/research/2026-09-22-assess-task-comparison/`, not standing
tooling. It is dev-only: never invoked by a skill or command at runtime, never part
of `just check`. It spends real model calls on the operator's own `claude` login —
one fresh `claude -p` process per card, sequentially — so a full run is 50 calls
against the pinned model, not free and not fast.

Run by path, from the repository root, once per agent (#814 asks for three
independent runs):

    D=dev_docs/research/2026-09-22-assess-task-comparison/references
    python3 $D/run-baseline.py --agent 1
    python3 $D/run-baseline.py --agent 2
    python3 $D/run-baseline.py --agent 3
    python3 $D/test_run_baseline.py                            # hermetic; no calls

    # a smoke probe over a few cards, not a measurement
    python3 $D/run-baseline.py --agent 1 --ids issue-277,issue-348 \\
        --out /tmp/probe.json

Each card is one `claude -p` invocation, prompted with the filled baseline prompt
on stdin, in a fresh empty temporary directory. Deliberate differences from a
production `Agent`-tool spawn:

- A fresh CLI process stands in for the subagent spawn; the two share no state.
- `--setting-sources ""`, `--strict-mcp-config` and `--tools ""` turn off user and
  project settings, plugins, hooks and MCP servers, and offer no tools at all — the
  record's no-file-read constraint is enforced mechanically (nothing to read, in an
  empty directory, with no tool to read it with), not only by the prompt.
- The effort level is the CLI default; nothing here raises or lowers it.

The output is the baseline file `compare-assess-task.py`'s module docstring
defines, one per agent: `latency_s` is wall clock around the whole subprocess call
(process start to exit), which includes the CLI's own startup and context load —
the record's "profile needed" to "profile in hand". A card whose call still fails
after two retries is left out of `cards` and recorded under `failed` instead; the
scorer already treats an absent card as a missing answer.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "measurement" / "baseline"
MAX_ATTEMPTS = 3  # one try, plus "up to 2 more times"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# The corpus and the exact prompt-filling code are build-corpus.py's, so a card's
# prompt here is byte-identical to `build-corpus.py --prompt <id>`. One home for
# the rendering, not two.
_bc = _load("build_corpus", HERE / "build-corpus.py")

# Pinned, never a floating alias: "all three incumbent runs use one pinned model
# version" (../README.md, "Input").
MODEL = "claude-opus-5-5"

# USD per million tokens, Opus 5.5 list prices; cache_write is the 1-hour TTL rate
# (2x the uncached input rate) because `--no-session-persistence` still lets the CLI
# write a 1h cache entry for the skill/system prompt. Verified against a smoke call:
# total_cost_usd 0.0475822 = (2 * 4 + 5421 * 8 + 531 * 0.2 + 205 * 20) / 1e6 exactly,
# for that call's uncached/cache_write/cache_read/output token counts.
PRICING_USD_PER_MTOK = {
    "uncached": 4.00,
    "cache_read": 0.20,
    "cache_write": 8.00,
    "output": 20.00,
}


def build_cases() -> list[dict]:
    """The corpus, in the same order build-corpus.py emits it, no network."""
    corpus = _bc.build(json.loads(_bc.SOURCE.read_text()))
    return corpus["cases"]


def render_prompt(case: dict) -> str:
    template = _bc.template_of(_bc.PROMPT.read_text())
    return _bc.render(template, case)


def select(cases: list[dict], ids: str | None) -> list[dict]:
    if ids is None:
        return cases
    wanted = [i.strip() for i in ids.split(",") if i.strip()]
    by_id = {c["id"]: c for c in cases}
    unknown = [i for i in wanted if i not in by_id]
    if unknown:
        sys.exit(f"not in the corpus: {unknown}")
    return [by_id[i] for i in wanted]


def cli_invocation(claude: str) -> list[str]:
    """The argv used for every card, minus the prompt (which travels on stdin)."""
    return [
        claude,
        "-p",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--tools",
        "",
        "--model",
        MODEL,
        "--output-format",
        "json",
        "--no-session-persistence",
    ]


def claude_version(claude: str) -> str:
    """`claude --version`'s stdout, trimmed. Best-effort: recorded, never required."""
    try:
        out = subprocess.run(
            [claude, "--version"], capture_output=True, text=True, timeout=30
        )
        return out.stdout.strip() or out.stderr.strip()
    except OSError as exc:
        return f"unknown ({exc})"


class CallFailed(Exception):
    """One `claude -p` attempt did not produce a usable answer."""


def call_once(cmd: list[str], prompt: str) -> tuple[dict, float]:
    """One fresh `claude -p` process, in a fresh empty directory. Raises CallFailed
    on a non-zero exit, unparseable JSON, or `is_error: true` — never partially
    trusts a bad response."""
    with tempfile.TemporaryDirectory() as cwd:
        start = time.monotonic()
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, cwd=cwd
        )
        latency = time.monotonic() - start
    if proc.returncode != 0:
        raise CallFailed(f"exit {proc.returncode}: {proc.stderr.strip()[:2000]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CallFailed(f"unparseable JSON: {exc}: {proc.stdout[:2000]!r}") from exc
    if data.get("is_error"):
        raise CallFailed(f"is_error: {json.dumps(data)[:2000]}")
    return data, latency


def to_entry(data: dict, latency: float) -> dict:
    """A successful response -> one baseline-file card entry, per
    compare-assess-task.py's format, plus the extra keys the scorer ignores."""
    usage = data["usage"]
    creation = usage.get("cache_creation") or {}
    return {
        "raw": data["result"],
        "latency_s": latency,
        "tokens": {
            "uncached": int(usage.get("input_tokens", 0)),
            "cache_read": int(usage.get("cache_read_input_tokens", 0)),
            "cache_write": int(usage.get("cache_creation_input_tokens", 0)),
            "output": int(usage.get("output_tokens", 0)),
        },
        "cache_warm": int(usage.get("cache_read_input_tokens", 0)) > 0,
        "cost_usd": data.get("total_cost_usd"),
        "duration_api_ms": data.get("duration_api_ms"),
        "served_models": sorted(data.get("modelUsage", {})),
        "cache_write_ttl": {
            "ephemeral_5m": int(creation.get("ephemeral_5m_input_tokens", 0)),
            "ephemeral_1h": int(creation.get("ephemeral_1h_input_tokens", 0)),
        },
    }


def ask_card(cmd: list[str], case: dict) -> tuple[dict | None, str | None]:
    """One card, up to MAX_ATTEMPTS attempts. Returns (entry, None) or (None, error).

    Interpretation: a card that never succeeds is left out of `cards` entirely
    (the scorer counts an absent card as a missing answer) rather than written with
    a placeholder, so a formatting failure is never mistaken for a judgment.
    """
    prompt = render_prompt(case)
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data, latency = call_once(cmd, prompt)
        except CallFailed as exc:
            last_error = str(exc)
            print(
                f"  {case['id']}: attempt {attempt} failed: {last_error[:200]}",
                file=sys.stderr,
            )
            continue
        served = sorted(data.get("modelUsage", {}))
        if served and any(m != MODEL for m in served):
            print(
                f"warning: {case['id']}: asked for {MODEL}, served {served}",
                file=sys.stderr,
            )
        entry = to_entry(data, latency)
        print(
            f"  {case['id']}: {latency:.2f}s tokens={entry['tokens']}",
            file=sys.stderr,
        )
        return entry, None
    return None, last_error


def run_agent(claude: str, agent: int, cases: list[dict]) -> dict:
    cmd = cli_invocation(claude)
    cards: dict = {}
    failed: dict = {}
    for case in cases:
        entry, error = ask_card(cmd, case)
        if entry is not None:
            cards[case["id"]] = entry
        else:
            failed[case["id"]] = error
    return {
        "agent": agent,
        "model": MODEL,
        "run_date": datetime.date.today().isoformat(),
        "pricing_usd_per_mtok": PRICING_USD_PER_MTOK,
        "claude_cli_version": claude_version(claude),
        "invocation": cmd,
        "cards": cards,
        "failed": failed,
    }


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--agent", type=int, required=True, choices=(1, 2, 3), help="panel agent id"
    )
    p.add_argument("--out", help="default: measurement/baseline/agent-N.json")
    p.add_argument("--ids", metavar="A,B", help="only these card ids (a probe)")
    p.add_argument("--claude", default="claude", help="the claude CLI to invoke")
    args = p.parse_args(argv)

    out = Path(args.out) if args.out else OUT_DIR / f"agent-{args.agent}.json"
    cases = select(build_cases(), args.ids)
    print(f"agent {args.agent}: {len(cases)} cards, model {MODEL}", file=sys.stderr)
    result = run_agent(args.claude, args.agent, cases)
    write_atomic(out, result)
    print(
        f"wrote {out}: {len(result['cards'])} cards, {len(result['failed'])} failed",
        file=sys.stderr,
    )
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
