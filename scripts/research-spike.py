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
`init` — the one verb that legitimately creates files — plus the field
semantics of every record kind and the referential checks that resolve
`blocks:`/`blocking:` against the decisions they name — and the `status`
report, whose derived readiness reads that same resolution. Ledger derivation
lands in a later task of the research-spike plan; the verbs it owns dispatch to
an explicit not-implemented-yet error here rather than a silent success.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

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

# The question lifecycle. `retired` is counted separately from `answered`
# everywhere, deliberately: folding the two together would let a project
# converge by giving up.
QUESTION_STATUSES = ("open", "answered", "retired")

# Where contract documents live. Optional and opt-in — `init` does not create
# it — but once it exists every file in it is covered, because contract prose
# states preconditions constantly and a contract document is not a backlog.
CONTRACTS_DIRNAME = "contracts"

# The organizer-owned file promoted decisions live in, and the decision
# lifecycle. `proposed` is deliberately **not** in the tuple: it is a real
# state, but only inside a track's `questions.md`, where a track agent files
# the decision it discovered it needs. Promotion into `decisions.md` is an
# organizer act (SKILL.md's `promote-decision`), never something this script
# performs — a script that promoted would be deciding who owns the decision
# list.
#
# There is no `ready` or `blocked` here, and no key for one either (see
# `KNOWN_FIELDS`): readiness is derived from the blockers on every run, so a
# stored copy could only ever disagree with the truth. Storing both was
# reviewed as a defect, not a convenience.
DECISIONS_FILENAME = "decisions.md"
DECISION_STATES = ("pending", "decided")
PROPOSED_STATE = "proposed"

# Where cards live, and the two enums task 3 owns. `open | discharged` is the
# obligation's whole lifecycle: there is no `in progress`, because a half-state
# is a place for work to sit and look accounted for.
OBLIGATIONS_DIRNAME = "obligations"
OBLIGATION_STATUSES = ("open", "discharged")
CARD_KINDS = ("stub", "receipt")

# A plan directory, by the `plan-with-docs` convention (`<name>_plan/`).
# Matched on the *directory* components of a path only. `/push-plan` deletes
# these folders after migrating them to a tracker, so a pointer into one rots
# on the first push — but the severity differs by field, deliberately. An
# obligation's `destination:` under a plan directory is a **warning**: pointing
# at an in-flight plan is legitimate work-routing, and the record is meant to
# be revisited. A decision's `decided_in:` is an **error**: it is the durable
# evidence of a decision already taken, so a pointer that is scheduled for
# deletion is not evidence at all.
PLAN_DIR_SUFFIX = "_plan"

# Keys each record kind accepts. Unknown keys are an error, not ignored — a
# `desination:` typo must not silently drop the constraint. Only membership is
# checked here; which keys are *required*, and what their values may be, is
# the `validate_*` functions'. Note what is **absent** from `decision`: there
# is no `ready` or `blocked` key, so a derived status has nowhere to be stored
# and this table is what makes hand-editing one impossible.
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

# The `suggest` phrase list — the advisory lexical scan's whole domain
# knowledge, kept in this one clearly-labelled constant so an adopting repo
# can edit it without hunting through the file. It is a **starting point**,
# not a contract (design §"What is repo-specific": "that repo's idiom").
# Matched case-insensitively as a literal substring against each non-inert
# line. "once … lands" is a grammatical shape rather than a fixed string, so
# it alone gets a small regex instead of living in the literal list.
SUGGEST_PHRASES: tuple[str, ...] = (
    "deferred to",
    "gated on",
    "belongs to",
    "handled by",
    "left to",
)
SUGGEST_ONCE_LANDS_RE = re.compile(r"\bonce\b.{0,80}?\blands\b", re.IGNORECASE)


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
        from a decision id, which is what `validate_references` resolves
        `blocks:` and `blocking:` against. Rejecting the placement is
        `validate_obligations`'s job; this
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
class CardCounts:
    """One track's cards, counted by kind.

    Exposed on the tree rather than printed: the ledgers (task 7) report stubs
    and receipts separately because they mean opposite things. A stub is work
    with nowhere to go yet — a growing stub count is the diagnostic — while a
    receipt is work that *did* go somewhere, into a system this validator
    cannot see. Folding them into one number would hide both signals.
    """

    stubs: int = 0
    receipts: int = 0


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
    # Card tallies per track, keyed `project/track`. Filled by the card checks
    # and read by task 7's ledgers — counted once, where the cards are already
    # being walked, so the ledger and the validator can never disagree about
    # how many stubs a track has.
    cards: dict[str, CardCounts] = field(default_factory=dict)

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
            # every rule then has one representation to read, and the reason
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
# Obligations and cards
# --------------------------------------------------------------------------
#
# The load-bearing rule of the whole instrument lives here. A deferral in the
# origin repo stayed visible **exactly when its destination was a file that
# already existed**; "defer to the account track" was invisible because naming
# a track does not create one. So `destination:` is not a comment, and every
# message below says why its rule matters — a bare "invalid path" teaches
# nobody, and a rule nobody understands is one they route around.


def field_line(rec: Record, key: str) -> int:
    """Where to hang a finding: the offending field's own line, or the block's
    opening fence when the field is the thing that is missing."""
    value = rec.fields.get(key)
    return value.line if value is not None else rec.line


def declared(rec: Record, key: str) -> str | None:
    """A field's text, or None when it is absent, blank, or the `none`
    sentinel — the three ways a required field can fail to say anything."""
    value = rec.fields.get(key)
    if value is None or value.is_none or not value.raw:
        return None
    return value.raw


def is_none_block(rec: Record) -> bool:
    """True for a **bare** `none: <reason>` block — `none` and nothing else.

    That is the coverage rule's explicit declaration — "this section owes
    nothing, and here is why" — not an obligation with fields to check. It is
    the only record shape allowed to carry no id, and task 4 is what makes
    sure the reason is there.

    The bareness is the whole guard. Exempting any block that merely *carries*
    a `none:` line let a record declaring nothing owed alongside a real
    `destination:` skip every check in this file, missing destination
    included — a `none:` line would have become the way to switch the
    validator off, which is worse than not having the rules.
    """
    return NONE_KEY in rec.fields and len(rec.fields) == 1


def check_declared_id(
    rec: Record, seen: dict[str, tuple[str, int]], report: Report
) -> None:
    """A declared `id:`'s shape and uniqueness — one implementation, every kind.

    `seen` is a **single namespace shared across record kinds** within a
    project/track, not one map per kind. Two records qualifying to the same
    string are ambiguous whatever their kinds: `blocks:` and `blocking:`
    resolve references by bare id, the status report prints questions and
    obligations under the same `project/track/id` form, and a ledger counting
    both would show one name twice. Per-kind namespaces would make
    `alpha/account/keychain-invariant` mean a question here and an obligation
    there, which is the ambiguity task 3's uniqueness rule exists to remove.

    Says nothing when the id is absent: *whether* a kind requires one, and why,
    is the caller's — the reasons differ per kind and the message should say
    the right one.
    """
    bare = declared(rec, "id")
    if bare is None:
        return
    line = field_line(rec, "id")
    if not KEBAB_RE.match(bare):
        report.error(
            rec.rel,
            line,
            f"{rec.kind} id {bare!r} is not a valid id shape — use "
            f"{KEBAB_DESCRIPTION}. Ids are qualified as project/track/id "
            "and read back in reports and references, so an id carrying a "
            "separator or a space corrupts the qualification.",
        )
        return
    qualified = rec.qualified_id
    if qualified is None:
        return
    prior = seen.get(qualified)
    if prior is None:
        seen[qualified] = (rec.rel, line)
        return
    report.error(
        rec.rel,
        line,
        f"duplicate {rec.kind} id '{qualified}' — already declared at "
        f"{prior[0]}:{prior[1]}. Qualified ids must be unique across the whole "
        "tree, and across record kinds: two records sharing one are counted "
        "twice in the ledger, and a `blocks:` or `blocking:` reference to them "
        "names neither. Rename one.",
    )


def in_obligations_dir(tree: Tree, path: Path) -> bool:
    """True for a file under some track's `obligations/` directory."""
    try:
        parts = path.relative_to(tree.research_dir).parts
    except ValueError:
        return False
    return (
        len(parts) >= 5
        and parts[1] == TRACKS_DIRNAME
        and parts[3] == OBLIGATIONS_DIRNAME
    )


def in_contracts_dir(tree: Tree, path: Path) -> bool:
    """True for a file under some track's `contracts/` directory.

    `suggest`'s only consumer: the contracts coverage rule is file-scoped, not
    section-scoped, so suppression there follows the same scope.
    """
    try:
        parts = path.relative_to(tree.research_dir).parts
    except ValueError:
        return False
    return (
        len(parts) >= 5 and parts[1] == TRACKS_DIRNAME and parts[3] == CONTRACTS_DIRNAME
    )


