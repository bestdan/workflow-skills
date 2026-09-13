#!/usr/bin/env python3
"""Turn the Linear export into a reviewable GitHub import plan.

`--plan` reads the export `linear-export.py` wrote, selects the live
workflow-skills issues, applies the Linear -> GitHub crosswalk, decides
create-versus-reopen for each one, and writes a diffable JSON plan. It performs
no GitHub write, which is the point: ~125 issues with labels, milestones,
parents and edges is a batch, and a batch nobody can read before it lands is a
batch nobody can check afterwards either. The plan file is what makes the
mapping reviewable per issue; `--apply` (a later task) executes it.

Read-only, but NOT offline. Reopen detection reads GitHub — one `gh issue view`
per candidate — so a cold run needs `gh auth` and network. Everything else is
computed from the export file.

THE SELECTION IS AN ENUMERATION, NEVER A PATTERN. Linear has no repo field, so
project name is the only proxy for "is this issue this repo's work", and it lies
in both directions: four of this repo's projects are not named for it, and
`Plugin data-ops enhancement` reads like plugin work while belonging to another
repo. A selector matching the name `workflow-skills` silently drops issues; one
matching "plugin-shaped" silently imports another project's. So the set arrives
as repeated `--project` names plus explicit `--issue` keys, resolved 2026-09-07
in the milestone-1 plan's open question 1 and validated against the export on
2026-09-13. A `--project` name the export does not contain, or an `--issue` key
that is absent or not live, is a refusal rather than an empty selection — a typo
in an enumeration must not read as "nothing matched". A selected project with
zero live issues is legal and reported: the token-cost-fix project is exactly
that.

THE CROSSWALK IS THE CONTRACT. It is stated in the milestone-1 plan ("Import the
live workflow-skills Linear issues into GitHub Issues", GitHub milestone 1) and
encoded here once:

  Linear state type `backlog`                        -> status:0_untriaged
  ... plus label `human-approval-requested`          -> status:1_needs_refinement
  Linear state type `unstarted`                      -> status:2_ready
  Linear state type `started`, not the review state  -> status:3_started
  Linear state type `started`, the review state      -> status:4_needs_review
  label `auto-eligible`                              -> auto:eligible
  no `auto-eligible`                                 -> auto:human-review-needed
  priority 1/2/3/4 (Urgent/High/Medium/Low)          -> prio:0/1/2/3
  priority 0 (None)                                  -> no `prio:` label
  estimate in labels.yml's est group                 -> est:<same value>
  estimate absent or off-vocabulary                  -> no `est:` label
  project                                            -> milestone by title
  parent                                             -> native sub-issue link
  relation `blocks`                                  -> native blocked_by edge
  relation `related`/`similar`/`duplicate`           -> a line in the body footer

Mind the collision that makes a mechanical mapping wrong: Linear priority `0`
means *none*, GitHub `prio:0` means *highest*. And Linear state NAMES are
configurable, so only the review row keys on a name (`--review-state`, default
`In Review`, refused if the export has no such state); every other row keys on
the state *type*.

The managed label set of every entry is validated with `gh-issue-state.py`'s own
`validate()` before the plan is written, and a single violation refuses the whole
file. That is the same check the write helper runs before it PATCHes, run here
where it is cheap: a raw REST write CREATES an unknown label rather than
rejecting it, so an `est:7` that reaches `--apply` enters the repo's namespace
silently.

Usage:
  python3 linear-import.py --plan --export <file> --repo bestdan/workflow-skills \\
      --project "workflow-skills backlog" \\
      --project "Auto-pilot mode — /deliver-task + /auto-pilot" \\
      --project "workflow-skills: Handler parity follow-ups" \\
      --project "Deterministic-script extraction" --project "reconcile-tasks" \\
      --project "Linear MCP token-cost fix — GraphQL fast-path for find-candidates" \\
      --project "reviewer-quality" \\
      --issue PRE-685 --issue PRE-815 \\
      --out <dir>/<date>-import-plan.json
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import DEFAULT_LABELS_FILE, expected_labels, load_vocabulary  # noqa: E402

ASSET_DIR = Path(__file__).resolve().parent

# Linear state types that count as live. Everything else (`completed`,
# `canceled`, `duplicate`) stays in Linear as a fact the ledger already cites —
# the same split the dotfiles migration used.
LIVE_STATE_TYPES = ("backlog", "unstarted", "started")

DEFAULT_REVIEW_STATE = "In Review"

# Linear's priority scale is 0=None, 1=Urgent .. 4=Low; GitHub's `prio:` runs
# 0=highest .. 3=lowest. The two zeroes mean opposite things, which is why this
# is a table and not arithmetic.
PRIORITY_TO_PRIO = {1: "0", 2: "1", 3: "2", 4: "3"}

# Linear labels the crosswalk CONSUMES: they decide a managed rung above, so
# carrying them as well would duplicate the same fact in two vocabularies.
CONSUMED_LABELS = ("auto-eligible", "human-approval-requested")

# Dropped on purpose: `status:3_started` already expresses "a session has this".
DROPPED_LABELS = ("auto-claimed",)

# Linear label -> the GitHub label to carry it as. Everything here lives OUTSIDE
# the four managed namespaces, so these ride alongside the rungs rather than
# competing with them.
CARRIED_LABELS = {
    "papercut": "papercut",
    "papercut-fix-now": "papercut-fix-now",
    "blocked": "blocked",
    "Bug": "bug",
    "Feature": "enhancement",
    "Improvement": "enhancement",
}

# Relation types with no GitHub equivalent. They become a footer line, never an
# edge — GitHub has `blocked_by` and sub-issues and nothing else.
FOOTER_RELATION_TYPES = ("related", "similar", "duplicate")

# Projects that are selected but get no milestone (milestone-1 plan open
# question 3, resolved 2026-09-07): the catch-all backlog, whose milestone would
# name every issue in the repo, and the token-cost-fix project, which has no live
# issues to group. Override with --no-milestone.
NO_MILESTONE_PROJECTS = (
    "workflow-skills backlog",
    "Linear MCP token-cost fix — GraphQL fast-path for find-candidates",
)

# An estimate this large does not fit one PR. The crosswalk still carries the
# `est:` label; the summary flags the issue for /break-down-task.
OVERSIZED_ESTIMATES = (8, 13)

# The two independent markers left on a Linear issue that was itself migrated
# OUT of GitHub Issues on 2026-08-08. Either one makes the issue a reopen
# candidate; both are checked because neither was written by this repo's code and
# an issue carrying only one of them is not a duplicate waiting to happen.
#
# The footer is matched anywhere in the description rather than only at its end,
# and through Linear's markdown link wrapping: the live export writes it as
# `Migrated from [https://…/issues/288](<https://…/issues/288>)` with a later
# sizing-flag block after it. An anchored, bare-URL pattern matches none of the
# eight real candidates, and a missed candidate is a duplicate issue rather than
# a loud failure.
MIGRATED_ATTACHMENT_RE = re.compile(r"^GitHub #(\d+) \(migrated\)$")
LINEAR_WORKSPACE_RE = re.compile(r"^https://linear\.app/([^/]+)/issue/")

# Linear's inline issue mention, as it appears in an exported description:
# `<issue id="…" href="https://linear.app/…/issue/PRE-5/…">PRE-5</issue>`. It is
# Linear's own markup, not markdown, so it renders as literal angle brackets on
# GitHub — or, worse, as an unknown HTML tag that the renderer swallows whole,
# taking the key with it.
ISSUE_MENTION_RE = re.compile(
    r"<issue\b(?P<attrs>[^>]*)>(?P<text>.*?)</issue>", re.DOTALL
)
HREF_RE = re.compile(r'href="([^"]+)"')


def rewrite_issue_mentions(text):
    """`<issue … href="URL">PRE-5</issue>` -> `PRE-5 (URL)`.

    The key is what has to survive: a later pass in the linking task rewrites
    keys that landed in this import to `#number`, and it can only find a key
    that is plain text. A mention with no href degrades to the bare key rather
    than to an empty string.

    The live 2026-09-13 export contains none of these — every mention in it is
    already a plain markdown link. The rewrite is kept because Linear writes this
    form when a mention is made in the UI, and the failure mode is a silently
    swallowed key rather than visible markup.
    """
    if not text:
        return text

    def replace(match):
        key = match.group("text").strip()
        href = HREF_RE.search(match.group("attrs") or "")
        return f"{key} ({href.group(1)})" if href else key

    return ISSUE_MENTION_RE.sub(replace, text)


def migrated_footer_re(repo):
    """Match a migrated-from footer that names THIS repo, capturing the number.

    Scoped to the target repo on purpose: a footer citing another repository's
    issue number is not an original to reopen here, and reopening by number
    alone would land on an unrelated issue.
    """
    return re.compile(
        r"Migrated from\s+\[?<?https://github\.com/"
        + re.escape(repo)
        + r"/issues/(\d+)>?\]?"
    )


class PlanError(Exception):
    """A refusal that must abort the plan rather than write a partial one.

    Raised rather than sys.exit'd so every guard is assertable from a hermetic
    test without catching SystemExit and re-reading the message; main() turns it
    into the exit.
    """


def load_validator():
    """`validate()` and `InvalidLabelSet` from gh-issue-state.py.

    One fact, one home: the invariants (exactly one `status:` and `auto:`, at
    most one `prio:` and `est:`, every name in labels.yml) are the write helper's,
    and a second copy here would drift silently. The filename has a dash, so it
    is not importable — hence importlib, the same way the tests load these assets.
    """
    path = ASSET_DIR / "gh-issue-state.py"
    spec = importlib.util.spec_from_file_location("gh_issue_state", path)
    if spec is None or spec.loader is None:
        raise PlanError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_export(path):
    with open(path, encoding="utf-8") as fh:
        document = json.load(fh)
    if not isinstance(document, dict) or not isinstance(document.get("issues"), list):
        raise PlanError(f"{path}: not an export document (no `issues` list)")
    return document


def workspace_slug(issues):
    """The Linear workspace slug, read off the issues' own URLs.

    The body footer cites the canonical short form
    `https://linear.app/<slug>/issue/<KEY>` rather than the export's slugged URL,
    and hardcoding the workspace here would make this asset wrong for any other
    one.
    """
    slugs = {
        match.group(1)
        for issue in issues
        if (match := LINEAR_WORKSPACE_RE.match(issue.get("url") or ""))
    }
    if not slugs:
        raise PlanError("no issue URL in the export names a Linear workspace")
    if len(slugs) > 1:
        raise PlanError(f"export spans several workspaces: {', '.join(sorted(slugs))}")
    return slugs.pop()


def is_live(issue):
    return ((issue.get("state") or {}).get("type")) in LIVE_STATE_TYPES


def project_name(issue):
    return (issue.get("project") or {}).get("name")


def select(issues, projects, keys):
    """The live issues named by the enumeration, in export order.

    Refuses a project name the export does not carry and a key that is absent or
    terminal: both are caller errors, and both would otherwise shrink the import
    silently. A selected project with no LIVE issues is fine and not reported
    here — the summary counts it.
    """
    by_key = {issue.get("identifier"): issue for issue in issues}
    known_projects = {project_name(issue) for issue in issues} - {None}

    unknown = [name for name in projects if name not in known_projects]
    if unknown:
        raise PlanError(
            "no project in the export is named: " + "; ".join(sorted(unknown))
        )

    absent = [key for key in keys if key not in by_key]
    if absent:
        raise PlanError("not in the export: " + ", ".join(sorted(absent)))
    terminal = [key for key in keys if not is_live(by_key[key])]
    if terminal:
        raise PlanError(
            "named with --issue but not live: "
            + ", ".join(
                f"{key} ({by_key[key]['state']['name']})" for key in sorted(terminal)
            )
        )

    wanted = set(projects)
    return [
        issue
        for issue in issues
        if is_live(issue)
        and (project_name(issue) in wanted or issue.get("identifier") in keys)
    ]


def resolve_review_state(issues, review_state):
    """Assert the team really has the state the review row keys on.

    Every other crosswalk row reads a state TYPE, which Linear fixes. This one
    reads a NAME, which the team can rename — and a rename would quietly route
    every in-review issue to `status:3_started`, which reads as healthy. So the
    name is checked against the states the export actually contains.
    """
    started = {
        name
        for issue in issues
        if ((issue.get("state") or {}).get("type")) == "started"
        and (name := (issue.get("state") or {}).get("name"))
    }
    if review_state not in started:
        raise PlanError(
            f"no `started` state named {review_state!r} in the export "
            f"(found: {', '.join(sorted(started)) or 'none'}) — "
            "pass --review-state with the team's own name"
        )
    return review_state


def status_label(issue, review_state):
    state = issue.get("state") or {}
    state_type, name = state.get("type"), state.get("name")
    if state_type == "backlog":
        if "human-approval-requested" in (issue.get("labels") or []):
            return "status:1_needs_refinement"
        return "status:0_untriaged"
    if state_type == "unstarted":
        return "status:2_ready"
    if state_type == "started":
        return "status:4_needs_review" if name == review_state else "status:3_started"
    raise PlanError(
        f"{issue.get('identifier')}: state type {state_type!r} has no crosswalk row"
    )


def estimate_label(issue, est_values):
    """`est:<n>` only for an estimate labels.yml actually defines.

    Linear's estimate is a number, not an enum — it arrives as a float on some
    rows and can hold a value (7) the vocabulary has no label for. Emitting one
    anyway would put a brand-new label into the repo on the first REST write.
    """
    estimate = issue.get("estimate")
    if estimate is None:
        return None
    try:
        value = int(estimate)
    except (TypeError, ValueError):
        return None
    if value != estimate or str(value) not in est_values:
        return None
    return f"est:{value}"


def managed_labels(issue, review_state, est_values):
    """The four-namespace set, in the crosswalk's own order."""
    labels = [status_label(issue, review_state)]
    if "auto-eligible" in (issue.get("labels") or []):
        labels.append("auto:eligible")
    else:
        labels.append("auto:human-review-needed")
    prio = PRIORITY_TO_PRIO.get(issue.get("priority"))
    if prio is not None:
        labels.append(f"prio:{prio}")
    est = estimate_label(issue, est_values)
    if est:
        labels.append(est)
    return labels


