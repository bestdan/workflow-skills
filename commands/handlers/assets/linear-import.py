#!/usr/bin/env python3
"""Turn the Linear export into a reviewable GitHub import plan.

`--plan` reads the export `linear-export.py` wrote, selects the live
workflow-skills issues, applies the Linear -> GitHub crosswalk, decides
create-versus-reopen for each one, and writes a diffable JSON plan. It performs
no GitHub write, which is the point: ~125 issues with labels, milestones,
parents and edges is a batch, and a batch nobody can read before it lands is a
batch nobody can check afterwards either. The plan file is what makes the
mapping reviewable per issue; `--apply` executes it.

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

`--apply` lands that plan. It EXECUTES the plan and never re-derives it: the
selection and the crosswalk are arguments settled in `--plan`, and re-deriving
them here is how the "project name is a lying proxy" bug returns. It needs no
export at all.

  python3 linear-import.py --apply \\
      --plan-file <dir>/<date>-import-plan.json \\
      --mapping <dir>/<date>-mapping.json [--limit 3] [--only PRE-746] [--sleep 2]

Two things make it a batch rather than a loop of prose steps.

RESUMABILITY. GitHub's content-creation secondary limit is well under 125 issues
plus labels plus comments in one burst, and any run can die mid-way. So each
issue passes through four phases (create/reopen, labels, comments, done) and
each phase's record is on disk BEFORE the next one starts — a rerun resumes at
the exact phase that died, not at the issue. The one failure that outruns the
mapping is a lost response: the write landed, the record did not. Two markers
cover it — a body-footer search at run start recovers a lost create, and the
comment's own first-line marker makes the transcript post idempotent.

THE LABEL WRITE IS A SECOND CALL. `gh issue create --label` reaches the board
with a name nothing validated, and a raw REST write CREATES an unknown label
rather than rejecting it (`gh-issue-state.py:18-25`). So the create carries only
title, body, milestone and assignee, and every label goes through
`gh-issue-state.py`, which validates against labels.yml before any network call
and PATCHes the complete set once.

Every write here needs the sandbox escape: `sandbox-network-guard` blocks
non-GET `gh api`, and 125 issues is a few hundred such writes.

`--show` reads a plan back, printing each named entry beside its Linear original.
That is how a person checks the plan: the file is 125 entries of JSON, and the
question asked of it is not whether it parses but whether the crosswalk did the
right thing to a known issue — which needs both rows in view at once.

  python3 linear-import.py --show PRE-746 --show PRE-555 --show PRE-416 \\
      --plan-file <dir>/<date>-import-plan.json --export <file>
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
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

# What the summary calls the bucket of entries with no milestone. On GitHub that
# absence already means the catch-all, so it needs a name a reader recognises
# rather than a null.
NO_MILESTONE_LABEL = "(none)"

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


def migrated_markers(repo):
    """The (footer, attachment-url) patterns identifying an original in THIS repo.

    Both are scoped to the target repo, and the symmetry is the point. A footer
    citing another repository's issue number is not an original to reopen here —
    and neither is an attachment, but an attachment **title** carries no
    repository identity at all, so a `GitHub #288 (migrated)` left by some other
    repo's migration would otherwise reopen #288 here. The export records each
    attachment's `url` beside its title, so the attachment path can run the same
    check the footer path does.
    """
    issue_url = r"https://github\.com/" + re.escape(repo) + r"/issues/(\d+)"
    return (
        re.compile(r"Migrated from\s+\[?<?" + issue_url + r">?\]?"),
        re.compile(issue_url + r"/?$"),
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


def resolve_review_state(issues, selected, review_state):
    """Assert the team really has the state the review row keys on.

    Every other crosswalk row reads a state TYPE, which Linear fixes. This one
    reads a NAME, which the team can rename — and a rename would quietly route
    every in-review issue to `status:3_started`, which reads as healthy. So the
    name is checked against the states the export actually contains.

    The export carries no state catalogue (`linear-export.py` exports
    `team { id key name }`), so existence is inferred from the states issues
    occupy — across the WHOLE export, not the selection, since a team-wide state
    is what the name has to exist in. The check only fires when at least one
    SELECTED issue is `started`: with none, the review row cannot apply to any
    entry, so a refusal there would be a false alarm about a row that is not
    being used. That is the only case the gate changes — a review column that
    empties while other started issues remain still fails to match the name, and
    still refuses.
    """
    started = {
        name
        for issue in issues
        if ((issue.get("state") or {}).get("type")) == "started"
        and (name := (issue.get("state") or {}).get("name"))
    }
    any_started = any(
        ((issue.get("state") or {}).get("type")) == "started" for issue in selected
    )
    if any_started and review_state not in started:
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
            "related/similar/duplicate are not native on GitHub)."
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


def migrated_number(issue, markers):
    """The GitHub issue this Linear issue was migrated out of, or None.

    Every marker found is read, and a disagreement refuses: two different
    numbers means the markers no longer describe one original, and picking
    either would reopen the wrong issue. An attachment contributes both its
    title's number and its url's, so a marker that disagrees with itself trips
    that same refusal instead of being resolved arbitrarily.
    """
    footer_re, url_re = markers
    numbers = []
    for attachment in issue.get("attachments") or []:
        title = MIGRATED_ATTACHMENT_RE.match(attachment.get("title") or "")
        if not title:
            continue
        # The url is what says whether this marker is about the target repo at
        # all. One naming a different repo — or absent, which is
        # indistinguishable from that — leaves the issue to be created fresh,
        # because its original does not live here.
        url = url_re.search((attachment.get("url") or "").strip())
        if not url:
            continue
        numbers.append(int(title.group(1)))
        numbers.append(int(url.group(1)))
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


def resolve_actions(selected, repo, markers, reader=None):
    """Decide create-versus-reopen for every selected issue.

    Reopening keeps the number that branch names and PR bodies already cite,
    which is the whole reason these are not recreated. The candidate's original
    is verified before the plan trusts it, and the three failure modes are
    reported TOGETHER rather than one per run: an original that cannot be read
    is unverifiable; one that is still OPEN means two live homes already exist,
    which is a state no import may deepen; and two Linear issues claiming the
    same number would have `--apply` reopen one issue twice and leave the other
    with no home at all — the only one of the three that is silent rather than
    merely wrong.
    """
    reader = reader or github_issue_state
    actions, problems = {}, []
    claimed: dict = {}
    for issue in selected:
        key = issue.get("identifier")
        number = migrated_number(issue, markers)
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
        elif number in claimed:
            problems.append(
                f"{key}: {repo}#{number} is already claimed by {claimed[number]} — "
                "two Linear issues cannot reopen one GitHub issue"
            )
        else:
            claimed[number] = key
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
        # Named rather than left as None: json.dump would emit that key as the
        # string "null", while the printed summary calls the same bucket
        # "(none)" — one bucket with two names across the two renderings of one
        # plan. The entries themselves keep `milestone: null`, which is the
        # field --apply reads.
        "by_milestone": tally(
            entry["milestone"] or NO_MILESTONE_LABEL for entry in entries
        ),
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
    review_state = resolve_review_state(issues, selected, args.review_state)
    markers = migrated_markers(args.repo)

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
                "actions": resolve_actions(selected, args.repo, markers, reader),
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
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
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
        print(f"    {milestone}: {count}")
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


def load_plan(path):
    with open(path, encoding="utf-8") as fh:
        document = json.load(fh)
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        raise PlanError(f"{path}: not an import plan (no `entries` list)")
    return document


def dash(value):
    """`-` for an absent value, so a blank never reads as an oversight."""
    if value is None or value == [] or value == "":
        return "-"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def show_lines(plan, export, keys):
    """Each named entry rendered beside its Linear original.

    This is what makes the plan checkable by a person. The file is 125 entries of
    JSON and the question asked of it is not "is it well-formed" but "did the
    crosswalk do the right thing to THIS issue" — which means holding the Linear
    row and the GitHub row side by side. Reading them out of two multi-megabyte
    files by hand means carrying the crosswalk in your head while scrolling; the
    comparison printed together IS the check.

    Refuses on a key the plan does not carry, naming it: silence there would read
    as "nothing to say about that issue", when it means the issue was never
    selected.
    """
    entries = {entry["key"]: entry for entry in plan["entries"]}
    originals = {issue.get("identifier"): issue for issue in export["issues"]}

    absent = [key for key in keys if key not in entries]
    if absent:
        raise PlanError(
            "not in the plan (so not selected for import): " + ", ".join(absent)
        )

    lines = []
    for key in keys:
        entry, origin = entries[key], originals.get(key, {})
        state = origin.get("state") or {}
        # Direction is shown, not flattened: `-> blocks X` means this issue
        # blocks X, `<- blocks X` means X blocks this one, and only the second
        # becomes a `blocked_by` edge. A merged list would hide the one thing a
        # reader is checking.
        relations = [
            f"-> {r['type']} {r['identifier']}" for r in origin.get("relations") or []
        ] + [
            f"<- {r['type']} {r['identifier']}"
            for r in origin.get("inverseRelations") or []
        ]
        lines += [
            "=" * 78,
            f"{key}  {origin.get('title', '')}",
            "-" * 78,
            f"  Linear  {state.get('name')} ({state.get('type')})"
            f"   priority {dash(origin.get('priority'))}"
            f"   estimate {dash(origin.get('estimate'))}",
            f"          project: {dash(project_name(origin))}",
            f"          labels: {dash(origin.get('labels'))}",
            f"          parent: {dash(origin.get('parent'))}"
            f"   assignee: {dash((origin.get('assignee') or {}).get('email'))}",
            f"          relations: {dash(relations)}",
            f"          attachments: "
            f"{dash([a.get('title') for a in origin.get('attachments') or []])}",
            "",
            f"  GitHub  {entry['action']}"
            + (f" -> #{entry['number']}" if entry["number"] else ""),
            f"          labels: "
            f"{dash(entry['managed_labels'] + entry['carried_labels'])}",
            f"          milestone: {dash(entry['milestone'])}"
            f"   assignee: {dash(entry['assignee'])}",
            f"          blocked_by: {dash(entry['blocked_by'])}"
            f"   parent: {dash(entry['parent'])}"
            f"   related: {dash(entry['related'])}",
            f"          comments: {len(entry['comments'])}",
            "",
            "  Footer",
        ]
        # The LAST rule, not the first: a Linear description may carry its own
        # `---`, and starting there would print half the body as the footer.
        footer = entry["body"].rstrip().splitlines()
        start = len(footer) - footer[::-1].index("---") - 1 if "---" in footer else 0
        lines += [f"          {line}" for line in footer[start:]]
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# --apply: land the plan on GitHub
# ---------------------------------------------------------------------------

# The phases one entry passes through, in order. The mapping records the last
# phase that COMPLETED, so a rerun resumes at the first one that did not. Each
# phase is a separate network write and none is idempotent by itself, which is
# why the resume point is per-phase rather than per-issue: a run that died after
# the create must not create again, and one that died after the comment must not
# post it twice.
APPLY_PHASES = ("created", "labelled", "commented", "done")

# The consolidated comment's first line. It is what makes the post idempotent
# against a LOST RESPONSE — the one failure the mapping cannot cover, because
# the write landed and the record did not. A rerun reads the issue's comments
# and recognises its own work.
COMMENT_MARKER = "<!-- linear-import: comments {key} -->"

# The footer marker `--plan` writes into every body, read back the other way.
# Recovering a lost create means finding the issue by the only thing on it that
# names the Linear key.
BODY_MARKER_RE = re.compile(r"Migrated from Linear ([A-Z][A-Z0-9]*-\d+)\b")
BODY_MARKER_SEARCH = 'in:body "Migrated from Linear"'

# How GitHub says the content-creation secondary limit has been hit. It is an
# HTTP 403 with a message, not a 429, so the message is the ONLY thing that
# separates it from a permissions failure — and a permissions failure must not
# be retried, because the retry cannot succeed and the batch should stop where
# a human can see it.
RATE_LIMIT_MARKERS = (
    "secondary rate limit",
    "abuse detection",
    "rate limit exceeded",
    "api rate limit",
)
RETRY_AFTER_RE = re.compile(r"retry[-_ ]?after[\"'\s:]+(\d+)", re.IGNORECASE)
DEFAULT_RETRY_WAIT = 60

# Asked for as one bound on the recovery search. It is far above the 125-entry
# plan on purpose: a truncated search would report a landed issue as absent and
# create it a second time, so overflow has to be visible rather than plausible.
RECOVERY_SEARCH_LIMIT = 500


class ApplyError(Exception):
    """A refusal that stops the batch where a human can see it.

    Distinct from PlanError because the two failures are recovered differently:
    a plan refusal means nothing was written and the plan can be regenerated,
    while an apply refusal means some issues have landed and the mapping file is
    the record of which. Every ApplyError that stops the loop therefore names
    the key the rerun resumes at.
    """


def nap(seconds):
    """The throttle, as a seam. Tests replace this rather than wait."""
    if seconds > 0:
        time.sleep(seconds)


def run_state_helper(args):
    """Run gh-issue-state.py and return (returncode, stdout, stderr).

    The second seam the tests stub, beside run_gh. The label write is not a `gh`
    call because it must not be one: `gh issue edit --add-label` is neither
    atomic nor validating, and a raw REST write CREATES an unknown label. That
    helper is the only supported writer, and calling it as a subprocess rather
    than importing it keeps its argv contract — which gh-issue-claim.md and the
    promote and complete flows also depend on — the thing under test.
    """
    proc = subprocess.run(
        [sys.executable, str(ASSET_DIR / "gh-issue-state.py"), *args],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def gh(args, what):
    """One `gh` call, retried once on a secondary rate limit, else a refusal.

    The retry is deliberately single and deliberately narrow. Single, because a
    loop against a limit that is still tightening turns one stall into an
    unbounded one, and the mapping already makes a rerun cheap. Narrow, because
    every other 403 — a missing scope, a label the board does not have, an issue
    somebody deleted — cannot be fixed by waiting, and retrying it only doubles
    the delay before a human reads the error.
    """
    code, out, err = run_gh(args)
    if code == 0:
        return out
    blob = f"{out}\n{err}"
    if any(marker in blob.lower() for marker in RATE_LIMIT_MARKERS):
        match = RETRY_AFTER_RE.search(blob)
        wait = int(match.group(1)) if match else DEFAULT_RETRY_WAIT
        print(f"  rate limited; waiting {wait}s before one retry", flush=True)
        nap(wait)
        code, out, err = run_gh(args)
        if code == 0:
            return out
        blob = f"{out}\n{err}"
    raise ApplyError(f"{what} failed (gh exited {code}): {blob.strip()}")


def gh_json(args, what, default=None):
    """`gh` returning parsed JSON. Unreadable output is a refusal, never a None:
    a write path must not continue on a response it could not read."""
    out = gh(args, what)
    try:
        return json.loads(out or json.dumps(default))
    except json.JSONDecodeError as exc:
        raise ApplyError(f"{what}: cannot read gh's JSON output ({exc})")


def load_label_sync():
    """`existing_labels` from gh-label-sync.py, wired to THIS module's seam.

    One fact, one home: the 500-label cap and its refuse-rather-than-truncate
    rule are that file's, and a second copy here would drift. Rebinding its
    run_gh to ours is what keeps the pre-flight hermetic in the tests — and what
    keeps the seam one seam rather than two.
    """
    path = ASSET_DIR / "gh-label-sync.py"
    spec = importlib.util.spec_from_file_location("gh_label_sync", path)
    if spec is None or spec.loader is None:
        raise ApplyError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    setattr(module, "run_gh", run_gh)
    return module


def check_carried_labels(repo, entries):
    """Refuse up front on a carried label the board does not have.

    `gh issue edit --add-label` rejects a name the repo lacks, which is the right
    behaviour at the wrong TIME: discovered at entry 87 it leaves a half-landed
    import. `blocked` is exactly this case — it is in the crosswalk and is not
    provisioned here — so the whole carried set is checked against one label read
    before anything is written. The managed set needs no such check: the helper
    validates it against labels.yml, and task 17 provisioned all four namespaces.
    """
    wanted = sorted({label for entry in entries for label in entry["carried_labels"]})
    if not wanted:
        return
    try:
        present = load_label_sync().existing_labels(repo)
    except SystemExit as exc:
        raise ApplyError(f"reading {repo}'s labels failed: {exc}")
    missing = [label for label in wanted if label not in present]
    if missing:
        raise ApplyError(
            f"refusing to start; {repo} has no label: {', '.join(missing)}. "
            "Provision them (gh-label-sync.py for the managed namespaces, "
            "`gh label create` for a carried one) and rerun — nothing has been "
            "written."
        )


def resolve_milestones(repo, titles):
    """title -> number, reusing before creating, refusing on an ambiguous title.

    Reuse-before-create is push-plan.md §5.2's rule and it is what makes a rerun
    safe: creating a milestone that already exists 422s. Two milestones sharing a
    title is a refusal rather than a pick, because the plan groups by title and
    either choice would scatter one group across both.
    """
    pages = gh_json(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/milestones?state=all&per_page=100",
        ],
        "listing milestones",
        default=[],
    )
    seen: dict = {}
    for page in pages:
        for milestone in page:
            seen.setdefault(milestone["title"], []).append(milestone["number"])
    ambiguous = {t: n for t, n in seen.items() if t in titles and len(n) > 1}
    if ambiguous:
        raise ApplyError(
            "refusing to start; two milestones share a title: "
            + "; ".join(
                f"{t} (#{', #'.join(str(x) for x in sorted(n))})"
                for t, n in sorted(ambiguous.items())
            )
        )

    resolved, created = {}, []
    for title in titles:
        if title in seen:
            resolved[title] = seen[title][0]
            continue
        payload = gh_json(
            ["api", f"repos/{repo}/milestones", "-f", f"title={title}"],
            f"creating milestone {title!r}",
            default={},
        )
        number = payload.get("number")
        if not isinstance(number, int):
            raise ApplyError(f"creating milestone {title!r}: no number in the response")
        resolved[title] = number
        created.append(title)
    return resolved, created


def recover_landed(repo, keys):
    """Linear key -> the GitHub issue already carrying its footer.

    The backstop for the one failure the mapping cannot cover: `gh issue create`
    landed and the process died before the record was written. The body footer is
    the only thing on the issue naming the Linear key, so this is a body search.

    ONE search for the whole run, not one per issue, and the second reason is the
    load-bearing one. Cost: 117 searches would sit against GitHub's 30/min search
    limit. Correctness: issue search is eventually consistent, so a just-created
    issue may not be indexed yet, which makes a per-issue search unreliable
    exactly where it would matter. A lost create is by definition from an earlier
    run, minutes or more ago, so a pre-pass sees it; and a same-run double create
    cannot happen, because the mapping is written before the next phase starts.

    A key found on two issues refuses: it has already been imported twice, and no
    rerun can decide which of them is the home.
    """
    payload = gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            str(RECOVERY_SEARCH_LIMIT),
            "--search",
            BODY_MARKER_SEARCH,
            "--json",
            "number,body",
        ],
        "searching for already-migrated issues",
        default=[],
    )
    found: dict = {}
    for issue in payload:
        for key in set(BODY_MARKER_RE.findall(issue.get("body") or "")):
            if key in keys:
                found.setdefault(key, set()).add(issue["number"])
    duplicated = {k: v for k, v in found.items() if len(v) > 1}
    if duplicated:
        raise ApplyError(
            "refusing to continue; a Linear key already has two GitHub homes: "
            + "; ".join(
                f"{k} -> #{', #'.join(str(n) for n in sorted(v))}"
                for k, v in sorted(duplicated.items())
            )
        )
    return {key: numbers.pop() for key, numbers in found.items()}


def load_mapping(path, plan):
    """The mapping file, or a fresh one. The repo is asserted, not assumed.

    A mapping belongs to one board: reusing one against a different `--repo`
    would read another board's issue numbers as this one's and edit strangers.
    """
    repo = plan["repo"]
    if not os.path.exists(path):
        return {
            "repo": repo,
            "plan_generated_at": plan.get("generated_at"),
            "milestones": {},
            "entries": {},
        }
    with open(path, encoding="utf-8") as fh:
        mapping = json.load(fh)
    if not isinstance(mapping, dict) or not isinstance(mapping.get("entries"), dict):
        raise ApplyError(f"{path}: not an import mapping (no `entries` object)")
    if mapping.get("repo") != repo:
        raise ApplyError(
            f"{path}: records repo {mapping.get('repo')!r}, not {repo!r} — "
            "a mapping belongs to one board"
        )
    return mapping


def save_mapping(mapping, path):
    """Write-then-rename, every time.

    A truncating redirect would leave the only record of what has landed as
    invalid JSON at exactly the moment it is the one thing standing between a
    rerun and a duplicate import.
    """
    mapping["updated_at"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def record(mapping, path, key, **fields):
    """One phase's record, on disk before the next phase starts."""
    entry = mapping["entries"].setdefault(key, {"key": key})
    entry.update(fields)
    save_mapping(mapping, path)
    return entry