def track_key(rec: Record) -> str:
    return f"{rec.project}/{rec.track}"


# Named once because four separate destination errors have to end the same
# way: every one of them is fixed by pointing at a file that is already there.
POINT_AT = (
    "point at a specific file that already exists — the plan's epic file "
    "(<name>_plan/<name>_plan.md), a particular task card, or a stub or "
    "receipt card under tracks/<track>/obligations/"
)


@dataclass(frozen=True)
class PathField:
    """One path-valued field's name, and the sentences its errors end with.

    `destination:` and `decided_in:` are checked by **one** implementation:
    both are repo-relative pointers at an existing regular file inside the
    repo, and two copies of that rule is two places for it to drift — the
    symlink-escape and symlink-loop cases in particular were each got wrong
    once already, and getting them wrong twice is what a second copy buys.

    What legitimately differs is *why* each field exists, so each carries its
    own closing prose: a rule that fires with the wrong field's explanation
    teaches nobody, and a rule nobody understands is one they route around.
    What also differs is what happens **after** containment holds — the
    cross-project rule and the plan-directory severity — and that stays with
    each caller rather than being flagged into this function.
    """

    key: str
    # The "point at …" sentence: how a reader fixes any of these.
    fix: str
    # Why a pointer to a file that is not there is the failure it is.
    missing: str
    # Optional extra clause on the it-is-a-directory error.
    dir_hint: str = ""


DESTINATION_FIELD = PathField(
    key="destination",
    fix=POINT_AT,
    missing=(
        "this is how deferred work goes dark: naming a place does not create "
        "one, and a deferral to a path that is not there reads as routed "
        "while routing nowhere. Write the file first — a stub card under "
        "tracks/<track>/obligations/ carrying its own `superseded_when:` is "
        "the usual move — then point at it."
    ),
    dir_hint=(
        " (If the file you pick sits under a *_plan/ directory, read the "
        "plan-directory warning first: /push-plan deletes those folders.)"
    ),
)


def check_contained_file(
    rec: Record, spec: PathField, raw: str, tree: Tree, report: Report
) -> Path | None:
    """The containment rules every path-valued field shares.

    Repo-relative, no `../`, no symlink escape, resolvable, and an existing
    **regular file**. Returns the resolved path when all of them hold, and
    None — having reported exactly one finding — when any does not.

    Checked in order and reported one at a time: a path that is absolute is
    not also usefully described as missing, and a stack of derived findings
    over one wrong line is how a report stops being read.
    """
    rel = rec.rel
    line = field_line(rec, spec.key)
    key = spec.key

    pure = PurePosixPath(raw)
    if pure.is_absolute() or raw.startswith("~"):
        report.error(
            rel,
            line,
            f"{key} '{raw}' is an absolute path — these pointers are "
            "repo-relative so the record means the same thing in every clone; "
            "an absolute one names somebody's own disk and resolves nowhere "
            "for the next reader. Write it relative to the repository root.",
        )
        return None
    if ".." in pure.parts:
        report.error(
            rel,
            line,
            f"{key} '{raw}' escapes the repository with '../' — a pointer has "
            "to be reviewable alongside the record that names it, and nothing "
            f"outside the tree is. {spec.fix}.",
        )
        return None

    try:
        resolved = (tree.root / raw).resolve()
    except (OSError, RuntimeError) as e:
        # A symlink loop is the realistic case, and pathlib's own error for it
        # is a **RuntimeError**, not an OSError — catching only OSError left
        # this line raising out of the middle of the walk. A traceback exits 1,
        # which this script's contract reserves for tree-content violations, so
        # the crash would be indistinguishable from a real finding while hiding
        # every record the scan never reached.
        report.error(
            rel,
            line,
            f"{key} '{raw}' cannot be resolved ({e}) — the address does not "
            "lead anywhere the filesystem can follow, so neither can a "
            f"reader. {spec.fix}.",
        )
        return None
    try:
        resolved.relative_to(tree.root)
    except ValueError:
        # No `..` in the text, so the escape came through a symlink. Same
        # consequence either way: the file it points at is in no clone of this
        # repo, so the record is addressed to nowhere.
        report.error(
            rel,
            line,
            f"{key} '{raw}' resolves through a symlink to '{resolved}', "
            "outside the repository — no clone of this repo contains that "
            f"file, so the pointer has no address anyone else can follow. "
            f"{spec.fix}.",
        )
        return None

    if not resolved.exists():
        report.error(rel, line, f"{key} '{raw}' does not exist — {spec.missing}")
        return None
    if resolved.is_dir():
        report.error(
            rel,
            line,
            f"{key} '{raw}' is a directory, not a file — a directory can "
            "exist while saying nothing about the work, whereas a file names "
            f"it. {spec.fix}.{spec.dir_hint}",
        )
        return None
    if not resolved.is_file():
        report.error(
            rel,
            line,
            f"{key} '{raw}' is not a regular file — a pointer has to name "
            f"something a reader can open and a diff can show. {spec.fix}.",
        )
        return None
    return resolved


def plan_directory(raw: str, resolved: Path, tree: Tree) -> str | None:
    """The `*_plan` directory component of a path, declared or resolved.

    Both forms are inspected, and the declared one is why: a pointer written
    *into* a plan directory that happens to be a symlink out to a durable file
    resolves somewhere safe and is deleted anyway, because `/push-plan` removes
    the folder — symlink included. Checking only the resolved path passed that
    pointer clean and let it dangle on the first push, which is the exact rot
    this rule exists to catch.

    Directory components only, in both forms: the file itself is not the folder
    `/push-plan` deletes, and a file legitimately named `<name>_plan.md` is the
    epic file an in-flight plan is *pointed at* by.
    """
    parts = (
        *PurePosixPath(raw).parts[:-1],
        *resolved.relative_to(tree.root).parts[:-1],
    )
    return next((p for p in parts if p.endswith(PLAN_DIR_SUFFIX)), None)


def check_destination(rec: Record, tree: Tree, report: Report) -> None:
    """The destination rules: the shared containment ones, plus the two this
    field owns — no cross-project destinations (error), and a plan directory
    (warning).
    """
    rel = rec.rel
    line = field_line(rec, "destination")
    raw = declared(rec, "destination")
    if raw is None:
        report.error(
            rel,
            line,
            "obligation block: 'destination:' is required — an obligation "
            "deferred into prose accrues invisibly, which is the failure this "
            f"instrument exists to prevent. {POINT_AT}.",
        )
        return

    resolved = check_contained_file(rec, DESTINATION_FIELD, raw, tree, report)
    if resolved is None:
        return

    research_parts: tuple[str, ...] = ()
    try:
        research_parts = resolved.relative_to(tree.research_dir).parts
    except ValueError:
        pass
    # `>= 2` because a file sitting *directly* in dev_docs/research/ (a README
    # at the root of the tree) is in no project at all — with `>= 1` its own
    # filename landed in `research_parts[0]` and got reported as "another
    # research project", which is a rule firing on a name it invented.
    if len(research_parts) >= 2 and research_parts[0] != rec.project:
        report.error(
            rel,
            line,
            f"destination '{raw}' lands in another research project "
            f"('{research_parts[0]}') — work an answer creates for another "
            "project is filed in that project, so every project's inbound "
            "dependencies stay visible in its own ledger rather than in a "
            "neighbour's. File the work there, then point this obligation at "
            "a receipt card under tracks/<track>/obligations/ recording the "
            "handoff.",
        )
        return

    plan_dir = plan_directory(raw, resolved, tree)
    if plan_dir is not None:
        report.warn(
            rel,
            line,
            f"destination '{raw}' is inside a plan directory "
            f"('{plan_dir}') — /push-plan deletes plan directories once it "
            "has migrated them to a tracker, so this pointer rots on the "
            "first push and the obligation goes dark exactly the way an "
            "unwritten destination does. Pointing at an in-flight plan is "
            "legitimate; revisit it before the plan is pushed, and replace it "
            "with a receipt card under tracks/<track>/obligations/ recording "
            "the handoff.",
        )