def carried_labels(issue, managed_groups):
    """(labels to carry, labels dropped as off-crosswalk).

    A Linear label sitting INSIDE a managed namespace is a refusal, not a
    carry: the crosswalk owns those four namespaces, and carrying a hand-typed
    `status:3_started` alongside the rung the crosswalk computed would produce a
    set with two `status:` labels — which `validate()` rejects at the far end of
    the plan, where the cause is much harder to read.
    """
    carried, dropped = [], []
    for label in issue.get("labels") or []:
        if label in CONSUMED_LABELS or label in DROPPED_LABELS:
            continue
        if any(label.startswith(f"{group}:") for group in managed_groups):
            raise PlanError(
                f"{issue.get('identifier')}: Linear label {label!r} sits in the "
                "managed namespace the crosswalk owns — it would collide with "
                "the computed rung"
            )
        mapped = CARRIED_LABELS.get(label)
        if mapped is None:
            dropped.append(label)
        elif mapped not in carried:
            carried.append(mapped)
    return carried, dropped


def key_number(key):
    """Sort key for a Linear identifier. An identifier is not an index — this
    orders footer lines readably and is never used to count anything."""
    match = re.search(r"(\d+)$", key or "")
    return (key.split("-")[0] if key else "", int(match.group(1)) if match else 0)


