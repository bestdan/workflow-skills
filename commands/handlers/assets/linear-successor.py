#!/usr/bin/env python3
"""Point each migrated Linear issue at its GitHub successor, and close it.

The import (`linear-import.py`) left every selected issue alive in two places.
A live Linear copy is a second board to read and a second board for a nightly
routine to act on, so each original gets a comment naming its successor, a link
attachment to it, and — with `--cancel` — a `canceled`-type state.

    python3 linear-successor.py --mapping <mapping.json> [--cancel] [--apply] [--json]

DRY RUN by default: it reads Linear, prints the writes it would make, and
changes nothing. `--apply` executes them.

`Duplicate` is deliberately not used even though it reads better. Linear's
`duplicateOf` accepts only another Linear issue, so a GitHub successor cannot be
expressed that way; `Canceled` plus the comment and the attachment is the whole
statement. See `dev_docs/gh-issue-migration/phase_4_migrate/gh_migration_task_9.md`.

**`--cancel` is a separate flag from `--apply` on purpose.** The state write is
the one step task 10 may reverse, and keeping it behind its own flag means the
inverse is this same script's shape rather than a new one: the comment and the
attachment stay true whatever task 10 decides.

IDEMPOTENT PER WRITE, NOT PER ISSUE. A crash between any two of the three writes
must leave the rerun able to finish the rest, so each is guarded by its own
question asked of the live issue:

  - the comment, when no comment on the issue quotes the successor URL;
  - the attachment, when no attachment carries that URL;
  - the state, when the issue's state type is not already `canceled`.

An issue that already has the attachment but not the comment therefore gets the
comment and the state and nothing else. There is no marker and no local ledger —
the issue itself is the record, which is what makes the rerun safe after a lost
response as well as after a crash.

TRUNCATION WOULD BREAK THAT. A comments page cut short reads exactly like an
issue with no successor comment, and the script would post a second one. So both
nested connections are fetched at `NESTED_PAGE` and RAISE when `hasNextPage` is
still true, rather than answering the idempotence question from a partial page.

The state id is resolved by TYPE, never by display name — names are
user-configurable (see `commands/handlers/linear-common.md` → "Kanban mapping").

SCHEMA NOTE — `issue(id:)` is asked for a human identifier (`PRE-824`) rather
than a UUID, which is documented Linear behaviour but is NOT verified against
the live API from this repo: it runs keyless, as `linear-export.py`'s own schema
note records. The failure mode if that assumption is wrong is loud and harmless:
every issue comes back null, the dry run reports each key as "no such issue in
Linear" and exits 1, and no write has been attempted. Because the default is a
dry run, the first live invocation is the check.

The API key is resolved by commands/handlers/assets/_secret_resolve.py, which
walks two independent ladders: secret/pointer (`$LINEAR_API_KEY` ->
`$LINEAR_API_KEY_REF` -> unavailable) and resolver (`$LINEAR_API_KEY_RESOLVER`
-> default `op`), against an allow-list of resolver identifiers (`op`, `opx`).
A failed resolve never falls through to the next rung. See
dev_docs/auth_key_access.md for the full contract. This script reads no config.

Either kind of credential works: `_linear_auth.py` frames a personal API key
bare and an OAuth `client_credentials` token as `Bearer`, decided from the
key's own shape. The writes here are what make that matter — a token minted
with the default `app` actor posts the comment as the OAuth app rather than
as a person, which is legible provenance for a migration but is not the same
author a personal key would record.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _linear_auth import auth_header  # noqa: E402
from _secret_resolve import SecretUnavailable, resolve_key  # noqa: E402
from _shape import ShapeError, expect  # noqa: E402

API = "https://api.linear.app/graphql"

# Nested-connection page size, and the cap that turns an overflow into a crash.
# Far above anything observed on this workspace (the busiest issue carries a
# couple of dozen comments), so an overflow means the assumption broke — which
# is the one case where a loud failure beats a quiet duplicate comment.
NESTED_PAGE = 250

# The phase `linear-import.py` records once an issue has fully landed on GitHub.
# Anything short of it is not a successor yet.
LANDED_PHASE = "done"

# `issue(id:)` takes the human identifier (`PRE-824`) as well as a UUID — the
# same lookup Linear's own SDK makes for `client.issue("ENG-123")`. The mapping
# records identifiers and not UUIDs, so that is what this asks for, one issue at
# a time rather than a filtered `issues` query: `IssueFilter` has no identifier
# field, and reconstructing one from a team filter plus `number` would put a
# second copy of the key-parsing rules here.
ISSUE_QUERY = """
query($key: String!, $nested: Int!) {
  issue(id: $key) {
    id
    identifier
    url
    state { id name type }
    team { id name }
    comments(first: $nested) {
      nodes { id body }
      pageInfo { hasNextPage }
    }
    attachments(first: $nested) {
      nodes { id url }
      pageInfo { hasNextPage }
    }
  }
}"""

# Team workflow states, for resolving the `canceled` one by type.
STATES_QUERY = """
query($team: String!) {
  team(id: $team) {
    states(first: 100) {
      nodes { id name type position }
      pageInfo { hasNextPage }
    }
  }
}"""

COMMENT_MUTATION = """
mutation($issue: String!, $body: String!) {
  commentCreate(input: { issueId: $issue, body: $body }) { success }
}"""

ATTACHMENT_MUTATION = """
mutation($issue: String!, $title: String!, $url: String!) {
  attachmentCreate(input: { issueId: $issue, title: $title, url: $url }) { success }
}"""

STATE_MUTATION = """
mutation($id: String!, $state: String!) {
  issueUpdate(id: $id, input: { stateId: $state }) { success }
}"""

# The three writes, in the order they are made. Named here so the planner, the
# applier and the summary all walk one list rather than three copies of it.
WRITES = ("comment", "attachment", "state")


class SuccessorError(Exception):
    """The script cannot form an opinion — an unreadable mapping, a truncated
    page, a team with no canceled state. Distinct from a write that failed,
    which is a reported per-issue outcome and not a reason to stop."""


def comment_body(repo, number, url):
    """The successor comment. `repo` comes from the mapping, which asserts the
    board it belongs to, so this names the real repository rather than a
    hardcoded one. PR #503 is the switch itself and is a fixed fact."""
    return (
        f"Migrated to GitHub as {repo}#{number} — {url}. "
        "This repo's tracker is GitHub Issues as of PR #503; the GitHub issue "
        "carries the work from here."
    )


