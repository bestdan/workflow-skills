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
parser and record model, question-section discovery, the reporting shape, and
`init` — the one verb that legitimately creates files. Field *semantics*
(required/enum/referential rules, ledger derivation, the status report) land in
the later tasks of the research-spike plan; the verbs they own dispatch to an
explicit not-implemented-yet error here rather than a silent success.
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

# An HTML comment, scanned by CommonMark's HTML-block-type-2 rule: a line whose
# content begins with `<!--` opens the block, and the first line containing
# `-->` (which may be that same line) closes it. Blank lines do not end it.
#
# The **at most three spaces** of indentation is CommonMark's, and load-bearing
# rather than pedantic: at four it is an indented code block instead, so prose
# that *shows* an opener in an indented sample would otherwise open a real
# comment region and swallow every record and heading after it — reproduced,
# and reported as an unterminated-comment error over a file that had none. A
# tab counts as four columns, so it does not open one either.
#
# Content inside a comment is inert — not a record, not a heading. That is what
# lets `init` scaffold a *worked example* of the record grammar into a fresh
# `questions.md` instead of merely describing it: an example that parsed would
# make `init` emit a tree that fails its own gate. The ledger markers below are
# themselves single-line comments, so they open and close on their own line and
# leave the ledger bullets between them ordinary, visible markdown.
COMMENT_OPEN_RE = re.compile(r"^ {0,3}<!--")
COMMENT_CLOSE = "-->"

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
NONE_SENTINEL_RE = re.compile(r"^none[ \t]*(?::(?P<reason>.*))?$")

# The shape every declared id has, and — the same rule, deliberately — every
# project and track name. Names become the `project/track/` qualification
# prefix, so anything carrying `/`, whitespace or `..` corrupts qualification,
# uniqueness reporting and the cross-project destination check, all of which
# parse on that prefix. Enforcing it on ids is a later task's; `init` enforces
# it on names.
#
# Anchored with `\Z`, not `$`: Python's `$` also matches immediately before a
# final newline, so `init $'foo\n'` would pass a `$`-anchored check and
# scaffold a directory whose name carries the whitespace this rule exists to
# forbid. `\Z` is end-of-string and nothing else.
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\Z")
KEBAB_DESCRIPTION = "lowercase letters, digits and single hyphens (a-z0-9, kebab-case)"

