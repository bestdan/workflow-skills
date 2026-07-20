#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Deterministic scan/rank/readiness for the task-loop's repo-pr file path.

Replaces the ad-hoc scan/rank/dependency-readiness/promote-gate logic
re-derived in repo-pr-execute.md §1-2, list-tasks.md §2-3, promote-tasks.md §1,
doctor.md checks 4-5.

Implements the rules in skills/task/SKILL.md VERBATIM (that file is the
canonical source; this script does not invent variants):

- Ranking: priority tier (urgent > high > medium > low), then impact/size
  descending (a card with no impact, or a missing/invalid size, has no score
  and ranks last within its priority tier — never dropped), then oldest
  `created` first. See "Ranking" in SKILL.md.
- Multi-blocker readiness: a card is dependency_ready iff every entry in
  is_blocked_by resolves to a file that is absent, or exists with
  status: done. Slugs resolve globally across dev_docs/tasks/**/*.md by
  filename stem (not directory-scoped).
- Epics (type: epic) are never ranked/executed; they roll up their members
  (parent: <epic-slug>, or living in the epic's plan directory tree,
  recursively).
- expired iff expires < today AND status is non-terminal (status != done).
- Promote HIGH gate: the 7 *deterministic* checks from SKILL.md's confidence
  check / promote-tasks.md step 2. The 8th check ("scope plausibly fits
  size 5") is NL judgment and stays in prose — NOT implemented here; a
  human/agent still makes that call (see `high_gate_check_8_scope_fits_size_5`
  below).

Usage:
    scripts/task-scan.py [task_dir] [--prs <json>] [--archive-candidates]

    task_dir              Directory to scan recursively for *.md task files.
                           Default: dev_docs/tasks (relative to cwd, NOT a
                           git-root lookup — a known consumer-repo path bug
                           with hardcoding `git rev-parse` root is being fixed
                           separately; this script takes the dir as an
                           argument instead).
    --prs <json>          Optional path to a JSON file of tracker-issue PRs,
                           for future tracker-issue merge. No-op / passthrough
                           for the repo-pr handler today.
    --archive-candidates  Extension point stub for a future mode that groups
                           status: done cards for /archive-tasks. Not
                           implemented yet — see archive_candidates() below.

Emits one JSON document on stdout. Fail-closed: malformed frontmatter (YAML
that fails to parse) exits non-zero with a clear message on stderr, rather
than silently skipping the file.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import yaml

FIBONACCI = {1, 2, 3, 5}
TERMINAL_STATUSES = {"done"}
# Ranking tier order — see "Ranking" in skills/task/SKILL.md. Any priority not
# in this map (missing, or an unrecognized value) sorts after every known
# tier so a malformed priority never wins a tie against a valid one.
PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
UNKNOWN_PRIORITY_RANK = len(PRIORITY_ORDER)

HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
BULLET_RE = re.compile(r"^\s*[-*]\s+\S")


def die(msg: str) -> None:
    print(f"task-scan: {msg}", file=sys.stderr)
    sys.exit(1)


def split_frontmatter(path: Path) -> tuple[object, str]:
    """Return (data, body). data is a dict, or an Exception if the YAML is
    invalid, or None if there is no frontmatter block (not a task card)."""
    text = path.read_text()
    if not text.startswith("---"):
        return None, text
    m = re.search(r"\n---\s*\n", text)
    if not m:
        return None, text
    try:
        data = yaml.safe_load(text[3 : m.start()]) or {}
    except yaml.YAMLError as e:
        return e, text[m.end() :]
    return data, text[m.end() :]


def parse_date(v) -> datetime.date | None:
    """YAML parses unquoted ISO dates (`created: 2026-03-23`) into
    datetime.date already; a quoted string needs an explicit parse."""
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, str):
        try:
            return datetime.date.fromisoformat(v.strip())
        except ValueError:
            return None
    return None


def as_list(v) -> list[str]:
    """is_blocked_by (and similar) may be a single string or a list of
    strings; a single string behaves exactly as a one-element list."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def body_sections(body: str) -> dict[str, list[str]]:
    """Split a task body into {lowercased heading text: [content lines]},
    keyed on ## (or deeper) headings only."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        m = HEADING_RE.match(line)
        if m:
            current = m.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def has_acceptance_criteria_bullet(sections: dict[str, list[str]]) -> bool:
    lines = sections.get("acceptance criteria", [])
    return any(BULLET_RE.match(line) for line in lines)


def has_open_questions_or_tbd_content(sections: dict[str, list[str]]) -> bool:
    for key in ("open questions", "tbd"):
        lines = sections.get(key)
        if lines is None:
            continue
        if any(line.strip() for line in lines):
            return True
    return False


def high_gate_check_8_scope_fits_size_5() -> None:
    """Deliberately unimplemented. Per SKILL.md's confidence check, whether
    the described scope plausibly fits within size 5 (~300 lines / ~5 files)
    is model judgment weighing the stated size, the ## Task steps, and
    related_files breadth — not a keyword scan. /promote-tasks still makes
    this call in prose; this script only reports the 7 deterministic checks
    and leaves a `note` field for the caller to apply check 8 itself."""
    raise NotImplementedError("check 8 is NL judgment; kept in prose, not here")