def related_keys(issue):
    out = []
    for entry in (issue.get("relations") or []) + (issue.get("inverseRelations") or []):
        if entry.get("type") in FOOTER_RELATION_TYPES:
            key = entry.get("identifier")
            if key and key not in out:
                out.append(key)
    return sorted(out, key=key_number)


def blocker_keys(issue):
    """The keys that block this issue.

    Direction: `inverseRelations` is what points AT this issue, so a `blocks`
    entry there means the other issue blocks this one. `relations` holds the
    mirror (this issue blocks that one) and is read from the other end, which is
    why it is not consulted here — reading both would double every edge and
    inverting one would write a real dependency backwards.
    """
    out = []
    for entry in issue.get("inverseRelations") or []:
        if entry.get("type") == "blocks":
            key = entry.get("identifier")
            if key and key not in out:
                out.append(key)
    return sorted(out, key=key_number)


def body(issue, slug, date, related, dropped_blockers):
    """Description plus the provenance footer.

    The footer carries what GitHub has no field for: where the issue came from,
    and the relations that are not native there. `Blockers not migrated` is
    deliberately NOT spelled `Blocked by:` — that spelling is an echo of a
    native edge, and `/reoptimize-tasks` reads it as a dependency claim. These
    blockers have no edge and never will, because the issue they name is not
    being imported, so echoing them in that spelling would mint a dependency
    nothing can satisfy.
    """
    key = issue.get("identifier")
    lines = [
        "---",
        f"Migrated from Linear {key} (https://linear.app/{slug}/issue/{key}) "
        f"on {date}.",
    ]
    if related:
        lines.append(
            f"Related: {', '.join(related)} (relations of type "
            "related/similar are not native on GitHub)."
        )
    if dropped_blockers:
        lines.append(
            f"Blockers not migrated: {', '.join(dropped_blockers)} "
            "(not in the import selection, so no native edge exists)."
        )
    description = rewrite_issue_mentions(issue.get("description") or "").rstrip()
    footer = "\n".join(lines)
    return f"{description}\n\n{footer}\n" if description else f"{footer}\n"