# The stored ledgers live between these markers. `init` installs them; only
# `write-ledger` rewrites what sits between them, and `validate` reports — never
# repairs — a stored block that disagrees with the derived one.
LEDGER_BEGIN = "<!-- research-spike:ledger -->"
LEDGER_END = "<!-- /research-spike:ledger -->"


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

        None when no id is declared **or** when the rule cannot produce one —
        a non-decision record outside any track. Falling back to the decision
        form there would give a stray obligation an identity indistinguishable
        from a decision id, which is what task 5 resolves `blocks:` and
        `blocking:` against. Rejecting the placement is task 3's job; this
        property only declines to invent an identity for it, and the inventory
        dump reports the record as unplaceable rather than dropping it.
        """
        bare = self.declared_id
        if bare is None:
            return None
        if self.kind == "decision":
            return f"{self.project}/{bare}"
        if self.track is None:
            return None
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


@dataclass
class Comment:
    """One HTML-comment region: everything inside it is inert."""

    open_index: int  # 0-based index of the line carrying `<!--`
    end_index: int  # 0-based index of the last line of the region, inclusive
    closed: bool


@dataclass
class Blocks:
    """The non-prose regions of one markdown file."""

    fences: list[Fence] = field(default_factory=list)
    unterminated_fence: Fence | None = None
    comments: list[Comment] = field(default_factory=list)

    def inert_lines(self) -> set[int]:
        """Every 0-based line index that is not ordinary prose — fence bodies
        and delimiters, and whole comment regions. A `### Q1.` written in
        either is a sample, not a heading."""
        inside: set[int] = set()
        for f in self.fences:
            start, end = f.body
            inside.add(f.open_index)
            inside.update(range(start, end))
            if f.closed:
                inside.add(end)
        for c in self.comments:
            inside.update(range(c.open_index, c.end_index + 1))
        return inside


def scan_blocks(lines: list[str]) -> Blocks:
    """Split `lines` into top-level fenced blocks and comment regions.

    Nesting inside a fence is handled by run length, per CommonMark: a fence
    closes only on a line of at least as many backticks with no info string, so
    a ````-fenced markdown example containing a ```obligation block yields one
    fence (the outer one), not two.

    Comments and fences cannot nest in either direction — whichever opens first
    owns the lines until it closes. A fence inside a comment is therefore never
    reported as a record, which is what makes `init`'s worked example inert.
    """
    blocks = Blocks()
    fences = blocks.fences
    i = 0
    n = len(lines)
    while i < n:
        if COMMENT_OPEN_RE.match(lines[i]):
            j = i
            while j < n and COMMENT_CLOSE not in lines[j]:
                j += 1
            closed = j < n
            end = j if closed else n - 1
            blocks.comments.append(Comment(open_index=i, end_index=end, closed=closed))
            i = end + 1
            continue
        m = FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        ticks = m.group("ticks")
        info = m.group("info").strip()
        if "`" in info:
            # Not a fence. CommonMark forbids backticks in a backtick fence's
            # info string exactly so a paragraph opening with an inline code
            # span stays a paragraph — a line like "```obligation``` blocks are
            # opened next to the prose" is a code span, not an obligation. Read
            # as a fence it would open here and close on the *first real
            # record's* closing fence, swallowing that record whole: no error,
            # exit 0, one record silently gone. That is the invisible accrual
            # this instrument exists to prevent, so it must not be introduced
            # by the parser itself.
            i += 1
            continue
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
            blocks.unterminated_fence = unterminated
            fences.append(unterminated)
            break
        fences.append(Fence(open_index=i, info=info, body=(i + 1, close), closed=True))
        i = close + 1
    return blocks


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
        if key == NONE_KEY:
            # A bare `none: <reason>` block is the coverage rule's explicit
            # declaration. Re-attach the sentinel so it parses to exactly the
            # same shape as the field-position form (`blocks: none: ...`):
            # tasks 3-5 then have one representation to read, and the reason
            # is never comma-split into "ids" the way ordinary prose would be.
            fields[key] = make_value(f"{NONE_KEY}:{raw}", lineno)
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
    blocks = scan_blocks(lines)
    unterminated = blocks.unterminated_fence
    if unterminated is not None:
        label = f"```{unterminated.info}" if unterminated.info else "```"
        report.error(
            rel,
            unterminated.open_index + 1,
            f"unterminated fenced block opened here ({label}) — every record "
            "after it would be silently swallowed",
        )
    for c in blocks.comments:
        if not c.closed:
            # Same failure as an unterminated fence, by a different door: an
            # HTML comment that never closes runs to end of file and makes
            # every record after it inert. Silent record loss is the accrual
            # this instrument exists to prevent, so it is reported, not absorbed.
            report.error(
                rel,
                c.open_index + 1,
                "unterminated HTML comment opened here (<!--) — every record "
                "after it would be silently swallowed",
            )
    records: list[Record] = []
    for f in blocks.fences:
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
    blocks or HTML comments are not headings.
    """
    lines = text.splitlines()
    inside = scan_blocks(lines).inert_lines()
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

    def read_md(md: Path, rel: str) -> str | None:
        """A markdown file's text, or None when it cannot be read.

        Decoded as UTF-8 explicitly rather than by the platform default: this
        script ships to consumers and runs wherever they run it, including
        Windows, where the default is the ANSI codepage regardless of locale.
        An undecodable or unreadable file is reported as an ordinary located
        finding instead of aborting the walk — a traceback would exit 1, which
        this script's contract reserves for tree-content violations, making a
        crash indistinguishable from a real finding and hiding every file the
        scan never reached.
        """
        try:
            return md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            report.error(rel, 0, f"cannot read: {e}")
            return None

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
            rel = rel_of(md)
            text = read_md(md, rel)
            if text is None:
                continue
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
        rel = rel_of(md)
        text = read_md(md, rel)
        if text is None:
            continue
        for rec in parse_records(text, md, rel, "", None, report):
            report.error(
                rel,
                rec.line,
                f"{rec.kind} block is outside any project directory — records "
                f"live under {research_dir.name}/<project>/",
            )
    return tree


# --------------------------------------------------------------------------
# Ledger rendering
# --------------------------------------------------------------------------


def read_file_lines(path: Path, rel: str) -> list[str]:
    """A file's lines, or a caller error naming it.

    `init` reads only files it is about to edit, and every one of them is a
    file it (or a previous `init`) wrote. A missing or undecodable one means
    the directory is not the research project it looks like, which is a caller
    error — not a traceback out of the middle of a scaffold.
    """
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise UsageError(
            f"{rel} does not exist — this does not look like a research "
            "project directory"
        ) from None
    except (OSError, UnicodeDecodeError) as e:
        raise UsageError(f"cannot read {rel}: {e}") from None


def write_lines(path: Path, lines: list[str]) -> None:
    """Write a generated markdown file: UTF-8, LF, one trailing newline.

    `newline=""` stops the platform translating `\\n` to `\\r\\n` on Windows —
    a generated file whose line endings depend on who ran `init` would be
    rewritten by the next person's formatter and fail the freshness check for
    reasons nobody could see in a diff.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines).rstrip("\n") + "\n"
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(body)