def validate_obligations(
    tree: Tree, seen: dict[str, tuple[str, int]], report: Report
) -> None:
    """Field semantics for every `obligation` block in the tree.

    Uniqueness is checked over the whole tree in one pass, not per file: two
    files each internally consistent is precisely how the reference
    implementation ended up with a `blocking:` reference that could mean
    either of two records. `seen` is passed in because the namespace is shared
    with every other kind that declares an id — see `check_declared_id`.
    """
    for rec in tree.records:
        if rec.kind != "obligation":
            continue
        rel = rec.rel
        if is_none_block(rec):
            # The coverage rule's explicit declaration. The one field it has is
            # checked here rather than in the coverage walk, because a bare
            # `none:` block is the same record wherever it is filed — and it
            # exists only so a reader can challenge the claim, which an empty
            # one gives them nothing to do.
            reason = rec.fields[NONE_KEY]
            if not reason.reason:
                report.error(
                    rel,
                    reason.line,
                    "a bare `none:` block gives no reason — the declaration is "
                    "worth having only because a reviewer can see it and "
                    "challenge it, and 'none' on its own is indistinguishable "
                    "from the forgetting this rule exists to remove. Say what "
                    "makes this owe nothing, e.g. `none: option (A) adds no "
                    "observation and owes no tooling`.",
                )
            continue

        if NONE_KEY in rec.fields:
            # Not exempt — reaching here means the block carries `none:` *and*
            # ordinary fields, so it is trying to be two records at once: a
            # declaration that nothing is owed, and an obligation owing
            # something. Reported, then checked in full, because the half that
            # registers work is the half that must not go unvalidated.
            report.error(
                rel,
                field_line(rec, NONE_KEY),
                "obligation block declares 'none:' alongside other fields — a "
                "block either declares that nothing is owed, or registers "
                "something owed; it cannot do both, and a reader cannot tell "
                "which one was meant. Split it into a bare `none:` block and "
                "an obligation, or drop the line that does not apply.",
            )

        if rec.track is None:
            report.error(
                rel,
                rec.line,
                "obligation block is outside any track — an obligation is "
                "identified as project/track/id and counted in its track's "
                "ledger, so one filed outside tracks/<track>/ belongs to no "
                "ledger and can be named by no reference. Move it into the "
                "track whose work it is.",
            )

        if declared(rec, "id") is None:
            report.error(
                rel,
                field_line(rec, "id"),
                "obligation block: 'id:' is required — an obligation with no "
                "id cannot be counted, referenced by `blocking:`, or reported "
                "as discharged.",
            )
        check_declared_id(rec, seen, report)

        if declared(rec, "owes") is None:
            report.error(
                rel,
                field_line(rec, "owes"),
                "obligation block: 'owes:' is required — one line, in your "
                "own words, saying what is owed. A destination without it is "
                "an address with no letter in it; nobody picking the work up "
                "later can tell what was deferred.",
            )

        status = declared(rec, "status")
        if status is None:
            report.error(
                rel,
                field_line(rec, "status"),
                "obligation block: 'status:' is required — "
                f"{' | '.join(OBLIGATION_STATUSES)}. The open count is half "
                "the divergence signal the ledger exists to show, and a "
                "record with no status is counted in neither column.",
            )
        elif status not in OBLIGATION_STATUSES:
            report.error(
                rel,
                field_line(rec, "status"),
                f"obligation status {status!r} is not one of "
                f"{' | '.join(OBLIGATION_STATUSES)} — there is deliberately "
                "no in-between state, because a half-state is somewhere work "
                "sits looking accounted for.",
            )

        discharged_by = rec.fields.get("discharged_by")
        if status == "discharged" and declared(rec, "discharged_by") is None:
            report.error(
                rel,
                field_line(rec, "discharged_by"),
                "a discharged obligation requires 'discharged_by:' — free "
                "text naming the change that discharged it, typically a PR or "
                "commit reference. Without it 'discharged' is an assertion "
                "nobody can check. It is deliberately not path-checked: the "
                "discharging artifact usually lives outside this tree.",
            )
        elif status == "open" and discharged_by is not None:
            report.error(
                rel,
                field_line(rec, "discharged_by"),
                "'discharged_by:' is set while status is open — one of the "
                "two is wrong, and either way the record is reported as "
                "outstanding work that somebody has already done. Flip the "
                "status, or drop the field.",
            )

        # `blocking:` is optional — most obligations should not carry it, and
        # scarcity is what keeps convergence meaningful. But a `blocking:` that
        # is *written* and names nothing is the same defect as task 4's
        # `blocks: ,`: it has truthy text, is not the sentinel, parses to zero
        # ids, and so declares a gate that gates nothing while reading as
        # wired up. Whether the ids it does carry name real decisions is
        # `validate_references`'s, which needs the whole tree.
        blocking = rec.fields.get("blocking")
        if blocking is not None and not blocking.is_none and not blocking.items:
            report.error(
                rel,
                blocking.line,
                "'blocking:' is written but names no decision — a separator or "
                "an empty value parses to zero ids, so this obligation is "
                "reported as gating nothing while looking like it gates "
                "something, and the decision it was meant to hold up converges "
                "without it. Name the decision, or drop the line: `blocking:` "
                "is optional, and it is meant to be scarce.",
            )

        check_destination(rec, tree, report)


def validate_cards(tree: Tree, report: Report) -> None:
    """Card blocks, and the files under `obligations/` that must carry one.

    Cards are what make stubs and handoffs first-class instead of folklore, so
    both halves are checked: a card block filed somewhere the ledger never
    walks, and a file in the directory the ledger *does* walk that turns out
    to hold nothing.
    """
    carded: dict[Path, Record] = {}
    for rec in tree.records:
        if rec.kind != "card":
            continue
        rel = rec.rel
        if not in_obligations_dir(tree, rec.path):
            report.error(
                rel,
                rec.line,
                "card block outside tracks/<track>/obligations/ — cards are "
                "counted by walking that directory, so one filed anywhere "
                "else is a stub or a handoff that no ledger will ever show. "
                "Move the file into the track's obligations/ directory.",
            )
            continue
        first = carded.setdefault(rec.path, rec)
        if first is not rec:
            # One card per file, by design: the file *is* the address an
            # obligation's `destination:` points at, so a second block in it
            # gives one address two meanings — and the destination can only
            # ever name the file. Not counted either; a tally that included
            # both would put a stub in the ledger nobody can point at.
            report.error(
                rel,
                rec.line,
                "a second card block in this file — a card file holds exactly "
                f"one card (the first is at line {first.line}), because the "
                "file is the address an obligation's `destination:` points at "
                "and a path cannot name one of two blocks. Split it into two "
                "files under obligations/.",
            )
            continue
        counts = tree.cards.setdefault(track_key(rec), CardCounts())

        kind = declared(rec, "kind")
        if kind is None:
            report.error(
                rel,
                field_line(rec, "kind"),
                "card block: 'kind:' is required — "
                f"{' | '.join(CARD_KINDS)}. The two are opposites in the "
                "ledger: a stub is work with nowhere to go yet, a receipt is "
                "work that went somewhere this validator cannot see.",
            )
            continue
        if kind not in CARD_KINDS:
            report.error(
                rel,
                field_line(rec, "kind"),
                f"card kind {kind!r} is not one of {' | '.join(CARD_KINDS)} — "
                "a card either holds a place open (stub) or records a handoff "
                "out of this tree (receipt); there is no third thing for the "
                "ledger to count.",
            )
            continue

        if kind == "stub":
            counts.stubs += 1
            if declared(rec, "superseded_when") is None:
                report.error(
                    rel,
                    field_line(rec, "superseded_when"),
                    "a stub card requires 'superseded_when:' — the condition "
                    "of this card's own deletion. A stub that never gets "
                    "superseded is a new place for work to hide, and the "
                    "written condition is what lets a later reader tell "
                    "whether it is still holding anything open.",
                )
        else:
            counts.receipts += 1
            if declared(rec, "url") is None:
                report.error(
                    rel,
                    field_line(rec, "url"),
                    "a receipt card requires 'url:' — the address of the work "
                    "in the system it was handed to. The receipt is the whole "
                    "reason the destination-must-exist rule survives a "
                    "handoff: the path stays local and real while the "
                    "external reference lives in content the validator never "
                    "fetches (it is offline by contract, and an unreachable "
                    "tracker must never fail this gate).",
                )

    for project in tree.projects:
        for track in project.tracks:
            directory = track.path / OBLIGATIONS_DIRNAME
            if not directory.is_dir():
                continue
            tree.cards.setdefault(f"{project.name}/{track.name}", CardCounts())
            # **Every** file, not just `*.md`. Globbing markdown alone let a
            # `notes.txt` sit here holding deferred work that no parser reads
            # and no ledger counts — a labelled hiding place inside the one
            # directory whose entire purpose is that work cannot hide in it.
            #
            # Dotfiles are exempt: a dotfile cannot plausibly be a card, and
            # two of them are unavoidable here — `init` writes
            # `obligations/.gitkeep` because an empty directory does not
            # survive git, and macOS's Finder drops `.DS_Store` into any
            # directory somebody opens.
            for entry in sorted(directory.rglob("*")):
                if not entry.is_file() or entry.name.startswith("."):
                    continue
                rel_entry = entry.relative_to(tree.root).as_posix()
                if entry.suffix != ".md":
                    report.error(
                        rel_entry,
                        0,
                        "not a markdown card — every file under obligations/ "
                        "is a card, and only markdown is parsed for card "
                        "blocks, so this file holds work no ledger will ever "
                        "count. Rewrite it as a `.md` card file, or move it "
                        "somewhere the directory's rule does not apply.",
                    )
                    continue
                if entry in carded:
                    continue
                report.error(
                    rel_entry,
                    0,
                    "no card block in this file — every file under "
                    "obligations/ is a card, and the ledger counts stubs and "
                    "receipts by walking this directory. A file here with no "
                    "block looks filed and is counted nowhere. Add a ```card "
                    "block: kind: stub with superseded_when:, or kind: "
                    "receipt with url:.",
                )


