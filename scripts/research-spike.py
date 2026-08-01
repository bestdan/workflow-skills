#!/usr/bin/env python3
"""The research-spike instrument: obligation-ledger tooling for research spikes.

Implements the script half of `dev_docs/designs/research_spike_skill.md`. The
boundary that design draws is hard and this file is one side of it: **if two
runs over the same tree could disagree, it belongs here.** Parsing, every
validation rule, decision-status computation, the `status` report, ledger
derivation and freshness, and `suggest` are the script's. Prose, proposed ids,
`none:` reasons, and the judgment of *whether* something is a deferral at all
belong to `skills/research-spike/SKILL.md`, which never computes a status or a
count.

Stdlib only, deliberately — the design mandates no dependencies, so there is
nothing to lock and no `uv` header. Invoked as:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" [--root DIR] VERB

The directory convention this walks is owned by the skill and is not
configurable (it is what replaces the reference implementation's per-repo
config for coverage files and heading patterns):

    dev_docs/research/<project>/
      PROJECT.md            # charter
      decisions.md          # organizer-owned
      LEDGER.md             # generated roll-up, organizer-owned
      tracks/<track>/
        questions.md        # questions + answers + the track's stored ledger
        contracts/          # optional; preconditions register here
        obligations/        # stub and receipt cards

Ids are declared **bare** and qualified by this script: `project/track/id`, or
`project/id` for decisions. In-record references (`blocks:`, `blocking:`) name
decisions by bare id, resolved within the enclosing project.

`--root` defaults to the **current working directory**, matching
`scripts/task-scan.py` and `scripts/claim-scan.sh`, and deliberately *not*
`__file__`. Unlike `scripts/validate.py` — whose script-relative `ROOT` exists
so the plugin can validate its own tree — this script ships to consumers and is
invoked through `${CLAUDE_PLUGIN_ROOT}`, so `__file__` is the *installed
plugin*. An `__file__`-anchored default would scan the plugin checkout instead
of the consumer repo, and would fail **silently green**: the plugin has no
`dev_docs/research/` tree, and "no research dir is clean" would report success.
See `dev_docs/deterministic-code-opportunity.md` §"Load-bearing decisions &
gotchas" item 1 — this is the exact defect PRE-611 fixed.

Exit codes:

    0  clean
    1  tree-content violations — a rule broken by what is in the tree
    2  caller errors — unparseable arguments, an unknown subcommand, a
       malformed or already-existing project named on the command line

That split is stated explicitly because several PRs write fixtures against it.
Argparse's own usage exit is `2` and is never overridden by a subcommand (a
scan that is exit-0 by design, like `suggest`, still cannot suppress the
dispatcher's `2`).

This file lands the skeleton: CLI dispatch, tree discovery, the fenced-block
parser and record model, question-section discovery, and the reporting shape.
Field *semantics* (required/enum/referential rules, ledger derivation, the
status report) land in the later tasks of the research-spike plan; the verbs
they own dispatch to an explicit not-implemented-yet error here rather than a
silent success.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROG = "research-spike"

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_USAGE = 2

# The tree this walks, relative to --root. Skill-owned, not configurable.
RESEARCH_SUBPATH = ("dev_docs", "research")
TRACKS_DIRNAME = "tracks"
# The one file the coverage rule reaches. Fixed by the directory convention,
# not configured per repo — see the design's "resolves structurally rather
# than by config" note.
QUESTIONS_FILENAME = "questions.md"

RECORD_KINDS = ("question", "obligation", "decision", "card")

# Keys each record kind accepts. Unknown keys are an error, not ignored — a
# `desination:` typo must not silently drop the constraint. Only membership is
# checked here; which keys are *required*, and what their values may be, is
# tasks 3-5 of the research-spike plan.
KNOWN_FIELDS: dict[str, frozenset[str]] = {
    "question": frozenset({"id", "status", "blocks", "answer", "retired_because"}),
    "obligation": frozenset(
        {"id", "owes", "destination", "status", "discharged_by", "blocking"}
    ),
    "decision": frozenset({"id", "state", "decided_in", "reopened_because"}),
    "card": frozenset({"kind", "superseded_when", "url", "handler", "tracker_id"}),
}
# `none` is legal in every kind: a bare `none: <reason>` block is a valid
# record shape (the coverage rule's explicit declaration), and any field may
# carry the sentinel as its value.
NONE_KEY = "none"

# A fence opener or closer. Backticks only — the reference parser has no tilde
# fences and gains none. The captured run length is what makes ````-nested
# examples parse correctly: a 4-backtick fence is closed only by 4 or more.
FENCE_RE = re.compile(r"^[ \t]*(?P<ticks>`{3,})(?P<info>.*)$")

# The `### Q<n>.` heading convention `init` installs. A section ends at the
# next heading of the same level or shallower (a `####` sub-heading stays
# inside the section).
QUESTION_HEADING_LEVEL = 3
QUESTION_HEADING_RE = re.compile(
    r"^#{3}[ \t]+Q(?P<number>\d+)\.[ \t]*(?P<title>.*?)[ \t]*$"
)
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+")

# The `none` sentinel. Recognized only as the whole value (`none`) or as the
# value's own leading key (`none: <reason>`) — never as a mere prefix, so
# `owes: nonetheless the receipt` stays ordinary prose.
NONE_SENTINEL_RE = re.compile(r"^none[ \t]*(?::(?P<reason>.*))?$", re.DOTALL)


# --------------------------------------------------------------------------
# Values, records, and the tree model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Value:
    """One `key: value` field's value.

    `raw` is everything after the **first** colon, verbatim apart from
    surrounding whitespace: the split is on the first colon only, and there is
    no inline comment syntax, so a `#` is part of the value and will fail
    whichever enum check owns that field.

    `is_none` marks the reserved sentinel. A none-declaration is *not* a list:
    without that exemption `blocks: none: it gates nothing, and probably never
    will` would comma-split into two "decision ids" and the referential check
    would report `and probably never will` as a dangling reference.
    """

    raw: str
    line: int
    is_none: bool = False
    reason: str = ""
    items: tuple[str, ...] = ()


def make_value(raw: str, line: int) -> Value:
    raw = raw.strip()
    m = NONE_SENTINEL_RE.match(raw)
    if m:
        reason = m.group("reason")
        return Value(
            raw=raw,
            line=line,
            is_none=True,
            # A bare `none` carries no reason; a `none:` with nothing after it
            # carries an empty one. Both are "no reason given" — the rule that
            # a `none` must explain itself is enforced by the coverage checks,
            # not by the parser.
            reason=reason.strip() if reason else "",
        )
    return Value(
        raw=raw,
        line=line,
        items=tuple(part.strip() for part in raw.split(",") if part.strip()),
    )


@dataclass
class Record:
    """One fenced record block, parsed and located.

    Field *semantics* are not this class's business — it carries the raw
    fields, where they came from, and which project/track encloses them.
    """

    kind: str
    path: Path
    rel: str
    line: int
    project: str
    track: str | None
    fields: dict[str, Value] = field(default_factory=dict)

    @property
    def declared_id(self) -> str | None:
        """The bare `id:` as written, or None when absent/blank/a sentinel."""
        v = self.fields.get("id")
        if v is None or v.is_none or not v.raw:
            return None
        return v.raw

    @property
    def qualified_id(self) -> str | None:
        """`project/track/id`, or `project/id` for decisions.

        Decisions qualify project-wide even when a `proposed` one is filed
        inside a track, because promoting it into `decisions.md` must not
        change its id.
        """
        bare = self.declared_id
        if bare is None:
            return None
        if self.kind == "decision" or self.track is None:
            return f"{self.project}/{bare}"
        return f"{self.project}/{self.track}/{bare}"


@dataclass
class Track:
    name: str
    path: Path


@dataclass
class Project:
    name: str
    path: Path
    tracks: list[Track] = field(default_factory=list)


@dataclass
class Tree:
    """A discovered `dev_docs/research/` tree.

    `present` is False when the root simply has no research directory, which
    is clean rather than an error — a repo that has not started a spike yet.
    """

    root: Path
    research_dir: Path
    present: bool
    projects: list[Project] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    # Question sections per `questions.md`, keyed by the file's repo-relative
    # path. Task 4's coverage rule walks these: every section must declare its
    # obligations or explicitly declare `none:` with a reason.
    sections: dict[str, list[QuestionSection]] = field(default_factory=dict)

    def project(self, name: str) -> Project | None:
        return next((p for p in self.projects if p.name == name), None)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    message: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.message}"


class Report:
    """Collected findings, printed sorted. Mirrors `scripts/validate.py`:
    warnings are reported but leave the exit code alone; errors mean exit 1."""

    def __init__(self) -> None:
        self.errors: list[Finding] = []
        self.warnings: list[Finding] = []

    def error(self, path: str, line: int, message: str) -> None:
        self.errors.append(Finding(path, line, message))

    def warn(self, path: str, line: int, message: str) -> None:
        self.warnings.append(Finding(path, line, message))

    def emit(self) -> int:
        """Print every finding, sorted by path then line, and return the exit
        code the findings imply."""
        for w in sorted(self.warnings):
            print(f"  ⚠ {w.render()}")
        if self.errors:
            print(f"{PROG}: FAIL")
            for e in sorted(self.errors):
                print(f"  ✘ {e.render()}")
            return EXIT_VIOLATIONS
        return EXIT_OK


class UsageError(Exception):
    """A caller error — exit 2. Distinct from anything found in the tree."""


# --------------------------------------------------------------------------
# Fence scanning
# --------------------------------------------------------------------------


@dataclass
class Fence:
    open_index: int  # 0-based index of the opening fence line
    info: str  # the info string, stripped
    body: tuple[int, int]  # [start, end) 0-based indices of the body lines
    closed: bool

    @property
    def kind(self) -> str | None:
        """The record kind this fence declares, or None for any other fence."""
        words = self.info.split()
        first = words[0].lower() if words else ""
        return first if first in RECORD_KINDS else None


def scan_fences(lines: list[str]) -> tuple[list[Fence], Fence | None]:
    """Split `lines` into top-level fenced blocks.

    Returns (fences, unterminated). Nesting is handled by run length, per
    CommonMark: a fence closes only on a line of at least as many backticks
    with no info string, so a ````-fenced markdown example containing a
    ```obligation block yields one fence (the outer one), not two.
    """
    fences: list[Fence] = []
    unterminated: Fence | None = None
    i = 0
    n = len(lines)
    while i < n:
        m = FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        ticks = m.group("ticks")
        info = m.group("info").strip()
        close = None
        j = i + 1
        while j < n:
            cm = FENCE_RE.match(lines[j])
            if (
                cm
                and not cm.group("info").strip()
                and len(cm.group("ticks")) >= len(ticks)
            ):
                close = j
                break
            j += 1
        if close is None:
            unterminated = Fence(open_index=i, info=info, body=(i + 1, n), closed=False)
            fences.append(unterminated)
            break
        fences.append(Fence(open_index=i, info=info, body=(i + 1, close), closed=True))
        i = close + 1
    return fences, unterminated


def fenced_line_indices(lines: list[str]) -> set[int]:
    """Every 0-based line index inside a fence, delimiters included — so a
    `### Q1.` written inside a code sample is not mistaken for a heading."""
    inside: set[int] = set()
    fences, _ = scan_fences(lines)
    for f in fences:
        start, end = f.body
        inside.add(f.open_index)
        inside.update(range(start, end))
        if f.closed:
            inside.add(end)
    return inside


