#!/usr/bin/env python3
"""Classify a pull request as `auto` or `human` merge tier.

Rules decide the tier and nothing else does: the inputs are the PR's changed
paths and line counts, its title, body and labels, the repo's merge policy,
and the AGENT_LAND kill switch. No model output and no co-review evidence is
read here; review evidence can only demote a PR further, and the lander
(land-pr.sh, not yet written) is to apply those demotions after this script.

The default is `human`. A PR is `auto` only when no rule below demotes it.
Every rule that fires is reported, so a human tier always says why.

Usage:
  python3 scripts/merge-tier.py --pr-json <file|-> --policy <file> \
    [--agent-land on|off]

`--pr-json` holds one object:
  {"files": [{"path": "...", "additions": N, "deletions": N,
               "status": "...", "previous_path": "..."}, ...],
   "title": "...", "body": "...", "labels": ["...", ...]}
`status` is required and `previous_path` optional; they are GitHub REST's
`status` and `previous_filename` from `pulls/<n>/files`. A missing or unknown
`status` makes the input invalid, because a file the caller did not describe
could be a rename. The path rules check a renamed or copied file under both its
paths, so a move out of an always-human path stays human, and a `renamed` or
`copied` file without `previous_path` is human.

`--policy` is the repo's `.github/merge-policy.yml`, read by the caller from
the PR's base branch, never from its head. It must use the YAML subset
`load_policy` accepts; anything outside it fails closed. Keys:
  auto:          path globs a PR may touch and stay `auto`
  always_human:  path globs that force `human`, whatever `auto` says
  size_cap:      most changed lines (additions + deletions) for `auto`; default 300
  allowed_types: Conventional Commit title types allowed `auto`
  repo_rules:    ids of repo-specific lints from REPO_RULES

Globs: `**` matches across directories, `*` and `?` within one path segment.

Output: one JSON object `{"tier": ..., "reasons": [...]}` on stdout, then a
final `TIER: auto|human` line. Exit 0 for any verdict, `human` included. Any
input or policy error is a `human` verdict, never a crash: a missing,
unreadable or malformed policy gives the reason `policy invalid: <detail>`.
A usage error exits 2.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_SIZE_CAP = 300
# The policy that grants autonomy, and the script that applies it, are always
# human-tier: a PR must not be able to widen its own tier.
SELF_PATHS = (".github/merge-policy.yml", "scripts/merge-tier.py")
# Title types that cut a release in this plugin (.github/workflows/release.yml).
RELEASING_TYPES = ("feat", "fix", "perf")
POLICY_KEYS = ("auto", "always_human", "size_cap", "allowed_types", "repo_rules")
LIST_KEYS = ("auto", "always_human", "allowed_types", "repo_rules")
TITLE_RE = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<bang>!)?: \S")
BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE: ", re.MULTILINE)
HOLD_LABEL = "merge:hold"
MOVE_STATUSES = ("renamed", "copied")
# GitHub's file statuses, REST spelling; GraphQL's upper-case ones are accepted.
FILE_STATUSES = ("added", "removed", "modified", "changed", "unchanged", *MOVE_STATUSES)


class PolicyError(Exception):
    pass


# --- policy -----------------------------------------------------------------


def _scalar(text, where):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    if not text:
        raise PolicyError(f"{where}: empty value")
    return text


def _strip_comment(line):
    # A comment starts at a `#` that opens the line or follows whitespace.
    match = re.search(r"(^|\s)#", line)
    return line[: match.start()] if match else line


def load_policy(path):
    """Parse the policy subset: `key: scalar`, `key: [a, b]`, and `key:`
    followed by `  - item` lines. Anything else raises PolicyError."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise PolicyError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise PolicyError(f"cannot decode {path}: {exc}") from exc

    raw: dict = {}
    current = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        where = f"line {lineno}"
        line = _strip_comment(line).rstrip()
        if not line.strip():
            continue
        if line.startswith(" "):
            item = line.strip()
            if current is None or not item.startswith("- "):
                raise PolicyError(f"{where}: unexpected indented line")
            raw[current].append(_scalar(item[2:], where))
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not re.fullmatch(r"[a-z_]+", key):
            raise PolicyError(f"{where}: expected `key: value`")
        if key not in POLICY_KEYS:
            raise PolicyError(f"{where}: unknown key `{key}`")
        if key in raw:
            raise PolicyError(f"{where}: duplicate key `{key}`")
        value = value.strip()
        current = None
        if key in LIST_KEYS:
            if not value:
                raw[key] = []
                current = key
            elif value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                raw[key] = (
                    [_scalar(v, where) for v in inner.split(",")] if inner else []
                )
            else:
                raise PolicyError(f"{where}: `{key}` must be a list")
        else:
            raw[key] = value

    return _validate(raw)


def _validate(raw):
    policy: dict = {k: list(raw.get(k, [])) for k in LIST_KEYS}
    cap = raw.get("size_cap", str(DEFAULT_SIZE_CAP))
    if not re.fullmatch(r"[1-9][0-9]*", cap):
        raise PolicyError(f"`size_cap` must be a positive integer, got `{cap}`")
    policy["size_cap"] = int(cap)
    for rule in policy["repo_rules"]:
        if rule not in REPO_RULES:
            raise PolicyError(f"unknown repo rule `{rule}`")
    return policy