def comments(issue):
    return [
        {
            "author": (entry.get("user") or {}).get("name"),
            "at": entry.get("createdAt"),
            "body": rewrite_issue_mentions(entry.get("body")),
        }
        for entry in issue.get("comments") or []
    ]


def migrated_number(issue, footer_re):
    """The GitHub issue this Linear issue was migrated out of, or None.

    Both markers are read, and a disagreement refuses: two different numbers
    means the markers no longer describe one original, and picking either would
    reopen the wrong issue.
    """
    numbers = []
    for attachment in issue.get("attachments") or []:
        match = MIGRATED_ATTACHMENT_RE.match(attachment.get("title") or "")
        if match:
            numbers.append(int(match.group(1)))
    match = footer_re.search(issue.get("description") or "")
    if match:
        numbers.append(int(match.group(1)))
    if not numbers:
        return None
    if len(set(numbers)) > 1:
        raise PlanError(
            f"{issue.get('identifier')}: migrated-from markers disagree "
            f"({', '.join(str(n) for n in sorted(set(numbers)))})"
        )
    return numbers[0]


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def github_issue_state(repo, number):
    """`OPEN`/`CLOSED` for an existing issue; None when it cannot be read."""
    code, out, _ = run_gh(
        ["issue", "view", str(number), "--repo", repo, "--json", "number,state"]
    )
    if code != 0:
        return None
    try:
        payload = json.loads(out or "{}")
    except json.JSONDecodeError:
        return None
    state = payload.get("state")
    return state.upper() if isinstance(state, str) else None


