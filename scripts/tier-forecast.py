#!/usr/bin/env python3
"""Forecast a task's merge tier and check its Intent block, for /promote-tasks.

The promoter lists the paths a task is expected to change and passes them here
with the task body. This script makes the deterministic half of the decision:

- It reads `.github/merge-policy.yml` from the default branch (`--ref`, default
  `origin/HEAD`) with `git show`, never from the working tree. The lander reads
  the policy from the PR's base branch for the same reason: a branch must not
  tier itself with its own edits to the policy.
- It tiers the paths with `merge-tier.py`'s forecast, which runs the path rules
  only and normalizes each path first.
- It parses the task's `## Intent` block.

Usage:
  python3 scripts/tier-forecast.py --task <file|-> [--ref origin/HEAD] [--repo-dir .]

`--task` holds one object: {"paths": ["..."], "body": "..."}. `body` is the
task's markdown and may be omitted.

Output: one JSON object on stdout, then a final `FORECAST: auto|human|skipped`
line. Exit 0 for any verdict; a usage error exits 2.
  {"forecast": "auto" | "human" | "skipped",
   "reasons": ["..."],
   "intent": {"state": "present" | "partial" | "absent", "missing": ["Finish", ...]},
   "consult_bar": 85}
`consult_bar` is the policy's key of that name (default 85): the least
confidence at which the Intent confirmer's verdict lets a human-forecast task
promote. It is the default whenever no policy was loaded.

The verdicts:
  skipped  `--ref` resolves but carries no policy file. The repo has not opted in
           to merge tiers, and with no lander the Intent rule gates nothing, so
           the promoter skips both. The reason is `no merge policy`.
  human    a path the policy keeps human, an empty path list, a policy that is
           present but malformed, a `--ref` that does not resolve, or input that
           cannot be read. Every failure is human, never a crash.
  auto     every path matches an `auto` glob.

The Intent block is a `## Intent` heading (any case, optional trailing colon,
level 2 only) followed by three labelled lines, `Change:`, `Premise:` and
`Finish:`, each with a value. A leading list marker and bold labels
(`- **Change:** ...` or `- **Change**: ...`) are accepted. The block ends at the
next heading. Fenced code is ignored: a fence closes only on the character that
opened it, at least as many times, so a nested example stays inside.

`state` is `present` when all three have values, `partial` when the heading is
there but some are missing (named in `missing`), and `absent` when there is no
heading. It is reported whatever the forecast. Deciding what a missing block
means is the promoter's job: skills/task/references/intent-confirmation.md
(added in #1382).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLICY_PATH = ".github/merge-policy.yml"
INTENT_LABELS = ("Change", "Premise", "Finish")
INTENT_HEADING_RE = re.compile(r"^##\s+Intent:?\s*$", re.IGNORECASE)
HEADING_RE = re.compile(r"^#{1,6}\s")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
LABEL_RE = re.compile(
    r"^(?:[-*]\s+)?(?:\*\*)?(?P<label>[A-Za-z]+)(?:\*\*)?:(?:\*\*)?\s*(?P<value>.*)$"
)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec is not None and spec.loader is not None, f"cannot load {filename}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tier_mod = _load("merge_tier", "merge-tier.py")


class ReadError(Exception):
    pass


def read_policy(repo_dir, ref):
    """The policy text at `ref`, or None when `ref` has no policy file."""
    try:
        resolved = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "rev-parse",
                "--verify",
                "--quiet",
                f"{ref}^{{commit}}",
            ],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ReadError(f"cannot run git: {exc}") from exc
    if resolved.returncode != 0:
        hint = (
            "; run `git remote set-head origin --auto`, or pass --ref"
            if ref == "origin/HEAD"
            else ""
        )
        raise ReadError(f"`{ref}` does not resolve to a commit{hint}")
    # `git show` exits 128 both for a missing path and for an unreadable object,
    # so ask the tree whether the path exists. Only a clean "absent" is no
    # policy; any other failure is an error, which the caller makes human.
    listed = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-tree", "--full-tree", ref, "--", POLICY_PATH],
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        raise ReadError(f"cannot list `{ref}`: {listed.stderr.strip()}")
    if not listed.stdout.strip():
        return None
    shown = subprocess.run(
        ["git", "-C", str(repo_dir), "show", f"{ref}:{POLICY_PATH}"],
        capture_output=True,
    )
    if shown.returncode != 0:
        detail = shown.stderr.decode(errors="replace").strip()
        raise ReadError(f"cannot read {POLICY_PATH} at `{ref}`: {detail}")
    try:
        return shown.stdout.decode()
    except UnicodeDecodeError as exc:
        raise ReadError(f"{POLICY_PATH} at `{ref}`: cannot decode: {exc}") from exc


def parse_intent(body):
    """{"state": present|partial|absent, "missing": [labels]} for a task body."""
    # Fenced code is quoted text, not the document: an example Intent block in a
    # fence must not count, and a `# comment` in one must not end the real block.
    lines: list = []
    fence = None  # the open fence's marker run, e.g. "````"
    for line in body.splitlines():
        stripped = line.strip()
        run = FENCE_RE.match(stripped)
        if fence is None:
            if run:
                fence = run.group(0)
                continue
            lines.append(line)
        elif run and run.group(0)[0] == fence[0] and len(run.group(0)) >= len(fence):
            if stripped == run.group(0):
                fence = None
    start = next(
        (i for i, line in enumerate(lines) if INTENT_HEADING_RE.match(line)), None
    )
    if start is None:
        return {"state": "absent", "missing": list(INTENT_LABELS)}
    found = set()
    for line in lines[start + 1 :]:
        if HEADING_RE.match(line):
            break
        match = LABEL_RE.match(line.strip())
        if (
            match
            and match.group("label") in INTENT_LABELS
            and match.group("value").strip()
        ):
            found.add(match.group("label"))
    missing = [label for label in INTENT_LABELS if label not in found]
    return {"state": "partial" if missing else "present", "missing": missing}


def load_task(source):
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text()
        task = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read task input: {exc}") from exc
    if not isinstance(task, dict):
        raise ValueError("task input must be a JSON object")
    paths = task.get("paths")
    body = task.get("body", "")
    if not (isinstance(paths, list) and all(isinstance(p, str) for p in paths)):
        raise ValueError("`paths` must be a list of strings")
    if not isinstance(body, str):
        raise ValueError("`body` must be a string")
    return paths, body


def evaluate(task_source, repo_dir, ref):
    try:
        paths, body = load_task(task_source)
    except ValueError as exc:
        return {
            "forecast": "human",
            "reasons": [f"forecast: input invalid: {exc}"],
            "intent": {"state": "absent", "missing": list(INTENT_LABELS)},
        }
    intent = parse_intent(body)
    try:
        text = read_policy(repo_dir, ref)
    except ReadError as exc:
        return {"forecast": "human", "reasons": [f"forecast: {exc}"], "intent": intent}
    if text is None:
        return {"forecast": "skipped", "reasons": ["no merge policy"], "intent": intent}
    with tempfile.TemporaryDirectory() as tmp:
        policy_file = Path(tmp) / "merge-policy.yml"
        policy_file.write_text(text, encoding="utf-8")
        try:
            policy = tier_mod.load_policy(policy_file)
        except tier_mod.PolicyError as exc:
            return {
                "forecast": "human",
                "reasons": [f"forecast: policy invalid: {exc}"],
                "intent": intent,
            }
    tier, reasons = tier_mod.forecast(paths, policy)
    return {
        "forecast": tier,
        "reasons": reasons,
        "intent": intent,
        "consult_bar": policy["consult_bar"],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--task", required=True, help="task paths and body as JSON, or - for stdin"
    )
    p.add_argument(
        "--ref", default="origin/HEAD", help="the ref to read the policy from"
    )
    p.add_argument(
        "--repo-dir", default=".", help="the checkout to read the policy from"
    )
    args = p.parse_args(argv)

    result = evaluate(args.task, args.repo_dir, args.ref)
    # The bar the Intent confirmer must reach. Only a loaded policy can set
    # it; on every other path the forecast is human or skipped and the
    # default stands.
    result.setdefault("consult_bar", tier_mod.DEFAULT_CONSULT_BAR)
    print(json.dumps(result))
    print(f"FORECAST: {result['forecast']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
