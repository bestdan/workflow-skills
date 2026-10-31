"""Refresh one GitHub PR or issue in the mirror, without a sync.

A full sync spends most of its time on the fleet searches, so after acting on
one item the page would stay stale until the next one. refresh() re-reads that
one item and rewrites its rows the way a sync would:

- ONE `gh api graphql` call, through github_gh's runner seam, reads the item
  with every field the sync's searches and per-PR lookups read: CI rollup,
  review decision, draft, author and its bot flag, the review requests, the
  closing issues' milestones (with their descriptions, for rule 4), the PR's
  comments (for the co-review stamp), an issue's labels, and the repo's
  `isArchived`. `gh pr view --json` has no archived flag and no viewer login,
  so it cannot answer the archived rule or the `@me` roles on its own.
  Two cases add calls, and both can apply to one PR. Someone else's PR with
  a team review request runs the `review-requested:@me` search over its
  repo (one call per page), since whether I am in the team is not in the
  read; a head commit with more than 100 check contexts takes its CI state
  from one `gh pr view`, as the sync does.
- An open PR gets the rows each search would have given it. Its `prs` row
  (the Jira sync's GitHub step) when it is mine or asks for my review; its
  `kind = pr` row (github_fleet) through github_fleet.queue_row(), with the
  role the first matching search would give it: author, then reviewer (both
  not draft and not approved), then others (not mine, not draft).
- An open issue gets its `kind = issue` row.
- A merged or closed item, one in an archived repo, and one GitHub cannot find
  have their rows deleted, so they leave every view. A merged or closed PR of
  mine gets the `closed_pr` row the closed-PR search would give it, so Stale
  sees the merged-PR evidence for its worktree at once.
- A repo GitHub resolves under a new name (renamed or transferred) has the
  rows under both names replaced, and when its new owner is not under
  `github.orgs` the item's rows are deleted, as the sync would drop them.

Which lane or page the item lands on is not decided here: every page builds
its lanes from these rows as it always does.

A refresh is not a sync: it writes no `sync_runs` row, so the sync figures on
Health and the header's age do not move. Only items under the configured
`github.orgs` are refreshed, as only those are synced; the `kind = pr` and
`kind = issue` rows are written only with `github.scope = "owner"`, as the sync
writes them.
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import github_fleet
import github_gh
import sync
from github_gh import DEFAULT_TIMEOUT, GhError, Runner, run

QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    nameWithOwner isArchived
    issueOrPullRequest(number: $number) {
      __typename
      ... on Issue {
        url number title state createdAt updatedAt
        labels(first: 20) { nodes { name } }
        milestone { number title }
        blocking(first: 50) { nodes { state } }
      }
      ... on PullRequest {
        url number title body state isDraft reviewDecision
        headRefName headRefOid createdAt updatedAt closedAt mergedAt
        changedFiles additions deletions files(first: 20) { nodes { path } }
        author { login __typename }
        reviewRequests(first: 20) {
          nodes { requestedReviewer { __typename ... on User { login } } }
        }
        closingIssuesReferences(first: 10) {
          nodes {
            number state repository { nameWithOwner }
            milestone { number description }
          }
        }
        commits(last: 1) { nodes { commit { statusCheckRollup {
          contexts(first: 100) { pageInfo { hasNextPage } nodes {
            __typename
            ... on CheckRun { status conclusion }
            ... on StatusContext { state }
          } }
        } } } }
        comments(last: 100) { nodes { body createdAt author { login } } }
      }
    }
  }
}
"""

UPDATED = "updated"
MERGED = "merged"
CLOSED = "closed"
ARCHIVED = "archived"
NOT_FOUND = "not_found"
OUT_OF_SCOPE = "out_of_scope"
REMOVED = (MERGED, CLOSED, ARCHIVED, NOT_FOUND, OUT_OF_SCOPE)

_REF = re.compile(
    r"^(?P<repo>[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+)#(?P<n>[1-9]\d*)$"
)
_URL = re.compile(
    r"^https://github\.com/(?P<repo>[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+)"
    r"/(?:pull|issues)/(?P<n>[1-9]\d*)/?$"
)