def body_file(text, suffix):
    """A body on disk, because it belongs on argv nowhere.

    A migrated description is arbitrary text — backticks, `$(…)`, newlines,
    kilobytes of it — and `--body` would put all of that through a quoting
    question that has no reason to exist. `--body-file` is also what makes the
    write byte-exact.
    """
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=suffix, delete=False, encoding="utf-8"
    )
    with handle:
        handle.write(text)
    return handle.name


def issue_number_from_url(url, key):
    match = re.search(r"/issues/(\d+)\s*$", (url or "").strip())
    if not match:
        raise ApplyError(f"{key}: cannot read an issue number out of {url!r}")
    return int(match.group(1))


def create_issue(repo, entry, milestone_number):
    """`gh issue create`, carrying everything EXCEPT the labels.

    The labels are a second call on purpose: `gh issue create --label` would
    reach the board with a name nothing validated, and the managed set has
    invariants (exactly one `status:`, exactly one `auto:`) that no per-flag
    write can enforce.
    """
    path = body_file(entry["body"], ".md")
    try:
        args = [
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            entry["title"],
            "--body-file",
            path,
        ]
        if milestone_number is not None:
            # The NUMBER, not the title: push-plan.md §5.2's rule, so neither a
            # rename nor a shell-unsafe character in a title breaks the grouping.
            args += ["--milestone", str(milestone_number)]
        if entry.get("assignee"):
            args += ["--assignee", entry["assignee"]]
        lines = gh(args, f"{entry['key']}: creating the issue").strip().splitlines()
        return issue_number_from_url(lines[-1] if lines else "", entry["key"])
    finally:
        os.unlink(path)


