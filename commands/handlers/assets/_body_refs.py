"""Shared dependency-phrase body parser for `/reoptimize-tasks` Dimension 1-2.

Both `gh-issue-graph.py` and `linear-relations.py` need the same answer to
"what dependency does this issue's body/description claim, on which other
issue, and how strongly": a fixed phrase -> direction/strength table that used
to be hand-walked identically in `gh-issue-reoptimize.md` and
`linear-reoptimize.md`, with the warning that `unblocks` reverses direction
and "a mistake here writes a real dependency backwards and nothing catches
it." This module is that one table, tested once instead of trusted twice.

Direction is always relative to the body being parsed (`self_id`):
  - `blocked_by` — the body's issue is blocked by the referenced one
    (`blocked on/by`, `relies on`, `depends on`, `requires`, `with X in
    place`).
  - `blocks`     — the body's issue blocks the referenced one. Only
    `unblocks` produces this; every other strong phrase points the other
    way, so getting `unblocks` backwards silently inverts a real dependency.
  - `related`    — a weak, non-blocking cross-reference (`re-scoped per`,
    `part of … plan`, or a bare mention with no phrase at all).

Strength is `strong` for every `blocked_by`/`blocks` phrase above and `weak`
for every `related` one — callers use it to decide whether a finding
proposes a real edge or only a cross-reference footer line.

**`id_pattern` contract.** A compiled regex whose every match carries exactly
one non-`None` capturing group, holding the reference's canonical identifier
— already stripped of any markup or qualifier the caller doesn't want
resolved locally (e.g. `gh-issue-graph.py`'s pattern excludes a repo-qualified
`owner/repo#<n>` from matching at all, since a cross-repo number is not a
local issue). `self_id` must be a string comparable to that same identifier
(case-insensitively) — a match whose identifier equals `self_id` is dropped:
a body restating its own id is never a self-reference.

**Code spans are excluded.** A mention inside inline `` `code` `` or a fenced
``` ``` ``` block is masked out before matching, on the theory that an id
inside a code sample is being quoted or illustrated, not asserted as a real
dependency — pinned by `scripts/test_body_refs.py`.
"""

import re

# Fenced blocks first: a fence's own body may itself contain single
# backticks (e.g. inline code inside a code sample), and matching spans
# first would end a fence early at the first inner backtick.
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")

# How much context around a candidate id is read to classify the phrase.
# Before: long enough for the longest phrase ("re-scoped per", "blocked by")
# plus a few words of intervening text. After: only needed for the two
# phrases whose id sits BETWEEN two words ("with X in place", "part of … X
# plan").
_BEFORE_WINDOW = 60
_AFTER_WINDOW = 40


def _mask_code(text):
    """Blank out fenced and inline code spans, preserving offsets.

    Replacing with equal-length whitespace (rather than deleting) keeps every
    surviving match's position identical to the original text, so window
    slicing below never has to re-derive an offset.
    """

    def blank(match):
        return " " * len(match.group(0))

    text = _FENCED_CODE_RE.sub(blank, text)
    text = _CODE_SPAN_RE.sub(blank, text)
    return text


def _extract_id(match):
    """The one non-`None` capturing group in an `id_pattern` match.

    Works whether the pattern has a single group (gh-issue's bare `#<n>`) or
    two differently-named groups feeding one alternation (linear's `<issue
    id="…">` tag vs. a bare `PRE-NNN`) — exactly one of them is populated by
    construction, per the module's `id_pattern` contract.
    """
    for group in match.groups():
        if group is not None:
            return group
    return match.group(0)


def _classify(before, after):
    """`(phrase, direction, strength)` for the text immediately around one id.

    Checked in order, most specific first. `unblocks` is checked before
    `blocked on/by` etc. even though none of those substrings overlap, so the
    reversal it causes is never shadowed by a rule below it.
    """
    if re.search(r"\bunblocks\b", before, re.I):
        return "unblocks", "blocks", "strong"
    if re.search(r"\bblocked\s+(?:on|by)\b", before, re.I):
        return "blocked on/by", "blocked_by", "strong"
    if re.search(r"\brelies\s+on\b", before, re.I):
        return "relies on", "blocked_by", "strong"
    if re.search(r"\bdepends\s+on\b", before, re.I):
        return "depends on", "blocked_by", "strong"
    if re.search(r"\brequires\b", before, re.I):
        return "requires", "blocked_by", "strong"
    if re.search(r"\bwith\b", before, re.I) and re.search(r"\bin place\b", after, re.I):
        return "with X in place", "blocked_by", "strong"
    if re.search(r"\bre-scoped\s+per\b", before, re.I):
        return "re-scoped per", "related", "weak"
    if re.search(r"\bpart of\b", before, re.I) and re.search(r"\bplan\b", after, re.I):
        return "part of … plan", "related", "weak"
    return "mention", "related", "weak"


def parse(body, self_id, id_pattern):
    """Every dependency-phrase reference to another issue in `body`.

    Returns a list of `{"target": <id>, "phrase": <name>, "direction":
    "blocked_by"|"blocks"|"related", "strength": "strong"|"weak"}` dicts, one
    per matched reference — duplicates included, since the same target may be
    named more than once with different phrasing and each mention is its own
    finding. Excludes any reference whose id equals `self_id`
    (case-insensitively) and any reference that falls entirely inside a code
    span.
    """
    masked = _mask_code(body)
    self_norm = str(self_id).casefold()
    # Matched up front, including self-mentions, so a neighboring reference's
    # position bounds this one's context window even when the neighbor is
    # excluded from the output below.
    matches = list(id_pattern.finditer(masked))
    references = []
    for i, match in enumerate(matches):
        target = _extract_id(match)
        if target.casefold() == self_norm:
            continue
        # Windows are clipped at the nearest OTHER reference, never just at a
        # fixed character count: "Blocked by #2; see #3 for context." would
        # otherwise let "Blocked by" — fully outside #3's own clause — fall
        # inside a naive 60-char window and misclassify #3 as blocked_by too.
        prev_end = matches[i - 1].end() if i > 0 else 0
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(masked)
        before_start = max(prev_end, match.start() - _BEFORE_WINDOW)
        after_end = min(next_start, match.end() + _AFTER_WINDOW)
        before = masked[before_start : match.start()]
        after = masked[match.end() : after_end]
        phrase, direction, strength = _classify(before, after)
        references.append(
            {
                "target": target,
                "phrase": phrase,
                "direction": direction,
                "strength": strength,
            }
        )
    return references