@dataclass(frozen=True)
class Counts:
    """One ledger's four numbers, plus the obligation subtotals.

    Derivation from the records is a later task's; what lives here is the
    *rendering*, because `init` must scaffold a tree that is ledger-**fresh**
    rather than merely marker-bearing. A fresh track whose stored block did not
    already match its derivation would be born stale, and a stale stored ledger
    is an error — `init` would emit a tree failing its own gate. Rendering
    therefore has exactly one implementation, used by `init` now and by
    `write-ledger` later; only the counting is added.
    """

    answered: int = 0
    open_questions: int = 0
    retired: int = 0
    discharged: int = 0
    open_obligations: int = 0
    blocking: int = 0
    stubs: int = 0
    external: int = 0


def render_counts(counts: Counts) -> list[str]:
    """The two stored ledger lines.

    A bullet list, not an aligned table, and deliberately: the reference
    implementation paid for this. A generated block the repo's formatter
    rewrites puts the freshness check and the formatter in a fight neither can
    win, and `dprint` reflows table columns while leaving bullets alone.
    """
    stubs = f"{counts.stubs} stub" + ("" if counts.stubs == 1 else "s")
    return [
        f"- **Questions:** {counts.answered} answered, "
        f"{counts.open_questions} open, {counts.retired} retired",
        f"- **Obligations:** {counts.discharged} discharged, "
        f"{counts.open_obligations} open "
        f"({counts.blocking} blocking, {stubs}, {counts.external} external)",
    ]


def render_project_ledger(tracks: list[tuple[str, Counts]], total: Counts) -> list[str]:
    """The `LEDGER.md` roll-up body: decisions, the same lines per track, totals.

    Totals never print without the per-track breakdown — big projects are
    exactly where one sick track hides inside healthy totals.
    """
    lines = ["## Decisions", "", "_None yet._", "", "## Tracks", ""]
    if not tracks:
        lines += ["_No tracks yet._", ""]
    for name, counts in tracks:
        lines += [f"### {name}", "", *render_counts(counts), ""]
    lines += ["## Total", "", *render_counts(total)]
    return lines


def find_ledger_block(lines: list[str]) -> tuple[int, int] | None:
    """(begin, end) 0-based indices of the ledger marker lines, or None."""
    begin = end = None
    for i, line in enumerate(lines):
        if line.strip() == LEDGER_BEGIN and begin is None:
            begin = i
        elif line.strip() == LEDGER_END and begin is not None:
            end = i
            break
    if begin is None or end is None:
        return None
    return begin, end


