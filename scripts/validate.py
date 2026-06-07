# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Repo-native structural & consistency validator for the workflow-skills plugin.

Dev/CI-only tooling — this script is never loaded by Claude Code as a plugin
component and never reaches plugin consumers at runtime. It enforces the
repo-specific rules that `claude plugin validate` and `dprint` don't cover:
frontmatter shape, name == directory, manifest version sync, task-file
frontmatter under dev_docs/tasks/, and the README component-count sentence.

Run via: `uv run scripts/validate.py` (deps are hash-locked in validate.py.lock).
Exits 0 when clean, 1 with a list of `path: message` failures otherwise.

Length policy: `description` is capped at 1024 chars (Anthropic skill-authoring
hard limit); Claude Code itself truncates description+when_to_use at 1536.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^[a-z0-9-]+$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
DESC_MAX = 1024
BODY_MAX_LINES = 500
FIBONACCI = {1, 2, 3, 5}
TASK_PRIORITIES = {"low", "medium", "high", "urgent"}
EPIC_STATUSES = {"active", "done", "abandoned"}
TASK_STATUSES = {
    "new",
    "needs_refinement",
    "ready",
    "in_progress",
    "blocked",
    "needs_review",
    "done",
}

errors: list[str] = []


def err(path, msg: str) -> None:
    errors.append(f"{path}: {msg}")


def split_frontmatter(path: Path):
    """Return (data, body). data is a dict, or an Exception if YAML is invalid,
    or None if there is no frontmatter block."""
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


def check_name(path, name, field: str = "name") -> None:
    if not isinstance(name, str) or not name:
        err(path, f"{field} must be a non-empty string")
        return
    if not NAME_RE.match(name):
        err(path, f"{field} '{name}' must match ^[a-z0-9-]+$")
    if len(name) > 64:
        err(path, f"{field} '{name}' exceeds 64 chars")
    if "claude" in name.lower() or "anthropic" in name.lower():
        err(path, f"{field} '{name}' must not contain 'claude' or 'anthropic'")


def check_description(path, desc) -> None:
    if not isinstance(desc, str) or not desc.strip():
        err(path, "description must be a non-empty string")
        return
    if len(desc) > DESC_MAX:
        err(path, f"description is {len(desc)} chars (max {DESC_MAX})")


def rel(p: Path) -> Path:
    return p.relative_to(ROOT)


# --- skills ---
skill_dirs = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())
for d in skill_dirs:
    sk = d / "SKILL.md"
    if not sk.exists():
        err(rel(d), "missing SKILL.md")
        continue
    data, body = split_frontmatter(sk)
    if not isinstance(data, dict):
        err(rel(sk), f"missing or unparseable frontmatter: {data}")
        continue
    check_description(rel(sk), data.get("description"))
    if "name" in data:
        check_name(rel(sk), data["name"])
        if data["name"] != d.name:
            err(rel(sk), f"name '{data['name']}' != directory '{d.name}'")
    n_lines = body.count("\n") + 1
    if n_lines > BODY_MAX_LINES:
        err(rel(sk), f"body is {n_lines} lines (max {BODY_MAX_LINES})")

# --- commands (top-level only) ---
# commands/handlers/*.md are reference procedures bundled into the task skill,
# not slash commands — they have no frontmatter and are intentionally skipped.
command_files = sorted((ROOT / "commands").glob("*.md"))
for c in command_files:
    data, _ = split_frontmatter(c)
    if not isinstance(data, dict):
        err(rel(c), f"missing or unparseable frontmatter: {data}")
        continue
    check_description(rel(c), data.get("description"))
    at = data.get("allowed-tools")
    if at is not None and not isinstance(at, (str, list)):
        err(rel(c), "allowed-tools must be a string or list")

# --- agents ---
agent_files = sorted((ROOT / "agents").glob("*.md"))
for a in agent_files:
    data, _ = split_frontmatter(a)
    if not isinstance(data, dict):
        err(rel(a), f"missing or unparseable frontmatter: {data}")
        continue
    if "name" in data:
        check_name(rel(a), data["name"])
    else:
        err(rel(a), "missing name")
    check_description(rel(a), data.get("description"))
    if not data.get("tools"):
        err(rel(a), "missing tools")