def resolve_actions(selected, repo, footer_re, reader=None):
    """Decide create-versus-reopen for every selected issue.

    Reopening keeps the number that branch names and PR bodies already cite,
    which is the whole reason these are not recreated. The candidate's original
    is verified before the plan trusts it, and the two failure modes are
    reported TOGETHER rather than one per run: an original that cannot be read
    is unverifiable, and one that is still OPEN means two live homes already
    exist, which is a state no import may deepen.
    """
    reader = reader or github_issue_state
    actions, problems = {}, []
    for issue in selected:
        key = issue.get("identifier")
        number = migrated_number(issue, footer_re)
        if number is None:
            actions[key] = {"action": "create", "number": None}
            continue
        state = reader(repo, number)
        if state is None:
            problems.append(f"{key}: cannot read {repo}#{number}")
        elif state == "OPEN":
            problems.append(
                f"{key}: {repo}#{number} is OPEN — two live homes already exist"
            )
        else:
            actions[key] = {"action": "reopen", "number": number}
    if problems:
        raise PlanError(
            "refusing to write the plan; reopen targets are not importable:\n  "
            + "\n  ".join(problems)
        )
    return actions


def build_entries(selected, options):
    """One plan entry per selected issue, validated as it is built."""
    validator = options["validator"]
    vocabulary = options["vocabulary"]
    managed_groups = options["managed_groups"]
    selected_keys = {issue.get("identifier") for issue in selected}

    entries, violations = [], []
    for issue in selected:
        key = issue.get("identifier")
        managed = managed_labels(issue, options["review_state"], options["est_values"])
        try:
            validator.validate(managed, vocabulary)
        except validator.InvalidLabelSet as exc:
            violations.append(f"{key}: {exc}")
            continue

        carried, dropped_labels = carried_labels(issue, managed_groups)
        blockers = blocker_keys(issue)
        kept_blockers = [k for k in blockers if k in selected_keys]
        dropped_blockers = [k for k in blockers if k not in selected_keys]
        related = related_keys(issue)
        parent = issue.get("parent")
        dropped_parent = parent if parent and parent not in selected_keys else None

        project = project_name(issue)
        milestone = (
            project if project and project not in options["no_milestone"] else None
        )

        assignee = None
        assignee_mismatch = None
        linear_assignee = issue.get("assignee") or {}
        identity = {
            value
            for value in (linear_assignee.get("name"), linear_assignee.get("email"))
            if value
        }
        if (issue.get("state") or {}).get("type") == "started" and identity:
            if options["viewer"] and options["viewer"] in identity:
                assignee = "@me"
            else:
                assignee_mismatch = sorted(identity)[0]

        action = options["actions"][key]
        entries.append(
            {
                "key": key,
                "action": action["action"],
                "number": action["number"],
                "title": issue.get("title"),
                "body": body(
                    issue,
                    options["slug"],
                    options["date"],
                    related,
                    dropped_blockers,
                ),
                "managed_labels": managed,
                "carried_labels": carried,
                "dropped_labels": dropped_labels,
                "milestone": milestone,
                "assignee": assignee,
                "assignee_mismatch": assignee_mismatch,
                "blocked_by": kept_blockers,
                "dropped_blockers": dropped_blockers,
                "parent": parent if parent in selected_keys else None,
                "dropped_parent": dropped_parent,
                "comments": comments(issue),
                "related": related,
                "linear": {
                    "project": project,
                    "state": (issue.get("state") or {}).get("name"),
                    "priority": issue.get("priority"),
                    "estimate": issue.get("estimate"),
                    "url": issue.get("url"),
                },
            }
        )

    if violations:
        raise PlanError(
            "refusing to write the plan; the crosswalk produced an unwritable "
            "label set:\n  " + "\n  ".join(violations)
        )
    return entries


