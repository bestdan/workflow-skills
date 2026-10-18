#!/usr/bin/env python3
"""Decide whether an eval row fired its skill, and — when it didn't — why.

Usage:
  python3 eval-triage.py --log <run.jsonl> --skill <name> [--rc N] [--json]

Reads one `claude -p --output-format stream-json --verbose` run log and prints
a verdict line plus, on a miss, the diagnosis. Exit code is the contract:
0 = the expected skill fired, 1 = it did not.

This exists because `❌ FAIL — skills invoked: none` is not a diagnosis. It is
the same string whether the run was killed by the wall-clock cap, died on an
API error, burned its `--max-turns` ceiling, never had the skill in its listing
at all, or saw the skill and answered without it. Those have different fixes,
so the harness has to tell them apart. `cause` is that discrimination:

  no-result-event  the run never reached a `result` event — killed (timeout
                   sends SIGTERM, so the log just stops) or crashed. Read `rc`.
  api-error        the run reached `result` but the API failed under it.
  max-turns        the ceiling was hit with the skill unfired. Raise the row's
                   max_turns, or shorten what the prompt makes the model do
                   first.
  not-surfaced     the skill was not in the session's own skill/command
                   listing, so the model could not have picked it. A plugin
                   loading problem, not a description problem.
  wrong-skill      some other skill fired — a description collision, the one
                   failure mode a ranking check over the descriptions can also
                   see.
  no-skill-chosen  the skill was surfaced, the run completed cleanly, and the
                   model answered without it. The genuine routing miss.

`surfaced` is read from the `system`/`init` event's `skills` and
`slash_commands` lists, which is what the model actually chooses from. Both are
checked because a plugin's entry point can reach the model as either. Names are
matched ignoring a `<plugin>:` prefix and a leading `/`, the same tolerance the
fired-skill match uses.

`plugin_dirs` records every loaded plugin whose name matches `--plugin`,
because the eval runs `--plugin-dir <repo>` inside a session that also loads
the user's installed plugins. Two copies of the same plugin, or the installed
copy winning over the repo one, would make the suite measure a different tree
than the one under test — so the log has to say which paths were loaded rather
than leaving it to be assumed.
"""

import argparse
import json
import sys

# Long enough to see what the model said instead of loading the skill; short
# enough that a retained log stays the place you go for the full text.
FINAL_TEXT_LIMIT = 400


def _bare(name):
    """Strip a `<plugin>:` prefix and a leading `/` so names compare equal."""
    if not isinstance(name, str):
        return ""
    name = name.lstrip("/")
    return name.split(":")[-1]


def _expected(skill):
    """The set of names that count as a pass for this row.

    A `|`-separated row accepts any of its alternatives. Some prompts have more
    than one right answer and no way to prefer one: a "don't let this fall
    through the cracks" prompt matches both the `task` umbrella and the
    `add-task` command that actually files it, and the model picks either.
    Asserting a single name there produces a row that fails a third of the time
    for choosing correctly — a flaky red that reads exactly like a routing
    defect, which is the confusion this script exists to end.
    """
    return {_bare(part) for part in skill.split("|") if part.strip()}


def _events(lines):
    """Yield the parsed JSON objects in a run log, skipping unparseable lines.

    A run log is not guaranteed to be clean JSONL: the harness's own stderr is
    redirected into it, and a killed run can leave a half-written final line.
    Those lines carry no events, and dropping them is what lets the triage
    still report on a truncated log instead of raising.
    """
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def triage(lines, skill, rc=None, plugin=None):
    """Return the verdict/diagnosis dict for one run log."""
    expected = _expected(skill)

    surfaced = None
    plugin_dirs = []
    skills_invoked = []
    tools_used = []
    result = None

    for event in _events(lines):
        if event.get("type") == "system" and event.get("subtype") == "init":
            listed = []
            for key in ("skills", "slash_commands"):
                value = event.get(key)
                if isinstance(value, list):
                    listed.extend(value)
            surfaced = bool(expected & {_bare(name) for name in listed})
            for entry in event.get("plugins") or []:
                if isinstance(entry, dict) and entry.get("name") == plugin:
                    plugin_dirs.append(entry.get("path"))
        elif event.get("type") == "assistant":
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for block in content or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                tools_used.append(name)
                if name != "Skill":
                    continue
                inputs = block.get("input")
                invoked = inputs.get("skill") if isinstance(inputs, dict) else None
                if isinstance(invoked, str):
                    skills_invoked.append(invoked)
        elif event.get("type") == "result":
            result = event

    fired = bool(expected & {_bare(name) for name in skills_invoked})

    final_text = result.get("result") if result else None
    if isinstance(final_text, str) and len(final_text) > FINAL_TEXT_LIMIT:
        final_text = final_text[:FINAL_TEXT_LIMIT] + "…"

    verdict = {
        "skill": skill,
        "fired": fired,
        "cause": None,
        # No terminal `result` event means the run did not finish — `timeout`
        # sends SIGTERM, so the stream simply stops. Tracked separately from
        # `cause` because it matters on a PASS too: a row killed after its Skill
        # call passes, and would have failed had the call landed a few seconds
        # later. Those passes are the population the misses are drawn from, so
        # counting them is what makes the failure rate measurable.
        "truncated": result is None,
        "rc": rc,
        "surfaced": surfaced,
        "plugin_dirs": plugin_dirs,
        "skills_invoked": sorted(set(skills_invoked)),
        "tools_used": sorted({name for name in tools_used if isinstance(name, str)}),
        "result_subtype": result.get("subtype") if result else None,
        "num_turns": result.get("num_turns") if result else None,
        "api_error_status": result.get("api_error_status") if result else None,
        "stop_reason": result.get("stop_reason") if result else None,
        "terminal_reason": result.get("terminal_reason") if result else None,
        "permission_denials": len(result.get("permission_denials") or [])
        if result
        else None,
        "final_text": final_text if not fired else None,
    }
    if not fired:
        verdict["cause"] = _classify(verdict, result)
    return verdict


