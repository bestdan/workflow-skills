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

Run via: `uv run scripts/validate.py [task_dir]` (deps are hash-locked in
validate.py.lock). Every check except the task-file frontmatter checks always
runs against this repo's own tree (ROOT, derived from __file__) — those are
plugin-repo structural rules, not something a consumer repo has. The task-file
checks run against `task_dir` if given, else default to `ROOT/dev_docs/tasks`
(this repo's own tasks — preserves today's CI behavior). Passing a consumer
repo's task dir here is how `/doctor` validates the *consumer's* cards instead
of the plugin's own — see commands/doctor.md check 4.

Exits 0 when clean, 1 with a list of `path: message` failures otherwise.

Length policy: `description` is capped at 1024 chars (Anthropic skill-authoring
hard limit); Claude Code itself truncates description+when_to_use at 1536.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^[a-z0-9-]+$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
# "<" and ">" are matched so a documentation placeholder
# (${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/<script>.py, linear-common.md)
# is captured whole and skipped below. Excluding them instead ends the match at
# the "<" and checks the parent directory, which passes silently when it exists
# and otherwise reports a path nobody wrote. The trailing class still excludes
# "." so a reference ending a sentence doesn't capture the period.
PLUGIN_ROOT_REF_RE = re.compile(
    r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./<>-]*[A-Za-z0-9_/<>-])"
)
# --- shell logic in runtime markdown ---
# A fenced shell block in a skill/command/handler/agent body is runtime prompt
# text. `scripts/lint-shell.sh` globs only `*.sh`/`*.bash`/`*.bats` from
# `git ls-files`, so nothing in the gate ever syntax-checks, shellchecks or runs
# such a block — see the "Logic goes in a typed file" section of CONTRIBUTING.md
# for the incident that produced this check.
SHELL_FENCE_RE = re.compile(r"^\s*```(bash|sh|shell|zsh)\s*$")
SHELL_OPENER_RE = re.compile(r"^\s*(if|for|while|until|case)\s")
# The prose filter. Several fenced `bash` blocks here are not shell at all —
# `repo-pr-execute.md` and `repo-pr.md` are `claude --remote "..."` prompt
# payloads, English with placeholders — and their sentences start with the words
# "if", "for" and "while" often enough that opener-counting alone flags them
# (measured: 11 hits in a block with zero lines of shell). A bare `fi`/`done`/
# `esac` on its own line is the thing English never writes, so requiring one is
# what separates a program from a paragraph. It also exempts guarded one-liners
# for free: `if ...; then ...; fi` keeps its terminator on the opener's line.
SHELL_TERMINATOR_RE = re.compile(r"^\s*(fi|done|esac)\s*(;|&&|\|\||#.*)?\s*$")
# One multi-line construct still reads inline; two openers is a program.
SHELL_OPENER_MAX = 1
# Blocks that predate the check, with the count they are allowed to keep. A new
# offending block anywhere — including a third one here — fails. Shrink an entry
# when you extract one; never raise one to make a new block pass.
#
# skills/local-review/SKILL.md: real logic, but written against a literal
# `<scratch>` placeholder, so it is not valid shell and needs parameterising
# before it can move to a file. Declared out of scope by
# https://github.com/bestdan/workflow-skills/issues/465.
SHELL_LOGIC_ALLOWLIST = {"skills/local-review/SKILL.md": 2}

DESC_MAX = 1024
BODY_MAX_LINES = 500
BODY_WARN_LINES = 450
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
# Non-epic required fields per "Field reference" in skills/task/SKILL.md —
# the canonical source; kept in sync with that table, not re-derived.
REQUIRED_TASK_FIELDS = (
    "title",
    "priority",
    "size",
    "status",
    "created",
    "source_branch",
    "related_files",
    "expires",
)
# Mirrors scripts/task-scan.py's TERMINAL_STATUSES — a card stops counting as
# "expired" once it reaches a terminal status, `done` today.
TERMINAL_STATUSES = {"done"}

errors: list[str] = []
warnings: list[str] = []


def err(path, msg: str) -> None:
    errors.append(f"{path}: {msg}")


def warn(path, msg: str) -> None:
    warnings.append(f"{path}: {msg}")


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


def parse_date(v) -> datetime.date | None:
    """YAML parses unquoted ISO dates (`created: 2026-03-23`) into
    datetime.date already; a quoted string needs an explicit parse. Mirrors
    scripts/task-scan.py's parse_date."""
    if isinstance(v, datetime.datetime):
        # PyYAML parses an unquoted timestamp (`expires: 2026-07-20T12:00:00`)
        # into datetime.datetime, a date subclass — returning it as-is would
        # make `expires < today` (a date) raise TypeError later. A timestamp is
        # not a valid task `expires` value, so reject it as a bad date.
        return None
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, str):
        try:
            return datetime.date.fromisoformat(v.strip())
        except ValueError:
            return None
    return None


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "task_dir",
    nargs="?",
    default=None,
    help="Directory to validate task-file frontmatter in (default: "
    "ROOT/dev_docs/tasks, this repo's own tasks)",
)
args = parser.parse_args()
task_dir = Path(args.task_dir) if args.task_dir else ROOT / "dev_docs" / "tasks"


