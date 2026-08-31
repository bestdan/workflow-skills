#!/usr/bin/env python3
"""coreview-rule-drift.py — detect co-review allow-rules that no longer match.

co-review approves each reviewer with an exact-match Bash allow-rule. When a
release changes a reviewer's dispatch string, the rule stops matching, and the
failure is silent in the mode that matters: under `--non-interactive` an
unapproved command is denied rather than queued, so the reviewer simply stops
appearing in the run summary. Nothing errors.

This script compares the rule templates documented by the INSTALLED plugin's
`skills/co-review/reviewers/*.md` against `permissions.allow` in the settings
files, and names what drifted. It is READ-ONLY: it never writes settings. The
repair is a human edit — the corrected string has to carry paths only the
operator knows (`<NEUTRAL>` in particular is never documented as a fixed value),
and on a sandboxed machine the settings file is write-denied anyway.

Read by `/doctor` (commands/doctor.md) as a check, and by co-review's own
pre-flight so a denied reviewer is explained rather than silently missing.

Usage:
  coreview-rule-drift.py [--plugin-root PATH] [--settings PATH]... [--json]

  --plugin-root  the installed plugin to read templates from. Defaults to
                 $CLAUDE_PLUGIN_ROOT. This must be the INSTALLED plugin, not a
                 working checkout: the installed copy is what dispatches, and
                 the two can differ by several releases.
  --settings     a settings.json to read `permissions.allow` from; repeatable.
                 Defaults to ~/.claude/settings.json plus ./.claude/settings.json
                 and ./.claude/settings.local.json when they exist.
  --json         emit the findings as JSON instead of a report.

Exit codes: 0 no drift, 1 drift found, 2 could not run (bad plugin root, etc).
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Placeholders a reviewer file leaves for the operator to substitute. The
# operator's real value is unknowable here — `<NEUTRAL>` is deliberately never
# documented as a fixed path — so each is matched as a wildcard, bounded by
# PLACEHOLDER_VALUE below.
PLACEHOLDER_RE = re.compile(r"(<INPUT-DIR>|<INPUT>|<NEUTRAL>)")

# Placeholders whose path is exempt from the off-machine check, because nothing
# about them can be wrong. `<NEUTRAL>` is created by the dispatch's own
# `mkdir -p`, which makes every missing parent, so any depth may legitimately be
# absent before a first run. And its only requirement is to be a dedicated empty
# directory — a mistyped `<NEUTRAL>` still yields one, so there is no failure to
# detect. Checking it would report a valid config as broken and catch nothing.
EXEMPT_PLACEHOLDERS = {"<NEUTRAL>"}

# What a placeholder may stand for. Every reviewer file requires a "literal
# fixed absolute path" here, so anything not starting with `/` is not a
# substitution of this template — which is what stops a general-purpose
# `Bash(cd "$(git rev-parse --show-toplevel)")` from being read as devin's
# neutral-cwd rule.
PLACEHOLDER_VALUE = r'(/[^"]+?)'

# Binaries a reviewer template shares with ordinary shell work. A settings rule
# driving one of these is not attributable to the reviewer, so it is never
# reported dead — but the template still has to be covered, or the reviewer's
# dispatch is denied at that segment.
GENERIC_BINARIES = {"cat", "cd", "gh", "git", "mkdir"}

# A rule line inside a ```json fence in a reviewer file: a JSON string holding a
# Bash() rule, optionally followed by the array comma.
RULE_LINE_RE = re.compile(r'^\s*("Bash\(.*\)")\s*,?\s*$')

# Opening fence, capturing its language. Only a `json` fence holds rules; a bash
# fence in the same file holds the invocation, whose lines must never be read as
# shipped templates.
FENCE_RE = re.compile(r"^\s*```(\w*)")


def die(msg):
    print(f"coreview-rule-drift: {msg}", file=sys.stderr)
    sys.exit(2)


def parse_templates(reviewer_file):
    """Extract the Bash() rule templates from a reviewer file's ```json fences.

    Only lines inside a `json` fence count, on both axes. Prose in these files
    quotes rule fragments to explain them, and a sibling `bash` fence carries the
    invocation itself — reading either as a template would invent rules the
    reviewer never ships and report them missing forever.
    """
    templates = []
    fence_lang = None
    for line in reviewer_file.read_text(encoding="utf-8").splitlines():
        m = FENCE_RE.match(line)
        if m:
            # An opening fence records its language; the next fence closes it.
            fence_lang = None if fence_lang is not None else m.group(1)
            continue
        if fence_lang != "json":
            continue
        m = RULE_LINE_RE.match(line)
        if m:
            templates.append(json.loads(m.group(1)))
    return templates


def template_to_regex(template):
    """Compile a template into a full-match regex, placeholders as captures.

    Everything outside a placeholder is escaped, so the comparison stays
    byte-for-byte wherever the template is literal — which is the whole point:
    a reordered flag must not match. The placeholders are captured rather than
    merely wildcarded so the substituted paths can be checked separately; the
    pattern alone cannot tell a legitimate path from a typo'd one, because both
    are equally well-formed absolute paths to it.

    Returns `(regex, names)`, where `names[i]` is the placeholder that produced
    capture group `i + 1`. The caller needs it because the placeholders are not
    interchangeable: what counts as an implausible path differs by which one it
    substitutes (see `offmachine_paths`).
    """
    # PLACEHOLDER_RE captures, so split interleaves: [lit, ph, lit, ph, …, lit].
    pieces = PLACEHOLDER_RE.split(template)
    literals, names = pieces[0::2], pieces[1::2]
    pattern = PLACEHOLDER_VALUE.join(re.escape(p) for p in literals)
    return re.compile(pattern + r"\Z"), names


def offmachine_paths(rule, rx, names):
    """Substituted absolute paths in `rule` that do not resolve on THIS machine.

    A placeholder path may legitimately not exist yet: `<INPUT>` is created by
    the dispatch's own `cat >`, which makes only the final component and needs
    the parent to be there already. So one missing trailing level is normal and
    two or more is not — a path that far from anything on disk cannot be written
    or read here, so the rule it sits in can never fire on this machine.

    That reasoning is specific to the `cat >` placeholders, so it is applied
    only to them; `EXEMPT_PLACEHOLDERS` names the ones it would misjudge and why.
    A single global threshold reported a valid deep `<NEUTRAL>` as unusable.

    **This says nothing about whether the rule is wrong.** A settings file
    shared across machines — a dotfiles repo synced to hosts whose usernames
    differ, say — must carry each machine's rules, so every host sees the other
    hosts' rules as unresolvable and that is correct, not drift. An off-machine
    rule is therefore reported and never counted as drift; if it leaves a
    template with no live rule, `missing` says so on its own, which is the
    signal that actually matters and is true on exactly the hosts where it is.

    """
    m = rx.fullmatch(rule)
    bad = []
    for name, value in zip(names, m.groups()):
        if name in EXEMPT_PLACEHOLDERS or not value.startswith("/"):
            continue
        p = Path(value)
        missing = 0
        while p != p.parent and not p.exists():
            p = p.parent
            missing += 1
        if missing >= 2:
            bad.append((value, str(p)))
    return bad


def command_word(rule):
    """The binary a `Bash(<cmd> ...)` rule invokes, or None if it is not one."""
    if not rule.startswith("Bash(") or not rule.endswith(")"):
        return None
    inner = rule[len("Bash(") : -1].strip()
    return inner.split()[0] if inner else None


def load_allow_rules(paths):
    """Union the `permissions.allow` arrays of every settings file that exists.

    A settings file that is absent is normal (most repos have no local
    override); one that is present but unparseable is a real problem the caller
    must hear about, so it is fatal rather than silently skipped.
    """
    rules, read = [], []
    for p in paths:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            die(f"{p} is not valid JSON: {exc}")
        allow = data.get("permissions", {}).get("allow", [])
        if not isinstance(allow, list):
            die(f"{p}: permissions.allow is not a list")
        rules.extend(r for r in allow if isinstance(r, str))
        read.append(str(p))
    return rules, read


def analyze(reviewers_dir, allow_rules):
    """Classify every reviewer's templates against the operator's allow rules."""
    findings = []
    for reviewer_file in sorted(reviewers_dir.glob("*.md")):
        name = reviewer_file.stem
        templates = parse_templates(reviewer_file)
        if not templates:
            continue

        # The binaries this reviewer's rules invoke, minus the general-purpose
        # ones. A rule in the operator's settings is attributed to this reviewer
        # only when it drives one of these, so neither an unrelated
        # `Bash(cat:*)` nor a general `Bash(cd ...)` is ever called dead.
        binaries = {
            w
            for w in (command_word(t) for t in templates)
            if w and w not in GENERIC_BINARIES
        }
        owned = [r for r in allow_rules if command_word(r) in binaries]

        matched_rules, missing, offmachine = set(), [], []
        for template in templates:
            rx, ph_names = template_to_regex(template)
            live = []
            # Coverage is asked of every allow rule, not just the owned ones:
            # devin's `mkdir -p` / `cd` segments are satisfied by whatever rule
            # covers them, generic or not. Deadness is asked only of the owned
            # ones, below.
            for rule in allow_rules:
                if not rx.fullmatch(rule):
                    continue
                bad = offmachine_paths(rule, rx, ph_names)
                if bad:
                    # Shape-correct but pointed somewhere this machine has no
                    # path to, so it cannot fire here. Not coverage — otherwise
                    # another host's rule would mask that this host has none —
                    # but not drift either; see offmachine_paths.
                    offmachine.append({"rule": rule, "paths": bad})
                    matched_rules.add(rule)
                else:
                    live.append(rule)
            if live:
                matched_rules.update(live)
            else:
                missing.append(template)

        dead = [r for r in owned if r not in matched_rules]
        findings.append(
            {
                "reviewer": name,
                "templates": len(templates),
                "matched": len(templates) - len(missing),
                "missing": missing,
                "dead": dead,
                "offmachine": offmachine,
                # No rule at all is "not set up", not "broke" — an operator who
                # does not use a reviewer should not be nagged about it.
                "configured": bool(owned),
            }
        )
    return findings


def has_drift(findings):
    """Drift is a CONFIGURED reviewer with a missing or dead rule.

    Off-machine rules are deliberately excluded: on a settings file shared
    across hosts they are the other hosts' correct rules, so counting them
    would report drift on every machine forever. A template they fail to cover
    is already reported as `missing`, which fires on exactly the hosts where it
    is true.
    """
    return any(f["configured"] and (f["missing"] or f["dead"]) for f in findings)


def report(findings, plugin_root, settings_read):
    print(f"co-review allow-rule drift — templates from {plugin_root}")
    for p in settings_read:
        print(f"  settings: {p}")
    print()

    for f in findings:
        name = f["reviewer"]
        if not f["configured"]:
            print(
                f"{name}: no allow-rule configured ({f['templates']} shipped) — "
                f"this reviewer cannot run"
            )
            continue
        if not f["missing"] and not f["dead"] and not f["offmachine"]:
            print(f"{name}: ok ({f['matched']}/{f['templates']} rules match)")
            continue

        print(f"{name}: {f['matched']}/{f['templates']} shipped rules covered")
        for rule in f["dead"]:
            print(f"  DEAD       {rule}")
        for s in f["offmachine"]:
            print(f"  OFF-MACHINE {s['rule']}")
            for value, deepest in s["paths"]:
                print(f"              no such path here: {value}")
                print(f"              deepest that exists: {deepest}")
        for template in f["missing"]:
            print(f"  MISSING    {template}")
        print()

    if any(f["offmachine"] for f in findings):
        print(
            "OFF-MACHINE rules are reported, never counted as drift. On a settings\n"
            "file shared across hosts they are another host's correct rules, and\n"
            "'fixing' one here breaks it there. Leave them alone unless you know the\n"
            "path is simply wrong everywhere.\n"
        )

    if has_drift(findings):
        print(
            "A dead rule fails silently: under `/co-review --non-interactive` the\n"
            "dispatch is denied, not queued, so the reviewer just stops appearing in\n"
            "the run summary. To repair: copy each MISSING template verbatim,\n"
            "substitute its placeholders with the same literal absolute paths your\n"
            "invocation uses, and delete the DEAD rule it replaces.\n"
            "\n"
            "Make that edit wherever the settings file above is MANAGED, which is not\n"
            "always the file itself — a generated or dotfiles-synced settings.json\n"
            "loses an in-place edit at the next sync, and the drift comes back with no\n"
            "sign of why. Nothing here writes settings for you."
        )
    else:
        print("No drift.")


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("--plugin-root", default=os.environ.get("CLAUDE_PLUGIN_ROOT"))
    ap.add_argument("--settings", action="append", default=[])
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if not args.plugin_root:
        die(
            "no plugin root — pass --plugin-root or run where $CLAUDE_PLUGIN_ROOT "
            "is set. It must point at the INSTALLED plugin, not a checkout."
        )
    reviewers = Path(args.plugin_root) / "skills" / "co-review" / "reviewers"
    if not reviewers.is_dir():
        die(f"{reviewers} is not a directory — is --plugin-root a plugin root?")

    if args.settings:
        settings_paths = [Path(p) for p in args.settings]
    else:
        settings_paths = [
            Path.home() / ".claude" / "settings.json",
            Path(".claude") / "settings.json",
            Path(".claude") / "settings.local.json",
        ]

    allow_rules, settings_read = load_allow_rules(settings_paths)
    if not settings_read:
        die(
            "none of the settings files exist: "
            + ", ".join(str(p) for p in settings_paths)
        )

    findings = analyze(reviewers, allow_rules)
    if args.as_json:
        print(
            json.dumps(
                {
                    "plugin_root": str(args.plugin_root),
                    "settings": settings_read,
                    "drift": has_drift(findings),
                    "reviewers": findings,
                },
                indent=2,
            )
        )
    else:
        report(findings, args.plugin_root, settings_read)

    sys.exit(1 if has_drift(findings) else 0)


if __name__ == "__main__":
    main()