def attachment_title(number):
    return f"GitHub #{number} (migrated)"


# ---------------------------------------------------------------------------
# Linear I/O. gql() is the seam the tests stub.
# ---------------------------------------------------------------------------


def get_key():
    try:
        return resolve_key("LINEAR_API_KEY")
    except SecretUnavailable as e:
        sys.exit(str(e))


def gql(key, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": auth_header(key),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Linear API error {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e.reason}")
    # Guard the root BEFORE the membership test: `"errors" in None` raises
    # TypeError, which is exactly the traceback this seam exists to remove.
    if not isinstance(payload, dict):
        sys.exit(f"GraphQL response: expected an object, got {type(payload).__name__}")
    if "errors" in payload:
        sys.exit("GraphQL error: " + json.dumps(payload["errors"], indent=2))
    try:
        return expect(payload, "data", dict, "GraphQL response")
    except ShapeError as exc:
        sys.exit(str(exc))


def nodes_of(issue, field, key):
    """A nested connection's nodes, refusing a page that was cut short.

    The overflow is fatal rather than a warning because both connections are
    read to answer "has this write already happened?", and a truncated page
    answers it wrong in the direction that writes a duplicate.
    """
    try:
        conn = expect(issue, field, dict, f"issue {key}")
        page = expect(conn, "pageInfo", dict, f"issue {key}.{field}")
        if page.get("hasNextPage"):
            raise SuccessorError(
                f"{key}: more than {NESTED_PAGE} {field} — refusing to decide "
                "idempotence from a truncated page. Raise NESTED_PAGE."
            )
        return expect(conn, "nodes", list, f"issue {key}.{field}")
    except ShapeError as exc:
        raise SuccessorError(str(exc))


def fetch_issue(api_key, key):
    data = gql(api_key, ISSUE_QUERY, {"key": key, "nested": NESTED_PAGE})
    issue = data.get("issue")
    if not isinstance(issue, dict):
        raise SuccessorError(f"{key}: no such issue in Linear")
    return issue