# --- task files (dev_docs/tasks/**/*.md) ---
# The repo-native task store (see skills/task/SKILL.md). Lenient like the rest
# of this script: validate the shape of fields that are present, don't hard-
# reject unknown keys, and skip non-task files. Files with no frontmatter
# (e.g. a legacy plan overview) are not task cards — skip them. Epic files
# (`type: epic`) are validated against the epic shape, not the task shape.
task_dir = ROOT / "dev_docs" / "tasks"
if task_dir.is_dir():
    for t in sorted(task_dir.rglob("*.md")):
        data, _ = split_frontmatter(t)
        if data is None:
            # split_frontmatter returns None both for a file with no
            # frontmatter and for one that opens with `---` but never closes
            # it. The former (e.g. a plan overview) is a legit non-task file;
            # the latter is a malformed card we should flag.
            if t.read_text().startswith("---"):
                err(rel(t), "malformed frontmatter: missing closing '---'")
            continue  # genuinely no frontmatter — not a task card
        if not isinstance(data, dict):
            err(rel(t), f"unparseable frontmatter: {data}")
            continue
        if data.get("type") == "epic":
            # Epic rollup files (see "Epics" in skills/task/SKILL.md), not task
            # cards. Validate the epic shape (title / status / owner) instead of
            # the task shape, then skip the task-specific checks below.
            title = data.get("title")
            if not isinstance(title, str) or not title.strip():
                err(rel(t), "epic title must be a non-empty string")
            est = data.get("status")
            if est is None or est not in EPIC_STATUSES:
                err(
                    rel(t),
                    f"epic status '{est}' must be one of {sorted(EPIC_STATUSES)}",
                )
            owner = data.get("owner")
            if owner is not None and not isinstance(owner, str):
                err(rel(t), "epic owner must be a string")
            continue
        for field in ("size", "impact"):
            v = data.get(field)
            # bool is an int subclass (True == 1) and 3.0 == 3, so a bare
            # `in FIBONACCI` test would let `size: true`/`impact: 3.0` pass.
            if v is not None and (
                not isinstance(v, int) or isinstance(v, bool) or v not in FIBONACCI
            ):
                err(rel(t), f"{field} '{v}' must be one of 1/2/3/5")
        pr = data.get("priority")
        if pr is not None and pr not in TASK_PRIORITIES:
            err(rel(t), f"priority '{pr}' must be one of {sorted(TASK_PRIORITIES)}")
        st = data.get("status")
        if st is not None and st not in TASK_STATUSES:
            err(rel(t), f"status '{st}' must be one of {sorted(TASK_STATUSES)}")
        blk = data.get("is_blocked_by")
        if blk is not None and not (
            isinstance(blk, str)
            or (isinstance(blk, list) and all(isinstance(x, str) for x in blk))
        ):
            err(rel(t), "is_blocked_by must be a string or a list of strings")
        # Type-only guard: the content is freeform (a handle, an id, a slug),
        # but a list/dict here is a YAML authoring slip, like is_blocked_by above.
        for field in ("assignee", "parent"):
            v = data.get(field)
            if v is not None and not isinstance(v, str):
                err(rel(t), f"{field} must be a string")

# --- manifests: version sync + cross-consistency ---
plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
entry = next(
    (p for p in market.get("plugins", []) if p.get("name") == plugin.get("name")), None
)

pv = plugin.get("version")
if not (isinstance(pv, str) and SEMVER_RE.match(pv)):
    err(".claude-plugin/plugin.json", f"version '{pv}' is not semver")
if not plugin.get("description"):
    err(".claude-plugin/plugin.json", "missing description")
if not market.get("description"):
    err(".claude-plugin/marketplace.json", "marketplace missing top-level description")
if entry is None:
    err(
        ".claude-plugin/marketplace.json",
        f"no plugin entry named '{plugin.get('name')}'",
    )
else:
    if entry.get("version") != pv:
        err(
            ".claude-plugin/marketplace.json",
            f"version '{entry.get('version')}' != plugin.json '{pv}' (keep them synced)",
        )
    if not entry.get("description"):
        err(".claude-plugin/marketplace.json", "plugin entry missing description")

# --- README component counts ---
readme = (ROOT / "README.md").read_text()
m = re.search(
    r"(\d+)\s+skills?,\s+(\d+)\s+commands?,\s+and\s+(\d+)\s+subagents?", readme
)
if not m:
    err(
        "README.md", "could not find 'N skills, M commands, and K subagent(s)' sentence"
    )
else:
    claimed = tuple(int(x) for x in m.groups())
    actual = (len(skill_dirs), len(command_files), len(agent_files))
    if claimed != actual:
        err(
            "README.md",
            f"claims (skills,commands,subagents)={claimed} but actual={actual}",
        )

if errors:
    print("validate.py: FAIL")
    for e in errors:
        print(f"  ✘ {e}")
    sys.exit(1)
print("validate.py: OK")