class RefreshError(ValueError):
    """A ref this instance does not refresh: malformed, or outside its scope."""


@dataclass(frozen=True)
class Result:
    repo: str
    number: int
    kind: "str | None"  # pr, issue, or None when GitHub found nothing
    outcome: str  # updated | merged | closed | archived | not_found
    url: str  # the item's URL (a PR's when the kind is unknown)

    @property
    def ref(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def removed(self) -> bool:
        return self.outcome in REMOVED

    def line(self) -> str:
        """What a person reads: the outcome, then the ref."""
        what = {
            UPDATED: "updated",
            MERGED: "merged (removed)",
            CLOSED: "closed (removed)",
            ARCHIVED: "archived repo (removed)",
            NOT_FOUND: "not found (removed)",
            OUT_OF_SCOPE: "moved outside github.orgs (removed)",
        }[self.outcome]
        kind = f" ({self.kind})" if self.kind else ""
        return f"{what}: {self.ref}{kind}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "url": self.url,
            "kind": self.kind,
            "outcome": self.outcome,
            "removed": self.removed,
        }


def parse_ref(text: str) -> "tuple[str, int]":
    """(`owner/repo`, number) from `owner/repo#N` or a PR or issue URL."""
    text = (text or "").strip()
    m = _REF.match(text) or _URL.match(text)
    if not m:
        raise RefreshError(
            f"not a GitHub item: {text!r} (want owner/repo#N or a PR or issue URL)"
        )
    return m["repo"], int(m["n"])


def _urls(repo: str, number: int) -> "tuple[str, str]":
    base = f"https://github.com/{repo}"
    return f"{base}/pull/{number}", f"{base}/issues/{number}"


