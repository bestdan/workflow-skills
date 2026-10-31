"""Lane tabs: a page with two or more lanes shows one lane at a time.

Every lane is a `section.lane[data-section]` whose first child is an `h2`
reading "Title (N)". tabify() reads those back out of a rendered page body and
draws one tab per lane under the include switches, so a view has only to give
each lane a counted heading, and a tab's count is that heading's. A lane with
none is named by its capitalized id and counts 0.

A tab reads its label, then its count in a pill. The label is the lane's
`data-tab`, a short name for a long title (tab_attr() writes it), else the
title itself; the full title is always the tab's tooltip. The lane's heading
stays in the page but is not shown beside a tab bar, which already names and
counts the lane.

WHICH LANE SHOWS, first match wins:

1. the URL's `?lane=<data-section>`, when the page has that lane;
2. the lane last chosen on that view, which the page's script keeps in
   `localStorage` per view and switches to on load (the server cannot see it),
   unless the include filter has since switched it off. A lane `?lane=` names
   opens even when off: the link asked for it;
3. the first lane with rows; a page whose lanes are all empty shows its first
   lane the include filter has not switched off.

Every lane is rendered and the others carry `hidden`, so every count stays
right, a tab switch needs no reload, and with no script `?lane=` still works:
each tab is a plain link to it.

A lane whose group the include filter switched off (it carries
`data-filtered`) keeps its tab, with a muted "off" for its count.

The page's text filter (server.JS) recounts each lane and leaves the count in
the lane's `data-shown`; JS below reads that to recount the tabs, dim the ones
with no hits, and move off a current tab the query emptied.
"""

import html
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode

PARAM = "lane"

_e = html.escape

# A lane's opening tag, then its heading when the heading comes first.
_LANE = re.compile(
    r'<section class="lane[^"]*" data-section="(?P<id>[^"]+)"(?P<attrs>[^>]*)>'
    r"(?:<h2>(?P<h2>[^<]*)</h2>)?"
)
_COUNT = re.compile(r"^(?P<title>.*?)\s*\((?P<n>\d+)\)$", re.S)
_TAB = re.compile(r'\sdata-tab="([^"]*)"')
_SWITCHES = re.compile(r'<div class="include"[^>]*>.*?</div>', re.S)


@dataclass(frozen=True)
class Lane:
    id: str
    title: str
    label: str  # the tab's text: data-tab, else the title
    count: int
    off: bool
    start: int  # where the opening tag's `>` is, for adding `hidden`


def lanes(body: str) -> list[Lane]:
    """The page's lanes, in page order; each id once."""
    out: list[Lane] = []
    seen: set[str] = set()
    for m in _LANE.finditer(body):
        lane_id = html.unescape(m["id"])
        if lane_id in seen:
            continue
        seen.add(lane_id)
        head = html.unescape(m["h2"] or "").strip()
        counted = _COUNT.match(head)
        title = (counted["title"] if counted else head) or lane_id.capitalize()
        count = int(counted["n"]) if counted else 0
        short = _TAB.search(m["attrs"])
        label = html.unescape(short[1]).strip() if short else ""
        tag_end = m.start() + m.group(0).index(">")
        off = "data-filtered" in m["attrs"]
        out.append(Lane(lane_id, title, label or title, count, off, tag_end))
    return out


def tab_attr(label: "str | None") -> str:
    """The ` data-tab="..."` a lane carries for a short tab label, or ""."""
    return f' data-tab="{_e(label)}"' if label else ""


def chosen(found: list[Lane], query: "dict[str, list[str]]") -> tuple[Lane, str]:
    """The lane to show and why: `url` or `default`."""
    wanted = (query.get(PARAM) or [""])[-1]
    for lane in found:
        if lane.id == wanted:
            return lane, "url"
    for lane in found:
        if lane.count and not lane.off:
            return lane, "default"
    # Nothing has rows: rather an empty lane than one switched off.
    on = [lane for lane in found if not lane.off]
    return (on or found)[0], "default"


def href(path: str, query: "dict[str, list[str]]", lane_id: str) -> str:
    """`path` with `lane` set and every other parameter kept."""
    pairs = [(k, v) for k, vs in query.items() if k != PARAM for v in vs]
    pairs.append((PARAM, lane_id))
    return f"{path}?{urlencode(pairs, safe=',')}"


def bar(
    found: Iterable[Lane], current: Lane, origin: str, view: str, path: str, query
) -> str:
    tabs = []
    for lane in found:
        selected = lane.id == current.id
        count = (
            '<span class="count off">off</span>'
            if lane.off
            else f'<span class="count">{lane.count}</span>'
        )
        off = ' data-off=""' if lane.off else ""
        tabs.append(
            f'<a role="tab" aria-selected="{"true" if selected else "false"}"'
            f' data-lane="{_e(lane.id)}" data-count="{lane.count}"{off}'
            f' title="{_e(lane.title)}" href="{_e(href(path, query, lane.id))}">'
            f'<span class="name">{_e(lane.label)}</span> {count}</a>'
        )
    return (
        f'<div class="lane-tabs" role="tablist" data-view="{_e(view)}"'
        f' data-origin="{origin}">{"".join(tabs)}</div>'
    )


