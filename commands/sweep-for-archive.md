---
description: Close-out sweep over a recent window — verify what closed was really delivered, complete anything whose PR merged, then archive exactly the verified closures. Composes /find-false-closures, /sweep-for-complete, and /archive-tasks with the verified id set carried between them
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(op *), Bash(python3 *), Glob, Grep, Read, AskUserQuestion, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__save_comment, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__linear__get_issue, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__linear__save_issue, mcp__linear__save_comment, mcp__linear__list_teams, mcp__linear__list_projects
argument-hint: "[--since 24h] [--apply] [--project <uuid>] [--restore-false-closures]"
---

# Sweep for Archive

A high-velocity day closes a dozen issues, and three separate questions follow:
were they _really_ delivered, is anything sitting merged-but-not-completed, and
can the settled ones stop consuming Linear's 250-active-issue cap.
`/sweep-for-complete` and `/find-false-closures` and `/archive-tasks` each answer
one of those. `/sweep-for-archive` is the close-out pass that runs all three **in
the one order that is safe**, and — the reason it is a command rather than three
invocations — **carries the verified id set between them**.

That carry is the whole point. `/archive-tasks --issues` takes a literal id list
and archives it whatever the age; the list this command hands it is exactly the
set the earlier legs _proved_ was delivered. Archiving is the deepest gate in the
loop (`linear-false-closures.md`: an archived completion is settled and the
backstop never revisits it), so an id that reaches leg 3 on a guess is a false
closure that can no longer be found. Nothing gets archived here that a merged PR
did not own.

**`linear` handler only.** Two of the three legs already refuse on
`repo-pr`/`gh-issue`/`jira` — see step 1.

## Arguments

- **`--since <window>`** — the close-out window: `24h` / `2d` shorthand, an ISO
  datetime, or a Linear duration (`-P2D`). **Defaults to `24h`.** This is the
  bound on legs 1 and 3; leg 2 is unbounded by design (a PR that merged last week
  against an issue still sitting `In Review` is exactly what the sweep is for).
- **`--apply`** — actually mutate: complete the sweep's verified matches and
  archive the verified closures. Without it every leg runs read-only and the
  command prints what it _would_ do (dry-run is the default posture, inherited
  from all three legs).
- **`--project <uuid>`** — scope every leg to one Linear project, overriding the
  configured `linear.projects`. Omit to cover all of them.
- **`--restore-false-closures`** — pre-answer the step 5 prompt with _yes_.
  Restoring is **off by default**; without this flag an interactive run asks, and
  an unattended one (`/loop`, `/schedule`, `--apply` with no TTY) declines. See
  step 5 for why the default is off.

## 1. Resolve the handler

Resolve the handler from the **merged view** — the committed config overlaid with
the optional local override (see `task-config.md` → "Resolving the handler"):

```bash
ROOT="$(git rev-parse --show-toplevel)"
cat "$ROOT/dev_docs/tasks/.task-config.yml" 2>/dev/null       # committed config
cat "$ROOT/dev_docs/tasks/.task-config.local.yml" 2>/dev/null # optional gitignored override
```

- `handler: linear` → continue with step 2.
- File absent, or `handler: repo-pr` / `gh-issue` / `jira` → **UNSUPPORTED.**
  Print: "unsupported for handler `<handler>` — `/find-false-closures` and
  `/sweep-for-complete` are both linear-only (each explains why for your
  handler), so there is no verified set to carry. Run `/archive-tasks` directly
  if you only want the retire step." Change nothing.
- Any other (unknown) value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

The legs below are **not re-specified here** — read and follow each referenced
file, exactly as its own command would. This file owns only the order, the id
set that moves between them, and the two decisions (step 5's prompt, step 6's
report) that are new.

## 2. Leg 1 — verify the window's closures (always read-only)

