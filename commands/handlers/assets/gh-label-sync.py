#!/usr/bin/env python3
"""Provision a repo with the gh-issue handler's label vocabulary.

Reads commands/handlers/assets/labels.yml, compares it against the labels a repo
actually has, creates the missing ones, and reports — never deletes — the ones it
does not recognise. A repo may carry unrelated labels of its own.

Idempotent by construction: it creates only what is missing, so a second run
creates nothing and exits 0.

Read-mostly. The only mutation is `gh label create` for a missing label, and only
under --apply; without it the script reports what it would create and changes
nothing.

Stdlib only, invoked as `python3 …/gh-label-sync.py` like the other handler
assets. labels.yml is parsed by the shared _labels.py rather than by PyYAML,
which is why that file is restricted to a small, fixed subset of YAML.

Usage:
  python3 gh-label-sync.py --repo owner/name            # report only
  python3 gh-label-sync.py --repo owner/name --apply    # create the missing ones
  python3 gh-label-sync.py --repo owner/name --json     # machine-readable
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import (  # noqa: E402
    DEFAULT_LABELS_FILE,
    VocabularyError,  # noqa: F401  (re-exported: callers catch it)
    expected_labels,
    load_vocabulary,
)


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def existing_labels(repo):
    code, out, err = run_gh(
        ["label", "list", "--repo", repo, "--limit", "500", "--json", "name"]
    )
    if code != 0:
        raise SystemExit(
            f"gh label list failed for {repo}: {err.strip() or out.strip()}"
        )
    return [entry["name"] for entry in json.loads(out or "[]")]


def create_label(repo, name, color):
    code, out, err = run_gh(["label", "create", name, "--repo", repo, "--color", color])
    if code != 0:
        raise SystemExit(
            f"gh label create {name} failed for {repo}: {err.strip() or out.strip()}"
        )


def sync(repo, labels_file, apply):
    groups, colors = load_vocabulary(labels_file)
    expected = expected_labels(groups, colors)
    present = set(existing_labels(repo))

    missing = [name for name in expected if name not in present]
    unknown = sorted(present - set(expected))

    if apply:
        for name in missing:
            create_label(repo, name, expected[name])

    return {
        "repo": repo,
        "expected": len(expected),
        "created" if apply else "would_create": missing,
        "unknown": unknown,
        "applied": apply,
    }


def report(result):
    verb = "Created" if result["applied"] else "Would create"
    pending = result.get("created", result.get("would_create", []))
    print(f"{result['repo']}: {result['expected']} labels in the vocabulary")
    if pending:
        print(f"{verb} {len(pending)}: {', '.join(pending)}")
    else:
        print("No changes — every vocabulary label is already present.")
    if result["unknown"]:
        print(
            f"Unknown labels present ({len(result['unknown'])}), left untouched: {', '.join(result['unknown'])}"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument(
        "--apply", action="store_true", help="create the missing labels"
    )
    parser.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = sync(args.repo, args.labels_file, args.apply)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