# --------------------------------------------------------------------------
# Questions and the coverage rule
# --------------------------------------------------------------------------
#
# The half that matters more. Records alone catch only *malformed* deferrals;
# they cannot catch the deferral nobody registered, which is the actual failure
# mode — and the naive fix, a grep for deferral vocabulary, asks a question that
# is unanswerable over English prose. "Is the field present?" is mechanical
# instead: forgetting stops being available, and only deliberate silence
# remains, which is a thing a reviewer can see and challenge.
#
# Scope is questions files and `contracts/` only. Widening it to every markdown
# file would turn the discipline into noise and train people to satisfy it
# mechanically (design §"What the skill must **not** do").

# The one sentence that ends every coverage error, because every one of them is
# fixed the same way.
DECLARE_SOMETHING = (
    "register an ```obligation block per deferral, or a bare "
    "`none: <reason>` block saying why nothing is owed"
)


def records_by_file(tree: Tree) -> dict[str, list[Record]]:
    """Every record, grouped by the file it was parsed from."""
    index: dict[str, list[Record]] = {}
    for rec in tree.records:
        index.setdefault(rec.rel, []).append(rec)
    return index


def check_question(
    rec: Record,
    covered: bool,
    seen: dict[str, tuple[str, int]],
    report: Report,
) -> None:
    """Field semantics for one `question` block, given its section's coverage."""
    rel = rec.rel

    if rec.track is None:
        report.error(
            rel,
            rec.line,
            "question block is outside any track — a question is identified as "
            "project/track/id and counted in its track's ledger, so one filed "
            "outside tracks/<track>/ belongs to no ledger and can be named by "
            "no reference. Move it into the track whose work it is.",
        )

    if declared(rec, "id") is None:
        report.error(
            rel,
            field_line(rec, "id"),
            "question block: 'id:' is required — a question with no id cannot "
            "be counted in the ledger, named as a decision's blocker, or "
            "reported answered.",
        )
    check_declared_id(rec, seen, report)

    status = declared(rec, "status")
    if status is None:
        report.error(
            rel,
            field_line(rec, "status"),
            "question block: 'status:' is required — "
            f"{' | '.join(QUESTION_STATUSES)}. The question pair is half the "
            "divergence signal the ledger exists to show, and a record with no "
            "status is counted in no column of it.",
        )
    elif status not in QUESTION_STATUSES:
        report.error(
            rel,
            field_line(rec, "status"),
            f"question status {status!r} is not one of "
            f"{' | '.join(QUESTION_STATUSES)} — a question either stands open, "
            "carries a recorded answer, or has left the board with its premise. "
            "Retirement is counted separately from answering on purpose: "
            "folding them together would let a project converge by giving up.",
        )

    blocks = rec.fields.get("blocks")
    # A non-sentinel value with nothing in its list is missing, not present:
    # `blocks: ,` has truthy raw text, parses to zero ids, and is not the
    # sentinel, so a raw-emptiness test let a separator typo validate clean
    # while naming no decision at all. The sentinel branch is kept separate
    # because a `none:` value legitimately has no items.
    if blocks is None or (not blocks.is_none and not blocks.items):
        report.error(
            rel,
            field_line(rec, "blocks"),
            "question block: 'blocks:' is required — one or more decision ids, "
            "or the sentinel `blocks: none: <reason>`. It is the convergence "
            "hook: a decision's blocker list is derived from these, so a "
            "question that names nothing is a question no report can place.",
        )
    elif blocks.is_none and not blocks.reason:
        # The reason is what makes the sentinel a claim rather than a shrug.
        report.error(
            rel,
            blocks.line,
            "'blocks: none' gives no reason — a question that gates no "
            "decision is worth noticing, so the sentinel has to say why "
            "(`blocks: none: <reason>`). Without one there is no way to tell a "
            "genuinely free-standing question from one nobody got round to "
            "wiring to the decision it gates.",
        )
    # A `blocks:` naming real decisions is accepted and recorded here; whether
    # those decisions exist is `validate_references`'s, which needs the whole
    # tree before any reference can be resolved.

    if status == "retired" and declared(rec, "retired_because") is None:
        report.error(
            rel,
            field_line(rec, "retired_because"),
            "a retired question requires 'retired_because:' — retirement is "
            "legitimate scope reduction, but a question that leaves the board "
            "without saying what killed its premise is indistinguishable from "
            "one quietly dropped. This is how questions leave without "
            "pretending to be answered.",
        )

    if status == "answered":
        if declared(rec, "answer") is None:
            report.error(
                rel,
                field_line(rec, "answer"),
                "an answered question requires 'answer:' — a one-line "
                "conclusion in the record. The evidence belongs in the section "
                "prose, but a conclusion left only in prose is one two readers "
                "can read differently, so `answered` cannot be reached without "
                "saying what the answer is.",
            )
        if not covered:
            report.error(
                rel,
                field_line(rec, "status"),
                "question is 'answered' while its section declares no "
                "obligations — answering a question in a spike mostly creates "
                "work rather than new questions, and that work is what accrues "
                "invisibly when it is deferred into prose. Coverage cannot be "
                f"satisfied by prose alone: {DECLARE_SOMETHING}, then flip the "
                "status.",
            )


def validate_questions(
    tree: Tree, seen: dict[str, tuple[str, int]], report: Report
) -> None:
    """Question records, and the coverage rule over their sections.

    The section is the unit, not the file: it is what the `### Q<n>.` heading
    addresses and what a reviewer reads, so it is what has to declare
    something.
    """
    index = records_by_file(tree)
    claimed: set[int] = set()
    for rel, sections in sorted(tree.sections.items()):
        file_records = index.get(rel, [])
        for section in sections:
            records = [
                r
                for r in file_records
                if section.start_line <= r.line <= section.end_line
            ]
            questions = [r for r in records if r.kind == "question"]
            claimed.update(id(r) for r in questions)
            # Every question record is checked, not just the section's first.
            # A second block is a misplacement *and* a record — leaving its
            # fields unchecked would also leave its id out of `seen`, so a
            # duplicate declared in an extra block went unreported entirely.
            # Any obligation block satisfies coverage, the bare `none:`
            # declaration included — the rule is that the section *declared*,
            # not that it owes. A malformed declaration is reported by its own
            # rule rather than a second time as missing coverage.
            covered = any(r.kind == "obligation" for r in records)

            if not questions:
                report.error(
                    rel,
                    section.start_line,
                    f"Q{section.number} carries no `question` block — the "
                    "heading is prose, and only the block carries the id, "
                    "status and `blocks:` that the ledger counts and a "
                    "decision's blocker list resolves against. Add a "
                    "```question block with `id:`, `status:` and `blocks:`.",
                )
            for extra in questions[1:]:
                report.error(
                    rel,
                    extra.line,
                    f"a second `question` block in Q{section.number} (the "
                    f"first is at line {questions[0].line}) — one section is "
                    "one question, because the section is what the coverage "
                    "rule declares against and what the `### Q<n>.` numbering "
                    "addresses. Give the second question its own section.",
                )

            if not covered:
                report.error(
                    rel,
                    section.start_line,
                    f"Q{section.number} declares nothing it owes — every "
                    "question section must say what answering it creates, "
                    "including saying that it creates nothing and why. "
                    "Deferrals left in prose accrue invisibly, which is the "
                    f"failure this instrument exists to prevent: "
                    f"{DECLARE_SOMETHING}.",
                )

            for rec in questions:
                check_question(rec, covered, seen, report)

    for rec in tree.records:
        if rec.kind != "question" or id(rec) in claimed:
            continue
        report.error(
            rec.rel,
            rec.line,
            "question block outside a `### Q<n>.` section — coverage is "
            "enforced per section, so a question filed outside one is "
            "never asked to declare what answering it owes, and no "
            "`### Q<n>.` heading names it for a reader. Move it under a "
            f"`### Q<n>.` heading in the track's {QUESTIONS_FILENAME}.",
        )
        # Checked in full anyway: the placement is wrong, but the record still
        # claims an id the rest of the tree can collide with, and a block whose
        # fields go unread is a block whose duplicate id nobody reports.
        # `covered=True` because the section that would carry the declaration
        # is the thing that is missing — a coverage error here would just
        # restate the placement error in different words.
        check_question(rec, True, seen, report)