def ledger_block_body(path: Path, rel: str) -> tuple[list[str], int, int]:
    """A file's ledger-block body, and the marker indices bounding it."""
    lines = read_file_lines(path, rel)
    span = find_ledger_block(lines)
    if span is None:
        raise UsageError(
            f"{rel} carries no ledger markers ({LEDGER_BEGIN} … {LEDGER_END}) — "
            "they are installed by `init` and are never inserted silently"
        )
    begin, end = span
    return lines, begin, end


def split_track_sections(section: list[str]) -> list[tuple[str, list[str]]]:
    """The `### <track>` sub-sections of a roll-up's `## Tracks` section."""
    groups: list[tuple[str, list[str]]] = []
    for line in section:
        if line.startswith("### "):
            groups.append((line[4:].strip(), [line]))
        elif groups:
            groups[-1][1].append(line)
    return groups


def insert_track_section(body: list[str], track: str, rel: str) -> list[str]:
    """Splice a zero-count section for `track` into an existing roll-up body.

    Deliberately surgical rather than a full re-render. Re-rendering resets
    every other track's stored numbers to zero, which silently destroys a
    roll-up the organizer had regenerated — and a number this script quietly
    zeroes is far worse than one it reports stale, since nothing ever flags it.
    An empty track contributes zero to every total, so leaving the `## Total`
    and `## Decisions` sections alone keeps the block exactly what the
    derivation would emit.
    """
    start = next((i for i, ln in enumerate(body) if ln.strip() == "## Tracks"), None)
    if start is None:
        raise UsageError(
            f"{rel}'s ledger block has no '## Tracks' section to add the track "
            "to — regenerate it with `write-ledger` first"
        )
    end = next(
        (i for i in range(start + 1, len(body)) if body[i].startswith("## ")),
        len(body),
    )
    groups = split_track_sections(body[start + 1 : end])
    if any(name == track for name, _ in groups):
        return body
    groups.append((track, [f"### {track}", "", *render_counts(Counts())]))
    # Sorted, because that is the order the full derivation emits — a roll-up
    # `init` left in some other order would read as stale the moment anyone ran
    # `write-ledger`. A `_No tracks yet._` placeholder belongs to no group and
    # is dropped here, which is what it is for.
    groups.sort(key=lambda g: g[0])
    section: list[str] = [""]
    for _, group in groups:
        trimmed = list(group)
        while trimmed and not trimmed[-1].strip():
            trimmed.pop()
        section += trimmed + [""]
    return body[: start + 1] + section + body[end:]


# --------------------------------------------------------------------------
# Scaffolding (`init`)
# --------------------------------------------------------------------------


def require_name(kind: str, name: str) -> None:
    """Reject a malformed project or track name — before any filesystem op.

    Grammar consistency, not security hardening: the name becomes the
    `project/track/` id-qualification prefix, so a `/`, whitespace or `..`
    corrupts qualification, uniqueness reporting and the cross-project
    destination check, all of which parse on that prefix. It also stops
    `init ../../outside` scaffolding outside the tree.
    """
    if not KEBAB_RE.match(name):
        raise UsageError(
            f"{kind} name {name!r} is not a valid id shape — use "
            f"{KEBAB_DESCRIPTION}. The name becomes this record set's "
            "id-qualification prefix, so a path separator or space would "
            "corrupt every qualified id derived from it."
        )


