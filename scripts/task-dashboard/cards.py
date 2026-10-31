"""The one card every tab draws, so a change to how a card looks lands on all.

A tab builds a `Card` from its own rows and calls `render()`. The layout is
fixed here: the next act as a chip, then the title and its muted ref behind
an icon for what the item is (an open PR, a draft, an issue, a session), then
one meta line led by the item's bare age (`130d`), its state and its CI as
GitHub's check, cross or dot. Now adds its ranking as an enrichment (`Score`): a number
beside a "?" that pops over the breakdown. Nothing else differs by tab, so a
tab cannot drift back into a card of its own (test_task_dashboard_cards.py
checks every tab).

Every field holds HTML a caller has already escaped, except `title`, `url`,
`ref`, `Age.tip`, `Score.total`, and the values of `Card.data` and
`Score.data`, which are plain text and escaped here.
"""

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import icons
import overlay
import view_model

_e = html.escape

CSS = """
.card { display:flow-root; position:relative; }
/* A lane's run of cards reads as one bordered list, as GitHub's do: square
   rows that share their borders, rounded only at the run's two ends. */
.lane > .card { margin:0; border-radius:0; background:var(--bg); }
.lane > .card + .card { border-top:0; }
.lane > .card:first-child, .lane > :not(.card) + .card { border-top-left-radius:6px;
  border-top-right-radius:6px; }
.lane > .card:last-child, .lane > .card:has(+ :not(.card)) {
  border-bottom-left-radius:6px; border-bottom-right-radius:6px; }
.lane > .card:has(+ :not(.card)) { margin-bottom:8px; }
.lane > .card:hover { background:var(--card); }
.card .title { color:var(--fg); font-size:15px; font-weight:600; }
.card a.title:hover { color:var(--accent); text-decoration:underline; }
/* The kind or status icon sits in a gutter left of the title. */
.card:has(.lead) { padding-left:34px; }
.card .lead { position:absolute; left:11px; top:9px; line-height:0; }
.card .lead .glyph { width:16px; height:16px; }
.card .lead .icon { width:auto; margin:0; }
.kind.k-open { color:var(--ok); }
.kind.k-draft, .kind.k-muted { color:var(--muted); }
.kind.k-session, .kind.k-ticket { color:var(--accent); }
.kind.k-design, .kind.k-epic { color:var(--done); }
.card > .act { float:right; margin:0 0 4px 12px; padding:1px 8px; border-radius:10px;
  border:1px solid var(--line); background:var(--bg); font-size:12px; font-weight:600; }
.card .act code { white-space:pre-wrap; word-break:break-all; }
@media (max-width:600px) {
  .card > .act { float:none; width:max-content; max-width:100%; margin:0 0 4px; }
}
details.card > summary { cursor:pointer; list-style-position:inside; }
.card .ref { color:var(--muted); font-size:12px; font-weight:400; }
.card .meta { color:var(--muted); font-size:12px; }
.card .age { color:var(--fg); font-weight:600; }
.ci { display:inline-flex; vertical-align:-0.2em; color:var(--muted); }
.ci .glyph { width:14px; height:14px; }
.ci.ci-ok { color:var(--ok); }
.ci.ci-bad { color:var(--bad); }
.ci.ci-pending { color:var(--warn); }
.card .pin { color:var(--accent); font-weight:600; }
.card .new { font-size:11px; font-weight:600; color:var(--accent);
  border:1px solid var(--accent); border-radius:999px; padding:0 6px; }
.card .stag { font-size:11px; color:var(--muted); border:1px solid var(--line);
  border-radius:999px; padding:0 6px; }
.card .label { font-size:11px; font-weight:500; color:var(--fg); border:1px solid var(--line);
  border-radius:999px; padding:0 7px; background:var(--card); }
.card .ask { margin:4px 0; color:var(--fg); overflow-wrap:anywhere; }
.card .brief { white-space:pre-line; margin:6px 0; }
.card .checklist { margin:4px 0; font-size:12px; color:var(--fg); }
.card .folded { margin:2px 0; font-size:12px; color:var(--muted); }
/* The score and "?" sit on the meta line's baseline, the icon sized to its text. */
.card .why-more { position:relative; display:inline-block; cursor:help; }
.card .why-more .glyph { display:inline-block; vertical-align:-0.15em; width:1.1em; height:1.1em; }
.card .why-more .score { margin-right:3px; font-variant-numeric:tabular-nums; }
.card .why-more:hover, .card .why-more:focus { color:var(--fg); outline:none; }
.card .why-more .why { display:none; position:absolute; z-index:5; top:calc(100% + 4px); left:-8px;
  width:max-content; max-width:min(420px, 80vw); margin:0; padding:6px 8px; border:1px solid var(--line);
  border-radius:6px; background:var(--card); color:var(--fg); box-shadow:0 2px 8px rgba(0,0,0,.25); }
.card .why-more:hover .why, .card .why-more:focus .why { display:block; }
@media (max-width:600px) {
  .card .why-more { position:static; }
  .card .why-more .why { top:auto; left:8px; right:8px; width:auto; max-width:none; margin-top:4px; }
}
"""