def verify_reopen_target(repo, number, key):
    """The guard `--plan` ran, run again against live state.

    Not a repetition of the plan's decision — a recheck of the premise it rests
    on. The plan verified this issue was closed when the plan was written; a
    human may have reopened it since, and reopening it again is the second live
    home the plan refused to create. The key check is the other half: an issue
    that no longer names this Linear key is not this issue's original.
    """
    payload = gh_json(
        [
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,state,body,comments",
        ],
        f"{key}: reading {repo}#{number}",
        default={},
    )
    state = (payload.get("state") or "").upper()
    if state != "CLOSED":
        raise ApplyError(
            f"{key}: refusing to reopen {repo}#{number} — it is "
            f"{state or 'unreadable'}, not closed. Two live homes would exist."
        )
    haystack = [payload.get("body") or ""] + [
        comment.get("body") or "" for comment in payload.get("comments") or []
    ]
    if not any(key in text for text in haystack):
        raise ApplyError(
            f"{key}: refusing to reopen {repo}#{number} — neither its body nor "
            f"its comments name {key}, so it is not this issue's original."
        )


def reopen_issue(repo, entry, milestone_number):
    """Retitle and rebody the original. It stays CLOSED until the label write.

    Reopening is gh-issue-state.py's `--reopen`, never a separate
    `gh issue reopen`: the label set and open/closed are two encodings of one
    fact and travel in one PATCH, so the issue is never open without its rungs.
    """
    number = entry["number"]
    verify_reopen_target(repo, number, entry["key"])
    path = body_file(entry["body"], ".md")
    try:
        args = [
            "issue",
            "edit",
            str(number),
            "--repo",
            repo,
            "--title",
            entry["title"],
            "--body-file",
            path,
        ]
        if milestone_number is not None:
            args += ["--milestone", str(milestone_number)]
        if entry.get("assignee"):
            args += ["--add-assignee", entry["assignee"]]
        gh(args, f"{entry['key']}: editing {repo}#{number}")
    finally:
        os.unlink(path)
    return number