def project_md(project: str, tracks: list[str]) -> list[str]:
    return [
        f"# {project} — research spike",
        "",
        "A research spike: for a while, this project's job is answering the",
        "questions that gate building the thing, on evidence, in the open.",
        "",
        "## What is being built",
        "",
        "_One paragraph. The thing this spike exists to de-risk._",
        "",
        "## Why the spike exists",
        "",
        "_What cannot be built until these questions are answered, and what",
        "building it blind would cost._",
        "",
        "## How this project is laid out",
        "",
        "- `decisions.md` — the decisions this spike exists to unblock."
        " **Organizer-owned.**",
        "- `LEDGER.md` — the generated roll-up. **Never hand-edited.**",
        "- `tracks/<name>/questions.md` — one track's questions, answers, and its"
        " own stored ledger.",
        "- `tracks/<name>/obligations/` — stub and receipt cards: where deferred"
        " work gets an address.",
        "- `tracks/<name>/contracts/` — optional. Every file in it must declare"
        " its obligations, or declare `none:` with a reason.",
        "",
        "Answering a question in a spike mostly creates **obligations**, not",
        "new questions — and obligations deferred into prose accrue invisibly.",
        "So a deferral must name a `destination:` that **already exists**, and",
        "every question section must say what it owes, or say `none:` and why.",
        "",
        "## Tracks",
        "",
        "A track is an agent-sized context bundle: working one needs `tracks/<name>/`",
        "plus this project's `decisions.md`, and nothing else. Add one with",
        f"`research-spike.py init {project} --track <name>`.",
        *([""] if tracks else []),
        *[track_index_entry(t) for t in tracks],
    ]


def decisions_md(project: str) -> list[str]:
    return [
        f"# {project} — decisions",
        "",
        "**Organizer-owned.** Decisions are filed here, carrying only the human",
        "lifecycle state (`state: pending | decided`) — readiness is derived by",
        "`status` from the questions and obligations that block them, never",
        "stored, so a stale status cannot disagree with the truth. A track that",
        "discovers it needs a decision files a `state: proposed` block in its own",
        "`questions.md` instead; promoting one into this file is an organizer act.",
    ]


def ledger_md(project: str, tracks: list[str]) -> list[str]:
    return [
        f"# {project} — ledger",
        "",
        "Generated by `research-spike.py write-ledger`. **Do not hand-edit the",
        "block below** — edit the records it counts, then regenerate. `validate`",
        "reports this file stale as a warning so track work is never failed by a",
        "file it must not touch; `validate --strict`, the organizer's gate, fails",
        "on it.",
        "",
        "The signal to read here is the **divergence**: questions converging while",
        "obligations climb is the state that feels like futility. These are",
        "snapshots — the trend is read across commits, not reported here.",
        "",
        LEDGER_BEGIN,
        "",
        *render_project_ledger([(t, Counts()) for t in tracks], Counts()),
        "",
        LEDGER_END,
    ]


def questions_md(project: str, track: str) -> list[str]:
    return [
        f"# {project} / {track} — questions",
        "",
        LEDGER_BEGIN,
        "",
        *render_counts(Counts()),
        "",
        LEDGER_END,
        "",
        "The block above is generated by `research-spike.py write-ledger` and",
        "stored, not computed on demand: a number you have to remember to run is",
        "invisible, which is the failure this whole instrument exists to fix. Add",
        "a record, then regenerate — `validate` fails this track if the stored",
        "numbers have gone stale.",
        "",
        "Each question below is a `### Q<n>.` section carrying one `question`",
        "block. Every section must also declare what answering it **owes**: an",
        "`obligation` block per deferral, or a bare `none:` block giving the",
        "reason it owes nothing. Forgetting is not one of the options; only",
        "deliberate silence is, and a reviewer can see that.",
        "",
        "A `destination:` must be a path that **already exists**. Naming a file",
        "that does not exist yet is how deferred work goes dark — write the stub",
        "card first (`obligations/`), then point at it.",
        "",
        "<!--",
        "Worked example. Inert: it lives inside this HTML comment, so `validate`",
        "sees neither the heading nor the blocks. Copy it out, drop the comment",
        "markers from your copy, and fill it in.",
        "",
        "### Q1. Does the account need an isolated uid domain?",
        "",
        "```question",
        "id: uid-domain-isolation",
        "status: open",
        "blocks: account-provisioning",
        "```",
        "",
        "The evidence goes here, in prose: what was measured, where, and what it",
        "showed. The conclusion goes in the block, as `answer:`, before `status:`",
        "may become `answered`.",
        "",
        "```obligation",
        "id: uid-domain-provisioning",
        "owes: the provisioning steps this answer implies",
        f"destination: dev_docs/research/{project}/tracks/{track}/obligations/uid-domain.md",
        "status: open",
        "```",
        "",
        "A section that owes nothing says so, with a reason:",
        "",
        "```obligation",
        "none: option (A) adds no observation and owes no tooling",
        "```",
        "-->",
        "",
        "## Questions",
    ]