def tabify(body: str, view: str, path: str, query: "dict[str, list[str]]") -> str:
    """`body` with a tab bar over its lanes and all but one lane hidden.

    A body with fewer than two lanes comes back unchanged. `view` keys the
    remembered lane; `path` is where each tab links.
    """
    found = lanes(body)
    if len(found) < 2:
        return body
    current, origin = chosen(found, query)
    out = body
    # Back to front, so each earlier offset still holds.
    for lane in reversed(found):
        if lane.id != current.id:
            out = out[: lane.start] + " hidden" + out[lane.start :]
    tab_bar = bar(found, current, origin, view, path, query)
    switches = _SWITCHES.search(out)
    if switches:
        at = switches.end()
    else:
        at = _LANE.search(out).start()  # type: ignore[union-attr]
    return out[:at] + tab_bar + out[at:]


CSS = """
/* GitHub's underline tabs, each count in a grey counter pill. */
.lane-tabs { display:flex; flex-wrap:wrap; gap:4px; margin:8px 0 12px;
  border-bottom:1px solid var(--line); }
.lane-tabs a { position:relative; display:inline-flex; align-items:center; gap:6px;
  padding:4px 10px 8px; color:var(--muted); text-decoration:none; }
.lane-tabs a:hover { color:var(--fg); }
.lane-tabs a[aria-selected=true] { color:var(--fg); font-weight:600; }
.lane-tabs a[aria-selected=true]::after { content:""; position:absolute; left:8px;
  right:8px; bottom:-1px; height:2px; border-radius:2px; background:var(--fg); }
.lane-tabs .count { min-width:20px; padding:0 6px; border-radius:999px; font-size:12px;
  font-weight:500; line-height:18px; text-align:center; color:var(--fg);
  background:rgba(129,139,152,.2); }
.lane-tabs .count.off { font-style:italic; opacity:.7; }
/* The tab already names its lane and counts it, so the lane's own heading
   would say it twice. It stays in the page, where lanes() and the scripts
   read it, and its full title is the tab's tooltip. */
body:has(.lane-tabs[data-view]) .lane > h2 { display:none; }
.lane-tabs a.dim { opacity:.45; }
.lane[hidden] { display:none !important; }
"""

# A tab is a plain link to `?lane=`. With script, a plain click shows its lane
# in place, puts `?lane=` in the URL and remembers the lane for the view.
# Opening the view with no `?lane=` then switches to the remembered lane. The
# text filter (server.JS) calls refresh() after each recount.
JS = """
(function () {
  var bar = document.querySelector('.lane-tabs[data-view]');
  if (!bar) return;
  var key = 'lane:' + bar.dataset.view;
  var tabs = Array.prototype.slice.call(bar.querySelectorAll('a[data-lane]'));
  function lane(id) {
    return document.querySelector('.lane[data-section="' + id + '"]');
  }
  function current() {
    return bar.querySelector('a[aria-selected=true]');
  }
  function show(tab, how) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      var l = lane(t.dataset.lane);
      if (l) l.hidden = !on;
    });
    if (how.url) {
      try { history.replaceState(null, '', tab.getAttribute('href')); } catch (e) {}
      // The include switches were built from the URL the page loaded with;
      // keep them on the lane now shown.
      document.querySelectorAll('.include a[data-include]').forEach(function (a) {
        ['href', 'data-toggle-href'].forEach(function (name) {
          var v = a.getAttribute(name);
          if (v === null) return;
          try {
            var u = new URL(v, location.href);
            u.searchParams.set('lane', tab.dataset.lane);
            a.setAttribute(name, u.pathname + u.search);
          } catch (e) {}
        });
      });
    }
    if (how.remember) {
      try { localStorage.setItem(key, tab.dataset.lane); } catch (e) {}
    }
  }
  function hits(tab) {
    var l = lane(tab.dataset.lane);
    if (!l || tab.dataset.off !== undefined) return 0;
    return l.dataset.shown === undefined ? +tab.dataset.count : +l.dataset.shown;
  }
  function refresh(q) {
    tabs.forEach(function (t) {
      if (t.dataset.off !== undefined) return;
      var n = q ? hits(t) : +t.dataset.count;
      t.querySelector('.count').textContent = n;
      t.classList.toggle('dim', !!q && n === 0);
    });
    var cur = current();
    if (q && cur && hits(cur) === 0) {
      for (var i = 0; i < tabs.length; i++) {
        if (hits(tabs[i]) > 0) { show(tabs[i], { url: true }); break; }
      }
    }
  }
  bar.addEventListener('click', function (ev) {
    var a = ev.target.closest('a[data-lane]');
    if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
    ev.preventDefault();
    show(a, { url: true, remember: true });
  });
  if (bar.dataset.origin !== 'url') {
    var saved = null;
    try { saved = localStorage.getItem(key); } catch (e) {}
    var tab = tabs.filter(function (t) { return t.dataset.lane === saved; })[0];
    // A remembered lane switched off since: the server's default stands.
    if (tab && tab.dataset.off === undefined) show(tab, {});
  }
  var box = document.getElementById('filter');
  window.laneTabs = { refresh: refresh };
  if (box && box.value.trim()) refresh(box.value.trim().toLowerCase());
})();
"""