def canceled_state(api_key, team, cache):
    """The team's `canceled`-type state id, resolved by type and never by name.

    Linear permits several states of one type. When a team has more than one,
    the lowest `position` is the leftmost on the board and is the one a human
    would pick; the choice is reported rather than made silently.
    """
    team_id = team["id"]
    if team_id in cache:
        return cache[team_id]
    data = gql(api_key, STATES_QUERY, {"team": team_id})
    node = data.get("team")
    if not isinstance(node, dict):
        raise SuccessorError(f"team {team.get('name', team_id)}: cannot read states")
    try:
        conn = expect(node, "states", dict, "team")
        if expect(conn, "pageInfo", dict, "team.states").get("hasNextPage"):
            raise SuccessorError("team has more than 100 workflow states")
        states = expect(conn, "nodes", list, "team.states")
    except ShapeError as exc:
        raise SuccessorError(str(exc))
    canceled = sorted(
        (s for s in states if s.get("type") == "canceled"),
        key=lambda s: (s.get("position") or 0, s.get("name") or ""),
    )
    if not canceled:
        raise SuccessorError(
            f"team {team.get('name', team_id)} has no `canceled`-type workflow "
            "state — nothing to cancel into."
        )
    chosen = canceled[0]
    if len(canceled) > 1:
        print(
            f"Note: team {team.get('name', team_id)} has "
            f"{len(canceled)} canceled-type states; using {chosen.get('name')!r}."
        )
    cache[team_id] = chosen
    return chosen


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def load_mapping(path):
    """The import mapping, with its repo. The repo is read, never assumed: the
    comment body names it, and a mapping belongs to exactly one board."""
    try:
        with open(path, encoding="utf-8") as fh:
            mapping = json.load(fh)
    except OSError as exc:
        raise SuccessorError(f"cannot read the mapping at {path}: {exc}")
    except json.JSONDecodeError as exc:
        raise SuccessorError(f"the mapping at {path} is not valid JSON: {exc}")
    if not isinstance(mapping, dict) or not isinstance(mapping.get("entries"), dict):
        raise SuccessorError(f"{path}: not an import mapping (no `entries` object)")
    if not mapping.get("repo"):
        raise SuccessorError(f"{path}: the mapping records no `repo`")
    return mapping


def landed(mapping):
    """(entries that landed, keys that did not). A key short of `done`, or with
    no number, has no successor to point at yet — it is listed and skipped
    rather than fatal, so a partial import can still be closed out for the part
    that landed and finished later."""
    ready, pending = [], []
    for key in sorted(mapping["entries"]):
        entry = mapping["entries"][key] or {}
        if entry.get("phase") != LANDED_PHASE or not entry.get("number"):
            pending.append(key)
            continue
        ready.append(
            {
                "key": key,
                "number": entry["number"],
                "url": entry.get("url")
                or f"https://github.com/{mapping['repo']}/issues/{entry['number']}",
            }
        )
    return ready, pending


# ---------------------------------------------------------------------------
# Planning — the three independent questions
# ---------------------------------------------------------------------------


def plan_issue(issue, entry, repo, cancel):
    """Which of the three writes this issue still needs.

    Each question is asked of the live issue alone. Nothing here consults what
    the other two decided, which is what makes a half-finished issue recover.
    """
    key = entry["key"]
    url = entry["url"]
    pending = []

    comments = nodes_of(issue, "comments", key)
    if not any(url in (c.get("body") or "") for c in comments):
        pending.append("comment")

    attachments = nodes_of(issue, "attachments", key)
    if not any((a.get("url") or "") == url for a in attachments):
        pending.append("attachment")

    if cancel:
        state = issue.get("state") or {}
        if state.get("type") != "canceled":
            pending.append("state")

    return {
        "key": key,
        "id": issue.get("id"),
        "number": entry["number"],
        "url": url,
        "state": (issue.get("state") or {}).get("name"),
        "state_type": (issue.get("state") or {}).get("type"),
        "team": issue.get("team") or {},
        "body": comment_body(repo, entry["number"], url),
        "pending": pending,
    }


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def mutate(api_key, query, variables, field):
    """Run one mutation and return whether Linear called it a success."""
    data = gql(api_key, query, variables)
    result = data.get(field)
    return bool(isinstance(result, dict) and result.get("success"))