def validate_contracts(tree: Tree, report: Report) -> None:
    """Coverage over `tracks/<track>/contracts/`.

    Contract documents state preconditions constantly, and a precondition is
    work somebody owes. That class hid the worst offender in the origin repo —
    a precondition that was on no list at all. Unlike arbitrary prose this is a
    bounded, opt-in directory the skill owns, so the mechanical rule is
    available here; declining it would be building a labeled hiding place.
    """
    index = records_by_file(tree)
    for project in tree.projects:
        for track in project.tracks:
            directory = track.path / CONTRACTS_DIRNAME
            if not directory.is_dir():
                continue
            # **Every** file, not just `*.md` — the same treatment the
            # obligations/ walk gets, and for the same reason: globbing
            # markdown alone leaves a `preconditions.txt` holding preconditions
            # no parser reads and no ledger counts, which is precisely the
            # labeled hiding place this rule exists to close. Dotfiles are
            # exempt (a dotfile cannot plausibly be a contract, and `.DS_Store`
            # lands in any directory somebody opens).
            for entry in sorted(directory.rglob("*")):
                if not entry.is_file() or entry.name.startswith("."):
                    continue
                rel = entry.relative_to(tree.root).as_posix()
                if entry.suffix != ".md":
                    report.error(
                        rel,
                        0,
                        "not a markdown contract — only markdown is parsed for "
                        "obligation blocks, so a contract written in any other "
                        "format states its preconditions where nothing can "
                        "read them and no ledger will ever count them. Rewrite "
                        "it as a `.md` file, or move it somewhere the "
                        "directory's rule does not apply.",
                    )
                    continue
                if any(r.kind == "obligation" for r in index.get(rel, [])):
                    continue
                report.error(
                    rel,
                    0,
                    "this contract file declares no obligations — a contract "
                    "document states preconditions constantly ('this component "
                    "must not be deployed on a shared host'), every one of them "
                    "is work somebody owes, and a contract document is not a "
                    f"backlog. {DECLARE_SOMETHING}.",
                )


# --------------------------------------------------------------------------
# Decisions, and referential integrity across record types
# --------------------------------------------------------------------------
#
# The convergence hook. Questions and obligations name the decision they block,
# so "what still blocks building?" is a derived fact rather than a feeling —
# and that only holds if the names resolve. A `blocks:` naming a decision
# nobody ever filed is the "track that did not exist" bug wearing a different
# hat: it reads as wired up, gates nothing, and no report can see the gap.
#
# Two rules below look like one and are worth separating in the reader's head.
# "A `decided` decision has zero open blockers" and "a new open blocker against
# a `decided` decision is an error" are the same *condition* seen from either
# end — so they are one check, reported at the referencing record, because that
# is where the fix goes.

DECIDED_IN_POINT_AT = (
    "point at durable evidence that already exists — an ADR, a permanent "
    "design doc, or a receipt card under tracks/<track>/obligations/"
)

DECIDED_IN_FIELD = PathField(
    key="decided_in",
    fix=DECIDED_IN_POINT_AT,
    missing=(
        "a decision's evidence is what the next reader consults instead of "
        "re-litigating it, and a pointer to a file that is not there records "
        "only that somebody typed a path. Write the ADR, design doc or "
        "receipt card first, then point at it."
    ),
)

# How a decision is reopened, quoted in three messages because the three rules
# that mention it are only coherent together.
REOPEN_SHAPE = (
    "`state: pending` plus `reopened_because:` plus the **retained** "
    "`decided_in:` of the decision being reopened"
)


def in_track_questions(rec: Record) -> bool:
    """True for a record filed in some track's `questions.md`."""
    return rec.track is not None and rec.path.name == QUESTIONS_FILENAME


def check_decided_in(
    rec: Record, state: str | None, tree: Tree, report: Report
) -> None:
    """`decided_in:`, and the reopen exemption that keeps it coherent.

    Required when `decided`, and permitted otherwise **only** as reopen
    evidence. That exemption is the whole reason a reopened decision can sit
    at `state: pending` while still carrying the pointer — and the retained
    pointer is, in turn, the only structural evidence that there was ever a
    decision to reopen. This validator reads one snapshot of the tree with no
    history, so without it a legitimately reopened decision is byte-identical
    to one that never decided anything and says `reopened_because:` anyway.
    """
    rel = rec.rel
    raw = declared(rec, "decided_in")
    reopened = declared(rec, "reopened_because")

    if raw is None:
        if reopened is not None:
            report.error(
                rel,
                field_line(rec, "reopened_because"),
                "'reopened_because:' with no retained 'decided_in:' — a "
                "decision that was never decided has nothing to reopen, and "
                "this validator reads one snapshot of the tree with no "
                "history, so the retained pointer is the only structural "
                "evidence that there was a prior decision at all. Keep the "
                f"`decided_in:` of the decision being reopened ({REOPEN_SHAPE}"
                "), or drop `reopened_because:` and file this as an ordinary "
                "pending decision.",
            )
        elif state == "decided":
            report.error(
                rel,
                field_line(rec, "decided_in"),
                "a decided decision requires 'decided_in:' — a pointer to the "
                "durable evidence of the decision: an ADR, a permanent design "
                "doc, or a receipt card. Without one 'decided' is an assertion "
                "with nothing behind it, and the next person to ask why "
                f"re-litigates it from scratch. {DECIDED_IN_POINT_AT}.",
            )
        return

    # The exemption is the whole reopen *shape*, not the presence of one field.
    # Keying it on `reopened_because:` alone let any other state carry the
    # pointer by adding a single line — a `proposed` decision could claim the
    # evidence of a decision nobody ever took, and validate clean while doing
    # it. `pending` **and** `reopened_because:`, together, or nothing.
    if state != "decided" and not (state == "pending" and reopened is not None):
        report.error(
            rel,
            field_line(rec, "decided_in"),
            f"'decided_in:' on a decision whose state is {state!r} — the "
            "pointer belongs to a decision that was taken, and the only "
            f"exemption is reopen evidence, which is a whole shape: "
            f"{REOPEN_SHAPE}. `reopened_because:` on its own does not earn it, "
            "or any state at all could claim evidence of a decision nobody "
            "took. Either set `state: decided`, or file this as the reopen it "
            "is.",
        )

    resolved = check_contained_file(rec, DECIDED_IN_FIELD, raw, tree, report)
    if resolved is None:
        return
    plan_dir = plan_directory(raw, resolved, tree)
    if plan_dir is not None:
        report.error(
            rel,
            field_line(rec, "decided_in"),
            f"decided_in '{raw}' is inside a plan directory ('{plan_dir}') — "
            "/push-plan deletes plan directories once it has migrated them to "
            "a tracker, so this pointer is scheduled for deletion and the "
            "evidence for the decision disappears on the first push. Unlike an "
            "obligation's destination, which may legitimately point at an "
            "in-flight plan, a decision's evidence has to outlive the work: "
            f"{DECIDED_IN_POINT_AT}.",
        )


def validate_decisions(
    tree: Tree, seen: dict[str, tuple[str, int]], report: Report
) -> None:
    """Field semantics for every `decision` block in the tree.

    Only the human lifecycle state is stored, so there is very little here on
    purpose: `state`, the evidence pointer, and where a `proposed` one may be
    filed. Readiness is derived on every run by the reference walk below, and
    there is no key to hand-edit it wrongly with — `KNOWN_FIELDS` rejects
    `ready:`/`blocked:` as unknown keys, which is the record format itself
    refusing to hold a number that could go stale.
    """
    for rec in tree.records:
        if rec.kind != "decision":
            continue
        rel = rec.rel

        if declared(rec, "id") is None:
            report.error(
                rel,
                field_line(rec, "id"),
                "decision block: 'id:' is required — the id is what a "
                "question's `blocks:` and an obligation's `blocking:` name, so "
                "a decision without one can gate nothing and appear in no "
                "convergence report.",
            )
        check_declared_id(rec, seen, report)

        state = declared(rec, "state")
        if state is None:
            report.error(
                rel,
                field_line(rec, "state"),
                "decision block: 'state:' is required — "
                f"{' | '.join(DECISION_STATES)} (plus `{PROPOSED_STATE}`, "
                "valid only inside a track's questions.md). It is the one "
                "thing a decision stores; everything else about it — ready, "
                "blocked, by what — is derived from the questions and "
                "obligations that name it.",
            )
        elif state == PROPOSED_STATE and not in_track_questions(rec):
            where = (
                "decisions.md holds decisions the organizer has already promoted"
                if rec.path.name == DECISIONS_FILENAME
                else "a proposed decision belongs next to the question that needs it"
            )
            report.error(
                rel,
                field_line(rec, "state"),
                f"a '{PROPOSED_STATE}' decision filed here — {where}, and a "
                "track that discovers it needs a decision files the "
                f"`state: {PROPOSED_STATE}` block in its own "
                f"{QUESTIONS_FILENAME}, next to the question. Promoting it "
                "into decisions.md is an organizer act (the skill's "
                "promote-decision procedure) and is deliberately not something "
                "this script does — a script that promoted would be deciding "
                "who owns the decision list. Move the block into the track's "
                f"{QUESTIONS_FILENAME}, or promote it by hand and set "
                f"`state: {DECISION_STATES[0]}`.",
            )
        elif state not in DECISION_STATES and state != PROPOSED_STATE:
            report.error(
                rel,
                field_line(rec, "state"),
                f"decision state {state!r} is not one of "
                f"{' | '.join(DECISION_STATES)} — a decision is either taken "
                "or it is not. In particular there is no stored 'ready' or "
                "'blocked': readiness is derived from the blockers on every "
                "run, so a stored copy could only ever disagree with the "
                f"truth. (`{PROPOSED_STATE}` is a state, but only inside a "
                f"track's {QUESTIONS_FILENAME}.)",
            )

        check_decided_in(rec, state, tree, report)


