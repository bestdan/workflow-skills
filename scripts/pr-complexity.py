#!/usr/bin/env python3
"""Score a diff's reviewable size and complexity, the same way on every run.

/co-review opens its summary with this report and feeds the score into its
scope (split) judgment. The judgment stays qualitative; the score is the
reproducible input to it, so two reviewers of the same diff see the same number.

Reads a unified diff (`gh pr diff`, `git diff`) from stdin or `--diff-file`,
drops excluded paths (generated code and lockfiles), and measures:

    R  reviewable lines changed (additions + deletions)
    F  reviewable files changed
    P  distinct top-level directories touched; repo-root files count as one
    H  diff hunks
    E  distinct change categories among code, test, docs, config, native

    score = min(40, R / 50) + min(20, F * 1.5) + clamp((P - 1) * 4, 0, 20)
          + min(10, H * 0.5) + clamp((E - 1) * 3, 0, 10)

rounded half-up to a whole number, banded `< 30` Low, `30-59` Moderate,
`>= 60` High. R, F, P and H and their terms match Betterment/mobile's
measure-pr-complexity action. E is the one departure: mobile leaves it to the
reviewer's judgment, because "feature" and "refactor" cannot be told apart from
a path. Here it is classified from paths alone, with feature and refactor
merged into one `code` category, so the whole score is deterministic.

`--untracked <path>` (repeatable) counts a file `git diff` does not show as
wholly added, in one hunk, for /co-review --local.

Configuration comes from flags or from the `complexity:` block of a
`.co-review.yml` passed as `--config`:

    complexity:
      split_threshold: 60
      exclude:
        - '\\.pb\\.go$'

`exclude` entries are Python regexes searched against the repo-relative path
and add to the defaults; `--no-default-excludes` drops the defaults. A missing
`--config` file is not an error (the config is optional).

Output, on stdout, exit 0:

    ### Complexity: 72 / 100 (High)
    Reviewable: +830 / -320 across 18 files · 5 packages · 34 hunks · code+test
    COMPLEXITY: score=72 band=High threshold=60 split=required r=1150 f=18 p=5 h=34 e=2 excluded=3

`split=required` when score >= threshold, else `split=not-required`. With
`--json`, one JSON object carrying the same fields instead. Exit 2 is a usage
error (bad flag, bad regex, unreadable config or diff file).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_THRESHOLD = 60

DEFAULT_EXCLUDES = (
    r"\.(fakes|freezed|g|gql|mapper)\.dart$",
    r"(^|/)__generated__/",
    r"(^|/)(pubspec|Podfile|Gemfile|Cargo|poetry|uv|composer|flake)\.lock$",
    r"(^|/)Package\.resolved$",
    r"(^|/)(package-lock|npm-shrinkwrap)\.json$",
    r"(^|/)(pnpm-lock\.yaml|yarn\.lock|bun\.lockb?|go\.sum)$",
    r"\.min\.(js|css)$",
)

CATEGORY_ORDER = ("code", "test", "docs", "config", "native")

NATIVE = re.compile(
    r"(^|/)(ios|android|macos)/|\.(swift|kt|kts|m|mm|gradle|xcconfig|pbxproj|plist)$"
)
TEST = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|integration_test|test_driver)/"
    r"|(^|/)test[_-][^/]*$|_test\.[^/]+$|\.(test|spec)\.[^/]+$|_spec\.[^/]+$"
)
DOCS = re.compile(
    r"(^|/)(docs?|dev_docs)/|\.(md|mdx|rst|adoc|txt)$|(^|/)(LICENSE|CHANGELOG)[^/]*$"
)
CONFIG = re.compile(
    r"(^|/)\.github/|\.(ya?ml|json|toml|ini|cfg|conf|lock)$|(^|/)\.[^/]+$"
    r"|(^|/)(Makefile|justfile|Dockerfile|Brewfile)$"
)


def category(path: str) -> str:
    for name, pattern in (
        ("native", NATIVE),
        ("test", TEST),
        ("docs", DOCS),
        ("config", CONFIG),
    ):
        if pattern.search(path):
            return name
    return "code"


@dataclass
class FileDiff:
    path: str
    additions: int = 0
    deletions: int = 0
    hunks: int = 0


@dataclass
class Measurement:
    additions: int = 0
    deletions: int = 0
    files: int = 0
    packages: int = 0
    hunks: int = 0
    categories: list[str] = field(default_factory=list)
    excluded: int = 0


def _strip_prefix(raw: str, prefix: str) -> str:
    raw = raw.rstrip("\n").split("\t", 1)[0]
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    return raw[len(prefix) :] if raw.startswith(prefix) else raw


def parse_diff(text: str) -> list[FileDiff]:
    """Split a unified git diff into per-file counts.

    The path comes from `+++ b/<path>` (or `--- a/<path>` for a deletion, or
    `rename to` for a pure rename); the `diff --git` line is only a fallback,
    because a path containing ` b/` makes it ambiguous. Inside a hunk the old
    and new line counts from the header bound the body, so a removed line that
    itself begins `---` is never mistaken for a file header.
    """
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    old_left = new_left = 0
    minus_path: str | None = None
    hunk_re = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
    for line in text.splitlines():
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                cur.additions += 1  # type: ignore[union-attr]
                new_left -= 1
            elif line.startswith("-"):
                cur.deletions += 1  # type: ignore[union-attr]
                old_left -= 1
            elif line.startswith("\\"):
                pass
            else:
                old_left -= 1
                new_left -= 1
            continue
        if line.startswith("diff --git "):
            m = re.match(r'^diff --git "?a/(.*?)"? "?b/(.*?)"?$', line)
            cur = FileDiff(path=m.group(2) if m else line[len("diff --git ") :])
            files.append(cur)
            minus_path = None
            continue
        if cur is None:
            continue
        if line.startswith("--- "):
            minus_path = (
                None
                if line[4:].startswith("/dev/null")
                else _strip_prefix(line[4:], "a/")
            )
        elif line.startswith("+++ "):
            if line[4:].startswith("/dev/null"):
                if minus_path:
                    cur.path = minus_path
            else:
                cur.path = _strip_prefix(line[4:], "b/")
        elif line.startswith("rename to "):
            cur.path = line[len("rename to ") :]
        else:
            m = hunk_re.match(line)
            if m:
                cur.hunks += 1
                old_left = int(m.group(1)) if m.group(1) is not None else 1
                new_left = int(m.group(2)) if m.group(2) is not None else 1
    return files


def untracked_file(path: str) -> FileDiff:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise SystemExit(_usage(f"cannot read untracked file {path}: {exc}"))
    if b"\0" in data:
        return FileDiff(path=path)
    lines = len(data.decode("utf-8", "replace").splitlines())
    return FileDiff(path=path, additions=lines, hunks=1 if lines else 0)


def measure(files: list[FileDiff], excludes: list[re.Pattern[str]]) -> Measurement:
    kept = [f for f in files if not any(p.search(f.path) for p in excludes)]
    cats = {category(f.path) for f in kept}
    return Measurement(
        additions=sum(f.additions for f in kept),
        deletions=sum(f.deletions for f in kept),
        files=len(kept),
        packages=len(
            {f.path.split("/", 1)[0] if "/" in f.path else "(root)" for f in kept}
        ),
        hunks=sum(f.hunks for f in kept),
        categories=[c for c in CATEGORY_ORDER if c in cats],
        excluded=len(files) - len(kept),
    )


def score(m: Measurement) -> int:
    r = m.additions + m.deletions
    total = (
        min(40.0, r / 50)
        + min(20.0, m.files * 1.5)
        + min(20.0, max(0.0, (m.packages - 1) * 4.0))
        + min(10.0, m.hunks * 0.5)
        + min(10.0, max(0.0, (len(m.categories) - 1) * 3.0))
    )
    return int(math.floor(total + 0.5))


def band(value: int) -> str:
    if value >= 60:
        return "High"
    if value >= 30:
        return "Moderate"
    return "Low"


def load_config(path: str) -> tuple[int | None, list[str]]:
    """Read `complexity.split_threshold` and `complexity.exclude` from a
    `.co-review.yml`. Only that block's two keys are understood: a scalar
    threshold and a block list of quoted or bare strings."""
    p = Path(path)
    if not p.exists():
        return None, []
    try:
        lines = p.read_text().splitlines()
    except OSError as exc:
        raise SystemExit(_usage(f"cannot read config {path}: {exc}"))
    threshold: int | None = None
    excludes: list[str] = []
    in_block = in_exclude = False
    block_indent = None
    for raw in lines:
        stripped = (
            raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        )
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip())
        text = stripped.strip()
        if indent == 0:
            in_block = text == "complexity:"
            in_exclude = False
            block_indent = None
            continue
        if not in_block:
            continue
        if block_indent is None:
            block_indent = indent
        is_item = in_exclude and text.startswith("- ")
        if indent == block_indent and not is_item:
            in_exclude = False
            key, _, value = text.partition(":")
            value = value.strip()
            if key == "split_threshold":
                try:
                    threshold = int(value)
                except ValueError:
                    raise SystemExit(
                        _usage(
                            f"{path}: complexity.split_threshold must be an integer, got {value!r}"
                        )
                    )
            elif key == "exclude":
                in_exclude = True
                if value not in ("", "[]"):
                    raise SystemExit(
                        _usage(f"{path}: complexity.exclude must be a block list")
                    )
        elif is_item:
            item = text[2:].strip()
            if len(item) >= 2 and item[0] == item[-1] and item[0] in "'\"":
                quote, item = item[0], item[1:-1]
                if quote == "'":
                    item = item.replace("''", "'")
            excludes.append(item)
    return threshold, excludes


def _usage(msg: str) -> int:
    print(f"pr-complexity: {msg}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--diff-file", help="unified diff to read instead of stdin")
    ap.add_argument("--untracked", action="append", default=[], metavar="PATH")
    ap.add_argument("--config", help="a .co-review.yml whose complexity: block to read")
    ap.add_argument("--exclude", action="append", default=[], metavar="REGEX")
    ap.add_argument("--no-default-excludes", action="store_true")
    ap.add_argument(
        "--threshold", type=int, help=f"split threshold (default {DEFAULT_THRESHOLD})"
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg_threshold, cfg_excludes = (
        load_config(args.config) if args.config else (None, [])
    )
    threshold = args.threshold if args.threshold is not None else cfg_threshold
    if threshold is None:
        threshold = DEFAULT_THRESHOLD

    sources = [] if args.no_default_excludes else list(DEFAULT_EXCLUDES)
    sources += cfg_excludes + args.exclude
    try:
        excludes = [re.compile(s) for s in sources]
    except re.error as exc:
        return _usage(f"bad exclude regex: {exc}")

    if args.diff_file:
        try:
            text = Path(args.diff_file).read_text(errors="replace")
        except OSError as exc:
            return _usage(f"cannot read diff file: {exc}")
    else:
        text = sys.stdin.read()

    files = parse_diff(text) + [untracked_file(p) for p in args.untracked]
    m = measure(files, excludes)
    value = score(m)
    split = "required" if value >= threshold else "not-required"
    cats = "+".join(m.categories) or "none"
    r = m.additions + m.deletions

    if args.json:
        print(
            json.dumps(
                {
                    "score": value,
                    "band": band(value),
                    "threshold": threshold,
                    "split": split,
                    "r": r,
                    "adds": m.additions,
                    "dels": m.deletions,
                    "f": m.files,
                    "p": m.packages,
                    "h": m.hunks,
                    "e": len(m.categories),
                    "categories": m.categories,
                    "excluded": m.excluded,
                }
            )
        )
        return 0

    noun = "file" if m.files == 1 else "files"
    pkg = "package" if m.packages == 1 else "packages"
    hunk = "hunk" if m.hunks == 1 else "hunks"
    print(f"### Complexity: {value} / 100 ({band(value)})")
    print(
        f"Reviewable: +{m.additions} / -{m.deletions} across {m.files} {noun} · "
        f"{m.packages} {pkg} · {m.hunks} {hunk} · {cats}"
    )
    print(
        f"COMPLEXITY: score={value} band={band(value)} threshold={threshold} split={split} "
        f"r={r} f={m.files} p={m.packages} h={m.hunks} e={len(m.categories)} excluded={m.excluded}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