def scaffold_project(project_dir: Path, project: str, tracks: list[str]) -> list[Path]:
    """Write the three project-level files.

    `tracks` is whatever this same `init` run is also creating, so a
    `init <p> --track <t>` scaffolds a roll-up that already lists the track
    rather than one patched immediately afterwards.
    """
    write_lines(project_dir / "PROJECT.md", project_md(project, tracks))
    write_lines(project_dir / "decisions.md", decisions_md(project))
    write_lines(project_dir / "LEDGER.md", ledger_md(project, tracks))
    return [
        project_dir,
        project_dir / "PROJECT.md",
        project_dir / "decisions.md",
        project_dir / "LEDGER.md",
    ]


def scaffold_track(track_dir: Path, project: str, track: str) -> list[Path]:
    write_lines(track_dir / QUESTIONS_FILENAME, questions_md(project, track))
    # An empty directory does not survive git, and `obligations/` must exist
    # before the first stub card can be written into it — the destination that
    # already exists is the whole invariant.
    write_lines(track_dir / "obligations" / ".gitkeep", [])
    # Deliberately no `contracts/`: it is optional, and an eagerly created one
    # would be an empty directory subject to the contracts coverage rule.
    return [
        track_dir,
        track_dir / QUESTIONS_FILENAME,
        track_dir / "obligations" / ".gitkeep",
    ]


def track_index_entry(track: str) -> str:
    return f"- [{track}](tracks/{track}/{QUESTIONS_FILENAME})"


def index_track(lines: list[str], track: str, rel: str) -> list[str] | None:
    """`PROJECT.md` with the new track added to its tracks index, or None.

    An index nobody updates is the dead structure this design criticises
    elsewhere, so `init` keeps it current. It appends within the `## Tracks`
    section and nowhere else; if a human has removed that heading, `init`
    returns None so the caller can say so and leave the file alone rather than
    guessing where the list went. A missing index is a note, not a refusal —
    unlike the ledger, nothing downstream reads it.
    """
    entry = track_index_entry(track)
    if entry in lines:
        return lines
    start = next(
        (i for i, ln in enumerate(lines) if ln.strip() == "## Tracks"),
        None,
    )
    if start is None:
        return None
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].startswith("## ") or lines[i].startswith("# ")
        ),
        len(lines),
    )
    body = lines[start:end]
    while body and not body[-1].strip():
        body.pop()
    return lines[:start] + body + ["", entry, ""] + lines[end:]


