#!/usr/bin/env python3
"""Shared reader for the gh-issue handler's label vocabulary (labels.yml).

Imported by gh-label-sync.py (which provisions a repo) and gh-label-write.py
(which writes an issue's label set). Both need the same vocabulary and the same
invariants, and labels.yml is the single source for them — so the parser lives
here rather than being restated in each caller.

Stdlib only, so the handler assets stay runnable as plain `python3 …` with no
dependency. That is why labels.yml is restricted to the small, fixed subset of
YAML that load_vocabulary() accepts.
"""

from pathlib import Path

DEFAULT_LABELS_FILE = Path(__file__).resolve().parent / "labels.yml"

# Groups an open issue must carry exactly one of, and groups it may carry at most
# one of. A closed ("done") issue carries no status rung — see labels.yml.
EXACTLY_ONE = ("status", "auto")
AT_MOST_ONE = ("prio", "est")


class VocabularyError(Exception):
    """labels.yml is malformed or does not fit the supported subset."""


def load_vocabulary(path=DEFAULT_LABELS_FILE):
    """Parse labels.yml into ({group: [value, ...]}, {group: color}).

    Supports exactly two line shapes, which is all labels.yml is allowed to use:
    a top-level `key: [a, b, c]` inline list, and a `colors:` block whose
    two-space-indented children are `key: value` scalars. Anything else raises,
    loudly, rather than being silently skipped — a dropped group would mean a
    repo provisioned with a hole in its state model, or a write helper that lets
    an out-of-vocabulary label through.
    """
    groups = {}
    colors = {}
    in_colors = False

    for lineno, raw in enumerate(Path(path).read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        if line.startswith("  "):
            if not in_colors:
                raise VocabularyError(
                    f"{path}:{lineno}: indented line outside a colors: block"
                )
            key, sep, value = line.strip().partition(":")
            if not sep or not value.strip():
                raise VocabularyError(f"{path}:{lineno}: expected `name: color`")
            colors[key.strip()] = value.strip()
            continue

        in_colors = False
        key, sep, value = line.partition(":")
        if not sep:
            raise VocabularyError(f"{path}:{lineno}: expected `key: value`")
        key, value = key.strip(), value.strip()

        if key == "colors" and not value:
            in_colors = True
            continue
        if not (value.startswith("[") and value.endswith("]")):
            raise VocabularyError(
                f"{path}:{lineno}: expected an inline list for `{key}`"
            )
        groups[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]

    if not groups:
        raise VocabularyError(f"{path}: no label groups found")
    missing_colors = sorted(set(groups) - set(colors))
    if missing_colors:
        raise VocabularyError(
            f"{path}: no color for group(s): {', '.join(missing_colors)}"
        )
    return groups, colors


def expected_labels(groups, colors):
    """Flatten the vocabulary into an ordered {label_name: color} mapping."""
    return {
        f"{group}:{value}": colors[group]
        for group, values in groups.items()
        for value in values
    }