def write_labels(repo, number, entry, reopen):
    """Carried labels first, then the managed set through the only writer.

    The order is load-bearing. gh-issue-state.py PATCHes the COMPLETE label set,
    carrying forward whatever its own read finds outside the four managed
    namespaces — so carried labels added before it are read and preserved.
    Reversed, that read would not yet see them and the PATCH would delete them.
    """
    key = entry["key"]
    if entry["carried_labels"]:
        gh(
            [
                "issue",
                "edit",
                str(number),
                "--repo",
                repo,
                "--add-label",
                ",".join(entry["carried_labels"]),
            ],
            f"{key}: adding carried labels to {repo}#{number}",
        )
    args = [
        "--repo",
        repo,
        "--issue",
        str(number),
        "--labels",
        ",".join(entry["managed_labels"]),
        "--apply",
    ]
    if reopen:
        args.append("--reopen")
    code, out, err = run_state_helper(args)
    if code != 0:
        raise ApplyError(
            f"{key}: writing labels on {repo}#{number} failed: {(err or out).strip()}"
        )


def comment_body(entry, reopen):
    """The one consolidated comment.

    One comment rather than one per Linear comment, resolved as the plan's open
    question 5: N comments is N writes against the secondary limit, they arrive
    attributed to the importer either way, and the thread then reads as a
    transcript rather than as a conversation that happened here.
    """
    key = entry["key"]
    lines = [COMMENT_MARKER.format(key=key)]
    if reopen:
        lines += [f"Returned from Linear {key}.", ""]
    lines.append(f"Comments migrated from Linear {key}:")
    for comment in entry["comments"]:
        author = comment.get("author") or "unknown"
        at = (comment.get("at") or "")[:10] or "unknown date"
        lines += ["", f"**{author}, {at}:**", "", (comment.get("body") or "").rstrip()]
    return "\n".join(lines) + "\n"


