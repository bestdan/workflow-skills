#!/usr/bin/env python3
"""Resolve a Jira transition id out of a `getTransitionsForJiraIssue` response.

Five handlers (`jira-claim.md`, `jira-complete.md`, `jira-promote.md`,
`jira.md`, `jira-archive.md`) each hand-walk "pick the right transition out of
`transitions[]`". Two policies exist:

- **Category mode** (claim, complete) — filter to a target `statusCategory`,
  drop excluded names, then disambiguate by name or stop. `jira-complete.md`
  records why the order matters: filtering cancellation-style names before
  counting is what stops a board whose only terminal transition is `Canceled`
  from being completed with the wrong resolution.
- **Exact mode** (promote, create, archive) — match a configured status name
  exactly, falling back to the transition's own name, and skip when absent.

## Output contract

Steps that consume this run as separate tool calls with no shared shell
state, so **stdout is the contract**:

    <id>\\t<to.name>              # success

    AMBIGUOUS                     # or NONE
    <candidate name>              # zero or more, one per line
    ...

Exit 0 on success, 2 when no single transition resolves, 1 on malformed
input or arguments.

Usage:
  python3 jira-resolve-transition.py --category indeterminate \\
      --exclude 'hold|block|review|validation|wait' --prefer 'in progress' < transitions.json
  python3 jira-resolve-transition.py --exact 'Selected for Development' < transitions.json
"""

import argparse
import json
import os
import re
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shape import ShapeError, expect  # noqa: E402


class Transition(NamedTuple):
    """One entry of `transitions[]`, validated."""

    id: str
    own_name: object  # str, or None when the response omits it
    to_name: str
    category: str


def parse_transitions(payload):
    """Return the validated `Transition` list, or raise ShapeError."""
    raw = expect(payload, "transitions", list, "response")
    transitions = []
    for i, entry in enumerate(raw):
        where = f"transitions[{i}]"
        tid = expect(entry, "id", str, where)
        to = expect(entry, "to", dict, where)
        to_name = expect(to, "name", str, f"{where}.to")
        category_obj = expect(to, "statusCategory", dict, f"{where}.to")
        category = expect(category_obj, "key", str, f"{where}.to.statusCategory")
        own_name = entry.get("name")
        own_name = own_name if isinstance(own_name, str) else None
        transitions.append(Transition(tid, own_name, to_name, category))
    return transitions


def resolve_category(transitions, category, exclude, prefer):
    """Category-mode resolution. Returns (Transition, None) or (None, report).

    `report` is `(status, [candidate names])` with `status` one of
    `AMBIGUOUS`/`NONE`, used only when no single transition resolves.
    """
    filtered = [t for t in transitions if t.category == category]
    if exclude:
        pattern = re.compile(exclude, re.IGNORECASE)
        excluded_names = filtered
        filtered = [t for t in filtered if not pattern.search(t.to_name)]
    else:
        excluded_names = filtered

    if len(filtered) == 1:
        return filtered[0], None
    if len(filtered) == 0:
        return None, ("NONE", [t.to_name for t in excluded_names])

    # Several left after category + exclude.
    if prefer:
        pattern = re.compile(prefer, re.IGNORECASE)
        preferred = [t for t in filtered if pattern.search(t.to_name)]
        if len(preferred) == 1:
            return preferred[0], None
        if len(preferred) == 0:
            return None, ("NONE", [t.to_name for t in filtered])
        return None, ("AMBIGUOUS", [t.to_name for t in preferred])

    return None, ("AMBIGUOUS", [t.to_name for t in filtered])


def resolve_exact(transitions, status):
    """Exact-mode resolution: `to.name`, falling back to the transition's own
    `name`. Returns (Transition, None) or (None, ("NONE"/"AMBIGUOUS", names))."""
    lowered = status.lower()
    matches = [t for t in transitions if t.to_name.lower() == lowered]
    if not matches:
        matches = [
            t for t in transitions if t.own_name and t.own_name.lower() == lowered
        ]
    if not matches:
        return None, ("NONE", [])
    if len(matches) > 1:
        return None, ("AMBIGUOUS", [t.to_name for t in matches])
    return matches[0], None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--category", help="target statusCategory.key to filter to")
    parser.add_argument(
        "--exclude",
        help="regex (case-insensitive) of to.name values to drop before counting",
    )
    parser.add_argument(
        "--prefer",
        help="regex (case-insensitive) of to.name values to prefer when several remain",
    )
    parser.add_argument(
        "--exact", help="status name to match exactly (to.name, then name)"
    )
    args = parser.parse_args(argv)

    if bool(args.category) == bool(args.exact):
        parser.error("exactly one of --category or --exact is required")
    if args.exact and (args.exclude or args.prefer):
        parser.error("--exclude/--prefer only apply to --category mode")

    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(f"invalid JSON on stdin: {exc}", file=sys.stderr)
        return 1

    try:
        transitions = parse_transitions(payload)
    except ShapeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.category:
        found, report = resolve_category(
            transitions, args.category, args.exclude, args.prefer
        )
    else:
        found, report = resolve_exact(transitions, args.exact)

    if found is not None:
        print(f"{found.id}\t{found.to_name}")
        return 0

    status, names = report
    print(status)
    for name in names:
        print(name)
    return 2


if __name__ == "__main__":
    sys.exit(main())