def summarize(document, selected, projects):
    """The counts a human needs to decide whether the plan is right.

    Deliberately includes the categories whose value is a ZERO: a selected
    project with no live issues, and blockers dropped at the selection edge. A
    zero there is information (the token-cost-fix project really is empty), and
    a category that only appears when non-empty cannot be read as reassurance.
    """
    entries = document["entries"]

    def tally(values):
        counts: dict = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: str(kv[0])))

    live_by_project = tally(project_name(issue) for issue in selected)
    return {
        "entries": len(entries),
        "by_action": tally(entry["action"] for entry in entries),
        "by_status": tally(entry["managed_labels"][0] for entry in entries),
        "by_milestone": tally(entry["milestone"] for entry in entries),
        "by_project": {name: live_by_project.get(name, 0) for name in projects},
        "empty_projects": sorted(
            name for name in projects if not live_by_project.get(name)
        ),
        "reopen_targets": {
            entry["key"]: entry["number"]
            for entry in entries
            if entry["action"] == "reopen"
        },
        "blockers_outside_selection": {
            entry["key"]: entry["dropped_blockers"]
            for entry in entries
            if entry["dropped_blockers"]
        },
        "parents_outside_selection": {
            entry["key"]: entry["dropped_parent"]
            for entry in entries
            if entry["dropped_parent"]
        },
        "oversized": {
            entry["key"]: entry["linear"]["estimate"]
            for entry in entries
            if entry["linear"]["estimate"] in OVERSIZED_ESTIMATES
        },
        "assignee_mismatches": {
            entry["key"]: entry["assignee_mismatch"]
            for entry in entries
            if entry["assignee_mismatch"]
        },
        "dropped_labels": tally(
            label for entry in entries for label in entry["dropped_labels"]
        ),
        "with_comments": sum(1 for entry in entries if entry["comments"]),
        "edges": sum(len(entry["blocked_by"]) for entry in entries),
        "sub_issues": sum(1 for entry in entries if entry["parent"]),
    }