# --------------------------------------------------------------------------
# Block parsing
# --------------------------------------------------------------------------


def parse_block_fields(
    kind: str,
    body_lines: list[str],
    first_line: int,
    rel: str,
    report: Report,
) -> dict[str, Value]:
    """Parse a record block body into `{key: Value}`.

    The grammar, kept at reference-implementation simplicity: one `key: value`
    per line, the split on the **first** colon only, no inline comment syntax,
    unknown keys are errors. Blank lines are ignored.
    """
    fields: dict[str, Value] = {}
    allowed = KNOWN_FIELDS[kind] | {NONE_KEY}
    for offset, line in enumerate(body_lines):
        lineno = first_line + offset
        if not line.strip():
            continue
        key, sep, raw = line.partition(":")
        key = key.strip()
        if not sep:
            report.error(
                rel,
                lineno,
                f"{kind} block: '{line.strip()}' is not a 'key: value' line",
            )
            continue
        if key not in allowed:
            report.error(
                rel,
                lineno,
                f"{kind} block: unknown key '{key}' "
                f"(known: {', '.join(sorted(allowed))})",
            )
            continue
        if key in fields:
            report.error(rel, lineno, f"{kind} block: duplicate key '{key}'")
            continue
        fields[key] = make_value(raw, lineno)
    return fields