def apply_issue(api_key, plan, state_for_team):
    """Make this issue's pending writes, in order, and report each outcome.

    A failed write does not abort the remaining two: they are independent
    statements, and the guards mean the next run retries exactly what is still
    missing.
    """
    done: list = []
    failed: list = []
    for write in WRITES:
        if write not in plan["pending"]:
            continue
        if write == "comment":
            ok = mutate(
                api_key,
                COMMENT_MUTATION,
                {"issue": plan["id"], "body": plan["body"]},
                "commentCreate",
            )
        elif write == "attachment":
            ok = mutate(
                api_key,
                ATTACHMENT_MUTATION,
                {
                    "issue": plan["id"],
                    "title": attachment_title(plan["number"]),
                    "url": plan["url"],
                },
                "attachmentCreate",
            )
        else:
            state = state_for_team(plan["team"])
            ok = mutate(
                api_key,
                STATE_MUTATION,
                {"id": plan["id"], "state": state["id"]},
                "issueUpdate",
            )
        (done if ok else failed).append(write)
    return done, failed


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render(summary, as_json):
    if as_json:
        print(json.dumps(summary, indent=2))
        return
    for row in summary["issues"]:
        if row["pending"]:
            verb = "would " if summary["dry_run"] else ""
            print(
                f"  {row['key']:<10} -> #{row['number']:<5} {verb}{', '.join(row['pending'])}"
            )
        else:
            print(f"  {row['key']:<10} -> #{row['number']:<5} up to date")
    for row in summary["issues"]:
        if row.get("failed"):
            print(f"  FAILED {row['key']}: {', '.join(row['failed'])}")
    if summary["unreadable"]:
        print("\nCould not read:")
        for key, why in summary["unreadable"]:
            print(f"  {key}: {why}")
    if summary["pending_import"]:
        print(
            "\nNot yet landed on GitHub (no successor to point at): "
            + ", ".join(summary["pending_import"])
        )
    counts = summary["counts"]
    if summary["dry_run"]:
        print(
            f"\nDRY RUN — nothing written. {counts['comment']} comment(s), "
            f"{counts['attachment']} attachment(s), {counts['state']} state "
            "change(s) outstanding. Re-run with --apply."
        )
    else:
        print(
            f"\nWrote {counts['comment']} comment(s), {counts['attachment']} "
            f"attachment(s), {counts['state']} state change(s); "
            f"{summary['failures']} failed."
        )


def run(api_key, mapping, cancel, apply_writes):
    repo = mapping["repo"]
    entries, pending_import = landed(mapping)
    cache: dict = {}

    def state_for_team(team):
        return canceled_state(api_key, team, cache)

    rows, unreadable = [], []
    counts = {write: 0 for write in WRITES}
    failures = 0

    for entry in entries:
        try:
            issue = fetch_issue(api_key, entry["key"])
            plan = plan_issue(issue, entry, repo, cancel)
        except SuccessorError as exc:
            unreadable.append((entry["key"], str(exc)))
            continue
        row = {
            "key": plan["key"],
            "number": plan["number"],
            "url": plan["url"],
            "state": plan["state"],
            "pending": list(plan["pending"]),
        }
        if apply_writes and plan["pending"]:
            try:
                done, failed = apply_issue(api_key, plan, state_for_team)
            except SuccessorError as exc:
                unreadable.append((plan["key"], str(exc)))
                continue
            row["done"] = done
            row["failed"] = failed
            for write in done:
                counts[write] += 1
            failures += len(failed)
        else:
            for write in plan["pending"]:
                counts[write] += 1
        rows.append(row)

    return {
        "repo": repo,
        "dry_run": not apply_writes,
        "cancel": cancel,
        "issues": rows,
        "unreadable": unreadable,
        "pending_import": pending_import,
        "counts": counts,
        "failures": failures,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Point migrated Linear issues at their GitHub successors."
    )
    ap.add_argument(
        "--mapping",
        required=True,
        help="The `linear-import.py` mapping file naming key -> GitHub number.",
    )
    ap.add_argument(
        "--cancel",
        action="store_true",
        help="Also move each issue to its team's canceled-type state. Separate "
        "from --apply so the state write can be reversed on its own.",
    )
    ap.add_argument("--apply", action="store_true", help="Write. Without it, DRY RUN.")
    ap.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    args = ap.parse_args(argv)

    try:
        mapping = load_mapping(args.mapping)
    except SuccessorError as exc:
        sys.exit(str(exc))

    api_key = get_key()
    try:
        summary = run(api_key, mapping, args.cancel, args.apply)
    except SuccessorError as exc:
        sys.exit(str(exc))

    render(summary, args.json)
    # An unreadable issue is a finding about the migration, not a hiccup: the
    # mapping says it landed and Linear cannot show it. Fail on that as loudly
    # as on a failed write.
    return 1 if summary["failures"] or summary["unreadable"] else 0


if __name__ == "__main__":
    sys.exit(main())