def fetch(
    repo: str, number: int, runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> "dict[str, Any] | None":
    """The GraphQL `data` for one item, or None when GitHub resolves nothing."""
    owner, name = repo.split("/", 1)
    argv = [
        "gh", "api", "graphql",
        "-f", f"query={QUERY}",
        "-f", f"owner={owner}",
        "-f", f"name={name}",
        "-F", f"number={int(number)}",
    ]  # fmt: skip
    rc, out, err = runner(argv, timeout)
    if rc != 0 and "could not resolve to" in err.lower():
        return None  # no such repo, or no issue or PR with that number
    data = github_gh._call(argv, lambda _a, _t: (rc, out, err), timeout)
    try:
        body = data["data"]
        found = body["repository"] and body["repository"]["issueOrPullRequest"]
        viewer = body["viewer"]["login"]
    except (KeyError, TypeError):
        raise GhError(f"unexpected response for {repo}#{number}") from None
    if not viewer:
        raise GhError(f"no viewer login in the response for {repo}#{number}")
    return body if found else None


class _Milestones:
    """github_fleet.MilestoneCache's interface over descriptions in hand."""

    def __init__(self, node: dict) -> None:
        self.by_key: dict[tuple[str, int], str] = {}
        for issue in (node.get("closingIssuesReferences") or {}).get("nodes") or []:
            ms = (issue or {}).get("milestone")
            if ms:
                key = (issue["repository"]["nameWithOwner"], ms["number"])
                self.by_key[key] = ms.get("description") or ""

    def description(self, repo: str, number: int) -> str:
        return self.by_key.get((repo, number), "")


def _rollup(node: dict, runner: Runner, timeout: float) -> str:
    """The PR's CI state from the rollup in hand; when the head commit has more
    check contexts than the query reads, from `gh pr view`, as the sync reads
    it."""
    commits = (node.get("commits") or {}).get("nodes") or []
    commit = ((commits[-1] or {}).get("commit") or {}) if commits else {}
    contexts = (commit.get("statusCheckRollup") or {}).get("contexts") or {}
    if (contexts.get("pageInfo") or {}).get("hasNextPage"):
        return github_gh.ci_state(node["url"], runner, timeout)
    return github_gh.reduce_rollup([c for c in contexts.get("nodes") or [] if c])


def _requested(
    node: dict, viewer: str, repo: str, runner: Runner, timeout: float
) -> bool:
    """Whether the PR asks for my review, as `review-requested:@me` would.

    A request to me by name answers it here. A request to a team matches that
    search only when I am in the team, which the item's own read cannot see,
    so a team request is decided by running the search over the PR's repo.
    """
    team = False
    for req in (node.get("reviewRequests") or {}).get("nodes") or []:
        who = (req or {}).get("requestedReviewer") or {}
        if who.get("__typename") == "User" and who.get("login") == viewer:
            return True
        team = team or who.get("__typename") == "Team"
    if not team:
        return False
    query = f"is:pr is:open review-requested:@me repo:{repo}"
    return node["url"] in {
        n["url"] for n in github_gh.search_prs(query, runner, timeout)
    }


def _projects(conn: sqlite3.Connection) -> "set[str]":
    """The Jira project keys in the mirror, as the sync's PR join reads them."""
    keys = [k for (k,) in conn.execute("SELECT key FROM issues WHERE source = 'jira'")]
    return {k.rsplit("-", 1)[0] for k in keys if "-" in k}


def _pr_rows(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    node: dict,
    viewer: str,
    repo: str,
    runner: Runner,
    timeout: float,
) -> "tuple[dict | None, dict | None]":
    """The open PR's (`prs` row, `kind = pr` row), either None when no search
    would return it."""
    author = (node.get("author") or {}).get("login")
    mine = author == viewer
    # The role of my own PR never depends on a review request, so a team
    # request on it (a CODEOWNERS one, say) costs no search.
    requested = not mine and _requested(node, viewer, repo, runner, timeout)
    ci = _rollup(node, runner, timeout)
    node = {**node, "repository": {"nameWithOwner": repo}}

    prs_row = None
    role = "author" if mine else "reviewer" if requested else None
    if role is not None:
        search_node = {
            k: node.get(k)
            for k in (
                "url", "title", "headRefName", "isDraft", "reviewDecision",
                "createdAt", "updatedAt", "repository", "changedFiles",
                "additions", "deletions", "files",
            )
        }  # fmt: skip
        prs_row = github_gh._row(search_node, role)
        prs_row["ci_state"] = ci
        text = f"{prs_row['title']} {prs_row.get('head_ref') or ''}"
        keys = github_gh.jira_keys(text, _projects(conn))
        prs_row["issue_key"] = keys[0] if keys else None

    fleet_row = None
    if sync.fleet_enabled(cfg):
        awaiting = github_fleet.in_review_queue(node)
        fleet_role = (
            "author" if mine and awaiting
            else "reviewer" if requested and awaiting
            else "others" if not mine and not node.get("isDraft")
            else None
        )  # fmt: skip
        if fleet_role is not None:
            comments = (node.get("comments") or {}).get("nodes") or []
            fleet_row, _ = github_fleet.queue_row(
                node, fleet_role, ci, _Milestones(node),
                lambda _repo, _n: (viewer, comments),
            )  # fmt: skip
    return prs_row, fleet_row


def _write(
    conn: sqlite3.Connection,
    repos: "tuple[str, ...]",
    number: int,
    prs_row: "dict | None",
    rows: list[dict],
    now: datetime,
) -> None:
    """Replace the item's rows in one transaction; nothing else is touched.

    `repos` names every repo the item's rows may sit under: the name asked
    for and, for a renamed or transferred repo, the one GitHub resolved.
    """
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    keys = [u for r in dict.fromkeys(repos) for u in _urls(r, number)]
    pr_urls = keys[::2]
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            f"DELETE FROM issues WHERE source = ? AND key IN ({_marks(keys)})"
            " AND kind IN ('pr', 'issue', 'closed_pr')",
            (github_fleet.SOURCE, *keys),
        )
        conn.execute(f"DELETE FROM prs WHERE url IN ({_marks(pr_urls)})", pr_urls)
        if prs_row is not None:
            fields = sync._PR_FIELDS
            conn.execute(
                f"INSERT INTO prs ({', '.join(fields)}) VALUES"
                f" ({', '.join('?' * len(fields))})",
                tuple(prs_row.get(c) for c in fields),
            )
        github_fleet.insert(conn, rows, stamp)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _marks(values: list) -> str:
    return ", ".join("?" * len(values))