def post_comments(repo, number, entry, reopen):
    """The consolidated comment, unless the marker says it is already there.

    The marker read is what a lost response costs: one `gh issue view` per
    commented issue, in exchange for never double-posting a transcript.
    """
    key = entry["key"]
    payload = gh_json(
        ["issue", "view", str(number), "--repo", repo, "--json", "comments"],
        f"{key}: reading comments on {repo}#{number}",
        default={},
    )
    marker = COMMENT_MARKER.format(key=key)
    if any(marker in (c.get("body") or "") for c in payload.get("comments") or []):
        return False
    path = body_file(comment_body(entry, reopen), ".md")
    try:
        gh(
            ["issue", "comment", str(number), "--repo", repo, "--body-file", path],
            f"{key}: commenting on {repo}#{number}",
        )
    finally:
        os.unlink(path)
    return True


def phase_reached(mapping, key):
    """The last phase recorded for this key, or None."""
    return (mapping["entries"].get(key) or {}).get("phase")


def done_after(phase, target):
    """True when `phase` is at or past `target`. An unrecorded phase is before
    every one of them, which is what makes a fresh key start at the beginning."""
    if phase not in APPLY_PHASES:
        return False
    return APPLY_PHASES.index(phase) >= APPLY_PHASES.index(target)


def apply_entry(entry, context):
    """One entry through every phase it has not already passed."""
    repo = context["repo"]
    mapping = context["mapping"]
    path = context["mapping_path"]
    key = entry["key"]
    reopen = entry["action"] == "reopen"
    milestone_number = context["milestones"].get(entry["milestone"])
    reached = phase_reached(mapping, key)

    number = (mapping["entries"].get(key) or {}).get("number")
    if number is None:
        number = context["recovered"].get(key)
        if number is not None:
            # Recovered, not created: the write landed on an earlier run and the
            # record did not. Adopting the number is the pre-pass's whole point.
            print(f"  {key}: adopting already-landed {repo}#{number}")
    if number is None and reopen:
        number = entry["number"]

    if not done_after(reached, "created"):
        if number is None:
            number = create_issue(repo, entry, milestone_number)
            resolution = "created"
        elif reopen:
            number = reopen_issue(repo, entry, milestone_number)
            resolution = "reopened"
        else:
            # A recovered create: the issue already exists carrying the body
            # this plan wrote, so this phase has nothing left to write.
            resolution = "adopted"
        record(
            mapping,
            path,
            key,
            number=number,
            action=entry["action"],
            resolution=resolution,
            url=f"https://github.com/{repo}/issues/{number}",
            milestone=entry["milestone"],
            phase="created",
        )

    if not done_after(reached, "labelled"):
        write_labels(repo, number, entry, reopen)
        record(mapping, path, key, phase="labelled")

    if not done_after(reached, "commented"):
        posted = bool(entry["comments"]) and post_comments(repo, number, entry, reopen)
        record(mapping, path, key, phase="commented", commented=bool(posted))

    if not done_after(reached, "done"):
        record(
            mapping,
            path,
            key,
            phase="done",
            landed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
    return number


def select_to_apply(entries, only):
    """The entries `--only` names, in plan order, or all of them.

    A named key the plan does not carry is a refusal, for the reason `--plan`'s
    own `--issue` check gives: a typo in an enumeration must not read as
    "nothing matched". The order stays the plan's, so a partial run and the full
    run land issues in the same sequence.
    """
    if not only:
        return list(entries)
    known = {entry["key"] for entry in entries}
    absent = sorted(set(only) - known)
    if absent:
        raise ApplyError(
            "--only names a key the plan does not carry: " + ", ".join(absent)
        )
    wanted = set(only)
    return [entry for entry in entries if entry["key"] in wanted]


def apply_plan(plan, mapping_path, limit=None, sleep=2.0, only=None):
    """Land the plan, phase by phase, recording as it goes.

    The plan is EXECUTED, never re-derived. The selection and the crosswalk are
    arguments settled in `--plan`; reading the export again here is how the
    "project name is a lying proxy" bug comes back.
    """
    repo = plan["repo"]
    entries = plan["entries"]
    planned = [entry["key"] for entry in entries]
    # Resolved first so an `--only` typo refuses before anything is created.
    todo = select_to_apply(entries, only)
    mapping = load_mapping(mapping_path, plan)
    mapping["plan_generated_at"] = plan.get("generated_at")

    # Both refusals run before the first write, so a board that cannot take the
    # import fails with nothing landed rather than with eighty issues landed.
    # They are checked over the WHOLE plan even on an `--only` run: a label the
    # board lacks is worth knowing before the batch, not at entry 87.
    check_carried_labels(repo, entries)
    milestones, created = resolve_milestones(repo, plan["milestones"])
    mapping["milestones"] = milestones
    save_mapping(mapping, mapping_path)
    if created:
        print(f"milestones created: {', '.join(created)}")
    print(f"milestones resolved: {len(milestones)}")

    recovered = recover_landed(repo, set(planned))
    if recovered:
        print(
            "already landed (adopting): "
            + ", ".join(f"{k}->#{v}" for k, v in sorted(recovered.items()))
        )

    context = {
        "repo": repo,
        "mapping": mapping,
        "mapping_path": mapping_path,
        "milestones": milestones,
        "recovered": recovered,
    }

    attempted, skipped, first = 0, 0, True
    for entry in todo:
        key = entry["key"]
        if phase_reached(mapping, key) == "done":
            skipped += 1
            continue
        if limit is not None and attempted >= limit:
            break
        if not first:
            nap(sleep)
        first = False
        attempted += 1
        try:
            number = apply_entry(entry, context)
        except ApplyError as exc:
            raise ApplyError(
                f"{exc}\n\nStopped at {key}. {attempted - 1} issue(s) completed "
                f"this run; rerun the same command to resume from {key}."
            )
        print(f"  {key} -> {repo}#{number} ({entry['action']})")

    return {
        "repo": repo,
        "mapping": str(mapping_path),
        "planned": len(planned),
        "attempted": attempted,
        "already_done": skipped,
        "milestones": len(milestones),
        "milestones_created": created,
        "incomplete": [k for k in planned if phase_reached(mapping, k) != "done"],
    }


def print_apply_summary(result):
    print(f"Applied {result['attempted']} entry(ies) to {result['repo']}")
    print(f"  mapping: {result['mapping']}")
    print(
        f"  planned={result['planned']}  worked this run={result['attempted']}  "
        f"already done={result['already_done']}  milestones={result['milestones']}"
    )
    if result["incomplete"]:
        print(
            f"  NOT yet done in the mapping ({len(result['incomplete'])}): "
            + ", ".join(result["incomplete"])
        )
    else:
        print("  every planned key is in the mapping at phase done")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # The three modes: build a plan, read one back, or land it. --show carries
    # its own subjects, so passing it both selects the mode and says what to show.
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="build the import plan")
    mode.add_argument(
        "--apply",
        action="store_true",
        help="land the plan on GitHub. Needs --plan-file and --mapping",
    )
    mode.add_argument(
        "--show",
        action="append",
        metavar="KEY",
        help=(
            "print this plan entry beside its Linear original instead of "
            "building a plan; repeatable. Needs --plan-file"
        ),
    )
    # Not required: `--apply` reads the plan and nothing else. The export is the
    # provenance record and the plan is derived from it, so needing it here would
    # make every apply depend on a 3.3 MB file the plan already answers for.
    ap.add_argument("--export", help="the linear-export.py JSON file (--plan/--show)")
    ap.add_argument("--plan-file", help="the plan JSON to read (--show/--apply)")
    ap.add_argument(
        "--mapping",
        help=(
            "where --apply records what landed, per phase. A rerun reads it and "
            "resumes; it is the only record of which issues exist"
        ),
    )
    ap.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="--apply: work at most N not-yet-done entries, for a first attended run",
    )
    ap.add_argument(
        "--only",
        action="append",
        metavar="KEY",
        help=(
            "--apply: restrict the run to these plan keys; repeatable. For the "
            "attended first run, where the entries worth eyeballing (a reopen, a "
            "milestoned create) are not the ones --limit would reach first"
        ),
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="--apply: pause between issues, against the secondary rate limit (default 2)",
    )
    ap.add_argument("--repo", help="owner/name the import targets (--plan)")
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
    ap.add_argument("--out", help="where to write the plan JSON (--plan)")
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

    if args.apply:
        for flag in ("plan_file", "mapping"):
            if not getattr(args, flag):
                ap.error(f"--apply needs --{flag.replace('_', '-')}")
        if args.limit is not None and args.limit < 1:
            ap.error("--limit must be at least 1")
        try:
            plan = load_plan(args.plan_file)
            if args.repo and args.repo != plan.get("repo"):
                raise ApplyError(
                    f"{args.plan_file} targets {plan.get('repo')!r}, not "
                    f"{args.repo!r} — refusing to land a plan on another board"
                )
            result = apply_plan(plan, args.mapping, args.limit, args.sleep, args.only)
        except (PlanError, ApplyError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_apply_summary(result)
        return 0

    if args.show:
        if not args.plan_file:
            ap.error("--show needs --plan-file")
        if not args.export:
            ap.error("--show needs --export")
        try:
            plan = load_plan(args.plan_file)
            export = load_export(args.export)
            for line in show_lines(plan, export, args.show):
                print(line)
        except PlanError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0

    for flag in ("repo", "out", "export"):
        if not getattr(args, flag):
            ap.error(f"--plan needs --{flag}")
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
