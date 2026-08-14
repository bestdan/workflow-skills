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
- Promote HIGH gate: the 6 *deterministic* checks from SKILL.md's confidence
  check / promote-tasks.md step 2. The 7th check ("scope plausibly fits
  size 5") is NL judgment and stays in prose — NOT implemented here; a
  human/agent still makes that call (see `high_gate_check_7_scope_fits_size_5`
  below).

Usage:
    scripts/task-scan.py [task_dir] [--prs <json>]
    scripts/task-scan.py [task_dir] --archive-candidates --older-than <N>

    task_dir              Directory to scan recursively for *.md task files.
                           Default: dev_docs/tasks (relative to cwd, NOT a
                           git-root lookup — a known consumer-repo path bug
                           with hardcoding `git rev-parse` root is being fixed
                           separately; this script takes the dir as an
                           argument instead).
    --prs <json>          Optional path to a JSON file of tracker-issue PRs,
                           for future tracker-issue merge. No-op / passthrough
                           for the repo-pr handler today.
    --archive-candidates  Selection mode for /archive-tasks (repo-pr handler,
                           commands/handlers/repo-pr-archive.md §2): emit
                           status: done cards whose resolved completion date
                           is more than --older-than days before today. See
                           archive_candidates() below for the exact three-way
                           completion-date fallback, lifted verbatim from that
                           prose.
    --older-than <N>      Required with --archive-candidates. Age threshold in
                           days.

Emits one JSON document on stdout. Fail-closed: malformed frontmatter (YAML
that fails to parse) exits non-zero with a clear message on stderr, rather
than silently skipping the file.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import yaml

FIBONACCI = {1, 2, 3, 5}
TERMINAL_STATUSES = {"done"}
# Ranking tier order — see "Ranking" in skills/task/SKILL.md. Any priority not
# in this map (missing, or an unrecognized value) sorts after every known
# tier so a malformed priority never wins a tie against a valid one.
PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
UNKNOWN_PRIORITY_RANK = len(PRIORITY_ORDER)

HEADING_RE = re.compile(r"^#{2,6}\s+(.*)$")
BULLET_RE = re.compile(r"^\s*[-*]\s+\S")


def die(msg: str) -> NoReturn:
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


def high_gate_check_7_scope_fits_size_5() -> None:
    """Deliberately unimplemented. Per SKILL.md's confidence check, whether
    the described scope plausibly fits within size 5 (~300 lines / ~5 files)
    is model judgment weighing the stated size, the ## Task steps, and
    related_files breadth — not a keyword scan. /promote-tasks still makes
    this call in prose; this script only reports the 6 deterministic checks
    and leaves a `note` field for the caller to apply check 7 itself."""
    raise NotImplementedError("check 7 is NL judgment; kept in prose, not here")


def promote_gate(data: dict, body: str) -> dict:
    """The 6 deterministic HIGH checks from SKILL.md's confidence check /
    promote-tasks.md step 2. Returns {checks: {name: bool}, high: bool,
    reasons: [str], note: str}. `high` reflects only the deterministic
    checks — the 7th (scope) is not evaluated here."""
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

    har = data.get("human_approval_requested")
    checks["human_approval_not_requested"] = har in (None, False)
    if not checks["human_approval_not_requested"]:
        reasons.append("human_approval_requested is true")

    return {
        "checks": checks,
        "high": all(checks.values()),
        "reasons": reasons,
        "note": (
            "deterministic checks only — check 7 (scope plausibly fits size 5) "
            "is NL judgment and is NOT evaluated here; the caller still makes "
            "that call"
        ),
    }


def rank_key(data: dict):
    """Sort key implementing SKILL.md "Ranking": priority tier, then
    impact/size descending (no-score sorts last within tier), then oldest
    created first."""
    priority = data.get("priority")
    # Frontmatter is user-written YAML, so `priority` can be absent, or a
    # non-string (`priority: 1` parses as an int). Both already ranked as
    # unknown — via `.get`'s default — but only by accident of `dict.get`
    # accepting any key; stated explicitly so the intent survives.
    priority_rank = (
        PRIORITY_ORDER.get(priority, UNKNOWN_PRIORITY_RANK)
        if isinstance(priority, str)
        else UNKNOWN_PRIORITY_RANK
    )

    impact = data.get("impact")
    size = data.get("size")
    size_valid = (
        isinstance(size, int) and not isinstance(size, bool) and size in FIBONACCI
    )
    impact_valid = (
        isinstance(impact, int) and not isinstance(impact, bool) and impact in FIBONACCI
    )
    has_score = size_valid and impact_valid
    # The isinstance checks are repeated inline rather than read off
    # `has_score`: narrowing does not survive a round trip through a bool, so
    # the division is otherwise `None / None` as far as any checker can tell.
    # Same condition, same result — `has_score` stays because it is what the
    # caller sorts on.
    score = (
        impact / size
        if isinstance(size, int) and isinstance(impact, int) and has_score
        else 0.0
    )

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


def git_commit_date(path: Path) -> datetime.date | None:
    """Last git-commit date for `path` (`git log -1 --format=%cs`), or None if
    the file has no commit history (uncommitted/untracked) or git is
    unavailable."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", path.name],
            cwd=path.parent,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    out = result.stdout.strip()
    return parse_date(out) if out else None


def resolve_completion_date(
    data: dict, path: Path, today: datetime.date
) -> tuple[datetime.date, str]:
    """The completion-date fallback from repo-pr-archive.md §2, verbatim:
    `completed` if present, else the file's last git-commit date, else (if
    that is empty too — uncommitted/untracked) today's date, so a freshly
    written not-yet-committed done file has age 0."""
    completed = parse_date(data.get("completed"))
    if completed is not None:
        return completed, "completed"
    commit_date = git_commit_date(path)
    if commit_date is not None:
        return commit_date, "git_commit_date"
    return today, "today_fallback"


def archive_candidates(
    cards: list[dict], older_than: int, today: datetime.date
) -> list[dict]:
    """status: done cards (per repo-pr-archive.md §2) whose resolved
    completion date is more than `older_than` days before today. Never a
    non-`done` status, whatever its age."""
    out = []
    for card in cards:
        if card["data"].get("status") != "done":
            continue
        completion_date, source = resolve_completion_date(
            card["data"], card["path"], today
        )
        age_days = (today - completion_date).days
        if age_days > older_than:
            out.append(
                {
                    "slug": card["slug"],
                    "path": str(card["path"]),
                    "completion_date": completion_date.isoformat(),
                    "completion_date_source": source,
                    "age_days": age_days,
                }
            )
    out.sort(key=lambda e: (-e["age_days"], e["slug"]))
    return out


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
        help=(
            "Emit status: done cards older than --older-than days "
            "(repo-pr-archive.md §2 candidate selection)."
        ),
    )
    parser.add_argument(
        "--older-than",
        type=int,
        default=None,
        help="Age threshold in days. Required with --archive-candidates.",
    )
    args = parser.parse_args()

    if args.archive_candidates and args.older_than is None:
        die("--archive-candidates requires --older-than <N>")
    if args.archive_candidates and args.older_than < 0:
        # A negative threshold makes `age_days > older_than` true for age-0
        # today_fallback cards (0 > -1), archiving freshly-written untracked
        # done files — the opposite of this mode's safety guarantee.
        die("--older-than must be a non-negative integer")

    task_dir = Path(args.task_dir)
    today = datetime.date.today()
    if not task_dir.is_dir():
        # A missing task dir is an empty scan, not an error — consumers report
        # "No tasks found" for an absent/empty tree. Fail-closed is reserved for
        # malformed frontmatter, not a directory a fresh repo simply lacks yet.
        if args.archive_candidates:
            print(
                json.dumps(
                    {
                        "task_dir": str(task_dir),
                        "generated_at": today.isoformat(),
                        "older_than": args.older_than,
                        "candidates": [],
                    },
                    indent=2,
                )
            )
            return
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

    files = sorted(
        p
        for p in task_dir.rglob("*.md")
        if "_archive" not in p.relative_to(task_dir).parts
    )

    epics: list[dict] = []
    cards: list[dict] = []
    # `str | None`, not `str`: a card with no `status:` in its frontmatter
    # stores None here, and the readiness check below treats that as
    # non-terminal (correctly — a card with no status is not done). The
    # annotation says so rather than claiming a str that is not always there.
    slug_status: dict[str, str | None] = {}

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
        # Rejected at ingestion, not tolerated downstream. Frontmatter is
        # user-written YAML, so `status:` can arrive as any type — and an
        # unhashable one (a list, a mapping) reached `TERMINAL_STATUSES`
        # membership and `cards_by_status.setdefault` as a dict key, where it
        # raised a bare `TypeError: unhashable type` traceback. That is the one
        # failure mode this scanner must not have: its contract is fail-closed
        # with a located message (the two `die`s above), and a traceback names
        # no file, so the card that caused it stays hidden. Checked here rather
        # than trusted from the annotation below, which cannot constrain what
        # `yaml.safe_load` returns through an unparameterized dict.
        if status is not None and not isinstance(status, str):
            die(
                f"invalid status in {path}: expected a string, got "
                f"{type(status).__name__} ({status!r})"
            )
        # Slugs resolve globally by filename stem, so two files in different
        # subdirectories can share one. For blocker readiness, fail toward
        # "still blocked": never let a later terminal (done) duplicate clear an
        # already-active status — only overwrite when the slug is new or the
        # stored status was itself terminal.
        if slug not in slug_status or slug_status[slug] in TERMINAL_STATUSES:
            slug_status[slug] = status
        cards.append({"path": path, "slug": slug, "data": data, "body": body})

    if args.archive_candidates:
        # Epic rollups marked `status: done` are in-scope for archival too (see
        # repo-pr-archive.md §"terminal state" — "epic rollups marked status:
        # done"), even though they're excluded from ranking/execution. Give
        # them the card shape archive_candidates expects (slug = filename stem).
        archivable = cards + [
            {"path": e["path"], "slug": e["path"].stem, "data": e["data"]}
            for e in epics
        ]
        candidates = archive_candidates(archivable, args.older_than, today)
        print(
            json.dumps(
                {
                    "task_dir": str(task_dir),
                    "generated_at": today.isoformat(),
                    "older_than": args.older_than,
                    "candidates": candidates,
                },
                indent=2,
            )
        )
        return

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
