#!/usr/bin/env python3
"""Score this repo's skill descriptions as one Jev Choice question per prompt.

The instrument behind section 1 of the `README.md` beside it. It exists so that
record's numbers can be reproduced rather than taken on trust.

What it measures: given a naive prompt and the `description` frontmatter of every
`skills/*/SKILL.md` as the option set, which skill wins, and by how much over the
runner-up. The **margin** is the point. A pass/fail harness reports only whether the
right skill won; the margin says how close the boundary was, which is what would
degrade first if a description drifted.

It asks two questions per prompt, in one request. The **Choice** above is the original.
The **Noul** beside it — "should the agent load one of these skills at all?" — closes
the gap the Choice cannot see: an argmax over the roster returns one of its members
even for a prompt that should load nothing, so a clean margin is compatible with the
router firing confidently on every off-topic message it is handed. The Noul is what a
false-positive rate is measured from.

Three suites:

  manifest    the 14 cases in evals/manifest.tsv. Each was written to trigger one
              specific skill, so these are the easy cases and a clean sweep here
              says little on its own.
  ambiguous   prompts written to sit BETWEEN near-neighbour skills. This is where a
              collision would actually show, so a claim about collisions rests on
              this suite, not the one above.
  negative    prompts that should load NO skill. The ground truth the other two do
              not contain — every manifest row names an expected skill, so the file
              cannot express "none of them". See NEGATIVE_PROBES for how they were
              written and what that costs the result.

The Choice's `state` is the bare prompt and its `criteria` are the skill descriptions,
both unchanged by the Noul: the roster the Noul needs lives in the Noul's own
`instructions` rather than in the shared state, so the Choice question's own input is
byte-identical to the one the record's section-1 numbers were measured from. The request
as a whole is not — it now carries a second question. What makes that harmless is Jev's
documented evaluation of questions in parallel and in isolation, which this file does not
verify; so the Noul costs latency it does not have and about 2.3k extra input tokens per
request (the roster), and nothing else.

An artifact of that record, not standing tooling. Nothing in this repo depends on the
Jev API; this exists so the record's numbers can be re-run when a `description`
changes or a new Jev version ships. It is dev-only — never invoked by a skill or
command at runtime, and never part of `just check`, since it costs money and needs
the network. Nothing runs it and nothing typechecks it; the one gate that still
touches it is `dprint`, which formats it. Otherwise it is frozen evidence, and it
breaks when someone reproduces the record or not at all.

Dependencies: the standard library only. Needs a TypeSafe key, resolved the way
`dev_docs/auth_key_access.md` describes. Its `resolve_key` and `ask_payload` are
borrowed by the two sibling records' instruments — see `jev-pick-tool.py`'s import
comment for why one copy of code that reads secrets beats three.

Run by path, from the repository root:
    D=dev_docs/research/2026-09-17-jev-applications/references
    python3 $D/jev-description-collision.py --suite both   # calls the API, needs a key
    python3 $D/jev-description-collision.py --suite all --runs 4   # the full record
    python3 $D/test_jev_description_collision.py           # hermetic, no key

`--runs` is not a convenience. Section 1 measured a threefold run-to-run spread on the
margin the whole proposal rests on, so a single run is not a result — for the Noul as
much as for the Choice, and the report prints the per-run spread for that reason.
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
# A Noul is a probability, and 0.5 is where "more likely yes than no" falls. Nothing
# subtler is defensible here: this measurement has no calibration data to tune a
# threshold against, and picking one that flattered the result would be the whole
# failure mode. `--noul-threshold` moves the threshold for a run; it cannot re-cut the
# same records, since every invocation issues fresh nondeterministic calls, so comparing
# two invocations conflates threshold sensitivity with run-to-run variance. Re-cutting at
# another value is done offline, from the per-row `needs_skill` scores in one `--json`
# output. The report prints the rate at the selected threshold only.
DEFAULT_NEEDS_SKILL = 0.5
PLACEHOLDER = "REPLACE_ME"
LOCAL_CONFIG = Path("dev_docs") / "tasks" / ".task-config.local.yml"
# The operator key file — a raw secret outside every checkout, so it carries no
# repo-tree exposure and needs no prefix typed at each invocation. Read directly
# rather than bridged, which is what makes it usable by a human running this by
# hand. See dev_docs/auth_key_access.md, "Three shapes".
OPERATOR_KEY_FILE = Path.home() / ".config" / "workflow-skills" / "typesafe_api_key"

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

# Prompts that should load NO skill. The ground truth neither suite above contains:
# every `evals/manifest.tsv` row names an expected skill, and the file's own header says
# the eval "asserts Claude auto-invokes the expected skill", so it cannot express "none
# of them" at all. A Choice over the roster cannot either — argmax always returns one —
# which is why measuring a false-positive rate needed this list written before it needed
# any code.
#
# THESE WERE AUTHORED, NOT SAMPLED, and that is the result's main limit. Sampling real
# off-topic prompts would be better ground truth; the only corpus on the machine that
# measured this is the operator's own Claude Code transcripts, and putting real messages
# from those into a public repo was declined. So these are one person's idea of what an
# off-topic message looks like, which is exactly the bias a false-positive rate is
# supposed to catch. Read the number with that in front of it.
#
# Two tiers, reported separately, because they are different claims:
#
#   plain   ordinary work no skill here covers, plus one non-coding request. A
#           reasonable reader would not route any of these. If the rate is non-zero
#           HERE, the Noul is firing on prompts nothing in the roster claims.
#   near    adjacent to a skill's territory but on the wrong side of its description —
#           a read rather than a review, a count rather than a claim, one-off
#           arithmetic rather than a model. This is where a false positive would
#           actually live, and it is also where the label is a judgment call rather
#           than a fact. A reader who disagrees with one of these should re-cut the
#           rate without it; the per-row records in `--json` are there for that.
NEGATIVE_PROBES: list[tuple[str, str, str]] = [
    (
        "plain",
        "flaky test",
        "This test passes locally and fails in CI about one run in five. Work out why.",
    ),
    (
        "plain",
        "rename a symbol",
        "Rename `resolve_key` to `load_key` everywhere and keep the tests passing.",
    ),
    ("plain", "explain a regex", r"What does `^(?=\S)` actually match here?"),
    (
        "plain",
        "add a flag",
        "Add a --verbose flag to that script so it prints each request as it goes.",
    ),
    (
        "plain",
        "undo a commit",
        "I committed too early. Undo the last commit but leave the changes in my tree.",
    ),
    (
        "plain",
        "bump a pin",
        "Bump the pinned shfmt version and fix whatever that breaks.",
    ),
    (
        "plain",
        "profile something",
        "This script got slow. Profile it and tell me where the time goes.",
    ),
    (
        "plain",
        "general knowledge",
        "What's the difference between a git worktree and a second clone?",
    ),
    (
        "plain",
        "not about code",
        "Write the team a short note saying the release slipped a day.",
    ),
    ("plain", "a machine question", "Is anything listening on port 5432 right now?"),
    (
        "near",
        "near co-review — a read, not a review",
        "What did the review bot leave on that PR? Just read it back to me — "
        "don't act on any of it.",
    ),
    (
        "near",
        "near task — a count, not a claim",
        "How many open issues are on the board right now?",
    ),
    (
        "near",
        "near tutor — one answer, not a teaching loop",
        "In a paragraph, what does the claim lock actually do?",
    ),
    # THE LABEL ON THIS ROW IS DISPUTED, and it is the only row that fires: all four
    # false positives in the record's 4/64 are this prompt, at 0.76 in every run. It was
    # written against `plan-with-docs`, whose territory the second clause rules out — but
    # it does not rule out `assess-task` ("scope", writes nothing down), which is what
    # Jev returns. The record judges the label wrong and reports 0/60 beside the 4/64;
    # the row keeps `needs_skill_truth=False` so this file still reproduces the 4/64 it
    # published. Re-cut it from the retained records, not by editing this table.
    (
        "near",
        "near plan-with-docs — explicitly nothing written down",
        "Roughly how much work is this? Don't write anything down, I just want a "
        "sense of it.",
    ),
    (
        "near",
        "near select-coder — a capability question, not a routing one",
        "Is codex installed on this machine?",
    ),
    # analysis-pipeline's own description says "Not for one-off arithmetic or
    # exploratory questions — just answer those directly", so a firing here is the
    # description contradicting itself rather than a debatable label.
    ("near", "near analysis-pipeline — one-off arithmetic", "What's 12% of 4,300?"),
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


def says_needs_skill(noul: float, threshold: float = DEFAULT_NEEDS_SKILL) -> bool:
    """A Noul at or above the threshold is a yes.

    At or above, not above, matching is_collision's boundary rule so the two questions
    round the same way. Jev returns 0.5 often enough for the difference to decide rows.
    """
    return noul >= threshold


def noul_instructions(criteria: dict[str, str]) -> str:
    """The roster-bearing instructions for the no-skill Noul.

    The roster goes HERE rather than into the shared `state`, and that placement is the
    whole reason this could be added without re-opening section 1. `state` is shared by
    every question in a request, so putting the whole roster there would have changed the
    Choice's input — and the Choice's numbers are what the record already reports.
    A question's own `instructions` are not shared, so the Choice sees exactly what it
    saw before: the bare prompt as state, the descriptions as `criteria`.

    The descriptions are the same text the Choice ranks, in name order, because the
    question being asked is whether ANY of them claims this prompt. A shorter summary
    would be measuring a roster this repo does not ship.
    """
    roster = "\n".join(f"- {name}: {desc}" for name, desc in sorted(criteria.items()))
    return (
        "A user typed this message to a coding agent. The agent has these specialised "
        "skills available, each with the description its author wrote to decide when "
        "it should fire:\n\n"
        f"{roster}\n\n"
        "Should the agent load one of these skills to handle this message? Answer no "
        "when the message is ordinary work the agent would simply do, or is outside "
        "what any of these skills covers."
    )


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

    A pointer written to the raw `api_key:` field is read as a ref rather than
    returned as one. The two field names differ by six characters and the mistake is
    invisible in the config: before this check the pointer went out verbatim in an
    Authorization header, which spends a request and advertises a vault name to a
    third party. Recovering beats refusing here — the user's evident intent is to
    resolve it, and resolving lands it at rung 3 where a pointer belongs — but it is
    still a malformed config, so it says so on stderr rather than fixing it silently.

    That recovery is a **fallback, not a winner**: a configured `api_key_ref` is
    consulted before the misplaced value is used, and wins. Recovering before that
    check would let a typo in the
    raw field shadow a correctly-placed pointer, and the two fields can name different
    items — `api_key: op://Private/Linear/token` beside a valid TypeSafe `api_key_ref`
    would resolve the Linear pointer and put a full-account token for another service
    into this API's Authorization header. That is the harm typesafe_block exists to
    prevent, one level down, so it is closed here rather than documented.
    """
    block = typesafe_block(config_text)
    if not block:
        return None
    misplaced = None
    raw = re.search(r'^\s*api_key:\s*"?([^"\n#]+)"?', block, re.M)
    if raw:
        value = raw.group(1).strip()
        if value.startswith("op://"):
            misplaced = value
        elif value and value != PLACEHOLDER:
            return "raw", value
    ref = re.search(r'^\s*api_key_ref:\s*"?(op://[^"\n#]+)"?', block, re.M)
    if ref:
        if misplaced:
            say(
                f"warning: {redact_ref(misplaced)} is in `api_key:`, the raw-secret "
                "field, and is being ignored — `api_key_ref:` is set and wins. Delete "
                "the `api_key:` line."
            )
        return "ref", ref.group(1).strip()
    if misplaced:
        say(
            f"warning: {redact_ref(misplaced)} is in `api_key:`, which is the raw-secret "
            "field — reading it as `api_key_ref:`. Move it to silence this."
        )
        return "ref", misplaced
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
    """Rung 0, then rung 1, then the operator key file, then rung 3 — see
    dev_docs/auth_key_access.md.

    The order is the contract, not an implementation detail. Resolving a configured
    pointer before reading the environment means an exported key cannot override a
    stale or unreachable one, and the caller gets an `op` failure where it expected
    its own key to win. So every config is read first for a raw secret, then the
    environment, then the operator key file, and only then is a pointer resolved.
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

    # The operator key file: the same raw secret as rung 0, kept outside every
    # checkout. It sits after the environment so a one-off prefix can still
    # override it, and before the pointer because a raw secret beats a reference
    # — the same precedence rung 0 has over rung 3.
    if OPERATOR_KEY_FILE.exists():
        val = OPERATOR_KEY_FILE.read_text().strip()
        if val:
            return val

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
        f"No TypeSafe key. Put it in {OPERATOR_KEY_FILE} (mode 600), or export\n"
        "TYPESAFE_API_KEY. A raw api_key in "
        f"{LOCAL_CONFIG} still works and still\n"
        "wins, but it puts the secret inside the repo tree. See\n"
        "dev_docs/auth_key_access.md."
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
                },
                # Rides the same request. Questions are answered in parallel and in
                # isolation, so this one never becomes context for the Choice and the
                # Choice never becomes context for it — which is the point: the Noul
                # has to be able to say "none" while the Choice is still naming a
                # winner, exactly as they disagree on a negative prompt.
                "needs_skill": {
                    "type": "noul",
                    "instructions": noul_instructions(criteria),
                },
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
    needs_skill_truth: bool = True,
    noul_threshold: float = DEFAULT_NEEDS_SKILL,
    tiers: dict[str, str] | None = None,
) -> dict:
    """One row per prompt. `expects` says whether rows[i][0] is an expected skill.

    `needs_skill_truth` is the OTHER ground truth, and it is a separate axis: it says
    whether a skill should fire at all, which is defined for every row here — including
    the ambiguous probes, where no single skill is expected but some skill plainly is.
    So the ambiguous suite is unlabelled for the Choice and labelled for the Noul, and
    the two rates below have different denominators for that reason.
    """
    results, tokens = [], 0
    for label, prompt in rows:
        data = ask(key, prompt, criteria, model)
        answer = data["answers"]["skill"]
        winner, p_win, runner, p_run, margin = rank(answer["probabilities"])
        noul = data["answers"]["needs_skill"]["noul"]
        tokens += data.get("usage", {}).get("input_tokens", 0)
        fired = says_needs_skill(noul, noul_threshold)
        record = {
            "label": label,
            "tier": (tiers or {}).get(label),
            "prompt": prompt,
            "winner": winner,
            "p_winner": p_win,
            "runner_up": runner,
            "p_runner_up": p_run,
            "margin": margin,
            "confidence": answer.get("confidence"),
            "collision": is_collision(margin, threshold),
            "misfire": (winner != label) if expects else None,
            "needs_skill": noul,
            "needs_skill_truth": needs_skill_truth,
            # A false positive is the whole point of the negative suite: the Noul
            # claiming a skill for a prompt that should load none. Its mirror is only
            # defined on the positive suites, and is reported beside it so a Noul that
            # scores well by saying "no" to everything cannot read as a good result.
            "false_positive": fired and not needs_skill_truth,
            "false_negative": (not fired) and needs_skill_truth,
        }
        results.append(record)

        flags = [
            f
            for f, on in (
                ("MISFIRE", record["misfire"]),
                ("COLLISION", record["collision"]),
                ("FALSE POSITIVE", record["false_positive"]),
                ("FALSE NEGATIVE", record["false_negative"]),
            )
            if on
        ]
        conf = record["confidence"]
        conf_text = f"   conf {conf:.2f}" if conf is not None else ""
        flag_text = ("   << " + " ".join(flags)) if flags else ""
        # Progress goes to stderr so `--json` leaves stdout carrying exactly one
        # JSON document, which is what the record advertises it for.
        if needs_skill_truth:
            say(("expect " if expects else "probing ") + label)
        else:
            say("no-skill " + label)
        say(f"   {prompt[:78]!r}")
        say(f"   1. {winner:<28} {p_win:.3f}")
        say(f"   2. {runner:<28} {p_run:.3f}   margin {margin:.3f}{conf_text}")
        say(f"   needs_skill {noul:.2f}{flag_text}")
        say("")
    return {"results": results, "input_tokens": tokens}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--suite",
        choices=("manifest", "ambiguous", "both", "negative", "all"),
        default="both",
        help="`both` is the two original suites; `all` adds the negative one",
    )
    ap.add_argument(
        "--margin",
        type=float,
        default=DEFAULT_MARGIN,
        help=f"collision threshold (default {DEFAULT_MARGIN})",
    )
    ap.add_argument(
        "--noul-threshold",
        type=float,
        default=DEFAULT_NEEDS_SKILL,
        help=f"needs-a-skill threshold (default {DEFAULT_NEEDS_SKILL})",
    )
    ap.add_argument(
        "--runs",
        type=int,
        default=1,
        help="passes over the selected suites; the record's numbers used 4",
    )
    ap.add_argument("--json", action="store_true", help="emit the raw records")
    ap.add_argument(
        "--model",
        default=MODEL,
        help=f"model to measure against (default {MODEL}, the version the record grades)",
    )
    args = ap.parse_args(argv)
    if args.runs < 1:
        ap.error("--runs must be at least 1")
    # A Noul is a probability, so anything outside 0-1 is not a threshold. Checked
    # before resolve_key so a typo cannot burn a paid run: out of range, the rate comes
    # back 0/64 or 64/64 and reads as a result rather than as an error. The comparison
    # also rejects nan, since every comparison against nan is False -- which matters
    # beyond the rate, because `--json` would then write the bare token `NaN` into the
    # records artifact, and that is not JSON a strict parser will read back.
    if not 0.0 <= args.noul_threshold <= 1.0:
        ap.error("--noul-threshold must be between 0 and 1")

    root = repo_root()
    key = resolve_key(root)
    criteria = load_descriptions(root)
    tiers = {label: tier for tier, label, _ in NEGATIVE_PROBES}
    negative_rows = [(label, prompt) for _, label, prompt in NEGATIVE_PROBES]

    passes, tokens = [], 0
    for run in range(args.runs):
        if args.runs > 1:
            say(f"--- run {run + 1}/{args.runs} ---\n")
        suites, records = {}, []
        if args.suite in ("manifest", "both", "all"):
            rows = load_manifest_cases(root)
            say(f"{len(criteria)} descriptions, {len(rows)} manifest cases\n")
            out = run_suite(
                key,
                criteria,
                rows,
                args.margin,
                True,
                args.model,
                noul_threshold=args.noul_threshold,
            )
            suites["manifest"], tokens = out, tokens + out["input_tokens"]
            records += out["results"]
        if args.suite in ("ambiguous", "both", "all"):
            say(
                f"{len(criteria)} descriptions, {len(AMBIGUOUS_PROBES)} ambiguous probes\n"
            )
            out = run_suite(
                key,
                criteria,
                AMBIGUOUS_PROBES,
                args.margin,
                False,
                args.model,
                noul_threshold=args.noul_threshold,
            )
            suites["ambiguous"], tokens = out, tokens + out["input_tokens"]
            records += out["results"]
        if args.suite in ("negative", "all"):
            say(f"{len(criteria)} descriptions, {len(negative_rows)} negative probes\n")
            out = run_suite(
                key,
                criteria,
                negative_rows,
                args.margin,
                False,
                args.model,
                needs_skill_truth=False,
                noul_threshold=args.noul_threshold,
                tiers=tiers,
            )
            suites["negative"], tokens = out, tokens + out["input_tokens"]
            records += out["results"]
        passes.append({"suites": suites, "records": records})

    if args.json:
        print(
            json.dumps(
                {
                    "model": args.model,
                    "runs": args.runs,
                    "noul_threshold": args.noul_threshold,
                    "passes": [{"suites": p["suites"]} for p in passes],
                },
                indent=2,
            )
        )
        return 0

    print("=" * 66)
    print(f"model:      {args.model}")
    print(f"runs:       {args.runs}")
    report_choice(passes, args.margin)
    report_noul(passes, args.noul_threshold)
    print(f"tokens: {tokens} in   (~${tokens / 1e6 * 0.042:.4f})")
    return 0


def rate_line(name: str, hits: list[int], totals: list[int]) -> str:
    """`name: 2/16 = 12.5%  (per run: 1, 3, 2, 2)`.

    Per-run counts are printed rather than a mean because section 1's finding was that
    the spread is the story — a rate averaged over four runs hides the same variance a
    single run does, just more convincingly.
    """
    total = sum(totals)
    pct = f"{sum(hits) / total * 100:.1f}%" if total else "n/a"
    spread = ", ".join(str(h) for h in hits)
    per_run = f"  (per run: {spread})" if len(hits) > 1 else ""
    return f"{name} {sum(hits)}/{total} = {pct}{per_run}"


def report_choice(passes: list[dict], margin: float) -> None:
    """The original Choice report, unchanged in substance."""
    # Only the manifest suite carries an expected skill, so it is the only half a
    # misfire is defined over; the other suites record `misfire: None`.
    mis_hits = [sum(1 for r in p["records"] if r["misfire"]) for p in passes]
    mis_totals = [
        sum(1 for r in p["records"] if r["misfire"] is not None) for p in passes
    ]
    col_hits = [sum(1 for r in p["records"] if r["collision"]) for p in passes]
    col_totals = [len(p["records"]) for p in passes]
    print(rate_line("misfires:  ", mis_hits, mis_totals) + " labelled cases")
    print(rate_line("collisions:", col_hits, col_totals) + f" (margin < {margin})")
    every = [r for p in passes for r in p["records"]]
    for r in sorted(every, key=lambda r: r["margin"])[:3]:
        print(
            f"   tightest: {r['winner']} vs {r['runner_up']} = {r['margin']:.3f}"
            f" (conf {r['confidence']:.2f})"
        )


def report_noul(passes: list[dict], threshold: float) -> None:
    """The no-skill Noul: the false-positive rate, and the mirror that keeps it honest.

    A Noul that answered "no skill" to everything would score a perfect 0% false
    positives, so the false-NEGATIVE rate over the positive suites is printed beside it.
    Neither number means much without the other.
    """
    negatives = [
        [r for r in p["records"] if not r["needs_skill_truth"]] for p in passes
    ]
    positives = [[r for r in p["records"] if r["needs_skill_truth"]] for p in passes]
    if not any(negatives) and not any(positives):
        return
    print(f"--- no-skill noul (threshold {threshold}) ---")
    if any(negatives):
        print(
            rate_line(
                "false positives:",
                [sum(1 for r in rs if r["false_positive"]) for rs in negatives],
                [len(rs) for rs in negatives],
            )
            + " negative prompts"
        )
        for tier in ("plain", "near"):
            per_run = [[r for r in rs if r["tier"] == tier] for rs in negatives]
            if any(per_run):
                print(
                    "   "
                    + rate_line(
                        f"{tier}:",
                        [sum(1 for r in rs if r["false_positive"]) for rs in per_run],
                        [len(rs) for rs in per_run],
                    )
                )
        worst = sorted(
            (r for rs in negatives for r in rs),
            key=lambda r: -r["needs_skill"],
        )[:3]
        for r in worst:
            print(
                f"   highest: {r['needs_skill']:.2f}  {r['label']}"
                f"  -> would load {r['winner']}"
            )
    if any(positives):
        print(
            rate_line(
                "false negatives:",
                [sum(1 for r in rs if r["false_negative"]) for rs in positives],
                [len(rs) for rs in positives],
            )
            + " positive prompts"
        )


if __name__ == "__main__":
    sys.exit(main())
