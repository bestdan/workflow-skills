#!/usr/bin/env python3
"""Write an issue's complete state atomically: validate, then one full-set PATCH.

"State" here means the label set AND open/closed together, because under this
schema they are two encodings of one fact: a closed issue is exactly "no rungs",
an open issue is exactly "one `status:` rung and one `auto:` rung". Writing them
separately would leave a window in which the issue contradicts itself, so the
single PATCH carries both.

Once the gh-issue handler migrates onto it, this will be the only supported way
to change an issue's status, routing, priority or estimate. Nothing calls it yet
— the handler transitions still use `gh issue edit`, and they move together in a
later change rather than half-now. Two measured facts force its shape, and
neither half is sufficient alone:

- `gh issue edit --add-label X --remove-label Y` is **not atomic** — it produced
  8 HTTP request lines. A crash between them strands an issue carrying two
  status labels, or none. So transitions cannot be incremental.
- `PATCH /repos/{owner}/{repo}/issues/{n}` with a full `labels` array replaces
  the set in **one request**, so no intermediate state exists. But a raw REST
  write **auto-creates unknown labels** — measured: posting `zz-undefined-label`
  created it. `gh`'s own label commands reject an undefined label; REST does not.

Hence: validate every name against labels.yml and enforce the invariants first,
locally, exiting non-zero before any network call — then issue one PATCH. A typo
like `est:7` fails loudly instead of silently entering the repo's namespace.

Never add `--add-label` / `--remove-label` to a state-transition path.

Note: the local `sandbox-network-guard` hook blocks non-GET `gh api`, so this
needs an allowlist entry to run unsandboxed locally.

This script is the LOCAL path. A cloud routine has no `gh` and no credentialed
raw HTTP, so it must go through the GitHub MCP connector's `issue_write` instead.
The same two-part rule applies there, measured 2026-08-24 against the live API:
`issue_write` with `labels` REPLACES the whole set (3 labels in, updated to 1,
read back as exactly 1), and it AUTO-CREATES a name the repo does not have
(`zz-mcp-undefined-20260824` was created, color ededed, and had to be deleted by
hand). So validate-then-replace is not a quirk of the `gh api` path — it is the
rule on both channels, and the routine path needs the same vocabulary check
before it writes.

Because the write replaces the issue's ENTIRE label set, anything not echoed back
is deleted — so this reads the issue's current labels first and carries forward
every label outside the four vocabulary namespaces. That is what keeps the
handler's own markers alive: gh-issue.md puts `follow-up` on task issues and
/archive-tasks refuses to sweep without it, and it can never live in labels.yml.
The read makes it two requests, but the WRITE is still one, which is where
atomicity is needed.

Open/closed travels in the same PATCH rather than being left to the caller. That
removes an ordering question instead of legislating one: whichever order a caller
picked for "close the issue" and "write its labels", one of the two would land
while the issue was in the other state, so rejecting a mismatch would forbid a
legal sequence arbitrarily. The rules:

- `--done` closes the issue and asserts neither rung is present
- an ordinary write against an OPEN issue omits `state` entirely, so the default
  path never touches it
- an ordinary write against a CLOSED issue is refused, because putting live rungs
  on a closed issue IS a reopen; `--reopen` says so out loud and performs it

Callers must not pair --done with a separate `gh issue close` — this does it.

Usage:
  python3 gh-issue-state.py --repo owner/name --issue 142 \
      --labels status:3_started,auto:eligible,prio:1,est:3 --apply

  # completion: closes the issue, which carries neither rung and keeps prio/est
  python3 gh-issue-state.py --repo owner/name --issue 142 \
      --labels prio:1,est:3 --done --apply

Without --apply it validates, reads the issue, and prints what it would write. That
path is read-only — it mutates nothing — but it is not offline: the read always runs.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _labels import (  # noqa: E402
    AT_MOST_ONE,
    DEFAULT_LABELS_FILE,
    EXACTLY_ONE,
    expected_labels,
    load_vocabulary,
)


class InvalidLabelSet(Exception):
    """The requested set is not writable: unknown name, or a broken invariant."""


def group_of(label):
    return label.split(":", 1)[0]


def in_managed_namespace(label, managed_groups):
    """True only for a label that actually sits inside one of the namespaces.

    The colon is load-bearing. A repo may carry a plain label named `status` or
    `est` that has nothing to do with this schema, and `group_of()` reports the
    whole bare name as its own group — so deciding on the split result alone
    would classify that label as ours and delete it with the full-set write.
    """
    return any(label.startswith(f"{group}:") for group in managed_groups)


def validate(labels, vocabulary, done=False):
    """Raise InvalidLabelSet unless `labels` is a legal complete set.

    Runs entirely locally and before any network call, which is the whole point:
    an unknown name reaching the PATCH would be created rather than rejected.
    """
    seen = []
    for label in labels:
        if label in seen:
            raise InvalidLabelSet(f"duplicate label: {label}")
        seen.append(label)

    unknown = [label for label in labels if label not in vocabulary]
    if unknown:
        raise InvalidLabelSet(
            "not in labels.yml (a raw REST write would create these): "
            + ", ".join(unknown)
        )

    counts = {}
    for label in labels:
        counts[group_of(label)] = counts.get(group_of(label), 0) + 1

    for group in EXACTLY_ONE:
        found = counts.get(group, 0)
        # A closed issue carries neither rung. `status:` is absent because "done"
        # IS that absence; `auto:` because it answers "may automation pick this
        # up?" — a live scheduling instruction, not a fact about the work. A
        # lingering `auto:eligible` on a closed issue is a hazard, not data.
        if done:
            if found:
                raise InvalidLabelSet(
                    f"--done means no `{group}:` rung, but {found} was given"
                )
            continue
        if found != 1:
            raise InvalidLabelSet(f"expected exactly one `{group}:` label, got {found}")

    for group in AT_MOST_ONE:
        found = counts.get(group, 0)
        if found > 1:
            raise InvalidLabelSet(f"expected at most one `{group}:` label, got {found}")

    return list(labels)


def run_gh(args, stdin=None):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin)
    return proc.returncode, proc.stdout, proc.stderr


def current_issue(repo, issue):
    """Read the issue's labels and open/closed state. Read-only; mutates nothing.

    State rides along on the same GET, so knowing it costs no extra request.
    """
    code, out, err = run_gh(
        ["issue", "view", str(issue), "--repo", repo, "--json", "labels,state"]
    )
    if code != 0:
        raise SystemExit(f"reading {repo}#{issue} failed: {err.strip() or out.strip()}")
    payload = json.loads(out or '{"labels": [], "state": "OPEN"}')
    labels = [entry["name"] for entry in payload.get("labels", [])]
    return labels, (payload.get("state") or "OPEN").lower()


def preserve_unmanaged(current, managed_groups):
    """The labels this helper does not own, and must therefore carry forward.

    The write replaces the issue's entire label set, so anything not echoed back
    is deleted. Only the four vocabulary namespaces belong to this helper: the
    handler's own marker (`follow-up`, which /archive-tasks refuses to sweep
    without) and anything a human added live outside them and must survive.
    """
    return [
        label for label in current if not in_managed_namespace(label, managed_groups)
    ]


def dropped_unrecognized(current, managed_groups, vocabulary):
    """Managed-namespace labels the vocabulary does not define.

    The full-set write deletes these, and correctly so — this helper owns those
    four namespaces, and an `prio:urgent` a human invented is exactly the garbage
    validate-then-replace exists to purge. But the deletion is invisible unless it
    is reported, so a caller would see a label vanish with no trace of why.

    A rung being REPLACED (status:2_ready -> status:3_started) is not in here:
    that is the ordinary transition, not a loss.
    """
    return [
        label
        for label in current
        if in_managed_namespace(label, managed_groups) and label not in vocabulary
    ]


def patch_issue(repo, issue, labels, state=None):
    """One PATCH carrying the complete set. Never --add-label/--remove-label."""
    payload = {"labels": labels}
    if state:
        payload["state"] = state
    body = json.dumps(payload)
    code, out, err = run_gh(
        ["api", "--method", "PATCH", f"repos/{repo}/issues/{issue}", "--input", "-"],
        stdin=body,
    )
    if code != 0:
        raise SystemExit(f"PATCH {repo}#{issue} failed: {err.strip() or out.strip()}")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--issue", required=True, type=int, help="issue number")
    parser.add_argument(
        "--labels",
        required=True,
        help=(
            "the complete MANAGED set, comma-separated — labels outside "
            "status:/auto:/prio:/est: are carried forward, not deleted"
        ),
    )
    # Opposite transitions. Accepting both would silently drop one of them, so
    # argparse rejects the pair before any work happens.
    state = parser.add_mutually_exclusive_group()
    state.add_argument(
        "--done",
        action="store_true",
        help="completion write: closes the issue and asserts no `status:`/`auto:` rung",
    )
    state.add_argument(
        "--reopen",
        action="store_true",
        help="reopen a closed issue and give it these rungs",
    )
    parser.add_argument("--apply", action="store_true", help="send the PATCH")
    parser.add_argument("--labels-file", type=Path, default=DEFAULT_LABELS_FILE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    groups, colors = load_vocabulary(args.labels_file)
    vocabulary = expected_labels(groups, colors)
    requested = [label.strip() for label in args.labels.split(",") if label.strip()]

    # Validate FIRST, before any network call: an unknown name must never reach
    # the API, because a raw REST write creates it rather than rejecting it.
    try:
        managed = validate(requested, vocabulary, done=args.done)
    except InvalidLabelSet as exc:
        print(f"refusing to write {args.repo}#{args.issue}: {exc}", file=sys.stderr)
        return 2

    current, state = current_issue(args.repo, args.issue)
    preserved = preserve_unmanaged(current, set(groups))
    dropped = dropped_unrecognized(current, set(groups), vocabulary)
    labels = managed + [label for label in preserved if label not in managed]

    # The label set and open/closed are two encodings of one fact, so the write
    # settles both. Only the two transitions touch `state`; an ordinary write to
    # an already-open issue omits it, so the common path cannot move it by
    # accident.
    if args.done:
        target_state = "closed"
    elif state == "closed":
        if not args.reopen:
            print(
                f"refusing to write {args.repo}#{args.issue}: issue is closed; "
                "pass --done for a rung-free write, or --reopen to reopen it "
                "with these rungs",
                file=sys.stderr,
            )
            return 2
        target_state = "open"
    else:
        target_state = None

    if args.apply:
        patch_issue(args.repo, args.issue, labels, target_state)

    result = {
        "repo": args.repo,
        "issue": args.issue,
        "labels": labels,
        "managed": managed,
        "preserved": preserved,
        "dropped": dropped,
        "state": target_state or state,
        "applied": args.apply,
    }
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        verb = "Wrote" if args.apply else "Would write"
        print(f"{verb} {args.repo}#{args.issue}: {', '.join(labels)}")
        if preserved:
            print(f"Carried forward (not managed here): {', '.join(preserved)}")
        if dropped:
            print(f"Dropped (not in labels.yml): {', '.join(dropped)}")
        if target_state:
            print(f"State: {target_state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