def reference_key(rec: Record) -> str | None:
    """The field this record names decisions with, or None for other kinds.

    A question `blocks:`; an obligation is `blocking:`. Two spellings of one
    relation, so the mapping from kind to key lives here rather than being
    re-derived by every reader of it.
    """
    if rec.kind == "question":
        return "blocks"
    if rec.kind == "obligation":
        return "blocking"
    return None


def is_live_blocker(rec: Record) -> bool:
    """True while this record is still outstanding work against its decision.

    An open question or an open obligation is a **live** blocker. A closed one
    is history, and history against a decided decision has to stay clean —
    otherwise deciding anything would require rewriting the records that led to
    it. The same predicate is what `status` derives readiness from, so the gate
    and the report cannot disagree about which records are holding a decision
    up.
    """
    return declared(rec, "status") == "open"


@dataclass
class Blockers:
    """Every `blocks:`/`blocking:` reference in one tree, resolved.

    `decisions` maps `(project, bare id)` to the decision record; `by_decision`
    maps the same key to the records naming it, live or not; `dangling` carries
    the `(record, key, bare id)` references that resolve to no decision at all.
    """

    decisions: dict[tuple[str, str], Record] = field(default_factory=dict)
    by_decision: dict[tuple[str, str], list[Record]] = field(default_factory=dict)
    dangling: list[tuple[Record, str, str]] = field(default_factory=list)


def resolve_blockers(tree: Tree) -> Blockers:
    """Resolve every reference against the decisions the tree declares.

    References name decisions by **bare** id and resolve within the enclosing
    project. That is not a shortcut: cross-project destinations are forbidden
    (see `check_destination`) precisely so a project can see every inbound
    dependency in its own ledger, so a reference reaching into a sibling
    project would have no ledger to appear in and is dangling by construction.

    One implementation, two consumers: `validate_references` reports what does
    not resolve, and `status` derives readiness from what does. A second copy
    of "which open records block decision D" would be a second answer to the
    question this whole instrument exists to answer — and the two would drift,
    with the report being the one nobody thinks to distrust.
    """
    resolved = Blockers()
    for rec in tree.records:
        bare = declared(rec, "id") if rec.kind == "decision" else None
        if bare is not None:
            # First declaration wins; a second is already reported as a
            # duplicate id, and resolving references against it too would
            # print the same defect twice in different words.
            resolved.decisions.setdefault((rec.project, bare), rec)

    for rec in tree.records:
        key = reference_key(rec)
        if key is None:
            continue
        value = rec.fields.get(key)
        if value is None or value.is_none:
            continue
        for bare in value.items:
            target = (rec.project, bare)
            if target not in resolved.decisions:
                resolved.dangling.append((rec, key, bare))
                continue
            blockers = resolved.by_decision.setdefault(target, [])
            # `blocks: stop-semantics, stop-semantics` names one decision
            # twice. That is one blocker to a reader, so it is one here: the
            # report would otherwise print the line twice and count it twice
            # in "by 2 questions".
            if not any(r is rec for r in blockers):
                blockers.append(rec)
    return resolved


def validate_references(tree: Tree, report: Report) -> None:
    """Every `blocks:` and `blocking:` reference, checked once resolved.

    Resolution is `resolve_blockers`', shared with `status` — see there for why
    the two must not each carry their own.
    """
    resolved = resolve_blockers(tree)

    for rec, key, bare in resolved.dangling:
        report.error(
            rec.rel,
            field_line(rec, key),
            f"{key} names decision '{bare}', which does not exist in "
            f"project '{rec.project}' — naming a decision does not "
            "create one, so this record reads as gating something "
            "while gating nothing, and no convergence report can see "
            "the gap. References resolve within the enclosing project "
            "(a decision of the same name in a sibling project is not "
            "visible here, by design). File the decision in "
            f"{rec.project}/{DECISIONS_FILENAME}, or file a "
            f"`state: {PROPOSED_STATE}` block in this track's "
            f"{QUESTIONS_FILENAME} and let the organizer promote it.",
        )

    for target, blockers in resolved.by_decision.items():
        decision = resolved.decisions[target]
        if declared(decision, "state") != "decided":
            continue
        bare = target[1]
        for rec in blockers:
            if not is_live_blocker(rec):
                continue
            key = reference_key(rec) or ""
            report.error(
                rec.rel,
                field_line(rec, key),
                f"this {rec.kind} is open and {key} decision '{bare}', which "
                f"is already decided at {decision.rel}:{decision.line} — a "
                "decided decision must have zero open blockers, so either this "
                "record is stale or the decision was taken too early. Close "
                "the record (answer or retire the question; discharge the "
                "obligation), or reopen the decision explicitly: "
                f"{REOPEN_SHAPE}. Reopening is the honest move and the "
                "records say so afterwards; silently filing new work against "
                "a decided decision is how a decision stops meaning anything.",
            )

    for (_, bare), rec in sorted(
        resolved.decisions.items(), key=lambda kv: (kv[1].rel, kv[1].line)
    ):
        if (rec.project, bare) in resolved.by_decision:
            continue
        report.warn(
            rec.rel,
            rec.line,
            f"decision '{rec.qualified_id or bare}' is referenced by nothing — "
            "no question `blocks:` it and no obligation is `blocking:` it, so "
            "the convergence report can only ever show it as ready, whatever "
            "is actually outstanding. Either it is already settled and belongs "
            "in the record of decisions taken, or the questions that gate it "
            "have not been wired to it yet. A warning, not an error: a "
            "decision with no blockers left is a normal end state.",
        )


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
# Convergence: the `status` report
# --------------------------------------------------------------------------
#
# "What still blocks building?" — the question the maintainer was really asking
# on day four, answered by derivation rather than by anybody's recollection.
# Nothing here is stored: readiness is computed from the blockers on every run
# (`KNOWN_FIELDS` leaves no key to hand-edit a stale copy into), and the counts
# come from the same walk the ledger and the validator use.
#
# `status` is a **report**. It never writes, and it exits 0 even when every
# decision is blocked — only `validate` gates. A report that could fail is a
# report people stop running.

# The three derived labels, plus the one stored state that is neither. Widths
# are computed from whichever of these a project actually uses, so a report
# with no proposed decisions is not padded for the word.
LABEL_DECIDED = "DECIDED"
LABEL_READY = "READY"
LABEL_BLOCKED = "BLOCKED"
LABEL_PROPOSED = "PROPOSED"

# Warn above a third. Fixed, and deliberately not configurable: a threshold
# with a knob is a threshold that gets turned up the first time it fires.
BLOCKING_SHARE_DENOMINATOR = 3


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def has_blocking(rec: Record) -> bool:
    """True for an obligation whose `blocking:` names at least one decision.

    A written-but-empty `blocking:` is a `validate` error, not a blocker: it
    gates nothing, so counting it here would inflate the scarcity ratio with
    records that hold nothing up.
    """
    value = rec.fields.get("blocking")
    return value is not None and not value.is_none and bool(value.items)


def derive_counts(
    tree: Tree, project: Project
) -> tuple[list[tuple[str, Counts]], Counts]:
    """One project's per-track counts, and their total.

    Track-scoped records only, and the total is exactly the sum of the tracks
    printed above it — a total that silently included records filed outside any
    track would be a number the breakdown cannot explain, which is the same
    hiding place the per-track lines exist to close. Such a record is a
    `validate` error in its own right; this report does not restate it as an
    unexplained discrepancy.

    Card tallies come from `tree.cards`, filled by the one walk that counts
    them, so the ledger, the validator and this report cannot disagree about
    how many stubs a track has.
    """
    fields = (
        "answered",
        "open_questions",
        "retired",
        "discharged",
        "open_obligations",
        "blocking",
    )
    tallies = {t.name: dict.fromkeys(fields, 0) for t in project.tracks}
    for rec in tree.records:
        if rec.project != project.name or rec.track not in tallies:
            continue
        tally = tallies[rec.track]
        # A record with no status, or one outside its enum, is counted in no
        # column — the same treatment `validate`'s own message promises.
        status = declared(rec, "status")
        if rec.kind == "question":
            if status == "answered":
                tally["answered"] += 1
            elif status == "open":
                tally["open_questions"] += 1
            elif status == "retired":
                tally["retired"] += 1
        elif rec.kind == "obligation":
            if status == "discharged":
                tally["discharged"] += 1
            elif status == "open":
                tally["open_obligations"] += 1
                if has_blocking(rec):
                    tally["blocking"] += 1

    tracks: list[tuple[str, Counts]] = []
    for track in project.tracks:
        cards = tree.cards.get(f"{project.name}/{track.name}", CardCounts())
        tracks.append(
            (
                track.name,
                Counts(
                    **tallies[track.name],
                    stubs=cards.stubs,
                    external=cards.receipts,
                ),
            )
        )
    total = Counts(
        **{f: sum(getattr(c, f) for _, c in tracks) for f in fields},
        stubs=sum(c.stubs for _, c in tracks),
        external=sum(c.external for _, c in tracks),
    )
    return tracks, total