def _in_scope(repo: str, owners: "set[str]") -> bool:
    return repo.split("/", 1)[0].lower() in owners


def refresh(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    ref: str,
    now: datetime,
    runner: Runner = run,
    timeout: float = DEFAULT_TIMEOUT,
) -> Result:
    """Re-read one item from GitHub and rewrite its mirror rows.

    Raises RefreshError for a ref outside this instance's scope, and GhError
    (AuthError included) when GitHub cannot be read; either leaves the mirror
    as it was.
    """
    gh = cfg["github"]
    if not gh["enabled"]:
        raise RefreshError("github.enabled is off, so nothing from GitHub is mirrored")
    repo, number = parse_ref(ref)
    owners = {str(o).lower() for o in gh["orgs"]}
    if not _in_scope(repo, owners):
        raise RefreshError(
            f"{repo} is not under github.orgs ({', '.join(sorted(owners)) or 'none'})"
        )
    data = fetch(repo, number, runner, timeout)
    pr_url, _ = _urls(repo, number)
    if data is None:
        _write(conn, (repo,), number, None, [], now)
        return Result(repo, number, None, NOT_FOUND, pr_url)
    viewer = data["viewer"]["login"]
    repository = data["repository"]
    # GitHub answers an old name with the repo it was renamed or transferred
    # to: the rows under the old name go as well, and the new owner has to be
    # one this instance syncs.
    asked, repo = repo, repository.get("nameWithOwner") or repo
    node = repository["issueOrPullRequest"]
    kind = "pr" if node.get("__typename") == "PullRequest" else "issue"
    url = node.get("url") or _urls(repo, number)[0]
    state = (node.get("state") or "").upper()
    archived = bool(repository.get("isArchived"))

    prs_row: "dict | None" = None
    rows: list[dict] = []
    if not _in_scope(repo, owners):
        outcome = OUT_OF_SCOPE
    elif kind == "pr" and state in ("MERGED", "CLOSED"):
        outcome = MERGED if state == "MERGED" or node.get("mergedAt") else CLOSED
        author = (node.get("author") or {}).get("login")
        if author == viewer and node.get("closedAt") and sync.fleet_enabled(cfg):
            # The closed-PR search has no `archived:false`: a PR merged before
            # its repo was archived is still evidence its worktree can go.
            rows.append(
                github_fleet.closed_row({**node, "repository": {"nameWithOwner": repo}})
            )
    elif state == "CLOSED":
        outcome = CLOSED
    elif archived:
        outcome = ARCHIVED
    else:
        outcome = UPDATED
        if kind == "pr":
            prs_row, fleet_row = _pr_rows(
                conn, cfg, node, viewer, repo, runner, timeout
            )
            rows += [fleet_row] if fleet_row else []
        elif sync.fleet_enabled(cfg):
            rows.append(
                github_fleet._issue_row({**node, "repository": {"nameWithOwner": repo}})
            )
    # Every row is deleted and rewritten in one transaction, and no sync_runs
    # row is touched: a refresh is not a sync.
    _write(conn, (asked, repo), number, prs_row, rows, now)
    return Result(repo, number, kind, outcome, url)


# --- the page ----------------------------------------------------------------

CSS = """
.card [data-refresh] { cursor:pointer; }
.card.refreshing { opacity:.65; }
@media (prefers-reduced-motion: no-preference) {
  .card.refreshing [data-refresh] .glyph { animation:refresh-spin .8s linear infinite; }
}
@keyframes refresh-spin { to { transform:rotate(360deg); } }
.card .refresh-error { color:var(--bad); font-size:12px; margin-top:4px; }
.refresh-note { color:var(--muted); font-size:12px; font-style:italic;
  border:1px dashed var(--line); border-radius:6px; padding:4px 10px; margin-bottom:6px; }
.refresh-stale { color:var(--muted); font-size:12px; margin-bottom:6px; }
"""

