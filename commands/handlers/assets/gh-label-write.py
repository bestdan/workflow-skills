#!/usr/bin/env python3
"""Write an issue's complete label set atomically: validate, then full-set PATCH.

This is the ONLY supported way for the gh-issue handler to change an issue's
status, routing, priority or estimate. Two measured facts force its shape, and
neither half is sufficient alone:

- `gh issue edit --add-label X --remove-label Y` is **not atomic** — it produced
  8 HTTP request lines. A crash between them strands an issue carrying two
  status labels, or none. So transitions cannot be incremental.
- `PATCH /repos/{owner}/{repo}/issues/{n}` with a full `labels` array replaces
  the set in **one request**, so no intermediate state exists. But a raw REST
  write **auto-creates unknown labels** — measured: posting `zz-undefined-label`
  created it. `gh`'s own label commands reject an undefined label; REST does not.

Hence: validate every name against labels.yml and enforce the invariants first,
locally, exiting non-zero before any network call — then issue one PATCH. A typo
like `est:7` fails loudly instead of silently entering the repo's namespace.

Never add `--add-label` / `--remove-label` to a state-transition path.

Note: the local `sandbox-network-guard` hook blocks non-GET `gh api`, so this
needs an allowlist entry to run unsandboxed locally. GitHub Actions and cloud
routines have no such hook.

Usage:
  python3 gh-label-write.py --repo owner/name --issue 142 \
      --labels status:3_started,auto:eligible,prio:1,est:3 --apply

  # completion: a closed issue carries no status rung, and keeps prio/est
  python3 gh-label-write.py --repo owner/name --issue 142 \
      --labels auto:eligible,prio:1,est:3 --done --apply

Without --apply it validates and prints the PATCH it would send, touching
nothing.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import (  # noqa: E402
    AT_MOST_ONE,
    DEFAULT_LABELS_FILE,
    EXACTLY_ONE,
    expected_labels,
    load_vocabulary,
)


class InvalidLabelSet(Exception):
    """The requested set is not writable: unknown name, or a broken invariant."""


def group_of(label):
    return label.split(":", 1)[0]


def validate(labels, vocabulary, done=False):
    """Raise InvalidLabelSet unless `labels` is a legal complete set.

    Runs entirely locally and before any network call, which is the whole point:
    an unknown name reaching the PATCH would be created rather than rejected.
    """
    seen = []
    for label in labels:
        if label in seen:
            raise InvalidLabelSet(f"duplicate label: {label}")
        seen.append(label)

    unknown = [label for label in labels if label not in vocabulary]
    if unknown:
        raise InvalidLabelSet(
            "not in labels.yml (a raw REST write would create these): "
            + ", ".join(unknown)
        )

    counts = {}
    for label in labels:
        counts[group_of(label)] = counts.get(group_of(label), 0) + 1

    for group in EXACTLY_ONE:
        found = counts.get(group, 0)
        # A closed issue carries no status rung — "done" is the absence of one.
        if group == "status" and done:
            if found:
                raise InvalidLabelSet(
                    f"--done means no status rung, but {found} `status:` label(s) given"
                )
            continue
        if found != 1:
            raise InvalidLabelSet(f"expected exactly one `{group}:` label, got {found}")

    for group in AT_MOST_ONE:
        found = counts.get(group, 0)
        if found > 1:
            raise InvalidLabelSet(f"expected at most one `{group}:` label, got {found}")

    return list(labels)


def run_gh(args, stdin=None):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin)
    return proc.returncode, proc.stdout, proc.stderr


def patch_labels(repo, issue, labels):
    """One PATCH carrying the complete set. Never --add-label/--remove-label."""
    body = json.dumps({"labels": labels})
    code, out, err = run_gh(
        ["api", "--method", "PATCH", f"repos/{repo}/issues/{issue}", "--input", "-"],
        stdin=body,
    )
    if code != 0:
        raise SystemExit(f"PATCH {repo}#{issue} failed: {err.strip() or out.strip()}")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--issue", required=True, help="issue number")
    parser.add_argument(
        "--labels",
        required=True,
        help="the COMPLETE desired set, comma-separated — it replaces what is there",
    )
    parser.add_argument(
        "--done",
        action="store_true",
        help="completion write: assert no `status:` label (a closed issue has no rung)",
    )
    parser.add_argument("--apply", action="store_true", help="send the PATCH")
    parser.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    groups, colors = load_vocabulary(args.labels_file)
    vocabulary = expected_labels(groups, colors)
    requested = [label.strip() for label in args.labels.split(",") if label.strip()]

    try:
        labels = validate(requested, vocabulary, done=args.done)
    except InvalidLabelSet as exc:
        print(f"refusing to write {args.repo}#{args.issue}: {exc}", file=sys.stderr)
        return 2

    if args.apply:
        patch_labels(args.repo, args.issue, labels)

    result = {
        "repo": args.repo,
        "issue": args.issue,
        "labels": labels,
        "applied": args.apply,
    }
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        verb = "Wrote" if args.apply else "Would write"
        print(f"{verb} {args.repo}#{args.issue}: {', '.join(labels)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