def age_text(since: "datetime | None", now: datetime) -> str:
    """How long ago `since` was, bare: `130d`, `3h`, `12m`."""
    if since is None:
        return "age unknown"
    s = max(0.0, (now - since).total_seconds())
    if s < 60:
        return "under a minute"
    if s < 90 * 60:
        return f"{round(s / 60)}m"
    if s < 36 * 3600:
        return f"{round(s / 3600)}h"
    return f"{round(s / 86400)}d"


@dataclass
class Age:
    """The card's age: when it started, and what started it, for the tooltip
    ("waiting since", "opened", "last activity")."""

    since: "datetime | None"
    tip: str


# "CI <state>" in a state line: shown as GitHub's mark for it, a green check,
# a red cross or an amber dot, with the state as its name and tooltip. A
# state with no mark (unknown, none) is a muted dashed circle.
_CI = re.compile(r"\bCI (\w+)")
_CI_MARKS = {
    "success": ("check", "ci ci-ok"),
    "failure": ("x", "ci ci-bad"),
    "error": ("x", "ci ci-bad"),
    "pending": ("dot-fill", "ci ci-pending"),
}


def ci(state: "str | None") -> str:
    """A CI state as the card shows it (_CI)."""
    word = _e(f"CI {state or 'unknown'}")
    glyph, cls = _CI_MARKS.get(state or "", ("circle-dashed", "ci"))
    return (
        f'<span class="{cls}" role="img" aria-label="{word}" title="{word}">'
        f"{icons.svg(glyph)}</span>"
    )


# A card's kind -> its leading icon, the icon's colour class, and its name.
# A kind not listed here draws no icon.
KINDS = {
    "pr": ("git-pull-request", "k-open", "Open pull request"),
    "pr-draft": ("git-pull-request-draft", "k-draft", "Draft pull request"),
    "issue": ("circle-dot", "k-open", "Open issue"),
    "drain": ("circle-dot", "k-open", "Drain item"),
    "session": ("terminal", "k-session", "Session"),
    "ticket": ("circle-dot", "k-ticket", "Jira ticket"),
    "design": ("pen-tool", "k-design", "Design"),
    "worktree": ("git-branch", "k-muted", "Worktree"),
    "handoff": ("file-text", "k-muted", "Handoff"),
    "epic": ("layers", "k-epic", "Epic"),
}


def label(name: str) -> str:
    """A GitHub label (`prio:2`) as a pill, as GitHub draws one."""
    return f'<span class="label">{_e(name)}</span>'


def kind_icon(kind: str) -> str:
    """The leading icon for a card of `kind` (KINDS), or "" for none."""
    if kind not in KINDS:
        return ""
    glyph, cls, name = KINDS[kind]
    return (
        f'<span class="kind {cls}" role="img" aria-label="{name}" title="{name}">'
        f"{icons.svg(glyph)}</span>"
    )


def ci_words(text: str) -> str:
    """Escaped `text`, each "CI <state>" in it as ci() draws it."""
    return _CI.sub(lambda m: ci(m.group(1)), _e(text))


def title(url: "str | None", text: str) -> str:
    """A card's title, as a `.title` the page's filter box searches: a link
    when `url` is one that leaves the dashboard."""
    if url and url.startswith("https://"):
        return view_model.link(url, text, "title")
    return f'<span class="title">{_e(text)}</span>'


def title_ref(url: "str | None", text: "str | None", ref: str) -> str:
    """The title, then the muted ref; the ref alone, as the title, when
    untitled."""
    head = title(url, text or ref)
    return head + f' <span class="ref">{_e(ref)}</span>' if text else head


def total(value: float) -> str:
    """A score as the card shows it beside its "?": `2.5`, `−0.4`."""
    return f"{value:g}".replace("-", "−")