def parse_records(
    text: str,
    path: Path,
    rel: str,
    project: str,
    track: str | None,
    report: Report,
) -> list[Record]:
    """Every recognized record block in one markdown file."""
    lines = text.splitlines()
    fences, unterminated = scan_fences(lines)
    if unterminated is not None:
        label = f"```{unterminated.info}" if unterminated.info else "```"
        report.error(
            rel,
            unterminated.open_index + 1,
            f"unterminated fenced block opened here ({label}) — every record "
            "after it would be silently swallowed",
        )
    records: list[Record] = []
    for f in fences:
        kind = f.kind
        if kind is None or not f.closed:
            continue
        start, end = f.body
        records.append(
            Record(
                kind=kind,
                path=path,
                rel=rel,
                line=f.open_index + 1,
                project=project,
                track=track,
                fields=parse_block_fields(
                    kind, lines[start:end], start + 1, rel, report
                ),
            )
        )
    return records


# --------------------------------------------------------------------------
# Question sections
# --------------------------------------------------------------------------


@dataclass
class QuestionSection:
    """One `### Q<n>.` section of a `questions.md`.

    Task 4's coverage rule consumes this: every question section must declare
    its obligations or explicitly declare `none:` with a reason.
    """

    number: int
    title: str
    start_line: int  # 1-based, the heading line
    end_line: int  # 1-based, inclusive
    lines: list[str]