# --- globs ------------------------------------------------------------------


def glob_regex(glob):
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path, globs):
    return any(glob_regex(g).match(path) for g in globs)


# --- repo rules -------------------------------------------------------------


def prompt_paths_need_releasing_type(pr, title_type):
    """skills/ and commands/ are runtime prompt text: a change there reaches
    every installed user only when its title cuts a release."""
    prompt = [
        p
        for f in pr["files"]
        for p in file_paths(f)
        if p.startswith(("skills/", "commands/"))
    ]
    # No type at all is already reported as an unconventional title.
    if prompt and title_type is not None and title_type not in RELEASING_TYPES:
        return [
            f"repo rule prompt-paths-need-releasing-type: {prompt[0]} changes with "
            f"non-releasing type `{title_type}`"
        ]
    return []


REPO_RULES = {"prompt-paths-need-releasing-type": prompt_paths_need_releasing_type}


# --- tiering ----------------------------------------------------------------


def file_paths(f):
    """A file's path, and its previous path when it was renamed or copied."""
    previous = f.get("previous_path")
    return [f["path"], previous] if previous and previous != f["path"] else [f["path"]]


def _count(value):
    # bool is an int subclass, and a negative count would shrink the size total.
    return type(value) is int and value >= 0


def load_pr(source):
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text()
        pr = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read PR input: {exc}") from exc
    if not isinstance(pr, dict):
        raise ValueError("PR input must be a JSON object")
    files = pr.get("files")
    if not isinstance(files, list):
        raise ValueError("`files` must be a list")
    for f in files:
        if not (
            isinstance(f, dict)
            and isinstance(f.get("path"), str)
            and _count(f.get("additions"))
            and _count(f.get("deletions"))
            and isinstance(f.get("previous_path", ""), str)
        ):
            raise ValueError(
                "each file needs a string `path` and non-negative integer `additions`/`deletions`"
            )
        status = f.get("status")
        if not (isinstance(status, str) and status.lower() in FILE_STATUSES):
            raise ValueError(
                f"{f['path']}: `status` must be one of {', '.join(FILE_STATUSES)}"
            )
        f["status"] = status.lower()
    for key in ("title", "body"):
        if not isinstance(pr.get(key, ""), str):
            raise ValueError(f"`{key}` must be a string")
    labels = pr.get("labels", [])
    if not (isinstance(labels, list) and all(isinstance(x, str) for x in labels)):
        raise ValueError("`labels` must be a list of strings")
    return {
        "files": files,
        "title": pr.get("title", ""),
        "body": pr.get("body") or "",
        "labels": labels,
    }


def classify(pr, policy, agent_land):
    reasons = []
    if agent_land == "off":
        reasons.append("AGENT_LAND=off: agent landing is switched off")
    if HOLD_LABEL in pr["labels"]:
        reasons.append(f"label {HOLD_LABEL}")

    title = TITLE_RE.match(pr["title"])
    title_type = title.group("type") if title else None
    if not title:
        reasons.append("title is not a Conventional Commit")
    else:
        if title.group("bang"):
            reasons.append("breaking change: title ends its type with `!`")
        if title_type not in policy["allowed_types"]:
            reasons.append(f"title type `{title_type}` not in allowed_types")
    if BREAKING_RE.search(pr["body"]):
        reasons.append("breaking change: BREAKING CHANGE footer")

    if not pr["files"]:
        reasons.append("no changed files")
    for f in pr["files"]:
        if f["status"] in MOVE_STATUSES and not f.get("previous_path"):
            reasons.append(f"{f['path']} is {f['status']} with no previous_path")
        for path in file_paths(f):
            if path in SELF_PATHS:
                reasons.append(
                    f"{path} is the merge policy or tier script, always human"
                )
            elif matches(path, policy["always_human"]):
                reasons.append(f"{path} matches always_human")
            elif not matches(path, policy["auto"]):
                reasons.append(f"{path} matches no auto glob")

    size = sum(f["additions"] + f["deletions"] for f in pr["files"])
    if size > policy["size_cap"]:
        reasons.append(f"size {size} changed lines over size_cap {policy['size_cap']}")

    for rule in policy["repo_rules"]:
        reasons.extend(REPO_RULES[rule](pr, title_type))

    if reasons:
        return "human", reasons
    return "auto", [
        f"all {len(pr['files'])} paths match auto, size {size} within cap, type `{title_type}` allowed"
    ]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--pr-json", required=True, help="PR facts as JSON, or - for stdin")
    p.add_argument("--policy", required=True, help="the repo's merge policy file")
    p.add_argument(
        "--agent-land",
        choices=("on", "off"),
        default="on",
        help="the repo's AGENT_LAND variable; off forces human",
    )
    args = p.parse_args(argv)

    try:
        pr = load_pr(args.pr_json)
    except ValueError as exc:
        tier, reasons = "human", [f"input invalid: {exc}"]
    else:
        try:
            policy = load_policy(args.policy)
        except PolicyError as exc:
            tier, reasons = "human", [f"policy invalid: {exc}"]
        else:
            tier, reasons = classify(pr, policy, args.agent_land)

    print(json.dumps({"tier": tier, "reasons": reasons}))
    print(f"TIER: {tier}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
