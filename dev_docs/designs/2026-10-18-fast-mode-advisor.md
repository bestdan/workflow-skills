---
created: 2026-10-18
status: proposed
---

# Fast-mode advisor for hand-run task stacks

This design covers suggestions in sessions the owner runs by hand. A later
design covers dispatched work — `select-coder` launching `claude -p` workers
in fast mode — and reuses this design's rule script.

## Intent

The owner hand-runs stacks of small PRs that must ship together. While a task
that others wait on is in flight, the rest of the stack — and the owner — sit
idle. Claude Code's fast mode (Opus at 2× price, up to ~2.5× output speed)
shortens that wait, but on a Max plan it is billed entirely from usage credits,
never from plan limits ([fast-mode docs](https://code.claude.com/docs/en/fast-mode)),
so leaving it on all the time costs roughly $3,000 a month at the owner's
current volume. The advisor tells the owner, at the moment a task starts,
whether this task is one where paying 2× buys back blocked time — and reminds
them to turn it off when it no longer is.

## Goals

- At task start, one line: suggest `/fast` (with a rough extra-cost range) when
  the task qualifies; say nothing when it doesn't. One line means at most 120
  characters, for example: "↯ #694 frees #693, ≥2 wait on it — if fast mode
  is off, `/fast on` (Opus): ≈$5–15 extra + ≈$1.60 to turn on now."
- When fast mode is on and the work moves to a task that doesn't qualify, one
  line suggesting the owner turn it off.
- Work for the two places the owner keeps stack order: the **gh-issue** handler
  (native `blocked_by` edges, `est:<n>` labels) and **plan-with-docs** task files
  (`is_blocked_by`, `size` frontmatter).

## Non-goals

- Turning fast mode on or off. Claude never does this; the owner types `/fast`.
  The documented switches are `/fast`, the `fastMode` key in the user
  `settings.json`, and `--settings '{"fastMode": true}'` at launch. None of
  them is reachable from inside a running interactive session. The sandbox
  denies writes to `settings.json`, and an agent enabling a 2× spend on itself
  is the thing that deny exists to prevent.
- Linear and Jira. Their dependency data exists; the owner doesn't keep stacks
  there. Easy to add later behind the same rule.
- Dispatched workers, `select-coder`, `orchestrate-coders`, `auto-pilot` — the
  later design. Input for it: the TypeScript Agent SDK's
  `applyFlagSettings({ fastMode: true })` switches a running session on its
  next turn and reports `fast_mode_state` (streaming-input mode only; the
  Python SDK has no equivalent), so one long-lived worker could be switched
  per task instead of launching a fresh `claude -p` for each
  ([SDK docs](https://code.claude.com/docs/en/agent-sdk/typescript)).
- The late-night "don't lose context before shipping" case. Fast mode is paid
  from usage credits, and late night is when the owner's spending self-control
  is lowest, so the owner chose to spend on blocking stack tasks instead.

## Anti-goals

- Nagging. Silence is the default; a suggestion must be rare enough to mean
  something. Against a snapshot of this repo's graph on 2026-10-18, the rule
  below suggests 14 of 210 open issues.
- Slowing the claim. Must not fetch the whole issue graph: a full
  `gh-issue-graph.py` run over this repo took over two minutes.
- A false sense of precision about cost. The range is labelled an estimate.

## The rule (decided, validated against this repo's live graph)

A task **qualifies** when its size is 1, 2 or 3 **and** either:

1. it is the **last open blocker** of at least one open task (finishing it frees
   that task now), or
2. at least **two** open tasks wait on it, directly or transitively.

Otherwise the advisor is silent. Size 5 or larger (`est:` allows 5, 8 and
13), or no size at all, is silent.
Cleanup / graduate-the-design tasks count like any other: the owner reviewed
the 14 tasks the rule picks in this repo, cleanups included, and accepted all
of them.

Why condition 1 is "last open blocker" rather than "any open dependent":
siblings that all feed one join task (#687–#691 → #686 in this repo) each
block it, but speeding any one of them frees nothing. Counting any open
dependent picks 27 tasks here; the extra 13 are siblings like these.

Cost range by size (placeholder, extra spend over standard): size 1 ≈ $2–5,
size 2 ≈ $5–15, size 3 ≈ $10–30.

Those ranges leave out a one-time charge: "The first time you enable fast mode
in a conversation, you pay the full fast mode uncached input token price for
the entire conversation context"
([fast-mode docs](https://code.claude.com/docs/en/fast-mode)). It doesn't
repeat when fast mode is toggled off and on again in the same conversation.
The advisor adds it to the estimate rather than advising `/clear`: when fast
mode is not already on and the status-line file gives the context size, the
line carries "+ ≈$X one-time to turn on now" (context tokens × $8/MTok on
Opus 5.5). That is about $1.60 at 200k tokens. `/clear` would only shrink the
charge — it rebuilds the cache, so the charge applies again to the new
context — and it throws away the stack context a hand-run session is built
on.

## Shape

- **`scripts/fast-advice.py`** (or under `commands/handlers/assets/` if the
  builder judges it handler-specific — builder's call), with a test pair. Inputs:
  the source (`gh-issue` + repo + issue number, or a plan task file path).
  Output: JSON `{qualifies, size, waiting, frees, cost_range, fast_on}` plus a
  one-line human message. Reads only.
  - `frees`: the open tasks for which this task is the last open blocker
    (condition 1).
  - `waiting`: distinct open tasks that depend on this task, directly or
    transitively — a lower bound, exact up to 2 and reported as "≥ 2" beyond,
    because the walk stops once condition 2 is decided.
  - The suggestion always says `/fast on`, never bare `/fast`, so following it
    can't switch fast mode off, and always carries a fixed "(Opus)" because
    turning fast mode on switches a non-Opus session to Opus. The script
    cannot read the live session model
    (the `model` settings key is a default, not live state), so it does not try
    to condition on it.
  - gh-issue: fetch the issue, its `blocking` list, and for each dependent its
    `blocked_by` list — neighbours only, never the whole repo. The transitive
    walk is breadth-first over `blocking` with a visited set, and stops as soon
    as two distinct open waiters are found. GitHub's cycle guard is one hop
    deep (`gh-issue-deps.py`), so longer cycles exist and the visited set is
    required, not hardening.
  - Closed nodes: the walk stops at a closed dependent, and a blocker closed
    for any reason (`not_planned` included) is not open — the same test
    `gh-issue-ready.py` applies (`state == "open"`).
  - Cross-repo edges, one-sided: skip cross-repo entries (identified by
    `repository_url`) in the task's own `blocking` list, but count an open
    cross-repo entry in a dependent's `blocked_by` list when deciding "last
    open blocker". Both halves err toward silence: the first under-counts
    `waiting`, the second prevents a false `frees`. The existing helpers are
    no precedent here — none of them reads `repository_url`.
  - plan: run `scripts/task-scan.py <dev_docs/tasks>` as a subprocess and read
    its JSON (`slug`, `status`, `size`, `is_blocked_by`,
    `unresolved_blockers`). That reuses its slug rules (global filename stems,
    `_archive/` and epics skipped, duplicates fail toward blocked) without
    copying them. A dependent is in `frees` when its `unresolved_blockers` is
    exactly this task's slug; "open" means status not `done`. A non-zero exit
    is unknown, and unknown is silent.
  - `fast_on`, live source first: the status line's input JSON carries the
    session's live `fast_mode`, its `session_id`, and
    `context_window.total_input_tokens`
    ([statusline docs](https://code.claude.com/docs/en/statusline)). Plugins
    can't ship a `statusLine` setting (only `agent` and `subagentStatusLine`
    take effect from a plugin's settings), so the plugin ships a small writer
    script that the owner calls from their existing status-line script, which
    already reads its input and `session_id`
    (`~/src/dotfiles/agents/status_line.sh`). The writer stores `fast_mode`
    and the context token count at
    `/tmp/claude/fast-mode/<sha256(session_id)>`, hashing the id because it
    arrives as untrusted input and goes into a path, as `status_line.sh`
    already does for its own state file. It uses the fixed `/tmp/claude` path,
    not `$TMPDIR`: the status line runs unsandboxed and the advisor sandboxed,
    and `$TMPDIR` differs across that boundary. The writer prunes files older
    than a day. The advisor finds the file by hashing
    `CLAUDE_CODE_SESSION_ID`, which Claude Code sets in Bash subprocesses to
    the same id as hooks' `session_id` and updates on `/clear`
    ([env-vars docs](https://code.claude.com/docs/en/env-vars)).
  - The file lags. `/fast` doesn't re-render the status line; the next render
    comes with the next assistant message or the `refreshInterval` tick (30s
    in the owner's settings). A fresh mtime doesn't prove the value is
    current — the writer runs on every render, so the file is fresh even in
    the window after `/fast` and before the next render. So the file is never
    treated as exact: both the suggestion ("if fast mode is off, `/fast on`")
    and the reminder ("if fast mode is still on…") stay conditional. The file
    makes them better targeted, not unconditional. The mtime only tells a
    live writer from a dead one: older than five minutes counts as missing
    and falls through to the fallback below. On
    `--continue` or `--resume` without an explicit id,
    `CLAUDE_CODE_SESSION_ID` may hold the startup id, the file isn't found,
    and the fallback applies; that is expected, not a bug.
  - `fast_on`, fallback when the file is missing (writer not installed, or no
    status-line render yet this session): read `fastMode` from
    `~/.claude/settings.json` (read is allowed). `/fast` persists its state
    there: the settings reference says "running `/fast` writes
    `fastMode: true` to `~/.claude/settings.json`". That key is the **saved
    preference**, not live session state: fast mode turns off for a session on
    credit exhaustion or a switch to a non-Opus model while the key stays
    `true`, and `fastModePerSessionOptIn: true` starts every session off. So
    here `fast_on` is a hint: `true` is on; `false` or an absent key is off;
    `fastModePerSessionOptIn: true` or an unreadable file is unknown, and
    unknown prints no reminder. The fallback reminder is phrased conditionally
    ("if fast mode is still on, this task doesn't qualify").
  - The JSON output carries `fast_on_source: file | settings | unknown` and,
    when the advisor stays silent because something failed, a `reason` (for
    example `task-scan failed: <stderr tail>`). Silence is also the normal
    answer, so without these a writer that was never installed, or a plan
    path that always fails, would look like "doesn't qualify" forever.
    `/doctor` gets a check that the writer is wired into the status line.
- **Prose hooks** — one sentence each; the logic stays in the script, per the
  repo's "logic goes in a typed file" rule. Each runs the script and relays its
  line verbatim when `qualifies`, or when `fast_on` and not `qualifies`. They
  fire when work starts in this session, and never on `--claim-only` (a
  reservation, not a start), in batch runs, or on a remote dispatch, where
  `/fast` in the owner's session buys nothing. The sentence carries those
  conditions itself: "in an attended session, and not on `--claim-only`".
  `--claim-only` still runs through "Claim the issue", and the batch path
  inlines `gh-issue-claim.md` into each dispatched session under a fixed
  five-substitution contract (`do-tasks.md` §4), so a bare sentence would fire
  in both. The batch prompt declares "No human is present" (`do-tasks.md` §4
  step 6), and `commands/handlers/attendedness.md` lists the other hard
  negatives (`--non-interactive`, `$AUTO_PILOT_UNTIL`, running as a subagent,
  a `/loop` or routine turn). Together they cover batch, remote and cron in
  one clause without adding a sixth substitution.
  A wrong guess costs one printed line. `commands/do-tasks.md` has no
  shared single-task claim step for gh-issue and repo-pr — each delegates to
  its handler — so the hooks go in the handlers:
  - gh-issue: `commands/handlers/gh-issue-claim.md`, at the end of "Claim the
    issue" (after step 4, before "Branch + execute"), and on the
    `--no-claim <#n>` entry.
  - repo-pr: `commands/handlers/repo-pr-execute.md`, in "Local mode
    (`--local`)" step 2, on the branch that executes (a named slug, or
    `size <= auto_execute_max_size`), never on a reserve. Not at the shared
    Claim protocol's step 4 "Reconcile": remote dispatch, `--local` and the
    size-gate reserve all run those steps. Also on the repo-pr
    `--no-claim` resume, whose procedure lives in `do-tasks.md`'s per-handler
    bullets. Not in the default `--remote` dispatch or its inlined prompt.
- **Plan-file hook**: the owner works plan stacks from the local task files
  first, before (or without) `/push-plan`, even in gh-issue repos. Nothing
  claims a local plan file there, so the trigger is the owner starting a plan
  task by its slug or path ("start `<name>_task_3`"). `plan-with-docs` does not
  load on that phrase — its description covers authoring plans only — so the
  hook goes in `skills/task/SKILL.md`: extend its description's "starts work on
  an existing tracker issue by naming its key" trigger to cover a
  `<name>_task_N` slug or a `dev_docs/tasks/…` path. That section's rule is
  "claim before the first edit" via `/do-tasks <KEY>`, so the new bullet is a
  carve-out: a plan task file is not a tracker issue on a tracker handler, so
  skip the claim, run the script on the file, relay its line, and continue as
  the owner directed. It must not route through `/do-tasks <slug>`, which on a
  gh-issue repo expects an issue number. On a repo-pr repo the skill hook does
  nothing: there a plan file is the claimable unit, the skill's normal route
  to `/do-tasks <slug>` applies, and the repo-pr handler hook owns the advice
  (on `--local`; a remote dispatch gets none, correctly). That leaves exactly
  one hook per start, with nothing to de-duplicate. Once a plan is pushed, its
  tasks are issues and the gh-issue path covers them.

## Constraints

- Bash 3.2 / Python stdlib only for the new script; no new dependencies. The
  plan path shells out to `scripts/task-scan.py`, an existing uv/pyyaml
  script the plugin already runs from `/do-tasks` and `/list-tasks`.
- gh calls are network: callers run them unsandboxed, as the other gh-issue
  helpers already are.
- Fast mode is research preview; its billing and model list may change. The
  design rests on: Opus-only (5.5, 5, 4.8; not Fable), credits-only on
  subscription plans, `fastMode` settings key, the status line's `fast_mode`
  field, and `CLAUDE_CODE_SESSION_ID`. Source: fast-mode, settings-reference,
  statusline and env-vars pages on code.claude.com, fetched 2026-10-18 and
  2026-10-21.
- No hook or tool lets Claude turn fast mode on in an interactive session.
  Prior requests for one ([#31579](https://github.com/anthropics/claude-code/issues/31579),
  [#29234](https://github.com/anthropics/claude-code/issues/29234)) were
  closed without it, and the nearest community router
  ([claude-model-router-hook](https://github.com/tzachbon/claude-model-router-hook))
  also defaults to warning rather than switching.

## Left to the builder

- Exact file location and CLI flags.
- Test fixtures: recorded `blocking` / `blocked_by` payloads injected through
  a `run_gh` seam the test stubs, as `scripts/test_gh_issue_ready.py` does, so
  the suite makes no network calls; add a cycle case. Entries carry
  `repository_url` (the existing fixtures don't), with one cross-repo entry.
  Cover the task-scan non-zero-exit branch too, and a plan whose blocker file
  was deleted: `task-scan.py` treats an absent blocker as resolved.
- `task-scan.py` groups its output under `cards.<status>`, and its default
  task directory is relative to the cwd, so pass the path from the repo root.
- Paginate each dependent's `blocked_by` read (`--paginate --slurp`) as
  `gh-issue-ready.py` does. The endpoint returns 30 per page, and an open
  blocker on page 2 would turn a non-qualifying task into a false `frees`. These real cases from
  this repo's graph are good shapes to record — a chain
  (#700 → #698 → #696 → #694 → #693), a fan-out root (#692), a join with
  siblings (#687–#691 → #686), and size-5 blockers (#674, #760).

## Evidence

The rule was checked by a throwaway prototype run over the full
`gh-issue-graph.py --json` output for this repo on 2026-10-18; neither was
kept, so the 14-of-210 figure is a snapshot, not reproducible. The builder's
script, run over the same repo, replaces it. Usage-cost figures come from the
owner's Claude Code transcripts,
2026-09-18 to 2026-10-18, priced at Opus 5.5 rates.