@dataclass(frozen=True)
class DecisionStatus:
    """One decision's derived line: what to call it, and why."""

    record: Record
    name: str
    label: str
    note: str
    blockers: tuple[Record, ...] = ()


def decision_status(rec: Record, blockers: list[Record]) -> DecisionStatus:
    """Derive one decision's status from the records naming it.

    Ready is **not** done: a decision with nothing outstanding against it is
    waiting on a human, and the project gate is all required decisions
    `decided`. So the three labels stay distinct, and `proposed` is reported as
    the fourth thing it is — a decision the organizer has not promoted yet, not
    a decision that is nearly taken.
    """
    name = declared(rec, "id") or "-"
    state = declared(rec, "state")
    if state == PROPOSED_STATE:
        where = f"{TRACKS_DIRNAME}/{rec.track}" if rec.track is not None else rec.rel
        return DecisionStatus(
            rec, name, LABEL_PROPOSED, f"awaiting promotion (filed in {where})"
        )
    if state == "decided":
        evidence = declared(rec, "decided_in")
        note = f"decided in {evidence}" if evidence else "decided"
        return DecisionStatus(rec, name, LABEL_DECIDED, note)

    live = tuple(r for r in blockers if is_live_blocker(r))
    if live:
        counts = [
            plural(sum(1 for r in live if r.kind == "question"), "question"),
            plural(sum(1 for r in live if r.kind == "obligation"), "obligation"),
        ]
        note = "by " + ", ".join(c for c in counts if not c.startswith("0 "))
        return DecisionStatus(rec, name, LABEL_BLOCKED, note, live)

    # Rule 4's last clause. Retirement is legitimate scope reduction, but a
    # decision that came free because a question left the board is a different
    # fact from one whose questions were answered, and a reader deciding on
    # this line deserves to know which. Said whenever a retired question names
    # the decision and nothing live does — with no history, "the last blocker"
    # is exactly that state, and this report has no history by design.
    retired = [r for r in blockers if declared(r, "status") == "retired"]
    if retired:
        ids = ", ".join(r.qualified_id or declared(r, "id") or "-" for r in retired)
        return DecisionStatus(
            rec,
            name,
            LABEL_READY,
            f"awaiting decision (unblocked by retirement: {ids})",
        )
    return DecisionStatus(rec, name, LABEL_READY, "awaiting decision")


def decision_statuses(
    tree: Tree, project: Project, resolved: Blockers
) -> list[DecisionStatus]:
    """Every decision in one project, in tree order, with its derived status."""
    statuses: list[DecisionStatus] = []
    seen: set[str] = set()
    for rec in tree.records:
        if rec.kind != "decision" or rec.project != project.name:
            continue
        bare = declared(rec, "id")
        # A decision with no id can be named by nothing and appears in no
        # report — `validate` says so; this one has no line to print for it.
        # A duplicate is reported there too, and references resolve to the
        # first, so the report follows the resolution rather than printing one
        # name twice.
        if bare is None or bare in seen:
            continue
        seen.add(bare)
        statuses.append(
            decision_status(rec, resolved.by_decision.get((project.name, bare), []))
        )
    return statuses


def blocker_row(rec: Record) -> tuple[str, str, str, str]:
    """One blocker line's four columns: kind letter, id, status, and path.

    The path is what makes the report actionable — a blocker a reader cannot
    open is a name, not a next step. An obligation shows its `destination:`,
    the place the deferred work is addressed to; a question shows the file its
    section lives in, which is where the answer gets written.
    """
    letter = "Q" if rec.kind == "question" else "O"
    ident = rec.qualified_id or declared(rec, "id") or "-"
    status = declared(rec, "status") or "-"
    where = rec.rel
    if rec.kind == "obligation":
        where = declared(rec, "destination") or rec.rel
    return letter, ident, status, where


def render_decision_block(statuses: list[DecisionStatus]) -> list[str]:
    """The per-decision lines, and the blocker lines under the blocked ones.

    Column widths are computed across the whole project, so one long id shifts
    the table rather than shearing it.
    """
    name_width = max(len(s.name) for s in statuses)
    label_width = max(len(s.label) for s in statuses)
    rows = [blocker_row(r) for s in statuses for r in s.blockers]
    ident_width = max((len(r[1]) for r in rows), default=0)
    status_width = max((len(r[2]) for r in rows), default=0)

    lines: list[str] = []
    for status in statuses:
        lines.append(
            f"  {status.name:<{name_width}}  "
            f"{status.label:<{label_width}} {status.note}".rstrip()
        )
        for blocker in status.blockers:
            letter, ident, state, where = blocker_row(blocker)
            lines.append(
                f"    {letter}: {ident:<{ident_width}}  "
                f"{state:<{status_width}}  → {where}"
            )
    return lines


def render_counts_parenthetical(counts: Counts) -> str:
    """The open-obligation subtotals, zeroes omitted.

    Stubs and receipts mean opposite things — work with nowhere to go yet
    versus work that went somewhere this validator cannot see — so they are
    never folded together, and a count that is zero says nothing worth the
    reader's eye.
    """
    parts = []
    if counts.blocking:
        parts.append(f"{counts.blocking} blocking")
    if counts.stubs:
        parts.append(plural(counts.stubs, "stub"))
    if counts.external:
        parts.append(f"{counts.external} external")
    return f" ({', '.join(parts)})" if parts else ""


def render_track_lines(tracks: list[tuple[str, Counts]], total: Counts) -> list[str]:
    """The question pair and the obligation pair, per track and then total.

    Totals never print without the per-track breakdown, including for a
    single-track project: big projects are exactly where one sick track hides
    inside healthy totals, and a report that drops the breakdown when it looks
    redundant is a report that drops it exactly when nobody is watching. A
    snapshot, deliberately — this script has no history, so it prints no trend,
    delta or arrow.
    """
    rows = [*tracks, ("total", total)]
    label_width = max(len(name) + 1 for name, _ in rows)
    widths = {
        f: max(len(str(getattr(c, f))) for _, c in rows)
        for f in (
            "answered",
            "open_questions",
            "retired",
            "discharged",
            "open_obligations",
        )
    }
    lines = []
    for name, counts in rows:
        lines.append(
            f"  {name + ':':<{label_width}}  "
            f"Q {counts.answered:>{widths['answered']}} answered / "
            f"{counts.open_questions:>{widths['open_questions']}} open / "
            f"{counts.retired:>{widths['retired']}} retired    "
            f"O {counts.discharged:>{widths['discharged']}} discharged / "
            f"{counts.open_obligations:>{widths['open_obligations']}} open"
            f"{render_counts_parenthetical(counts)}"
        )
    return lines


def scarcity_warning(total: Counts) -> str | None:
    """The blocking-obligation scarcity check — a warning, never a failure.

    `blocking:` is meant to be rare. Past a third of the open obligations the
    flag has stopped distinguishing anything: if everything blocks, nothing
    converges, and the report's blocked list becomes the backlog rather than
    the gate. Reported so somebody looks, not enforced — deciding which
    obligations really gate a decision is judgment, and a gate here would be
    answered by deleting the flag rather than by thinking.
    """
    if total.open_obligations == 0:
        return None
    if total.blocking * BLOCKING_SHARE_DENOMINATOR <= total.open_obligations:
        return None
    return (
        f"  ⚠ {total.blocking} of {total.open_obligations} open obligations "
        "carry `blocking:` — more than a third. If everything blocks, nothing "
        "converges and the flag has become emphasis: keep it for the "
        "obligations that genuinely gate a decision."
    )