def question_sections(text: str) -> list[QuestionSection]:
    """Split a `questions.md` into its question sections.

    A section starts at a `### Q<n>.` heading and ends at the next heading of
    the same level or shallower, or at end of file. Headings inside fenced
    blocks are not headings.
    """
    lines = text.splitlines()
    inside = fenced_line_indices(lines)
    starts: list[tuple[int, int, str]] = []  # (index, number, title)
    boundaries: list[int] = []  # indices of headings at level <= 3
    for i, line in enumerate(lines):
        if i in inside:
            continue
        h = HEADING_RE.match(line)
        if not h:
            continue
        if len(h.group("hashes")) <= QUESTION_HEADING_LEVEL:
            boundaries.append(i)
        q = QUESTION_HEADING_RE.match(line)
        if q:
            starts.append((i, int(q.group("number")), q.group("title").strip()))

    sections: list[QuestionSection] = []
    for index, number, title in starts:
        end = next((b for b in boundaries if b > index), len(lines))
        sections.append(
            QuestionSection(
                number=number,
                title=title,
                start_line=index + 1,
                end_line=end,
                lines=lines[index:end],
            )
        )
    return sections


# --------------------------------------------------------------------------
# Tree discovery
# --------------------------------------------------------------------------


def locate(project_dir: Path, md: Path) -> str | None:
    """The track a file belongs to: the component right after `tracks/`, or
    None for a project-level file (`PROJECT.md`, `decisions.md`, `LEDGER.md`)."""
    parts = md.relative_to(project_dir).parts
    if len(parts) >= 3 and parts[0] == TRACKS_DIRNAME:
        return parts[1]
    return None


def discover(root: Path, report: Report) -> Tree:
    """Walk `<root>/dev_docs/research/`, parsing every record it holds.

    A root with no research directory is clean, not an error: `present` comes
    back False and callers report nothing.
    """
    research_dir = root.joinpath(*RESEARCH_SUBPATH)
    tree = Tree(root=root, research_dir=research_dir, present=research_dir.is_dir())
    if not tree.present:
        return tree

    def rel_of(p: Path) -> str:
        return p.relative_to(root).as_posix()

    project_dirs = sorted(p for p in research_dir.iterdir() if p.is_dir())
    for pdir in project_dirs:
        project = Project(name=pdir.name, path=pdir)
        tracks_dir = pdir / TRACKS_DIRNAME
        if tracks_dir.is_dir():
            project.tracks = [
                Track(name=t.name, path=t)
                for t in sorted(tracks_dir.iterdir())
                if t.is_dir()
            ]
        tree.projects.append(project)
        for md in sorted(pdir.rglob("*.md")):
            if not md.is_file():
                continue
            text = md.read_text()
            rel = rel_of(md)
            tree.records.extend(
                parse_records(text, md, rel, project.name, locate(pdir, md), report)
            )
            if md.name == QUESTIONS_FILENAME:
                tree.sections[rel] = question_sections(text)

    # A record outside any project directory has no project to scope its id to
    # and no ledger that would ever count it. Silence there would be exactly
    # the invisible-accrual failure this instrument exists to prevent, so it is
    # an error — while a plain README at the research root stays fine.
    for md in sorted(research_dir.glob("*.md")):
        if not md.is_file():
            continue
        for rec in parse_records(md.read_text(), md, rel_of(md), "", None, report):
            report.error(
                rel_of(md),
                rec.line,
                f"{rec.kind} block is outside any project directory — records "
                f"live under {research_dir.name}/<project>/",
            )
    return tree


# --------------------------------------------------------------------------
# Verbs
# --------------------------------------------------------------------------


def render_value(value: Value) -> str:
    if value.is_none:
        return f"none reason={value.reason!r}"
    items = ", ".join(repr(i) for i in value.items)
    return f"{value.raw!r} items=[{items}]"


