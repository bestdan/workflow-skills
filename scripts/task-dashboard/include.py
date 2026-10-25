"""Per-view include filters: which groups of rows a view shows.

Every mirrored row carries a `kind`, and KIND_GROUPS, the one place that maps
a kind onto a group, sorts it into one of four groups:

- `tasks`: Jira tickets and GitHub issues (and a drain's "needs you" items);
- `prs`: pull requests, open or closed;
- `sessions`: hook and cloud Claude sessions;
- `other`: worktrees, handoffs, tmux sessions and designs, and any kind this
  table does not name yet.

WHICH GROUPS A VIEW SHOWS is decided in this order, first match wins:

1. the URL's `?include=tasks,prs` (repeated `include` values are joined, and
   `include=` with nothing names no group). The URL is the source of truth, so
   a filtered view is a link that can be bookmarked;
2. the choice last made with that view's switches, remembered in the overlay
   (overlay.py, the `include` action), so a bare URL restores it;
3. config `[views.<view>] include = [...]`;
4. every group, which is what a view showed before filters existed.

WHAT A SWITCH CLICK ASKS FOR (switches() below). The row is an "All" switch
and then one switch per group:

- a plain click on a group shows only that group; clicking the group that is
  already the only one shown goes back to every group;
- a shift-click on a group adds it to the selection, or removes it, so several
  groups can show at once (cmd/ctrl-click is left to open a new tab);
- "All" shows every group, and reads as on when every group is included.

Each switch's `href` and `data-include` are its plain-click target, so a page
with no script still gets the solo behaviour; its `data-toggle` and
`data-toggle-href` are the shift-click target, which the page's script picks
when the shift key is held. Either way the click is remembered like any other
choice, so only what a click asks for differs, not the precedence above.

An unknown group name in the URL is an IncludeError, which the server turns
into a 400 naming the valid groups; config.load turns one into a ConfigError.

The header gauges are never filtered: they are computed from the mirror
before, and apart from, any view's rows.

This module imports nothing from the package, so config.py and overlay.py can
both validate against it.
"""

import html
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlencode

GROUPS = ("tasks", "prs", "sessions", "other")
LABELS = {"tasks": "Tasks", "prs": "PRs", "sessions": "Sessions", "other": "Other"}

# The one kind -> group mapping. A kind missing here is `other`.
KIND_GROUPS = {
    "ticket": "tasks",  # Jira
    "issue": "tasks",  # GitHub, github_fleet
    "drain": "tasks",  # needs.drain_items
    "pr": "prs",
    "closed_pr": "prs",
    "session": "sessions",  # sessions.py and claude_remote.py
    "worktree": "other",
    "handoff": "other",
    "tmux": "other",
    "design": "other",
}

# The views a filter applies to, by the name config and the overlay use: the
# route path without its slash. `/` is `needs` on a fleet instance and `board`
# on a Jira one.
VIEWS = (
    "needs",
    "now",
    "away",
    "waiting-on-me",
    "waiting-on",
    "board",
    "stale",
    "epics",
    "health",
)

PARAM = "include"


class IncludeError(ValueError):
    """An include value that names a group this module does not know."""


def group_of(kind: "str | None") -> str:
    return KIND_GROUPS.get(kind or "", "other")


def canonical(groups: Iterable[str]) -> tuple[str, ...]:
    """`groups` in GROUPS order, each once."""
    chosen = set(groups)
    return tuple(g for g in GROUPS if g in chosen)


def parse(values: Iterable[str]) -> frozenset[str]:
    """Comma-separated group names (one or more values) -> the group set."""
    names = [p.strip() for v in values for p in str(v).split(",") if p.strip()]
    unknown = sorted({n for n in names if n not in GROUPS})
    if unknown:
        raise IncludeError(
            f"unknown include group: {', '.join(unknown)};"
            f" valid groups are {', '.join(GROUPS)}"
        )
    return frozenset(names)


def from_query(query: "dict[str, list[str]]") -> "frozenset[str] | None":
    """The URL's include set, or None when the URL names none.

    `query` must come from parse_qs(..., keep_blank_values=True), or an empty
    `include=` (every switch off) would read as no include at all.
    """
    if PARAM not in query:
        return None
    return parse(query[PARAM])


def config_default(cfg: dict[str, Any], view: str) -> "frozenset[str] | None":
    table = (cfg.get("views") or {}).get(view) or {}
    groups = table.get("include")
    # parse(), as validate_config did: `[" prs "]` and `["tasks,prs"]` validate,
    # so they must mean what they validated as.
    return None if groups is None else parse(groups)


@dataclass(frozen=True)
class Include:
    view: str
    groups: frozenset[str]
    origin: str  # url | remembered | config | default

    def shows(self, kind: "str | None") -> bool:
        return group_of(kind) in self.groups

    def has(self, group: str) -> bool:
        return group in self.groups

    @property
    def everything(self) -> bool:
        return self.groups >= set(GROUPS)


ALL = frozenset(GROUPS)


def everything(view: str = "") -> Include:
    return Include(view, ALL, "default")