def build_plan(export, args, validator, vocabulary, groups, reader=None):
    """The whole plan document. Raises PlanError rather than writing anything."""
    issues = export["issues"]
    selected = select(issues, args.project, args.issue)
    review_state = resolve_review_state(issues, args.review_state)
    footer_re = migrated_footer_re(args.repo)

    document = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repo": args.repo,
        "export": {
            "path": str(args.export),
            "exported_at": export.get("exported_at"),
            "issue_count": export.get("issue_count"),
            "team": export.get("team"),
        },
        "selection": {
            "projects": list(args.project),
            "issues": list(args.issue),
            "live_state_types": list(LIVE_STATE_TYPES),
            "no_milestone_projects": list(args.no_milestone),
            "review_state": review_state,
            "selected": len(selected),
        },
        "entries": build_entries(
            selected,
            {
                "validator": validator,
                "vocabulary": vocabulary,
                "managed_groups": set(groups),
                "est_values": set(groups["est"]),
                "review_state": review_state,
                "no_milestone": set(args.no_milestone),
                "viewer": args.viewer,
                "slug": workspace_slug(issues),
                "date": args.date,
                "actions": resolve_actions(selected, args.repo, footer_re, reader),
            },
        ),
    }
    document["milestones"] = sorted(
        {entry["milestone"] for entry in document["entries"]} - {None}
    )
    document["summary"] = summarize(document, selected, args.project)
    return document


