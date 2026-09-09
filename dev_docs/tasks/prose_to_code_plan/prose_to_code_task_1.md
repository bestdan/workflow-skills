---
title: Extract Jira transition-id resolution into jira-resolve-transition.py
priority: high
size: 3
impact: 5
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - commands/handlers/jira-claim.md:213-230
  - commands/handlers/jira-complete.md:82-111
  - commands/handlers/jira-promote.md:117-125
  - commands/handlers/jira.md:79-86
  - commands/handlers/jira-archive.md:64-69
  - commands/handlers/assets/gh-issue-state.py
  - CONTRIBUTING.md
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - jira
---

Plan: [[prose_to_code_plan]] · Index row 2.

## Context

Five Jira handlers each hand-walk "pick the right transition id out of the
`getTransitionsForJiraIssue` array". Two policies exist. Claim and complete
filter by `to.statusCategory.key`, drop excluded names, prefer a name pattern,
then disambiguate or stop. Promote, create and archive match `to.name` to a
configured status exactly (fallback: the transition's own `name`) and skip when
absent. `jira-complete.md` records why the order matters: filtering
cancellation-style names before counting is what stops a board whose only
terminal transition is Canceled from being completed with the wrong resolution.
No `jira-*.py` asset exists today.

The MCP fetch stays in prose; the helper owns the decision over the JSON.

## Task

1. Add `commands/handlers/assets/jira-resolve-transition.py` (Python 3.9,
   stdlib). Input: the `transitions[]` JSON on stdin. Modes, mutually
   exclusive:
   - `--category <key> [--exclude <regex>] [--prefer <regex>]` — filter to
     `to.statusCategory.key == <key>`, drop names matching `--exclude`
     (case-insensitive) **before** counting, then: one left → print its id;
     several → keep those matching `--prefer`; still 0 or >1 → print
     `AMBIGUOUS` or `NONE` plus the candidate names, exit 2.
   - `--exact <status>` — `to.name == <status>` (case-insensitive), fallback
     `name == <status>`; print the id, or `NONE` and exit 2.
   Print `<id>\t<to.name>` on success so the prose can name the landing
   status.
2. Add `scripts/test_jira_resolve_transition.py` (unittest, no network) with a
   fixture per prose case: the real-workflow shape from `jira-claim.md` (In
   Execution / Validation / On Hold, no "In Progress"), the Canceled-only
   terminal board, the exact-match fallback, and the ambiguous case. Add
   `scripts/test-jira-resolve-transition.sh` that `exec`s it.
3. Rewrite the five call sites to: fetch, write the JSON to a file, run the
   helper, read stdout. Keep each call site's own stop message. Mirror the
   `$CLAUDE_PLUGIN_ROOT`-unset fallback wording from `gh-issue-promote.md`.
4. Update the capability matrix in `commands/task-config.md` only if a
   capability changes (it should not).

## Acceptance Criteria

- Code-enforced: `just check` passes; the new test file pins the four cases
  above, and the Canceled-only case fails if the exclude filter runs after the
  count.
- Code-enforced: `rg -n 'statusCategory' commands/handlers/jira*.md` shows no
  remaining hand-walked filter — only the helper call and its output contract.
- User-run: `/complete-task` on a Jira site whose terminal transitions include
  a cancel name resolves to the done transition, not the cancel one.