def resolve(
    view: str,
    query: "dict[str, list[str]]",
    remembered: "frozenset[str] | None",
    cfg: dict[str, Any],
) -> Include:
    """The include set for one request, by the order in the module docstring."""
    url = from_query(query)
    if url is not None:
        return Include(view, url, "url")
    if remembered is not None:
        return Include(view, remembered, "remembered")
    default = config_default(cfg, view)
    if default is not None:
        return Include(view, default, "config")
    return everything(view)


def validate_config(views: Any) -> "str | None":
    """The first problem with config `[views]`, or None when it is valid."""
    if not isinstance(views, dict):
        return "views must be a table of [views.<view>] tables"
    for view, table in views.items():
        if view not in VIEWS:
            return f"views.{view} names no view; valid views are {', '.join(VIEWS)}"
        if not isinstance(table, dict) or set(table) - {"include"}:
            return f"views.{view} must hold only `include = [...]`"
        groups = table.get("include")
        if groups is None:
            continue
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            return f"views.{view}.include must be a list of group names"
        try:
            parse(groups)
        except IncludeError as exc:
            return f"views.{view}.include: {exc}"
    return None


# --- rendering ---------------------------------------------------------------

_e = html.escape

CSS = """
.include { display:flex; flex-wrap:wrap; gap:6px; align-items:center;
  padding:8px 0 4px; font-size:12px; color:var(--muted); }
.include a { padding:2px 10px; border-radius:999px; border:1px solid var(--line);
  color:var(--muted); text-decoration:none; }
.include a[aria-checked=true] { color:var(--fg); background:var(--card);
  border-color:var(--accent); font-weight:600; }
.include .hint { margin-left:4px; }
.lane[data-filtered] .empty { font-style:italic; }
/* The header's text filter hides an empty lane's text; a switched-off lane
   keeps saying why it is empty. */
.lane.filtered-empty .empty.switched-off { display:block; }
"""

# A switch is a link to its plain-click URL, so it works with no script at all.
# With one, a shift-click swaps in the switch's shift-click target, and either
# click first remembers the choice through the overlay POST (Host, Origin and
# token checked like every other write), then follows it. Which target each
# click means is computed in switches(), so this only picks between the two.
JS = """
(function () {
  var row = document.querySelector('.include[data-view]');
  if (!row) return;
  var token = document.querySelector('meta[name=dashboard-token]').content;
  row.addEventListener('click', function (ev) {
    var a = ev.target.closest('a[data-include]');
    if (!a) return;
    if (ev.metaKey || ev.ctrlKey) return;
    ev.preventDefault();
    var shift = ev.shiftKey && a.dataset.toggle !== undefined;
    var value = shift ? a.dataset.toggle : a.dataset.include;
    var url = shift ? a.dataset.toggleHref : a.href;
    var body = 'token=' + encodeURIComponent(token) + '&view=' +
      encodeURIComponent(row.dataset.view) + '&include=' +
      encodeURIComponent(value);
    function go() { location.href = url; }
    fetch('/overlay/include', { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body
    }).then(function (r) {
      if (!r.ok) { alert('Filter not remembered (' + r.status + ')'); }
      go();
    }).catch(go);
  });
})();
"""


def href(path: str, query: "dict[str, list[str]]", groups: Iterable[str]) -> str:
    """`path` with `include` set to `groups` and every other parameter kept."""
    pairs = [(k, v) for k, vs in query.items() if k != PARAM for v in vs]
    pairs.append((PARAM, ",".join(canonical(groups))))
    return f"{path}?{urlencode(pairs, safe=',')}"


def solo(inc: Include, group: str) -> frozenset[str]:
    """What a plain click on `group` shows: it alone, or every group when it
    is already the only one shown."""
    return ALL if inc.groups == {group} else frozenset({group})


def toggle(inc: Include, group: str) -> frozenset[str]:
    """What a shift-click on `group` shows: the selection with it flipped."""
    return inc.groups - {group} if inc.has(group) else inc.groups | {group}


HINT = "Click shows one group; shift-click adds or removes one."


def switches(inc: Include, path: str, query: "dict[str, list[str]]") -> str:
    """The row of switches that goes under the nav: All, then each group."""

    def value(groups: Iterable[str]) -> str:
        return _e(",".join(canonical(groups)))

    links = [
        f'<a role="switch" aria-checked="{"true" if inc.everything else "false"}"'
        f' data-all="" data-include="{value(ALL)}"'
        f' href="{_e(href(path, query, ALL))}">All</a>'
    ]
    for g in GROUPS:
        plain, shift = solo(inc, g), toggle(inc, g)
        links.append(
            f'<a role="switch" aria-checked="{"true" if inc.has(g) else "false"}"'
            f' data-group="{g}" data-include="{value(plain)}"'
            f' href="{_e(href(path, query, plain))}"'
            f' data-toggle="{value(shift)}"'
            f' data-toggle-href="{_e(href(path, query, shift))}">{_e(LABELS[g])}</a>'
        )
    return (
        f'<div class="include" data-view="{_e(inc.view)}"'
        f' data-include="{value(inc.groups)}"'
        f' data-origin="{_e(inc.origin)}">Include: {"".join(links)}'
        f'<span class="hint">{_e(HINT)}</span></div>'
    )


def off_note(*groups: str) -> str:
    """The body of a lane whose every group is switched off."""
    names = [LABELS[g] for g in groups]
    text = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f'<p class="empty switched-off">{_e(text)} are switched off.</p>'
