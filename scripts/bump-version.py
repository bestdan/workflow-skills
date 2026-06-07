#!/usr/bin/env python3
"""Conventional-Commits version bump for the workflow-skills plugin.

Reads the current version from ``.claude-plugin/plugin.json``, inspects the
Conventional-Commit messages since the last ``v*`` git tag, and decides the
bump level:

    BREAKING CHANGE / ``type!:``  -> major
    ``feat:``                     -> minor
    ``fix:`` / ``perf:``          -> patch
    anything else (chore, docs, ci, test, refactor, build, style, ...) -> none

If nothing release-worthy landed, no version is produced -- that is the
"meaningful changes only" filter, so docs/chore merges never bump.

With ``--apply`` it writes the new version into BOTH manifests, kept in sync
and as plain ``X.Y.Z`` (``scripts/validate.py`` rejects a ``v`` prefix or
pre-release), and prepends a grouped section to ``CHANGELOG.md``. It does NOT
commit, tag, push, or create a GitHub release -- that is the release workflow's
job (``.github/workflows/release.yml``). Keeping the math here makes it
runnable and reviewable locally:

    python3 scripts/bump-version.py            # dry run: print the decision
    python3 scripts/bump-version.py --apply    # write manifests + CHANGELOG

In GitHub Actions it appends machine-readable results to ``$GITHUB_OUTPUT``
(``bumped``, ``old_version``, ``new_version``) and, when ``RELEASE_NOTES_FILE``
is set, writes the release notes there so the workflow never has to interpolate
free text through YAML.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKET = ROOT / ".claude-plugin" / "marketplace.json"
CHANGELOG = ROOT / "CHANGELOG.md"

# Conventional-Commit header: `type(scope)!: description`. scope and `!` optional.
HEADER_RE = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<bang>!)?:\s*(?P<desc>.+)$")
BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE", re.MULTILINE)
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
VTAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

LEVELS = {"major": 3, "minor": 2, "patch": 1}

CHANGELOG_HEADER = (
    "# Changelog\n\n"
    "All notable changes to this plugin. Sections are auto-generated from\n"
    "[Conventional Commits](https://www.conventionalcommits.org/) on merge to\n"
    "`main` by `.github/workflows/release.yml`.\n"
)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def last_version_tag() -> str | None:
    """Most recent `vX.Y.Z` tag, or None if the repo has never been tagged."""
    tags = git("tag", "--list", "v*", "--sort=-v:refname").splitlines()
    return next((t for t in tags if VTAG_RE.match(t)), None)


def commits_since(ref: str | None) -> list[tuple[str, str, str]]:
    """Return (short_hash, subject, body) for each non-merge commit in range."""
    rng = f"{ref}..HEAD" if ref else "HEAD"
    out = git("log", "--no-merges", "--format=%h%x1f%s%x1f%b%x1e", rng)
    commits = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        fields = record.split("\x1f")
        short, subject = fields[0], fields[1]
        body = fields[2] if len(fields) > 2 else ""
        commits.append((short.strip(), subject.strip(), body))
    return commits


def level_for(subject: str, body: str) -> str | None:
    m = HEADER_RE.match(subject)
    if not m:
        return None
    if m.group("bang") or BREAKING_RE.search(body or ""):
        return "major"
    if m.group("type") == "feat":
        return "minor"
    if m.group("type") in ("fix", "perf"):
        return "patch"
    return None


def next_version(version: str, level: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def grouped_notes(commits: list[tuple[str, str, str]]) -> str:
    """Markdown release notes grouped into Breaking / Features / Fixes."""
    breaking: list[str] = []
    feats: list[str] = []
    fixes: list[str] = []
    for short, subject, body in commits:
        m = HEADER_RE.match(subject)
        if not m:
            continue
        line = f"- {m.group('desc').strip()} ({short})"
        if m.group("bang") or BREAKING_RE.search(body or ""):
            breaking.append(line)
        elif m.group("type") == "feat":
            feats.append(line)
        elif m.group("type") in ("fix", "perf"):
            fixes.append(line)

    sections = []
    if breaking:
        sections.append("### ⚠ Breaking Changes\n\n" + "\n".join(breaking))
    if feats:
        sections.append("### Features\n\n" + "\n".join(feats))
    if fixes:
        sections.append("### Fixes\n\n" + "\n".join(fixes))
    return "\n\n".join(sections)


def set_version(path: Path, old: str, new: str) -> None:
    """Swap the single `"version": "X.Y.Z"` field, preserving all other bytes."""
    text = path.read_text()
    needle = f'"version": "{old}"'
    if text.count(needle) != 1:
        sys.exit(f"{path}: expected exactly one {needle!r}, found {text.count(needle)}")
    path.write_text(text.replace(needle, f'"version": "{new}"'))


def update_changelog(version: str, notes: str) -> None:
    today = date.today().isoformat()
    section = f"## [{version}] - {today}\n\n{notes}\n"
    if CHANGELOG.exists():
        lines = CHANGELOG.read_text().splitlines(keepends=True)
        idx = next(
            (i for i, ln in enumerate(lines) if ln.startswith("## [")), len(lines)
        )
        new = "".join(lines[:idx]) + section + "\n" + "".join(lines[idx:])
    else:
        new = CHANGELOG_HEADER + "\n" + section
    CHANGELOG.write_text(new)


def emit_output(bumped: bool, old: str, new: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a") as fh:
        fh.write(f"bumped={'true' if bumped else 'false'}\n")
        fh.write(f"old_version={old}\n")
        fh.write(f"new_version={new}\n")


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    current = json.loads(PLUGIN.read_text())["version"]
    if not SEMVER_RE.match(current):
        sys.exit(f"{PLUGIN}: version {current!r} is not plain X.Y.Z")

    tag = last_version_tag()
    if tag is None:
        # First ever run: no boundary to diff against. Don't guess a bump from
        # all of history -- report the current version so the workflow can seed
        # a baseline `v<current>` tag, after which every merge has a boundary.
        print(
            f"No `v*` tag yet; current version is {current}. "
            "Seeding baseline -- no bump this run."
        )
        emit_output(False, current, current)
        return 0

    commits = commits_since(tag)
    levels = [lvl for lvl in (level_for(s, b) for _, s, b in commits) if lvl]
    if not levels:
        print(f"No release-worthy commits since {tag} (staying at {current}).")
        emit_output(False, current, current)
        return 0

    level = max(levels, key=lambda lvl: LEVELS[lvl])
    new = next_version(current, level)
    notes = grouped_notes(commits)
    print(f"Bump ({level}): {current} -> {new}\n")
    print(notes)

    if apply:
        set_version(PLUGIN, current, new)
        set_version(MARKET, current, new)
        update_changelog(new, notes)
        notes_file = os.environ.get("RELEASE_NOTES_FILE")
        if notes_file:
            Path(notes_file).write_text(notes + "\n")
        print(f"\nApplied. Updated {PLUGIN.name}, {MARKET.name}, CHANGELOG.md")

    emit_output(True, current, new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