def rel_task(p: Path) -> Path:
    """Like rel(), but relative to task_dir's parent — task_dir may be
    outside ROOT when an explicit consumer-repo dir is passed."""
    try:
        return p.relative_to(ROOT)
    except ValueError:
        return p.relative_to(task_dir.parent)


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
    elif n_lines > BODY_WARN_LINES:
        warn(
            rel(sk),
            f"body is {n_lines} lines (warn above {BODY_WARN_LINES}, "
            f"hard cap {BODY_MAX_LINES}) — move content to references/ before it's full",
        )

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

# --- plugin-root script paths ---
# Skills/commands are markdown instruction files an agent reads at runtime; a
# stale ${CLAUDE_PLUGIN_ROOT}/<path> reference surfaces as "file not found" in
# a user's session and is otherwise invisible to CI.
plugin_root_ref_files = sorted(
    [
        *(ROOT / "skills").rglob("*.md"),
        *(ROOT / "commands").rglob("*.md"),
        *(ROOT / "agents").rglob("*.md"),
    ]
)
for f in plugin_root_ref_files:
    for n, line in enumerate(f.read_text().splitlines(), start=1):
        for ref in PLUGIN_ROOT_REF_RE.finditer(line):
            captured = ref.group(1)
            if "<" in captured:
                continue  # a <placeholder>, not a literal path
            if not (ROOT / captured).exists():
                err(
                    rel(f),
                    f"line {n}: ${{CLAUDE_PLUGIN_ROOT}}/{captured} does not exist",
                )