def _classify(verdict, result):
    """Name the miss. Ordered most-mechanical cause first — an unfinished run
    explains itself, and asking 'did the model choose badly' only makes sense
    once the run is known to have completed with the skill in front of it."""
    if result is None:
        return "no-result-event"
    if (
        verdict["api_error_status"]
        or verdict["result_subtype"] == "error_during_execution"
    ):
        return "api-error"
    if verdict["result_subtype"] == "error_max_turns":
        return "max-turns"
    if verdict["surfaced"] is False:
        return "not-surfaced"
    if verdict["skills_invoked"]:
        return "wrong-skill"
    return "no-skill-chosen"


def render(verdict):
    """Human-readable lines for the harness's stdout."""
    # Printed on a pass as well as a failure, because the expected name matches
    # whichever copy served it: the eval runs `--plugin-dir <repo>` inside a
    # session that also loads the installed plugin, so a row can be satisfied by
    # the installed copy and report a clean pass over a tree that is not the one
    # under test. Deliberately no `⚠` and no log retention — unlike a truncated
    # pass, this is a property of the machine, identical for every row in the
    # run, so keeping N logs would repeat one fact N times, and the kept log
    # could not answer the question anyway: the Skill event does not record
    # which copy served the call. The remedy is to run with the installed copy
    # disabled, not to collect evidence.
    dup = (
        [
            "     plugin loaded twice: "
            + " ".join(str(p) for p in verdict["plugin_dirs"])
        ]
        if len(verdict["plugin_dirs"]) > 1
        else []
    )
    if verdict["fired"]:
        if verdict["truncated"]:
            return [
                "  ⚠ PASS on a truncated run — the skill fired before the run was killed",
                f"     rc={verdict['rc']}  no result event  "
                "(a later Skill call would have failed this row)",
            ] + dup
        return ["  ✅ PASS"] + dup
    invoked = " ".join(verdict["skills_invoked"]) or "none"
    lines = [f"  ❌ FAIL — skills invoked: {invoked}  (cause: {verdict['cause']})"]
    detail = [
        f"rc={verdict['rc']}",
        f"result={verdict['result_subtype']}",
        f"turns={verdict['num_turns']}",
        f"surfaced={verdict['surfaced']}",
    ]
    if verdict["api_error_status"]:
        detail.append(f"api_error={verdict['api_error_status']}")
    if verdict["permission_denials"]:
        detail.append(f"denials={verdict['permission_denials']}")
    lines.append("     " + "  ".join(detail))
    if verdict["tools_used"]:
        lines.append("     tools: " + " ".join(verdict["tools_used"]))
    lines.extend(dup)
    if verdict["final_text"]:
        lines.append(
            "     said instead: " + verdict["final_text"].replace("\n", " ")[:200]
        )
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", required=True, help="stream-json run log")
    parser.add_argument("--skill", required=True, help="skill the row expects")
    parser.add_argument(
        "--rc", type=int, default=None, help="exit code of the claude run"
    )
    parser.add_argument(
        "--plugin", default=None, help="plugin name to report loaded paths for"
    )
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    args = parser.parse_args(argv)

    with open(args.log, encoding="utf-8", errors="replace") as handle:
        verdict = triage(handle, args.skill, rc=args.rc, plugin=args.plugin)

    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        print("\n".join(render(verdict)))
    return 0 if verdict["fired"] else 1


if __name__ == "__main__":
    sys.exit(main())