# The card's Refresh button posts to /refresh. On success it fetches the page
# again, as the server now builds it, and moves the item's cards to match:
# every lane the item was in or is now in takes the new lane's contents, each
# heading and tab its new count. A lane where another card has its "why not"
# line open or unsaved text is not swapped: only the item's own cards change there,
# and a hint says the lane may be out of date. Where the clicked card left its lane, a short
# note stands in its place. A failure leaves the card as it was, with the
# error on it. The page is never reloaded, except when the item lands in a
# lane this page did not draw (an optional lane that was empty), which would
# need a new tab.
JS = """
(function () {
  var token = document.querySelector('meta[name=dashboard-token]').content;
  var GONE = { merged: 'merged', closed: 'closed', archived: 'its repo is archived',
    not_found: 'not found on GitHub', out_of_scope: 'moved outside github.orgs' };
  function matching(root, source, keys) {
    return Array.prototype.filter.call(root.querySelectorAll('.card[data-key]'),
      function (c) { return c.dataset.source === source && keys.indexOf(c.dataset.key) !== -1; });
  }
  // Whether a card other than the item's own has an open <details> or
  // "why not" line, or a field holding text not yet saved.
  function busy(lane, source, keys) {
    return Array.prototype.some.call(lane.querySelectorAll('.card'), function (c) {
      if (c.dataset.source === source && keys.indexOf(c.dataset.key) !== -1) return false;
      if (c.querySelector('details[open]')) return true;
      if (Array.prototype.some.call(c.querySelectorAll('.whynot'),
        function (w) { return !w.hidden; })) return true;
      return Array.prototype.some.call(c.querySelectorAll('textarea, input'),
        function (f) { return f.value !== f.defaultValue; });
    });
  }
  // A busy lane keeps every other card as it is: only the item's own cards
  // change, and a hint says the rest may be out of date.
  function patch(cur, next, source, keys) {
    var olds = matching(cur, source, keys), news = matching(next, source, keys);
    var order = Array.prototype.slice.call(next.querySelectorAll('.card'));
    news.forEach(function (n, i) {
      var el = document.importNode(n, true);
      if (olds[i]) { olds[i].replaceWith(el); return; }
      for (var j = order.indexOf(n) - 1; j >= 0; j--) {
        var prev = matching(cur, order[j].dataset.source, [order[j].dataset.key])[0];
        if (prev) { prev.after(el); return; }
      }
      var first = cur.querySelector('.card');
      if (first) first.before(el); else cur.appendChild(el);
    });
    olds.slice(news.length).forEach(function (c) { c.remove(); });
    var h = cur.querySelector('h2'), nh = next.querySelector('h2');
    if (h && nh) h.textContent = nh.textContent;
    if (!cur.querySelector('.refresh-stale')) {
      var hint = document.createElement('div');
      hint.className = 'refresh-stale';
      hint.innerHTML = 'Lane may be out of date; <a href="">reload</a>';
      if (h) h.after(hint); else cur.prepend(hint);
    }
  }
  function laneOf(el) {
    var l = el.closest('.lane[data-section]');
    return l ? l.dataset.section : null;
  }
  function laneIn(root, id) {
    return Array.prototype.filter.call(root.querySelectorAll('.lane[data-section]'),
      function (l) { return l.dataset.section === id; })[0] || null;
  }
  function label(id) {
    var tab = document.querySelector('.lane-tabs a[data-lane="' + id + '"] .name');
    var lane = laneIn(document, id), h = lane && lane.querySelector('h2');
    return tab ? tab.textContent : h ? h.textContent.replace(/\\s*\\(\\d+\\)$/, '') : id;
  }
  function failed(card, b, why) {
    card.classList.remove('refreshing');
    b.disabled = false;
    var e = document.createElement('div');
    e.className = 'refresh-error';
    e.setAttribute('role', 'alert');
    e.textContent = 'Refresh failed: ' + why;
    card.appendChild(e);
  }
  function apply(doc, res, clicked) {
    // A renamed or transferred repo's item comes back under its new URL.
    var source = clicked.dataset.source, keys = [clicked.dataset.key];
    if (res.url && keys.indexOf(res.url) === -1) keys.push(res.url);
    var was = laneOf(clicked), touched = [];
    function touch(id) { if (id && touched.indexOf(id) === -1) touched.push(id); }
    matching(document, source, keys).forEach(function (c) { touch(laneOf(c)); });
    var now = matching(doc, source, keys);
    now.forEach(function (c) { touch(laneOf(c)); });
    for (var i = 0; i < touched.length; i++) {
      if (!laneIn(document, touched[i])) { location.reload(); return; }
    }
    var cards = was ? Array.prototype.slice.call(
      laneIn(document, was).querySelectorAll('.card')) : [];
    var at = cards.indexOf(clicked);
    touched.forEach(function (id) {
      var cur = laneIn(document, id), next = laneIn(doc, id);
      if (next && busy(cur, source, keys)) { patch(cur, next, source, keys); return; }
      if (next) { cur.innerHTML = next.innerHTML; return; }
      // The new page leaves this lane out (an optional lane, now empty).
      matching(cur, source, keys).forEach(function (c) { c.remove(); });
      var h = cur.querySelector('h2');
      if (h) {
        h.textContent = h.textContent.replace(/\\s*\\(\\d+\\)$/, '') +
          ' (' + cur.querySelectorAll('.card').length + ')';
      }
    });
    var stays = now.some(function (c) { return laneOf(c) === was; });
    if (was && !stays) {
      var lanes = [];
      now.forEach(function (c) { var id = laneOf(c); if (lanes.indexOf(id) === -1) lanes.push(id); });
      var n = document.createElement('div');
      n.className = 'refresh-note';
      n.textContent = GONE[res.outcome] ? GONE[res.outcome] + ' \\u2014 removed: ' + res.ref
        : lanes.length ? 'moved to ' + lanes.map(label).join(', ') + ': ' + res.ref
        : 'no longer on this page: ' + res.ref;
      var lane = laneIn(document, was), left = lane.querySelectorAll('.card');
      if (at >= 0 && at < left.length) left[at].before(n);
      else if (left.length) left[left.length - 1].after(n);
      else lane.appendChild(n);
    }
    // Tab counts, from the new page's tab bar.
    doc.querySelectorAll('.lane-tabs a[data-lane]').forEach(function (t) {
      var mine = document.querySelector('.lane-tabs a[data-lane="' + t.dataset.lane + '"]');
      if (!mine) return;
      mine.dataset.count = t.dataset.count;
      if (mine.dataset.off === undefined) {
        mine.querySelector('.count').textContent = t.dataset.count;
      }
    });
    // A tab whose lane the new page left out (an optional lane, now empty)
    // counts what is left in its lane here.
    document.querySelectorAll('.lane-tabs a[data-lane]').forEach(function (mine) {
      var id = mine.dataset.lane, lane = laneIn(document, id);
      if (!lane || mine.dataset.off !== undefined) return;
      if (doc.querySelector('.lane-tabs a[data-lane="' + id + '"]')) return;
      var n = lane.querySelectorAll('.card').length;
      mine.dataset.count = n;
      mine.querySelector('.count').textContent = n;
    });
    var box = document.getElementById('filter');
    var q = box ? box.value.trim() : '';
    if (q && window.cardFilter) window.cardFilter.apply();
    else if (window.laneTabs) window.laneTabs.refresh('');
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-refresh]');
    if (!b) return;
    ev.preventDefault();
    var card = b.closest('.card');
    if (!card || card.classList.contains('refreshing')) return;
    var old = card.querySelector('.refresh-error');
    if (old) old.remove();
    card.classList.add('refreshing');
    b.disabled = true;
    fetch('/refresh', { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'token=' + encodeURIComponent(token) + '&key=' + encodeURIComponent(card.dataset.key)
    }).then(function (r) {
      return r.text().then(function (t) {
        var res = null;
        try { res = JSON.parse(t); } catch (e) {}
        if (!r.ok || !res || !res.ok) {
          throw new Error((res && res.error) || (t.trim() || 'HTTP ' + r.status));
        }
        return res;
      });
    }).then(function (res) {
      return fetch(location.href, { headers: { Accept: 'text/html' } }).then(function (r) {
        if (!r.ok) throw new Error('the page answered ' + r.status);
        return r.text();
      }).then(function (html) {
        apply(new DOMParser().parseFromString(html, 'text/html'), res, card);
      });
    }).catch(function (e) { failed(card, b, e.message || 'the server did not answer'); });
  });
})();
"""