@dataclass
class Score:
    """Why a card ranks where it does, behind a "?" at the end of its meta
    line: `why` is the breakdown (HTML), `total` the number beside the "?"
    ("" where the ranking is a rule, not a score)."""

    why: str
    total: str = ""
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Card:
    item: "overlay.Item | None"  # the overlay identity; None for no controls
    title: str
    url: "str | None" = None
    ref: "str | None" = None
    act: str = ""  # the next act, after "→ "
    kind: str = ""  # what it is (KINDS): picks the leading icon
    icon: str = ""  # the leading icon in place of the kind's: a Jira status
    tag: str = ""  # after the title: a bot badge, a session's tag
    age: "Age | None" = None
    flags: list[str] = field(default_factory=list)  # before the age: "new"
    meta: list[str] = field(default_factory=list)  # after the age
    score: "Score | None" = None
    above: list[str] = field(default_factory=list)  # between title and meta
    below: list[str] = field(default_factory=list)  # after the meta line
    controls: str = ""  # overlay.card_controls, or a tab's own buttons
    tail: str = ""  # after the controls: Now's snooze-length picker
    cls: str = ""  # classes beside `card`
    data: dict[str, str] = field(default_factory=dict)
    folded: bool = False  # a <details> whose summary is the title and meta


def _age(a: Age, now: datetime) -> str:
    # The date is UTC's, so a page (and the Now golden) reads the same on
    # every host.
    day = a.since.astimezone(timezone.utc).date().isoformat() if a.since else ""
    tip = f"{a.tip} {day}" if day else a.tip
    return f'<span class="age" title="{_e(tip)}">{_e(age_text(a.since, now))}</span>'


def _score(c: Card) -> str:
    s = c.score
    assert s is not None
    # A screen reader hears the label, then the breakdown it describes: the
    # role makes the label count, and describedby reads the hidden
    # breakdown, which role=button alone would flatten away.
    key = f"{c.item[0]}\0{c.item[1]}" if c.item else c.title
    wid = "why-" + hashlib.sha1(key.encode()).hexdigest()[:10]
    data = "".join(f' data-{k}="{_e(v)}"' for k, v in s.data.items())
    num = f'<span class="score">{_e(s.total)}</span>' if s.total else ""
    label = f"Score {s.total}: why it ranks here" if s.total else "Why it ranks here"
    return (
        f' <span class="why-more" tabindex="0" aria-label="{_e(label)}"'
        f' role="button" aria-describedby="{wid}">{num}{icons.svg("circle-help")}'
        f'<div class="why"{data} id="{wid}" role="tooltip">{s.why}</div></span>'
    )


def _attrs(c: Card, ov: "overlay.Overlay | None", now: datetime) -> str:
    if c.item is not None and ov is not None:
        attrs = overlay.card_attrs(c.item, ov, now)
    elif c.item is not None:
        attrs = f'class="card" data-source="{_e(c.item[0])}" data-key="{_e(c.item[1])}"'
    else:
        attrs = 'class="card"'
    if c.cls:
        attrs = attrs.replace('class="card', f'class="card {c.cls}', 1)
    return attrs + "".join(f' data-{k}="{_e(v)}"' for k, v in c.data.items())


def render(c: Card, ov: "overlay.Overlay | None", now: datetime) -> str:
    """The card's HTML: act, title, meta line, the rest, then controls."""
    pinned = c.item is not None and ov is not None and ov.is_pinned(c.item)
    bits = (['<span class="pin">pinned</span>'] if pinned else []) + c.flags
    if c.age is not None:
        bits.append(_age(c.age, now))
    line = " · ".join(b for b in bits + c.meta if b)
    if c.score is not None:
        line += _score(c)
    lead = c.icon or kind_icon(c.kind)
    head = (
        (f'<span class="lead">{lead}</span>' if lead else "")
        + (
            title_ref(c.url, c.title, c.ref)
            if c.ref is not None
            else title(c.url, c.title)
        )
        + c.tag
    )
    act = f'<div class="act">→ {c.act}</div>' if c.act else ""
    rest = "".join(c.below) + c.controls + c.tail
    attrs = _attrs(c, ov, now)
    if c.folded:
        meta = f' <span class="meta">{line}</span>' if line else ""
        return (
            f"<details {attrs}><summary>{head}{meta}</summary>"
            f"{act}{''.join(c.above)}{rest}</details>"
        )
    meta = f'<div class="meta">{line}</div>' if line else ""
    return f"<div {attrs}>{act}{head}{''.join(c.above)}{meta}{rest}</div>"