def promote_gate(data: dict, body: str) -> dict:
    """The 7 deterministic HIGH checks from SKILL.md's confidence check /
    promote-tasks.md step 2. Returns {checks: {name: bool}, high: bool,
    reasons: [str], note: str}. `high` reflects only the deterministic
    checks — the 8th (scope) is not evaluated here."""
    sections = body_sections(body)
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    required_present = all(
        data.get(f) not in (None, "")
        for f in ("title", "priority", "size", "created", "source_branch", "expires")
    )
    checks["required_fields_present"] = required_present
    if not required_present:
        reasons.append("missing a required field")

    size = data.get("size")
    size_valid = (
        isinstance(size, int) and not isinstance(size, bool) and size in FIBONACCI
    )
    checks["size_valid"] = size_valid
    if not size_valid:
        reasons.append(f"size '{size}' must be one of 1/2/3/5")

    related_files = data.get("related_files")
    tags = data.get("tags") or []
    has_related = isinstance(related_files, list) and len(related_files) >= 1
    has_research_tag = isinstance(tags, list) and "scope: research" in tags
    checks["related_files_or_research"] = has_related or has_research_tag
    if not checks["related_files_or_research"]:
        reasons.append("related_files is empty and tags lacks 'scope: research'")

    checks["has_acceptance_criteria"] = has_acceptance_criteria_bullet(sections)
    if not checks["has_acceptance_criteria"]:
        reasons.append("no ## Acceptance Criteria bullet")

    checks["no_open_questions_or_tbd"] = not has_open_questions_or_tbd_content(sections)
    if not checks["no_open_questions_or_tbd"]:
        reasons.append("## Open Questions or ## TBD has content")

    checks["priority_not_urgent"] = data.get("priority") != "urgent"
    if not checks["priority_not_urgent"]:
        reasons.append("priority is urgent")

    har = data.get("human_approval_requested")
    checks["human_approval_not_requested"] = har in (None, False)
    if not checks["human_approval_not_requested"]:
        reasons.append("human_approval_requested is true")

    return {
        "checks": checks,
        "high": all(checks.values()),
        "reasons": reasons,
        "note": (
            "deterministic checks only — check 8 (scope plausibly fits size 5) "
            "is NL judgment and is NOT evaluated here; the caller still makes "
            "that call"
        ),
    }


def rank_key(data: dict):
    """Sort key implementing SKILL.md "Ranking": priority tier, then
    impact/size descending (no-score sorts last within tier), then oldest
    created first."""
    priority = data.get("priority")
    priority_rank = PRIORITY_ORDER.get(priority, UNKNOWN_PRIORITY_RANK)

    impact = data.get("impact")
    size = data.get("size")
    size_valid = (
        isinstance(size, int) and not isinstance(size, bool) and size in FIBONACCI
    )
    impact_valid = (
        isinstance(impact, int) and not isinstance(impact, bool) and impact in FIBONACCI
    )
    has_score = size_valid and impact_valid
    score = (impact / size) if has_score else 0.0

    created = parse_date(data.get("created"))
    # A missing/unparseable created date can't win an age tie-break honestly,
    # so it sorts after every dated card (max date) rather than arbitrarily
    # winning "oldest first".
    created_sort = created or datetime.date.max

    # 0 before 1 so has_score sorts before no-score; -score for descending.
    return (priority_rank, 0 if has_score else 1, -score, created_sort)


def epic_slug(path: Path, task_dir: Path) -> str:
    stem = path.stem
    return stem[: -len("_plan")] if stem.endswith("_plan") else stem


def is_epic_member(
    card_path: Path, card_data: dict, epic_path: Path, slug: str
) -> bool:
    if card_data.get("parent") == slug:
        return True
    # Directory-tree membership only applies to plan-style epics
    # (`<name>_plan.md`), which by convention live directly inside their own
    # `<name>_plan/` directory — that directory is epic_path.parent, and
    # membership is recursive (rglob already walked nested phase_N/ dirs). A
    # standalone epic file (not `*_plan.md`) has no directory tree — parent:
    # is its only membership signal.
    if not epic_path.stem.endswith("_plan"):
        return False
    plan_dir = epic_path.parent
    try:
        card_path.relative_to(plan_dir)
        return True
    except ValueError:
        return False


