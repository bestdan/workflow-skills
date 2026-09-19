#!/usr/bin/env python3
"""Score this repo's skill descriptions as one Jev Choice question per prompt.

The instrument behind section 1 of
`dev_docs/research/2026-09-17-jev-applications.md`. It exists so that record's
numbers can be reproduced rather than taken on trust.

What it measures: given a naive prompt and the `description` frontmatter of every
`skills/*/SKILL.md` as the option set, which skill wins, and by how much over the
runner-up. The **margin** is the point. A pass/fail harness reports only whether the
right skill won; the margin says how close the boundary was, which is what would
degrade first if a description drifted.

Two suites:

  manifest    the 14 cases in evals/manifest.tsv. Each was written to trigger one
              specific skill, so these are the easy cases and a clean sweep here
              says little on its own.
  ambiguous   prompts written to sit BETWEEN near-neighbour skills. This is where a
              collision would actually show, so a claim about collisions rests on
              this suite, not the one above.

An artifact of that record, not standing tooling. Nothing in this repo depends on the
Jev API; this exists so the record's numbers can be re-run when a `description`
changes or a new Jev version ships. It is dev-only — never invoked by a skill or
command at runtime, and never part of `just check`, since it costs money and needs
the network. It lives under scripts/ because that is the only tree the linters and
typechecker cover; dev_docs/research/ holds markdown records only.

Run directly:
    python3 scripts/jev-description-collision.py --suite both
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
# Pinned, not `jev-latest`. The record this reproduces grades one model version, so a
# floating alias would silently re-measure a different model under the same numbers —
# defeating the only reason this file exists. `--model` is how you deliberately
# re-measure against a newer one.
MODEL = "jev-1.13.0"
DEFAULT_MARGIN = 0.10
PLACEHOLDER = "REPLACE_ME"
LOCAL_CONFIG = Path("dev_docs") / "tasks" / ".task-config.local.yml"

# Prompts written to straddle a near-neighbour pair. There is no expected answer:
# the measurement is the margin, not correctness. Each is deliberately underspecified
# in the way a real user's first message usually is.
AMBIGUOUS_PROBES: list[tuple[str, str]] = [
    (
        "co-review / local-review",
        "I've got changes I want gone over before they land. Pull up what changed "
        "and let's go through it.",
    ),
    (
        "research-spike / research-spike-tutorial",
        "Show me how the obligation ledger works.",
    ),
    (
        "task / assess-task",
        "There's a ticket here I need to deal with. What am I actually looking at?",
    ),
    ("task / deliver-task", "Take this one and run with it."),
    (
        "select-coder / orchestrate-coders",
        "I want this built by something other than you. Sort out how.",
    ),
    (
        "analysis-pipeline / analysis-conventions",
        "I'm about to start on the cost model notebook.",
    ),
    ("tutor / research-spike-tutorial", "Walk me through it so I actually get it."),
    (
        "plan-with-docs / break-down-task",
        "This is way too big as one chunk. Split it up and write it down.",
    ),
]


# ---------------------------------------------------------------- pure helpers


def say(line: str) -> None:
    """Progress output. Stderr, so stdout stays parseable under `--json`."""
    print(line, file=sys.stderr)


def rank(probabilities: dict[str, float]) -> tuple[str, float, str, float, float]:
    """(winner, p_winner, runner_up, p_runner_up, margin), highest first.

    A single-option set has no runner-up; its margin is the winner's own mass, since
    there is nothing for it to be confused with.
    """
    if not probabilities:
        raise ValueError("no probabilities to rank")
    ordered = sorted(probabilities.items(), key=lambda kv: -kv[1])
    winner, p_win = ordered[0]
    if len(ordered) == 1:
        return winner, p_win, "", 0.0, p_win
    runner, p_run = ordered[1]
    return winner, p_win, runner, p_run, p_win - p_run


def is_collision(margin: float, threshold: float = DEFAULT_MARGIN) -> bool:
    """A margin at or above the threshold is a clean separation, not a collision."""
    return margin < threshold


def parse_descriptions(skill_files: dict[str, str]) -> dict[str, str]:
    """name -> description, from raw SKILL.md text keyed by any label.

    Handles the three frontmatter spellings in this repo: a plain one-line
    `description:`, and the folded/literal block scalars (`>` and `|`), which are how
    every long description here is written. A file with no frontmatter, no `name` or
    no `description` is skipped rather than raising — a malformed skill should not
    take the whole run down.
    """
    out: dict[str, str] = {}
    for text in skill_files.values():
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not m:
            continue
        block = m.group(1)
        name = re.search(r"^name:\s*(.+)$", block, re.M)
        desc = re.search(
            r"^description:\s*(?:[>|][-+]?\s*\n((?:\s+.*\n?)+)|(.+))$", block, re.M
        )
        if not (name and desc):
            continue
        body = desc.group(1) or desc.group(2) or ""
        body = " ".join(line.strip() for line in body.strip().splitlines())
        if body:
            out[name.group(1).strip()] = body
    return out


def parse_manifest(text: str) -> list[tuple[str, str]]:
    """(skill, prompt_path) per row, skipping comments and blanks."""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        rows.append((parts[0].strip(), parts[1].strip()))
    return rows


def typesafe_block(config_text: str) -> str:
    """The top-level `typesafe:` mapping only, or empty when it is absent.

    Scoping this is not defensive tidiness. The same file holds other services'
    credentials — `commands/handlers/linear-config.md` documents `linear.api_key`
    living in `dev_docs/tasks/.task-config.local.yml` — so an unscoped `api_key:`
    search returns whichever service happens to appear first in the file, and a
    `linear:` block above `typesafe:` would send a full-account Linear token to a
    third party in an Authorization header.
    """
    head = re.search(r"^typesafe:[ \t]*$", config_text, re.M)
    if not head:
        return ""
    rest = config_text[head.end() :]
    nxt = re.search(r"^(?=\S)", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def redact_ref(ref: str) -> str:
    """`op://vault/item/field` reduced to `op://vault/…`.

    dev_docs/auth_key_access.md: "Never print a full reference." A personal pointer
    advertises which vault holds a full-account token.
    """
    parts = ref.split("/")
    return "/".join(parts[:3]) + "/…" if len(parts) > 3 else ref


def extract_key(config_text: str) -> tuple[str, str] | None:
    """(kind, value) from a local config, where kind is 'raw' or 'ref'.

    Raw beats ref, matching rung 0 of dev_docs/auth_key_access.md. The template's
    placeholder is treated as absent so an unfilled file falls through to the next
    rung instead of sending a literal REPLACE_ME to the API. Both searches are
    scoped to the `typesafe:` block — see typesafe_block.
    """
    block = typesafe_block(config_text)
    if not block:
        return None
    raw = re.search(r'^\s*api_key:\s*"?([^"\n#]+)"?', block, re.M)
    if raw:
        value = raw.group(1).strip()
        if value and value != PLACEHOLDER:
            return "raw", value
    ref = re.search(r'^\s*api_key_ref:\s*"?(op://[^"\n#]+)"?', block, re.M)
    if ref:
        return "ref", ref.group(1).strip()
    return None


# ------------------------------------------------------------------- io layer


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def local_config_paths(root: Path) -> list[Path]:
    """This checkout's local config, then the main checkout's.

    `dev_docs/tasks/` is gitignored, so a worktree does NOT share the operator's copy
    with the main checkout — each holds its own untracked file, and the key is
    usually only in one of them. Falling back beats copying a secret around.
    """
    paths = [root / LOCAL_CONFIG]
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    main = Path(common).parent / LOCAL_CONFIG
    if main not in paths:
        paths.append(main)
    return [p for p in paths if p.exists()]


def resolve_key(root: Path) -> str:
    """Rung 0, then rung 1, then rung 3 — dev_docs/auth_key_access.md.

    The order is the contract, not an implementation detail. Resolving a configured
    pointer before reading the environment means an exported key cannot override a
    stale or unreachable one, and the caller gets an `op` failure where it expected
    its own key to win. So every config is read first for a raw secret, then the
    environment, and only then is a pointer resolved.
    """
    refs: list[str] = []
    for path in local_config_paths(root):
        found = extract_key(path.read_text())
        if not found:
            continue
        if found[0] == "raw":
            return found[1]
        refs.append(found[1])

    env = os.environ.get("TYPESAFE_API_KEY")
    if env:
        return env

    for ref in refs:
        got = subprocess.run(["op", "read", ref], capture_output=True, text=True)
        if got.returncode != 0:
            # A failed resolve never falls through to the next rung, and the message
            # carries neither the full pointer nor the resolver's stderr — both can
            # name the vault holding a full-account token.
            sys.exit(
                "could not resolve the configured TypeSafe reference "
                f"({redact_ref(ref)}); run `op read` on it yourself to see why"
            )
        return got.stdout.strip()
    sys.exit(
        f"No TypeSafe key. Put it in {LOCAL_CONFIG} as\n"
        '  typesafe:\n    api_key: "..."\n'
        "or export TYPESAFE_API_KEY. See dev_docs/auth_key_access.md."
    )


def load_descriptions(root: Path) -> dict[str, str]:
    files = {
        str(p): p.read_text() for p in sorted((root / "skills").glob("*/SKILL.md"))
    }
    return parse_descriptions(files)


def load_manifest_cases(root: Path) -> list[tuple[str, str]]:
    manifest = (root / "evals" / "manifest.tsv").read_text()
    return [
        (skill, (root / "evals" / rel).read_text().strip())
        for skill, rel in parse_manifest(manifest)
    ]


def ask(key: str, state: str, criteria: dict[str, str], model: str = MODEL) -> dict:
    return ask_payload(
        key,
        {
            "state": state,
            "model": model,
            "questions": {
                "skill": {
                    "type": "choice",
                    "instructions": (
                        "A user typed this message to a coding agent. Which skill, if "
                        "any, should the agent load to handle it? Choose the single "
                        "best fit."
                    ),
                    "criteria": criteria,
                }
            },
        },
    )


def ask_payload(key: str, payload: dict) -> dict:
    """POST one System One request. Every question in `payload` rides together."""
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:500]}")


# ----------------------------------------------------------------- the report


def run_suite(
    key: str,
    criteria: dict[str, str],
    rows: list[tuple[str, str]],
    threshold: float,
    expects: bool,
    model: str = MODEL,
) -> dict:
    """One row per prompt. `expects` says whether rows[i][0] is an expected skill."""
    results, tokens = [], 0
    for label, prompt in rows:
        data = ask(key, prompt, criteria, model)
        answer = data["answers"]["skill"]
        winner, p_win, runner, p_run, margin = rank(answer["probabilities"])
        tokens += data.get("usage", {}).get("input_tokens", 0)
        record = {
            "label": label,
            "prompt": prompt,
            "winner": winner,
            "p_winner": p_win,
            "runner_up": runner,
            "p_runner_up": p_run,
            "margin": margin,
            "confidence": answer.get("confidence"),
            "collision": is_collision(margin, threshold),
            "misfire": (winner != label) if expects else None,
        }
        results.append(record)

        flags = [
            f
            for f, on in (
                ("MISFIRE", record["misfire"]),
                ("COLLISION", record["collision"]),
            )
            if on
        ]
        conf = record["confidence"]
        conf_text = f"   conf {conf:.2f}" if conf is not None else ""
        flag_text = ("   << " + " ".join(flags)) if flags else ""
        # Progress goes to stderr so `--json` leaves stdout carrying exactly one
        # JSON document, which is what the record advertises it for.
        say(("expect " if expects else "probing ") + label)
        say(f"   {prompt[:78]!r}")
        say(f"   1. {winner:<28} {p_win:.3f}")
        say(
            f"   2. {runner:<28} {p_run:.3f}   margin {margin:.3f}"
            f"{conf_text}{flag_text}"
        )
        say("")
    return {"results": results, "input_tokens": tokens}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--suite", choices=("manifest", "ambiguous", "both"), default="both"
    )
    ap.add_argument(
        "--margin",
        type=float,
        default=DEFAULT_MARGIN,
        help=f"collision threshold (default {DEFAULT_MARGIN})",
    )
    ap.add_argument("--json", action="store_true", help="emit the raw records")
    ap.add_argument(
        "--model",
        default=MODEL,
        help=f"model to measure against (default {MODEL}, the version the record grades)",
    )
    args = ap.parse_args(argv)

    root = repo_root()
    key = resolve_key(root)
    criteria = load_descriptions(root)

    suites, tokens, records = {}, 0, []
    if args.suite in ("manifest", "both"):
        rows = load_manifest_cases(root)
        say(f"{len(criteria)} descriptions, {len(rows)} manifest cases\n")
        out = run_suite(key, criteria, rows, args.margin, True, args.model)
        suites["manifest"], tokens = out, tokens + out["input_tokens"]
        records += out["results"]
    if args.suite in ("ambiguous", "both"):
        say(f"{len(criteria)} descriptions, {len(AMBIGUOUS_PROBES)} ambiguous probes\n")
        out = run_suite(key, criteria, AMBIGUOUS_PROBES, args.margin, False, args.model)
        suites["ambiguous"], tokens = out, tokens + out["input_tokens"]
        records += out["results"]

    if args.json:
        print(json.dumps({"model": args.model, "suites": suites}, indent=2))
        return 0

    # Only the manifest suite carries an expected skill, so it is the only half a
    # misfire is defined over; the ambiguous probes record `misfire: None`.
    labelled = [r for r in records if r["misfire"] is not None]
    misfires = [r for r in labelled if r["misfire"]]
    collisions = [r for r in records if r["collision"]]
    print("=" * 66)
    print(f"model:      {args.model}")
    print(f"misfires:   {len(misfires)}/{len(labelled)} labelled cases")
    print(f"collisions: {len(collisions)}/{len(records)} (margin < {args.margin})")
    for r in sorted(records, key=lambda r: r["margin"])[:3]:
        print(
            f"   tightest: {r['winner']} vs {r['runner_up']} = {r['margin']:.3f}"
            f" (conf {r['confidence']:.2f})"
        )
    print(f"tokens: {tokens} in   (~${tokens / 1e6 * 0.042:.4f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