def print_inventory_for(tree: Tree, project: Project) -> None:
    """The verbose dump: what discovery found and what the parser made of it.

    This is the observation surface the fixture tests assert on — the parsed
    form of every field, so grammar rules (no comment stripping, comma lists,
    the `none` sentinel) are visible without a validation rule to fail. It is
    also the only consumer of the record model and the section helper until
    the validation rules land in tasks 3-5.
    """
    for rec in tree.records:
        if rec.project != project.name:
            continue
        scope = f"{rec.project}/{rec.track}" if rec.track else rec.project
        print(
            f"    {rec.kind} @ {rec.rel}:{rec.line} [{scope}] "
            f"id={rec.qualified_id or '-'}"
        )
        for key, value in rec.fields.items():
            print(f"      {key} = {render_value(value)}")
    prefix = f"{project.path.relative_to(tree.root).as_posix()}/"
    for rel, sections in sorted(tree.sections.items()):
        if not rel.startswith(prefix):
            continue
        for section in sections:
            print(
                f"    section @ {rel}:{section.start_line}-{section.end_line} "
                f"Q{section.number} {section.title!r}"
            )


def verb_validate(args: argparse.Namespace, root: Path) -> int:
    report = Report()
    tree = discover(root, report)
    if not tree.present:
        # No research tree is clean, not an error: nothing to say, exit 0.
        return EXIT_OK
    code = report.emit()
    if code != EXIT_OK:
        return code
    track_count = sum(len(p.tracks) for p in tree.projects)
    print(
        f"{PROG}: OK — {len(tree.projects)} projects, "
        f"{track_count} tracks, {len(tree.records)} records"
    )
    for project in tree.projects:
        tracks = ", ".join(t.name for t in project.tracks) or "none"
        count = sum(1 for r in tree.records if r.project == project.name)
        print(f"  {project.name} — tracks: {tracks} ({count} records)")
        if args.verbose:
            print_inventory_for(tree, project)
    return EXIT_OK


def unimplemented(verb: str, task: str):
    """Verbs whose behaviour lands in a later task of the research-spike plan.

    Deliberately exit 2 rather than 0: a stub that reports success is how an
    instrument becomes theatre, and from the caller's side asking for a verb
    this build cannot perform is the same class of mistake as naming one that
    does not exist.
    """

    def run(args: argparse.Namespace, root: Path) -> int:
        raise UsageError(
            f"'{verb}' is not implemented yet — it lands in {task} of the "
            "research-spike plan"
        )

    return run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Obligation-ledger tooling for research spikes. Validates and "
            "reports on a dev_docs/research/ tree."
        ),
    )
    parser.add_argument(
        "--root",
        default=".",
        help=(
            "Directory holding dev_docs/research/ (default: the current "
            "working directory — never this script's own location)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Dump every discovered record and its parsed fields.",
    )
    subparsers = parser.add_subparsers(dest="verb", required=True)

    p_init = subparsers.add_parser(
        "init", help="Scaffold a research project directory."
    )
    p_init.add_argument("project", nargs="?", help="Project name to scaffold.")
    p_init.add_argument("--track", help="Add a track to an existing project.")
    p_init.set_defaults(run=unimplemented("init", "task 2"))

    p_validate = subparsers.add_parser(
        "validate", help="The gate: parse and check the tree."
    )
    p_validate.add_argument("--project", help="Limit the scan to one project.")
    p_validate.add_argument("--track", help="Limit the scan to one track.")
    p_validate.add_argument(
        "--strict",
        action="store_true",
        help="Organizer flavor: LEDGER.md staleness becomes an error.",
    )
    p_validate.set_defaults(run=verb_validate)

    p_ledger = subparsers.add_parser("ledger", help="Print the derived ledgers.")
    p_ledger.set_defaults(run=unimplemented("ledger", "task 7"))

    p_write = subparsers.add_parser(
        "write-ledger", help="Rewrite the stored ledgers in place."
    )
    p_write.set_defaults(run=unimplemented("write-ledger", "task 7"))

    p_status = subparsers.add_parser(
        "status", help="The convergence report for one project."
    )
    p_status.add_argument("project", nargs="?", help="Project to report on.")
    p_status.set_defaults(run=unimplemented("status", "task 6"))

    p_suggest = subparsers.add_parser(
        "suggest", help="Advisory lexical scan; never fails."
    )
    p_suggest.set_defaults(run=unimplemented("suggest", "task 8"))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = Path(args.root)
        if not root.is_dir():
            raise UsageError(f"--root '{args.root}' is not a directory")
        return args.run(args, root.resolve())
    except UsageError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
