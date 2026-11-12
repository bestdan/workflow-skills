#!/usr/bin/env python3
"""Choose the test units `scripts/check.sh --changed` runs for this branch's diff.

A test unit is one job of the full gate: a `test/*.bats` file, a
`scripts/test-*.sh` harness, or `scripts/test-spawn-orchestrator.sh`. The
selection is a heuristic over file names, measured in #1492 before it was
built. It is safe to be wrong only because plain `just check` and CI always run
the full gate.

The changed files are everything differing from the merge-base with the default
branch, plus untracked files: the same set `scripts/lint-shell.sh --fast` lints.
Set CHECK_CHANGED_BASE to a ref to diff against its merge-base instead, for a
branch stacked on another.

A changed file selects:

- itself, when it is a test unit;
- every test unit whose text names it: by repo path for markdown, by file name
  otherwise, or by module name for a hyphenated or underscored Python file;
- every test unit that names a test helper (`scripts/test_*.py`, a bats
  helper) which names it, since a `scripts/test-*.sh` wrapper names the
  unittest file it runs rather than the code under test;
- for a file in a subdirectory, every test unit naming that directory, which
  is how a Python package's modules reach the tests that import them;
- `scripts/test-spawn-orchestrator.sh`, for a file that one of its parts
  (`scripts/test-spawn-orchestrator-*.sh`) names;
- `scripts/test-validate.sh`, for a file under `skills/`, `commands/` or
  `agents/`, which the real-tree validator reads.

The result is the full gate instead when a changed file is gate infrastructure,
or selects nothing: a file no test names may still be read by one through a
glob or a loop, so an unplaced file is never taken as safe to skip. The one
exception is `dev_docs/`, which no test unit reads apart from the two that
`check.sh` always runs.

Usage: scripts/check-select.py
Prints `full <reason>`, or `subset` followed by one `unit <path>` line per test
unit. Exits 2 when the changed files cannot be listed.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

DEFAULT_REFS = ("origin/main", "main", "origin/master", "master")

# Infrastructure: a change here can reach every unit, so it runs the full gate.
FULL_FILES = {
    "scripts/check.sh",
    "scripts/check-select.py",
    "scripts/test-shell.sh",
    "scripts/ensure-bats.sh",
    "test/test_helper.bash",
    "justfile",
    "mise.toml",
    "pyproject.toml",
    "uv.lock",
    "dprint.json",
    ".gitmodules",
}
FULL_PREFIXES = ("scripts/lib/", "test/vendor/", ".github/")

# check.sh always runs the two units that read the real dev_docs/ tree.
UNREAD_PREFIXES = ("dev_docs/",)
ORCHESTRATOR = "scripts/test-spawn-orchestrator.sh"
VALIDATED_PREFIXES = ("skills/", "commands/", "agents/")
VALIDATOR_TEST = "scripts/test-validate.sh"
# A token shorter than this matches too much text to mean anything.
MIN_TOKEN = 6


def is_unit(path: str) -> bool:
    if path.startswith("test/") and path.endswith(".bats") and path.count("/") == 1:
        return True
    if not (path.startswith("scripts/test-") and path.endswith(".sh")):
        return False
    if path.endswith("-live.sh") or path == "scripts/test-shell.sh":
        return False
    # The orchestrator's parts run inside scripts/test-spawn-orchestrator.sh.
    if path.startswith("scripts/test-spawn-orchestrator-"):
        return False
    return path.count("/") == 1


def is_helper(path: str) -> bool:
    name = Path(path).name
    if (
        path.startswith("scripts/")
        and name.startswith("test_")
        and name.endswith(".py")
    ):
        return True
    return path.startswith("test/") and path.endswith(".bash")


def orchestrator_part(path: str) -> bool:
    return path.startswith("scripts/test-spawn-orchestrator-") and path.endswith(".sh")


def tokens(path: str) -> List[str]:
    """The strings that, found in a test's text, mean the test names `path`."""
    p = Path(path)
    if p.suffix == ".md":
        return [path]
    found = [p.name]
    if p.suffix == ".py" and ("-" in p.stem or "_" in p.stem):
        found += [p.stem, p.stem.replace("-", "_")]
    return [t for t in found if len(t) >= MIN_TOKEN]


def dir_tokens(path: str) -> List[str]:
    parts = path.split("/")
    if len(parts) < 3:
        return []
    return [t for t in ("/".join(parts[:-1]) + "/", parts[-2]) if len(t) >= MIN_TOKEN]


def select(changed: Iterable[str], texts: Dict[str, str]) -> Set[str]:
    """Units to run for `changed`. Raises FullGate when it needs the full gate.

    `texts` maps every file this module may search to its contents.
    """
    units = {p for p in texts if is_unit(p)}
    helpers = {p for p in texts if is_helper(p)}
    parts = {p for p in texts if orchestrator_part(p)}

    def naming(subject_tokens: List[str], within: Set[str]) -> Set[str]:
        return {p for p in within if any(t in texts[p] for t in subject_tokens)}

    chosen: Set[str] = set()
    for path in changed:
        if path in FULL_FILES or path.startswith(FULL_PREFIXES):
            raise FullGate(f"{path} is gate infrastructure")
        found: Set[str] = set()
        if path in units:
            found.add(path)
        own = tokens(path)
        if orchestrator_part(path) or naming(own, parts - {path}):
            found.add(ORCHESTRATOR)
        found |= naming(own, units - {path})
        for helper in naming(own, helpers - {path}):
            found |= naming(tokens(helper), units)
        found |= naming(dir_tokens(path), units - {path})
        if path.startswith(VALIDATED_PREFIXES) and VALIDATOR_TEST in units:
            found.add(VALIDATOR_TEST)
        if not found and not path.startswith(UNREAD_PREFIXES):
            raise FullGate(f"no test names {path}")
        chosen |= found
    return chosen


class FullGate(Exception):
    """The diff needs the full gate; the message says why."""


def _git(*args: str) -> Optional[str]:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True, timeout=30
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def changed_files() -> Optional[List[str]]:
    override = os.environ.get("CHECK_CHANGED_BASE")
    for ref in (override,) if override else DEFAULT_REFS:
        base = (_git("merge-base", "HEAD", ref) or "").strip()
        if base:
            break
    else:
        return None
    # --no-renames lists a renamed file under its old path too, so the tests
    # that still name the old path are selected.
    diff = _git("diff", "--name-only", "--no-renames", base)
    untracked = _git("ls-files", "--others", "--exclude-standard")
    if diff is None or untracked is None:
        return None
    return sorted(set(diff.split("\n") + untracked.split("\n")) - {""})


def tree_texts() -> Dict[str, str]:
    """Tracked and untracked, non-ignored files, so a new test is a unit too."""
    listing = _git("ls-files", "--cached", "--others", "--exclude-standard") or ""
    texts: Dict[str, str] = {}
    for path in listing.split("\n"):
        if not path or path.startswith(("test/vendor/", "dev_docs/")):
            continue
        try:
            texts[path] = Path(path).read_text(errors="ignore")
        except OSError:
            continue
    return texts


def main(argv: List[str]) -> int:
    if argv:
        print(__doc__.split("Usage:")[1].strip().split("\n")[0], file=sys.stderr)
        return 2
    changed = changed_files()
    if changed is None:
        print("full no merge-base with the base branch")
        return 0
    try:
        chosen = select(changed, tree_texts())
    except FullGate as e:
        print(f"full {e}")
        return 0
    print("subset")
    for unit in sorted(chosen):
        print(f"unit {unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