def verb_init(args: argparse.Namespace, root: Path) -> int:
    """Scaffold a project, and/or add a track to one.

    Scaffolding is the one place this script legitimately creates files.
    Everywhere else, creating a file so that a record resolves is exactly the
    theatre the instrument exists to prevent.
    """
    project: str = args.project
    track: str | None = args.track
    # Both names are checked before anything touches the filesystem, so a
    # rejected `init` never leaves a half-made tree behind.
    require_name("project", project)
    if track is not None:
        require_name("track", track)

    research_dir = root.joinpath(*RESEARCH_SUBPATH)
    project_dir = research_dir / project
    rel = project_dir.relative_to(root).as_posix()
    track_dir = project_dir / TRACKS_DIRNAME / track if track else None

    if track is None and project_dir.exists():
        raise UsageError(
            f"project already exists: {rel} — `init` never overwrites one. "
            f"To add a track to it, run: init {project} --track <name>"
        )
    if track_dir is not None and track_dir.exists():
        raise UsageError(
            f"track already exists: {track_dir.relative_to(root).as_posix()} — "
            "`init` never overwrites one"
        )

    scaffolded_project = not project_dir.exists()
    # Everything this run must read or edit in an **existing** project is
    # resolved here, before a single byte is written. Creating the track first
    # and discovering afterwards that PROJECT.md is unreadable or LEDGER.md has
    # lost its markers leaves a half-made track behind — and the retry then
    # fails on "track already exists", so the caller is stuck with a tree only
    # a manual `rm` gets them out of.
    pending: list[tuple[Path, list[str]]] = []
    index_missing = False
    if track is not None and not scaffolded_project:
        project_path = project_dir / "PROJECT.md"
        ledger_path = project_dir / "LEDGER.md"
        ledger_rel = f"{rel}/LEDGER.md"
        lines, begin, end = ledger_block_body(ledger_path, ledger_rel)
        body = insert_track_section(lines[begin + 1 : end], track, ledger_rel)
        pending.append((ledger_path, lines[: begin + 1] + body + lines[end:]))
        indexed = index_track(
            read_file_lines(project_path, f"{rel}/PROJECT.md"), track, rel
        )
        if indexed is None:
            index_missing = True
        else:
            pending.append((project_path, indexed))

    created: list[Path] = []
    if scaffolded_project:
        created += scaffold_project(project_dir, project, [track] if track else [])
    if track is not None and track_dir is not None:
        created += scaffold_track(track_dir, project, track)
    for path, lines in pending:
        write_lines(path, lines)

    if scaffolded_project:
        print(f"{PROG}: initialized {rel}")
    else:
        print(f"{PROG}: added track '{track}' to {rel}")
    for path in created:
        suffix = "/" if path.is_dir() else ""
        print(f"  {path.relative_to(root).as_posix()}{suffix}")
    if index_missing:
        print(
            f"  note: {rel}/PROJECT.md has no '## Tracks' heading — add "
            f"'{track_index_entry(track)}' to its index by hand"
        )
    if track_dir is None:
        print(f"  next: add a track — init {project} --track <name>")
    else:
        questions = track_dir.relative_to(root).as_posix()
        print(
            f"  next: file your first question in {questions}/{QUESTIONS_FILENAME} — "
            "every section declares what answering it owes, or declares "
            "`none:` and why"
        )
    return EXIT_OK


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
        if rec.qualified_id is not None:
            ident = rec.qualified_id
        elif rec.declared_id is not None:
            # An id the qualification rule cannot place. Named, not dropped —
            # task 3 rejects the placement; silence here would be the
            # invisible-accrual failure this instrument exists to prevent.
            ident = f"{rec.declared_id} (unplaceable: outside any track)"
        else:
            ident = "-"
        print(f"    {rec.kind} @ {rec.rel}:{rec.line} [{scope}] id={ident}")
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
    if args.verbose:
        # Printed before the verdict, and whatever the verdict. The inventory
        # is the only view of what the parser actually made of the tree, so
        # suppressing it on a failing run withholds it exactly when it is most
        # useful — while a finding at `path:line` is far easier to read against
        # the record it came from. Findings stay last so they end the output.
        for project in tree.projects:
            print_inventory_for(tree, project)
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
    p_init.add_argument("project", help="Project name to scaffold.")
    p_init.add_argument(
        "--track",
        metavar="NAME",
        help=(
            "Also add this track. Valid on the initial init and repeatedly "
            "afterwards — it is how a project grows."
        ),
    )
    p_init.set_defaults(run=verb_init)

    # Whole-tree scanning only. `validate [<project>] [--track <t>]
    # [--strict]` is task 7's, and accepting those options here while
    # ignoring them would be the silently-green interface this instrument
    # exists to prevent: `--track mine` would scan every track and report OK,
    # and `--strict` would pass without ever running the strict tier.
    p_validate = subparsers.add_parser(
        "validate", help="The gate: parse and check the whole tree."
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
    # This script's reports carry `—`, `⚠` and `✘`, and it is the one file here
    # that runs on machines nobody in this repo controls — `scripts/validate.py`
    # and `scripts/check.sh` print the same glyphs but only ever run in CI on a
    # UTF-8 host. A consumer's stdout is not guaranteed UTF-8 (on Windows a
    # *redirected* stdout, which is how a plugin invocation captures it, uses
    # the ANSI codepage, and cp1252 has no `✘`), and the encode failure would
    # land on the FAIL path — the one that matters.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
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