def write_plan(document, path, force):
    if os.path.exists(path) and not force:
        raise PlanError(f"refusing to overwrite {path} — pass --force to replace it.")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    # Write-then-rename: a crash mid-write must not leave a half-written plan
    # where --apply would read it as the whole mapping.
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def print_summary(document, path):
    summary = document["summary"]
    print(f"Import plan for {document['repo']} -> {path}")
    print(f"  {summary['entries']} issue(s) selected from {document['export']['path']}")
    print(
        "  actions: "
        + ", ".join(f"{k}={v}" for k, v in summary["by_action"].items())
        + f"   edges={summary['edges']}  sub-issues={summary['sub_issues']}"
        + f"  with_comments={summary['with_comments']}"
    )
    print("  status rungs:")
    for rung, count in summary["by_status"].items():
        print(f"    {rung}: {count}")
    print("  milestones:")
    for milestone, count in summary["by_milestone"].items():
        print(f"    {milestone or '(none)'}: {count}")
    print("  live issues per selected project:")
    for name, count in summary["by_project"].items():
        print(f"    {count:>4}  {name}")
    if summary["empty_projects"]:
        print(
            "  selected but contributing nothing: "
            + "; ".join(summary["empty_projects"])
        )
    if summary["reopen_targets"]:
        print(
            "  reopen: "
            + ", ".join(f"{k}->#{v}" for k, v in summary["reopen_targets"].items())
        )
    if summary["oversized"]:
        print(
            "  over the size-5 ceiling (run /break-down-task): "
            + ", ".join(f"{k}={v}" for k, v in summary["oversized"].items())
        )
    print(
        "  blockers outside the selection (edge dropped, footer instead): "
        + (
            ", ".join(
                f"{k}<-{'+'.join(v)}"
                for k, v in summary["blockers_outside_selection"].items()
            )
            or "none"
        )
    )
    if summary["parents_outside_selection"]:
        print(
            "  parents outside the selection (sub-issue link dropped): "
            + ", ".join(
                f"{k}->{v}" for k, v in summary["parents_outside_selection"].items()
            )
        )
    if summary["assignee_mismatches"]:
        print(
            "  started but not assigned to the viewer (no assignee written): "
            + ", ".join(f"{k}={v}" for k, v in summary["assignee_mismatches"].items())
        )
    if summary["dropped_labels"]:
        print(
            "  Linear labels dropped (no crosswalk row): "
            + ", ".join(f"{k}={v}" for k, v in summary["dropped_labels"].items())
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--plan",
        action="store_true",
        required=True,
        help="build the import plan (the only mode this asset has today)",
    )
    ap.add_argument("--export", required=True, help="the linear-export.py JSON file")
    ap.add_argument("--repo", required=True, help="owner/name the import targets")
    ap.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="NAME",
        help="a Linear project to select, by exact name; repeatable",
    )
    ap.add_argument(
        "--issue",
        action="append",
        default=[],
        metavar="KEY",
        help="an extra Linear issue to select regardless of project; repeatable",
    )
    ap.add_argument("--out", required=True, help="where to write the plan JSON")
    ap.add_argument(
        "--review-state",
        default=DEFAULT_REVIEW_STATE,
        help=f"the team's review state NAME (default {DEFAULT_REVIEW_STATE!r})",
    )
    ap.add_argument(
        "--viewer",
        help=(
            "the Linear assignee name or email that counts as you; a `started` "
            "issue assigned to them gets assignee @me. Without this no assignee "
            "is written and each mismatch is listed in the summary"
        ),
    )
    ap.add_argument(
        "--no-milestone",
        action="append",
        metavar="NAME",
        help=(
            "a selected project that gets no milestone; repeatable. Defaults to "
            + "; ".join(NO_MILESTONE_PROJECTS)
        ),
    )
    ap.add_argument(
        "--date",
        help="the migration date in the body footer (default: today, UTC)",
    )
    ap.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    ap.add_argument(
        "--force", action="store_true", help="overwrite an existing plan file"
    )
    args = ap.parse_args(argv)

    if not args.project and not args.issue:
        ap.error("select something: --project and/or --issue")
    if args.no_milestone is None:
        args.no_milestone = list(NO_MILESTONE_PROJECTS)
    if not args.date:
        args.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    groups, colors = load_vocabulary(args.labels_file)
    vocabulary = expected_labels(groups, colors)

    try:
        validator = load_validator()
        export = load_export(args.export)
        document = build_plan(export, args, validator, vocabulary, groups)
        write_plan(document, args.out, args.force)
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"path": args.out, **document["summary"]}, indent=2))
    else:
        print_summary(document, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