def print_project_status(tree: Tree, project: Project, resolved: Blockers) -> None:
    statuses = decision_statuses(tree, project, resolved)
    tallied = {label: 0 for label in (LABEL_DECIDED, LABEL_READY, LABEL_BLOCKED)}
    proposed = 0
    for status in statuses:
        if status.label == LABEL_PROPOSED:
            proposed += 1
        else:
            tallied[status.label] += 1
    # The three counts stay separate: "ready" is not "done", and the project
    # gate is all required decisions **decided**. Proposed decisions are
    # counted too, and apart — they are not yet on the organizer's list, and a
    # header that omitted them would hide a decision the tree already knows it
    # needs.
    header = ", ".join(
        f"{tallied[label]} {label.lower()}"
        for label in (LABEL_DECIDED, LABEL_READY, LABEL_BLOCKED)
    )
    if proposed:
        header += f", {proposed} proposed"
    print(f"{project.name} — decisions: {header if statuses else 'none filed'}")
    print()
    if statuses:
        for line in render_decision_block(statuses):
            print(line)
        print()

    tracks, total = derive_counts(tree, project)
    if not tracks:
        print("  no tracks yet")
        return
    for line in render_track_lines(tracks, total):
        print(line)
    warning = scarcity_warning(total)
    if warning is not None:
        print()
        print(warning)


def verb_status(args: argparse.Namespace, root: Path) -> int:
    """The convergence report: every project, or the one named.

    Discovery's own findings are `validate`'s, not this report's — a status run
    that printed parse errors would be a second, weaker gate. `validate_cards`
    is called for the same reason it exists at all: it is where cards are
    counted, and counting them a second time here is how the ledger and the
    report start to disagree.
    """
    tree = discover(root, Report())
    known = [p.name for p in tree.projects]
    if args.project is not None and args.project not in known:
        raise UsageError(
            f"unknown project '{args.project}' — known projects: "
            f"{', '.join(known) if known else 'none'}. `status` with no "
            "project argument reports every one."
        )
    validate_cards(tree, Report())
    resolved = resolve_blockers(tree)
    printed = False
    for project in tree.projects:
        if args.project is not None and project.name != args.project:
            continue
        if printed:
            print()
        print_project_status(tree, project, resolved)
        printed = True
    return EXIT_OK


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
        "That `destination:` has to exist **before** the block does — write the",
        "stub card in `obligations/` first, then point at it. A path that is not",
        "there yet is exactly how deferred work goes dark.",
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
    # A blank line before the entry only when the entry starts the list: the
    # fresh render emits consecutive entries, and a grown index that inserted a
    # blank between them would be a loose list a fresh `init` never writes.
    sep = [] if body and body[-1].startswith("- [") else [""]
    return lines[:start] + body + sep + [entry, ""] + lines[end:]


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
    also where a record the rules cannot place is still named, so nothing
    the parser saw is invisible.
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
    # One id namespace, shared by every kind that declares an id: a qualified
    # id that could mean two records is ambiguous whatever their kinds, and
    # `blocks:`/`blocking:` resolve by bare id.
    seen: dict[str, tuple[str, int]] = {}
    validate_decisions(tree, seen, report)
    validate_obligations(tree, seen, report)
    validate_questions(tree, seen, report)
    validate_contracts(tree, report)
    validate_cards(tree, report)
    # Last, and over the whole tree: a reference is only resolvable once every
    # record that could satisfy it has been discovered.
    validate_references(tree, report)
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


def find_deferral_phrase(line: str) -> str | None:
    """The first `SUGGEST_PHRASES` (or `once … lands`) hit on this line, or
    None. One hit per line, in list order: a sentence naming two phrases at
    once is one finding to a human reader, not two, and doubling the entry
    would be noise rather than signal.
    """
    lower = line.lower()
    for phrase in SUGGEST_PHRASES:
        if phrase in lower:
            return phrase
    m = SUGGEST_ONCE_LANDS_RE.search(line)
    return m.group(0) if m else None


def verb_suggest(args: argparse.Namespace, root: Path) -> int:
    """The advisory lexical scan (design §"What is deliberately not in the
    gate"). Measured, not assumed: run against the reference tree this
    returned 29 hits, most of them prose describing behaviour rather than
    deferring work — so this is a report, never a gate, and there is no code
    path below that returns non-zero, including a directory this run cannot
    even list (`PermissionError` out of `discover`'s own `iterdir()` calls —
    `rglob`/`is_file` already swallow that themselves on the Python versions
    this runs on, but `discover` does not, so the whole body below is wrapped
    in one `try`). A file this run cannot read is reported and skipped; a file
    `scan_blocks` cannot fully close (an unterminated fence or HTML comment)
    is reported by name — the spec's "report it and continue" — rather than
    silently scanning nothing after it, which would be indistinguishable from
    a clean file on exactly the file most likely to be hiding something.
    `discover`'s own parse findings (unknown keys, bad ids, and the like) are
    `validate`'s business, not this scan's, and are never surfaced here.

    Suppression matters more than the phrase list (per the design), and the
    interpretation of "immediately adjacent" is a judgment call, stated
    plainly here rather than left implicit. A hit is suppressed when:

      - it falls inside a `### Q<n>.` section of a `questions.md` whose
        section already carries an `obligation` block — a real one or a bare
        `none:` — anywhere in that section. The section is the coverage
        rule's own unit, so a hit already declared over there is exactly the
        "done correctly" case the design says would otherwise dominate the
        output; or
      - it falls anywhere in a file under `tracks/<track>/contracts/` that
        carries at least one `obligation` block anywhere in the file — the
        contracts coverage rule is file-scoped, not section-scoped (see
        `validate_contracts`), so suppression follows the same scope.

    Deliberately **not** suppressed: prose sitting above a covered heading.
    Reading "the next question is covered" backward onto the paragraph before
    it would be an inference this scan has no basis for — the section is the
    unit the coverage rule declares over, and text outside its line range
    made no such declaration. An advisory tool with false positives has to
    err toward reporting, not toward inventing scope a human never granted it.

    A hit inside a backtick-fenced code block or an HTML comment is
    suppressed too: both are inert to `validate` for the same reason (a
    documentation sample is not a record), and a phrase inside a worked
    example describing the convention is not a deferral either — reporting
    it would just be noise next to `init`'s own scaffolded example. (Only
    backtick fences: `scan_blocks` recognizes no other fence syntax, and
    `suggest` must not disagree with `validate` about what counts as inert.)
    """
    try:
        tree = discover(root, Report())  # discover's findings are validate's, not ours
        if not tree.present:
            return EXIT_OK

        records_by_rel = records_by_file(tree)
        research_dir = root.joinpath(*RESEARCH_SUBPATH)
        for path in sorted(p for p in research_dir.rglob("*.md") if p.is_file()):
            rel = path.relative_to(root).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                print(f"{rel}: cannot read — {e}")
                continue

            lines = text.splitlines()
            blocks = scan_blocks(lines)
            inert = blocks.inert_lines()
            file_records = records_by_rel.get(rel, [])

            if blocks.unterminated_fence is not None:
                print(
                    f"{rel}:{blocks.unterminated_fence.open_index + 1}: "
                    "unterminated fence — lines after it are not scanned"
                )
            unterminated_comment = next(
                (c for c in blocks.comments if not c.closed), None
            )
            if unterminated_comment is not None:
                print(
                    f"{rel}:{unterminated_comment.open_index + 1}: "
                    "unterminated HTML comment — lines after it are not scanned"
                )

            covered_ranges: list[tuple[int, int]] = []
            contract_covered = False
            if path.name == QUESTIONS_FILENAME:
                for section in tree.sections.get(rel, []):
                    if any(
                        r.kind == "obligation"
                        and section.start_line <= r.line <= section.end_line
                        for r in file_records
                    ):
                        covered_ranges.append((section.start_line, section.end_line))
            elif in_contracts_dir(tree, path):
                contract_covered = any(r.kind == "obligation" for r in file_records)

            for index, line in enumerate(lines):
                if index in inert or contract_covered:
                    continue
                lineno = index + 1
                if any(start <= lineno <= end for start, end in covered_ranges):
                    continue
                phrase = find_deferral_phrase(line)
                if phrase is None:
                    continue
                print(f"{rel}:{lineno}: {phrase!r} — {line.strip()}")
    except OSError as e:
        # The escape the exit-0 guarantee has to close: a directory this
        # process cannot even list (chmod 000 on `dev_docs/research` or a
        # `tracks/` dir) raises out of `discover`'s `iterdir()` calls, not out
        # of anything caught above — an uncaught traceback there would be
        # exit 1, indistinguishable from validate's own tree-content failures.
        print(f"cannot scan — {e}")
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
    p_status.add_argument(
        "project",
        nargs="?",
        help="Project to report on. Omitted, every project is reported.",
    )
    p_status.set_defaults(run=verb_status)

    p_suggest = subparsers.add_parser(
        "suggest",
        help=(
            "Advisory lexical scan for unregistered deferral prose; always "
            "exits 0 — a false positive matched against English prose has "
            "nowhere legal to go, so this is a report, never a gate."
        ),
    )
    p_suggest.set_defaults(run=verb_suggest)

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