Follow **`commands/handlers/linear-false-closures.md`** ("Invoked from
`/find-false-closures`"), passing `--since <window>` and, if given, `--project`.
Run it **without `--apply` even when this command was given `--apply`** — this
leg's mutation is a _restore_, which step 5 gates separately.

Split its output into two sets and hold both:

- **`verified`** — every `ok <IDENTIFIER> <- <PR URL>` line. A merged PR owns
  each of these; they are leg 3's candidate list.
- **`false`** — every `FALSE CLOSURES` row. These are **excluded from `verified`
  permanently** and never reach leg 3, whatever step 5 decides. A false closure
  is unfinished work wearing a Done label; archiving it would bury it.

If leg 1 fails outright (no `op` session, a GraphQL error, a non-zero exit that
is not the "found false closures" exit `1`), **stop the whole command** and say
so. `verified` is a _positive_ proof list — a leg that could not run produces an
empty one, not a safe one, and continuing would archive nothing while reporting
a clean sweep.

## 3. Leg 2 — complete what merged

Follow **`commands/handlers/linear-sweep-complete.md`** in full, passing
`--apply` through only if this command got it, and `--project` if given. Do not
pass `--since`: that file has no such flag and the sweep is deliberately
unbounded (see the window note above).

Every issue it **completes in this run** joins `verified`. That is sound for the
same reason the sweep is safe: it completed the issue only after independently
confirming that the issue's **own** structurally-linked PR merged — the identical
ownership proof leg 1 computes, arrived at from the other direction. Issues it
leaves (`open`, `unresolved`, `closed unmerged`, `no PR found`) join nothing.

On a **dry run**, nothing is actually completed, so nothing joins `verified` —
report those candidates as "would complete, then would archive" rather than
silently folding them into leg 3's list.

Carry leg 2's out-of-scope warning line through to the final report unchanged.

## 4. Leg 3 — archive the verified set

If `verified` is empty, skip the leg and report "nothing verified in the window —
nothing to archive."

Otherwise follow **`commands/handlers/linear-archive.md`** → "Named issues
instead of a sweep (`--issues <refs>`)", passing `verified` as the refs and
`--apply` only if this command got it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-archive.py" \
  --team "<linear.team>" --issues "<verified ids, comma-separated>" [--apply]
```

Three properties of that path matter here and none are optional:

- **`--issues` is the right mode, not a shortcut past a safety rail.** The
  `archive_after` threshold exists to bound a _sweep_ that would otherwise have
  no candidate list. Here `--since` already bounded it and leg 1 proved every
  member, so the threshold is redundant — this run deliberately archives work
  closed minutes ago, which is the case the threshold was blocking.
- **Terminal state is still checked**, client-side, by the asset. It cannot be
  reached from here.
- **The 250-ref cap applies.** A window wide enough to exceed it is a window that
  wanted `/archive-tasks --older-than` instead; say so and stop rather than
  splitting the list silently.

## 5. The false-closure prompt (`--apply` only)

If `false` is empty, skip this step entirely.

Otherwise, print the flagged rows, then **ask** — one question, default _no_:

> N false closure(s) found. Restore them to Todo?

- **`--restore-false-closures` was passed** → skip the ask, treat as _yes_.
- **No TTY / unattended run** (`/loop`, `/schedule`, `/auto-pilot`) → **decline
  silently and report it.** Never block a scheduled close-out on an unanswered
  prompt, and never restore without an answer.
- **Yes** → re-run leg 1's asset with `--apply --only <the flagged ids>` (that
  flag exists precisely so an apply matches a specific approval rather than
  reopening whatever a later scan turns up).
- **No** (the default) → change nothing; the rows stay in the report.

**Why off by default.** A restore is a _demotion_: it drags an issue the team
sees as Done back into Todo. The other two legs only ever move work forward
(complete, archive), so they are safe to batch behind one `--apply`; this one
reverses a human-visible state and deserves its own answer. The detection is also
the least certain of the three — "no owning merged PR" legitimately describes
work delivered outside the PR flow. Leaving it flagged-but-untouched costs a line
in the report; restoring it wrongly costs a re-triage.

## 6. Report

One block, in leg order:

- **Scope** — `window: <since> · projects: <names> · mode: dry-run | apply`.
- **Leg 1 — verified** — count, and the `IDENTIFIER <- PR #n` rows.
- **Leg 1 — false closures** — count and rows, each with the PR that most likely
  tripped it, plus the step 5 outcome (`restored`, `left (declined)`,
  `left (unattended)`, or `left (dry-run)`).
- **Leg 2 — sweep** — the handler's own counts (`k completed, m open, u
  unresolved, s no-PR skipped, c closed-unmerged`) and its out-of-scope warning
  line verbatim when it printed one.
- **Leg 3 — archived** — count archived, any failures with their error, and
  which ids came from leg 1 versus leg 2.
- **Dry run** — the same block with each leg's mutations stated as _would_, and
  one explicit closing line: "nothing changed (dry-run) — re-run with `--apply`."

State the two exclusions explicitly whenever they are non-empty, because their
absence from the archived list is the report's most load-bearing silence:
`N false closure(s) excluded from the archive set` and `N issue(s) left in flight
(open PR)`.

## 7. Scheduling

The close-out is a daily-cadence job. Both wrappers work and neither needs new
infrastructure:

- **`/schedule`** — a cloud routine running
  `/sweep-for-archive --since 24h --apply` daily. Unattended, so step 5 declines
  on its own and false closures accumulate in the report rather than being
  silently reopened.
- **`/loop`** — `/loop 1d /sweep-for-archive --since 24h --apply` in a session
  you keep open, where step 5 can actually ask you.

Keep `--since` and the cadence in agreement: a daily run with `--since 24h`
covers everything exactly once, and a wider window is only redundant, never
harmful (leg 1 excludes archived issues, so yesterday's already-archived
closures never come back).