# --- shell logic in runtime markdown ---
# Reuses plugin_root_ref_files: the same skills/commands/agents bodies, which is
# exactly the set an agent executes at runtime.
def shell_logic_blocks(text: str):
    """Yield (start_line, opener_count) for each fenced shell block with logic."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if SHELL_FENCE_RE.match(lines[i]):
            i += 1
            start = i
            openers = 0
            terminators = 0
            while i < len(lines) and not lines[i].strip().startswith("```"):
                if SHELL_OPENER_RE.match(lines[i]):
                    openers += 1
                if SHELL_TERMINATOR_RE.match(lines[i]):
                    terminators += 1
                i += 1
            if openers > SHELL_OPENER_MAX and terminators:
                yield start, openers
        i += 1


for f in plugin_root_ref_files:
    offenders = list(shell_logic_blocks(f.read_text()))
    allowed = SHELL_LOGIC_ALLOWLIST.get(str(rel(f)), 0)
    if len(offenders) > allowed:
        for line_no, openers in offenders[allowed:]:
            err(
                rel(f),
                f"line {line_no}: fenced shell block carries {openers} control-flow "
                f"statements (max {SHELL_OPENER_MAX}). Nothing in the gate can lint or "
                f"run shell inside markdown — move the logic to a typed file "
                f"(commands/handlers/assets/<name>.py or scripts/<name>.sh) and call it "
                f"from here. See CONTRIBUTING.md, 'Logic goes in a typed file'.",
            )
# An allowlist entry with more headroom than the file needs is stale config
# pretending to be a rule; fail on it so extractions actually shrink the list.
# A *missing* file is skipped rather than flagged: validate.py is copied into
# the fixture plugins scripts/test-validate.sh builds, and those carry none of
# this repo's own skills.
for allowed_path, count in SHELL_LOGIC_ALLOWLIST.items():
    target = ROOT / allowed_path
    if not target.exists():
        continue
    present = len(list(shell_logic_blocks(target.read_text())))
    if present < count:
        err(
            "scripts/validate.py",
            f"SHELL_LOGIC_ALLOWLIST allows {count} block(s) in {allowed_path} but it "
            f"has {present} — lower the entry, or drop it if the count is 0",
        )

# --- task files (task_dir/**/*.md, default ROOT/dev_docs/tasks) ---
# The repo-native task store (see skills/task/SKILL.md). Lenient like the rest
# of this script: validate the shape of fields that are present, don't hard-
# reject unknown keys, and skip non-task files. Files with no frontmatter
# (e.g. a legacy plan overview) are not task cards — skip them. Epic files
# (`type: epic`) are validated against the epic shape, not the task shape.
# A missing required field, or an expired non-terminal card, is reported as a
# warning (not an error) — those are pre-existing hygiene gaps in real repos
# (e.g. cards predating the `expires` field), not authoring mistakes to fail
# CI over. `/doctor` classifies them into FAIL/WARN itself (see check 4/5 in
# commands/doctor.md) using the field name in the message.
today = datetime.date.today()
if task_dir.is_dir():
    for t in sorted(task_dir.rglob("*.md")):
        data, _ = split_frontmatter(t)
        if data is None:
            # split_frontmatter returns None both for a file with no
            # frontmatter and for one that opens with `---` but never closes
            # it. The former (e.g. a plan overview) is a legit non-task file;
            # the latter is a malformed card we should flag.
            if t.read_text().startswith("---"):
                err(rel_task(t), "malformed frontmatter: missing closing '---'")
            continue  # genuinely no frontmatter — not a task card
        if not isinstance(data, dict):
            err(rel_task(t), f"unparseable frontmatter: {data}")
            continue
        if data.get("type") == "epic":
            # Epic rollup files (see "Epics" in skills/task/SKILL.md), not task
            # cards. Validate the epic shape — title and status required, owner
            # optional (typed when present) — instead of the task shape, then
            # skip the task-specific checks below.
            title = data.get("title")
            if not isinstance(title, str) or not title.strip():
                err(rel_task(t), "epic title must be a non-empty string")
            est = data.get("status")
            if est is None or est not in EPIC_STATUSES:
                err(
                    rel_task(t),
                    f"epic status '{est}' must be one of {sorted(EPIC_STATUSES)}",
                )
            owner = data.get("owner")
            if owner is not None and (not isinstance(owner, str) or not owner.strip()):
                err(rel_task(t), "epic owner must be a non-empty string")
            # An epic pushed to a tracker (see "plan→tracker sync") records the
            # grouping container's id the same way a task records its issue id.
            for field in ("tracker_id", "tracker_url"):
                v = data.get(field)
                if v is not None and not isinstance(v, str):
                    err(rel_task(t), f"{field} must be a string")
            continue
        dtype = data.get("type")
        if dtype is not None and dtype != "task":
            # Non-task reference docs (e.g. `type: design`, `type: notes`) may
            # live under dev_docs/tasks/ alongside cards. They are not task
            # cards, so the task-shape checks don't apply; scans key off
            # status new/ready, so they stay invisible to /do-tasks and
            # /promote-tasks regardless.
            continue
        for field in ("size", "impact"):
            v = data.get(field)
            # bool is an int subclass (True == 1) and 3.0 == 3, so a bare
            # `in FIBONACCI` test would let `size: true`/`impact: 3.0` pass.
            if v is not None and (
                not isinstance(v, int) or isinstance(v, bool) or v not in FIBONACCI
            ):
                err(rel_task(t), f"{field} '{v}' must be one of 1/2/3/5")
        pr = data.get("priority")
        if pr is not None and pr not in TASK_PRIORITIES:
            err(
                rel_task(t), f"priority '{pr}' must be one of {sorted(TASK_PRIORITIES)}"
            )
        st = data.get("status")
        if st is not None and st not in TASK_STATUSES:
            err(rel_task(t), f"status '{st}' must be one of {sorted(TASK_STATUSES)}")
        blk = data.get("is_blocked_by")
        if blk is not None and not (
            isinstance(blk, str)
            or (isinstance(blk, list) and all(isinstance(x, str) for x in blk))
        ):
            err(rel_task(t), "is_blocked_by must be a string or a list of strings")
        # Type-only guard: the content is freeform (a handle, an id, a slug, a
        # tracker identifier/URL recorded by plan→tracker sync), but a list/dict
        # here is a YAML authoring slip, like is_blocked_by above.
        for field in ("assignee", "parent", "tracker_id", "tracker_url"):
            v = data.get(field)
            if v is not None and not isinstance(v, str):
                err(rel_task(t), f"{field} must be a string")
        # 4.2 — missing required fields, per the "Field reference" table in
        # skills/task/SKILL.md (the canonical source).
        for field in REQUIRED_TASK_FIELDS:
            if data.get(field) in (None, ""):
                warn(rel_task(t), f"missing required field '{field}'")
        # expires semantics: shape (must be a valid ISO date when present)
        # and the check-5 expired computation (mirrors task-scan.py) — a
        # non-terminal card whose expires date has passed.
        raw_expires = data.get("expires")
        if raw_expires is not None:
            expires = parse_date(raw_expires)
            if expires is None:
                err(rel_task(t), f"expires '{raw_expires}' is not a valid ISO date")
            elif expires < today and st not in TERMINAL_STATUSES:
                warn(
                    rel_task(t),
                    f"expired: expires {expires} < today ({today}) and "
                    f"status '{st}' is non-terminal",
                )

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

for w in warnings:
    print(f"  ⚠ {w}")

if errors:
    print("validate.py: FAIL")
    for e in errors:
        print(f"  ✘ {e}")
    sys.exit(1)
print("validate.py: OK")