def archive_candidates(cards_by_status: dict) -> None:
    """Extension point for a future `--archive-candidates` mode that groups
    status: done cards for /archive-tasks (task 7). Not implemented here —
    the repo-pr handler currently deletes task files on merge, so there is
    little to group yet; wire this up when that changes."""
    raise NotImplementedError("--archive-candidates is not implemented yet (task 7)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "task_dir",
        nargs="?",
        default="dev_docs/tasks",
        help="Directory to scan recursively for *.md task files (default: dev_docs/tasks)",
    )
    parser.add_argument(
        "--prs",
        default=None,
        help="Optional JSON file of tracker-issue PRs. No-op for repo-pr today.",
    )
    parser.add_argument(
        "--archive-candidates",
        action="store_true",
        help="Stub for a future done-card archive-grouping mode. Not implemented yet.",
    )
    args = parser.parse_args()

    task_dir = Path(args.task_dir)
    today = datetime.date.today()
    if not task_dir.is_dir():
        # A missing task dir is an empty scan, not an error — consumers report
        # "No tasks found" for an absent/empty tree. Fail-closed is reserved for
        # malformed frontmatter, not a directory a fresh repo simply lacks yet.
        print(
            json.dumps(
                {
                    "task_dir": str(task_dir),
                    "generated_at": today.isoformat(),
                    "cards": {},
                    "epics": [],
                },
                indent=2,
            )
        )
        return

    if args.prs:
        prs_path = Path(args.prs)
        try:
            json.loads(prs_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            die(f"--prs '{args.prs}' could not be read as JSON: {e}")
        # No-op / passthrough for repo-pr — see module docstring.

    if args.archive_candidates:
        try:
            archive_candidates({})
        except NotImplementedError as e:
            die(str(e))

    files = sorted(
        p
        for p in task_dir.rglob("*.md")
        if "_archive" not in p.relative_to(task_dir).parts
    )

    epics: list[dict] = []
    cards: list[dict] = []
    slug_status: dict[str, str] = {}

    for path in files:
        data, body = split_frontmatter(path)
        if isinstance(data, Exception):
            die(f"malformed frontmatter in {path}: {data}")
        if data is None:
            continue  # no frontmatter — not a task card (e.g. a plan overview)
        if not isinstance(data, dict):
            die(f"unparseable frontmatter in {path}: expected a mapping, got {data!r}")

        dtype = data.get("type")
        if dtype == "epic":
            epics.append({"path": path, "data": data})
            continue
        if dtype is not None and dtype != "task":
            continue  # non-task reference doc (e.g. type: design) — not a card

        slug = path.stem
        status = data.get("status")
        slug_status[slug] = status
        cards.append({"path": path, "slug": slug, "data": data, "body": body})

    def resolve_blockers(is_blocked_by) -> list[str]:
        unresolved = []
        for blocker_slug in as_list(is_blocked_by):
            if blocker_slug not in slug_status:
                continue  # absent file → blocker satisfied
            # Present but not done (including a card with no `status` field) →
            # the blocker is unresolved: only "absent or status: done" satisfies.
            if slug_status[blocker_slug] not in TERMINAL_STATUSES:
                unresolved.append(blocker_slug)
        return unresolved

    cards_by_status: dict[str, list[dict]] = {}
    for card in cards:
        data = card["data"]
        status = data.get("status") or "unknown"

        unresolved = resolve_blockers(data.get("is_blocked_by"))
        expires = parse_date(data.get("expires"))
        expired = bool(
            expires is not None and expires < today and status not in TERMINAL_STATUSES
        )

        entry = {
            "slug": card["slug"],
            "path": str(card["path"]),
            "title": data.get("title"),
            "priority": data.get("priority"),
            "size": data.get("size"),
            "impact": data.get("impact"),
            "status": status,
            "created": str(data.get("created"))
            if data.get("created") is not None
            else None,
            "expires": str(data.get("expires"))
            if data.get("expires") is not None
            else None,
            "is_blocked_by": as_list(data.get("is_blocked_by")),
            "parent": data.get("parent"),
            "tags": data.get("tags"),
            "human_approval_requested": data.get("human_approval_requested"),
            "dependency_ready": len(unresolved) == 0,
            "unresolved_blockers": unresolved,
            "expired": expired,
            "_rank_key": rank_key(data),
        }
        if status == "new":
            entry["promote_gate"] = promote_gate(data, card["body"])
        cards_by_status.setdefault(status, []).append(entry)

    for status, group in cards_by_status.items():
        group.sort(key=lambda e: e["_rank_key"])
        for i, entry in enumerate(group, start=1):
            entry["rank"] = i
            del entry["_rank_key"]

    epic_rollups = []
    for epic in epics:
        slug = epic_slug(epic["path"], task_dir)
        members = [
            c for c in cards if is_epic_member(c["path"], c["data"], epic["path"], slug)
        ]
        member_statuses = [m["data"].get("status") for m in members]
        epic_rollups.append(
            {
                "slug": slug,
                "path": str(epic["path"]),
                "title": epic["data"].get("title"),
                "status": epic["data"].get("status"),
                "owner": epic["data"].get("owner"),
                "member_count": len(members),
                "members": [m["slug"] for m in members],
                "done": sum(1 for s in member_statuses if s == "done"),
                "in_progress": sum(1 for s in member_statuses if s == "in_progress"),
                "blocked": sum(1 for s in member_statuses if s == "blocked"),
                "note": (
                    "file-based rollup only — does not include task-loop PR "
                    "signal (open/merged PRs for deleted-on-merge task files); "
                    "see list-tasks.md step 3 'Epic rollups' for the PR-merged "
                    "supplement"
                ),
            }
        )

    output = {
        "task_dir": str(task_dir),
        "generated_at": today.isoformat(),
        "cards": cards_by_status,
        "epics": epic_rollups,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
